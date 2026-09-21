# Integration Test Suite Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a live integration test suite that validates the deployed AWS DFMEA system by calling real API endpoints, including a full end-to-end pipeline test that auto-approves all 4 HITL gates and asserts a PDF report is produced.

**Architecture:** Three files under `tests/integration/` — `conftest.py` (session fixtures: CloudFormation URL lookup, Cognito test user lifecycle, ApiClient helper), `test_smoke.py` (12 fast endpoint checks), `test_pipeline.py` (full pipeline marked `@pytest.mark.slow`). All configuration is auto-fetched from CloudFormation outputs, matching how the frontend config.json is built.

**Tech Stack:** Python 3.12, pytest, boto3 (CloudFormation + Cognito), requests, pytest marks (`slow`).

**Spec:** `docs/superpowers/specs/2026-07-01-integration-tests-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `tests/integration/__init__.py` | Create | Package marker |
| `tests/integration/conftest.py` | Create | Session fixtures: api_url, cognito_ids, test_user, auth_token, api (ApiClient) |
| `tests/integration/test_smoke.py` | Create | 12 smoke tests covering all API routes |
| `tests/integration/test_pipeline.py` | Create | Full pipeline: upload → create → 4 gates → COMPLETE → PDF |
| `pytest.ini` | Modify | Add `slow` mark registration |

---

### Task 1: Package init + pytest mark registration

**Files:**
- Create: `tests/integration/__init__.py`
- Modify: `pytest.ini`

- [ ] **Step 1: Create the integration test package**

```bash
touch /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype/tests/integration/__init__.py
```

- [ ] **Step 2: Register the `slow` mark in pytest.ini**

Edit `pytest.ini` to add markers config. Current content:
```ini
[pytest]
pythonpath = .
testpaths = tests
```

Updated content:
```ini
[pytest]
pythonpath = .
testpaths = tests
markers =
    slow: marks tests as slow (full pipeline, ~15min). Deselect with -m "not slow".
```

- [ ] **Step 3: Verify pytest discovers integration directory**

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype
python -m pytest tests/integration/ --collect-only 2>&1 | head -10
```
Expected: `no tests ran` (no test files yet, but no collection error)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/__init__.py pytest.ini
git commit -m "test: add integration test package and slow mark"
```

---

### Task 2: conftest.py — fixtures

**Files:**
- Create: `tests/integration/conftest.py`

- [ ] **Step 1: Write conftest.py**

```python
# tests/integration/conftest.py
"""
Session-scoped fixtures for integration tests.
Auto-fetches API URL and Cognito config from CloudFormation outputs.
Creates a temporary Cognito test user and cleans it up after all tests.
"""
import uuid
import pytest
import boto3
import requests
from botocore.exceptions import ClientError


# ── CloudFormation helpers ─────────────────────────────────────────────────

def _cfn_output(stack_name: str, output_key: str) -> str:
    """Return a CloudFormation stack output value by CDK logical ID."""
    cf = boto3.client("cloudformation")
    try:
        resp = cf.describe_stacks(StackName=stack_name)
    except ClientError as e:
        if "does not exist" in str(e):
            pytest.skip(f"Stack {stack_name!r} not deployed — skipping integration tests")
        raise
    outputs = {o["OutputKey"]: o["OutputValue"] for o in resp["Stacks"][0].get("Outputs", [])}
    if output_key not in outputs:
        pytest.skip(f"Output {output_key!r} not found in {stack_name} — skipping")
    return outputs[output_key]


# ── Session fixtures ───────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api_url() -> str:
    """REST API base URL from DfmeaApiStack CloudFormation output."""
    url = _cfn_output("DfmeaApiStack", "RestApiUrl")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def cognito_ids() -> dict:
    """User Pool ID and App Client ID from DfmeaAuthStack."""
    return {
        "user_pool_id":  _cfn_output("DfmeaAuthStack", "UserPoolId"),
        "client_id":     _cfn_output("DfmeaAuthStack", "UserPoolClientId"),
    }


