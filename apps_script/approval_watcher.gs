/**
 * YouTube Shorts approval watcher (Google Apps Script, runs as you, every 5 minutes).
 *
 * Responsibilities (no AI/model calls):
 *  1. Send approval-request emails for VideoQueue rows in AWAITING_APPROVAL without a request.
 *  2. Read replies in those threads; accept only exactly "YES VIDEO-ID" / "NO VIDEO-ID" from the
 *     configured approver, sent after the request and within 24 h, not quoted, not replayed.
 *  3. Log EVERY evaluated reply to ApprovalLog; transition AWAITING_APPROVAL -> APPROVED/REJECTED.
 *  4. Expire requests older than 24 h (AWAITING_APPROVAL -> EXPIRED).
 *  5. Call the publisher (HMAC-signed) for APPROVED rows; retry until it answers.
 *  6. Email ErrorLog notifications.
 *
 * Script properties (Project Settings > Script properties):
 *   SPREADSHEET_ID, APPROVER_EMAIL, PUBLISHER_URL, WEBHOOK_SECRET (>= 32 random chars),
 *   APPROVER_IS_OWNER ("true" when APPROVER_EMAIL is this Google account), DRY_RUN ("true"/"false"),
 *   ARCHIVE_FOLDER_ID (Drive "Archive" folder: the video file is moved there after a successful upload)
 * Advanced services: Gmail API ("Gmail", labelIds + Authentication-Results of replies) and
 * YouTube Data API ("YouTube", the upload). Both are declared in appsscript.json.
 *   KEYS_FILE_ID (optional): Drive id of YouTubeAutomation/00_Config/keys.json; then SPREADSHEET_ID,
 *   APPROVER_EMAIL, PUBLISHER_URL, ARCHIVE_FOLDER_ID and DRY_RUN can live in that file instead.
 * Setup: run setup() once, authorise, done.
 */

// Bump on every change and add an entry to apps_script/CHANGELOG.md: the daily run compares this
// with the deployed version and emails the approver when an update is needed.
var SCRIPT_VERSION = '2026.11.26-1';
var TTL_HOURS = 24;
var SLOT_MARGIN_MIN = 15;            // approvals must arrive this long before the publish slot
var MAX_UPLOAD_BYTES = 45 * 1024 * 1024;   // Apps Script blob limit: bigger files use the chunked upload
var CHUNK_BYTES = 32 * 1024 * 1024;        // resumable upload chunk (multiple of 256 KiB, < 50 MB fetch limit)
// Long-form (16:9) videos use ids VIDEO-YYYYMMDD-101…199; Shorts use 001…099 (same reply format).
function isLong_(vid) { return Number(String(vid).slice(-3)) > 100; }
// Tamil versions: Short 051–099, long 151–199 (English: 001–050, 101–150)
function isTamil_(vid) { return Number(String(vid).slice(-3)) % 100 > 50; }
function kind_(vid) { return (isLong_(vid) ? 'long' : 'short') + (isTamil_(vid) ? '-ta' : '-en'); }
var CMD_RE = /^(YES|NO|APPROVED|REJECTED|APPROVE|REJECT) (VIDEO-\d{8}-\d{3})$/;
var CMD_MAP = { YES: 'YES', APPROVED: 'YES', APPROVE: 'YES', NO: 'NO', REJECTED: 'NO', REJECT: 'NO' };
// Upload flags live ONLY in Script Properties (the Claude pipeline can write keys.json, not these):
//   UPLOAD_ENABLED=true  -> approved videos are uploaded by this script (private)
//   PUBLICATION_ENABLED=true -> upload carries publishAt = the approved evening slot
//   UPLOADER=cloud_run   -> use the Cloud Run publisher instead (needs PUBLISHER_URL + WEBHOOK_SECRET)
var QUOTE_MARKERS = [/^-{2,}\s*Original Message\s*-{2,}/i, /^_{5,}$/, /^From: .+/, /^Sent from my .+/i, /^Get Outlook for .+/i];

function props_() { return PropertiesService.getScriptProperties(); }

// Non-secret settings may come from Drive 00_Config/keys.json (set Script Property KEYS_FILE_ID).
// WEBHOOK_SECRET is never read from Drive: it stays in Script Properties only.
var FROM_KEYS_FILE = ['SPREADSHEET_ID', 'APPROVER_EMAIL', 'PUBLISHER_URL', 'ARCHIVE_FOLDER_ID', 'DRY_RUN'];
var keysCache_ = null;
function keysFile_() {
  if (keysCache_ !== null) return keysCache_;
  keysCache_ = {};
  var id = props_().getProperty('KEYS_FILE_ID') || findKeysFileId_();
  if (!id) return keysCache_;
  var f = DriveApp.getFileById(id);
  var access = f.getSharingAccess();
  if (access === DriveApp.Access.ANYONE || access === DriveApp.Access.ANYONE_WITH_LINK ||
      access === DriveApp.Access.DOMAIN || access === DriveApp.Access.DOMAIN_WITH_LINK) {
    throw new Error('keys.json is shared too widely; set sharing to Restricted');
  }
  keysCache_ = JSON.parse(f.getBlob().getDataAsString());
  return keysCache_;
}
/** YouTubeAutomation/00_Config/keys.json in My Drive (exactly one match required). */
function findKeysFileId_() {
  var roots = DriveApp.getRootFolder().getFoldersByName('YouTubeAutomation');
  if (!roots.hasNext()) return null;
  var root = roots.next();
  if (roots.hasNext()) throw new Error('More than one YouTubeAutomation folder in My Drive');
  var cfg = root.getFoldersByName('00_Config');
  if (!cfg.hasNext()) return null;
  var files = cfg.next().getFilesByName('keys.json');
  if (!files.hasNext()) return null;
  var id = files.next().getId();
  if (files.hasNext()) throw new Error('More than one keys.json in 00_Config');
  return id;
}

function prop_(k) {
  var v = props_().getProperty(k);
  if (!v && FROM_KEYS_FILE.indexOf(k) >= 0) v = keysFile_()[k];
  if (!v) throw new Error('Missing setting ' + k + ' (Script Properties or keys.json)');
  return String(v);
}
function nowIso_() { return new Date().toISOString().replace(/\.\d{3}Z$/, '+00:00'); }

function setup() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('run').timeBased().everyMinutes(5).create();
  ['SPREADSHEET_ID', 'APPROVER_EMAIL'].forEach(prop_);   // publisher settings are only needed later
  var ws = props_().getProperty('WEBHOOK_SECRET');
  if (ws && ws.length < 32) throw new Error('WEBHOOK_SECRET too short');
  // Ledger cells must stay plain text (no automatic date/number conversion).
  SpreadsheetApp.openById(prop_('SPREADSHEET_ID')).getSheets().forEach(function (sh) {
    sh.getRange(1, 1, sh.getMaxRows(), sh.getMaxColumns()).setNumberFormat('@');
  });
}

