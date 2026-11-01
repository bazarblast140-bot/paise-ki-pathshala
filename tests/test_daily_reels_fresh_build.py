import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from pick_fresh_build import pick  # noqa: E402

TODAY = date(2026, 10, 30)


def run(i, title, created, concl="success"):
    return {"databaseId": i, "displayTitle": title, "conclusion": concl, "createdAt": created}


def test_today_build_picked():
    runs = [run(1, "daily-reel news build", "2026-10-30T02:00:00Z")]
    assert pick(runs, "news", "50 2 * * 1-5", TODAY) == 1


def test_stale_build_rejected():
    runs = [run(2, "daily-reel news build", "2026-10-14T03:00:00Z")]
    assert pick(runs, "news", "50 2 * * 1-5", TODAY) is None


def test_ist_conversion():
    # 29-Oct 20:00 UTC = 30-Oct 01:30 IST -> today; 29-Oct 18:00 UTC = 29-Oct 23:30 IST -> stale
    assert pick([run(3, "daily-reel 20 10 * * 1-5 cron", "2026-10-29T20:00:00Z")], "close", "20 10 * * 1-5", TODAY) == 3
    assert pick([run(4, "daily-reel close build", "2026-10-29T18:00:00Z")], "close", "20 10 * * 1-5", TODAY) is None


def test_failed_and_other_kinds_skipped():
    runs = [run(5, "daily-reel ta build", "2026-10-30T07:00:00Z", "failure"),
            run(6, "daily-reel news build", "2026-10-30T07:00:00Z"),
            run(7, "daily-reel ta build", "2026-10-30T06:00:00Z")]
    assert pick(runs, "ta", "35 12 * * 1-5", TODAY) == 7


def test_workflow_uses_date_filter():
    wf = yaml.safe_load((ROOT / ".github/workflows/daily-reels.yml").read_text())
    steps = [s for j in wf["jobs"].values() for s in j.get("steps", [])]
    step = next(s for s in steps if s.get("name") == "Publish via publish-artifact")
    body = step["run"]
    assert "createdAt" in body
    assert "pick_fresh_build.py" in body
    assert "build from today to publish" in body
    for kind in ("news)", "close)", "ta)"):
        assert kind in body
