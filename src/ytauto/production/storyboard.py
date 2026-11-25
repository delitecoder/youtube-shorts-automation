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
    "custom": {"code"},          # scene-coder (camera man) HTML+GSAP in scenes/<code>.html/.js
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


def _split(text: str, default_label: str) -> tuple[str, str]:
    head, sep, tail = str(text).partition(":")
    return (head.strip(), tail.strip()) if sep and len(head) <= 24 else (default_label, str(text).strip())


def normalize_visual(v: dict) -> dict:
    """Accept the simple string forms writers often use and convert them to the renderer's shape:
    comparison left/right "Before: a, b" -> {"title", "items"}; timeline "2026-07-28: text" ->
    {"label", "text"}; bullets/diagram items coerced to strings."""
    v = dict(v)
    t = v.get("type")
    if t == "comparison":
        for side, default in (("left", "Before"), ("right", "After")):
            x = v.get(side)
            if isinstance(x, str):
                title, rest = _split(x, default)
                v[side] = {"title": title, "items": [i.strip() for i in rest.split(",") if i.strip()]}
            elif isinstance(x, dict):
                x.setdefault("title", default)
                x["items"] = [str(i) for i in x.get("items", [])]
    elif t == "timeline":
        ev = []
        for e in v.get("events", []):
            if isinstance(e, str):
                label, text = _split(e, "")
                ev.append({"label": label or text[:12], "text": text if label else ""})
            elif isinstance(e, dict):
                ev.append({"label": str(e.get("label", "")), "text": str(e.get("text", ""))})
        v["events"] = ev
    elif t in ("bullets", "diagram"):
        key = "items" if t == "bullets" else "nodes"
        v[key] = [str(i) for i in v.get(key, [])]
    return v


def normalize(sb: dict) -> dict:
    out = dict(sb)
    out["scenes"] = [dict(s, visual=normalize_visual(s.get("visual", {}))) for s in sb.get("scenes", [])]
    return out


def with_outro(sb: dict, text: str) -> dict:
    """Append the standard subscribe end card (narration + animated button)."""
    out = dict(sb)
    vis = {"type": OUTRO_TYPE}
    if is_long(sb):
        vis["text"] = "A new system-design breakdown every day"
    out["scenes"] = list(sb["scenes"]) + [{
        "narration": text, "on_screen_text": "Enjoyed this?", "transition": "fade",
        "visual": vis, "outro": True}]
    return out


def narration_chars(sb: dict) -> int:
    """Characters billed by TTS (whitespace collapsed, as tts.clean_text sends them)."""
    return sum(len(" ".join(s.get("narration", "").split())) for s in sb.get("scenes", []))


def _char_check(sb: dict, e: list[str], fmt: str) -> None:
    from ..config import NARRATION_CHAR_LIMIT
    n, lim = narration_chars(sb), NARRATION_CHAR_LIMIT[fmt]
    if n > lim:
        e.append(f"narration is {n} characters; keep it ≤ {lim} so the voice stays in the free tier")


def is_long(sb: dict) -> bool:
    return sb.get("format") == "long"


def chapters(sb: dict, timings: list[dict]) -> list[tuple[float, str]]:
    """(start seconds, title) per chapter: a scene with "chapter" starts one; the first is 0:00."""
    out = []
    for s, t in zip(sb.get("scenes", []), timings):
        if s.get("chapter"):
            out.append((0.0 if not out else t["start"], str(s["chapter"]).strip()))
    if out and out[0][0] != 0.0:
        out[0] = (0.0, out[0][1])
    return out


def chapter_block(chs: list[tuple[float, str]]) -> str:
    """YouTube chapters: first at 0:00, ≥ 3 entries, each ≥ 10 s, ascending."""
    return "\n".join(f"{int(t // 60)}:{int(t % 60):02d} {title}" for t, title in chs)


