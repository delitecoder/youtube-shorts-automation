"""ffprobe wrapper (read-only)."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


@dataclass
class MediaInfo:
    format_name: str
    duration: float
    width: int
    height: int
    rotation: int
    fps: float
    vcodec: str
    pix_fmt: str
    acodec: str
    sample_rate: int
    size_bytes: int

    @property
    def display_size(self) -> tuple[int, int]:
        if abs(self.rotation) % 180 == 90:
            return self.height, self.width
        return self.width, self.height


def probe(path: Path) -> MediaInfo:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True)
    if out.returncode != 0:
        raise ValueError(f"ffprobe failed: {out.stderr.strip()[:300]}")
    d = json.loads(out.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    rot = 0
    if v:
        rot = int(v.get("tags", {}).get("rotate", 0) or 0)
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
    fps = float(Fraction(v["avg_frame_rate"])) if v and v.get("avg_frame_rate", "0/0") != "0/0" else 0.0
    fmt = d.get("format", {})
    return MediaInfo(
        format_name=fmt.get("format_name", ""), duration=float(fmt.get("duration", 0) or 0),
        width=int(v["width"]) if v else 0, height=int(v["height"]) if v else 0, rotation=rot,
        fps=round(fps, 3), vcodec=v.get("codec_name", "") if v else "",
        pix_fmt=v.get("pix_fmt", "") if v else "", acodec=a.get("codec_name", "") if a else "",
        sample_rate=int(a.get("sample_rate", 0)) if a else 0,
        size_bytes=int(fmt.get("size", 0) or Path(path).stat().st_size))


def decode_check(path: Path, timeout: int = 600) -> str:
    """Full decode to null sink; returns '' when clean, else the first error lines."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True, timeout=timeout)
    return "" if r.returncode == 0 and not r.stderr.strip() else (r.stderr.strip()[:500] or "decode failed")


def loudness_lufs(path: Path) -> float | None:
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in reversed(r.stderr.splitlines()):
        line = line.strip()
        if line.startswith("I:") and "LUFS" in line:
            return float(line.split()[1])
    return None
