#!/usr/bin/env bash
# Deploy / update the Cloud Run publisher from Google Cloud Shell (shell.cloud.google.com).
#
#   bash scripts/deploy_publisher.sh MODE [publisher_token.json]
#
# MODE controls the safety flags on the publisher (the ONLY component that can upload):
#   verify   DRY_RUN=false UPLOAD_ENABLED=false PUBLICATION_ENABLED=false  (checks approvals, uploads nothing)
#   private  DRY_RUN=false UPLOAD_ENABLED=true  PUBLICATION_ENABLED=false  (approved videos upload PRIVATE)
#   live     DRY_RUN=false UPLOAD_ENABLED=true  PUBLICATION_ENABLED=true   (approved videos scheduled public 19:30 IST)
#   off      DRY_RUN=true  UPLOAD_ENABLED=false PUBLICATION_ENABLED=false  (emergency stop)
# The token file is needed only the first time (or to replace the login); it is stored in Secret
# Manager and then deleted from Cloud Shell. Requires billing on the project (Cloud Run free tier).
set -euo pipefail
MODE="${1:?usage: deploy_publisher.sh verify|private|live|off [publisher_token.json]}"
TOKEN="${2:-}"
REGION="${REGION:-us-central1}"
SERVICE=ytauto-publisher
PROJECT="$(gcloud config get-value project 2>/dev/null)"
[ -n "$PROJECT" ] || { echo "Run: gcloud config set project YOUR_PROJECT_ID"; exit 1; }

case "$MODE" in
  verify)  FLAGS="DRY_RUN=false,UPLOAD_ENABLED=false,PUBLICATION_ENABLED=false" ;;
  private) FLAGS="DRY_RUN=false,UPLOAD_ENABLED=true,PUBLICATION_ENABLED=false" ;;
  live)    FLAGS="DRY_RUN=false,UPLOAD_ENABLED=true,PUBLICATION_ENABLED=true" ;;
  off)     FLAGS="DRY_RUN=true,UPLOAD_ENABLED=false,PUBLICATION_ENABLED=false" ;;
  *) echo "unknown mode $MODE"; exit 1 ;;
esac

if ! gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null | grep -qi true; then
  echo "Billing is not enabled on $PROJECT. Cloud Run needs a billing account (usage stays in the free tier)."
  echo "Enable it: https://console.cloud.google.com/billing/linkedaccount?project=$PROJECT  (set a budget alert too)"
  exit 1
fi

echo "== enabling services"
gcloud services enable run.googleapis.com secretmanager.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com --project "$PROJECT" --quiet

echo "== secrets"
if [ -n "$TOKEN" ]; then
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d.get('refresh_token'), 'no refresh_token'; \
s=' '.join(d.get('scopes',[])); assert 'youtube.upload' in s, 'not a publisher token (run authorize.py --profile publisher)'" "$TOKEN"
  if gcloud secrets describe yta-publisher-oauth --project "$PROJECT" >/dev/null 2>&1; then
    gcloud secrets versions add yta-publisher-oauth --data-file="$TOKEN" --project "$PROJECT" --quiet
  else
    gcloud secrets create yta-publisher-oauth --data-file="$TOKEN" --project "$PROJECT" --quiet
  fi
  shred -u "$TOKEN" 2>/dev/null || rm -f "$TOKEN"
  echo "   publisher login stored in Secret Manager; local copy deleted"
fi
gcloud secrets describe yta-publisher-oauth --project "$PROJECT" >/dev/null 2>&1 || {
  echo "First deploy needs the publisher token: bash scripts/deploy_publisher.sh $MODE publisher_token.json"; exit 1; }
if ! gcloud secrets describe yta-webhook-secret --project "$PROJECT" >/dev/null 2>&1; then
  python3 -c "import secrets; print(secrets.token_hex(32), end='')" | \
    gcloud secrets create yta-webhook-secret --data-file=- --project "$PROJECT" --quiet
  NEW_SECRET=1
fi
SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for s in yta-publisher-oauth yta-webhook-secret; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor --project "$PROJECT" --quiet >/dev/null
done

echo "== build"
gcloud artifacts repositories describe ytauto --location "$REGION" --project "$PROJECT" >/dev/null 2>&1 || \
  gcloud artifacts repositories create ytauto --repository-format=docker --location "$REGION" --project "$PROJECT" --quiet
IMAGE="$REGION-docker.pkg.dev/$PROJECT/ytauto/publisher:$(git rev-parse --short HEAD)"
gcloud builds submit --config cloudrun/cloudbuild.yaml --substitutions "_IMAGE=$IMAGE" --project "$PROJECT" .

echo "== deploy ($MODE: $FLAGS)"
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --project "$PROJECT" \
  --min-instances 0 --max-instances 1 --concurrency 1 --cpu 1 --memory 512Mi --timeout 900 \
  --cpu-throttling --allow-unauthenticated \
  --set-secrets YTA_PUBLISHER_OAUTH=yta-publisher-oauth:latest,WEBHOOK_SECRET=yta-webhook-secret:latest \
  --set-env-vars "$FLAGS,APPROVER_IS_OWNER=true" --quiet

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format='value(status.url)')"
curl -fsS "$URL/healthz" >/dev/null && echo "   health check OK"
echo
echo "PUBLISHER_URL = $URL"
echo "Next: put that URL in Drive YouTubeAutomation/00_Config/keys.json -> \"PUBLISHER_URL\" (or ask Claude to)."
if [ "${NEW_SECRET:-0}" = 1 ]; then
  echo "Then copy the webhook secret into the Apps Script (Project Settings -> Script properties -> WEBHOOK_SECRET):"
  echo "   gcloud secrets versions access latest --secret yta-webhook-secret --project $PROJECT; echo"
  echo "(Run that command, copy the output into the property. Do not paste it anywhere else.)"
fi
