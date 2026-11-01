import subprocess
from pathlib import Path
import yaml

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "daily-reels.yml"


def _gate_script(event, schedule="", kind="", sample="false"):
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    run = wf["jobs"]["gate"]["steps"][0]["run"]
    return (run.replace("${{ github.event_name }}", event)
               .replace("${{ github.event.schedule }}", schedule)
               .replace("${{ inputs.kind }}", kind)
               .replace("${{ inputs.sample }}", sample))


def _skip(pause, **kw):
    out = Path("/tmp/_gate_out")
    out.write_text("")
    subprocess.run(["bash", "-c", _gate_script(**kw)], check=True,
                   env={"PAUSE_KINDS": pause, "GITHUB_OUTPUT": str(out), "PATH": "/usr/bin:/bin"})
    return "skip=1" in out.read_text()


def test_reel_job_needs_gate():
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    assert wf["jobs"]["reel"]["needs"] == "gate"
    assert "skip != '1'" in wf["jobs"]["reel"]["if"]


def test_pause_close_ta_keeps_news():
    p = "close,ta"
    assert not _skip(p, event="schedule", schedule="50 2 * * 1-5")
    assert not _skip(p, event="schedule", schedule="15 3 * * 1-5")
    for s in ("20 10 * * 1-5", "45 10 * * 1-5", "35 12 * * 1-5", "0 13 * * 1-5"):
        assert _skip(p, event="schedule", schedule=s)
    assert _skip(p, event="workflow_dispatch", kind="ta")
    assert not _skip(p, event="workflow_dispatch", kind="news")


def test_sample_dispatch_does_not_lift_a_live_pause():
    # A fixture sample is allowed through. A normal ta dispatch stays paused.
    assert _skip("close,ta", event="workflow_dispatch", kind="ta")
    assert not _skip("close,ta", event="workflow_dispatch", kind="ta", sample="true")
    assert _skip("close,ta", event="schedule", schedule="0 13 * * 1-5")


def test_empty_pauses_nothing():
    for s in ("50 2 * * 1-5", "20 10 * * 1-5", "0 13 * * 1-5"):
        assert not _skip("", event="schedule", schedule=s)
