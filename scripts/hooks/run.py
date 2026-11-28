"""Entry point for every Claude Code hook: `python scripts/hooks/run.py <EventName>` (payload on stdin)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from studio.hooks import dispatch  # noqa: E402

code, out, err = dispatch(sys.argv[1] if len(sys.argv) > 1 else "", sys.stdin.read())
if out:
    print(out)
if err:
    print(err, file=sys.stderr)
sys.exit(code)
