"""Topic bank shape, placeholder rewrite, visuals, and dated-topic expiry."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import board_kit
from board_scenes import BEATS, _topic_kind, render_beat
from board_short import FORBIDDEN, beat_images
from competitor_patterns import (
    blocked_topic_keys, is_near_competitor_duplicate, market_entry_expired,
    market_priority, score_topic,
)
from pick_topic import _pick_fresh, is_placeholder_topic, remember_gate_skip

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))
REQUIRED = ("id", "used", "used_short", "key", "cat", "topic", "thumb", "sticker", "beats", "facts", "seo")
OPTIONAL = {"used_short_at", "used_short_fails", "levels", "cover_lines", "badge", "data_short", "hook_hint"}
BEAT_NAMES = ("hook", "pehchan", "matlab", "karein")


def _load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _blob(topic):
    parts = [topic.get("topic", ""), topic.get("seo", ""), topic.get("sticker", "")]
    parts.extend(topic.get("facts") or [])
    parts.extend(topic.get("thumb") or [])
    beats = topic.get("beats") or {}
    if isinstance(beats, dict):
        for group in beats.values():
            if isinstance(group, list):
                parts.extend(group)
    return "\n".join(str(p) for p in parts)


class TopicBankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = _load("topic_bank.json")
        cls.sources = _load("data/topic_bank_sources.json")
        cls.market = _load("data/current_market_topics.json")
        cls.topics = cls.bank["topics"]
        cls.by_id = {t["id"]: t for t in cls.topics}
        cls.by_key = {t["key"]: t for t in cls.topics}
        cls.phrases = _load("data/seen_competitor_topics.json")[
            "phrases_to_avoid_as_exact_or_near_duplicate_topics"
        ]

    def test_files_parse_and_schema_matches(self):
        self.assertIsInstance(self.bank.get("_comment"), str)
        self.assertIsInstance(self.topics, list)
        self.assertGreater(len(self.topics), 0)
        self.assertGreater(len(self.sources["rewritten_ids"]), 0)
        self.assertIn("topics", self.market)
        for topic in self.topics:
            keys = set(topic)
            self.assertTrue(set(REQUIRED) <= keys, topic.get("key"))
            self.assertFalse(keys - set(REQUIRED) - OPTIONAL, topic.get("key"))
            self.assertIsInstance(topic["id"], int)
            self.assertIsInstance(topic["used"], bool)
            self.assertIsInstance(topic["used_short"], bool)
            self.assertIsInstance(topic["key"], str)
            self.assertTrue(topic["key"].strip(), topic["id"])
            self.assertIsInstance(topic["cat"], str)
            self.assertTrue(topic["cat"].strip(), topic["key"])
            self.assertIsInstance(topic["topic"], str)
            self.assertTrue(topic["topic"].strip(), topic["key"])
            self.assertIsInstance(topic["seo"], str)
            self.assertTrue(topic["seo"].strip(), topic["key"])
            self.assertIsInstance(topic["sticker"], str)
            self.assertTrue(topic["sticker"].strip(), topic["key"])
            self.assertEqual(len(topic["thumb"]), 2)
            self.assertTrue(all(isinstance(line, str) and line.strip() for line in topic["thumb"]))
            self.assertIsInstance(topic["facts"], list)
            self.assertGreaterEqual(len(topic["facts"]), 1)
            self.assertTrue(all(isinstance(line, str) and line.strip() for line in topic["facts"]))
            self.assertEqual(set(topic["beats"]), set(BEAT_NAMES))
            for name in BEAT_NAMES:
                pair = topic["beats"][name]
                self.assertEqual(len(pair), 2, (topic["key"], name))
                self.assertTrue(all(isinstance(line, str) and line.strip() for line in pair))
            if "used_short_at" in topic:
                self.assertIsInstance(topic["used_short_at"], str)
                datetime.strptime(topic["used_short_at"], "%Y-%m-%dT%H:%M:%SZ")
            if "used_short_fails" in topic:
                self.assertIsInstance(topic["used_short_fails"], int)
                self.assertGreaterEqual(topic["used_short_fails"], 0)

    def test_no_duplicate_ids_keys_or_titles(self):
        for field in ("id", "key", "topic"):
            values = [t[field] for t in self.topics]
            self.assertEqual(len(values), len(set(values)), field)

    def test_rewritten_topics_pass_the_placeholder_detector(self):
        # Content decides, not the old 63–133 id block.
        templated = {
            "id": 1,
            "topic": "hand authored title",
            "thumb": ["PEG", "आसान भाषा में समझो"],
            "beats": {
                "hook": ["PEG", "इसे आसान तरीके से समझो"],
                "pehchan": ["पहचान", "same sentence"],
                "matlab": ["मतलब", "same sentence"],
                "karein": ["करें", "check"],
            },
            "facts": ["same sentence"],
        }
        self.assertTrue(is_placeholder_topic(templated))
        real = {
            "id": 100,
            "topic": "Hammer candle wick",
            "beats": {"hook": ["नीचे लंबी wick", "खरीदार लौटे?"]},
            "facts": ["निचली wick बॉडी से 2 गुना"],
        }
        self.assertFalse(is_placeholder_topic(real))

        for topic_id in self.sources["rewritten_ids"]:
            topic = self.by_id[topic_id]
            self.assertFalse(is_placeholder_topic(topic), topic["key"])
        for topic in self.topics:
            if not topic.get("used_short"):
                self.assertFalse(
                    is_placeholder_topic(topic),
                    f"unused placeholder would never be picked: {topic['key']}",
                )

    def test_every_topic_maps_to_a_renderable_visual(self):
        self.assertEqual(_topic_kind("pf_sip_power"), "pf")
        self.assertEqual(_topic_kind("tax_ltcg_equity"), "tax")
        self.assertEqual(_topic_kind("ipo_timeline"), "ipo")
        self.assertEqual(_topic_kind("fund_pe"), "fund")
        self.assertEqual(_topic_kind("zz_unknown_topic"), "market")
        for key in ("fund_pe", "pf_sip_power", "tax_crypto_30", "ipo_drhp", "zz_unknown_topic"):
            for beat in BEATS:
                base, annotated = render_beat(key, beat, board_kit.background())
                self.assertEqual(base.size, (1080, 1920), (key, beat))
                self.assertEqual(annotated.size, (1080, 1920), (key, beat))
        for topic in self.topics:
            for beat in BEATS:
                pics = beat_images(topic, beat)
                self.assertIsInstance(pics, tuple, (topic["key"], beat))
                self.assertGreaterEqual(len(pics), 2, (topic["key"], beat))
                for pic in pics:
                    self.assertEqual(pic.size, (1080, 1920), (topic["key"], beat))

    def test_dated_topics_expire(self):
        by_key = {item["topic_key"]: item for item in self.market["topics"]}
        for key in ("market_rbi_policy_oct2026", "ipo_timeline", "market_muhurat_2026"):
            self.assertIn(key, by_key)
            self.assertIn(key, self.by_key)
        rbi = by_key["market_rbi_policy_oct2026"]
        ipo = by_key["ipo_timeline"]
        muhurat = by_key["market_muhurat_2026"]
        self.assertFalse(market_entry_expired(rbi, datetime(2026, 10, 7, 9, 59, tzinfo=IST)))
        self.assertTrue(market_entry_expired(rbi, datetime(2026, 10, 7, 10, 0, tzinfo=IST)))
        self.assertFalse(market_entry_expired(ipo, datetime(2026, 10, 8, 23, 30, tzinfo=IST)))
        self.assertTrue(market_entry_expired(ipo, datetime(2026, 10, 9, 0, 0, tzinfo=IST)))
        self.assertFalse(market_entry_expired(muhurat, datetime(2026, 11, 8, 18, 0, tzinfo=IST)))
        self.assertTrue(market_entry_expired(muhurat, datetime(2026, 11, 9, 0, 1, tzinfo=IST)))
        self.assertFalse(market_entry_expired({"topic_key": "opt_stt_hike_2026", "priority": 1}))
        self.assertFalse(market_entry_expired({"valid_until": "not-a-date"}))

        topic = {"key": "market_rbi_policy_oct2026", "cat": "MARKET FACT", "topic": "RBI repo"}
        before = score_topic(topic, {}, self.market, {}, now=datetime(2026, 10, 1, tzinfo=IST))
        after = score_topic(topic, {}, self.market, {}, now=datetime(2026, 10, 7, 10, 0, tzinfo=IST))
        self.assertAlmostEqual(before, 1.0)
        self.assertAlmostEqual(after, 0.0)
        self.assertEqual(market_priority(rbi, datetime(2026, 10, 1, tzinfo=IST)), 2.0)

        fresh = []
        for key in ("market_rbi_policy_oct2026", "ipo_timeline", "pf_sip_power"):
            row = dict(self.by_key[key])
            row["used_short"] = False
            fresh.append(row)
        bank = {"topics": fresh}
        research = ({}, self.market, {})
        early = _pick_fresh(bank, "used_short", [], research, "short", now=datetime(2026, 10, 1, 8, 0, tzinfo=IST))
        self.assertEqual(early["key"], "market_rbi_policy_oct2026")
        after_rbi = _pick_fresh(bank, "used_short", [], research, "short", now=datetime(2026, 10, 7, 10, 0, tzinfo=IST))
        self.assertEqual(after_rbi["key"], "ipo_timeline")
        after_ipo = _pick_fresh(bank, "used_short", [], research, "short", now=datetime(2026, 10, 9, 0, 0, tzinfo=IST))
        skipped = _pick_fresh(
            bank, "used_short", [], research, "short",
            now=datetime(2026, 10, 1, 8, 0, tzinfo=IST),
            skips=["market_rbi_policy_oct2026"],
        )
        self.assertEqual(skipped["key"], "ipo_timeline")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gate_skips.json"
            remember_gate_skip("risk_journal", path)
            remember_gate_skip("risk_journal", path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["keys"], ["risk_journal"])
        self.assertEqual(after_ipo["key"], "pf_sip_power")
        self.assertIn("ipo_timeline", blocked_topic_keys(self.market, datetime(2026, 10, 9, tzinfo=IST)))
        self.assertNotIn("pf_sip_power", blocked_topic_keys(self.market, datetime(2026, 10, 9, tzinfo=IST)))

    def test_competitor_phrase_and_banned_word_filters_pass(self):
        for topic in self.topics:
            self.assertFalse(
                is_near_competitor_duplicate(topic["topic"], self.phrases),
                topic["key"],
            )
            low = _blob(topic).lower()
            bad = [word for word in FORBIDDEN if word.lower() in low]
            self.assertEqual(bad, [], topic["key"])

    def test_recent_named_share_prices_were_dropped_and_sourced(self):
        self.assertNotIn("ipo_lot_size", self.by_key)
        self.assertNotIn("ipo_gmp", self.by_key)
        self.assertNotIn(154, self.by_id)
        self.assertNotIn(155, self.by_id)
        blob = json.dumps(self.bank, ensure_ascii=False)
        self.assertNotIn("Nityas", blob)
        self.assertNotIn("SRIT", blob)
        dropped = {row["key"]: row for row in self.sources["dropped"]}
        self.assertEqual(set(dropped), {"ipo_lot_size", "ipo_gmp"})
        for row in dropped.values():
            self.assertTrue(row["reason"])
            self.assertTrue(row["facts"])
            self.assertTrue(row["sources_note"]["sources"])
        for key in self.sources["price_review"]["dropped_keys"]:
            self.assertNotIn(key, self.by_key)
        market_keys = {item["topic_key"] for item in self.market["topics"]}
        self.assertNotIn("ipo_gmp", market_keys)
        for key in market_keys:
            self.assertIn(key, self.by_key, key)
        for topic_id in self.sources["new_ids"] + self.sources["rewritten_ids"]:
            self.assertIn(topic_id, self.by_id)


if __name__ == "__main__":
    unittest.main()
