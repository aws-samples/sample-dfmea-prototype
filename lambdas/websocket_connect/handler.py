"""
lambdas/websocket_connect/handler.py — API Gateway WebSocket $connect / $disconnect handler.

Stores and removes connection records in the dfmea-reviews table so that
the fan-out Lambda can target live connections.
"""
from __future__ import annotations
import os
import boto3
from boto3.dynamodb.conditions import Key

REVIEWS_TABLE = os.environ.get("REVIEWS_TABLE_NAME", "dfmea-reviews")
CONNECTIONS_TABLE = os.environ.get("CONNECTIONS_TABLE_NAME", "dfmea-ws-connections")

dynamodb = boto3.resource("dynamodb")


def _connections_table():
    return dynamodb.Table(CONNECTIONS_TABLE)


def handler(event: dict, context) -> dict:
    request_context = event.get("requestContext", {})
    route_key = request_context.get("routeKey", "$connect")
    connection_id = request_context.get("connectionId", "")
    query = event.get("queryStringParameters") or {}
    review_id = query.get("review_id", "")

    table = _connections_table()

    if route_key == "$connect":
        table.put_item(
            Item={
                "connection_id": connection_id,
                "review_id": review_id,
                "ttl": _ttl_hours(8),
            }
        )
        print(f"[ws_connect] CONNECT connection_id={connection_id} review_id={review_id}")
        return {"statusCode": 200}

    if route_key == "$disconnect":
        table.delete_item(Key={"connection_id": connection_id})
        print(f"[ws_connect] DISCONNECT connection_id={connection_id}")
        return {"statusCode": 200}

    return {"statusCode": 400, "body": "Unknown route"}


def _ttl_hours(hours: int) -> int:
    import time
    return int(time.time()) + hours * 3600
