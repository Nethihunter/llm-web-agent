"""Common interface for LLM providers.

A provider only has to implement ``_complete()`` (one HTTP call). Everything
else lives here, so each provider stays small:

- prompt building (stable content first, variable content last, so that
  provider-side prefix caches can hit),
- cost estimate + human confirmation,
- HTTP retries (429 / 5xx / network),
- JSON parsing and validation of the plan,
- retry with a larger token budget when the JSON is truncated.

Why a single base class:
    Every provider (DeepSeek, OpenAI, Anthropic, Grok, Ollama) shares the
    same failure modes: truncated JSON, hallucinated fields, HTTP 429,
    prompt cache misses. Fixing them once here means a new provider is
    30-60 lines (``_complete`` plus a few constants) and inherits all the
    robustness. See ``docs/LLM_PROVIDERS.md`` for the comparison table and
    ``docs/AI_ASSISTED.md`` for the failure modes that shaped this design.

Prompt ordering rule:
    The user message is built as ``GOAL`` → ``PAGE HTML`` → ``HISTORY``,
    with ``GOAL`` and ``PAGE HTML`` being stable across consecutive calls
    on the same page. Provider-side prefix caches (DeepSeek, OpenAI) only
    hit on an *identical prefix*, so putting variable content (``HISTORY``)
    last is what makes the cache effective. This was Failure Mode 3 in
    ``docs/AI_ASSISTED.md``: reversing the order took the DeepSeek cache
    hit rate from ~30% back to 0%.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests


ALLOWED_ACTIONS = ("click", "fill", "extract_text")
REQUIRED_FIELDS = (
    "is_goal_reached",
    "thought",
    "selector",
    "action",
    "confidence",
    "limitations",
)

SYSTEM_PROMPT = """You are a supervised browser-automation agent.
You receive a global goal and a reduced HTML view of the current page
(interactive elements and, sometimes, text blocks). Decide the single NEXT
action that brings you closer to the goal.

Reply with ONE valid JSON object and nothing else (no Markdown, no text
before or after), with exactly this structure:
{
  "is_goal_reached": true | false,
  "thought": "What you see and what you will do (1-2 sentences).",
  "selector": "CSS selector, or null",
  "action": "click" | "fill" | "extract_text" | null,
  "value": "string or null",
  "extracted_data": object | null,
  "confidence": 0.0,
  "limitations": "What you cannot guarantee"
}

RULES:
1. The selector must be valid CSS, must exist in the provided HTML and must
   target exactly ONE element. Never invent an attribute (data-testid, id,
   class) that is not in the HTML.
2. Selector priority: data-testid / data-test > id > name > aria-label >
   specific CSS class. Never use XPath.
3. "fill" requires "value". When a variable applies, write it as a
   placeholder such as {username}. Never guess or invent secret values.
4. Set is_goal_reached to true only when the page clearly shows that the goal
   is done. If the goal asks for data, return it in "extracted_data" in that
   same reply, following the requested structure, and set action to null.
5. If you are not sure, set confidence below 0.5 and explain in
   "limitations". If nothing matches, use selector null, action null,
   confidence 0.0.
6. All fields must be present, even when null. No trailing commas.
7. NEVER repeat an action that already succeeded. Check the HISTORY:
   if you already clicked "Add to cart" on this item, do not click it again.
   If the button still says "Add to cart", it may be a different item — verify
   the item name in the HTML before clicking.
"""


class LLMError(RuntimeError):
    """Any failure while talking to an LLM provider."""


class LLMConfigError(LLMError):
    """Missing key, unknown provider, unreadable key.json (exit code 2)."""


class LLMCancelled(LLMError):
    """The user declined the confirmation prompt."""


class LLMParseError(LLMError):
    """The reply is not a usable JSON object (often: truncated)."""


@dataclass
class RawCompletion:
    """What a provider returns for one HTTP call.

    Raw, unvalidated output. The JSON may be truncated, malformed, or
    contain unknown actions — ``BaseLLMClient.parse_plan`` is what turns
    this into a trusted plan. The token counts are what the provider
    reported, including any cache-hit breakdown.
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    finish_reason: str | None = None


