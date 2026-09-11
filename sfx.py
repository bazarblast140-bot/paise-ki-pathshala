"""Generate the UI sounds the video uses, as WAV, with no asset downloads.

A short click on each new shot is the cheapest retention trick there is: it marks
the cut so the ear knows something changed even when the eye has drifted. It has
to be quiet — an obvious sound effect every six seconds is worse than silence.

Everything is synthesized with numpy so there are no licensing questions and no
files to ship.
"""
import wave

import numpy as np

from common import ROOT

SR = 44100
SFX_DIR = ROOT / "assets" / "sfx"


def _write(path, samples):
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767).astype("<i2")
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def click(path=None, gain=0.16):
    """A soft 'tok' — a fast downward pitch sweep with a sharp decay. Reads as a
    UI tick rather than a beep."""
    path = path or SFX_DIR / "click.wav"
    dur = 0.055
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    freq = 1500 * np.exp(-26 * t) + 420          # sweep down, settle low
    phase = 2 * np.pi * np.cumsum(freq) / SR
    env = np.exp(-58 * t)                         # near-instant decay
    body = np.sin(phase) * env
    # a touch of noise on the attack gives it a physical edge
    noise = np.random.default_rng(7).normal(0, 1, t.size) * np.exp(-260 * t) * 0.35
    return _write(path, (body + noise) * gain)


def whoosh(path=None, gain=0.10):
    """Filtered noise sweep for section changes."""
    path = path or SFX_DIR / "whoosh.wav"
    dur = 0.34
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    rng = np.random.default_rng(11)
    noise = rng.normal(0, 1, t.size)
    # one-pole lowpass whose cutoff opens then closes -> a swept 'shhh'
    cutoff = np.sin(np.pi * t / dur) ** 2
    out = np.zeros_like(noise)
    prev = 0.0
    for i in range(noise.size):
        a = 0.004 + 0.16 * cutoff[i]
        prev += a * (noise[i] - prev)
        out[i] = prev
    env = np.sin(np.pi * t / dur) ** 1.5
    return _write(path, out * env * 6.0 * gain)


def positive_ping(path=None, gain=0.18):
    """A pleasant, rising 'ding' for good news."""
    path = path or SFX_DIR / "positive_ping.wav"
    dur = 0.4
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    # Two sine waves, one rising, one a harmonic
    freq1 = 880 * (1 + 0.4 * t)
    freq2 = 1320 * (1 + 0.4 * t)
    phase1 = 2 * np.pi * np.cumsum(freq1) / SR
    phase2 = 2 * np.pi * np.cumsum(freq2) / SR
    # Gentle attack, longer decay
    env = np.sin(np.pi * t / dur) ** 0.5 * np.exp(-4 * t)
    body = (np.sin(phase1) * 0.6 + np.sin(phase2) * 0.4) * env
    return _write(path, body * gain)


def click_track(times, total_seconds, path=None, gain=0.16):
    """One WAV holding every shot-change click at its exact offset.

    Mixing a pre-built track against the narration needs two ffmpeg inputs; the
    alternative — one delayed input per shot — means a 60-input filtergraph on an
    8-minute video.
    """
    path = path or SFX_DIR / "clicks.wav"
    tick_path = SFX_DIR / "click.wav"
    if not tick_path.exists():
        click(tick_path, gain)
    with wave.open(str(tick_path), "rb") as w:
        tick = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32767.0

    track = np.zeros(int(SR * (total_seconds + 0.5)), dtype=np.float32)
    for t in times:
        i = int(SR * max(0.0, float(t)))
        end = min(track.size, i + tick.size)
        if end > i:
            track[i:end] += tick[:end - i]
    return _write(path, track)


def sfx_track(shots, total_seconds, offset=0.0, path=None):
    """One WAV holding special SFX (whooshes, pings) at their offsets."""
    path = path or SFX_DIR / "special_sfx.wav"
    sfx_files = ensure()

    sfx_audio = {}
    for name, sfx_path in sfx_files.items():
        if name == "click":
            continue
        with wave.open(str(sfx_path), "rb") as w:
            sfx_audio[name] = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32767.0

    track = np.zeros(int(SR * (total_seconds + 1.0)), dtype=np.float32)
    for shot in shots:
        sfx_name = shot.get("sfx")
        if not sfx_name or sfx_name not in sfx_audio:
            continue

        audio_data = sfx_audio[sfx_name]
        t = float(shot["start_sec"]) + offset
        i = int(SR * max(0.0, t))
        end = min(track.size, i + audio_data.size)
        if end > i:
            track[i:end] = np.clip(track[i:end] + audio_data[:end - i], -1.0, 1.0)
    return _write(path, track)


def ensure():
    """Create any missing sfx and return their paths."""
    paths = {"click": SFX_DIR / "click.wav", "whoosh": SFX_DIR / "whoosh.wav", "positive_ping": SFX_DIR / "positive_ping.wav"}
    if not paths["click"].exists():
        click(paths["click"])
    if not paths["whoosh"].exists():
        whoosh(paths["whoosh"])
    if not paths["positive_ping"].exists():
        positive_ping(paths["positive_ping"])
    return paths


if __name__ == "__main__":
    for name, p in ensure().items():
        print(f"{name}: {p} ({p.stat().st_size} bytes)")
