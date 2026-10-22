#!/usr/bin/env python3
"""Experiment 8 -- manually tamper with an earlier turn's reasoning and check that
the next user turn's answer changes accordingly.

Setup: turn 1 is a completed plain answer whose reasoning contains a random
8-hex canary A (A never appears in any visible text). At turn 2 the same history
is sent for a new user question, with the reasoning text in one of three shapes:

  original  -- reasoning contains A
  modified  -- identical text but A replaced by a different 8-hex code B
  stripped  -- no reasoning block at all

The question is a forced choice among A, B and C (C never appeared), with the
option order randomized per trial. If the model reads the replayed reasoning,
original answers A and modified answers B; stripped cannot know and should not
pick the appeared code. Everything else (visible answer, signature, tools,
thinking, prompt) is held constant.

Controls: modified reasoning with (a) no tools, (b) thinking disabled,
(c) signature removed.
"""

from __future__ import annotations

import random
import re
import secrets

import probes
from probes import Recorder, call, new_system

CODE_RE = re.compile(r"暗号\s*[=＝:：]\s*([0-9A-Fa-f]{6,12})")
HEX8_RE = re.compile(r"\b[0-9A-Fa-f]{8}\b")
TURN1_PROMPT = (
    "请先在你的思考/reasoning 中生成一个 8 位十六进制随机串，按格式写下：暗号=XXXXXXXX。"
    "然后回答：1+1 等于几？最终回答只输出数字，不要出现暗号，也不要出现任何 8 位十六进制串。"
    "（会话标记 {nonce}）"
)
TRIALS = 6
CONTROL_TRIALS = 4


def replace_ci(text: str, old: str, new: str) -> str:
    return re.sub(re.escape(old), new, text, flags=re.IGNORECASE)


def generate_turn1(surface: probes.Surface, system: str) -> dict:
    for _ in range(10):
        prompt = TURN1_PROMPT.format(nonce=secrets.token_hex(3))
        outcome = call(surface, [surface.user(prompt)], tools=None,
                       system=system, max_tokens=1024)
        if not outcome.ok:
            continue
        reasoning = surface.reasoning_of(outcome.result)
        codes = {match.upper() for match in CODE_RE.findall(reasoning)}
        if len(codes) != 1:
            continue
        code = codes.pop()
        visible = outcome.result.text
        if code.lower() in visible.lower() or "暗号" in visible or HEX8_RE.search(visible):
            continue
        return {"prompt": prompt, "outcome": outcome, "code": code,
                "reasoning": reasoning, "signature": surface.signature_of(outcome.result)}
    raise RuntimeError(f"{surface.name}: could not generate a clean turn-1 canary")


def main() -> None:
    for name in ("anthropic", "openai"):
        run(probes.make_surface(name))


