"""
agents/other/agent.py — Schema Completeness Specialist (AgentCore Runtime).

Runs as an Amazon Bedrock AgentCore Runtime (Strands Agent). Tools are loaded
from the AgentCore Gateway via an MCP client authenticated with a JWT bearer token.

This agent also consults the ML pre-screen risk scores (get_ml_scores Gateway
tool) and records ML-flagged rows as findings before performing its own
schema-completeness analysis.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agents.shared.runtime_common import run_specialist

app = BedrockAgentCoreApp()

_AGENT_LABEL = "other"
_SYSTEM_PROMPT = (
    "You are a Schema Completeness Specialist for DFMEA. Flag rows missing required "
    "fields (function, failure_mode, effect, cause), rows with S>=8 lacking a "
    "recommended_action, and rows with incomplete S/O/D scoring. You also review the "
    "ML pre-screen risk scores: call get_ml_scores and, for each row the ML model "
    "labelled 'under_scored' or 'over_scored' or flagged as an anomaly, record an "
    "'ml_flag'/'ml_anomaly' finding (prefix the description with [ML]). Only report "
    "findings you are confident about (>= 0.6)."
)
_TASK = (
    "First call get_ml_scores with review_id to retrieve ML pre-screen labels and "
    "write ML-flagged findings. Then analyse the DFMEA rows for schema completeness "
    "gaps — missing required fields and incomplete rows."
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
