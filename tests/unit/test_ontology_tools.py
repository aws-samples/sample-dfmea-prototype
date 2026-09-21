"""Unit tests for ontology_tools @tool wrappers."""
import sys, os, json, unittest.mock as mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import agents.shared.ontology_client as oc
import agents.shared.ontology_tools as ot


def test_get_component_failure_modes_calls_client():
    with mock.patch.object(oc, "get_failure_modes_for_component", return_value=[{"name": "buckling", "label": "Buckling"}]) as m:
        result = json.loads(ot.get_component_failure_modes("B-Pillar_InnerPanel"))
    m.assert_called_once_with("B-Pillar_InnerPanel")
    assert result["count"] == 1
    assert result["failure_modes"][0]["name"] == "buckling"


def test_get_component_failure_modes_empty():
    with mock.patch.object(oc, "get_failure_modes_for_component", return_value=[]):
        result = json.loads(ot.get_component_failure_modes("Unknown"))
    assert result["count"] == 0
    assert result["failure_modes"] == []


def test_get_component_standards_calls_client():
    with mock.patch.object(oc, "get_standards_for_component", return_value=[{"standard": "FMVSS_214", "clause": "S5"}]) as m:
        result = json.loads(ot.get_component_standards("B-Pillar_InnerPanel"))
    m.assert_called_once_with("B-Pillar_InnerPanel")
    assert result["count"] == 1
    assert result["standards"][0]["standard"] == "FMVSS_214"


def test_get_ontology_subclasses_calls_client():
    with mock.patch.object(oc, "get_subclasses", return_value=[{"name": "BucklingMode", "label": "Buckling Mode"}]) as m:
        result = json.loads(ot.get_ontology_subclasses("FailureMode"))
    m.assert_called_once_with("FailureMode")
    assert result["count"] == 1
    assert result["subclasses"][0]["name"] == "BucklingMode"


def test_all_functions_return_strings():
    """All @tool wrappers must return JSON strings, not dicts."""
    with mock.patch.object(oc, "get_failure_modes_for_component", return_value=[]):
        r1 = ot.get_component_failure_modes("X")
    with mock.patch.object(oc, "get_standards_for_component", return_value=[]):
        r2 = ot.get_component_standards("X")
    with mock.patch.object(oc, "get_subclasses", return_value=[]):
        r3 = ot.get_ontology_subclasses("X")
    assert isinstance(r1, str) and isinstance(r2, str) and isinstance(r3, str)
