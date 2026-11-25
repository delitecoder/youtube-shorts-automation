"""Deterministic commands used by the daily Claude Code task (see CLAUDE.md).

Every command prints one compact JSON object to stdout, so agents can pass structured data on.
Model calls never happen here.
"""
from __future__ import annotations

import argparse
import os
import re
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import LONG_THEME, RUNTIME_DIR, SYSTEM_DESIGN_SYLLABUS, theme_for
from ..integrations.drive import sha256_file
from ..integrations.google_auth import MissingCredentials
from ..production import storyboard as sbmod
from ..production.source_video import assess
from ..publishing.schedule import free_slot, next_slot, rfc3339
from ..research.validate import validate_research
from ..state import ACTIVE_STATES, State, VideoRecord, iso, parse_iso, utcnow
from ..store import is_long, log_error, next_video_id
from .context import Ctx

WORK = RUNTIME_DIR / "work"
ROOT_DIR = Path(__file__).resolve().parents[3]
PIPELINE_STATES = {State.DISCOVERED, State.RESEARCHING, State.GENERATING, State.QUALITY_CHECK}
CHECKPOINT_SKIP = (".wav",)      # voice/music are cheap to regenerate; everything else is kept


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
        "drive_keys": ctx.keys_report,
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
    setup_complete = all([checks["pipeline_oauth_present"], checks["approver_email_set"],
                          checks["spreadsheet_id_set"], len(ctx.s.folders) >= 10])
    out({"ok": not blockers, "setup_complete": setup_complete, "blockers": blockers, "checks": checks})
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


def _slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def rank_topics(topics: list[str], insights: dict) -> tuple[str, int]:
    """First topic with the most word overlap with viewers' search terms / suggested-next-to
    titles; ties keep syllabus order. Returns (topic, score)."""
    def toks(x):
        return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", x.lower()) if len(w) > 3}
    words = set()
    for x in insights.get("search_terms", []) + insights.get("suggested_next_to", []):
        words |= toks(x)
    stop = {"design", "system", "what", "when", "with", "from", "your", "into", "pres", "happen", "type"}
    score, _, t = max((len((toks(t) - stop) & words), -k, t) for k, t in enumerate(topics))
    return t, score


def _long_topic(ctx: Ctx, insights: dict | None = None) -> dict:
    """Today's system-design topic: a ThemePlan row "long: <topic>" wins, else the first syllabus
    topic not yet researched for a long video."""
    try:
        plan = ctx.store.rows("ThemePlan")
    except Exception:
        plan = []
    today = _today_local(ctx)
    from ..config import _plan_date
    for row in reversed(plan):
        th = str(row.get("theme", "")).strip()
        if _plan_date(row.get("date", "")) == today and th.lower().startswith("long:"):
            return {**LONG_THEME, "topic": th[5:].strip(), "details": row.get("details", ""), "source": "plan"}
    try:
        hist = ctx.store.rows("ResearchHistory")
    except Exception:
        hist = []
    done = {_slug(h.get("topic", "")) for h in hist} | {h.get("topic_key", "") for h in hist}
    left = [t for t in SYSTEM_DESIGN_SYLLABUS if _slug(t) not in done]
    if left:
        # Audience first: a remaining topic that matches what viewers search for / watch next to us
        t, score = rank_topics(left, insights or {})
        return {**LONG_THEME, "topic": t, "topic_key": _slug(t),
                "source": "audience match" if score else "syllabus order"}
    return {**LONG_THEME, "topic": "", "source": "syllabus finished: pick a new system-design topic "
            "(a real product people use) not in ResearchHistory"}


