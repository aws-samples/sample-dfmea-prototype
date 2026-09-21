"""
lambdas/notifier/handler.py

Subscribes to the DFMEA HITL SNS topic.  Receives raw gate notification
JSON published by Step Functions and sends a formatted HTML email via SES.
"""
from __future__ import annotations

import json
import os

import boto3

SENDER     = os.environ.get("SES_SENDER",    "mahasrid@amazon.com")
RECIPIENT  = os.environ.get("SES_RECIPIENT", "mahasrid@amazon.com")
PORTAL_URL = os.environ.get("PORTAL_URL",    "https://d3jlrhdhaj2p2z.cloudfront.net")

_GATE_COLOR = {
    1: "#1a56db",
    2: "#0e9f6e",
    3: "#7e3af2",
    4: "#d61f69",
}
_GATE_LABEL = {
    1: "Gate 1 — Intake Validation",
    2: "Gate 2 — CAD Verification",
    3: "Gate 3 — Agent Analysis Review",
    4: "Gate 4 — Final Human Approval",
}
_GATE_ICON = {1: "&#x1F4CB;", 2: "&#x1F50D;", 3: "&#x1F916;", 4: "&#x2705;"}

_AGENT_ROWS = [
    ("Failure-mode", "AIAG-VDA knowledge base scan"),
    ("Structural",   "BOM and interface matrix completeness"),
    ("Regulatory",   "FMVSS 214 / 216 / ISO 26262 citations"),
    ("Schema",       "DFMEA field completeness and S/O/D ratings"),
]


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _detail_row(label: str, value: str, bg: str = "#f9fafb") -> str:
    return (
        f'<tr style="background:{bg};">'
        f'<td style="padding:9px 14px;font-weight:600;color:#374151;'
        f'border-bottom:1px solid #e5e7eb;white-space:nowrap;width:140px;">{label}</td>'
        f'<td style="padding:9px 14px;color:#111827;border-bottom:1px solid #e5e7eb;">{value}</td>'
        f'</tr>'
    )


def _agent_table() -> str:
    rows = ""
    for i, (agent, desc) in enumerate(_AGENT_ROWS):
        bg = "#f9fafb" if i % 2 == 0 else "#ffffff"
        rows += (
            f'<tr style="background:{bg};">'
            f'<td style="padding:8px 14px;font-weight:600;color:#374151;'
            f'border-bottom:1px solid #e5e7eb;">{agent}</td>'
            f'<td style="padding:8px 14px;color:#6b7280;font-size:13px;'
            f'border-bottom:1px solid #e5e7eb;">{desc}</td>'
            f'<td style="padding:8px 14px;text-align:center;border-bottom:1px solid #e5e7eb;">'
            f'<span style="background:#d1fae5;color:#065f46;padding:3px 10px;'
            f'border-radius:9999px;font-size:12px;font-weight:600;">Complete</span></td>'
            f'</tr>'
        )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;border:1px solid #e5e7eb;'
        'border-radius:6px;overflow:hidden;margin-top:12px;">'
        '<thead><tr style="background:#f3f4f6;">'
        '<th style="padding:8px 14px;text-align:left;font-size:11px;color:#6b7280;'
        'text-transform:uppercase;letter-spacing:.06em;">Agent</th>'
        '<th style="padding:8px 14px;text-align:left;font-size:11px;color:#6b7280;'
        'text-transform:uppercase;letter-spacing:.06em;">Analysis</th>'
        '<th style="padding:8px 14px;text-align:center;font-size:11px;color:#6b7280;'
        'text-transform:uppercase;letter-spacing:.06em;">Status</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )


