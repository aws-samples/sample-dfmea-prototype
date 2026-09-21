"""
lambdas/agent_invoker/handler.py — Thin invoker for AgentCore Runtime agents.

Each dfmea-invoke-<specialist> Lambda uses this handler. It calls the agent's
Amazon Bedrock AgentCore Runtime via `invoke_agent_runtime`, forwarding only the
review payload. The Runtime authenticates to Gateway with Cognito M2M credentials,
performs the analysis, and persists findings through the Gateway `write_finding`
tool.

Env vars:
  AGENT_RUNTIME_ARN   ARN of the AgentCore Runtime to invoke (set post-deploy)
  AGENT_LABEL         logical agent name (for logging / session id)
  AWS_REGION          region
"""
from __future__ import annotations
from decimal import Decimal
import json
import os

import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "us-east-1")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-agentcore",
            region_name=REGION,
            config=Config(
                connect_timeout=10,
                # Normal runs observed at ~72s; three minutes leaves headroom
                # without holding a Step Functions branch for eight minutes.
                read_timeout=180,
                # AgentCore may have accepted a request before a read timeout.
                # Retrying that request could produce duplicate findings.
                retries={"mode": "standard", "total_max_attempts": 1},
            ),
        )
    return _client


def _json_default(value):
    """Convert DynamoDB numeric values without stringifying valid numbers."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _session_id(agent_label: str, review_id: str) -> str:
    # AgentCore requires runtimeSessionId length >= 33 chars.
    return f"dfmea-{agent_label}-{review_id}".ljust(33, "-")


def _drain_response(response: dict) -> str:
    """Accumulate text from an invoke_agent_runtime response (stream or body)."""
    body = response.get("response")
    if body is None:
        return ""
    acc = ""
    try:
        # Streaming (SSE-style) response
        for line in body.iter_lines():
            if not line:
                continue
            s = line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else str(line)
            acc += s + "\n"
    except AttributeError:
        try:
            raw = body.read()
            acc = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        except Exception:
            acc = str(body)
    return acc[:4000]


def _raise_for_runtime_error(text: str, agent_label: str) -> None:
    """Detect error objects yielded by an AgentCore entrypoint over SSE."""
    if not text.strip():
        raise RuntimeError(f"{agent_label} AgentCore runtime returned an empty response")

    for raw_line in text.splitlines():
        candidate = raw_line.strip()
        if candidate.startswith("data:"):
            candidate = candidate[5:].strip()
        if not candidate or candidate.startswith("event:"):
            continue
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(
                f"{agent_label} AgentCore runtime failed: {payload['error']}"
            )


def invoke_runtime(agent_label: str, review_id: str,
                   extra_payload: dict | None = None) -> dict:
    runtime_arn = os.environ.get("AGENT_RUNTIME_ARN", "")
    if not runtime_arn:
        raise RuntimeError(
            f"AGENT_RUNTIME_ARN not set for {agent_label} — cannot invoke runtime"
        )

    payload_dict = {"review_id": review_id}
    if extra_payload:
        payload_dict.update(extra_payload)

    try:
        payload = json.dumps(payload_dict, default=_json_default).encode("utf-8")
        response = _get_client().invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=_session_id(agent_label, review_id),
            payload=payload,
            qualifier="DEFAULT",
        )
        text = _drain_response(response)
        _raise_for_runtime_error(text, agent_label)
        print(f"[agent_invoker] {agent_label} review={review_id} runtime completed")
        return {"review_id": review_id, "agent": agent_label, "status": "completed", "result": text}
    except Exception as exc:
        print(f"[agent_invoker] {agent_label} invocation failed: {exc}")
        raise


def handler(event: dict, context) -> dict:
    """Generic specialist invoker Lambda entry point."""
    review_id = event.get("review_id", "")
    agent_label = os.environ.get("AGENT_LABEL", "specialist")
    if not review_id:
        raise ValueError("review_id is required")
    return invoke_runtime(agent_label, review_id)
