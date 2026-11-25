"""The Apps Script chunked (resumable) upload for long videos, run in Node against fake Google services."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

GS = Path(__file__).resolve().parents[1] / "apps_script" / "approval_watcher.gs"

HARNESS = r"""
const vm = require('vm'); const fs = require('fs');
const SIZE = 80 * 1024 * 1024, props = {}, calls = [], logs = [];
let received = 0, failAt = Number(process.argv[3]);
const resp = (code, body, headers) => ({ getResponseCode: () => code, getContentText: () => body || '',
  getAllHeaders: () => headers || {}, getContent: () => body });
const ctx = { console, JSON, Date, Number, String, Math,
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => props[k] || null,
    setProperty: (k, v) => { props[k] = v; }, deleteProperty: k => { delete props[k]; } }) },
  ScriptApp: { getOAuthToken: () => 'tok' },
  UrlFetchApp: { fetch: (url, o) => {
    o = o || {}; calls.push((o.method || 'get') + ' ' + url.split('?')[0]);
    if (url.includes('uploadType=resumable')) return resp(200, '', { Location: 'https://up/session' });
    if (url.includes('alt=media')) { const [a, b] = o.headers.Range.slice(6).split('-').map(Number);
      return resp(206, { length: b - a + 1, from: a }); }
    if (url === 'https://up/session') { const [rng, total] = o.headers['Content-Range'].slice(6).split('/');
      const [a, b] = rng.split('-').map(Number);
      if (a !== received) return resp(400, 'out of order');
      if (failAt === a) return resp(500, 'boom');
      received = b + 1;
      return received >= Number(total) ? resp(200, JSON.stringify({ id: 'yt123' })) : resp(308, '', { Range: 'bytes=0-' + b }); }
    throw 'unexpected ' + url; } } };
vm.createContext(ctx); vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
ctx.snap_ = () => ({ drive_file_id: 'f1', title: 't', sha256: 'abc' });
ctx.table_ = () => ({ rows: [{ video_id: 'VIDEO-20261125-101', version: '3', state: 'UPLOADING' }] });
ctx.casUpdate_ = (ss, vid, from, ver, upd) => { logs.push('cas ' + upd.state); return true; };
ctx.logError_ = (ss, vid, c, m) => logs.push('log ' + m);
ctx.uploaded_ = (ss, vid, sn, cur, id, pa) => logs.push('uploaded ' + id);
props['rs:VIDEO-20261125-101'] = JSON.stringify({ url: '', offset: 0, size: SIZE, resource: {}, publish_at: '' });
ctx.continueChunked_({}, { video_id: 'VIDEO-20261125-101' });
console.log(JSON.stringify({ received, logs, left: props['rs:VIDEO-20261125-101'] || null,
  puts: calls.filter(c => c.startsWith('put')).length, isLong: [ctx.isLong_('VIDEO-20261125-101'), ctx.isLong_('VIDEO-20261125-001')] }));
"""


def _run(tmp_path, fail_at=-1):
    if not shutil.which("node"):
        pytest.skip("node not installed")
    h = tmp_path / "h.js"
    h.write_text(HARNESS)
    p = subprocess.run(["node", str(h), str(GS), str(fail_at)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_chunked_upload_sends_all_chunks_in_order(tmp_path):
    r = _run(tmp_path)
    assert r["received"] == 80 * 1024 * 1024 and r["puts"] == 3      # 32 + 32 + 16 MB
    assert r["logs"] == ["uploaded yt123"] and r["left"] is None
    assert r["isLong"] == [True, False]


def test_chunked_upload_failure_goes_to_manual_review(tmp_path):
    r = _run(tmp_path, fail_at=32 * 1024 * 1024)
    assert "cas NEEDS_MANUAL_REVIEW" in r["logs"] and r["left"] is None
    assert not any(x.startswith("uploaded") for x in r["logs"])
