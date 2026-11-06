"""Click with popup and overlay handling.

Ad-heavy pages and modals can intercept a click. This module:

- dismisses common cookie/newsletter overlays before clicking,
- closes popup tabs that appear after a click,
- refocuses the main tab,
- waits for the URL or DOM to change.

Popup policy:
    Popups whose URL is on another domain are treated as ads and closed.
    Same-site popups are kept, and so are popups still on ``about:blank``
    (OAuth and payment windows start blank; closing them early would break
    the flow).

Synchronization policy:
    After a click, the module waits for one of:
    - a URL change (``_wait_url_change``), then for the new page to be
      ready (``_wait_page_ready``, "networkidle" + a 4 s cap);
    - no URL change (SPA, "Add to cart"), in which case a short 1.5 s
      settle is enough.

    A small ``time.sleep(0.8)`` before the popup check gives the browser
    a chance to actually create the popup window. Without it, ``context.pages``
    often does not contain the new tab yet on slow sites.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlparse

# Substrings that identify known ad / tracker networks. A popup whose URL
# contains one of these is closed even if the main domain also appears in
# the URL (e.g. "https://doubleclick.net/?u=site.com").
# Rule when adding an entry: it must be a domain fragment, not a generic
# word ("ads" would match "uploads"), and it must be well-known enough that
# a false positive is unlikely.
SUSPICIOUS_URL_FRAGMENTS = (
    "doubleclick", "googlesyndication", "googleadservices",
    "google-analytics", "googletagmanager",
    "adservice", "adsystem", "popads", "popcash", "propellerads",
    "adnxs", "taboola", "outbrain", "criteo", "amazon-adsystem",
    "facebook.com/tr",       # Facebook pixel
)

# Overlay selectors, ordered from most specific to most generic. The
# first one that is visible and clickable wins, then we stop: clicking
# every match would dismiss the main dialog if it also matches a generic
# "Close" button.
COMMON_OVERLAY_SELECTORS = (
    "button:has-text('Accept')",
    "button:has-text('Accepter')",
    "button:has-text('OK')",
    "button:has-text('Got it')",
    "button:has-text('Close')",
    "button:has-text('Fermer')",
    "[aria-label='Close']",
    "[aria-label='Fermer']",
    "button[class*='close']",
    "button[class*='dismiss']",
    ".modal-close",
    "#onetrust-accept-btn-handler",
)


def _domain_of(url: str) -> str:
    """Return the lowercased ``netloc`` of ``url``, or ``""`` on error."""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _strip_www(host: str) -> str:
    """Remove a leading ``www.`` so that both forms compare equal."""
    return host[4:] if host.startswith("www.") else host


def _same_site(url: str, main_domain: str) -> bool:
    """Same site, ignoring 'www.' and allowing sub-domains in both directions.

    "Same site" here is deliberately loose: ``pay.site.com`` and
    ``site.com`` are considered the same site, because payment and OAuth
    flows legitimately cross sub-domains. The check is only used to decide
    whether a popup is an ad; a stricter same-origin check would wrongly
    close the payment popup.
    """
    host = _strip_www(_domain_of(url))
    main = _strip_www(main_domain.lower())
    if not host or not main:
        return False
    return host == main or host.endswith("." + main) or main.endswith("." + host)


def _is_likely_ad(url: str, main_domain: str) -> bool:
    """Heuristic: is ``url`` an ad popup that should be closed?

    Returns ``False`` (keep) for:
    - empty URLs and ``about:*``: the popup has not navigated yet. OAuth
      and payment windows start on ``about:blank`` and would break if
      closed.
    - same-site URLs (see ``_same_site``).

    Returns ``True`` (close) for:
    - any URL containing a fragment from ``SUSPICIOUS_URL_FRAGMENTS``,
    - any cross-site URL.
    """
    if not url or url.startswith("about:"):
        return False                      # not navigated yet: keep it
    low = url.lower()
    if any(frag in low for frag in SUSPICIOUS_URL_FRAGMENTS):
        return True
    return not _same_site(url, main_domain)


def _dismiss_common_overlays(page) -> None:
    """Click the first visible overlay-dismiss button, if any.

    Tries selectors in order and stops at the first successful click.
    Failures are silent: a missing overlay is the normal case, and an
    overlay we cannot click is not a reason to fail the whole step.
    """
    for sel in COMMON_OVERLAY_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=300):
                loc.click(timeout=1500)
                print(f"   🧹 Overlay closed: {sel}")
                page.wait_for_timeout(200)
                return
        except Exception:
            continue


def _wait_url_change(page, old_url: str, timeout_ms: int = 1500) -> bool:
    """Wait until ``window.location.href`` differs from ``old_url``.

    ``json.dumps`` is used to build the JS literal: it escapes quotes and
    backslashes the way a JS string literal expects, which a naive
    ``f'"{old_url}"'`` would not.

    Returns ``True`` on change, ``False`` on timeout (no exception).
    """
    try:
        page.wait_for_function(
            f"() => window.location.href !== {json.dumps(old_url)}",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


def _wait_page_ready(page, timeout_ms: int = 4000) -> None:
    """Best-effort wait for the page to settle after a navigation.

    Two-stage strategy:

    1. ``wait_for_load_state("networkidle")`` — accurate on classic sites,
       but deprecated in recent Playwright versions (it can wait forever
       on pages with long-polling or WebSocket traffic). The timeout caps
       that, and the caller usually passes a small value.
    2. Fallback: wait for a common interactive element to be attached.
       This catches SPAs that fire "networkidle" before the UI is mounted.

    Both stages swallow their exceptions: this is a synchronization hint,
    not a correctness check. The post-step check (if any) is responsible
    for deciding whether the click actually did something.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        return
    except Exception:
        pass
    try:
        page.wait_for_selector(
            "input, button, a[href], form, [role='button']",
            timeout=2000,
            state="attached",
        )
    except Exception:
        pass


