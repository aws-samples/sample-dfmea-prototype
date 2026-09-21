"""
agents/shared/tools.py — Shared utilities for all DFMEA agents.

Read-only Bedrock KB and S3 tools are decorated with @tool for Strands agent
loop invocation. write_finding is intentionally NOT decorated with @tool to avoid
strands-agents 0.1.7 thread_pool_wrapper AttributeError during tool execution.
Agents call write_finding() directly in Python after the Strands agent returns.
"""
from __future__ import annotations
import json
import os
import uuid

import boto3

REGION       = os.environ.get("AWS_REGION", "us-east-1")
TABLE_PREFIX = os.environ.get("DFMEA_TABLE_PREFIX", "dfmea")
MODEL_ID     = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
FM_KB_ID     = os.environ.get("FAILURE_MODE_KB_ID", "")
REG_KB_ID    = os.environ.get("REGULATORY_KB_ID", "")

# Strands @tool decorator — graceful no-op when strands is not installed (test environment)
try:
    from strands import tool
except ImportError:
    def tool(fn):  # type: ignore[misc]
        return fn

_dynamo        = None
_s3            = None
_bedrock_rt    = None
_bedrock_agent = None


def _get_dynamo():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb", region_name=REGION)
    return _dynamo


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _get_bedrock():
    global _bedrock_rt
    if _bedrock_rt is None:
        _bedrock_rt = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock_rt


def _get_bedrock_agent():
    global _bedrock_agent
    if _bedrock_agent is None:
        _bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=REGION)
    return _bedrock_agent


# ── AIAG-VDA 2019 Action Priority ────────────────────────────────────────────

def aiag_ap(s: int, o: int, d: int) -> str:
    """
    Deterministic AIAG-VDA 2019 Action Priority lookup.
    Returns 'H', 'M', or 'L'.
    """
    s = max(1, min(10, int(s)))
    o = max(1, min(10, int(o)))
    d = max(1, min(10, int(d)))

    if s >= 9:
        return "H"
    if s >= 7:
        if o >= 6:
            return "H"
        if o >= 4:
            return "H" if d >= 7 else "M" if d >= 2 else "L"
        if o >= 2:
            return "M" if d >= 7 else "L"
        return "L"
    if s >= 4:
        if o >= 6:
            return "M" if d >= 4 else "L"
        if o >= 4:
            return "M" if d >= 7 else "L"
        return "L"
    return "L"


def _ap_label(ap: str) -> str:
    """Normalise any AP string to H/M/L."""
    ap = ap.strip().upper()
    if ap in ("H", "HIGH"):
        return "H"
    if ap in ("M", "MED", "MEDIUM"):
        return "M"
    return "L"


# ── Strands-callable read-only tools ─────────────────────────────────────────

@tool
def lookup_ap(severity: int, occurrence: int, detection: int) -> str:
    """
    Look up AIAG-VDA 2019 Action Priority for given S/O/D scores.
    Returns 'H' (High), 'M' (Medium), or 'L' (Low).
    """
    return aiag_ap(severity, occurrence, detection)


@tool
def search_failure_mode_kb(query: str, max_results: int = 5) -> str:
    """
    Search the Failure Mode Knowledge Base for failure modes similar to the query.
    Returns JSON string with list of matching evidence items from the KB.
    """
    if not FM_KB_ID:
        return json.dumps({"results": [], "message": "Failure Mode KB not configured"})
    try:
        resp = _get_bedrock_agent().retrieve(
            knowledgeBaseId=FM_KB_ID,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": max_results}
            },
        )
        results = [
            {
                "text": r["content"]["text"][:600],
                "score": r.get("score", 0),
                "source": r.get("location", {}).get("s3Location", {}).get("uri", ""),
            }
            for r in resp.get("retrievalResults", [])
        ]
        return json.dumps({"results": results, "count": len(results)})
    except Exception as exc:
        print(f"[tools:search_failure_mode_kb] {exc}")
        return json.dumps({"results": [], "error": str(exc)})


