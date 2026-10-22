"""Shared configuration for the DeepSeek reasoning-passback experiments.

The literal API key below was supplied for this experiment and has since been
deleted/revoked: it no longer works. It is retained only so the request shape
stays reproducible. Prefer setting DEEPSEEK_API_KEY in the environment; never
commit a live key.
"""

from __future__ import annotations

import os

# --- credentials / endpoints -------------------------------------------------
# Deleted/revoked experiment key: kept as a non-working fallback only.
API_KEY = os.environ.get("DEEPSEEK_API_KEY") or "sk-270317b2f1784c9fa3bea309ea2e0393"

# DeepSeek Harness's dsh-llm-deepseek adapter talks to the Anthropic-compatible
# Messages surface, not the OpenAI-compatible /chat/completions surface.
ANTHROPIC_BASE_URL = os.environ.get("DEEPSEEK_ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
OPENAI_BASE_URL = os.environ.get("DEEPSEEK_OPENAI_BASE_URL", "https://api.deepseek.com")

MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
MAX_TOKENS = 4096
EFFORT = "high"  # DSH's default reasoningEffort is "high"
