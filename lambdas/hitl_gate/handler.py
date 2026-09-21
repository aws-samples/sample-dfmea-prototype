"""
lambdas/hitl_gate/handler.py — Generalised HITL gate handler for gates 1-4.

Receives an approve/reject decision from a human reviewer, resolves the
corresponding Step Functions task token from DynamoDB, sends the outcome back
to the state machine, and persists the decision to the reviews table.

Gate token fields in DynamoDB:
  Gate 1 → gate_1_task_token
  Gate 2 → gate_2_task_token
  Gate 3 → gate_3_task_token
  Gate 4 → hitl_task_token
"""
from __future__ import annotations

import datetime
import json
import os

import boto3
from botocore.exceptions import ClientError

REVIEWS_TABLE = os.environ.get("REVIEWS_TABLE_NAME", "dfmea-reviews")

VALID_GATES = {1, 2, 3, 4}

_GATE_TOKEN_FIELD: dict[int, str] = {
    1: "gate_1_task_token",
    2: "gate_2_task_token",
    3: "gate_3_task_token",
    4: "hitl_task_token",
}

_GATE_STATUS_FIELD: dict[int, str] = {
    1: "gate_1_status",
    2: "gate_2_status",
    3: "gate_3_status",
    4: "gate_4_status",
}


# ---------------------------------------------------------------------------
# Lazy getters — keep module-level state out of the way of unit-test patches.
# ---------------------------------------------------------------------------

def _get_sfn():
    return boto3.client("stepfunctions")


def _get_dynamodb():
    return boto3.resource("dynamodb")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _err(message: str, status: int = 400) -> dict:
    return {"statusCode": status, "body": json.dumps({"error": message})}


def _get_review_item(review_id: str) -> dict:
    """Return the full DynamoDB item for review_id."""
    table = _get_dynamodb().Table(REVIEWS_TABLE)
    resp = table.get_item(Key={"review_id": review_id})
    return resp.get("Item", {})


def _get_task_token(review_id: str, gate: int) -> str | None:
    """Return the SFN task token for *gate* stored on *review_id*, or None."""
    item = _get_review_item(review_id)
    token_field = _GATE_TOKEN_FIELD[gate]
    return item.get(token_field)


def _write_gate_decision(
    review_id: str, gate: int, decision: str, comment: str
) -> None:
    """Persist one canonical decision and remove its consumed task token."""
    table = _get_dynamodb().Table(REVIEWS_TABLE)
    status_field = _GATE_STATUS_FIELD[gate]
    token_field = _GATE_TOKEN_FIELD[gate]
    now = datetime.datetime.now(datetime.UTC).isoformat()

    set_parts = ["#gs = :decision", "#gc = :comment", "updated_at = :ts"]
    names = {
        "#gs": status_field,
        "#gc": f"gate_{gate}_comment",
        "#token": token_field,
    }
    values = {
        ":decision": decision,
        ":comment": comment,
        ":ts": now,
    }

    # Gate 4 historically used hitl_* fields while the generalized endpoint
    # used gate_4_* fields. Keep both synchronized so every reader sees one
    # decision and cannot ask the reviewer to approve the same gate again.
    if gate == 4:
        set_parts.extend(["hitl_decision = :decision", "hitl_comment = :comment"])

    # Persist a real, review-level milestone immediately after the callback so
    # the UI can explain expensive work instead of showing a simulated timer.
    names["#review_status"] = "status"
    if decision == "APPROVED":
        values[":review_status"] = {
            1: "CAD_RUNNING",
            2: "AGENTS_RUNNING",
            3: "SYNTHESIS_RUNNING",
            4: "REPORT_GENERATING",
        }[gate]
    else:
        values[":review_status"] = "HITL_REJECTED" if gate == 4 else "FAILED"
    set_parts.append("#review_status = :review_status")

    table.update_item(
        Key={"review_id": review_id},
        UpdateExpression=f"SET {', '.join(set_parts)} REMOVE #token",
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    """
    Expected body (JSON):
      {
        "review_id": "rev-xxx",
        "gate":      1 | 2 | 3 | 4,
        "action":    "approve" | "reject",
        "comment":   "optional text"
      }
    """
    # Parse body — may be a JSON string (API GW proxy) or already a dict.
    if "body" in event and isinstance(event["body"], str):
        body = json.loads(event["body"])
    elif "body" in event and isinstance(event["body"], dict):
        body = event["body"]
    else:
        body = event

    review_id = body.get("review_id", "")
    gate_raw = body.get("gate")
    action = (body.get("action", "") or "").lower()
    comment = body.get("comment", "")

    # Validate gate
    try:
        gate = int(gate_raw)
    except (TypeError, ValueError):
        return _err(f"gate must be one of {sorted(VALID_GATES)}, got: {gate_raw!r}")

    if gate not in VALID_GATES:
        return _err(f"gate must be one of {sorted(VALID_GATES)}, got: {gate}")

    # Validate action
    if action not in ("approve", "reject"):
        return _err("action must be 'approve' or 'reject'")

    # Validate review_id presence
    if not review_id:
        return _err("review_id is required")

    # Pre-flight: if the gate is already decided, return 200 immediately.
    # This prevents double-submit errors when the UI has stale state.
    item = _get_review_item(review_id)
    current_gate_status = item.get(_GATE_STATUS_FIELD[gate], "NOT_STARTED")
    if current_gate_status in ("APPROVED", "REJECTED"):
        print(f"[hitl_gate] review_id={review_id} gate={gate} already {current_gate_status} — no-op")
        return {
            "statusCode": 200,
            "body": json.dumps({"review_id": review_id, "gate": gate, "action": action, "note": "already decided"}),
        }

    token_field = _GATE_TOKEN_FIELD[gate]
    task_token = item.get(token_field)
    if not task_token:
        return _err(
            f"No pending task token for review_id={review_id} gate={gate}",
            status=404,
        )

    sfn = _get_sfn()

    try:
        if action == "approve":
            decision = "APPROVED"
            sfn.send_task_success(
                taskToken=task_token,
                output=json.dumps({"review_id": review_id, "gate": gate, "decision": decision, "comment": comment}),
            )
        else:
            decision = "REJECTED"
            sfn.send_task_failure(
                taskToken=task_token,
                error="HumanRejected",
                cause=comment,
            )

        _write_gate_decision(review_id, gate, decision, comment)

        print(f"[hitl_gate] review_id={review_id} gate={gate} action={action}")
        return {
            "statusCode": 200,
            "body": json.dumps({"review_id": review_id, "gate": gate, "action": action}),
        }

    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        # Token already consumed or execution moved on — treat as success since
        # the gate was previously processed.
        if code in ("TaskTimedOut", "TaskDoesNotExist", "InvalidToken"):
            print(f"[hitl_gate] SFN token stale ({code}) for review_id={review_id} gate={gate} — writing decision anyway")
            _write_gate_decision(review_id, gate, "APPROVED" if action == "approve" else "REJECTED", comment)
            return {
                "statusCode": 200,
                "body": json.dumps({"review_id": review_id, "gate": gate, "action": action, "note": f"sfn token stale: {code}"}),
            }
        print(f"[hitl_gate] StepFunctions error {code}: {exc}")
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)})}
