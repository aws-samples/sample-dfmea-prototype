"""
agents/analyst/report.py — Deterministic DFMEA PDF report builder (S6).

This is pure, deterministic (non-LLM) logic that runs in the analyst thin
invoker Lambda at stage S6_REPORT. It reads findings from DynamoDB, builds a
professional multi-page PDF with no external libraries, uploads it to the
reports S3 bucket, and marks the review COMPLETE.
"""
from __future__ import annotations
import datetime
import os

import boto3
from boto3.dynamodb.conditions import Key


def _esc(s: str) -> str:
    """Escape special characters for PDF string literals."""
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("\n", " ")


# ── Professional PDF report builder ──────────────────────────────────────────

_PW, _PH  = 612.0, 792.0
_ML       = 50.0
_CW       = _PW - 2 * _ML           # 512 usable width

_NAVY     = (0.13, 0.28, 0.52)
_WHITE    = (1.0,  1.0,  1.0)
_LGRAY    = (0.94, 0.94, 0.94)
_MGRAY    = (0.75, 0.75, 0.75)
_BLACK    = (0.0,  0.0,  0.0)

_AP_DARK  = {"H": (0.80, 0.10, 0.05), "M": (0.68, 0.38, 0.00), "L": (0.06, 0.46, 0.12)}
_AP_LIGHT = {"H": (1.00, 0.90, 0.89), "M": (1.00, 0.96, 0.82), "L": (0.89, 0.97, 0.89)}
_AP_LABEL = {"H": "HIGH", "M": "MEDIUM", "L": "LOW"}

_FCOLS = [
    ("AP",          0,   30,  2),
    ("Agent",      30,   60,  8),
    ("Type",       90,   80, 13),
    ("Component", 170,  108, 17),
    ("S",         278,   22,  2),
    ("O",         300,   22,  2),
    ("D",         322,   22,  2),
    ("Description", 344, 168, 25),
]


def _c(rgb) -> str:
    return f"{rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f}"


def _clip2(s: str, n: int) -> str:
    s = str(s).replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 2] + ".."


def _ops_txt(x: float, y: float, text: str, font: str = "F1", size: float = 9.0,
             rgb=(0.0, 0.0, 0.0)) -> str:
    return (f"{_c(rgb)} rg BT /{font} {size:.1f} Tf {x:.1f} {y:.1f} Td "
            f"({_esc(str(text))}) Tj ET")


def _ops_frect(x: float, y: float, w: float, h: float, rgb) -> str:
    return f"{_c(rgb)} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f"


def _ops_brect(x: float, y: float, w: float, h: float, fill, stroke,
               lw: float = 0.5) -> str:
    return (f"{_c(fill)} rg {_c(stroke)} RG {lw:.2f} w "
            f"{x:.1f} {y:.1f} {w:.1f} {h:.1f} re B")


def _ops_srect(x: float, y: float, w: float, h: float, stroke,
               lw: float = 0.5) -> str:
    return f"{_c(stroke)} RG {lw:.2f} w {x:.1f} {y:.1f} {w:.1f} {h:.1f} re S"


def _ops_hline(x: float, y: float, w: float, rgb, lw: float = 0.5) -> str:
    return f"{_c(rgb)} RG {lw:.2f} w {x:.1f} {y:.1f} m {x + w:.1f} {y:.1f} l S"


def _ops_vline(x: float, y1: float, y2: float, rgb, lw: float = 0.3) -> str:
    return f"{_c(rgb)} RG {lw:.2f} w {x:.1f} {y1:.1f} m {x:.1f} {y2:.1f} l S"


def _pdf_stream(ops: list) -> bytes:
    data = "\n".join(ops).encode("latin-1", errors="replace")
    return b"<</Length " + str(len(data)).encode() + b">>\nstream\n" + data + b"\nendstream"


def _footer_ops(page_num: int, total_pages: int) -> list:
    txt = f"Page {page_num} of {total_pages}   |   DFMEA Agentic Review System  -  AIAG-VDA 2019"
    return [
        _ops_hline(_ML, 30.0, _CW, _MGRAY, 0.3),
        _ops_txt(_ML, 18.0, txt, font="F1", size=7.0, rgb=(0.5, 0.5, 0.5)),
    ]


