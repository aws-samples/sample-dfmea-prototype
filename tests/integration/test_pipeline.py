# tests/integration/test_pipeline.py
"""
Full end-to-end pipeline integration test.
Submits a real DFMEA review, auto-approves all 4 gates, and asserts
a PDF report is generated. Marked slow — takes ~15 minutes.

Run: pytest tests/integration/test_pipeline.py -v -s
Skip: pytest tests/integration/ -m "not slow"
"""
import time
import json
import pytest
import requests

TERMINAL_STATES = {"ERROR", "FAILED", "HITL_REJECTED", "ARCHIVED"}

# Sample DFMEA JSON for the test review
SAMPLE_REVIEW = {
    "assembly_name": "Integration Test B-Pillar Assembly",
    "rows": [
        {
            "item_number": "1",
            "component": "B-Pillar reinforcement",
            "function": "Transfer side-impact load",
            "failure_mode": "Hydrogen-induced fracture",
            "effect": "Delayed brittle fracture",
            "severity": 9,
            "cause": "Weld thermal cycle",
            "occurrence": 3,
            "current_controls": "Embrittlement test",
            "detection": 4,
            "action_priority": "H",
        },
        {
            "item_number": "2",
            "component": "Spot-weld joints",
            "function": "Form to crash geometry",
            "failure_mode": "Excessive thinning",
            "effect": "Reduced crash performance",
            "severity": 7,
            "cause": "Forming params / TRB zone",
            "occurrence": 4,
            "current_controls": "SPC Cpk >= 1.33",
            "detection": 5,
            "action_priority": "M",
        },
    ],
}


# ── Polling helper ─────────────────────────────────────────────────────────

