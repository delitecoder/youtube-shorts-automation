"""Stickman scenes: a code-drawn, hand-drawn-looking stick figure that ACTS the narration.

Style after Stickman Video Director's "B1 Minimal Line Art" (MIT, github.com/kaomei/
stickman-video-director): slender figure ~4.3 heads tall, fine ink lines with a slight hand-drawn
"boil", exaggerated faces, whole-body acting, big short handwritten text, a few coloured props.
Acting after HyperFrames' character guide: keyed poses the rig eases between, anticipation before
a move, the head leads and the arms follow (never in unison), the reaction is held.
Everything is SVG + GSAP generated here: no image generation, no external assets, deterministic.

Storyboard visual:
  {"type": "stickman", "text": "1 server. 1M users.",          # big handwritten words (≤ 4–6)
   "actions": [{"at": 0.0, "pose": "stand", "face": "neutral"},  # at = fraction of the scene (0–1)
               {"at": 0.3, "pose": "shocked", "face": "surprised"},
               {"at": 0.7, "pose": "point", "face": "happy"}],
   "prop": "server", "side": "left", "walk_in": true}
"""
from __future__ import annotations

import html
import json

# joint angles in degrees: torso, head, l_up, l_fore, r_up, r_fore, l_thigh, l_shin, r_thigh, r_shin
# arms hang at 0; positive = rotate clockwise (towards the viewer's left for the figure's right arm)
POSES = {
    "stand":     (0, 0, 12, 4, -12, -4, 6, 0, -6, 0),
    "wave":      (0, -4, 12, 4, -150, -35, 6, 0, -6, 0),
    "point":     (-4, -6, 14, 6, -95, -5, 8, 0, -8, 0),
    "point_up":  (-3, -10, 12, 4, -165, -10, 6, 0, -6, 0),
    "think":     (3, 8, 15, 5, -40, -135, 6, 0, -6, 0),
    "shrug":     (0, 6, 70, -110, -70, 110, 7, 0, -7, 0),
    "celebrate": (0, -8, 150, 20, -150, -20, 18, -10, -18, 10),
    "shocked":   (-10, -12, 55, 40, -55, -40, 14, 0, -14, 0),
    "explain":   (-3, -4, 40, -60, -45, 60, 7, 0, -7, 0),
    "sad":       (12, 22, 4, 0, -4, 0, 4, 0, -4, 0),
    "type":      (6, 10, 60, -70, -60, 70, 6, 0, -6, 0),
    "run1":      (-12, -6, -45, -70, 40, 60, 40, -70, -30, 10),
    "run2":      (-12, -6, 40, 60, -45, -70, -30, 10, 40, -70),
    "facepalm":  (6, 14, 14, 4, -130, -150, 6, 0, -6, 0),
}
FACES = ("neutral", "happy", "surprised", "worried", "smirk", "sad")
# brow (left, right) rotations and eye scale per face
FACE_PARAMS = {"neutral": (0, 0, 1.0), "happy": (-8, 8, 1.0), "surprised": (-18, 18, 1.35),
               "worried": (18, -18, 1.1), "smirk": (-14, 4, 0.9), "sad": (22, -22, 0.9)}
MOUTHS = {"neutral": "M-11 0 L11 0", "happy": "M-14 -3 Q0 14 14 -3", "surprised": "M-7 0 a7 9 0 1 0 14 0 a7 9 0 1 0 -14 0",
          "worried": "M-12 5 Q0 -6 12 5", "smirk": "M-12 2 Q4 8 14 -5", "sad": "M-13 8 Q0 -6 13 8"}
JOINTS = ("torso", "head", "lup", "lfo", "rup", "rfo", "lth", "lsh", "rth", "rsh")
# each joint group is placed at its pivot in its parent's coordinates and rotates around it
PIVOT = {"torso": (0, 0), "head": (0, -170), "lup": (-2, -150), "rup": (2, -150), "lfo": (0, 78), "rfo": (0, 78),
         "lth": (-4, 0), "rth": (4, 0), "lsh": (0, 95), "rsh": (0, 95)}


def _tf(j: str, ang: float) -> str:
    x, y = PIVOT[j]
    return f"translate({x},{y}) rotate({ang:g})"
