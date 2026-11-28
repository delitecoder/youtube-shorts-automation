"""Deterministic render manifests: every input that can change the output, hashed.

Inputs: canonical storyboard JSON, each voice clip, assets referenced by the storyboard, custom scene code,
renderer package lock, the audio/video config, engine and music/sfx flags, plus the studio code version of
the render/audio modules. Same manifest + earlier QC pass + output present => the render is reused.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from studio.core.config import ROOT

CODE = ("src/studio/video/render.py", "src/studio/video/hyperframes.py", "src/studio/audio/stems.py",
        "src/studio/audio/cues.py", "src/studio/audio/mix.py", "src/studio/animation/world.py",
        "src/studio/animation/stickman.py", "renderer/hyperframes/package-lock.json",
        "renderer/hyperframes/three_scenes.js", "config/audio.yaml", "config/video.yaml", "config/pronunciation.yaml")


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file(p: Path) -> str:
    return _h(p.read_bytes()) if p.exists() else "missing"


def build_manifest(storyboard: dict, scene_audio: list[Path], workdir: Path, engine: str, music: bool, sfx: bool) -> dict:
    assets = sorted({str(s["visual"].get("asset")) for s in storyboard.get("scenes", []) if s.get("visual", {}).get("asset")})
    scenes_dir = workdir / "scenes"
    custom = sorted(scenes_dir.glob("*")) if scenes_dir.exists() else []
    inputs = {
        "storyboard": _h(json.dumps(storyboard, sort_keys=True, ensure_ascii=False).encode()),
        "voice": [_file(Path(p)) for p in scene_audio],
        "assets": {a: _file(workdir / "assets" / Path(a).name) for a in assets},
        "custom_scenes": {p.name: _file(p) for p in custom if p.is_file()},
        "code": {c: _file(ROOT / c) for c in CODE},
        "engine": engine, "music": music, "sfx": sfx,
    }
    return {"manifest_sha256": _h(json.dumps(inputs, sort_keys=True).encode()), "inputs": inputs}


def cached_report(manifest: dict, workdir: Path, out_mp4: Path) -> dict | None:
    prev = workdir / "render_manifest.json"
    timings = workdir / "timings.json"
    if not (prev.exists() and timings.exists() and out_mp4.exists()):
        return None
    old = json.loads(prev.read_text())
    if old.get("manifest_sha256") != manifest["manifest_sha256"] or not old.get("qc_ok"):
        return None
    report = json.loads(timings.read_text()).get("qc") or {}
    return {**report, "cached": True} if report.get("ok") else None
