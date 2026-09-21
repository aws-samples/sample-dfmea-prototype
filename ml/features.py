"""
ml/features.py — Feature extraction for DFMEA ML models.

Converts DFMEA row dicts into a numeric feature matrix.
Features (7 total):
  0 - severity        int 1-10
  1 - occurrence      int 1-10
  2 - detection       int 1-10
  3 - functional_category  label-encoded
  4 - component_type       label-encoded
  5 - cause_type           label-encoded
  6 - ap_encoded           0=Low, 1=Medium, 2=High
"""
from __future__ import annotations
import numpy as np
from sklearn.preprocessing import LabelEncoder
import joblib

FUNCTIONAL_CATEGORIES = ["structural", "thermal", "corrosion", "fatigue", "dimensional", "chemical"]
COMPONENT_TYPES       = ["panel", "bracket", "joint", "reinforcement", "hinge", "mount"]
CAUSE_TYPES           = ["material", "design", "manufacturing", "environmental", "operational"]
AP_VALUES             = ["Low", "Medium", "High"]


def build_encoders() -> dict[str, LabelEncoder]:
    encoders = {}
    for name, values in [
        ("functional_category", FUNCTIONAL_CATEGORIES),
        ("component_type",      COMPONENT_TYPES),
        ("cause_type",          CAUSE_TYPES),
        ("action_priority",     AP_VALUES),
    ]:
        le = LabelEncoder()
        le.fit(values)
        encoders[name] = le
    return encoders


def encode_rows(rows: list[dict], encoders: dict[str, LabelEncoder]) -> np.ndarray:
    """Return (N, 7) feature matrix from a list of DFMEA row dicts."""
    X = []
    for r in rows:
        fn_cat   = _safe_encode(encoders["functional_category"], r.get("functional_category", "structural"))
        comp     = _safe_encode(encoders["component_type"],      r.get("component_type", "panel"))
        cause    = _safe_encode(encoders["cause_type"],          r.get("cause_type", "design"))
        ap_enc   = _safe_encode(encoders["action_priority"],     r.get("action_priority", "Low"))
        X.append([
            int(r.get("severity",   5)),
            int(r.get("occurrence", 5)),
            int(r.get("detection",  5)),
            fn_cat,
            comp,
            cause,
            ap_enc,
        ])
    return np.array(X, dtype=np.float32)


def _safe_encode(le: LabelEncoder, value: str) -> int:
    """Return encoded int; falls back to 0 for unseen labels."""
    try:
        return int(le.transform([value])[0])
    except ValueError:
        return 0


def save_encoders(encoders: dict, path: str) -> None:
    joblib.dump(encoders, path)


def load_encoders(path: str) -> dict[str, LabelEncoder]:
    return joblib.load(path)
