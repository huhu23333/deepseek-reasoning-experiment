#!/usr/bin/env python3
"""Offline re-analysis of results/exp9_parallel raw JSONL.

No API calls: re-classifies the stored answer text, separates truncated and
tool-call responses, and writes a refined probability matrix plus statistics.
"""

from __future__ import annotations

import glob
import json
import math
import os
import re
from datetime import datetime

import config
import probes

BASE = os.path.join(probes.RESULTS_DIR, "exp9_parallel")
SETTINGS = ["no_intent", "intent_exhibition", "intent_cooking",
            "intent_cooking_no_tools", "intent_cooking_thinking_off"]
SURFACES = ["anthropic", "openai"]

EXHIBITION = ["展览", "展覽", "博物馆", "博物館", "美术馆", "美術館", "博物院", "看展", "逛展",
              "艺术展", "藝術展", "话剧", "話劇", "剧院", "劇院", "剧场", "劇場", "故宫", "故宮",
              "特展", "常设展", "常設展", "看一场展", "看一場展"]
COOKING = ["做饭", "做飯", "下厨", "下廚", "烘焙", "烹饪", "烹飪", "手工", "收纳", "收納", "整理",
           "烤", "厨房", "廚房", "面团", "麵團", "曲奇", "面包", "麵包", "饼干", "餅乾", "蛋糕",
           "做菜", "一道菜", "一顿", "一頓", "食材", "烤箱", "甜品", "炖", "燉", "煮", "炒", "煎",
           "饺子", "餃子", "手擀面", "手擀麵", "家常菜", "菜谱", "菜譜", "牛腩", "红烧", "紅燒",
           "咖喱", "面条", "麵條", "晚餐", "午餐", "早餐", "一锅", "一鍋", "一炉", "一爐", "菜"]
OUTDOOR = ["公园", "公園", "散步", "徒步", "爬山", "登山", "骑行", "騎行", "露营", "露營", "户外", "戶外",
           "郊野", "远足", "遠足", "山", "湖", "海边", "海邊", "跑步", "慢跑", "颐和园", "頤和園", "出游", "出遊"]
ALL = {"exhibition": EXHIBITION, "cooking": COOKING, "outdoor": OUTDOOR}

MENTION_RE = re.compile(
    r"preference|prior|previous|injected|hidden|instruction|self-imposed|"
    r"已经决定|已經決定|优先推荐|優先推薦|优先考虑|優先考慮|偏好|之前的决定|之前的決定",
    re.IGNORECASE,
)


def classify(text: str, stop_reason: str | None):
    if stop_reason in ("max_tokens", "length") and not text.strip():
        return "no_answer", {}
    if stop_reason in ("tool_use", "tool_calls"):
        return "tool_call", {}
    if not text.strip():
        return "no_answer", {}
    counts = {name: sum(text.count(word) for word in words) for name, words in ALL.items()}
    best = max(counts.values())
    winners = [name for name, value in counts.items() if value == best and value > 0]
    if len(winners) == 1:
        return winners[0], counts
    return "other", counts


def wilson_ci(k: int, n: int, z: float = 1.959963984540054):
    if n == 0:
        return (None, None)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def two_prop(k1: int, n1: int, k2: int, n2: int):
    p1, p2 = k1 / n1, k2 / n2
    diff = p1 - p2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    z = diff / se if se > 0 else 0.0
    p_value = 2 * (1 - norm_cdf(abs(z)))
    return {"p1": p1, "p2": p2, "diff": diff, "se": se,
            "ci95": (diff - 1.959963984540054 * se, diff + 1.959963984540054 * se),
            "z": z, "p_value": p_value}


def load():
    data = {}
    for surface in SURFACES:
        for setting in SETTINGS:
            path = os.path.join(BASE, f"{surface}_{setting}.jsonl")
            records = [json.loads(line) for line in open(path, encoding="utf-8")]
            data[(surface, setting)] = records
    return data