def _cover_stream(review: dict, ap_counts: dict, total: int,
                  page_num: int, total_pages: int) -> bytes:
    import datetime as _dt
    assembly  = review.get("assembly_name", "Unknown Assembly")
    review_id = review.get("review_id", "")
    created   = str(review.get("created_at", ""))[:19]
    status    = str(review.get("status", "COMPLETE"))
    generated = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    ops: list[str] = []
    ops.append(_ops_frect(0, _PH - 72, _PW, 72.0, _NAVY))
    ops.append(_ops_txt(_ML, _PH - 32, "DFMEA FINAL ANALYSIS REPORT",
                        font="F2", size=18.0, rgb=_WHITE))
    ops.append(_ops_txt(_ML, _PH - 50, f"Assembly: {_clip2(assembly, 70)}",
                        font="F1", size=11.0, rgb=_WHITE))
    ops.append(_ops_txt(_ML, _PH - 63, "AIAG-VDA 2019 Agentic Review System",
                        font="F1", size=8.0, rgb=(0.75, 0.82, 0.93)))

    y_meta = _PH - 160.0
    bw_m, bh_m, gap_m = 248.0, 52.0, 16.0
    for i, (label, val) in enumerate([
        ("Review ID", str(review_id)[:36]),
        ("Created",   created),
        ("Status",    status),
        ("Generated", generated),
    ]):
        col = i % 2
        row = i // 2
        bx  = _ML + col * (bw_m + gap_m)
        by  = y_meta - row * (bh_m + gap_m)
        ops.append(_ops_brect(bx, by, bw_m, bh_m, _LGRAY, _MGRAY, 0.4))
        ops.append(_ops_txt(bx + 8, by + bh_m - 16, label, font="F2", size=8.0, rgb=_NAVY))
        ops.append(_ops_txt(bx + 8, by + 10, _clip2(val, 38), font="F1", size=10.0, rgb=_BLACK))

    y_div = y_meta - 2 * (bh_m + gap_m) - 14
    ops.append(_ops_hline(_ML, y_div, _CW, _NAVY, 1.0))
    ops.append(_ops_txt(_ML, y_div + 4, "ACTION PRIORITY SUMMARY",
                        font="F2", size=10.0, rgb=_NAVY))

    y_ap = y_div - 90.0
    abw  = (_CW - 17.0) / 3
    abh  = 76.0
    for i, ap_key in enumerate(["H", "M", "L"]):
        ax    = _ML + i * (abw + 8.5)
        count = ap_counts.get(ap_key, 0)
        dk, lt = _AP_DARK[ap_key], _AP_LIGHT[ap_key]
        ops.append(_ops_brect(ax, y_ap, abw, abh, lt, dk, 1.0))
        ops.append(_ops_frect(ax, y_ap, abw, 22.0, dk))
        lbl = _AP_LABEL[ap_key] + " PRIORITY"
        ops.append(_ops_txt(ax + abw / 2 - len(lbl) * 2.6,
                            y_ap + 7, lbl, font="F2", size=8.0, rgb=_WHITE))
        cs = str(count)
        ops.append(_ops_txt(ax + abw / 2 - len(cs) * 9.5,
                            y_ap + 36, cs, font="F2", size=30.0, rgb=dk))
        ops.append(_ops_txt(ax + abw / 2 - 17, y_ap + 26,
                            "findings", font="F1", size=7.0, rgb=(0.4, 0.4, 0.4)))

    y_bar     = y_ap - 34.0
    bar_total = max(total, 1)
    ops.append(_ops_txt(_ML, y_bar + 14, "Distribution of Findings by Priority",
                        font="F2", size=9.0, rgb=_NAVY))
    bar_x = _ML
    for ap_key in ["H", "M", "L"]:
        cnt    = ap_counts.get(ap_key, 0)
        bw_seg = _CW * cnt / bar_total
        if bw_seg > 0.5:
            ops.append(_ops_frect(bar_x, y_bar - 22, bw_seg, 18.0, _AP_DARK[ap_key]))
            if bw_seg > 30:
                ops.append(_ops_txt(bar_x + 4, y_bar - 15,
                                    f"{100 * cnt // bar_total}%",
                                    font="F1", size=7.0, rgb=_WHITE))
        bar_x += bw_seg
    ops.append(_ops_srect(_ML, y_bar - 22, _CW, 18.0, _MGRAY, 0.4))

    lx = _ML
    for ap_key in ["H", "M", "L"]:
        ops.append(_ops_frect(lx, y_bar - 36, 10.0, 8.0, _AP_DARK[ap_key]))
        ops.append(_ops_txt(lx + 13, y_bar - 36,
                            f"{_AP_LABEL[ap_key]}: {ap_counts.get(ap_key, 0)}",
                            font="F1", size=7.0, rgb=_BLACK))
        lx += 90.0

    y_total = y_bar - 52.0
    ops.append(_ops_txt(_ML, y_total, f"Total findings identified: {total}",
                        font="F1", size=9.0, rgb=_BLACK))
    if ap_counts.get("H", 0) > 0:
        msg = (f"ATTENTION: {ap_counts['H']} high-priority finding(s) require immediate "
               f"corrective action per AIAG-VDA 2019 Section 7.")
        msg_rgb = _AP_DARK["H"]
    elif total == 0:
        msg     = "No failure mode gaps identified. Design passes preliminary DFMEA screening."
        msg_rgb = _AP_DARK["L"]
    else:
        msg     = "All findings are medium or low priority. Review and disposition as required."
        msg_rgb = _BLACK
    ops.append(_ops_txt(_ML, y_total - 16, _clip2(msg, 95),
                        font="F1", size=9.0, rgb=msg_rgb))

    ops += _footer_ops(page_num, total_pages)
    return _pdf_stream(ops)


