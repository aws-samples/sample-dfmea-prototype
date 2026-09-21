"""Unit tests for Task 9: MCP tools Lambda, analyst synthesis migration, API JWT."""
import sys, os, json, unittest.mock as mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


# ── MCP tools Lambda ──────────────────────────────────────────────────────────

def test_mcp_handler_routes_known_tool():
    import lambdas.agentcore_tools.handler as mcp
    mcp._TOOL_REGISTRY = {}  # reset cache

    mock_fn = mock.MagicMock(return_value='{"results": []}')
    mcp._TOOL_REGISTRY = {"search_failure_mode_kb": mock_fn}

    result = mcp.handler({"toolName": "search_failure_mode_kb", "parameters": {"query": "buckling"}}, None)
    mock_fn.assert_called_once_with(query="buckling")
    assert "result" in result


def test_mcp_handler_unknown_tool():
    import lambdas.agentcore_tools.handler as mcp
    mcp._TOOL_REGISTRY = {"search_failure_mode_kb": mock.MagicMock()}

    result = mcp.handler({"toolName": "nonexistent_tool", "parameters": {}}, None)
    assert result["statusCode"] == 404


def test_mcp_handler_missing_tool_name():
    import lambdas.agentcore_tools.handler as mcp
    mcp._TOOL_REGISTRY = {}

    result = mcp.handler({}, None)
    assert result["statusCode"] == 400


def test_mcp_all_10_tools_registered():
    """All 10 tools must be registered after _register_tools()."""
    import lambdas.agentcore_tools.handler as mcp
    mcp._TOOL_REGISTRY = {}

    # Mock all dependencies to avoid real boto3/neptune calls
    with mock.patch.dict("sys.modules", {
        "agents.shared.tools": mock.MagicMock(),
        "agents.shared.ontology_tools": mock.MagicMock(),
        "agents.shared.ml_scorer": mock.MagicMock(),
    }):
        mcp._register_tools()

    expected = {
        "search_failure_mode_kb", "search_regulatory_kb", "get_review_context",
        "get_cad_anchors", "write_finding", "validate_shacl",
        "get_component_failure_modes", "get_component_standards",
        "get_ontology_subclasses", "get_ml_scores",
    }
    assert expected == set(mcp._TOOL_REGISTRY.keys())
    mcp._TOOL_REGISTRY = {}  # clean up


# ── Analyst synthesis → AgentCore Runtime (thin dispatcher) ───────────────────

def test_analyst_dispatcher_synthesis_uses_m2m_runtime_auth(monkeypatch):
    """S4 forwards findings but never forwards a frontend token to Gateway."""
    import lambdas.agent_invoker.analyst_handler as ah

    captured = {}

    def fake_invoke(label, rid, extra_payload=None, frontend_jwt=""):
        captured.update(label=label, rid=rid, extra=extra_payload, jwt=frontend_jwt)
        return {"agent": label, "review_id": rid, "status": "invoked"}

    monkeypatch.setattr(ah, "invoke_runtime", fake_invoke)

    result = ah.handler({
        "review_id": "r-synth-1",
        "findings_from_all_specialists": [{"description": "test finding"}],
        "frontend_jwt": "jwt-tok-1",
    }, None)

    assert captured["label"] == "analyst"
    assert captured["rid"] == "r-synth-1"
    assert captured["jwt"] == ""
    assert captured["extra"]["findings_from_all_specialists"] == [{"description": "test finding"}]
    assert result["status"] == "invoked"


# ── API review input must not forward the frontend JWT to the M2M Gateway ─────

def test_api_post_reviews_uses_m2m_gateway_auth(monkeypatch):
    import lambdas.api.handler as api

    monkeypatch.setattr(api, "dynamodb", mock.MagicMock())
    monkeypatch.setattr(api, "sfn_client", mock.MagicMock())
    monkeypatch.setattr(api, "REVIEW_SM_ARN", "arn:aws:states:us-east-1:123:stateMachine:dfmea")

    event = {
        "body": json.dumps({"assembly_name": "B-Pillar", "file_key": "uploads/test.json"}),
        "headers": {"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.test.sig"},
    }
    api.post_reviews(event)

    call_kwargs = api.sfn_client.start_execution.call_args[1]
    sfn_input = json.loads(call_kwargs["input"])
    assert "frontend_jwt" not in sfn_input


def test_mcp_handler_logs_inbound_token():
    """Handler must extract inbound_token from sessionAttributes (audit trail)."""
    import lambdas.agentcore_tools.handler as mcp

    mock_fn = mock.MagicMock(return_value='{"results": []}')
    mcp._TOOL_REGISTRY = {"search_failure_mode_kb": mock_fn}

    event = {
        "toolName": "search_failure_mode_kb",
        "parameters": {"query": "test"},
        "sessionAttributes": {"inbound_token": "Bearer eyJhbGc.test.sig"},
    }

    # Should not raise — inbound_token is read and logged
    result = mcp.handler(event, None)
    assert "result" in result
