"""Shared CLI helpers and constants (moved from the old monolithic cli)."""
from __future__ import annotations

from pathlib import Path
from studio.core.config import LONG_THEME
from studio.core.config import RUNTIME_DIR
from studio.core.config import SYSTEM_DESIGN_SYLLABUS
from studio.core.config import theme_for
from studio.core.state import State
from studio.core.state import VideoRecord
from studio.core.state import parse_iso
from studio.core.state import utcnow
from studio.editorial import storyboard as sbmod
from studio.orchestration.context import Ctx
from studio.storage.store import is_long
from studio.storage.store import language_of
from studio.storage.store import next_video_id
from zoneinfo import ZoneInfo
import json
import re


WORK = RUNTIME_DIR / "work"


ROOT_DIR = Path(__file__).resolve().parents[3]


PIPELINE_STATES = {State.DISCOVERED, State.RESEARCHING, State.GENERATING, State.QUALITY_CHECK}


CHECKPOINT_SKIP = (".wav",)      # voice/music are cheap to regenerate; everything else is kept


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, default=str))


def fit_json(obj, limit: int = 45000) -> str:
    """JSON that fits a Sheets cell WITHOUT slicing (slicing produced invalid JSON that readers silently
    dropped). Largest lists are halved / largest values removed, and `_trimmed` records what was cut."""
    s = json.dumps(obj, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return s
    data = json.loads(s) if isinstance(obj, (dict, list)) else {"value": str(obj)}
    if not isinstance(data, dict):
        data = {"items": data}
    trimmed = []
    while len(json.dumps(data, ensure_ascii=False)) > limit:
        key = max((k for k in data if k != "_trimmed"), key=lambda k: len(json.dumps(data[k], ensure_ascii=False)))
        v = data[key]
        if isinstance(v, list) and len(v) > 1:
            data[key] = v[: len(v) // 2]
            trimmed.append(f"{key}[:{len(v) // 2}]")
        else:
            data.pop(key)
            trimmed.append(key)
        data["_trimmed"] = trimmed
    return json.dumps(data, ensure_ascii=False)


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text())


def _today_local(ctx):
    return utcnow().astimezone(ZoneInfo(ctx.s.timezone)).date()


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
    from studio.core.config import _plan_date
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


DONE_ENGLISH = {State.AWAITING_APPROVAL, State.APPROVED, State.UPLOADING, State.SCHEDULED, State.PUBLISHED}


def _queued_future(ctx: Ctx, recs: list) -> str | None:
    """An English item made on an EARLIER day that publishes today or later (a backlog): making
    today's item too would push every later video back a day."""
    from zoneinfo import ZoneInfo
    today = _today_local(ctx).strftime("%Y%m%d")
    for r in recs:
        if (language_of(r.video_id) != "en" or r.video_id[6:14] >= today or not r.proposed_publish_at
                or r.state not in (State.AWAITING_APPROVAL, State.APPROVED, State.UPLOADING, State.SCHEDULED)):
            continue
        slot_day = parse_iso(r.proposed_publish_at).astimezone(ZoneInfo(ctx.s.timezone)).strftime("%Y%m%d")
        if slot_day >= today:
            return r.video_id
    return None


def _needs_localize(ctx: Ctx, recs: list, today: str) -> dict | None:
    """Today's submitted English video still needs its Tamil version."""
    if "ta" not in ctx.s.video_languages:
        return None
    for r in sorted(recs, key=lambda x: x.video_id):
        if (r.video_id.startswith(f"VIDEO-{today}-") and language_of(r.video_id) == "en"
                and r.state in DONE_ENGLISH and r.source_type == "GENERATED"
                and not any((o.notes or "").startswith(f"ta version of {r.video_id}") for o in recs)):
            return {"action": "localize", "lang": "ta", "video_id": r.video_id,
                    "next": "translator agent → storyboard.ta.json + metadata.ta.json, then "
                            f"python -m studio localize --video-id {r.video_id} --storyboard … --metadata …"}
    return None


def _theme_today(ctx: Ctx) -> dict:
    try:
        plan = ctx.store.rows("ThemePlan")
    except Exception:                      # tab missing / sheet unavailable: use the rotation
        plan = []
    th = theme_for(_today_local(ctx), plan)
    if th.get("key") == "trending_mix":    # lanes of the last 2 English Shorts: pick a different one
        try:
            hist = [h for h in ctx.store.rows("ResearchHistory")
                    if not is_long(h.get("video_id", "")) and language_of(h.get("video_id", "")) == "en"]
        except Exception:
            hist = []
        try:     # the researcher's own lane per video (bug fix: ResearchHistory never had a theme column)
            lanes = {r["video_id"]: r["text"] for r in ctx.store.rows("ContentMemory") if r.get("kind") == "lane"}
        except Exception:
            lanes = {}
        th["recent_lanes"] = [lanes.get(h.get("video_id")) or _lane_of(h.get("category", "")) for h in hist[-2:]]
    return th


def _lane_of(category: str) -> str:
    return {"technology news": "trending_news", "tech stories": "tech_story",
            "system design": "system_design_short"}.get(category, "trending_search")


def _new_record(ctx: Ctx, source_type: str, source_file_id: str = "", long: bool = False) -> VideoRecord:
    vid = next_video_id([r.video_id for r in ctx.store.all()], utcnow(), ctx.s.timezone, long=long)
    return ctx.store.create(VideoRecord(video_id=vid, source_type=source_type,
                                        source_file_id=source_file_id))


LIBRARY = RUNTIME_DIR / "library"


def _sync_brand(ctx: Ctx) -> str:
    """Logo badge: Drive References/Images/brand_logo.png, else the channel avatar (saved to Drive)."""
    import urllib.request

    from studio.video import brand

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
    from studio.core.google_auth import build_service

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
            elif (e.get("type") in ("sfx", "music") and e.get("source") and "procedural" not in e["source"]
                  and e.get("attribution_required", True) and not str(e.get("license", "")).startswith("CC0")):
                lines.append(f"{e['type'].upper()}: {e['source']} ({e['license']})")
    return ("\n\nCredits:\n" + "\n".join(dict.fromkeys(lines))) if lines else ""


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


def repo_script_version() -> str:
    import re as _re
    m = _re.search(r"var SCRIPT_VERSION = '([^']+)'", (ROOT_DIR / "apps_script" / "approval_watcher.gs").read_text())
    return m.group(1) if m else ""


def _style_notes(ctx: Ctx) -> list[str]:
    try:
        return json.loads(ctx.store.get_config("style_notes", "") or "[]")
    except ValueError:
        return []


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


def _run_audit(ctx: Ctx) -> dict:
    from studio.analytics import audit as au
    ours = {r.youtube_video_id for r in ctx.store.all() if r.youtube_video_id}
    rep = au.collect(ctx.yt_analytics, ctx.youtube, _today_local(ctx), ours)
    res = au.analyse(rep)
    ctx.store.set_config("last_audit", fit_json(res))
    try:                                   # tags/hashtags that brought views on the owner's videos
        reach = au.channel_reach(ctx.youtube)
        if reach:
            ctx.store.set_config("channel_reach", json.dumps(reach)[:20000])
    except Exception:  # noqa: BLE001 - optional; submit falls back to the default reach set
        pass
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
    return {"style_notes": _style_notes(ctx), "available": True, "date": res.get("date"), "search_terms": sig.get("search_terms", []),
            "suggested_next_to": sig.get("suggested_next_to", []), "by_format": sig.get("by_format", {}),
            "suggestions": res.get("suggestions", []), "guidance": res.get("guidance", {})}
