"""Event-based sound design: every cue is tied to a visible or narrative event and carries its reason.

Sources of cues, in priority order:
1. `scene.audio_events` declared by the storyboard: [{"at": 0-1 (fraction of scene) or "t": seconds,
   "event": "server_start", "sound": "machine"|..., "reason": "...", "pan": -1..1, "gain_db": -10}]
2. Story-world actions the viewer SEES (walk, speech pop, pose changes, props) and place ambience.
3. Reveal accents for elements that visibly appear (stat counter lands, list/diagram reveal).
Never: a sound just because the scene changed. A transition gets a whoosh only when it has real motion
(swipe/zoom/slide), and the opening impact only when scene 1 declares it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

MOTION_TRANSITIONS = {"swipe", "zoom", "slide", "push", "whip"}
STORY_POSE_CUES = {"shocked": "impact", "facepalm": "fail", "sad": "fail", "celebrate": "success", "type": "typing"}
AMBIENCE = {"server_room": "amb_machine", "street": "amb_city", "shop": "amb_city"}


@dataclass
class Cue:
    scene: int
    t: float
    event: str
    sound: str
    reason: str
    gain_db: float = -10.0
    pan: float = 0.0
    fade_s: float = 0.02
    stem: str = "sfx"            # sfx | ambience

    def as_dict(self) -> dict:
        return asdict(self)


def _story(i: int, v: dict, t0: float, dur: float) -> list[Cue]:
    out: list[Cue] = []
    loc = v.get("location")
    if loc in AMBIENCE:
        out.append(Cue(i, t0, "place_ambience", AMBIENCE[loc], f"scene set in {loc}", -24.0, 0.0, 0.8, "ambience"))
    for c in v.get("cast", []):
        who = c.get("id", "character")
        side = {"left": -0.4, "right": 0.4}.get(c.get("walk_from"), 0.0)
        if c.get("walk_from") in ("left", "right"):
            out += [Cue(i, t0 + 0.1 + k * 0.32, "footstep", "footstep", f"{who} walks in from {c['walk_from']}",
                        -16.0, side * (1 - k / 4)) for k in range(4)]
        if c.get("say"):
            out.append(Cue(i, t0 + max(0.0, min(0.9, float(c.get("say_at", 0.15)))) * dur, "speech_bubble", "pop",
                           f"{who}'s speech bubble appears", -14.0))
        for a in (c.get("actions") or [])[1:]:
            k = STORY_POSE_CUES.get(a.get("pose"))
            if k:
                out.append(Cue(i, t0 + max(0.0, min(0.95, float(a.get("at", 0)))) * dur, f"pose_{a['pose']}", k,
                               f"{who} {a['pose']}", -12.0))
        for m in c.get("mood") or []:
            k = {"overloaded": "fail", "happy": "success", "surprised": "notify"}.get(m.get("face"))
            if k and float(m.get("at", 0)) > 0:
                out.append(Cue(i, t0 + float(m["at"]) * dur, f"mood_{m['face']}", k, f"{who} looks {m['face']}", -14.0))
        if c.get("prop") == "phone":
            out.append(Cue(i, t0 + 0.4, "phone_notification", "notify", f"{who}'s phone shows a message", -14.0))
        elif c.get("prop") == "coin":
            out.append(Cue(i, t0 + 0.4, "payment", "money", f"{who} pays", -14.0))
    return out


def plan(scenes: list[dict], timings: list[dict]) -> list[Cue]:
    cues: list[Cue] = []
    for i, (s, tm) in enumerate(zip(scenes, timings)):
        t0, dur = tm["start"], tm["end"] - tm["start"]
        v = s.get("visual", {}) or {}
        kind = v.get("type")
        for e in s.get("audio_events") or []:
            t = float(e["t"]) if "t" in e else t0 + float(e.get("at", 0)) * dur
            if not e.get("reason"):
                raise ValueError(f"scene {i + 1}: audio event {e.get('event')!r} has no reason")
            cues.append(Cue(i, t, e.get("event", "event"), e.get("sound", "pop"), e["reason"],
                            float(e.get("gain_db", -12.0)), float(e.get("pan", 0.0)), float(e.get("fade_s", 0.02)),
                            "ambience" if str(e.get("sound", "")).startswith("amb_") else "sfx"))
        if kind == "story":
            cues += _story(i, v, t0, dur)
        if i > 0 and s.get("transition") in MOTION_TRANSITIONS:
            cues.append(Cue(i, max(0.0, t0 - 0.2), "transition_motion", "whoosh",
                            f"{s['transition']} transition moves the frame", -18.0))
        if kind == "stat":
            cues.append(Cue(i, t0 + 0.45, "number_lands", "pop", "the stat counter lands on its value", -14.0))
        elif kind in ("bullets", "diagram", "timeline"):
            cues.append(Cue(i, t0 + 0.5, "reveal", "tick", f"first {kind} element is revealed", -18.0))
        elif kind == "subscribe":
            cues.append(Cue(i, t0 + 0.3, "subscribe_button", "chime", "subscribe button pops in", -14.0))
    return sorted(cues, key=lambda c: c.t)


def check(cues: list[Cue], duration: float) -> list[str]:
    """Cue-sheet QC: reasons present, inside the timeline, no stacked duplicates."""
    problems, seen = [], set()
    for c in cues:
        if not c.reason:
            problems.append(f"cue {c.event} at {c.t:.2f}s has no reason")
        if not 0 <= c.t <= duration:
            problems.append(f"cue {c.event} at {c.t:.2f}s is outside 0..{duration:.2f}s")
        key = (round(c.t, 1), c.sound)
        if key in seen and c.stem == "sfx":
            problems.append(f"duplicate {c.sound} at {c.t:.2f}s")
        seen.add(key)
    return problems
