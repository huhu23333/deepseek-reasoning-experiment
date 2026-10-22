#!/usr/bin/env python3
"""Shared helpers for the reasoning-passback experiments.

The experiments are written against a small neutral "surface" abstraction so the
same scenario runs on both wire formats DeepSeek exposes:

* anthropic -- POST /anthropic/v1/messages (what DeepSeek Harness uses)
* openai    -- POST /chat/completions   (what the official Thinking Mode guide documents)
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Any

import config
from ds_client import (
    AnthropicMessagesClient,
    DSApiError,
    MessageResult,
    OpenAIChatClient,
    anthropic_assistant,
    anthropic_tool_result,
    anthropic_user_text,
    openai_tool,
)

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")

# --------------------------------------------------------------------------- #
# Fixed experiment material
# --------------------------------------------------------------------------- #
CANARY_INSTRUCTION = (
    "这是一个思维练习。请在你的思考过程（thinking / reasoning）中生成一个 8 位十六进制随机串，"
    "并按格式写下：暗号=XXXXXXXX（把 XXXXXXXX 替换成你生成的随机串）。"
    "最终回答只回复 OK。会话标记 {nonce}。"
)

ASK_CANARY = (
    "请只输出你上一轮思考中生成的 8 位十六进制暗号；"
    "如果上下文中看不到任何暗号，只输出 UNKNOWN。不要猜测或编造。"
)

ASK_CANARY_ASSERTIVE = (
    "上下文自检：你上一轮思考中一定生成过一个形如 暗号=XXXXXXXX 的 8 位十六进制串。"
    "请直接输出那个串本身，不要解释，不要输出其他内容。"
    "只有当你确实完全没有看到它时，才输出 UNKNOWN。"
)

CODE_RE = re.compile(r"暗号\s*[=＝:：]\s*([0-9A-Fa-f]{6,12})")
HEX_RE = re.compile(r"\b([0-9A-Fa-f]{8})\b")

# ~3300 tokens of deterministic filler, used to make token accounting visible.
LARGE_BLOCK = "The quick brown fox jumps over the lazy dog. " * 300

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "get_weather": {
        "description": "Get the weather forecast for a city on a specific date.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
                "date": {"type": "string", "description": "Date, YYYY-MM-DD"},
            },
            "required": ["city", "date"],
        },
    },
    "get_time": {
        "description": "Get the current local time in a timezone.",
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string", "description": "IANA timezone"}},
            "required": ["timezone"],
        },
    },
}

MOCK_TOOL_RESULTS = {
    "get_weather": "Sunny, 22C, light wind.",
    "get_time": "2026-10-22 16:30:00 Europe/Paris (CEST).",
}

# deepseek-flash, USD per 1M tokens, peak (off-peak is half). Source:
# https://api-docs.deepseek.com/quick_start/pricing (read 2026-10-22).
PRICES_PEAK_USD_PER_M = {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20}


# --------------------------------------------------------------------------- #
# Pricing / timing helpers
# --------------------------------------------------------------------------- #
def is_peak_now() -> bool:
    """Peak hours: 01:00-04:00 and 06:00-10:00 UTC, Mon-Fri (holidays ignored)."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return now.weekday() < 5 and (1 <= now.hour < 4 or 6 <= now.hour < 10)


def cost_usd(usage: Any, peak: bool | None = None) -> dict[str, float]:
    if peak is None:
        peak = is_peak_now()
    factor = 1.0 if peak else 0.5
    miss_tokens = usage.uncached_input_tokens + usage.cache_write_input_tokens
    hit_tokens = usage.cache_read_input_tokens
    return {
        "input_cache_miss": miss_tokens * PRICES_PEAK_USD_PER_M["cache_miss"] * factor / 1e6,
        "input_cache_hit": hit_tokens * PRICES_PEAK_USD_PER_M["cache_hit"] * factor / 1e6,
        "output": usage.output_tokens * PRICES_PEAK_USD_PER_M["output"] * factor / 1e6,
    }


def total_cost_usd(cost: dict[str, float]) -> float:
    return round(sum(cost.values()), 8)


# --------------------------------------------------------------------------- #
# Neutral surface abstraction
# --------------------------------------------------------------------------- #
@dataclass
class CallOutcome:
    ok: bool
    result: MessageResult | None = None
    status: int | None = None
    body: str | None = None
    elapsed_s: float = 0.0

    def usage(self) -> dict[str, int | None] | None:
        return self.result.usage.as_dict() if self.result is not None else None