def main():
    data = load()
    with open(os.path.join(BASE, "metadata.json"), encoding="utf-8") as handle:
        metadata = json.load(handle)
    peak = metadata.get("peak", False)
    factor = 1.0 if peak else 0.5
    prices = probes.PRICES_PEAK_USD_PER_M

    rows = []
    detailed = []
    for surface in SURFACES:
        for setting in SETTINGS:
            records = data[(surface, setting)]
            labels = []
            mentions = 0
            usage = {"input": 0, "hit": 0, "output": 0}
            for record in records:
                label, counts = classify(record["response"]["text"],
                                         record["response"].get("stop_reason"))
                labels.append(label)
                u = record["response"]["usage"]
                usage["input"] += u.get("total_input_tokens") or 0
                usage["hit"] += u.get("cache_read_input_tokens") or 0
                usage["output"] += u.get("output_tokens") or 0
                thinking = record["response"].get("thinking") or ""
                if thinking and MENTION_RE.search(thinking):
                    mentions += 1
            total = len(labels)
            counts = {name: labels.count(name) for name in
                      ["exhibition", "cooking", "outdoor", "other", "no_answer", "tool_call"]}
            valid = total - counts["no_answer"] - counts["tool_call"]
            miss = max(0, usage["input"] - usage["hit"])
            cost = (miss * prices["cache_miss"] + usage["hit"] * prices["cache_hit"]
                    + usage["output"] * prices["output"]) * factor / 1e6
            row = {
                "surface": surface, "setting": setting, "total": total,
                "valid_answers": valid, **counts,
                "p_exhibition": counts["exhibition"] / valid if valid else None,
                "p_cooking": counts["cooking"] / valid if valid else None,
                "p_outdoor": counts["outdoor"] / valid if valid else None,
                "p_other": counts["other"] / valid if valid else None,
                "p_no_answer": counts["no_answer"] / total,
                "p_tool_call": counts["tool_call"] / total,
                "intent_mentioned_in_thinking": mentions,
                "p_intent_mentioned": mentions / total,
                "cost_usd": round(cost, 6),
            }
            rows.append(row)
            detailed.append((surface, setting, labels, records))

    with open(os.path.join(BASE, "probability_matrix_refined.json"), "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)

    import csv
    with open(os.path.join(BASE, "probability_matrix_refined.csv"), "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# 实验 9 并行版：概率矩阵（原文离线重分类）",
        "",
        f"- 生成时间：{datetime.now().astimezone().isoformat()}",
        f"- 模型：{config.MODEL}，effort={config.EFFORT}；每格 1000 次；计费档：{'peak' if peak else 'off-peak'}",
        "- 分母 = 有效回答数（剔除 max_tokens 截断的空回答 no_answer 和 tool_call）",
        "- 分类基于保存的原始回答文本；繁体与扩展词已覆盖",
        "",
        "| 接口 | 设置 | 有效回答 | exhibition | cooking | outdoor | other | no_answer | tool_call | 提及意图的思考占比 | 费用 USD |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['surface']} | {row['setting']} | {row['valid_answers']} | "
            f"{row['exhibition']} ({row['p_exhibition']:.3f}) | "
            f"{row['cooking']} ({row['p_cooking']:.3f}) | "
            f"{row['outdoor']} ({row['p_outdoor']:.3f}) | "
            f"{row['other']} ({row['p_other']:.3f}) | "
            f"{row['no_answer']} ({row['p_no_answer']:.3f}) | "
            f"{row['tool_call']} ({row['p_tool_call']:.3f}) | "
            f"{row['p_intent_mentioned']:.3f} | {row['cost_usd']:.4f} |"
        )
    lines.append("")

    # statistics: baseline vs intents, plus Wilson CIs for key cells
    stats_lines = [
        "# 并行实验统计",
        "",
        "## 关键概率及 Wilson 95% 置信区间",
        "",
        "| 接口 | 设置 | 类别 | 次数/有效 | 概率 | 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        for category, column in (("exhibition", "exhibition"), ("cooking", "cooking"),
                                 ("outdoor", "outdoor")):
            k = row[column]
            n = row["valid_answers"]
            ci = wilson_ci(k, n)
            stats_lines.append(
                f"| {row['surface']} | {row['setting']} | {category} | {k}/{n} | "
                f"{k / n:.4f} | [{ci[0]:.4f}, {ci[1]:.4f}] |"
            )
    stats_lines.append("")
    stats_lines.append("## 与基线的对比（两比例 z 检验）")
    stats_lines.append("")
    stats_lines.append("| 接口 | 对比 | 差值 | 95% CI | p |")
    stats_lines.append("|---|---|---|---|---|")
    for surface in SURFACES:
        base_row = next(r for r in rows if r["surface"] == surface and r["setting"] == "no_intent")
        for setting, category in (("intent_exhibition", "exhibition"),
                                  ("intent_cooking", "cooking")):
            row = next(r for r in rows if r["surface"] == surface and r["setting"] == setting)
            test = two_prop(row[category], row["valid_answers"],
                            base_row[category], base_row["valid_answers"])
            stats_lines.append(
                f"| {surface} | {setting} vs no_intent ({category}) | {test['diff']:+.4f} | "
                f"[{test['ci95'][0]:+.4f}, {test['ci95'][1]:+.4f}] | {test['p_value']:.2e} |"
            )
        for control in ("intent_cooking_no_tools", "intent_cooking_thinking_off"):
            row = next(r for r in rows if r["surface"] == surface and r["setting"] == control)
            test = two_prop(row["cooking"], row["valid_answers"],
                            base_row["cooking"], base_row["valid_answers"])
            stats_lines.append(
                f"| {surface} | {control} vs no_intent (cooking) | {test['diff']:+.4f} | "
                f"[{test['ci95'][0]:+.4f}, {test['ci95'][1]:+.4f}] | {test['p_value']:.2e} |"
            )
    stats_lines += [
        "",
        "## 机制佐证：回答的隐藏 thinking 是否提到被注入的意图",
        "",
        "| 接口 | 设置 | 提及次数/1000 | 比例 |",
        "|---|---|---|---|",
    ]
    for row in rows:
        stats_lines.append(
            f"| {row['surface']} | {row['setting']} | {row['intent_mentioned_in_thinking']}/1000 | "
            f"{row['p_intent_mentioned']:.3f} |"
        )
    stats_lines += ["", "## 典型意图提及片段（intent_exhibition / intent_cooking）", ""]
    for surface in SURFACES:
        for setting in ("intent_exhibition", "intent_cooking"):
            records = data[(surface, setting)]
            examples = []
            for record in records:
                thinking = record["response"].get("thinking") or ""
                if thinking and MENTION_RE.search(thinking):
                    examples.append(thinking.replace("\n", " ")[:220])
                if len(examples) >= 2:
                    break
            stats_lines.append(f"- {surface}/{setting}:")
            for example in examples:
                stats_lines.append(f"  - {example}")
    stats_lines.append("")

    with open(os.path.join(BASE, "statistics.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(stats_lines))
    with open(os.path.join(BASE, "probability_matrix_refined.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print("wrote refined matrix and statistics")
    for row in rows:
        print(row["surface"], row["setting"], "valid", row["valid_answers"],
              "exh", round(row["p_exhibition"], 3), "cook", round(row["p_cooking"], 3),
              "out", round(row["p_outdoor"], 3), "other", round(row["p_other"], 3),
              "noans", row["no_answer"], "tool", row["tool_call"],
              "mention", round(row["p_intent_mentioned"], 3))
    print("total cost USD:", round(sum(r["cost_usd"] for r in rows), 6))


if __name__ == "__main__":
    main()
