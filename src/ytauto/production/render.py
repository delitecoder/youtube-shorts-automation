"""CPU renderer: Pillow frames piped to FFmpeg -> 1080x1920, 30 fps, H.264 (yuv420p) + AAC 48 kHz.

Layout (safe zones for the Shorts UI):
  y   0–200   top bar (progress line, small series label)
  y 230–400   scene header (on_screen_text)
  y 430–1180  animated visual
  y 1230–1450 captions (active word highlighted)
  y >1500     kept clear: YouTube overlays title, channel and buttons there; right 150 px avoided.
All visuals are drawn from scratch (no third-party footage, music or images).
"""
from __future__ import annotations

import json
import math
import shutil
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import audio, captions, sound
from .media_probe import loudness_lufs, probe

W, H, FPS = 1080, 1920, 30
MARGIN_L, MARGIN_R = 90, 150
LOGO_PX = 76
BG_TOP, BG_BOTTOM = (10, 14, 32), (22, 18, 48)
WHITE, MUTED = (245, 247, 255), (150, 160, 190)
ACCENTS = [(34, 211, 238), (251, 191, 36), (244, 114, 182), (163, 230, 53)]
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"]


def _font(name: str, size: int):
    for d in FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


F = {
    "head": _font("DejaVuSans-Bold.ttf", 66), "big": _font("DejaVuSans-Bold.ttf", 150),
    "body": _font("DejaVuSans.ttf", 50), "bodyb": _font("DejaVuSans-Bold.ttf", 52),
    "cap": _font("DejaVuSans-Bold.ttf", 64), "code": _font("DejaVuSansMono.ttf", 40),
    "small": _font("DejaVuSans.ttf", 34), "q": _font("DejaVuSans-Bold.ttf", 78),
}


def ease(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def wrap(draw, text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


class Background:
    def __init__(self):
        base = Image.new("RGB", (W, H + 200))
        px = ImageDraw.Draw(base)
        for y in range(H + 200):
            k = y / (H + 200)
            px.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(BG_TOP, BG_BOTTOM)))
        for y in range(0, H + 200, 60):                       # subtle dot grid
            for x in range(30, W, 60):
                px.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(40, 48, 80))
        self.img = base

    def frame(self, t: float) -> Image.Image:
        off = int((t * 18) % 60)                                # slow drift
        return self.img.crop((0, off, W, off + H)).convert("RGBA")


# ------------------------------------------------------------------ visuals
def draw_image(layer: Image.Image, img: Image.Image, p: float, caption: str, accent,
               focus: list | None = None) -> None:
    """Cover-fit into the visual area with a slow Ken Burns zoom and rounded corners. With a
    detector `focus` box (normalised x0,y0,x1,y1) the frame drifts toward that object."""
    bx0, by0, bx1, by1 = MARGIN_L - 30, 440, W - MARGIN_R + 30, 1150
    bw, bh = bx1 - bx0, by1 - by0
    zoom = 1.0 + 0.08 * ease(p)
    scale = max(bw / img.width, bh / img.height) * zoom
    rs = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    ox, oy = (rs.width - bw) // 2, (rs.height - bh) // 2
    if focus:
        fx, fy = (focus[0] + focus[2]) / 2 * rs.width, (focus[1] + focus[3]) / 2 * rs.height
        k = ease(p)
        ox = int(ox + k * ((fx - bw / 2) - ox))
        oy = int(oy + k * ((fy - bh / 2) - oy))
        ox, oy = max(0, min(ox, rs.width - bw)), max(0, min(oy, rs.height - bh))
    crop = rs.crop((ox, oy, ox + bw, oy + bh))
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw, bh], 32, fill=int(255 * ease(p * 4)))
    layer.paste(crop, (bx0, by0), mask)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([bx0, by0, bx1, by1], 32, outline=accent, width=4)
    if caption:
        d.text((bx0 + 10, by1 + 16), caption[:60], font=F["small"], fill=MUTED)


