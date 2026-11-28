# Security model

## The approval boundary
```
Claude daily task ──(no upload scope)──> Drive + Sheets: item in AWAITING_APPROVAL
Apps Script (your Gmail) ──> approval email ──> your reply "APPROVED VIDEO-…"
Apps Script validates reply ──> ApprovalLog + APPROVED ──> HMAC-signed call
Cloud Run publisher ──> re-validates everything ──> private upload + publishAt
```
1. **Least privilege by component.** The Claude session's OAuth token (pipeline profile) cannot
   upload to YouTube at all. Only the publisher token has `youtube.upload`, and it lives only in
   Secret Manager for the Cloud Run service. A model mistake or prompt injection cannot publish.
2. **Two independent validators.** Apps Script and the publisher implement the same rules
   (`approval_watcher.gs` / `approval/gate.py`; parity is tested). The publisher does not trust the
   webhook: it re-reads the ledger, requires exactly one accepted `YES`, re-checks sender, thread,
   timing, expiry and command text, and re-fetches the Gmail message by ID (gmail.readonly) to
   compare it with the ledger.
3. **Accepted command.** Exactly one new line `YES VIDEO-ID` or `NO VIDEO-ID` (case-sensitive),
   after removing quoted history, `--` signatures and "Sent from my …" lines. Rejected: lowercase,
   extra words, two commands, commands only inside quotes, wrong ID, wrong thread, replies sent
   before the request, after 24 h, in the future, replays of an already processed message.
   Silence, errors and timeouts are never approval; pending items expire after 24 h.
4. **Sender authentication.** Approver = your own mailbox (default): the reply must carry Gmail's
   `SENT` label, so a spoofed "From" that arrives in the inbox is rejected. External approver:
   Gmail's own `Authentication-Results` must show `dkim=pass` and an aligned `dmarc=pass`.
5. **Integrity.** The SHA-256 of the exact file is recorded before approval and re-computed on
   download by the publisher. A mismatch stops publication (NEEDS_MANUAL_REVIEW).
6. **Idempotency.** Compare-and-set transitions `APPROVED → UPLOADING → SCHEDULED`. Any upload
   error ends in `NEEDS_MANUAL_REVIEW` (never retried automatically, because YouTube may already
   have created the video). Repeated webhook calls return `duplicate`.
7. **Webhook.** HMAC-SHA256 over `timestamp.nonce.body`, ±300 s window, single-use nonce,
   strict video-id regex. The secret is ≥ 32 chars and stored in Secret Manager + Apps Script
   script properties. The service is reachable without IAM because Apps Script cannot mint
   Cloud Run identity tokens easily; requests without a valid signature get 401 and do nothing.

## Ownership of transitions (avoids concurrent writers)
| Transition | Owner |
|---|---|
| DISCOVERED … QUALITY_CHECK → AWAITING_APPROVAL | Claude daily task (`studio` CLI) |
| AWAITING_APPROVAL → APPROVED / REJECTED / EXPIRED | Apps Script (with LockService) |
| APPROVED → UPLOADING → SCHEDULED / NEEDS_MANUAL_REVIEW | Publisher |
| SCHEDULED → PUBLISHED | Claude daily task (`reconcile`) |
Every write is compare-and-set on `(state, version)`, and the Python Sheets store verifies its
random `write_token` after writing.

## Secrets
| Secret | Where it lives | Never in |
|---|---|---|
| Pipeline OAuth token | Claude cloud environment variable `YTA_PIPELINE_OAUTH` (your personal environment) | repo, logs, chat |
| Publisher OAuth token | GCP Secret Manager → Cloud Run env | Claude environment |
| TTS API key (restricted to Text-to-Speech API) | Claude environment *network secret* for `texttospeech.googleapis.com` | container env |
| Webhook secret | Secret Manager + Apps Script script property | repo |
| Bluesky app password | Secret Manager / environment secret, only if promotion is enabled | repo |
`.gitignore` blocks `.env`, token and credential JSON files. Logs and emails never include
secret values; ErrorLog messages are truncated to 2,000 chars.

Note: a Claude environment *variable* is readable by commands in your own sessions on that
environment. The pipeline token is therefore limited to Drive (folder allow-list enforced in
code), Sheets and read-only YouTube/Analytics scopes.

## Drive and previews
- Only the configured folder IDs are listed or downloaded (`OutsideAllowedFolders` otherwise).
- Previews are the private Drive links (owner-only). Files are never shared publicly.
- Existing videos are never modified; checksums before/after assessment are compared.
- Drive scope note: `drive.file` cannot read files you add by hand, so the pipeline uses the
  `drive` scope with the code-level folder allow-list; the publisher uses `drive.readonly`.

## Third-party media
- Internet images: only CC0, Public Domain and CC BY (no SA/NC/ND); each is credited in the
  description automatically. Candidates with watermarks, logos or recognisable faces (per the
  local OmniVision check) are rejected. Downloads are capped at 15 MB and verified as images.
- Owner images (Drive `References/Images`), SFX and music libraries are used only with a
  `RIGHTS:` declaration or a manifest entry naming source and licence.
- Vision runs as a subprocess without a shell (file names cannot inject commands).
- Edge TTS is off by default because it is an unofficial use of a Microsoft service.
- The repository is public. It contains no credentials, IDs, email addresses or personal data:
  all of those live in the Claude environment, Apps Script properties and Secret Manager. The
  security model does not rely on the code being secret.
- Images that are not openly licensed (IMAGE_LICENSE_MODE=owner_approval) are credited, but a
  credit is not a licence: approving the video means you accept that copyright risk.

## Claude Code guard hooks (`.claude/settings.json` → `scripts/hooks/run.py`, logic in `src/studio/hooks.py`)
| Event | Behaviour |
|---|---|
| PreToolUse | **Denies** (fail closed): turning upload/publication/promotion flags on or dry-run off; forced pushes and history rewrites; writing env, OAuth, key or token files; editing `rules.md` unless `STUDIO_RULES_EDIT_AUTHORIZED=1` (owner authorisation). The match is deliberately strict, so a command that merely *mentions* those words is also refused: write such text with the Write tool instead. |
| PreCompact | Re-saves the durable checkpoint; never blocks compaction. |
| SessionStart | Restores `runtime/state/*` from the RunState tab and prints the next actions. |
| PostToolUse / SubagentStop / Stop | Local JSON logs only (fail open). |

Destructive commands also refuse to run while the local checkpoint is unverified (`CHECKPOINT_UNVERIFIED`).
