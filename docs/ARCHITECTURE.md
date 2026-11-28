# Architecture

`src/studio` is one package with one entry point (`python -m studio`; `python -m ytauto` is a deprecated shim).

| Package | Responsibility |
|---|---|
| core | settings (`config.py`, env), YAML config loader (`conf.py`, rejects protected flags), state machine (13 states), Google auth, model router |
| storage | `store.py` (JsonFileStore / SheetsStore, compare-and-set), Sheets, Drive, idempotency keys |
| youtube | read-only client, schedule/peak slots, approval gate (`approval/gate.py`, mirrored by `apps_script/approval_watcher.gs`), Cloud Run publisher, promotion |
| research | research validation, licensed image search, local vision checks |
| editorial | storyboard validation + slop check, editorial policy (topic gate, research contract, schema-2 scene contract), text extraction for localisation |
| animation | style registry (`config/styles.yaml`), stickman world, custom scenes |
| audio | TTS chain, pronunciation, cue sheet, 4-stem mix, mastering, audio QC, ASR (faster-whisper) |
| video | HyperFrames + Pillow renderers, render manifest/cache, video QC, thumbnails, brand, reference analysis |
| subtitles | captions aligned to the measured speech |
| analytics | daily audit, peak times, snapshots, VideoFacts (unknown ≠ 0) |
| memory | content memory (MinHash) for originality checks |
| experimentation | one-variable experiments, 70/20/10 allocation, bootstrap CI, recommendations |
| playlists | subject playlist planner (executed by the Apps Script after upload) |
| notifications | operational email templates (queued via ErrorLog) |
| observability | JSON logs, usage ledger |
| resource_manager | RAM/CPU/disk/tool probe, usage budget |
| orchestration | run context, durable checkpoints (local cache + RunState tab, sha256 verified) |
| cli | `main.py` + `commands/{setup,planning,production,assets,publishing,analytics,state,intelligence,lifecycle}` |

## Data flow
discover → new-item → researcher → record-research → plan → storywriter → validate-storyboard / novelty →
resolve-images → review → director + scene-coder → voice → scene-frames → render (manifest, cache, QC) →
qc → submit (originality gate, full QC, playlist plan, memory rows) → approval email (Apps Script) →
approver reply → publisher upload → reconcile → analytics → facts → experiment evaluate → next plan.

## External contract
Sheets tabs/columns, states, id ranges (Short en 001–050 / ta 051–099; long en 101–150 / ta 151–199),
approval format and Drive folders are unchanged, so the deployed Apps Script keeps working
(docs/MIGRATION_INVENTORY.md). New tabs (RunState, Ledger, ProductionManifest, ContentMemory, Experiments,
Assignments, VideoFacts, Recommendations, Pronunciation, Playlists) are created on first use.
