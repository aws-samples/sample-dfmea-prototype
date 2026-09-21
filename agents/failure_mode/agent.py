"""
agents/failure_mode/agent.py — Failure Mode Specialist (AgentCore Runtime).

Runs as an Amazon Bedrock AgentCore Runtime (Strands Agent). Tools are loaded
from the AgentCore Gateway via an MCP client authenticated with a JWT bearer
token. Deployed with the bedrock_agentcore_starter_toolkit
(scripts/deploy_agentcore_runtimes.py).
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agents.shared.runtime_common import run_specialist

app = BedrockAgentCoreApp()

_AGENT_LABEL = "failure_mode"
_SYSTEM_PROMPT = (
    "You are a Failure Mode Specialist for DFMEA (Design Failure Mode and Effects "
    "Analysis). Identify missing failure modes, under-scored risks (severity S>=7 "
    "with low occurrence or detection), and knowledge-base gaps. Use the "
    "search_failure_mode_kb tool to ground your analysis in AIAG-VDA failure mode "
    "patterns. Only report findings you are confident about (>= 0.6)."
)
_TASK = (
    "Analyse the DFMEA rows for missing failure modes and under-scored risks. "
    "Search the failure-mode knowledge base for comparable patterns where useful."
)


@app.entrypoint
async def invoke(payload: dict):
    """AgentCore Runtime entrypoint."""
    review_id = payload.get("review_id", "")
    if not review_id:
        yield {"error": "review_id is required"}
        return
    text = run_specialist(_AGENT_LABEL, review_id, _SYSTEM_PROMPT, _TASK)
    yield {"review_id": review_id, "agent": _AGENT_LABEL, "result": text}


if __name__ == "__main__":
    app.run()
