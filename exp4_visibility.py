#!/usr/bin/env python3
"""Experiment 4 -- behavioural corroboration: is replayed reasoning visible?

Token accounting (experiments 1-3) already shows whether a replayed block
enters the prompt. This experiment adds a behavioural check: the model puts a
random 8-hex canary ONLY inside its reasoning, and a later turn asks it to
repeat the code.

The check is repeated because the model sometimes refuses to repeat a
"secret" even when it can see it; the hit count, not one answer, is the signal.
"""

from __future__ import annotations

import probes
from probes import ASK_CANARY_ASSERTIVE, Recorder, call, gen_canary, new_system, visible


def run(surface: probes.Surface) -> None:
    recorder = Recorder("exp4_visibility", surface)
    gen = gen_canary(surface)
    code = gen["code"]
    recorder.add("canary_generation", outcome=gen["outcome"],
                 extra={"canary_code": code, "reasoning_chars": len(gen["thinking"])})

    tools = surface.tools_payload(["get_weather"])
    for label, use_tools, trials in (("tools", True, 5), ("no_tools", False, 3)):
        hits = 0
        for trial in range(trials):
            outcome = call(
                surface,
                [surface.user(gen["prompt"]),
                 surface.assistant(thinking=gen["thinking"], signature=gen["signature"],
                                   text=gen["content"]),
                 surface.user(ASK_CANARY_ASSERTIVE)],
                tools=tools if use_tools else None,
                system=new_system("vis"),
                max_tokens=1024,
            )
            hit = visible(outcome, code)
            hits += 1 if hit else 0
            recorder.add(f"{label}_trial{trial + 1}", outcome=outcome,
                         extra={"canary_visible": hit})
        recorder.add(f"{label}_summary",
                     extra={"hits": hits, "trials": trials, "canary_visible": hits > 0})

    recorder.save()


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "anthropic"
    run(probes.make_surface(name))
