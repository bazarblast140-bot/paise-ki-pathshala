"""Spot effects under the narration. No downloaded samples.

The pad in bed.wav is not mixed. whoosh.wav and click.wav are the same numpy
synthesis as sfx.py, written here so the files can be committed (assets/sfx/
is gitignored). See assets/bed/LICENSE.
"""
import json
import math
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


# The old whoosh sat at 0.85, about as loud as the voice. 0.085 of that is a
# little past -18 dB. The callout click takes the same cut from its old 0.7.
# A quieter voice pulls both further down so the SFX peak stays at least 18 dB
# under that voice's RMS. The pad in bed.wav is not mixed.
OLD_WHOOSH_GAIN = 0.85
OLD_CLICK_GAIN = 0.7
SFX_OF_OLD = 0.085
SFX_UNDER_DB = 18.0


def _rms(mono):
    if mono.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))))


def _placed_gain(nominal, file_peak, voice_rms):
    """Nominal gain, or lower so the placed peak is 18 dB under the voice RMS.

    The cap is one PCM step under the line. A 16-bit write cannot round back over it.
    """
    nominal = float(nominal)
    file_peak = float(file_peak) or 1.0
    cap = float(voice_rms) * (10 ** (-SFX_UNDER_DB / 20.0))
    steps = max(0, int(np.floor(cap * 32767.0)) - 1)
    pcm_cap = steps / 32767.0
    return float(min(nominal, pcm_cap / file_peak))


def sfx_track(scenes, n_samples, sr=SR, voice_rms=0.0):
    """Whoosh on every scene change after t=0, click on callouts. No bed."""
    whoosh, wsr = _read(BED_DIR / "whoosh.wav")
    click, csr = _read(BED_DIR / "click.wav")
    if wsr != sr or csr != sr:
        raise RuntimeError("spot effects must be 44100 Hz")
    if whoosh.ndim > 1:
        whoosh = whoosh[:, 0]
    if click.ndim > 1:
        click = click[:, 0]
    whoosh_gain = _placed_gain(OLD_WHOOSH_GAIN * SFX_OF_OLD, float(np.max(np.abs(whoosh)) or 1.0), voice_rms)
    click_gain = _placed_gain(OLD_CLICK_GAIN * SFX_OF_OLD, float(np.max(np.abs(click)) or 1.0), voice_rms)
    track = np.zeros(n_samples, dtype=np.float32)
    for scene in scenes or []:
        start = float(scene.get("start") or 0)
        if start > 0.05:
            _place(track, whoosh * np.float32(whoosh_gain), start, sr)
        if scene.get("callout"):
            _place(track, click * np.float32(click_gain), start + 0.08, sr)
    return track


def mix_into(voice_path, scenes, out_path):
    """Whoosh and callout click under the narration. The bed is not mixed.

    Sample count stays the same. voice_path is only read.
    """
    voice, sr = _read(voice_path)
    if sr != SR:
        raise RuntimeError(f"voice must be {SR} Hz")
    stereo = voice.ndim == 2
    mono = voice.mean(axis=1) if stereo else voice
    voice_rms = _rms(mono)
    spots = sfx_track(scenes, mono.shape[0], sr, voice_rms=voice_rms)
    if stereo:
        mixed = voice + spots[:, None]
    else:
        mixed = voice + spots
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.98:
        mixed = mixed * np.float32(0.98 / peak)
    _write(out_path, mixed, sr)
    spot_rms = _rms(spots)
    present = spot_rms >= 1e-8
    under = None
    if present and voice_rms > 0:
        under = 20.0 * math.log10(voice_rms / spot_rms)
    record = {
        "bed_mixed": False,
        "background": False,
        "sfx_present": present,
        "sfx_under_db": under,
        "voice_rms": voice_rms,
        "sfx_rms": spot_rms,
    }
    out_path = Path(out_path)
    (out_path.parent / "audio_mix.json").write_text(
        json.dumps(record) + "\n", encoding="utf-8")
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
