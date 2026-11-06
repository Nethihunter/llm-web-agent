"""HTML reduction, iframe extraction and DOM signature.

Two views of a page can be sent to the LLM:

- ``mode="interactive"`` (default): buttons, inputs, links, forms, headings.
- ``mode="text"``: the same, plus text blocks (p, li, td, th, ...). Needed for
  extraction tasks, where the answer is text and not a clickable element.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

NOISE_TAGS = ["script", "style", "noscript", "svg", "path", "meta", "link",
              "iframe", "template"]
INTERACTIVE_TAGS = ["a", "button", "input", "textarea", "select", "option",
                    "form", "label"]
HEADING_TAGS = ["h1", "h2", "h3"]
TEXT_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th",
             "dt", "dd", "caption", "time", "blockquote"]

KEPT_ATTRS = ["id", "class", "name", "type", "href", "placeholder",
              "aria-label", "role", "data-testid", "data-test", "data-cy",
              "data-qa", "data-id", "title", "for"]
VALUE_TYPES = {"submit", "button", "reset", "checkbox", "radio"}
NO_TEXT_TAGS = {"input", "textarea", "form", "select"}

DEFAULT_MAX_CHARS = {"interactive": 4000, "text": 12000}
MAX_HREF_CHARS = 120
MAX_ELEMENT_TEXT = {"interactive": 200, "text": 1000}
MAX_CLASSES = 4
TRUNCATION_MARK = "... (truncated) ..."


def _norm(text: str) -> str:
    return " ".join(text.split())


def _hidden_itself(tag) -> bool:
    if tag.has_attr("hidden"):
        return True
    if tag.name == "input" and (tag.get("type") or "").lower() == "hidden":
        return True
    style = (tag.get("style") or "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _is_hidden(tag) -> bool:
    """Hidden itself, or inside a hidden ancestor."""
    if _hidden_itself(tag):
        return True
    return any(_hidden_itself(parent) for parent in tag.parents
               if getattr(parent, "attrs", None) is not None)


def _compact_classes(classes) -> str:
    """Drop utility-framework noise (md:flex, w-[200px], w-1/2) and cap the count."""
    if isinstance(classes, str):
        classes = classes.split()
    kept = [c for c in classes
            if not re.search(r"[:\[\]/]", c) and len(c) <= 25]
    return " ".join(kept[:MAX_CLASSES])


def _attr_value(tag, attr: str) -> str:
    value = tag.get(attr)
    if value is None:
        return ""
    if attr == "class":
        return _compact_classes(value)
    if isinstance(value, list):
        value = " ".join(value)
    value = str(value)
    if attr == "href" and len(value) > MAX_HREF_CHARS:
        value = value[:MAX_HREF_CHARS] + "..."
    return value


def _attr_string(tag) -> str:
    parts = []
    for attr in KEPT_ATTRS:
        if tag.has_attr(attr):
            value = _attr_value(tag, attr)
            if value:
                parts.append(f'{attr}="{value}"')
    # 'value' is useful for buttons / choices, but would leak typed user data
    # if we kept it on text inputs.
    keep_value = tag.name == "option" or tag.name == "button" or (
        tag.name == "input" and (tag.get("type") or "").lower() in VALUE_TYPES
    )
    if keep_value and tag.has_attr("value"):
        parts.append(f'value="{_attr_value(tag, "value")}"')
    return " ".join(parts)


def _truncate(text: str, max_chars: int) -> str:
    """Keep the head (70%) and the tail (30%), cutting on line boundaries.

    The tail matters: footers often hold facts such as 'last edited on ...'.
    """
    if len(text) <= max_chars:
        return text
    head_budget = int(max_chars * 0.7)
    tail_budget = max_chars - head_budget
    lines = text.split("\n")

    head, used = [], 0
    for line in lines:
        if used + len(line) + 1 > head_budget:
            break
        head.append(line)
        used += len(line) + 1

    tail, used = [], 0
    for line in reversed(lines[len(head):]):
        if used + len(line) + 1 > tail_budget:
            break
        tail.append(line)
        used += len(line) + 1
    tail.reverse()
    return "\n".join(head + [TRUNCATION_MARK] + tail)


def extract_clean_html(html: str, max_chars: int | None = None,
                       mode: str = "interactive") -> str:
    if mode not in DEFAULT_MAX_CHARS:
        raise ValueError(f"mode must be one of {list(DEFAULT_MAX_CHARS)}")
    if max_chars is None:
        max_chars = DEFAULT_MAX_CHARS[mode]

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(NOISE_TAGS):
        tag.decompose()

    wanted = INTERACTIVE_TAGS + HEADING_TAGS
    if mode == "text":
        wanted = wanted + TEXT_TAGS
    wanted = list(dict.fromkeys(wanted))
    max_text = MAX_ELEMENT_TEXT[mode]

    lines = []
    for tag in soup.find_all(wanted):
        if _is_hidden(tag):
            continue

        if mode == "text" and tag.name in TEXT_TAGS:
            if tag.find(TEXT_TAGS):          # only leaf blocks: no duplicates
                continue
            inner = tag.find(["a", "button"])
            if inner and _norm(inner.get_text()) == _norm(tag.get_text()):
                continue                     # <li><a>x</a></li>: keep the <a>

        attrs = _attr_string(tag)
        open_tag = f"<{tag.name} {attrs}>" if attrs else f"<{tag.name}>"
        text = ""
        if tag.name not in NO_TEXT_TAGS:
            text = _norm(tag.get_text(" ", strip=True))[:max_text]

        if tag.name == "input":
            lines.append(open_tag)
        elif text:
            lines.append(f"{open_tag}{text}</{tag.name}>")
        else:
            lines.append(open_tag)

    return _truncate("\n".join(lines), max_chars)


def get_dom_signature(html: str) -> str:
    """Fingerprint of the interactive structure.

    Includes href and visible text, so two pages made only of links do not
    share a signature (a stale plan is worse than one extra LLM call).
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(NOISE_TAGS):
        tag.decompose()

    sig_attrs = ["id", "name", "type", "data-testid", "data-test",
                 "aria-label", "href"]
    parts = []
    for tag in soup.find_all(["a", "button", "input", "textarea", "select", "form"]):
        attrs = [f"{a}={_attr_value(tag, a)}" for a in sig_attrs if tag.has_attr(a)]
        label = ""
        if tag.name not in NO_TEXT_TAGS:
            label = _norm(tag.get_text(" ", strip=True))[:40]
        parts.append(f"{tag.name}[{','.join(attrs)}]{label}")
    parts.sort()
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _origin(url: str) -> str:
    p = urlparse(url or "")
    return f"{p.scheme}://{p.netloc}"


def extract_iframe_contents(page, max_chars_per_frame: int = 2000,
                            mode: str = "interactive") -> str:
    """Cleaned HTML of the same-origin iframes of a Playwright page.

    ``page.content()`` only returns the main frame. Cross-origin frames
    (ads, trackers) are skipped on purpose.
    """
    main_origin = _origin(page.url)
    blocks = []
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            url = frame.url or ""
            if not url.startswith("about:") and _origin(url) != main_origin:
                continue
            clean = extract_clean_html(frame.content(), max_chars_per_frame, mode)
        except Exception:
            continue
        if clean:
            blocks.append(f"<!-- iframe src={url} -->\n{clean}")
    return "\n".join(blocks)


def reduction_stats(raw_html: str, clean_html: str) -> dict:
    """Numbers for a benchmark (1 token is roughly 4 characters)."""
    raw_chars, clean_chars = len(raw_html), len(clean_html)
    pct = (1 - clean_chars / raw_chars) * 100 if raw_chars else 0.0
    return {
        "raw_chars": raw_chars,
        "clean_chars": clean_chars,
        "raw_tokens": raw_chars // 4,
        "clean_tokens": clean_chars // 4,
        "reduction_pct": round(pct, 1),
    }