def cmd_discover(ctx: Ctx, a) -> int:
    if getattr(a, "format", "short") == "long":
        return _discover_long(ctx, a)
    recs = [r for r in ctx.store.all() if not is_long(r.video_id)]
    # Unfinished production work (e.g. a run that ran out of tokens) is resumed first.
    resumable = [r for r in recs if r.state in PIPELINE_STATES]
    if resumable:
        r = sorted(resumable, key=lambda x: x.video_id)[0]
        out({"action": "resume", "video_id": r.video_id, "state": r.state.value,
             "next": "python -m ytauto restore --video-id " + r.video_id})
        return 0
    today = _today_local(ctx).strftime("%Y%m%d")
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
             "next": "python -m ytauto restore --video-id " + r.video_id})
        return 0
    today = _today_local(ctx).strftime("%Y%m%d")
    if any(r.video_id.startswith(f"VIDEO-{today}-") for r in recs) and not a.force:
        out({"action": "none", "format": "long", "reason": "already produced today's long video"})
        return 0
    ins = _insights(ctx)
    out({"action": "research", "format": "long", "theme": _long_topic(ctx, ins), "insights": ins})
    return 0


def _theme_today(ctx: Ctx) -> dict:
    try:
        plan = ctx.store.rows("ThemePlan")
    except Exception:                      # tab missing / sheet unavailable: use the rotation
        plan = []
    return theme_for(_today_local(ctx), plan)


def _new_record(ctx: Ctx, source_type: str, source_file_id: str = "", long: bool = False) -> VideoRecord:
    vid = next_video_id([r.video_id for r in ctx.store.all()], utcnow(), ctx.s.timezone, long=long)
    return ctx.store.create(VideoRecord(video_id=vid, source_type=source_type,
                                        source_file_id=source_file_id))


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
    long_ = sbmod.is_long(sb)
    if long_ != is_long(rec.video_id):
        out({"ok": False, "error": "storyboard format does not match the video id (long ids are -101…-199)"})
        return 2
    outro = ctx.s.long_outro_text if long_ else ctx.s.outro_text
    full = sbmod.with_outro(sb, outro) if ctx.s.outro_enabled else sb
    texts = [s["narration"] for s in full["scenes"]]
    try:
        clips, provider, fallbacks = synthesize_all(texts, wd, ctx.s, ctx.store,
                                                    log=lambda m: print(m, file=sys.stderr))
        report = render(full, clips, wd, wd / f"{rec.video_id}.mp4", music=ctx.s.music_enabled,
                        sfx=ctx.s.sfx_enabled, sfx_dir=LIBRARY / "sfx", music_dir=LIBRARY / "music",
                        engine=os.environ.get("RENDER_ENGINE", "auto"))
    except Exception as e:  # noqa: BLE001
        log_error(ctx.store, "render", str(e), rec.video_id)
        out({"ok": False, "error": str(e)[:500], "hint": "fix and re-run render (state stays GENERATING)"})
        return 2
    report |= {"voice": provider, "voice_fallbacks": fallbacks}
    if not report["ok"]:
        out({"ok": False, "qc": report})
        return 2
    rec = ctx.store.transition(rec.video_id, State.GENERATING, State.QUALITY_CHECK, rec.version,
                               duration_s=str(report["duration"]), width="1920" if long_ else "1080",
                               height="1080" if long_ else "1920",
                               hook=sb["hooks"][sb["chosen_hook"]],
                               references=json.dumps(sb.get("references", [])),
                               quality_report=json.dumps({"render_qc": report}))
    out({"ok": True, "video": str(wd / f"{rec.video_id}.mp4"), "voice": provider,
         "engine": report.get("engine"), "fallback_reason": report.get("fallback_reason", ""),
         "fallbacks": fallbacks, "qc": {k: report[k] for k in ("ok", "duration", "lufs")},
         "tts_chars": sum(len(t) for t in texts)})
    return 0


