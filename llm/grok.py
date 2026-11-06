"""Grok provider (xAI). The API is OpenAI-compatible.

Reads GROK_API_KEY / GROK_API_URL / GROK_MODEL from key.json. It never falls
back to the OPENAI_* keys.
"""
from __future__ import annotations

from llm.openai import OpenAIClient


class GrokClient(OpenAIClient):
    provider_name = "grok"
    CONFIG_PREFIX = "GROK"
    DEFAULT_URL = "https://api.x.ai/v1/chat/completions"
    DEFAULT_MODEL = "grok-beta"             # old name: check xAI's current models
    PRICE_IN_PER_M = None                   # unknown: disables the cost estimate
    PRICE_OUT_PER_M = None
