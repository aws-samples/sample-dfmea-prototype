"""Unit tests for validate_shacl — pyshacl + rdflib implementation."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def test_conforms_on_valid_row():
    """A row with valid S/O/D and correct AP should conform."""
    from agents.shared.tools import validate_shacl
    rows = [{"severity": 5, "occurrence": 3, "detection": 4, "action_priority": "L"}]
    result = validate_shacl(rows)
    assert result["conforms"] is True
    assert result["violations"] == []


def test_violation_on_invalid_ap():
    """A row with action_priority 'High' (not 'H') should fail SHACL."""
    from agents.shared.tools import validate_shacl
    rows = [{"severity": 5, "occurrence": 3, "detection": 4, "action_priority": "High"}]
    result = validate_shacl(rows)
    assert result["conforms"] is False
    assert len(result["violations"]) > 0


def test_empty_rows_conforms():
    """Empty row list produces an empty graph — SHACL should conform (no violations)."""
    from agents.shared.tools import validate_shacl
    result = validate_shacl([])
    assert result["conforms"] is True


def test_returns_dict_not_string():
    """validate_shacl must return a dict, not a JSON string."""
    from agents.shared.tools import validate_shacl
    result = validate_shacl([{"severity": 5, "occurrence": 3, "detection": 4, "action_priority": "M"}])
    assert isinstance(result, dict)
    assert "conforms" in result
    assert "violations" in result
