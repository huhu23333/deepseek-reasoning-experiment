#!/usr/bin/env python3
"""Smoke test: one round trip on each wire surface, printing the shapes we rely on."""

from __future__ import annotations

import json

import config
from ds_client import AnthropicMessagesClient, OpenAIChatClient

SEP = "=" * 78


def show_anthropic(client: AnthropicMessagesClient) -> None:
    result = client.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": [{"type": "text", "text": "Reply with exactly: PONG. Think briefly before answering."}]}],
        max_tokens=1024,
        thinking=True,
        effort=config.EFFORT,
        stream=True,
    )
    print(f"stop_reason={result.stop_reason!r} elapsed={result.elapsed_s}s")
    print("usage:", json.dumps(result.usage.as_dict(), ensure_ascii=False))
    for block in result.content:
        summary = {k: (v if k not in ("thinking", "text", "signature") else f"<{len(v)} chars>") for k, v in block.items()}
        print(" block:", summary)
    print("thinking head:", repr(result.thinking[:120]))
    print("signature len:", [len(s) for s in result.signatures])


def show_anthropic_tools(client: AnthropicMessagesClient) -> None:
    tools = [{
        "name": "get_weather",
        "description": "Get weather of a location",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}, "date": {"type": "string"}},
            "required": ["city", "date"],
        },
    }]
    result = client.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": [{"type": "text", "text": "What is the weather in Paris tomorrow? Use the tool."}]}],
        tools=tools,
        max_tokens=1024,
        thinking=True,
        effort=config.EFFORT,
        stream=True,
    )
    print(f"stop_reason={result.stop_reason!r}")
    print("usage:", json.dumps(result.usage.as_dict(), ensure_ascii=False))
    for block in result.content:
        print(" block:", {k: (v if k not in ("thinking", "text", "signature") else f"<{len(v)} chars>") for k, v in block.items()})
    for call in result.tool_calls:
        print(" tool_call:", call.get("name"), json.dumps(call.get("input"), ensure_ascii=False))


def show_openai(client: OpenAIChatClient) -> None:
    result = client.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": "Reply with exactly: PONG. Think briefly before answering."}],
        max_tokens=1024,
        thinking=True,
        effort=config.EFFORT,
    )
    print(f"stop_reason={result.stop_reason!r} elapsed={result.elapsed_s}s")
    print("usage:", json.dumps(result.usage.as_dict(), ensure_ascii=False))
    for block in result.content:
        print(" block:", {k: (v if k not in ("thinking", "text", "signature") else f"<{len(v)} chars>") for k, v in block.items()})
    print("reasoning raw:", json.dumps(result.usage.raw, ensure_ascii=False))


def show_openai_tools(client: OpenAIChatClient) -> None:
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather of a location",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}, "date": {"type": "string"}},
                "required": ["city", "date"],
            },
        },
    }]
    result = client.create(
        model=config.MODEL,
        messages=[{"role": "user", "content": "What is the weather in Paris tomorrow? Use the tool."}],
        tools=tools,
        max_tokens=1024,
        thinking=True,
        effort=config.EFFORT,
    )
    print(f"stop_reason={result.stop_reason!r}")
    print("usage:", json.dumps(result.usage.as_dict(), ensure_ascii=False))
    print("text:", repr(result.text[:200]))
    print("reasoning head:", repr(result.thinking[:200]))
    for call in result.tool_calls:
        print(" tool_call:", call.get("name"), json.dumps(call.get("input"), ensure_ascii=False))


def main() -> None:
    anthropic = AnthropicMessagesClient(config.API_KEY, config.ANTHROPIC_BASE_URL)
    openai = OpenAIChatClient(config.API_KEY, config.OPENAI_BASE_URL)

    print(SEP, "\nAnthropic /v1/messages (stream, thinking on, no tools)\n", SEP)
    show_anthropic(anthropic)
    print(SEP, "\nAnthropic /v1/messages (stream, thinking on, tools)\n", SEP)
    show_anthropic_tools(anthropic)
    print(SEP, "\nOpenAI /chat/completions (thinking on, no tools)\n", SEP)
    show_openai(openai)
    print(SEP, "\nOpenAI /chat/completions (thinking on, tools)\n", SEP)
    show_openai_tools(openai)


if __name__ == "__main__":
    main()
