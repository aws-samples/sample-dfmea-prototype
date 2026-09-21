# dfmea-prototype/tests/unit/test_search_indexer.py
import os, sys, json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
os.environ.setdefault("AOSS_ENDPOINT", "https://test.aoss.us-east-1.amazonaws.com")
os.environ.setdefault("AOSS_INDEX",    "regulatory-docs")
os.environ.setdefault("KB_DOCS_BUCKET", "dfmea-kb-documents-123")


@patch("boto3.client")
def test_index_single_document(mock_boto):
    mock_s3 = MagicMock()
    mock_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "regulatory/fmvss-214.txt"}]
    }
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: b"Side impact protection standard text")
    }
    mock_boto.return_value = mock_s3

    with patch("lambdas.search_indexer.handler._bulk_index") as mock_index:
        mock_index.return_value = {"errors": False, "items": []}
        from lambdas.search_indexer import handler
        result = handler.handler({}, None)
        assert result["statusCode"] == 200
        mock_index.assert_called_once()
        docs = mock_index.call_args[0][0]
        assert len(docs) == 1
        assert docs[0]["key"] == "regulatory/fmvss-214.txt"


@patch("boto3.client")
def test_empty_bucket_returns_200(mock_boto):
    mock_s3 = MagicMock()
    mock_s3.list_objects_v2.return_value = {"Contents": []}
    mock_boto.return_value = mock_s3

    from lambdas.search_indexer import handler
    result = handler.handler({}, None)
    assert result["statusCode"] == 200


@patch("boto3.client")
def test_index_error_returns_500(mock_boto):
    mock_s3 = MagicMock()
    mock_s3.list_objects_v2.side_effect = Exception("S3 access denied")
    mock_boto.return_value = mock_s3

    from lambdas.search_indexer import handler
    result = handler.handler({}, None)
    assert result["statusCode"] == 500