@dataclass
class LLMResponse:
    """A validated plan plus usage, summed over all attempts.

    Usage is summed, not per-attempt: if the first reply was truncated and
    the retry succeeded, both API calls were billed and both contribute to
    ``input_tokens`` / ``output_tokens``. That is what the dashboard shows
    and what ``WorkflowManager.log_run_end`` persists.
    """

    data: dict
    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int
    provider: str
    model: str


class BaseLLMClient(ABC):
    """Shared behavior for every LLM provider.

    Subclasses must set ``provider_name`` and (usually) ``model``, and
    implement ``_complete``. Everything else is provided here.

    The class-level constants below are tunable per provider by
    overriding them in the subclass — for example ``PRICE_IN_PER_M`` /
    ``PRICE_OUT_PER_M`` to enable the cost estimate, or ``DEFAULT_MODEL``
    to pin a specific model. The defaults reflect the DeepSeek pricing
    because DeepSeek is the default provider (see
    ``docs/LLM_PROVIDERS.md``).
    """

    provider_name: str = "base"
    model: str = ""

    # Conservative ratio for French + HTML content (see estimate_cost).
    CHARS_PER_TOKEN = 3.2
    ESTIMATED_OUTPUT_TOKENS = 250
    HISTORY_WINDOW = 5
    MAX_HTML_CHARS = 16000
    DEFAULT_MAX_TOKENS = 800
    RETRY_MAX_TOKENS = 1200
    PARSE_ATTEMPTS = 2
    HTTP_ATTEMPTS = 3
    # Indicative prices in USD per 1M tokens; None disables the cost estimate.
    PRICE_IN_PER_M: float | None = None
    PRICE_OUT_PER_M: float | None = None

    def __init__(self, config: dict, auto_confirm: bool = False):
        self.auto_confirm = auto_confirm

    # ------------------------------------------------------------------
    # To implement in each provider
    # ------------------------------------------------------------------
    @abstractmethod
    def _complete(self, system: str, user: str, max_tokens: int) -> RawCompletion:
        """Perform ONE API call and return the raw completion.

        Implementations must:
        - send ``system`` and ``user`` as the provider expects (some
          providers have a top-level ``system`` field, others use a
          system message);
        - honor ``max_tokens``;
        - return the text exactly as received (do not try to parse JSON
          here — that is ``parse_plan``'s job);
        - fill in the token counts from the provider's ``usage`` field
          when available, 0 otherwise.

        Errors (network, HTTP 4xx that is not a retryable 429, non-JSON
        200) must raise ``LLMError`` or let the exception propagate:
        ``_post_json`` already classifies the common cases.
        """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def plan_next_step(
        self,
        html: str,
        goal: str,
        history: list[str],
        previous_error: str | None = None,
        variable_names: list[str] | None = None,
    ) -> LLMResponse:
        """Ask the LLM for the single next action, with retry on bad JSON.

        Two attempts are made, both hitting the same endpoint:

        - attempt 1 with ``DEFAULT_MAX_TOKENS`` (800 by default);
        - attempt 2 with ``RETRY_MAX_TOKENS`` (1200), only if the first
          reply was unusable (truncated, malformed, or rejected by
          ``parse_plan``).

        The retry doubles the cost when it happens, so it is better to
        avoid it than to rely on it: the first-attempt size is chosen so
        that ~95% of plans fit.

        Raises:
            LLMCancelled: the user declined the confirmation prompt.
            LLMError: any other failure, including two consecutive
                unusable JSON replies.
        """
        user = self._build_user_message(
            html, goal, history, previous_error, variable_names
        )
        self._confirm(user)

        max_tokens = self.DEFAULT_MAX_TOKENS
        in_tok = out_tok = cache_tok = 0
        last_error: Exception | None = None

        for attempt in range(1, self.PARSE_ATTEMPTS + 1):
            raw = self._complete(SYSTEM_PROMPT, user, max_tokens)
            in_tok += raw.input_tokens
            out_tok += raw.output_tokens
            cache_tok += raw.cache_hit_tokens
            print(
                f"   [API] IN={raw.input_tokens} OUT={raw.output_tokens} "
                f"| Cache Hit={raw.cache_hit_tokens}"
            )
            try:
                # ``finish_reason == "length"`` means the provider
                # truncated the output to fit ``max_tokens``. The JSON is
                # almost certainly cut mid-object, so we skip the parse
                # attempt and go straight to the retry with a larger budget.
                if raw.finish_reason == "length":
                    raise LLMParseError("response cut by max_tokens")
                data = self.parse_plan(raw.text)
            except LLMParseError as e:
                last_error = e
                print(f"⚠️ Unusable JSON (attempt {attempt}): {e}")
                max_tokens = self.RETRY_MAX_TOKENS
                continue
            return LLMResponse(
                data=data,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_hit_tokens=cache_tok,
                provider=self.provider_name,
                model=self.model,
            )

        raise LLMError(
            f"No valid JSON after {self.PARSE_ATTEMPTS} attempts: {last_error}"
        )

    # ------------------------------------------------------------------
    # Prompt, cost, confirmation
    # ------------------------------------------------------------------
    def _build_user_message(
        self, html, goal, history, previous_error, variable_names
    ) -> str:
        """Assemble the user message, stable content first.

        Section order is fixed on purpose (see the module docstring):
        ``GOAL`` → ``AVAILABLE VARIABLES`` → ``PAGE HTML`` → ``HISTORY``
        → ``PREVIOUS ERROR``. Prefix caches only hit on an identical
        prefix, so the two large, mostly-stable sections (goal and HTML)
        must come before the per-step variable ones.

        Only variable *names* are included, never their values: secrets
        must not leave the machine, even to an LLM the user trusts.
        """
        recent = history[-self.HISTORY_WINDOW:] if history else []
        history_str = "\n".join(recent) if recent else "(start of the task)"
        parts = [f"GOAL: {goal}"]
        if variable_names:
            # Names only. Values never leave the machine.
            parts.append(
                "AVAILABLE VARIABLES (use as {name} in 'value'): "
                + ", ".join(sorted(variable_names))
            )
        parts.append(f"PAGE HTML (reduced):\n{html[: self.MAX_HTML_CHARS]}")
        parts.append(f"HISTORY (last {self.HISTORY_WINDOW} actions):\n{history_str}")
        if previous_error:
            parts.append(f"PREVIOUS ERROR: {previous_error}\nFix your action.")
        return "\n\n".join(parts)

    def estimate_cost(self, user: str) -> tuple[int, int, float | None]:
        """Estimate input/output tokens before the API call.

        Uses a conservative ratio for French + HTML content:
        1 token ≈ 3.2 characters. This over-estimates slightly, so the
        user is never surprised by a higher actual cost.
        The provider's response contains the real counts, which are
        displayed after the call.

        Returns:
            ``(estimated_input_tokens, estimated_output_tokens, cost)``.
            ``cost`` is ``None`` when the provider does not declare its
            prices (Grok, Ollama): in that case the user still sees the
            token estimate but no dollar figure.
        """
        est_in = int((len(SYSTEM_PROMPT) + len(user)) / self.CHARS_PER_TOKEN) + 1
        est_out = self.ESTIMATED_OUTPUT_TOKENS
        cost = None
        if self.PRICE_IN_PER_M is not None and self.PRICE_OUT_PER_M is not None:
            cost = (
                est_in * self.PRICE_IN_PER_M + est_out * self.PRICE_OUT_PER_M
            ) / 1_000_000
        return est_in, est_out, cost

    def _confirm(self, user: str) -> None:
        """Print the cost estimate and, unless auto-confirmed, ask the user.

        This is the human-in-the-loop part of the design: every API call
        can be vetoed before it is sent. ``--yes`` on the CLI sets
        ``auto_confirm=True``, which is what makes batch runs (CI, the
        dashboard "Run" button) possible.

        Raises:
            LLMCancelled: the user answered anything other than yes/oui/o.
        """
        est_in, est_out, cost = self.estimate_cost(user)
        print(f"\n💰 [ESTIMATE] Tokens IN: ≈{est_in} | OUT: ≈{est_out}")
        print("   (approximate — real values are shown after the API call)")
        if cost is not None:
            print(f"💵 Estimated cost: ~${cost:.5f} (indicative prices)")
        if self.auto_confirm:
            print("   [auto-confirm] sending...")
            return
        answer = input(
            f"❓ Send this request to {self.provider_name} "
            f"(≈{est_in} in / ≈{est_out} out tokens)? (y/n): "
        )
        if answer.strip().lower() not in ("y", "yes", "o", "oui"):
            raise LLMCancelled("Request cancelled by the user.")

    # ------------------------------------------------------------------
    # HTTP helper shared by providers
    # ------------------------------------------------------------------
    def _post_json(self, url: str, headers: dict, payload: dict, timeout: int = 60):
        """POST a JSON payload and return the parsed JSON body.

        Retries on the two classes of error that a client can safely
        retry:

        - ``429`` (rate limit): back off and try again;
        - ``5xx`` (server error): same.

        Does **not** retry on ``4xx`` other than ``429``: a 400/401/403
        will not fix itself, and retrying would just multiply the bill.
        A non-JSON 200 body is also a hard error (some providers return
        an HTML error page with status 200).

        Backoff is exponential: 1s, 2s, … between attempts. This is short
        by design (``HTTP_ATTEMPTS = 3``); a run that hits it will fail
        with a clear message rather than hang for minutes.
        """
        last_error = None
        for attempt in range(self.HTTP_ATTEMPTS):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            except requests.RequestException as e:
                last_error = f"network error: {e}"
            else:
                if r.status_code == 200:
                    try:
                        return r.json()
                    except ValueError as e:
                        raise LLMError(f"Non-JSON HTTP 200 response: {e}") from e
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    last_error = f"HTTP {r.status_code}"
                else:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
            if attempt < self.HTTP_ATTEMPTS - 1:
                time.sleep(2**attempt)
        raise LLMError(f"Retries exhausted (last error: {last_error})")

    # ------------------------------------------------------------------
    # Parsing and validation
    # ------------------------------------------------------------------
    @staticmethod
    def parse_plan(raw: str) -> dict:
        """Turn a raw LLM reply into a validated plan dict.

        Three layers of cleaning, in order:

        1. **Markdown fences**: some models wrap the JSON in ```json … ```
           even when told not to. We extract the first fenced block that
           starts with ``{``.
        2. **Surrounding text**: even without fences, a model may add a
           sentence before or after the JSON. We take everything from the
           first ``{`` to the last ``}``.
        3. **Schema checks**: every field in ``REQUIRED_FIELDS`` must be
           present; ``confidence`` is clamped to ``[0, 1]``; action/selector
           coherence is enforced (see below).

        Coherence rules when ``is_goal_reached`` is false:
        - ``action`` must be one of ``ALLOWED_ACTIONS`` or ``None``;
        - ``click`` and ``extract_text`` require a ``selector``;
        - ``fill`` requires both a ``selector`` and a ``value``.

        Raises:
            LLMParseError: the reply is not a JSON object (this includes
                truncated replies — the most common case in practice).
            LLMError: the JSON is valid but does not match the contract
                (missing field, unknown action, missing selector/value).
                This distinction matters: ``LLMParseError`` triggers the
                retry with a larger token budget, ``LLMError`` does not.
        """
        cleaned = (raw or "").strip()

        if "```" in cleaned:
            for part in cleaned.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    cleaned = part
                    break

        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise LLMParseError(f"no JSON object found: {cleaned[:200]!r}")

        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as e:
            raise LLMParseError(f"invalid JSON: {e}") from e
        if not isinstance(data, dict):
            raise LLMParseError("JSON is not an object")

        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise LLMError(f"Missing fields: {missing} (got: {list(data)})")

        try:
            data["confidence"] = min(1.0, max(0.0, float(data["confidence"])))
        except (TypeError, ValueError):
            data["confidence"] = 0.0
        data.setdefault("value", None)
        data.setdefault("extracted_data", None)

        if not data["is_goal_reached"]:
            action = data["action"]
            if action is not None and action not in ALLOWED_ACTIONS:
                raise LLMError(f"Unknown action: {action!r}")
            if action in ALLOWED_ACTIONS and not data["selector"]:
                raise LLMError(f"Action '{action}' without selector.")
            if action == "fill" and not data["value"]:
                raise LLMError("Action 'fill' without value.")
        return data