_F_HDR_H = 22.0
_F_ROW_H = 30.0
_WRAP_CHARS = [3, 12, 16, 22, 2, 2, 2, 36]


def _wrap2(text: str, chars: int) -> tuple[str, str]:
    text = str(text).replace("\n", " ").strip()
    if len(text) <= chars:
        return text, ""
    split = text.rfind(" ", 0, chars)
    if split <= 0:
        split = text.rfind("_", 0, chars)
    if split <= 0:
        split = chars
    line1 = text[:split].rstrip("_").rstrip()
    rest  = text[split:].strip().lstrip("_")
    if len(rest) > chars:
        rest = rest[:chars - 2] + ".."
    return line1, rest


def _findings_stream(chunk: list, page_num: int, total_pages: int,
                     chunk_start: int) -> bytes:
    ops: list[str] = []
    ops.append(_ops_frect(0, _PH - 44, _PW, 44.0, _NAVY))
    ops.append(_ops_txt(_ML, _PH - 26, "DFMEA FINDINGS",
                        font="F2", size=14.0, rgb=_WHITE))
    ops.append(_ops_txt(_ML, _PH - 38,
                        f"Items {chunk_start + 1} to {chunk_start + len(chunk)}",
                        font="F1", size=8.0, rgb=(0.75, 0.82, 0.93)))

    y_hdr = _PH - 60.0
    ops.append(_ops_frect(_ML, y_hdr, _CW, _F_HDR_H, _NAVY))
    for label, cx_off, _cw, _ in _FCOLS:
        ops.append(_ops_txt(_ML + cx_off + 3, y_hdr + 7, label,
                            font="F2", size=8.0, rgb=_WHITE))
    for _, cx_off, _, _ in _FCOLS[1:]:
        ops.append(_ops_vline(_ML + cx_off, y_hdr, y_hdr + _F_HDR_H, _WHITE, 0.3))

    y_row = y_hdr - _F_ROW_H
    for row_i, f in enumerate(chunk):
        ap     = str(f.get("action_priority", "L"))
        dk     = _AP_DARK.get(ap, _BLACK)
        lt     = _AP_LIGHT.get(ap, _LGRAY)
        stripe = _LGRAY if row_i % 2 == 1 else _WHITE

        ops.append(_ops_frect(_ML, y_row, _CW, _F_ROW_H, stripe))
        ops.append(_ops_frect(_ML, y_row, float(_FCOLS[0][2]), _F_ROW_H, lt))
        ops.append(_ops_hline(_ML, y_row, _CW, _MGRAY, 0.3))

        raw_vals = [
            ap,
            str(f.get("source", f.get("agent", ""))),
            str(f.get("finding_type", "")),
            str(f.get("affected_component", "")),
            str(f.get("severity", "")),
            str(f.get("occurrence", "")),
            str(f.get("detection", "")),
            str(f.get("description", "")),
        ]
        for col_i, (_, cx_off, _, _) in enumerate(_FCOLS):
            cx    = _ML + cx_off + 3
            fnt   = "F2" if col_i == 0 else "F1"
            sz    = 7.5 if col_i == 7 else 8.0
            rgb   = dk if col_i == 0 else _BLACK
            line1, line2 = _wrap2(raw_vals[col_i], _WRAP_CHARS[col_i])
            if line2:
                ops.append(_ops_txt(cx, y_row + 20, line1, font=fnt, size=sz, rgb=rgb))
                ops.append(_ops_txt(cx, y_row + 8,  line2, font=fnt, size=sz, rgb=rgb))
            else:
                ops.append(_ops_txt(cx, y_row + 11, line1, font=fnt, size=sz, rgb=rgb))
        y_row -= _F_ROW_H

    ops.append(_ops_srect(_ML, y_hdr - len(chunk) * _F_ROW_H,
                          _CW, _F_HDR_H + len(chunk) * _F_ROW_H, _NAVY, 0.6))

    ops += _footer_ops(page_num, total_pages)
    return _pdf_stream(ops)


