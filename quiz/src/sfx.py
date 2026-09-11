"""Saare sound effects yahin generate hote hain — koi royalty/copyright issue nahi,
koi download nahi. numpy se banate hain, WAV likhte hain."""
import math
import wave
import numpy as np
from config import SAMPLE_RATE, OUT


def _env(n, attack=0.005, decay=0.12):
    """Short percussive envelope."""
    a = int(SAMPLE_RATE * attack)
    d = n - a
    env = np.concatenate([
        np.linspace(0, 1, max(a, 1)),
        np.exp(-np.linspace(0, 1, max(d, 1)) / max(decay, 1e-6)),
    ])
    return env[:n]


def _tone(freq, dur, kind="sine"):
    n = int(SAMPLE_RATE * dur)
    t = np.arange(n) / SAMPLE_RATE
    if kind == "sine":
        w = np.sin(2 * math.pi * freq * t)
    elif kind == "tri":
        w = 2 * np.abs(2 * ((t * freq) % 1) - 1) - 1
    else:  # noise-ish click
        w = np.random.default_rng(int(freq)).uniform(-1, 1, n)
    return w


def tick(high=False):
    """Clock tik / tok. high=True -> 'tik', False -> 'tok'."""
    dur = 0.07
    n = int(SAMPLE_RATE * dur)
    base = 2100 if high else 1500
    body = 0.6 * _tone(base, dur, "sine") + 0.4 * _tone(base * 1.6, dur, "tri")
    click = 0.5 * _tone(base, dur, "noise")
    sig = (body + click) * _env(n, attack=0.001, decay=0.05)
    return sig.astype(np.float32)


def ding():
    """Sahi jawab reveal — do note ka pleasant chime (C6 -> E6)."""
    a = _tone(1046.5, 0.30) * _env(int(SAMPLE_RATE * 0.30), decay=0.35)
    b = _tone(1318.5, 0.55) * _env(int(SAMPLE_RATE * 0.55), decay=0.40)
    n = max(len(a), len(b))
    out = np.zeros(n, dtype=np.float64)
    out[:len(a)] += a * 0.7
    out[:len(b)] += b * 0.8
    return (out / (np.max(np.abs(out)) + 1e-9) * 0.85).astype(np.float32)


def whoosh():
    """Question transition."""
    dur = 0.45
    n = int(SAMPLE_RATE * dur)
    t = np.arange(n) / SAMPLE_RATE
    sweep = np.sin(2 * math.pi * (300 + 1400 * (t / dur) ** 2) * t)
    noise = np.random.default_rng(7).uniform(-1, 1, n) * 0.35
    env = np.sin(math.pi * t / dur) ** 2
    return ((sweep * 0.6 + noise) * env * 0.5).astype(np.float32)


def build_timer_bed(seconds: int, volume: float) -> np.ndarray:
    """8 second ka tik-tik bed. Har second ek tick, aakhri 3 second me speed-up
    (2 tick/sec) taki tension bane."""
    n = int(SAMPLE_RATE * seconds)
    bed = np.zeros(n, dtype=np.float32)
    marks = []
    for s in range(seconds):
        if s >= seconds - 3:
            marks += [s, s + 0.5]
        else:
            marks.append(s)
    for i, m in enumerate(marks):
        start = int(m * SAMPLE_RATE)
        snd = tick(high=(i % 2 == 0))
        end = min(start + len(snd), n)
        if end > start:
            bed[start:end] += snd[:end - start]
    return bed * volume


def write_wav(path, mono: np.ndarray, sr=SAMPLE_RATE):
    mono = np.clip(mono, -1.0, 1.0)
    pcm = (mono * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


if __name__ == "__main__":
    write_wav(OUT / "_sfx_timer.wav", build_timer_bed(8, 0.3))
    write_wav(OUT / "_sfx_ding.wav", ding())
    print("sfx ok")
