"""Claude Code hook logic (pure functions; scripts/hooks/run.py is the thin entry point).

Schemas: https://code.claude.com/docs/en/hooks. Each handler takes the stdin payload dict and returns
(exit_code, stdout, stderr). Policy:
- security / authorization checks FAIL CLOSED (PreToolUse deny);
- checkpoint integrity: an unverified checkpoint blocks destructive actions (fail closed), but compaction
  itself is never blocked (a stuck session is worse than a recoverable one);
- logging-only hooks FAIL OPEN (never break a session because a log write failed).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from studio.core.config import ROOT, RUNTIME_DIR

STATE = Path(RUNTIME_DIR) / "state"
UNVERIFIED = STATE / "CHECKPOINT_UNVERIFIED"
PROTECTED_ON = re.compile(r"\b(UPLOAD_ENABLED|PUBLICATION_ENABLED|PROMOTION_ENABLED)\s*[=:]\s*['\"]?(true|1|yes)\b", re.I)
DRYRUN_OFF = re.compile(r"\bDRY_RUN\s*[=:]\s*['\"]?(false|0|no)\b", re.I)
FORCE_PUSH = re.compile(r"\bgit\s+push\b[^|;&]*(\s--force(-with-lease)?\b|\s-f\b|\s\+\S+)")
HISTORY_REWRITE = re.compile(r"\bgit\s+(filter-branch|filter-repo|push\s+[^|;&]*--delete\b[^|;&]*\b(main|archive/|backup/))")
DESTRUCTIVE = re.compile(r"\bgit\s+push\b|\bstudio\s+(publish|approve|upload)\b|-m\s+studio\s+(publish|approve|upload)\b"
                         r"|\brm\s+-[a-z]*r[a-z]*f?\s+(/|\.|src|docs|config|tests)\b")
SECRET_FILE = re.compile(r"(^|/)(\.env(\.[^/]*)?|client_secret[^/]*\.json|[^/]*credentials[^/]*\.json|"
                         r"service-account[^/]*\.json|[^/]*token[^/]*\.json|[^/]*\.pem|[^/]*\.key)$")


def _deny(reason: str) -> tuple[int, str, str]:
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                  "permissionDecisionReason": reason}}
    return 0, json.dumps(out), ""


def pre_tool_use(p: dict) -> tuple[int, str, str]:
    tool, ti = p.get("tool_name", ""), p.get("tool_input") or {}
    if tool == "Bash":
        cmd = ti.get("command", "")
        if PROTECTED_ON.search(cmd) or DRYRUN_OFF.search(cmd):
            return _deny("Protected flags (DRY_RUN/UPLOAD/PUBLICATION/PROMOTION) are owner-only settings (rules.md).")
        if FORCE_PUSH.search(cmd) or HISTORY_REWRITE.search(cmd):
            return _deny("Force-push, history rewrite and deleting main/backup refs are forbidden (rules.md).")
        if DESTRUCTIVE.search(cmd) and UNVERIFIED.exists():
            return _deny("The last checkpoint is UNVERIFIED. Run `python -m studio checkpoint --verify` "
                         "until it passes before pushing, publishing or deleting.")
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(ti.get("file_path") or ti.get("notebook_path") or "")
        if SECRET_FILE.search(path):
            return _deny(f"Writing secret/credential files is forbidden ({Path(path).name}).")
        text = " ".join(str(ti.get(k, "")) for k in ("content", "new_string", "new_source"))
        if PROTECTED_ON.search(text) or DRYRUN_OFF.search(text):
            return _deny("This edit would turn a protected flag on (owner-only, rules.md).")
        if Path(path).name == "rules.md" and Path(path).resolve().parent == ROOT.resolve() \
                and os.environ.get("STUDIO_RULES_EDIT_AUTHORIZED") != "1":
            return _deny("rules.md is the owner's contract. Edits need STUDIO_RULES_EDIT_AUTHORIZED=1 set by the owner.")
    return 0, "", ""


def _ledger(event: str, p: dict, **extra) -> None:
    try:
        d = Path(RUNTIME_DIR) / "logs"
        d.mkdir(parents=True, exist_ok=True)
        from studio.core.state import iso, utcnow
        rec = {"at": iso(utcnow()), "event": event, "session": p.get("session_id", ""), **extra}
        with open(d / "hook_ledger.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 - logging only: fail open
        pass


def post_tool_use(p: dict) -> tuple[int, str, str]:
    ti = p.get("tool_input") or {}
    cmd = ti.get("command", "") if p.get("tool_name") == "Bash" else ""
    if re.search(r"-m\s+studio\b|\bgit\s+(commit|push)\b", cmd):
        _ledger("tool", p, tool="Bash", command=cmd[:160])
    return 0, "", ""


def subagent_stop(p: dict) -> tuple[int, str, str]:
    _ledger("subagent_stop", p, agent_type=p.get("agent_type", ""), agent_id=p.get("agent_id", ""))
    return 0, "", ""


def stop(p: dict) -> tuple[int, str, str]:
    _ledger("stop", p)
    return 0, "", ""


def _store():
    from studio.orchestration.context import Ctx
    return Ctx().store


def pre_compact(p: dict, store=None) -> tuple[int, str, str]:
    """Re-save the current local checkpoint to the durable store and verify it. Never blocks compaction."""
    from studio.orchestration.checkpoint import Checkpoint, Checkpointer
    f = STATE / "checkpoint.json"
    if not f.exists():
        return 0, "", "pre-compact: no local checkpoint to persist"
    try:
        cp = Checkpoint(**json.loads(f.read_text()))
        cp.decisions = [*cp.decisions, f"pre-compact save ({p.get('trigger', p.get('matcher', ''))})"][-30:]
        Checkpointer(store or _store(), STATE).save(cp)
        UNVERIFIED.unlink(missing_ok=True)
        return 0, "", ""
    except Exception as e:  # noqa: BLE001
        STATE.mkdir(parents=True, exist_ok=True)
        UNVERIFIED.write_text(f"{type(e).__name__}: {str(e)[:300]}\n")
        return 0, "", f"pre-compact: checkpoint NOT verified ({type(e).__name__}); destructive actions blocked"


def session_start(p: dict, store=None) -> tuple[int, str, str]:
    """Recovery context: rules pointer, durable checkpoint (restored if local copy is gone), next actions."""
    from studio.orchestration.checkpoint import Checkpointer, summary_md
    lines = ["Studio session start. Read rules.md and CLAUDE.md first; resume from the checkpoint below, "
             "not from memory."]
    fresh = not (STATE / "checkpoint.json").exists()
    try:
        ck = Checkpointer(store or _store(), STATE)
        cp = ck.restore() if fresh else ck.latest()
        if cp:
            lines += [f"Container {'fresh: state restored from durable store' if fresh else 'has local state'}.",
                      summary_md(cp, verified=True)]
            UNVERIFIED.unlink(missing_ok=True)
        else:
            lines.append("No durable checkpoint found: start a new run with `python -m studio plan`.")
    except Exception as e:  # noqa: BLE001 - context only; never claims recovery succeeded
        lines.append(f"WARNING: durable checkpoint unavailable ({type(e).__name__}). Recovery NOT verified; "
                     "destructive actions stay blocked until `python -m studio checkpoint --verify` passes.")
        STATE.mkdir(parents=True, exist_ok=True)
        UNVERIFIED.write_text("session start could not verify the durable checkpoint\n")
    ctx = "\n".join(lines)[:9000]
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}
    return 0, json.dumps(out), ""


HANDLERS = {"PreToolUse": pre_tool_use, "PostToolUse": post_tool_use, "SubagentStop": subagent_stop,
            "Stop": stop, "PreCompact": pre_compact, "SessionStart": session_start}


def dispatch(event: str, raw: str) -> tuple[int, str, str]:
    try:
        p = json.loads(raw or "{}")
    except json.JSONDecodeError:
        p = {}
        if event == "PreToolUse":        # malformed payload on a security hook: fail closed
            return 2, "", "malformed hook payload"
    fn = HANDLERS.get(event)
    if fn is None:
        return 0, "", ""
    try:
        return fn(p)
    except Exception as e:  # noqa: BLE001
        if event == "PreToolUse":
            return 2, "", f"hook error (fail closed): {type(e).__name__}"
        return 0, "", f"hook error (ignored): {type(e).__name__}"