def _gates_stream(review: dict, page_num: int, total_pages: int) -> bytes:
    ops: list[str] = []
    ops.append(_ops_frect(0, _PH - 44, _PW, 44.0, _NAVY))
    ops.append(_ops_txt(_ML, _PH - 26, "GATE DECISIONS & APPROVALS",
                        font="F2", size=14.0, rgb=_WHITE))

    gates = [
        ("Gate 1", "Intake Validation",   str(review.get("gate_1_status", "N/A"))),
        ("Gate 2", "CAD Verification",    str(review.get("gate_2_status", "N/A"))),
        ("Gate 3", "Agent Analysis",      str(review.get("gate_3_status", "N/A"))),
        ("Gate 4", "HITL Final Approval", str(review.get("hitl_decision",  "N/A"))),
    ]

    y_card = _PH - 90.0
    cw2    = (_CW - 12.0) / 2
    for i, (gate, desc, status) in enumerate(gates):
        gx = _ML + (i % 2) * (cw2 + 12.0)
        gy = y_card - (i // 2) * 78.0
        ops.append(_ops_brect(gx, gy - 64.0, cw2, 64.0, _LGRAY, _MGRAY, 0.4))
        ops.append(_ops_txt(gx + 8, gy - 18, gate, font="F2", size=10.0, rgb=_NAVY))
        ops.append(_ops_txt(gx + 8, gy - 32, desc, font="F1", size=8.0,
                            rgb=(0.3, 0.3, 0.3)))
        sup = status.upper()
        if any(k in sup for k in ("PASS", "APPROVED", "COMPLETE", "APPROVE")):
            pill_c = _AP_DARK["L"]
        elif any(k in sup for k in ("FAIL", "REJECT", "DENIED")):
            pill_c = _AP_DARK["H"]
        else:
            pill_c = (0.40, 0.40, 0.40)
        ops.append(_ops_frect(gx + 8, gy - 54.0, 90.0, 14.0, pill_c))
        ops.append(_ops_txt(gx + 11, gy - 50.0, _clip2(status, 15),
                            font="F1", size=7.0, rgb=_WHITE))

    y_cmt = y_card - 2 * 78.0 - 10.0
    ops.append(_ops_hline(_ML, y_cmt, _CW, _NAVY, 0.8))
    ops.append(_ops_txt(_ML, y_cmt - 16, "HITL Reviewer Comment:",
                        font="F2", size=9.0, rgb=_NAVY))
    comment = str(review.get("hitl_comment", "(none)"))
    words, lines, line = comment.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > 88:
            lines.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        lines.append(line)
    for ln_i, ln in enumerate(lines[:5]):
        ops.append(_ops_txt(_ML + 8, y_cmt - 32 - ln_i * 13,
                            ln, font="F1", size=9.0, rgb=_BLACK))

    y_sig = y_cmt - 32 - min(len(lines), 5) * 13 - 28.0
    ops.append(_ops_hline(_ML, y_sig, _CW, _NAVY, 0.8))
    ops.append(_ops_txt(_ML, y_sig - 14, "SIGNATURES",
                        font="F2", size=9.0, rgb=_NAVY))
    sw = _CW / 3
    for i, role in enumerate(["DFMEA Team Leader", "Quality Manager", "Engineering Manager"]):
        sx = _ML + i * sw
        ops.append(_ops_hline(sx + 8, y_sig - 50.0, sw - 16, _BLACK, 0.5))
        ops.append(_ops_txt(sx + 8, y_sig - 62.0, role, font="F1", size=8.0,
                            rgb=(0.3, 0.3, 0.3)))
        ops.append(_ops_txt(sx + 8, y_sig - 73.0, "Date: _______________",
                            font="F1", size=7.0, rgb=(0.5, 0.5, 0.5)))

    ops += _footer_ops(page_num, total_pages)
    return _pdf_stream(ops)


def _assemble_pdf2(content_streams: list) -> bytes:
    """Build PDF 1.4 with two fonts: F1=Helvetica, F2=Helvetica-Bold."""
    objects: list[bytes] = []

    def _add(b: bytes) -> int:
        objects.append(b)
        return len(objects)

    ph        = b""
    catalog_i = _add(ph)   # 1
    pages_i   = _add(ph)   # 2
    f1_i      = _add(ph)   # 3  Helvetica
    f2_i      = _add(ph)   # 4  Helvetica-Bold

    page_ids: list[int] = []
    for stream_bytes in content_streams:
        cont_i = _add(stream_bytes)
        page_i = _add(
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            b"/Resources<</Font<</F1 3 0 R/F2 4 0 R>>>>"
            b"/Contents " + str(cont_i).encode() + b" 0 R>>"
        )
        page_ids.append(page_i)

    kids = b" ".join(str(i).encode() + b" 0 R" for i in page_ids)
    objects[pages_i   - 1] = (b"<</Type/Pages/Kids[" + kids + b"]/Count "
                               + str(len(page_ids)).encode() + b">>")
    objects[catalog_i - 1] = b"<</Type/Catalog/Pages 2 0 R>>"
    objects[f1_i      - 1] = b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>"
    objects[f2_i      - 1] = b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold/Encoding/WinAnsiEncoding>>"

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    body   = header
    offsets: list[int] = []
    for i, obj in enumerate(objects):
        offsets.append(len(body))
        body += str(i + 1).encode() + b" 0 obj\n" + obj + b"\nendobj\n"

    xref_off = len(body)
    xref  = b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    xref += b"0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        b"trailer\n<</Size " + str(len(objects) + 1).encode()
        + b"/Root 1 0 R>>\nstartxref\n"
        + str(xref_off).encode() + b"\n%%EOF\n"
    )
    return body + xref + trailer


