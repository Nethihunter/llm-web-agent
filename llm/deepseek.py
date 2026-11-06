"""DeepSeek client (default provider).

Uses the OpenAI-compatible chat completions endpoint. When OpenAI and Grok
are added, the shared part of this file will move to a common base class.

Why DeepSeek is the default (see docs/LLM_PROVIDERS.md):
    - cheapest input price among supported providers (~$0.14/M tokens);
    - automatic prompt caching with no configuration required;
    - an OpenAI-compatible API, so the code is small enough to read in one
      screen.

The ``DEEPSEEK_*`` config keys are read only from ``key.json`` and are
never borrowed from another provider's keys. A user with both
``OPENAI_API_KEY`` and ``DEEPSEEK_API_KEY`` set cannot accidentally send
an OpenAI key to DeepSeek.
"""
from __future__ import annotations

from llm.base import BaseLLMClient, LLMConfigError, RawCompletion


class DeepSeekClient(BaseLLMClient):
    """DeepSeek provider: chat completions, JSON mode, automatic caching.

    The only provider-specific logic is ``_complete``: the request shape
    and the way usage tokens are read from the response. Everything else
    (retry, cost estimate, JSON parsing) is inherited from
    ``BaseLLMClient``.
    """

    provider_name = "deepseek"
    DEFAULT_URL = "https://api.deepseek.com/v1/chat/completions"
    DEFAULT_MODEL = "deepseek-flash"
    # Indicative prices, check the provider's pricing page. Set either to
    # None to disable the cost estimate in ``_confirm``.
    PRICE_IN_PER_M = 0.14   # indicative, check the provider's pricing page
    PRICE_OUT_PER_M = 0.28

    def __init__(self, config: dict, auto_confirm: bool = False):
        """Read the DeepSeek key, URL and model from ``config``.

        Placeholder detection: ``key.json.example`` ships with a fake key
        starting with ``sk-your``. Raising on it gives a clearer error than
        letting the first API call fail with a 401.

        Raises:
            LLMConfigError: the key is missing or still the placeholder.
        """
        super().__init__(config, auto_confirm)
        self.api_key = config.get("DEEPSEEK_API_KEY", "")
        if not self.api_key or self.api_key.startswith("sk-your"):
            raise LLMConfigError("DEEPSEEK_API_KEY missing in key.json")
        self.api_url = config.get("DEEPSEEK_API_URL", self.DEFAULT_URL)
        self.model = config.get("DEEPSEEK_MODEL", self.DEFAULT_MODEL)

    def _complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        """Send one chat-completion request and return the raw text.

        Request choices:
        - ``temperature=0.1``: we want deterministic selectors, not
          creative ones. Slightly above 0 to avoid provider-side
          repetition penalties on long histories.
        - ``response_format={"type": "json_object"}``: DeepSeek's JSON
          mode. It is not a guarantee (the model can still return
          non-JSON or truncated JSON), but it removes the "Sure, here is
          the JSON:" preamble in practice.
        - ``max_tokens`` is passed through: the caller owns the
          first-attempt vs retry size decision (see
          ``BaseLLMClient.plan_next_step``).

        Usage fields:
        - ``prompt_tokens`` / ``completion_tokens`` are the standard
          OpenAI-compatible counters;
        - ``prompt_cache_hit_tokens`` is DeepSeek-specific and reflects
          the prefix cache. It is recorded but not used in the cost
          calculation (DeepSeek bills cached input at a lower rate that
          is not exposed separately in this code).
        """
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
        # ``choices[0]`` is always present on a 200 response; a missing
        # key would mean the provider changed its API shape, which is a
        # real bug and should not be silently swallowed.
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        return RawCompletion(
            text=choice["message"]["content"] or "",
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
            finish_reason=choice.get("finish_reason"),
        )