def run(surface: probes.Surface) -> None:
    recorder = Recorder("exp8_reasoning_tamper", surface)
    tools = surface.tools_payload(["get_weather"])
    system = new_system("exp8")

    gen = generate_turn1(surface, system)
    code_a = gen["code"]
    code_b = secrets.token_hex(4).upper()
    code_c = secrets.token_hex(4).upper()
    while code_b == code_a:
        code_b = secrets.token_hex(4).upper()
    while code_c in (code_a, code_b):
        code_c = secrets.token_hex(4).upper()

    modified = replace_ci(gen["reasoning"], code_a, code_b)
    assert code_a.lower() not in modified.lower()
    assert code_b.lower() in modified.lower()

    recorder.add("turn1_generate_canary", outcome=gen["outcome"], extra={
        "canary_original": code_a,
        "canary_modified": code_b,
        "never_seen": code_c,
        "reasoning_chars": len(gen["reasoning"]),
    }, notes="canary A only in reasoning; visible answer is just a number")

    visible = gen["outcome"].result.text
    prompt = gen["prompt"]

    def history(reasoning: str | None, with_signature: bool = True) -> list[dict]:
        assistant = surface.assistant(
            thinking=reasoning,
            signature=gen["signature"] if with_signature else None,
            text=visible,
        )
        return [surface.user(prompt), assistant]

    def choice_question() -> tuple[str, list[str]]:
        order = [code_a, code_b, code_c]
        random.SystemRandom().shuffle(order)
        text = (
            "上下文自检：你上一轮 assistant 的 thinking/reasoning 中出现过下面哪一个 "
            "8 位十六进制串？只输出那个串本身，不要解释。候选：" + "、".join(order)
        )
        return text, order

    def trial(label: str, messages: list[dict], *, use_tools: bool, thinking: bool,
              trials: int, expected: str | None) -> None:
        hits = 0
        answers = []
        for _ in range(trials):
            question, order = choice_question()
            outcome = call_with_flags(
                surface, messages + [surface.user(question)],
                tools=tools if use_tools else None, system=system,
                thinking=thinking, max_tokens=512,
            )
            text = (outcome.result.text if outcome.ok else "ERROR").strip()
            pick = next((code for code in (code_a, code_b, code_c)
                         if code.lower() in text.lower()), "none")
            correct = expected is not None and pick == expected
            hits += 1 if correct else 0
            answers.append({"pick": pick, "order": order, "correct": correct})
            recorder.add(f"{label}_step{len(answers)}", outcome=outcome,
                         extra={"pick": pick, "option_order": order, "correct": correct})
        recorder.add(f"{label}_summary", extra={
            "hits": hits, "trials": trials, "expected": expected, "answers": answers,
        }, notes="forced choice; option order randomized each trial")

    # Natural tool-call turn? Not needed here: turn 1 is a completed plain answer
    # whose reasoning stays replayed by DSH, matching the "earlier turn" question.
    original = history(gen["reasoning"])
    modified_hist = history(modified)
    stripped = history(None)

    trial("original_with_tools", original, use_tools=True, thinking=True,
          trials=TRIALS, expected=code_a)
    trial("modified_with_tools", modified_hist, use_tools=True, thinking=True,
          trials=TRIALS, expected=code_b)
    trial("stripped_with_tools", stripped, use_tools=True, thinking=True,
          trials=CONTROL_TRIALS, expected=None)

    # Controls: each should stop the replayed reasoning from being used.
    trial("modified_no_tools", modified_hist, use_tools=False, thinking=True,
          trials=CONTROL_TRIALS, expected=None)
    trial("modified_thinking_off", modified_hist, use_tools=True, thinking=False,
          trials=CONTROL_TRIALS, expected=None)
    trial("modified_no_signature", history(modified, with_signature=False),
          use_tools=True, thinking=True, trials=CONTROL_TRIALS, expected=code_b)

    # The edit keeps the text length identical, so token counts should match:
    # the answer change must be semantic, not a prompt-length artefact.
    neutral = [surface.user("再补充一问：2+2 等于几？只输出数字。")]
    original_tokens = call(surface, original + neutral, tools=tools,
                           system=system, max_tokens=256)
    modified_tokens = call(surface, modified_hist + neutral, tools=tools,
                           system=system, max_tokens=256)
    delta = None
    if original_tokens.ok and modified_tokens.ok:
        delta = (modified_tokens.result.usage.total_input_tokens
                 - original_tokens.result.usage.total_input_tokens)
    recorder.add("token_sanity_original", outcome=original_tokens)
    recorder.add("token_sanity_modified", outcome=modified_tokens,
                 extra={"delta_vs_original": delta})

    recorder.save()


def call_with_flags(surface: probes.Surface, messages, *, tools, system,
                    thinking: bool, max_tokens: int):
    """Call with an explicit thinking flag through the surface client."""
    from probes import CallOutcome
    from ds_client import DSApiError

    try:
        if surface.name == "openai":
            # The OpenAI client takes the system prompt as a message.
            if system is not None:
                messages = [{"role": "system", "content": system}] + messages
            result = surface.client.create(
                model=probes.config.MODEL, messages=messages, tools=tools,
                max_tokens=max_tokens, thinking=thinking,
                effort=probes.config.EFFORT,
            )
        else:
            result = surface.client.create(
                model=probes.config.MODEL, messages=messages, tools=tools,
                system=system, max_tokens=max_tokens, thinking=thinking,
                effort=probes.config.EFFORT,
            )
        return CallOutcome(ok=True, result=result)
    except DSApiError as error:
        return CallOutcome(ok=False, status=error.status, body=error.body)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        run(probes.make_surface(sys.argv[1]))
    else:
        main()
