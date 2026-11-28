"""rules.md is the owner's operating contract. Changing it needs the owner's explicit authorisation
(hook: STUDIO_RULES_EDIT_AUTHORIZED=1); the checksum below is updated in the same reviewed commit."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_SHA256 = "81b58001062dc5a0b2ba305e176f6dfbf5eaa9b945c9ca815b47a33056e6f83c"


def test_rules_file_unchanged_and_referenced():
    assert hashlib.sha256((ROOT / "rules.md").read_bytes()).hexdigest() == RULES_SHA256, \
        "rules.md changed without updating RULES_SHA256 (owner authorisation required)"
    claude = (ROOT / "CLAUDE.md").read_text()
    assert "rules.md" in claude


def test_rules_cover_required_sections():
    text = (ROOT / "rules.md").read_text()
    for heading in ("Editorial identity", "Research and factual integrity", "Creative quality", "Audio quality",
                    "Copyright and licensing", "Approval and publishing", "Reliability and recovery",
                    "Resource limits", "Self-improvement", "Security"):
        assert heading in text, heading
