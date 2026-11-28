"""Content memory + originality checks (rules.md §1.4, §3.3).

Stored per submitted video in the ContentMemory tab: title, hook, script and per-scene MinHash signatures,
scene-signature sequence, custom animation code signatures, music track, thumbnail style.
check() compares a new storyboard against memory and returns findings:
- block:  near-copied script (Jaccard >= 0.6), identical hook, near-copied custom animation code (>= 0.6),
          a reused run of >= 3 identical scene signatures;
- review: similar hook (>= 0.5), similar scene text, same music bed as one of the last 3 videos.
Teaching the same concept differently is NOT blocked: only near-verbatim reuse is.
Tamil localisations reuse their English video's scenes by design and are exempt.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from studio.core.state import iso, utcnow

NPERM = 64
_P = (1 << 61) - 1
_SEEDS = [(int(hashlib.sha256(f"a{i}".encode()).hexdigest()[:15], 16) | 1,
           int(hashlib.sha256(f"b{i}".encode()).hexdigest()[:15], 16)) for i in range(NPERM)]


def shingles(text: str, k: int = 5, code: bool = False) -> set[int]:
    if code:
        toks = re.findall(r"[A-Za-z_]+|[{}();=<>+*/-]", re.sub(r"#[0-9a-fA-F]{3,8}|\d+(\.\d+)?", "N", text))
    else:
        toks = re.findall(r"[a-z0-9]+", text.lower())
    if len(toks) < k:
        toks = toks + [""] * (k - len(toks))
    return {int(hashlib.blake2b(" ".join(toks[i:i + k]).encode(), digest_size=8).hexdigest(), 16)
            for i in range(len(toks) - k + 1)}


def minhash(sh: set[int]) -> list[int]:
    if not sh:
        return [0] * NPERM
    return [min((a * x + b) % _P for x in sh) for a, b in _SEEDS]


def similarity(a: list[int], b: list[int]) -> float:
    if not a or not b or not any(a) or not any(b):
        return 0.0
    return sum(x == y for x, y in zip(a, b)) / NPERM


def _enc(sig: list[int]) -> str:
    return ",".join(format(x, "x") for x in sig)


def _dec(s: str) -> list[int]:
    return [int(x, 16) for x in s.split(",")] if s else []


def scene_signature(scene: dict) -> str:
    v = scene.get("visual", {}) or {}
    keys = ("type", "template", "location", "camera", "layout", "kind", "chart")
    sig = {k: v.get(k) for k in keys if v.get(k)}
    if v.get("type") == "story":
        sig["cast"] = sorted(c.get("id", "") for c in v.get("cast", []))
        sig["actions"] = sorted({a.get("pose", "") for c in v.get("cast", []) for a in (c.get("actions") or [])})
    if v.get("type") == "custom":
        sig["code"] = v.get("code")
    return hashlib.sha1(json.dumps(sig, sort_keys=True).encode()).hexdigest()[:12]


def _script(sb: dict) -> str:
    return " ".join(s.get("narration", "") for s in sb.get("scenes", []) if not s.get("outro"))


def _hook(sb: dict) -> str:
    hooks = sb.get("hooks") or []
    i = sb.get("chosen_hook", 0)
    return hooks[i] if isinstance(i, int) and 0 <= i < len(hooks) else (sb.get("scenes") or [{}])[0].get("narration", "")


def _code_sigs(scenes_dir: Path | None) -> dict[str, list[int]]:
    if not scenes_dir or not scenes_dir.exists():
        return {}
    by_scene: dict[str, str] = {}
    for p in sorted(scenes_dir.glob("*.*")):
        if p.suffix in (".html", ".js"):
            by_scene[p.stem] = by_scene.get(p.stem, "") + p.read_text(errors="ignore")
    return {k: minhash(shingles(v, code=True)) for k, v in by_scene.items()}


def record(store, video_id: str, sb: dict, scenes_dir: Path | None = None, music: str = "",
           thumbnail_style: str = "") -> int:
    """Append this video's memory rows. Returns rows written."""
    now = iso(utcnow())
    rows = [("title", (sb.get("titles") or [""])[sb.get("chosen_title", 0) if isinstance(sb.get("chosen_title"), int) else 0],
             ""),
            ("hook", _hook(sb), _enc(minhash(shingles(_hook(sb), k=3)))),
            ("script", _script(sb)[:500], _enc(minhash(shingles(_script(sb))))),
            ("scene_sequence", "", "|".join(scene_signature(s) for s in sb.get("scenes", []) if not s.get("outro"))),
            ("music", music, ""), ("thumbnail_style", thumbnail_style, "")]
    rows += [(f"code:{k}", "", _enc(v)) for k, v in _code_sigs(scenes_dir).items()]
    for kind, text, fp in rows:
        store.append("ContentMemory", {"recorded_at": now, "video_id": video_id, "kind": kind.split(":")[0],
                                       "key": kind, "fingerprint": fp, "text": str(text)[:500]})
    return len(rows)


def _longest_common_run(a: list[str], b: list[str]) -> int:
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best


def check(store, sb: dict, scenes_dir: Path | None = None, music: str = "", exclude_video: str = "") -> dict:
    if sb.get("language", "en") != "en" or str(sb.get("source_video_id", "")):
        return {"block": [], "review": [], "exempt": "localisation reuses its English scenes"}
    mem = [r for r in store.rows("ContentMemory") if r.get("video_id") != exclude_video]
    block, review = [], []
    hook, script = _hook(sb), _script(sb)
    hook_sig, script_sig = minhash(shingles(hook, k=3)), minhash(shingles(script))
    seq = [scene_signature(s) for s in sb.get("scenes", []) if not s.get("outro")]
    codes = _code_sigs(scenes_dir)
    recent_music = [r["text"] for r in mem if r.get("kind") == "music" and r.get("text")][-3:]
    for r in mem:
        vid, kind = r.get("video_id"), r.get("kind")
        if kind == "hook":
            if r.get("text", "").strip().lower() == hook.strip().lower() and hook.strip():
                block.append(f"identical hook to {vid}")
            elif (s := similarity(hook_sig, _dec(r.get("fingerprint", "")))) >= 0.5:
                review.append(f"hook {s:.0%} similar to {vid}: {r.get('text', '')[:60]!r}")
        elif kind == "script" and (s := similarity(script_sig, _dec(r.get("fingerprint", "")))) >= 0.6:
            block.append(f"script {s:.0%} similar to {vid} (near-verbatim reuse)")
        elif kind == "scene_sequence" and r.get("fingerprint"):
            run = _longest_common_run(seq, r["fingerprint"].split("|"))
            if run >= 3:
                block.append(f"reuses a run of {run} identical scene layouts from {vid}")
        elif kind == "code":
            for name, sig in codes.items():
                if (s := similarity(sig, _dec(r.get("fingerprint", "")))) >= 0.6:
                    block.append(f"custom scene {name} is a {s:.0%} near-copy of {vid}/{r['key'].split(':', 1)[-1]}")
    if music and music in recent_music:
        review.append(f"music bed {music!r} was used in one of the last 3 videos")
    return {"block": sorted(set(block)), "review": sorted(set(review))}
