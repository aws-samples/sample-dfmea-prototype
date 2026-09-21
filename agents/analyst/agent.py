"""
agents/analyst/agent.py — DFMEA Analyst synthesis (AgentCore Runtime + Memory).

Runs as an Amazon Bedrock AgentCore Runtime (Strands Agent). Consolidates and
deduplicates the specialist findings and persists the synthesised set via the
`write_finding` Gateway tool (Action Priority recomputed deterministically in
the tool). Uses AgentCore Memory (session per review_id) following the RFQ
multi-agent reference pattern.

The deterministic S6 PDF report is NOT part of this runtime — it runs in the
analyst thin invoker Lambda (see agents/analyst/report.py).
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent

from agents.shared.gateway_client import gateway_mcp_client, resolve_gateway_token
from agents.shared.runtime_common import get_model

app = BedrockAgentCoreApp()

MEMORY_ID = os.getenv("DFMEA_MEMORY_ID", "")

_SYSTEM_PROMPT = (
    "You are a DFMEA Analyst responsible for synthesising specialist findings. "
    "Deduplicate near-identical findings (same component + same failure mode), "
    "verify Action Priority using AIAG-VDA 2019 rules, and persist the clean, "
    "deduplicated set. For each unique synthesised finding, call the write_finding "
    "Gateway tool with agent='analyst', a clear description, affected_component, and "
    "complete severity/occurrence/detection integers. Use validate_shacl when useful "
    "to sanity-check the source rows. Do not invent findings."
)


def _build_session_manager(review_id: str):
    """AgentCore Memory session manager (RFQ pattern). Degrades gracefully."""
    if not MEMORY_ID:
        return None
    try:
        from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )
        config = AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=f"dfmea-review-{review_id}",
            actor_id=f"dfmea-analyst-{review_id}",
        )
        return AgentCoreMemorySessionManager(config)
    except Exception as exc:  # pragma: no cover - depends on live AWS
        print(f"[analyst] memory session manager unavailable: {exc}")
        return None


@app.entrypoint
async def invoke(payload: dict):
    """AgentCore Runtime entrypoint — synthesis stage."""
    review_id = payload.get("review_id", "")
    findings = payload.get("findings_from_all_specialists") or payload.get("findings") or []
    if not review_id:
        yield {"error": "review_id is required"}
        return

    session_manager = _build_session_manager(review_id)
    token = resolve_gateway_token()

    with gateway_mcp_client(token) as gateway:
        tools = gateway.list_tools_sync()
        agent_kwargs = dict(model=get_model(), tools=tools, system_prompt=_SYSTEM_PROMPT)
        if session_manager is not None:
            agent_kwargs["session_manager"] = session_manager
        agent = Agent(**agent_kwargs)

        findings_json = json.dumps(findings, indent=2)
        prompt = (
            f"Synthesise the specialist findings for review_id='{review_id}'.\n\n"
            f"Specialist findings ({len(findings)}):\n"
            f"{findings_json}\n\n"
            f"Steps:\n"
            f"1. Deduplicate near-identical findings (same component + same failure mode).\n"
            f"2. For each unique finding, call write_finding with agent='analyst' and "
            f"complete S/O/D so Action Priority is computed deterministically.\n"
            f"3. Summarise the consolidated finding count by Action Priority level."
        )
        result = agent(prompt)
        try:
            text = str(result.message)
        except AttributeError:
            text = str(result)
        yield {"review_id": review_id, "agent": "analyst", "status": "synthesised", "result": text}


if __name__ == "__main__":
    app.run()
