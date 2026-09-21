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
