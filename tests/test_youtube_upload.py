import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from ytauto.integrations.youtube import UploadError, UploadRequest, build_body, run_resumable


def http_error(code):
    return HttpError(Response({"status": code}), b"{}")


class FakeRequest:
    """Simulates a resumable session: scripted outcomes per next_chunk() call."""

    def __init__(self, script):
        self.script, self.calls = list(script), 0

    def next_chunk(self):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Progress:
    def progress(self):
        return 0.5


def test_retries_transient_errors_on_same_session():
    req = FakeRequest([http_error(503), ConnectionError("reset"), (Progress(), None), (None, {"id": "abc"})])
    sleeps = []
    assert run_resumable(req, sleep=sleeps.append, log=lambda *_: None)["id"] == "abc"
    assert req.calls == 4 and len(sleeps) == 2


def test_non_retriable_error_raises_immediately():
    req = FakeRequest([http_error(403)])
    with pytest.raises(UploadError, match="403"):
        run_resumable(req, sleep=lambda s: None, log=lambda *_: None)
    assert req.calls == 1


def test_gives_up_after_max_retries():
    req = FakeRequest([http_error(500)] * 20)
    with pytest.raises(UploadError, match="retries"):
        run_resumable(req, sleep=lambda s: None, log=lambda *_: None)


def test_body_is_private_with_publish_at(tmp_path):
    b = build_body(UploadRequest(tmp_path / "v.mp4", "T", "D", ["a"], "27", "2026-11-24T14:00:00Z"))
    assert b["status"]["privacyStatus"] == "private"
    assert b["status"]["publishAt"] == "2026-11-24T14:00:00Z"
    assert b["status"]["selfDeclaredMadeForKids"] is False
    b2 = build_body(UploadRequest(tmp_path / "v.mp4", "T", "D", [], "27", None))
    assert "publishAt" not in b2["status"] and b2["status"]["privacyStatus"] == "private"
