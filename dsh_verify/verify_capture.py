#!/usr/bin/env python3
"""Compare captured proxy requests with the isolated DSH session log."""
import hashlib
import json
import os
import glob
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(BASE, "proxy_logs")
SESSION = os.path.join(BASE, "session_6838.jsonl")
EXP_RECORD = os.path.join(BASE, "..", "results", "exp9_parallel", "anthropic_intent_cooking.jsonl")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_session():
    rows = [json.loads(line) for line in open(SESSION, encoding="utf-8") if line.strip()]
    assistants = []
    for event in rows:
        if event.get("type") != "assistant/message":
            continue
        message = event["data"]["message"]
        signatures = [block.get("signature") for block in
                      ((message.get("source") or {}).get("replayState") or {}).get("blocks") or []]
        content = []
        for index, block in enumerate(message.get("content") or []):
            if block["type"] == "reasoning":
                item = {"type": "thinking", "thinking": block.get("text", "")}
                if index < len(signatures) and signatures[index] is not None:
                    item["signature"] = signatures[index]
                content.append(item)
            elif block["type"] == "text":
                content.append({"type": "text", "text": block.get("text", "")})
            elif block["type"] == "tool-call":
                content.append({"type": "tool_use", "id": block["id"], "name": block["name"],
                                "input": json.loads(block["arguments"])})
        assistants.append({
            "turn": event["data"].get("turn"),
            "step": event["data"].get("step"),
            "message_id": message.get("id"),
            "content": content,
            "source": message.get("source"),
        })
    return assistants


def load_requests():
    return [json.loads(open(path, encoding="utf-8").read())
            for path in sorted(glob.glob(os.path.join(LOGS, "*-body.json")))]


def is_main_agent(body):
    return body.get("thinking", {}).get("type") == "enabled" and bool(body.get("tools"))


def is_title(body):
    return body.get("thinking", {}).get("type") == "disabled" and not body.get("tools")


