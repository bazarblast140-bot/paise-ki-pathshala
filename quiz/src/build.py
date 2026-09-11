"""Poora short banata hai: sawaal chuno -> voice -> frames -> audio -> MP4 + SEO JSON.

Sync ka tareeka (ye critical hai):
Har segment ki duration pehle FRAMES me convert hoti hai, phir audio us frame
offset pe rakha jaata hai. Isse video aur audio kabhi drift nahi karte —
dono ka source of truth ek hi number hai (frame count).

Usage:
    python src/build.py --slot morning
    python src/build.py --slot evening --seed 42
"""
import argparse
import json
import shutil
import subprocess
import sys
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import config as C
import render
import sfx
import tts
from pick import pick
from seo import build as build_seo


# ---------------------------------------------------------------- audio io
def read_wav(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2, f"{path}: 16-bit WAV chahiye"
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        sr = w.getframerate()
    a = data.astype(np.float32) / 32768.0
    if sr != C.SAMPLE_RATE:                       # safety resample
        n = int(len(a) * C.SAMPLE_RATE / sr)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
    return a


def mix(track, clip, at_sample, gain=1.0):
    end = min(at_sample + len(clip), len(track))
    if end > at_sample:
        track[at_sample:end] += clip[: end - at_sample] * gain
    return track


# ---------------------------------------------------------------- plan
ENGINES = []


def plan_segments(questions):
    """Segments ki list banata hai: (kind, frames, payload, audio_events).
    audio_events = [(offset_seconds, wav_path_or_array, gain)]"""
    segs = []
    n = len(questions)

    # Koi intro segment nahi. Video ka frame 0 hi pehla sawaal hai — Shorts me
    # branding/intro card sirf scroll-away banata hai.
    for i, q in enumerate(questions):
        v, d, eng = tts.say(tts.question_script(q, i, n), f"q{i}_{q['id']}")
        ENGINES.append(eng)
        print(f"[voice] Q{i+1} {d:.2f}s via {eng}")
        segs.append(dict(kind="read", dur=d + C.READ_PAD_SECONDS,
                         audio=[(0.0, v, C.VOICE_VOLUME)],
                         payload=(q, i + 1, n)))

        # Options timer ke DAURAN bolte hain. Agar bolne me 8s se zyada lage to
        # segment utna lamba ho jaata hai — par countdown hamesha poore 8 second
        # ka dikhta hai (ring segment ke aakhri 8 second me chalti hai).
        ov, od, oeng = tts.say(tts.options_script(q), f"o{i}_{q['id']}")
        ENGINES.append(oeng)
        tdur = max(float(C.TIMER_SECONDS), od + 0.5)
        print(f"[voice] Q{i+1} options {od:.2f}s via {oeng} -> timer segment {tdur:.2f}s")
        segs.append(dict(kind="timer", dur=tdur,
                         audio=[(0.0, "TICKBED", C.TICK_VOLUME),
                                (0.0, ov, C.VOICE_VOLUME)],
                         payload=(q, i + 1, n)))

        # CTA jawab se PEHLE. Viewer yahan ruka hua hai — 8 second lagakar
        # jawab ka intezaar kar raha hai. Video ke ant me CTA daalne ka koi
        # matlab nahi, jawab sunte hi log swipe kar dete hain.
        cv, cd, ceng = tts.say(tts.cta_script(), "cta")
        ENGINES.append(ceng)
        segs.append(dict(kind="cta", dur=cd + 0.35,
                         audio=[(0.0, cv, C.VOICE_VOLUME)],
                         payload=(q, i + 1, n)))

        hv, hd, heng = tts.say(tts.hint_script(q), f"h{i}_{q['id']}")
        ENGINES.append(heng)
        segs.append(dict(kind="hint", dur=hd + 0.4,
                         audio=[(0.0, hv, C.VOICE_VOLUME)],
                         payload=(q, i + 1, n)))

        # Aakhri segment. Video yahin khatam — iske baad kuch nahi.
        v, d, eng = tts.say(tts.answer_script(q), f"a{i}_{q['id']}")
        ENGINES.append(eng)
        segs.append(dict(kind="reveal",
                         dur=max(C.REVEAL_SECONDS, d + 0.8),
                         audio=[(0.0, "DING", 0.8), (0.45, v, C.VOICE_VOLUME)],
                         payload=(q, i + 1, n)))

    # Koi outro nahi. Jawab hi aakhri cheez hai — uske baad viewer waise bhi
    # swipe kar deta hai, to wahan kuch rakhna sirf duration badhata hai.
    for s in segs:
        s["frames"] = max(1, round(s["dur"] * C.FPS))
    return segs


# ---------------------------------------------------------------- render
def render_video(segs, frames_dir: Path):
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)
    idx = 0
    for s in segs:
        kind, payload, nf = s["kind"], s["payload"], s["frames"]

        q, qn, tot = payload
        if True:
            if kind == "reveal":
                base = render.reveal_panel(q, qn, tot)
            elif kind == "cta":
                base = render.cta_panel(q, qn, tot)
            elif kind == "hint":
                base = render.hint_panel(q, qn, tot)
            else:
                # Chaaron option shuru se dikhte hain — Rajesh ka faisla.
                # (Progressive reveal try kiya tha retention ke liye, par wo
                # hata diya gaya.)
                base, opt_end = render.question_panel(q, qn, tot)
                render.footer(base, "ध्यान से सुनिए…" if kind == "read" else C.CTA_TEXT,
                              y=max(render.FOOTER_Y, opt_end + 34))

        if kind == "timer":
            seg_secs = nf / C.FPS
            for f in range(nf):                     # sirf yahan per-frame kaam
                elapsed = f / C.FPS
                remaining = min(float(C.TIMER_SECONDS), seg_secs - elapsed)
                img = base.copy()
                render.timer_ring(img, remaining, C.TIMER_SECONDS)
                img.save(frames_dir / f"f{idx:06d}.jpg", quality=92)
                idx += 1
        else:
            first = frames_dir / f"f{idx:06d}.jpg"
            base.save(first, quality=92)
            idx += 1
            for _ in range(nf - 1):                 # static -> hardlink, fast
                dst = frames_dir / f"f{idx:06d}.jpg"
                try:
                    dst.hardlink_to(first)
                except OSError:
                    shutil.copyfile(first, dst)
                idx += 1
        print(f"[render] {kind:<7} {nf:>4} frames  (total {idx})")
    return idx


