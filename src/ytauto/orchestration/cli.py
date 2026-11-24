"""Deterministic commands used by the daily Claude Code task (see CLAUDE.md).

Every command prints one compact JSON object to stdout, so agents can pass structured data on.
Model calls never happen here.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import RUNTIME_DIR
from ..integrations.drive import sha256_file
from ..integrations.google_auth import MissingCredentials
from ..production import storyboard as sbmod
from ..production.source_video import assess
from ..publishing.schedule import next_slot, rfc3339
from ..research.validate import validate_research
from ..state import ACTIVE_STATES, State, VideoRecord, iso, parse_iso, utcnow
from ..store import log_error, next_video_id
from .context import Ctx

WORK = RUNTIME_DIR / "work"


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, default=str))


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text())


def _today_local(ctx):
    return utcnow().astimezone(ZoneInfo(ctx.s.timezone)).date()


# ---------------------------------------------------------------- commands
def cmd_doctor(ctx: Ctx, a) -> int:
    import os
    checks = {
        "flags": ctx.s.describe_flags(),
        "approver_email_set": bool(ctx.s.approver_email),
        "spreadsheet_id_set": bool(ctx.s.spreadsheet_id),
        "drive_folders_set": sorted(ctx.s.folders),
        "pipeline_oauth_present": bool(os.environ.get("YTA_PIPELINE_OAUTH")),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "tts_provider": ctx.s.tts_provider,
        "tts_budget": ctx.s.tts_monthly_char_budget,
    }
    blockers = []
    if not checks["ffmpeg"]:
        blockers.append("ffmpeg missing")
    if not ctx.s.dry_run:
        for k in ("approver_email_set", "spreadsheet_id_set", "pipeline_oauth_present"):
            if not checks[k]:
                blockers.append(k.replace("_set", "").replace("_present", "") + " missing")
        if len(ctx.s.folders) < 10:
            blockers.append("DRIVE_FOLDER_IDS incomplete")
    if ctx.s.upload_enabled:
        blockers.append("UPLOAD_ENABLED must be false in the pipeline environment (publisher only)")
    out({"ok": not blockers, "blockers": blockers, "checks": checks})
    return 0 if not blockers else 2


def cmd_init(ctx: Ctx, a) -> int:
    if ctx.s.dry_run:
        out({"ok": False, "error": "init creates Drive folders and a spreadsheet; run with DRY_RUN=false"})
        return 2
    from ..integrations.drive import Drive
    from ..integrations.google_auth import build_service
    from ..integrations.sheets import SheetsStore
    drive = Drive(build_service("drive", "v3", "pipeline"), ctx.s)
    ids = drive.ensure_structure()
    sid = ctx.s.spreadsheet_id or SheetsStore.create_spreadsheet(build_service("sheets", "v4", "pipeline"))
    SheetsStore(build_service("sheets", "v4", "pipeline"), sid).ensure_schema()
    out({"ok": True, "SPREADSHEET_ID": sid, "DRIVE_FOLDER_IDS": ids})
    return 0


def cmd_discover(ctx: Ctx, a) -> int:
    recs = ctx.store.all()
    pending = [r.video_id for r in recs if r.state in ACTIVE_STATES]
    if pending:
        out({"action": "none", "reason": f"item(s) still in progress: {pending}"})
        return 0
    today = _today_local(ctx).strftime("%Y%m%d")
    if any(r.video_id.startswith(f"VIDEO-{today}-") for r in recs) and not a.force:
        out({"action": "none", "reason": "already produced an item today (one per day)"})
        return 0
    seen = {r.source_file_id for r in recs if r.source_file_id} | {r.sha256 for r in recs if r.sha256}
    try:
        for f in ctx.drive.list_folder("inbox_videos", "video/"):
            if f["id"] not in seen and f.get("sha256Checksum") not in seen:
                out({"action": "existing_video", "file": {k: f.get(k) for k in
                     ("id", "name", "size", "mimeType", "description", "sha256Checksum")}})
                return 0
        docs = [f for f in ctx.drive.list_folder("inbox_documents") if f["id"] not in seen]
        refs = [{"id": f["id"], "name": f["name"]} for f in ctx.drive.list_folder("inbox_references")][:10]
        if docs:
            out({"action": "document", "file": {"id": docs[0]["id"], "name": docs[0]["name"],
                                                 "mimeType": docs[0]["mimeType"]}, "references": refs})
            return 0
    except (MissingCredentials, RuntimeError) as e:
        if not ctx.s.dry_run:
            raise
        out({"action": "research", "note": f"Drive unavailable in dry run: {e}"})
        return 0
    out({"action": "research", "note": "no unprocessed Drive items"})
    return 0


def _new_record(ctx: Ctx, source_type: str, source_file_id: str = "") -> VideoRecord:
    vid = next_video_id([r.video_id for r in ctx.store.all()], utcnow(), ctx.s.timezone)
    return ctx.store.create(VideoRecord(video_id=vid, source_type=source_type,
                                        source_file_id=source_file_id))


def cmd_new_item(ctx: Ctx, a) -> int:
    rec = _new_record(ctx, "GENERATED", a.source_file_id or "")
    out({"video_id": rec.video_id, "state": rec.state.value})
    return 0


def cmd_prepare_existing(ctx: Ctx, a) -> int:
    if a.local_path:                                  # dry-run/testing path
        path, desc, file_id = Path(a.local_path), a.description or "", a.file_id or "local"
        digest = sha256_file(path)
    else:
        meta = ctx.drive.metadata(a.file_id)
        desc, file_id = meta.get("description", ""), a.file_id
        path, digest = ctx.drive.download(a.file_id, WORK / "src" / meta["name"])
        if meta.get("sha256Checksum") and meta["sha256Checksum"] != digest:
            out({"ok": False, "error": "download checksum differs from Drive checksum"})
            return 2
    dup = [r.video_id for r in ctx.store.all() if r.sha256 == digest]
    if dup:
        out({"ok": False, "duplicate_of": dup})
        return 0
    before = sha256_file(path)
    el = assess(path, desc, ctx.s.max_source_video_seconds, ctx.s.max_source_video_bytes)
    assert sha256_file(path) == before, "source bytes changed during assessment"
    rec = _new_record(ctx, "EXISTING", file_id)
    info = el.info
    rec = ctx.store.update_fields(rec.video_id, rec.version, drive_file_id=file_id, file_name=path.name,
                                  sha256=digest, duration_s=str(round(info.get("duration", 0), 2)),
                                  width=str(info.get("display", [0, 0])[0]),
                                  height=str(info.get("display", [0, 0])[1]))
    if el.ok:
        rec = ctx.store.transition(rec.video_id, State.DISCOVERED, State.QUALITY_CHECK, rec.version,
                                   quality_report=json.dumps({"eligibility": el.warnings, "rights": el.rights}))
    else:
        rec = ctx.store.transition(rec.video_id, State.DISCOVERED, State.NEEDS_MANUAL_REVIEW, rec.version,
                                   notes="; ".join(el.reasons)[:500])
        log_error(ctx.store, "prepare-existing", "Source video needs manual review: " + "; ".join(el.reasons),
                  rec.video_id, "WARNING")
    out({"ok": el.ok, "video_id": rec.video_id, "state": rec.state.value, "sha256": digest,
         "reasons": el.reasons, "warnings": el.warnings, "duration": info.get("duration")})
    return 0


def cmd_doc_text(ctx: Ctx, a) -> int:
    out({"file_id": a.file_id, "text": ctx.drive.export_text(a.file_id, a.max_chars)})
    return 0


def cmd_record_research(ctx: Ctx, a) -> int:
    r = load_json(a.file)
    rec = ctx.store.get(a.video_id)
    errs, warns = validate_research(r, _today_local(ctx), ctx.store.rows("ResearchHistory"))
    if errs:
        out({"ok": False, "errors": errs, "warnings": warns})
        return 2
    ctx.store.append("ResearchHistory", {"researched_at": iso(utcnow()), "video_id": a.video_id,
                                         "topic": r["topic"], "topic_key": r["topic_key"],
                                         "category": r["category"], "sources": json.dumps(r["sources"]),
                                         "summary": r["angle"][:500]})
    if rec.state == State.DISCOVERED:
        ctx.store.transition(a.video_id, State.DISCOVERED, State.RESEARCHING, rec.version)
    out({"ok": True, "warnings": warns})
    return 0


def cmd_validate_storyboard(ctx: Ctx, a) -> int:
    sb = load_json(a.file)
    errs, warns = sbmod.validate_storyboard(sb, require_sources=not a.document_based)
    out({"ok": not errs, "errors": errs, "warnings": warns, "estimate": sbmod.estimate(sb)})
    return 0 if not errs else 2


LIBRARY = RUNTIME_DIR / "library"


def cmd_render(ctx: Ctx, a) -> int:
    from ..integrations.tts import synthesize_all
    from ..production.render import render
    sb = load_json(a.storyboard)
    errs, _ = sbmod.validate_storyboard(sb, require_sources=not a.document_based)
    rec = ctx.store.get(a.video_id)
    wd = WORK / rec.video_id
    for i, s in enumerate(sb["scenes"], 1):
        v = s["visual"]
        if v["type"] == "image" and not (wd / "assets" / Path(v["asset"]).name).exists():
            errs.append(f"scene {i}: image asset {v['asset']} missing (run resolve-images / fetch-asset)")
    if errs:
        out({"ok": False, "errors": errs})
        return 2
    if sb["video_id"] != rec.video_id:
        out({"ok": False, "error": "storyboard video_id mismatch"})
        return 2
    allowed = {State.RESEARCHING, State.QUALITY_CHECK, State.GENERATING}
    if rec.state not in allowed:
        out({"ok": False, "error": f"cannot render in state {rec.state.value}"})
        return 2
    if rec.state != State.GENERATING:
        rec = ctx.store.transition(rec.video_id, rec.state, State.GENERATING, rec.version)
    wd.mkdir(parents=True, exist_ok=True)
    full = sbmod.with_outro(sb, ctx.s.outro_text) if ctx.s.outro_enabled else sb
    texts = [s["narration"] for s in full["scenes"]]
    try:
        clips, provider, fallbacks = synthesize_all(texts, wd, ctx.s, ctx.store,
                                                    log=lambda m: print(m, file=sys.stderr))
        report = render(full, clips, wd, wd / f"{rec.video_id}.mp4", music=ctx.s.music_enabled,
                        sfx=ctx.s.sfx_enabled, sfx_dir=LIBRARY / "sfx", music_dir=LIBRARY / "music")
    except Exception as e:  # noqa: BLE001
        log_error(ctx.store, "render", str(e), rec.video_id)
        out({"ok": False, "error": str(e)[:500], "hint": "fix and re-run render (state stays GENERATING)"})
        return 2
    report |= {"voice": provider, "voice_fallbacks": fallbacks}
    if not report["ok"]:
        out({"ok": False, "qc": report})
        return 2
    rec = ctx.store.transition(rec.video_id, State.GENERATING, State.QUALITY_CHECK, rec.version,
                               duration_s=str(report["duration"]), width="1080", height="1920",
                               hook=sb["hooks"][sb["chosen_hook"]],
                               references=json.dumps(sb.get("references", [])),
                               quality_report=json.dumps({"render_qc": report}))
    out({"ok": True, "video": str(wd / f"{rec.video_id}.mp4"), "voice": provider,
         "fallbacks": fallbacks, "qc": {k: report[k] for k in ("ok", "duration", "lufs")},
         "tts_chars": sum(len(t) for t in texts)})
    return 0


def cmd_resolve_images(ctx: Ctx, a) -> int:
    """Find licensed internet images for `image` scenes that have a `query` but no file yet.
    Unresolvable scenes fall back to a title card, so rendering never blocks on images."""
    import requests

    from ..production.vision import make_describer, summarize
    from ..research import images as im
    sb = load_json(a.storyboard)
    wd = WORK / sb["video_id"]
    (wd / "assets").mkdir(parents=True, exist_ok=True)
    describer, session = make_describer(ctx.store), requests.Session()
    credits_path = wd / "image_credits.json"
    credits = json.loads(credits_path.read_text()) if credits_path.exists() else []
    report = []
    for i, s in enumerate(sb["scenes"], 1):
        v = s["visual"]
        if v["type"] != "image" or (v.get("asset") and (wd / "assets" / Path(v["asset"]).name).exists()):
            continue
        q = v.get("query") or s.get("on_screen_text", "")
        cands = []
        for search in (im.search_commons, im.search_openverse):
            try:
                cands += im.filter_candidates(search(q, session))
            except Exception as e:  # noqa: BLE001 - network/API errors are not fatal
                report.append({"scene": i, "warning": f"{search.__name__}: {str(e)[:120]}"})
            if len(cands) >= 4:
                break
        best, notes = im.choose(q, cands, wd, f"scene{i}", session, describer) if cands else (None, [])
        if best:
            v["asset"] = best.file
            v.setdefault("caption", "")
            credits.append({"type": "image", "file": best.file, "credit": best.credit,
                            "creator": best.creator, "license": best.license, "page_url": best.page_url,
                            "needs_attribution": best.needs_attribution or best.requires_permission,
                            "requires_permission": best.requires_permission,
                            "visual_unverified": best.visual_unverified,
                            "description": best.caption, "vision": best.vision})
            # Claude only reads this compact line (~30-40 tokens); details stay in image_credits.json
            report.append({"s": i, "f": best.file, "lic": best.license[:16],
                           "perm": int(best.requires_permission), **summarize(best.vision or {})})
        else:
            s["visual"] = {"type": "title", "headline": s.get("on_screen_text") or q[:40]}
            report.append({"s": i, "fallback": "title"})
    credits_path.write_text(json.dumps(credits, indent=1))
    Path(a.storyboard).write_text(json.dumps(sb, indent=1, ensure_ascii=False))
    out({"ok": True, "describer": describer.name, "scenes": report})
    return 0


def cmd_fetch_asset(ctx: Ctx, a) -> int:
    """Your own image from Drive 01_Inbox/References/Images (needs RIGHTS: line in description)."""
    from ..production.source_video import rights_declaration
    meta = ctx.drive.metadata(a.file_id)
    if ctx.s.folders.get("references_images") not in meta.get("parents", []):
        out({"ok": False, "error": "file is not in References/Images"})
        return 2
    rights = rights_declaration(meta.get("description"))
    if not rights:
        out({"ok": False, "error": "add 'RIGHTS: OWNED' or 'RIGHTS: LICENSED <note>' to the Drive description"})
        return 2
    wd = WORK / a.video_id / "assets"
    name = f"{a.name}{Path(meta['name']).suffix.lower() or '.jpg'}"
    ctx.drive.download(a.file_id, wd / name)
    from PIL import Image
    with Image.open(wd / name) as img:
        img.verify()
    cp = WORK / a.video_id / "image_credits.json"
    credits = json.loads(cp.read_text()) if cp.exists() else []
    credits.append({"type": "image", "file": name, "credit": f"Own image ({rights})", "license": rights,
                    "needs_attribution": rights.startswith("LICENSED"), "visual_unverified": False})
    cp.write_text(json.dumps(credits, indent=1))
    out({"ok": True, "asset": name, "rights": rights})
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
    out({"ok": True, "library": got})
    return 0


def _credits_block(video_id: str) -> str:
    lines = []
    for f in ("attributions.json", "image_credits.json"):
        p = WORK / video_id / f
        for e in json.loads(p.read_text()) if p.exists() else []:
            if e.get("type") == "image" and e.get("requires_permission"):
                lines.append(f"Image: © {e.get('creator') or 'creator'} — {e['credit']} (used with permission)")
            elif e.get("type") == "image" and e.get("needs_attribution"):
                lines.append(f"Image: {e['credit']}")
            elif e.get("type") in ("sfx", "music") and e.get("source") and "procedural" not in e["source"]:
                lines.append(f"{e['type'].upper()}: {e['source']} ({e['license']})")
    return ("\n\nCredits:\n" + "\n".join(dict.fromkeys(lines))) if lines else ""


def cmd_submit(ctx: Ctx, a) -> int:
    rec = ctx.store.get(a.video_id)
    if rec.state != State.QUALITY_CHECK:
        out({"ok": False, "error": f"state is {rec.state.value}, need QUALITY_CHECK"})
        return 2
    meta, review = load_json(a.metadata), load_json(a.review)
    credits = _credits_block(rec.video_id)
    if credits and "Credits:" not in meta["description"]:
        meta["description"] = meta["description"].rstrip() + credits
    errs = sbmod.validate_metadata(meta)
    if review.get("verdict") != "pass":
        errs.append("quality review verdict is not 'pass'")
    if errs:
        out({"ok": False, "errors": errs})
        return 2
    now = utcnow()
    slot = rfc3339(next_slot(now + timedelta(hours=1), ctx.s.publish_time, ctx.s.timezone,
                             ctx.s.min_lead_minutes))
    qr = json.loads(rec.quality_report or "{}") | {"review": review}
    cp = WORK / rec.video_id / "image_credits.json"
    if cp.exists():
        qr["images"] = [{k: e.get(k) for k in ("file", "creator", "license", "page_url", "requires_permission",
                                                "visual_unverified", "description")}
                        for e in json.loads(cp.read_text())]
    fields = dict(title=meta["title"], description=meta["description"], tags=json.dumps(meta["tags"]),
                  pinned_comment=meta["pinned_comment"], hook=meta.get("hook", rec.hook),
                  quality_report=json.dumps(qr)[:45000], proposed_publish_at=slot)
    if rec.source_type == "GENERATED":
        path = Path(a.video or WORK / rec.video_id / f"{rec.video_id}.mp4")
        digest = sha256_file(path)
        up = ctx.external_write(f"upload {path.name} to 02_Generated", lambda: ctx.drive.upload(
            path, "generated", "video/mp4", f"{rec.video_id} generated by ytauto"))
        file_id = up["id"] if up else "dry-run"
        if up and up.get("sha256Checksum") and up["sha256Checksum"] != digest:
            out({"ok": False, "error": "Drive checksum differs after upload"})
            return 2
        fields |= dict(drive_file_id=file_id, file_name=path.name, sha256=digest)
    else:
        file_id = rec.drive_file_id
    ctx.external_write(f"move {file_id} to 03_Awaiting_Approval",
                       lambda: ctx.drive.move(file_id, "awaiting_approval"))
    fields["preview_url"] = f"https://drive.google.com/file/d/{file_id}/view"  # private: owner only
    rec = ctx.store.transition(rec.video_id, State.QUALITY_CHECK, State.AWAITING_APPROVAL, rec.version, **fields)
    out({"ok": True, "video_id": rec.video_id, "state": rec.state.value, "proposed_publish_at": slot,
         "note": "Apps Script sends the approval email within ~5 minutes" if not ctx.s.dry_run
         else "DRY_RUN: no email will be sent"})
    return 0


def _archive(ctx: Ctx, rec, done: list) -> None:
    """Move an uploaded video's Drive file into Archive (Apps Script normally did it already)."""
    if not rec.drive_file_id or rec.drive_file_id == "dry-run":
        return
    if ctx.s.dry_run:
        ctx.external_write(f"move {rec.drive_file_id} to Archive", lambda: None)
        return
    meta = ctx.drive.metadata(rec.drive_file_id)
    if ctx.s.folder("archive") not in meta.get("parents", []):
        ctx.drive.move(rec.drive_file_id, "archive")
        done.append({"video_id": rec.video_id, "moved": "Archive"})


