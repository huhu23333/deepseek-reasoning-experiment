#!/usr/bin/env python3
"""Experiment 6 -- after the model has completed an answer, does the next user
turn still carry the reasoning of that completed answer?

Scenario:
  turn 1: user asks a plain question; the assistant reasons (canary in reasoning)
          and gives the final answer. No tool call in this turn.
  turn 2: NEW user question ("repeat the canary from your previous reasoning").
  turn 3: another user turn asks again.

At turn 2 the same history is sent with and without "tools" to show that the
decision belongs to the current request, not to the completed turn.
"""

from __future__ import annotations

import re
import secrets

import probes
from probes import ASK_CANARY_ASSERTIVE, LARGE_BLOCK, Recorder, call, new_system, visible

TURN1_PROMPT = (
    "请先在你的思考/reasoning 中生成一个 8 位十六进制随机串，按格式写下：暗号=XXXXXXXX。"
    "然后回答：1+1 等于几？最终回答只输出数字，不要出现暗号，也不要出现任何 8 位十六进制串。"
    "（会话标记 {nonce}）"
)
TURN2_ASK = ASK_CANARY_ASSERTIVE
TURN3_ASK = ("再次确认：请输出你第一轮回答前思考里的那个 8 位十六进制暗号；"
             "看不到就输出 UNKNOWN。")
TURN2_NEUTRAL = "再补充一问：2+2 等于几？只输出数字。"
HEX8_RE = re.compile(r"\b[0-9A-Fa-f]{8}\b")
CODE_RE = re.compile(r"暗号\s*[=＝:：]\s*([0-9A-Fa-f]{6,12})")


def generate_completed_turn1(surface: probes.Surface, system: str) -> dict:
    """One completed plain answer whose canary stays only in the reasoning."""
    last = None
    for attempt in range(10):
        prompt = TURN1_PROMPT.format(nonce=secrets.token_hex(3))
        outcome = call(surface, [surface.user(prompt)], tools=None,
                       system=system, max_tokens=1024)
        last = outcome
        if not outcome.ok:
            continue
        reasoning = surface.reasoning_of(outcome.result)
        codes = {m.upper() for m in CODE_RE.findall(reasoning)}
        if not codes or len(codes) > 1:
            continue
        code = codes.pop()
        text = outcome.result.text
        if code.lower() in text.lower() or "暗号" in text or HEX8_RE.search(text):
            continue
        return {"prompt": prompt, "outcome": outcome, "code": code,
                "reasoning": reasoning, "signature": surface.signature_of(outcome.result),
                "answer": text, "attempt": attempt + 1}
    raise RuntimeError(f"{surface.name}: could not build a completed clean turn 1 "
                       f"(last status {last and last.status})")


def run(surface: probes.Surface) -> None:
    recorder = Recorder("exp6_completed_answer_reasoning", surface)
    tools = surface.tools_payload(["get_weather"])
    system = new_system("exp6")

    # -- turn 1: a completed plain answer, canary only in its reasoning -----
    gen = generate_completed_turn1(surface, system)
    code = gen["code"]
    recorder.add("turn1_completed_answer", outcome=gen["outcome"], extra={
        "canary_code": code,
        "attempt": gen["attempt"],
        "reasoning_chars": len(gen["reasoning"]),
    }, notes="the model has finished answering; canary is only in that turn's reasoning")

    history = [surface.user(gen["prompt"]), surface.assistant_from_result(gen["outcome"].result)]

    # -- turn 2: new user question, with and without tools ------------------
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

    # -- billing: append a 3k-token block to the completed turn's reasoning --
    assistant_natural = surface.assistant_from_result(gen["outcome"].result)
    assistant_big = surface.assistant(
        thinking=gen["reasoning"] + "\n\n" + LARGE_BLOCK,
        signature=gen["signature"],
        text=gen["answer"],
    )
    assistant_stripped = surface.assistant_from_result(gen["outcome"].result,
                                                       include_reasoning=False)

    def history_with(assistant) -> list[dict]:
        return [surface.user(gen["prompt"]), assistant]

    question = [surface.user(TURN2_NEUTRAL)]
    natural_t = call(surface, history_with(assistant_natural) + question,
                     tools=tools, system=system, max_tokens=512)
    big_t = call(surface, history_with(assistant_big) + question,
                 tools=tools, system=system, max_tokens=512)
    stripped_t = call(surface, history_with(assistant_stripped) + question,
                      tools=tools, system=system, max_tokens=512)
    delta_tools = None
    if natural_t.ok and big_t.ok:
        delta_tools = (big_t.result.usage.total_input_tokens
                       - natural_t.result.usage.total_input_tokens)
    reasoning_tokens = None
    if natural_t.ok and stripped_t.ok:
        reasoning_tokens = (natural_t.result.usage.total_input_tokens
                            - stripped_t.result.usage.total_input_tokens)
    recorder.add("turn2_tools_natural_history", outcome=natural_t,
                 notes="new user question, current request carries tools")
    recorder.add("turn2_tools_plus_3k_on_completed_reasoning", outcome=big_t,
                 extra={"delta_input_tokens": delta_tools,
                        "large_block_chars": len(LARGE_BLOCK)})
    recorder.add("turn2_tools_completed_reasoning_stripped", outcome=stripped_t,
                 extra={"delta_vs_natural": reasoning_tokens})

    natural_nt = call(surface, history_with(assistant_natural) + question,
                      tools=None, system=system, max_tokens=512)
    big_nt = call(surface, history_with(assistant_big) + question,
                  tools=None, system=system, max_tokens=512)
    stripped_nt = call(surface, history_with(assistant_stripped) + question,
                       tools=None, system=system, max_tokens=512)
    delta_no_tools = None
    if natural_nt.ok and big_nt.ok:
        delta_no_tools = (big_nt.result.usage.total_input_tokens
                          - natural_nt.result.usage.total_input_tokens)
    reasoning_tokens_no_tools = None
    if natural_nt.ok and stripped_nt.ok:
        reasoning_tokens_no_tools = (natural_nt.result.usage.total_input_tokens
                                     - stripped_nt.result.usage.total_input_tokens)
    recorder.add("turn2_no_tools_natural_history", outcome=natural_nt,
                 notes="same new user question, current request without tools")
    recorder.add("turn2_no_tools_plus_3k_on_completed_reasoning", outcome=big_nt,
                 extra={"delta_input_tokens": delta_no_tools,
                        "large_block_chars": len(LARGE_BLOCK)})
    recorder.add("turn2_no_tools_completed_reasoning_stripped", outcome=stripped_nt,
                 extra={"delta_vs_natural": reasoning_tokens_no_tools})

    # -- cache: identical big prefix sent again -----------------------------
    cached = call(surface, history_with(assistant_big) + question,
                  tools=tools, system=system, max_tokens=512)
    recorder.add("turn2_tools_plus_3k_cached_repeat", outcome=cached)

    # -- turn 3: another user turn; is the completed answer's reasoning still there?
    if last_with_tools is not None:
        turn2_assistant = surface.assistant_from_result(last_with_tools.result)
        turn3 = call(
            surface,
            history + [surface.user(TURN2_ASK), turn2_assistant, surface.user(TURN3_ASK)],
            tools=tools, system=system, max_tokens=1024,
        )
        recorder.add("turn3_tools_replay", outcome=turn3,
                     extra={"canary_visible": visible(turn3, code)},
                     notes="another user turn after the completed answer")
        turn3_stripped = call(
            surface,
            history_with(assistant_stripped) + [surface.user(TURN2_ASK), turn2_assistant,
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
