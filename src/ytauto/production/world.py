"""Story scenes: a consistent animated stickman WORLD (cast + locations + camera + speech).

Visual type "story" (full-frame):
  {"type": "story", "location": "office", "time": "day", "camera": "push",
   "sign": "CDN = copies nearby",                 # text written on the board/screen/banner of the place
   "cast": [{"who": "arjun", "x": 0.30, "walk_from": "left", "face": "right",
             "actions": [{"at": 0, "pose": "stand", "face": "worried"}, {"at": 0.5, "pose": "explain", "face": "happy"}],
             "say": "Why is it so slow?", "say_at": 0.1, "prop": "laptop"},
            {"who": "byte", "x": 0.72, "mood": [{"at": 0, "face": "overloaded"}, {"at": 0.6, "face": "happy"}]}]}

Cast (identical in every video): arjun (developer, glasses + blue hoodie), priya (curious friend,
ponytail + orange scarf), raju (shop owner, cap + moustache), byte (little server robot).
Locations: office, street, server_room, home, shop, classroom, network. One fixed flat-colour
world style; day/night lighting. Ambient motion always has a cause (traffic, clouds, a ticking
clock, blinking server LEDs, code being typed), never decorative loops.
Built on stickman.py's rig (keyed poses, anticipation, head leads, line boil). Deterministic SVG+GSAP.
"""
from __future__ import annotations

import html
import random

from . import stickman as stk

INK = "#1d2433"
# one world palette for every video (consistency); night variants darken the sky only
SKY = {"day": ("#bfe3ff", "#eaf6ff"), "night": ("#1b2550", "#3a3f78")}
WALL, FLOOR, WOOD, METAL = "#f6efe3", "#e2d6c3", "#c98b55", "#8e9bb0"
CAST = {
    "arjun": {"name": "Arjun", "torso": "#2f6fde", "acc": "glasses_hair"},
    "priya": {"name": "Priya", "torso": "#e5484d", "acc": "ponytail_scarf"},
    "raju": {"name": "Raju", "torso": "#3bb273", "acc": "cap_moustache"},
}
LOCATIONS = ("office", "street", "server_room", "home", "shop", "classroom", "network")


def e(s) -> str:
    return html.escape(str(s), quote=True)


# ------------------------------------------------------------------ cast accessories (head/torso coords)
def _accessories(kind: str) -> tuple[str, str]:
    """(head_svg in head-centre coords, neck_svg in torso coords where the neck is at y=-170)."""
    s = f'stroke="{INK}" stroke-width="5" stroke-linecap="round"'
    if kind == "glasses_hair":
        head = (f'<path d="M-40 -14 C-36 -52 34 -56 41 -16 L30 -34 L18 -26 L6 -38 L-6 -27 L-18 -38 L-28 -26 Z" fill="{INK}"/>'
                f'<circle cx="-14" cy="-3" r="12" fill="none" {s} stroke-width="4"/>'
                f'<circle cx="14" cy="-3" r="12" fill="none" {s} stroke-width="4"/>'
                f'<line x1="-2" y1="-4" x2="2" y2="-4" {s} stroke-width="4"/>')
        neck = f'<path d="M-22 -168 Q0 -150 22 -168" fill="none" stroke="#2f6fde" stroke-width="10" stroke-linecap="round"/>'
    elif kind == "ponytail_scarf":
        head = (f'<path d="M-42 -6 C-46 -50 44 -56 42 -8 C30 -30 -26 -34 -42 -6 Z" fill="{INK}"/>'
                f'<path d="M38 -24 C70 -20 72 22 52 40 C60 14 52 -6 36 -10 Z" fill="{INK}"/>'
                f'<circle cx="40" cy="-20" r="6" fill="#e5484d"/>')
        neck = (f'<path d="M-24 -166 Q0 -148 24 -166" fill="none" stroke="#f5a524" stroke-width="12" stroke-linecap="round"/>'
                f'<path d="M14 -160 L22 -128" stroke="#f5a524" stroke-width="10" stroke-linecap="round"/>')
    elif kind == "cap_moustache":
        head = (f'<path d="M-40 -16 C-38 -52 38 -52 40 -16 Z" fill="#3bb273" {s} stroke-width="4"/>'
                f'<path d="M30 -18 L62 -14" {s} stroke-width="7"/>'
                f'<path d="M-14 10 Q-6 4 0 9 Q6 4 14 10" fill="none" {s} stroke-width="6"/>')
        neck = f'<path d="M-6 -168 L0 -150 L6 -168" fill="#3bb273" {s} stroke-width="3"/>'
    else:
        head, neck = "", ""
    return head, neck


