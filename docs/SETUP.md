# Setup guide (about 1 hour, once)

Nothing is uploaded until you reply `YES VIDEO-…` to an approval email, and uploads stay switched
off until you turn them on yourself (step 9).

## 1. Google Cloud project (console.cloud.google.com)
1. Create a project, e.g. `yt-shorts-automation`.
2. **APIs & Services → Library**, enable: YouTube Data API v3, YouTube Analytics API, Google Drive API,
   Google Sheets API, Gmail API, Cloud Text-to-Speech API (optional: Cloud Vision API).
3. **OAuth consent screen**: External → add your Gmail as a test user → **Publish app** (status
   "In production"). Otherwise logins expire after 7 days.
4. **Credentials → Create credentials → OAuth client ID → Desktop app** → download the JSON as
   `client_secret.json` (keep it on your computer only).
5. **Credentials → API key** → *Restrict key* → allow only Cloud Text-to-Speech (and Cloud Vision
   if you use it). Text-to-Speech and Vision need a billing account even inside their free tiers;
   set a budget alert (Billing → Budgets) first. Without billing, the free offline voice (Kokoro) is used.
6. Submit the free **YouTube API audit** (https://support.google.com/youtube/contact/yt_api_form);
   until approved, API uploads stay private.

## 2. Log in once (on your computer)
```bash
git clone https://github.com/delitecoder/youtube-shorts-automation.git
```
```bash
cd youtube-shorts-automation && pip install -r requirements.txt
```
```bash
python scripts/authorize.py --profile pipeline --client-secret client_secret.json --out pipeline_token.json
```
```bash
python scripts/authorize.py --profile publisher --client-secret client_secret.json --out publisher_token.json
```

## 3. Create the Drive folders, the keys file and the control sheet
```bash
YTA_PIPELINE_OAUTH="$(cat pipeline_token.json)" DRY_RUN=false PYTHONPATH=src python -m ytauto init
```
This creates in your Drive:
```
YouTubeAutomation/
  00_Config/keys.json        ← your keys and settings (private!)
  01_Inbox/Videos  Documents  References/{Images,SFX,Music}
  02_Generated  03_Awaiting_Approval  04_Published  05_Rejected  Archive
```
and the spreadsheet `YouTube_Automation_Control`. It prints `SPREADSHEET_ID` and `DRIVE_FOLDER_IDS`.

## 4. Fill `00_Config/keys.json` (open it in Drive → Open with → Text editor, or edit and re-upload)
```json
{
  "APPROVER_EMAIL": "<the Gmail you approve from>",
  "SPREADSHEET_ID": "<printed by init>",
  "DRIVE_FOLDER_IDS": { "...": "<paste the whole object printed by init>" },
  "ARCHIVE_FOLDER_ID": "<the 'archive' id from DRIVE_FOLDER_IDS>",
  "PUBLISHER_URL": "<Cloud Run URL from step 6, later>",
  "GOOGLE_TTS_API_KEY": "<API key from 1.5, or empty>",
  "GOOGLE_VISION_API_KEY": "", "GOOGLE_VISION_ENABLED": "false",
  "BLUESKY_HANDLE": "", "BLUESKY_APP_PASSWORD": "",
  "IMAGE_API_CONTACT": "<optional contact for image APIs>",
  "DRY_RUN": "true", "PROMOTION_ENABLED": "false", "IMAGE_LICENSE_MODE": "owner_approval"
}
```
Rules enforced by the code: the file must be owned by you and **Sharing = Restricted** (it is
refused if shared with "anyone" or a domain). `UPLOAD_ENABLED`, `PUBLICATION_ENABLED`, the
publisher login and the webhook secret are never read from this file.

## 5. Claude cloud environment ("Youtube Automation")
In claude.ai/code → environment menu → **Edit**:
- **Environment variable** `YTA_PIPELINE_OAUTH` = the full contents of `pipeline_token.json`.
  This is the only secret Claude needs; it is what lets the daily run open keys.json.
  Optionally `YTA_KEYS_FILE_ID` = the id of keys.json (faster lookup, not secret).
- **Network**: allow `oauth2.googleapis.com`, `www.googleapis.com`, `sheets.googleapis.com`,
  `youtube.googleapis.com`, `youtubeanalytics.googleapis.com`, `texttospeech.googleapis.com`
  (already allowed: Wikimedia, Openverse, Hugging Face hosts). Ask Claude to add them for you.
Then delete `pipeline_token.json` from your computer.

## 6. Publisher on Cloud Run (needs billing; skip until you want real uploads)
Follow `docs/deployment.md` §5: put `publisher_token.json` and a random webhook secret in Secret
Manager and deploy. Copy the service URL into keys.json → `PUBLISHER_URL`.

## 7. Apps Script (approval emails)
1. script.google.com → New project → paste `apps_script/approval_watcher.gs`.
2. Services (+) → Gmail API.
3. Project Settings → Script properties: `KEYS_FILE_ID` = id of keys.json, `WEBHOOK_SECRET` = the
   same random secret as in Secret Manager (generate: `python -c "import secrets;print(secrets.token_hex(32))"`),
   `APPROVER_IS_OWNER` = `true`.
4. Run `setup` once and allow access. It then checks every 5 minutes.

## 8. Daily schedule
Already created by Claude: a scheduled task at 10:07 IST every day that clones this repo, runs the
pipeline in a fresh cloud session and stops early (cheaply) while setup is incomplete. The approval
email arrives shortly after; reply `YES VIDEO-…` or `NO VIDEO-…`. After a successful upload the
video file moves to `Archive`.

## 9. Go live in phases (each one is your decision)
1. keys.json `DRY_RUN=false` → real Drive/Sheets + real approval emails, nothing uploads.
2. Apps Script property `DRY_RUN=false` + Cloud Run `DRY_RUN=false` → publisher verifies, still no upload.
3. Cloud Run `UPLOAD_ENABLED=true` → approved videos upload **private**.
4. Cloud Run `PUBLICATION_ENABLED=true` → approved videos are scheduled public at 19:30 IST.