def cmd_reconcile(ctx: Ctx, a) -> int:
    """Uploaded videos -> Drive Archive; SCHEDULED -> PUBLISHED once public; optional promotion."""
    done = []
    for rec in ctx.store.all():
        if rec.state in (State.SCHEDULED, State.PUBLISHED) and rec.youtube_video_id:
            try:
                _archive(ctx, rec, done)
            except Exception as e:  # noqa: BLE001
                log_error(ctx.store, "archive", str(e), rec.video_id, "WARNING")
        if rec.state == State.SCHEDULED and rec.youtube_video_id:
            st = ctx.youtube.video_status(rec.youtube_video_id)
            if st and st["status"].get("privacyStatus") == "public" and st["status"].get("uploadStatus") == "processed":
                r2 = ctx.store.transition(rec.video_id, State.SCHEDULED, State.PUBLISHED, rec.version,
                                          published_at=st["snippet"].get("publishedAt", iso(utcnow())))
                done.append({"video_id": rec.video_id, "state": "PUBLISHED"})
                if ctx.s.promotion_enabled and not ctx.s.dry_run:
                    from ..promotion import bluesky
                    try:
                        uri = bluesky.post(ctx.s.bluesky_handle, r2.title,
                                           f"https://youtube.com/shorts/{rec.youtube_video_id}",
                                           [t for t in r2.tag_list][:3])
                        done[-1]["bluesky"] = uri
                    except Exception as e:  # noqa: BLE001
                        log_error(ctx.store, "bluesky", str(e), rec.video_id, "WARNING")
        elif rec.state in (State.REJECTED, State.EXPIRED) and rec.drive_file_id and "moved" not in rec.notes:
            ctx.external_write(f"move {rec.drive_file_id} to 05_Rejected",
                               lambda r=rec: ctx.drive.move(r.drive_file_id, "rejected"))
            ctx.store.update_fields(rec.video_id, rec.version, notes=(rec.notes + " moved").strip())
            done.append({"video_id": rec.video_id, "moved": "05_Rejected"})
    out({"ok": True, "changes": done})
    return 0


