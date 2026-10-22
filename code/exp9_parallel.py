#!/usr/bin/env python3
"""Parallelized experiment 9.

Runs every exp9 setting N times (default 1000) on one or both wire surfaces,
stores every raw conversation as JSONL, and writes a probability matrix.

The shared turn-1 history is generated once per surface; every trial reuses it.
Only the turn-1 reasoning text differs between settings (the injected intent),
so the only variable is the reasoning content.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import config
import exp9_intent_injection as exp9
import probes
from ds_client import AnthropicMessagesClient, DSApiError, OpenAIChatClient

SETTINGS = [
    ("no_intent", None, True, True),
    ("intent_exhibition", exp9.INTENT_EXHIBITION, True, True),
    ("intent_cooking", exp9.INTENT_COOKING, True, True),
    ("intent_cooking_no_tools", exp9.INTENT_COOKING, False, True),
    ("intent_cooking_thinking_off", exp9.INTENT_COOKING, True, False),
]
CATEGORIES = ["exhibition", "cooking", "outdoor", "other"]
_thread_local = threading.local()


def get_client(surface_name: str):
    client = getattr(_thread_local, "client", None)
    if client is None:
        if surface_name == "anthropic":
            client = AnthropicMessagesClient(config.API_KEY, config.ANTHROPIC_BASE_URL)
        else:
            client = OpenAIChatClient(config.API_KEY, config.OPENAI_BASE_URL)
        _thread_local.client = client
    return client


def call_with_retry(surface_name: str, messages, *, tools, system, thinking: bool,
                    max_tokens: int = 1024, attempts: int = 5):
    """One request with retry; returns (result, messages_actually_sent)."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            if surface_name == "anthropic":
                result = get_client(surface_name).create(
                    model=config.MODEL, messages=messages, tools=tools, system=system,
                    max_tokens=max_tokens, thinking=thinking, effort=config.EFFORT,
                    stream=True,
                )
                return result, messages, attempt
            sent = ([{"role": "system", "content": system}] + messages) if system else messages
            result = get_client(surface_name).create(
                model=config.MODEL, messages=sent, tools=tools,
                max_tokens=max_tokens, thinking=thinking, effort=config.EFFORT,
            )
            return result, sent, attempt
        except DSApiError as error:
            last_error = error
            retryable = error.status in (408, 429, 500, 502, 503, 504) or error.status is None
            if not retryable or attempt == attempts:
                raise
        except Exception as error:  # network timeouts and connection resets
            last_error = error
            if attempt == attempts:
                raise
        time.sleep(min(30.0, 0.5 * (2 ** (attempt - 1))))
    raise RuntimeError(f"unreachable: {last_error}")


def generate_shared_history(surface_name: str, system: str) -> dict:
    surface = probes.make_surface(surface_name)
    gen = exp9.generate_turn1(surface, system)
    signature = surface.signature_of(gen["outcome"].result)
    histories = {}
    for setting, intent, _, _ in SETTINGS:
        reasoning = gen["reasoning"] if intent is None else gen["reasoning"] + "\n\n" + intent
        histories[setting] = [
            surface.user(gen["prompt"]),
            surface.assistant(thinking=reasoning, signature=signature, text=gen["answer"]),
        ]
    return {
        "surface": surface,
        "prompt": gen["prompt"],
        "reasoning": gen["reasoning"],
        "answer": gen["answer"],
        "signature": signature,
        "histories": histories,
        "ask": surface.user(exp9.ASK),
    }


def one_trial(surface_name: str, shared: dict, setting: str, trial: int,
              tools_payload, tools_enabled: bool, thinking: bool, system: str) -> dict:
    started = time.time()
    messages = list(shared["histories"][setting]) + [shared["ask"]]
    try:
        result, sent, attempt = call_with_retry(
            surface_name, messages,
            tools=tools_payload if tools_enabled else None,
            system=system, thinking=thinking,
        )
        verdict = exp9.classify(result.text)
        return {
            "ok": True,
            "surface": surface_name,
            "setting": setting,
            "trial": trial,
            "system": system,
            "messages": sent,
            "response": {
                "thinking": result.thinking,
                "text": result.text,
                "content_blocks": result.content,
                "stop_reason": result.stop_reason,
                "request_id": result.request_id,
                "usage": result.usage.as_dict(),
            },
            "category": verdict["label"],
            "counts": verdict["counts"],
            "attempt": attempt,
            "elapsed_s": round(time.time() - started, 3),
            "timestamp": datetime.now().astimezone().isoformat(),
        }
    except Exception as error:
        status = getattr(error, "status", None)
        body = getattr(error, "body", None)
        return {
            "ok": False,
            "surface": surface_name,
            "setting": setting,
            "trial": trial,
            "system": system,
            "error": str(error)[:1000],
            "status": status,
            "body": (body or "")[:1000] if isinstance(body, str) else None,
            "elapsed_s": round(time.time() - started, 3),
            "timestamp": datetime.now().astimezone().isoformat(),
        }


