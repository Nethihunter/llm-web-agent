"""create_client(config) -> BaseLLMClient.

Provider and model overrides (``--provider`` / ``--model``) are applied in
memory. ``key.json`` is never rewritten.

Design choices worth knowing:

- **Lazy imports.** Providers are imported only when selected, via
  ``importlib``. This keeps ``run.py --help`` (or importing this module
  from a test) from importing every provider's HTTP dependencies, and it
  lets a user work with a single provider without installing optional
  packages if one is ever added.
- **No config mutation.** ``create_client`` copies ``config`` before
  injecting the ``--model`` override. Callers can pass the result of
  ``load_config`` and reuse it for another client without seeing the
  override leak.
- **Friendly config errors.** A missing key, an unknown provider, or a
  provider that exists in ``PROVIDERS`` but is not yet implemented all
  raise ``LLMConfigError``, which ``run.py`` maps to exit code 2. This
  is the difference between "your config is wrong" (2) and "the task
  failed" (1).
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

from llm.base import BaseLLMClient, LLMConfigError

# name -> (module, class). The class is imported lazily so that a typo in
# an optional provider's module (or a missing dependency) does not break
# ``--help`` or the use of another provider.
PROVIDERS = {
    "deepseek": ("llm.deepseek", "DeepSeekClient"),
    "openai": ("llm.openai", "OpenAIClient"),
    "anthropic": ("llm.anthropic", "AnthropicClient"),
    "grok": ("llm.grok", "GrokClient"),
    "ollama": ("llm.ollama", "OllamaClient"),
}
DEFAULT_PROVIDER = "deepseek"


def load_config(path: str = "key.json") -> dict:
    """Load and parse a ``key.json`` file.

    Kept separate from ``create_client`` so callers can inspect the raw
    config (for example the dashboard displaying the active provider)
    without instantiating a client.

    Raises:
        LLMConfigError: the file is missing, or its content is not valid
            JSON. Both cases are user-fixable; the message names the file
            to edit and what to do with it.
    """
    p = Path(path)
    if not p.exists():
        raise LLMConfigError(
            f"{path} not found. Copy key.json.example to key.json and fill it in."
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LLMConfigError(f"{path} is not valid JSON: {e}") from e


def create_client(
    config: dict,
    auto_confirm: bool = False,
    provider: str | None = None,
    model: str | None = None,
) -> BaseLLMClient:
    """Instantiate the configured LLM provider.

    Provider selection, in decreasing priority:
        1. the ``provider`` argument (``--provider`` on the CLI);
        2. the ``LLM_PROVIDER`` key in ``config``;
        3. ``DEFAULT_PROVIDER``.

    Model selection, in decreasing priority:
        1. the ``model`` argument (``--model`` on the CLI);
        2. the ``<PROVIDER>_MODEL`` key in ``config``;
        3. the provider class's ``DEFAULT_MODEL``.

    The ``--model`` override is applied by setting
    ``cfg["<PROVIDER>_MODEL"]`` on a **copy** of ``config``, which is
    exactly the key each provider reads in its ``__init__``. The caller's
    dict is never mutated, and ``key.json`` is never rewritten — the
    override lives only for the lifetime of the returned client.

    Args:
        config: parsed ``key.json`` (see ``load_config``).
        auto_confirm: passed to the client. When true, the interactive
            "send this request? (y/n)" prompt is skipped. Set by
            ``--yes`` on the CLI.
        provider: overrides ``LLM_PROVIDER``. Unknown names raise.
        model: overrides the provider's default model.

    Returns:
        A ``BaseLLMClient`` configured for the selected provider.

    Raises:
        LLMConfigError: unknown provider, or the provider is listed in
            ``PROVIDERS`` but its module/class is missing (e.g. a provider
            in development). Provider-specific config errors (missing
            key, placeholder key) also raise ``LLMConfigError``, from the
            provider's own ``__init__``.
    """
    cfg = dict(config)  # never mutate the caller's dict
    name = (provider or cfg.get("LLM_PROVIDER") or DEFAULT_PROVIDER).lower()
    if name not in PROVIDERS:
        raise LLMConfigError(
            f"Unknown provider '{name}'. Available: {', '.join(PROVIDERS)}"
        )
    if model:
        # The key format is the same one each provider's __init__ reads,
        # which is what makes the override invisible to the provider code.
        cfg[f"{name.upper()}_MODEL"] = model

    module_name, class_name = PROVIDERS[name]
    module = importlib.import_module(module_name)
    try:
        cls = getattr(module, class_name)
    except AttributeError as e:
        # The provider is listed in PROVIDERS but its module does not yet
        # expose the expected class. This is the shape a not-yet-implemented
        # provider takes (see llm/ollama.py in v1.0).
        raise LLMConfigError(f"Provider '{name}' is not implemented yet.") from e
    return cls(cfg, auto_confirm=auto_confirm)
