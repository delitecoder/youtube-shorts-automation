"""`studio` commands: setup."""
from __future__ import annotations

from datetime import datetime
from studio.orchestration.context import Ctx
from studio.storage.store import log_error
from zoneinfo import ZoneInfo
import json
import shutil

from studio.cli.common import (ROOT_DIR, _today_local, out, repo_script_version)


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
    from studio.storage.drive import Drive
    from studio.core.google_auth import build_service
    from studio.storage.sheets import SheetsStore
    drive = Drive(build_service("drive", "v3", "pipeline"), ctx.s)
    ids = drive.ensure_structure()
    sid = ctx.s.spreadsheet_id or SheetsStore.create_spreadsheet(build_service("sheets", "v4", "pipeline"))
    SheetsStore(build_service("sheets", "v4", "pipeline"), sid).ensure_schema()
    out({"ok": True, "SPREADSHEET_ID": sid, "DRIVE_FOLDER_IDS": ids})
    return 0


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


def cmd_budget(ctx: Ctx, a) -> int:
    """Decide whether/how to make today's video from the session's real usage numbers.
    --record start|end stores utilization so the per-video cost is learned over time."""
    from studio.resource_manager import usage as b
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


def cmd_status(ctx: Ctx, a) -> int:
    recs = ctx.store.all()[-a.limit:]
    out({"flags": ctx.s.describe_flags(),
         "items": [{"video_id": r.video_id, "state": r.state.value, "title": r.title[:60],
                    "youtube": r.youtube_video_id} for r in recs]})
    return 0


def register(sub) -> None:
    sub.add_parser("doctor")
    sub.add_parser("init")
    sub.add_parser("apps-script-check")
    p = sub.add_parser("budget")
    p.add_argument("--five-hour", type=float, help="five_hour utilization 0-1")
    p.add_argument("--five-reset", type=int, help="five_hour resetsAt (epoch s)")
    p.add_argument("--seven-day", type=float, help="seven_day utilization 0-1")
    p.add_argument("--seven-reset", type=int, help="seven_day resetsAt (epoch s)")
    p.add_argument("--five-status", default="allowed"); p.add_argument("--seven-status", default="allowed")
    p.add_argument("--record", choices=["start", "end"])
    p.add_argument("--unknown", action="store_true", help="usage cannot be read in this session")
    p = sub.add_parser("status"); p.add_argument("--limit", type=int, default=15)
