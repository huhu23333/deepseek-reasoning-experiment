#!/usr/bin/env python3
"""Experiment 5 -- later turns: is an earlier tool-call turn's reasoning reused and billed?

Scenario (a natural multi-turn conversation that carries tools):

  user turn 1  -> assistant reasons (canary in reasoning) + calls get_weather
               -> tool result -> assistant final answer
  user turn 2  -> ask the model to repeat the canary from turn 1's tool-call reasoning
  user turn 3  -> ask again

The whole turn 1 is regenerated until the canary appears ONLY in the reasoning of
the tool-call turn: no code in the tool-call turn's text, no code in the final
answer. Otherwise later recall would not prove that the replayed reasoning was
actually reused.

The same history is then sent at turn 2 in three shapes:
  * current request carries tools  -> the earlier tool-turn reasoning is reused
  * current request has no tools   -> the same reasoning should be ignored
  * a ~3k-token block appended to the earlier tool-call reasoning, paired against
    the natural history, isolates the exact billed input-token delta.
"""

from __future__ import annotations

import re
import secrets

import probes
from probes import ASK_CANARY_ASSERTIVE, LARGE_BLOCK, Recorder, call, find_code, new_system, visible

TURN1_PROMPT = (
    "这是会话第 1 轮。请先在你的思考/reasoning 中生成一个 8 位十六进制随机串，"
    "按格式写下：暗号=XXXXXXXX。然后调用 get_weather 查询 Paris 明天的天气；"
    "拿到工具结果后，用一句话总结。最终回答里不要再出现暗号，也不要出现任何 8 位十六进制串。"
)
TURN2_ASK = ASK_CANARY_ASSERTIVE
TURN3_ASK = (
    "再次确认：请输出你第 1 轮调用工具前思考里的那个 8 位十六进制暗号；"
    "看不到就输出 UNKNOWN。"
)
TURN2_EXTRA = "再补充一问：Paris 和 Hangzhou 明天哪里的天气更好？请简要回答。"
HEX8_RE = re.compile(r"\b[0-9A-Fa-f]{8}\b")
CODE_RE = re.compile(r"暗号\s*[=＝:：]\s*([0-9A-Fa-f]{6,12})")


def generate_turn1(surface: probes.Surface, tools, system: str) -> dict:
    """Regenerate the whole first user turn until the canary stays hidden."""
    last_reason = None
    for attempt in range(10):
        prompt = TURN1_PROMPT + "（会话标记 " + secrets.token_hex(3) + "）"
        first = call(surface, [surface.user(prompt)], tools=tools,
                     system=system, max_tokens=2048)
        if not first.ok:
            last_reason = f"status {first.status}"
            continue
        reasoning = surface.reasoning_of(first.result)
        calls = surface.calls_of(first.result)
        codes = {m.upper() for m in CODE_RE.findall(reasoning)}
        if not codes or len(codes) > 1 or not calls:
            last_reason = f"codes={codes} calls={len(calls)}"
            continue
        code = codes.pop()
        if code.lower() in first.result.text.lower() or "暗号" in first.result.text:
            last_reason = "canary leaked into tool-call turn text"
            continue

        messages1 = [surface.user(prompt), surface.assistant_from_result(first.result)]
        messages1.extend(surface.tool_results(calls))
        final1 = call(surface, messages1, tools=tools, system=system, max_tokens=2048)
        if not final1.ok:
            last_reason = f"final status {final1.status}"
            continue
        final_text = final1.result.text
        if (code.lower() in final_text.lower() or "暗号" in final_text
                or HEX8_RE.search(final_text)):
            last_reason = "canary leaked into the final answer"
            continue

        history = messages1 + [surface.assistant_from_result(final1.result)]
        return {
            "prompt": prompt, "first": first, "final1": final1, "code": code,
            "calls": calls, "reasoning": reasoning,
            "signature": surface.signature_of(first.result),
            "history": history, "attempt": attempt + 1,
        }
    raise RuntimeError(f"{surface.name}: could not build a clean turn 1 ({last_reason})")


