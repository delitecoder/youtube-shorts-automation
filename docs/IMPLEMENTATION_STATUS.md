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

## Test results
`python -m pytest -q` → 128 passed (real renders, real Kokoro synthesis, Apps Script parity).
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

## Next action
First full dry-run production in the cloud → then Phase 2 when the owner sets keys.json DRY_RUN=false
(real approval emails, uploads still disabled). Apps Script trigger not verifiable from here.
