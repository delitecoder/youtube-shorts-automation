# YouTube Shorts automation — operating rules for Claude Code

Read `docs/IMPLEMENTATION_STATUS.md` first when resuming development work.

## Hard rules (never break)
- **Nothing is uploaded or published without an explicit email approval.** This session has no
  YouTube upload permission by design; only the Cloud Run publisher does, after re-validating the
  approval. Never try to work around this.
- Never change `DRY_RUN`, `UPLOAD_ENABLED`, `PUBLICATION_ENABLED`, `PROMOTION_ENABLED` without the
  user's explicit authorisation in chat. Never enable paid APIs or infrastructure.
- Never print, log, email or commit secrets. Never ask the user to paste credentials into chat.
- Existing Drive videos are never edited, converted or re-encoded.
- One content item per daily cycle. Stop when `discover` says `"action": "none"`.
- Use Python commands for deterministic work; use subagents only for research, writing and review.

## Model routing (keep token use minimal)
| Role | Model | Used for |
|---|---|---|
| Orchestrator (main session) | **Opus** (`.claude/settings.json` → `opus`) | runs the workflow, calls CLI + subagents |
| researcher, quality-reviewer | **Haiku** | research JSON, fact/quality checks |
| storywriter, code-engineer | **Sonnet** | scripts/metadata, code fixes |
| advisor | **Fable** | only for genuinely hard decisions; ≤ 120-word answers; at most once per run |
Images are described and checked by local vision models (OmniVision → SmolVLM → Google Vision → CLIP+YOLOv8);
Claude reads only the compact JSON summary (`kw`, `flags`, `rel`), never the image or the full description.

## Usage budget (check before working — finish inside the current window when possible)
How to read the real numbers (costs almost nothing):
1. `get_session` (no id) → `session_context`/`external_metadata.rate_limit_info` (status, resetsAt).
2. `list_events` for your own session id with `kinds: ["rate_limit_event"]`, `limit: 20` → take the
   newest event's `rate_limit_info.unifiedWindows`: `five_hour.utilization/resetsAt/status` and
   `seven_day.utilization/resetsAt/status`.
