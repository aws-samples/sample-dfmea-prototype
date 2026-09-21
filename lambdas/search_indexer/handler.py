"""
lambdas/search_indexer/handler.py — Index regulatory docs from S3 into AOSS.

Invoked manually or on schedule to refresh the regulatory corpus index.
Uses opensearch-py (Lambda layer) with SigV4 request signing.
"""
from __future__ import annotations
import json
import os

AOSS_ENDPOINT  = os.environ.get("AOSS_ENDPOINT", "")
AOSS_INDEX     = os.environ.get("AOSS_INDEX", "regulatory-docs")
KB_DOCS_BUCKET = os.environ.get("KB_DOCS_BUCKET", "")


def _get_s3():
    import boto3
    return boto3.client("s3")


def _get_opensearch_client():
    """Build opensearch-py client with SigV4 auth (requires opensearch-py in layer)."""
    from opensearchpy import OpenSearch, RequestsHttpConnection
    from requests_aws4auth import AWS4Auth
    import boto3 as _boto3

    session = _boto3.session.Session()
    creds   = session.get_credentials()
    region  = session.region_name or "us-east-1"
    awsauth = AWS4Auth(creds.access_key, creds.secret_key, region, "aoss",
                      session_token=creds.token)
    host    = AOSS_ENDPOINT.replace("https://", "").rstrip("/")
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )


def _list_docs() -> list[str]:
    s3   = _get_s3()
    resp = s3.list_objects_v2(Bucket=KB_DOCS_BUCKET, Prefix="regulatory/")
    return [obj["Key"] for obj in resp.get("Contents", [])]


def _bulk_index(docs: list[dict]) -> dict:
    client = _get_opensearch_client()
    body   = []
    for doc in docs:
        body.append({"index": {"_index": AOSS_INDEX, "_id": doc["key"]}})
        body.append({"key": doc["key"], "content": doc["content"],
                     "source": doc.get("source", "")})
    return client.bulk(body=body)


def handler(event: dict, context) -> dict:
    try:
        s3    = _get_s3()
        resp  = s3.list_objects_v2(Bucket=KB_DOCS_BUCKET, Prefix="regulatory/")
        keys  = [obj["Key"] for obj in resp.get("Contents", [])]
        if not keys:
            return {"statusCode": 200, "body": json.dumps({"indexed": 0})}
        docs  = []
        for key in keys:
            obj     = s3.get_object(Bucket=KB_DOCS_BUCKET, Key=key)
            content = obj["Body"].read().decode("utf-8", errors="replace")
            docs.append({"key": key, "content": content, "source": key})
        result = _bulk_index(docs)
        print(f"[search_indexer] indexed {len(docs)} docs, errors={result.get('errors')}")
        return {"statusCode": 200, "body": json.dumps({"indexed": len(docs)})}
    except Exception as exc:
        print(f"[search_indexer] error: {exc}")
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)})}
