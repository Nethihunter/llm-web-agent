"""Tests for browser/browser_utils.py. No Docker, no browser."""
import unittest
from types import SimpleNamespace
from unittest import mock

import requests

from browser.browser_utils import (
    _needs_navigation,
    _pick_active_page,
    _wait_for_cdp,
    resolve_cdp_url,
)


class ResolveCdpUrl(unittest.TestCase):
    @mock.patch("browser.browser_utils.socket.gethostbyname", return_value="172.19.0.3")
    def test_service_name_becomes_ip(self, _):
        self.assertEqual(resolve_cdp_url("http://browser:9222"), "http://172.19.0.3:9222")

    @mock.patch("browser.browser_utils.socket.gethostbyname")
    def test_ip_and_localhost_untouched(self, gethost):
        for url in ("http://127.0.0.1:9222", "http://172.19.0.3:9222", "http://localhost:9222"):
            self.assertEqual(resolve_cdp_url(url), url)
        gethost.assert_not_called()

    @mock.patch("browser.browser_utils.socket.gethostbyname", side_effect=OSError("nope"))
    def test_unresolvable_name_is_left_alone(self, _):
        self.assertEqual(resolve_cdp_url("http://nohost:9222"), "http://nohost:9222")


class WaitForCdp(unittest.TestCase):
    @mock.patch("browser.browser_utils.requests.get")
    def test_retries_until_ready(self, get):
        ok = mock.Mock(status_code=200)
        get.side_effect = [requests.ConnectionError("refused"), ok]
        _wait_for_cdp("http://1.2.3.4:9222", timeout=5, interval=0.01)
        self.assertEqual(get.call_count, 2)

    @mock.patch("browser.browser_utils.requests.get",
                side_effect=requests.ConnectionError("refused"))
    def test_timeout_gives_an_actionable_message(self, _):
        with self.assertRaises(RuntimeError) as ctx:
            _wait_for_cdp("http://1.2.3.4:9222", timeout=0.1, interval=0.02)
        msg = str(ctx.exception)
        self.assertIn("docker compose logs browser", msg)
        self.assertIn("refused", msg)


class PageSelection(unittest.TestCase):
    def test_www_is_ignored_when_matching_tabs(self):
        old = SimpleNamespace(url="https://en.wikipedia.org/x", is_closed=lambda: False,
                              bring_to_front=lambda: None)
        good = SimpleNamespace(url="https://saucedemo.com/", is_closed=lambda: False,
                               bring_to_front=lambda: None)
        ctx = SimpleNamespace(pages=[old, good])
        self.assertIs(_pick_active_page(ctx, "https://www.saucedemo.com"), good)

    def test_navigation_needed_only_when_site_or_path_differs(self):
        self.assertFalse(_needs_navigation("https://saucedemo.com/", "https://www.saucedemo.com"))
        self.assertTrue(_needs_navigation("https://saucedemo.com/inventory.html",
                                          "https://www.saucedemo.com"))
        self.assertTrue(_needs_navigation("https://other.com/", "https://www.saucedemo.com"))
        self.assertFalse(_needs_navigation("about:blank", "about:blank"))


if __name__ == "__main__":
    unittest.main()
