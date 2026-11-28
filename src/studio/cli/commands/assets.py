"""`studio` commands: assets."""
from __future__ import annotations

from pathlib import Path
from studio.core.config import RUNTIME_DIR
from studio.orchestration.context import Ctx
import json
import sys

from studio.cli.common import (fit_json, LIBRARY, _style_folder, _style_notes, _sync_brand, out)


def cmd_import_audio(ctx: Ctx, a) -> int:
    """Licensed SFX + music (CC0 / CC BY) from Stickman Video Director's audio library into
    runtime/library (merged into the manifests; already-downloaded files are kept)."""
    from studio.audio import library as audio_library
    out({"ok": True, **audio_library.fetch(LIBRARY, log=lambda m: print(m, file=sys.stderr))})
    return 0


def cmd_sync_library(ctx: Ctx, a) -> int:
    """Copy SFX/Music library folders (with manifest.json) from Drive to runtime/library/."""
    got = {}
    for key, sub in (("references_sfx", "sfx"), ("references_music", "music")):
        dest = LIBRARY / sub
        dest.mkdir(parents=True, exist_ok=True)
        files = ctx.drive.list_folder(key, limit=100)
        names = {f["name"]: f["id"] for f in files}
        if "manifest.json" not in names:
            got[sub] = "no manifest.json (procedural fallback will be used)"
            continue
        ctx.drive.download(names["manifest.json"], dest / "manifest.json")
        wanted = {e["file"] for e in json.loads((dest / "manifest.json").read_text()).get("assets", [])}
        n = 0
        for nm in wanted & set(names):
            if not (dest / nm).exists():
                ctx.drive.download(names[nm], dest / Path(nm).name)
                n += 1
        got[sub] = f"{n} new files"
    try:
        got["brand"] = _sync_brand(ctx)
    except Exception as e:                       # branding is optional; never block a video
        got["brand"] = f"unavailable ({type(e).__name__}: {str(e)[:120]})"
    out({"ok": True, "library": got})
    return 0


def cmd_analyze_reference(ctx: Ctx, a) -> int:
    """Analyse reference videos (Drive References/Style, or --dir) for the director. Compact output;
    contact sheets are written to runtime/reference/ for the director to view."""
    from studio.video import reference as ref
    out_dir = RUNTIME_DIR / "reference"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(a.dir) if a.dir else out_dir / "videos"
    try:
        known = json.loads(ctx.store.get_config("reference_analysis", "") or "{}")
    except ValueError:
        known = {}
    new_names: set[str] = set()
    if not a.dir:
        src.mkdir(exist_ok=True)
        style = _style_folder(ctx)
        q = f"'{style}' in parents and trashed = false"
        for f in ctx.drive.svc.files().list(q=q, fields="files(id,name,size)", pageSize=50).execute().get("files", []):
            if f["id"] in known and not a.all:
                continue                         # analysed in an earlier run (results kept in the sheet)
            new_names.add(Path(f["name"]).stem.split(".")[0])
            if f["name"].lower().endswith((".mp4", ".webm", ".mkv", ".mov", ".vtt")) and not (src / f["name"]).exists():
                req = ctx.drive.svc.files().get_media(fileId=f["id"])
                from googleapiclient.http import MediaIoBaseDownload
                with open(src / f["name"], "wb") as fh:
                    dl, done = MediaIoBaseDownload(fh, req, chunksize=16 * 1024 * 1024), False
                    while not done:
                        _, done = dl.next_chunk(num_retries=3)
                if f["name"].lower().endswith((".mp4", ".webm", ".mkv", ".mov")):
                    known[f["id"]] = {"file": f["name"]}
    vids = sorted(p for p in src.iterdir() if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")
                  and (a.dir or p.stem.split(".")[0] in new_names))
    if not vids:
        out({"ok": True, "new": 0, "style_notes": _style_notes(ctx),
             "note": "no new reference videos (owner: put them in Drive YouTubeAutomation/01_Inbox/References/Style)"})
        return 0
    results = [ref.analyze(v, out_dir) for v in vids[: a.limit]]
    ref.save(results, out_dir / "analysis.json")
    by_name = {r["file"]: r for r in results}
    for fid, k in known.items():
        r = by_name.get(k.get("file"))
        if r:
            known[fid] = {x: r.get(x) for x in ("file", "duration", "size", "cuts_per_min", "avg_shot_s",
                                               "first_cut_s", "wpm", "hook_first_3s", "lufs")}
    if not a.dir:
        ctx.store.set_config("reference_analysis", fit_json(known))
    out({"ok": True, "new": len(results), "videos": results, "style_notes": _style_notes(ctx),
         "next": "director: look at each contact_sheet, then add 1–3 concrete notes with "
                 "`python -m studio style-note --add \"…\"` (shot length, text style, transitions, hook)"})
    return 0


def cmd_style_note(ctx: Ctx, a) -> int:
    """Director's learnings from reference videos; shown in every `discover` (last 20 kept)."""
    notes = _style_notes(ctx)
    if a.add:
        notes = (notes + [a.add.strip()[:300]])[-20:]
        ctx.store.set_config("style_notes", json.dumps(notes))
    out({"ok": True, "style_notes": notes})
    return 0


def register(sub) -> None:
    sub.add_parser("sync-library")
    sub.add_parser("import-audio")
    p = sub.add_parser("analyze-reference"); p.add_argument("--dir"); p.add_argument("--limit", type=int, default=6)
    p.add_argument("--all", action="store_true", help="re-analyse references already analysed")
    p = sub.add_parser("style-note"); p.add_argument("--add")
