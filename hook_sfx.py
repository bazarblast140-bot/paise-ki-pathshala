"""Keyboard click for the hook typewriter. Synthesized here, no sample file.

Same idea as the quiz Shorts click: a short noise burst plus a high tick,
written as PCM so there is nothing to license. One click is placed on each
grapheme reveal and mixed under the voice at 0.18 gain. The voice buffer is
not shifted, so narration does not wait for the clicks. The sum is limited
to full scale so overlapping clicks cannot clip.
"""
import wave

import numpy as np

SR = 44100
GAIN = 0.18


def keyclick(sr=SR):
    """Unit-peak click, about 28 ms. Deterministic, so tests and renders match."""
    dur = 0.028
    n = int(sr * dur)
    t = np.arange(n, dtype=np.float64) / sr
    rng = np.random.default_rng(18618)
    noise = rng.standard_normal(n)
    tick = np.sin(2 * np.pi * 1850 * t) * np.exp(-t * 220)
    thud = np.sin(2 * np.pi * 320 * t) * np.exp(-t * 90)
    burst = noise * np.exp(-t * 420)
    sig = 0.55 * burst + 0.30 * tick + 0.15 * thud
    peak = float(np.max(np.abs(sig))) or 1.0
    return (sig / peak).astype(np.float32)


def mix_under_voice(voice, times, gain=GAIN, sr=SR):
    """Add one click at each time. Same length as `voice`. Peak stays within [-1, 1]."""
    voice = np.asarray(voice, dtype=np.float32)
    stereo = voice.ndim == 2
    out = voice.copy()
    click = keyclick(sr) * float(gain)
    for t in times or []:
        i = int(round(float(t) * sr))
        if i >= (out.shape[0] if stereo else out.size) or i < 0:
            continue
        end = min(out.shape[0] if stereo else out.size, i + click.size)
        n = end - i
        if n <= 0:
            continue
        if stereo:
            out[i:end, :] += click[:n, None]
        else:
            out[i:end] += click[:n]
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > 1.0:
        out *= np.float32(1.0 / peak)
    return out


def _read(path):
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sr = handle.getframerate()
        width = handle.getsampwidth()
        frames = handle.getnframes()
        raw = handle.readframes(frames)
    if width != 2:
        raise ValueError(f"expected 16-bit wav, got {width * 8}-bit")
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels)
    return pcm, sr, channels


def _write(path, samples, sr):
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples.reshape(-1) * 32767.0).astype("<i2")
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())


def mix_file(path, times, gain=GAIN):
    """Mix clicks into an existing narration wav, in place. Length is unchanged."""
    voice, sr, _channels = _read(path)
    mixed = mix_under_voice(voice, times, gain=gain, sr=sr)
    _write(path, mixed, sr)
    return mixed