def _character(sid: str, who: str) -> str:
    """The stickman rig with this cast member's look (torso colour + accessories)."""
    look = CAST.get(who, CAST["arjun"])
    svg = stk._figure(sid, INK, "#fffdf8")
    head, neck = _accessories(look["acc"])
    # coloured torso stroke (hoodie/shirt) under the ink line, accessories on head and neck
    torso_old = (f'<g id="{sid}-torso" transform="{stk._tf("torso", 0)}"><line x1="0" y1="0" x2="0" y2="-170" '
                 f'stroke="{INK}" stroke-width="6" stroke-linecap="round" fill="none"/>')
    assert torso_old in svg and f'<g id="{sid}-eyes">' in svg, "stickman rig changed: update world._character"
    svg = svg.replace(torso_old,
                      f'<g id="{sid}-torso" transform="{stk._tf("torso", 0)}">'
                      f'<line x1="0" y1="-8" x2="0" y2="-160" stroke="{look["torso"]}" stroke-width="22" stroke-linecap="round"/>'
                      f'<line x1="0" y1="0" x2="0" y2="-170" stroke="{INK}" stroke-width="6" stroke-linecap="round" fill="none"/>'
                      f'{neck}', 1)
    svg = svg.replace(f'<g id="{sid}-eyes">', f'{head}<g id="{sid}-eyes">', 1)
    return svg


# ------------------------------------------------------------------ Byte, the server robot
BYTE_FACES = {"happy": ("#3bb273", "M-14 10 Q0 22 14 10"), "neutral": ("#2f6fde", "M-12 14 L12 14"),
              "overloaded": ("#e5484d", "M-14 18 Q0 6 14 18"), "sleep": ("#8e9bb0", "M-10 14 L10 14"),
              "surprised": ("#f5a524", "M-6 14 a6 7 0 1 0 12 0 a6 7 0 1 0 -12 0")}


def _byte(sid: str) -> str:
    faces = "".join(f'<path id="{sid}-bm-{k}" d="{d}" stroke="{c}" stroke-width="5" fill="none" stroke-linecap="round" '
                    f'opacity="{1 if k == "neutral" else 0}"/>' for k, (c, d) in BYTE_FACES.items())
    eyes = "".join(f'<ellipse id="{sid}-be{k}" cx="{x}" cy="-8" rx="7" ry="9" fill="#2f6fde"/>'
                   for k, x in (("L", -16), ("R", 16)))
    return (f'<svg class="byte-svg" viewBox="-110 -170 220 260" overflow="visible">'
            f'<g id="{sid}-body"><rect x="-70" y="-150" width="140" height="190" rx="18" fill="#dfe6f2" stroke="{INK}" stroke-width="6"/>'
            f'<rect x="-50" y="-128" width="100" height="70" rx="10" fill="#0f1830" stroke="{INK}" stroke-width="4"/>'
            f'<g transform="translate(0,-92)">{eyes}{faces}</g>'
            + "".join(f'<rect x="-50" y="{-40 + k * 22}" width="70" height="10" rx="4" fill="#b8c3d6"/>'
                      f'<circle id="{sid}-led{k}" cx="38" cy="{-35 + k * 22}" r="6" fill="#3bb273"/>' for k in range(3))
            + f'<g id="{sid}-sweat" opacity="0"><path d="M74 -120 q8 14 0 22 q-8 -8 0 -22 Z" fill="#7fd4ff" stroke="{INK}" stroke-width="3"/>'
            f'<path d="M-80 -100 q8 14 0 22 q-8 -8 0 -22 Z" fill="#7fd4ff" stroke="{INK}" stroke-width="3"/></g>'
            f'<line x1="-36" y1="40" x2="-36" y2="78" stroke="{INK}" stroke-width="7" stroke-linecap="round"/>'
            f'<line x1="36" y1="40" x2="36" y2="78" stroke="{INK}" stroke-width="7" stroke-linecap="round"/>'
            f'<ellipse cx="-42" cy="82" rx="16" ry="7" fill="{INK}"/><ellipse cx="42" cy="82" rx="16" ry="7" fill="{INK}"/></g></svg>')


