# Apps Script changelog (approval_watcher.gs)

The owner updates the script; Claude never pushes it. Newest first. Each entry = one SCRIPT_VERSION.

## 2026.11.24-1
- Uploads approved videos itself (no Cloud Run needed), scheduled for the evening peak slot.
- Neat HTML approval email (poster, Drive preview button); reply `APPROVED VIDEO-…` / `REJECTED VIDEO-…`.
- No reply before the slot (−15 min) = skipped; moves uploaded videos to Archive.
- Emails: daily views audit, Claude usage-limit alerts, "no video today" watchdog, update notices.
- Needs: YouTube Data API v3 service + running `setup` once (new YouTube permission).
