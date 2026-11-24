"""End-to-end DRY_RUN flows through the real CLI (subprocess) and the Cloud Run endpoint."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"


def run(rt, *args, check=True):
    env = {**os.environ, "YTA_RUNTIME_DIR": str(rt), "DRY_RUN": "true", "TTS_PROVIDER": "tone",
           "PYTHONPATH": str(ROOT / "src")}
    for k in ("YTA_PIPELINE_OAUTH", "SPREADSHEET_ID", "UPLOAD_ENABLED"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-m", "ytauto", *args], capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise AssertionError(p.stdout + p.stderr)
    return json.loads(p.stdout.strip().splitlines()[-1])


@pytest.mark.slow
def test_generated_flow_dry_run(tmp_path):
    rt = tmp_path / "rt"
    assert run(rt, "doctor")["ok"]
    assert run(rt, "discover")["action"] == "research"
    vid = run(rt, "new-item")["video_id"]
    research = {"topic": "Hash tables", "topic_key": "hash-tables", "category": "programming",
                "angle": "one-step lookups", "sources": [
                    {"title": f"s{i}", "url": f"https://example.com/{i}", "publisher": "p",
                     "published": "2026-01-01"} for i in range(3)],
                "facts": [{"claim": "dicts are hash tables", "source_urls": ["https://example.com/0"],
                           "confidence": "high"}]}
    (tmp_path / "r.json").write_text(json.dumps(research))
    assert run(rt, "record-research", "--video-id", vid, "--file", str(tmp_path / "r.json"))["ok"]
    sb = json.loads((FIX / "sample_storyboard.json").read_text()) | {"video_id": vid}
    (tmp_path / "sb.json").write_text(json.dumps(sb))
    r = run(rt, "render", "--video-id", vid, "--storyboard", str(tmp_path / "sb.json"))
    assert r["ok"] and r["qc"]["ok"]
    meta = {"title": sb["titles"][0], "description": "How hash tables find data in one step. #Shorts #Python",
            "tags": ["hash table", "python", "data structures"], "pinned_comment": sb["closing_question"]}
    (tmp_path / "m.json").write_text(json.dumps(meta))
    (tmp_path / "rev.json").write_text(json.dumps({"verdict": "fail", "issues": ["x"]}))
    bad = run(rt, "submit", "--video-id", vid, "--metadata", str(tmp_path / "m.json"),
              "--review", str(tmp_path / "rev.json"), check=False)
    assert not bad["ok"]
    (tmp_path / "rev.json").write_text(json.dumps({"verdict": "pass", "issues": []}))
    r = run(rt, "submit", "--video-id", vid, "--metadata", str(tmp_path / "m.json"),
            "--review", str(tmp_path / "rev.json"))
    assert r["ok"] and r["state"] == "AWAITING_APPROVAL"
    actions = (rt / "dry_run_actions.jsonl").read_text()
    assert "02_Generated" in actions and "03_Awaiting_Approval" in actions
    # one item per cycle: nothing new while one is pending
    assert run(rt, "discover")["action"] == "none"


def test_existing_video_flow_dry_run(tmp_path):
    from test_source_video import make_video
    rt = tmp_path / "rt"
    v = make_video(tmp_path / "clip.mp4", 1080, 1920, 2)
    before = v.read_bytes()
    r = run(rt, "prepare-existing", "--local-path", str(v), "--description", "RIGHTS: OWNED")
    assert r["ok"] and r["state"] == "QUALITY_CHECK"
    assert v.read_bytes() == before
    again = run(rt, "prepare-existing", "--local-path", str(v), "--description", "RIGHTS: OWNED")
    assert again["duplicate_of"] == [r["video_id"]]
    v2 = make_video(tmp_path / "land.mp4", 320, 180, 1)
    r2 = run(rt, "prepare-existing", "--local-path", str(v2), "--description", "RIGHTS: OWNED")
    assert r2["state"] == "NEEDS_MANUAL_REVIEW"


def test_doctor_blocks_upload_flag_in_pipeline(tmp_path):
    env = {**os.environ, "YTA_RUNTIME_DIR": str(tmp_path), "UPLOAD_ENABLED": "true",
           "PYTHONPATH": str(ROOT / "src")}
    p = subprocess.run([sys.executable, "-m", "ytauto", "doctor"], capture_output=True, text=True, env=env)
    assert p.returncode == 2 and "UPLOAD_ENABLED" in p.stdout


# ------------------------------------------------------------------ Cloud Run endpoint
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "k" * 40)
    sys.path.insert(0, str(ROOT / "cloudrun"))
    import service

    calls = []

    class Stub:
        def publish(self, vid):
            calls.append(vid)
            from ytauto.publishing.publisher import PublishResult
            return PublishResult("dry_run", "ok")

    monkeypatch.setattr(service, "get_publisher", lambda: Stub())
    service.nonces = service.NonceCache()
    return service.app.test_client(), calls


def signed(body, secret="k" * 40, nonce="a" * 32, ts=None):
    from ytauto.approval.webhook_auth import sign
    ts = ts or str(int(time.time()))
    return {"X-YTA-Timestamp": ts, "X-YTA-Nonce": nonce, "X-YTA-Signature": sign(secret, ts, nonce, body)}


def test_service_accepts_signed_request(client):
    c, calls = client
    body = b'{"video_id": "VIDEO-20261124-001"}'
    r = c.post("/v1/approved", data=body, headers=signed(body))
    assert r.status_code == 200 and calls == ["VIDEO-20261124-001"]


def test_service_rejects_unsigned_and_replayed(client):
    c, calls = client
    body = b'{"video_id": "VIDEO-20261124-001"}'
    assert c.post("/v1/approved", data=body).status_code == 401
    h = signed(body)
    assert c.post("/v1/approved", data=body, headers=h).status_code == 200
    assert c.post("/v1/approved", data=body, headers=h).status_code == 401   # replay
    assert calls == ["VIDEO-20261124-001"]


def test_service_rejects_bad_video_id(client):
    c, calls = client
    body = b'{"video_id": "../../etc"}'
    assert c.post("/v1/approved", data=body, headers=signed(body)).status_code == 400
    assert not calls


def test_resume_after_interrupted_run(tmp_path):
    rt = tmp_path / "rt"
    vid = run(rt, "new-item")["video_id"]
    wd = rt / "work" / vid
    wd.mkdir(parents=True)
    (wd / "research.json").write_text('{"topic": "x"}')
    (wd / "voice.wav").write_bytes(b"RIFF")                    # not checkpointed
    assert run(rt, "checkpoint", "--video-id", vid)["ok"]
    import shutil
    shutil.rmtree(wd)                                         # simulate a fresh session
    d = run(rt, "discover")
    assert d["action"] == "resume" and d["video_id"] == vid
    r = run(rt, "restore", "--video-id", vid)
    assert r["restored"] == ["research.json"] and (wd / "research.json").exists()
    assert r["next_step"] == "research"
