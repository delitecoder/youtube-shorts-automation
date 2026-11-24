"""Narration assembly: per-scene TTS -> 48 kHz mono PCM -> timeline with pads -> loudness-normalised WAV."""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

SR = 48000
LEAD_S = 0.10          # narration starts almost immediately: the hook lands in the first second
SCENE_PAD_S = 0.35
TAIL_S = 0.8


def to_pcm(src: Path) -> bytes:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-f", "s16le", "-ac", "1",
                        "-ar", str(SR), "-"], capture_output=True, check=True)
    return r.stdout


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(round(seconds * SR))


def build_timeline(scene_audio: list[Path], out_wav: Path) -> list[dict]:
    """Concatenate scene clips; returns per-scene timing measured from real audio."""
    pcm = bytearray(silence(LEAD_S))
    timings, t = [], LEAD_S
    for i, clip in enumerate(scene_audio):
        data = to_pcm(clip)
        speech = len(data) / 2 / SR
        start = t if i else 0.0
        pcm += data
        t += speech
        pad = TAIL_S if i == len(scene_audio) - 1 else SCENE_PAD_S
        pcm += silence(pad)
        timings.append({"scene": i + 1, "start": round(start, 3), "speech_start": round(t - speech, 3),
                        "speech_end": round(t, 3), "end": round(t + pad, 3)})
        t += pad
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(pcm))
    return timings


def normalize(src: Path, dst: Path, lufs: float = -14.0, tp: float = -1.5) -> Path:
    """Single-pass EBU R128 loudness normalisation (target -14 LUFS, true peak -1.5 dBTP)."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af",
                    f"loudnorm=I={lufs}:TP={tp}:LRA=11", "-ar", str(SR), "-ac", "1", str(dst)],
                   check=True)
    return dst
