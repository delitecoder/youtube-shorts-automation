"""CLI structure: every registered command has a handler; new commands work in a fresh process; no import cycles."""
import importlib
import json
import os
import pkgutil
import subprocess
import sys

import studio
from studio.cli.main import build_parser, handler


def _commands():
    ap = build_parser()
    sub = next(a for a in ap._actions if a.__class__.__name__ == "_SubParsersAction")
    return sorted(sub.choices)


def test_every_command_has_exactly_one_handler():
    cmds = _commands()
    assert len(cmds) >= 35
    for c in cmds:
        assert callable(handler(c)), c


def test_every_command_parses_help():
    ap = build_parser()
    for c in _commands():
        try:
            ap.parse_args([c, "--help"])
        except SystemExit as e:
            assert e.code == 0, c


def test_all_modules_import_cleanly_in_isolation():
    """Importing every module on its own (fresh process per batch) proves there are no circular imports."""
    names = [m.name for m in pkgutil.walk_packages(studio.__path__, "studio.") if not m.name.endswith("__main__")]
    code = "import importlib,sys\n" + "".join(
        f"importlib.import_module({n!r})\n" for n in names)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": "src"}, timeout=300)
    assert p.returncode == 0, p.stderr[-2000:]
    for n in names:                                    # and each one alone, in this process order-independently
        importlib.import_module(n)


def _run(rt, *args):
    env = {**os.environ, "PYTHONPATH": "src", "DRY_RUN": "true", "YTA_RUNTIME_DIR": str(rt), "YTA_SKIP_DRIVE_KEYS": "true",
           "YTA_PIPELINE_OAUTH": "", "SPREADSHEET_ID": ""}
    p = subprocess.run([sys.executable, "-m", "studio", *args], capture_output=True, text=True, env=env, timeout=120)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_state_save_verify_resume_in_fresh_processes(tmp_path):
    f = tmp_path / "cp.json"
    f.write_text(json.dumps({"run_id": "run-x", "stage": "render", "next_actions": ["python -m studio render --video-id V"]}))
    code, r = _run(tmp_path, "state", "save", "--file", str(f))
    assert code == 0 and r["verified"]
    code, r = _run(tmp_path, "state", "verify")
    assert code == 0 and r["stage"] == "render"
    import shutil
    shutil.rmtree(tmp_path / "state")                   # container replaced: local cache gone
    code, r = _run(tmp_path, "resume")
    assert r["action"] == "resume" and r["next_actions"] == ["python -m studio render --video-id V"]
    assert (tmp_path / "state" / "checkpoint.json").exists()


def test_route_and_probe_commands(tmp_path):
    code, r = _run(tmp_path, "route", "--task", "scriptwriting")
    assert r["model"] == "sonnet"
    code, r = _run(tmp_path, "probe", "--offline")
    assert r["cpu_count"] >= 1 and "credentials_present" in r and r["workers"] >= 1


def test_legacy_ytauto_entry_point_still_works(tmp_path):
    env = {**os.environ, "PYTHONPATH": "src", "DRY_RUN": "true", "YTA_RUNTIME_DIR": str(tmp_path), "YTA_SKIP_DRIVE_KEYS": "true"}
    p = subprocess.run([sys.executable, "-m", "ytauto", "route", "--task", "research"], capture_output=True, text=True,
                       env=env, timeout=120)
    assert json.loads(p.stdout)["model"] == "haiku" and "deprecated" in p.stderr


def test_plan_writes_style_and_experiment_into_storyboard(tmp_path):
    sb = tmp_path / "sb.json"
    sb.write_text(json.dumps({"video_id": "VIDEO-20261128-001", "scenes": [{"narration": "x", "visual": {"type": "title"}}],
                              "hooks": ["Why?"], "chosen_hook": 0}))
    code, r = _run(tmp_path, "plan", "--video-id", "VIDEO-20261128-001", "--category", "system design",
                   "--storyboard", str(sb))
    data = json.loads(sb.read_text())
    assert code == 0 and r["style"] and data["style_id"].startswith(r["style"]) and data["palette"]
    assert data["experiment"]["mode"] in ("proven", "variation", "exploration")
    code, r2 = _run(tmp_path, "plan", "--video-id", "VIDEO-20261128-001")
    assert r2["policy"]["reused"] is True                      # idempotent per video
    code, r3 = _run(tmp_path, "experiment", "list")
    assert r3["ok"]
