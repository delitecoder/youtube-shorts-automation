"""Storyboard + metadata schema and deterministic validation (no model calls).

Storyboard JSON (written by the storywriter agent):
{
  "video_id": "VIDEO-20261124-001",
  "topic": "...", "category": "artificial intelligence",
  "hooks": ["h1", "h2", "h3"], "chosen_hook": 0,
  "titles": ["t1", "t2", "t3"], "chosen_title": 0,
  "scenes": [
    {"narration": "...", "on_screen_text": "...", "transition": "fade|slide|cut",
     "visual": {"type": "title|bullets|code|diagram|stat|comparison|timeline|question", ...}}
  ],
  "closing_question": "...",
  "references": [{"title": "...", "url": "https://...", "publisher": "...", "published": "YYYY-MM-DD"}],
  "attribution": []
}
"""
from __future__ import annotations

import re

WPS = 2.55            # measured average for Chirp 3 HD / WaveNet at rate 1.0 (~153 wpm)
SCENE_PAD_S = 0.35
VISUAL_TYPES = {
    "title": {"headline"},
    "bullets": {"items"},
    "code": {"code"},
    "diagram": {"nodes"},
    "stat": {"value", "label"},
    "comparison": {"left", "right"},
    "timeline": {"events"},
    "question": {"question"},
    "image": {"asset"},          # file name of an image fetched with `ytauto fetch-asset`
}
OUTRO_TYPE = "subscribe"         # added automatically by with_outro(); not written by the storywriter
TRANSITIONS = {"fade", "slide", "cut"}
URL_RE = re.compile(r"^https://[^\s]+$")
HASHTAG_RE = re.compile(r"#\w+")


def words(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def estimate(sb: dict) -> dict:
    t, scenes = 0.0, []
    for i, s in enumerate(sb.get("scenes", [])):
        d = words(s.get("narration", "")) / WPS + SCENE_PAD_S
        scenes.append({"scene": i + 1, "start": round(t, 2), "end": round(t + d, 2)})
        t += d
    total_words = sum(words(s.get("narration", "")) for s in sb.get("scenes", []))
    return {"words": total_words, "est_seconds": round(t, 1), "scenes": scenes}


def with_outro(sb: dict, text: str) -> dict:
    """Append the standard subscribe end card (narration + animated button)."""
    out = dict(sb)
    out["scenes"] = list(sb["scenes"]) + [{
        "narration": text, "on_screen_text": "Enjoyed this?", "transition": "fade",
        "visual": {"type": OUTRO_TYPE}, "outro": True}]
    return out


def validate_storyboard(sb: dict, require_sources: bool = True) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings). Errors block rendering."""
    e, w = [], []
    for k in ("video_id", "topic", "hooks", "titles", "scenes", "closing_question"):
        if k not in sb:
            e.append(f"missing '{k}'")
    if e:
        return e, w
    if len(sb["hooks"]) != 3:
        e.append("need exactly 3 hook alternatives")
    if len(sb["titles"]) != 3:
        e.append("need exactly 3 title alternatives")
    for key, arr in (("chosen_hook", "hooks"), ("chosen_title", "titles")):
        if not isinstance(sb.get(key), int) or not 0 <= sb[key] < len(sb[arr]):
            e.append(f"'{key}' must index into '{arr}'")
    scenes = sb["scenes"]
    if not 5 <= len(scenes) <= 8:
        e.append(f"{len(scenes)} scenes; need 5–8")
    for i, s in enumerate(scenes, 1):
        if not s.get("narration", "").strip():
            e.append(f"scene {i}: empty narration")
        if len(s.get("on_screen_text", "")) > 42:
            e.append(f"scene {i}: on_screen_text over 42 chars")
        if s.get("transition", "cut") not in TRANSITIONS:
            e.append(f"scene {i}: unknown transition")
        v = s.get("visual", {})
        req = VISUAL_TYPES.get(v.get("type"))
        if req is None:
            e.append(f"scene {i}: visual.type must be one of {sorted(VISUAL_TYPES)}")
        elif missing := req - set(v):
            e.append(f"scene {i}: visual missing {sorted(missing)}")
        if "subscribe" in s.get("narration", "").lower():
            w.append(f"scene {i}: the subscribe ask is added automatically; remove it from the script")
        if v.get("type") == "code" and len(v.get("code", "").splitlines()) > 12:
            e.append(f"scene {i}: code longer than 12 lines")
    if scenes and isinstance(sb.get("chosen_hook"), int) and 0 <= sb["chosen_hook"] < len(sb["hooks"]):
        if sb["hooks"][sb["chosen_hook"]].strip().lower()[:25] not in scenes[0]["narration"].lower():
            w.append("first scene narration does not start with the chosen hook")
    est = estimate(sb)
    if est["est_seconds"] > 75:
        e.append(f"estimated {est['est_seconds']}s; too long (target 45–60s, hard cap 75s)")
    elif not 40 <= est["est_seconds"] <= 65:
        w.append(f"estimated {est['est_seconds']}s outside the ~45–60s target")
    if not 100 <= est["words"] <= 145:
        w.append(f"{est['words']} words (target ~100–145)")
    if not sb["closing_question"].strip().endswith("?"):
        e.append("closing_question must be a question")
    refs = sb.get("references", [])
    if require_sources and not 3 <= len(refs) <= 5:
        e.append(f"{len(refs)} references; need 3–5 for researched topics")
    for r in refs:
        if not URL_RE.match(r.get("url", "")):
            e.append(f"reference URL must be https: {r.get('url')}")
    return e, w


def validate_metadata(m: dict) -> list[str]:
    """YouTube limits: title <= 100 chars, description <= 5000 bytes, tags <= 500 chars total,
    no '<' or '>' in title/description."""
    e = []
    t, d, tags = m.get("title", ""), m.get("description", ""), m.get("tags", [])
    if not t.strip() or len(t) > 100:
        e.append("title must be 1–100 characters")
    if len(d.encode()) > 5000:
        e.append("description over 5000 bytes")
    if any(c in t + d for c in "<>"):
        e.append("title/description must not contain < or >")
    tag_chars = sum(len(x) + (2 if " " in x else 0) for x in tags) + max(len(tags) - 1, 0)
    if tag_chars > 500:
        e.append(f"tags use {tag_chars} of 500 characters")
    if len(HASHTAG_RE.findall(d)) > 15:
        e.append("more than 15 hashtags (YouTube ignores all of them)")
    if not m.get("pinned_comment", "").strip():
        e.append("pinned_comment suggestion missing")
    banned = ("guaranteed", "100% viral", "you won't believe")
    if any(b in (t + d).lower() for b in banned):
        e.append("misleading/clickbait phrasing")
    return e
