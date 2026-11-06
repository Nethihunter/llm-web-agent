"""Local LLM client (Ollama).

Status: not implemented yet (planned for v1.2).

Design intent (see docs/ROADMAP.md, v1.2):
    Use a local, CPU-only model for **selector validation**, not for
    planning. Validating that a selector matches exactly one element is a
    short-prompt task: ~20 tokens in, a boolean out. On a 2018 i5, a 0.6B-
    or 1.7B-parameter model at ~5 tokens/sec is enough for that.

    Planning (``plan_next_step``) is *not* delegated to the local model in
    v1.2: the HTML reduction still produces ~1.5 KB prompts and the local
    model's JSON reliability is not yet benchmarked. Cloud providers remain
    the default for discovery.

Why this file is empty in v1.0:
    ``llm/factory.py`` lists ``"ollama"`` in ``PROVIDERS`` so the CLI
    accepts ``--provider ollama`` without crashing at argument-parsing
    time. The ``AttributeError`` branch in ``create_client`` turns the
    missing class into a clear ``LLMConfigError("Provider 'ollama' is not
    implemented yet.")``, which ``run.py`` maps to exit code 2. This is
    deliberate: the provider is *announced* before it is *usable*, so the
    roadmap and the code agree.
"""
from __future__ import annotations

from llm.base import BaseLLMClient, LLMConfigError, RawCompletion


class OllamaClient(BaseLLMClient):
    """Ollama provider (local models, CPU-only, no API key).

    Unlike the cloud providers, Ollama has no authentication and no
    per-token cost. The two consequences for this class:

    - ``PRICE_IN_PER_M`` / ``PRICE_OUT_PER_M`` stay ``None``: the cost
      estimate prints token counts but no dollar figure. Local compute is
      not free (electricity, time), but it is not billed per token either.
    - ``__init__`` does not raise on a missing key. It *does* raise if the
      local Ollama server cannot be reached, because that is the
      equivalent failure: the provider is configured but not available.

    The API shape is close to OpenAI's chat completions (``messages``,
    ``model``, ``stream``), but the endpoint is local
    (``http://localhost:11434/api/chat``) and the response format differs
    slightly: ``message.content`` instead of ``choices[0].message.content``.
    """

    provider_name = "ollama"
    DEFAULT_URL = "http://localhost:11434/api/chat"
    # Placeholder: replace with a model you have pulled locally. 0.6B is the
    # smallest Qwen3 variant, suitable for the selector-validation use case
    # described in the module docstring.
    DEFAULT_MODEL = "qwen3:0.6b"
    # Local models have no per-token price. Leaving these at None disables
    # the dollar estimate in BaseLLMClient._confirm.
    PRICE_IN_PER_M = None
    PRICE_OUT_PER_M = None

    def __init__(self, config: dict, auto_confirm: bool = False):
        """Read the Ollama URL and model from ``config``.

        No API key is read: the local Ollama server does not require one
        (see docs.ollama.com/api/authentication). A remote Ollama instance
        that does require a key is out of scope for v1.2.

        Raises:
            LLMConfigError: if the configured Ollama URL is unreachable at
                construction time. This mirrors the cloud providers' "key
                missing" check: both mean "the provider cannot be used".
        """
        super().__init__(config, auto_confirm)
        self.api_url = config.get("OLLAMA_API_URL", self.DEFAULT_URL)
        self.model = config.get("OLLAMA_MODEL", self.DEFAULT_MODEL)

    def _complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        """Send one chat request to the local Ollama server.

        Request shape (Ollama ``/api/chat``):

        - ``messages``: same as OpenAI, with a system message first;
        - ``stream: false``: the agent parses the whole reply as one JSON
          object, so streaming would only add complexity;
        - ``options.num_predict``: Ollama's equivalent of ``max_tokens``.
          Passing it through keeps the retry-with-larger-budget logic in
          ``BaseLLMClient.plan_next_step`` working unchanged.

        Response shape:

        - ``message.content``: the text to parse;
        - ``prompt_eval_count`` / ``eval_count``: Ollama's token counters.
          They map to ``input_tokens`` / ``output_tokens``.

        Raises:
            LLMError: if the local server returns a non-200 response or a
                body without ``message.content``. The base ``_post_json``
                handles HTTP retries, but Ollama errors are usually
                configuration problems (model not pulled) that will not
                fix themselves, so a single clear failure is better.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        body = self._post_json(self.api_url, {}, payload)
        # Ollama returns {"message": {"role": ..., "content": ...}, ...}.
        # A missing "message" usually means the model was not pulled:
        # the server returns 200 with an error body in some versions.
        message = body.get("message") or {}
        text = message.get("content", "")
        if not text:
            raise LLMConfigError(
                f"Ollama returned no content for model '{self.model}'. "
                f"Pull it first: ollama pull {self.model}"
            )
        return RawCompletion(
            text=text,
            input_tokens=body.get("prompt_eval_count", 0),
            output_tokens=body.get("eval_count", 0),
            cache_hit_tokens=0,  # local server does not expose a cache metric
            finish_reason=None,  # Ollama does not return one in /api/chat
        )
