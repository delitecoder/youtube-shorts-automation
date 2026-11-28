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


def measure_lufs(path: Path) -> float | None:
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in reversed(r.stderr.splitlines()):
        line = line.strip()
        if line.startswith("I:") and "LUFS" in line:
            return float(line.split()[1])
    return None


def normalize_two_pass(src: Path, dst: Path, lufs: float = -14.0, tp: float = -1.5) -> dict:
    """Accurate EBU R128 normalisation for short narrated clips.

    Single-pass `loudnorm` undershoots on 45–60 s mixes with pauses (measured -15.8 / -16.2 LUFS).
    Pass 1 measures the whole file, pass 2 applies the measured values in linear mode, then the
    result is measured again and trimmed with a plain gain + true-peak limiter if it is still off
    by more than 0.5 LU. Returns {"lufs": final, "passes": n}."""
    import json as _json
    import re as _re

    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(src), "-af",
                        f"loudnorm=I={lufs}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = _re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, _re.S)
    if m:
        d = _json.loads(m.group(0))
        af = (f"loudnorm=I={lufs}:TP={tp}:LRA=11:measured_I={d['input_i']}:measured_TP={d['input_tp']}:"
              f"measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}:"
              f"offset={d['target_offset']}:linear=true")
    else:                                            # could not measure: fall back to one pass
        af = f"loudnorm=I={lufs}:TP={tp}:LRA=11"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", af, "-ar", str(SR), "-ac", "1",
                    str(dst)], check=True)
    passes, final = 2, measure_lufs(dst)
    if final is not None and abs(final - lufs) > 0.5:
        tmp = dst.with_name(dst.stem + "_trim.wav")
        limit = 10 ** (tp / 20)                      # -1.5 dBTP -> 0.841 linear
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(dst), "-af",
                        f"volume={lufs - final:.2f}dB,alimiter=limit={limit:.3f}:level=disabled",
                        "-ar", str(SR), "-ac", "1", str(tmp)], check=True)
        tmp.replace(dst)
        passes, final = 3, measure_lufs(dst)
    return {"lufs": final, "passes": passes}


def normalize(src: Path, dst: Path, lufs: float = -14.0, tp: float = -1.5) -> Path:
    """Single-pass EBU R128 loudness normalisation (target -14 LUFS, true peak -1.5 dBTP)."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af",
                    f"loudnorm=I={lufs}:TP={tp}:LRA=11", "-ar", str(SR), "-ac", "1", str(dst)],
                   check=True)
    return dst
