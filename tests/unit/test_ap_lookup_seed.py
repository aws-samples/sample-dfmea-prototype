# tests/unit/test_ap_lookup_seed.py
import json, os, pytest

SEED_PATH = os.path.join(os.path.dirname(__file__), "../../ontology/ap_lookup_seed.json")


@pytest.fixture(scope="module")
def seed():
    with open(SEED_PATH) as f:
        return json.load(f)


def test_seed_loads(seed):
    assert isinstance(seed, list)


def test_all_severity_occurrence_detection_covered(seed):
    """AIAG-VDA 2019 uses S 1-10, O 1-10, D 1-10. All 1000 combinations must be present."""
    covered = {(r["severity"], r["occurrence"], r["detection"]) for r in seed}
    missing = [(s, o, d) for s in range(1, 11) for o in range(1, 11) for d in range(1, 11)
               if (s, o, d) not in covered]
    assert not missing, f"Missing {len(missing)} S/O/D combinations"


def test_high_severity_always_high_ap(seed):
    """AIAG-VDA 2019 rule: S=9 or S=10 always -> High AP regardless of O/D."""
    violations = [r for r in seed if r["severity"] in (9, 10) and r["ap"] != "High"]
    assert not violations, f"Severity 9/10 must always be High AP; violations: {violations[:5]}"


def test_ap_values_valid(seed):
    valid = {"High", "Medium", "Low"}
    invalid = [r for r in seed if r["ap"] not in valid]
    assert not invalid, f"Invalid AP values found: {[r['ap'] for r in invalid[:5]]}"


def test_seed_record_schema(seed):
    required_keys = {"severity", "occurrence", "detection", "ap"}
    for r in seed:
        assert required_keys.issubset(r.keys()), f"Record missing keys: {r}"
