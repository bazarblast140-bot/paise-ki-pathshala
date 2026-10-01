"""Referral links in the YouTube description (no network, no secrets)."""
import json
import os
import unittest
from unittest import mock

import publish

SEC = {"TITLE": "Hammer candle", "HOOK": "Hammer candle ka asli matlab.",
       "DISCLAIMER LINE": "सिर्फ़ जानकारी, निवेश सलाह नहीं।"}
CFG = {"channel": {"name": "Paise Ki Pathshala", "tagline": "t"}}
TOPIC = {"cat": "CANDLESTICK", "seo": "Hammer Candlestick"}


def describe(fmt, topic=TOPIC):
    with mock.patch.dict(os.environ, {"VIDEO_FORMAT": fmt}), \
            mock.patch.object(publish, "load_topic", return_value=topic):
        return publish.build_description(SEC, CFG)


class ReferralDataTests(unittest.TestCase):
    def setUp(self):
        self.ref = json.loads(publish.REFERRALS_PATH.read_text(encoding="utf-8"))

    def test_every_mapped_platform_exists_with_https_url(self):
        plats = self.ref["platforms"]
        for cat, names in self.ref["category_map"].items():
            for n in names:
                self.assertIn(n, plats, f"{cat} maps to unknown platform {n}")
                self.assertTrue(plats[n]["url"].startswith("https://"))

    def test_crypto_is_bio_only_and_never_inserted(self):
        self.assertTrue(self.ref["platforms"]["delta_exchange"]["bio_only"])
        hacked = json.loads(json.dumps(self.ref))
        hacked["category_map"]["OPTIONS"].append("delta_exchange")
        urls = [u for _, u in publish.referral_links("OPTIONS", hacked)]
        self.assertFalse(any("delta.exchange" in u for u in urls))


class DescriptionTests(unittest.TestCase):
    def test_short_has_disclosed_cta_before_disclaimer_and_urls_below(self):
        d = describe("short")
        cta = d.index("🔗 Zerodha / Upstox demat account")
        self.assertLess(cta, d.index("सिर्फ़ जानकारी"))
        self.assertIn("commission", d[cta:cta + 200])
        self.assertIn("\nhttps://zerodha.com/open-account?c=KU3466", d)
        self.assertNotIn("Delta", d)   # old text promised a Delta link that was never there
        self.assertIn("@rajesh_technical_trader", d)

    def test_long_puts_clickable_urls_right_under_the_hook(self):
        d = describe("long")
        first = d.split("\n\n")[1]
        self.assertIn("Zerodha: https://zerodha.com/open-account?c=KU3466", first)
        self.assertIn("commission", first)
        self.assertLess(d.index("zerodha.com"), 300)

    def test_unmapped_topic_gets_no_referral(self):
        d = describe("short", {"cat": "PENNY STOCK", "seo": "Penny stock"})
        self.assertNotIn("referral", d.lower())
        self.assertNotIn("zerodha.com", d)

    def test_broken_referral_file_does_not_block_upload(self):
        with mock.patch.object(publish, "load_referrals", return_value={"category_map": []}):
            d = describe("short")
        self.assertIn("Hammer candle", d)
        self.assertNotIn("zerodha.com", d)

    def test_fits_youtube_limit(self):
        self.assertLessEqual(len(describe("short")), publish.MAX_DESC)


if __name__ == "__main__":
    unittest.main()
