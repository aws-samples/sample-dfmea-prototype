"""
lambdas/agent_invoker/analyst_handler.py — Analyst thin invoker (dispatch).

The analyst Lambda is invoked by the review state machine for TWO stages:

  * S4 synthesis  -> invoke the analyst AgentCore Runtime (LLM), forwarding the
    specialists' findings (read from DynamoDB) and the inbound JWT.
  * S6_REPORT     -> run the deterministic PDF report locally (no LLM), via
    agents/analyst/report.py.

Keeping the report deterministic and in-Lambda preserves authoritative,
reproducible AIAG-VDA output while the reasoning stage runs natively on
AgentCore Runtime.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import boto3
from boto3.dynamodb.conditions import Key

from lambdas.agent_invoker.handler import invoke_runtime

REGION = os.environ.get("AWS_REGION", "us-east-1")
FINDINGS_TABLE = os.environ.get("FINDINGS_TABLE_NAME", "dfmea-analysis-findings")

_dynamo = None


def _get_dynamo():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb", region_name=REGION)
    return _dynamo


def _load_specialist_findings(review_id: str) -> list[dict]:
    """Read all specialist findings already written to DynamoDB."""
    try:
        table = _get_dynamo().Table(FINDINGS_TABLE)
        query_args = {"KeyConditionExpression": Key("review_id").eq(review_id)}
        items: list[dict] = []
        while True:
            resp = table.query(**query_args)
            items.extend(resp.get("Items", []))
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            query_args["ExclusiveStartKey"] = last_key
        # Exclude prior analyst output so synthesis works from specialist input.
        return [item for item in items if item.get("agent") != "analyst"]
    except Exception as exc:
        print(f"[analyst_invoker] findings read failed: {exc}")
        raise RuntimeError("failed to load specialist findings") from exc


def handler(event: dict, context) -> dict:
    """Analyst Lambda entry point — dispatches on stage."""
    review_id = event.get("review_id", "")
    if not review_id:
        raise ValueError("review_id is required")

    if event.get("stage") == "S6_REPORT":
        from agents.analyst.report import run_report
        return run_report(event)

    # S4 synthesis — invoke the analyst AgentCore Runtime. Gateway access is
    # authenticated inside the Runtime with Cognito M2M credentials.
    findings = event.get("findings_from_all_specialists") or _load_specialist_findings(review_id)
    return invoke_runtime(
        "analyst",
        review_id,
        extra_payload={"findings_from_all_specialists": findings},
    )
