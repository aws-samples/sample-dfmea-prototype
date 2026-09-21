"""
agents/regulatory/agent.py — Regulatory Compliance Specialist (AgentCore Runtime).

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

_AGENT_LABEL = "regulatory"
_SYSTEM_PROMPT = (
    "You are a Regulatory Compliance Specialist for DFMEA. Identify missing standard "
    "citations for high-severity rows (S>=7), applicable standards not referenced "
    "(ISO 26262, IATF 16949, FMEA-MSR, FMVSS 214/216), and compliance gaps for "
    "safety-critical components. Use the search_regulatory_kb and get_component_standards "
    "tools to ground citations in real standards. Only report findings you are "
    "confident about (>= 0.6)."
)
_TASK = (
    "Analyse the DFMEA rows for regulatory compliance gaps, especially rows with S>=7. "
    "Search the regulatory knowledge base and ontology for applicable standards."
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