function run() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return;
  try {
    var ss = SpreadsheetApp.openById(prop_('SPREADSHEET_ID'));
    reportVersion_(ss);
    sendRequests_(ss);
    checkReplies_(ss);
    expire_(ss);
    if (props_().getProperty('UPLOADER') === 'cloud_run') callPublisher_(ss); else uploadApproved_(ss);
    try { setThumbnails_(ss); } catch (e) { logError_(ss, '', 'uploader', 'thumbnails: ' + e, 'WARNING'); }
    noVideoCheck_(ss);
    cleanupArchive_(ss);
    sendNotifications_(ss);
  } catch (e) {
    try { logError_(SpreadsheetApp.openById(prop_('SPREADSHEET_ID')), '', 'apps_script', String(e)); } catch (ignored) {}
  } finally {
    lock.releaseLock();
  }
}

/** Record the deployed version (once per change) so the daily run can spot an outdated script. */
function reportVersion_(ss) {
  if (props_().getProperty('reported_version') === SCRIPT_VERSION) return;
  append_(ss, 'Configuration', { key: 'apps_script_version', value: SCRIPT_VERSION, updated_at: nowIso_() });
  props_().setProperty('reported_version', SCRIPT_VERSION);
}

/* ------------------------------------------------------------------ sheet helpers */
function table_(ss, name) {
  var sh = ss.getSheetByName(name);
  var values = sh.getDataRange().getDisplayValues();
  var hdr = values[0].map(String);
  var rows = [];
  for (var i = 1; i < values.length; i++) {
    var o = { _row: i + 1 };
    hdr.forEach(function (h, j) { o[h] = values[i][j] === null ? '' : String(values[i][j]); });
    rows.push(o);
  }
  return { sheet: sh, hdr: hdr, rows: rows };
}

function append_(ss, name, obj) {
  var t = table_(ss, name);
  t.sheet.appendRow(t.hdr.map(function (h) { return obj[h] === undefined ? '' : String(obj[h]); }));
}

/** Compare-and-set update of one VideoQueue row (state + version must match). */
function casUpdate_(ss, videoId, expectState, expectVersion, updates) {
  var t = table_(ss, 'VideoQueue');
  var row = t.rows.filter(function (r) { return r.video_id === videoId; })[0];
  if (!row || row.state !== expectState || String(row.version) !== String(expectVersion)) return false;
  updates.version = String(Number(row.version) + 1);
  updates.updated_at = nowIso_();
  updates.write_token = Utilities.getUuid().replace(/-/g, '').slice(0, 16);
  var values = t.hdr.map(function (h) { return updates[h] !== undefined ? String(updates[h]) : row[h]; });
  t.sheet.getRange(row._row, 1, 1, t.hdr.length).setValues([values]);
  return true;
}

function logError_(ss, videoId, component, message, severity) {
  append_(ss, 'ErrorLog', { logged_at: nowIso_(), video_id: videoId, component: component,
    severity: severity || 'ERROR', message: String(message).slice(0, 2000), notified: '' });
}