def cmd_voice(ctx: Ctx, a) -> int:
    """Create the narration voice on its own (before render). Clips are cached by exact text, so
    `render` re-uses them and only changed scenes ever cost characters again."""
    from ..integrations.tts import TTSBudget, clean_text, synthesize_all
    sb = load_json(a.storyboard)
    rec = ctx.store.get(a.video_id)
    wd = WORK / rec.video_id
    wd.mkdir(parents=True, exist_ok=True)
    long_ = sbmod.is_long(sb)
    full = sbmod.with_outro(sb, ctx.s.long_outro_text if long_ else ctx.s.outro_text) if ctx.s.outro_enabled else sb
    texts = [s["narration"] for s in full["scenes"]]
    budget = TTSBudget(ctx.store, ctx.s.tts_monthly_char_budget)
    before = budget.used()
    clips, provider, fallbacks = synthesize_all(texts, wd, ctx.s, ctx.store, log=lambda m: print(m, file=sys.stderr))
    used = budget.used()
    out({"ok": True, "voice": provider, "fallbacks": fallbacks, "clips": len(clips),
         "video_chars": sum(len(clean_text(t)) for t in texts), "chars_billed_now": used - before,
         "month_used": used, "month_limit": ctx.s.tts_monthly_char_budget,
         "google_free_tier": 1_000_000})
    return 0


def cmd_resolve_images(ctx: Ctx, a) -> int:
    """Find licensed internet images for `image` scenes that have a `query` but no file yet.
    Unresolvable scenes fall back to a title card, so rendering never blocks on images."""
    import requests

    from ..production.vision import make_describer, make_detector, summarize
    from ..research import images as im
    sb = load_json(a.storyboard)
    wd = WORK / sb["video_id"]
    (wd / "assets").mkdir(parents=True, exist_ok=True)
    describer, detector, session = make_describer(ctx.store), make_detector(), requests.Session()
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
        best, notes = im.choose(q, cands, wd, f"scene{i}", session, describer, detector=detector) if cands else (None, [])
        if best:
            v["asset"] = best.file
            v.setdefault("caption", "")
            if (best.vision or {}).get("focus"):
                v["focus"] = best.vision["focus"]      # renderer zooms toward this box
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
    try:
        got["brand"] = _sync_brand(ctx)
    except Exception as e:                       # branding is optional; never block a video
        got["brand"] = f"unavailable ({type(e).__name__}: {str(e)[:120]})"
    out({"ok": True, "library": got})
    return 0


def _sync_brand(ctx: Ctx) -> str:
    """Logo badge: Drive References/Images/brand_logo.png, else the channel avatar (saved to Drive)."""
    import urllib.request

    from ..production import brand

    files = {f["name"]: f for f in ctx.drive.list_folder("references_images", "image/", limit=100)}
    if brand.LOGO_NAME in files:
        f = files[brand.LOGO_NAME]
        stamp = brand.BRAND_DIR / ".drive_id"
        if brand.logo_path() and stamp.exists() and stamp.read_text() == f["id"] + f.get("modifiedTime", ""):
            return "cached"
        tmp = brand.BRAND_DIR / "drive_logo.tmp"
        ctx.drive.download(f["id"], tmp)
        brand.make_badge(tmp.read_bytes())
        tmp.unlink()
        stamp.write_text(f["id"] + f.get("modifiedTime", ""))
        if not brand.handle():
            brand.save_meta(_channel()[1] if not ctx.s.dry_run else "")
        return "from Drive"
    url, handle = _channel()
    with urllib.request.urlopen(url, timeout=30) as r:            # nosec - Google-hosted avatar URL
        raw = r.read()
    brand.make_badge(raw)
    brand.save_meta(handle)
    if not ctx.s.dry_run:                                         # keep the ORIGINAL logo on Drive
        src = brand.BRAND_DIR / "source" / brand.LOGO_NAME
        src.parent.mkdir(exist_ok=True)
        src.write_bytes(raw)
        f = ctx.drive.upload(src, "references_images", mime="image/png",
                             description="DeliteCoder logo used in every video (replace this file to change it)")
        (brand.BRAND_DIR / ".drive_id").write_text(f["id"] + f.get("modifiedTime", ""))
        src.unlink()
    return "from YouTube channel avatar (saved to Drive)"


