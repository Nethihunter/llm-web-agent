"""OpenAI provider (GPT-4o, GPT-4o-mini) and base for OpenAI-compatible APIs.

Uses the Chat Completions API with JSON mode. Inherits prompt building, retry,
cost estimate and JSON parsing from ``BaseLLMClient``; only ``_complete()`` is
provider-specific.

OpenAI-compatible providers (Grok, ...) subclass this and only change
``CONFIG_PREFIX`` and the defaults. Each provider reads ITS OWN keys from
key.json (``<PREFIX>_API_KEY``, ``<PREFIX>_API_URL``, ``<PREFIX>_MODEL``), so an
OpenAI key can never be sent to another provider.
"""
from __future__ import annotations

from llm.base import BaseLLMClient, LLMConfigError, RawCompletion

PLACEHOLDER_PREFIXES = ("sk-your", "sk-ant-your", "xai-your")


class OpenAIClient(BaseLLMClient):
    provider_name = "openai"
    CONFIG_PREFIX = "OPENAI"
    DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
    DEFAULT_MODEL = "gpt-4o-mini"           # check the provider's current models
    PRICE_IN_PER_M = 0.15
    PRICE_OUT_PER_M = 0.60

    def __init__(self, config: dict, auto_confirm: bool = False):
        super().__init__(config, auto_confirm)
        p = self.CONFIG_PREFIX
        self.api_key = config.get(f"{p}_API_KEY", "")
        if not self.api_key or self.api_key.startswith(PLACEHOLDER_PREFIXES):
            raise LLMConfigError(f"{p}_API_KEY missing in key.json")
        self.api_url = config.get(f"{p}_API_URL", self.DEFAULT_URL)
        self.model = config.get(f"{p}_MODEL", self.DEFAULT_MODEL)

    def _complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = self._post_json(self.api_url, headers, payload)
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        cache = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        return RawCompletion(
            text=choice["message"]["content"] or "",
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cache_hit_tokens=cache,
            finish_reason=choice.get("finish_reason"),
        )
