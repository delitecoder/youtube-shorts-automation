# Apps Script changelog (approval_watcher.gs)

The owner updates the script; Claude never pushes it. Newest first. Each entry = one SCRIPT_VERSION.

## 2026.11.25-3
- **Tamil versions**: every video is also made in Tamil (ids: Short 051–099, long 151–199). The
  approval email says "Tamil Short" / "Tamil long video"; the upload sets the video and audio
  language from the video (Tamil = `ta`) instead of always English.
- Skipped videos are only re-offered for a slot of the same format AND language.
- Approval reply format unchanged (`APPROVED VIDEO-…`). No new service or permission.

## 2026.11.25-2
- Every upload now declares "no paid product placement" explicitly. YouTube stopped assuming it
  on 2026-11-03; without it videos are not marked as free of paid promotion.
- (Includes 2026.11.25-1 below: long videos + chunked upload.) No new service or permission.

## 2026.11.25-1
- **Daily long video (16:9 system-design explainer, ids VIDEO-YYYYMMDD-101…199)** next to the Short.
  Files over 45 MB are uploaded with YouTube's **resumable upload in 32 MB chunks** read straight
  from Drive; before uploading, Drive's own SHA-256 of the file must equal the approved checksum.
  A long upload can continue across 5-minute runs.
- Approval email says "long video" or "Short"; skipped videos are only re-offered for a slot of the
  same kind (a Short never replaces the long video and vice versa).
- Approval rules and reply format unchanged. No new advanced service. Google may ask you to
  re-authorise once (the script now calls the Drive/YouTube REST endpoints with its own token).

## 2026.11.24-4
- All notification emails (upload confirmations, daily views audit, alerts, test messages, update
  notices) are now neat HTML: one colour-coded card per update, clickable links and a
  "Watch on YouTube" button for uploads. Plain text is kept as a fallback.
- No new service or permission.

## 2026.11.24-3
- Approval email also offers up to 3 **skipped videos** from the last 7 days ("Upload this one"
  button or reply `APPROVED <their id>`). Choosing one uses today's slot; the others, including
  today's new video, stay skipped and can be chosen another day.
- Daily **archive cleanup**: Archive files of videos published more than 30 days ago go to Drive
  Trash (restorable for 30 days). Skipped/rejected/unknown files are never touched.
- Buttons web app (`approval_buttons.gs`) updated too: re-paste it and redeploy (Manage
  deployments → edit → New version) so the skipped-video buttons work.
- No new service or permission.

## 2026.11.24-2
- Approval email can show one-click **Approve / Reject** buttons. They open a small web app
  (`apps_script/approval_buttons.gs`, a separate project owned by the approver) that sends the
  normal one-line reply from the approver's Gmail; all existing reply checks still apply.
- New optional Script Property `APPROVAL_BUTTONS_URL` (the buttons web app `/exec` URL). Without it
  the email is unchanged. Setup steps are at the top of `approval_buttons.gs`.
- No new service or permission for this project (the buttons project needs Gmail only).

## 2026.11.24-1
- Uploads approved videos itself (no Cloud Run needed), scheduled for the evening peak slot.
- Neat HTML approval email (poster, Drive preview button); reply `APPROVED VIDEO-…` / `REJECTED VIDEO-…`.
- No reply before the slot (−15 min) = skipped; moves uploaded videos to Archive.
- Emails: daily views audit, Claude usage-limit alerts, "no video today" watchdog, update notices.
- Needs: YouTube Data API v3 service + running `setup` once (new YouTube permission).
