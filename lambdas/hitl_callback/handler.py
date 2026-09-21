"""
lambdas/hitl_callback/handler.py — HITL Gate 4 approve/reject callback.

Called by the REST API when a human reviewer approves or rejects a pending
HITL review. Sends the task token back to Step Functions, advancing the
S0-S7 state machine past the Wait state at Gate 4.
"""
from __future__ import annotations
import json
import os
import boto3
from botocore.exceptions import ClientError

sfn = boto3.client("stepfunctions")
dynamodb = boto3.resource("dynamodb")

REVIEWS_TABLE = os.environ.get("REVIEWS_TABLE_NAME", "dfmea-reviews")


def _get_task_token(review_id: str) -> str | None:
    table = dynamodb.Table(REVIEWS_TABLE)
    resp = table.get_item(Key={"review_id": review_id})
    item = resp.get("Item", {})
    return item.get("hitl_task_token")


def _update_review_status(review_id: str, status: str, comment: str) -> None:
    """Persist legacy Gate 4 callbacks in the canonical gate and HITL fields."""
    import datetime
    decision = "APPROVED" if status == "HITL_APPROVED" else "REJECTED"
    table = dynamodb.Table(REVIEWS_TABLE)
    table.update_item(
        Key={"review_id": review_id},
        UpdateExpression=(
            "SET #s = :s, gate_4_status = :d, gate_4_comment = :c, "
            "hitl_decision = :d, hitl_comment = :c, updated_at = :t "
            "REMOVE hitl_task_token"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": status,
            ":d": decision,
            ":c": comment,
            ":t": datetime.datetime.now(datetime.UTC).isoformat(),
        },
    )


def handler(event: dict, context) -> dict:
    """
    event: {review_id, action: 'approve'|'reject', comment?: str}
    Called from the API Gateway REST endpoint POST /reviews/{id}/hitl.
    """
    # Support both direct invocation and API GW proxy events
    if "body" in event and isinstance(event["body"], str):
        body = json.loads(event["body"])
        path_params = event.get("pathParameters") or {}
        review_id = path_params.get("review_id") or body.get("review_id", "")
    else:
        body = event
        review_id = body.get("review_id", "")

    action = (body.get("action", "") or "").lower()
    comment = body.get("comment", "")

    if not review_id or action not in ("approve", "reject"):
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "review_id and action (approve|reject) are required"}),
        }

    task_token = _get_task_token(review_id)
    if not task_token:
        return {
            "statusCode": 404,
            "body": json.dumps({"error": f"No pending HITL task for review_id={review_id}"}),
        }

    try:
        if action == "approve":
            sfn.send_task_success(
                taskToken=task_token,
                output=json.dumps({"hitl_decision": "APPROVED", "comment": comment}),
            )
            _update_review_status(review_id, "HITL_APPROVED", comment)
        else:
            sfn.send_task_failure(
                taskToken=task_token,
                error="HumanRejected",
                cause=comment or "Rejected by human reviewer",
            )
            _update_review_status(review_id, "HITL_REJECTED", comment)

        print(f"[hitl_callback] review_id={review_id} action={action}")
        return {
            "statusCode": 200,
            "body": json.dumps({"review_id": review_id, "action": action}),
        }

    except ClientError as e:
        code = e.response["Error"]["Code"]
        print(f"[hitl_callback] StepFunctions error {code}: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
