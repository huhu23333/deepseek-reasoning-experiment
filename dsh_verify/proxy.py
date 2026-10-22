#!/usr/bin/env python3
"""Local API proxy for verifying what DSH actually sends.

Listens on 127.0.0.1:18080. DSH points DEEPSEEK_BASE_URL at
http://127.0.0.1:18080/anthropic, so every Messages request arrives here,
is saved verbatim, then is forwarded to the real DeepSeek API with the real key.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "code"))
import config  # noqa: E402  (experiment config holds the real key)

HOST = "127.0.0.1"
PORT = int(os.environ.get("PROXY_PORT", "18080"))
UPSTREAM = "https://api.deepseek.com"
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_logs")
os.makedirs(LOG_DIR, exist_ok=True)
_counter = 0
_lock = threading.Lock()


def next_seq() -> int:
    global _counter
    with _lock:
        _counter += 1
        return _counter


def summarize_body(body: dict) -> dict:
    messages = body.get("messages") or []
    summary = {
        "model": body.get("model"),
        "stream": body.get("stream"),
        "thinking": body.get("thinking"),
        "output_config": body.get("output_config"),
        "has_tools": "tools" in body,
        "tool_count": len(body.get("tools") or []),
        "message_count": len(messages),
        "messages": [],
    }
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            block_types = [block.get("type") for block in content if isinstance(block, dict)]
            thinking_chars = sum(len(block.get("thinking") or "") for block in content
                                 if isinstance(block, dict) and block.get("type") == "thinking")
            text_chars = sum(len(block.get("text") or "") for block in content
                             if isinstance(block, dict) and block.get("type") == "text")
        else:
            block_types, thinking_chars, text_chars = ["string"], 0, len(content or "")
        summary["messages"].append({
            "role": message.get("role"),
            "blocks": block_types,
            "thinking_chars": thinking_chars,
            "text_chars": text_chars,
        })
    return summary


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        return

    def do_GET(self):
        if self.path == "/health":
            payload = json.dumps({"ok": True, "requests": _counter}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("content-length") or "0")
        raw = self.rfile.read(length)
        seq = next_seq()
        stamp = time.time()
        try:
            body = json.loads(raw)
        except Exception:
            body = None

        prefix = os.path.join(LOG_DIR, f"{seq:04d}")
        with open(prefix + "-body.json", "wb") as handle:
            handle.write(raw)
        meta = {
            "seq": seq,
            "time": stamp,
            "path": self.path,
            "headers": {
                key: ("<redacted>" if key.lower() in ("x-api-key", "authorization") else value)
                for key, value in self.headers.items()
            },
            "body_summary": summarize_body(body) if isinstance(body, dict) else None,
        }
        with open(prefix + "-meta.json", "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False, indent=2)
        with open(os.path.join(LOG_DIR, "index.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")

        upstream_url = UPSTREAM + self.path
        headers = {
            "content-type": self.headers.get("content-type", "application/json"),
            "accept": self.headers.get("accept", "text/event-stream"),
            "x-api-key": config.API_KEY,
            "anthropic-version": self.headers.get("anthropic-version", "2023-06-01"),
        }
        if self.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = self.headers["anthropic-beta"]
        if isinstance(body, dict):
            model = body.get("model")
            bsum = summarize_body(body)
            print(f"[proxy {seq:04d}] {self.path} model={model} messages={bsum['message_count']} "
                  f"tools={bsum['tool_count']} thinking={bsum['thinking']} "
                  f"assistant_thinking_chars="
                  f"{[m['thinking_chars'] for m in bsum['messages'] if m['role'] == 'assistant']}",
                  flush=True)

        try:
            response = requests.post(upstream_url, data=raw, headers=headers,
                                     stream=True, timeout=(30, 600))
        except Exception as error:
            payload = json.dumps({"error": {"message": f"proxy upstream failed: {error}"}}).encode()
            self.send_response(502)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True
            return

        with open(prefix + "-response.txt", "wb") as response_log:
            response_log.write(f"status {response.status_code}\n".encode())
            for key, value in response.headers.items():
                response_log.write(f"{key}: {value}\n".encode())
            response_log.write(b"\n")

        self.send_response(response.status_code)
        for key, value in response.headers.items():
            lowered = key.lower()
            if lowered in ("content-length", "transfer-encoding", "connection",
                           "content-encoding", "keep-alive"):
                continue
            self.send_header(key, value)
        self.send_header("connection", "close")
        self.end_headers()
        try:
            for chunk in response.iter_content(8192):
                if not chunk:
                    continue
                self.wfile.write(chunk)
                self.wfile.flush()
                with_open = True
                try:
                    response_log.write(chunk)
                except Exception:
                    pass
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                response_log.close()
            except Exception:
                pass
            self.close_connection = True


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"proxy listening on http://{HOST}:{PORT} -> {UPSTREAM}", flush=True)
    print(f"logs: {LOG_DIR}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
