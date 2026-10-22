#!/usr/bin/env python3
"""Experiment 3 -- billing accounting.

Question: is ignored reasoning billed? Is generated reasoning billed? How does
prompt caching change the bill?

Uses usage counters as the billing basis (the API bills prompt/completion
tokens; the platform's published prices are applied to the reported split).
"""

from __future__ import annotations

import probes
from probes import LARGE_BLOCK, Recorder, call, gen_canary, new_system


def _delta(before, after) -> int | None:
    if before.ok and after.ok:
        return (after.result.usage.total_input_tokens
                - before.result.usage.total_input_tokens)
    return None


def run(surface: probes.Surface) -> None:
    recorder = Recorder("exp3_billing", surface)

    # -- no tools: the block should be dropped before tokenisation ----------
    system = new_system("bill-none")
    control = call(surface, [surface.user("Say OK."), surface.assistant(text="OK"),
                             surface.user("Say OK again.")], system=system)
    ignored = call(surface, [surface.user("Say OK."),
                             surface.assistant(thinking=LARGE_BLOCK, text="OK"),
                             surface.user("Say OK again.")], system=system)
    recorder.add("no_tools_control", outcome=control)
    recorder.add("no_tools_large_reasoning", outcome=ignored,
                 extra={"delta_input_tokens": _delta(control, ignored),
                        "large_block_chars": len(LARGE_BLOCK)})

    # -- tools: the block should enter the prompt and be billed -------------
    tools = surface.tools_payload(["get_weather"])
    system = new_system("bill-tools")
    control_t = call(surface, [surface.user("Say OK."), surface.assistant(text="OK"),
                               surface.user("Say OK again.")], tools=tools, system=system)
    included = call(surface, [surface.user("Say OK."),
                              surface.assistant(thinking=LARGE_BLOCK, text="OK"),
                              surface.user("Say OK again.")],
                    tools=tools, system=system, max_tokens=1024)
    recorder.add("tools_control", outcome=control_t)
    recorder.add("tools_large_reasoning", outcome=included,
                 extra={"delta_input_tokens": _delta(control_t, included),
                        "large_block_chars": len(LARGE_BLOCK)})

    # -- generated reasoning is counted inside output tokens ---------------
    gen = gen_canary(surface, attempts=4)
    usage = gen["outcome"].result.usage
    recorder.add("generated_reasoning_output", outcome=gen["outcome"], extra={
        "reasoning_chars": len(gen["thinking"]),
        "output_tokens": usage.output_tokens,
        "reported_reasoning_tokens": usage.reasoning_tokens,
    }, notes="reasoning tokens are part of the billed completion tokens")

    # -- prompt caching: identical request twice ----------------------------
    cache_system = new_system("cache")
    cache_messages = [surface.user("Say OK."),
                      surface.assistant(thinking=LARGE_BLOCK, text="OK"),
                      surface.user("Say OK again.")]
    first = call(surface, cache_messages, tools=tools, system=cache_system, max_tokens=1024)
    second = call(surface, cache_messages, tools=tools, system=cache_system, max_tokens=1024)
    recorder.add("cache_first", outcome=first)
    recorder.add("cache_second", outcome=second)

    # -- the ignored block must not become cached prefix either -------------
    system = new_system("bill-none-cache")
    ignored_first = call(surface, [surface.user("Say OK."),
                                   surface.assistant(thinking=LARGE_BLOCK, text="OK"),
                                   surface.user("Say OK again.")], system=system)
    ignored_second = call(surface, [surface.user("Say OK."),
                                    surface.assistant(thinking=LARGE_BLOCK, text="OK"),
                                    surface.user("Say OK again.")], system=system)
    recorder.add("ignored_cache_first", outcome=ignored_first)
    recorder.add("ignored_cache_second", outcome=ignored_second)

    recorder.save()


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "anthropic"
    run(probes.make_surface(name))
