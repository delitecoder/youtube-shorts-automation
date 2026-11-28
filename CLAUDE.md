# DeliteCoder video studio — operating rules for Claude Code

**Read `rules.md` first, every session (interactive and scheduled).** It is the owner's operating contract
and wins over everything below except the Hard rules. Edit `rules.md` only with the owner's explicit
authorisation in chat (a hook blocks edits otherwise; `tests/test_rules_file.py` pins its hash).
Resuming? The SessionStart hook prints the durable checkpoint; trust it over memory (docs/RECOVERY.md).

## Hard rules (never break)
- **Nothing is uploaded or published without the approver's authenticated email reply** (`APPROVED VIDEO-…`).
  This session has no YouTube write permission; only the owner's Apps Script / Cloud Run publisher does,
  after re-validating the approval. Never work around it. `studio approve` is read-only.
- Never change `DRY_RUN`, `UPLOAD_ENABLED`, `PUBLICATION_ENABLED`, `PROMOTION_ENABLED` without explicit
  authorisation in chat. Never enable paid APIs or infrastructure.
- Public repo: never print, log, email or commit secrets, `.env`, OAuth files, keys, tokens or personal
  emails (commit as `delitecoder@users.noreply.github.com`). Never ask for credentials in chat.
- Never force-push, rewrite history, or delete Drive / Sheets / YouTube data.
- Never edit or deploy the deployed Apps Script. Changes go in `apps_script/` with a `SCRIPT_VERSION` bump
  and a CHANGELOG entry; the owner is emailed and deploys it.
- Existing Drive videos are never edited, converted or re-encoded.
- Per day: one Short (10:07 IST, `discover`) and one long video (03:47 IST, `discover --format long`),
  each also in Tamil: at most 2 Shorts + 2 long videos. Stop when `discover` says `"action": "none"`.
- No AI video-generation models for daily videos; every video must be original (`studio plan` /
  `studio novelty` block reuse of a hook, script, scene sequence or animation code).
- Deterministic work is Python (`python -m studio …`); subagents only for research, writing and review.

## Where things are
| What | Where |
|---|---|
| Daily + long-form runbook, usage budget, resume safety net | docs/OPERATIONS.md |
| Architecture, modules, data contract | docs/ARCHITECTURE.md, docs/MIGRATION_INVENTORY.md |
| Model routing (which agent, which model) | docs/MODEL_ROUTING.md, config/models.yaml |
| Audio / video systems and QC gates | docs/AUDIO_SYSTEM.md, docs/VIDEO_SYSTEM.md |
| Analytics, experiments, playlists | docs/ANALYTICS.md |
| Recovery, security | docs/RECOVERY.md, docs/SECURITY.md |
| Path-scoped coding rules | .claude/rules/*.md |

## Development
- Tests: `PYTHONPATH=src python -m pytest -q` (all mocked; `-m "not slow"` skips real renders).
- Keep `src/studio/youtube/approval/gate.py` and `apps_script/approval_watcher.gs` in sync.
- Config lives in `config/*.yaml`; protected flags can never be set from YAML (the loader rejects them).
- Update `docs/IMPLEMENTATION_STATUS.md` and `docs/CHANGELOG.md` after meaningful changes.
- Finish runs with a 5-line summary: item, state, title, warnings, next step.