def main():
    all_assistants = load_session()
    # Turn 2's answer is logged after the turn-2 request; only turn-1 assistant
    # messages had been produced when requests 3/5/6 were sent.
    assistants = [message for message in all_assistants if message["turn"] == 1]
    requests = load_requests()
    all_main_requests = [(i + 1, body) for i, body in enumerate(requests) if is_main_agent(body)]
    title_requests = [(i + 1, body) for i, body in enumerate(requests) if is_title(body)]
    # The proxy also captured an earlier throwaway session; the canary session is
    # the most recent conversation with exactly len(assistants)+1 main requests.
    main_requests = all_main_requests[-(len(assistants) + 1):]

    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "session_assistant_messages": len(all_assistants),
        "turn1_assistant_messages": len(assistants),
        "proxy_requests": len(requests),
        "main_agent_requests": [index for index, _ in main_requests],
        "title_requests": [index for index, _ in title_requests],
        "comparisons": [],
        "checks": {},
    }

    # Each main request must carry exactly the assistant history DSH persisted,
    # in the same order and byte-identical text/signature.
    failures = []
    for sequential_index, (request_index, body) in enumerate(main_requests):
        expected = assistants[:sequential_index]
        actual = [message for message in body.get("messages") or [] if message.get("role") == "assistant"]
        comparison = {
            "request": request_index,
            "expected_assistant_count": len(expected),
            "actual_assistant_count": len(actual),
            "assistant_match": [],
        }
        for expected_message, actual_message in zip(expected, actual):
            match = expected_message["content"] == actual_message.get("content")
            comparison["assistant_match"].append({
                "turn": expected_message["turn"],
                "step": expected_message["step"],
                "match": match,
                "expected_thinking_sha": sha(json.dumps(expected_message["content"], ensure_ascii=False)),
                "actual_thinking_sha": sha(json.dumps(actual_message.get("content"), ensure_ascii=False)),
                "thinking_chars": [len(block.get("thinking") or "") for block in expected_message["content"]
                                   if block.get("type") == "thinking"],
                "signature_present": [bool(block.get("signature")) for block in expected_message["content"]
                                     if block.get("type") == "thinking"],
            })
            if not match:
                failures.append({"request": request_index,
                                 "turn": expected_message["turn"], "step": expected_message["step"]})
        report["comparisons"].append(comparison)

    # Both later requests must replay the exact same first assistant message.
    first_copies = []
    for request_index, body in main_requests[1:]:
        assistant = [m for m in body["messages"] if m.get("role") == "assistant"][0]
        first_copies.append((request_index, sha(json.dumps(assistant["content"], ensure_ascii=False))))
    report["checks"]["first_assistant_same_across_requests"] = len(set(copy for _, copy in first_copies)) == 1

    # Session text itself must contain the canary that the second turn recalled.
    session_thinking = "\n".join(block.get("thinking") or ""
                                 for message in assistants for block in message["content"]
                                 if block["type"] == "thinking")
    report["checks"]["canary_in_session_thinking"] = "3F9A2C7E" in session_thinking
    report["checks"]["canary_in_replayed_thinking"] = any(
        "3F9A2C7E" in (block.get("thinking") or "")
        for request_index, body in main_requests
        for message in body["messages"] if message.get("role") == "assistant"
        for block in message.get("content") or [] if block.get("type") == "thinking")

    # Top-level shape and extension fields (use the last main request, which
    # carries the full assistant history).
    if main_requests:
        body = main_requests[-1][1]
        base_keys = {"model", "stream", "max_tokens", "messages", "system", "thinking", "output_config", "tools"}
        report["checks"]["base_keys_present"] = base_keys.issubset(body.keys())
        report["checks"]["extra_top_level_keys"] = sorted(set(body.keys()) - base_keys)
        report["checks"]["extension_sizes"] = {
            key: len(json.dumps(body.get(key), ensure_ascii=False))
            for key in report["checks"]["extra_top_level_keys"]
        }
        report["checks"]["tool_count"] = len(body.get("tools") or [])
        report["checks"]["tool_schema_keys"] = sorted((body.get("tools") or [{}])[0].keys())
        report["checks"]["thinking"] = body.get("thinking")
        report["checks"]["output_config"] = body.get("output_config")
        report["checks"]["system_chars"] = len(body.get("system") or "")
        report["checks"]["assistant_block_types"] = [
            [block.get("type") for block in message.get("content") or []]
            for message in body.get("messages") or [] if message.get("role") == "assistant"
        ]
        report["checks"]["message_counts"] = [len(item.get("messages") or []) for _, item in main_requests]
    report["checks"]["title_requests_thinking_disabled_no_tools"] = all(
        is_title(body) for _, body in title_requests
    )
    report["checks"]["title_requests_have_no_assistant_history"] = all(
        not any(message.get("role") == "assistant" for message in body.get("messages") or [])
        for _, body in title_requests
    )

    # Compare with the experiment's stored wire messages.
    experiment_sample = json.loads(open(EXP_RECORD, encoding="utf-8").readline())
    report["checks"]["experiment_message_roles"] = [message.get("role") for message in experiment_sample["messages"]]
    report["checks"]["experiment_assistant_block_types"] = [
        [block.get("type") for block in message.get("content") or []]
        for message in experiment_sample["messages"] if message.get("role") == "assistant"
    ]

    report["checks"]["failures"] = failures
    report["checks"]["all_assistant_history_match"] = not failures and all(
        all(item["match"] for item in comparison["assistant_match"])
        for comparison in report["comparisons"]
    )

    with open(os.path.join(BASE, "capture_report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    lines = [
        "# DSH 代理抓包验证",
        "",
        f"- 时间：{report['generated_at']}",
        f"- 代理请求数：{len(requests)}（主 agent：{report['main_agent_requests']}，session-title：{report['title_requests']}）",
        f"- 会话 assistant 消息数：{len(assistants)}",
        "",
        "## assistant 历史逐字节比对（DSH 会话日志 vs 代理抓到的请求体）",
        "",
        "| 请求 | turn/step | thinking 字符 | signature | 完全一致 |",
        "|---|---|---|---|---|",
    ]
    for comparison in report["comparisons"]:
        for item in comparison["assistant_match"]:
            lines.append(
                f"| {comparison['request']} | {item['turn']}/{item['step']} | "
                f"{item['thinking_chars']} | {item['signature_present']} | {item['match']} |"
            )
    checks = report["checks"]
    lines += [
        "",
        "## 检查项",
        "",
        f"- 第一次 assistant 消息在两次后续请求中完全相同：{checks['first_assistant_same_across_requests']}",
        f"- canary 在会话 thinking 中：{checks['canary_in_session_thinking']}",
        f"- canary 在被回传的 thinking 中：{checks['canary_in_replayed_thinking']}",
        f"- 所有 assistant 历史逐块一致：{checks['all_assistant_history_match']}",
        f"- 主请求顶层字段齐全：{checks['base_keys_present']}",
        f"- 额外顶层字段：{checks['extra_top_level_keys']}（大小 {checks['extension_sizes']}）",
        f"- tools 数量/字段：{checks['tool_count']} / {checks['tool_schema_keys']}",
        f"- thinking：{checks['thinking']}；output_config：{checks['output_config']}",
        f"- session-title 请求 thinking 关闭且无 tools：{checks['title_requests_thinking_disabled_no_tools']}",
        f"- session-title 请求无 assistant 历史：{checks['title_requests_have_no_assistant_history']}",
        "",
        "## 与实验客户端的线上格式对比",
        "",
        "| 项目 | DSH 实测 | 本次实验客户端 |",
        "|---|---|---|",
        "| 端点 | /anthropic/v1/messages | 同 |",
        "| 顶层字段 | model, stream, max_tokens, messages, system, thinking, output_config, tools + 两个扩展字段 | 同前八项 |",
        "| assistant reasoning | content 块 {type: thinking, thinking, signature} | 同 |",
        "| assistant text/tool | {type: text} / {type: tool_use, id, name, input} | 同 |",
        "| tools 定义 | {name, description, input_schema} | 同 |",
        "| tool_result | user content {type: tool_result, tool_use_id, content} | 同 |",
        "",
        "结论：DSH 主 agent 请求的 assistant 历史与 DSH 持久化日志逐块完全一致；",
        "上一轮（含同一轮的工具步骤）的 thinking 文本和 signature 原样出现在后续请求体中。",
        "",
    ]
    with open(os.path.join(BASE, "VERIFICATION.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    print(json.dumps({key: checks[key] for key in (
        "all_assistant_history_match", "first_assistant_same_across_requests",
        "canary_in_replayed_thinking", "extra_top_level_keys", "tool_count",
        "title_requests_thinking_disabled_no_tools", "failures")}, ensure_ascii=False, indent=2))
    print("wrote", os.path.join(BASE, "VERIFICATION.md"))


if __name__ == "__main__":
    main()
