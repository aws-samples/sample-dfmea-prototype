# tests/unit/test_ontology.py
import json, os, pytest

ONTOLOGY_PATH = os.path.join(os.path.dirname(__file__), "../../ontology/ontology.json-ld")

REQUIRED_CLASSES = [
    "Component", "Function", "Interface", "FailureMode",
    "Cause", "Effect", "Standard", "Material",
    "ThermalZone", "GeometricRef", "JoiningPattern",
]


@pytest.fixture(scope="module")
def ontology():
    with open(ONTOLOGY_PATH) as f:
        return json.load(f)


def test_ontology_loads(ontology):
    assert isinstance(ontology, dict)


def test_context_present(ontology):
    assert "@context" in ontology


def test_graph_present(ontology):
    assert "@graph" in ontology
    assert isinstance(ontology["@graph"], list)


def test_all_11_classes_defined(ontology):
    class_ids = {
        node.get("@id", "").split("#")[-1].split(":")[-1]
        for node in ontology["@graph"]
        if node.get("@type") == "owl:Class"
    }
    missing = [c for c in REQUIRED_CLASSES if c not in class_ids]
    assert not missing, f"Missing ontology classes: {missing}"


def test_ont_prefix_used(ontology):
    ctx = ontology["@context"]
    assert "ont" in ctx, "Context must define 'ont' prefix"


def test_b_pillar_seed_term_present(ontology):
    """Ontology must include at least one B-Pillar seed instance."""
    labels = [
        node.get("rdfs:label", "")
        for node in ontology["@graph"]
        if "rdfs:label" in node
    ]
    b_pillar_terms = [l for l in labels if "B-Pillar" in l or "b_pillar" in l.lower()]
    assert b_pillar_terms, "No B-Pillar seed terms found in ontology"
