# Implementation status (resume here)

_Last updated: 2026-11-24_

## Done (code + tests in this repo)
- Config with fail-safe flags; workflow states + compare-and-set transitions; JSON and Sheets stores.
- Approval gate (Python) + Apps Script watcher with identical rules (Node parity test).
- HMAC webhook auth with timestamp window and nonce replay protection.
- Publisher: ledger + Gmail re-verification, SHA-256 re-check, flags, UPLOADING lock,
  resumable upload with retries, manual review on any failure, no duplicate uploads.
- Cloud Run Flask service + Dockerfile + Cloud Build config.
- Drive (folder allow-list), Sheets, YouTube, YouTube Analytics, Gmail (read one message), Google TTS
  with monthly budget, Piper fallback, Bluesky post, Reddit drafts (manual only).
- Renderer: Pillow → FFmpeg 1080×1920/30 fps/H.264/AAC, loudness -14 LUFS, captions, 8 visual types,
  automatic QC. Existing-video eligibility (rights declaration, ≤ 3 min, square/vertical, decode check).
- CLI (`python -m ytauto …`), 4 subagents, CLAUDE.md daily workflow, docs.

## Added (2026-11-24: themes + approval buttons)
- Daily content theme rotation (`CONTENT_ROTATION`/`theme_for` in config.py, IST weekday):
  Mon/Fri trending tech searches, Tue/Sat tech story, Wed/Sun tech news, Thu tech motivation.
  `discover` returns `theme`; researcher/storywriter follow it (series title prefix). New
  categories "tech stories" and "motivation". Tests: tests/test_themes.py.
- One-click Approve/Reject buttons in the approval email (Apps Script 2026.11.24-2): optional
  `APPROVAL_BUTTONS_URL` → approver-owned web app `apps_script/approval_buttons.gs` that sends the
  normal one-line reply from the approver's Gmail. Gate rules unchanged (gate.py untouched).

## Added (2026-11-24: branding, music mood, theme plan, skipped-video choice, cleanup)
- Branding (production/brand.py): logo badge + @handle + series label top-left, pop-in/pulse,
  chapter dashes; both renderers. Logo synced by `sync-library` (Drive original, else channel avatar).
- Music: numpy analysis (tempo/energy/brightness, cached in music_features.json) picks licensed
  library tracks by theme mood (`THEME_MOOD`); procedural music tempo follows the theme.
- Sheet tab `ThemePlan` (date, theme, details) overrides the rotation for that date.
- Apps Script 2026.11.24-3: skipped (EXPIRED, ≤7 days) videos re-offered in the approval thread
  (EXPIRED → AWAITING_APPROVAL allowed); one approval per thread, others back to EXPIRED.
  Daily trash of Archive files of videos published > 30 days.
- Owner request checklist: docs/OWNER_REQUESTS.md.

## Added (round 2)
- Voice fallback chain: Google → Kokoro-82M (offline, tested for real here, ~real-time) → Piper;
  Edge TTS opt-in only. One voice per video; budget exhaustion falls back instead of failing.
- Sound: procedural SFX + original procedural music; licensed libraries (Earshot importer,
  ACE-Step music skill) via manifests; sidechain-ducked mix normalised to -14 LUFS.
- Images: Wikimedia Commons + Openverse (CC0/PD/CC BY), OmniVision local check via VISION_CMD,
  owner images from Drive; credits appended to descriptions.
- Automatic subscribe end card + narration line.
- Model routing: Opus orchestrator (.claude/settings.json), Haiku research/review,
  Sonnet writing/code, Fable advisor (rare, ≤120 words). Music skill in .claude/skills/music-bed.

## Added (round 3)
- Vision fallback chain: OmniVision (VISION_CMD) → SmolVLM-500M (detailed description, tested live:
  ~50 s/image CPU) → Google Vision (opt-in, budgeted) → CLIP relevance + YOLOv8 people check (tested
  live) → metadata-only. Claude reads a ~25-token JSON summary per image.
- IMAGE_LICENSE_MODE=owner_approval: non-open images allowed only with a named creator; credited in
  the description; covered by the single video approval (no per-image approval).
- After a successful upload the video file moves to Drive `Archive` (Apps Script + reconcile).

## Added (round 4)
- Grounding DINO (Apache-2.0) open-vocabulary detector: coordinates for framing (Ken Burns drifts to
  the main object; tested live) + logo/watermark only at score >= 0.40 (low scores gave false hits).