/* ------------------------------------------------------------------ 1. requests */
function ist_(iso) {
  return iso ? Utilities.formatDate(new Date(iso), 'Asia/Kolkata', 'EEE d MMM yyyy, HH:mm') + ' IST' : '-';
}
function deadline_(requestedIso, publishIso) {
  var d = new Date(requestedIso).getTime() + TTL_HOURS * 3600e3;
  if (publishIso) d = Math.min(d, new Date(publishIso).getTime() - SLOT_MARGIN_MIN * 60e3);
  return d;
}
function snap_(vid) { var v = props_().getProperty('req:' + vid); return v ? JSON.parse(v) : null; }
function esc_(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

function sendRequests_(ss) {
  var approver = prop_('APPROVER_EMAIL');
  table_(ss, 'VideoQueue').rows.forEach(function (r) {
    if (r.state !== 'AWAITING_APPROVAL' || r.approval_thread_id) return;
    var qr = {}; try { qr = JSON.parse(r.quality_report || '{}'); } catch (e) {}
    var refs = []; try { refs = JSON.parse(r.references || '[]'); } catch (e) {}
    if (props_().getProperty('APPROVER_IS_OWNER') === 'false') {      // approver always gets access
      try { DriveApp.getFileById(r.drive_file_id).addViewer(approver); } catch (e) {
        logError_(ss, r.video_id, 'apps_script', 'Could not share the video with the approver: ' + e, 'WARNING'); }
    }
    var deadline = deadline_(new Date().toISOString(), r.proposed_publish_at);
    var buttons = props_().getProperty('APPROVAL_BUTTONS_URL') || '';   // approver's approval_buttons.gs web app
    if (!/^https:\/\/script\.google\.com\/macros\/s\/[\w-]+\/exec$/.test(buttons)) buttons = '';
    var subject = '🎬 Approve today\'s ' + (isTamil_(r.video_id) ? 'Tamil ' : '') + (isLong_(r.video_id) ? 'long video' : 'Short') + '? ' + r.video_id + ' · ' + r.title;
    var cands = candidates_(ss, r);                     // recently skipped videos, offered as alternatives
    var btn_ = function (action, vid) { return buttons + '?a=' + action + '&v=' + vid + '&r=' + r.video_id; };
    var plain = [
      'Video ' + r.video_id + ': ' + r.title, 'Preview (private Drive link): ' + r.preview_url,
      'Publishes: ' + ist_(r.proposed_publish_at) + ' if approved before ' + ist_(new Date(deadline).toISOString()),
      '', 'Reply with exactly one line:', '  APPROVED ' + r.video_id, '  REJECTED ' + r.video_id,
      buttons ? 'Or use the buttons: approve ' + btn_('approve', r.video_id) +
                ' | reject ' + btn_('reject', r.video_id) : '',
      cands.length ? '\nOr upload a skipped video in this slot instead (reply APPROVED <its id>):' : '']
      .concat(cands.map(function (c) { return '  APPROVED ' + c.video_id + '  (' + c.title + ') ' + c.preview_url +
                                               (buttons ? ' | ' + btn_('approve', c.video_id) : ''); })).concat([
      'No reply = skipped (nothing is uploaded).']).join('\n');
    var poster = null;
    if (qr.poster_file_id) { try { poster = DriveApp.getFileById(qr.poster_file_id).getBlob().setName('poster.jpg'); } catch (e) {} }
    var imgs = (qr.images || []).map(function (i) {
      return '<li>' + esc_(i.description || i.file).slice(0, 200) + ' <span style="color:#667">— ' + esc_(i.creator || 'unknown') +
        ', ' + esc_(i.license) + '</span></li>'; }).join('');
    var refsHtml = refs.map(function (x) { return '<li><a href="' + esc_(x.url) + '">' + esc_(x.title || x.url) + '</a></li>'; }).join('');
    var review = (qr.review || {});
    var html =
      '<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1b1f2a">' +
      '<div style="background:#0f1530;color:#fff;padding:18px 22px;border-radius:14px 14px 0 0">' +
      '<div style="font-size:12px;letter-spacing:2px;color:#9fb0ff">DAILY SHORT · ' + esc_(r.video_id) + '</div>' +
      '<div style="font-size:22px;font-weight:bold;margin-top:6px">' + esc_(r.title) + '</div></div>' +
      '<div style="border:1px solid #e3e6f0;border-top:0;border-radius:0 0 14px 14px;padding:20px 22px">' +
      (poster ? '<img src="cid:poster" style="width:220px;border-radius:12px;float:right;margin:0 0 12px 16px">' : '') +
      '<p style="margin:0 0 10px"><b>Hook:</b> ' + esc_(r.hook) + '</p>' +
      '<p style="margin:0 0 10px"><b>Duration:</b> ' + esc_(r.duration_s) + ' s · <b>Source:</b> ' + esc_(r.source_type) +
      (review.score ? ' · <b>Review score:</b> ' + esc_(review.score) + '/10' : '') + '</p>' +
      '<p style="margin:0 0 10px"><b>Publishes:</b> ' + ist_(r.proposed_publish_at) + '</p>' +
      '<p><a href="' + esc_(r.preview_url) + '" style="background:#2563eb;color:#fff;padding:12px 20px;border-radius:10px;' +
      'text-decoration:none;font-weight:bold;display:inline-block">▶ Watch the preview</a></p>' +
      '<div style="clear:both"></div>' +
      '<p style="margin:14px 0 4px"><b>Description</b></p><div style="white-space:pre-wrap;background:#f6f7fb;padding:10px;' +
      'border-radius:8px;font-size:13px">' + esc_(r.description) + '</div>' +
      (imgs ? '<p style="margin:14px 0 4px"><b>Images</b></p><ul style="font-size:13px;padding-left:18px">' + imgs + '</ul>' : '') +
      (refsHtml ? '<p style="margin:14px 0 4px"><b>Sources</b></p><ul style="font-size:13px;padding-left:18px">' + refsHtml + '</ul>' : '') +
      '<div style="background:#ecfdf3;border:1px solid #a6f4c5;border-radius:12px;padding:14px 16px;margin-top:16px">' +
      (buttons ? '<p style="margin:0 0 12px">' +
        '<a href="' + esc_(btn_('approve', r.video_id)) + '" style="background:#16a34a;color:#fff;padding:12px 22px;' +
        'border-radius:10px;text-decoration:none;font-weight:bold;display:inline-block;margin:0 8px 8px 0">✔ Approve</a>' +
        '<a href="' + esc_(btn_('reject', r.video_id)) + '" style="background:#dc2626;color:#fff;padding:12px 22px;' +
        'border-radius:10px;text-decoration:none;font-weight:bold;display:inline-block;margin:0 0 8px 0">✖ Reject</a><br>' +
        '<span style="color:#555;font-size:13px">A button sends the reply below for you from your Gmail.</span></p>' +
        '<b>Or reply to this email with exactly one line:</b><br>'
        : '<b>Reply to this email with exactly one line:</b><br>') +
      '<code style="font-size:16px">APPROVED ' + esc_(r.video_id) + '</code> &nbsp;to upload &amp; schedule<br>' +
      '<code style="font-size:16px">REJECTED ' + esc_(r.video_id) + '</code> &nbsp;to skip<br>' +
      '<span style="color:#555;font-size:13px">Approve before <b>' + ist_(new Date(deadline).toISOString()) +
      '</b>. No reply = skipped, nothing is uploaded.</span></div>' +
      (cands.length ? '<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:14px 16px;margin-top:14px">' +
        '<b>Or upload a skipped video in this slot instead</b><br><span style="color:#555;font-size:13px">' +
        'Approving one of these uses today\'s slot; the others (and today\'s new video) stay skipped for later.</span>' +
        cands.map(function (c) {
          return '<div style="border-top:1px solid #fde3c8;margin-top:10px;padding-top:10px"><b>' + esc_(c.title) + '</b><br>' +
            '<a href="' + esc_(c.preview_url) + '">▶ preview</a> &nbsp; ' +
            (buttons ? '<a href="' + esc_(btn_('approve', c.video_id)) + '" style="background:#16a34a;color:#fff;padding:8px 16px;' +
              'border-radius:8px;text-decoration:none;font-weight:bold">✔ Upload this one</a> &nbsp; ' : '') +
            '<code>APPROVED ' + esc_(c.video_id) + '</code></div>';
        }).join('') + '</div>' : '') +
      '<p style="color:#99a;font-size:11px;margin-top:14px">SHA-256 ' + esc_(r.sha256) + '</p></div></div>';
    var opts = { htmlBody: html };
    if (poster) opts.inlineImages = { poster: poster };
    var msg = GmailApp.createDraft(approver, subject, plain, opts).send();
    var sentAt = msg.getDate().toISOString().replace(/\.\d{3}Z$/, '+00:00');
    // Snapshot of exactly what was put in front of the approver. Uploads use ONLY this snapshot.
    props_().setProperty('req:' + r.video_id, JSON.stringify({
      drive_file_id: r.drive_file_id, sha256: r.sha256, title: r.title, description: r.description,
      tags: r.tags, publish_at: r.proposed_publish_at, thread_id: msg.getThread().getId(),
      category_id: String((qr.youtube || {}).category_id || '28'),
      language: String((qr.youtube || {}).language || 'en'),
      synthetic: (qr.youtube || {}).synthetic === true, thumbnail_file_id: qr.thumbnail_file_id || '',
      request_message_id: msg.getId(), requested_at: sentAt }));
    var ok = casUpdate_(ss, r.video_id, 'AWAITING_APPROVAL', r.version, {
      approval_thread_id: msg.getThread().getId(), approval_request_message_id: msg.getId(),
      approval_requested_at: sentAt,
      approval_expires_at: new Date(deadline_(sentAt, r.proposed_publish_at)).toISOString().replace(/\.\d{3}Z$/, '+00:00')
    });
    if (!ok) logError_(ss, r.video_id, 'apps_script', 'request sent but row changed concurrently; check manually');
    reoffer_(ss, cands, r, msg, sentAt, approver);
  });
}

/** Skipped (EXPIRED) videos from the last 7 days that were never uploaded and whose file still exists. */
function candidates_(ss, r) {
  var since = Utilities.formatDate(new Date(Date.now() - 7 * 864e5), 'Asia/Kolkata', 'yyyyMMdd');
  return table_(ss, 'VideoQueue').rows.filter(function (c) {
    if (c.state !== 'EXPIRED' || c.video_id === r.video_id || !c.drive_file_id || !snap_(c.video_id)) return false;
    if (kind_(c.video_id) !== kind_(r.video_id)) return false;   // same format and language only
    if (c.video_id.slice(6, 14) < since || props_().getProperty('up:' + c.video_id)) return false;
    try { return !DriveApp.getFileById(c.drive_file_id).isTrashed(); } catch (e) { return false; }
  }).sort(function (a, b) { return a.video_id < b.video_id ? 1 : -1; }).slice(0, 3);
}

/** Put the offered skipped videos into this request: same thread, today's slot, fresh snapshot. */
function reoffer_(ss, cands, r, msg, sentAt, approver) {
  cands.forEach(function (c) {
    var old = snap_(c.video_id);
    props_().setProperty('req:' + c.video_id, JSON.stringify(Object.assign({}, old, {
      publish_at: r.proposed_publish_at, thread_id: msg.getThread().getId(),
      request_message_id: msg.getId(), requested_at: sentAt })));
    if (props_().getProperty('APPROVER_IS_OWNER') === 'false') {
      try { DriveApp.getFileById(c.drive_file_id).addViewer(approver); } catch (e) {}
    }
    if (!casUpdate_(ss, c.video_id, 'EXPIRED', c.version, {
          state: 'AWAITING_APPROVAL', approval_thread_id: msg.getThread().getId(),
          approval_request_message_id: msg.getId(), approval_requested_at: sentAt,
          proposed_publish_at: r.proposed_publish_at,
          approval_expires_at: new Date(deadline_(sentAt, r.proposed_publish_at)).toISOString().replace(/\.\d{3}Z$/, '+00:00'),
          notes: 're-offered with ' + r.video_id }))
      logError_(ss, c.video_id, 'apps_script', 'could not re-offer skipped video (row changed)', 'WARNING');
  });
}

function imageLines_(qr) {
  var imgs = qr.images || [];
  if (!imgs.length) return [];
  var out = ['', 'Images used:'];
  imgs.forEach(function (i) {
    out.push('- ' + i.file + ': ' + (i.description || '(no description)').slice(0, 300));
    out.push('  Creator: ' + (i.creator || 'unknown') + ' | Licence: ' + i.license + ' | ' + (i.page_url || ''));
  });
  var restricted = imgs.filter(function (i) { return i.requires_permission === true || i.requires_permission === 'true'; });
  if (restricted.length) {
    out.push('Note: ' + restricted.length + ' image(s) are not openly licensed; their creators are credited in the ' +
      'description. Your YES/NO for this video covers them (no separate image approval).');
  }
  return out;
}

/* ------------------------------------------------------------------ 2. replies */
function newText_(body) {
  var lines = String(body).replace(/\r\n?/g, '\n').split('\n');
  var out = [];
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim();
    if (line.charAt(0) === '>' || line === '--') break;
    if (QUOTE_MARKERS.some(function (re) { return re.test(line); })) break;
    if (/^On .*/.test(line) && lines.slice(i, i + 3).join(' ').indexOf('wrote:') >= 0) break;
    out.push(line);
  }
  return out.filter(function (l) { return l.length > 0; });
}