def _channel() -> tuple[str, str]:
    from ..integrations.google_auth import build_service

    items = build_service("youtube", "v3", "pipeline").channels().list(part="snippet", mine=True).execute()["items"]
    sn = items[0]["snippet"]
    return sn["thumbnails"]["high"]["url"], sn.get("customUrl", "")


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
    long_ = is_long(rec.video_id)
    if long_:                                   # YouTube chapters from the rendered timings
        chs = _chapters_for(rec.video_id)
        if chs and not re.search(r"(?m)^0:00 ", meta["description"]):
            meta["description"] = meta["description"].rstrip() + "\n\nChapters\n" + chs + "\n"
    credits = _credits_block(rec.video_id)
    if credits and "Credits:" not in meta["description"]:
        meta["description"] = meta["description"].rstrip() + credits
    errs = sbmod.validate_metadata(meta, long=long_)
    if review.get("verdict") != "pass":
        errs.append("quality review verdict is not 'pass'")
    if errs:
        out({"ok": False, "errors": errs})
        return 2
    now = utcnow()
    taken = {r.proposed_publish_at for r in ctx.store.all()
             if r.video_id != rec.video_id and r.proposed_publish_at and is_long(r.video_id) == long_ and r.state in
             (State.AWAITING_APPROVAL, State.APPROVED, State.UPLOADING, State.SCHEDULED)}
    try:
        slots = json.loads(ctx.store.get_config("publish_slots", "") or "{}").get("slots")
    except ValueError:
        slots = None
    if long_:
        slot = rfc3339(free_slot(now, taken, ctx.s.long_publish_time, ctx.s.timezone, ctx.s.min_lead_minutes))
    else:
        slot = rfc3339(free_slot(now, taken, ctx.s.publish_time, ctx.s.timezone, ctx.s.min_lead_minutes,
                                 weekday_slots=slots))
    qr = json.loads(rec.quality_report or "{}") | {"review": review}
    qr["youtube"] = {"category_id": str(meta.get("category_id", "28")), "made_for_kids": False,
                     "language": "en", "embeddable": True, "public_stats": True}
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
    # Poster frame for the approval email (embedded inline; private Drive file next to the video).
    src = Path(a.video or WORK / rec.video_id / f"{rec.video_id}.mp4") if rec.source_type == "GENERATED" \
        else WORK / "src" / (rec.file_name or "")
    poster = WORK / rec.video_id / "poster.jpg"
    if src.exists():
        poster.parent.mkdir(parents=True, exist_ok=True)
        dur = float(rec.duration_s or 10)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{min(3.0, dur / 3):.2f}", "-i", str(src),
                        "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4", str(poster)], check=False)
    if poster.exists():
        up_p = ctx.external_write(f"upload poster for {rec.video_id}",
                                  lambda: ctx.drive.upload(poster, "awaiting_approval", "image/jpeg"))
        if up_p:
            qr["poster_file_id"] = up_p["id"]
            fields["quality_report"] = json.dumps(qr)[:45000]
    fields["preview_url"] = f"https://drive.google.com/file/d/{file_id}/view"  # private: owner only
    rec = ctx.store.transition(rec.video_id, State.QUALITY_CHECK, State.AWAITING_APPROVAL, rec.version, **fields)
    out({"ok": True, "video_id": rec.video_id, "state": rec.state.value, "proposed_publish_at": slot,
         "note": "Apps Script sends the approval email within ~5 minutes" if not ctx.s.dry_run
         else "DRY_RUN: no email will be sent"})
    return 0


def _chapters_for(video_id: str) -> str:
    wd = WORK / video_id
    try:
        sb = json.loads((wd / "storyboard.json").read_text())
        timings = json.loads((wd / "timings.json").read_text())["timings"]
    except (OSError, ValueError, KeyError):
        return ""
    chs = sbmod.chapters(sb, timings)
    good = [c for k, c in enumerate(chs) if k == 0 or c[0] - chs[k - 1][0] >= 10]
    return sbmod.chapter_block(good) if len(good) >= 3 else ""


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