def build_report_pdf(review: dict, findings: list) -> bytes:
    """Build a professional DFMEA report PDF: cover + findings table + gate decisions."""
    ap_counts = {"H": 0, "M": 0, "L": 0}
    for f in findings:
        ap = str(f.get("action_priority", "L"))
        ap_counts[ap] = ap_counts.get(ap, 0) + 1
    total = len(findings)

    CHUNK = 22
    n_fp  = max(1, (total + CHUNK - 1) // CHUNK) if total > 0 else 1
    total_pages = 1 + n_fp + 1  # cover + findings + gates

    streams: list[bytes] = [
        _cover_stream(review, ap_counts, total, 1, total_pages),
    ]

    if total == 0:
        eo: list[str] = [
            _ops_frect(0, _PH - 44, _PW, 44.0, _NAVY),
            _ops_txt(_ML, _PH - 26, "DFMEA FINDINGS", font="F2", size=14.0, rgb=_WHITE),
            _ops_txt(_ML, _PH / 2 + 10,
                     "No failure mode findings were identified for this review.",
                     font="F1", size=11.0, rgb=(0.3, 0.3, 0.3)),
            _ops_txt(_ML, _PH / 2 - 6,
                     "Design passes preliminary DFMEA screening.",
                     font="F1", size=11.0, rgb=_AP_DARK["L"]),
        ]
        eo += _footer_ops(2, total_pages)
        streams.append(_pdf_stream(eo))
    else:
        for pi, start in enumerate(range(0, total, CHUNK)):
            streams.append(_findings_stream(findings[start: start + CHUNK],
                                            pi + 2, total_pages, start))

    streams.append(_gates_stream(review, total_pages, total_pages))
    return _assemble_pdf2(streams)


def run_report(event: dict) -> dict:
    """
    S6_REPORT stage: read findings from DynamoDB, build a PDF, upload to S3,
    and mark the review COMPLETE.
    """
    review_id      = event["review_id"]
    region         = os.environ.get("AWS_REGION", "us-east-1")
    findings_table = os.environ.get("FINDINGS_TABLE_NAME", "dfmea-analysis-findings")
    reviews_table  = os.environ.get("REVIEWS_TABLE_NAME", "dfmea-reviews")
    reports_bucket = os.environ.get("REPORTS_BUCKET_NAME", "dfmea-reports")

    dynamodb = boto3.resource("dynamodb", region_name=region)
    s3       = boto3.client("s3", region_name=region)

    review = dynamodb.Table(reviews_table).get_item(Key={"review_id": review_id}).get("Item", {})

    table = dynamodb.Table(findings_table)
    query_args = {"KeyConditionExpression": Key("review_id").eq(review_id)}
    all_findings: list[dict] = []
    while True:
        resp = table.query(**query_args)
        all_findings.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        query_args["ExclusiveStartKey"] = last_key

    # The analyst writes the deduplicated authoritative set. Fall back to raw
    # specialist findings only when synthesis intentionally produced no rows.
    analyst_findings = [f for f in all_findings if f.get("agent") == "analyst"]
    findings = analyst_findings or all_findings

    ap_counts = {"H": 0, "M": 0, "L": 0}
    for f in findings:
        ap = f.get("action_priority", "L")
        ap_counts[ap] = ap_counts.get(ap, 0) + 1

    now = datetime.datetime.now(datetime.UTC).isoformat()
    pdf_bytes = build_report_pdf(review, findings)

    report_key = f"reviews/{review_id}/final-report.pdf"
    s3.put_object(
        Bucket=reports_bucket,
        Key=report_key,
        Body=pdf_bytes,
        ContentType="application/pdf",
        ContentDisposition=f'attachment; filename="dfmea-report-{review_id[:8]}.pdf"',
    )

    dynamodb.Table(reviews_table).update_item(
        Key={"review_id": review_id},
        UpdateExpression="SET #s = :s, report_key = :k, completed_at = :t, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "COMPLETE",
            ":k": report_key,
            ":t": now,
        },
    )
    print(f"[analyst:S6_REPORT] review={review_id} findings={len(findings)} pdf={len(pdf_bytes)}B")
    return {
        "review_id":  review_id,
        "report_key": report_key,
        "findings":   len(findings),
        "ap_counts":  ap_counts,
        "status":     "COMPLETE",
    }


