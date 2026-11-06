# LLM Provider Support

This project supports multiple LLM providers. Each is behind a common
interface (`llm/base.py`). Switching providers is a configuration change,
not a code change.

---

## Comparison

Prices are in USD per 1M tokens (indicative, based on provider docs).
Model names change often; check each provider's documentation.

| Provider | Model | Input $/1M | Output $/1M | EU access | Notes |
|---|---|---|---|---|---|
| **DeepSeek** | `deepseek-flash` | $0.15 (off-peak)<br>$0.30 (peak) | $0.60 (off-peak)<br>$1.20 (peak) | ⚠️ Check compliance | Cheapest. Default for development. Supports JSON mode. |
| **DeepSeek** | `deepseek-v4-pro` | $0.66 (off-peak)<br>$1.32 (peak) | $1.98 (off-peak)<br>$3.96 (peak) | ⚠️ Check compliance | Higher quality, no vision. |
| **OpenAI** | `gpt-4o-mini` | $0.15 | $0.60 | ✅ | Wide availability. |
| **Anthropic** | `claude-haiku-5-5` | $0.10 | $0.50 | ✅ | Fast, cheap. Good for validation. |
| **Anthropic** | `claude-sonnet-5-5` | $2.00 | $10.00 | ✅ | Best balance of speed/intelligence. |
| **Grok** | `grok-4.3` | $1.25 | $2.50 | ✅ | OpenAI-compatible API. |
| **Grok** | `grok-4.7` | $2.00 | $6.00 | ✅ | Higher quality. |
| **Ollama** | local (e.g. `qwen3`) | $0 | $0 | ✅ | CPU-only. Slow but free. |

> **Note on DeepSeek cache pricing:** Cached input tokens are drastically 
> cheaper (e.g., $0.003/1M for `deepseek-flash` off-peak). The agent logs 
> cache hits to help you monitor this.

Prices are indicative (2026-06) and model names change often. Check each
provider's documentation for current models and rates.

---

## Configuration

Edit `key.json`:

```json
{
  "LLM_PROVIDER": "openai",
  "OPENAI_API_KEY": "sk-...",
  "OPENAI_MODEL": "gpt-4o-mini"
}
```

Supported values for `LLM_PROVIDER`: `deepseek`, `openai`, `anthropic`,
`grok`, `ollama`.

---

## Per-run override

You can override the provider without editing `key.json`:

```bash
python3 run.py --task saucedemo_checkout \
  --provider anthropic \
  --model claude-3-5-haiku-20241022 \
  --yes
```

The override is written to `key.json` and persists for subsequent runs.

---

## Why DeepSeek is the default

DeepSeek is the cheapest provider for the kind of traffic this agent
generates (short HTML-reduced prompts) and has strong automatic prompt
caching. For development with a tight API budget, it is the pragmatic choice.

**Note for EU users**: before using DeepSeek for professional work, check
your GDPR and data-residency obligations. If they are a problem, use OpenAI,
Anthropic, or Grok instead. This is a deployment decision, not a technical
limitation — the code is identical.

---

## Adding a new provider

1. Create `llm/myprovider.py`:

```python
from llm.base import BaseLLMClient, LLMConfigError, RawCompletion

class MyProviderClient(BaseLLMClient):
    provider_name = "myprovider"
    DEFAULT_URL = "https://api.myprovider.com/v1/chat"
    DEFAULT_MODEL = "myprovider-mini"
    PRICE_IN_PER_M = 0.10   # Optional: set to enable cost estimation
    PRICE_OUT_PER_M = 0.30

    def __init__(self, config, auto_confirm=False):
        super().__init__(config, auto_confirm)
        self.api_key = config.get("MYPROVIDER_API_KEY", "")
        if not self.api_key:
            raise LLMConfigError("MYPROVIDER_API_KEY missing in key.json")
        self.api_url = config.get("MYPROVIDER_API_URL", self.DEFAULT_URL)
        self.model = config.get("MYPROVIDER_MODEL", self.DEFAULT_MODEL)

    def _complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        # 1. Build the payload (e.g., OpenAI-compatible format)
        # 2. POST to self.api_url
        # 3. Parse the JSON response
        return RawCompletion(
            text=extracted_text,
            input_tokens=usage["prompt_tokens"],
            output_tokens=usage["completion_tokens"],
            finish_reason=choice["finish_reason"],
        )
```

2. Register it in `llm/factory.py`:

```python
PROVIDERS = {
    # ...
    "myprovider": ("llm.myprovider", "MyProviderClient"),
}
if provider == "myprovider":
    from llm.myprovider import MyProviderClient
    return MyProviderClient(config, auto_confirm=auto_confirm)
```

3. Add `"MYPROVIDER_API_KEY"` to `key.json.example`.

That's it. The agent does not need any other change.

---

## Prompt caching

Different providers expose cache metrics differently:

| Provider | Cache field | Notes |
|---|---|---|
| DeepSeek | `usage.prompt_cache_hit_tokens` | Automatic, no config |
| OpenAI | `usage.prompt_tokens_details.cached_tokens` | Automatic, 50% discount |
| Anthropic | `usage.cache_read_input_tokens` | Requires `cache_control` in request (not implemented) |
| Grok | Not exposed | — |

The agent displays cache hits in logs when available.
