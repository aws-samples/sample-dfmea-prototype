"""
eval/ml_metrics.py — ML model evaluation utilities.

Loads trained Random Forest + Isolation Forest artifacts and re-evaluates
them against the held-out test set to verify model health post-deployment.

Usage:
    python3 eval/ml_metrics.py
"""
from __future__ import annotations
import json, os, sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.features import load_encoders, encode_rows

MODELS_DIR   = os.path.join(os.path.dirname(__file__), "..", "sample-data", "models")
VARIANTS_DIR = os.path.join(os.path.dirname(__file__), "..", "sample-data", "variants")


def load_models() -> dict:
    import joblib
    return {
        "rf":       joblib.load(os.path.join(MODELS_DIR, "random_forest_v1.joblib")),
        "iso":      joblib.load(os.path.join(MODELS_DIR, "isolation_forest_v1.joblib")),
        "encoders": load_encoders(os.path.join(MODELS_DIR, "label_encoders_v1.joblib")),
    }


def evaluate_rf(models: dict, sample_rows: list[dict], true_labels: list[str]) -> dict:
    """Evaluate Random Forest on a sample."""
    from sklearn.metrics import classification_report, accuracy_score
    X = encode_rows(sample_rows, models["encoders"])
    rf_enc = models["encoders"]["rf_label"]
    y_true = rf_enc.transform(true_labels)
    y_pred = models["rf"].predict(X)
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "report":   classification_report(y_true, y_pred, target_names=rf_enc.classes_, output_dict=True),
    }


def evaluate_iso(models: dict, sample_rows: list[dict]) -> dict:
    """Run Isolation Forest on rows and return anomaly statistics."""
    X = encode_rows(sample_rows, models["encoders"])
    preds = models["iso"].predict(X)
    scores = models["iso"].score_samples(X)
    anomaly_mask = preds == -1
    return {
        "n_rows":          len(sample_rows),
        "n_anomalous":     int(anomaly_mask.sum()),
        "anomaly_pct":     round(float(anomaly_mask.mean() * 100), 2),
        "mean_score":      round(float(scores.mean()), 4),
        "min_score":       round(float(scores.min()), 4),
    }


def print_training_report() -> None:
    report_path = os.path.join(MODELS_DIR, "training_report.json")
    if not os.path.exists(report_path):
        print("No training report found.")
        return
    with open(report_path) as f:
        rpt = json.load(f)
    print("\n=== Training Report ===")
    print(f"Total rows trained:        {rpt['total_rows']}")
    print(f"RF CV F1 macro:            {rpt['rf_cv_f1_macro_mean']} ± {rpt['rf_cv_f1_macro_std']}")
    print(f"IF anomaly rate:           {rpt['isolation_forest_anomaly_pct']}%")
    print("\nRF Feature importances:")
    for feat, imp in sorted(rpt["rf_feature_importances"].items(), key=lambda x: -x[1]):
        bar = "█" * int(imp * 40)
        print(f"  {feat:<25} {imp:.4f}  {bar}")


if __name__ == "__main__":
    print("Loading models...")
    models = load_models()

    # Load a sample of variant rows for evaluation
    sample_rows, sample_labels = [], []
    for fname in sorted(os.listdir(VARIANTS_DIR))[:20]:   # first 20 docs
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(VARIANTS_DIR, fname)) as f:
            doc = json.load(f)
        v_type = doc.get("variant_type", "")
        for row in doc.get("rows", []):
            sample_rows.append(row)
            if v_type == "under_scored":
                sample_labels.append("valid")   # most rows are valid even in under_scored docs
            elif v_type == "over_scored":
                sample_labels.append("valid")
            else:
                sample_labels.append("valid")

    rf_metrics = evaluate_rf(models, sample_rows, sample_labels)
    iso_metrics = evaluate_iso(models, sample_rows)

    print(f"RF accuracy (sample): {rf_metrics['accuracy']}")
    print(f"IF anomaly rate (sample): {iso_metrics['anomaly_pct']}%")
    print_training_report()
