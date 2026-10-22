#!/usr/bin/env python3
"""Experiment 1 -- multi-turn conversation WITHOUT tools.

Question: when history is replayed into a request without the "tools"
parameter, which reasoning content is still seen by the model, and is the
ignored content paid for as input tokens?

Both surfaces are exercised:
  * anthropic -- thinking blocks + signature (DeepSeek Harness's wire format)
  * openai    -- assistant.reasoning_content (the format the official guide describes)

Canary method: the model puts a random 8-hex code ONLY inside its reasoning.
The code never appears in any user message, so a later turn can repeat it only
if the replayed reasoning actually reached the model.
"""

from __future__ import annotations

import probes
from probes import ASK_CANARY, LARGE_BLOCK, Recorder, call, gen_canary, new_system, visible


def run(surface: probes.Surface) -> None:
    recorder = Recorder("exp1_no_tools_multiturn", surface)

    # -- turn 1: canary lives only in reasoning ------------------------------
    gen = gen_canary(surface)
    code = gen["code"]
    recorder.add(
        "turn1_generate_canary", outcome=gen["outcome"],
        extra={"canary_code": code, "attempt": gen["attempt"]},
        notes="canary must appear only inside reasoning",
    )

    u1 = surface.user(gen["prompt"])
    a_replay = surface.assistant(thinking=gen["thinking"], signature=gen["signature"], text=gen["content"])
    a_strip = surface.assistant(text=gen["content"])

    # -- turn 2: replay vs strip --------------------------------------------
    replay2 = call(surface, [u1, a_replay, surface.user(ASK_CANARY)], system=new_system())
    strip2 = call(surface, [u1, a_strip, surface.user(ASK_CANARY)], system=new_system())
    recorder.add("turn2_replay_reasoning", outcome=replay2,
                 extra={"canary_visible": visible(replay2, code)})
    recorder.add("turn2_stripped_reasoning", outcome=strip2,
                 extra={"canary_visible": visible(strip2, code)})

    if surface.name == "anthropic":
        a_unsigned = surface.assistant(thinking=gen["thinking"], text=gen["content"])
        unsigned2 = call(surface, [u1, a_unsigned, surface.user(ASK_CANARY)], system=new_system())
        recorder.add("turn2_unsigned_replay", outcome=unsigned2,
                     extra={"canary_visible": visible(unsigned2, code)})

    # -- token accounting: a ~3.3k-token synthetic reasoning block ----------
    system_pair = new_system("ignored-pair")
    control = call(
        surface,
        [surface.user("Say OK."), surface.assistant(text="OK"), surface.user("Say OK again.")],
        system=system_pair,
    )
    with_block = call(
        surface,
        [surface.user("Say OK."),
         surface.assistant(thinking=LARGE_BLOCK, text="OK"),
         surface.user("Say OK again.")],
        system=system_pair,
    )
    delta = None
    if control.ok and with_block.ok:
        delta = (with_block.result.usage.total_input_tokens
                 - control.result.usage.total_input_tokens)
    recorder.add("ignored_block_control", outcome=control,
                 notes="no tools, no reasoning block")
    recorder.add("ignored_block_replayed", outcome=with_block,
                 extra={"delta_input_tokens": delta, "large_block_chars": len(LARGE_BLOCK)},
                 notes="no tools; reasoning block should be ignored and unpaid")

    # -- turn 3: a real second exchange, then ask for the turn-1 canary -----
    question2 = "请用一句话确认你理解了这个练习，不要说出暗号本身。"
    ack_replay = call(surface, [u1, a_replay, surface.user(question2)], system=new_system())
    ack_strip = call(surface, [u1, a_strip, surface.user(question2)], system=new_system())
    recorder.add("turn2_ack_replay", outcome=ack_replay)
    recorder.add("turn2_ack_stripped", outcome=ack_strip)

    if ack_replay.ok and ack_strip.ok:
        assistant2_replay = surface.assistant_from_result(ack_replay.result)
        assistant2_strip = surface.assistant_from_result(ack_strip.result)
        turn3_replay = call(
            surface,
            [u1, a_replay, surface.user(question2), assistant2_replay, surface.user(ASK_CANARY)],
            system=new_system(),
        )
        turn3_strip = call(
            surface,
            [u1, a_strip, surface.user(question2), assistant2_strip, surface.user(ASK_CANARY)],
            system=new_system(),
        )
        turn3_delta = None
        if turn3_replay.ok and turn3_strip.ok:
            turn3_delta = (turn3_replay.result.usage.total_input_tokens
                           - turn3_strip.result.usage.total_input_tokens)
        recorder.add("turn3_replay_all_reasoning", outcome=turn3_replay,
                     extra={"canary_visible": visible(turn3_replay, code),
                            "delta_input_tokens": turn3_delta})
        recorder.add("turn3_stripped_reasoning", outcome=turn3_strip,
                     extra={"canary_visible": visible(turn3_strip, code)})

    recorder.save()


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "anthropic"
    run(probes.make_surface(name))
