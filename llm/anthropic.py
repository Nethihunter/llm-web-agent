"""Anthropic provider (Claude).

Differences vs OpenAI:
- ``system`` is a top-level field, not a message.
- No native JSON mode; the system prompt already instructs JSON-only.
- ``max_tokens`` is required.

Two smaller differences worth knowing when reading ``_complete``:

- The response ``content`` is a list of blocks (``{"type": "text", ...}``,
  ``{"type": "tool_use", ...}``), not a single string. We concatenate the
  ``text`` blocks and ignore the others. The agent never uses tool use,
  so a non-text block in the reply would be an unexpected provider
  change, but silently ignoring it keeps a stray ``tool_use`` from
  breaking a run.
- Cache metrics use a different name (``cache_read_input_tokens``) and
  only populate if the request opts into caching via ``cache_control``
  markers. This client does **not** opt in, so the cache count is
  usually 0. The field is still read so the log line is consistent
  across providers.
"""
from __future__ import annotations

from llm.base import BaseLLMClient, LLMConfigError, RawCompletion


class AnthropicClient(BaseLLMClient):
    """Anthropic provider: Messages API, no JSON mode, no prompt caching.

    The system prompt is passed as a top-level ``system`` field, which is
    how the Anthropic Messages API differs most visibly from OpenAI's
    chat completions. Everything else (retry, cost estimate, JSON
    parsing) is inherited from ``BaseLLMClient``.
    """

    provider_name = "anthropic"
    DEFAULT_URL = "https://api.anthropic.com/v1/messages"
    DEFAULT_MODEL = "claude-haiku-5-5"
    # Prices are left unset on purpose: Anthropic changes its rates often
    # enough that a hardcoded value would drift. With both at None,
    # ``_confirm`` shows the token estimate but no dollar figure, which
    # is more honest than a stale number. Set them from the pricing page
    # to enable the estimate.
    PRICE_IN_PER_M = None    # set from the pricing page to enable the estimate
    PRICE_OUT_PER_M = None

    def __init__(self, config: dict, auto_confirm: bool = False):
        """Read the Anthropic key, URL and model from ``config``.

        Placeholder detection: ``key.json.example`` ships with a fake key
        starting with ``sk-ant-your``. Raising on it gives a clearer error
        than letting the first API call fail with a 401.

        Raises:
            LLMConfigError: the key is missing or still the placeholder.
        """
        super().__init__(config, auto_confirm)
        self.api_key = config.get("ANTHROPIC_API_KEY", "")
        if not self.api_key or self.api_key.startswith("sk-ant-your"):
            raise LLMConfigError("ANTHROPIC_API_KEY missing in key.json")
        self.api_url = config.get("ANTHROPIC_API_URL", self.DEFAULT_URL)
        self.model = config.get("ANTHROPIC_MODEL", self.DEFAULT_MODEL)

    def _complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        """Send one Messages API request and return the raw text.

        Request choices:

        - ``system`` as a top-level field: this is the Anthropic
          convention. Sending it as a ``{"role": "system"}`` message, as
          for OpenAI, returns a 400.
        - ``max_tokens`` is required by the API. The caller decides the
          first-attempt vs retry size (see
          ``BaseLLMClient.plan_next_step``), and this client passes it
          through unchanged.
        - ``temperature=0.1``: same reasoning as the DeepSeek client —
          we want deterministic selectors, not creative ones.

        Headers:

        - ``x-api-key`` instead of ``Authorization: Bearer``;
        - ``anthropic-version`` is required by the API and pins the
          response shape. Bumping it is a deliberate change, not a
          routine one.

        Response handling:

        - ``content`` is a list of blocks; we keep only ``type == "text"``
          and join them. Any other block type (tool use, images) is
          ignored — see the module docstring.
        - ``stop_reason`` maps to our ``finish_reason``. ``"max_tokens"``
          is what ``plan_next_step`` uses to decide the reply was
          truncated and must be retried with a larger budget.
        """
        payload = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = self._post_json(self.api_url, headers, payload)
        usage = body.get("usage") or {}
        # Keep only text blocks: the agent never uses tool use or images.
        # A stray non-text block would otherwise be concatenated as a
        # string representation of a dict, which the JSON parser would
        # then reject with a confusing error.
        text = "".join(
            block.get("text", "")
            for block in body.get("content", [])
            if block.get("type") == "text"
        )
        return RawCompletion(
            text=text,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_hit_tokens=usage.get("cache_read_input_tokens", 0),
            finish_reason=body.get("stop_reason"),
        )