def validate_long(sb: dict, require_sources: bool = True) -> tuple[list[str], list[str]]:
    """Long-form (16:9, ~6–8 min) system-design explainer."""
    e, w = [], []
    for k in ("video_id", "topic", "hooks", "titles", "scenes", "closing_question"):
        if k not in sb:
            e.append(f"missing '{k}'")
    if e:
        return e, w
    if len(sb["hooks"]) != 3 or len(sb["titles"]) != 3:
        e.append("need exactly 3 hooks and 3 titles")
    for key, arr in (("chosen_hook", "hooks"), ("chosen_title", "titles")):
        if not isinstance(sb.get(key), int) or not 0 <= sb[key] < len(sb[arr]):
            e.append(f"'{key}' must index into '{arr}'")
    scenes = sb["scenes"]
    if not 30 <= len(scenes) <= 90:
        e.append(f"{len(scenes)} scenes; need 30–90 (one visual beat every ~5–12 s)")
    if not scenes or not scenes[0].get("chapter"):
        e.append("scene 1 must start a chapter (e.g. \"chapter\": \"Intro\")")
    n_ch = sum(1 for s in scenes if s.get("chapter"))
    if n_ch < 4:
        e.append(f"{n_ch} chapters; need ≥ 4 (YouTube shows chapters from 3, viewers jump to them)")
    run = 1
    for i, s in enumerate(scenes, 1):
        if not s.get("narration", "").strip():
            e.append(f"scene {i}: empty narration")
        if len(s.get("on_screen_text", "")) > 60:
            e.append(f"scene {i}: on_screen_text over 60 chars")
        if s.get("transition", "cut") not in TRANSITIONS:
            e.append(f"scene {i}: unknown transition")
        v = normalize_visual(s.get("visual", {}))
        req = VISUAL_TYPES.get(v.get("type"))
        if req is None:
            e.append(f"scene {i}: visual.type must be one of {sorted(VISUAL_TYPES)}")
        elif missing := req - set(v):
            e.append(f"scene {i}: visual missing {sorted(missing)}")
        if v.get("type") == "code" and len(v.get("code", "").splitlines()) > 16:
            e.append(f"scene {i}: code longer than 16 lines")
        d = words(s.get("narration", "")) / WPS
        if d > 20:
            w.append(f"scene {i}: ~{d:.0f}s on one visual; split it (attention drops after ~12 s)")
        if i > 1 and v.get("type") == scenes[i - 2].get("visual", {}).get("type"):
            run += 1
            if run == 3:
                w.append(f"scene {i}: third '{v.get('type')}' in a row; vary the visual (pattern interrupt)")
        else:
            run = 1
        if "subscribe" in s.get("narration", "").lower() and i > 3:
            w.append(f"scene {i}: the subscribe ask is added automatically at the end")
    if scenes and isinstance(sb.get("chosen_hook"), int) and 0 <= sb["chosen_hook"] < len(sb["hooks"]):
        if sb["hooks"][sb["chosen_hook"]].strip().lower()[:25] not in scenes[0]["narration"].lower():
            w.append("first scene narration does not start with the chosen hook")
    est = estimate(sb)
    if est["est_seconds"] > 720:
        e.append(f"estimated {est['est_seconds']}s; too long (target 6–8 min, hard cap 12 min)")
    elif not 330 <= est["est_seconds"] <= 540:
        w.append(f"estimated {est['est_seconds']}s outside the ~6–8 min target")
    _char_check(sb, e, "long")
    custom = sum(1 for s in scenes if s.get("visual", {}).get("type") == "custom")
    if custom < 4:
        w.append(f"only {custom} custom (camera-man) scenes; key diagrams should be custom (aim 6–12)")
    if not sb["closing_question"].strip().endswith("?"):
        e.append("closing_question must be a question")
    refs = sb.get("references", [])
    if require_sources and not 3 <= len(refs) <= 8:
        e.append(f"{len(refs)} references; need 3–8")
    for r in refs:
        if not URL_RE.match(r.get("url", "")):
            e.append(f"reference URL must be https: {r.get('url')}")
    return e, w


def validate_storyboard(sb: dict, require_sources: bool = True) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings). Errors block rendering."""
    if is_long(sb):
        return validate_long(sb, require_sources)
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
        v = normalize_visual(s.get("visual", {}))
        req = VISUAL_TYPES.get(v.get("type"))
        if req is None:
            e.append(f"scene {i}: visual.type must be one of {sorted(VISUAL_TYPES)}")
        elif missing := req - set(v):
            e.append(f"scene {i}: visual missing {sorted(missing)}")
        if "subscribe" in s.get("narration", "").lower():
            w.append(f"scene {i}: the subscribe ask is added automatically; remove it from the script")
        if v.get("type") == "comparison" and not all(v[k].get("items") for k in ("left", "right") if k in v):
            e.append(f"scene {i}: comparison sides need items")
        if v.get("type") == "timeline" and not v.get("events"):
            e.append(f"scene {i}: timeline needs events")
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
    _char_check(sb, e, "short")
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


def validate_metadata(m: dict, long: bool = False) -> list[str]:
    """YouTube limits: title <= 100 chars, description <= 5000 bytes, tags <= 500 chars total,
    no '<' or '>' in title/description. long=True: a normal (16:9) video, never tagged #Shorts."""
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
    tags_in_desc = HASHTAG_RE.findall(d)
    if len(tags_in_desc) > 15:
        e.append("more than 15 hashtags (YouTube ignores all of them)")
    if not 3 <= len(tags_in_desc) <= 6:
        e.append(f"{len(tags_in_desc)} hashtags in the description; use 3–5 (the first 3 show above the title)")
    if long and not re.search(r"(?m)^0:00 ", d):
        e.append("long-form description needs chapters starting at 0:00 (added by submit from the storyboard)")
    if long and "#shorts" in d.lower():
        e.append("long-form description must not include #Shorts")
    elif not long and "#shorts" not in d.lower():
        e.append("description must include #Shorts")
    if not 8 <= len(tags) <= 15:
        e.append(f"{len(tags)} tags; use 8–15 (broad + specific)")
    if str(m.get("category_id", "28")) not in ("27", "28"):
        e.append("category_id must be 28 (Science & Technology) or 27 (Education)")
    if len(t) > 70:
        e.append("title over 70 characters; keep it ~60 so it is not cut off on phones")
    if not m.get("pinned_comment", "").strip():
        e.append("pinned_comment suggestion missing")
    banned = ("guaranteed", "100% viral", "you won't believe")
    if any(b in (t + d).lower() for b in banned):
        e.append("misleading/clickbait phrasing")
    return e
