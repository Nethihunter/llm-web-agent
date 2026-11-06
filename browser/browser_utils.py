"""Browser connection utilities: local Chrome, containerized Chrome, remote CDP.

Three connection strategies, one per deployment shape:

- ``connect_local_chrome``:    Chrome already running on the host (dev).
- ``connect_container_chrome``: Chrome started by ``BrowserContainerManager``
                                via the Docker CLI (prod on a single host).
- ``connect_remote_chrome``:   Chrome running in another container, reached
                                over the Docker network (docker-compose).

All three end up calling ``_connect_and_prepare``, which is the only place
that creates a Playwright browser context. This keeps the "one context per
run" invariant: the agent opens a single ``sync_playwright()`` and every
connection path reuses it.

CDP quirks worth knowing:
    Chrome's DevTools HTTP server rejects requests whose ``Host`` header is
    neither an IP address nor ``localhost``. A Docker service name like
    ``http://browser:9222`` therefore fails at the HTTP handshake, even
    though the TCP connection succeeds. ``resolve_cdp_url`` works around
    this by resolving the hostname to an IP before connecting.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlparse

import requests


def resolve_cdp_url(cdp_url: str) -> str:
    """Replace a hostname by its IP address.

    Chrome's DevTools HTTP server rejects requests whose Host header is
    neither an IP address nor "localhost" ("Host header is specified and is
    not an IP address or localhost"). A Docker service name such as
    ``http://browser:9222`` therefore fails: connect to its IP instead.

    If the host is already an IP, is ``localhost``, or cannot be resolved,
    the URL is returned unchanged: the connection error (if any) will be
    clearer than a resolution error raised here.
    """
    parts = urlparse(cdp_url)
    host = parts.hostname
    if not host or host == "localhost":
        return cdp_url
    try:
        ipaddress.ip_address(host)
        return cdp_url                     # already an IP
    except ValueError:
        pass
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return cdp_url                     # let the connection error speak
    netloc = f"{ip}:{parts.port}" if parts.port else ip
    return parts._replace(netloc=netloc).geturl()


def _wait_for_cdp(http_url: str, timeout: float = 20.0, interval: float = 0.5) -> None:
    """Wait until ``<http_url>/json/version`` answers, or raise a clear error.

    This is a readiness probe, not a connection: it only checks that the
    DevTools HTTP server is up. Playwright's ``connect_over_cdp`` performs
    the actual WebSocket handshake afterward.

    The error message names the two most common causes in this project
    (Docker container not started, socat not forwarding the port) so the
    user does not have to read the code to understand the failure.
    """
    deadline = time.monotonic() + timeout
    last_error = "no answer"
    while True:
        try:
            r = requests.get(f"{http_url}/json/version", timeout=2)
            if r.status_code == 200:
                return
            last_error = f"HTTP {r.status_code}: {r.text[:120]}"
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Chrome DevTools not reachable at {http_url} after {timeout:.0f}s "
                f"({last_error}). If Chrome runs in Docker, check "
                f"'docker compose logs browser' and that port 9222 is forwarded "
                f"(socat in start-chrome.sh)."
            )
        time.sleep(interval)


def connect_local_chrome(pw, port: int = 9222, url: str = "about:blank"):
    """Connect to a Chrome instance already running on the host.

    Checks that the CDP port is open **before** calling Playwright, so the
    user gets a one-line "start Chrome with scripts/start_local_chrome.sh"
    message instead of a Playwright traceback.

    Returns:
        ``(browser, context, page)`` — the page is already navigated to
        ``url`` if it was not on the right site.
    """
    if not _port_open(port):
        raise RuntimeError(
            f"No local Chrome on port {port}. Start it with: "
            f"scripts/start_local_chrome.sh"
        )
    return _connect_and_prepare(pw, f"http://127.0.0.1:{port}", url)


def connect_remote_chrome(pw, cdp_url: str, url: str = "about:blank",
                          wait: float = 20.0):
    """Connect to a Chrome CDP endpoint over the network.

    Used when the agent runs in a container and Chrome runs in another
    container (same Docker network). ``cdp_url`` is for example
    ``http://browser:9222``.

    The hostname is resolved to an IP first (see ``resolve_cdp_url`` for
    why), then the CDP server is waited on, then the Playwright connection
    is opened. ``wait`` is the readiness timeout, not the connection timeout.
    """
    http_url = resolve_cdp_url(cdp_url)
    print(f"🔌 Connecting to remote CDP: {cdp_url} -> {http_url}")
    _wait_for_cdp(http_url, timeout=wait)
    return _connect_and_prepare(pw, http_url, url)


def connect_container_chrome(pw, url: str = "about:blank", auto_start: bool = True):
    """Start (or reuse) an isolated Chrome container, then connect.

    Two cases:
    - the container is already running and its CDP port is reachable →
      connect directly;
    - it is not → build the image (if missing), start the container, wait
      for CDP, then connect. ``auto_start=False`` turns this into a hard
      error instead, so a caller can decide to skip the run.

    The container manager is imported lazily: this module is used by
    ``agent.py`` even when ``--browser local`` is selected, and importing
    Docker-related code at module load would require the ``docker`` CLI to
    exist even for users who never use it.
    """
    from browser.container_manager import BrowserContainerManager  # lazy: needs docker

    manager = BrowserContainerManager()
    status = manager.status()
    if not (status.running and status.cdp_port):
        if not auto_start:
            raise RuntimeError("Chrome container not running (auto_start=False)")
        manager.build_image()
        status = manager.start()
        if not (status.running and status.cdp_port):
            raise RuntimeError(f"Container start failed: {status.error}")
    return _connect_and_prepare(pw, f"http://127.0.0.1:{status.cdp_port}", url)


def _connect_and_prepare(pw, cdp_http_url: str, url: str):
    """Open a Playwright browser, context and page over CDP.

    Reuses the first existing browser context instead of creating a new
    one. This is deliberate: creating a new context would give a blank
    profile, losing cookies, logins, and any state the user set up
    manually in the Chrome window. The agent is a supervised tool — the
    user can see the Chrome window (VNC or local) and interact with it.

    Navigation is best-effort:
    - the ``goto`` exception is caught and printed, because some sites
      throw on a redirect chain even though the page ends up loaded;
    - ``networkidle`` is waited on with a short timeout, because a page
      with long-polling may never reach it.

    Returns:
        ``(browser, context, page)``.
    """
    browser = pw.chromium.connect_over_cdp(cdp_http_url)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = _pick_active_page(context, url)

    if _needs_navigation(page.url, url):
        print(f"🌐 Navigating to: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"⚠️ Partial navigation ({type(e).__name__})")
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
    return browser, context, page


def _port_open(port: int, timeout: float = 0.5) -> bool:
    """Quick TCP probe on ``127.0.0.1:port``.

    Used to fail fast with a helpful message when no local Chrome is
    running, instead of letting Playwright time out after several seconds.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def _strip_www(host: str) -> str:
    """Lowercase ``host`` and remove a leading ``www.``."""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _pick_active_page(context, target_url: str):
    """Return the open page that best matches ``target_url``.

    Selection order:
    1. a page whose URL contains the target hostname (after stripping
       ``www.``), brought to the front;
    2. the first open page;
    3. a new page if none is open.

    This matters in CDP mode: the connected Chrome is a real browser the
    user may have used before, so ``context.pages[0]`` is not necessarily
    the tab the agent should act on. See "Failure Mode 5: The Ghost Tab"
    in docs/AI_ASSISTED.md.
    """
    target_host = _strip_www(urlparse(target_url).netloc) if target_url else ""
    pages = [p for p in context.pages if not p.is_closed()]
    if not pages:
        return context.new_page()
    if target_host:
        for p in pages:
            try:
                if target_host in p.url.lower():
                    p.bring_to_front()
                    return p
            except Exception:
                continue
    return pages[0]


def _needs_navigation(current_url: str, target_url: str) -> bool:
    """Decide whether the current page must be navigated to ``target_url``.

    Compares scheme-less hostnames (``www.`` stripped) and paths only.
    Query strings and fragments are ignored on purpose: navigating from
    ``/search?q=a`` to ``/search`` would reload the page for no benefit,
    and some sites treat the two as the same view.

    ``about:blank`` as a target means "do not navigate": this is the
    default when the caller only wants a page to act on, not a specific
    URL.
    """
    if target_url == "about:blank":
        return False
    try:
        curr, tgt = urlparse(current_url), urlparse(target_url)
    except Exception:
        return current_url != target_url
    if _strip_www(curr.netloc) != _strip_www(tgt.netloc):
        return True
    return (curr.path or "/") != (tgt.path or "/")
