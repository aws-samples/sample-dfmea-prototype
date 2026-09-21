"""
agents/structural/agent.py — Structural Decomposition Specialist (AgentCore Runtime).

Runs as an Amazon Bedrock AgentCore Runtime (Strands Agent). Tools are loaded
from the AgentCore Gateway via an MCP client authenticated with a JWT bearer token.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agents.shared.runtime_common import run_specialist

app = BedrockAgentCoreApp()

_AGENT_LABEL = "structural"
_SYSTEM_PROMPT = (
    "You are a Structural Decomposition Specialist for DFMEA. Identify CAD/BOM gaps "
    "(components referenced but not decomposed), interface matrix gaps (missing "
    "interface failure modes between adjacent components), and structural hierarchy "
    "issues. Use get_cad_anchors and the ontology tools "
    "(get_component_failure_modes, get_component_standards) to ground your analysis. "
    "Only report findings you are confident about (>= 0.6)."
)
_TASK = (
    "Analyse the DFMEA rows for structural decomposition gaps and CAD/BOM issues. "
    "Cross-check components against CAD anchors and the ontology."
)


@app.entrypoint
async def invoke(payload: dict):
    review_id = payload.get("review_id", "")
    if not review_id:
        yield {"error": "review_id is required"}
        return
    text = run_specialist(_AGENT_LABEL, review_id, _SYSTEM_PROMPT, _TASK)
    yield {"review_id": review_id, "agent": _AGENT_LABEL, "result": text}


if __name__ == "__main__":
    app.run()
