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
 * Advanced service required: Gmail API (identifier "Gmail") – used only to read labelIds and the
 * Authentication-Results header of reply messages.
 *   KEYS_FILE_ID (optional): Drive id of YouTubeAutomation/00_Config/keys.json; then SPREADSHEET_ID,
 *   APPROVER_EMAIL, PUBLISHER_URL, ARCHIVE_FOLDER_ID and DRY_RUN can live in that file instead.
 * Setup: run setup() once, authorise, done.
 */

var TTL_HOURS = 24;
var CMD_RE = /^(YES|NO) (VIDEO-\d{8}-\d{3})$/;
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
    sendRequests_(ss);
    checkReplies_(ss);
    expire_(ss);
    callPublisher_(ss);
    sendNotifications_(ss);
  } catch (e) {
    try { logError_(SpreadsheetApp.openById(prop_('SPREADSHEET_ID')), '', 'apps_script', String(e)); } catch (ignored) {}
  } finally {
    lock.releaseLock();
  }
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
function sendRequests_(ss) {
  var approver = prop_('APPROVER_EMAIL');
  table_(ss, 'VideoQueue').rows.forEach(function (r) {
    if (r.state !== 'AWAITING_APPROVAL' || r.approval_thread_id) return;
    var qr = {}; try { qr = JSON.parse(r.quality_report || '{}'); } catch (e) {}
    var refs = []; try { refs = JSON.parse(r.references || '[]'); } catch (e) {}
    var subject = '[Approval needed] ' + r.video_id + ' - ' + r.title;
    var lines = [
      'Video ID: ' + r.video_id,
      'Title: ' + r.title,
      'Source type: ' + r.source_type,
      'Duration: ' + r.duration_s + ' s (' + r.width + 'x' + r.height + ')',
      'Proposed publication: ' + r.proposed_publish_at + ' (UTC)',
      'Private Drive preview (only you can open it): ' + r.preview_url,
      'SHA-256: ' + r.sha256,
      '', 'Opening hook:', r.hook,
      '', 'Description:', r.description,
      '', 'Tags: ' + r.tags,
      'Suggested pinned comment: ' + r.pinned_comment,
      '', 'Quality report:', JSON.stringify(qr, null, 1).slice(0, 3000),
      '', 'References:'
    ].concat(refs.map(function (x) { return '- ' + (x.title || '') + ' ' + (x.url || ''); })).concat(
      imageLines_(qr)).concat([
      '', '----------------------------------------',
      'Reply to THIS email with exactly one line:',
      '   YES ' + r.video_id + '   to approve upload (private, scheduled)',
      '   NO ' + r.video_id + '    to reject',
      'Anything else is ignored. This request expires in ' + TTL_HOURS + ' hours.'
    ]);
    var draft = GmailApp.createDraft(approver, subject, lines.join('\n'));
    var msg = draft.send();
    var sentAt = msg.getDate();
    var ok = casUpdate_(ss, r.video_id, 'AWAITING_APPROVAL', r.version, {
      approval_thread_id: msg.getThread().getId(),
      approval_request_message_id: msg.getId(),
      approval_requested_at: sentAt.toISOString().replace(/\.\d{3}Z$/, '+00:00'),
      approval_expires_at: new Date(sentAt.getTime() + TTL_HOURS * 3600e3).toISOString().replace(/\.\d{3}Z$/, '+00:00')
    });
    if (!ok) logError_(ss, r.video_id, 'apps_script', 'request sent but row changed concurrently; check manually');
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
  if (ev.sent_at.getTime() > requested.getTime() + TTL_HOURS * 3600e3) throw 'reply arrived after the approval expired';
  if (ev.sent_at.getTime() > Date.now() + 5 * 60e3) throw 'reply timestamp is in the future';
  var lines = newText_(ev.body);
  if (lines.length === 0) throw 'empty reply (command may only be inside quoted text)';
  if (lines.length > 1) throw 'ambiguous reply: more than one line of new text';
  var m = lines[0].match(CMD_RE);
  if (!m) throw "malformed command; expected exactly 'YES VIDEO-ID' or 'NO VIDEO-ID'";
  if (m[2] !== r.video_id) throw 'command names ' + m[2] + ', but this thread is for ' + r.video_id;
  return m[1];
}

function checkReplies_(ss) {
  var approver = prop_('APPROVER_EMAIL').toLowerCase();
  var logRows = table_(ss, 'ApprovalLog').rows;
  var used = {};
  logRows.forEach(function (l) { used[l.message_id] = true; });
  table_(ss, 'VideoQueue').rows.forEach(function (r) {
    if (r.state !== 'AWAITING_APPROVAL' || !r.approval_thread_id) return;
    var thread = GmailApp.getThreadById(r.approval_thread_id);
    if (!thread) return;
    var decided = false;
    thread.getMessages().forEach(function (msg) {
      var id = msg.getId();
      if (decided || used[id] || id === r.approval_request_message_id) return;
      var full = Gmail.Users.Messages.get('me', id, { format: 'metadata', metadataHeaders: ['Authentication-Results'] });
      var authHdr = ((full.payload && full.payload.headers) || []).filter(function (h) {
        return h.name.toLowerCase() === 'authentication-results'; })[0];
      var ev = { message_id: id, thread_id: msg.getThread().getId(), sender: msg.getFrom(), sent_at: msg.getDate(),
                 body: msg.getPlainBody(), label_ids: full.labelIds || [], auth_results: authHdr ? authHdr.value : '' };
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
          entry.accepted = 'true'; entry.transition = 'AWAITING_APPROVAL->' + target; decided = true;
        } else {
          entry.reason = 'state changed concurrently; not applied';
        }
      } catch (reason) {
        entry.reason = String(reason);
        if (parseAddr_(ev.sender) === approver) {
          logError_(ss, r.video_id, 'approval', 'Reply not accepted: ' + entry.reason +
            '. Reply again with exactly "YES ' + r.video_id + '" or "NO ' + r.video_id + '".', 'WARNING');
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
    if (Date.now() > new Date(r.approval_requested_at).getTime() + TTL_HOURS * 3600e3) {
      if (casUpdate_(ss, r.video_id, 'AWAITING_APPROVAL', r.version, { state: 'EXPIRED', notes: 'no valid reply within 24h' }))
        logError_(ss, r.video_id, 'approval', 'Approval request expired without a valid reply. Nothing was uploaded.', 'INFO');
    }
  });
}

/* ------------------------------------------------------------------ 4. publisher call */
function hex_(bytes) { return bytes.map(function (b) { return ('0' + (b & 0xff).toString(16)).slice(-2); }).join(''); }

function callPublisher_(ss) {
  var dry = props_().getProperty('DRY_RUN') || String(keysFile_().DRY_RUN || 'true');
  if (dry !== 'false') return;  // never call the publisher in dry run
  var url = prop_('PUBLISHER_URL'), secret = props_().getProperty('WEBHOOK_SECRET');
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

/** After a confirmed YouTube upload, move the exact uploaded file into the Drive "Archive" folder. */
function archiveVideo_(ss, r) {
  var archiveId = props_().getProperty('ARCHIVE_FOLDER_ID');
  if (!archiveId || !r.drive_file_id) return;
  try {
    DriveApp.getFileById(r.drive_file_id).moveTo(DriveApp.getFolderById(archiveId));
  } catch (e) {
    logError_(ss, r.video_id, 'archive', 'Uploaded, but moving the file to Archive failed: ' + e, 'WARNING');
  }
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
  GmailApp.sendEmail(approver, '[YouTube automation] ' + pending.length + ' notification(s)', body);
  pending.forEach(function (r) { t.sheet.getRange(r._row, col).setValue(nowIso_()); });
}
