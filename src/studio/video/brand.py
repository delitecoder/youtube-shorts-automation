"""Channel branding: the DeliteCoder logo badge + handle shown in every video.

The logo is cached in runtime/library/brand/ (≈30 KB). Source order: Drive
`References/Images/brand_logo.png` (owner can replace it any time) → the channel avatar from the
YouTube API, which is then saved to Drive once so later runs never need YouTube.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from studio.core.config import RUNTIME_DIR

BRAND_DIR = RUNTIME_DIR / "library" / "brand"
LOGO_NAME = "brand_logo.png"
SIZE = 256


def logo_path() -> Path | None:
    p = BRAND_DIR / LOGO_NAME
    return p if p.exists() and p.stat().st_size > 0 else None


def handle() -> str:
    try:
        return json.loads((BRAND_DIR / "brand.json").read_text()).get("handle", "")
    except (OSError, ValueError):
        return ""


def save_meta(handle_: str) -> None:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    (BRAND_DIR / "brand.json").write_text(json.dumps({"handle": handle_}))


def make_badge(data: bytes, dest: Path | None = None) -> Path:
    """Any image bytes -> 256 px rounded-square PNG badge (keeps a wordmark fully visible)."""
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    # trim plain white/transparent margins so the mark fills the badge, then pad 10 %
    ink = Image.eval(im.convert("L"), lambda v: 255 if v < 235 else 0)
    if im.getchannel("A").getextrema()[0] < 255:
        ink = Image.composite(ink, Image.new("L", im.size, 0), im.getchannel("A"))
    box = ink.getbbox()
    if box:
        im = im.crop(box)
        pad = int(max(im.size) * 0.12)
        side = max(im.size) + 2 * pad
        sq = Image.new("RGBA", (side, side), (255, 255, 255, 255))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2), im)
        im = sq
    s = min(im.size)
    im = im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2))
    im = im.resize((SIZE, SIZE), Image.LANCZOS)
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=SIZE // 5, fill=255)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    dest = dest or BRAND_DIR / LOGO_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(dest, optimize=True)
    return dest