3. `python -m ytauto budget --five-hour U5 --five-reset R5 --seven-day U7 --seven-reset R7
   [--five-status S5 --seven-status S7] --record start` and follow `decision`:
   - `go_full`: normal workflow.
   - `go_lean`: lean plan — one research pass, no storywriter revision loop (one review only;
     if it fails, stop and checkpoint), `VISION_PROVIDERS=clip,metadata` (or metadata only),
     skip analytics snapshot and Reddit drafts, keep subagent prompts short.
   - `wait`: arm `send_later` for `resume_after_minutes`, checkpoint, stop (start after the reset).
   - `weekly_stop`: stop now. The command already queued an alert email ("weekly Claude limit
     reached, no videos until <reset>"). One video per day is never exceeded.
4. Re-check (steps 2–3 without `--record`) before research, before render, and before submit; if the
   decision got worse, switch to lean or wait at that point instead of running out mid-step.
5. After `submit` (or when stopping), run the same command with `--record end` so the per-video cost
   is learned (Configuration tab).
An Apps Script watchdog independently emails the owner if no video exists by 12:30 IST (covers the
case where Claude is fully blocked and cannot even start).

## Usage-limit safety net (dynamic resume)
Claude subscription usage resets about 5 hours after usage starts, so resumes follow the reset,
not a fixed clock:
- As soon as `discover` returns work (`resume` or a new item), call the `send_later` tool with
  `delay_minutes: 310` and the message "Usage window has reset: continue the YouTube Short run —
  run `python -m ytauto discover` and continue (resume) per CLAUDE.md". Keep the returned trigger id.
- If you are woken by that message, first arm a new 310-minute reminder the same way, then continue.
- When the run ends normally (video submitted, or nothing to do), delete your reminder with
  `delete_trigger`. Never leave more than one reminder armed.
- If `send_later` is not available, skip this; the next daily run resumes from the checkpoint.
- `submit` automatically picks the next free 19:30 IST slot, so a late video never collides with
  the next day's video.

## Daily workflow (scheduled task)
Run from the repo root with `PYTHONPATH=src`. Work files go in `runtime/work/<VIDEO_ID>/`.

1. `python -m ytauto doctor` → stop and report if `ok` is false.
   `bash scripts/fetch_kokoro.sh` (offline fallback voice) and `python -m ytauto sync-library`
   (SFX/music libraries; procedural sound is used when empty).
2. `python -m ytauto reconcile` (marks scheduled videos public, moves files, optional promotion).
3. `python -m ytauto audit --email` (daily views audit: trends, top/bottom videos, traffic sources,
   retention → concrete suggestions; emails the owner once a day) → pass its `guidance` and
   `suggestions` to the storywriter, who must apply them. `python -m ytauto peak-time` (weekly,
   cached) keeps the per-weekday peak publish slots current; `submit` uses them automatically.
   `python -m ytauto analytics --snapshot` keeps per-video history for our own videos.
4. `python -m ytauto discover` →
   - `resume`: an earlier run stopped part-way (e.g. token limit). Run
     `python -m ytauto restore --video-id <id>` and continue from the reported `next_step`
     (research → storyboard → review → render → submit), skipping steps whose files exist.
   - `none`: report the reason and stop.
   - `existing_video`: `python -m ytauto prepare-existing --file-id <id>`. If not ok, stop (a
     manual-review notification is emailed). If ok: **storywriter** writes `metadata.json` only →
     **quality-reviewer** writes `review.json` → `python -m ytauto submit --video-id … --metadata … --review …`.
   - `document`: `python -m ytauto new-item --source-file-id <id>` →
     `python -m ytauto doc-text --file-id <id>` → continue with step 5 (source_type drive_document).
   - `research`: `python -m ytauto new-item` → step 5.
5. **researcher** (haiku) → `research.json` (give it ResearchHistory topic_keys to avoid) →
   `python -m ytauto record-research --video-id … --file research.json` (fix and retry once on errors).
   After EVERY step from here on: `python -m ytauto checkpoint --video-id …` (saves work to Drive so
   the next session can continue if this one stops).
6. **storywriter** (sonnet) → `storyboard.json` + `metadata.json` →
   `python -m ytauto validate-storyboard --file storyboard.json` (add `--document-based` for docs).
   Every video should have 1–2 `image` scenes (licensed internet photos):
   `pip install -q -r requirements-vision.txt` (if not installed), then
   `python -m ytauto resolve-images --storyboard storyboard.json`
   (internet images: CC0/PD/CC BY only, checked locally by OmniVision; owner images from Drive
   `References/Images` via `fetch-asset`). Unresolved scenes become title cards automatically.
7. **quality-reviewer** (haiku) → `review.json`. If `fail`, one revision by storywriter, review again.
   Still failing → stop and report (item stays in its state for a human).
8. `python -m ytauto render --video-id … --storyboard storyboard.json` (QC must be ok). The HyperFrames
   engine (animated HTML/GSAP: transitions, kinetic text, particles, light sweeps, Ken Burns images)
   is used automatically; if it fails the Pillow renderer takes over (`engine` in the output).
   Needs `npm ci --prefix renderer/hyperframes` once per container.
   Voice: Google TTS → Kokoro (offline) → Piper; one voice per video. Adds music (ducked), SFX
   and the automatic subscribe end card. Image/SFX/music credits are added to the description.
9. `python -m ytauto submit --video-id … --metadata metadata.json --review review.json`.
   Apps Script emails a neat HTML approval request (poster + Drive preview button). The approver
   replies `APPROVED VIDEO-…` before the evening slot (19:30 IST − 15 min) and the Apps Script uploads
   it scheduled for 19:30 IST; no reply = skipped. Uploading never needs Claude. After a successful upload the
   video file is moved to the Drive `Archive` folder (Apps Script; `reconcile` repeats it if needed).
10. Optionally: storywriter writes Reddit drafts JSON → `python -m ytauto reddit-drafts …`
    (drafts only; never post).

Finish with a 5-line summary: item, state, title, warnings, next step. Keep model output compact;
pass JSON files between agents rather than long prose.

## Development
- Tests: `python -m pytest -q` (all mocked; `-m "not slow"` skips real renders).
- Keep `src/ytauto/approval/gate.py` and `apps_script/approval_watcher.gs` in sync.
- Update `docs/IMPLEMENTATION_STATUS.md` after meaningful changes.