@tool
def search_regulatory_kb(query: str, standard_keywords: str = "", max_results: int = 5) -> str:
    """
    Search the Regulatory Knowledge Base for applicable standards.
    Returns JSON string with matching regulatory references.
    """
    if not REG_KB_ID:
        return json.dumps({"results": [], "message": "Regulatory KB not configured"})
    full_query = f"{query} {standard_keywords}".strip()
    try:
        resp = _get_bedrock_agent().retrieve(
            knowledgeBaseId=REG_KB_ID,
            retrievalQuery={"text": full_query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": max_results}
            },
        )
        results = [
            {
                "text": r["content"]["text"][:600],
                "score": r.get("score", 0),
                "source": r.get("location", {}).get("s3Location", {}).get("uri", ""),
            }
            for r in resp.get("retrievalResults", [])
        ]
        return json.dumps({"results": results, "count": len(results)})
    except Exception as exc:
        print(f"[tools:search_regulatory_kb] {exc}")
        return json.dumps({"results": [], "error": str(exc)})


@tool
def get_cad_anchors(review_id: str) -> str:
    """
    Retrieve the list of CAD component labels for a review.
    Returns JSON with list of component names from the engineering drawing.
    Falls back to component names extracted from DFMEA rows if no CAD file exists.
    """
    bucket = os.environ.get("PROCESSED_BUCKET_NAME", "dfmea-processed")
    key = f"reviews/{review_id}/cad_anchors.json"
    try:
        obj = _get_s3().get_object(Bucket=bucket, Key=key)
        return obj["Body"].read().decode()
    except Exception:
        rows = load_rows(review_id)
        components = sorted({r.get("part_name", "") for r in rows if r.get("part_name")})
        return json.dumps({"components": components, "source": "dfmea_rows"})


@tool
def get_review_context(review_id: str) -> str:
    """
    Retrieve full DFMEA review context including rows, BOM, and interface matrix.
    Returns JSON string with complete review data.
    """
    rows = load_rows(review_id)
    context: dict = {
        "review_id": review_id,
        "row_count": len(rows),
        "rows": rows[:50],
        "components": sorted({r.get("part_name", "") for r in rows if r.get("part_name")}),
    }
    bucket = os.environ.get("PROCESSED_BUCKET_NAME", "dfmea-processed")
    for artifact_key, label in [
        (f"reviews/{review_id}/bom.json", "bom"),
        (f"reviews/{review_id}/interface_matrix.json", "interface_matrix"),
    ]:
        try:
            obj = _get_s3().get_object(Bucket=bucket, Key=artifact_key)
            context[label] = json.loads(obj["Body"].read())
        except Exception:
            pass
    return json.dumps(context)


@tool
def validate_shacl(rows: list[dict]) -> dict:
    """
    Validate DFMEA rows against SHACL ontology constraints.
    Returns dict with conforms bool and violations list.
    """
    import uuid as _uuid
    from pathlib import Path
    from rdflib import Graph, Literal, URIRef, Namespace, RDF, XSD
    from pyshacl import validate as shacl_validate

    ONT = Namespace("https://dfmea.example.com/ontology/v0.1.0#")
    data_graph = Graph()
    for row in rows:
        node = URIRef(f"{ONT}row_{_uuid.uuid4().hex[:8]}")
        data_graph.add((node, RDF.type, ONT.FailureMode))
        for field in ("severity", "occurrence", "detection"):
            val = row.get(field)
            if val is not None:
                try:
                    data_graph.add((node, getattr(ONT, field), Literal(int(val), datatype=XSD.integer)))
                except (ValueError, TypeError):
                    pass
        if row.get("action_priority"):
            data_graph.add((node, ONT.actionPriority, Literal(row["action_priority"])))

    shapes_path = Path(__file__).parent.parent.parent / "ontology" / "shapes.ttl"
    try:
        conforms, _, report_text = shacl_validate(
            data_graph, shacl_graph=str(shapes_path), inference="rdfs"
        )
        return {
            "conforms": conforms,
            "violations": [] if conforms else _parse_shacl_report(report_text),
        }
    except Exception as exc:
        print(f"[tools:validate_shacl] {exc}")
        return {"conforms": False, "violations": [{"message": str(exc)}]}


def _parse_shacl_report(report_text: str) -> list[dict]:
    """Extract violation messages from pyshacl report text."""
    violations = []
    for line in (report_text or "").splitlines():
        line = line.strip()
        if line.startswith("Constraint Violation"):
            violations.append({"message": line})
        elif "Message:" in line:
            violations.append({"message": line.split("Message:")[-1].strip()})
    return violations or [{"message": (report_text or "")[:500]}]


