"""
agents/shared/ml_scorer.py — ML pre-screening for DFMEA rows.

Lazy-loads trained RF (valid/under_scored/over_scored) and IsolationForest
(anomaly detection) from S3, derives features from normalised rows, writes
scores to dfmea-risk-scores DynamoDB table.
"""
from __future__ import annotations
import io
import json
import logging
import os

import boto3

log = logging.getLogger(__name__)

ML_MODELS_BUCKET = os.environ.get("ML_MODELS_BUCKET_NAME", "")
RISK_SCORES_TABLE = os.environ.get("RISK_SCORES_TABLE_NAME", "dfmea-risk-scores")
REGION = os.environ.get("AWS_REGION", "us-east-1")

# Strands @tool decorator — graceful no-op when strands not installed
try:
    from strands import tool
except ImportError:
    def tool(fn):  # type: ignore[misc]
        return fn

_models: dict = {}
_s3 = None
_dynamo = None


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _get_dynamo():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb", region_name=REGION)
    return _dynamo


def _load_models() -> dict:
    """Lazy-load RF + IsolationForest + LabelEncoders from S3. Cached after first load."""
    global _models
    if _models:
        return _models
    import joblib

    if not ML_MODELS_BUCKET:
        raise RuntimeError("ML_MODELS_BUCKET_NAME not set")

    loaded = {}
    for s3_key, model_key in (
        ("random_forest_v1.joblib",    "rf_classifier"),
        ("isolation_forest_v1.joblib", "isolation_forest"),
        ("label_encoders_v1.joblib",   "encoders"),
    ):
        buf = io.BytesIO()
        _get_s3().download_fileobj(ML_MODELS_BUCKET, s3_key, buf)
        buf.seek(0)
        loaded[model_key] = joblib.load(buf)
    _models = loaded
    return _models


# Keyword → category mappings aligned with ml/features.py training values
_FUNCTIONAL_CATEGORY_MAP = [
    ("thermal", "thermal"),
    ("corros",  "corrosion"),
    ("fatigue", "fatigue"),
    ("dimensi", "dimensional"),
    ("support", "structural"),
    ("load",    "structural"),
    ("seal",    "structural"),
]
_COMPONENT_TYPE_MAP = [
    ("panel",         "panel"),
    ("bracket",       "bracket"),
    ("joint",         "joint"),
    ("reinforcement", "reinforcement"),
    ("hinge",         "hinge"),
    ("mount",         "mount"),
]
_CAUSE_TYPE_MAP = [
    ("corros",   "material"),
    ("material", "material"),
    ("design",   "design"),
    ("manufactur", "manufacturing"),
    ("environ",  "environmental"),
    ("operat",   "operational"),
]


def _safe_le_encode(encoders: dict, name: str, value: str) -> int:
    """Encode a categorical value with the stored LabelEncoder; returns 0 on unseen labels."""
    le = encoders.get(name)
    if le is None:
        return 0
    try:
        return int(le.transform([value])[0])
    except ValueError:
        return 0


def _derive_features(row: dict, encoders: dict) -> list[float]:
    """Build 7-feature vector matching the schema used during training (ml/features.py)."""
    def _match(text: str, mapping: list, default: str) -> str:
        text_lower = (text or "").lower()
        for keyword, category in mapping:
            if keyword in text_lower:
                return category
        return default

    fn_cat   = _match(row.get("function", ""),   _FUNCTIONAL_CATEGORY_MAP, "structural")
    comp     = _match(row.get("part_name", ""),   _COMPONENT_TYPE_MAP,      "panel")
    cause    = _match(row.get("cause", ""),       _CAUSE_TYPE_MAP,          "design")
    ap_raw   = str(row.get("action_priority") or "Low").capitalize()
    if ap_raw not in ("Low", "Medium", "High"):
        ap_raw = {"L": "Low", "M": "Medium", "H": "High"}.get(ap_raw[:1].upper(), "Low")

    return [
        float(int(row.get("severity",   5) or 5)),
        float(int(row.get("occurrence", 5) or 5)),
        float(int(row.get("detection",  5) or 5)),
        float(_safe_le_encode(encoders, "functional_category", fn_cat)),
        float(_safe_le_encode(encoders, "component_type",      comp)),
        float(_safe_le_encode(encoders, "cause_type",          cause)),
        float(_safe_le_encode(encoders, "action_priority",     ap_raw)),
    ]


def score_rows(rows: list[dict], review_id: str) -> int:
    """Run ML pre-screening, persist every score, and return the score count.

    Model loading, inference, or persistence failures propagate so intake cannot
    mark a review complete without its required ML pre-screen.
    """
    if not rows:
        log.info("ML pre-screen completed review_id=%s rows=0", review_id)
        return 0

    import datetime
    import numpy as np

    models = _load_models()
    rf = models["rf_classifier"]
    iso = models["isolation_forest"]
    encoders = models["encoders"]

    feature_matrix = [_derive_features(row, encoders) for row in rows]
    X = np.array(feature_matrix, dtype=np.float32)

    rf_preds = rf.predict(X)   # "valid" / "under_scored" / "over_scored"
    iso_preds = iso.predict(X)  # 1=normal, -1=anomaly

    # rf returns integer indices — decode with rf_label encoder if stored
    rf_label_enc = encoders.get("rf_label")
    if rf_label_enc is not None and hasattr(rf_label_enc, "inverse_transform"):
        rf_labels = rf_label_enc.inverse_transform(rf_preds)
    else:
        rf_labels = [str(prediction) for prediction in rf_preds]

    scored_at = datetime.datetime.now(datetime.UTC).isoformat()
    table = _get_dynamo().Table(RISK_SCORES_TABLE)
    with table.batch_writer() as batch:
        for index, row in enumerate(rows):
            finding_id = row.get("row_id") or row.get("finding_id") or f"row_{index}"
            batch.put_item(Item={
                "review_id": review_id,
                "finding_id": finding_id,
                "rf_label": str(rf_labels[index]),
                "anomaly": bool(iso_preds[index] == -1),
                "model_version": "v1",
                "scored_at": scored_at,
            })

    log.info("ML pre-screen completed review_id=%s rows=%d model_version=v1", review_id, len(rows))
    return len(rows)


@tool
def get_ml_scores(review_id: str) -> str:
    """Retrieve every paginated ML pre-screen score for a review."""
    try:
        from boto3.dynamodb.conditions import Key

        table = _get_dynamo().Table(RISK_SCORES_TABLE)
        query_args = {"KeyConditionExpression": Key("review_id").eq(review_id)}
        items: list[dict] = []
        while True:
            response = table.query(**query_args)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query_args["ExclusiveStartKey"] = last_key
        return json.dumps({"review_id": review_id, "scores": items, "count": len(items)})
    except Exception as exc:
        log.warning("get_ml_scores failed review_id=%s: %s", review_id, exc)
        return json.dumps({"review_id": review_id, "scores": [], "count": 0, "error": str(exc)})