def run(surface: probes.Surface) -> None:
    recorder = Recorder("exp5_multiturn_tool_reasoning", surface)
    tools = surface.tools_payload(["get_weather"])
    system = new_system("exp5-conversation")

    # -- user turn 1: reason (canary) + tool call, then final answer ----------
    gen = generate_turn1(surface, tools, system)
    code = gen["code"]
    recorder.add("turn1_tool_call", outcome=gen["first"], extra={
        "canary_code": code,
        "attempt": gen["attempt"],
        "tool_calls": [{"name": c["name"], "input": c["input"]} for c in gen["calls"]],
    }, notes="canary lives only in the reasoning of the tool-call turn")
    recorder.add("turn1_final_answer", outcome=gen["final1"],
                 notes="checked: no code leaked into the visible final answer")
    history = gen["history"]

    # -- user turn 2: is the earlier tool-turn reasoning visible? -----------
    def visibility_trials(label: str, use_tools: bool, trials: int) -> dict | None:
        hits = 0
        last_ok = None
        for trial in range(trials):
            outcome = call(
                surface,
                history + [surface.user(TURN2_ASK)],
                tools=tools if use_tools else None,
                system=system, max_tokens=1024,
            )
            hit = visible(outcome, code)
            hits += 1 if hit else 0
            recorder.add(f"{label}_trial{trial + 1}", outcome=outcome,
                         extra={"canary_visible": hit})
            if outcome.ok:
                last_ok = outcome
        recorder.add(f"{label}_summary",
                     extra={"hits": hits, "trials": trials, "canary_visible": hits > 0})
        return last_ok

    last_with_tools = visibility_trials("turn2_tools", True, 2)
    visibility_trials("turn2_no_tools", False, 2)

    # -- billing: append a 3k-token block to the earlier tool-turn reasoning --
    assistant1_natural = surface.assistant_from_result(gen["first"].result)
    assistant1_big = surface.assistant(
        thinking=gen["reasoning"] + "\n\n" + LARGE_BLOCK,
        signature=gen["signature"],
        text=gen["first"].result.text,
        tool_calls=gen["calls"],
    )
    assistant1_stripped = surface.assistant_from_result(gen["first"].result,
                                                        include_reasoning=False)
    final_assistant = surface.assistant_from_result(gen["final1"].result)

    def history_with(assistant1) -> list[dict]:
        return [surface.user(gen["prompt"]), assistant1,
                *surface.tool_results(gen["calls"]), final_assistant]

    question = [surface.user(TURN2_EXTRA)]
    pair_system = system  # same conversation prefix; only the tool reasoning differs
    natural_t = call(surface, history_with(assistant1_natural) + question,
                     tools=tools, system=pair_system, max_tokens=512)
    big_t = call(surface, history_with(assistant1_big) + question,
                 tools=tools, system=pair_system, max_tokens=512)
    stripped_t = call(surface, history_with(assistant1_stripped) + question,
                      tools=tools, system=pair_system, max_tokens=512)
    delta_tools = None
    if natural_t.ok and big_t.ok:
        delta_tools = (big_t.result.usage.total_input_tokens
                       - natural_t.result.usage.total_input_tokens)
    reasoning_tokens = None
    if natural_t.ok and stripped_t.ok:
        reasoning_tokens = (natural_t.result.usage.total_input_tokens
                            - stripped_t.result.usage.total_input_tokens)
    recorder.add("turn2_tools_natural_history", outcome=natural_t,
                 notes="current request carries tools")
    recorder.add("turn2_tools_plus_3k_on_tool_reasoning", outcome=big_t,
                 extra={"delta_input_tokens": delta_tools,
                        "large_block_chars": len(LARGE_BLOCK)},
                 notes="exact billing delta for an earlier tool-turn reasoning block")
    recorder.add("turn2_tools_tool_reasoning_stripped", outcome=stripped_t,
                 extra={"delta_vs_natural": reasoning_tokens})

    # same pair, but the current request does NOT carry tools
    natural_nt = call(surface, history_with(assistant1_natural) + question,
                      tools=None, system=pair_system, max_tokens=512)
    big_nt = call(surface, history_with(assistant1_big) + question,
                  tools=None, system=pair_system, max_tokens=512)
    stripped_nt = call(surface, history_with(assistant1_stripped) + question,
                       tools=None, system=pair_system, max_tokens=512)
    delta_no_tools = None
    if natural_nt.ok and big_nt.ok:
        delta_no_tools = (big_nt.result.usage.total_input_tokens
                          - natural_nt.result.usage.total_input_tokens)
    reasoning_tokens_no_tools = None
    if natural_nt.ok and stripped_nt.ok:
        reasoning_tokens_no_tools = (natural_nt.result.usage.total_input_tokens
                                     - stripped_nt.result.usage.total_input_tokens)
    recorder.add("turn2_no_tools_natural_history", outcome=natural_nt,
                 notes="same history, current request without tools")
    recorder.add("turn2_no_tools_plus_3k_on_tool_reasoning", outcome=big_nt,
                 extra={"delta_input_tokens": delta_no_tools,
                        "large_block_chars": len(LARGE_BLOCK)})
    recorder.add("turn2_no_tools_tool_reasoning_stripped", outcome=stripped_nt,
                 extra={"delta_vs_natural": reasoning_tokens_no_tools})

    # -- the big prefix is cached: sending it again should be a cache hit ----
    cached = call(surface, history_with(assistant1_big) + question,
                  tools=tools, system=pair_system, max_tokens=512)
    recorder.add("turn2_tools_plus_3k_cached_repeat", outcome=cached,
                 notes="identical request again; reasoning sits in the cached prefix")

    # -- user turn 3: is it still used/billed two turns later? --------------
    if last_with_tools is not None:
        turn2_assistant = surface.assistant_from_result(last_with_tools.result)
        turn3 = call(
            surface,
            history + [surface.user(TURN2_ASK), turn2_assistant, surface.user(TURN3_ASK)],
            tools=tools, system=system, max_tokens=1024,
        )
        recorder.add("turn3_tools_replay", outcome=turn3,
                     extra={"canary_visible": visible(turn3, code)},
                     notes="two user turns after the tool call")
        turn3_stripped = call(
            surface,
            history_with(assistant1_stripped) + [surface.user(TURN2_ASK), turn2_assistant,
                                                 surface.user(TURN3_ASK)],
            tools=tools, system=system, max_tokens=1024,
        )
        delta_turn3 = None
        if turn3.ok and turn3_stripped.ok:
            delta_turn3 = (turn3.result.usage.total_input_tokens
                           - turn3_stripped.result.usage.total_input_tokens)
        recorder.add("turn3_tools_stripped", outcome=turn3_stripped,
                     extra={"delta_vs_replay": delta_turn3})

    recorder.save()


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "anthropic"
    run(probes.make_surface(name))
