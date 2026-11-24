# Going to production

Each step is reversible. `bash scripts/deploy_publisher.sh off` is the emergency stop.

| Step | Who | What happens |
|---|---|---|
| 1. `keys.json` → `"DRY_RUN": "false"` | owner (or Claude on request) | Daily run writes to Drive/Sheets and sends real approval emails. No uploads possible yet. |
| 2. Publisher login | owner, on the PC (one Allow) | `python scripts/authorize.py --profile publisher --client-secret client_secret.json --out publisher_token.json` |
| 3. Billing + budget alert | owner | Cloud Run needs a billing account (usage stays in the free tier). |
| 4. Deploy in `verify` mode | owner, Cloud Shell | `bash scripts/deploy_publisher.sh verify publisher_token.json` → prints PUBLISHER_URL |
| 5. Wire it | owner + Claude | PUBLISHER_URL into keys.json; WEBHOOK_SECRET into Apps Script Script properties |
| 6. Approve one test video | owner | Reply YES: publisher verifies approval + checksum, uploads nothing (`verify`). |
| 7. `deploy_publisher.sh private` | owner | Approved videos upload as **private**. Check one on YouTube Studio. |
| 8. `deploy_publisher.sh live` | owner | Approved videos are scheduled public at 19:30 IST. |

YouTube note: until the free API compliance audit is approved, YouTube keeps API uploads private
even in `live` mode (https://support.google.com/youtube/contact/yt_api_form).
