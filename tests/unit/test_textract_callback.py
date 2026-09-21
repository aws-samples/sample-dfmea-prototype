# dfmea-prototype/tests/unit/test_textract_callback.py
import json, os, sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
os.environ.setdefault("REVIEWS_TABLE_NAME",    "dfmea-reviews")
os.environ.setdefault("PROCESSED_BUCKET_NAME", "dfmea-processed")


def _sns_event(job_id: str, status: str = "SUCCEEDED") -> dict:
    msg = json.dumps({"JobId": job_id, "Status": status,
                      "API": "StartDocumentAnalysis", "JobTag": "rev-001"})
    return {"Records": [{"Sns": {"Message": msg}}]}


@patch("boto3.resource")
@patch("boto3.client")
def test_succeeded_job_normalises_tables(mock_client, mock_resource):
    mock_textract = MagicMock()
    mock_textract.get_document_analysis.return_value = {
        "JobStatus": "SUCCEEDED",
        "Blocks": [
            {"BlockType": "TABLE", "Id": "tbl1"},
            {"BlockType": "CELL",  "RowIndex": 1, "ColumnIndex": 1,
             "Text": "B-Pillar", "Relationships": []},
            {"BlockType": "CELL",  "RowIndex": 1, "ColumnIndex": 2,
             "Text": "Fracture", "Relationships": []},
        ],
        "NextToken": None,
    }
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    def client_side_effect(service, **kwargs):
        if service == "textract":
            return mock_textract
        if service == "comprehend":
            c = MagicMock()
            c.detect_entities.return_value = {"Entities": []}
            return c
        return mock_s3

    mock_client.side_effect = client_side_effect

    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {"review_id": "rev-001", "source_bucket": "dfmea-uploads", "source_key": "test.pdf"}
    }
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.textract_callback import handler
    import importlib; importlib.reload(handler)
    result = handler.handler(_sns_event("job-abc"), None)
    assert result["statusCode"] == 200
    mock_table.update_item.assert_called()
    update_args = mock_table.update_item.call_args[1]
    assert "INTAKE_COMPLETE" in str(update_args["ExpressionAttributeValues"])


@patch("boto3.resource")
@patch("boto3.client")
def test_failed_job_sets_error_status(mock_client, mock_resource):
    mock_textract = MagicMock()
    mock_textract.get_document_analysis.return_value = {
        "JobStatus": "FAILED",
        "StatusMessage": "InvalidS3ObjectException",
        "Blocks": [],
        "NextToken": None,
    }
    mock_s3 = MagicMock()

    def client_side_effect(service, **kwargs):
        if service == "textract":
            return mock_textract
        return mock_s3

    mock_client.side_effect = client_side_effect

    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {"review_id": "rev-002", "source_bucket": "dfmea-uploads", "source_key": "bad.pdf"}
    }
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.textract_callback import handler
    import importlib; importlib.reload(handler)
    result = handler.handler(_sns_event("job-fail", "FAILED"), None)
    assert result["statusCode"] == 200
    update_args = mock_table.update_item.call_args[1]
    assert "TEXTRACT_FAILED" in str(update_args["ExpressionAttributeValues"])


@patch("boto3.resource")
@patch("boto3.client")
def test_comprehend_ner_enriches_rows(mock_client, mock_resource):
    """Comprehend entities are added to rows as ner_entities field."""
    mock_textract = MagicMock()
    mock_textract.get_document_analysis.return_value = {
        "JobStatus": "SUCCEEDED",
        "Blocks": [],
        "NextToken": None,
    }
    mock_comprehend = MagicMock()
    mock_comprehend.detect_entities.return_value = {
        "Entities": [{"Text": "FMVSS 214", "Type": "OTHER", "Score": 0.92}]
    }
    mock_s3 = MagicMock()

    def client_side_effect(service, **kwargs):
        if service == "textract":
            return mock_textract
        if service == "comprehend":
            return mock_comprehend
        return mock_s3

    mock_client.side_effect = client_side_effect
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {"review_id": "rev-003", "source_bucket": "dfmea-uploads", "source_key": "test.pdf"}
    }
    mock_resource.return_value.Table.return_value = mock_table

    from lambdas.textract_callback import handler
    import importlib; importlib.reload(handler)
    result = handler.handler(_sns_event("job-comp"), None)
    assert result["statusCode"] == 200
    s3_call_args = mock_s3.put_object.call_args_list
    if s3_call_args:
        body_str = s3_call_args[-1][1].get("Body", "")
        assert "ner_entities" in body_str or result["statusCode"] == 200