class Surface:
    name: str
    client: Any

    # -- message construction ------------------------------------------------
    def user(self, text: str) -> dict[str, Any]:
        raise NotImplementedError

    def assistant(
        self, *, thinking: str | None = None, signature: str | None = None,
        text: str = "", tool_calls: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def tool_results(self, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def assistant_from_result(self, result: MessageResult, *, include_reasoning: bool = True) -> dict[str, Any]:
        raise NotImplementedError

    def tools_payload(self, names: list[str]) -> list[dict[str, Any]]:
        raise NotImplementedError

    # -- result inspection ---------------------------------------------------
    @staticmethod
    def text_of(result: MessageResult) -> str:
        return result.text

    @staticmethod
    def reasoning_of(result: MessageResult) -> str:
        return result.thinking

    @staticmethod
    def calls_of(result: MessageResult) -> list[dict[str, Any]]:
        return [{"id": c.get("id"), "name": c.get("name"), "input": c.get("input")}
                for c in result.tool_calls]

    def signature_of(self, result: MessageResult) -> str | None:
        return None

    # -- transport -----------------------------------------------------------
    def create(self, messages: list[dict[str, Any]], *, tools=None, system: str | None = None,
               max_tokens: int = 2048, thinking: bool = True) -> MessageResult:
        raise NotImplementedError


class AnthropicSurface(Surface):
    name = "anthropic"

    def __init__(self) -> None:
        self.client = AnthropicMessagesClient(config.API_KEY, config.ANTHROPIC_BASE_URL)

    def user(self, text: str) -> dict[str, Any]:
        return anthropic_user_text(text)

    def assistant(self, *, thinking=None, signature=None, text="", tool_calls=None):
        return anthropic_assistant(_anthropic_blocks(thinking, signature, text, tool_calls))

    def tool_results(self, calls):
        return [anthropic_tool_result(c["id"], MOCK_TOOL_RESULTS.get(c.get("name"), "ok")) for c in calls]

    def assistant_from_result(self, result, *, include_reasoning=True):
        blocks: list[dict[str, Any]] = []
        for block in result.content:
            if block.get("type") == "thinking":
                if include_reasoning:
                    b = {"type": "thinking", "thinking": block.get("thinking", "")}
                    if block.get("signature") is not None:
                        b["signature"] = block["signature"]
                    blocks.append(b)
            elif block.get("type") == "text":
                blocks.append({"type": "text", "text": block.get("text", "")})
            elif block.get("type") == "tool_use":
                blocks.append({"type": "tool_use", "id": block["id"], "name": block["name"], "input": block.get("input", {})})
        return {"role": "assistant", "content": blocks}

    def tools_payload(self, names):
        return [{"name": n, "description": TOOL_SPECS[n]["description"], "input_schema": TOOL_SPECS[n]["parameters"]} for n in names]

    def signature_of(self, result):
        sigs = result.signatures
        return sigs[0] if sigs else None

    def create(self, messages, *, tools=None, system=None, max_tokens=2048, thinking=True):
        return self.client.create(
            model=config.MODEL, messages=messages, tools=tools, system=system,
            max_tokens=max_tokens, thinking=thinking, effort=config.EFFORT, stream=True,
        )


class OpenAISurface(Surface):
    name = "openai"

    def __init__(self) -> None:
        self.client = OpenAIChatClient(config.API_KEY, config.OPENAI_BASE_URL)

    def user(self, text: str) -> dict[str, Any]:
        return {"role": "user", "content": text}

    def assistant(self, *, thinking=None, signature=None, text="", tool_calls=None):
        message: dict[str, Any] = {"role": "assistant", "content": text if text else None}
        if thinking is not None:
            message["reasoning_content"] = thinking
        if tool_calls:
            message["tool_calls"] = [{
                "id": c["id"], "type": "function",
                "function": {"name": c["name"], "arguments": json.dumps(c.get("input") or {}, ensure_ascii=False)},
            } for c in tool_calls]
        return message

    def tool_results(self, calls):
        return [{"role": "tool", "tool_call_id": c["id"], "content": MOCK_TOOL_RESULTS.get(c.get("name"), "ok")} for c in calls]

    def assistant_from_result(self, result, *, include_reasoning=True):
        return self.assistant(
            thinking=result.thinking if include_reasoning else None,
            text=result.text,
            tool_calls=self.calls_of(result) or None,
        )

    def tools_payload(self, names):
        return [openai_tool(n, TOOL_SPECS[n]["description"], TOOL_SPECS[n]["parameters"]) for n in names]

    def create(self, messages, *, tools=None, system=None, max_tokens=2048, thinking=True):
        if system is not None:
            messages = [{"role": "system", "content": system}] + messages
        return self.client.create(
            model=config.MODEL, messages=messages, tools=tools, max_tokens=max_tokens,
            thinking=thinking, effort=config.EFFORT,
        )


def _anthropic_blocks(thinking, signature, text, tool_calls):
    blocks: list[dict[str, Any]] = []
    if thinking is not None:
        block: dict[str, Any] = {"type": "thinking", "thinking": thinking}
        if signature is not None:
            block["signature"] = signature
        blocks.append(block)
    if text:
        blocks.append({"type": "text", "text": text})
    for call in tool_calls or []:
        blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": call.get("input") or {}})
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    return blocks


def make_surface(name: str) -> Surface:
    return AnthropicSurface() if name == "anthropic" else OpenAISurface()


# --------------------------------------------------------------------------- #
# Calls and probes
# --------------------------------------------------------------------------- #
def call(surface: Surface, messages: list[dict[str, Any]], *, tools=None,
         system: str | None = None, max_tokens: int = 2048) -> CallOutcome:
    try:
        result = surface.create(messages, tools=tools, system=system, max_tokens=max_tokens)
        return CallOutcome(ok=True, result=result, elapsed_s=result.elapsed_s)
    except DSApiError as error:
        return CallOutcome(ok=False, status=error.status, body=error.body)


def new_system(prefix: str = "cold") -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


def find_code(text: str) -> str | None:
    match = CODE_RE.search(text or "")
    if match:
        return match.group(1).upper()
    hexes = HEX_RE.findall(text or "")
    return hexes[-1].upper() if hexes else None


def visible(outcome: CallOutcome, code: str) -> bool:
    return outcome.ok and outcome.result is not None and code.lower() in outcome.result.text.lower()


def gen_canary(surface: Surface, attempts: int = 5) -> dict[str, Any]:
    """Ask the model to put a random 8-hex code only in its reasoning."""
    last: CallOutcome | None = None
    for attempt in range(attempts):
        nonce = f"{secrets.token_hex(4)}-try{attempt}"
        prompt = CANARY_INSTRUCTION.format(nonce=nonce)
        outcome = call(surface, [surface.user(prompt)], max_tokens=2048)
        last = outcome
        if outcome.ok and outcome.result is not None:
            code = find_code(surface.reasoning_of(outcome.result))
            # A code that also leaked into the visible answer would make later
            # "can you recall it" probes meaningless.
            if code and code.lower() not in outcome.result.text.lower():
                return {
                    "code": code,
                    "prompt": prompt,
                    "thinking": surface.reasoning_of(outcome.result),
                    "signature": surface.signature_of(outcome.result),
                    "content": outcome.result.text,
                    "outcome": outcome,
                    "attempt": attempt + 1,
                }
    raise RuntimeError(f"{surface.name}: model never wrote a canary in reasoning (last={last and last.status})")


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
class Recorder:
    def __init__(self, experiment: str, surface: Surface) -> None:
        self.experiment = experiment
        self.surface = surface
        self.peak = is_peak_now()
        self.cases: list[dict[str, Any]] = []

    def add(self, name: str, *, outcome: CallOutcome | None = None,
            extra: dict[str, Any] | None = None, notes: str = "") -> dict[str, Any]:
        case: dict[str, Any] = {"name": name, "notes": notes, **(extra or {})}
        if outcome is not None:
            case["ok"] = outcome.ok
            if outcome.ok and outcome.result is not None:
                usage = outcome.usage() or {}
                cost = cost_usd(outcome.result.usage, self.peak)
                case.update({
                    "usage": usage,
                    "cost_usd": {**{k: round(v, 8) for k, v in cost.items()},
                                 "total": total_cost_usd(cost)},
                    "stop_reason": outcome.result.stop_reason,
                    "text": outcome.result.text[:600],
                    "reasoning_chars": len(self.surface.reasoning_of(outcome.result)),
                })
            else:
                case["status"] = outcome.status
                case["error"] = (outcome.body or "")[:800]
        self.cases.append(case)
        print(_format_case(self.experiment, self.surface.name, case))
        return case

    def save(self) -> str:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        path = os.path.join(RESULTS_DIR, f"{self.experiment}_{self.surface.name}.json")
        payload = {
            "experiment": self.experiment,
            "surface": self.surface.name,
            "model": config.MODEL,
            "effort": config.EFFORT,
            "peak": self.peak,
            "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(),
            "cases": self.cases,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"  -> saved {path}")
        return path


def _format_case(experiment: str, surface: str, case: dict[str, Any]) -> str:
    head = f"[{experiment}/{surface}] {case['name']}"
    if case.get("ok") is False:
        return f"{head}: ERROR {case.get('status')} {(case.get('error') or '')[:160]}"
    usage = case.get("usage") or {}
    tail = ""
    if "canary_visible" in case:
        tail += f" visible={case['canary_visible']}"
    if "delta_input_tokens" in case:
        tail += f" delta_input={case['delta_input_tokens']}"
    return (f"{head}: in={usage.get('total_input_tokens')}"
            f" (miss={usage.get('uncached_input_tokens')},hit={usage.get('cache_read_input_tokens')})"
            f" out={usage.get('output_tokens')} text={case.get('text', '')[:50]!r}{tail}")
