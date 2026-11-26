"""Licensed SFX + music from Stickman Video Director's audio library (CC0 / CC BY 4.0 recordings,
each with its own license evidence; https://github.com/kaomei/stickman-video-director, MIT code).

`fetch()` downloads a curated selection (pinned commit) into runtime/library/{sfx,music} and merges
manifest.json entries {file, category, source (attribution), license, mood}. Only recordings whose
catalog license allows commercial use and modification are taken. CC0 needs no credit; CC BY
credits are added to the video description automatically (attributions.json → credits block).
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

COMMIT = "10287822ba47579321068da1b93801face9160dd"
BASE = (f"https://raw.githubusercontent.com/kaomei/stickman-video-director/{COMMIT}/"
        "skills/directing-stickman-videos/assets/b-audio-library/")

# our cue kind -> (catalog categories, words in id/title/tags), max files
SFX_RULES = {
    "whoosh": (("whoosh", "ui-transition"), ("whoosh", "swish", "swoosh"), 5),
    "pop": (("ui-feedback",), ("pop", "bubble", "bong", "drop", "pluck", "maximize"), 4),
    "tick": (("ui-feedback",), ("click", "tick", "tap"), 4),
    "impact": (("impact",), ("impact", "punch", "thud"), 4),
    "chime": (("musical-stinger",), ("positive", "success", "win", "jingle", "correct"), 4),
    "fail": (("digital-game", "musical-stinger"), ("high-down", "low-down", "phaserdown", "fail", "lose"), 3),
    "success": (("digital-game", "musical-stinger"), ("high-up", "powerup", "phaserup", "success", "win"), 3),
    "footstep": (("footstep", "footsteps"), ("footstep", "step", "walk"), 4),
    "typing": (("ui-feedback",), ("type", "typing", "keyboard", "click"), 3),
    "notify": (("ui-feedback", "digital-game"), ("notification", "alert", "beep", "ping", "confirm"), 3),
    "door": (("door-wood", "door-foley"), ("door",), 2),
    "money": (("money-foley",), ("coin", "cash", "money"), 2),
    "amb_machine": (("machine-ambience",), ("machine", "hum", "fan", "computer"), 2),
    "amb_city": (("general-ambience",), ("street", "city", "traffic", "crowd", "ambient"), 2),
}
# music mood -> catalog ids (Kevin MacLeod / incompetech, CC BY 4.0), chosen for voice-over stories
MUSIC_MOODS = {
    "curious": ("bgm-investigations", "bgm-sneaky-snitch", "bgm-cipher"),
    "upbeat": ("bgm-carefree", "bgm-getting-it-done", "bgm-jaunty-gumption"),
    "calm": ("bgm-easy-lemon", "bgm-morning", "bgm-impact-moderato"),
    "tense": ("bgm-decisions",),
    "fun": ("bgm-marty-gots-a-plan", "bgm-quirky-dog"),
}


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ytauto-audio-import"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # nosec - pinned public GitHub content
        return r.read()


def _ok_license(lic: dict) -> bool:
    return bool(lic.get("commercial_use") and lic.get("modifications_allowed") and lic.get("id") in ("CC0-1.0", "CC-BY-4.0", "CC-BY-3.0"))


def select(catalog: list[dict]) -> list[tuple[dict, str, str]]:
    """[(asset, our category, mood)] — deterministic choice from the catalog."""
    out, used = [], set()
    for kind, (cats, words, n) in SFX_RULES.items():
        hits = []
        for a in catalog:
            if a.get("kind") == "music" or not _ok_license(a.get("license", {})) or a["id"] in used:
                continue
            text = " ".join([a["id"], a.get("title", "")] + a.get("tags", [])).lower()
            if a.get("category") in cats and any(w in text for w in words) and a.get("duration", 9) <= 12:
                hits.append(a)
        for a in sorted(hits, key=lambda x: (x.get("duration", 0) > 4, x["id"]))[:n]:
            used.add(a["id"])
            out.append((a, kind, ""))
    by_id = {a["id"]: a for a in catalog}
    for mood, ids in MUSIC_MOODS.items():
        for i in ids:
            a = by_id.get(i)
            if a and _ok_license(a.get("license", {})):
                out.append((a, "music", mood))
    return out


def fetch(library_dir: Path, log=print) -> dict:
    catalog = json.loads(_get(BASE + "catalog.json"))["assets"]
    counts = {"sfx": 0, "music": 0, "cached": 0}
    for sub in ("sfx", "music"):
        (library_dir / sub).mkdir(parents=True, exist_ok=True)
    manifests = {}
    for sub in ("sfx", "music"):
        mf = library_dir / sub / "manifest.json"
        try:
            manifests[sub] = json.loads(mf.read_text())
        except (OSError, ValueError):
            manifests[sub] = {"assets": []}
    for a, kind, mood in select(catalog):
        sub = "music" if kind == "music" else "sfx"
        name = f"svd_{a['id']}{Path(a['path']).suffix}"
        dest = library_dir / sub / name
        if dest.exists():
            counts["cached"] += 1
        else:
            try:
                dest.write_bytes(_get(BASE + a["path"]))
            except Exception as ex:  # noqa: BLE001 - one missing file must not stop the import
                log(f"audio import: skip {a['id']}: {str(ex)[:80]}")
                continue
            counts[sub] += 1
        lic = a["license"]
        entry = {"file": name, "category": kind, "mood": mood, "source": lic.get("attribution") or a.get("author", ""),
                 "license": lic["id"], "attribution_required": bool(lic.get("attribution_required"))}
        assets = [x for x in manifests[sub]["assets"] if x.get("file") != name]
        manifests[sub]["assets"] = assets + [entry]
    for sub, m in manifests.items():
        (library_dir / sub / "manifest.json").write_text(json.dumps(m, indent=1))
    counts["total"] = sum(len(m["assets"]) for m in manifests.values())
    return counts
