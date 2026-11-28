"""`studio` lifecycle commands named in the operating contract. They reuse the domain commands; none of them
can bypass the email approval gate or the protected flags:
- research <file> -> record-research · produce/render -> render · audio -> voice · qc -> objective video+audio QC
- approve -> READ-ONLY approval status (approval only happens by the approver's authenticated email reply)
- publish -> reconcile (the owner's Apps Script uploads approved videos; nothing is published from here)
- report -> one-page status: queue, durable checkpoint, ledger, experiments, recommendations
- playlist -> planned subject playlist for a video
"""
from __future__ import annotations

import json
from pathlib import Path

from studio.cli.common import WORK, out
from studio.core.state import State


def cmd_qc(ctx, a) -> int:
    from studio.audio import qc as aqc
    from studio.storage.store import is_long
    from studio.video import qc as vqc
    wd = WORK / a.video_id
    mp4 = Path(a.video) if a.video else wd / f"{a.video_id}.mp4"
    v = vqc.check(mp4, long_=is_long(a.video_id))
    au = aqc.check(wd / "narration.wav", wd / "stems" if (wd / "stems").exists() else None,
                   video_duration=v["measured"].get("duration_s"))
    ok = v["pass"] and au["pass"]
    (wd / "qc_report.json").write_text(json.dumps({"video": v, "audio": au}, indent=1, default=str)) if wd.exists() else None
    out({"ok": ok, "video": v, "audio": au})
    return 0 if ok else 2


def cmd_approve(ctx, a) -> int:
    rec = ctx.store.get(a.video_id)
    if not rec:
        out({"ok": False, "error": "unknown video"})
        return 2
    out({"ok": True, "video_id": rec.video_id, "state": rec.state.value,
         "approval_requested_at": rec.approval_requested_at, "approval_expires_at": rec.approval_expires_at,
         "how_to_approve": f"The approver replies exactly: APPROVED {rec.video_id} to the approval email. "
                           "There is no command-line approval (rules.md §6)."})
    return 0


def cmd_report(ctx, a) -> int:
    from studio.experimentation import engine
    from studio.observability import ledger
    from studio.orchestration.checkpoint import Checkpointer
    recs = ctx.store.all()
    by_state: dict[str, int] = {}
    for r in recs:
        by_state[r.state.value] = by_state.get(r.state.value, 0) + 1
    cp = Checkpointer(ctx.store).latest()
    exps = engine.latest_experiments(ctx.store)
    out({"ok": True, "videos_by_state": by_state,
         "awaiting_approval": [r.video_id for r in recs if r.state == State.AWAITING_APPROVAL],
         "checkpoint": {"run_id": cp.run_id, "stage": cp.stage, "status": cp.status, "next": cp.next_actions[:3]} if cp else None,
         "ledger": ledger.summary(ctx.store),
         "experiments": {s: sum(1 for e in exps.values() if e.get("status") == s) for s in ("running", "concluded")},
         "recommendations": [{k: r.get(k) for k in ("scope", "dimension", "choice", "evidence")}
                             for r in engine.recommendations(ctx.store).values()]})
    return 0


def cmd_playlist(ctx, a) -> int:
    from studio.playlists.planner import plan
    p = plan(ctx.store, a.video_id, a.category)
    out({"ok": True, "playlist": p})
    return 0


def register(sub) -> None:
    p = sub.add_parser("qc", help="objective video + audio QC of a rendered video")
    p.add_argument("--video-id", required=True); p.add_argument("--video")
    p = sub.add_parser("approve", help="READ-ONLY approval status (approval is by authenticated email only)")
    p.add_argument("--video-id", required=True)
    sub.add_parser("report", help="queue, checkpoint, ledger, experiments, recommendations")
    p = sub.add_parser("playlist", help="planned subject playlist for a video")
    p.add_argument("--video-id", required=True); p.add_argument("--category", required=True)


ALIASES = {"research": "record-research", "produce": "render", "audio": "voice", "publish": "reconcile"}