function parseAddr_(from) {
  var m = String(from).match(/<([^>]+)>/);
  return (m ? m[1] : String(from)).trim().toLowerCase();
}

function senderAuthenticated_(labelIds, authResults, approver) {
  if (props_().getProperty('APPROVER_IS_OWNER') !== 'false') return labelIds.indexOf('SENT') >= 0;
  var ar = String(authResults).toLowerCase().replace(/\s+/g, ' ');
  var domain = approver.split('@')[1];
  return ar.indexOf('mx.google.com;') === 0 && ar.indexOf('dmarc=pass') >= 0 &&
         ar.indexOf('header.from=' + domain) >= 0 && ar.indexOf('dkim=pass') >= 0;
}

/** Same rules as src/ytauto/approval/gate.py::validate_reply. Returns 'YES'/'NO' or throws reason. */
function validate_(r, ev, approver, usedIds) {
  if (r.state !== 'AWAITING_APPROVAL') throw 'record state is ' + r.state;
  if (!r.approval_thread_id || ev.thread_id !== r.approval_thread_id) throw 'reply is not in the approval request thread';
  if (ev.message_id === r.approval_request_message_id) throw 'message is the approval request itself';
  if (usedIds[ev.message_id]) throw 'message already processed (replay/duplicate)';
  if (parseAddr_(ev.sender) !== approver) throw 'sender is not the configured approver';
  if (!senderAuthenticated_(ev.label_ids, ev.auth_results, approver)) throw 'sender authentication failed';
  var requested = new Date(r.approval_requested_at);
  if (!(ev.sent_at > requested)) throw 'reply predates the approval request';
  var sn = r.video_id ? snap_(r.video_id) : null;
  var publishAt = (sn && sn.publish_at) || r.proposed_publish_at || '';
  if (ev.sent_at.getTime() > deadline_(r.approval_requested_at, publishAt)) throw 'reply arrived after the approval expired';
  if (ev.sent_at.getTime() > Date.now() + 5 * 60e3) throw 'reply timestamp is in the future';
  var lines = newText_(ev.body);
  if (lines.length === 0) throw 'empty reply (command may only be inside quoted text)';
  if (lines.length > 1) throw 'ambiguous reply: more than one line of new text';
  var m = lines[0].match(CMD_RE);
  if (!m) throw "malformed command; expected exactly 'APPROVED VIDEO-ID' or 'REJECTED VIDEO-ID'";
  if (m[2] !== r.video_id) throw 'command names ' + m[2] + ', but this thread is for ' + r.video_id;
  return CMD_MAP[m[1]];
}

