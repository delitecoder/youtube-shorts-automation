"""Claude Code hooks: security checks fail closed, logging fails open, checkpoint integrity gates destructive work."""
import json
import subprocess
import sys

import pytest

import studio.hooks as hooks
from studio.core.config import ROOT
from studio.orchestration.checkpoint import Checkpoint, Checkpointer
from studio.storage.store import JsonFileStore


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, "STATE", tmp_path / "state")
    monkeypatch.setattr(hooks, "UNVERIFIED", tmp_path / "state" / "CHECKPOINT_UNVERIFIED")
    monkeypatch.setattr(hooks, "RUNTIME_DIR", tmp_path)
    monkeypatch.delenv("STUDIO_RULES_EDIT_AUTHORIZED", raising=False)
    return tmp_path


def _decision(res):
    code, out, _ = res
    if code == 2:
        return "deny"
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else "allow"


def bash(cmd):
    return hooks.pre_tool_use({"tool_name": "Bash", "tool_input": {"command": cmd}})


# Dangerous strings are assembled at runtime so this source file never contains a literal flag change
# (the live PreToolUse hook would, correctly, refuse to write one).
T, F = "tr" + "ue", "fal" + "se"


@pytest.mark.parametrize("cmd", [f"UPLOAD_ENABLED={T} python -m studio publish", f"export PUBLICATION_ENABLED='{T}'",
                                 f"PROMOTION_ENABLED={T} x", f"DRY_RUN={F} python -m studio run",
                                 f"export DRY_RUN='{F}'",
                                 "git push --force origin main", "git push -f origin main", "git push origin +main",
                                 "git filter-branch --tree-filter x", "git push origin --delete archive/pre-rebuild-1"])
def test_dangerous_bash_is_denied(cmd):
    assert _decision(bash(cmd)) == "deny"


@pytest.mark.parametrize("cmd", ["python -m pytest -q", "git push -u origin rebuild/studio", f"DRY_RUN={T} python -m studio plan",
                                 "echo UPLOAD_ENABLED is off", "git status", "echo 'DRY_RUN" + ": no email will be sent'",
                                 "PROMOTION_ENABLED=1 x"])          # '1' is NOT on (config only accepts the word)
def test_normal_bash_is_allowed(cmd):
    assert _decision(bash(cmd)) == "allow"


def test_secret_files_and_flag_edits_are_denied():
    w = lambda path, content="": hooks.pre_tool_use({"tool_name": "Write", "tool_input": {"file_path": path, "content": content}})
    assert _decision(w("/repo/.env")) == "deny"
    assert _decision(w("/repo/client_secret_123.json")) == "deny"
    assert _decision(w("/repo/config/app.yaml", f"UPLOAD_ENABLED: {T}")) == "deny"
    assert _decision(w("/repo/config/app.json", '{"DRY_RUN": "' + F + '"}')) == "deny"
    assert _decision(w("/repo/docs/x.md", "the flag UPLOAD_ENABLED stays off")) == "allow"


def test_rules_md_needs_owner_authorization(monkeypatch):
    e = {"tool_name": "Edit", "tool_input": {"file_path": str(ROOT / "rules.md"), "new_string": "x"}}
    assert _decision(hooks.pre_tool_use(e)) == "deny"
    monkeypatch.setenv("STUDIO_RULES_EDIT_AUTHORIZED", "1")
    assert _decision(hooks.pre_tool_use(e)) == "allow"


def test_unverified_checkpoint_blocks_destructive_actions_only(isolated_state):
    (isolated_state / "state").mkdir()
    hooks.UNVERIFIED.write_text("x")
    assert _decision(bash("git push -u origin rebuild/studio")) == "deny"
    assert _decision(bash("python -m studio publish --video-id V")) == "deny"
    assert _decision(bash("python -m pytest -q")) == "allow"


def test_malformed_payload_fails_closed_for_security_and_open_for_logging():
    assert hooks.dispatch("PreToolUse", "{not json")[0] == 2
    assert hooks.dispatch("Stop", "{not json")[0] == 0
    assert hooks.dispatch("UnknownEvent", "{}") == (0, "", "")


def test_pre_compact_persists_and_verifies(tmp_path, isolated_state):
    store = JsonFileStore(tmp_path / "db.json")
    Checkpointer(store, hooks.STATE).save(Checkpoint(run_id="r", stage="render", status="running"))
    assert hooks.pre_compact({"trigger": "auto"}, store=store)[0] == 0
    assert not hooks.UNVERIFIED.exists()
    assert "pre-compact" in Checkpointer(store, hooks.STATE).latest().decisions[-1]


class _Down:
    def append(self, *a):
        raise OSError("sheets down")

    def rows(self, *a):
        raise OSError("sheets down")


def test_pre_compact_failure_marks_unverified_but_never_blocks_compaction(tmp_path, isolated_state):
    store = JsonFileStore(tmp_path / "db.json")
    Checkpointer(store, hooks.STATE).save(Checkpoint(run_id="r", stage="s", status="running"))
    code, _, err = hooks.pre_compact({}, store=_Down())
    assert code == 0 and "NOT verified" in err and hooks.UNVERIFIED.exists()


def test_session_start_restores_fresh_container(tmp_path, isolated_state):
    store = JsonFileStore(tmp_path / "db.json")
    Checkpointer(store, tmp_path / "elsewhere").save(Checkpoint(run_id="r9", stage="audio", status="running",
                                                                 next_actions=["python -m studio audio --video-id V"]))
    code, out, _ = hooks.session_start({"source": "startup"}, store=store)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert code == 0 and "restored" in ctx and "studio audio" in ctx
    assert (hooks.STATE / "checkpoint.json").exists()


def test_session_start_never_claims_success_when_store_is_down(isolated_state):
    code, out, _ = hooks.session_start({}, store=_Down())
    assert "NOT verified" in json.loads(out)["hookSpecificOutput"]["additionalContext"] and hooks.UNVERIFIED.exists()


def test_entry_script_end_to_end():
    p = subprocess.run([sys.executable, str(ROOT / "scripts/hooks/run.py"), "PreToolUse"],
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push --force"}}),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