def render_audio(segs, total_frames) -> np.ndarray:
    total_samples = int(round(total_frames / C.FPS * C.SAMPLE_RATE))
    track = np.zeros(total_samples, dtype=np.float32)
    ding = sfx.ding()
    frame_cursor = 0
    for s in segs:
        seg_start = int(round(frame_cursor / C.FPS * C.SAMPLE_RATE))
        for off, src, gain in s["audio"]:
            if src == "TICKBED":
                # tick sirf aakhri 8 second me, taaki wo countdown se sync rahe
                seg_secs = s["frames"] / C.FPS
                lead = max(0.0, seg_secs - C.TIMER_SECONDS)
                clip = sfx.build_timer_bed(C.TIMER_SECONDS, 1.0)
                at_extra = int(lead * C.SAMPLE_RATE)
                mix(track, clip, seg_start + at_extra, gain)
                continue
            elif src == "DING":
                clip = ding
            else:
                clip = read_wav(src)
            mix(track, clip, seg_start + int(off * C.SAMPLE_RATE), gain)
        frame_cursor += s["frames"]

    peak = float(np.max(np.abs(track)))
    if peak > 0:
        track = track / peak * 0.92          # normalize, clipping se bacho
    return track


def mux(frames_dir: Path, wav: Path, out_mp4: Path):
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-framerate", str(C.FPS), "-i", str(frames_dir / "f%06d.jpg"),
        "-i", str(wav),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart", "-shortest",
        str(out_mp4),
    ], check=True)
    return out_mp4


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=C.SLOT_ORDER, default=C.SLOT_ORDER[0])
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="sawaal use-history me mat likho (testing ke liye)")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d")
    slug = f"{stamp}_{args.slot}"
    outdir = C.OUT / slug
    outdir.mkdir(parents=True, exist_ok=True)

    questions = pick(C.QUESTIONS_PER_VIDEO, dry_run=args.dry_run,
                     seed=args.seed, slot=args.slot)
    print("[pick] " + " || ".join(f"{q['sub']}/{q['id']}: {q['q']}" for q in questions))

    segs = plan_segments(questions)
    total_dur = sum(s["frames"] for s in segs) / C.FPS
    print(f"[plan] {len(segs)} segments, {total_dur:.2f}s total")
    # 60s koi platform limit NAHI hai (asli limit 3 min hai) — ye retention
    # target hai. Isliye yahan sirf intro/outro trim karte hain; agar phir bhi
    # thoda upar reh jaaye to koi dikkat nahi, video Short hi rahega.
    if total_dur > C.TARGET_SECONDS - 2:
        need = total_dur - (C.TARGET_SECONDS - 2)
        for kind, floor in (("cta", 2.0),):
            if need <= 0:
                break
            for sg in segs:
                if sg["kind"] != kind:
                    continue
                cur = sg["frames"] / C.FPS
                cut = min(need, max(0.0, cur - floor))
                if cut > 0:
                    sg["frames"] = max(1, round((cur - cut) / (1 / C.FPS)))
                    need -= cut
        total_dur = sum(s["frames"] for s in segs) / C.FPS
        print(f"[plan] trimmed to {total_dur:.2f}s (target {C.TARGET_SECONDS:.0f}s)")
    if total_dur > C.HARD_LIMIT_SECONDS:
        print(f"[ERROR] {total_dur:.1f}s — 3 min se lamba hai, ye Short nahi "
              "banega, normal video ban jaayega. TIMER_SECONDS ya "
              "QUESTIONS_PER_VIDEO ghatao.")
    elif total_dur > C.TARGET_SECONDS + 5:
        print(f"[note] {total_dur:.1f}s — Short to rahega (limit 3 min), par "
              f"retention ke liye {C.TARGET_SECONDS:.0f}s ke aas-paas behtar hai. "
              "ELEVEN_SPEED badhao ya 'why' chhota karo.")

    frames_dir = outdir / "frames"
    total_frames = render_video(segs, frames_dir)
    track = render_audio(segs, total_frames)
    wav = sfx.write_wav(outdir / "audio.wav", track)

    mp4 = mux(frames_dir, wav, outdir / f"{slug}.mp4")
    shutil.rmtree(frames_dir)

    meta = build_seo(questions, args.slot)
    meta["video_file"] = str(mp4)
    meta["subjects"] = [q.get("sub", "GK") for q in questions]
    meta["duration_sec"] = round(total_frames / C.FPS, 2)
    # voice_engines metadata WRITE se PEHLE set hona chahiye — pehle ye baad me
    # set ho raha tha, isliye file me kabhi pahuncha hi nahi aur Telegram/
    # LAST_RUN dono me "kaunsi voice chali" gayab tha.
    used = {e.split("+")[0] for e in ENGINES}
    meta["voice_engines"] = sorted(used)
    (outdir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n🎙  voice: {', '.join(sorted(used))}"
          + ("  <- ElevenLabs fail hua, free voice pe chala" if "edge" in used else ""))
    print(f"✅ {mp4}  ({meta['duration_sec']}s)")
    print(f"📝 {outdir / 'metadata.json'}")
    print(f"🎬 {meta['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