def _pipeline_table(active_gate: int) -> str:
    rows = ""
    for g in range(1, 5):
        if g < active_gate:
            badge = ('<span style="background:#d1fae5;color:#065f46;padding:3px 10px;'
                     'border-radius:9999px;font-size:12px;font-weight:600;">Approved</span>')
            bg = "#ffffff"
        elif g == active_gate:
            badge = ('<span style="background:#fef3c7;color:#92400e;padding:3px 10px;'
                     'border-radius:9999px;font-size:12px;font-weight:600;">&#x23F3; Pending</span>')
            bg = "#fffbeb"
        else:
            badge = ('<span style="background:#f3f4f6;color:#9ca3af;padding:3px 10px;'
                     'border-radius:9999px;font-size:12px;font-weight:600;">Not started</span>')
            bg = "#f9fafb"
        rows += (
            f'<tr style="background:{bg};">'
            f'<td style="padding:9px 14px;font-weight:{"700" if g == active_gate else "400"};'
            f'color:{"#111827" if g == active_gate else "#6b7280"};'
            f'border-bottom:1px solid #e5e7eb;">'
            f'{_GATE_ICON[g]}&nbsp; {_GATE_LABEL[g]}</td>'
            f'<td style="padding:9px 14px;text-align:right;border-bottom:1px solid #e5e7eb;">'
            f'{badge}</td>'
            f'</tr>'
        )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;border:1px solid #e5e7eb;'
        'border-radius:6px;overflow:hidden;margin-top:10px;">'
        f'<tbody>{rows}</tbody></table>'
    )


def _gate_detail(gate: int) -> str:
    if gate == 1:
        return (
            '<p style="margin:16px 0 4px;color:#6b7280;font-size:13px;line-height:1.6;">'
            'The intake Lambda has parsed and normalised the uploaded DFMEA file. '
            'Please review the submission details and approve to proceed to CAD extraction.</p>'
        )
    if gate == 2:
        return (
            '<p style="margin:16px 0 4px;color:#6b7280;font-size:13px;line-height:1.6;">'
            'The CAD extraction Lambda has analysed the assembly hierarchy and performed a BOM '
            'cross-check. Please verify the structural connections and approve to dispatch '
            'the parallel AI analysis agents.</p>'
        )
    if gate == 3:
        return (
            '<p style="margin:16px 0 4px;font-weight:600;color:#374151;">AI Agent Results</p>'
            + _agent_table()
        )
    # gate == 4
    return (
        '<p style="margin:16px 0 4px;font-weight:600;color:#374151;">AI Agent Results</p>'
        + _agent_table()
        + '<p style="margin:14px 0 0;color:#6b7280;font-size:13px;line-height:1.6;">'
        'The synthesis agent has consolidated all findings, deduplicated overlaps, and computed '
        'AIAG-VDA 2019 Action Priority ratings (S &times; O &times; D). '
        'Approving this gate will trigger final PDF report generation.</p>'
    )


