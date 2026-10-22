#!/usr/bin/env python3
"""Run all four experiments on both wire surfaces, then aggregate the results.

Usage:
    python3 run_all.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import requests

import config
from analyze_results import analyze
from probes import RESULTS_DIR, is_peak_now

BALANCE_URL = config.OPENAI_BASE_URL.rstrip("/") + "/user/balance"
EXPERIMENTS = [
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


def balance() -> dict:
    try:
        response = requests.get(
            BALANCE_URL,
            headers={"Authorization": f"Bearer {config.API_KEY}"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        infos = payload.get("balance_infos") or []
        return {"raw": payload, "cny": infos[0].get("total_balance") if infos else None}
    except Exception as error:  # balance is a nice-to-have, never fail the suite
        return {"error": str(error)}


def main() -> None:
    before = balance()
    started = time.time()
    here = os.path.dirname(os.path.abspath(__file__))
    for experiment in EXPERIMENTS:
        for surface in SURFACES:
            print(f"\n===== {experiment} / {surface} =====")
            subprocess.run(
                [sys.executable, os.path.join(here, f"{experiment}.py"), surface],
                check=True, cwd=here,
            )

    summary = analyze()
    summary["peak"] = is_peak_now()
    summary["balance"] = {
        "before": before,
        "after": balance(),
        "elapsed_seconds": round(time.time() - started, 1),
    }
    path = os.path.join(RESULTS_DIR, "summary.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("total estimated cost USD:", summary["grand_total"]["cost_usd"])
    print("balance before:", before.get("cny"), "CNY")
    print("balance after :", summary["balance"]["after"].get("cny"), "CNY")
    print("summary:", path)


if __name__ == "__main__":
    main()