def cmd_analytics(ctx: Ctx, a) -> int:
    from ..analytics import youtube_analytics as ya
    published = [r.to_row() for r in ctx.store.all() if r.state == State.PUBLISHED]
    n = 0
    if a.snapshot and published:
        n = ya.snapshot(ctx.store, ctx.yt_analytics, published)
    rows = ctx.store.rows("Analytics")
    titles = {r.youtube_video_id: r.title for r in ctx.store.all() if r.youtube_video_id}
    out({"snapshots_written": n, "summary": ya.summary(rows, titles),
         "publish_time": ya.recommend_publish_time(rows, ctx.s.timezone, ctx.s.publish_time)})
    return 0


def cmd_status(ctx: Ctx, a) -> int:
    recs = ctx.store.all()[-a.limit:]
    out({"flags": ctx.s.describe_flags(),
         "items": [{"video_id": r.video_id, "state": r.state.value, "title": r.title[:60],
                    "youtube": r.youtube_video_id} for r in recs]})
    return 0


def cmd_reddit_drafts(ctx: Ctx, a) -> int:
    from ..promotion import reddit_drafts
    rec = ctx.store.get(a.video_id)
    md = reddit_drafts.render(rec.video_id, f"https://youtube.com/shorts/{rec.youtube_video_id or 'PENDING'}",
                              reddit_drafts.load(a.file))
    p = WORK / rec.video_id / f"{rec.video_id}_reddit_drafts.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md)
    ctx.external_write(f"upload {p.name} to 04_Published",
                       lambda: ctx.drive.upload(p, "published", "text/markdown"))
    out({"ok": True, "file": str(p)})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ytauto")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("init")
    p = sub.add_parser("discover"); p.add_argument("--force", action="store_true")
    p = sub.add_parser("new-item"); p.add_argument("--source-file-id")
    p = sub.add_parser("prepare-existing"); p.add_argument("--file-id"); p.add_argument("--local-path")
    p.add_argument("--description")
    p = sub.add_parser("doc-text"); p.add_argument("--file-id", required=True); p.add_argument("--max-chars", type=int, default=20000)
    p = sub.add_parser("record-research"); p.add_argument("--video-id", required=True); p.add_argument("--file", required=True)
    p = sub.add_parser("validate-storyboard"); p.add_argument("--file", required=True); p.add_argument("--document-based", action="store_true")
    p = sub.add_parser("render"); p.add_argument("--video-id", required=True); p.add_argument("--storyboard", required=True)
    p.add_argument("--document-based", action="store_true")
    p = sub.add_parser("submit"); p.add_argument("--video-id", required=True); p.add_argument("--metadata", required=True)
    p.add_argument("--review", required=True); p.add_argument("--video")
    p = sub.add_parser("resolve-images"); p.add_argument("--storyboard", required=True)
    p = sub.add_parser("fetch-asset"); p.add_argument("--video-id", required=True); p.add_argument("--file-id", required=True)
    p.add_argument("--name", required=True)
    sub.add_parser("sync-library")
    sub.add_parser("reconcile")
    p = sub.add_parser("analytics"); p.add_argument("--snapshot", action="store_true")
    p = sub.add_parser("status"); p.add_argument("--limit", type=int, default=15)
    p = sub.add_parser("reddit-drafts"); p.add_argument("--video-id", required=True); p.add_argument("--file", required=True)
    a = ap.parse_args(argv)
    ctx = Ctx()
    fn = globals()["cmd_" + a.cmd.replace("-", "_")]
    try:
        return fn(ctx, a)
    except MissingCredentials as e:
        out({"ok": False, "error": str(e)})
        return 3


if __name__ == "__main__":
    sys.exit(main())
