"""Objective audio QC (signal analysis, not model judgement). Critical failures block approval.

Checks on the final mix: decodable/non-empty, integrated loudness, true peak, loudness range, clipped samples.
Checks on stems: long silences inside the dialogue, speech-to-music ratio where speech is present,
stems present. Optional: duration match against the video, ASR word error (studio.audio.speech).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from studio.core import conf


def _read(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf
    x, sr = sf.read(str(path), always_2d=True, dtype="float32")
    return x, sr


def loudness(path: Path) -> dict:
    """EBU R128 integrated loudness, loudness range and true peak via ffmpeg ebur128."""
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    s = r.stderr[r.stderr.rfind("Summary:"):] if "Summary:" in r.stderr else r.stderr

    def num(pat):
        m = re.search(pat, s)
        return float(m.group(1)) if m else None
    return {"integrated_lufs": num(r"I:\s+(-?[\d.]+) LUFS"), "lra": num(r"LRA:\s+(-?[\d.]+) LU"),
            "true_peak_dbtp": num(r"Peak:\s+(-?[\d.]+|-inf) dBFS")}


def _frames_db(x: np.ndarray, sr: int, win: float = 0.05) -> np.ndarray:
    mono = x.mean(axis=1)
    n = int(sr * win)
    k = len(mono) // n
    if k == 0:
        return np.array([-120.0])
    f = mono[: k * n].reshape(k, n)
    return 20 * np.log10(np.sqrt((f ** 2).mean(axis=1)) + 1e-9)


def silences(x: np.ndarray, sr: int, thresh_db: float = -45.0, min_s: float = 1.2) -> list[tuple[float, float]]:
    """Internal silences (leading/trailing excluded) longer than min_s."""
    db = _frames_db(x, sr)
    active = np.where(db > thresh_db)[0]
    if len(active) == 0:
        return [(0.0, len(x) / sr)]
    out, run = [], None
    for i in range(active[0], active[-1] + 1):
        if db[i] <= thresh_db:
            run = i if run is None else run
        elif run is not None:
            if (i - run) * 0.05 >= min_s:
                out.append((round(run * 0.05, 2), round((i - run) * 0.05, 2)))
            run = None
    return out


def speech_to_music_db(dialogue: np.ndarray, music: np.ndarray, sr: int) -> float | None:
    d, m = _frames_db(dialogue, sr), _frames_db(music, sr)
    n = min(len(d), len(m))
    if n == 0:
        return None
    d, m = d[:n], m[:n]
    speech = d > (np.percentile(d, 90) - 20)              # frames where the narrator is talking
    if speech.sum() < 5:
        return None
    return float(np.median(d[speech] - m[speech]))


def check(final: Path, stems_dir: Path | None = None, video_duration: float | None = None,
          cfg: dict | None = None, wer: float | None = None) -> dict:
    c = cfg or conf.load("audio")
    q, ms = c["qc"], c["master"]
    critical, warnings, m = [], [], {}
    if not final.exists() or final.stat().st_size < 1000:
        return {"pass": False, "critical": ["final audio missing or empty"], "warnings": [], "measured": {}}
    try:
        x, sr = _read(final)
    except Exception as e:  # noqa: BLE001
        return {"pass": False, "critical": [f"final audio does not decode: {type(e).__name__}"], "warnings": [], "measured": {}}
    m["duration_s"] = round(len(x) / sr, 3)
    m["channels"] = x.shape[1]
    if np.abs(x).max() < 1e-4:
        critical.append("final audio is silent")
    m.update(loudness(final))
    m["clipped_samples"] = int((np.abs(x) >= 0.999).sum())
    if m["integrated_lufs"] is None or abs(m["integrated_lufs"] - ms["integrated_lufs"]) > 1.5:
        critical.append(f"integrated loudness {m['integrated_lufs']} LUFS vs target {ms['integrated_lufs']}")
    if m["true_peak_dbtp"] is not None and m["true_peak_dbtp"] > ms["true_peak_dbtp"] + 0.5:
        critical.append(f"true peak {m['true_peak_dbtp']} dBTP above {ms['true_peak_dbtp']}")
    if m["clipped_samples"] > q["clipping_samples_max"]:
        critical.append(f"{m['clipped_samples']} clipped samples")
    if m["lra"] is not None and m["lra"] > ms["lra_max"] + 4:
        warnings.append(f"loudness range {m['lra']} LU is wide")
    if video_duration is not None and abs(m["duration_s"] - video_duration) > 0.1:
        critical.append(f"audio {m['duration_s']} s vs video {video_duration} s")
    if stems_dir:
        missing = [s for s in c["stems"] if not (stems_dir / f"{s}.wav").exists()]
        if missing:
            critical.append(f"missing stems: {missing}")
        else:
            dlg, _ = _read(stems_dir / "dialogue.wav")
            mus, _ = _read(stems_dir / "music.wav")
            sil = silences(dlg, sr, min_s=q["max_silence_s"])
            m["long_silences"] = sil
            if sil:
                warnings.append(f"{len(sil)} silence(s) over {q['max_silence_s']} s in narration")
            r = speech_to_music_db(dlg, mus, sr)
            m["speech_to_music_db"] = None if r is None else round(r, 1)
            if np.abs(mus).max() > 1e-4 and r is not None and r < q["min_speech_to_music_db"]:
                critical.append(f"music masks speech: speech only {r:.1f} dB above music")
    if wer is not None:
        m["asr_wer"] = round(wer, 3)
        if wer > q["max_wer"]:
            critical.append(f"speech does not match the script (WER {wer:.2f} > {q['max_wer']})")
    return {"pass": not critical, "critical": critical, "warnings": warnings, "measured": m}


def write_report(report: dict, path: Path) -> Path:
    path.write_text(json.dumps(report, indent=1, default=str))
    return path