def run_surface(surface_name: str, trials: int, concurrency: int, outdir: str) -> dict:
    system = probes.new_system("exp9-parallel")
    shared = generate_shared_history(surface_name, system)
    tools_payload = shared["surface"].tools_payload(["get_weather"])
    print(f"[{surface_name}] shared turn-1 generated; system={system}")

    counts = {setting: {category: 0 for category in CATEGORIES} for setting, *_ in SETTINGS}
    failures = {setting: 0 for setting, *_ in SETTINGS}
    usage_totals = {setting: {"input": 0, "output": 0, "hit": 0} for setting, *_ in SETTINGS}

    for setting, intent, tools_enabled, thinking in SETTINGS:
        path = os.path.join(outdir, f"{surface_name}_{setting}.jsonl")
        print(f"[{surface_name}/{setting}] start {trials} trials -> {path}")
        started = time.time()
        with open(path, "w", encoding="utf-8") as handle:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(one_trial, surface_name, shared, setting, trial,
                                tools_payload, tools_enabled, thinking, system)
                    for trial in range(trials)
                ]
                done = 0
                for future in as_completed(futures):
                    record = future.result()
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    if record["ok"]:
                        counts[setting][record["category"]] += 1
                        usage = record["response"]["usage"]
                        usage_totals[setting]["input"] += usage.get("total_input_tokens") or 0
                        usage_totals[setting]["output"] += usage.get("output_tokens") or 0
                        usage_totals[setting]["hit"] += usage.get("cache_read_input_tokens") or 0
                    else:
                        failures[setting] += 1
                    done += 1
                    if done % 200 == 0:
                        print(f"  [{surface_name}/{setting}] {done}/{trials} in {time.time()-started:.1f}s")
        print(f"[{surface_name}/{setting}] done in {time.time()-started:.1f}s, failures={failures[setting]}")

    return {
        "surface": surface_name,
        "system": system,
        "prompt": shared["prompt"],
        "reasoning": shared["reasoning"],
        "answer": shared["answer"],
        "counts": counts,
        "failures": failures,
        "usage_totals": usage_totals,
    }


def write_matrix(results: list[dict], trials: int, outdir: str, peak: bool) -> dict:
    factor = 1.0 if peak else 0.5
    prices = probes.PRICES_PEAK_USD_PER_M
    rows = []
    for result in results:
        surface = result["surface"]
        for setting, intent, tools_enabled, thinking in SETTINGS:
            counts = result["counts"][setting]
            total = sum(counts.values())
            row = {
                "surface": surface,
                "setting": setting,
                "intent": intent,
                "carries_tools": tools_enabled,
                "thinking": thinking,
                "trials_requested": trials,
                "trials_completed": total,
                "failures": result["failures"][setting],
            }
            for category in CATEGORIES:
                row[category] = counts[category]
                row[f"p_{category}"] = round(counts[category] / total, 6) if total else None
            usage = result["usage_totals"][setting]
            miss = max(0, usage["input"] - usage["hit"])
            cost = (miss * prices["cache_miss"] + usage["hit"] * prices["cache_hit"]
                    + usage["output"] * prices["output"]) * factor / 1e6
            row.update({
                "input_tokens": usage["input"],
                "output_tokens": usage["output"],
                "cache_hit_tokens": usage["hit"],
                "cost_usd": round(cost, 6),
            })
            rows.append(row)

    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "probability_matrix.json"), "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)

    import csv
    with open(os.path.join(outdir, "probability_matrix.csv"), "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# 实验 9 并行版：概率矩阵",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat()}",
        f"- 模型：{config.MODEL}，effort={config.EFFORT}，接口：anthropic / openai",
        f"- 每个设置每接口目标次数：{trials}",
        f"- 计费档：{'peak' if peak else 'off-peak'}，费用为按 usage × 官方价估算",
        "",
        "| 接口 | 设置 | 带 tools | thinking | 完成 | 失败 | exhibition | cooking | outdoor | other | 费用 USD |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['surface']} | {row['setting']} | {row['carries_tools']} | {row['thinking']} | "
            f"{row['trials_completed']} | {row['failures']} | "
            f"{row['exhibition']} ({row['p_exhibition']:.3f}) | "
            f"{row['cooking']} ({row['p_cooking']:.3f}) | "
            f"{row['outdoor']} ({row['p_outdoor']:.3f}) | "
            f"{row['other']} ({row['p_other']:.3f}) | {row['cost_usd']:.4f} |"
        )
    lines += ["", "概率 = 该设置下该类别次数 / 完成次数。", ""]
    with open(os.path.join(outdir, "probability_matrix.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    return {"rows": rows, "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=300)
    parser.add_argument("--surface", choices=["anthropic", "openai", "both"], default="both")
    parser.add_argument("--outdir", default=os.path.join(probes.RESULTS_DIR, "exp9_parallel"))
    args = parser.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    peak = probes.is_peak_now()
    surfaces = ["anthropic", "openai"] if args.surface == "both" else [args.surface]
    started = time.time()
    results = []
    for surface in surfaces:
        result = run_surface(surface, args.trials, args.concurrency, args.outdir)
        results.append(result)
        with open(os.path.join(args.outdir, f"meta_{surface}.json"), "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)

    matrix = write_matrix(results, args.trials, args.outdir, peak)
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "model": config.MODEL,
        "effort": config.EFFORT,
        "trials_per_setting": args.trials,
        "concurrency": args.concurrency,
        "surfaces": surfaces,
        "peak": peak,
        "elapsed_seconds": round(time.time() - started, 1),
        "total_cost_usd": matrix["total_cost_usd"],
        "settings": [
            {"name": name, "intent": intent, "carries_tools": tools, "thinking": thinking}
            for name, intent, tools, thinking in SETTINGS
        ],
        "matrix": matrix["rows"],
    }
    with open(os.path.join(args.outdir, "metadata.json"), "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
    print("done in", metadata["elapsed_seconds"], "s, cost ~$", metadata["total_cost_usd"])
    print("files in", args.outdir)


if __name__ == "__main__":
    main()
