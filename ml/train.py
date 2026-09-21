#!/usr/bin/env python3
"""
ml/train.py — Trains Random Forest (S/O/D validator) and Isolation Forest (anomaly pre-filter).

Reads all variant JSONs + metadata.json, builds feature matrix, trains + evaluates models,
saves artifacts to sample-data/models/.

Usage:
    cd dfmea-prototype
    python3 ml/train.py

Artifacts:
    sample-data/models/random_forest_v1.joblib
    sample-data/models/isolation_forest_v1.joblib
    sample-data/models/label_encoders_v1.joblib
    sample-data/models/training_report.json
"""
from __future__ import annotations
import json, os, sys
import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.preprocessing import LabelEncoder

# Ensure repo root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ml.features import build_encoders, encode_rows, save_encoders

VARIANTS_DIR = os.path.join(os.path.dirname(__file__), "..", "sample-data", "variants")
META_PATH    = os.path.join(os.path.dirname(__file__), "..", "sample-data", "metadata.json")
MODELS_DIR   = os.path.join(os.path.dirname(__file__), "..", "sample-data", "models")
RANDOM_STATE = 42


def _load_all_variant_rows() -> list[dict]:
    rows = []
    for fname in sorted(os.listdir(VARIANTS_DIR)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(VARIANTS_DIR, fname)) as f:
            doc = json.load(f)
        rows.extend(doc.get("rows", []))
    return rows


def _build_rf_dataset(rows: list[dict], metadata: list[dict], encoders: dict) -> tuple[np.ndarray, np.ndarray]:
    """
    Build labelled dataset for Random Forest.
    Labels: valid=0, under_scored=1, over_scored=2.
    Uses metadata to tag rows; rows without a metadata tag default to 'valid'.
    """
    # Build a lookup: (document_id, item_number) -> label
    label_map: dict[tuple, str] = {}
    for m in metadata:
        key = (m.get("document_id", ""), m.get("item_number", ""))
        lbl = m.get("label", "valid")
        if lbl in ("under_scored", "over_scored", "valid"):
            label_map[key] = lbl

    rf_label_encoder = LabelEncoder()
    rf_label_encoder.fit(["valid", "under_scored", "over_scored"])

    # We need rows paired with their document_id — load from variants dir
    paired: list[tuple[dict, str]] = []  # (row, document_id)
    for fname in sorted(os.listdir(VARIANTS_DIR)):
        if not fname.endswith(".json"):
            continue
        doc_id = fname.replace(".json", "")
        with open(os.path.join(VARIANTS_DIR, fname)) as f:
            doc = json.load(f)
        v_type = doc.get("variant_type", "")
        for row in doc.get("rows", []):
            paired.append((row, doc_id, v_type))

    X_list, y_list = [], []
    for row, doc_id, v_type in paired:
        key = (doc_id, row.get("item_number", ""))
        if v_type == "under_scored":
            lbl = label_map.get(key, "valid")
        elif v_type == "over_scored":
            lbl = label_map.get(key, "valid")
        else:
            lbl = "valid"

        feat = encode_rows([row], encoders)[0]
        X_list.append(feat)
        y_list.append(lbl)

    X = np.array(X_list, dtype=np.float32)
    y = rf_label_encoder.transform(y_list)
    return X, y, rf_label_encoder


def train_random_forest(X: np.ndarray, y: np.ndarray) -> RandomForestClassifier:
    rf = RandomForestClassifier(
        n_estimators=200,
        max_depth=10,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    rf.fit(X, y)
    return rf


def train_isolation_forest(X: np.ndarray) -> IsolationForest:
    """Train unsupervised anomaly detector on all rows (anomalous rows are in minority)."""
    iso = IsolationForest(
        n_estimators=200,
        contamination=0.05,   # ~5% of rows are anomalous
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    iso.fit(X)
    return iso


def main() -> None:
    os.makedirs(MODELS_DIR, exist_ok=True)
    print("Loading variant data...")

    with open(META_PATH) as f:
        metadata = json.load(f)

    encoders = build_encoders()
    all_rows = _load_all_variant_rows()
    print(f"  Total rows: {len(all_rows)}")

    # ── Random Forest ──────────────────────────────────────────────────────────
    print("\nBuilding RF training dataset...")
    X, y, rf_label_enc = _build_rf_dataset(all_rows, metadata, encoders)
    print(f"  X shape: {X.shape}, class distribution: {dict(zip(rf_label_enc.classes_, np.bincount(y)))}")

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=RANDOM_STATE, stratify=y)
    print(f"  Train: {len(X_train)}, Test: {len(X_test)}")

    print("Training Random Forest...")
    rf = train_random_forest(X_train, y_train)
    y_pred = rf.predict(X_test)
    rf_report = classification_report(y_test, y_pred, target_names=rf_label_enc.classes_, output_dict=True)
    print(classification_report(y_test, y_pred, target_names=rf_label_enc.classes_))

    cv_scores = cross_val_score(rf, X, y, cv=5, scoring="f1_macro", n_jobs=-1)
    print(f"  5-fold CV F1 macro: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")

    rf_path = os.path.join(MODELS_DIR, "random_forest_v1.joblib")
    joblib.dump(rf, rf_path)
    print(f"  Saved -> {rf_path}")

    # ── Isolation Forest ───────────────────────────────────────────────────────
    print("\nTraining Isolation Forest (unsupervised)...")
    # Use all rows for training (contamination param handles anomaly fraction)
    X_all = encode_rows(all_rows, encoders)
    iso = train_isolation_forest(X_all)

    iso_path = os.path.join(MODELS_DIR, "isolation_forest_v1.joblib")
    joblib.dump(iso, iso_path)
    print(f"  Saved -> {iso_path}")

    # Sanity check: anomaly ratio
    preds = iso.predict(X_all)
    anomaly_count = (preds == -1).sum()
    print(f"  Isolation Forest flags {anomaly_count}/{len(X_all)} rows as anomalous ({anomaly_count/len(X_all)*100:.1f}%)")

    # ── Encoders + RF label encoder ────────────────────────────────────────────
    encoders["rf_label"] = rf_label_enc
    enc_path = os.path.join(MODELS_DIR, "label_encoders_v1.joblib")
    save_encoders(encoders, enc_path)
    print(f"  Saved encoders -> {enc_path}")

    # ── Training report ────────────────────────────────────────────────────────
    report = {
        "total_rows": len(all_rows),
        "rf_train_rows": len(X_train),
        "rf_test_rows":  len(X_test),
        "rf_cv_f1_macro_mean": round(float(cv_scores.mean()), 4),
        "rf_cv_f1_macro_std":  round(float(cv_scores.std()), 4),
        "rf_classification_report": rf_report,
        "isolation_forest_anomaly_count": int(anomaly_count),
        "isolation_forest_anomaly_pct": round(float(anomaly_count / len(X_all) * 100), 2),
        "feature_names": ["severity", "occurrence", "detection", "functional_category", "component_type", "cause_type", "action_priority"],
        "rf_feature_importances": {
            name: round(float(imp), 4)
            for name, imp in zip(
                ["severity", "occurrence", "detection", "functional_category", "component_type", "cause_type", "action_priority"],
                rf.feature_importances_
            )
        },
    }
    report_path = os.path.join(MODELS_DIR, "training_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nTraining report -> {report_path}")
    print("\nDone.")


if __name__ == "__main__":
    main()
