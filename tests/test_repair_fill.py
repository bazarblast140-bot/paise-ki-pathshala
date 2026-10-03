"""The 3 Oct restore lifts holds and never uses an instant-public dispatch."""
import json
import unittest
from datetime import datetime
from pathlib import Path

import repair_fill
import slot_guard

ROOT = Path(__file__).resolve().parent.parent
IST = slot_guard.IST


def _row(date, slot, **extra):
    row = {"date_ist": date, "slot": slot, "video_id": extra.pop("video_id", None), "format": "short"}
    row.update(extra)
    return row


class LiveLedgerTests(unittest.TestCase):
    def test_oct3_holds_are_gone_and_oct2_withdrawn_ids_stay(self):
        entries = json.loads((ROOT / "data" / "published.json").read_text(encoding="utf-8"))
        for slot in ("16:00", "21:00"):
            self.assertNotEqual(repair_fill.target_state(entries, slot), "held", slot)
        kept = {
            (entry.get("date_ist"), entry.get("slot"), entry.get("video_id")): entry
            for entry in entries
        }
        morning = kept[("2026-10-02", "08:30", "Z7PvCtS3a18")]
        afternoon = kept[("2026-10-02", "16:00", "Lx2vFMDO5ws")]
        self.assertEqual(morning.get("status"), "withdrawn")
        self.assertEqual(afternoon.get("status"), "withdrawn")
        self.assertIn("Do not republish", morning.get("note", ""))
        self.assertIn("Do not republish", afternoon.get("note", ""))


class PlanTests(unittest.TestCase):
    def test_open_afternoon_slots_are_the_public_picker_choice(self):
        now = datetime(2026, 10, 3, 10, 30, tzinfo=IST)
        entries = [
            _row("2026-10-02", "08:30", video_id="Z7PvCtS3a18", status="withdrawn"),
            _row("2026-10-02", "16:00", video_id="Lx2vFMDO5ws", status="withdrawn"),
        ]
        self.assertEqual(repair_fill.open_targets(entries), ["16:00", "21:00"])
        choice = repair_fill.next_public_slot(now, entries)
        self.assertEqual((choice["date_ist"], choice["slot"]), ("2026-10-03", "16:00"))
        self.assertEqual(choice["publish_at"], "2026-10-03T10:30:00Z")
        self.assertFalse(repair_fill.lead_is_short(now, choice))

    def test_holds_are_not_a_public_slot(self):
        now = datetime(2026, 10, 3, 10, 30, tzinfo=IST)
        entries = [
            _row("2026-10-03", "16:00", platform="hold"),
            _row("2026-10-03", "21:00", platform="hold"),
        ]
        self.assertEqual(repair_fill.open_targets(entries), [])
        self.assertIsNone(repair_fill.next_public_slot(now, entries))

    def test_filled_1600_moves_to_2100_still_public(self):
        now = datetime(2026, 10, 3, 11, 0, tzinfo=IST)
        entries = [_row("2026-10-03", "16:00", video_id="abcdefghijk")]
        choice = repair_fill.next_public_slot(now, entries)
        self.assertEqual(choice["slot"], "21:00")
        self.assertNotIn("now", choice["publish_at"])

    def test_inside_two_hours_is_not_dispatched_as_now(self):
        now = datetime(2026, 10, 3, 14, 30, tzinfo=IST)
        entries = []
        choice = repair_fill.next_public_slot(now, entries)
        self.assertEqual(choice["slot"], "21:00")
        self.assertNotEqual(choice["slot"], "16:00")

    def test_only_the_repair_day_acts(self):
        self.assertTrue(repair_fill.on_repair_day(datetime(2026, 10, 3, 23, 0, tzinfo=IST)))
        self.assertFalse(repair_fill.on_repair_day(datetime(2026, 10, 4, 0, 10, tzinfo=IST)))

    def test_ready_count_ignores_used_items(self):
        manifest = {"items": [
            {"state": "ready"},
            {"state": "used"},
            {"state": "claimed"},
            "nope",
        ]}
        self.assertEqual(repair_fill.count_ready_manifest(manifest), 1)
        self.assertEqual(repair_fill.count_ready_manifest({}), 0)


class NextActionTests(unittest.TestCase):
    def test_first_slot_try_is_not_skipped_after_the_edge_budget(self):
        self.assertEqual(repair_fill.next_action(0, 0, False, 0), "slot")
        self.assertEqual(repair_fill.next_action(0, repair_fill.MAX_BUFFER_BUILDS, False, 0), "slot")

    def test_ready_buffer_goes_to_the_slot(self):
        self.assertEqual(repair_fill.next_action(1, 1, False, 0), "slot")

    def test_failed_slot_is_retried_before_more_buffer(self):
        self.assertEqual(repair_fill.next_action(0, 0, False, 1), "slot")
        self.assertEqual(repair_fill.next_action(0, 3, False, 1), "slot")

    def test_exhausted_buffer_still_tries_the_slot_then_stops(self):
        self.assertEqual(repair_fill.next_action(0, repair_fill.MAX_BUFFER_BUILDS, False, 0), "slot")
        self.assertEqual(repair_fill.next_action(0, repair_fill.MAX_BUFFER_BUILDS, False, repair_fill.SLOT_TRIES), "stop")

    def test_short_lead_posts_the_slot_before_more_buffer(self):
        self.assertEqual(repair_fill.next_action(0, 0, True, 0), "slot")

    def test_no_slot_only_tops_up_buffer(self):
        self.assertEqual(repair_fill.next_action(1, 1, False, None), "buffer")
        self.assertEqual(repair_fill.next_action(2, 1, False, None), "stop")


class WorkflowGuardTests(unittest.TestCase):
    def test_dispatcher_is_public_and_does_not_touch_quiz(self):
        text = (ROOT / ".github" / "workflows" / "repair-fill-2026-10-03.yml").read_text(encoding="utf-8")
        script = (ROOT / "repair_fill.py").read_text(encoding="utf-8")
        self.assertNotIn("privacy=now", text)
        self.assertNotIn("privacy=now", script)
        self.assertNotIn("Trader Quiz", text)
        self.assertNotIn('workflow", "enable", "Trader Quiz', script)
        self.assertIn("privacy=public", script)
        self.assertIn('actions: write', text)
        self.assertIn("Paise Ki Pathshala", script)
        self.assertIn("Slot watch", script)
        self.assertIn("Buffer refill", script)
        self.assertIn("names_needing_enable", script)
        self.assertIn("repair-fill-2026-10-03.yml", text)
        self.assertNotIn("ELEVENLABS", text)

    def test_active_workflows_are_not_enabled_again(self):
        states = {
            "Paise Ki Pathshala": "active",
            "Slot watch": "active",
            "Buffer refill": "disabled_manually",
            "Trader Quiz Shorts": "disabled_manually",
        }
        self.assertEqual(repair_fill.names_needing_enable(states), ["Buffer refill"])
        self.assertEqual(repair_fill.names_needing_enable({name: "active" for name in repair_fill.ENABLE}), [])


if __name__ == "__main__":
    unittest.main()