def safe_click_with_popup_handling(context, page, selector: str):
    """Click ``selector``, handle popups, return the active page.

    This is the click primitive used by ``agent.py`` in both discovery and
    replay modes. It is defensive by design: an ad-heavy page must not
    crash the run.

    Behavior:
        1. If ``page`` is already closed, recover an active page from
           ``context``.
        2. Try the click. On failure, dismiss common overlays and retry
           once.
        3. Wait 0.8 s for any popup to appear.
        4. For each new tab: close it if it is an ad, keep it otherwise.
           If the main tab was closed during the click and a popup was
           kept, switch to the first kept popup.
        5. Bring the active page to the front.
        6. Wait for either a URL change or a short settle.

    Args:
        context: a Playwright ``BrowserContext``. Used to enumerate and
            close popups.
        page: the current ``Page``. May be closed when the function is
            called, but must be a valid Playwright page object.
        selector: CSS selector (or Playwright selector) of the element to
            click.

    Returns:
        A ``Page`` that is guaranteed to be open. It may be a **different
        object** than the ``page`` argument if the original was closed and
        another tab was kept.

    Raises:
        RuntimeError: if no page is open before the click, or if all tabs
            ended up closed after it. These are unrecoverable: the caller
            has no page to continue on.
    """
    print(f"🖱️ Click: {selector}")

    if page.is_closed():
        page = _recover_page(context)
        if page is None:
            raise RuntimeError("No page available for click")

    main_domain = _domain_of(page.url)
    pages_before = set(context.pages)
    old_url = page.url

    try:
        # force=False: if the element is covered by an overlay, we want
        # Playwright to time out so we can dismiss the overlay. force=True
        # would dispatch the click through the overlay and mask the real
        # problem.
        page.click(selector, timeout=5000, force=False)
    except Exception as e:
        print(f"   ⚠️ Click failed ({type(e).__name__}), retrying after overlay dismiss")
        _dismiss_common_overlays(page)
        page.click(selector, timeout=5000, force=False)

    # Give the browser time to actually create the popup window.
    # Without this, `context.pages` often does not yet contain the new tab
    # on slow sites.
    time.sleep(0.8)
    new_pages = set(context.pages) - pages_before

    if new_pages:
        kept = []
        closed = 0
        for popup in new_pages:
            try:
                popup_url = popup.url if not popup.is_closed() else ""
            except Exception:
                popup_url = ""
            if _is_likely_ad(popup_url, main_domain):
                try:
                    popup.close()
                    closed += 1
                    print(f"   🧹 Ad closed: {popup_url[:80]}")
                except Exception:
                    pass
            else:
                kept.append(popup)
                print(f"   📌 Tab kept: {popup_url[:80]}")
        if closed:
            print(f"🧹 {closed} ad popup(s) closed")
        # The click may have closed the main tab (e.g. "target=_blank"
        # navigation on some sites). In that case, follow the first kept
        # popup instead of trying to use a dead page.
        if page.is_closed() and kept:
            page = kept[0]

    if not page.is_closed():
        try:
            page.bring_to_front()
        except Exception:
            pass

    # Navigation: wait for the new page. No URL change (SPA, "Add to cart"):
    # a short settle is enough, no need to burn several seconds per click.
    if _wait_url_change(page, old_url, timeout_ms=1500):
        _wait_page_ready(page, timeout_ms=4000)
    else:
        _wait_page_ready(page, timeout_ms=1500)

    if page.is_closed():
        page = _recover_page(context)
        if page is None:
            raise RuntimeError("All tabs closed after click")
    return page


def _recover_page(context):
    """Return an open page from ``context``, creating one if needed.

    Preference order: the first open tab (brought to the front), then a
    freshly created page. Returns ``None`` if even ``context.new_page()``
    fails, which means the browser context itself is dead.
    """
    for p in context.pages:
        try:
            if not p.is_closed():
                p.bring_to_front()
                return p
        except Exception:
            continue
    try:
        return context.new_page()
    except Exception:
        return None