INK = "#1d2433"
PROP_SVG = {   # small hand-drawn-style props, drawn at the figure's right hand (0,0 = hand)
    "server": '<g stroke="{ink}" stroke-width="4" fill="#ffe7a3"><rect x="10" y="-70" width="70" height="90" rx="6"/>'
              '<line x1="20" y1="-45" x2="70" y2="-45"/><line x1="20" y1="-20" x2="70" y2="-20"/>'
              '<circle cx="66" cy="5" r="4" fill="#3bb273"/></g>',
    "phone": '<g stroke="{ink}" stroke-width="4" fill="#cfe8ff"><rect x="6" y="-62" width="42" height="74" rx="8"/>'
             '<line x1="20" y1="4" x2="34" y2="4"/></g>',
    "laptop": '<g stroke="{ink}" stroke-width="4" fill="#cfe8ff"><rect x="8" y="-58" width="86" height="54" rx="5"/>'
              '<path d="M0 -2 L102 -2 L94 10 L8 10 Z" fill="#e9eef5"/></g>',
    "lightbulb": '<g stroke="{ink}" stroke-width="4" fill="#ffd84d"><circle cx="30" cy="-48" r="26"/>'
                 '<rect x="20" y="-24" width="20" height="16" fill="#e9eef5"/></g>',
    "coin": '<g stroke="{ink}" stroke-width="4" fill="#ffcf3f"><circle cx="30" cy="-30" r="28"/>'
            '<text x="30" y="-19" font-size="34" text-anchor="middle" fill="{ink}" stroke="none" font-family="Caveat">$</text></g>',
    "chart": '<g stroke="{ink}" stroke-width="4" fill="none"><path d="M8 -6 L8 -80 M8 -6 L96 -6"/>'
             '<path d="M16 -18 L40 -40 L60 -30 L90 -72" stroke="#e5484d"/></g>',
    "filmbox": '<g stroke="{ink}" stroke-width="4"><rect x="8" y="-96" width="80" height="100" rx="8" fill="#e5484d"/>'
               '<rect x="18" y="-80" width="60" height="40" fill="#1d2433"/><path d="M38 -72 l18 12 l-18 12 Z" fill="#fff" stroke="none"/>'
               '<line x1="18" y1="-24" x2="78" y2="-24"/><line x1="18" y1="-10" x2="78" y2="-10"/></g>',
    "question": '<text x="18" y="-20" font-size="96" fill="#e5484d" font-family="Caveat" font-weight="700">?</text>',
}


def e(s) -> str:
    return html.escape(str(s), quote=True)


def _figure(sid: str, ink: str = INK, face_fill: str = "#fffdf8") -> str:
    """The rig. Group ids: {sid}-torso … each rotates around its own joint."""
    s = f'stroke="{ink}" stroke-width="6" stroke-linecap="round" fill="none"'
    brow = lambda k, x: (f'<g id="{sid}-brow{k}" transform="translate({x},-16) rotate(0)">'  # noqa: E731
                         f'<line x1="-9" y1="0" x2="9" y2="0" {s} stroke-width="5"/></g>')
    mouths = "".join(f'<path id="{sid}-m-{k}" d="{d}" {s} stroke-width="5" opacity="{1 if k == "neutral" else 0}"/>'
                     for k, d in MOUTHS.items())
    leg = lambda k: (f'<g id="{sid}-{k}th" transform="{_tf(k + "th", 0)}"><line x1="0" y1="0" x2="0" y2="95" {s}/>'  # noqa: E731
                     f'<g id="{sid}-{k}sh" transform="{_tf(k + "sh", 0)}"><line x1="0" y1="0" x2="0" y2="92" {s}/>'
                     f'<ellipse cx="{-10 if k == "l" else 10}" cy="94" rx="15" ry="7" fill="{ink}"/></g></g>')
    arm = lambda k: (f'<g id="{sid}-{k}up" transform="{_tf(k + "up", 0)}"><line x1="0" y1="0" x2="0" y2="78" {s}/>'  # noqa: E731
                     f'<g id="{sid}-{k}fo" transform="{_tf(k + "fo", 0)}"><line x1="0" y1="0" x2="0" y2="72" {s}/>'
                     f'<circle cx="0" cy="76" r="9" fill="{ink}"/>'
                     + (f'<g id="{sid}-hprop" transform="translate(0,76) scale(1)"></g>' if k == "r" else "") + '</g></g>')
    return (f'<svg class="stick-svg" viewBox="-200 -330 400 640" width="400" height="640" overflow="visible">'
            f'<defs><filter id="{sid}-boil"><feTurbulence id="{sid}-turb" type="fractalNoise" baseFrequency="0.035" '
            f'numOctaves="1" seed="1"/><feDisplacementMap in="SourceGraphic" scale="3.2"/></filter></defs>'
            f'<g id="{sid}-fig" filter="url(#{sid}-boil)">'
            f'<g id="{sid}-gprop" transform="translate(58,60) scale(1.5)"></g>'
            f'<g id="{sid}-legs">{leg("l")}{leg("r")}</g>'
            f'<g id="{sid}-torso" transform="{_tf("torso", 0)}"><line x1="0" y1="0" x2="0" y2="-170" {s}/>'
            f'{arm("l")}{arm("r")}'
            f'<g id="{sid}-head" transform="{_tf("head", 0)}"><g transform="translate(0,-48)">'
            f'<circle cx="0" cy="0" r="42" {s} fill="{face_fill}"/>'
            f'<g id="{sid}-eyes"><ellipse id="{sid}-eyeL" cx="-14" cy="-4" rx="5" ry="7" fill="{ink}"/>'
            f'<ellipse id="{sid}-eyeR" cx="14" cy="-4" rx="5" ry="7" fill="{ink}"/></g>'
            f'{brow("L", -14)}{brow("R", 14)}<g transform="translate(0,18)">{mouths}</g></g></g>'
            f'</g></g></svg>')


