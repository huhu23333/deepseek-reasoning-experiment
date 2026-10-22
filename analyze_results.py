#!/usr/bin/env python3
"""Aggregate the recorded experiment JSON into results/summary.json and SUMMARY.md."""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime

import config
from probes import RESULTS_DIR, is_peak_now

EXPERIMENT_ORDER = [
    "exp1_no_tools_multiturn",
    "exp2_tools_multistep",
    "exp3_billing",
    "exp4_visibility",
    "exp5_multiturn_tool_reasoning",
    "exp6_completed_answer_reasoning",
    "exp7_thinking_gate",
    "exp8_reasoning_tamper",
    "exp9_intent_injection",
]
SURFACES = ["anthropic", "openai"]


def load_results() -> dict[tuple[str, str], dict]:
    loaded: dict[tuple[str, str], dict] = {}
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json"))):
        if os.path.basename(path) == "summary.json":
            continue
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        loaded[(payload["experiment"], payload["surface"])] = payload
    return loaded


def _usage(case: dict) -> dict:
    return case.get("usage") or {}


def _cost(case: dict) -> float:
    return float((case.get("cost_usd") or {}).get("total") or 0.0)


def _case(payload: dict, name: str) -> dict | None:
    for case in payload.get("cases", []):
        if case.get("name") == name:
            return case
    return None


