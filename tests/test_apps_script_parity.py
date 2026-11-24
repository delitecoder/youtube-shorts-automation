"""Runs the Apps Script validator (approval_watcher.gs) in Node with the same cases as the Python
gate, so the two independent implementations cannot silently diverge."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

GS = Path(__file__).resolve().parents[1] / "apps_script" / "approval_watcher.gs"
VID = "VIDEO-20261124-001"

CASES = [
    ("yes", f"YES {VID}", {}, "YES"),
    ("no", f"NO {VID}", {}, "NO"),
    ("quoted-sig", f"YES {VID}\n--\nMe\nOn Tue, Bot wrote:\n> x", {}, "YES"),
    ("lower", f"yes {VID}", {}, "malformed"),
    ("extra", f"YES {VID} ok", {}, "malformed"),
    ("two", f"YES {VID}\nNO {VID}", {}, "ambiguous"),
    ("quote-only", f"> YES {VID}", {}, "empty"),
    ("on-wrote", f"On Mon, Bob wrote:\n> YES {VID}", {}, "empty"),
    ("wrong-id", "YES VIDEO-20261124-002", {}, "this thread is for"),
    ("sender", f"YES {VID}", {"sender": "Mallory <m@evil.com>"}, "approver"),
    ("spoof", f"YES {VID}", {"label_ids": ["INBOX"]}, "authentication"),
    ("old", f"YES {VID}", {"minutes": -5}, "predates"),
    ("expired", f"YES {VID}", {"minutes": 24 * 60 + 1}, "expired"),
    ("replay", f"YES {VID}", {"used": True}, "replay"),
    ("thread", f"YES {VID}", {"thread_id": "other"}, "thread"),
]

HARNESS = r"""
const vm = require('vm'); const fs = require('fs');
const ctx = { PropertiesService: { getScriptProperties: () => ({ getProperty: k => ({APPROVER_IS_OWNER: 'true'})[k] || null }) },
              console };
vm.createContext(ctx); vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const cases = JSON.parse(process.argv[3]); const out = {};
const rec = { state: 'AWAITING_APPROVAL', video_id: '%VID%', approval_thread_id: 't1',
              approval_request_message_id: 'req', approval_requested_at: new Date(Date.now() - 3 * 86400e3).toISOString() };
for (const c of cases) {
  const o = c.opts; const req = new Date(rec.approval_requested_at);
  const ev = { message_id: 'm1', thread_id: o.thread_id || 't1', sender: o.sender || 'Me <owner@example.com>',
               sent_at: new Date(req.getTime() + (o.minutes === undefined ? 10 : o.minutes) * 60e3),
               body: c.body, label_ids: o.label_ids || ['SENT'], auth_results: '' };
  try { out[c.name] = vm.runInContext('validate_', ctx)(rec, ev, 'owner@example.com', o.used ? {m1: true} : {}); }
  catch (e) { out[c.name] = 'ERR:' + String(e); }
}
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_apps_script_validator_matches_python_rules(tmp_path):
    js = tmp_path / "h.js"
    js.write_text(HARNESS.replace("%VID%", VID))
    cases = [{"name": n, "body": b, "opts": o} for n, b, o, _ in CASES]
    p = subprocess.run(["node", str(js), str(GS), json.dumps(cases)], capture_output=True, text=True, check=True)
    res = json.loads(p.stdout)
    for name, _, _, expect in CASES:
        got = res[name]
        if expect in ("YES", "NO"):
            assert got == expect, (name, got)
        else:
            assert got.startswith("ERR:") and expect in got, (name, got)
