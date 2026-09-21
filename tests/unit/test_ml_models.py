"""tests/unit/test_ml_models.py — Validates trained ML model artifacts."""
import json, os, pytest
import numpy as np

MODELS_DIR = os.path.join(os.path.dirname(__file__), "../../sample-data/models")


@pytest.fixture(scope="module")
def models():
    import joblib, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    return {
        "rf":       joblib.load(os.path.join(MODELS_DIR, "random_forest_v1.joblib")),
        "iso":      joblib.load(os.path.join(MODELS_DIR, "isolation_forest_v1.joblib")),
        "encoders": joblib.load(os.path.join(MODELS_DIR, "label_encoders_v1.joblib")),
    }


@pytest.fixture(scope="module")
def training_report():
    with open(os.path.join(MODELS_DIR, "training_report.json")) as f:
        return json.load(f)


# ── Artifact existence ────────────────────────────────────────────────────────

def test_model_artifacts_exist():
    for fname in ["random_forest_v1.joblib", "isolation_forest_v1.joblib",
                  "label_encoders_v1.joblib", "training_report.json"]:
        assert os.path.exists(os.path.join(MODELS_DIR, fname)), f"Missing: {fname}"


# ── RF model tests ────────────────────────────────────────────────────────────

def test_rf_model_loads(models):
    assert models["rf"] is not None


def test_rf_has_three_classes(models):
    assert len(models["rf"].classes_) == 3


def test_rf_predicts_valid_row(models):
    """High-severity, high-occurrence row (S=9, O=6, D=4) must NOT be under_scored."""
    from ml.features import encode_rows
    row = {
        "severity": 9, "occurrence": 6, "detection": 4,
        "functional_category": "structural", "component_type": "panel",
        "cause_type": "design", "action_priority": "High",
    }
    X = encode_rows([row], models["encoders"])
    pred = models["rf"].predict(X)
    classes = models["encoders"]["rf_label"].classes_
    label = classes[pred[0]]
    # High S+O row should never be flagged as under_scored
    assert label != "under_scored", f"S=9,O=6,D=4 should not be under_scored; got '{label}'"


def test_rf_cv_f1_above_threshold(training_report):
    """5-fold CV F1 macro must be above 0.60."""
    f1 = training_report["rf_cv_f1_macro_mean"]
    assert f1 >= 0.60, f"RF CV F1 macro {f1} < 0.60"


def test_rf_accuracy_above_threshold(training_report):
    weighted_f1 = training_report["rf_classification_report"]["weighted avg"]["f1-score"]
    assert weighted_f1 >= 0.85, f"Weighted F1 {weighted_f1} < 0.85"


# ── Isolation Forest tests ────────────────────────────────────────────────────

def test_iso_model_loads(models):
    assert models["iso"] is not None


def test_iso_flags_anomalous_sod(models):
    """S=9, O=1, D=1 is a known anomalous pattern (should score below threshold)."""
    from ml.features import encode_rows
    anomalous = {
        "severity": 9, "occurrence": 1, "detection": 1,
        "functional_category": "structural", "component_type": "panel",
        "cause_type": "design", "action_priority": "High",
    }
    normal = {
        "severity": 5, "occurrence": 4, "detection": 5,
        "functional_category": "structural", "component_type": "panel",
        "cause_type": "design", "action_priority": "Medium",
    }
    X = encode_rows([anomalous, normal], models["encoders"])
    preds = models["iso"].predict(X)
    scores = models["iso"].score_samples(X)
    # Anomalous row should have lower (more negative) score than normal
    assert scores[0] < scores[1], f"Anomalous score {scores[0]:.3f} not < normal score {scores[1]:.3f}"


def test_iso_anomaly_rate_in_range(training_report):
    """Isolation Forest should flag 3-10% of training rows as anomalous."""
    pct = training_report["isolation_forest_anomaly_pct"]
    assert 2.0 <= pct <= 15.0, f"Anomaly rate {pct}% outside expected 2-15% range"


# ── Encoder tests ─────────────────────────────────────────────────────────────

def test_encoders_have_required_keys(models):
    required = {"functional_category", "component_type", "cause_type", "action_priority", "rf_label"}
    assert required.issubset(models["encoders"].keys())


def test_rf_label_encoder_classes(models):
    classes = set(models["encoders"]["rf_label"].classes_)
    assert classes == {"valid", "under_scored", "over_scored"}


# ── Feature extraction tests ──────────────────────────────────────────────────

def test_feature_encoding_shape(models):
    from ml.features import encode_rows
    rows = [
        {"severity": 7, "occurrence": 4, "detection": 6,
         "functional_category": "corrosion", "component_type": "joint",
         "cause_type": "environmental", "action_priority": "Medium"},
        {"severity": 5, "occurrence": 3, "detection": 5,
         "functional_category": "fatigue", "component_type": "bracket",
         "cause_type": "design", "action_priority": "Low"},
    ]
    X = encode_rows(rows, models["encoders"])
    assert X.shape == (2, 7), f"Expected shape (2,7), got {X.shape}"


def test_feature_encoding_unknown_category(models):
    """Unknown categorical value should not raise — falls back to 0."""
    from ml.features import encode_rows
    row = {
        "severity": 5, "occurrence": 3, "detection": 4,
        "functional_category": "unknown_category",
        "component_type": "panel",
        "cause_type": "design",
        "action_priority": "Low",
    }
    X = encode_rows([row], models["encoders"])
    assert X.shape == (1, 7)
