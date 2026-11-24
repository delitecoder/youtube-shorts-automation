# Deployment and activation

Order matters: each step stays safe (no uploads) until **Phase 4**, and each phase needs your
explicit go-ahead. Never paste secrets into chat; every secret goes into a protected store below.

## 0. Prerequisites
- Google account that owns the YouTube channel.
- A Google Cloud project (console.cloud.google.com). A **billing account** is needed for Cloud
  Text-to-Speech and Cloud Run even within their free tiers (see docs/costs.md); attach it only
  if you accept that, and create a budget alert first.
- This repository, private on GitHub, attached to your Claude cloud environment.

## 1. Google Cloud project
1. Enable APIs: YouTube Data API v3, YouTube Analytics API, Google Drive API, Google Sheets API,
   Gmail API, Cloud Text-to-Speech API (+ Cloud Run Admin, Secret Manager, Cloud Build,
   Artifact Registry when you deploy the publisher).
2. OAuth consent screen: External, your account as test user, then **Publish app → In
   production** (otherwise refresh tokens expire after 7 days). You will see an "unverified app"
   warning when authorising your own account; that is expected for personal use.
3. Credentials → OAuth client ID → *Desktop app* → download `client_secret.json` (keep it local).
4. Credentials → API key → restrict it to **Cloud Text-to-Speech API** only.
5. **YouTube API audit:** uploads from unverified API projects are locked to *private*. Submit the
   free audit form (https://support.google.com/youtube/contact/yt_api_form) early.

## 2. Tokens (on your computer, once)
```bash
pip install google-auth-oauthlib
```
```bash
python scripts/authorize.py --profile pipeline --client-secret client_secret.json --out pipeline_token.json
```
```bash
python scripts/authorize.py --profile publisher --client-secret client_secret.json --out publisher_token.json
```

## 3. Drive folders + control spreadsheet (on your computer, once)
```bash
YTA_PIPELINE_OAUTH="$(cat pipeline_token.json)" DRY_RUN=false PYTHONPATH=src python -m ytauto init
```
It prints `SPREADSHEET_ID` and `DRIVE_FOLDER_IDS` (IDs only, not secrets).

## 4. Apps Script approval watcher
1. script.google.com → New project → paste `apps_script/approval_watcher.gs`.
2. Services (+) → **Gmail API** (identifier `Gmail`).
3. Project Settings → Script properties: `SPREADSHEET_ID`, `APPROVER_EMAIL`, `APPROVER_IS_OWNER=true`,
   `PUBLISHER_URL` (after step 5; use a placeholder until then), `ARCHIVE_FOLDER_ID` (the Drive
   `Archive` folder id printed by `init`), `WEBHOOK_SECRET` (generate with
   `python -c "import secrets; print(secrets.token_hex(32))"`), `DRY_RUN=true`.
4. Run `setup` once and authorise. It installs the 5-minute trigger and sets ledger cells to text.

## 5. Cloud Run publisher (needs billing — only after you authorise it)
```bash
gcloud secrets create yta-publisher-oauth --data-file=publisher_token.json
```
```bash
printf '%s' "<the same WEBHOOK_SECRET>" | gcloud secrets create yta-webhook-secret --data-file=-
```
```bash
gcloud artifacts repositories create ytauto --repository-format=docker --location=us-central1
```
```bash
gcloud builds submit --config cloudrun/cloudbuild.yaml --substitutions _IMAGE=us-central1-docker.pkg.dev/PROJECT_ID/ytauto/publisher:v1 .
```
```bash
gcloud run deploy ytauto-publisher --image us-central1-docker.pkg.dev/PROJECT_ID/ytauto/publisher:v1 \
  --region us-central1 --min-instances 0 --max-instances 1 --concurrency 1 --cpu 1 --memory 512Mi \
  --timeout 900 --cpu-throttling --allow-unauthenticated --env-vars-file publisher-env.yaml \
  --set-secrets YTA_PUBLISHER_OAUTH=yta-publisher-oauth:latest,WEBHOOK_SECRET=yta-webhook-secret:latest
```
`publisher-env.yaml` (not secret, but keep it out of git) contains `DRY_RUN: "true"`,
`UPLOAD_ENABLED: "false"`, `PUBLICATION_ENABLED: "false"`, `APPROVER_EMAIL`, `APPROVER_IS_OWNER`,
`SPREADSHEET_ID`, `DRIVE_FOLDER_IDS` (JSON string), `PUBLISH_TIMEZONE`, `PUBLISH_TIME`.
The service runs as the default compute service account: grant it
`roles/secretmanager.secretAccessor` on the two secrets.
Then delete the local token files.

## 6. Claude cloud environment (pipeline)
- **Network:** allow `oauth2.googleapis.com`, `www.googleapis.com` (Drive), `sheets.googleapis.com`,
  `youtube.googleapis.com`, `youtubeanalytics.googleapis.com`, `texttospeech.googleapis.com`,
  and for images: `commons.wikimedia.org`, `upload.wikimedia.org`, `api.openverse.org`; for the
  vision model downloads: `huggingface.co` and `us.aws.cdn.hf.co` (set `HF_HUB_DISABLE_XET=1`);
  optional Google Vision: `vision.googleapis.com` (API key as a network secret; needs billing).
- **Network secret:** TTS API key for host `texttospeech.googleapis.com`, header `X-Goog-Api-Key`.
- **Environment variables:** `YTA_PIPELINE_OAUTH` (contents of pipeline_token.json — entered by
  you in the environment settings), `DRY_RUN=true`, `APPROVER_EMAIL`, `SPREADSHEET_ID`,
  `DRIVE_FOLDER_IDS`, `TTS_PROVIDER=google`, `TTS_MONTHLY_CHAR_BUDGET=100000`.
  Do **not** set `UPLOAD_ENABLED` here (doctor refuses to run if it is true).
- **Setup script:** `apt-get install -y ffmpeg fonts-dejavu-core`,
  `pip install -r youtube-shorts-automation/requirements.txt` and
  `(cd youtube-shorts-automation && bash scripts/fetch_kokoro.sh)`.
- **Vision (optional, recommended):** install an OmniVLM-968M runtime (Nexa SDK or llama.cpp
  build with OmniVLM support) and set `VISION_CMD` (see `src/ytauto/production/vision.py`).
  Without it images are marked `visual_unverified` and you check them in the approval preview.

## 7. Daily scheduled task
Claude Code scheduled task, new session per run, daily at **10:07 Asia/Kolkata** (leaves time to
approve before 19:30). Prompt: `docs/scheduled_task_prompt.md`.

## Activation phases (each needs your explicit authorisation)
| Phase | Pipeline | Apps Script | Publisher | What happens |
|---|---|---|---|---|
| 1 Dry run | DRY_RUN=true | DRY_RUN=true | DRY_RUN=true | Local renders only; no Drive/Sheets writes, no emails |
| 2 Approval test | DRY_RUN=false | DRY_RUN=true | not called | Real Drive/Sheets, real approval emails; YES is logged, nothing uploads |
| 3 Publisher test | DRY_RUN=false | DRY_RUN=false | DRY_RUN=false, UPLOAD_ENABLED=false | Publisher verifies approval + checksum, skips upload |
| 4 Private upload | same | same | UPLOAD_ENABLED=true, PUBLICATION_ENABLED=false | Approved video uploads **private**, unscheduled |
| 5 Scheduled publishing | same | same | PUBLICATION_ENABLED=true | Approved video uploads private with publishAt 19:30 IST |
| 6 Promotion | PROMOTION_ENABLED=true | — | — | One Bluesky post after the video is public |
