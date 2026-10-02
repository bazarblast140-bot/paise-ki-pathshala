"""Original sound bed and spot effects. No downloaded samples.

bed.wav is a synthesized pad, measured near -22 LUFS before it is ducked.
whoosh.wav and click.wav are the same numpy synthesis as sfx.py, written here
so the files can be committed (assets/sfx/ is gitignored). See assets/bed/LICENSE.
"""
import wave
from pathlib import Path

import numpy as np

import sfx

SR = 44100
BED_DIR = Path(__file__).resolve().parent / "assets" / "bed"
TARGET_LUFS = -22.0


def _write(path, samples, sr=SR):
    samples = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    if samples.ndim == 1:
        channels = 1
        pcm = (samples * 32767.0).astype("<i2")
    else:
        channels = samples.shape[1]
        pcm = (samples.reshape(-1) * 32767.0).astype("<i2")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())
    return path


def _read(path):
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sr = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels)
    return pcm, sr


def synthesize_bed(seconds=32.0, sr=SR):
    """Calm two-note pad plus a little filtered noise. Deterministic."""
    n = int(sr * seconds)
    t = np.arange(n, dtype=np.float64) / sr
    lfo = 0.85 + 0.15 * np.sin(2 * np.pi * 0.07 * t)
    tone = (
        0.34 * np.sin(2 * np.pi * 110.0 * t)
        + 0.22 * np.sin(2 * np.pi * 164.81 * t)
        + 0.14 * np.sin(2 * np.pi * 220.0 * t)
        + 0.06 * np.sin(2 * np.pi * 329.63 * t + 0.4)
    ) * lfo
    rng = np.random.default_rng(20261002)
    noise = rng.normal(0, 1, n)
    prev = 0.0
    air = np.empty(n, dtype=np.float64)
    for i in range(n):
        prev += 0.02 * (noise[i] - prev)
        air[i] = prev
    fade = np.ones(n)
    a = int(sr * 1.2)
    fade[:a] = np.linspace(0, 1, a)
    fade[-a:] = np.linspace(1, 0, a)
    raw = ((tone + air * 0.35) * fade).astype(np.float32)
    # synthesize_bed() measures about -13 LUFS. This gain lands the file near -22.
    return raw * np.float32(10 ** ((-22.0 - (-13.0)) / 20.0))


def _place(track, clip, start_s, sr):
    i = int(round(float(start_s) * sr))
    if i >= track.shape[0] or i < 0:
        return
    n = min(clip.shape[0], track.shape[0] - i)
    if n <= 0:
        return
    if track.ndim == 1:
        track[i:i + n] += clip[:n]
    else:
        track[i:i + n, :] += clip[:n, None]


def sfx_track(scenes, n_samples, sr=SR):
    """Whoosh on every scene change after t=0, soft click on callouts."""
    whoosh, wsr = _read(BED_DIR / "whoosh.wav")
    click, csr = _read(BED_DIR / "click.wav")
    if wsr != sr or csr != sr:
        raise RuntimeError("spot effects must be 44100 Hz")
    if whoosh.ndim > 1:
        whoosh = whoosh[:, 0]
    if click.ndim > 1:
        click = click[:, 0]
    track = np.zeros(n_samples, dtype=np.float32)
    for scene in scenes or []:
        start = float(scene.get("start") or 0)
        if start > 0.05:
            _place(track, whoosh * np.float32(0.85), start, sr)
        if scene.get("callout"):
            _place(track, click * np.float32(0.7), start + 0.08, sr)
    return track


def _duck_envelope(voice_mono, sr):
    """Causal peak follower. Loud voice pulls the bed down; the voice is not shifted."""
    alpha = np.exp(-1.0 / (sr * 0.12))
    follow = np.empty(voice_mono.shape[0], dtype=np.float32)
    acc = np.float32(0.0)
    for i, sample in enumerate(voice_mono):
        level = np.float32(abs(float(sample)))
        acc = level if level > acc else np.float32(alpha) * acc
        follow[i] = acc
    # Speech peaks near full scale; quiet gaps stay near 0.
    duck = np.clip(1.0 - follow * 6.0, 0.18, 1.0).astype(np.float32)
    return duck


def mix_into(voice_path, scenes, out_path):
    """Ducked bed + whoosh/click under the narration. Sample count stays the same."""
    voice, sr = _read(voice_path)
    if sr != SR:
        raise RuntimeError(f"voice must be {SR} Hz")
    stereo = voice.ndim == 2
    mono = voice.mean(axis=1) if stereo else voice
    bed, bsr = _read(BED_DIR / "bed.wav")
    if bsr != SR:
        raise RuntimeError("bed must be 44100 Hz")
    if bed.ndim > 1:
        bed = bed[:, 0]
    n = mono.shape[0]
    if bed.shape[0] < n:
        reps = int(np.ceil(n / bed.shape[0]))
        bed = np.tile(bed, reps)
    bed = bed[:n]
    duck = _duck_envelope(mono, sr)
    spots = sfx_track(scenes, n, sr)
    added = bed * duck + spots
    if stereo:
        mixed = voice + added[:, None]
    else:
        mixed = voice + added
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.98:
        mixed = mixed * np.float32(0.98 / peak)
    _write(out_path, mixed, sr)
    return out_path


def ensure_assets():
    """Write the committed-style assets if they are missing. Does not retune LUFS."""
    BED_DIR.mkdir(parents=True, exist_ok=True)
    if not (BED_DIR / "whoosh.wav").exists():
        sfx.whoosh(BED_DIR / "whoosh.wav", gain=0.20)
    if not (BED_DIR / "click.wav").exists():
        sfx.click(BED_DIR / "click.wav", gain=0.12)
    if not (BED_DIR / "bed.wav").exists():
        _write(BED_DIR / "bed.wav", synthesize_bed())
    return {
        "bed": BED_DIR / "bed.wav",
        "whoosh": BED_DIR / "whoosh.wav",
        "click": BED_DIR / "click.wav",
    }


if __name__ == "__main__":
    BED_DIR.mkdir(parents=True, exist_ok=True)
    sfx.whoosh(BED_DIR / "whoosh.wav", gain=0.20)
    sfx.click(BED_DIR / "click.wav", gain=0.12)
    _write(BED_DIR / "bed.wav", synthesize_bed())
    for name, path in (("bed", BED_DIR / "bed.wav"), ("whoosh", BED_DIR / "whoosh.wav"), ("click", BED_DIR / "click.wav")):
        print(f"{name}: {path} ({path.stat().st_size} bytes)")