@pytest.fixture(scope="session")
def test_user(cognito_ids, request) -> tuple[str, str]:
    """
    Creates a temporary Cognito user for the test session.
    Cleans up via finalizer even if tests fail.
    """
    uid = uuid.uuid4().hex[:8]
    username = f"dfmea-inttest-{uid}@test.invalid"
    password = f"Inttest-{uid}-Aa1!"  # meets Cognito complexity requirements

    idp = boto3.client("cognito-idp")
    pool_id = cognito_ids["user_pool_id"]

    idp.admin_create_user(
        UserPoolId=pool_id,
        Username=username,
        TemporaryPassword=password,
        MessageAction="SUPPRESS",
    )
    idp.admin_set_user_password(
        UserPoolId=pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )

    def cleanup():
        try:
            idp.admin_delete_user(UserPoolId=pool_id, Username=username)
        except ClientError as e:
            if e.response["Error"]["Code"] != "UserNotFoundException":
                raise

    request.addfinalizer(cleanup)
    return username, password


@pytest.fixture(scope="session")
def auth_token(test_user, cognito_ids) -> str:
    """ID token for the test user, obtained via USER_PASSWORD_AUTH."""
    username, password = test_user
    idp = boto3.client("cognito-idp")
    resp = idp.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
        ClientId=cognito_ids["client_id"],
    )
    return resp["AuthenticationResult"]["IdToken"]


# ── ApiClient helper ───────────────────────────────────────────────────────

class ApiClient:
    """Thin wrapper around requests with base URL and auth header pre-set."""

    def __init__(self, base_url: str, token: str):
        self._base = base_url
        self._headers = {"Authorization": token, "Content-Type": "application/json"}

    def get(self, path: str, **kwargs):
        return requests.get(f"{self._base}{path}", headers=self._headers, **kwargs)

    def post(self, path: str, **kwargs):
        return requests.post(f"{self._base}{path}", headers=self._headers, **kwargs)

    def delete(self, path: str, **kwargs):
        return requests.delete(f"{self._base}{path}", headers=self._headers, **kwargs)

    def get_no_auth(self, path: str, **kwargs):
        return requests.get(f"{self._base}{path}", **kwargs)


@pytest.fixture(scope="session")
def api(api_url, auth_token) -> ApiClient:
    return ApiClient(api_url, auth_token)
```

- [ ] **Step 2: Verify conftest syntax**

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype
python -c "import tests.integration.conftest; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/integration/conftest.py
git commit -m "test: add integration test conftest with CloudFormation + Cognito fixtures"
```

---

### Task 3: test_smoke.py

**Files:**
- Create: `tests/integration/test_smoke.py`

- [ ] **Step 1: Write test_smoke.py**

```python
# tests/integration/test_smoke.py
"""
Smoke tests — verify every API endpoint returns the expected status code
and basic response shape. Fast (~30s), no pipeline execution.
"""
import pytest
import requests


# ── /health ────────────────────────────────────────────────────────────────

def test_health(api):
    r = api.get_no_auth("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert "timestamp" in body


# ── /reviews ───────────────────────────────────────────────────────────────

def test_list_reviews(api):
    r = api.get("/reviews")
    assert r.status_code == 200
    body = r.json()
    assert "reviews" in body
    assert isinstance(body["reviews"], list)


def test_create_review_missing_body(api):
    r = api.post("/reviews", json={})
    assert r.status_code == 400
    assert "error" in r.json()


def test_create_and_get_review(api):
    # Create
    r = api.post("/reviews", json={
        "assembly_name": "Smoke Test Assembly",
        "file_key": "uploads/smoke-test-placeholder.json",
    })
    assert r.status_code == 201
    body = r.json()
    assert "review_id" in body
    review_id = body["review_id"]

    try:
        # Get
        r2 = api.get(f"/reviews/{review_id}")
        assert r2.status_code == 200
        data = r2.json()
        assert data["review_id"] == review_id
        assert data["assembly_name"] == "Smoke Test Assembly"
    finally:
        api.delete(f"/reviews/{review_id}")


def test_get_nonexistent_review(api):
    r = api.get("/reviews/does-not-exist-00000000")
    assert r.status_code == 404


# ── /upload-url ────────────────────────────────────────────────────────────

def test_get_upload_url(api):
    r = api.get("/upload-url?filename=test.json")
    assert r.status_code == 200
    body = r.json()
    assert "url" in body
    assert "key" in body
    assert body["key"].startswith("uploads/")


# ── /admin/metrics ─────────────────────────────────────────────────────────

def test_admin_metrics(api):
    r = api.get("/admin/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "ap_counts" in body
    assert set(body["ap_counts"].keys()) == {"H", "M", "L"}
    assert "total" in body


# ── DELETE /reviews/{id} ───────────────────────────────────────────────────

def test_delete_review(api):
    r = api.post("/reviews", json={
        "assembly_name": "Delete Test",
        "file_key": "uploads/delete-test.json",
    })
    assert r.status_code == 201
    review_id = r.json()["review_id"]

    r2 = api.delete(f"/reviews/{review_id}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "ARCHIVED"


# ── /reviews/{id}/findings ─────────────────────────────────────────────────

def test_findings_empty(api):
    r = api.post("/reviews", json={
        "assembly_name": "Findings Test",
        "file_key": "uploads/findings-test.json",
    })
    review_id = r.json()["review_id"]
    try:
        r2 = api.get(f"/reviews/{review_id}/findings")
        assert r2.status_code == 200
        body = r2.json()
        assert "findings" in body
        assert isinstance(body["findings"], list)
    finally:
        api.delete(f"/reviews/{review_id}")


# ── /reviews/{id}/gates ────────────────────────────────────────────────────

def test_gates_not_started(api):
    r = api.post("/reviews", json={
        "assembly_name": "Gates Test",
        "file_key": "uploads/gates-test.json",
    })
    review_id = r.json()["review_id"]
    try:
        r2 = api.get(f"/reviews/{review_id}/gates")
        assert r2.status_code == 200
        body = r2.json()
        assert "gates" in body
        gates = body["gates"]
        assert set(gates.keys()) == {"1", "2", "3", "4"}
        for g in gates.values():
            assert g["status"] in ("NOT_STARTED", "PENDING")
    finally:
        api.delete(f"/reviews/{review_id}")


# ── /search ────────────────────────────────────────────────────────────────

def test_search_missing_q(api):
    r = api.get("/search")
    assert r.status_code == 400
    assert "error" in r.json()


# ── /reviews/{id}/gate/{n} — bad body ─────────────────────────────────────

def test_gate_action_missing_body(api):
    r = api.post("/reviews", json={
        "assembly_name": "Gate Action Test",
        "file_key": "uploads/gate-action-test.json",
    })
    review_id = r.json()["review_id"]
    try:
        r2 = api.post(f"/reviews/{review_id}/gate/1", json={})
        assert r2.status_code == 400
    finally:
        api.delete(f"/reviews/{review_id}")
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_smoke.py
git commit -m "test: add integration smoke tests for all API endpoints"
```