function checkReplies_(ss) {
  var approver = prop_('APPROVER_EMAIL').toLowerCase();
  var logRows = table_(ss, 'ApprovalLog').rows;
  var used = {};
  logRows.forEach(function (l) { used[l.message_id] = true; });
  // One request thread can offer several videos: today's new one + re-offered skipped ones.
  var groups = {}, order = [];
  table_(ss, 'VideoQueue').rows.forEach(function (r) {
    if (r.state !== 'AWAITING_APPROVAL' || !r.approval_thread_id) return;
    if (!groups[r.approval_thread_id]) { groups[r.approval_thread_id] = []; order.push(r.approval_thread_id); }
    groups[r.approval_thread_id].push(r);
  });
  order.forEach(function (tid) {
    var rows = groups[tid].sort(function (a, b) { return a.video_id < b.video_id ? 1 : -1; });   // newest first
    var thread = GmailApp.getThreadById(tid);
    if (!thread) return;
    var decided = false;
    thread.getMessages().forEach(function (msg) {
      var id = msg.getId();
      if (decided || used[id] || id === rows[0].approval_request_message_id || !rows.length) return;
      var full = Gmail.Users.Messages.get('me', id, { format: 'metadata', metadataHeaders: ['Authentication-Results'] });
      var authHdr = ((full.payload && full.payload.headers) || []).filter(function (h) {
        return h.name.toLowerCase() === 'authentication-results'; })[0];
      var ev = { message_id: id, thread_id: msg.getThread().getId(), sender: msg.getFrom(), sent_at: msg.getDate(),
                 body: msg.getPlainBody(), label_ids: full.labelIds || [], auth_results: authHdr ? authHdr.value : '' };
      // the reply names the video it decides; default to the thread's own (newest) video
      var lines = newText_(ev.body), named = lines.length === 1 ? (lines[0].match(CMD_RE) || [])[2] : null;
      var r = rows.filter(function (x) { return x.video_id === named; })[0] || rows[0];
      var entry = { logged_at: nowIso_(), video_id: r.video_id, message_id: id, thread_id: ev.thread_id,
                    sender: ev.sender, sent_at: ev.sent_at.toISOString().replace(/\.\d{3}Z$/, '+00:00'),
                    label_ids: ev.label_ids.join(','), auth_results: ev.auth_results.slice(0, 1000),
                    body_excerpt: String(ev.body).slice(0, 4000), command: '', accepted: 'false', reason: '', transition: '' };
      used[id] = true;
      try {
        var decision = validate_(r, ev, approver, used_without_(used, id));
        entry.command = decision + ' ' + r.video_id;
        var target = decision === 'YES' ? 'APPROVED' : 'REJECTED';
        var upd = decision === 'YES'
          ? { state: target, approval_message_id: id, approved_by: approver, approved_at: entry.sent_at, webhook_status: 'pending' }
          : { state: target, approval_message_id: id, notes: 'rejected by approver' };
        if (casUpdate_(ss, r.video_id, 'AWAITING_APPROVAL', r.version, upd)) {
          entry.accepted = 'true'; entry.transition = 'AWAITING_APPROVAL->' + target;
          rows = rows.filter(function (x) { return x.video_id !== r.video_id; });
          if (decision === 'YES') {
            decided = true;                                  // one video per slot: the others are skipped
            props_().setProperty('appr:' + r.video_id, JSON.stringify({ message_id: id, approved_at: entry.sent_at }));
            rows.forEach(function (o) {
              casUpdate_(ss, o.video_id, 'AWAITING_APPROVAL', o.version,
                         { state: 'EXPIRED', notes: 'skipped: ' + r.video_id + ' was chosen for this slot' });
            });
          }
        } else {
          entry.reason = 'state changed concurrently; not applied';
        }
      } catch (reason) {
        entry.reason = String(reason);
        if (parseAddr_(ev.sender) === approver) {
          logError_(ss, r.video_id, 'approval', 'Reply not accepted: ' + entry.reason +
            '. Reply again with exactly "APPROVED ' + r.video_id + '" or "REJECTED ' + r.video_id + '".', 'WARNING');
        }
      }
      append_(ss, 'ApprovalLog', entry);
    });
  });
}

function used_without_(used, id) {
  var copy = {}; Object.keys(used).forEach(function (k) { if (k !== id) copy[k] = true; }); return copy;
}

/* ------------------------------------------------------------------ 3. expiry */
function expire_(ss) {
  table_(ss, 'VideoQueue').rows.forEach(function (r) {
    if (r.state !== 'AWAITING_APPROVAL' || !r.approval_requested_at) return;
    var sn = snap_(r.video_id);
    if (Date.now() > deadline_(r.approval_requested_at, (sn && sn.publish_at) || r.proposed_publish_at)) {
      if (casUpdate_(ss, r.video_id, 'AWAITING_APPROVAL', r.version, { state: 'EXPIRED', notes: 'not approved before the slot' }))
        logError_(ss, r.video_id, 'approval', 'Not approved in time, so this video was skipped. Nothing was uploaded.', 'INFO');
    }
  });
}

/* ------------------------------------------------------------------ 4. publisher call */
function hex_(bytes) { return bytes.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join(''); }

function callPublisher_(ss) {
  var dry = props_().getProperty('DRY_RUN') || String(keysFile_().DRY_RUN || 'true');
  if (dry !== 'false') return;  // never call the publisher in dry run
  var url = props_().getProperty('PUBLISHER_URL') || keysFile_().PUBLISHER_URL;
  if (!url) return;   // publisher not deployed yet: approved items simply wait (no error spam)
  var secret = props_().getProperty('WEBHOOK_SECRET');
  if (!secret || secret.length < 32) throw new Error('Set Script Property WEBHOOK_SECRET (>= 32 chars) before DRY_RUN=false');
  table_(ss, 'VideoQueue').rows.forEach(function (r) {
    if (r.state !== 'APPROVED' || r.webhook_status === 'done') return;
    var body = JSON.stringify({ video_id: r.video_id });
    var ts = String(Math.floor(Date.now() / 1000));
    var nonce = Utilities.getUuid().replace(/-/g, '');
    var sig = hex_(Utilities.computeHmacSha256Signature(ts + '.' + nonce + '.' + body, secret));
    var resp = UrlFetchApp.fetch(url.replace(/\/$/, '') + '/v1/approved', {
      method: 'post', contentType: 'application/json', payload: body, muteHttpExceptions: true,
      headers: { 'X-YTA-Timestamp': ts, 'X-YTA-Nonce': nonce, 'X-YTA-Signature': sig } });
    var code = resp.getResponseCode();
    var status = code === 200 ? 'done' : 'error ' + code;
    var result = {}; try { result = JSON.parse(resp.getContentText()); } catch (e) {}
    if (code === 200 && (result.status === 'scheduled' || result.status === 'uploaded_private')) {
      archiveVideo_(ss, r);
    }
    var fresh = table_(ss, 'VideoQueue').rows.filter(function (x) { return x.video_id === r.video_id; })[0];
    if (fresh && fresh.state === 'APPROVED') casUpdate_(ss, r.video_id, 'APPROVED', fresh.version, { webhook_status: status });
    if (code !== 200) logError_(ss, r.video_id, 'publisher_call', 'Publisher returned ' + code + ': ' + resp.getContentText().slice(0, 300));
  });
}

