# Setup (about 10 minutes, once)

Nothing is uploaded to YouTube during setup. Uploads stay off until you turn them on later.

## What you need
- `client_secret.json` — your OAuth **Desktop app** client from Google Cloud. Keep it on **your
  computer**. Do **not** upload it to Drive or GitHub; it is only needed by the command below.
- Python 3.11+ on your computer.

## 1. Run one command on your computer
```bash
git clone https://github.com/delitecoder/youtube-shorts-automation.git
```
```bash
cd youtube-shorts-automation && pip install -r requirements-setup.txt
```
Put `client_secret.json` in this `youtube-shorts-automation` folder, then:
```bash
python scripts/bootstrap.py --client-secret client_secret.json --approver YOUR_GMAIL
```
- It first prints a link that enables all needed Google APIs in your project: open it, click
  **Enable**, come back.
- Your browser opens twice: choose your Google account and click **Allow** each time
  (1: daily pipeline login, 2: creating the approval Apps Script).
- It creates in your Drive:
  ```
  YouTubeAutomation/
    00_Config/keys.json  +  YouTube_Automation_Control (spreadsheet)
    01_Inbox/Videos  Documents  References/{Images,SFX,Music}
    02_Generated  03_Awaiting_Approval  04_Published  05_Rejected  Archive
  ```
  and fills `keys.json` with all IDs and your approver email (DRY_RUN = true).

## 2. Three clicks the script prints at the end
1. Open the Apps Script link → select function **setup** → **Run** → **Allow**.
2. claude.ai/code → environment **Youtube Automation** → **Edit** → add environment variable
   `YTA_PIPELINE_OAUTH` = the whole text of `pipeline_token.json` (open it in a text editor).
   Then delete `pipeline_token.json` from your computer.
3. Tell Claude "setup done" — it runs a test and checks everything.

## Optional, any time later
- **Better voice:** create an API key restricted to Cloud Text-to-Speech (needs billing; free
  tier) and put it in `keys.json` → `GOOGLE_TTS_API_KEY`. Without it the free offline voice is used.
- **Uploads:** the Cloud Run publisher (docs/deployment.md §5) — only when you decide to go live.
- **Your own content:** drop videos in `01_Inbox/Videos` (with `RIGHTS: OWNED` in the file's
  Drive description), documents in `01_Inbox/Documents`, images in `References/Images`.

## Go live in phases (each one is your decision)
1. `keys.json` → `DRY_RUN: "false"`: real Drive/Sheets + real approval emails; nothing uploads.
2. Publisher deployed, `DRY_RUN=false` there and in Apps Script: approvals verified, still no upload.
3. Publisher `UPLOAD_ENABLED=true`: approved videos upload **private**.
4. Publisher `PUBLICATION_ENABLED=true`: approved videos are scheduled public at 19:30 IST.
