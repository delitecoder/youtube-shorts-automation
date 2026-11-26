"""rules.md holds the owner's rules; Claude never edits it. If the OWNER changes it, they update
RULES_SHA256 below (sha256sum rules.md)."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_SHA256 = "cd3d6782c4bc620194ecd4e9e26ae91726d9e0d6d4d9a658e4394a89c18fdc9c"


def test_rules_file_unchanged_and_referenced():
    assert hashlib.sha256((ROOT / "rules.md").read_bytes()).hexdigest() == RULES_SHA256, \
        "rules.md changed: only the owner may edit it (then update RULES_SHA256)"
    claude = (ROOT / "CLAUDE.md").read_text()
    assert "rules.md" in claude and "Never modify `rules.md`" in claude