# ── Minimal single-font PDF builder (used by tests + simple fallbacks) ────────

def _build_pdf(pages: list) -> bytes:
    """Build a minimal valid PDF 1.4 document from a list of pages.

    Each page is a list of text lines. No external libraries required.
    """
    objects: list[bytes] = []

    def add_obj(content: bytes) -> int:
        objects.append(content)
        return len(objects)  # 1-based index

    placeholder = b""
    catalog_idx = add_obj(placeholder)   # 1
    pages_idx   = add_obj(placeholder)   # 2
    font_idx    = add_obj(placeholder)   # 3

    page_obj_ids: list[int] = []
    for page_lines in pages:
        ops: list[str] = []
        y = 760
        for line in page_lines:
            ops.append(f"BT /F1 10 Tf 40 {y} Td ({_esc(str(line))}) Tj ET")
            y -= 14
            if y < 40:
                break
        content_bytes = "\n".join(ops).encode()
        content_idx = add_obj(
            b"<</Length " + str(len(content_bytes)).encode() + b">>\nstream\n"
            + content_bytes + b"\nendstream"
        )
        page_idx = add_obj(
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            b"/Resources<</Font<</F1 3 0 R>>>>"
            b"/Contents " + str(content_idx).encode() + b" 0 R>>"
        )
        page_obj_ids.append(page_idx)

    kids = b" ".join(str(i).encode() + b" 0 R" for i in page_obj_ids)
    objects[pages_idx - 1]   = b"<</Type/Pages/Kids[" + kids + b"]/Count " + str(len(page_obj_ids)).encode() + b">>"
    objects[catalog_idx - 1] = b"<</Type/Catalog/Pages 2 0 R>>"
    objects[font_idx - 1]    = b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>"

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    body   = header
    offsets: list[int] = []
    for i, obj in enumerate(objects):
        offsets.append(len(body))
        body += str(i + 1).encode() + b" 0 obj\n" + obj + b"\nendobj\n"

    xref_offset = len(body)
    xref = b"xref\n0 " + str(len(objects) + 1).encode() + b"\n"
    xref += b"0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        b"trailer\n<</Size " + str(len(objects) + 1).encode()
        + b"/Root 1 0 R>>\nstartxref\n"
        + str(xref_offset).encode() + b"\n%%EOF\n"
    )
    return body + xref + trailer