/* ------------------------------------------------------------------ 4b. upload (default uploader) */
/** Re-read the approval reply straight from Gmail and re-check it against the snapshot. */
function verifyApproval_(vid, sn, ap) {
  var approver = prop_('APPROVER_EMAIL').toLowerCase();
  var msg = GmailApp.getMessageById(ap.message_id);
  if (!msg) throw 'approval message not found';
  if (msg.getThread().getId() !== sn.thread_id) throw 'approval is not in the request thread';
  if (parseAddr_(msg.getFrom()) !== approver) throw 'approval sender mismatch';
  var full = Gmail.Users.Messages.get('me', ap.message_id, { format: 'metadata', metadataHeaders: ['Authentication-Results'] });
  var ah = ((full.payload && full.payload.headers) || []).filter(function (h) { return h.name.toLowerCase() === 'authentication-results'; })[0];
  if (!senderAuthenticated_(full.labelIds || [], ah ? ah.value : '', approver)) throw 'approval sender not authenticated';
  var sent = msg.getDate().getTime();
  if (!(sent > new Date(sn.requested_at).getTime()) || sent > deadline_(sn.requested_at, sn.publish_at)) throw 'approval outside the allowed window';
  var lines = newText_(msg.getPlainBody());
  var m = lines.length === 1 ? lines[0].match(CMD_RE) : null;
  if (!m || CMD_MAP[m[1]] !== 'YES' || m[2] !== vid) throw 'approval text is not an exact APPROVED command for ' + vid;
}

function uploadApproved_(ss) {
  var dry = props_().getProperty('DRY_RUN') || String(keysFile_().DRY_RUN || 'true');
  table_(ss, 'VideoQueue').rows.forEach(function (r) {
    if (r.state === 'UPLOADING' && props_().getProperty('rs:' + r.video_id)) { continueChunked_(ss, r); return; }
    if (r.state !== 'APPROVED') return;
    var vid = r.video_id, sn = snap_(vid), apRaw = props_().getProperty('appr:' + vid);
    if (!sn || !apRaw) { logOnce_(ss, vid, 'nosnap', 'Approved row without this script\'s own approval record; not uploading.'); return; }
    if (props_().getProperty('up:' + vid)) return;                      // never upload twice
    if (dry !== 'false' || props_().getProperty('UPLOAD_ENABLED') !== 'true') {
      logOnce_(ss, vid, 'flag', 'Approved. Waiting: set Script Property UPLOAD_ENABLED=true (and DRY_RUN=false) to upload.');
      return;
    }
    try { verifyApproval_(vid, sn, JSON.parse(apRaw)); } catch (e) {
      logError_(ss, vid, 'uploader', 'Approval re-check failed, not uploading: ' + e); props_().setProperty('up:' + vid, 'rejected'); return;
    }
    var publishAt = null;
    if (props_().getProperty('PUBLICATION_ENABLED') === 'true' && sn.publish_at) {
      if (new Date(sn.publish_at).getTime() - Date.now() < 5 * 60e3) {
        logError_(ss, vid, 'uploader', 'Approved too close to the publish slot; skipped.', 'INFO');
        props_().setProperty('up:' + vid, 'skipped'); return;
      }
      publishAt = new Date(sn.publish_at).toISOString().replace(/\.\d{3}Z$/, 'Z');
    }
    if (!casUpdate_(ss, vid, 'APPROVED', r.version, { state: 'UPLOADING', webhook_status: 'apps_script' })) return;
    props_().setProperty('up:' + vid, 'uploading');                    // lock before touching YouTube
    var cur = table_(ss, 'VideoQueue').rows.filter(function (x) { return x.video_id === vid; })[0];
    try {
      var file = DriveApp.getFileById(sn.drive_file_id);
      var tags = []; try { tags = JSON.parse(sn.tags || '[]'); } catch (e) {}
      var status = { privacyStatus: 'private', selfDeclaredMadeForKids: false, embeddable: true,
                     publicStatsViewable: true, license: 'youtube' };
      if (sn.synthetic || props_().getProperty('SYNTHETIC_MEDIA') === 'true') status.containsSyntheticMedia = true;
      if (publishAt) status.publishAt = publishAt;
      var resource = { snippet: { title: sn.title, description: sn.description, tags: tags,
        categoryId: sn.category_id || '28', defaultLanguage: sn.language || 'en', defaultAudioLanguage: sn.language || 'en' }, status: status,
        // since 2026-11-03 YouTube no longer assumes "no paid placement": declare it explicitly
        paidProductPlacementDetails: { hasPaidProductPlacement: false } };
      if (file.getSize() > MAX_UPLOAD_BYTES) {
        // Big (long-form) file: Drive's own SHA-256 of the stored file must equal the approved one,
        // then a resumable upload in chunks that continues across 5-minute runs if needed.
        var meta = driveMeta_(sn.drive_file_id);
        if (meta.sha256Checksum !== sn.sha256) throw 'checksum mismatch: the file changed after approval';
        props_().setProperty('rs:' + vid, JSON.stringify({ url: '', offset: 0, size: Number(meta.size),
                                                           resource: resource, publish_at: publishAt || '' }));
        continueChunked_(ss, table_(ss, 'VideoQueue').rows.filter(function (x) { return x.video_id === vid; })[0]);
        return;
      }
      var blob = file.getBlob();
      var digest = hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, blob.getBytes()));
      if (digest !== sn.sha256) throw 'checksum mismatch: the file changed after approval';
      var res = YouTube.Videos.insert(resource, 'snippet,status,paidProductPlacementDetails', blob);
      uploaded_(ss, vid, sn, cur, res.id, publishAt);
    } catch (e) {
      casUpdate_(ss, vid, 'UPLOADING', cur.version, { state: 'NEEDS_MANUAL_REVIEW', notes: String(e).slice(0, 400) });
      logError_(ss, vid, 'uploader', 'Upload stopped (needs a manual look; it will not retry automatically): ' + e);
    }
  });
}

/** Success bookkeeping shared by the normal and the chunked upload. */
function uploaded_(ss, vid, sn, cur, ytId, publishAt) {
  props_().setProperty('up:' + vid, JSON.stringify({ youtube_id: ytId, at: nowIso_() }));
  if (sn.thumbnail_file_id) {              // set after processing (Shorts need it); retried each run
    props_().setProperty('thumbq:' + vid, JSON.stringify({ yt: ytId, file: sn.thumbnail_file_id, since: Date.now() }));
  }
  casUpdate_(ss, vid, 'UPLOADING', cur.version, { state: 'SCHEDULED', youtube_video_id: ytId,
                                                 proposed_publish_at: publishAt || '' });
  append_(ss, 'PublishedVideos', { recorded_at: nowIso_(), video_id: vid, youtube_video_id: ytId, privacy: 'private',
    publish_at: publishAt || '', title: sn.title, sha256: sn.sha256, status: publishAt ? 'scheduled' : 'uploaded_private' });
  archiveVideo_(ss, { video_id: vid, drive_file_id: sn.drive_file_id });
  logError_(ss, vid, 'uploader', 'Uploaded ✓ https://youtu.be/' + ytId + (publishAt ? ' — goes public ' + ist_(publishAt)
    : ' — private (PUBLICATION_ENABLED is off)'), 'INFO');
}

