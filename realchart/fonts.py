"""One bundled font for every caption: Hind covers Latin, digits and Devanagari.

The runner's fonts-noto-core NotoSansDevanagari-Bold has no Latin letters, and
DejaVu has no Devanagari, so either one alone turns half a caption into boxes
(close Reel, 15 Oct). The font ships in assets/fonts and is loaded by path.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_BOLD = ROOT / "assets" / "fonts" / "Hind-Bold.ttf"
FONT_SEMI = ROOT / "assets" / "fonts" / "Hind-SemiBold.ttf"
# Characters that are never drawn as glyphs (joiners, variation selectors).
_INVISIBLE = {"\u200c", "\u200d", "\ufe0f", "\ufe0e"}


class FontMissing(RuntimeError):
    pass


@lru_cache(maxsize=64)
def font(size, semi=False):
    from PIL import ImageFont
    path = FONT_SEMI if semi else FONT_BOLD
    if not path.is_file():
        raise FontMissing(f"bundled font missing: {path}")
    layout = ImageFont.Layout.RAQM if shaping_ok() else ImageFont.Layout.BASIC
    return ImageFont.truetype(str(path), size, layout_engine=layout)


def shaping_ok():
    """Devanagari matras need raqm (HarfBuzz) shaping."""
    try:
        from PIL import features
        return bool(features.check("raqm"))
    except Exception:  # noqa: BLE001
        return False


@lru_cache(maxsize=4)
def _cmap(path):
    from fontTools.ttLib import TTFont
    return set(TTFont(str(path)).getBestCmap().keys())


def missing_glyphs(text, path=FONT_BOLD):
    """Characters in text that the font would draw as tofu."""
    cmap = _cmap(str(path))
    return sorted({ch for ch in str(text or "") if not ch.isspace() and ch not in _INVISIBLE and ord(ch) not in cmap})


def tofu_faults(strings, path=FONT_BOLD):
    faults = []
    for text in strings:
        missing = missing_glyphs(text, path)
        if missing:
            shown = " ".join(f"U+{ord(ch):04X}" for ch in missing[:6])
            faults.append(f"tofu: {shown} in {str(text)[:40]!r}")
    if not shaping_ok():
        faults.append("tofu: Pillow has no raqm shaping, Devanagari would render broken")
    return faults
