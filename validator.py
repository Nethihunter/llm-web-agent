"""Static selector validation with BeautifulSoup.

This is the first of the three validation layers described in
``docs/ARCHITECTURE.md``:

1. ``validator.py``         — static: does the selector match exactly one
                              element in the DOM? No browser needed.
2. ``validator_dynamic.py`` — dynamic: does the real Playwright engine agree?
3. post-execution check    — did the action actually change the page?
                              (see ``agent._post_step_check``).

The ``failure_type`` values are stable identifiers used by the agent loop
and by the tests (``no_match``, ``ambiguous``, ``invalid_css``,
``null_selector``, ``confidently_wrong``). Only the human-readable
``reason`` strings are meant to change.
"""
from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

# An LLM that reports high confidence on a selector must also be right about
# the action it wants to perform on that element. Below this threshold the
# mismatch is tolerated: the dynamic validator will catch it.
CONFIDENCE_CHECK_THRESHOLD = 0.8

TEXT_INPUT_TYPES = ("text", "email", "password", "search", "tel", "url")


@dataclass
class ValidationResult:
    valid: bool
    reason: str
    match_count: int = 0
    failure_type: str | None = None


def validate_llm_output(html: str, llm_output: dict) -> ValidationResult:
    """Validate one LLM plan against the current page HTML.

    Returns a ``ValidationResult``. ``failure_type`` is one of the stable
    identifiers listed in the module docstring; ``reason`` is a short,
    human-readable explanation suitable for logs and for the next LLM call.
    """
    selector = llm_output.get("selector")
    confidence = llm_output.get("confidence", 0.0)

    if selector is None:
        return ValidationResult(
            valid=False,
            reason="LLM returned selector=null",
            failure_type="null_selector",
        )

    try:
        soup = BeautifulSoup(html, "lxml")
        matches = soup.select(selector)
    except Exception as e:
        return ValidationResult(
            valid=False,
            reason=f"Invalid CSS: {e}",
            failure_type="invalid_css",
        )

    if len(matches) == 0:
        return ValidationResult(
            valid=False,
            reason="Selector is valid but matches 0 elements",
            failure_type="no_match",
        )

    if len(matches) > 1:
        return ValidationResult(
            valid=False,
            reason=f"Ambiguous selector: {len(matches)} elements match",
            match_count=len(matches),
            failure_type="ambiguous",
        )

    # Only check the action/element coherence when the model is sure of
    # itself. A low-confidence wrong action is a normal failure that the
    # LLM will correct on the next step; a high-confidence one is the
    # "confidently wrong" failure mode documented in docs/AI_ASSISTED.md.
    if confidence >= CONFIDENCE_CHECK_THRESHOLD:
        element = matches[0]
        action = llm_output.get("action")
        mismatch = _check_action_mismatch(element, action)
        if mismatch:
            return ValidationResult(
                valid=False,
                reason=f"Confidently wrong: {mismatch}",
                match_count=1,
                failure_type="confidently_wrong",
            )

    return ValidationResult(valid=True, reason="OK", match_count=1)


def _check_action_mismatch(element, action: str) -> str | None:
    """Return a human-readable reason if ``action`` cannot target ``element``.

    Returns ``None`` when the action is plausible for the element.
    """
    tag = element.name

    if action == "click":
        if tag == "input":
            input_type = (element.get("type") or "text").lower()
            if input_type in TEXT_INPUT_TYPES:
                return "action=click on a text input (use fill instead)"

    elif action == "fill":
        if tag not in ("input", "textarea", "select"):
            return f"action=fill on <{tag}> (expected input/textarea/select)"

    elif action == "extract_text":
        if not element.get_text(strip=True):
            return "action=extract_text on an empty element"

    return None