def _byte_js(sid: str, moods: list[dict], t0: float, dur: float) -> list[str]:
    js = []
    for m in sorted(moods or [{"at": 0, "face": "neutral"}], key=lambda x: x.get("at", 0)):
        t = t0 + max(0.0, min(0.95, float(m.get("at", 0)))) * dur
        f = m.get("face", "neutral")
        col = BYTE_FACES.get(f, BYTE_FACES["neutral"])[0]
        for k in BYTE_FACES:
            js.append(f'tl.to("#{sid}-bm-{k}",{{opacity:{1 if k == f else 0},duration:0.08}},{t:.3f});')
        js += [f'tl.to("#{sid}-beL,#{sid}-beR",{{attr:{{fill:"{col}",ry:{3 if f == "sleep" else 12 if f == "surprised" else 9}}},'
               f'duration:0.15}},{t:.3f});',
               f'tl.to("#{sid}-sweat",{{opacity:{1 if f == "overloaded" else 0},duration:0.15}},{t:.3f});']
        if f == "overloaded":         # shakes under load, LEDs flash red (the cause is on screen)
            js.append(f'tl.fromTo("#{sid}-body",{{attr:{{transform:"translate(-4,0)"}}}},{{attr:{{transform:"translate(4,0)"}},'
                      f'duration:0.06,repeat:{max(2, int(dur * 4))},yoyo:true,ease:"none"}},{t:.3f});')
            js.append(f'tl.to("#{sid}-led0,#{sid}-led1,#{sid}-led2",{{attr:{{fill:"#e5484d"}},duration:0.1}},{t:.3f});')
        elif f == "happy":
            js.append(f'tl.fromTo("#{sid}-body",{{attr:{{transform:"translate(0,0)"}}}},{{attr:{{transform:"translate(0,-24)"}},'
                      f'duration:0.22,yoyo:true,repeat:1,ease:"power2.out"}},{t:.3f});')
            js.append(f'tl.to("#{sid}-led0,#{sid}-led1,#{sid}-led2",{{attr:{{fill:"#3bb273"}},duration:0.1}},{t:.3f});')
    return js


# ------------------------------------------------------------------ locations
def _sky(W, H, time, sid):
    top, bot = SKY.get(time, SKY["day"])
    return (f'<defs><linearGradient id="{sid}-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bot}"/></linearGradient></defs>'
            f'<rect x="-200" y="-200" width="{W + 400}" height="{H + 400}" fill="url(#{sid}-sky)"/>')


def _clouds(W, H, sid, rnd, dur, t0, n=3):
    out, js = [], []
    for k in range(n):
        y, x, s = rnd.randint(40, int(H * 0.25)), rnd.randint(0, W), rnd.uniform(0.7, 1.3)
        out.append(f'<g id="{sid}-cl{k}" transform="translate({x},{y}) scale({s:.2f})" opacity="0.9">'
                   f'<ellipse cx="0" cy="0" rx="70" ry="26" fill="#fff"/><ellipse cx="-40" cy="8" rx="44" ry="20" fill="#fff"/>'
                   f'<ellipse cx="42" cy="8" rx="48" ry="20" fill="#fff"/></g>')
        js.append(f'tl.fromTo("#{sid}-cl{k}",{{attr:{{transform:"translate({x},{y}) scale({s:.2f})"}}}},'
                  f'{{attr:{{transform:"translate({x + 40 + 10 * k},{y}) scale({s:.2f})"}},duration:{dur:.2f},ease:"none"}},{t0:.3f});')
    return "".join(out), js


def _walkers(W, gy, sid, rnd, dur, t0, n=3, scale=0.32, color="#8e9bb0"):
    """Small background people walking across (the place is alive)."""
    out, js = [], []
    for k in range(n):
        y = gy - 4
        d = 1 if k % 2 == 0 else -1
        x0 = -80 if d > 0 else W + 80
        x1 = W + 80 if d > 0 else -80
        speed = rnd.uniform(9, 14)
        out.append(f'<g id="{sid}-wk{k}" transform="translate({x0},{y}) scale({scale})" opacity="0.65">'
                   f'<circle cx="0" cy="-230" r="34" fill="none" stroke="{color}" stroke-width="10"/>'
                   f'<line x1="0" y1="-196" x2="0" y2="-80" stroke="{color}" stroke-width="10" stroke-linecap="round"/>'
                   f'<g id="{sid}-wl{k}" transform="translate(0,-80) rotate(20)"><line x1="0" y1="0" x2="0" y2="80" stroke="{color}" stroke-width="10" stroke-linecap="round"/></g>'
                   f'<g id="{sid}-wr{k}" transform="translate(0,-80) rotate(-20)"><line x1="0" y1="0" x2="0" y2="80" stroke="{color}" stroke-width="10" stroke-linecap="round"/></g></g>')
        delay = rnd.uniform(0, max(0.1, dur * 0.4))
        js.append(f'tl.fromTo("#{sid}-wk{k}",{{attr:{{transform:"translate({x0},{y}) scale({scale})"}}}},'
                  f'{{attr:{{transform:"translate({x1},{y}) scale({scale})"}},duration:{speed:.2f},ease:"none"}},{t0 + delay:.3f});')
        steps = int(speed / 0.35)
        js.append(f'tl.fromTo("#{sid}-wl{k}",{{attr:{{transform:"translate(0,-80) rotate(20)"}}}},{{attr:{{transform:"translate(0,-80) rotate(-20)"}},'
                  f'duration:0.35,repeat:{steps},yoyo:true,ease:"sine.inOut"}},{t0 + delay:.3f});')
        js.append(f'tl.fromTo("#{sid}-wr{k}",{{attr:{{transform:"translate(0,-80) rotate(-20)"}}}},{{attr:{{transform:"translate(0,-80) rotate(20)"}},'
                  f'duration:0.35,repeat:{steps},yoyo:true,ease:"sine.inOut"}},{t0 + delay:.3f});')
    return "".join(out), js


