"""Usage ledger: every pipeline stage / model call as one row in the Ledger tab.

Token counts are recorded ONLY when a real number is known (e.g. a model usage report); otherwise the
field stays empty. Never estimate tokens into this ledger.
"""
from __future__ import annotations

from studio.core.state import iso, utcnow


def record(store, *, run_id: str, stage: str, actor: str, model: str = "", reason: str = "",
           wall_s: float | None = None, tokens_in: int | None = None, tokens_out: int | None = None,
           outcome: str = "ok", video_id: str = "") -> dict:
    row = {"logged_at": iso(utcnow()), "run_id": run_id, "video_id": video_id, "stage": stage,
           "actor": actor, "model": model, "reason": reason,
           "wall_s": "" if wall_s is None else f"{wall_s:.1f}",
           "tokens_in": "" if tokens_in is None else str(int(tokens_in)),
           "tokens_out": "" if tokens_out is None else str(int(tokens_out)), "outcome": outcome}
    store.append("Ledger", row)
    return row


def summary(store, run_id: str | None = None) -> dict:
    rows = [r for r in store.rows("Ledger") if not run_id or r.get("run_id") == run_id]
    by_model: dict[str, dict] = {}
    for r in rows:
        m = r.get("model") or "python"
        d = by_model.setdefault(m, {"steps": 0, "wall_s": 0.0, "tokens_known": 0, "tokens_unknown_steps": 0})
        d["steps"] += 1
        d["wall_s"] += float(r.get("wall_s") or 0)
        if r.get("tokens_in") or r.get("tokens_out"):
            d["tokens_known"] += int(r.get("tokens_in") or 0) + int(r.get("tokens_out") or 0)
        elif m != "python":
            d["tokens_unknown_steps"] += 1
    return {"steps": len(rows), "by_model": by_model}