def analyze() -> dict:
    data = load_results()
    per_run = {}
    grand = {"requests": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    for experiment in EXPERIMENT_ORDER:
        for surface in SURFACES:
            payload = data.get((experiment, surface))
            if payload is None:
                continue
            cases = payload.get("cases", [])
            # Cases carrying ok=True/False are real API calls; the rest are
            # aggregate bookkeeping records (loop summaries, visibility totals).
            api_cases = [c for c in cases if c.get("ok") is True or c.get("ok") is False]
            usage_cases = [c for c in cases if c.get("usage")]
            run = {
                "cases": len(api_cases),
                "requests_ok": sum(1 for c in api_cases if c.get("ok") is True),
                "requests_error": sum(1 for c in api_cases if c.get("ok") is False),
                "input_tokens": sum((_usage(c).get("total_input_tokens") or 0) for c in usage_cases),
                "output_tokens": sum((_usage(c).get("output_tokens") or 0) for c in usage_cases),
                "cost_usd": round(sum(_cost(c) for c in usage_cases), 8),
            }
            per_run[f"{experiment}/{surface}"] = run
            grand["requests"] += run["cases"]
            grand["errors"] += run["requests_error"]
            grand["input_tokens"] += run["input_tokens"]
            grand["output_tokens"] += run["output_tokens"]
            grand["cost_usd"] = round(grand["cost_usd"] + run["cost_usd"], 8)

    metrics: dict = {"ignored_vs_billed": {}, "cache": {}, "visibility": {},
                     "missing_reasoning_errors": {}, "signature": {},
                     "generated_reasoning_output": {}, "multiturn_tool_reasoning": {},
                     "completed_answer_reasoning": {}, "thinking_gate": {},
                     "reasoning_tamper": {}, "intent_injection": {}}

    for surface in SURFACES:
        exp1 = data.get(("exp1_no_tools_multiturn", surface), {})
        exp3 = data.get(("exp3_billing", surface), {})
        exp2 = data.get(("exp2_tools_multistep", surface), {})
        exp4 = data.get(("exp4_visibility", surface), {})

        ignored = _case(exp3, "no_tools_large_reasoning") or _case(exp1, "ignored_block_replayed")
        included = _case(exp3, "tools_large_reasoning") or _case(exp2, "tools_large_reasoning")
        metrics["ignored_vs_billed"][surface] = {
            "no_tools_ignored_block_delta_input_tokens": (ignored or {}).get("delta_input_tokens"),
            "tools_included_block_delta_input_tokens": (included or {}).get("delta_input_tokens"),
            "block_chars": (included or {}).get("large_block_chars"),
            "no_tools_block_chars": (ignored or {}).get("large_block_chars"),
        }

        first = _case(exp3, "cache_first")
        second = _case(exp3, "cache_second")
        if first and second:
            metrics["cache"][surface] = {
                "first_input_tokens": _usage(first).get("total_input_tokens"),
                "first_cost_usd": _cost(first),
                "second_input_tokens": _usage(second).get("total_input_tokens"),
                "second_cost_usd": _cost(second),
                "second_cache_hit_tokens": _usage(second).get("cache_read_input_tokens"),
            }

        tools_summary = _case(exp4, "tools_summary") or {}
        no_tools_summary = _case(exp4, "no_tools_summary") or {}
        metrics["visibility"][surface] = {
            "with_tools_hits": tools_summary.get("hits"),
            "with_tools_trials": tools_summary.get("trials"),
            "no_tools_hits": no_tools_summary.get("hits"),
            "no_tools_trials": no_tools_summary.get("trials"),
            "canary_code": (_case(exp4, "canary_generation") or {}).get("canary_code"),
        }

        missing = _case(exp2, "fabricated_tool_call_missing_reasoning") or {}
        metrics["missing_reasoning_errors"][surface] = {
            "ok": missing.get("ok"),
            "status": missing.get("status"),
            "error": (missing.get("error") or "")[:300],
        }

        if surface == "anthropic":
            metrics["signature"][surface] = {
                name: {
                    "ok": (_case(exp2, name) or {}).get("ok"),
                    "input_tokens": _usage(_case(exp2, name) or {}).get("total_input_tokens"),
                }
                for name in ("signature_signed", "signature_absent", "signature_bogus",
                             "signature_empty", "signature_tampered_text")
            }

        generated = _case(exp3, "generated_reasoning_output") or {}
        metrics["generated_reasoning_output"][surface] = {
            "output_tokens": _usage(generated).get("output_tokens"),
            "reasoning_chars": generated.get("reasoning_chars"),
            "reported_reasoning_tokens": generated.get("reported_reasoning_tokens"),
            "cost_usd": _cost(generated),
        }

        exp5 = data.get(("exp5_multiturn_tool_reasoning", surface), {})

        def exp5_case(name: str) -> dict:
            return _case(exp5, name) or {}

        def exp5_extra(name: str, key: str):
            return exp5_case(name).get(key)

        tools_summary5 = exp5_case("turn2_tools_summary")
        no_tools_summary5 = exp5_case("turn2_no_tools_summary")
        metrics["multiturn_tool_reasoning"][surface] = {
            "turn2_with_tools_hits": tools_summary5.get("hits"),
            "turn2_with_tools_trials": tools_summary5.get("trials"),
            "turn2_no_tools_hits": no_tools_summary5.get("hits"),
            "turn2_no_tools_trials": no_tools_summary5.get("trials"),
            "appended_block_delta_with_tools": exp5_extra(
                "turn2_tools_plus_3k_on_tool_reasoning", "delta_input_tokens"),
            "tool_reasoning_input_tokens_with_tools": exp5_extra(
                "turn2_tools_tool_reasoning_stripped", "delta_vs_natural"),
            "appended_block_delta_no_tools": exp5_extra(
                "turn2_no_tools_plus_3k_on_tool_reasoning", "delta_input_tokens"),
            "tool_reasoning_input_tokens_no_tools": exp5_extra(
                "turn2_no_tools_tool_reasoning_stripped", "delta_vs_natural"),
            "turn2_with_tools_input_tokens": _usage(
                exp5_case("turn2_tools_natural_history")).get("total_input_tokens"),
            "turn2_with_tools_stripped_input_tokens": _usage(
                exp5_case("turn2_tools_tool_reasoning_stripped")).get("total_input_tokens"),
            "cached_repeat_input_tokens": _usage(
                exp5_case("turn2_tools_plus_3k_cached_repeat")).get("total_input_tokens"),
            "cached_repeat_hit_tokens": _usage(
                exp5_case("turn2_tools_plus_3k_cached_repeat")).get("cache_read_input_tokens"),
            "turn3_canary_visible": exp5_case("turn3_tools_replay").get("canary_visible"),
            "turn3_delta_vs_stripped": exp5_extra("turn3_tools_stripped", "delta_vs_replay"),
        }

        exp6 = data.get(("exp6_completed_answer_reasoning", surface), {})

        def exp6_case(name: str) -> dict:
            return _case(exp6, name) or {}

        def exp6_extra(name: str, key: str):
            return exp6_case(name).get(key)

        tools_summary6 = exp6_case("turn2_tools_summary")
        no_tools_summary6 = exp6_case("turn2_no_tools_summary")
        metrics["completed_answer_reasoning"][surface] = {
            "turn2_with_tools_hits": tools_summary6.get("hits"),
            "turn2_with_tools_trials": tools_summary6.get("trials"),
            "turn2_no_tools_hits": no_tools_summary6.get("hits"),
            "turn2_no_tools_trials": no_tools_summary6.get("trials"),
            "appended_block_delta_with_tools": exp6_extra(
                "turn2_tools_plus_3k_on_completed_reasoning", "delta_input_tokens"),
            "completed_reasoning_input_tokens_with_tools": exp6_extra(
                "turn2_tools_completed_reasoning_stripped", "delta_vs_natural"),
            "appended_block_delta_no_tools": exp6_extra(
                "turn2_no_tools_plus_3k_on_completed_reasoning", "delta_input_tokens"),
            "completed_reasoning_input_tokens_no_tools": exp6_extra(
                "turn2_no_tools_completed_reasoning_stripped", "delta_vs_natural"),
            "turn3_canary_visible": exp6_case("turn3_tools_replay").get("canary_visible"),
            "turn3_delta_vs_stripped": exp6_extra("turn3_tools_stripped", "delta_vs_replay"),
            "cached_repeat_input_tokens": _usage(
                exp6_case("turn2_tools_plus_3k_cached_repeat")).get("total_input_tokens"),
            "cached_repeat_hit_tokens": _usage(
                exp6_case("turn2_tools_plus_3k_cached_repeat")).get("cache_read_input_tokens"),
        }

        exp7 = data.get(("exp7_thinking_gate", surface), {})
        metrics["thinking_gate"][surface] = {
            case.get("name"): {
                "ok": case.get("ok"),
                "thinking": case.get("thinking"),
                "carries_tools": case.get("carries_tools"),
                "delta_input_tokens": case.get("delta_input_tokens"),
            }
            for case in exp7.get("cases", [])
        }

        exp8 = data.get(("exp8_reasoning_tamper", surface), {})

        def exp8_case(name: str) -> dict:
            return _case(exp8, name) or {}

        def exp8_picks(name: str) -> list:
            return [answer.get("pick") for answer in (exp8_case(name).get("answers") or [])]

        original8 = exp8_case("original_with_tools_summary")
        modified8 = exp8_case("modified_with_tools_summary")
        stripped8 = exp8_case("stripped_with_tools_summary")
        no_signature8 = exp8_case("modified_no_signature_summary")
        token_original8 = _usage(exp8_case("token_sanity_original")).get("total_input_tokens")
        token_modified8 = _usage(exp8_case("token_sanity_modified")).get("total_input_tokens")
        metrics["reasoning_tamper"][surface] = {
            "original_code": original8.get("expected"),
            "modified_code": modified8.get("expected"),
            "original_hits": original8.get("hits"),
            "original_trials": original8.get("trials"),
            "modified_hits": modified8.get("hits"),
            "modified_trials": modified8.get("trials"),
            "stripped_hits": stripped8.get("hits"),
            "stripped_trials": stripped8.get("trials"),
            "stripped_picks": exp8_picks("stripped_with_tools_summary"),
            "no_tools_picks": exp8_picks("modified_no_tools_summary"),
            "thinking_off_picks": exp8_picks("modified_thinking_off_summary"),
            "no_signature_hits": no_signature8.get("hits"),
            "no_signature_trials": no_signature8.get("trials"),
            "token_original": token_original8,
            "token_modified": token_modified8,
            "token_delta": (None if token_original8 is None or token_modified8 is None
                            else token_modified8 - token_original8),
        }

        exp9 = data.get(("exp9_intent_injection", surface), {})

        def exp9_case(name: str) -> dict:
            return _case(exp9, name) or {}

        metrics["intent_injection"][surface] = {
            "no_intent": exp9_case("no_intent_summary"),
            "intent_exhibition": exp9_case("intent_exhibition_summary"),
            "intent_cooking": exp9_case("intent_cooking_summary"),
            "intent_cooking_no_tools": exp9_case("intent_cooking_no_tools_summary"),
            "intent_cooking_thinking_off": exp9_case("intent_cooking_thinking_off_summary"),
            "token_delta": exp9_case("token_sanity_cooking").get("delta_vs_exhibition"),
        }

    summary = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "model": config.MODEL,
        "effort": config.EFFORT,
        "peak": is_peak_now(),
        "per_run": per_run,
        "grand_total": grand,
        "metrics": metrics,
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
    _write_markdown(summary)
    return summary


def _write_markdown(summary: dict) -> None:
    lines = [
        "# 实验结果汇总（自动生成）",
        "",
        f"- 生成时间：{summary['generated_at']}",
        f"- 模型：{summary['model']}，reasoning effort：{summary['effort']}",
        f"- 当前计费档：{'peak（高峰）' if summary['peak'] else 'off-peak（低谷）'}",
        "",
        "## 每个实验的请求与费用",
        "",
        "| 实验 / 接口 | 用例数 | 成功 | 预期错误 | 输入 tokens | 输出 tokens | 估算费用 USD |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, run in summary["per_run"].items():
        lines.append(
            f"| {key} | {run['cases']} | {run['requests_ok']} | {run['requests_error']} | "
            f"{run['input_tokens']} | {run['output_tokens']} | {run['cost_usd']:.6f} |"
        )
    grand = summary["grand_total"]
    lines += [
        f"| **合计** | {grand['requests']} | - | {grand['errors']} | "
        f"{grand['input_tokens']} | {grand['output_tokens']} | **{grand['cost_usd']:.6f}** |",
        "",
        "## 关键结论指标",
        "",
        "### 无 tools：被忽略的 reasoning 是否计费",
        "",
        "| 接口 | 3.3k token 合成 reasoning 块带来的输入 token 增量 |",
        "|---|---|",
    ]
    for surface, item in summary["metrics"]["ignored_vs_billed"].items():
        lines.append(
            f"| {surface} | {item.get('no_tools_ignored_block_delta_input_tokens')} |"
        )
    lines += [
        "",
        "### 有 tools：reasoning 是否进入上下文并计费",
        "",
        "| 接口 | 3.3k token 合成 reasoning 块带来的输入 token 增量 |",
        "|---|---|",
    ]
    for surface, item in summary["metrics"]["ignored_vs_billed"].items():
        lines.append(
            f"| {surface} | {item.get('tools_included_block_delta_input_tokens')} |"
        )
    lines += [
        "",
        "### 行为侧可见性（同一 canary，重复试验）",
        "",
        "| 接口 | 有 tools 命中 | 无 tools 命中 |",
        "|---|---|---|",
    ]
    for surface, item in summary["metrics"]["visibility"].items():
        lines.append(
            f"| {surface} | {item.get('with_tools_hits')}/{item.get('with_tools_trials')} | "
            f"{item.get('no_tools_hits')}/{item.get('no_tools_trials')} |"
        )
    lines += [
        "",
        "### 多轮后续轮次：前几轮 tool reasoning 是否沿用并计费",
        "",
        "| 接口 | turn2 有 tools 命中 | turn2 无 tools 命中 | 追加 3k 块增量（有 tools） | 工具轮 reasoning 计费 tokens（turn2） | 无 tools 请求增量 | turn3 仍可见 | turn3 仍计费 tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for surface, item in summary["metrics"]["multiturn_tool_reasoning"].items():
        lines.append(
            f"| {surface} | {item.get('turn2_with_tools_hits')}/{item.get('turn2_with_tools_trials')} | "
            f"{item.get('turn2_no_tools_hits')}/{item.get('turn2_no_tools_trials')} | "
            f"{item.get('appended_block_delta_with_tools')} | "
            f"{item.get('tool_reasoning_input_tokens_with_tools')} | "
            f"{item.get('appended_block_delta_no_tools')} | "
            f"{item.get('turn3_canary_visible')} | "
            f"{item.get('turn3_delta_vs_stripped')} |"
        )
    lines += [
        "",
        "### 模型已完成后，新一轮用户提问是否仍带上该轮 reasoning",
        "",
        "| 接口 | 带 tools 命中 | 无 tools 命中 | 追加 3k 块增量（有 tools） | 已完成轮 reasoning 计费 tokens | 无 tools 增量 | turn3 仍可见 | turn3 仍计费 tokens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for surface, item in summary["metrics"]["completed_answer_reasoning"].items():
        lines.append(
            f"| {surface} | {item.get('turn2_with_tools_hits')}/{item.get('turn2_with_tools_trials')} | "
            f"{item.get('turn2_no_tools_hits')}/{item.get('turn2_no_tools_trials')} | "
            f"{item.get('appended_block_delta_with_tools')} | "
            f"{item.get('completed_reasoning_input_tokens_with_tools')} | "
            f"{item.get('appended_block_delta_no_tools')} | "
            f"{item.get('turn3_canary_visible')} | "
            f"{item.get('turn3_delta_vs_stripped')} |"
        )
    lines += [
        "",
        "### thinking 开关：历史 reasoning 的采用条件（2x2）",
        "",
        "| 当前请求 | anthropic 输入增量 | openai 输入增量 |",
        "|---|---|---|",
    ]
    gate_names = ["thinking_on_tools", "thinking_on_no_tools",
                  "thinking_off_tools", "thinking_off_no_tools"]
    gate_labels = {
        "thinking_on_tools": "thinking 开 + 带 tools",
        "thinking_on_no_tools": "thinking 开 + 不带 tools",
        "thinking_off_tools": "thinking 关 + 带 tools",
        "thinking_off_no_tools": "thinking 关 + 不带 tools",
    }
    for name in gate_names:
        a = summary["metrics"]["thinking_gate"].get("anthropic", {}).get(name, {})
        o = summary["metrics"]["thinking_gate"].get("openai", {}).get(name, {})
        lines.append(
            f"| {gate_labels[name]} | {a.get('delta_input_tokens')} | {o.get('delta_input_tokens')} |"
        )
    lines += [
        "",
        "### 手动篡改历史 reasoning 后的下一轮回答（实验 8）",
        "",
        "| 接口 | 原始 reasoning 命中 A | 篡改后命中 B | 去掉 reasoning | 无 tools（篡改后） | thinking 关（篡改后） | 无 signature（篡改后） | 文本长度同，token 差 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for surface, item in summary["metrics"]["reasoning_tamper"].items():
        lines.append(
            f"| {surface} | {item.get('original_hits')}/{item.get('original_trials')} | "
            f"{item.get('modified_hits')}/{item.get('modified_trials')} | "
            f"{item.get('stripped_hits')}/{item.get('stripped_trials')} | "
            f"{item.get('no_tools_picks')} | {item.get('thinking_off_picks')} | "
            f"{item.get('no_signature_hits')}/{item.get('no_signature_trials')} | "
            f"{item.get('token_delta')} |"
        )
    lines += [
        "",
        "### 仅在 reasoning 中注入意图，开放问题是否体现（实验 9）",
        "",
        "| 接口 | 无意图（基线） | 注入“看展”意图 | 注入“做饭”意图 | 做饭意图 + 无 tools | 做饭意图 + thinking 关 | 两句意图的 token 差 |",
        "|---|---|---|---|---|---|---|",
    ]
    for surface, item in summary["metrics"]["intent_injection"].items():
        def brief(case: dict) -> str:
            return (f"看展 {case.get('exhibition_hits', 0)}/{case.get('trials')}，"
                    f"做饭 {case.get('cooking_hits', 0)}/{case.get('trials')}，"
                    f"户外 {case.get('outdoor_hits', 0)}/{case.get('trials')}")
        lines.append(
            f"| {surface} | {brief(item.get('no_intent') or {})} | "
            f"{brief(item.get('intent_exhibition') or {})} | "
            f"{brief(item.get('intent_cooking') or {})} | "
            f"{brief(item.get('intent_cooking_no_tools') or {})} | "
            f"{brief(item.get('intent_cooking_thinking_off') or {})} | "
            f"{item.get('token_delta')} |"
        )
    lines += [
        "",
        "### 缺失 reasoning 的 tool_calls turn（伪造的冷历史）",
        "",
        "| 接口 | HTTP | 错误信息 |",
        "|---|---|---|",
    ]
    for surface, item in summary["metrics"]["missing_reasoning_errors"].items():
        error = (item.get("error") or "").replace("|", "/").replace("\n", " ")[:160]
        lines.append(f"| {surface} | {item.get('status')} | {error} |")
    lines += [
        "",
        "### Anthropic thinking signature 是否校验",
        "",
        "| 变体 | 请求成功 | 总输入 tokens |",
        "|---|---|---|",
    ]
    for name, item in summary["metrics"]["signature"].get("anthropic", {}).items():
        lines.append(f"| {name} | {item.get('ok')} | {item.get('input_tokens')} |")
    lines += [
        "",
        "### 生成的 reasoning（输出侧）计费",
        "",
        "| 接口 | 输出 tokens | reasoning 字符数 | 上报 reasoning tokens | 费用 USD |",
        "|---|---|---|---|---|",
    ]
    for surface, item in summary["metrics"]["generated_reasoning_output"].items():
        lines.append(
            f"| {surface} | {item.get('output_tokens')} | {item.get('reasoning_chars')} | "
            f"{item.get('reported_reasoning_tokens')} | {item.get('cost_usd'):.6f} |"
        )
    lines += [
        "",
        "### Prompt cache 对 reasoning 前缀的影响",
        "",
        "| 接口 | 首次输入 | 首次费用 USD | 二次输入 | 二次命中 | 二次费用 USD |",
        "|---|---|---|---|---|---|",
    ]
    for surface, item in summary["metrics"]["cache"].items():
        lines.append(
            f"| {surface} | {item.get('first_input_tokens')} | {item.get('first_cost_usd'):.6f} | "
            f"{item.get('second_input_tokens')} | {item.get('second_cache_hit_tokens')} | "
            f"{item.get('second_cost_usd'):.6f} |"
        )
    lines.append("")

    with open(os.path.join(RESULTS_DIR, "SUMMARY.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


if __name__ == "__main__":
    result = analyze()
    print(json.dumps(result["grand_total"], ensure_ascii=False, indent=2))
    print(f"wrote {os.path.join(RESULTS_DIR, 'summary.json')}")
    print(f"wrote {os.path.join(RESULTS_DIR, 'SUMMARY.md')}")
