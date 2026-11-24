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

## Daily workflow (scheduled task)
Run from the repo root with `PYTHONPATH=src`. Work files go in `runtime/work/<VIDEO_ID>/`.

1. `python -m ytauto doctor` → stop and report if `ok` is false.
   `bash scripts/fetch_kokoro.sh` (offline fallback voice) and `python -m ytauto sync-library`
   (SFX/music libraries; procedural sound is used when empty).
2. `python -m ytauto reconcile` (marks scheduled videos public, moves files, optional promotion).
3. `python -m ytauto analytics --snapshot` → keep the `summary` JSON for the storywriter.
4. `python -m ytauto discover` →
   - `none`: report the reason and stop.
   - `existing_video`: `python -m ytauto prepare-existing --file-id <id>`. If not ok, stop (a
     manual-review notification is emailed). If ok: **storywriter** writes `metadata.json` only →
     **quality-reviewer** writes `review.json` → `python -m ytauto submit --video-id … --metadata … --review …`.
   - `document`: `python -m ytauto new-item --source-file-id <id>` →
     `python -m ytauto doc-text --file-id <id>` → continue with step 5 (source_type drive_document).
   - `research`: `python -m ytauto new-item` → step 5.
5. **researcher** (haiku) → `research.json` (give it ResearchHistory topic_keys to avoid) →
   `python -m ytauto record-research --video-id … --file research.json` (fix and retry once on errors).
6. **storywriter** (sonnet) → `storyboard.json` + `metadata.json` →
   `python -m ytauto validate-storyboard --file storyboard.json` (add `--document-based` for docs).
   If it has `image` scenes: `python -m ytauto resolve-images --storyboard storyboard.json`
   (internet images: CC0/PD/CC BY only, checked locally by OmniVision; owner images from Drive
   `References/Images` via `fetch-asset`). Unresolved scenes become title cards automatically.
7. **quality-reviewer** (haiku) → `review.json`. If `fail`, one revision by storywriter, review again.
   Still failing → stop and report (item stays in its state for a human).
8. `python -m ytauto render --video-id … --storyboard storyboard.json` (QC must be ok).
   Voice: Google TTS → Kokoro (offline) → Piper; one voice per video. Adds music (ducked), SFX
   and the automatic subscribe end card. Image/SFX/music credits are added to the description.
9. `python -m ytauto submit --video-id … --metadata metadata.json --review review.json`.
   Apps Script emails the approval request; the run ends here. After a successful upload the
   video file is moved to the Drive `Archive` folder (Apps Script; `reconcile` repeats it if needed).
10. Optionally: storywriter writes Reddit drafts JSON → `python -m ytauto reddit-drafts …`
    (drafts only; never post).

Finish with a 5-line summary: item, state, title, warnings, next step. Keep model output compact;
pass JSON files between agents rather than long prose.

## Development
- Tests: `python -m pytest -q` (all mocked; `-m "not slow"` skips real renders).
- Keep `src/ytauto/approval/gate.py` and `apps_script/approval_watcher.gs` in sync.
- Update `docs/IMPLEMENTATION_STATUS.md` after meaningful changes.