def _pose_js(sid: str, pose: str, t: float, prev: str | None) -> list[str]:
    a = POSES.get(pose, POSES["stand"])
    out = []
    if prev and prev != pose and not pose.startswith("run"):
        # anticipation: a small dip of the torso the opposite way before the move
        pa = POSES.get(prev, POSES["stand"])
        out.append(f'tl.to("#{sid}-torso",{{attr:{{transform:"{_tf("torso", pa[0] + (3 if a[0] <= pa[0] else -3))}"}},'
                   f'duration:0.1,ease:"power1.in"}},{t:.3f});')
        t += 0.1
    for k, (j, ang) in enumerate(zip(JOINTS, a)):
        lead = 0.0 if j in ("head", "torso") else 0.06 + 0.025 * (k % 3)   # head leads, limbs follow, offset
        ease = "back.out(1.3)" if j in ("lup", "rup", "lfo", "rfo") else "expo.out"
        dur = 0.18 if pose.startswith("run") else 0.42
        out.append(f'tl.to("#{sid}-{j}",{{attr:{{transform:"{_tf(j, ang)}"}},duration:{dur},ease:"{ease}"}},{t + lead:.3f});')
    return out


def _face_js(sid: str, face: str, t: float) -> list[str]:
    bl, br, eye = FACE_PARAMS.get(face, FACE_PARAMS["neutral"])
    up = -20 if face == "surprised" else -16
    out = [f'tl.to("#{sid}-browL",{{attr:{{transform:"translate(-14,{up}) rotate({bl})"}},duration:0.2}},{t:.3f});',
           f'tl.to("#{sid}-browR",{{attr:{{transform:"translate(14,{up}) rotate({br})"}},duration:0.2}},{t:.3f});',
           f'tl.to("#{sid}-eyeL",{{attr:{{ry:{7 * eye:.1f},rx:{5 * eye:.1f}}},duration:0.2}},{t:.3f});',
           f'tl.to("#{sid}-eyeR",{{attr:{{ry:{7 * eye:.1f},rx:{5 * eye:.1f}}},duration:0.2}},{t:.3f});']
    for k in MOUTHS:
        out.append(f'tl.to("#{sid}-m-{k}",{{opacity:{1 if k == face else 0},duration:0.08}},{t + 0.05:.3f});')
    return out


