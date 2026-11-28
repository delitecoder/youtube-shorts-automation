"""`studio` commands: planning."""
from __future__ import annotations

from pathlib import Path
from studio.core.google_auth import MissingCredentials
from studio.core.state import State
from studio.core.state import iso
from studio.core.state import utcnow
from studio.orchestration.context import Ctx
from studio.research.validate import validate_research
from studio.storage.drive import sha256_file
from studio.storage.store import is_long
from studio.storage.store import log_error
from studio.video.source_video import assess
import json

from studio.cli.common import (PIPELINE_STATES, WORK, _insights, _long_topic, _needs_localize, _new_record, _queued_future, _theme_today, _today_local, load_json, out)


def cmd_discover(ctx: Ctx, a) -> int:
    if getattr(a, "format", "short") == "long":
        return _discover_long(ctx, a)
    recs = [r for r in ctx.store.all() if not is_long(r.video_id)]
    # Unfinished production work (e.g. a run that ran out of tokens) is resumed first.
    resumable = [r for r in recs if r.state in PIPELINE_STATES]
    if resumable:
        r = sorted(resumable, key=lambda x: x.video_id)[0]
        out({"action": "resume", "video_id": r.video_id, "state": r.state.value,
             "next": "python -m studio restore --video-id " + r.video_id})
        return 0
    today = _today_local(ctx).strftime("%Y%m%d")
    loc = _needs_localize(ctx, recs, today)
    if loc:
        out(loc)
        return 0
    q = _queued_future(ctx, recs)
    if q and not a.force:
        out({"action": "none", "reason": f"{q} is already queued for an upcoming slot; making another would push every later video back a day"})
        return 0
    waiting_today = [r.video_id for r in recs if r.state == State.AWAITING_APPROVAL and r.video_id.startswith(f"VIDEO-{today}-")]
    if waiting_today:
        out({"action": "none", "reason": f"today's video is waiting for approval: {waiting_today}"})
        return 0
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
        out({"action": "research", "note": f"Drive unavailable in dry run: {e}",
             "theme": _theme_today(ctx), "insights": _insights(ctx)})
        return 0
    out({"action": "research", "note": "no unprocessed Drive items",
         "theme": _theme_today(ctx), "insights": _insights(ctx)})
    return 0


def _discover_long(ctx: Ctx, a) -> int:
    recs = [r for r in ctx.store.all() if is_long(r.video_id)]
    resumable = [r for r in recs if r.state in PIPELINE_STATES]
    if resumable:
        r = sorted(resumable, key=lambda x: x.video_id)[0]
        out({"action": "resume", "format": "long", "video_id": r.video_id, "state": r.state.value,
             "next": "python -m studio restore --video-id " + r.video_id})
        return 0
    today = _today_local(ctx).strftime("%Y%m%d")
    loc = _needs_localize(ctx, recs, today)
    if loc:
        out(dict(loc, format="long"))
        return 0
    q = _queued_future(ctx, recs)
    if q and not a.force:
        out({"action": "none", "format": "long", "reason": f"{q} is already queued for an upcoming slot"})
        return 0
    if any(r.video_id.startswith(f"VIDEO-{today}-") for r in recs) and not a.force:
        out({"action": "none", "format": "long", "reason": "already produced today's long video"})
        return 0
    ins = _insights(ctx)
    out({"action": "research", "format": "long", "theme": _long_topic(ctx, ins), "insights": ins})
    return 0


def cmd_new_item(ctx: Ctx, a) -> int:
    rec = _new_record(ctx, "GENERATED", a.source_file_id or "", long=getattr(a, "format", "short") == "long")
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
    errs, warns = validate_research(r, _today_local(ctx), ctx.store.rows("ResearchHistory"),
                                    long=is_long(a.video_id))
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


def register(sub) -> None:
    p = sub.add_parser("discover"); p.add_argument("--force", action="store_true")
    p.add_argument("--format", choices=["short", "long"], default="short")
    p = sub.add_parser("new-item"); p.add_argument("--source-file-id")
    p.add_argument("--format", choices=["short", "long"], default="short")
    p = sub.add_parser("prepare-existing"); p.add_argument("--file-id"); p.add_argument("--local-path")
    p.add_argument("--description")
    p = sub.add_parser("doc-text"); p.add_argument("--file-id", required=True); p.add_argument("--max-chars", type=int, default=20000)
    p = sub.add_parser("record-research"); p.add_argument("--video-id", required=True); p.add_argument("--file", required=True)
