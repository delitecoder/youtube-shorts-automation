# Migration inventory: `ytauto` → `studio` (2026-11-28)

## Backup
| What | Where |
|---|---|
| Pre-rebuild commit | `6c12d99681c1834240dc44cd56b24254cc026770` |
| Backup branch (remote) | `archive/pre-rebuild-20261128-0237` (pushed to origin) |
| Backup tag | `backup/pre-rebuild-20261128-0237`: created locally. Pushing the tag through the session's git proxy is disconnected every time, so the **branch is the durable backup**. |
| CPU AI-video experiments (LTX, Qwen-Image, lip-sync scripts, notes, research reports, sample clips) | Google Drive `YouTubeAutomation/Archive/exp_archive_20261128.zip` (143 MB, id `18wQeB-hSILi2F3TgUk5clNVp-bHEHaZ7`). The model weights are not archived; they are public and can be downloaded again. |
| Baseline tests | 204 passed, 3 slow deselected (`pytest -m "not slow"`) |

## External contract that MUST be kept
The approval uploader is the owner-deployed Apps Script (`apps_script/approval_watcher.gs`). Claude never deploys it, and it reads these structures directly. Changing them breaks approvals, so the new code keeps them **byte-compatible**:
- **Google Sheets control spreadsheet** (`SPREADSHEET_ID`):
  - tabs `VideoQueue`, `ResearchHistory`, `PublishedVideos`, `ApprovalLog`, `Analytics`, `Configuration`, `ErrorLog`, `ThemePlan`;
  - `VideoQueue` columns = `VideoRecord` fields;
  - compare-and-set on `state` + `version`.
  New tabs may be **added**; existing tabs and columns keep their names and order.
- **States** (13): `DISCOVERED, RESEARCHED, STORYBOARDED, RENDERED, QC_PASSED, AWAITING_APPROVAL, APPROVED, UPLOADING, UPLOADED, PUBLISHED, REJECTED, EXPIRED, FAILED` (+ the transitions table).
- **Video ids:** `VIDEO-YYYYMMDD-NNN`; Short en 001–050, ta 051–099; long en 101–150, ta 151–199.
- **Approval reply:** one line `APPROVED VIDEO-…`.
  - Approver authenticated by the SENT label or DKIM+DMARC.
  - Replay and idempotency script properties.
  - Deadline = min(request + 24 h, slot − 15 min).
  - Python `approval/gate.py` stays in parity, tested by running the `.gs` in Node.
- **Drive folders** (`DRIVE_FOLDER_IDS` keys): root, config, inbox, inbox_videos, inbox_documents, inbox_references, references_images, references_sfx, references_music, generated, awaiting_approval, published, rejected, archive. `00_Config/keys.json` (names only, never values in logs).
- **Cloud Run publisher** `POST /v1/approved`: HMAC, `/healthz`.

## Environment variables (names only)
`ALLOW_EDGE_TTS APPROVAL_TTL_HOURS APPROVER_EMAIL APPROVER_IS_OWNER BLUESKY_APP_PASSWORD BLUESKY_HANDLE DETECTOR_MODEL DRIVE_FOLDER_IDS DRY_RUN EDGE_VOICE GOOGLE_TTS_API_KEY GOOGLE_VISION_API_KEY GOOGLE_VISION_ENABLED IMAGE_API_CONTACT IMAGE_LICENSE_MODE KOKORO_MODELS_DIR KOKORO_VOICE LONG_PUBLISH_TIME LONG_PUBLISH_TIME_TA MUSIC_ENABLED OUTRO_ENABLED OUTRO_TEXT PIPER_MODEL PROMOTION_ENABLED PUBLICATION_ENABLED PUBLISHER_VERIFY_GMAIL PUBLISH_TIME PUBLISH_TIMEZONE PUBLISH_TIME_TA RENDER_ENGINE SFX_ENABLED SPREADSHEET_ID SYNTHETIC_MEDIA TTS_FALLBACKS TTS_LANGUAGE TTS_MONTHLY_CHAR_BUDGET TTS_PROVIDER TTS_VOICE TTS_VOICE_TA UPLOAD_ENABLED VIDEO_LANGUAGES VISION_CMD VISION_MONTHLY_UNIT_BUDGET VISION_PROVIDERS VLM_MODEL YOLO_WEIGHTS YOUTUBE_CATEGORY_ID YTA_PIPELINE_OAUTH YTA_RUNTIME_DIR YTA_SKIP_DRIVE_KEYS`

