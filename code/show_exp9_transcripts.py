#!/usr/bin/env python3
"""Render one complete transcript per exp9 setting (Anthropic / DSH wire format)."""
import json
import os

import exp9_intent_injection as exp9
import probes
from probes import new_system

OUT = os.path.join(probes.RESULTS_DIR, "exp9_transcripts.md")
FENCE = chr(96) * 3
TICK = chr(96)


def render(surface, setting_label, intent, use_tools, thinking, gen, system):
    reasoning_turn1 = gen["reasoning"] if intent is None else gen["reasoning"] + "\n\n" + intent
    signature = surface.signature_of(gen["outcome"].result)
    history = [
        surface.user(gen["prompt"]),
        surface.assistant(thinking=reasoning_turn1, signature=signature, text=gen["answer"]),
    ]
    outcome = exp9.call_with_flags(
        surface, history + [surface.user(exp9.ASK)],
        tools=surface.tools_payload(["get_weather"]) if use_tools else None,
        system=system, thinking=thinking, max_tokens=1024,
    )
    if not outcome.ok:
        raise RuntimeError(f"{setting_label}: {outcome.status} {outcome.body}")
    verdict = exp9.classify(outcome.result.text)
    return {
        "setting": setting_label,
        "injected_intent": intent,
        "carries_tools": use_tools,
        "thinking_enabled": thinking,
        "system": system,
        "turn1_user": gen["prompt"],
        "turn1_thinking": reasoning_turn1,
        "turn1_answer": gen["answer"],
        "turn2_user": exp9.ASK,
        "turn2_thinking": outcome.result.thinking,
        "turn2_answer": outcome.result.text,
        "category": verdict["label"],
        "counts": verdict["counts"],
        "usage": outcome.result.usage.as_dict(),
        "stop_reason": outcome.result.stop_reason,
    }


def block(title, value):
    return f"**{title}**\n\n{FENCE}text\n{value}\n{FENCE}\n"


def inline(value):
    return f"{TICK}{value}{TICK}"


def main():
    surface = probes.make_surface("anthropic")
    system = new_system("exp9-transcript")
    gen = exp9.generate_turn1(surface, system)

    settings = [
        ("A. 无意图（基线）", None, True, True),
        ("B. 注入“看展”意图", exp9.INTENT_EXHIBITION, True, True),
        ("C. 注入“做饭”意图", exp9.INTENT_COOKING, True, True),
        ("D. 做饭意图 + 当前请求不带 tools", exp9.INTENT_COOKING, False, True),
        ("E. 做饭意图 + thinking 关闭", exp9.INTENT_COOKING, True, False),
    ]

    lines = [
        "# 实验 9：五种设置各一段完整对话",
        "",
        "以下全部是真实 API 调用（Anthropic Messages / DSH 实际使用的接口，deepseek-flash，effort=high）。",
        "第 1 轮是同一个中性回答；只有第 1 轮 assistant 的 reasoning 文本在设置之间不同。",
        "第 2 轮用户问题完全相同，均不含任何活动类别词。",
        "",
    ]
    records = []
    for label, intent, use_tools, thinking in settings:
        record = render(surface, label, intent, use_tools, thinking, gen, system)
        records.append(record)
        lines.append(f"## {label}")
        lines.append("")
        lines.append(f"- 当前请求带 tools：{inline('是' if use_tools else '否')}；thinking：{inline('开启' if thinking else '关闭')}")
        lines.append(f"- 第 2 轮回答分类：**{record['category']}**（关键词计数 {record['counts']}）")
        lines.append(f"- 用量：{inline(json.dumps(record['usage'], ensure_ascii=False))}，stop={record['stop_reason']}")
        lines.append("")
        lines.append(f"**System**：{inline(system)}")
        lines.append("")
        lines.append(block("Turn 1 · user", record["turn1_user"]))
        turn1_note = record["turn1_thinking"]
        if intent is not None:
            turn1_note = turn1_note + "\n\n[↑ 最后一段就是本设置注入的意图句，仅存在于 reasoning]"
        lines.append(block("Turn 1 · assistant · reasoning (thinking)", turn1_note))
        lines.append(block("Turn 1 · assistant · 可见回答", record["turn1_answer"]))
        lines.append(block("Turn 2 · user", record["turn2_user"]))
        if thinking:
            lines.append(block("Turn 2 · assistant · reasoning (thinking)", record["turn2_thinking"] or "(空)"))
        else:
            lines.append(block("Turn 2 · assistant · reasoning (thinking)", "(thinking 关闭，没有 reasoning)"))
        lines.append(block("Turn 2 · assistant · 可见回答", record["turn2_answer"]))
        lines.append("---")
        lines.append("")

    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print("wrote", OUT)
    print("cases:", [(r["setting"], r["category"]) for r in records])


if __name__ == "__main__":
    main()