def _checkpoint_name(vid: str) -> str:
    return f"{vid}_work.zip"


def cmd_checkpoint(ctx: Ctx, a) -> int:
    """Save runtime/work/<VIDEO_ID> (JSON, assets, rendered MP4; no WAVs) to Drive 02_Generated so a
    later session can continue after a stop (token limit, crash, container reset)."""
    import zipfile
    wd = WORK / a.video_id
    if not wd.exists():
        out({"ok": False, "error": "nothing to checkpoint"})
        return 2
    zp = WORK / _checkpoint_name(a.video_id)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(wd.rglob("*")):
            rel = f.relative_to(wd)
            if f.is_file() and not f.name.endswith(CHECKPOINT_SKIP) and "hf" not in rel.parts \
                    and "_candidates" not in rel.parts:
                z.write(f, rel.as_posix())
    def push():
        q = (f"name = '{zp.name}' and trashed = false and '{ctx.s.folder('generated')}' in parents")
        old = ctx.drive.svc.files().list(q=q, fields="files(id)").execute().get("files", [])
        for o in old:
            ctx.drive.svc.files().delete(fileId=o["id"]).execute()
        return ctx.drive.upload(zp, "generated", "application/zip", f"checkpoint {a.video_id}")
    ctx.external_write(f"checkpoint {zp.name} to 02_Generated", push)
    out({"ok": True, "checkpoint": zp.name, "bytes": zp.stat().st_size})
    return 0


def cmd_restore(ctx: Ctx, a) -> int:
    """Restore a checkpoint saved by an earlier session into runtime/work/<VIDEO_ID>."""
    import zipfile
    wd = WORK / a.video_id
    wd.mkdir(parents=True, exist_ok=True)
    zp = WORK / _checkpoint_name(a.video_id)
    if not ctx.s.dry_run:
        q = (f"name = '{zp.name}' and trashed = false and '{ctx.s.folder('generated')}' in parents")
        hits = ctx.drive.svc.files().list(q=q, fields="files(id)").execute().get("files", [])
        if not hits:
            out({"ok": True, "restored": [], "note": "no checkpoint; continue from the current state"})
            return 0
        ctx.drive.download(hits[0]["id"], zp)
    if not zp.exists():
        out({"ok": True, "restored": [], "note": "no checkpoint"})
        return 0
    restored = []
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():
            target = (wd / n).resolve()
            if not str(target).startswith(str(wd.resolve())):      # zip-slip guard
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
            restored.append(n)
    rec = ctx.store.get(a.video_id)
    have = {n.split("/")[-1] for n in restored}
    nxt = {"DISCOVERED": "research", "RESEARCHING": "storyboard" if "storyboard.json" not in have else "review/render",
           "GENERATING": "render", "QUALITY_CHECK": "submit" if f"{a.video_id}.mp4" in have else "render"}
    out({"ok": True, "restored": sorted(restored)[:30], "state": rec.state.value if rec else "?",
         "next_step": nxt.get(rec.state.value if rec else "", "check state")})
    return 0


def repo_script_version() -> str:
    import re as _re
    m = _re.search(r"var SCRIPT_VERSION = '([^']+)'", (ROOT_DIR / "apps_script" / "approval_watcher.gs").read_text())
    return m.group(1) if m else ""