def poll_until(condition_fn, interval: int = 10, timeout: int = 300,
               description: str = "condition") -> object:
    """
    Polls condition_fn every `interval` seconds until it returns non-None.
    Returns None means "not yet". Any other value (including falsy like 0 or {})
    means "done". Fails immediately if a terminal error state is detected.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition_fn()
        if result is not None:
            return result
        time.sleep(interval)
    pytest.fail(f"Timed out after {timeout}s waiting for: {description}")


def _check_terminal(status: str, description: str) -> None:
    """Fail immediately if review is in a terminal error state."""
    if status in TERMINAL_STATES:
        pytest.fail(f"Review entered terminal state {status!r} while waiting for: {description}")


# ── Pipeline test ──────────────────────────────────────────────────────────

@pytest.mark.slow
def test_full_pipeline(api):
    """
    Full pipeline: upload file → create review → approve gates 1-3 →
    approve HITL (gate 4) → wait for COMPLETE → assert PDF.
    """
    review_id = None
    try:
        # ── Step 1: Get presigned upload URL ──────────────────────────────
        r = api.get("/upload-url?filename=integration-test.json")
        assert r.status_code == 200, f"upload-url failed: {r.text}"
        upload_url = r.json()["url"]
        file_key   = r.json()["key"]

        # ── Step 2: Upload sample review JSON to S3 ───────────────────────
        file_bytes = json.dumps(SAMPLE_REVIEW).encode()
        put_r = requests.put(
            upload_url,
            data=file_bytes,
            headers={"Content-Type": "application/json"},
        )
        assert put_r.status_code == 200, f"S3 presigned PUT failed: {put_r.status_code} {put_r.text}"

        # ── Step 3: Create review ─────────────────────────────────────────
        r = api.post("/reviews", json={
            "assembly_name": SAMPLE_REVIEW["assembly_name"],
            "file_key": file_key,
        })
        assert r.status_code == 201, f"POST /reviews failed: {r.text}"
        review_id = r.json()["review_id"]
        print(f"\n  review_id = {review_id}")

        # ── Gate 1: Intake validation ─────────────────────────────────────
        print("  Waiting for Gate 1 PENDING...")

        def gate1_pending():
            # Gate is pending when the task token is stored (gates[N]["pending"] == True)
            # gate_N_status is only written AFTER approval/rejection — never set to "PENDING"
            review = api.get(f"/reviews/{review_id}").json()
            _check_terminal(review.get("status", ""), "Gate 1 PENDING")
            gates = api.get(f"/reviews/{review_id}/gates").json().get("gates", {})
            return True if gates.get("1", {}).get("pending") else None

        poll_until(gate1_pending, interval=10, timeout=300, description="Gate 1 pending")
        r = api.post(f"/reviews/{review_id}/gate/1",
                     json={"action": "approve", "comment": "Auto-approved by integration test"})
        assert r.status_code == 200, f"Gate 1 approve failed: {r.text}"
        print("  Gate 1 approved")

        # ── Gate 2: CAD verification ──────────────────────────────────────
        print("  Waiting for Gate 2 PENDING...")

        def gate2_pending():
            review = api.get(f"/reviews/{review_id}").json()
            _check_terminal(review.get("status", ""), "Gate 2 PENDING")
            gates = api.get(f"/reviews/{review_id}/gates").json().get("gates", {})
            return True if gates.get("2", {}).get("pending") else None

        poll_until(gate2_pending, interval=10, timeout=300, description="Gate 2 pending")
        r = api.post(f"/reviews/{review_id}/gate/2",
                     json={"action": "approve", "comment": "Auto-approved by integration test"})
        assert r.status_code == 200, f"Gate 2 approve failed: {r.text}"
        print("  Gate 2 approved")

        # ── Gate 3: Agent analysis review ─────────────────────────────────
        print("  Waiting for Gate 3 PENDING (parallel Bedrock agents running)...")

        def gate3_pending():
            review = api.get(f"/reviews/{review_id}").json()
            _check_terminal(review.get("status", ""), "Gate 3 PENDING")
            gates = api.get(f"/reviews/{review_id}/gates").json().get("gates", {})
            return True if gates.get("3", {}).get("pending") else None

        poll_until(gate3_pending, interval=15, timeout=600, description="Gate 3 pending")
        r = api.post(f"/reviews/{review_id}/gate/3",
                     json={"action": "approve", "comment": "Auto-approved by integration test"})
        assert r.status_code == 200, f"Gate 3 approve failed: {r.text}"
        print("  Gate 3 approved")

        # ── Gate 4: HITL approval ─────────────────────────────────────────
        print("  Waiting for HITL_PENDING...")

        def hitl_pending():
            status = api.get(f"/reviews/{review_id}").json().get("status", "")
            _check_terminal(status, "HITL_PENDING")
            return status if status == "HITL_PENDING" else None

        poll_until(hitl_pending, interval=10, timeout=300, description="HITL_PENDING status")
        r = api.post(f"/reviews/{review_id}/hitl",
                     json={"action": "approve", "comment": "Auto-approved by integration test"})
        assert r.status_code == 200, f"HITL approve failed: {r.text}"
        print("  Gate 4 (HITL) approved")

        # ── Wait for COMPLETE ─────────────────────────────────────────────
        print("  Waiting for COMPLETE (S6 report generation)...")

        def review_complete():
            status = api.get(f"/reviews/{review_id}").json().get("status", "")
            _check_terminal(status, "COMPLETE")
            return status if status == "COMPLETE" else None

        poll_until(review_complete, interval=10, timeout=300, description="status COMPLETE")
        print("  Review COMPLETE")

        # ── Assert PDF report ─────────────────────────────────────────────
        r = api.get(f"/reviews/{review_id}/report")
        assert r.status_code == 200, f"GET /report failed: {r.text}"
        report_url = r.json()["url"]
        assert report_url, "report URL is empty"

        pdf_r = requests.get(report_url)
        assert pdf_r.status_code == 200, f"Presigned URL fetch failed: {pdf_r.status_code}"
        assert pdf_r.content[:8] == b"%PDF-1.4", \
            f"Response is not a PDF. First bytes: {pdf_r.content[:20]!r}"
        print(f"  PDF report verified ({len(pdf_r.content)} bytes)")

    finally:
        if review_id:
            api.delete(f"/reviews/{review_id}")
            print(f"  Cleaned up review {review_id}")
