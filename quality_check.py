"""Pre-publish deterministic quality gate. No LLM/API credits."""
import os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT / "run"


def fail(msg):
    print("[QUALITY-GATE] FAIL:", msg)
    raise SystemExit(1)


def semantic_visual_guard():
    """Zero-credit guard for known topic/visual mismatches before publish."""
    topic_file = RUN / "topic.json"
    if not topic_file.exists():
        return
    import json
    topic = json.loads(topic_file.read_text(encoding="utf-8"))
    key = str(topic.get("key", "")).strip()
    if key == "intraday_ema":
        short_src = (ROOT / "board_short.py").read_text(encoding="utf-8")
        scenes_src = (ROOT / "board_scenes.py").read_text(encoding="utf-8")
        if 'low == "intraday_ema":' not in short_src or "return S.render_beat(key, beat, img)" not in short_src:
            fail("intraday_ema is not routed to its dedicated topic scene")
        if "def intraday_ema_scene():" not in scenes_src or '"intraday_ema"' not in scenes_src:
            fail("intraday_ema dedicated scene is missing")
        print("[QUALITY-GATE] semantic guard PASS: intraday_ema -> dedicated EMA scene")



def audio_continuity_guard():
    """Reject long voice gaps before publish; no API/credit cost."""
    voice = RUN / "voice.wav"
    if not voice.exists():
        fail("voice.wav missing")
    try:
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(voice), "-af",
             "silencedetect=noise=-38dB:d=0.85", "-f", "null", "-"],
            capture_output=True, text=True
        )
        log = (p.stderr or "") + (p.stdout or "")
        import re
        gaps = []
        starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", log)]
        ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", log)]
        for a, b in zip(starts, ends):
            if b - a >= 0.85:
                gaps.append(round(b - a, 2))
        # One long gap is enough to make a spoken Short sound broken.
        if gaps:
            fail(f"voice continuity gap(s) detected: {gaps[:5]}s")
        print("[QUALITY-GATE] audio continuity PASS: no long voice gap")
    except SystemExit:
        raise
    except Exception as e:
        fail("audio continuity check error: " + str(e))


def cover_visual_guard():
    """Reject an empty first-frame/cover composition."""
    thumb = RUN / "thumbnail.jpg"
    hook = RUN / "b_hook_A.png"
    if not thumb.exists():
        fail("thumbnail.jpg missing")
    if not hook.exists():
        fail("b_hook_A.png missing")
    from PIL import Image
    import numpy as np
    im = Image.open(hook).convert("RGB")
    a = np.asarray(im, dtype=np.uint8)
    # Educational board region only; ignore outer captions/branding.
    h, w = a.shape[:2]
    crop = a[int(h*0.34):int(h*0.83), int(w*0.08):int(w*0.92)]
    bright = float((crop.mean(axis=2) > 42).mean())
    chroma = float((crop.max(axis=2).astype(int) - crop.min(axis=2).astype(int) > 25).mean())
    if bright < 0.010 and chroma < 0.003:
        fail(f"hook visual looks empty: bright={bright:.3%}, chroma={chroma:.3%}")
    print("[QUALITY-GATE] cover visual PASS")


def main():
    fmt = os.environ.get("VIDEO_FORMAT", "short")
    semantic_visual_guard()
    audio_continuity_guard()
    cover_visual_guard()
    final = RUN / "final.mp4"
    if not final.exists() or final.stat().st_size < 100_000:
        fail("final.mp4 missing/suspiciously small")

    try:
        p = subprocess.run(
            ["ffprobe","-v","error","-show_entries",
             "format=duration:stream=codec_type,width,height",
             "-of","json",str(final)],
            capture_output=True,text=True,check=True)
        import json
        m=json.loads(p.stdout)
        dur=float(m["format"]["duration"])
        streams=m.get("streams",[])
        v=next((s for s in streams if s.get("codec_type")=="video"),None)
        if not v or not any(s.get("codec_type")=="audio" for s in streams):
            fail("video/audio stream missing")
        if fmt == "short":
            if (v.get("width"),v.get("height")) != (1080,1920):
                fail(f"wrong short resolution {v.get('width')}x{v.get('height')}")
            sys.path.insert(0, str(ROOT))
            from common import short_qa_window
            lo, hi = short_qa_window()
            if not lo <= dur <= hi:
                fail(f"short duration {dur:.1f}s outside {lo:.0f}-{hi:.0f}s")
            frames=sorted(RUN.glob("b_*_*.png"))
            if len(frames)<4:
                fail("board frames missing")
            from PIL import Image
            import numpy as np
            for f in frames:
                a=np.asarray(Image.open(f).convert("RGB"),dtype=np.uint8)
                gray=a.mean(axis=2)
                if gray.std()<5.0 or (gray>42).mean()<0.010:
                    fail(f"blank/static board: {f.name}")
            print(f"[QUALITY-GATE] PASS short: {len(frames)} boards, {dur:.1f}s, 1080x1920")
        else:
            if (v.get("width"),v.get("height")) != (1920,1080):
                fail(f"wrong long resolution {v.get('width')}x{v.get('height')}")
            if dur < 60:
                fail(f"long video unexpectedly short: {dur:.1f}s")
            print(f"[QUALITY-GATE] PASS long: {dur:.1f}s, 1920x1080")
    except SystemExit:
        raise
    except Exception as e:
        fail(str(e))


if __name__ == "__main__":
    main()
