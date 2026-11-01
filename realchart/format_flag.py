"""Which daily-reel layout to build.

`story` is the newer Reel (reversal, results, flows, news). `classic` is the
older support/resistance Reel. Env wins over config.json. Unset means classic,
so existing tests keep the previous layout until the flag is turned on.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ON = ("story", "1", "true", "yes", "on")
_OFF = ("classic", "0", "false", "no", "off")


def story_on(kind):
    """True when this kind should use the story layout."""
    kind = str(kind or "").strip().lower()
    if kind not in ("ta", "close"):
        return False
    env = os.environ.get(f"REALCHART_{kind.upper()}_FORMAT", "").strip().lower()
    if env in _ON:
        return True
    if env in _OFF:
        return False
    chosen = _from_config(kind)
    return chosen in _ON


def _from_config(kind):
    path = Path(os.environ.get("REALCHART_CONFIG") or (ROOT / "config.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    block = data.get("realchart") or {}
    return str(block.get(f"{kind}_format") or "").strip().lower()
