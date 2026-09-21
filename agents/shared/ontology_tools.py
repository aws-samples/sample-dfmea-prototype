"""
agents/shared/ontology_tools.py — @tool wrappers for ontology SPARQL queries.

Exposed as MCP tools via AgentCore Gateway (lambdas/agentcore_tools/handler.py).
"""
from __future__ import annotations
import json

# Strands @tool decorator — graceful no-op when strands is not installed
try:
    from strands import tool
except ImportError:
    def tool(fn):  # type: ignore[misc]
        return fn

from agents.shared import ontology_client as _oc


@tool
def get_component_failure_modes(component_name: str) -> str:
    """
    Return failure modes linked to a component via ont:hasFailureMode.
    Returns JSON string with list of {name, label} dicts.
    """
    results = _oc.get_failure_modes_for_component(component_name)
    return json.dumps({"component": component_name, "failure_modes": results, "count": len(results)})


@tool
def get_component_standards(component_name: str) -> str:
    """
    Return regulatory standards applicable to a component.
    Returns JSON string with list of {standard, label, clause} dicts.
    """
    results = _oc.get_standards_for_component(component_name)
    return json.dumps({"component": component_name, "standards": results, "count": len(results)})


@tool
def get_ontology_subclasses(class_name: str) -> str:
    """
    Return direct subclasses of an ontology class.
    Returns JSON string with list of {name, label} dicts.
    """
    results = _oc.get_subclasses(class_name)
    return json.dumps({"class": class_name, "subclasses": results, "count": len(results)})