def _board(x, y, w, h, sid, sign, kind="white"):
    fill, txt = ("#fbfbf7", INK) if kind == "white" else ("#0f1830", "#7fd4ff")
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{INK}" stroke-width="6"/>'
            + (f'<foreignObject x="{x + 18}" y="{y + 10}" width="{w - 36}" height="{h - 20}">'
               f'<div xmlns="http://www.w3.org/1999/xhtml" class="w-sign" id="{sid}-sign" style="color:{txt}">{e(sign)}</div>'
               f'</foreignObject>' if sign else ""))


def location(loc: str, W: int, H: int, sid: str, time: str, sign: str, seed: int, t0: float, dur: float):
    """Returns (svg_background, svg_foreground, js) for a place. Ground line at gy."""
    rnd = random.Random(seed)
    gy = int(H * 0.86)
    js: list[str] = []
    fg = ""
    night = time == "night"
    if loc == "street":
        sky = _sky(W, H, time, sid)
        cl, cj = _clouds(W, H, sid, rnd, dur, t0) if not night else ("", [])
        js += cj
        bld = []
        for k in range(9):                     # skyline far, then shops near
            bw, bh = rnd.randint(110, 190), rnd.randint(int(H * 0.25), int(H * 0.5))
            x = k * (W // 8) - 40
            col = "#9fb3d1" if not night else "#2a3263"
            bld.append(f'<rect x="{x}" y="{gy - 110 - bh}" width="{bw}" height="{bh}" fill="{col}"/>')
            for r in range(3):
                for c in range(2):
                    lit = "#ffe9a8" if night and rnd.random() > 0.35 else "#dbe7f7"
                    bld.append(f'<rect x="{x + 18 + c * 50}" y="{gy - 90 - bh + r * 56}" width="30" height="34" fill="{lit}"/>')
        road = (f'<rect x="-200" y="{gy - 110}" width="{W + 400}" height="110" fill="#e9dfcf"/>'
                f'<rect x="-200" y="{gy}" width="{W + 400}" height="{H - gy + 200}" fill="#5b6478"/>'
                + "".join(f'<rect x="{k * 160}" y="{gy + 40}" width="80" height="10" fill="#f4f1ea"/>' for k in range(W // 160 + 2)))
        cars = []
        for k, (col, y_off, d) in enumerate((("#e5484d", 26, 1), ("#f5a524", 70, -1))):
            x0, x1 = (-260, W + 260) if d > 0 else (W + 260, -260)
            cars.append(f'<g id="{sid}-car{k}" transform="translate({x0},{gy + y_off})">'
                        f'<rect x="-90" y="-54" width="180" height="48" rx="14" fill="{col}" stroke="{INK}" stroke-width="5"/>'
                        f'<rect x="-50" y="-86" width="96" height="38" rx="10" fill="#cfe8ff" stroke="{INK}" stroke-width="5"/>'
                        f'<circle cx="-50" cy="-4" r="18" fill="{INK}"/><circle cx="50" cy="-4" r="18" fill="{INK}"/></g>')
            js.append(f'tl.fromTo("#{sid}-car{k}",{{attr:{{transform:"translate({x0},{gy + y_off})"}}}},'
                      f'{{attr:{{transform:"translate({x1},{gy + y_off})"}},duration:{rnd.uniform(3.2, 4.6):.2f},ease:"none",'
                      f'repeat:{max(0, int(dur / 4))}}},{t0 + rnd.uniform(0, 1.5):.3f});')
        wk, wj = _walkers(W, gy - 112, sid, rnd, dur, t0, n=3)
        js += wj
        billboard = _board(int(W * 0.3), int(H * 0.06), int(W * 0.4), int(H * 0.14), sid, sign) if sign else ""
        bg = sky + cl + "".join(bld) + billboard + road + wk
        fg = "".join(cars)
    elif loc == "server_room":
        bg = (f'<rect x="-200" y="-200" width="{W + 400}" height="{H + 400}" fill="#141b33"/>'
              f'<rect x="-200" y="{gy}" width="{W + 400}" height="{H}" fill="#0c1124"/>')
        racks = []
        n = 6 if W > 1500 else 4
        for k in range(n):
            x = int((k + 0.5) * W / n) - 70
            racks.append(f'<rect x="{x}" y="{gy - 420}" width="140" height="420" rx="8" fill="#232c4d" stroke="#0a0f20" stroke-width="5"/>')
            for r in range(8):
                racks.append(f'<rect x="{x + 14}" y="{gy - 400 + r * 50}" width="112" height="34" rx="4" fill="#2e3961"/>')
                led = f"{sid}-led{k}_{r}"
                racks.append(f'<circle id="{led}" cx="{x + 112}" cy="{gy - 383 + r * 50}" r="5" fill="#3bb273"/>')
                js.append(f'tl.to("#{led}",{{opacity:0.25,duration:0.01,repeat:{max(1, int(dur / 0.6))},yoyo:true,'
                          f'repeatDelay:{0.25 + rnd.random() * 0.6:.2f},ease:"steps(1)"}},{t0 + rnd.random():.3f});')
        bg += "".join(racks) + (_board(int(W * 0.06), int(H * 0.06), int(W * 0.34), int(H * 0.16), sid, sign, "screen") if sign else "")
    elif loc == "network":
        bg = f'<rect x="-200" y="-200" width="{W + 400}" height="{H + 400}" fill="#0b1026"/>'
        pts = [(rnd.randint(60, W - 60), rnd.randint(60, int(H * 0.8))) for _ in range(14)]
        lines, dots = [], []
        for k, (x, y) in enumerate(pts):
            a, b = pts[(k * 5 + 3) % len(pts)]
            lines.append(f'<line x1="{x}" y1="{y}" x2="{a}" y2="{b}" stroke="#2c3d78" stroke-width="3"/>')
            dots.append(f'<circle cx="{x}" cy="{y}" r="10" fill="#7fd4ff" stroke="#fff" stroke-width="2"/>')
            pid = f"{sid}-pk{k}"
            dots.append(f'<circle id="{pid}" cx="{x}" cy="{y}" r="7" fill="#f5a524"/>')
            js.append(f'tl.fromTo("#{pid}",{{attr:{{cx:{x},cy:{y}}}}},{{attr:{{cx:{a},cy:{b}}},duration:{rnd.uniform(1.2, 2.2):.2f},'
                      f'repeat:{max(0, int(dur / 1.8))},ease:"none"}},{t0 + rnd.random():.3f});')
        bg += "".join(lines) + "".join(dots) + (_board(int(W * 0.25), int(H * 0.05), int(W * 0.5), int(H * 0.13), sid, sign, "screen") if sign else "")
    else:   # indoor places share a room shell (wall, floor, window) + their furniture
        sky = _sky(W, H, time, sid)
        win_w, win_h = int(W * 0.26), int(H * 0.3)
        wx, wy = int(W * 0.06), int(H * 0.08)
        cl, cj = _clouds(W, H, sid, rnd, dur, t0, n=2) if not night else ("", [])
        js += cj
        window_svg = "" if loc == "classroom" else (
                f'<svg x="{wx}" y="{wy}" width="{win_w}" height="{win_h}" viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid slice">{sky}{cl}</svg>'
                f'<rect x="{wx}" y="{wy}" width="{win_w}" height="{win_h}" fill="none" stroke="{WOOD}" stroke-width="12"/>'
                f'<line x1="{wx + win_w // 2}" y1="{wy}" x2="{wx + win_w // 2}" y2="{wy + win_h}" stroke="{WOOD}" stroke-width="8"/>')
        room = (f'<rect x="-200" y="-200" width="{W + 400}" height="{H + 400}" fill="{WALL}"/>' + window_svg +
                f'<rect x="-200" y="{gy}" width="{W + 400}" height="{H}" fill="{FLOOR}"/>'
                f'<line x1="-200" y1="{gy}" x2="{W + 200}" y2="{gy}" stroke="{INK}" stroke-width="5"/>')
        # the clock sits where no board/screen of this place is
        cx, cy = (int(W * 0.42), int(H * 0.14)) if loc == "home" else (int(W * 0.9), int(H * 0.16))
        clock = (f'<circle cx="{cx}" cy="{cy}" r="46" fill="#fff" stroke="{INK}" stroke-width="6"/>'
                 f'<line x1="{cx}" y1="{cy}" x2="{cx}" y2="{cy - 26}" stroke="{INK}" stroke-width="6" stroke-linecap="round"/>'
                 f'<g id="{sid}-sec" transform="translate({cx},{cy}) rotate(0)"><line x1="0" y1="6" x2="0" y2="-36" stroke="#e5484d" stroke-width="3"/></g>')
        js.append(f'tl.fromTo("#{sid}-sec",{{attr:{{transform:"translate({cx},{cy}) rotate(0)"}}}},'
                  f'{{attr:{{transform:"translate({cx},{cy}) rotate({int(dur * 6)})"}},duration:{dur:.2f},ease:"steps({max(1, int(dur))})"}},{t0:.3f});')
        furn = ""
        if loc == "office":
            dx, dw = int(W * 0.52), int(W * 0.4)
            furn = (f'<rect x="{dx}" y="{gy - 150}" width="{dw}" height="22" fill="{WOOD}" stroke="{INK}" stroke-width="5"/>'
                    f'<line x1="{dx + 20}" y1="{gy - 128}" x2="{dx + 20}" y2="{gy}" stroke="{INK}" stroke-width="8"/>'
                    f'<line x1="{dx + dw - 20}" y1="{gy - 128}" x2="{dx + dw - 20}" y2="{gy}" stroke="{INK}" stroke-width="8"/>'
                    f'<rect x="{dx + dw // 2 - 110}" y="{gy - 330}" width="220" height="150" rx="10" fill="#0f1830" stroke="{INK}" stroke-width="6"/>'
                    f'<rect x="{dx + dw // 2 - 12}" y="{gy - 180}" width="24" height="32" fill="{METAL}"/>'
                    + "".join(f'<rect id="{sid}-code{k}" x="{dx + dw // 2 - 92}" y="{gy - 310 + k * 24}" width="{rnd.randint(70, 170)}" height="10" rx="4" '
                              f'fill="{["#7fd4ff", "#f5a524", "#3bb273"][k % 3]}"/>' for k in range(5))
                    + f'<path d="M{int(W * 0.04)} {gy} l20 -90 h40 l20 90 Z" fill="#c98b55" stroke="{INK}" stroke-width="5"/>'
                    f'<path d="M{int(W * 0.04) + 40} {gy - 90} q-40 -80 -10 -130 q20 60 10 130 q30 -90 60 -110 q-10 80 -60 110" fill="#3bb273" stroke="{INK}" stroke-width="4"/>')
            for k in range(5):                 # code being typed on the monitor
                js.append(f'tl.from("#{sid}-code{k}",{{attr:{{width:0}},duration:0.5,ease:"none"}},{t0 + 0.3 + k * 0.45:.3f});')
            fg = ""
            room += clock
            if sign:
                room += _board(int(W * 0.36), int(H * 0.05), int(W * 0.34), int(H * 0.14), sid, sign)
        elif loc == "home":
            furn = (f'<rect x="{int(W * 0.5)}" y="{gy - 120}" width="{int(W * 0.42)}" height="120" rx="30" fill="#e5484d" stroke="{INK}" stroke-width="6"/>'
                    f'<rect x="{int(W * 0.5)}" y="{gy - 190}" width="{int(W * 0.42)}" height="90" rx="30" fill="#f07075" stroke="{INK}" stroke-width="6"/>'
                    f'<line x1="{int(W * 0.42)}" y1="{gy}" x2="{int(W * 0.42)}" y2="{gy - 260}" stroke="{INK}" stroke-width="6"/>'
                    f'<path d="M{int(W * 0.42) - 50} {gy - 260} h100 l-24 -70 h-52 Z" fill="#ffd84d" stroke="{INK}" stroke-width="5"/>')
            room += clock + (_board(int(W * 0.55), int(H * 0.08), int(W * 0.32), int(H * 0.2), sid, sign, "screen") if sign else "")
        elif loc == "shop":
            furn = (f'<rect x="{int(W * 0.5)}" y="{gy - 200}" width="{int(W * 0.45)}" height="200" fill="{WOOD}" stroke="{INK}" stroke-width="6"/>'
                    f'<rect x="{int(W * 0.5)}" y="{gy - 214}" width="{int(W * 0.45)}" height="20" fill="#a26a3c" stroke="{INK}" stroke-width="5"/>'
                    f'<rect x="{int(W * 0.84)}" y="{gy - 330}" width="80" height="110" rx="8" fill="#fff" stroke="{INK}" stroke-width="5"/>'
                    + "".join(f'<rect x="{int(W * 0.84) + 14 + (k % 3) * 18}" y="{gy - 310 + (k // 3) * 22}" width="14" height="14" fill="{INK}"/>' for k in range(9))
                    + "".join(f'<rect x="{int(W * 0.55) + k * 70}" y="{gy - 420}" width="54" height="70" rx="6" fill="{c}" stroke="{INK}" stroke-width="4"/>'
                              for k, c in enumerate(["#f5a524", "#3bb273", "#2f6fde", "#e5484d"])))
            banner = (f'<rect x="{int(W * 0.04)}" y="{int(H * 0.03)}" width="{int(W * 0.92)}" height="{int(H * 0.11)}" fill="#e5484d" stroke="{INK}" stroke-width="6"/>'
                      + (f'<foreignObject x="{int(W * 0.06)}" y="{int(H * 0.035)}" width="{int(W * 0.88)}" height="{int(H * 0.1)}">'
                         f'<div xmlns="http://www.w3.org/1999/xhtml" class="w-sign w-banner" id="{sid}-sign">{e(sign)}</div></foreignObject>' if sign else ""))
            wk, wj = _walkers(W, gy, sid, rnd, dur, t0, n=2, scale=0.4)
            js += wj
            room += banner + wk
        elif loc == "classroom":
            room += _board(int(W * 0.18), int(H * 0.1), int(W * 0.64), int(H * 0.36), sid, sign or " ")
            furn = "".join(f'<rect x="{int(W * (0.08 + k * 0.3))}" y="{gy - 90}" width="{int(W * 0.2)}" height="18" fill="{WOOD}" stroke="{INK}" stroke-width="5"/>'
                           for k in range(3))
        bg = room + furn
    if sign:                                   # the sign is written on, word by word
        js.append(f'tl.from("#{sid}-sign",{{opacity:0,filter:"blur(6px)",duration:0.3,ease:"power2.out"}},{t0 + 0.4:.3f});')
    return bg, fg, js


# ------------------------------------------------------------------ speech bubble
def _bubble(sid: str, x: float, y: float, text: str, W: int, flip: bool) -> str:
    bw = min(int(W * 0.46), max(220, 30 * len(text) // 2 + 80))
    bx = max(20, min(W - bw - 20, int(x - bw / 2)))
    tail_x = max(bx + 30, min(bx + bw - 30, int(x)))
    return (f'<g id="{sid}" opacity="0"><foreignObject x="{bx}" y="{int(y) - 170}" width="{bw}" height="150">'
            f'<div xmlns="http://www.w3.org/1999/xhtml" class="w-bwrap"><div class="w-bubble">{e(text)}</div></div></foreignObject>'
            f'<path d="M{tail_x - 18} {int(y) - 26} L{tail_x} {int(y) + 6} L{tail_x + 18} {int(y) - 26} Z" fill="#fff" stroke="{INK}" stroke-width="4"/></g>')


CAMERA = {"wide": (1.0, 1.0, 0, 0), "push": (1.0, 1.08, 0, 0), "pan": (1.04, 1.04, -40, 40),
          "close": (1.22, 1.22, 0, 0)}


def visual(i: int, v: dict, t0: float, dur: float, long_: bool, seed: int) -> tuple[str, list[str]]:
    W, H = (1920, 800) if long_ else (1080, 1040)
    sid = f"s{i}w"
    loc = v.get("location", "office") if v.get("location") in LOCATIONS else "office"
    time = "night" if v.get("time") == "night" else "day"
    bg, fg, js = location(loc, W, H, sid, time, str(v.get("sign", "")), seed + i * 7, t0, dur)
    gy = int(H * 0.86) if loc != "street" else int(H * 0.86) - 40      # street: stand on the pavement
    chars, bubbles = [], []
    ch_h = int(H * (0.56 if long_ else 0.58))
    cast = v.get("cast") or []
    for k, c in enumerate(cast[:3]):
        who = c.get("who", "arjun")
        cx = int(float(c.get("x", 0.3 + 0.4 * k)) * W)
        csid = f"s{i}c{k}"
        flip = c.get("face") == "left"
        if who == "byte":
            bw = int(ch_h * 0.78)
            inner = (f'<svg x="{-bw // 2}" y="{gy - int(bw * 1.18)}" width="{bw}" height="{int(bw * 1.18)}" '
                     f'viewBox="-110 -170 220 260" overflow="visible">{_byte(csid)[_byte(csid).index(">") + 1:]}')
            js += _byte_js(csid, c.get("mood") or c.get("actions") or [], t0, dur)
            head_y = gy - int(bw * 1.18)
        else:
            vb_h = int(ch_h * 640 / 454)
            vb_w = int(vb_h * 400 / 640)
            fig = _character(csid, who)
            inner = f'<svg x="{-vb_w // 2}" y="{gy - int(vb_h * (194 + 330) / 640)}" width="{vb_w}" height="{vb_h}"' + fig[len('<svg class="stick-svg"'):]
            head_y = gy - ch_h
            acts = sorted(c.get("actions") or [{"at": 0, "pose": "stand", "face": "neutral"}], key=lambda x: x.get("at", 0))
            a0 = stk.POSES.get(acts[0].get("pose", "stand"), stk.POSES["stand"])
            for j, ang in zip(stk.JOINTS, a0):
                js.append(f'tl.set("#{csid}-{j}",{{attr:{{transform:"{stk._tf(j, ang)}"}}}},{t0:.3f});')
            js += stk._face_js(csid, acts[0].get("face", "neutral"), t0)
            js.append(f'for(let q=0;q<{max(1, int(dur * 12))};q++){{tl.set("#{csid}-turb",{{attr:{{seed:(q%5)+1}}}},{t0:.3f}+q/12);}}')
            walk = c.get("walk_from")
            prev = acts[0].get("pose", "stand")
            if walk in ("left", "right"):
                for q in range(6):
                    js += stk._pose_js(csid, "run1" if q % 2 == 0 else "run2", t0 + q * 0.2, None)
                js += stk._pose_js(csid, prev, t0 + 1.2, "run2")
            for act in acts[1:]:
                t = t0 + max(0.0, min(0.95, float(act.get("at", 0)))) * dur
                js += stk._pose_js(csid, act.get("pose", "stand"), t, prev)
                js += stk._face_js(csid, act.get("face", "neutral"), t + 0.05)
                prev = act.get("pose", "stand")
            prop = c.get("prop")
            if prop in stk.PROP_SVG:
                held = prop == "phone"
                gid, base = (f"{csid}-hprop", "translate(0,76)") if held else (f"{csid}-gprop", "translate(58,60)")
                inner = inner.replace(f'<g id="{gid}" transform="{base} scale{"(1)" if held else "(1.5)"}"></g>',
                                      f'<g id="{gid}" transform="{base} scale{"(1)" if held else "(1.5)"}">{stk.PROP_SVG[prop].format(ink=INK)}</g>')
        sx = -1 if flip else 1
        start_x = {"left": -300, "right": W + 300}.get(c.get("walk_from"), cx)
        chars.append(f'<g id="{csid}-pos" transform="translate({start_x},0)"><g transform="scale({sx},1)">{inner}</g></g>')
        if c.get("walk_from") in ("left", "right"):
            js.append(f'tl.fromTo("#{csid}-pos",{{attr:{{transform:"translate({start_x},0)"}}}},{{attr:{{transform:"translate({cx},0)"}},'
                      f'duration:1.2,ease:"power1.out"}},{t0:.3f});')
        else:
            js.append(f'tl.from("#{csid}-pos",{{opacity:0,duration:0.3,ease:"power2.out"}},{t0:.3f});')
        if c.get("say"):
            bid = f"{csid}-say"
            bubbles.append(_bubble(bid, cx, head_y - 10, str(c["say"]), W, flip))
            ta = t0 + max(0.0, min(0.9, float(c.get("say_at", 0.15)))) * dur
            te = t0 + max(0.1, min(1.0, float(c.get("say_end", 0.95)))) * dur
            js.append(f'tl.fromTo("#{bid}",{{opacity:0,attr:{{transform:"translate(0,20)"}}}},{{opacity:1,attr:{{transform:"translate(0,0)"}},'
                      f'duration:0.25,ease:"back.out(1.6)"}},{ta:.3f});')
            js.append(f'tl.to("#{bid}",{{opacity:0,duration:0.15}},{max(ta + 0.5, te - 0.15):.3f});')
    s0, s1, px0, px1 = CAMERA.get(v.get("camera", "wide"), CAMERA["wide"])
    if v.get("zoom"):                            # fixed framing (thumbnails, close-ups)
        s0 = s1 = float(v["zoom"])
        px0 = px1 = 0
    focus = int(float((cast[0].get("x", 0.5) if cast else 0.5)) * W)
    def cam(s, px):
        return f"translate({focus * (1 - s) + px:.1f},{gy * (1 - s):.1f}) scale({s:.3f})"
    js.append(f'tl.fromTo("#{sid}-cam",{{attr:{{transform:"{cam(s0, px0)}"}}}},{{attr:{{transform:"{cam(s1, px1)}"}},'
              f'duration:{dur:.2f},ease:"power1.inOut"}},{t0:.3f});')
    svg = (f'<svg class="world-svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" preserveAspectRatio="xMidYMid slice">'
           f'<g id="{sid}-cam" transform="{cam(s0, px0)}">{bg}{"".join(chars)}{fg}{"".join(bubbles)}</g></svg>')
    return f'<div class="world" id="{sid}">{svg}</div>', js


CSS = """
.world{position:absolute;inset:0;overflow:hidden}
.world-svg{position:absolute;left:0;top:0;width:100%;height:100%}
.w-sign{font-family:'Caveat','Noto Sans Tamil',cursive;font-weight:700;font-size:64px;line-height:1.05;text-align:center;
 display:flex;align-items:center;justify-content:center;height:100%}
.w-banner{color:#fff;font-size:56px}
.w-bwrap{height:100%;display:flex;align-items:flex-end;justify-content:center}
.w-bubble{background:#fff;border:4px solid #1d2433;border-radius:28px;padding:14px 22px;font-family:'Caveat','Noto Sans Tamil',cursive;
 font-weight:700;font-size:46px;line-height:1.05;color:#1d2433;text-align:center}
"""
