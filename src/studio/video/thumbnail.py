"""Thumbnail per video: the story world + the main character reacting + 3–5 big words.

Built from the video's own first `story` scene (same cast, same world: consistent with the video),
captured with `hyperframes snapshot`, saved as JPEG < 2 MB (YouTube limit).
Long videos: 1280×720. Shorts: 1080×1920 (YouTube allows custom Shorts thumbnails since 2026-11-03).
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path

from studio.video import hyperframes as hfr
from studio.animation import world as wld


def _words(sb: dict) -> str:
    t = sb.get("thumbnail_text") or sb.get("titles", [""])[sb.get("chosen_title", 0) or 0]
    t = t.split(":", 1)[-1].strip() if ":" in t else t
    return " ".join(t.split()[:6])


def make(sb: dict, workdir: Path, long_: bool) -> Path | None:
    scene = next((s for s in sb.get("scenes", []) if s.get("visual", {}).get("type") == "story"), None)
    v = dict(scene["visual"]) if scene else {"type": "story", "location": "office",
                                              "cast": [{"who": "arjun", "x": 0.3}]}
    cast = [dict(c) for c in v.get("cast", [])][:1] or [{"who": "arjun"}]
    cast[0]["x"] = 0.26 if long_ else 0.5          # long: character left, words right; Short: centred
    v["zoom"] = 1.3 if long_ else 1.35
    for c in cast:                                   # strongest readable reaction for the thumbnail
        c.pop("say", None)
        c.pop("walk_from", None)
        if c.get("who") == "byte":
            c["mood"] = [{"at": 0, "face": "surprised"}]
        else:
            c["actions"] = [{"at": 0, "pose": "shocked", "face": "surprised"}]
    v["cast"] = cast
    v.pop("sign", None)
    world_html, js = wld.visual(0, v, 0.0, 1.0, long_, 1)
    W, H = (1920, 1080) if long_ else (1080, 1920)
    title = html.escape(_words(sb).upper() if sb.get("language", "en") == "en" else _words(sb))
    proj = workdir / "thumb"
    if proj.exists():
        shutil.rmtree(proj)
    proj.mkdir(parents=True)
    shutil.copy(hfr.RENDERER_DIR / "node_modules" / "gsap" / "dist" / "gsap.min.js", proj / "gsap.min.js")
    css = (wld.CSS + f"""
html,body{{margin:0}}#stage{{position:relative;width:{W}px;height:{H}px;overflow:hidden;background:#f6efe3}}
.world{{position:absolute;left:0;right:0;bottom:0;top:{0 if long_ else int(H * 0.2)}px}}
.ttl{{position:absolute;{"left:46%;right:3%;top:8%" if long_ else "left:4%;right:4%;top:3%"};font-family:'DejaVu Sans','Noto Sans Tamil',sans-serif;font-weight:900;
 font-size:{128 if long_ else 124}px;line-height:1.0;color:#fff;-webkit-text-stroke:10px #1d2433;paint-order:stroke fill;
 text-shadow:0 10px 0 #1d2433}}
.ttl b{{color:#ffd84d}}
""")
    words = title.split()
    if words:
        words[-1] = f"<b>{words[-1]}</b>"
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><style>{css}</style></head><body>'
           f'<div id="stage" data-composition-id="main" data-start="0" data-duration="1" data-width="{W}" data-height="{H}" data-fps="30">'
           f'{world_html}<div class="ttl">{" ".join(words)}</div></div>'
           f'<script src="gsap.min.js"></script><script>const tl=gsap.timeline({{paused:true}});\n' + "\n".join(js)
           + '\ntl.set({}, {}, 1);window.__timelines={main:tl};</script></body></html>')
    (proj / "index.html").write_text(doc, encoding="utf-8")
    hf = str(hfr.RENDERER_DIR / "node_modules" / ".bin" / "hyperframes")
    snap = workdir / "thumb_snap"
    if snap.exists():
        shutil.rmtree(snap)
    subprocess.run([hf, "snapshot", str(proj), "--at", "0.95", "--no-end", "--output", str(snap)],
                   capture_output=True, text=True, timeout=300)
    pngs = sorted(snap.glob("*.png"))
    if not pngs:
        return None
    out = workdir / "thumbnail.jpg"
    size = "1280:720" if long_ else "1080:1920"
    for q in (3, 5, 8):                              # keep under YouTube's 2 MB limit
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(pngs[0]), "-vf", f"scale={size}", "-q:v", str(q), str(out)],
                       check=True)
        if out.stat().st_size < 1_900_000:
            break
    (workdir / "thumbnail.json").write_text(json.dumps({"text": _words(sb)}))
    return out
