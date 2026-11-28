"""Objective video QC on the RENDERED file (not the source code): decode, black frames, frozen runs,
resolution/aspect, A/V sync. Thresholds from config/video.yaml. Critical failures block approval."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from studio.core import conf
from studio.video.media_probe import decode_check, probe


def black_ratio(path: Path, duration: float) -> tuple[float, list[tuple[float, float]]]:
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-vf", "blackdetect=d=0.1:pix_th=0.08",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    spans = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", r.stderr)]
    total = sum(b - a for a, b in spans)
    return (total / duration if duration else 0.0), spans


def frozen_runs(path: Path, min_s: float) -> list[tuple[float, float]]:
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-vf", f"freezedetect=n=-60dB:d={min_s}",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", r.stderr)]
    durs = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", r.stderr)]
    return list(zip(starts, durs + [None] * (len(starts) - len(durs))))


def check(path: Path, long_: bool = False, cfg: dict | None = None) -> dict:
    c = cfg or conf.load("video")
    q = c["qc"]
    canvas = c["canvas"]["long" if long_ else "short"]
    critical, warnings, m = [], [], {}
    if not path.exists() or path.stat().st_size < 10_000:
        return {"pass": False, "critical": ["rendered video missing or empty"], "warnings": [], "measured": {}}
    err = decode_check(path)
    m["decode_ok"] = not err
    if err and q.get("require_decode_ok", True):
        critical.append(f"decode errors: {err[:160]}")
    try:
        info = probe(path)
    except ValueError as e:
        return {"pass": False, "critical": [*critical, f"unreadable container: {str(e)[:160]}"], "warnings": [],
                "measured": m}
    m.update({"width": info.width, "height": info.height, "fps": info.fps, "duration_s": round(info.duration, 2)})
    if (info.width, info.height) != (canvas["w"], canvas["h"]):
        critical.append(f"resolution {info.width}x{info.height} != {canvas['w']}x{canvas['h']}")
    ratio, spans = black_ratio(path, info.duration)
    m["black_ratio"], m["black_spans"] = round(ratio, 4), spans[:10]
    if ratio > q["black_frame_ratio_max"]:
        critical.append(f"black frames {ratio:.1%} of the video")
    frozen = frozen_runs(path, q["frozen_run_s_max"])
    m["frozen_runs"] = frozen[:10]
    if frozen:
        warnings.append(f"{len(frozen)} frozen run(s) of >= {q['frozen_run_s_max']} s (static shots? check intent)")
    a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    v = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    if not a:
        critical.append("no audio stream")
    else:
        drift = abs(float(a) - float(v or 0)) * 1000
        m["av_drift_ms"] = round(drift, 1)
        if drift > q["av_sync_max_ms"]:
            critical.append(f"A/V duration drift {drift:.0f} ms")
    return {"pass": not critical, "critical": critical, "warnings": warnings, "measured": m}
