"""Durable, verifiable run checkpoints (compaction- and container-loss-proof).

Local files under runtime/state/ are a CACHE. The source of truth is the durable store's RunState tab
(Google Sheets in production, JSON store in dry-run/tests). Every save is read back from the durable
store and its sha256 verified; a failure raises CheckpointError and `require_verified()` blocks
destructive/external steps until a verified checkpoint exists (fail closed).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from studio.core.config import ROOT, RUNTIME_DIR
from studio.core.state import utcnow

MAX_CELL = 45000          # Sheets cell limit is 50k chars; payloads are trimmed structurally, never sliced
FILES = ("current_run.json", "current_task.json", "checkpoint.json", "next_actions.json",
         "decisions.jsonl", "errors.jsonl", "session_summary.md", "last_verified_commit.txt")


class CheckpointError(RuntimeError):
    pass


@dataclass
class Checkpoint:
    run_id: str
    stage: str
    status: str                                   # running | done | failed | blocked
    task: dict = field(default_factory=dict)      # {task_id, title, goal}
    video_id: str = ""
    completed: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    files_changed: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    next_actions: list = field(default_factory=list)   # exact commands
    experiments: list = field(default_factory=list)
    approval_state: dict = field(default_factory=dict)
    blockers: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    commit: str = ""
    saved_at: str = ""

    def payload(self) -> str:
        d = asdict(self)
        for k, keep in (("decisions", 30), ("evidence", 30), ("completed", 60), ("errors", 20), ("files_changed", 80)):
            d[k] = d[k][-keep:]
        s = json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
        while len(s) > MAX_CELL:                       # trim oldest list items until it fits
            k = max(("completed", "evidence", "decisions", "files_changed", "errors"), key=lambda x: len(d[x]))
            if not d[k]:
                raise CheckpointError("checkpoint payload too large even after trimming")
            d[k] = d[k][len(d[k]) // 2:]
            s = json.dumps(d, sort_keys=True, ensure_ascii=False, default=str)
        return s


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


class Checkpointer:
    def __init__(self, store, state_dir: Path | None = None):
        self.store = store
        self.dir = Path(state_dir or RUNTIME_DIR / "state")

    # ---------------- write
    def save(self, cp: Checkpoint) -> Checkpoint:
        cp.saved_at = utcnow().isoformat(timespec="microseconds")   # unique ordering key
        cp.commit = cp.commit or _git_head()
        body = cp.payload()
        digest = _sha(body)
        try:
            self.store.append("RunState", {"saved_at": cp.saved_at, "run_id": cp.run_id, "video_id": cp.video_id,
                                           "stage": cp.stage, "status": cp.status, "payload": body, "sha256": digest})
        except Exception as e:  # noqa: BLE001
            self._write_local(cp, verified=False)
            raise CheckpointError(f"durable checkpoint write failed: {type(e).__name__}: {e}") from e
        back = self.latest(cp.run_id)
        if back is None or back.saved_at != cp.saved_at or _sha(back.payload()) != digest:
            self._write_local(cp, verified=False)
            raise CheckpointError("durable checkpoint read-back verification failed")
        self._write_local(cp, verified=True)
        return cp

    def _write_local(self, cp: Checkpoint, verified: bool) -> None:
        d = self.dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "current_run.json").write_text(json.dumps({"run_id": cp.run_id, "video_id": cp.video_id,
                                                         "stage": cp.stage, "status": cp.status,
                                                         "saved_at": cp.saved_at, "verified": verified}, indent=1))
        (d / "current_task.json").write_text(json.dumps(cp.task, indent=1))
        (d / "checkpoint.json").write_text(cp.payload())
        (d / "next_actions.json").write_text(json.dumps(cp.next_actions, indent=1))
        with open(d / "decisions.jsonl", "a", encoding="utf-8") as fh:
            for x in cp.decisions[-5:]:
                fh.write(json.dumps({"at": cp.saved_at, "run_id": cp.run_id, "decision": x}, default=str) + "\n")
        with open(d / "errors.jsonl", "a", encoding="utf-8") as fh:
            for x in cp.errors[-5:]:
                fh.write(json.dumps({"at": cp.saved_at, "run_id": cp.run_id, "error": x}, default=str) + "\n")
        (d / "session_summary.md").write_text(summary_md(cp, verified))
        if verified:
            (d / "last_verified_commit.txt").write_text(cp.commit + "\n")

    # ---------------- read
    def latest(self, run_id: str | None = None) -> Checkpoint | None:
        rows = [r for r in self.store.rows("RunState") if r.get("payload") and (not run_id or r.get("run_id") == run_id)]
        order = sorted(enumerate(rows), key=lambda ir: (ir[1].get("saved_at", ""), ir[0]), reverse=True)
        for _, r in order:
            if _sha(r["payload"]) != r.get("sha256"):
                continue                                  # corrupt row: never trusted, try older
            return Checkpoint(**json.loads(r["payload"]))
        return None

    def restore(self) -> Checkpoint | None:
        """Fresh container: rebuild runtime/state from the durable store (verified rows only)."""
        cp = self.latest()
        if cp:
            self._write_local(cp, verified=True)
        return cp

    def require_verified(self, action: str) -> Checkpoint:
        """Fail closed: destructive/external actions need a verified durable checkpoint first."""
        cp = self.latest()
        if cp is None:
            raise CheckpointError(f"refusing {action}: no verified durable checkpoint")
        if cp.status == "failed":
            raise CheckpointError(f"refusing {action}: last checkpoint is failed ({cp.stage})")
        return cp


def summary_md(cp: Checkpoint, verified: bool) -> str:
    lines = [f"# Session summary ({'verified' if verified else 'UNVERIFIED'})",
             f"- run `{cp.run_id}` · video `{cp.video_id or '-'}` · stage **{cp.stage}** · status **{cp.status}**",
             f"- saved {cp.saved_at} · commit `{cp.commit[:12]}`", "", "## Next actions"]
    lines += [f"1. `{a}`" if isinstance(a, str) else f"1. {json.dumps(a)}" for a in cp.next_actions] or ["- none"]
    if cp.blockers:
        lines += ["", "## Blockers", *[f"- {b}" for b in cp.blockers]]
    if cp.completed:
        lines += ["", "## Completed (latest)", *[f"- {c}" for c in cp.completed[-10:]]]
    return "\n".join(lines) + "\n"
