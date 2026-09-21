"""Unit tests for the required DFMEA ML pre-screen."""
import json
import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import agents.shared.ml_scorer as ms


class _Encoder:
    def __init__(self, values):
        self.values = values

    def transform(self, values):
        value = values[0]
        if value not in self.values:
            raise ValueError(value)
        return [self.values.index(value)]


def _encoders():
    return {
        "functional_category": _Encoder(["structural", "thermal", "corrosion", "fatigue", "dimensional"]),
        "component_type": _Encoder(["panel", "bracket", "joint", "reinforcement", "hinge", "mount"]),
        "cause_type": _Encoder(["material", "design", "manufacturing", "environmental", "operational"]),
        "action_priority": _Encoder(["Low", "Medium", "High"]),
    }


def test_derive_features_maps_keywords():
    row = {
        "severity": 7,
        "occurrence": 5,
        "detection": 3,
        "function": "support the body structure",
        "part_name": "B-Pillar_OuterPanel",
        "cause": "material fatigue",
        "action_priority": "M",
    }
    features = ms._derive_features(row, _encoders())
    assert features[:3] == [7.0, 5.0, 3.0]
    assert features[3] == 0.0  # structural
    assert features[4] == 0.0  # panel
    assert features[5] == 0.0  # material
    assert features[6] == 1.0  # Medium


def test_derive_features_unknown_keywords_use_training_defaults():
    row = {
        "severity": 1,
        "occurrence": 1,
        "detection": 1,
        "function": "unknown",
        "part_name": "unknown",
        "cause": "unknown",
    }
    features = ms._derive_features(row, _encoders())
    assert features[3:7] == [0.0, 0.0, 1.0, 0.0]


def test_score_rows_propagates_model_error():
    with mock.patch.object(ms, "_load_models", side_effect=RuntimeError("no models bucket")):
        with pytest.raises(RuntimeError, match="no models bucket"):
            ms.score_rows([{"severity": 5}], "rev-001")


def test_score_rows_propagates_dynamo_error():
    import numpy as np

    rf_mock = mock.MagicMock()
    rf_mock.predict.return_value = ["valid"]
    iso_mock = mock.MagicMock()
    iso_mock.predict.return_value = np.array([1])
    models = {"rf_classifier": rf_mock, "isolation_forest": iso_mock, "encoders": {}}
    with mock.patch.object(ms, "_load_models", return_value=models), \
         mock.patch.object(ms, "_get_dynamo", side_effect=RuntimeError("dynamo down")):
        with pytest.raises(RuntimeError, match="dynamo down"):
            ms.score_rows([{"severity": 5, "occurrence": 3, "detection": 4}], "rev-002")


def test_get_ml_scores_paginates():
    pages = [
        {
            "Items": [{"review_id": "r1", "finding_id": "f1", "rf_label": "valid", "anomaly": False}],
            "LastEvaluatedKey": {"review_id": "r1", "finding_id": "f1"},
        },
        {
            "Items": [{"review_id": "r1", "finding_id": "f2", "rf_label": "under_scored", "anomaly": True}],
        },
    ]
    dynamo_mock = mock.MagicMock()
    dynamo_mock.Table.return_value.query.side_effect = pages
    with mock.patch.object(ms, "_get_dynamo", return_value=dynamo_mock):
        result = json.loads(ms.get_ml_scores("r1"))
    assert result["count"] == 2
    assert {score["finding_id"] for score in result["scores"]} == {"f1", "f2"}


def test_get_ml_scores_error_path():
    with mock.patch.object(ms, "_get_dynamo", side_effect=Exception("table not found")):
        result = json.loads(ms.get_ml_scores("r-bad"))
    assert result["count"] == 0
    assert "error" in result
