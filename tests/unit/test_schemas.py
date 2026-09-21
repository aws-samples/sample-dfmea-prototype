# tests/unit/test_schemas.py
import json, os, jsonschema, pytest

SCHEMAS_DIR = os.path.join(os.path.dirname(__file__), "../../schemas")


@pytest.fixture
def dfmea_row_schema():
    with open(os.path.join(SCHEMAS_DIR, "dfmea-row.json")) as f:
        return json.load(f)


@pytest.fixture
def finding_schema():
    with open(os.path.join(SCHEMAS_DIR, "finding.json")) as f:
        return json.load(f)


def test_dfmea_row_schema_valid_json(dfmea_row_schema):
    assert "$schema" in dfmea_row_schema


def test_dfmea_row_required_fields(dfmea_row_schema):
    required = dfmea_row_schema.get("required", [])
    for field in ["item_number", "part_name", "function", "failure_mode", "effect", "severity", "cause", "occurrence", "detection"]:
        assert field in required, f"Required field missing: {field}"


def test_valid_dfmea_row_validates(dfmea_row_schema):
    valid_row = {
        "item_number": "1.1",
        "part_name": "B-Pillar Inner Panel",
        "function": "Maintain side-impact load path",
        "failure_mode": "Buckling under lateral load",
        "effect": "Reduced occupant protection",
        "severity": 9,
        "cause": "Insufficient material thickness",
        "occurrence": 4,
        "detection": 6,
        "action_priority": "High",
        "current_prevention_controls": "Material spec review",
        "current_detection_controls": "Dimensional inspection"
    }
    jsonschema.validate(valid_row, dfmea_row_schema)  # must not raise


def test_invalid_severity_rejected(dfmea_row_schema):
    bad_row = {
        "item_number": "1.1", "part_name": "X", "function": "Y",
        "failure_mode": "Z", "effect": "W",
        "severity": 11,  # invalid: must be 1-10
        "cause": "C", "occurrence": 5, "detection": 5,
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad_row, dfmea_row_schema)


def test_finding_schema_valid_json(finding_schema):
    assert "$schema" in finding_schema


def test_valid_finding_validates(finding_schema):
    valid_finding = {
        "finding_id": "f-001",
        "review_id": "review-2026-06-15-b-pillar",
        "agent": "failure_mode",
        "finding_type": "missing_failure_mode",
        "description": "No failure mode covering corrosion at weld joint",
        "affected_component": "B-Pillar Inner Panel",
        "suggested_failure_mode": "Corrosion at spot weld",
        "severity_suggestion": 7,
        "occurrence_suggestion": 5,
        "detection_suggestion": 6,
        "action_priority": "High",
        "evidence_ids": ["ev-001", "ev-002"],
        "confidence": 0.87,
        "gap_source": "kb"
    }
    jsonschema.validate(valid_finding, finding_schema)
