# dfmea-prototype/tests/unit/test_intake_handler.py
import json, os, sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
os.environ.setdefault("REVIEWS_TABLE_NAME",    "dfmea-reviews")
os.environ.setdefault("PROCESSED_BUCKET_NAME", "dfmea-processed")
os.environ.setdefault("TEXTRACT_SNS_TOPIC_ARN","arn:aws:sns:us-east-1:123:dfmea-textract")
os.environ.setdefault("TEXTRACT_ROLE_ARN",     "arn:aws:iam::123:role/dfmea-textract-role")


def _s3_event(bucket: str, key: str) -> dict:
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


@patch("boto3.resource")
@patch("boto3.client")
def test_json_path_creates_review(mock_client, mock_resource):
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps({"rows": [
            {"part_name": "B-Pillar", "failure_mode": "Fracture",
             "severity": 9, "occurrence": 3, "detection": 5}
        ]}).encode())
    }
    mock_s3.put_object.return_value = {}
    mock_client.return_value = mock_s3

    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.intake import handler
    import importlib; importlib.reload(handler)
    result = handler.handler(_s3_event("dfmea-uploads", "test.json"), None)
    assert result["statusCode"] == 200
    mock_table.put_item.assert_called_once()
    item = mock_table.put_item.call_args[1]["Item"]
    assert item["status"] == "INTAKE_COMPLETE"
    assert item["row_count"] == 1


@patch("boto3.resource")
@patch("boto3.client")
def test_pdf_path_starts_textract_job(mock_client, mock_resource):
    mock_s3 = MagicMock()
    mock_textract = MagicMock()
    mock_textract.start_document_analysis.return_value = {"JobId": "job-abc-123"}

    def side_effect(service, **kwargs):
        if service == "textract":
            return mock_textract
        return mock_s3

    mock_client.side_effect = side_effect
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.intake import handler
    import importlib; importlib.reload(handler)
    result = handler.handler(_s3_event("dfmea-uploads", "test.pdf"), None)
    assert result["statusCode"] == 200
    mock_textract.start_document_analysis.assert_called_once()
    item = mock_table.put_item.call_args[1]["Item"]
    assert item["status"] == "TEXTRACT_IN_PROGRESS"
    assert item["textract_job_id"] == "job-abc-123"


@patch("boto3.resource")
@patch("boto3.client")
def test_pdf_path_stores_review_id_for_callback(mock_client, mock_resource):
    mock_s3 = MagicMock()
    mock_textract = MagicMock()
    mock_textract.start_document_analysis.return_value = {"JobId": "job-xyz"}

    def side_effect(service, **kwargs):
        if service == "textract":
            return mock_textract
        return mock_s3

    mock_client.side_effect = side_effect
    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.intake import handler
    import importlib; importlib.reload(handler)
    handler.handler(_s3_event("dfmea-uploads", "report.pdf"), None)
    item = mock_table.put_item.call_args[1]["Item"]
    assert "review_id" in item
    assert len(item["review_id"]) == 36  # UUID format


@patch("boto3.resource")
@patch("boto3.client")
def test_sfn_invocation_uses_provided_review_id(mock_client, mock_resource):
    """When called as a SFN task (no Records), uses the event review_id."""
    os.environ["UPLOADS_BUCKET_NAME"] = "dfmea-uploads-test"

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps({"rows": [
            {"part_name": "B-Pillar", "failure_mode": "Fracture",
             "severity": 8, "occurrence": 4, "detection": 6}
        ]}).encode())
    }
    mock_s3.put_object.return_value = {}
    mock_client.return_value = mock_s3

    mock_table = MagicMock()
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.intake import handler
    import importlib; importlib.reload(handler)

    review_id = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
    result = handler.handler(
        {"review_id": review_id, "file_key": "uploads/test.json"},
        None,
    )

    # Must return the same review_id
    assert result["review_id"] == review_id
    assert result["row_count"] == 1

    # Must write normalised.json under the SFN review_id
    put_call = mock_s3.put_object.call_args[1]
    assert put_call["Key"] == f"reviews/{review_id}/normalised.json"

    # Must update (not create) the DynamoDB record
    mock_table.update_item.assert_called_once()
    update_kwargs = mock_table.update_item.call_args[1]
    assert update_kwargs["Key"]["review_id"] == review_id
    assert "INTAKE_COMPLETE" in str(update_kwargs["ExpressionAttributeValues"])
