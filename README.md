# YouTube Shorts automation (approval-gated)

Produces **one English educational tech Short per day** from Google Drive. Either it prepares
an existing video unchanged, or it researches, writes and renders an original story-driven Short.
**Nothing is uploaded until you reply `YES VIDEO-ID` to an approval email.**

```
Claude Code scheduled task (daily, subscription)          Apps Script (every 5 min)      Cloud Run
 doctor → reconcile → analytics → discover                 send approval email             verify HMAC
 ├ existing video: checksum + eligibility (no edits)       read reply, validate strictly   re-validate ledger
 └ new video: researcher(haiku) → storywriter(sonnet)      ledger + APPROVED/REJECTED      + Gmail message
   → quality-reviewer(haiku) → TTS → render → QC           expire after 24 h               verify SHA-256
 → submit: Drive 03_Awaiting_Approval + Sheets ledger      call publisher (signed)         private upload,
                                                                                            publishAt 19:30 IST
```

| Path | Purpose |
|---|---|
| `CLAUDE.md` | Rules + the daily workflow the scheduled task follows |
| `.claude/agents/` | researcher (haiku), storywriter (sonnet), code-engineer (sonnet), quality-reviewer (haiku) |
| `src/ytauto/` | Python package: `integrations/`, `research/`, `production/`, `approval/`, `publishing/`, `analytics/`, `promotion/`, `orchestration/` (CLI) |
| `apps_script/approval_watcher.gs` | Gmail approval watcher (mirrors `approval/gate.py`) |
| `cloudrun/` | Publisher service, Dockerfile, Cloud Build config |
| `tests/` | Unit, mocked integration, security, idempotency and real render tests |
| `docs/` | `deployment.md`, `security.md`, `costs.md`, `IMPLEMENTATION_STATUS.md`, scheduled task prompt |

## Quick start (local dry run, no credentials)
```bash
sudo apt-get install -y ffmpeg fonts-dejavu-core
```
```bash
pip install -r requirements.txt
```
```bash
python -m pytest -q
```
```bash
DRY_RUN=true TTS_PROVIDER=tone PYTHONPATH=src python -m ytauto doctor
```

## Output format (generated videos)
1080×1920, 30 fps, H.264 High/yuv420p, AAC 48 kHz, -14 LUFS, burned-in captions inside Shorts
safe zones, 5–8 animated scenes (title, bullets, code, diagram, stat, comparison, timeline,
question), ~45–60 s driven by narration length. Every render is QC-checked automatically.

## Safety flags (initial state)
`DRY_RUN=true`, `UPLOAD_ENABLED=false`, `PUBLICATION_ENABLED=false`, `PROMOTION_ENABLED=false`.
Activation is phased; see `docs/deployment.md`. Costs and free allowances: `docs/costs.md`.