def _build_html(gate: int, review_id: str, stage: str) -> str:
    color      = _GATE_COLOR.get(gate, "#1a56db")
    gate_label = _GATE_LABEL.get(gate, f"Gate {gate}")
    gate_icon  = _GATE_ICON.get(gate, "&#x1F514;")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background:#f3f4f6;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0"
  style="background:#f3f4f6;padding:32px 16px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
  style="background:#ffffff;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.12);
  overflow:hidden;max-width:600px;">

  <!-- Header bar -->
  <tr>
    <td style="background:{color};padding:26px 32px;">
      <p style="margin:0;color:rgba(255,255,255,.7);font-size:11px;
        text-transform:uppercase;letter-spacing:.12em;">DFMEA Review Portal</p>
      <h1 style="margin:6px 0 0;color:#ffffff;font-size:21px;font-weight:700;
        line-height:1.3;">{gate_icon}&nbsp; {gate_label}</h1>
    </td>
  </tr>

  <!-- Body -->
  <tr>
    <td style="padding:28px 32px;">

      <p style="margin:0 0 20px;color:#374151;font-size:15px;line-height:1.65;">
        A DFMEA review requires your action. Please open the portal, review the
        details below, and approve or reject this gate.
      </p>

      <!-- Review detail table -->
      <table width="100%" cellpadding="0" cellspacing="0"
        style="border-collapse:collapse;border:1px solid #e5e7eb;
        border-radius:6px;overflow:hidden;">
        <tbody>
          {_detail_row("Review ID",
            f'<code style="font-size:12px;color:#6b7280;">{review_id}</code>',
            "#ffffff")}
          {_detail_row("Gate", f'{gate_icon}&nbsp; {gate_label}', "#f9fafb")}
          {_detail_row("Stage",
            f'<code style="font-size:12px;color:#6b7280;">{stage}</code>',
            "#ffffff")}
          {_detail_row("Status",
            '<span style="background:#fef3c7;color:#92400e;padding:3px 10px;'
            'border-radius:9999px;font-size:12px;font-weight:600;">'
            '&#x23F3; Awaiting Approval</span>',
            "#f9fafb")}
        </tbody>
      </table>

      <!-- Gate-specific detail / agent table -->
      {_gate_detail(gate)}

      <!-- Pipeline progress -->
      <p style="margin:24px 0 6px;font-weight:600;color:#374151;font-size:14px;">
        Pipeline Progress
      </p>
      {_pipeline_table(gate)}

      <!-- CTA -->
      <div style="text-align:center;margin-top:30px;">
        <a href="{PORTAL_URL}"
          style="display:inline-block;background:{color};color:#ffffff;
          text-decoration:none;padding:13px 34px;border-radius:6px;
          font-weight:600;font-size:15px;letter-spacing:.01em;">
          Open Review Portal &rarr;
        </a>
      </div>

    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="background:#f9fafb;padding:14px 32px;border-top:1px solid #e5e7eb;">
      <p style="margin:0;color:#9ca3af;font-size:12px;text-align:center;">
        DFMEA Review System &middot; Automated notification &middot; Do not reply
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _build_text(gate: int, review_id: str, stage: str) -> str:
    gate_label = _GATE_LABEL.get(gate, f"Gate {gate}")
    lines = [
        f"DFMEA Review Portal — {gate_label}",
        "=" * 52,
        "",
        f"  Review ID : {review_id}",
        f"  Gate      : {gate_label}",
        f"  Stage     : {stage}",
        f"  Status    : Awaiting Approval",
        "",
    ]
    if gate in (3, 4):
        lines += [
            "AI Agent Results",
            "-" * 52,
            f"  {'Agent':<18} {'Analysis':<30} Status",
            "  " + "-" * 48,
        ]
        for agent, desc in _AGENT_ROWS:
            lines.append(f"  {agent:<18} {desc:<30} Complete")
        lines.append("")

    lines += ["Pipeline Progress", "-" * 52]
    for g in range(1, 5):
        lbl = _GATE_LABEL[g]
        if g < gate:
            status = "Approved"
        elif g == gate:
            status = "PENDING  <<<"
        else:
            status = "Not started"
        lines.append(f"  Gate {g}: {lbl:<38} {status}")

    lines += [
        "",
        f"Open portal: {PORTAL_URL}",
        "",
        "DFMEA Review System · Automated notification",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event: dict, _context) -> dict:
    ses = boto3.client("ses", region_name="us-east-1")

    for record in event.get("Records", []):
        raw_message = record["Sns"]["Message"]
        try:
            msg = json.loads(raw_message)
        except (json.JSONDecodeError, TypeError):
            print(f"[notifier] non-JSON SNS message, skipping: {raw_message[:120]}")
            continue

        review_id = msg.get("review_id", "unknown")
        gate      = int(msg.get("gate", 0))
        stage     = msg.get("stage", "UNKNOWN")

        if gate == 0:
            print(f"[notifier] no gate field in message — skipping: {msg}")
            continue

        gate_label = _GATE_LABEL.get(gate, f"Gate {gate}")
        subject    = (
            f"Action Required: DFMEA {gate_label} — "
            f"{review_id[:8].upper()}"
        )

        try:
            ses.send_email(
                Source=SENDER,
                Destination={"ToAddresses": [RECIPIENT]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": _build_html(gate, review_id, stage), "Charset": "UTF-8"},
                        "Text": {"Data": _build_text(gate, review_id, stage), "Charset": "UTF-8"},
                    },
                },
            )
            print(f"[notifier] HTML email sent: review_id={review_id} gate={gate}")
        except Exception as exc:
            print(f"[notifier] SES send_email failed: {exc}")

    return {"statusCode": 200}
