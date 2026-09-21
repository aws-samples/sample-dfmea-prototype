# tests/unit/test_analyst_report.py
"""Unit tests for the deterministic analyst S6 PDF report (agents/analyst/report.py)
and the analyst thin-invoker stage dispatch (lambdas/agent_invoker/analyst_handler.py)."""
import os, sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
os.environ.setdefault("REVIEWS_TABLE_NAME",  "dfmea-reviews")
os.environ.setdefault("FINDINGS_TABLE_NAME", "dfmea-analysis-findings")
os.environ.setdefault("REPORTS_BUCKET_NAME", "dfmea-reports")


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_finding(ap: str, comp: str = "BoltA", fm: str = "fracture") -> dict:
    return {
        "review_id":          "rev-report-001",
        "finding_id":         "f-001",
        "affected_component": comp,
        "failure_mode":       fm,
        "effect":             "loss of clamp load",
        "action_priority":    ap,
        "severity":           Decimal("8"),
        "occurrence":         Decimal("3"),
        "detection":          Decimal("4"),
        "source":             "kb",
        "agent":              "analyst",
    }


def _mock_ddb(findings: list, review_item: dict | None = None):
    table_mock = MagicMock()
    table_mock.get_item.return_value = {"Item": review_item or {
        "review_id":     "rev-report-001",
        "assembly_name": "Test Assembly",
        "status":        "HITL_APPROVED",
        "created_at":    "2026-06-30T00:00:00Z",
        "hitl_decision": "APPROVED",
    }}
    table_mock.query.return_value    = {"Items": findings}
    table_mock.update_item.return_value = {}
    resource_mock = MagicMock()
    resource_mock.Table.return_value = table_mock
    return resource_mock


# ── _build_pdf ────────────────────────────────────────────────────────────────

def test_build_pdf_produces_valid_pdf_header():
    from agents.analyst.report import _build_pdf
    pdf = _build_pdf([["Hello", "World"]])
    assert pdf.startswith(b"%PDF-1.4"), "PDF must start with %PDF-1.4 header"
    assert b"%%EOF" in pdf


def test_build_pdf_multiple_pages():
    from agents.analyst.report import _build_pdf
    pages = [["Page one line"], ["Page two line"], ["Page three line"]]
    pdf = _build_pdf(pages)
    assert pdf.count(b"0 obj") == len(pages) * 2 + 3  # 2 objs per page + catalog+pages+font


def test_build_pdf_escapes_special_chars():
    from agents.analyst.report import _build_pdf
    pdf = _build_pdf([["Hello (world) \\ test"]])
    assert b"\\(" in pdf
    assert b"\\\\" in pdf


# ── run_report ────────────────────────────────────────────────────────────────

@patch("boto3.client")
@patch("boto3.resource")
def test_run_report_uploads_pdf_to_s3(mock_resource, mock_client):
    findings = [_make_finding("H"), _make_finding("M"), _make_finding("L")]
    mock_resource.return_value = _mock_ddb(findings)
    s3_mock = MagicMock()
    mock_client.return_value = s3_mock

    from agents.analyst import report
    result = report.run_report({"review_id": "rev-report-001", "stage": "S6_REPORT"})

    assert result["status"] == "COMPLETE"
    assert result["findings"] == 3
    assert result["ap_counts"] == {"H": 1, "M": 1, "L": 1}
    s3_mock.put_object.assert_called_once()
    call_kwargs = s3_mock.put_object.call_args[1]
    assert call_kwargs["Key"] == "reviews/rev-report-001/final-report.pdf"
    assert call_kwargs["ContentType"] == "application/pdf"
    assert call_kwargs["Body"].startswith(b"%PDF-1.4")


@patch("boto3.client")
@patch("boto3.resource")
def test_run_report_marks_review_complete(mock_resource, mock_client):
    mock_resource.return_value = _mock_ddb([_make_finding("H")])
    mock_client.return_value   = MagicMock()

    from agents.analyst import report
    report.run_report({"review_id": "rev-report-001", "stage": "S6_REPORT"})

    table = mock_resource.return_value.Table.return_value
    table.update_item.assert_called_once()
    expr_vals = table.update_item.call_args[1]["ExpressionAttributeValues"]
    assert ":s" in expr_vals and expr_vals[":s"] == "COMPLETE"


@patch("boto3.client")
@patch("boto3.resource")
def test_run_report_zero_findings(mock_resource, mock_client):
    mock_resource.return_value = _mock_ddb([])
    mock_client.return_value   = MagicMock()

    from agents.analyst import report
    result = report.run_report({"review_id": "rev-report-001", "stage": "S6_REPORT"})

    assert result["findings"] == 0
    assert result["ap_counts"] == {"H": 0, "M": 0, "L": 0}
    pdf = mock_client.return_value.put_object.call_args[1]["Body"]
    assert b"No failure mode findings" in pdf


# ── analyst dispatcher (stage routing) ────────────────────────────────────────

def test_analyst_handler_dispatches_on_stage(monkeypatch):
    """analyst_handler must run the PDF report for S6_REPORT, else invoke the runtime."""
    import lambdas.agent_invoker.analyst_handler as ah
    import agents.analyst.report as report

    with patch.object(report, "run_report", return_value={"status": "COMPLETE"}) as rr, \
         patch.object(ah, "invoke_runtime", return_value={"status": "invoked"}) as ri:
        ah.handler({"review_id": "r1", "stage": "S6_REPORT"}, None)
        rr.assert_called_once()
        ri.assert_not_called()

        rr.reset_mock(); ri.reset_mock()
        monkeypatch.setattr(ah, "_load_specialist_findings", lambda rid: [])
        ah.handler({"review_id": "r1"}, None)
        ri.assert_called_once()
        rr.assert_not_called()
