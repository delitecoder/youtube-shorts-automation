"""Translatable text of a storyboard, as a flat list, so a language version changes ONLY text.

export(sb) -> [{"path": "scenes.2.narration", "text": "..."}, ...]
merge(sb, items, lang) -> a copy of the English storyboard with those texts replaced; every
visual (types, custom scene code, assets, numbers, diagram nodes, code) stays identical.
The translator writes only the list (far fewer tokens than a whole storyboard).
"""
from __future__ import annotations

import copy

TOP = ("hooks", "titles", "closing_question", "topic")
SCENE = ("narration", "on_screen_text", "chapter")
# human sentences inside visuals; technical labels (diagram nodes, code, stat values) stay English
VISUAL = {"title": ("headline", "kicker"), "bullets": ("items",), "stat": ("label",),
          "question": ("question",), "image": ("caption",), "timeline": ("events.*.text",),
          "comparison": ("left.title", "left.items", "right.title", "right.items")}


def _get(obj, parts):
    for p in parts:
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    return obj


def _set(obj, parts, value):
    for p in parts[:-1]:
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    last = parts[-1]
    if isinstance(obj, list):
        obj[int(last)] = value
    else:
        obj[last] = value


def _emit(out, path, val):
    if isinstance(val, str) and val.strip():
        out.append({"path": path, "text": val})
    elif isinstance(val, list):
        for k, x in enumerate(val):
            _emit(out, f"{path}.{k}", x)


def export(sb: dict) -> list[dict]:
    out: list[dict] = []
    for k in TOP:
        if k in sb:
            _emit(out, k, sb[k])
    for i, s in enumerate(sb.get("scenes", [])):
        for k in SCENE:
            if k in s:
                _emit(out, f"scenes.{i}.{k}", s[k])
        v = s.get("visual", {})
        for field in VISUAL.get(v.get("type"), ()):
            if "*" in field:
                head, _, tail = field.partition(".*.")
                for j, ev in enumerate(v.get(head, []) or []):
                    if isinstance(ev, dict) and tail in ev:
                        _emit(out, f"scenes.{i}.visual.{head}.{j}.{tail}", ev[tail])
                continue
            try:
                val = _get(v, field.split("."))
            except (KeyError, IndexError, TypeError):
                continue
            _emit(out, f"scenes.{i}.visual.{field}", val)
    return out


def merge(sb: dict, items: list[dict], lang: str) -> tuple[dict, list[str]]:
    """Returns (translated storyboard, errors). Every exported path must be translated."""
    from .storyboard import normalize
    base = normalize(copy.deepcopy(sb))
    want = {x["path"] for x in export(base)}
    got = {x.get("path"): x.get(lang) or x.get("text_" + lang) for x in items}
    errs = [f"missing translation: {p}" for p in sorted(want) if not (got.get(p) or "").strip()]
    errs += [f"unknown path: {p}" for p in sorted(set(got) - want)]
    if errs:
        return base, errs
    for p in want:
        _set(base, p.split("."), got[p].strip())
    base["language"] = lang
    return base, []
