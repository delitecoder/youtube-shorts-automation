"""Eligibility checks for an EXISTING video from Drive. Read-only: the file is never modified,
converted or re-encoded. Unsuitable files go to manual review instead of being "fixed".

Rights: Drive cannot tell us who owns the content, so the file's Drive description must contain
an explicit declaration, one of:
    RIGHTS: OWNED
    RIGHTS: LICENSED <licence/permission note>
Otherwise the item is NEEDS_MANUAL_REVIEW.

Shorts eligibility (YouTube Help, "Understand three-minute YouTube Shorts"): square or vertical
aspect ratio and up to 3 minutes. Verified November 2026; re-check docs/costs.md links.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from studio.video.media_probe import MediaInfo, decode_check, probe

RIGHTS_RE = re.compile(r"^\s*RIGHTS:\s*(OWNED|LICENSED\s+\S.*)$", re.M)
ALLOWED_CONTAINERS = ("mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm")


@dataclass
class Eligibility:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rights: str = ""
    info: dict = field(default_factory=dict)


def rights_declaration(description: str | None) -> str:
    m = RIGHTS_RE.search(description or "")
    return m.group(1).strip() if m else ""


def assess(path: Path, drive_description: str | None, max_seconds: float = 180.0,
           max_bytes: int = 2 * 1024**3, full_decode: bool = True) -> Eligibility:
    reasons, warnings = [], []
    rights = rights_declaration(drive_description)
    if not rights:
        reasons.append("no rights declaration (add 'RIGHTS: OWNED' or 'RIGHTS: LICENSED <note>' "
                       "to the Drive file description)")
    try:
        info: MediaInfo = probe(path)
    except ValueError as e:
        return Eligibility(False, reasons + [f"unreadable media: {e}"], rights=rights)
    w, h = info.display_size
    if info.format_name not in ALLOWED_CONTAINERS:
        reasons.append(f"container {info.format_name} not accepted (mp4/mov/webm)")
    if not info.vcodec:
        reasons.append("no video stream")
    if info.duration <= 0 or info.duration > max_seconds:
        reasons.append(f"duration {info.duration:.1f}s outside 0–{max_seconds:.0f}s")
    if w and h and h < w:
        reasons.append(f"landscape {w}x{h}; Shorts need square or vertical")
    if info.size_bytes > max_bytes:
        reasons.append(f"file too large ({info.size_bytes} bytes)")
    if not info.acodec:
        warnings.append("no audio stream")
    if (w, h) != (1080, 1920):
        warnings.append(f"resolution {w}x{h} (not 1080x1920); uploaded unchanged as required")
    if w and h and abs(h / w - 16 / 9) > 0.02 and h != w:
        warnings.append("not exactly 9:16; YouTube may letterbox")
    if full_decode and not reasons:
        err = decode_check(path)
        if err:
            reasons.append(f"integrity check failed: {err}")
    return Eligibility(not reasons, reasons, warnings, rights, info.__dict__ | {"display": [w, h]})
