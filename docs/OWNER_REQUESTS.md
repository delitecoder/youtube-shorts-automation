# Owner requests (2026-11-24) — checklist so nothing is lost

Status: [x] done in code + tested · [ ] still to do. Keep every earlier feature working.

## Content
- [x] Daily theme rotation (IST weekday): Mon/Fri Trending Tech (most-searched), Tue/Sat Tech Story,
      Wed/Sun Tech News (last 7 days), Thu Tech Motivation. `discover` → `theme`. Categories
      "tech stories", "motivation". (config.py `CONTENT_ROTATION`, tests/test_themes.py)
- [x] Owner-written theme for a date in the Google Sheet (tab `ThemePlan`: date, theme, notes) overrides
      the rotation for that day.
- [x] Storywriter: series label = theme label as title prefix; motivation ends with one lesson.

## Look & sound (reference style: big keyword, section label, chapter dashes, caption pill, recurring host)
- [x] DeliteCoder logo: synced by `sync-library` (Drive References/Images/brand_logo.png, original logo;
      first fetched from the channel avatar), badge + "@delitecoder" + series label top-left,
      pops in and pulses at each scene, chapter dashes top-right. Both renderers.
- [x] Music: lightweight analysis (tempo/energy/brightness, numpy only) of licensed library tracks,
      pick by theme mood; procedural music tuned to theme. NO copyrighted trending songs
      (Content ID/strikes on API uploads) — explain to owner.
- Reference Shorts (Rick The Engineer) could not be downloaded: YouTube bot check (not bypassed).

## Approval email / Apps Script (bump SCRIPT_VERSION + CHANGELOG; owner deploys)
- [x] One-click Approve/Reject buttons (approval_buttons.gs web app, `APPROVAL_BUTTONS_URL`).
- [x] Select a skipped (EXPIRED) video from the email to upload in today's slot instead.
- [x] Cleanup (Apps Script, once a day): trash Archive files older than 30 days ONLY for published videos.

## Constraints
- Low storage, fast. Never bypass approval; never change upload flags; no secrets in output.
- Push to `main` needs owner approval of the permission prompt (blocked so far).
- Owner must loosen network restriction themselves (Claude cannot change environment settings).