---

### Task 4: test_pipeline.py

**Files:**
- Create: `tests/integration/test_pipeline.py`

- [ ] **Step 1: Write test_pipeline.py**

```python
# tests/integration/test_pipeline.py
"""
Full end-to-end pipeline integration test.
Submits a real DFMEA review, auto-approves all 4 gates, and asserts
a PDF report is generated. Marked slow — takes ~15 minutes.

Run: pytest tests/integration/test_pipeline.py -v -s
Skip: pytest tests/integration/ -m "not slow"
"""
import os
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
def test_full_pipeline(api, api_url):
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
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/test_pipeline.py
git commit -m "test: add full pipeline integration test with auto gate approval"
```

---

### Task 5: Verify smoke tests run (requires deployed stack)

- [ ] **Step 1: Check AWS credentials are available**

```bash
aws sts get-caller-identity
```
Expected: JSON with Account, UserId, Arn.

- [ ] **Step 2: Run smoke tests**

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype
python -m pytest tests/integration/test_smoke.py -v -s
```
Expected: All 12 tests PASS in ~30s.

- [ ] **Step 3: Confirm pipeline test is collected but skipped by default**

```bash
python -m pytest tests/integration/ -m "not slow" --collect-only 2>&1 | tail -5
```
Expected: 12 smoke tests collected, pipeline test deselected.

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "test: integration test suite complete — smoke + full pipeline"
```

---

## Running the Tests

```bash
# Smoke only (~30s) — safe to run after every deploy
python -m pytest tests/integration/test_smoke.py -v

# Full pipeline (~15min) — run to validate a complete deployment
python -m pytest tests/integration/test_pipeline.py -v -s

# Both
python -m pytest tests/integration/ -v -s

# Skip pipeline
python -m pytest tests/integration/ -m "not slow" -v
```

## Dependencies

Ensure `requests` is available:
```bash
pip install requests
```
Or add to `requirements-dev.txt` if it doesn't already include it.

## Required AWS Permissions

The executing role/user needs:
- `cloudformation:DescribeStacks` on `DfmeaApiStack` and `DfmeaAuthStack`
- `cognito-idp:AdminCreateUser`, `AdminSetUserPassword`, `AdminDeleteUser`, `InitiateAuth` on the DFMEA user pool
