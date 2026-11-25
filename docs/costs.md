# Costs and free allowances

Verified 24 Nov 2026 from Google's public pages (cached copies did not all agree, so re-check
the linked pages before activation). **Nothing paid is enabled by this repository.** Two parts of
the architecture need a Google Cloud *billing account* even when you stay inside the free tier:
Cloud Text-to-Speech and Cloud Run. Turning billing on is your decision. If you turn it on, set a
budget alert first (Billing → Budgets & alerts → e.g. US$1 with alerts at 50/90/100 %).

| Component | What we use | Free allowance (verify) | Our expected use | Cost risk |
|---|---|---|---|---|
| Claude Code cloud scheduled task | 1 run/day: Haiku researcher + reviewer, Sonnet storywriter | Included in your subscription's usage limits (not unlimited) | ~1 session/day, compact JSON hand-offs | Uses subscription limits; no API key or per-token billing |
| Fallback voice: Kokoro-82M (kokoro-onnx) | offline, CPU (~real-time on 4 vCPU, measured) | Free, Apache-2.0 weights | used when Google TTS fails or its budget is reached | None |
| Edge TTS (opt-in) | unofficial client for Microsoft Edge voices | No official terms for this use; Microsoft has blocked it before | disabled unless `ALLOW_EDGE_TTS=true` | ToS / reliability risk, not money |
| Music | procedural bed (default) or your ACE-Step 1.5 library (MIT) | Free | generated in < 1 s | None; MusicGen weights are non-commercial, so not used |
| Sound effects | procedural (default) or Earshot SFX pack (free, read its License.txt) | Free | ≤ 10 cues/video | None |
| Internet images | Wikimedia Commons + Openverse APIs (CC0 / PD / CC BY only, credited) | Free (Openverse anonymous limits are low; ~1–4 searches/day) | ≤ 2 images/video | None |
| Image check: OmniVision (OmniVLM-968M) | local CPU model, ~60-token answers | Free, Apache-2.0 (current model card) | ≤ 8 checks/video | None; saves Claude tokens |
| YouTube Data API v3 | 1 upload/day + a few reads | 10,000 units/day default. `videos.insert` is listed at 1,600 units in the quota calculator; the newer reference page describes a separate "Video Uploads" bucket | < 2,000 units/day | None (quota, not money) |
| YouTube Analytics API | per-video reports | Free (quota limited) | a few calls/day | None |
| Drive / Sheets / Gmail APIs, Apps Script | files, ledger, emails, 5-min trigger | Free within consumer quotas (Apps Script: 90 min/day of trigger runtime for consumer accounts; Gmail: 100 recipients/day for scripts) | ~288 short trigger runs/day, ≤ 5 emails/day | None; quota errors are logged |
| Cloud Text-to-Speech | Chirp 3 HD voice (default `en-US-Chirp3-HD-Charon`) | Chirp 3 HD: first 1M chars/month free, then US$30/1M. WaveNet/Standard: 4M free, then US$4/1M | Short ≤ 1,000 + long ≤ 8,000 chars/day → ≤ ~280k chars/month; voice cache makes re-renders free. Hard stop at `TTS_MONTHLY_CHAR_BUDGET` (default 600,000, never above 900,000) | **Needs billing enabled, but stays in the 1M free tier.** Over the cap the free offline Kokoro voice is used |
| Cloud Run (publisher) | min instances 0, max 1, 1 vCPU, 512 MiB, request-based billing, us-central1 | 180,000 vCPU-s, 360,000 GiB-s, 2M requests/month (Tier 1 regions) | ~1–2 min/day of runtime | **Needs billing.** Very small usage; scale-to-zero |
| Cloud Build + Artifact Registry (deploy only) | build the container on deploy | Artifact Registry 0.5 GB free storage; Cloud Build has a free monthly allowance | 1 image (~150 MB) | Small; delete old images |
| Secret Manager | 3–4 secrets | 6 active secret versions and 10,000 access operations/month free | few accesses/day | Keep ≤ 6 active versions |
| Bluesky | 1 post per published Short | Free | ≤ 1/day | None |
| Reddit | Drafts only (no API) | — | — | None |

Not used by default: ElevenLabs, paid video generation, GPUs, the Anthropic Messages API
(no `ANTHROPIC_API_KEY` is required anywhere), LinkedIn.

## Zero-billing fallback
If you prefer not to attach a billing account:
- **Voice:** set `TTS_PROVIDER=piper` with a Piper voice model whose licence permits commercial
  use (check the model card), installed in the cloud environment's setup script.
- **Publisher:** the Cloud Run step can be replaced by running `cloudrun/service.py` anywhere you
  already host (it is a plain Flask app). Apps Script itself cannot reliably upload large videos
  (50 MB blob limit), so it is not used as the uploader.

## Sources
- [Text-to-Speech pricing](https://cloud.google.com/text-to-speech/pricing)
- [YouTube Data API quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost) and [Videos: insert](https://developers.google.com/youtube/v3/docs/videos/insert)
- [Quota and compliance audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits)
- [Cloud Run pricing](https://cloud.google.com/run/pricing)
- [Three-minute YouTube Shorts](https://support.google.com/youtube/answer/15424877)
- [YouTube Analytics metrics](https://developers.google.com/youtube/analytics/metrics)
