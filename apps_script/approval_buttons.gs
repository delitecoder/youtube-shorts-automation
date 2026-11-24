/**
 * One-click Approve / Reject buttons for the daily Short approval email.
 *
 * This is a SEPARATE, tiny Apps Script project owned by the APPROVER's Google account
 * (not the approval_watcher project). Clicking a button in the approval email opens this web
 * app; after one confirm tap it sends the normal one-line reply ("APPROVED VIDEO-…" or
 * "REJECTED VIDEO-…") from the approver's own Gmail, as a real reply in the request thread.
 * The watcher then checks that reply exactly like a hand-typed one (approver address, Gmail
 * DKIM/DMARC or SENT label, deadline, exact command), so no security check is bypassed:
 * the button only saves typing. It can only send from the account that deployed it.
 *
 * Setup (once, signed in as the APPROVER):
 *  1. script.google.com → New project → name it "Short approval buttons" → paste this file.
 *  2. Deploy → New deployment → type "Web app" → Execute as: Me · Who has access: Only myself
 *     → Deploy → authorise (Gmail) → copy the Web app URL (ends in /exec).
 *  3. In the approval_watcher project: Project Settings → Script Properties → add
 *     APPROVAL_BUTTONS_URL = that URL. New approval emails then show the two buttons.
 */
var BUTTONS_VERSION = '2026.11.24-2';
var VID_RE = /^VIDEO-\d{8}-\d{3}$/;
var ACTIONS = { approve: 'APPROVED', reject: 'REJECTED' };

function doGet(e) {
  var p = (e && e.parameter) || {};
  var cmd = ACTIONS[p.a], vid = String(p.v || '');
  if (!cmd || !VID_RE.test(vid)) return page_('Invalid link', 'This link is not a valid approval button.', '');
  var t = HtmlService.createTemplate(
    '<p>Send <b><?= cmd ?> <?= vid ?></b> as your reply to the approval email?</p>' +
    '<button id="b" onclick="go()" style="font-size:18px;padding:14px 26px;border:0;border-radius:10px;color:#fff;' +
    'background:<?= cmd === "APPROVED" ? "#16a34a" : "#dc2626" ?>">' +
    '<?= cmd === "APPROVED" ? "✔ Approve" : "✖ Reject" ?></button>' +
    '<p id="m" style="margin-top:16px"></p>' +
    '<script>function go(){var b=document.getElementById("b");b.disabled=true;b.textContent="Sending…";' +
    'google.script.run.withSuccessHandler(function(r){document.getElementById("m").textContent=r;b.style.display="none";})' +
    '.withFailureHandler(function(err){document.getElementById("m").textContent="Not sent: "+err.message;b.disabled=false;})' +
    '.sendDecision(<?!= JSON.stringify(p.a) ?>,<?!= JSON.stringify(vid) ?>);}</script>');
  t.cmd = cmd; t.vid = vid; t.p = { a: p.a };
  return page_('Approval: ' + vid, '', t.evaluate().getContent());
}

/** Replies to the approval request for `vid` from this (the approver's) mailbox. */
function sendDecision(action, vid) {
  var cmd = ACTIONS[action];
  if (!cmd || !VID_RE.test(String(vid))) throw new Error('invalid request');
  var request = findRequest_(vid);
  if (!request) throw new Error('approval email for ' + vid + ' not found in this mailbox (last 3 days)');
  request.reply(cmd + ' ' + vid);
  return 'Sent "' + cmd + ' ' + vid + '". The video updates within 5 minutes; you can close this page.';
}

function findRequest_(vid) {
  var threads = GmailApp.search('subject:"' + vid + '" newer_than:3d', 0, 10);
  var best = null;
  threads.forEach(function (th) {
    th.getMessages().forEach(function (m) {
      var subj = m.getSubject();
      if (subj.indexOf(vid) < 0 || subj.indexOf('Approve today') < 0 || /^re:/i.test(subj)) return;
      if (!best || m.getDate() > best.getDate()) best = m;          // newest request wins
    });
  });
  return best;
}

function page_(title, text, html) {
  return HtmlService.createHtmlOutput(
    '<div style="font-family:Arial,sans-serif;max-width:480px;margin:40px auto;padding:0 16px;text-align:center">' +
    '<h2 style="font-size:20px">' + title.replace(/[<>&"]/g, '') + '</h2>' +
    (text ? '<p>' + text + '</p>' : '') + html + '</div>')
    .setTitle('Short approval')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}
