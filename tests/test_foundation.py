"""Foundation: YAML config, model router, ledger, resource manager, durable checkpoints, structured logs."""
import json
import shutil

import pytest
import yaml

from studio.core import conf
from studio.core.model_router import RoutingError, route, table
from studio.observability import ledger
from studio.observability.log import log, redact
from studio.orchestration.checkpoint import Checkpoint, Checkpointer, CheckpointError
from studio.resource_manager.probe import ResourceError, guard, probe, workers
from studio.storage.store import JsonFileStore


@pytest.fixture
def store(tmp_path):
    return JsonFileStore(tmp_path / "state.json")


# ---------------- config
def test_all_config_files_load():
    for name in ("app", "models", "resources", "audio", "video", "experiments"):
        assert isinstance(conf.load(name), dict)


def test_yaml_cannot_set_protected_flags(tmp_path):
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump({"x": {"UPLOAD_ENABLED": True}}))
    with pytest.raises(ValueError, match="protected"):
        conf.load("bad", base=tmp_path)


# ---------------- model router
def test_routes_by_task_and_deterministic_tasks_get_no_model():
    assert route("topic_classification").model == "haiku"
    assert route("scriptwriting").model == "sonnet"
    assert route("architecture").model == "opus"
    assert route("audio_qc").model is None


def test_escalation_is_bounded_and_reasoned():
    r = route("topic_classification", attempt=1, why="validation_failed")
    assert r.model == "sonnet" and "escalated" in r.reason
    with pytest.raises(RoutingError):
        route("topic_classification", attempt=1, why="i feel like it")
    with pytest.raises(RoutingError):
        route("topic_classification", attempt=5, why="validation_failed")


def test_unavailable_model_falls_back_without_upgrading():
    r = route("architecture", available={"sonnet", "haiku"})
    assert r.model == "sonnet" and "fallback" in r.reason
    with pytest.raises(RoutingError):
        route("scriptwriting", available=set())


def test_unknown_task_is_an_error_and_table_covers_everything():
    with pytest.raises(RoutingError):
        route("write_poem")
    t = table()
    assert {r["task"] for r in t} >= {"scriptwriting", "audio_qc"}
    assert all(r["model"] in {"haiku", "sonnet", "opus", "python"} for r in t)


# ---------------- ledger
def test_ledger_never_invents_tokens(store):
    ledger.record(store, run_id="r1", stage="script", actor="storywriter", model="sonnet", wall_s=12.3)
    ledger.record(store, run_id="r1", stage="render", actor="python", wall_s=90, tokens_in=None)
    rows = store.rows("Ledger")
    assert rows[0]["tokens_in"] == "" and rows[0]["tokens_out"] == ""
    s = ledger.summary(store, "r1")
    assert s["by_model"]["sonnet"]["tokens_unknown_steps"] == 1 and s["by_model"]["sonnet"]["tokens_known"] == 0


# ---------------- resources
def test_probe_reports_environment_without_secret_values(monkeypatch):
    monkeypatch.setenv("GOOGLE_TTS_API_KEY", "sk-should-not-appear")
    p = probe(check_network=False)
    assert p["cpu_count"] >= 1 and p["ram_total_gb"] > 0
    assert p["credentials_present"]["GOOGLE_TTS_API_KEY"] is True
    assert "sk-should-not-appear" not in json.dumps(p)


def test_workers_limited_by_memory_first():
    cfg = conf.load("resources")
    assert workers(cfg, available_gb=3.0) == 1
    assert workers(cfg, available_gb=100.0) == min(cfg["cpu"]["max_workers"], __import__("os").cpu_count())