function driveMeta_(fileId) {
  var r = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/files/' + encodeURIComponent(fileId) +
    '?fields=size,sha256Checksum&supportsAllDrives=true',
    { headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() }, muteHttpExceptions: true });
  if (r.getResponseCode() !== 200) throw 'Drive metadata failed: HTTP ' + r.getResponseCode();
  return JSON.parse(r.getContentText());
}

/** Resumable YouTube upload in 32 MB chunks read straight from Drive (no 50 MB blob limit).
 *  Progress lives in Script Property rs:<id>; a run stops after ~4 min and the next run continues. */
function continueChunked_(ss, r) {
  var vid = r.video_id, sn = snap_(vid), st = JSON.parse(props_().getProperty('rs:' + vid) || 'null');
  if (!st || !sn) return;
  var token = ScriptApp.getOAuthToken(), started = Date.now();
  try {
    if (st.publish_at && new Date(st.publish_at).getTime() - Date.now() < 5 * 60e3)
      throw 'the publish slot arrived before the upload finished';
    if (!st.url) {
      var init = UrlFetchApp.fetch('https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status,paidProductPlacementDetails', {
        method: 'post', contentType: 'application/json; charset=UTF-8', payload: JSON.stringify(st.resource),
        headers: { Authorization: 'Bearer ' + token, 'X-Upload-Content-Length': String(st.size),
                   'X-Upload-Content-Type': 'video/mp4' }, muteHttpExceptions: true });
      if (init.getResponseCode() !== 200) throw 'resumable start failed: HTTP ' + init.getResponseCode() + ' ' + init.getContentText().slice(0, 200);
      var h = init.getAllHeaders();
      st.url = h.Location || h.location;
      props_().setProperty('rs:' + vid, JSON.stringify(st));
    }
    while (st.offset < st.size) {
      if (Date.now() - started > 4 * 60e3) return;                         // continue in the next run
      var end = Math.min(st.offset + CHUNK_BYTES, st.size) - 1;
      var part = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/files/' + encodeURIComponent(sn.drive_file_id) +
        '?alt=media&supportsAllDrives=true', { headers: { Authorization: 'Bearer ' + token,
        Range: 'bytes=' + st.offset + '-' + end } }).getContent();
      var put = UrlFetchApp.fetch(st.url, { method: 'put', contentType: 'video/mp4', payload: part,
        headers: { Authorization: 'Bearer ' + token, 'Content-Range': 'bytes ' + st.offset + '-' + end + '/' + st.size },
        muteHttpExceptions: true });
      var code = put.getResponseCode();
      if (code === 200 || code === 201) {
        props_().deleteProperty('rs:' + vid);
        var cur = table_(ss, 'VideoQueue').rows.filter(function (x) { return x.video_id === vid; })[0];
        uploaded_(ss, vid, sn, cur, JSON.parse(put.getContentText()).id, st.publish_at);
        return;
      }
      if (code !== 308) throw 'chunk upload failed: HTTP ' + code + ' ' + put.getContentText().slice(0, 200);
      var rg = put.getAllHeaders().Range || put.getAllHeaders().range || '';
      st.offset = rg ? Number(String(rg).split('-')[1]) + 1 : end + 1;
      props_().setProperty('rs:' + vid, JSON.stringify(st));
    }
  } catch (e) {
    props_().deleteProperty('rs:' + vid);
    var row = table_(ss, 'VideoQueue').rows.filter(function (x) { return x.video_id === vid; })[0];
    casUpdate_(ss, vid, 'UPLOADING', row.version, { state: 'NEEDS_MANUAL_REVIEW', notes: String(e).slice(0, 400) });
    logError_(ss, vid, 'uploader', 'Upload stopped (needs a manual look; it will not retry automatically): ' + e);
  }
}

/** Designed thumbnails: YouTube accepts them once processing is done, so try each run for 48 h. */
function setThumbnails_(ss) {
  var all = props_().getProperties();
  Object.keys(all).filter(function (k) { return k.indexOf('thumbq:') === 0; }).forEach(function (k) {
    var vid = k.slice(7), q = JSON.parse(all[k]);
    try {
      YouTube.Thumbnails.set(q.yt, DriveApp.getFileById(q.file).getBlob());
      props_().deleteProperty(k);
      logError_(ss, vid, 'uploader', 'Thumbnail set ✓ https://youtu.be/' + q.yt, 'INFO');
    } catch (e) {
      if (Date.now() - q.since > 48 * 3600e3) {
        props_().deleteProperty(k);
        logError_(ss, vid, 'uploader', 'Could not set the thumbnail (custom thumbnails need a verified channel: ' +
          'youtube.com/verify): ' + e, 'WARNING');
      }
    }
  });
}

function logOnce_(ss, vid, key, msg) {
  var k = 'note:' + key + ':' + vid;
  if (props_().getProperty(k)) return;
  props_().setProperty(k, '1');
  logError_(ss, vid, 'uploader', msg, 'INFO');
}

/** After a confirmed YouTube upload, move the exact uploaded file into the Drive "Archive" folder. */
function archiveVideo_(ss, r) {
  var archiveId = props_().getProperty('ARCHIVE_FOLDER_ID') || keysFile_().ARCHIVE_FOLDER_ID;
  if (!archiveId || !r.drive_file_id) return;
  try {
    DriveApp.getFileById(r.drive_file_id).moveTo(DriveApp.getFolderById(archiveId));
  } catch (e) {
    logError_(ss, r.video_id, 'archive', 'Uploaded, but moving the file to Archive failed: ' + e, 'WARNING');
  }
}

/* ------------------------------------------------------------------ 4b2. archive cleanup */
var ARCHIVE_KEEP_DAYS = 30;
/** Once a day: moves to Drive Trash (restorable for 30 more days) the Archive files of videos
 *  that are PUBLISHED on YouTube for more than ARCHIVE_KEEP_DAYS days. Anything not published
 *  (skipped, rejected, failed, unknown files) is never touched. */
