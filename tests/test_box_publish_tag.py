import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import box_publish  # noqa: E402
import stale_guard  # noqa: E402


def test_sunday_ta_tag_is_today_not_friday_session():
    qa = {"kind": "ta", "session": "2026-10-30"}
    assert box_publish.release_tag(qa, today="2026-11-01") == "box-reel-ta-2026-11-01"


def test_tag_passes_stale_guard_tag_check():
    tag = box_publish.release_tag({"kind": "ta"}, today="2026-11-01")
    url = f"https://github.com/bazarblast140-bot/paise-ki-pathshala/releases/download/{tag}/final.mp4"
    assert stale_guard.BOX_TAG_DATE.search(url).group(1) == "2026-11-01"
