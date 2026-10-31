import json
import shutil
from datetime import datetime

import pytest

import stale_guard as g

FRI = datetime(2026, 10, 30, 17, 0, tzinfo=g.IST)
SAT = datetime(2026, 10, 31, 17, 35, tzinfo=g.IST)


def mk(tmp_path, kind="close", d="2026-10-30"):
    (tmp_path / "qa.json").write_text(json.dumps({"kind": kind}))
    if d:
        (tmp_path / "levels.json").write_text(json.dumps({"last_date": d}))
    return tmp_path


def meta(branch="main", created="2026-10-30T10:25:00Z"):
    return {"id": 1, "head_branch": branch, "created_at": created}


def test_fresh_ok(tmp_path):
    assert g.check(mk(tmp_path), meta(), now=FRI) == []


def test_old_data_date(tmp_path):
    p = g.check(mk(tmp_path, d="2026-10-09"), meta(), now=FRI)
    assert any("data date 2026-10-09" in x for x in p)


def test_saturday_close_refused(tmp_path):
    p = g.check(mk(tmp_path, kind="close", d="2026-10-30"), meta(created="2026-10-31T10:20:00Z"), now=SAT)
    assert any("not an NSE trading session" in x for x in p)


SUN_AM = datetime(2026, 11, 1, 8, 45, tzinfo=g.IST)
SUN_PM = datetime(2026, 11, 1, 18, 30, tzinfo=g.IST)


def test_sunday_news_ok(tmp_path):
    assert g.check(mk(tmp_path, kind="news", d="2026-10-30"), meta(created="2026-11-01T02:50:00Z"), now=SUN_AM) == []
    assert g.check(mk(tmp_path, kind="news", d="2026-11-01"), meta(created="2026-11-01T02:50:00Z"), now=SUN_AM) == []


def test_sunday_ta_ok(tmp_path):
    assert g.check(mk(tmp_path, kind="ta", d="2026-10-30"), meta(created="2026-11-01T12:35:00Z"), now=SUN_PM) == []


def test_sunday_ta_old_data_or_run_refused(tmp_path):
    assert g.check(mk(tmp_path, kind="ta", d="2026-10-29"), meta(created="2026-11-01T12:35:00Z"), now=SUN_PM)
    assert g.check(mk(tmp_path, kind="ta", d="2026-10-30"), meta(created="2026-10-31T12:35:00Z"), now=SUN_PM)
    assert g.check(mk(tmp_path, kind="ta", d="2026-11-01"), meta(created="2026-11-01T12:35:00Z"), now=SUN_PM)


def test_saturday_recap_ok(tmp_path):
    sat = datetime(2026, 10, 31, 11, 30, tzinfo=g.IST)
    assert g.check(mk(tmp_path, kind="recap", d="2026-10-30"), meta(created="2026-10-31T05:35:00Z"), now=sat) == []


def test_weekday_news_morning_previous_session_ok(tmp_path):
    fri_am = datetime(2026, 10, 30, 8, 45, tzinfo=g.IST)
    assert g.check(mk(tmp_path, kind="news", d="2026-10-29"), meta(created="2026-10-30T02:50:00Z"), now=fri_am) == []


def test_weekday_ta_evening_needs_today(tmp_path):
    fri_pm = datetime(2026, 10, 30, 18, 30, tzinfo=g.IST)
    assert g.check(mk(tmp_path, kind="ta", d="2026-10-30"), meta(created="2026-10-30T12:35:00Z"), now=fri_pm) == []
    assert g.check(mk(tmp_path, kind="ta", d="2026-10-29"), meta(created="2026-10-30T12:35:00Z"), now=fri_pm)


def test_other_branch(tmp_path):
    p = g.check(mk(tmp_path), meta(branch="cursor/reel-concat-path-00e1"), now=FRI)
    assert any("branch" in x for x in p)


def test_old_run(tmp_path):
    p = g.check(mk(tmp_path), meta(created="2026-10-14T14:13:00Z"), now=FRI)
    assert any("created 2026-10-14" in x for x in p)


def test_missing_date_for_dated_kind(tmp_path):
    assert g.check(mk(tmp_path, d=None), meta(), now=FRI)


def test_source_md_fallback(tmp_path):
    (tmp_path / "qa.json").write_text(json.dumps({"kind": "close"}))
    (tmp_path / "source.md").write_text("- symbol NSE:NIFTY date 2026-10-30 setup x\n")
    assert g.check(tmp_path, meta(), now=FRI) == []


def test_real_incident_artifact(tmp_path):
    src = "/workspace/wk/art"
    import os
    if not os.path.isdir(src):
        pytest.skip("evidence not present")
    for f in ("qa.json", "levels.json", "source.md"):
        shutil.copy(f"{src}/{f}", tmp_path)
    p = g.check(tmp_path, meta(branch="cursor/reel-concat-path-00e1", created="2026-10-14T14:13:00Z"), now=SAT)
    assert len(p) == 3  # recap data 09-Oct is not latest session 30-Oct


def test_main_prints_stop(tmp_path, monkeypatch, capsys):
    mk(tmp_path, d="2026-10-09")
    monkeypatch.setenv("RUN_DIR", str(tmp_path))
    monkeypatch.delenv("SOURCE_RUN", raising=False)
    assert g.main() == 1
    assert "[STOP] stale build" in capsys.readouterr().out
