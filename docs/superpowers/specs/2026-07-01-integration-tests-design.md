# Integration Test Suite — Design Spec
**Date:** 2026-07-01
**Status:** Approved

## Overview

Replace slow local CDK synthesis tests with a live integration test suite that validates the deployed AWS infrastructure by calling real API endpoints. Tests run in ~30s (smoke) or ~15min (full pipeline).

## Goals

- Validate the deployed DFMEA system end-to-end: API Gateway → Lambda → DynamoDB → Step Functions → Bedrock → S3
- Auto-configure from CloudFormation outputs (same source as frontend `config.json`)
- Auto-manage a Cognito test user (create before tests, delete after)
- Fully automated pipeline execution including all 4 HITL gate approvals
- Safe to run repeatedly without side effects (cleanup on pass or fail)

## File Structure

```
tests/integration/
  __init__.py
  conftest.py        ← session-scoped fixtures: URL, auth token, test user lifecycle
  test_smoke.py      ← fast API endpoint checks, no auth-free + authenticated
  test_pipeline.py   ← full submit → auto-approve gates → COMPLETE + PDF (~15min)
```

Registered in `pytest.ini` under `testpaths = tests` (already covers `tests/`).
Pipeline tests marked `@pytest.mark.slow` — skip with `pytest -m "not slow"`.

## Configuration

No manual config. All values sourced at runtime:

| Value | Source |
|-------|--------|
| `DFMEA_API_URL` | CloudFormation `DfmeaApiStack` output `DfmeaRestApiUrl` |
| Cognito User Pool ID | CloudFormation `DfmeaAuthStack` output |
| Cognito Client ID | CloudFormation `DfmeaAuthStack` output |
| Test user credentials | Auto-generated: `dfmea-inttest-{uuid8}@test.invalid` / random password |

Override with env vars `DFMEA_API_URL`, `DFMEA_TEST_USER`, `DFMEA_TEST_PASSWORD` if needed.

## `conftest.py` — Session Fixtures

### `api_url` (session scope)
Calls `boto3` CloudFormation `describe-stacks("DfmeaApiStack")`, extracts output with `OutputKey == "RestApiUrl"` (CDK logical ID, not export name). Raises `pytest.skip` with clear message if stack not found (so tests skip cleanly in environments without a deployment).

### `cognito_ids` (session scope)
Fetches User Pool ID and App Client ID from `DfmeaAuthStack` CloudFormation outputs using CDK logical IDs: `OutputKey == "UserPoolId"` and `OutputKey == "UserPoolClientId"`.

### `test_user` (session scope)
1. Creates Cognito user via `admin_create_user` with generated credentials
2. Forces password with `admin_set_user_password` (permanent)
3. Yields `(username, password)`
4. Finalizer: `admin_delete_user` wrapped in `try/except ClientError` that ignores `UserNotFoundException` (guards against cleanup failure if user creation itself failed)

### `auth_token` (session scope, depends on `test_user` + `cognito_ids`)
Calls `initiate_auth(AuthFlow=USER_PASSWORD_AUTH)`, returns the `IdToken`.

### `api` (session scope)
Returns a thin `ApiClient` helper:
```python
class ApiClient:
    def get(self, path, **kwargs)   # includes Authorization header
    def post(self, path, **kwargs)
    def delete(self, path, **kwargs)
    def get_no_auth(self, path)     # for /health
```

## `test_smoke.py` — Smoke Tests

All tests run in <30s total.

| Test | Endpoint | Auth | Assertion |
|------|----------|------|-----------|
| `test_health` | `GET /health` | None | 200, `status=healthy` |
| `test_list_reviews` | `GET /reviews` | Yes | 200, body has `reviews` list |
| `test_create_review_missing_body` | `POST /reviews` (empty) | Yes | 400 |
| `test_create_and_get_review` | `POST /reviews` → `GET /reviews/{id}` | Yes | 201 + review_id; 200 + fields match |
| `test_get_nonexistent_review` | `GET /reviews/does-not-exist` | Yes | 404 |
| `test_get_upload_url` | `GET /upload-url?filename=test.json` | Yes | 200, url in body |
| `test_admin_metrics` | `GET /admin/metrics` | Yes | 200, ap_counts has H/M/L |
| `test_delete_review` | `DELETE /reviews/{id}` | Yes | 200, status=ARCHIVED (soft delete) |
| `test_findings_empty` | `GET /reviews/{id}/findings` | Yes | 200, findings=[] |
| `test_gates_not_started` | `GET /reviews/{id}/gates` | Yes | 200, all 4 gates NOT_STARTED |
| `test_search_missing_q` | `GET /search` (no q param) | Yes | 400 |
| `test_gate_action_missing_body` | `POST /reviews/{id}/gate/1` (empty body) | Yes | 400 |

