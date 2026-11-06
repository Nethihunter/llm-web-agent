"""Dynamic selector validation via the real Playwright engine.

The static validator (BeautifulSoup) uses a CSS engine that differs slightly
from Chromium. Some selectors that are valid for Playwright fail in
BeautifulSoup (e.g. ``:has-text()``, ``:visible``). This module validates
the selector against the actual browser.

Usage:
    from validator_dynamic import validate_selector_in_browser
    result = validate_selector_in_browser(page, selector, action)
"""
from __future__ import annotations

from dataclasses import dataclass

# Tokens that only Playwright understands (not standard CSS).
PLAYWRIGHT_ONLY_TOKENS = (
    ":has-text(",
    ":contains(",
    "text=",
    ">>",
    ":visible",
)


@dataclass
class DynamicValidationResult:
    valid: bool
    reason: str
    match_count: int = 0


def is_playwright_selector(selector: str) -> bool:
    """True if the selector uses Playwright-only syntax."""
    return any(tok in selector for tok in PLAYWRIGHT_ONLY_TOKENS)


def validate_selector_in_browser(
    page, selector: str, action: str = "click"
) -> DynamicValidationResult:
    """Validate a selector against the live browser DOM.

    Uses ``locator.count()`` which counts attached elements (not only
    visible ones), so this matches what ``page.click()`` would target.
    """
    if not selector:
        return DynamicValidationResult(valid=False, reason="empty_selector")

    try:
        loc = page.locator(selector)
        count = loc.count()
    except Exception as e:
        return DynamicValidationResult(
            valid=False, reason=f"playwright_invalid: {e}"
        )

    if count == 0:
        return DynamicValidationResult(valid=False, reason="no_match_in_browser")

    if count > 1:
        return DynamicValidationResult(
            valid=False,
            reason=f"ambiguous_in_browser ({count} matches)",
            match_count=count,
        )

    # Semantic sanity checks on the matched element.
    try:
        tag_name = loc.evaluate("el => el.tagName.toLowerCase()")
        input_type = loc.evaluate("el => (el.type || '').toLowerCase()")
    except Exception:
        tag_name, input_type = "", ""

    mismatch = _check_mismatch(tag_name, input_type, action)
    if mismatch:
        return DynamicValidationResult(
            valid=False,
            reason=f"action_mismatch: {mismatch}",
            match_count=1,
        )

    return DynamicValidationResult(valid=True, reason="OK_browser", match_count=1)


def _check_mismatch(tag_name: str, input_type: str, action: str) -> str | None:
    if action == "click":
        if tag_name == "input" and input_type in ("text", "email", "password", "search"):
            return f"click on <input type={input_type}> (fill instead?)"
    elif action == "fill":
        if tag_name not in ("input", "textarea", "select"):
            return f"fill on <{tag_name}> (needs input/textarea/select)"
    elif action == "extract_text":
        if tag_name in ("input", "textarea"):
            return f"extract_text on <{tag_name}> (no text content)"
    return None
