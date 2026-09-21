"""
lambdas/agentcore_tools/handler.py — MCP tools Lambda for AgentCore Gateway.

Routes tool invocations from AgentCore Gateway to the appropriate
implementation in agents/shared/. Supports 10 tools:
  1. search_failure_mode_kb       5. write_finding
  2. search_regulatory_kb         6. get_component_failure_modes
  3. get_review_context           7. get_component_standards
  4. get_cad_anchors              8. get_ontology_subclasses
                                  9. validate_shacl
                                  10. get_ml_scores

AgentCore Gateway invokes this Lambda with event:
  {"toolName": "<name>", "parameters": {...}}
"""
from __future__ import annotations
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

log = logging.getLogger(__name__)

_TOOL_REGISTRY: dict = {}


def _register_tools() -> None:
    """Lazy-import and register all tool functions."""
    global _TOOL_REGISTRY
    if _TOOL_REGISTRY:
        return

    from agents.shared.tools import (
        search_failure_mode_kb,
        search_regulatory_kb,
        get_review_context,
        get_cad_anchors,
        write_finding,
        validate_shacl,
    )
    from agents.shared.ontology_tools import (
        get_component_failure_modes,
        get_component_standards,
        get_ontology_subclasses,
    )
    from agents.shared.ml_scorer import get_ml_scores

    _TOOL_REGISTRY = {
        "search_failure_mode_kb":      search_failure_mode_kb,
        "search_regulatory_kb":        search_regulatory_kb,
        "get_review_context":          get_review_context,
        "get_cad_anchors":             get_cad_anchors,
        "write_finding":               write_finding,
        "validate_shacl":              validate_shacl,
        "get_component_failure_modes": get_component_failure_modes,
        "get_component_standards":     get_component_standards,
        "get_ontology_subclasses":     get_ontology_subclasses,
        "get_ml_scores":               get_ml_scores,
    }


def _resolve_tool_name(event: dict, context) -> str:
    """
    Determine the tool name.

    AgentCore Gateway Lambda targets pass the tool name in the Lambda client
    context as ``context.client_context.custom['bedrockAgentCoreToolName']``,
    prefixed with ``<targetName>___``. Fall back to event fields for direct
    invocation / tests.
    """
    name = ""
    try:
        custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
        name = custom.get("bedrockAgentCoreToolName", "") or ""
    except Exception:
        name = ""
    if name:
        delimiter = "___"
        if delimiter in name:
            name = name[name.index(delimiter) + len(delimiter):]
        return name
    # Fallbacks (direct invoke / unit tests)
    return event.get("toolName") or event.get("tool") or ""


def _tool_args(event: dict) -> dict:
    """
    Extract tool arguments. The AgentCore Gateway passes tool args as the raw
    event dict; direct/test invocations may nest them under 'parameters'.
    """
    if isinstance(event.get("parameters"), dict):
        return event["parameters"]
    if isinstance(event.get("params"), dict):
        return event["params"]
    # Raw event = args, minus known routing/metadata keys
    reserved = {"toolName", "tool", "parameters", "params", "sessionAttributes"}
    return {k: v for k, v in event.items() if k not in reserved}


def handler(event: dict, context) -> dict:
    """
    Route an AgentCore Gateway MCP tool invocation to the appropriate implementation.

    Tool name: context.client_context.custom['bedrockAgentCoreToolName'] ('<target>___<tool>').
    Tool args: the raw event dict.
    """
    _register_tools()

    tool_name = _resolve_tool_name(event, context)
    params = _tool_args(event)

    if not tool_name:
        return {"error": "toolName is required", "statusCode": 400}

    fn = _TOOL_REGISTRY.get(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name}", "statusCode": 404}

    # Only pass kwargs the tool actually accepts (gateway may include extras)
    try:
        import inspect
        sig = inspect.signature(fn)
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        if not accepts_kwargs:
            params = {k: v for k, v in params.items() if k in sig.parameters}
    except (ValueError, TypeError):
        pass

    try:
        result = fn(**params)
        if isinstance(result, str):
            return {"result": result}
        return {"result": json.dumps(result)}
    except (TypeError, ValueError) as exc:
        log.warning("[mcp-tools] %s parameter error: %s", tool_name, exc)
        return {"error": str(exc), "statusCode": 400}
    except Exception as exc:
        log.error("[mcp-tools] %s failed: %s", tool_name, exc)
        return {"error": str(exc), "statusCode": 500}