Smoke tests create throwaway reviews and archive them in teardown.

## `test_pipeline.py` — Full Pipeline Test

Marked `@pytest.mark.slow`. Uses `sample-data/base/b_pillar_assembly.json` as the review file.

### Steps

1. **Upload file** — `GET /upload-url` → presigned PUT URL → `requests.put(url, data=file_bytes)` → 200
2. **Create review** — `POST /reviews` with `assembly_name="Integration Test Assembly"`, `file_key` → 201, capture `review_id`
3. **Gate 1 — Intake validation**
   - Poll `GET /reviews/{id}/gates` every 10s, timeout 5min, until `gates.1.status == PENDING`
   - `POST /reviews/{id}/gate/1` with `{"action": "approve", "comment": "Auto-approved by integration test"}`
   - Assert 200
4. **Gate 2 — CAD verification**
   - Poll gates every 10s, timeout 5min, until `gates.2.status == PENDING`
   - `POST /reviews/{id}/gate/2` approve → 200
5. **Gate 3 — Agent analysis review**
   - Poll gates every 15s, timeout 10min (parallel Bedrock agents run here)
   - `POST /reviews/{id}/gate/3` approve → 200
6. **Gate 4 — HITL approval**
   - Poll `GET /reviews/{id}` every 10s, timeout 5min, until `status == HITL_PENDING`
   - `POST /reviews/{review_id}/hitl` with `{"action": "approve", "comment": "Auto-approved by integration test"}`
   - Assert 200
7. **Wait for COMPLETE**
   - Poll `GET /reviews/{id}` every 10s, timeout 5min, until `status == COMPLETE`
8. **Verify report**
   - `GET /reviews/{id}/report` → 200, `url` field present
   - Fetch the presigned URL directly (no auth) → assert response body starts with `%PDF-1.4`

### Cleanup
`finally` block runs `DELETE /reviews/{id}` regardless of pass/fail.

### Polling helper
```python
TERMINAL_STATES = {"ERROR", "FAILED", "HITL_REJECTED"}

def poll_until(condition_fn, interval=10, timeout=300, description="condition"):
    """
    condition_fn returns None to mean "not yet", or any other value (including
    falsy values like 0 or {}) to mean "done". Explicit None check avoids
    conflating empty results with "not ready".
    Fails immediately if review enters a terminal error state.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition_fn()
        if result is not None:
            return result
        time.sleep(interval)
    pytest.fail(f"Timed out after {timeout}s waiting for: {description}")
```

All condition functions return `None` when the condition is not yet met, and return the relevant value (status string, gate dict, etc.) when ready. If `GET /reviews/{id}` returns a status in `TERMINAL_STATES`, condition functions raise `pytest.fail` immediately rather than burning the timeout.

## Running the Tests

```bash
# Smoke tests only (~30s)
python -m pytest tests/integration/test_smoke.py -v

# Full pipeline (~15min)
python -m pytest tests/integration/test_pipeline.py -v -s

# All integration tests
python -m pytest tests/integration/ -v

# Skip slow pipeline test
python -m pytest tests/integration/ -m "not slow" -v
```

Requires AWS credentials with:
- `cloudformation:DescribeStacks`
- `cognito-idp:AdminCreateUser`, `AdminSetUserPassword`, `AdminDeleteUser`, `InitiateAuth`

## Error Handling

- If `DfmeaApiStack` not found: all integration tests skip with `pytest.skip("DfmeaApiStack not deployed")`
- If Cognito user creation fails: session fixture raises, all tests error with clear message
- If polling times out: `pytest.fail` with descriptive message including last observed status
- Test user cleanup always runs via fixture finalizer (not `yield` + `try/finally` — use `addfinalizer`)
