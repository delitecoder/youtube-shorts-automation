"""Operational notification templates: subject + plain text + Gmail-safe HTML (inline styles, table layout).

Delivery: `notify()` queues the plain text in the ErrorLog tab (the owner's Apps Script digest emails it, as
before); the HTML is returned for senders that support it. Approval emails stay in the Apps Script (they
carry the authenticated approval flow). Values are escaped; no secrets are ever included.
"""
from __future__ import annotations

import html

from studio.core.state import iso, utcnow

EVENTS = {
    "production_completed": ("✅ Ready for approval: {video_id}", "INFO",
                             "The video passed every QC gate and the approval email is on its way.\n{details}"),
    "approval_rejected": ("❌ Rejected: {video_id}", "INFO", "The approver rejected the video. Notes:\n{details}"),
    "upload_completed": ("⬆️ Uploaded: {video_id}", "INFO", "Uploaded privately and scheduled.\n{details}"),
    "publication_completed": ("🎉 Published: {video_id}", "INFO", "The video is public.\n{details}"),
    "render_failed": ("⚠️ Render failed: {video_id}", "ERROR", "The render did not pass video QC.\n{details}"),
    "audio_qc_failed": ("🔈 Audio QC failed: {video_id}", "ERROR", "Audio QC blocked approval.\n{details}"),
    "research_failed": ("🔎 Research rejected: {video_id}", "WARNING", "The research did not pass validation.\n{details}"),
    "budget_warning": ("💳 Budget warning", "WARNING", "Usage is close to a limit.\n{details}"),
    "workflow_paused": ("⏸️ Workflow paused: {video_id}", "WARNING", "The run stopped and saved a checkpoint.\n{details}"),
    "workflow_resumed": ("▶️ Workflow resumed: {video_id}", "INFO", "Resumed from the durable checkpoint.\n{details}"),
    "weekly_analytics": ("📈 Weekly analytics", "INFO", "{details}"),
    "experiment_results": ("🧪 Experiment result: {exp_id}", "INFO", "{details}"),
}


def _details(data: dict) -> str:
    items = data.get("items") or {}
    lines = [f"- {k}: {v}" for k, v in items.items()]
    if data.get("text"):
        lines.insert(0, str(data["text"]))
    return "\n".join(lines)


def render(event: str, **data) -> dict:
    if event not in EVENTS:
        raise KeyError(event)
    subj_t, severity, body_t = EVENTS[event]
    fields = {"video_id": data.get("video_id", ""), "exp_id": data.get("exp_id", ""), "details": _details(data)}
    subject = subj_t.format(**fields)
    text = body_t.format(**fields).strip()
    rows = "".join(f'<tr><td style="padding:4px 10px 4px 0;color:#667;font-size:13px">{html.escape(str(k))}</td>'
                   f'<td style="padding:4px 0;font-size:13px">{html.escape(str(v))}</td></tr>'
                   for k, v in (data.get("items") or {}).items())
    intro = html.escape(body_t.split("\n")[0].format(**fields))
    extra = f'<p style="margin:8px 0;font-size:14px">{html.escape(str(data["text"]))}</p>' if data.get("text") else ""
    body = (f'<div style="font-family:Arial,Helvetica,sans-serif;background:#f4f5f9;padding:16px">'
            f'<table role="presentation" width="100%" style="max-width:560px;margin:auto;background:#fff;border-radius:10px">'
            f'<tr><td style="padding:18px 20px"><h2 style="margin:0 0 8px;font-size:18px;color:#111">{html.escape(subject)}</h2>'
            f'<p style="margin:0 0 8px;font-size:14px;color:#333">{intro}</p>{extra}'
            f'<table role="presentation">{rows}</table>'
            f'<p style="margin:14px 0 0;color:#99a;font-size:11px">DeliteCoder studio · automated notice</p>'
            f'</td></tr></table></div>')
    return {"subject": subject, "text": text, "html": body, "severity": severity}


def notify(store, event: str, video_id: str = "", **data) -> dict:
    msg = render(event, video_id=video_id, **data)
    store.append("ErrorLog", {"logged_at": iso(utcnow()), "video_id": video_id, "component": f"notify:{event}",
                              "severity": msg["severity"], "message": f"{msg['subject']}\n{msg['text']}"[:4000],
                              "notified": ""})
    return msg
