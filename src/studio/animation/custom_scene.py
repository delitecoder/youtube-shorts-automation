"""Custom scenes: the "camera man" (Sonnet scene-coder agent) writes HTML + GSAP for one scene,
following the director's (Opus) shot brief. This module checks that code before it is rendered.

Files (per video): runtime/work/<VIDEO_ID>/scenes/<name>.html and <name>.js
  * html: a fragment placed inside the 1080x1920 scene layer (no <script>, no <iframe>).
  * js:   GSAP statements on the shared paused timeline `tl`, using the injected constants
          T0 (scene start, s), DUR (scene length, s) and P (id prefix, e.g. "s3-").
Rules (checked here, violations -> the scene falls back to a title card and the director is told):
  * every id in the html starts with P (so scenes cannot collide);
  * no network: no http(s) URLs, fetch, XMLHttpRequest, WebSocket, import, eval, Function();
  * only local images from assets/ ;
  * all tweens placed on `tl` at T0-relative times (seekable, deterministic);
  * size limits keep scenes small.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_HTML, MAX_JS = 20_000, 20_000
FORBIDDEN_JS = re.compile(r"\b(fetch|XMLHttpRequest|WebSocket|import|eval|Function|setTimeout|setInterval|"
                          r"requestAnimationFrame|localStorage|document\.cookie|navigator|window\.open)\b")
FORBIDDEN_HTML = re.compile(r"<\s*(script|iframe|object|embed|link|meta|base|form)\b", re.I)
URL = re.compile(r"(https?:)?//[a-z0-9.-]+\.[a-z]{2,}", re.I)


class SceneRejected(ValueError):
    pass


def check(html: str, js: str, prefix: str) -> list[str]:
    errs = []
    if len(html) > MAX_HTML or len(js) > MAX_JS:
        errs.append("scene code too large")
    if FORBIDDEN_HTML.search(html):
        errs.append("html may not contain script/iframe/object/embed/link/meta/base/form tags")
    if re.search(r"\son\w+\s*=", html, re.I):
        errs.append("html may not use inline event handlers (onclick=…)")
    if URL.search(html) or URL.search(js):
        errs.append("no external URLs (only assets/… files)")
    for src in re.findall(r"""src\s*=\s*["']([^"']+)""", html):
        if not re.fullmatch(r"assets/[\w.-]+\.(png|jpe?g|webp|svg)", src):
            errs.append(f"image src must be assets/<file>: {src}")
    for i in re.findall(r"""\sid\s*=\s*["']([^"']+)""", html):
        if not i.startswith(prefix):
            errs.append(f"id '{i}' must start with '{prefix}'")
    if FORBIDDEN_JS.search(js):
        errs.append(f"js uses a forbidden API: {FORBIDDEN_JS.search(js).group(0)}")
    if "tl." not in js:
        errs.append("js must animate on the shared timeline `tl`")
    if re.search(r"\btl\s*=", js) or re.search(r"gsap\.timeline\(", js):
        errs.append("js must not create or replace the timeline")
    return errs


def load(workdir: Path, name: str, prefix: str) -> tuple[str, str]:
    if not re.fullmatch(r"[\w-]{1,40}", name):
        raise SceneRejected(f"bad scene name {name!r}")
    base = workdir / "scenes"
    html_p, js_p = base / f"{name}.html", base / f"{name}.js"
    if not html_p.exists() or not js_p.exists():
        raise SceneRejected(f"missing scenes/{name}.html or .js")
    html, js = html_p.read_text(encoding="utf-8"), js_p.read_text(encoding="utf-8")
    errs = check(html, js, prefix)
    if errs:
        raise SceneRejected("; ".join(errs))
    return html, js


def wrap_js(js: str, t0: float, dur: float, prefix: str) -> str:
    """Isolate the scene: constants injected, errors contained so one scene cannot break the film."""
    return (f"(function(T0,DUR,P){{try{{\n{js}\n}}catch(err){{console.error('scene '+P+' failed',err);}}}})"
            f"({t0:.3f},{dur:.3f},{prefix!r});")
