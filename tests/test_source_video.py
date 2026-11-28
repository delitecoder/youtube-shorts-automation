"""Existing-video policy: integrity, eligibility, rights, and byte-for-byte preservation."""
import subprocess

import pytest

from studio.storage.drive import sha256_file
from studio.video.source_video import assess, rights_declaration


def make_video(path, w, h, secs, audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration={secs}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=duration={secs}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac"] if audio else []
    subprocess.run(cmd + ["-shortest", str(path)], check=True)
    return path


@pytest.fixture(scope="module")
def vertical(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("v") / "vertical.mp4", 1080, 1920, 3)


def test_eligible_vertical_video_and_bytes_unchanged(vertical):
    before = sha256_file(vertical)
    el = assess(vertical, "My clip\nRIGHTS: OWNED")
    assert el.ok, el.reasons
    assert el.info["display"] == [1080, 1920]
    assert sha256_file(vertical) == before


def test_missing_rights_declaration_needs_review(vertical):
    el = assess(vertical, "just a description")
    assert not el.ok and any("rights" in r for r in el.reasons)


def test_licensed_declaration_requires_note():
    assert rights_declaration("RIGHTS: LICENSED CC-BY 4.0 from Jane") == "LICENSED CC-BY 4.0 from Jane"
    assert rights_declaration("RIGHTS: LICENSED") == ""


def test_landscape_rejected_not_modified(tmp_path):
    p = make_video(tmp_path / "land.mp4", 320, 180, 1)
    before = sha256_file(p)
    el = assess(p, "RIGHTS: OWNED")
    assert not el.ok and any("landscape" in r for r in el.reasons)
    assert sha256_file(p) == before


def test_too_long_rejected(tmp_path):
    p = make_video(tmp_path / "long.mp4", 64, 112, 181, audio=False)
    el = assess(p, "RIGHTS: OWNED")
    assert not el.ok and any("duration" in r for r in el.reasons)


def test_square_is_eligible_with_warning(tmp_path):
    el = assess(make_video(tmp_path / "sq.mp4", 720, 720, 1), "RIGHTS: OWNED")
    assert el.ok and any("1080x1920" in w for w in el.warnings)


def test_corrupt_file_rejected(tmp_path, vertical):
    p = tmp_path / "corrupt.mp4"
    data = vertical.read_bytes()
    p.write_bytes(data[: len(data) // 3])
    el = assess(p, "RIGHTS: OWNED")
    assert not el.ok


def test_not_a_video(tmp_path):
    p = tmp_path / "notes.mp4"
    p.write_text("hello")
    el = assess(p, "RIGHTS: OWNED")
    assert not el.ok and any("unreadable" in r for r in el.reasons)