def test_guard_blocks_unaffordable_steps():
    cfg = conf.load("resources")
    guard("ok", need_gb=1, cfg=cfg, available_gb=12, disk_free_gb=40, rss_gb=1)
    with pytest.raises(ResourceError, match="free RAM"):
        guard("render", need_gb=10, cfg=cfg, available_gb=11, disk_free_gb=40, rss_gb=0.5)
    with pytest.raises(ResourceError, match="working set"):
        guard("render", need_gb=4, cfg=cfg, available_gb=14, disk_free_gb=40, rss_gb=8)
    with pytest.raises(ResourceError, match="disk"):
        guard("download", need_disk_gb=10, cfg=cfg, available_gb=14, disk_free_gb=12, rss_gb=1)


# ---------------- checkpoints
def _cp(**kw):
    base = dict(run_id="run-1", stage="render", status="running", task={"task_id": "t1"},
                next_actions=["python -m studio render --video-id VIDEO-20261128-001"])
    base.update(kw)
    return Checkpoint(**base)


def test_checkpoint_save_verifies_and_writes_all_state_files(store, tmp_path):
    ck = Checkpointer(store, tmp_path / "state")
    ck.save(_cp(decisions=["use story structure X"]))
    for f in ("current_run.json", "current_task.json", "checkpoint.json", "next_actions.json",
              "decisions.jsonl", "errors.jsonl", "session_summary.md"):
        assert (tmp_path / "state" / f).exists(), f
    assert json.loads((tmp_path / "state" / "current_run.json").read_text())["verified"] is True
    assert "studio render" in (tmp_path / "state" / "session_summary.md").read_text()


def test_restore_after_local_state_is_deleted(store, tmp_path):
    ck = Checkpointer(store, tmp_path / "state")
    ck.save(_cp(stage="audio", status="done"))
    shutil.rmtree(tmp_path / "state")
    fresh = Checkpointer(store, tmp_path / "state")         # new process, empty runtime
    cp = fresh.restore()
    assert cp.stage == "audio" and json.loads((tmp_path / "state" / "next_actions.json").read_text())


def test_corrupt_durable_row_is_never_trusted(store, tmp_path):
    ck = Checkpointer(store, tmp_path / "state")
    ck.save(_cp(stage="script", status="done"))
    ck.save(_cp(stage="render", status="running"))
    data = json.loads((tmp_path / "state.json").read_text())
    rows = data["RunState"]
    rows[-1]["payload"] = rows[-1]["payload"].replace("render", "publish")    # tamper newest row
    (tmp_path / "state.json").write_text(json.dumps(data))
    assert ck.latest().stage == "script"                    # falls back to the last VERIFIED row


class _BrokenStore:
    def append(self, tab, row):
        raise OSError("network down")

    def rows(self, tab):
        return []


def test_failed_checkpoint_blocks_destructive_continuation(tmp_path):
    ck = Checkpointer(_BrokenStore(), tmp_path / "state")
    with pytest.raises(CheckpointError):
        ck.save(_cp())
    assert json.loads((tmp_path / "state" / "current_run.json").read_text())["verified"] is False
    with pytest.raises(CheckpointError, match="refusing upload"):
        ck.require_verified("upload")


def test_failed_status_blocks_destructive_actions(store, tmp_path):
    ck = Checkpointer(store, tmp_path / "state")
    ck.save(_cp(status="failed"))
    with pytest.raises(CheckpointError, match="failed"):
        ck.require_verified("publish")


def test_large_payload_is_trimmed_structurally_not_sliced(store, tmp_path):
    ck = Checkpointer(store, tmp_path / "state")
    cp = _cp(completed=[f"step {i} " + "x" * 400 for i in range(400)])
    ck.save(cp)
    back = ck.latest()
    assert back.completed and json.loads(back.payload())       # valid JSON, newest kept
    assert back.completed[-1].startswith("step 399")


# ---------------- logs
def test_log_redacts_secrets_and_binds_context(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_LOG_STDERR", "0")
    rec = log("render_done", run_id=None, api_key="abc", nested={"refresh_token": "x"}, video_id="V")
    assert rec["api_key"] == "[redacted]" and rec["nested"]["refresh_token"] == "[redacted]"
    assert redact({"password": 1})["password"] == "[redacted]"
