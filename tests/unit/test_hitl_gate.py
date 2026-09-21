# dfmea-prototype/tests/unit/test_hitl_gate.py
import json, os, sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
os.environ.setdefault("REVIEWS_TABLE_NAME", "dfmea-reviews")


def _event(review_id: str, gate: int, action: str, comment: str = "") -> dict:
    return {
        "body": json.dumps({
            "review_id": review_id,
            "gate":      gate,
            "action":    action,
            "comment":   comment,
        })
    }


@patch("boto3.client")
@patch("boto3.resource")
def test_approve_gate_1_sends_task_success(mock_resource, mock_sfn_client):
    mock_sfn = MagicMock()
    mock_sfn.send_task_success.return_value = {}
    mock_sfn_client.return_value = mock_sfn

    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {"review_id": "rev-001", "gate_1_task_token": "tok-aaa"}
    }
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.hitl_gate import handler
    import importlib; importlib.reload(handler)
    result = handler.handler(_event("rev-001", 1, "approve", "Looks good"), None)
    assert result["statusCode"] == 200
    mock_sfn.send_task_success.assert_called_once_with(
        taskToken="tok-aaa",
        output=json.dumps({"review_id": "rev-001", "gate": 1, "decision": "APPROVED", "comment": "Looks good"}),
    )


@patch("boto3.client")
@patch("boto3.resource")
def test_reject_gate_2_sends_task_failure(mock_resource, mock_sfn_client):
    mock_sfn = MagicMock()
    mock_sfn.send_task_failure.return_value = {}
    mock_sfn_client.return_value = mock_sfn

    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {"review_id": "rev-002", "gate_2_task_token": "tok-bbb"}
    }
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.hitl_gate import handler
    import importlib; importlib.reload(handler)
    result = handler.handler(_event("rev-002", 2, "reject", "Missing component"), None)
    assert result["statusCode"] == 200
    mock_sfn.send_task_failure.assert_called_once_with(
        taskToken="tok-bbb",
        error="HumanRejected",
        cause="Missing component",
    )


@patch("boto3.client")
@patch("boto3.resource")
def test_invalid_gate_number_returns_400(mock_resource, mock_sfn_client):
    mock_resource.return_value.Table.return_value = MagicMock()
    from lambdas.hitl_gate import handler
    import importlib; importlib.reload(handler)
    result = handler.handler(_event("rev-003", 9, "approve"), None)
    assert result["statusCode"] == 400


@patch("boto3.client")
@patch("boto3.resource")
def test_missing_task_token_returns_404(mock_resource, mock_sfn_client):
    mock_table = MagicMock()
    mock_table.get_item.return_value = {"Item": {"review_id": "rev-004"}}  # no token
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.hitl_gate import handler
    import importlib; importlib.reload(handler)
    result = handler.handler(_event("rev-004", 3, "approve"), None)
    assert result["statusCode"] == 404


@patch("boto3.client")
@patch("boto3.resource")
def test_gate_status_written_to_ddb(mock_resource, mock_sfn_client):
    mock_sfn = MagicMock()
    mock_sfn.send_task_success.return_value = {}
    mock_sfn_client.return_value = mock_sfn

    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {"review_id": "rev-005", "gate_3_task_token": "tok-ccc"}
    }
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.hitl_gate import handler
    import importlib; importlib.reload(handler)
    handler.handler(_event("rev-005", 3, "approve", "All findings accepted"), None)
    mock_table.update_item.assert_called_once()
    update_args = mock_table.update_item.call_args[1]
    expr_vals   = update_args["ExpressionAttributeValues"]
    assert any("APPROVED" in str(v) for v in expr_vals.values())
