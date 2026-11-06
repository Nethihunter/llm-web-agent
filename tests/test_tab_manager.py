"""Tests for the popup/ad heuristics of tab_manager.py."""
import unittest

from tab_manager import _is_likely_ad, _same_site


class SameSite(unittest.TestCase):
    def test_www_and_bare_domain_are_the_same_site(self):
        self.assertTrue(_same_site("https://saucedemo.com/x", "www.saucedemo.com"))
        self.assertTrue(_same_site("https://www.saucedemo.com/x", "saucedemo.com"))

    def test_subdomains(self):
        self.assertTrue(_same_site("https://pay.site.com/", "www.site.com"))
        self.assertTrue(_same_site("https://site.com/", "checkout.site.com"))

    def test_lookalike_domain_is_not_the_same_site(self):
        self.assertFalse(_same_site("https://evilsite.com/", "site.com"))
        self.assertFalse(_same_site("https://site.com.evil.net/", "site.com"))


class LikelyAd(unittest.TestCase):
    def test_other_domain_is_an_ad(self):
        self.assertTrue(_is_likely_ad("https://promo.example.net/", "www.site.com"))

    def test_known_ad_network_is_an_ad_even_with_main_domain_in_url(self):
        self.assertTrue(_is_likely_ad("https://doubleclick.net/?u=site.com", "site.com"))

    def test_same_site_popup_is_kept(self):
        self.assertFalse(_is_likely_ad("https://site.com/oauth", "www.site.com"))

    def test_blank_popup_is_kept_until_it_navigates(self):
        self.assertFalse(_is_likely_ad("about:blank", "site.com"))
        self.assertFalse(_is_likely_ad("", "site.com"))


if __name__ == "__main__":
    unittest.main()
