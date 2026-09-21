"""
agents/shared/runtime_common.py — shared helpers for AgentCore Runtime agents.

Each specialist agent runs inside an AgentCore Runtime as a Strands Agent whose
tools are loaded from the AgentCore Gateway (MCP). The agent is instructed to:
  1. call `get_review_context` (and other read tools) to gather DFMEA rows,
  2. reason about gaps in its domain,
  3. persist each finding by calling the `write_finding` Gateway tool
     (Action Priority is recomputed deterministically inside that tool).

This keeps every tool call flowing through the Gateway via the MCP client with
JWT auth — no direct DynamoDB/Bedrock-KB access from the runtime.
"""
from __future__ import annotations

import os

from strands import Agent
from strands.models import BedrockModel

from agents.shared.gateway_client import gateway_mcp_client, resolve_gateway_token

MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

_model = None


def get_model() -> BedrockModel:
    global _model
    if _model is None:
        _model = BedrockModel(model_id=MODEL_ID, temperature=0.2)
    return _model


def run_specialist(
    agent_label: str,
    review_id: str,
    system_prompt: str,
    task_instruction: str,
    session_manager=None,
) -> str:
    """
    Run a specialist Strands agent against the Gateway tools and return the
    agent's final text output. All tool calls (read + write_finding) go through
    the AgentCore Gateway with Cognito M2M authentication.
    """
    token = resolve_gateway_token()
    with gateway_mcp_client(token) as gateway:
        tools = gateway.list_tools_sync()
        agent_kwargs = dict(model=get_model(), tools=tools, system_prompt=system_prompt)
        if session_manager is not None:
            agent_kwargs["session_manager"] = session_manager
        agent = Agent(**agent_kwargs)

        prompt = (
            f"DFMEA review_id: {review_id}\n\n"
            f"{task_instruction}\n\n"
            f"Available tools are provided via the AgentCore Gateway. "
            f"First call get_review_context with review_id='{review_id}' to load the "
            f"DFMEA rows and components. Then, for every gap you identify, call "
            f"write_finding with review_id='{review_id}', agent='{agent_label}', and "
            f"complete severity/occurrence/detection integers so Action Priority can "
            f"be computed. Do not fabricate findings; only report real gaps."
        )
        result = agent(prompt)
        try:
            return str(result.message)
        except AttributeError:
            return str(result)
