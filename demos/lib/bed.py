"""Synthesises a quiet music bed for a demo video from scratch (no samples, no third-party music).

Usage: python bed.py cues.json out.wav

Adapted from the promo's synthesiser (promo/audio/synth.py): the same warm pad on the A minor loop,
softer and slower, with chimes on the real moments in the edit. cues.json is written by render.mjs:
  {"duration": seconds, "cues": [{"t": seconds, "kind": "card" | "chapter" | "approval" | "success" | "block" | "tick"}]}
"""

import json
import sys
import wave

import numpy as np
from scipy.signal import lfilter

SR = 48000
BPM = 84
BEAT = 60 / BPM
RNG = np.random.default_rng(20261002)


def midi(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def lowpass(x, cutoff):
    a = np.exp(-2 * np.pi * cutoff / SR)
    return lfilter([1 - a], [1, -a], x)


def envelope(n, attack, release):
    t = np.arange(n) / SR
    env = np.minimum(1.0, t / max(attack, 1e-4))
    total = n / SR
    return env * np.clip((total - t) / max(release, 1e-4), 0, 1)


def add(buf, start_s, sig, gain=1.0):
    i = int(round(start_s * SR))
    if i >= buf.shape[1]:
        return
    if i < 0:
        sig = sig[:, -i:]
        i = 0
    n = min(buf.shape[1] - i, sig.shape[-1])
    buf[:, i:i + n] += gain * sig[:, :n]


def pan(sig, p):
    a = (p + 1) * np.pi / 4
    return np.vstack([sig * np.cos(a), sig * np.sin(a)])


def pad_voice(freq, dur, bright):
    n = int(dur * SR)
    t = np.arange(n) / SR
    sig = np.zeros(n)
    for det in (-0.08, 0.0, 0.07):
        f = freq * 2 ** (det / 12)
        ph = RNG.uniform(0, 2 * np.pi)
        for h, amp in ((1, 1.0), (2, 0.36), (3, 0.16), (4, 0.08)):
            sig += amp * np.sin(2 * np.pi * f * h * t + ph * h)
    return lowpass(sig / 6, bright)


def pluck(freq, dur=1.4, decay=4.2):
    n = int(dur * SR)
    t = np.arange(n) / SR
    env = np.exp(-t * decay) * np.minimum(1, t / 0.005)
    return (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t) * np.exp(-t * 8)) * env


def bell(freq, dur=2.6):
    n = int(dur * SR)
    t = np.arange(n) / SR
    partials = ((1, 1.0, 2.2), (2.76, 0.42, 3.8), (5.4, 0.2, 6.5), (8.93, 0.08, 9.0))
    return sum(a * np.sin(2 * np.pi * freq * r * t) * np.exp(-t * d) for r, a, d in partials) * np.minimum(1, t / 0.003)


def thud(dur=0.6):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 55 + 40 * np.exp(-t * 20)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)


def reverb(stereo, mix=0.25):
    out = stereo.copy()
    for k, d in enumerate((0.0297, 0.0371, 0.0411, 0.0437, 0.053, 0.067)):
        di = int(d * SR)
        g = 0.62 - k * 0.04
        for ch in range(2):
            src = stereo[ch]
            y = np.zeros_like(src)
            for rep in range(1, 9):
                off = di * rep + ch * 113
                if off >= len(src):
                    break
                y[off:] += src[:-off] * g ** rep
            out[ch] += lowpass(y, 3600) * mix / 6 * 2.2
    return out


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    dur = float(spec["duration"])
    cues = spec["cues"]
    n = int(dur * SR) + 1
    mix = np.zeros((2, n))
    chords = [[45, 57, 60, 64, 67, 71], [41, 53, 57, 60, 64, 69], [48, 55, 59, 62, 64, 67], [43, 55, 59, 62, 64, 66]]
    chord_len = BEAT * 8
    pos, k = 0.0, 0
    while pos < dur:
        seg = min(chord_len + 1.5, dur - pos + 0.01)
        for j, note in enumerate(chords[k % 4]):
            v = pad_voice(midi(note), seg, 700 if note < 50 else 1900) * envelope(int(seg * SR), 1.2, 1.5)
            add(mix, pos, pan(v, j / 5 * 1.1 - 0.55), 0.14 if note < 50 else 0.06)
        # a sparse, gentle arpeggio: one pluck per beat
        for b in range(8):
            t = pos + b * BEAT
            if t >= dur - 1.0:
                break
            tones = chords[k % 4][1:]
            note = tones[(b * 3 + k) % len(tones)] + 12
            add(mix, t, pan(pluck(midi(note)), 0.4 * np.sin(b + k)), 0.035 if b % 2 else 0.05)
        pos += chord_len
        k += 1

    for c in cues:
        t, kind = float(c["t"]), c["kind"]
        if kind in ("card", "chapter"):
            for j, note in enumerate([76, 79, 83]):
                add(mix, t + j * 0.08, pan(bell(midi(note), 2.0), -0.3 + j * 0.3), 0.04)
        elif kind == "approval":
            add(mix, t, pan(bell(midi(79), 2.2), -0.15), 0.08)
            add(mix, t + 0.23, pan(bell(midi(84), 2.6), 0.15), 0.08)
        elif kind == "success":
            for j, note in enumerate([69, 72, 76, 79, 84]):
                add(mix, t + j * 0.075, pan(bell(midi(note), 2.4), -0.5 + j * 0.25), 0.055)
        elif kind == "block":
            add(mix, t, pan(thud(), 0), 0.25)
            add(mix, t + 0.02, pan(bell(midi(62), 1.4), 0), 0.04)
        elif kind == "tick":
            add(mix, t, pan(bell(midi(88), 0.6), 0.3), 0.025)
        else:
            raise ValueError(f"unknown cue kind {kind!r}")

    t_axis = np.arange(n) / SR
    mix *= np.clip(t_axis / 1.5, 0, 1) * np.clip((dur - t_axis) / 2.0, 0, 1) ** 1.5
    mix = reverb(mix)
    mix = np.tanh(mix * 1.5) / 1.5
    peak = np.max(np.abs(mix))
    if peak <= 0:
        raise RuntimeError("synthesised audio is silent")
    mix *= 0.85 / peak
    pcm = (np.clip(mix.T, -1, 1) * 32767).astype("<i2")
    with wave.open(sys.argv[2], "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"Wrote {sys.argv[2]}: {dur:.2f} s, {len(cues)} cues")


if __name__ == "__main__":
    main()
