#!/usr/bin/env python3
"""Experiment 7 -- the thinking toggle is a second gate.

Experiments 1-6 established that history reasoning is used when the current
request carries tools and ignored when it does not. This experiment adds the
other axis: the request's "thinking" toggle.

Matrix (same history, one 3.3k-token synthetic reasoning block vs stripped):
  thinking ON  + tools     -> ?
  thinking ON  + no tools  -> ?
  thinking OFF + tools     -> ?
  thinking OFF + no tools  -> ?
"""

from __future__ import annotations

import config
import probes
from probes import CallOutcome, LARGE_BLOCK, Recorder
from ds_client import DSApiError


def raw_call(surface: probes.Surface, messages, tools, thinking: bool) -> CallOutcome:
    try:
        result = surface.client.create(
            model=config.MODEL, messages=messages, tools=tools,
            max_tokens=512, thinking=thinking,
        )
        return CallOutcome(ok=True, result=result)
    except DSApiError as error:
        return CallOutcome(ok=False, status=error.status, body=error.body)


def run(surface: probes.Surface) -> None:
    recorder = Recorder("exp7_thinking_gate", surface)
    tools = surface.tools_payload(["get_weather"])

    natural = [
        surface.user("Say OK."),
        surface.assistant(thinking=LARGE_BLOCK, text="OK"),
        surface.user("Say OK again."),
    ]
    stripped = [
        surface.user("Say OK."),
        surface.assistant(text="OK"),
        surface.user("Say OK again."),
    ]

    for thinking_label, thinking in (("thinking_on", True), ("thinking_off", False)):
        for tools_label, tools_arg in (("tools", tools), ("no_tools", None)):
            with_block = raw_call(surface, natural, tools_arg, thinking)
            without_block = raw_call(surface, stripped, tools_arg, thinking)
            delta = None
            if with_block.ok and without_block.ok:
                delta = (with_block.result.usage.total_input_tokens
                         - without_block.result.usage.total_input_tokens)
            recorder.add(
                f"{thinking_label}_{tools_label}",
                outcome=with_block,
                extra={
                    "thinking": thinking,
                    "carries_tools": tools_arg is not None,
                    "delta_input_tokens": delta,
                    "stripped_input_tokens": (without_block.result.usage.total_input_tokens
                                              if without_block.ok else None),
                },
                notes="input-token delta caused by the 3.3k-token history reasoning block",
            )

    recorder.save()


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "anthropic"
    run(probes.make_surface(name))
