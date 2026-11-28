"""Style registry + selection (replaces the old date-modulo rotation).

choose(): an experiment assignment wins; otherwise the best-suited style for the topic category that was
NOT used in the recent window of the same format (novelty), preferring the least recently used, then the
best measured performance when evidence exists. apply() writes the explicit style into the storyboard,
so rendering is deterministic and the choice is recorded in the ProductionManifest.
"""
from __future__ import annotations

from studio.core import conf


def registry(cfg: dict | None = None) -> list[dict]:
    return (cfg or conf.load("styles"))["styles"]


def by_id(style_id: str, cfg: dict | None = None) -> dict:
    for s in registry(cfg):
        if s["id"] == style_id:
            return s
    raise KeyError(style_id)


def choose(category: str, history: list[dict], fmt: str = "short", assigned: str | None = None,
           performance: dict[str, float] | None = None, cfg: dict | None = None) -> tuple[dict, str]:
    """history: ProductionManifest rows (newest last). Returns (style, reason)."""
    c = cfg or conf.load("styles")
    styles = c["styles"]
    if assigned:
        return by_id(assigned, c), f"experiment assignment {assigned}"
    same_fmt = [{**h, "style_id": str(h["style_id"]).split("@")[0]} for h in history
                if h.get("format", "short") == fmt and h.get("style_id")]
    recent = [h["style_id"] for h in same_fmt[-c.get("recent_window", 4):]]
    last_used = {h["style_id"]: i for i, h in enumerate(same_fmt)}
    cat = (category or "").lower()
    suitable = [s for s in styles if cat in [x.lower() for x in s["categories"]]] or styles
    fresh = [s for s in suitable if s["id"] not in recent] or [s for s in styles if s["id"] not in recent] or suitable
    perf = performance or {}
    fresh.sort(key=lambda s: (last_used.get(s["id"], -1), -perf.get(s["id"], 0.0), s["id"]))
    pick = fresh[0]
    why = f"suits '{category}'" if pick in suitable else "no suitable unused style; closest unused"
    return pick, f"{why}; not used in the last {len(recent)} {fmt} videos"


def apply(storyboard: dict, style: dict) -> dict:
    out = dict(storyboard)
    out["style_id"] = f"{style['id']}@v{style.get('version', 1)}"
    out["palette"] = style["palette"]
    out["style"] = {"transitions": style["transitions"], "title": style["title"], "captions": style["captions"]}
    return out