function cleanupArchive_(ss) {
  var day = Utilities.formatDate(new Date(), 'Asia/Kolkata', 'yyyyMMdd');
  if (props_().getProperty('cleanup:' + day)) return;
  props_().setProperty('cleanup:' + day, 'done');
  var archiveId = props_().getProperty('ARCHIVE_FOLDER_ID') || keysFile_().ARCHIVE_FOLDER_ID;
  if (!archiveId) return;
  var cutoff = Date.now() - ARCHIVE_KEEP_DAYS * 864e5, n = 0;
  var byFile = {};
  table_(ss, 'VideoQueue').rows.forEach(function (r) { if (r.drive_file_id) byFile[r.drive_file_id] = r; });
  var files = DriveApp.getFolderById(archiveId).getFiles();
  while (files.hasNext()) {
    var f = files.next(), r = byFile[f.getId()];
    if (!r || r.state !== 'PUBLISHED' || !r.youtube_video_id) continue;
    var published = new Date(r.proposed_publish_at || r.updated_at || '').getTime();
    if (!(published < cutoff)) continue;
    try { f.setTrashed(true); n++; } catch (e) { logError_(ss, r.video_id, 'archive', 'Cleanup failed: ' + e, 'WARNING'); }
  }
  if (n) logError_(ss, '', 'archive', 'Archive cleanup: moved ' + n + ' published video file(s) older than ' +
                   ARCHIVE_KEEP_DAYS + ' days to Drive Trash (restorable for 30 days).', 'INFO');
}

/* ------------------------------------------------------------------ 4c. "no video today" watchdog */
/** Independent of Claude: if by 12:30 IST no video was started today, tell the owner once. The
 *  usual cause is an exhausted Claude usage limit (a blocked session cannot send this itself). */
function noVideoCheck_(ss) {
  var now = new Date();
  var hm = Number(Utilities.formatDate(now, 'Asia/Kolkata', 'HHmm'));
  if (hm < 1230) return;
  var day = Utilities.formatDate(now, 'Asia/Kolkata', 'yyyyMMdd');
  if (props_().getProperty('novideo:' + day)) return;
  var made = table_(ss, 'VideoQueue').rows.some(function (r) { return r.video_id.indexOf('VIDEO-' + day + '-') === 0; });
  props_().setProperty('novideo:' + day, made ? 'ok' : 'alerted');
  if (made) return;
  var weekly = '';
  table_(ss, 'Configuration').rows.forEach(function (c) { if (c.key === 'claude_weekly_resets_at') weekly = c.value; });
  logError_(ss, '', 'watchdog', 'No video was created today. Most likely the Claude usage limit is used up' +
    (weekly ? ' (weekly limit resets ' + ist_(weekly) + ')' : '') + ', so no video can be made or uploaded until it resets. ' +
    'Nothing is needed from you unless this repeats after the reset.', 'ALERT');
}

/* ------------------------------------------------------------------ 5. notifications */
function sendNotifications_(ss) {
  var approver = prop_('APPROVER_EMAIL');
  var t = table_(ss, 'ErrorLog');
  var col = t.hdr.indexOf('notified') + 1;
  var pending = t.rows.filter(function (r) { return !r.notified; });
  if (!pending.length) return;
  var body = pending.map(function (r) {
    return '[' + r.severity + '] ' + (r.video_id || '-') + ' ' + r.component + ': ' + r.message; }).join('\n\n');
  // Separate thread on purpose: notifications are never posted into an approval thread.
  var uploaded = pending.filter(function (r) { return String(r.message).indexOf('Uploaded ✓') === 0; }).length;
  var alerts = pending.filter(function (r) { return r.severity === 'ALERT'; });
  var audits = pending.filter(function (r) { return r.severity === 'AUDIT'; });
  var actions = pending.filter(function (r) { return r.severity === 'ACTION'; });
  var subject = actions.length ? '🔧 Action needed: update your Apps Script'
    : alerts.length ? '⚠️ Claude usage limit reached — no new videos until it resets'
    : uploaded ? '✅ Short uploaded' : audits.length === pending.length ? '📈 Daily views audit'
    : '[YouTube automation] ' + pending.length + ' update(s)';
  GmailApp.sendEmail(approver, subject, body, { htmlBody: notificationHtml_(pending, subject) });
  pending.forEach(function (r) { t.sheet.getRange(r._row, col).setValue(nowIso_()); });
}

var SEVERITY_STYLE = {
  ALERT:   { label: 'Alert',          color: '#b91c1c', bg: '#fef2f2', border: '#fecaca' },
  ACTION:  { label: 'Action needed',  color: '#c2410c', bg: '#fff7ed', border: '#fed7aa' },
  ERROR:   { label: 'Error',          color: '#b91c1c', bg: '#fef2f2', border: '#fecaca' },
  WARNING: { label: 'Warning',        color: '#a16207', bg: '#fefce8', border: '#fde68a' },
  AUDIT:   { label: 'Views audit',    color: '#1d4ed8', bg: '#eff6ff', border: '#bfdbfe' },
  INFO:    { label: 'Update',         color: '#15803d', bg: '#f0fdf4', border: '#bbf7d0' }
};

/** Linkify URLs and keep line breaks; everything else is escaped. */
function richText_(s) {
  return esc_(s).replace(/(https:\/\/[^\s<]+)/g, '<a href="$1" style="color:#2563eb">$1</a>').replace(/\n/g, '<br>');
}

/** Neat HTML version of the notification email: one coloured card per update. */
function notificationHtml_(rows, subject) {
  var cards = rows.map(function (r) {
    var st = SEVERITY_STYLE[r.severity] || SEVERITY_STYLE.INFO;
    var yt = String(r.message).match(/https:\/\/youtu\.be\/[\w-]+/);
    return '<div style="background:' + st.bg + ';border:1px solid ' + st.border + ';border-radius:12px;padding:14px 16px;margin:0 0 12px">' +
      '<div style="font-size:12px;font-weight:bold;letter-spacing:1px;color:' + st.color + ';text-transform:uppercase">' +
      esc_(st.label) + (r.video_id ? ' · ' + esc_(r.video_id) : '') + '</div>' +
      '<div style="font-size:14px;line-height:1.5;margin-top:6px;color:#1b1f2a">' + richText_(r.message) + '</div>' +
      (yt ? '<p style="margin:12px 0 0"><a href="' + yt[0] + '" style="background:#dc2626;color:#fff;padding:10px 18px;' +
            'border-radius:10px;text-decoration:none;font-weight:bold;display:inline-block">▶ Watch on YouTube</a></p>' : '') +
      '<div style="font-size:11px;color:#8a90a6;margin-top:8px">' + ist_(r.logged_at) + ' · ' + esc_(r.component) + '</div></div>';
  }).join('');
  return '<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1b1f2a">' +
    '<div style="background:#0f1530;color:#fff;padding:18px 22px;border-radius:14px 14px 0 0">' +
    '<div style="font-size:12px;letter-spacing:2px;color:#9fb0ff">YOUTUBE SHORTS AUTOMATION</div>' +
    '<div style="font-size:20px;font-weight:bold;margin-top:6px">' + esc_(subject) + '</div></div>' +
    '<div style="border:1px solid #e3e6f0;border-top:0;border-radius:0 0 14px 14px;padding:18px 22px">' + cards +
    '<p style="font-size:11px;color:#99a;margin:6px 0 0">Sent by your approval Apps Script (version ' + SCRIPT_VERSION + ').</p>' +
    '</div></div>';
}