**Protected flags**, unchanged by this migration, with the same safe defaults:
- `DRY_RUN` is true unless exactly `false`.
- `UPLOAD_ENABLED`, `PUBLICATION_ENABLED` and `PROMOTION_ENABLED` are false unless exactly `true`.

Secrets live only in the environment or in Drive `keys.json`. None are in git.

## Scheduled entry points
Cloud scheduled tasks (10:07 IST Short, 03:47 IST long) follow `CLAUDE.md` and `docs/scheduled_task_prompt*.md` and call `python -m ytauto …`. After the migration they call `python -m studio …`. A thin `python -m ytauto` shim forwards old command names for one transition period, so a stored task prompt cannot break.

## Module decisions
| Old module | Decision | New home |
|---|---|---|
| `state.py`, `store.py` (JsonFileStore, TABS, id ranges) | **retain** (contract) | `studio/core/state.py`, `studio/storage/store.py` |
| `config.py` (Settings, themes, syllabus) | **redesign**: YAML config + env overlay, typed | `config/*.yaml`, `studio/core/config.py`, `studio/editorial/themes.py` |
| `approval/gate.py`, `approval/webhook_auth.py` | **retain** (security, parity tests) | `studio/youtube/approval/` |
| `integrations/sheets.py`, `drive.py`, `google_auth.py`, `keys.py`, `gmail_check.py` | **retain** | `studio/storage/`, `studio/core/google/` |
| `integrations/youtube.py`, `publishing/*`, `cloudrun/` | **retain + fix** (synthetic flag on Cloud Run path, category id) | `studio/youtube/` |
| `integrations/tts.py` | **redesign**: provider abstraction + pronunciation layer + cache | `studio/audio/tts/` |
| `production/audio.py`, `sound.py`, `audio_library.py` | **redesign**: stems, stereo bus, event cue sheet, sectioned music, mastering, audio QC | `studio/audio/` |
| `production/captions.py` | **replace**: word timings from ASR alignment when available, estimate as labelled fallback | `studio/subtitles/` |
| `production/hyperframes.py`, `world.py`, `stickman.py`, `custom_scene.py`, `thumbnail.py`, `render.py`, `pillow` renderer, `renderer/hyperframes/` | **retain engine, redesign selection**: style registry + novelty instead of date modulo; per-scene cache; video QC (decode, black, frozen) | `studio/video/`, `studio/animation/` |
| `production/storyboard.py` | **redesign**: schema with narrative purpose, claim ref, audio events, licence per scene | `studio/editorial/storyboard.py` |
| `production/vision.py`, `research/images.py` | **retain** | `studio/research/images/` |
| `research/validate.py` | **retain + extend** (claim status, access dates) | `studio/research/` |
| `analytics/*` | **redesign**: missing metric = unknown (not 0), Shorts/long and en/ta separated, VideoFacts | `studio/analytics/` |
| `orchestration/cli.py` (1280 lines) | **replace**: split into command modules | `studio/cli/` |
| `orchestration/budget.py` | **retain logic** | `studio/resource_manager/usage.py` |
| `promotion/*` | **retain** (still flag-gated) | `studio/youtube/promotion/` |
| — | **new** | `studio/memory/` (content memory, novelty), `studio/experimentation/`, `studio/playlists/`, `studio/observability/` (JSON logs, ledger), `studio/resource_manager/` (env probe, memory guard), `studio/orchestration/checkpoint.py` (durable state), `studio/notifications/` |
| `apps_script/*.gs` | **retain**; any change follows the existing owner-email flow (`SCRIPT_VERSION` bump + CHANGELOG) | `apps_script/` |

## Known bugs fixed during migration
1. Lane memory reads a missing `theme` column.
2. Category id 28 vs 27 (Python side; the Apps Script side goes via the owner update).
3. Cloud Run upload misses `containsSyntheticMedia`.
4. `json.dumps(...)[:45000]` truncation corrupts JSON.
5. Long videos get `/shorts/` links.
6. Peak slots are ignored for Tamil and long videos.
7. Duplicate `videos.list` calls in the audit.
8. Apps Script marks `webhook_status=done` on dry-run responses (owner-deployed fix).

## Retired
- `NEVER_FROM_DRIVE` and `imageLines_` (dead code).
- Date-modulo style rotation.
- Estimated-only captions as the default.
- The `music-bed` ACE-Step GPU guide moves to docs (no GPU here).
