"""Reference-video analysis for the director (Opus) and frame sheets for reviewing our renders.

analyze(video) measures what makes a reference video work, cheaply and deterministically:
  duration, aspect, cut times (ffmpeg scene detection), average shot length, cuts per minute,
  time of the first cut, loudness, words per minute and the first 3 seconds of speech (from a
  .vtt caption file next to the video, e.g. yt-dlp --write-auto-subs), plus a contact sheet: one
  frame per shot in a grid the director looks at (a single image, so very few tokens).
Reference videos are only analysed, never reused in our videos.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

from studio.video.media_probe import loudness_lufs, probe


def cut_times(video: Path, threshold: float = 0.3) -> list[float]:
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(video), "-vf",
                        f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    return [round(float(m), 2) for m in re.findall(r"pts_time:([\d.]+)", r.stderr)]


def vtt_words(vtt: Path) -> list[tuple[float, str]]:
    """(start seconds, text) per caption cue, de-duplicating YouTube's rolling auto-captions."""
    out, last = [], ""
    for m in re.finditer(r"(\d+):(\d+):([\d.]+) --> [^\n]*\n(.+?)(?:\n\n|\Z)", vtt.read_text("utf-8", "replace"), re.S):
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        text = re.sub(r"<[^>]+>", "", m.group(4)).strip().replace("\n", " ")
        if text and text != last:
            new = text[len(last):].strip() if text.startswith(last) else text
            if new:
                out.append((t, new))
            last = text
    return out


def contact_sheet(video: Path, times: list[float], dest: Path, cols: int = 6, width: int = 180) -> Path:
    times = times[:30] or [0.5]
    frames = []
    for k, t in enumerate(times):
        f = dest.parent / f".{dest.stem}_{k}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t + 0.15:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={width}:-2", str(f)], check=False)
        if f.exists():
            frames.append(f)
    rows = math.ceil(len(frames) / cols)
    inputs = []
    for f in frames:
        inputs += ["-i", str(f)]
    pad = cols * rows - len(frames)
    layout = "|".join(f"{(k % cols)}_{(k // cols)}" for k in range(cols * rows))
    if len(frames) == 1:
        frames[0].replace(dest)
        return dest
    # xstack needs equal tiles; pad with the last frame
    for _ in range(pad):
        inputs += ["-i", str(frames[-1])]
    tile_w = width
    tile_h = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height",
                                 "-of", "csv=p=0", str(frames[0])], capture_output=True, text=True).stdout.strip() or 320)
    layout = "|".join(f"{(k % cols) * tile_w}_{(k // cols) * tile_h}" for k in range(cols * rows))
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex",
                    f"xstack=inputs={cols * rows}:layout={layout}", str(dest)], check=True)
    for f in frames:
        f.unlink(missing_ok=True)
    return dest


def analyze(video: Path, out_dir: Path) -> dict:
    info = probe(video)
    cuts = cut_times(video)
    shots = [b - a for a, b in zip([0.0] + cuts, cuts + [info.duration]) if b - a > 0.05]
    res = {"file": video.name, "duration": round(info.duration, 1), "size": f"{info.width}x{info.height}",
           "vertical": info.height > info.width, "cuts": len(cuts),
           "cuts_per_min": round(len(cuts) / max(info.duration / 60, 0.01), 1),
           "avg_shot_s": round(sum(shots) / len(shots), 2) if shots else None,
           "first_cut_s": cuts[0] if cuts else None, "lufs": loudness_lufs(video)}
    vtt = next(iter(sorted(video.parent.glob(video.stem + "*.vtt"))), None)
    if vtt:
        words = vtt_words(vtt)
        n = sum(len(t.split()) for _, t in words)
        res["wpm"] = round(n / max(info.duration / 60, 0.01))
        res["hook_first_3s"] = " ".join(t for s, t in words if s < 3.0)[:200]
        res["opening_15s"] = " ".join(t for s, t in words if s < 15.0)[:600]
    sheet = out_dir / f"{video.stem}_sheet.png"
    contact_sheet(video, [0.0] + cuts, sheet)
    res["contact_sheet"] = str(sheet)
    return res


def scene_frames(video: Path, timings: list[dict], dest: Path) -> Path:
    """Director review sheet for OUR render: 2 frames per scene (40 % and 85 % into the scene)."""
    times = []
    for t in timings:
        d = t["end"] - t["start"]
        times += [t["start"] + 0.4 * d, t["start"] + 0.85 * d]
    return contact_sheet(video, [x - 0.15 for x in times], dest, cols=min(8, len(times)), width=200)


def save(results: list[dict], path: Path) -> None:
    path.write_text(json.dumps(results, indent=1))