def cmd_apps_script_check(ctx: Ctx, a) -> int:
    """Compare the deployed Apps Script version with this repo; email the approver once per version."""
    want = repo_script_version()
    have = ctx.store.get_config("apps_script_version", "")
    if have == want:
        out({"up_to_date": True, "version": have})
        return 0
    if ctx.store.get_config("apps_script_update_notified", "") != want:
        log = (ROOT_DIR / "apps_script" / "CHANGELOG.md").read_text()
        entry = log.split("## ")[1].strip() if "## " in log else want
        script = "https://script.google.com/home"
        msg = (f"Your approval Apps Script needs an update (running: {have or 'an older version'}, latest: {want}).\n\n"
               f"What changed:\n## {entry}\n\nHow to update (2 minutes):\n"
               "1. Open https://raw.githubusercontent.com/delitecoder/youtube-shorts-automation/main/apps_script/approval_watcher.gs "
               "and copy everything.\n"
               f"2. Open {script} → YouTube Shorts Approval Watcher → replace all code in Code.gs → Save.\n"
               "3. If the changes mention a new service or permission, add it under Services (+).\n"
               "4. Choose 'setup' → Run → Allow.\n"
               "Claude never changes this script itself; nothing else is needed.")
        log_error(ctx.store, "apps_script", msg, "", "ACTION")
        ctx.store.set_config("apps_script_update_notified", want)
    out({"up_to_date": False, "deployed": have or None, "latest": want, "approver_emailed": True})
    return 0


