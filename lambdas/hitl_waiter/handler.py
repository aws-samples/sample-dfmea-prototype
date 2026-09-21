"""
lambdas/hitl_waiter/handler.py — Gate 4 waitForTaskToken shim.

Invoked by Step Functions with waitForTaskToken. Stores the task token
in DynamoDB and sets the review status to HITL_PENDING so the UI shows
the approve/reject panel.  The execution stays suspended until
hitl_callback sends send_task_success / send_task_failure.
"""
from __future__ import annotations
import datetime
import os
import boto3

REVIEWS_TABLE = os.environ.get("REVIEWS_TABLE_NAME", "dfmea-reviews")
dynamodb = boto3.resource("dynamodb")


def handler(event: dict, context) -> None:
    review_id  = event.get("review_id", "")
    task_token = event.get("taskToken", "")

    dynamodb.Table(REVIEWS_TABLE).update_item(
        Key={"review_id": review_id},
        UpdateExpression=(
            "SET #s = :s, gate_4_status = :pending, updated_at = :u, "
            "hitl_task_token = :t"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "HITL_PENDING",
            ":pending": "PENDING",
            ":t": task_token,
            ":u": datetime.datetime.now(datetime.UTC).isoformat(),
        },
    )
    print(f"[hitl-waiter] review_id={review_id} → HITL_PENDING, token stored")
    # No return — Lambda stays suspended; SFN resumes via send_task_success/failure
