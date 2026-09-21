"""Tests that intake requires a complete ML pre-screen."""
import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def _make_sfn_event():
    return {"review_id": "rev-test-001", "file_key": "uploads/test.json"}


def _s3_mock(body: bytes):
    return mock.MagicMock(**{
        "get_object.return_value": {"Body": mock.MagicMock(read=lambda: body)},
        "put_object.return_value": {},
    })


def test_sfn_intake_calls_score_rows(monkeypatch):
    """ML scorer must produce one score for every normalized row."""
    import lambdas.intake.handler as handler

    score_rows = mock.MagicMock(return_value=1)
    monkeypatch.setattr(handler, "s3_client", _s3_mock(
        b'[{"part_name":"P","function":"f","failure_mode":"fm","effect":"e","cause":"c","severity":5,"occurrence":3,"detection":4}]'
    ))
    monkeypatch.setattr(handler, "dynamodb", mock.MagicMock())

    with mock.patch.dict(
        "sys.modules",
        {"agents.shared.ml_scorer": mock.MagicMock(score_rows=score_rows)},
    ):
        result = handler._sfn_intake(_make_sfn_event())

    score_rows.assert_called_once()
    assert score_rows.call_args.args[1] == "rev-test-001"
    assert result["row_count"] == 1


def test_sfn_intake_fails_when_ml_fails(monkeypatch):
    """A model or persistence failure must stop intake."""
    import lambdas.intake.handler as handler

    monkeypatch.setattr(handler, "s3_client", _s3_mock(b'[{"part_name":"P"}]'))
    monkeypatch.setattr(handler, "dynamodb", mock.MagicMock())
    score_rows = mock.MagicMock(side_effect=RuntimeError("no model"))

    with mock.patch.dict(
        "sys.modules",
        {"agents.shared.ml_scorer": mock.MagicMock(score_rows=score_rows)},
    ):
        with pytest.raises(RuntimeError, match="no model"):
            handler._sfn_intake(_make_sfn_event())


def test_sfn_intake_does_not_mark_complete_after_ml_failure(monkeypatch):
    """No review/normalized output may be committed after an ML failure."""
    import lambdas.intake.handler as handler

    s3 = _s3_mock(b'[{"part_name":"P"}]')
    dynamodb = mock.MagicMock()
    monkeypatch.setattr(handler, "s3_client", s3)
    monkeypatch.setattr(handler, "dynamodb", dynamodb)
    score_rows = mock.MagicMock(side_effect=RuntimeError("no model"))

    with mock.patch.dict(
        "sys.modules",
        {"agents.shared.ml_scorer": mock.MagicMock(score_rows=score_rows)},
    ):
        with pytest.raises(RuntimeError, match="no model"):
            handler._sfn_intake(_make_sfn_event())

    s3.put_object.assert_not_called()
    dynamodb.Table.return_value.update_item.assert_not_called()


def test_s3_trigger_path_calls_score_rows():
    """The S3-trigger path also requires one score for its one row."""
    import json
    import lambdas.intake.handler as handler

    score_rows = mock.MagicMock(return_value=1)
    row_bytes = json.dumps([{
        "part_name": "P",
        "function": "f",
        "failure_mode": "fm",
        "effect": "e",
        "cause": "c",
        "severity": 5,
        "occurrence": 3,
        "detection": 4,
    }]).encode()
    event = {
        "Records": [{
            "s3": {
                "bucket": {"name": "dfmea-uploads"},
                "object": {"key": "uploads/test.json"},
            }
        }]
    }

    with mock.patch.object(handler, "s3_client", _s3_mock(row_bytes)), \
         mock.patch.object(handler, "dynamodb", mock.MagicMock()), \
         mock.patch.dict(
             "sys.modules",
             {"agents.shared.ml_scorer": mock.MagicMock(score_rows=score_rows)},
         ):
        handler.handler(event, None)

    score_rows.assert_called_once()
