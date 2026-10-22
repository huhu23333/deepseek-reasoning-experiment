#!/usr/bin/env python3
"""Minimal DeepSeek clients for the reasoning-passback experiments.

Two wire surfaces are implemented because the question is about both:

* AnthropicMessagesClient -- exactly the surface DeepSeek Harness uses:
  POST {base}/v1/messages, header x-api-key + anthropic-version, SSE stream,
  assistant history carrying {"type": "thinking", "thinking": ..., "signature": ...}
  blocks and enabled thinking with output_config.effort.

* OpenAIChatClient -- POST {base}/chat/completions, the surface the official
  "Thinking Mode" guide documents. Reasoning comes back as reasoning_content
  on the assistant message and is passed back in the same field.

No third-party SDK is used so the JSON on the wire stays visible.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests


ANTHROPIC_VERSION = "2023-06-01"


class DSApiError(RuntimeError):
    """A non-2xx response from the DeepSeek API (or an in-band SSE error)."""

    def __init__(self, status: int | None, body: str, message: str = "") -> None:
        self.status = status
        self.body = body
        super().__init__(message or f"DeepSeek API error (status={status}): {body[:800]}")


@dataclass
class Usage:
    """Normalised usage across both wire formats.

    ``total_input_tokens`` is the provider's own prompt total. For Anthropic
    Messages that is input + cache-read + cache-write; for Chat Completions it
    is ``prompt_tokens``, which already contains the hit/miss split.
    """

    total_input_tokens: int = 0
    uncached_input_tokens: int = 0          # charged at the cache-miss rate
    cache_read_input_tokens: int = 0        # charged at the cache-hit rate
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None     # reported inside output for Chat Completions
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_input_tokens": self.total_input_tokens,
            "uncached_input_tokens": self.uncached_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


@dataclass
class MessageResult:
    content: list[dict[str, Any]]
    usage: Usage
    stop_reason: str | None = None
    request_id: str | None = None
    elapsed_s: float = 0.0
    raw_events: list[dict[str, Any]] | None = None

    def blocks(self, kind: str) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == kind]

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def thinking(self) -> str:
        return "".join(b.get("thinking", "") for b in self.content if b.get("type") == "thinking")

    @property
    def signatures(self) -> list[str]:
        return [b.get("signature", "") for b in self.content if b.get("type") == "thinking"]

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return self.blocks("tool_use")


# --------------------------------------------------------------------------- #
# Anthropic-compatible Messages surface (what DSH uses)
# --------------------------------------------------------------------------- #
class AnthropicMessagesClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 600.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    # -- public ---------------------------------------------------------------
    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        thinking: bool = True,
        effort: str = "high",
        stream: bool = True,
        temperature: float | None = None,
        stop_sequences: list[str] | None = None,
    ) -> MessageResult:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "thinking": {"type": "enabled" if thinking else "disabled"},
        }
        if stream:
            body["stream"] = True
        if thinking:
            body["output_config"] = {"effort": effort}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools
        if temperature is not None:
            body["temperature"] = temperature
        if stop_sequences:
            body["stop_sequences"] = stop_sequences

        headers = {
            "content-type": "application/json",
            "accept": "text/event-stream" if stream else "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "user-agent": "dsh-reasoning-experiment/1.0",
        }
        started = time.time()
        resp = self.session.post(
            f"{self.base_url}/v1/messages",
            headers=headers,
            json=body,
            stream=stream,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            text = resp.text
            raise DSApiError(resp.status_code, text)
        if stream:
            result = self._consume_stream(resp)
        else:
            result = self._consume_json(resp)
        result.elapsed_s = round(time.time() - started, 3)
        return result

    # -- wire ----------------------------------------------------------------
    @staticmethod
    def _consume_json(resp: requests.Response) -> MessageResult:
        payload = resp.json()
        usage = Usage(raw=payload.get("usage") or {})
        AnthropicMessagesClient._apply_usage(usage, payload.get("usage") or {})
        return MessageResult(
            content=payload.get("content") or [],
            usage=usage,
            stop_reason=payload.get("stop_reason"),
            request_id=payload.get("id"),
        )

    @staticmethod
    def _apply_usage(usage: Usage, raw: dict[str, Any]) -> None:
        if not raw:
            return
        if isinstance(raw.get("input_tokens"), int):
            usage.uncached_input_tokens = raw["input_tokens"]
        if isinstance(raw.get("output_tokens"), int):
            usage.output_tokens = raw["output_tokens"]
        if isinstance(raw.get("cache_read_input_tokens"), int):
            usage.cache_read_input_tokens = raw["cache_read_input_tokens"]
        if isinstance(raw.get("cache_creation_input_tokens"), int):
            usage.cache_write_input_tokens = raw["cache_creation_input_tokens"]
        usage.total_input_tokens = (
            usage.uncached_input_tokens
            + usage.cache_read_input_tokens
            + usage.cache_write_input_tokens
        )
        usage.raw.update(raw)

    @staticmethod
    def _iter_sse(resp: requests.Response) -> Iterator[tuple[str | None, str]]:
        event: str | None = None
        data: list[str] = []
        for line in resp.iter_lines(decode_unicode=True):
            if line is None:
                continue
            if line == "":
                if data:
                    yield event, "\n".join(data)
                event, data = None, []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value.lstrip(" ")
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
        if data:
            yield event, "\n".join(data)

    def _consume_stream(self, resp: requests.Response) -> MessageResult:
        blocks: dict[int, dict[str, Any]] = {}
        usage = Usage()
        stop_reason: str | None = None
        request_id: str | None = None
        events: list[dict[str, Any]] = []

        for _event, data in self._iter_sse(resp):
            if data == "[DONE]":
                continue
            payload = json.loads(data)
            events.append(payload)
            kind = payload.get("type")
            if kind == "message_start":
                message = payload.get("message") or {}
                request_id = message.get("id")
                self._apply_usage(usage, message.get("usage") or {})
            elif kind == "content_block_start":
                index = payload["index"]
                block = dict(payload.get("content_block") or {})
                if block.get("type") == "tool_use":
                    block["_json"] = ""
                blocks[index] = block
            elif kind == "content_block_delta":
                index = payload["index"]
                delta = payload.get("delta") or {}
                block = blocks[index]
                if delta.get("type") == "text_delta":
                    block["text"] = block.get("text", "") + delta.get("text", "")
                elif delta.get("type") == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                elif delta.get("type") == "signature_delta":
                    block["signature"] = block.get("signature", "") + delta.get("signature", "")
                elif delta.get("type") == "input_json_delta":
                    block["_json"] = block.get("_json", "") + delta.get("partial_json", "")
            elif kind == "content_block_stop":
                block = blocks[payload["index"]]
                if block.get("type") == "tool_use":
                    raw_json = block.pop("_json", "") or "{}"
                    try:
                        block["input"] = json.loads(raw_json)
                    except json.JSONDecodeError:
                        block["input"] = {}
                        block["input_raw"] = raw_json
            elif kind == "message_delta":
                self._apply_usage(usage, payload.get("usage") or {})
                stop_reason = (payload.get("delta") or {}).get("stop_reason", stop_reason)
            elif kind == "error":
                raise DSApiError(None, json.dumps(payload, ensure_ascii=False), "in-band SSE error")

        content = [blocks[i] for i in sorted(blocks)]
        return MessageResult(
            content=content, usage=usage, stop_reason=stop_reason,
            request_id=request_id, raw_events=events,
        )


# --------------------------------------------------------------------------- #
# OpenAI-compatible Chat Completions surface (what the official guide documents)
# --------------------------------------------------------------------------- #
class OpenAIChatClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 600.0) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        thinking: bool = True,
        effort: str = "high",
        temperature: float | None = None,
        stream: bool = False,
    ) -> MessageResult:
        if stream:
            raise NotImplementedError("This experiment suite uses non-streaming chat completions.")
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "thinking": {"type": "enabled" if thinking else "disabled"},
        }
        if thinking:
            body["reasoning_effort"] = effort
        if tools:
            body["tools"] = tools
        if temperature is not None:
            body["temperature"] = temperature

        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
            "user-agent": "dsh-reasoning-experiment/1.0",
        }
        started = time.time()
        resp = self.session.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=body,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise DSApiError(resp.status_code, resp.text)
        payload = resp.json()
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        raw_usage = payload.get("usage") or {}
        usage = Usage(raw=raw_usage)
        usage.total_input_tokens = int(raw_usage.get("prompt_tokens") or 0)
        usage.cache_read_input_tokens = int(raw_usage.get("prompt_cache_hit_tokens") or 0)
        usage.uncached_input_tokens = int(raw_usage.get("prompt_cache_miss_tokens") or 0)
        usage.output_tokens = int(raw_usage.get("completion_tokens") or 0)
        details = raw_usage.get("completion_tokens_details") or {}
        if isinstance(details.get("reasoning_tokens"), int):
            usage.reasoning_tokens = details["reasoning_tokens"]

        content: list[dict[str, Any]] = []
        if message.get("reasoning_content"):
            content.append({"type": "thinking", "thinking": message["reasoning_content"]})
        if message.get("content"):
            content.append({"type": "text", "text": message["content"]})
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            content.append({
                "type": "tool_use",
                "id": call.get("id"),
                "name": fn.get("name"),
                "input": json.loads(fn.get("arguments") or "{}"),
                "arguments_raw": fn.get("arguments"),
            })
        return MessageResult(
            content=content,
            usage=usage,
            stop_reason=choice.get("finish_reason"),
            request_id=payload.get("id"),
            elapsed_s=round(time.time() - started, 3),
        )


# --------------------------------------------------------------------------- #
# Convenience builders
# --------------------------------------------------------------------------- #
def anthropic_assistant(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Anthropic assistant turn. blocks: text / thinking / tool_use dicts as-is."""
    return {"role": "assistant", "content": blocks}


def anthropic_user_text(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def anthropic_tool_result(tool_use_id: str, text: str, is_error: bool = False) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": [{"type": "text", "text": text}],
            "is_error": is_error,
        }],
    }


def openai_tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": parameters,
    }}


def openai_assistant(
    content: str | None,
    reasoning_content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message
