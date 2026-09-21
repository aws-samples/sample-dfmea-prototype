"""tests/unit/test_synthetic_data.py — Validates base docs + variant data integrity."""
import json, os, pytest

BASE_DIR     = os.path.join(os.path.dirname(__file__), "../../sample-data/base")
VARIANTS_DIR = os.path.join(os.path.dirname(__file__), "../../sample-data/variants")
META_PATH    = os.path.join(os.path.dirname(__file__), "../../sample-data/metadata.json")

ASSEMBLIES = ["b_pillar", "door_ring", "roof_rail", "sill_assembly", "strut_tower"]
REQUIRED_ROW_FIELDS = {"item_number", "part_name", "function", "failure_mode",
                       "effect", "severity", "cause", "occurrence", "detection", "action_priority"}
VALID_APS = {"High", "Medium", "Low"}
VALID_VARIANT_TYPES = {"missing_failure_mode", "under_scored", "over_scored",
                       "missing_citation", "structural_gap", "anomalous_sod"}


def _ap(s, o, d):
    if s >= 9: return "High"
    if s >= 5:
        if o >= 4 and d >= 7: return "High"
        if o >= 4 and d <= 6: return "Medium"
        return "Low"
    if o >= 6: return "Medium"
    return "Low"


# ── Base document tests ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def base_docs():
    docs = {}
    for assembly in ASSEMBLIES:
        path = os.path.join(BASE_DIR, f"{assembly}_base.json")
        with open(path) as f:
            docs[assembly] = json.load(f)
    return docs


def test_all_five_base_docs_exist(base_docs):
    assert len(base_docs) == 5


def test_base_docs_have_minimum_rows(base_docs):
    for assembly, doc in base_docs.items():
        assert len(doc["rows"]) >= 100, f"{assembly} has only {len(doc['rows'])} rows"


def test_base_row_required_fields(base_docs):
    for assembly, doc in base_docs.items():
        for row in doc["rows"]:
            missing = REQUIRED_ROW_FIELDS - set(row.keys())
            assert not missing, f"{assembly} row {row.get('item_number')} missing: {missing}"


def test_base_sod_ranges(base_docs):
    for assembly, doc in base_docs.items():
        for row in doc["rows"]:
            assert 1 <= row["severity"]   <= 10, f"severity out of range in {assembly}"
            assert 1 <= row["occurrence"] <= 10, f"occurrence out of range in {assembly}"
            assert 1 <= row["detection"]  <= 10, f"detection out of range in {assembly}"


def test_base_ap_matches_aiag_vda_rules(base_docs):
    for assembly, doc in base_docs.items():
        for row in doc["rows"]:
            expected = _ap(row["severity"], row["occurrence"], row["detection"])
            assert row["action_priority"] == expected, (
                f"{assembly} {row['item_number']}: AP={row['action_priority']} "
                f"but expected {expected} for S={row['severity']},O={row['occurrence']},D={row['detection']}"
            )


# ── Variant tests ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def variant_index():
    """Load summary info for all 150 variants without loading all rows."""
    index = []
    for fname in sorted(os.listdir(VARIANTS_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(VARIANTS_DIR, fname)) as f:
            doc = json.load(f)
        index.append({
            "id": doc["variant_id"],
            "assembly": doc["assembly"],
            "type": doc["variant_type"],
            "row_count": doc["row_count"],
        })
    return index


def test_150_variants_exist(variant_index):
    assert len(variant_index) == 150, f"Expected 150 variants, found {len(variant_index)}"


def test_30_variants_per_assembly(variant_index):
    from collections import Counter
    counts = Counter(v["assembly"] for v in variant_index)
    for assembly in ASSEMBLIES:
        assert counts[assembly] == 30, f"{assembly} has {counts[assembly]} variants, expected 30"


def test_all_six_variant_types_present(variant_index):
    from collections import Counter
    type_counts = Counter(v["type"] for v in variant_index)
    for vtype in VALID_VARIANT_TYPES:
        assert type_counts[vtype] > 0, f"Variant type {vtype} missing"


def test_variants_have_reasonable_row_counts(variant_index):
    for v in variant_index:
        assert v["row_count"] >= 10, f"{v['id']} has only {v['row_count']} rows"
        assert v["row_count"] <= 200, f"{v['id']} has suspiciously many rows: {v['row_count']}"


def test_total_rows_exceeds_15000(variant_index):
    total = sum(v["row_count"] for v in variant_index)
    assert total >= 15000, f"Total rows {total} < 15000"


# ── Metadata tests ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def metadata():
    with open(META_PATH) as f:
        return json.load(f)


def test_metadata_loads(metadata):
    assert isinstance(metadata, list)
    assert len(metadata) > 0


def test_metadata_has_required_keys(metadata):
    required = {"document_id", "assembly", "variant_type", "label"}
    for m in metadata[:100]:   # spot-check first 100
        missing = required - set(m.keys())
        assert not missing, f"Metadata record missing keys: {missing}"


def test_metadata_label_values_valid(metadata):
    valid_labels = {"valid", "gap", "under_scored", "over_scored",
                    "missing_citation", "structural_gap", "bom_gap", "anomalous"}
    for m in metadata:
        assert m["label"] in valid_labels, f"Unknown label: {m['label']}"


def test_metadata_has_all_variant_types(metadata):
    from collections import Counter
    types = Counter(m["variant_type"] for m in metadata)
    for vtype in VALID_VARIANT_TYPES:
        assert types[vtype] > 0, f"No metadata entries for variant type {vtype}"