def draw_subscribe(d: ImageDraw.ImageDraw, p: float, t: float) -> None:
    """Original end card: pulsing subscribe button + ringing bell (no third-party logos)."""
    cx = (W - MARGIN_R + MARGIN_L) / 2
    pressed = p > 0.55
    s = 1 + (0.06 * math.sin(t * 6) if not pressed else 0) + (0.25 * (1 - ease(p * 4)))
    bw, bh = 560 * s, 130 * s
    col = (204, 0, 0) if not pressed else (90, 96, 120)
    d.rounded_rectangle([cx - bw / 2, 600 - bh / 2, cx + bw / 2, 600 + bh / 2], int(bh / 2), fill=col)
    label = "SUBSCRIBE" if not pressed else "SUBSCRIBED ✓"
    lw = d.textlength(label, font=F["bodyb"])
    d.text((cx - lw / 2, 600 - 32), label, font=F["bodyb"], fill=WHITE)
    # bell
    ang = 0.35 * math.sin(t * 14) * (1 if pressed else 0.2)
    bx, by, r = cx, 830, 70
    pts = []
    for k in range(0, 181, 15):
        a = math.radians(180 + k)
        pts.append((bx + r * math.cos(a + ang), by + r * math.sin(a + ang)))
    pts += [(bx + r * 1.15, by + 60), (bx - r * 1.15, by + 60)]
    d.polygon(pts, fill=(251, 191, 36))
    d.ellipse([bx - 16, by + 58, bx + 16, by + 90], fill=(251, 191, 36))
    sub = "A new tech story every day"
    sw = d.textlength(sub, font=F["body"])
    d.text((cx - sw / 2, 960), sub, font=F["body"], fill=WHITE + (int(255 * ease(p * 3)),))