- Keys/settings from Drive `00_Config/keys.json` (whitelist, must be private & owned; never flags or
  publisher secrets). Only YTA_PIPELINE_OAUTH stays in the Claude environment. Apps Script reads
  non-secret settings from the same file (KEYS_FILE_ID).
- docs/SETUP.md step-by-step; daily scheduled task (10:07 IST) clones the public repo and stops
  early while setup is incomplete.

## Added (round 5) — daily automatic flow
- HyperFrames renderer (Apache-2.0; HTML/GSAP → MP4, headless Chromium) is the default engine with
  the Pillow renderer as automatic fallback; verified live (62 s video, all QC checks, ~3.5 min).
- Apps Script is now the uploader (no Cloud Run/billing): HTML approval email with poster + Drive
  button, approver gets Drive access if not the owner, replies APPROVED/REJECTED (YES/NO still ok),
  deadline = earlier of 24 h and slot − 15 min, upload uses only the script's own snapshot of what
  was approved (Script Properties), re-checks the Gmail reply + SHA-256, uploads private with
  publishAt 19:30 IST, moves the file to Archive, emails "✅ Short uploaded". Flags UPLOAD_ENABLED /
  PUBLICATION_ENABLED live only in Script Properties. Cloud Run publisher remains optional
  (UPLOADER=cloud_run).
- Resume across sessions: checkpoint after each step → Drive 02_Generated/<id>_work.zip; discover
  returns `resume`; 13:37 IST catch-up scheduled task (plus 10:07 daily). Both run on Opus.
- SEO metadata rules enforced (title ≤ 70, 3–5 hashtags incl. #Shorts, 8–15 tags, category 28/27).
- YouTube audit not required to publish (lifted 2026-11-02 per API revision history).

## Fixed (2026-11-25, from the first scheduled run)
- Loudness QC failed (-16.2 LUFS): single-pass loudnorm on the mix undershoots on short clips with
  pauses. Now two-pass (measure → linear apply → verify/trim with true-peak limiter):
  real narration mix -14.3 LUFS, final MP4 QC ok.
- Scheduled sessions cannot read usage limits (no get_session/list_events): `budget --unknown`
  returns go_lean without inventing numbers; weekly-limit cases are covered by the 12:30 watchdog.
- Fresh containers can have `pip` for a different Python than `python`: always `python -m pip`.

## Test results
`python -m pytest -q` → 140 passed (real renders, real Kokoro synthesis, Apps Script parity).
Mutation check: removing the SENT-label check, request-time check, or checksum check makes tests fail.

## Verified with the owner's accounts (2026-11-24)
- bootstrap done by owner; cloud session: doctor ok, setup_complete=true, keys.json loaded from Drive
  (8 keys), Drive folders readable, control spreadsheet with all 7 tabs, YouTube channel found,
  Analytics API returns data. All flags still safe (DRY_RUN=true, uploads/publication/promotion off).

## Not verified (needs owner's accounts)
- Apps Script not yet deployed; Cloud Run not deployed (needs billing authorisation).
- Google TTS voice name `en-US-Chirp3-HD-Charon` must be confirmed with the first real synthesis.
- Scheduled task not created yet (waiting for repo + credentials).
- Image search verified live (Commons + Openverse -> 11 licensed candidates, PD image chosen).
  Wikimedia rate-limits anonymous cloud IPs (HTTP 429): retries honour Retry-After; set
  IMAGE_API_CONTACT for a compliant User-Agent. Network hosts were added to the environment.
- OmniVision runtime not installed here (Nexa SDK wheel failed to build; llama.cpp build not done);
  adapter tested with mocks. Until VISION_CMD is set, images are flagged visual_unverified.

## Blockers / owner actions
1. Repository: github.com/delitecoder/youtube-shorts-automation (public; no secrets or personal data in it).
2. Google Cloud setup + tokens (docs/deployment.md §1–3). No YouTube audit needed (lifted 2026-11-02).
3. Decide on billing (TTS + Cloud Run) or the zero-billing fallback (docs/costs.md).

## Next action (previous)
First full dry-run production in the cloud → then Phase 2 when the owner sets keys.json DRY_RUN=false
(real approval emails, uploads still disabled). Apps Script trigger not verifiable from here.

## Next action
Owner updates the Apps Script (new code + YouTube service), runs setup, sets Script Properties
UPLOAD_ENABLED=true and PUBLICATION_ENABLED=true. VIDEO-20261124-001 was requested by the old
script (no snapshot) so it will not auto-upload; the first automatic upload is the next daily video.