def visual(i: int, v: dict, t0: float, dur: float, accent: str, long_: bool = False,
           dark: bool = False) -> tuple[str, list[str]]:
    """dark=True: chalkboard look (white ink on the video's dark palette)."""
    sid = f"s{i}st"
    ink, face_fill = ("#f4f1ea", "#151a2c") if dark else (INK, "#fffdf8")
    side = v.get("side", "left")
    text = str(v.get("text", "")).strip()
    prop = v.get("prop")
    fig_x = "4%" if side == "left" else "52%"
    txt_x = "52%" if side == "left" else "2%"
    html_ = (f'<div class="stick" id="{sid}"><div class="stick-fig" id="{sid}-wrap" style="left:{fig_x}">{_figure(sid, ink, face_fill)}</div>'
             + (f'<div class="stick-text" id="{sid}-txt" style="left:{txt_x};color:{ink}">{e(text)}'
                f'<svg class="stick-under" viewBox="0 0 300 20" preserveAspectRatio="none"><path id="{sid}-ul" '
                f'd="M4 12 C80 4 160 18 296 8" stroke="{accent}" stroke-width="7" fill="none" stroke-linecap="round" stroke-dasharray="400 400"/></svg></div>'
                if text else "") + '</div>')
    js = []
    # line boil: redraw the ink ~12 times a second, deterministic per frame
    js.append(f'for(let k=0;k<{max(1, int(dur * 12))};k++){{tl.set("#{sid}-turb",{{attr:{{seed:(k%5)+1}}}},{t0:.3f}+k/12);}}')
    actions = sorted(v.get("actions") or [{"at": 0, "pose": "stand", "face": "neutral"}], key=lambda x: x.get("at", 0))
    first = actions[0]
    a0 = POSES.get(first.get("pose", "stand"), POSES["stand"])
    for j, ang in zip(JOINTS, a0):          # start pose set instantly (no tween from zero)
        js.append(f'tl.set("#{sid}-{j}",{{attr:{{transform:"{_tf(j, ang)}"}}}},{t0:.3f});')
    js += _face_js(sid, first.get("face", "neutral"), t0)
    if v.get("walk_in"):
        js.append(f'tl.from("#{sid}-wrap",{{x:{-500 if side == "left" else 500},duration:0.9,ease:"power2.out"}},{t0:.3f});')
        for k in range(4):
            js += _pose_js(sid, "run1" if k % 2 == 0 else "run2", t0 + k * 0.2, None)
        js += _pose_js(sid, first.get("pose", "stand"), t0 + 0.85, "run2")
    else:
        js.append(f'tl.from("#{sid}-wrap",{{opacity:0,y:30,duration:0.35,ease:"expo.out"}},{t0:.3f});')
    prev = first.get("pose", "stand")
    for act in actions[1:]:
        t = t0 + max(0.0, min(0.95, float(act.get("at", 0)))) * dur
        js += _pose_js(sid, act.get("pose", "stand"), t, prev)
        js += _face_js(sid, act.get("face", "neutral"), t + 0.05)
        prev = act.get("pose", "stand")
    if prop in PROP_SVG:
        # a phone is held; bigger props stand beside the figure so a hand near the face never hides it
        held = prop == "phone"
        gid, base = (f"{sid}-hprop", "translate(0,76)") if held else (f"{sid}-gprop", "translate(58,60)")
        js.append(f'(function(){{const g=document.getElementById("{gid}");if(g){{g.innerHTML={json.dumps(PROP_SVG[prop].format(ink=ink))};}}}})();')
        js.append(f'tl.from("#{gid}",{{attr:{{transform:"{base} scale(0.01)"}},duration:0.35,ease:"back.out(2)"}},{t0 + 0.4:.3f});')
    if text:
        js.append(f'tl.from("#{sid}-txt",{{opacity:0,filter:"blur(6px)",duration:0.25,ease:"power2.out"}},{t0 + 0.35:.3f});')
        js.append(f'tl.from("#{sid}-ul",{{attr:{{"stroke-dasharray":"0 400"}},duration:0.5,ease:"power2.out"}},{t0 + 0.6:.3f});')
    return html_, js


CSS = """
.stick{position:absolute;inset:0}
.stick-fig{position:absolute;top:-40px;width:470px;height:760px}
.stick-svg{width:100%;height:100%}
.stick-text{position:absolute;top:150px;width:48%;font-family:'Caveat','Noto Sans Tamil',cursive;font-weight:700;
 font-size:96px;line-height:1.0;color:#1d2433}
.stick-under{display:block;width:90%;height:22px;margin-top:6px}
"""
CSS_LONG = """
.stick-fig{top:-60px;width:440px;height:704px}
.stick-text{top:140px;font-size:110px}
"""

# Light "paper" look used with stickman videos (palette "paper")
PAPER_CSS = """
#stage{background:#FDFDFC !important}
.grid{background-image:linear-gradient(rgba(76,90,105,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(76,90,105,.05) 1px,transparent 1px) !important;
 background-size:64px 64px !important;opacity:1 !important}
.top{color:#5b6478}
.head{font-family:'Caveat','Noto Sans Tamil',cursive;font-size:84px}
.vis,.title h1,.question h1,.kicker,.bullets li,.label,.card li,.ev p,.sub p{color:#1d2433 !important}
.card,.node{background:#fffaf0 !important}
.cap{color:#1d2433;text-shadow:0 0 6px #fff,0 0 12px #fff,3px 3px 0 #fff}
.chapters i{background:#d9dde6}
"""
