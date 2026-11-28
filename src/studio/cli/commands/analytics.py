"""`studio` commands: analytics."""
from __future__ import annotations

from datetime import datetime
from studio.core.state import State
from studio.orchestration.context import Ctx
from studio.storage.store import log_error
import json

from studio.cli.common import (_run_audit, _today_local, out)


def cmd_audit(ctx: Ctx, a) -> int:
    """Daily views audit: analyse the channel, store guidance for the storywriter, email the owner."""
    from studio.analytics import audit as au
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
    from studio.analytics import peak
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


def cmd_analytics(ctx: Ctx, a) -> int:
    from studio.analytics import snapshots as ya
    published = [r.to_row() for r in ctx.store.all() if r.state == State.PUBLISHED]
    n = 0
    if a.snapshot and published:
        n = ya.snapshot(ctx.store, ctx.yt_analytics, published)
    rows = ctx.store.rows("Analytics")
    titles = {r.youtube_video_id: r.title for r in ctx.store.all() if r.youtube_video_id}
    out({"snapshots_written": n, "summary": ya.summary(rows, titles),
         "publish_time": ya.recommend_publish_time(rows, ctx.s.timezone, ctx.s.publish_time)})
    return 0


def register(sub) -> None:
    p = sub.add_parser("peak-time"); p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("audit"); p.add_argument("--email", action="store_true")
    p = sub.add_parser("analytics"); p.add_argument("--snapshot", action="store_true")