def draw_visual(d: ImageDraw.ImageDraw, v: dict, p: float, accent, t: float,
                layer: Image.Image | None = None, images: dict | None = None) -> None:
    kind = v["type"]
    if kind == "image":
        draw_image(layer, images[v["asset"]], p, v.get("caption", ""), accent, v.get("focus"))
        return
    if kind == "subscribe":
        draw_subscribe(d, p, t)
        return
    top, bottom, width = 440, 1180, W - MARGIN_L - MARGIN_R
    if kind == "title":
        lines = wrap(d, v["headline"], F["q"], width)
        y = 640 - len(lines) * 50
        for i, line in enumerate(lines):
            a = ease(p * 3 - i * 0.3)
            d.text((MARGIN_L, y + (1 - a) * 60), line, font=F["q"], fill=WHITE + (int(255 * a),))
            y += 100
        if v.get("kicker"):
            d.text((MARGIN_L, y + 30), v["kicker"], font=F["body"], fill=accent + (int(255 * ease(p * 2 - .5)),))
        d.rectangle([MARGIN_L, y + 120, MARGIN_L + int(width * ease(p * 2)), y + 132], fill=accent)
    elif kind == "bullets":
        items, y = v["items"][:4], top + 40
        for i, item in enumerate(items):
            a = ease(p * (len(items) + 1) - i)
            x = MARGIN_L + (1 - a) * 80
            d.ellipse([x, y + 18, x + 28, y + 46], fill=accent + (int(255 * a),))
            for j, line in enumerate(wrap(d, item, F["bodyb"], width - 60)[:2]):
                d.text((x + 55, y + j * 62), line, font=F["bodyb"], fill=WHITE + (int(255 * a),))
            y += 170
    elif kind == "code":
        lines = v["code"].splitlines()[:12]
        box = [MARGIN_L - 20, top, W - MARGIN_R + 20, top + 80 + 58 * len(lines)]
        d.rounded_rectangle(box, 24, fill=(6, 8, 20, 235), outline=accent, width=3)
        for k, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([box[0] + 26 + k * 34, box[1] + 22, box[0] + 46 + k * 34, box[1] + 42], fill=col)
        total = sum(len(l) for l in lines) or 1
        shown = int(ease(p * 1.6) * total)
        y = top + 60
        for line in lines:
            vis, shown = line[:max(shown, 0)], shown - len(line)
            x = box[0] + 30
            for tok in re.findall(r"\s+|\w+|[^\w\s]", vis):
                kw = tok in {"def", "return", "class", "import", "from", "for", "in", "if", "else",
                             "while", "async", "await", "const", "let", "function", "True", "False", "None"}
                colr = accent if kw else ((251, 191, 36) if tok.isdigit() else WHITE)
                d.text((x, y), tok, font=F["code"], fill=colr)
                x += d.textlength(tok, font=F["code"])
            y += 58
    elif kind == "diagram":
        nodes = v["nodes"][:5]
        gap = min(170, (bottom - top - 40) // max(len(nodes), 1))
        for i, n in enumerate(nodes):
            a = ease(p * (len(nodes) + 1) - i)
            y = top + 20 + i * gap
            d.rounded_rectangle([MARGIN_L + 60, y, W - MARGIN_R - 60, y + 110], 26,
                                fill=(30, 38, 72, int(240 * a)), outline=ACCENTS[i % 4] + (int(255 * a),), width=4)
            label = wrap(d, n, F["bodyb"], W - MARGIN_L - MARGIN_R - 180)[0]
            tw = d.textlength(label, font=F["bodyb"])
            d.text(((W - MARGIN_R + MARGIN_L - tw) / 2, y + 26), label, font=F["bodyb"], fill=WHITE + (int(255 * a),))
            if i < len(nodes) - 1 and a > 0.6:
                cx = (W - MARGIN_R + MARGIN_L) / 2
                d.line([cx, y + 112, cx, y + gap - 6], fill=accent, width=6)
                d.polygon([(cx - 16, y + gap - 24), (cx + 16, y + gap - 24), (cx, y + gap - 4)], fill=accent)
    elif kind == "stat":
        m = re.match(r"^([^\d]*)([\d.,]+)(.*)$", str(v["value"]))
        txt = str(v["value"])
        if m:
            num = float(m.group(2).replace(",", ""))
            cur = num * ease(p * 1.5)
            dec = len(m.group(2).split(".")[1]) if "." in m.group(2) else 0
            txt = f"{m.group(1)}{cur:,.{dec}f}{m.group(3)}"
        tw = d.textlength(txt, font=F["big"])
        d.text(((W - tw) / 2, 560), txt, font=F["big"], fill=accent)
        for i, line in enumerate(wrap(d, v["label"], F["body"], width)[:3]):
            lw = d.textlength(line, font=F["body"])
            d.text(((W - lw) / 2, 780 + i * 66), line, font=F["body"], fill=WHITE + (int(255 * ease(p * 2)),))
    elif kind == "comparison":
        colw = (width - 40) // 2
        for side, (key, col) in enumerate((("left", ACCENTS[2]), ("right", ACCENTS[0]))):
            a = ease(p * 2.5 - side * 0.5)
            x = MARGIN_L + side * (colw + 40)
            d.rounded_rectangle([x, top, x + colw, top + 640], 28, fill=(28, 34, 66, int(230 * a)), outline=col, width=4)
            d.text((x + 28, top + 28), v[key]["title"][:16], font=F["bodyb"], fill=col)
            y = top + 120
            for item in v[key].get("items", [])[:4]:
                for line in wrap(d, item, F["small"], colw - 56)[:2]:
                    d.text((x + 28, y), line, font=F["small"], fill=WHITE + (int(255 * a),))
                    y += 44
                y += 26
    elif kind == "timeline":
        ev = v["events"][:5]
        d.line([MARGIN_L + 30, top + 20, MARGIN_L + 30, top + 20 + int(650 * ease(p * 1.5))], fill=accent, width=6)
        for i, e in enumerate(ev):
            a = ease(p * (len(ev) + 1) - i)
            y = top + 20 + i * 165
            d.ellipse([MARGIN_L + 14, y, MARGIN_L + 46, y + 32], fill=ACCENTS[i % 4] + (int(255 * a),))
            d.text((MARGIN_L + 80, y - 12), e["label"], font=F["bodyb"], fill=ACCENTS[i % 4] + (int(255 * a),))
            for j, line in enumerate(wrap(d, e.get("text", ""), F["small"], width - 100)[:2] if e.get("text") else []):
                d.text((MARGIN_L + 80, y + 48 + j * 42), line, font=F["small"], fill=WHITE + (int(255 * a),))
    elif kind == "question":
        pulse = 1 + 0.03 * math.sin(t * 4)
        lines = wrap(d, v["question"], F["q"], width)
        y = 760 - len(lines) * 55
        for line in lines:
            lw = d.textlength(line, font=F["q"])
            d.text(((W - lw) / 2, y), line, font=F["q"], fill=WHITE + (int(255 * ease(p * 3)),))
            y += 104
        r = int(70 * pulse)
        d.ellipse([W / 2 - r, y + 40, W / 2 + r, y + 40 + 2 * r], outline=accent, width=8)
        d.text((W / 2 - 22, y + 40 + r - 52), "?", font=F["big"].font_variant(size=96), fill=accent)


def draw_captions(d, cap: dict | None, t: float, accent) -> None:
    if not cap:
        return
    ws = cap["words"]
    widths = [d.textlength(w[0] + " ", font=F["cap"]) for w in ws]
    total = sum(widths)
    lines = [ws] if total <= W - MARGIN_L - MARGIN_R else [ws[: len(ws) // 2], ws[len(ws) // 2:]]
    y = 1300 - (len(lines) - 1) * 40
    for line in lines:
        lw = sum(d.textlength(w[0] + " ", font=F["cap"]) for w in line)
        x = (W - MARGIN_R + MARGIN_L - lw) / 2
        for w in line:
            col = accent if w[1] <= t < w[2] else WHITE
            d.text((x, y), w[0], font=F["cap"], fill=col, stroke_width=6, stroke_fill=(0, 0, 0))
            x += d.textlength(w[0] + " ", font=F["cap"])
        y += 84


# ------------------------------------------------------------------ main entry
def render(storyboard: dict, scene_audio: list[Path], workdir: Path, out_mp4: Path,
           preset: str = "veryfast", music: bool = True, sfx: bool = True,
           sfx_dir: Path | None = None, music_dir: Path | None = None, engine: str = "pillow") -> dict:
    """engine: pillow | hyperframes | auto (hyperframes, falling back to pillow)."""
    from .storyboard import normalize
    workdir.mkdir(parents=True, exist_ok=True)
    storyboard = normalize(storyboard)
    long_ = storyboard.get("format") == "long"
    # Pillow cannot shape Tamil script and has no 16:9 layout: those need HyperFrames
    hf_only = long_ or storyboard.get("language", "en") != "en" or any(
        s["visual"].get("type") in ("stickman", "three") for s in storyboard["scenes"])
    if hf_only and engine == "pillow":
        raise RuntimeError("long-form and Tamil videos need the HyperFrames engine")
    scenes = storyboard["scenes"]
    images = {}
    for s in scenes:
        if s["visual"]["type"] == "image":
            name = Path(s["visual"]["asset"]).name                 # no path traversal
            images[s["visual"]["asset"]] = Image.open(workdir / "assets" / name).convert("RGB")
    # channel branding (logo badge + handle), same look in both engines
    from . import brand as brandmod
    logo_src, handle = brandmod.logo_path(), brandmod.handle()
    logo_img = None
    if logo_src:
        (workdir / "assets").mkdir(exist_ok=True)
        shutil.copy(logo_src, workdir / "assets" / brandmod.LOGO_NAME)
        logo_img = Image.open(logo_src).convert("RGBA").resize((LOGO_PX, LOGO_PX), Image.LANCZOS)
    series = storyboard.get("series_label") or "TECH IN 60s"
    top_label = f"{series.upper()}  ·  {handle}" if handle else series.upper()
    raw_wav, norm_wav = workdir / "narration_raw.wav", workdir / "narration.wav"
    timings = audio.build_timeline(scene_audio, raw_wav)
    seed = sum(storyboard.get("video_id", "x").encode())
    attributions = sound.mix(raw_wav, norm_wav, timings[-1]["end"], scenes, timings, workdir, seed,
                             music=music, sfx=sfx, sfx_dir=sfx_dir, music_dir=music_dir,
                             theme=storyboard.get("theme"))
    (workdir / "attributions.json").write_text(json.dumps(attributions, indent=1))
    caps = captions.build(scenes, timings)
    (workdir / "captions.srt").write_text(captions.to_srt(caps))
    duration = timings[-1]["end"]
    n_frames = int(math.ceil(duration * FPS))
    if engine in ("auto", "hyperframes"):
        from . import hyperframes as hfr
        try:
            proj = hfr.build(storyboard, timings, caps, workdir, top_label,
                             logo=f"assets/{brandmod.LOGO_NAME}" if logo_img else None)
            hfr.render(proj, norm_wav, out_mp4, n_frames / FPS,
                       timeout=7200 if long_ else 1800, crf=20 if long_ else 18)
            report = qc(out_mp4, timings, long_=long_)
            report |= {"attributions": attributions, "engine": "hyperframes"}
            if report["ok"]:
                (workdir / "timings.json").write_text(json.dumps({"timings": timings, "qc": report}, indent=1))
                return report
            fallback_reason = f"hyperframes QC failed: {report['checks']}"
        except Exception as ex:  # noqa: BLE001 - fall back to the Pillow renderer
            fallback_reason = f"hyperframes: {str(ex)[:300]}"
        if engine == "hyperframes" or hf_only:     # Pillow: vertical English only
            raise RuntimeError(fallback_reason)
    else:
        fallback_reason = ""

    bg = Background()
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(norm_wav),
           "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", preset, "-crf", "20",
           "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", "60",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
           "-movflags", "+faststart", "-t", f"{n_frames / FPS:.3f}", str(out_mp4)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    ci = 0
    try:
        for f in range(n_frames):
            t = f / FPS
            si = next((i for i, tm in enumerate(timings) if t < tm["end"]), len(timings) - 1)
            s, tm = scenes[si], timings[si]
            accent = ACCENTS[si % len(ACCENTS)]
            p = (t - tm["start"]) / max(tm["end"] - tm["start"], 0.01)
            frame = bg.frame(t)
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            # top bar: progress + series label
            d.rectangle([0, 150, int(W * t / duration), 158], fill=accent)
            lx = MARGIN_L
            if logo_img:
                pop = ease(min(t / 0.5, 1.0))
                if pop > 0.05:
                    sz = max(int(LOGO_PX * pop), 1)
                    layer.alpha_composite(logo_img.resize((sz, sz)), (MARGIN_L + (LOGO_PX - sz) // 2, 58 + (LOGO_PX - sz) // 2))
                lx += LOGO_PX + 22
            d.text((lx, 84), top_label, font=F["small"], fill=MUTED)
            n_sc = len(scenes)                       # chapter dashes (top right)
            for k in range(n_sc):
                x0 = W - 60 - (n_sc - k) * 42
                d.rounded_rectangle([x0, 174, x0 + 34, 182], 3,
                                    fill=(ACCENTS[si % len(ACCENTS)] if k <= si else (70, 78, 110)))
            head_a = ease(p * 6)
            for j, line in enumerate(wrap(d, s.get("on_screen_text", ""), F["head"], W - MARGIN_L - MARGIN_R)[:2]):
                d.text((MARGIN_L, 240 + j * 80 + (1 - head_a) * 30), line, font=F["head"],
                       fill=accent + (int(255 * head_a),))
            draw_visual(d, s["visual"], p, accent, t, layer, images)
            while ci < len(caps) - 1 and t >= caps[ci]["end"]:
                ci += 1
            cap = caps[ci] if caps and caps[ci]["start"] - 0.05 <= t < caps[ci]["end"] + 0.15 else None
            draw_captions(d, cap, t, accent)
            frame.alpha_composite(layer)
            # transitions: short fade in from the background at scene start
            trans = s.get("transition", "fade")
            k = (t - tm["start"]) * FPS
            if si > 0 and trans != "cut" and k < 8:
                frame = Image.blend(bg.frame(t), frame, k / 8)
                if trans == "slide":
                    shift = int((1 - k / 8) * 120)
                    frame = frame.transform(frame.size, Image.AFFINE, (1, 0, -shift, 0, 1, 0))
            proc.stdin.write(frame.convert("RGB").tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {err[:500]}")
    except BrokenPipeError:
        raise RuntimeError(f"ffmpeg exited early: {proc.stderr.read().decode()[:500]}")
    report = qc(out_mp4, timings)
    report["attributions"] = attributions
    report["engine"] = "pillow"
    if fallback_reason:
        report["fallback_reason"] = fallback_reason
    (workdir / "timings.json").write_text(json.dumps({"timings": timings, "qc": report}, indent=1))
    return report


def qc(path: Path, timings: list[dict] | None = None, long_: bool = False) -> dict:
    info = probe(path)
    a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    v = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    ad, vd = float(a.stdout.strip() or 0), float(v.stdout.strip() or 0)
    lufs = loudness_lufs(path)
    checks = {
        ("resolution_1920x1080" if long_ else "resolution_1080x1920"):
            (info.width, info.height) == ((1920, 1080) if long_ else (W, H)),
        "fps_30": abs(info.fps - FPS) < 0.01,
        "h264": info.vcodec == "h264",
        "yuv420p": info.pix_fmt == "yuv420p",
        "aac": info.acodec == "aac",
        "audio_48k": info.sample_rate == 48000,
        "av_sync_within_100ms": abs(ad - vd) <= 0.1,
        ("duration_le_900s" if long_ else "duration_le_180s"): info.duration <= (900 if long_ else 180),
        "loudness_-14_pm2": lufs is not None and abs(lufs + 14) <= 2,
    }
    return {"ok": all(checks.values()), "checks": checks, "duration": round(info.duration, 2),
            "audio_duration": ad, "video_duration": vd, "lufs": lufs,
            "scenes": len(timings or []), "size_bytes": info.size_bytes}
