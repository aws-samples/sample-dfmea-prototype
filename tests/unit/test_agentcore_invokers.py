"""Unit tests for the thin AgentCore Runtime invoker Lambdas.

The agent reasoning now runs inside AgentCore Runtimes (Strands + Gateway MCP),
so these tests cover the thin invoker Lambdas that call invoke_agent_runtime and
the analyst dispatcher (synthesis -> runtime, S6 -> deterministic PDF report).

The runtime agent modules themselves (agents/*/agent.py) require bedrock_agentcore
and strands and run in the container, so they are intentionally not imported here.
"""
import sys, os, json, unittest.mock as mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def _mock_stream_response(text: str):
    body = mock.MagicMock()
    body.iter_lines.return_value = [line.encode() for line in text.splitlines()]
    return {"response": body}


# ── thin specialist invoker ───────────────────────────────────────────────────

def test_invoke_runtime_calls_invoke_agent_runtime(monkeypatch):
    import lambdas.agent_invoker.handler as inv

    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/fm")
    client = mock.MagicMock()
    client.invoke_agent_runtime.return_value = _mock_stream_response("ok")
    monkeypatch.setattr(inv, "_get_client", lambda: client)

    result = inv.invoke_runtime("failure_mode", "r1")

    kwargs = client.invoke_agent_runtime.call_args[1]
    assert kwargs["agentRuntimeArn"].endswith("runtime/fm")
    payload = json.loads(kwargs["payload"].decode())
    assert payload == {"review_id": "r1"}
    assert len(kwargs["runtimeSessionId"]) >= 33   # AgentCore session id requirement
    assert result["status"] == "completed"


def test_invoke_runtime_missing_arn(monkeypatch):
    import lambdas.agent_invoker.handler as inv
    monkeypatch.delenv("AGENT_RUNTIME_ARN", raising=False)
    with pytest.raises(RuntimeError, match="AGENT_RUNTIME_ARN not set"):
        inv.invoke_runtime("structural", "r2")


def test_specialist_handler_requires_review_id(monkeypatch):
    import lambdas.agent_invoker.handler as inv
    monkeypatch.setenv("AGENT_LABEL", "regulatory")
    with pytest.raises(ValueError, match="review_id is required"):
        inv.handler({}, None)


def test_specialist_handler_invokes(monkeypatch):
    import lambdas.agent_invoker.handler as inv
    monkeypatch.setenv("AGENT_LABEL", "structural")
    monkeypatch.setattr(inv, "invoke_runtime",
                        lambda label, rid, **kw: {"agent": label, "review_id": rid, "status": "invoked"})
    result = inv.handler({"review_id": "r9"}, None)
    assert result["agent"] == "structural"
    assert result["review_id"] == "r9"


# ── analyst dispatcher ────────────────────────────────────────────────────────

def test_analyst_handler_synthesis_invokes_runtime(monkeypatch):
    import lambdas.agent_invoker.analyst_handler as ah

    captured = {}

    def fake_invoke(label, rid, extra_payload=None, frontend_jwt=""):
        captured["label"] = label
        captured["rid"] = rid
        captured["extra"] = extra_payload
        return {"agent": label, "review_id": rid, "status": "invoked"}

    monkeypatch.setattr(ah, "invoke_runtime", fake_invoke)
    monkeypatch.setattr(ah, "_load_specialist_findings", lambda rid: [{"description": "f1"}])

    result = ah.handler({"review_id": "r-syn", "frontend_jwt": "jwt-1"}, None)

    assert captured["label"] == "analyst"
    assert captured["rid"] == "r-syn"
    assert captured["extra"]["findings_from_all_specialists"] == [{"description": "f1"}]
    assert result["status"] == "invoked"


def test_analyst_handler_report_stage_runs_pdf(monkeypatch):
    import lambdas.agent_invoker.analyst_handler as ah
    import agents.analyst.report as report

    monkeypatch.setattr(report, "run_report",
                        lambda event: {"review_id": event["review_id"], "status": "COMPLETE"})

    result = ah.handler({"review_id": "r-rep", "stage": "S6_REPORT"}, None)
    assert result["status"] == "COMPLETE"


def test_analyst_handler_requires_review_id():
    import lambdas.agent_invoker.analyst_handler as ah
    with pytest.raises(ValueError, match="review_id is required"):
        ah.handler({}, None)
