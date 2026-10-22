#!/usr/bin/env python3
"""Experiment 2 -- requests WITH tools, multi-step tool loop inside one user turn.

Question: with "tools" present, is replaying reasoning required/used, and what
happens when it is dropped? Covers the same-turn multi-step agent loop
(assistant tool_use -> tool_result -> assistant tool_use -> tool_result -> answer)
plus multi-turn continuation, cold (session-resume-like) history, fabricated
history, and Anthropic thinking signatures.
"""

from __future__ import annotations

import secrets

import probes
from probes import Recorder, call, gen_canary, new_system

PROMPT = (
    "不要向我确认，直接开始。请先调用 get_weather 查询 Paris 明天的天气；"
    "收到结果后，再调用 get_weather 查询 Hangzhou 明天的天气；最后用一句话总结两座城市。"
    "每次回复只调用一个工具。"
)
FOLLOW_UP = "再补充一句：这两座城市里，哪一座更热？"


def run(surface: probes.Surface) -> None:
    recorder = Recorder("exp2_tools_multistep", surface)
    tools = surface.tools_payload(["get_weather"])
    system = new_system("tool-loop")

    # -- same-turn multi-step loop, replaying reasoning at every step --------
    messages = [surface.user(PROMPT)]
    messages_without_reasoning = [surface.user(PROMPT)]
    assistant_results = []
    tool_steps = 0
    final_outcome = None
    for step in range(4):
        outcome = call(surface, messages, tools=tools, system=system, max_tokens=2048)
        calls = surface.calls_of(outcome.result) if outcome.ok and outcome.result else []
        recorder.add(
            f"step{step + 1}", outcome=outcome,
            extra={"tool_calls": [{"name": c["name"], "input": c["input"]} for c in calls]},
        )
        if not outcome.ok:
            break
        messages.append(surface.assistant_from_result(outcome.result))
        messages_without_reasoning.append(
            surface.assistant_from_result(outcome.result, include_reasoning=False)
        )
        if not calls:
            final_outcome = outcome
            break
        assistant_results.append(outcome.result)
        tool_steps += 1
        results = surface.tool_results(calls)
        messages.extend(results)
        messages_without_reasoning.extend(results)
    recorder.add("loop_summary", extra={
        "tool_steps": tool_steps,
        "final_text": final_outcome.result.text if final_outcome is not None else None,
    })

    # -- turn 2 of the same conversation, still carrying tools ---------------
    if final_outcome is not None and final_outcome.result.text.strip():
        follow_up = call(
            surface, messages + [surface.user(FOLLOW_UP)],
            tools=tools, system=system, max_tokens=2048,
        )
        recorder.add("followup_turn_with_tools_replaying_reasoning", outcome=follow_up)
        follow_up_drop = call(
            surface, messages_without_reasoning + [surface.user(FOLLOW_UP)],
            tools=tools, system=new_system("follow-drop"), max_tokens=2048,
        )
        recorder.add("followup_turn_with_tools_dropping_reasoning", outcome=follow_up_drop)

    # -- drop the step-1 reasoning: warm vs cold -----------------------------
    if assistant_results:
        first = assistant_results[0]
        first_calls = surface.calls_of(first)
        no_reason = surface.assistant_from_result(first, include_reasoning=False)
        base = [surface.user(PROMPT), no_reason] + surface.tool_results(first_calls)
        warm = call(surface, base, tools=tools, system=system, max_tokens=2048)
        recorder.add("drop_step1_reasoning_warm", outcome=warm,
                     notes="same prefix, server retains the reasoning for a fresh call id")
        cold = call(surface, base, tools=tools, system=new_system("cold-real-id"), max_tokens=2048)
        recorder.add("drop_step1_reasoning_cold_real_id", outcome=cold,
                     notes="different system prefix but a real server-issued tool id")

    # -- fabricated tool call: server has never seen the call -----------------
    fake_id = ("toolu_" if surface.name == "anthropic" else "call_") + secrets.token_hex(8)
    fake_call = {"id": fake_id, "name": "get_weather",
                 "input": {"city": "Paris", "date": "2026-10-23"}}
    base_hist = [
        surface.user("What is the weather in Paris? Use the get_weather tool."),
        surface.assistant(tool_calls=[fake_call]),
        *surface.tool_results([fake_call]),
    ]
    missing = call(surface, base_hist, tools=tools, system=new_system("fake"), max_tokens=1024)
    recorder.add("fabricated_tool_call_missing_reasoning", outcome=missing,
                 notes="documented 400 case: server cannot recover the reasoning")

    keep = list(base_hist)
    keep[1] = surface.assistant(thinking="I need to call get_weather for Paris.",
                                tool_calls=[fake_call])
    with_reasoning = call(surface, keep, tools=tools, system=new_system("fake"), max_tokens=1024)
    recorder.add("fabricated_tool_call_with_reasoning", outcome=with_reasoning)

    empty = list(base_hist)
    empty[1] = surface.assistant(thinking="", tool_calls=[fake_call])
    empty_reasoning = call(surface, empty, tools=tools, system=new_system("fake"), max_tokens=1024)
    recorder.add("fabricated_tool_call_empty_reasoning", outcome=empty_reasoning)

    # -- plain assistant turn (no tool call) inside a tool-carrying request --
    plain = call(
        surface,
        [surface.user("1+1 等于几？只回答数字。"),
         surface.assistant(text="2"),
         surface.user("2+2 呢？")],
        tools=tools, system=new_system("plain"), max_tokens=512,
    )
    recorder.add("plain_turn_with_tools_missing_reasoning", outcome=plain,
                 notes="tools present, no tool call in history, reasoning omitted")

    # -- how much does a tool-carrying request charge for reasoning? ---------
    pair_system = new_system("tools-pair")
    control = call(surface,
                   [surface.user("Say OK."), surface.assistant(text="OK"),
                    surface.user("Say OK again.")],
                   tools=tools, system=pair_system)
    with_block = call(surface,
                      [surface.user("Say OK."),
                       surface.assistant(thinking=probes.LARGE_BLOCK, text="OK"),
                       surface.user("Say OK again.")],
                      tools=tools, system=pair_system, max_tokens=1024)
    delta = None
    if control.ok and with_block.ok:
        delta = (with_block.result.usage.total_input_tokens
                 - control.result.usage.total_input_tokens)
    recorder.add("tools_control", outcome=control)
    recorder.add("tools_large_reasoning", outcome=with_block,
                 extra={"delta_input_tokens": delta, "large_block_chars": len(probes.LARGE_BLOCK)})

    # -- Anthropic only: is the signature checked? --------------------------
    # Same 3.3k-token thinking under four signature shapes. If the signature
    # were validated, a bogus/absent/tampered one would be rejected or ignored
    # (and the input token count would drop).
    if surface.name == "anthropic":
        signature = gen_canary(surface)["signature"]
        signature_system = new_system("sig")
        for label, thinking, sig in (
            ("signature_signed", probes.LARGE_BLOCK, signature),
            ("signature_absent", probes.LARGE_BLOCK, None),
            ("signature_bogus", probes.LARGE_BLOCK, "A" * 36),
            ("signature_empty", probes.LARGE_BLOCK, ""),
            ("signature_tampered_text", probes.LARGE_BLOCK + " TAMPERED", signature),
        ):
            outcome = call(
                surface,
                [surface.user("Say OK."),
                 surface.assistant(thinking=thinking, signature=sig, text="OK"),
                 surface.user("Say OK again.")],
                tools=tools, system=signature_system, max_tokens=512,
            )
            recorder.add(label, outcome=outcome)

    recorder.save()


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "anthropic"
    run(probes.make_surface(name))