@tool
def render_svg_overlay(review_id: str, findings_json: str = "") -> str:
    """
    Generate a summary of findings mapped to CAD component anchors.
    Returns JSON describing which components have findings for SVG overlay generation.
    """
    try:
        findings = json.loads(findings_json) if findings_json else []
    except Exception:
        findings = []
    components = sorted({f.get("affected_component", "") for f in findings if f.get("affected_component")})
    return json.dumps({
        "svg_generated": len(components) > 0,
        "components_with_findings": components,
        "finding_count": len(findings),
    })


# ── Claude helper ─────────────────────────────────────────────────────────────

def call_claude(prompt: str, system: str = "") -> str:
    """Call Claude via Bedrock Converse API. Returns text response."""
    messages = [{"role": "user", "content": [{"text": prompt}]}]
    kwargs: dict = {
        "modelId": MODEL_ID,
        "messages": messages,
        "inferenceConfig": {"maxTokens": 2048, "temperature": 0.2},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    try:
        resp = _get_bedrock().converse(**kwargs)
        return resp["output"]["message"]["content"][0]["text"].strip()
    except Exception as exc:
        print(f"[tools:call_claude] {exc}")
        return ""


# ── S3 context loader ─────────────────────────────────────────────────────────

def load_rows(review_id: str) -> list[dict]:
    """Load normalised DFMEA rows from S3 processed bucket."""
    bucket = os.environ.get("PROCESSED_BUCKET_NAME", "dfmea-processed")
    key = f"reviews/{review_id}/normalised.json"
    try:
        obj = _get_s3().get_object(Bucket=bucket, Key=key)
        data = json.loads(obj["Body"].read())
        return data.get("rows", [])
    except Exception as exc:
        print(f"[tools:load_rows] {exc}")
        return []


# ── Finding writer (plain function — NOT a Strands @tool) ─────────────────────
# Deliberately excluded from @tool to avoid strands-agents 0.1.7
# thread_pool_wrapper AttributeError. Called directly by agent run_analysis()
# after the Strands agent loop completes.

def write_finding(
    review_id: str,
    agent: str,
    finding_type: str,
    description: str,
    action_priority: str = "",
    affected_component: str = "",
    suggested_failure_mode: str = "",
    severity: int = 0,
    occurrence: int = 0,
    detection: int = 0,
    standard_citations: list | None = None,
    gap_source: str = "analysis",
    confidence: float = 0.9,
) -> str:
    # Always recompute AP deterministically — never trust LLM-supplied value
    if severity > 0 and occurrence > 0 and detection > 0:
        action_priority = aiag_ap(severity, occurrence, detection)
    else:
        action_priority = _ap_label(action_priority) if action_priority else "L"

    finding_id = f"{agent}-{uuid.uuid4().hex[:12]}"
    item: dict = {
        "review_id":              review_id,
        "finding_id":             finding_id,
        "agent":                  agent,
        "finding_type":           finding_type,
        "description":            description,
        "action_priority":        action_priority,
        "confidence":             str(confidence),
        "affected_component":     affected_component,
        "suggested_failure_mode": suggested_failure_mode,
        "standard_citations":     standard_citations or [],
        "gap_source":             gap_source,
    }
    if severity > 0:
        item["severity"]   = severity
        item["occurrence"] = occurrence
        item["detection"]  = detection

    _get_dynamo().Table(f"{TABLE_PREFIX}-analysis-findings").put_item(Item=item)
    print(f"[tools:write_finding] {finding_id} AP={action_priority} type={finding_type} component={affected_component!r}")
    return finding_id


# ── JSON findings parser — used by agents to extract structured output ─────────

def parse_findings_json(text: str) -> list[dict]:
    """
    Extract a JSON findings array from agent output text.
    Handles: FINDINGS_JSON delimited blocks, markdown code blocks, bare JSON arrays.
    Returns empty list if nothing parseable is found.
    """
    import re

    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    text = re.sub(r"```(?:json)?\s*", "", text)

    # Primary: delimited block (END marker optional — may be absent)
    m = re.search(r"FINDINGS_JSON:\s*(\[.*?\])\s*(?:END_FINDINGS_JSON)?", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Secondary: any JSON array containing 'finding_type' — greedy to find outermost ]
    m = re.search(r"(\[[\s\S]*\"finding_type\"[\s\S]*\])", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    print("[tools:parse_findings_json] Could not extract findings JSON from agent output")
    return []
