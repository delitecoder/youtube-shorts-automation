"""`studio` commands: durable run state (checkpoint save/verify/show, resume), model routing, environment probe."""
from __future__ import annotations

import json
from pathlib import Path

from studio.cli.common import out
from studio.core.model_router import route, table
from studio.orchestration.checkpoint import Checkpoint, Checkpointer, CheckpointError
from studio.resource_manager.probe import probe, workers


def _ck(ctx) -> Checkpointer:
    return Checkpointer(ctx.store)


def cmd_state(ctx, a) -> int:
    ck = _ck(ctx)
    if a.action == "save":
        base = {}
        if (ck.dir / "checkpoint.json").exists():
            base = json.loads((ck.dir / "checkpoint.json").read_text())
        upd = json.loads(Path(a.file).read_text()) if a.file else {}
        for k in ("run_id", "stage", "status", "video_id"):
            if getattr(a, k, None):
                upd[k] = getattr(a, k)
        for k in ("completed", "decisions", "evidence", "blockers", "files_changed"):
            if k in upd and k in base:                       # append, newest last
                upd[k] = [*base.get(k, []), *[x for x in upd[k] if x not in base.get(k, [])]]
        merged = {**base, **upd}
        if not merged.get("run_id") or not merged.get("stage"):
            out({"ok": False, "error": "run_id and stage are required"})
            return 2
        merged.setdefault("status", "running")
        try:
            cp = ck.save(Checkpoint(**{k: v for k, v in merged.items() if k in Checkpoint.__dataclass_fields__}))
        except CheckpointError as e:
            out({"ok": False, "verified": False, "error": str(e)})
            return 4
        (ck.dir / "CHECKPOINT_UNVERIFIED").unlink(missing_ok=True)
        out({"ok": True, "verified": True, "run_id": cp.run_id, "stage": cp.stage, "saved_at": cp.saved_at})
        return 0
    if a.action == "verify":
        cp = ck.latest()
        ok = cp is not None
        if ok:
            (ck.dir / "CHECKPOINT_UNVERIFIED").unlink(missing_ok=True)
        out({"ok": ok, "verified": ok, "run_id": cp.run_id if cp else None, "stage": cp.stage if cp else None})
        return 0 if ok else 4
    cp = ck.latest()
    out({"ok": cp is not None, "checkpoint": json.loads(cp.payload()) if cp else None})
    return 0


def cmd_resume(ctx, a) -> int:
    """Fresh container or new session: rebuild runtime/state from the durable store and print next actions."""
    ck = _ck(ctx)
    cp = ck.restore()
    if not cp:
        out({"ok": True, "action": "none", "reason": "no durable checkpoint; start with `python -m studio discover`"})
        return 0
    out({"ok": True, "action": "resume", "run_id": cp.run_id, "video_id": cp.video_id, "stage": cp.stage,
         "status": cp.status, "next_actions": cp.next_actions, "blockers": cp.blockers})
    return 0


def cmd_route(ctx, a) -> int:
    if a.task:
        r = route(a.task, attempt=a.attempt, why=a.why or "")
        out({"ok": True, "task": r.task, "model": r.model or "python", "max_output_tokens": r.max_output_tokens,
             "retries": r.retries, "reason": r.reason})
    else:
        out({"ok": True, "routes": table()})
    return 0


def cmd_probe(ctx, a) -> int:
    p = probe(check_network=not a.offline)
    out({"ok": True, **p, "workers": workers()})
    return 0


def register(sub) -> None:
    p = sub.add_parser("state", help="durable run checkpoint: save | verify | show")
    p.add_argument("action", choices=["save", "verify", "show"])
    p.add_argument("--file", help="JSON with checkpoint fields to merge (save)")
    p.add_argument("--run-id"); p.add_argument("--stage"); p.add_argument("--status"); p.add_argument("--video-id")
    sub.add_parser("resume", help="restore runtime/state from the durable store; print next actions")
    p = sub.add_parser("route", help="model routing for a task (config/models.yaml)")
    p.add_argument("--task"); p.add_argument("--attempt", type=int, default=0); p.add_argument("--why")
    p = sub.add_parser("probe", help="measure RAM/CPU/disk/tools before heavy work")
    p.add_argument("--offline", action="store_true")
