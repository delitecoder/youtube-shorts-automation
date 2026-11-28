---
paths: ["src/studio/youtube/**", "apps_script/**", "cloudrun/**"]
---
- `gate.py` and `approval_watcher.gs` implement the same rules; change both together and keep the parity tests green.
- Silence, errors and timeouts are never approval. Upload errors end in NEEDS_MANUAL_REVIEW, never retried.
- apps_script/ changes: bump `SCRIPT_VERSION`, add a CHANGELOG entry; the owner deploys. Never deploy it yourself.
