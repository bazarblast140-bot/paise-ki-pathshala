"""Chart Board Short: topic -> narration (LLM, gated) -> voice per beat -> video + hook thumbnail.

Same template for every topic:
  HOOK -> 1 PEHCHAN -> 2 MATLAB -> 3 KYA KAREIN
Pictures come from board_scenes (code, not the LLM). On-screen Hindi comes from
topic_bank.json. The LLM writes only the spoken lines, and may use only numbers
that already appear in the topic; if it keeps failing, the narration is built
from the on-screen text so the day's video still ships.

Writes run/: final.mp4, thumbnail.jpg, script.txt, spoken.txt, shots.json, subs.ass
(the files run_verify.py and publish.py already read).

  python board_short.py          # normal (needs DEEPSEEK/KIMI/ANTHROPIC key)
  python board_short.py --qa     # no LLM, no voice: renders frames + thumbnail only
"""
import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

import board_kit as K
import board_scenes as S
from common import RUN, ROOT, cfg, claude_json, die, ok, save

BEATS = S.BEATS
STEP_CHIP = {"hook": "HOOK", "pehchan": "1  PEHCHAN", "matlab": "2  MATLAB", "karein": "3  KYA KAREIN"}
CTA = "अगला कौन सा market topic समझना है? Comment में लिखो—ऐसी आसान सीख के लिए जुड़े रहो।"
DISCLAIMER = "सिर्फ़ जानकारी, निवेश सलाह नहीं।"
FORBIDDEN = ["दोस्तों", "like", "subscribe", "सब्सक्राइब", "लाइक", "टारगेट", "target price", "टिप",
             "ज़रूर ख़रीद", "जरूर खरीद", "ख़रीद लो", "खरीद लो", "बेच दो", "गारंटी से", "पक्का मुनाफ़ा"]

_DEV_FONTS = ["/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansDevanagari-Bold.ttf",
              r"C:\\Windows\\Fonts\\Nirmala.ttc"]


def _dev_font(size):
    from PIL import ImageFont
    import pathlib
    for p in _DEV_FONTS:
        if pathlib.Path(p).exists():
            return ImageFont.truetype(p, size)
    return None


def fit_size(text, max_w, max_size, min_size=40):
    f = _dev_font(100)
    for size in range(max_size, min_size - 1, -2):
        w = (f.getlength(text) * size / 100 * 1.12) if f else len(text) * size * 0.62
        if w <= max_w:
            return size
    return min_size


CAT_CHIPS = {
    "REAL DATA": {"hook": "HOOK", "pehchan": "1  DATA", "matlab": "2  HISAAB", "karein": "3  NATEEJA"},
    "MARKET FACT": {"hook": "HOOK", "pehchan": "1  FACT", "matlab": "2  MATLAB", "karein": "3  YAAD RAKHO"},
}


def beat_images(topic, beat):
    img = K.background()
    K.chips(img, topic["cat"], CAT_CHIPS.get(topic["cat"], STEP_CHIP)[beat])
    key = topic["key"]
    low = key.lower()
    # Permanent fix: intraday topics must show real charts, never empty finance cards
    if low.startswith("intraday_"):
        try:
            import board_orb
            if any(x in low for x in ("opening", "orb", "range", "breakout")):
                return board_orb.opening_range_breakout()(beat, img)
            return board_orb.intraday_chart_fallback(key)(beat, img)
        except Exception as e:  # noqa: BLE001
            print(f"[board_orb] fallback to generic: {e}")
    return S.render_beat(key, beat, img)