def cmd_analyze_reference(ctx: Ctx, a) -> int:
    """Analyse reference videos (Drive References/Style, or --dir) for the director. Compact output;
    contact sheets are written to runtime/reference/ for the director to view."""
    from ..production import reference as ref
    out_dir = RUNTIME_DIR / "reference"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(a.dir) if a.dir else out_dir / "videos"
    if not a.dir:
        src.mkdir(exist_ok=True)
        style = _style_folder(ctx)
        q = f"'{style}' in parents and trashed = false"
        for f in ctx.drive.svc.files().list(q=q, fields="files(id,name,size)", pageSize=50).execute().get("files", []):
            if f["name"].lower().endswith((".mp4", ".webm", ".mkv", ".mov", ".vtt")) and not (src / f["name"]).exists():
                req = ctx.drive.svc.files().get_media(fileId=f["id"])
                from googleapiclient.http import MediaIoBaseDownload
                with open(src / f["name"], "wb") as fh:
                    dl, done = MediaIoBaseDownload(fh, req, chunksize=16 * 1024 * 1024), False
                    while not done:
                        _, done = dl.next_chunk(num_retries=3)
    vids = sorted(p for p in src.iterdir() if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov"))
    if not vids:
        out({"ok": False, "error": f"no reference videos in {src} (owner: put them in Drive "
                                    "YouTubeAutomation/01_Inbox/References/Style)"})
        return 2
    results = [ref.analyze(v, out_dir) for v in vids[: a.limit]]
    ref.save(results, out_dir / "analysis.json")
    out({"ok": True, "videos": results})
    return 0


def _style_folder(ctx: Ctx) -> str:
    """Drive folder References/Style (created on first use) for owner-supplied reference videos."""
    parent = ctx.s.folder("inbox_references")
    q = (f"name = 'Style' and mimeType = 'application/vnd.google-apps.folder' and trashed = false "
         f"and '{parent}' in parents")
    hits = ctx.drive.svc.files().list(q=q, fields="files(id)").execute().get("files", [])
    if hits:
        return hits[0]["id"]
    return ctx.drive.svc.files().create(body={"name": "Style", "mimeType": "application/vnd.google-apps.folder",
                                              "parents": [parent]}, fields="id").execute()["id"]


def cmd_frames(ctx: Ctx, a) -> int:
    """Director review: one image with 2 frames per scene of the rendered video."""
    from ..production import reference as ref
    wd = WORK / a.video_id
    timings = json.loads((wd / "timings.json").read_text())["timings"]
    sheet = ref.scene_frames(wd / f"{a.video_id}.mp4", timings, wd / "review_sheet.png")
    rep = wd / "custom_scenes_report.json"
    out({"ok": True, "sheet": str(sheet), "scenes": len(timings),
         "custom_scenes": json.loads(rep.read_text()) if rep.exists() else []})
    return 0


def _run_audit(ctx: Ctx) -> dict:
    from ..analytics import audit as au
    ours = {r.youtube_video_id for r in ctx.store.all() if r.youtube_video_id}
    rep = au.collect(ctx.yt_analytics, ctx.youtube, _today_local(ctx), ours)
    res = au.analyse(rep)
    ctx.store.set_config("last_audit", json.dumps(res)[:45000])
    return res


def _insights(ctx: Ctx) -> dict:
    """Today's audience insights (runs the audit first if today's is missing). Every new video
    is planned from these: search terms viewers use, videos we are suggested next to, what works."""
    today = _today_local(ctx).isoformat()
    try:
        res = json.loads(ctx.store.get_config("last_audit", "") or "{}")
    except ValueError:
        res = {}
    if res.get("date") != today:
        try:
            res = _run_audit(ctx)
        except Exception as e:  # noqa: BLE001 - no analytics access (dry run / no channel yet)
            return {"available": False, "reason": f"audit failed: {str(e)[:160]}"}
    sig = res.get("topic_signals", {})
    return {"available": True, "date": res.get("date"), "search_terms": sig.get("search_terms", []),
            "suggested_next_to": sig.get("suggested_next_to", []), "by_format": sig.get("by_format", {}),
            "suggestions": res.get("suggestions", []), "guidance": res.get("guidance", {})}


def cmd_audit(ctx: Ctx, a) -> int:
    """Daily views audit: analyse the channel, store guidance for the storywriter, email the owner."""
    from ..analytics import audit as au
    res = _run_audit(ctx)
    today = _today_local(ctx).isoformat()
    if a.email and ctx.store.get_config("audit_emailed", "") != today:
        log_error(ctx.store, "audit", au.email_text(res), "", "AUDIT")
        ctx.store.set_config("audit_emailed", today)
    # compact output for the orchestrator/storywriter (keeps tokens low)
    out({"trend": res["trend"], "channel_median": res.get("channel_median"), "suggestions": res["suggestions"],
         "guidance": res["guidance"], "top": res.get("top", [])[:3], "topic_signals": res.get("topic_signals")})
    return 0


def cmd_peak_time(ctx: Ctx, a) -> int:
    """Learn the best publish slot per weekday from the audience's country and the channel's own
    early-views history; stored as Configuration publish_slots (refreshed weekly)."""
    from ..analytics import peak
    cur = ctx.store.get_config("publish_slots", "")
    if cur and not a.refresh:
        try:
            age = (_today_local(ctx) - datetime.fromisoformat(json.loads(cur)["computed_on"]).date()).days
            if age < 7:
                out(json.loads(cur) | {"cached": True})
                return 0
        except (ValueError, KeyError):
            pass
    res = peak.compute(ctx.youtube, ctx.yt_analytics, ctx.s.timezone, _today_local(ctx))
    ctx.store.set_config("publish_slots", json.dumps(res))
    out(res)
    return 0


def cmd_budget(ctx: Ctx, a) -> int:
    """Decide whether/how to make today's video from the session's real usage numbers.
    --record start|end stores utilization so the per-video cost is learned over time."""
    from . import budget as b
    if a.unknown:
        # The session cannot read its usage limits (tools unavailable): never invent numbers.
        out({"decision": "go_lean", "usage_known": False,
             "reason": "usage limits not readable in this session; lean plan keeps the run short. "
                       "Weekly-limit problems are still caught by the Apps Script 12:30 IST watchdog."})
        return 0
    if None in (a.five_hour, a.five_reset, a.seven_day, a.seven_reset):
        out({"error": "pass all four usage numbers, or --unknown"})
        return 2
    samples = b.loads(ctx.store.get_config("claude_usage_samples", "[]"))
    now_util = {"five_hour": a.five_hour, "seven_day": a.seven_day}
    if a.record == "start":
        ctx.store.set_config("claude_usage_run_start", json.dumps(now_util))
    elif a.record == "end":
        start = json.loads(ctx.store.get_config("claude_usage_run_start", "{}") or "{}")
        if start:
            ctx.store.set_config("claude_usage_samples", json.dumps(b.record_sample(samples, start, now_util)))
            samples = b.loads(ctx.store.get_config("claude_usage_samples", "[]"))
    p = b.plan(a.five_hour, a.five_reset, a.seven_day, a.seven_reset, samples=samples,
               five_status=a.five_status, seven_status=a.seven_status)
    ctx.store.set_config("claude_weekly_resets_at", p["weekly_resets_at"])
    if p["decision"] == "weekly_stop" and a.record != "end":
        today = _today_local(ctx).isoformat()
        if ctx.store.get_config("weekly_alert_sent", "") != today:
            reset_ist = datetime.fromisoformat(p["weekly_resets_at"]).astimezone(ZoneInfo(ctx.s.timezone))
            log_error(ctx.store, "claude_budget",
                      "Weekly Claude usage limit is (almost) used up, so no new videos can be made or "
                      f"uploaded until it resets on {reset_ist:%a %d %b %H:%M} IST. Nothing is needed from you; "
                      "the daily run resumes automatically after the reset.", "", "ALERT")
            ctx.store.set_config("weekly_alert_sent", today)
    out(p)
    return 0


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
    p.add_argument("--format", choices=["short", "long"], default="short")
    p = sub.add_parser("new-item"); p.add_argument("--source-file-id")
    p.add_argument("--format", choices=["short", "long"], default="short")
    p = sub.add_parser("prepare-existing"); p.add_argument("--file-id"); p.add_argument("--local-path")
    p.add_argument("--description")
    p = sub.add_parser("doc-text"); p.add_argument("--file-id", required=True); p.add_argument("--max-chars", type=int, default=20000)
    p = sub.add_parser("record-research"); p.add_argument("--video-id", required=True); p.add_argument("--file", required=True)
    p = sub.add_parser("validate-storyboard"); p.add_argument("--file", required=True); p.add_argument("--document-based", action="store_true")
    p = sub.add_parser("render"); p.add_argument("--video-id", required=True); p.add_argument("--storyboard", required=True)
    p.add_argument("--document-based", action="store_true")
    p = sub.add_parser("submit"); p.add_argument("--video-id", required=True); p.add_argument("--metadata", required=True)
    p.add_argument("--review", required=True); p.add_argument("--video")
    p = sub.add_parser("voice"); p.add_argument("--video-id", required=True); p.add_argument("--storyboard", required=True)
    p = sub.add_parser("resolve-images"); p.add_argument("--storyboard", required=True)
    p = sub.add_parser("fetch-asset"); p.add_argument("--video-id", required=True); p.add_argument("--file-id", required=True)
    p.add_argument("--name", required=True)
    sub.add_parser("sync-library")
    p = sub.add_parser("peak-time"); p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("audit"); p.add_argument("--email", action="store_true")
    sub.add_parser("apps-script-check")
    p = sub.add_parser("analyze-reference"); p.add_argument("--dir"); p.add_argument("--limit", type=int, default=6)
    p = sub.add_parser("frames"); p.add_argument("--video-id", required=True)
    p = sub.add_parser("budget")
    p.add_argument("--five-hour", type=float, help="five_hour utilization 0-1")
    p.add_argument("--five-reset", type=int, help="five_hour resetsAt (epoch s)")
    p.add_argument("--seven-day", type=float, help="seven_day utilization 0-1")
    p.add_argument("--seven-reset", type=int, help="seven_day resetsAt (epoch s)")
    p.add_argument("--five-status", default="allowed"); p.add_argument("--seven-status", default="allowed")
    p.add_argument("--record", choices=["start", "end"])
    p.add_argument("--unknown", action="store_true", help="usage cannot be read in this session")
    p = sub.add_parser("checkpoint"); p.add_argument("--video-id", required=True)
    p = sub.add_parser("restore"); p.add_argument("--video-id", required=True)
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
