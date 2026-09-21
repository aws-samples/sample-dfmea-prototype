"""
lambdas/websocket_fanout/handler.py — WebSocket fan-out Lambda.

Triggered by DynamoDB Streams on dfmea-reviews table (and optionally
dfmea-analysis-findings). Pushes progress events to all live WebSocket
connections that have subscribed to the relevant review_id.
"""
from __future__ import annotations
import json
import os
import boto3
from botocore.exceptions import ClientError

CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE_NAME", "dfmea-ws-connections")
WEBSOCKET_ENDPOINT = os.environ.get("WEBSOCKET_ENDPOINT", "")  # https://{api-id}.execute-api.{region}.amazonaws.com/{stage}

dynamodb = boto3.resource("dynamodb")


def _get_connections_for_review(review_id: str) -> list[str]:
    table = dynamodb.Table(CONNECTIONS_TABLE)
    resp = table.query(
        IndexName="review_id-index",
        KeyConditionExpression="review_id = :r",
        ExpressionAttributeValues={":r": review_id},
    )
    return [item["connection_id"] for item in resp.get("Items", [])]


def _send_to_connection(apigw, connection_id: str, payload: dict) -> None:
    try:
        apigw.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(payload).encode("utf-8"),
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("GoneException", "410"):
            # Connection is stale; clean it up
            dynamodb.Table(CONNECTIONS_TABLE).delete_item(
                Key={"connection_id": connection_id}
            )
        else:
            print(f"[ws_fanout] post_to_connection error {code}: {e}")


def _process_dynamodb_record(record: dict) -> tuple[str | None, dict | None]:
    """Extract review_id and a progress payload from a DynamoDB Stream record."""
    if record.get("eventName") not in ("INSERT", "MODIFY"):
        return None, None
    new_image = record.get("dynamodb", {}).get("NewImage", {})
    review_id = (new_image.get("review_id") or {}).get("S")
    if not review_id:
        return None, None
    status = (new_image.get("status") or {}).get("S", "")
    payload = {
        "type": "REVIEW_STATUS_CHANGE",
        "review_id": review_id,
        "status": status,
    }
    return review_id, payload


def handler(event: dict, context) -> dict:
    if not WEBSOCKET_ENDPOINT:
        print("[ws_fanout] WEBSOCKET_ENDPOINT not configured — skipping fan-out")
        return {"statusCode": 200}

    endpoint_url = WEBSOCKET_ENDPOINT.rstrip("/")
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=endpoint_url)

    for record in event.get("Records", []):
        review_id, payload = _process_dynamodb_record(record)
        if not review_id or not payload:
            continue
        connection_ids = _get_connections_for_review(review_id)
        for cid in connection_ids:
            _send_to_connection(apigw, cid, payload)
        print(f"[ws_fanout] review_id={review_id} pushed to {len(connection_ids)} connections")

    return {"statusCode": 200}
