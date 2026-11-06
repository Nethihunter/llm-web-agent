# LLM Provider Support

This project supports multiple LLM providers. Each is behind a common
interface (`llm/base.py`). Switching providers is a configuration change,
not a code change.

---

## Comparison

| Provider | Model | Input $/1M | Output $/1M | EU access | Notes |
|---|---|---|---|---|---|
| **DeepSeek** | deepseek-chat | $0.14 | $0.28 | ⚠️ Check compliance | Cheapest. Default for development. |
| **OpenAI** | gpt-4o-mini | $0.15 | $0.60 | ✅ | Wide availability. |
| **OpenAI** | gpt-4o | $2.50 | $10.00 | ✅ | Higher quality, higher cost. |
| **Anthropic** | claude-3-5-haiku | $0.80 | $4.00 | ✅ | Fast, cheap. Good for validation. |
| **Anthropic** | claude-3-5-sonnet | $3.00 | $15.00 | ✅ | Best quality. Use for complex tasks. |
| **Grok** | grok-beta | $5.00 | $15.00 | ✅ | OpenAI-compatible API. |
| **Ollama** | any local | $0 | $0 | ✅ | CPU-only. Slow but free. |

Prices are indicative (2025-03) and model names change often. Check each
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

DeepSeek is the cheapest provider ($0.14/1M input tokens vs $0.15 for
gpt-4o-mini) and has strong prompt caching. For development with a tight
API budget, it is the pragmatic choice.

**Note for EU users**: before using DeepSeek for professional work, check
your GDPR and data-residency obligations. If they are a problem, use OpenAI,
Anthropic, or Grok instead. This is a deployment decision, not a technical
limitation — the code is identical.

---

## Adding a new provider

1. Create `llm/myprovider.py`:

```python
from llm.base import BaseLLMClient, LLMError, LLMResponse

class MyProviderClient(BaseLLMClient):
    provider_name = "myprovider"

    def __init__(self, config, auto_confirm=False):
        self.api_key = config.get("MYPROVIDER_API_KEY", "")
        # ...

    def plan_next_step(self, html, goal, history, previous_error=None):
        # Build request, call API, parse JSON
        return LLMResponse(
            data=parsed_plan,
            input_tokens=...,
            output_tokens=...,
            cache_hit_tokens=...,
            provider=self.provider_name,
            model=self.model,
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
