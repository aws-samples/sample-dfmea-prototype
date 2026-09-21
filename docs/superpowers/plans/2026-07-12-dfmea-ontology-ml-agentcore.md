# DFMEA Ontology, ML and AgentCore Migration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire ontology (SPARQL/SHACL), ML pre-screening, and AgentCore Runtime into the DFMEA system so all three subsystems are active at runtime, and fix deploy.sh/destroy.sh to be complete single-point scripts.

**Architecture:** Ontology fixes propagate JSON-LD → Neptune → SPARQL queries at agent runtime. ML pre-screening runs at intake (S1) and results are read by the schema agent. Strands SDK agent Lambdas are replaced by thin invokers calling Bedrock AgentCore Runtime; tools are exposed via AgentCore Gateway backed by a single MCP tools Lambda. M2M Cognito pool issues JWT for AgentCore→Gateway auth. Frontend JWT flows as AgentCore sessionAttribute for audit.

**Tech Stack:** Python 3.12, AWS CDK Python, boto3, pyshacl==0.26.0, rdflib==7.1.1, scikit-learn==1.5.2, joblib==1.4.2, numpy==1.26.4, Amazon Bedrock AgentCore Runtime, Amazon Bedrock AgentCore Gateway, Amazon Neptune Serverless, pytest

---

> **Scope note:** Four logically distinct subsystems that deploy together per operational rule #1 (deploy.sh is single point). Tasks are ordered so each produces working, committable code independently.

---

## File Map

### Created
- `agents/shared/ontology_tools.py` — @tool wrappers calling ontology_client.py
- `agents/shared/ml_scorer.py` — ML inference + feature derivation + DynamoDB write
- `lambdas/agentcore_tools/handler.py` — MCP tools Lambda handler (routes 10 tools)
- `infra/stacks/agentcore_stack.py` — AgentCore MCP tools Lambda + outputs for CLI registration
- `tests/unit/test_ontology_client.py` — unit tests for namespace fix + new SPARQL function
- `tests/unit/test_shacl.py` — unit tests for pyshacl validate_shacl
- `tests/unit/test_ml_scorer.py` — unit tests for feature derivation + graceful degradation

### Modified
- `ontology/ontology.json-ld` — add subClassOf, hasFailureMode, mustComplyWith edges
- `ontology/shapes.ttl` — fix actionPriority values to H/M/L
- `agents/shared/ontology_client.py` — fix ONTOLOGY_PREFIX, add get_failure_modes_for_component
- `agents/shared/tools.py` — replace validate_shacl stub with pyshacl
- `agents/failure_mode/agent.py` — remove Strands SDK, thin invoker handler
- `agents/structural/agent.py` — same
- `agents/regulatory/agent.py` — same
- `agents/other/agent.py` — thin invoker + read ML scores, emit ML findings
- `agents/analyst/agent.py` — synthesis thin invoker; report stage unchanged
- `lambdas/intake/handler.py` — add ml_scorer.score_rows() call
- `lambdas/api/handler.py` — extract + pass frontend JWT to SFN input
- `infra/stacks/auth_stack.py` — M2M pool, resource server, app client, KMS secret
- `infra/stacks/agent_stack.py` — thin invoker Lambdas + ML_MODELS_BUCKET_NAME
- `infra/app.py` — add DfmeaAgentCoreStack
- `scripts/deploy.sh` — 16-step sequence
- `scripts/destroy.sh` — correct names, all 13 stacks, local cleanup

---

## Task 1: Fix Ontology JSON-LD

**Files:**
- Modify: `ontology/ontology.json-ld`

- [ ] **Step 1: Add graph edges after the last existing node**

In `ontology/ontology.json-ld`, after the `laser_weld` entry (before the closing `]}`), add:

```json
,
{"@id": "ont:FailureMode#buckling_under_load", "rdfs:subClassOf": {"@id": "ont:FailureMode"}},
{"@id": "ont:FailureMode#corrosion_at_joint",  "rdfs:subClassOf": {"@id": "ont:FailureMode"}},
{"@id": "ont:FailureMode#weld_fracture",        "rdfs:subClassOf": {"@id": "ont:FailureMode"}},
{"@id": "ont:Component#B-Pillar_InnerPanel",
 "hasFailureMode": [{"@id": "ont:FailureMode#buckling_under_load"}, {"@id": "ont:FailureMode#weld_fracture"}],
 "mustComplyWith": [{"@id": "ont:Standard#FMVSS_214"}, {"@id": "ont:Standard#FMVSS_216"}, {"@id": "ont:Standard#ISO_26262"}]},
{"@id": "ont:Component#B-Pillar_OuterPanel",
 "hasFailureMode": [{"@id": "ont:FailureMode#buckling_under_load"}, {"@id": "ont:FailureMode#corrosion_at_joint"}],
 "mustComplyWith": [{"@id": "ont:Standard#FMVSS_214"}, {"@id": "ont:Standard#FMVSS_216"}, {"@id": "ont:Standard#ISO_26262"}]},
{"@id": "ont:Component#B-Pillar_Reinforcement",
 "hasFailureMode": [{"@id": "ont:FailureMode#buckling_under_load"}],
 "mustComplyWith": [{"@id": "ont:Standard#FMVSS_214"}, {"@id": "ont:Standard#FMVSS_216"}, {"@id": "ont:Standard#ISO_26262"}]},
{"@id": "ont:Component#B-Pillar_Hinge",
 "hasFailureMode": [{"@id": "ont:FailureMode#weld_fracture"}, {"@id": "ont:FailureMode#corrosion_at_joint"}],
 "mustComplyWith": [{"@id": "ont:Standard#FMVSS_214"}, {"@id": "ont:Standard#FMVSS_216"}, {"@id": "ont:Standard#ISO_26262"}]}
```

- [ ] **Step 2: Validate JSON**

```bash
python3 -c "import json; json.load(open('ontology/ontology.json-ld')); print('valid')"
```
Expected: `valid`

- [ ] **Step 3: Commit**

```bash
git add ontology/ontology.json-ld
git commit -m "fix(ontology): add subClassOf, hasFailureMode, mustComplyWith edges for B-Pillar components"
```

---

## Task 2: Fix SHACL Shapes

**Files:**
- Modify: `ontology/shapes.ttl`

- [ ] **Step 1: Fix actionPriority values**

Change line 34 from:
```
sh:in ( "High" "Medium" "Low" ) ;
sh:message "actionPriority must be High, Medium, or Low" ;
```
to:
```
sh:in ( "H" "M" "L" ) ;
sh:message "actionPriority must be H, M, or L" ;
```

- [ ] **Step 2: Verify shapes load**

```bash
pip install pyshacl==0.26.0 rdflib==7.1.1 -q
python3 -c "
from pyshacl import validate
from rdflib import Graph
conforms, _, _ = validate(Graph(), shacl_graph='ontology/shapes.ttl')
print('shapes loaded, conforms on empty graph:', conforms)
"
```
Expected: `shapes loaded, conforms on empty graph: True`

- [ ] **Step 3: Commit**

```bash
git add ontology/shapes.ttl
git commit -m "fix(shacl): actionPriority sh:in values corrected to H/M/L"
```

---

## Task 3: Fix ontology_client.py

**Files:**
- Modify: `agents/shared/ontology_client.py`
- Create: `tests/unit/test_ontology_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/__init__.py` (empty) and `tests/unit/test_ontology_client.py`:

```python
"""Unit tests for ontology_client — no Neptune connection needed."""
import sys, os, unittest.mock as mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import agents.shared.ontology_client as oc


def test_prefix_is_correct():
    assert oc.ONTOLOGY_PREFIX == "https://dfmea.example.com/ontology/v0.1.0#"


def test_new_function_exists():
    assert callable(getattr(oc, "get_failure_modes_for_component", None))


def test_query_uses_has_failure_mode():
    with mock.patch.object(oc, "_sparql_query", return_value={"results": {"bindings": []}}) as m:
        oc.get_failure_modes_for_component("B-Pillar_InnerPanel")
        assert "hasFailureMode" in m.call_args[0][0]


def test_bindings_are_mapped():
    bindings = [{"fm": {"value": "https://dfmea.example.com/ontology/v0.1.0#buckling_under_load"},
                 "label": {"value": "Buckling Under Load"}}]
    with mock.patch.object(oc, "_sparql_query", return_value={"results": {"bindings": bindings}}):
        result = oc.get_failure_modes_for_component("B-Pillar_InnerPanel")
    assert result[0]["name"] == "buckling_under_load"
    assert result[0]["label"] == "Buckling Under Load"


def test_graceful_on_network_error():
    with mock.patch.object(oc, "_sparql_query", side_effect=Exception("network")):
        assert oc.get_failure_modes_for_component("X") == []
```

- [ ] **Step 2: Run tests — expect failures**

```bash
cd /mnt/c/Users/mahasrid/Downloads/EXT-Repo/DFMEA_Demo/dfmea-prototype
pip install pytest -q
pytest tests/unit/test_ontology_client.py -v
```
Expected: `FAILED` on prefix check and missing function.

- [ ] **Step 3: Fix ONTOLOGY_PREFIX in ontology_client.py line 18**

Change:
```python
ONTOLOGY_PREFIX  = "https://dfmea.example.com/ontology#"
```
to:
```python
ONTOLOGY_PREFIX  = "https://dfmea.example.com/ontology/v0.1.0#"
```

- [ ] **Step 4: Add get_failure_modes_for_component after get_failure_modes_for_class**

```python
def get_failure_modes_for_component(component_name: str) -> list[dict]:
    """Return failure modes linked directly to a component via ont:hasFailureMode."""
    sparql = f"""
    PREFIX onto: <{ONTOLOGY_PREFIX}>
    SELECT ?fm ?label WHERE {{
        onto:Component#{component_name} onto:hasFailureMode ?fm .
        OPTIONAL {{ ?fm <http://www.w3.org/2000/01/rdf-schema#label> ?label }}
    }}
    LIMIT 50
    """
    try:
        resp = _sparql_query(sparql)
        return [
            {
                "name":  b["fm"]["value"].split("#")[-1],
                "label": b.get("label", {}).get("value", b["fm"]["value"].split("#")[-1]),
            }
            for b in resp["results"]["bindings"]
        ]
    except Exception as exc:
        log.warning("ontology_client.get_failure_modes_for_component failed: %s", exc)
        return []
```

- [ ] **Step 5: Run tests — expect all pass**

```bash
pytest tests/unit/test_ontology_client.py -v
```
Expected: `5 passed`

- [ ] **Step 6: Commit**

```bash
git add agents/shared/ontology_client.py tests/unit/__init__.py tests/unit/test_ontology_client.py
git commit -m "fix(ontology): correct ONTOLOGY_PREFIX; add get_failure_modes_for_component with tests"
```

---

## Task 4: Replace validate_shacl Stub

**Files:**
- Modify: `agents/shared/tools.py`
- Create: `tests/unit/test_shacl.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_shacl.py`:

```python
"""Unit tests for validate_shacl — uses real pyshacl + shapes.ttl."""
import sys, os, json, unittest.mock as mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def _validate(rows):
    with mock.patch("agents.shared.tools.load_rows", return_value=rows):
        from agents.shared.tools import validate_shacl
        return json.loads(validate_shacl("test-review-id"))


def test_valid_rows_conform():
    rows = [{"severity": 8, "occurrence": 4, "detection": 5, "action_priority": "H"}]
    result = _validate(rows)
    assert result["conforms"] is True


def test_wrong_ap_value_fails():
    rows = [{"severity": 5, "occurrence": 3, "detection": 3, "action_priority": "High"}]
    result = _validate(rows)
    assert result["conforms"] is False


def test_sod_out_of_range_fails():
    rows = [{"severity": 11, "occurrence": 3, "detection": 3, "action_priority": "M"}]
    result = _validate(rows)
    assert result["conforms"] is False


def test_missing_sod_fails():
    rows = [{"action_priority": "L"}]
    result = _validate(rows)
    assert result["conforms"] is False
```

- [ ] **Step 2: Run tests — expect failures (stub returns wrong format)**

```bash
pytest tests/unit/test_shacl.py -v
```
Expected: failures — stub has wrong return format and doesn't use pyshacl.

- [ ] **Step 3: Replace validate_shacl function in tools.py**

Find the `@tool\ndef validate_shacl` function (starts around line 220). Replace the entire function with:

```python
@tool
def validate_shacl(review_id: str) -> str:
    """
    Validate DFMEA rows against SHACL shapes in ontology/shapes.ttl.
    Returns JSON: {conforms: bool, violation_count: int, violations: list}.
    """
    import uuid as _uuid
    from pathlib import Path
    from rdflib import Graph, Literal, URIRef, Namespace, RDF
    from rdflib.namespace import XSD
    from pyshacl import validate as shacl_validate

    rows = load_rows(review_id)
    ONT = Namespace("https://dfmea.example.com/ontology/v0.1.0#")
    data_graph = Graph()

    for row in rows:
        node = URIRef(f"{ONT}row_{_uuid.uuid4().hex[:8]}")
        data_graph.add((node, RDF.type, ONT.FailureMode))
        for field, pred in [
            ("severity",   ONT.severity),
            ("occurrence", ONT.occurrence),
            ("detection",  ONT.detection),
        ]:
            val = row.get(field)
            if val is not None:
                try:
                    data_graph.add((node, pred, Literal(int(val), datatype=XSD.integer)))
                except (ValueError, TypeError):
                    pass
        ap = row.get("action_priority", "")
        if ap:
            data_graph.add((node, ONT.actionPriority, Literal(str(ap))))

    shapes_path = Path(__file__).parent.parent.parent / "ontology" / "shapes.ttl"
    try:
        conforms, _, report_text = shacl_validate(
            data_graph,
            shacl_graph=str(shapes_path),
            inference="rdfs",
            abort_on_first=False,
        )
        violations = _parse_shacl_report(report_text) if not conforms else []
    except Exception as exc:
        print(f"[tools:validate_shacl] pyshacl error: {exc}")
        return json.dumps({"conforms": False, "violations": [{"error": str(exc)}]})

    return json.dumps({
        "conforms": conforms,
        "violation_count": len(violations),
        "violations": violations[:20],
    })


def _parse_shacl_report(report_text: str) -> list[dict]:
    violations = []
    for line in (report_text or "").splitlines():
        line = line.strip()
        if line.startswith("Constraint Violation") or "sh:resultMessage" in line:
            violations.append({"message": line})
    return violations or [{"message": (report_text or "")[:500]}]
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/unit/test_shacl.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add agents/shared/tools.py tests/unit/test_shacl.py
git commit -m "fix(shacl): replace hardcoded validate_shacl stub with real pyshacl+rdflib implementation"
```

---

## Task 5: New ontology_tools.py

**Files:**
- Create: `agents/shared/ontology_tools.py`

- [ ] **Step 1: Create the file**

Create `agents/shared/ontology_tools.py`:

```python
"""
agents/shared/ontology_tools.py — MCP-registered @tool wrappers for Neptune ontology.
Wired into failure_mode, structural, and regulatory agents via AgentCore Gateway.
"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from agents.shared import ontology_client

try:
    from strands import tool
except ImportError:
    def tool(fn): return fn


@tool
def get_component_failure_modes(component_name: str) -> str:
    """
    Query Neptune ontology for known failure modes of a named B-Pillar component.
    Returns JSON: {component, failure_modes: [{name, label}], count}.
    component_name: exact name e.g. 'B-Pillar_InnerPanel'
    """
    results = ontology_client.get_failure_modes_for_component(component_name)
    return json.dumps({"component": component_name, "failure_modes": results, "count": len(results)})


@tool
def get_component_standards(component_name: str) -> str:
    """
    Query Neptune ontology for regulatory standards applicable to a component.
    Returns JSON: {component, standards: [{standard, clause}], count}.
    component_name: exact name e.g. 'B-Pillar_OuterPanel'
    """
    results = ontology_client.get_related_standards(component_name)
    return json.dumps({"component": component_name, "standards": results, "count": len(results)})


@tool
def get_ontology_subclasses(class_name: str) -> str:
    """
    Query Neptune ontology for direct subclasses of a class.
    Returns JSON: {class, subclasses: [{name, label}], count}.
    class_name: e.g. 'FailureMode', 'Component'
    """
    results = ontology_client.get_subclasses(class_name)
    return json.dumps({"class": class_name, "subclasses": results, "count": len(results)})
```

- [ ] **Step 2: Smoke-test**

```bash
python3 -c "from agents.shared.ontology_tools import get_component_failure_modes, get_component_standards, get_ontology_subclasses; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add agents/shared/ontology_tools.py
git commit -m "feat(ontology): add ontology_tools.py with three @tool wrappers for Neptune SPARQL"
```

---

## Task 6: New ml_scorer.py

**Files:**
- Create: `agents/shared/ml_scorer.py`
- Create: `tests/unit/test_ml_scorer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_ml_scorer.py`:

```python
"""Unit tests for ml_scorer — no S3/DynamoDB/model files needed."""
import sys, os, unittest.mock as mock, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def test_derive_structural():
    from agents.shared.ml_scorer import _derive_features
    f = _derive_features({"function": "provide structural support", "part_name": "inner panel", "cause": "fatigue loading"})
    assert f["functional_category"] == "structural"
    assert f["component_type"] == "panel"
    assert f["cause_type"] == "loading"


def test_derive_nvh_joining_material():
    from agents.shared.ml_scorer import _derive_features
    f = _derive_features({"function": "seal joint", "part_name": "weld bead", "cause": "corrosion at interface"})
    assert f["functional_category"] == "NVH"
    assert f["component_type"] == "joining"
    assert f["cause_type"] == "material"


def test_derive_defaults_on_unknown():
    from agents.shared.ml_scorer import _derive_features
    f = _derive_features({"function": "xyz", "part_name": "xyz", "cause": "xyz"})
    assert f["functional_category"] == "structural"
    assert f["component_type"] == "panel"
    assert f["cause_type"] == "design"


def test_score_rows_graceful_model_load_failure():
    from agents.shared.ml_scorer import score_rows
    with mock.patch("agents.shared.ml_scorer._load_models", side_effect=Exception("S3 down")):
        score_rows([{"severity": 8, "occurrence": 4, "detection": 5}], "rev-1")


def test_score_rows_graceful_write_failure():
    from agents.shared.ml_scorer import score_rows
    rf = mock.MagicMock(); rf.predict.return_value = np.array(["valid"])
    iso = mock.MagicMock(); iso.predict.return_value = np.array([1])
    enc = mock.MagicMock()
    with mock.patch("agents.shared.ml_scorer._load_models", return_value=(rf, iso, enc)):
        with mock.patch("agents.shared.ml_scorer._write_scores", side_effect=Exception("DDB down")):
            score_rows([{"severity": 8, "occurrence": 4, "detection": 5, "action_priority": "H",
                         "function": "", "part_name": "", "cause": ""}], "rev-1")
```

- [ ] **Step 2: Run tests — expect ModuleNotFoundError**

```bash
pip install scikit-learn==1.5.2 joblib==1.4.2 numpy==1.26.4 -q
pytest tests/unit/test_ml_scorer.py -v
```
Expected: `ModuleNotFoundError: No module named 'agents.shared.ml_scorer'`

- [ ] **Step 3: Create agents/shared/ml_scorer.py**

```python
"""
agents/shared/ml_scorer.py — ML pre-screening integration for DFMEA.

score_rows()     called by lambdas/intake/handler.py post-normalisation.
get_ml_scores()  MCP tool for schema/other agent to retrieve flagged rows.

Graceful degradation: any exception logs warning and returns without raising.
"""
from __future__ import annotations
import json, logging, os, sys
import boto3

log = logging.getLogger(__name__)

REGION            = os.environ.get("AWS_REGION", "us-east-1")
ML_MODELS_BUCKET  = os.environ.get("ML_MODELS_BUCKET_NAME", "")
RISK_SCORES_TABLE = os.environ.get("RISK_SCORES_TABLE_NAME", "dfmea-risk-scores")
ML_MODEL_PREFIX   = os.environ.get("ML_MODEL_PREFIX", "v1/")

_rf_model = _iso_model = _encoders = None

_FUNCTIONAL_MAP = {
    "support": "structural", "structural": "structural", "reinforce": "structural",
    "seal": "NVH", "nvh": "NVH", "noise": "NVH", "vibration": "NVH",
    "transmit": "load_path", "load": "load_path", "transfer": "load_path",
    "protect": "barrier", "shield": "barrier",
    "thermal": "thermal", "heat": "thermal",
    "corros": "corrosion", "rust": "corrosion",
}
_COMPONENT_MAP = {
    "panel": "panel", "sheet": "panel",
    "weld": "joining", "spot": "joining", "laser": "joining", "bond": "joining",
    "adhesive": "bonding",
    "reinforcement": "structural", "reinforce": "structural", "bracket": "structural",
    "hinge": "hinge", "mount": "mount",
}
_CAUSE_MAP = {
    "corrosion": "material", "rust": "material", "material": "material",
    "fatigue": "loading", "impact": "loading", "overload": "loading",
    "load": "loading", "stress": "loading",
    "tolerance": "manufacturing", "manufacturing": "manufacturing",
    "weld": "manufacturing",
    "temperature": "environmental", "thermal": "environmental", "moisture": "environmental",
}


def _derive_features(row: dict) -> dict:
    fn   = (row.get("function") or "").lower()
    part = (row.get("part_name") or "").lower()
    caus = (row.get("cause") or "").lower()
    fc = next((v for k, v in _FUNCTIONAL_MAP.items() if k in fn), "structural")
    ct = next((v for k, v in _COMPONENT_MAP.items() if k in part), "panel")
    ca = next((v for k, v in _CAUSE_MAP.items() if k in caus), "design")
    return {"functional_category": fc, "component_type": ct, "cause_type": ca}


def _load_models():
    global _rf_model, _iso_model, _encoders
    if _rf_model is not None:
        return _rf_model, _iso_model, _encoders
    import joblib, tempfile
    s3 = boto3.client("s3", region_name=REGION)
    with tempfile.TemporaryDirectory() as tmp:
        for fname in ["rf_model.joblib", "iso_model.joblib", "encoders.joblib"]:
            s3.download_file(ML_MODELS_BUCKET, f"{ML_MODEL_PREFIX}{fname}", f"{tmp}/{fname}")
        _rf_model  = joblib.load(f"{tmp}/rf_model.joblib")
        _iso_model = joblib.load(f"{tmp}/iso_model.joblib")
        _encoders  = joblib.load(f"{tmp}/encoders.joblib")
    return _rf_model, _iso_model, _encoders


def _write_scores(review_id: str, scores: list[dict]) -> None:
    dynamo = boto3.resource("dynamodb", region_name=REGION)
    table  = dynamo.Table(RISK_SCORES_TABLE)
    with table.batch_writer() as batch:
        for score in scores:
            batch.put_item(Item={"review_id": review_id, **score})


def score_rows(rows: list[dict], review_id: str) -> None:
    """Run RF + IsolationForest on normalised rows. Writes to dfmea-risk-scores."""
    try:
        if not rows or not ML_MODELS_BUCKET:
            log.warning("[ml_scorer] skipped — no rows or ML_MODELS_BUCKET not set")
            return
        _repo_root = os.path.join(os.path.dirname(__file__), "../..")
        if _repo_root not in sys.path:
            sys.path.insert(0, _repo_root)
        from ml.features import encode_rows
        rf, iso, encoders = _load_models()
        enriched = [{**r, **_derive_features(r)} for r in rows]
        X = encode_rows(enriched, encoders)
        rf_preds  = rf.predict(X)
        iso_preds = iso.predict(X)
        scores = [
            {
                "finding_id":   f"ml-row-{i:04d}",
                "row_index":    i,
                "part_name":    rows[i].get("part_name", ""),
                "failure_mode": rows[i].get("failure_mode", ""),
                "rf_prediction": str(rf_preds[i]),
                "anomaly":      bool(iso_preds[i] == -1),
            }
            for i in range(len(rows))
        ]
        _write_scores(review_id, scores)
        log.info("[ml_scorer] scored %d rows for %s", len(rows), review_id)
    except Exception as exc:
        log.warning("[ml_scorer] pre-screen skipped for %s: %s", review_id, exc)


def get_ml_scores(review_id: str) -> str:
    """MCP tool: fetch ML pre-screen scores for a review from DynamoDB."""
    try:
        from boto3.dynamodb.conditions import Key as DKey
        table = boto3.resource("dynamodb", region_name=REGION).Table(RISK_SCORES_TABLE)
        items = table.query(KeyConditionExpression=DKey("review_id").eq(review_id)).get("Items", [])
        flagged = [i for i in items if i.get("rf_prediction") != "valid" or i.get("anomaly")]
        return json.dumps({"review_id": review_id, "total_rows": len(items),
                           "flagged_count": len(flagged), "flagged_rows": flagged})
    except Exception as exc:
        log.warning("[ml_scorer.get_ml_scores] %s", exc)
        return json.dumps({"review_id": review_id, "total_rows": 0, "flagged_count": 0, "flagged_rows": []})
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
pytest tests/unit/test_ml_scorer.py -v
```
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add agents/shared/ml_scorer.py tests/unit/test_ml_scorer.py
git commit -m "feat(ml): add ml_scorer.py — feature derivation, S3 model load, graceful degradation, MCP tool"
```

---

## Task 7: Wire ML into Intake Handler

**Files:**
- Modify: `lambdas/intake/handler.py`

- [ ] **Step 1: Locate _normalise_rows call in handler.py**

Read `lambdas/intake/handler.py`. Find where `rows = _normalise_rows(...)` is called and `normalised.json` is written to S3.

- [ ] **Step 2: Insert ML scoring call after _normalise_rows**

After the `rows = _normalise_rows(raw_rows)` line and before the S3 put:

```python
    # ML pre-screening — best-effort, never blocks intake
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "../.."))
        from agents.shared.ml_scorer import score_rows as _score_rows
        _score_rows(rows, review_id)
    except Exception as _ml_exc:
        print(f"[intake] ML pre-screen skipped: {_ml_exc}")
```

- [ ] **Step 3: Verify handler imports cleanly**

```bash
python3 -c "import sys; sys.path.insert(0,'.'); import lambdas.intake.handler; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add lambdas/intake/handler.py
git commit -m "feat(intake): add ML pre-screening call after _normalise_rows"
```

---

## Task 8: Migrate Specialist Agents to AgentCore Thin Invokers

**Files:**
- Modify: `agents/failure_mode/agent.py`
- Modify: `agents/structural/agent.py`
- Modify: `agents/regulatory/agent.py`
- Modify: `agents/other/agent.py`

- [ ] **Step 1: Replace agents/failure_mode/agent.py**

```python
"""
agents/failure_mode/agent.py — Failure Mode Specialist (AgentCore Runtime).
SYSTEM_PROMPT is registered in AgentCore Runtime. Lambda is a thin invoker.
"""
from __future__ import annotations
import json, os
import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENTCORE_AGENT_ID = os.environ.get("AGENTCORE_AGENT_ID", "")

SYSTEM_PROMPT = """You are the Failure Mode Specialist agent for DFMEA review (AIAG-VDA 2019).

Your task at Stage S4:
1. Call get_review_context(review_id) to load all DFMEA rows and component names
2. For each component, call get_component_failure_modes(component_name) to retrieve known failure modes from the ontology
3. Call search_failure_mode_kb(query) to retrieve KB evidence for each component+function
4. Compare ontology and KB failure modes against existing DFMEA rows to identify gaps
5. Identify: (a) failure modes present in ontology/KB but missing from DFMEA,
             (b) existing failure modes with under-estimated S/O/D scores

Output findings between FINDINGS_JSON: and END_FINDINGS_JSON markers.
Each finding: finding_type, description, affected_component, suggested_failure_mode,
              severity (1-10), occurrence (1-10), detection (1-10), confidence (>=0.6).
"""


def handler(event, context):
    review_id    = event.get("review_id", "")
    frontend_jwt = event.get("frontend_jwt", "")
    client = boto3.client("bedrock-agentcore-runtime", region_name=REGION)
    resp = client.invoke_agent(
        agentId=AGENTCORE_AGENT_ID,
        inputText=json.dumps({"review_id": review_id}),
        sessionAttributes={"inbound_token": frontend_jwt, "review_id": review_id},
    )
    return {"status": "ok", "review_id": review_id, "output": resp.get("output", {})}
```

- [ ] **Step 2: Apply same pattern to structural/agent.py**

Keep existing SYSTEM_PROMPT content; add `get_component_failure_modes` and `get_component_standards` to tool instructions in the prompt. Replace Strands imports + Agent creation + run_analysis with thin invoker handler (identical pattern).

- [ ] **Step 3: Apply same pattern to regulatory/agent.py**

Keep existing SYSTEM_PROMPT; add `get_component_standards` to tool instructions. Thin invoker handler.

- [ ] **Step 4: Update agents/other/agent.py — add ML findings**

In `DEFAULT_SYSTEM_PROMPT`, add steps 4-6:
```
4. Call get_ml_scores(review_id) to retrieve ML pre-screening results
5. For each row where rf_prediction is 'under_scored', emit a finding:
   "[ML] Classifier flagged row as under-scored AP — review S/O/D values"
6. For each row where rf_prediction is 'over_scored', emit:
   "[ML] Classifier flagged row as over-scored AP — may inflate risk register"
7. For each row where anomaly is true, emit:
   "[ML] Anomaly detector: unusual S/O/D combination — verify scoring"
```
Replace Strands Agent with thin invoker handler.

- [ ] **Step 5: Verify all four agent files import cleanly**

```bash
python3 -c "
import agents.failure_mode.agent
import agents.structural.agent
import agents.regulatory.agent
import agents.other.agent
print('all ok')
"
```
Expected: `all ok`

- [ ] **Step 6: Commit**

```bash
git add agents/failure_mode/agent.py agents/structural/agent.py agents/regulatory/agent.py agents/other/agent.py
git commit -m "feat(agents): migrate all 4 specialist agents to AgentCore thin invokers; add ontology+ML tool references"
```

---

## Task 9: MCP Tools Lambda + Analyst Migration + API JWT

**Files:**
- Create: `lambdas/agentcore_tools/handler.py`
- Modify: `agents/analyst/agent.py`
- Modify: `lambdas/api/handler.py`

- [ ] **Step 1: Create lambdas/agentcore_tools/__init__.py (empty)**

```bash
touch lambdas/agentcore_tools/__init__.py
```

- [ ] **Step 2: Create lambdas/agentcore_tools/handler.py**

```python
"""
lambdas/agentcore_tools/handler.py — AgentCore Gateway MCP tools Lambda.
Deployed as 'dfmea-mcp-tools'. Routes all 10 tool invocations from AgentCore Gateway.

Request: {"tool": "<name>", "parameters": {...}, "sessionAttributes": {...}}
Response: {"result": <tool_return_value>}
"""
from __future__ import annotations
import json, logging, os, sys
log = logging.getLogger(__name__)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def _tools():
    from agents.shared.tools import (
        search_failure_mode_kb, search_regulatory_kb,
        get_review_context, get_cad_anchors, validate_shacl, write_finding,
    )
    from agents.shared.ontology_tools import (
        get_component_failure_modes, get_component_standards, get_ontology_subclasses,
    )
    from agents.shared.ml_scorer import get_ml_scores
    return {
        "search_failure_mode_kb":      search_failure_mode_kb,
        "search_regulatory_kb":        search_regulatory_kb,
        "get_review_context":          get_review_context,
        "get_cad_anchors":             get_cad_anchors,
        "validate_shacl":              validate_shacl,
        "write_finding":               write_finding,
        "get_component_failure_modes": get_component_failure_modes,
        "get_component_standards":     get_component_standards,
        "get_ontology_subclasses":     get_ontology_subclasses,
        "get_ml_scores":               get_ml_scores,
    }


def handler(event, context):
    inbound = (event.get("sessionAttributes") or {}).get("inbound_token", "")
    if inbound:
        log.info("[mcp-tools] inbound_token len=%d", len(inbound))

    tool_name  = event.get("tool", "")
    parameters = event.get("parameters") or {}
    if not tool_name:
        return {"error": "missing 'tool' in request"}

    registry = _tools()
    fn = registry.get(tool_name)
    if fn is None:
        return {"error": f"unknown tool: {tool_name}", "available": list(registry)}
    try:
        return {"result": fn(**parameters)}
    except Exception as exc:
        log.error("[mcp-tools] %s error: %s", tool_name, exc)
        return {"error": str(exc)}
```

- [ ] **Step 3: Update agents/analyst/agent.py — synthesis thin invoker, keep run_report**

In `agents/analyst/agent.py`, replace the `run_synthesis` function body:

```python
def run_synthesis(event: dict) -> dict:
    """Thin invoker: calls AgentCore Runtime analyst agent for synthesis."""
    import boto3
    review_id    = event["review_id"]
    frontend_jwt = event.get("frontend_jwt", "")
    region       = os.environ.get("AWS_REGION", "us-east-1")
    agent_id     = os.environ.get("AGENTCORE_AGENT_ID", "")

    client = boto3.client("bedrock-agentcore-runtime", region_name=region)
    resp = client.invoke_agent(
        agentId=agent_id,
        inputText=json.dumps({"review_id": review_id, "stage": "synthesis"}),
        sessionAttributes={"inbound_token": frontend_jwt, "review_id": review_id},
    )
    return {"status": "ok", "review_id": review_id, "stage": "synthesis", "output": resp.get("output", {})}
```

Remove the Strands Agent import and `create_analyst_agent()` function. Keep all of `run_report()` and `handler()` unchanged.

- [ ] **Step 4: Update lambdas/api/handler.py — extract JWT and pass to SFN**

Find the `start_execution` call in the POST /reviews handler. Add JWT extraction before it:

```python
    auth_header  = (event.get("headers") or {}).get("Authorization") or \
                   (event.get("headers") or {}).get("authorization") or ""
    frontend_jwt = auth_header.replace("Bearer ", "").strip()
```

In the SFN input JSON, add `"frontend_jwt": frontend_jwt`.

- [ ] **Step 5: Verify all three files import cleanly**

```bash
python3 -c "
import lambdas.agentcore_tools.handler
import agents.analyst.agent
import lambdas.api.handler
print('all ok')
"
```
Expected: `all ok`

- [ ] **Step 6: Commit**

```bash
git add lambdas/agentcore_tools/__init__.py lambdas/agentcore_tools/handler.py agents/analyst/agent.py lambdas/api/handler.py
git commit -m "feat(agentcore): MCP tools Lambda; analyst synthesis thin invoker; API JWT passthrough"
```

---

## Task 10: CDK Infrastructure Changes

**Files:**
- Modify: `infra/stacks/auth_stack.py`
- Modify: `infra/stacks/agent_stack.py`
- Create: `infra/stacks/agentcore_stack.py`
- Modify: `infra/app.py`

- [ ] **Step 1: Update auth_stack.py — add M2M pool**

Add to `__init__` before the NagSuppressions block:

```python
        from aws_cdk import aws_secretsmanager as secretsmanager

        # ── M2M User Pool (AgentCore → Gateway JWT auth) ──────────────────────
        self.m2m_user_pool = cognito.UserPool(
            self, "DfmeaM2MUserPool",
            user_pool_name="dfmea-m2m-userpool",
            self_sign_up_enabled=False,
            removal_policy=RemovalPolicy.RETAIN,
        )
        rs = self.m2m_user_pool.add_resource_server(
            "GatewayResourceServer",
            identifier="dfmea-gateway",
            scopes=[cognito.ResourceServerScope(
                scope_name="tools.invoke",
                scope_description="Invoke AgentCore Gateway tools",
            )],
        )
        self.m2m_client = self.m2m_user_pool.add_client(
            "DfmeaM2MClient",
            user_pool_client_name="dfmea-m2m-client",
            generate_secret=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[cognito.OAuthScope.resource_server(rs, rs.scopes[0])],
            ),
            access_token_validity=cdk.Duration.hours(1),
            enable_token_revocation=True,
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
        )
        self.m2m_user_pool.add_domain(
            "M2MDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=f"dfmea-m2m-{self.account}"),
        )
        self.m2m_secret = secretsmanager.Secret(
            self, "DfmeaM2MSecret",
            secret_name="dfmea/m2m-client-credentials",
            description="AgentCore M2M client credentials",
            encryption_key=foundation.cmk,
        )
        NagSuppressions.add_resource_suppressions(
            self.m2m_user_pool,
            [
                {"id": "AwsSolutions-COG2", "reason": "M2M pool has no human users — MFA not applicable"},
                {"id": "AwsSolutions-COG3", "reason": "AdvancedSecurityMode not required for M2M pool"},
            ],
        )
        cdk.CfnOutput(self, "M2MUserPoolId", value=self.m2m_user_pool.user_pool_id, export_name="DfmeaM2MUserPoolId")
        cdk.CfnOutput(self, "M2MClientId",   value=self.m2m_client.user_pool_client_id, export_name="DfmeaM2MClientId")
        cdk.CfnOutput(self, "M2MSecretArn",  value=self.m2m_secret.secret_arn, export_name="DfmeaM2MSecretArn")
```

- [ ] **Step 2: Update agent_stack.py — thin invoker Lambda names + ML env var**

In `common_env`, add:
```python
            "ML_MODELS_BUCKET_NAME":  data.ml_models_bucket.bucket_name,
            "RISK_SCORES_TABLE_NAME": data.risk_scores_table.table_name,
```

Add bedrock-agentcore IAM permission after the existing Bedrock policy:
```python
        agent_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgent"],
            resources=["*"],
        ))
```

Replace the five `_agent_lambda(...)` calls with thin invoker names:
- `"AnalystAgentFn"` → `"AnalystInvokerFn"`, name `"invoke-analyst"`
- `"FailureModeAgentFn"` → `"FailureModeInvokerFn"`, name `"invoke-failure-mode"`
- `"StructuralAgentFn"` → `"StructuralInvokerFn"`, name `"invoke-structural"`
- `"RegulatoryAgentFn"` → `"RegulatoryInvokerFn"`, name `"invoke-regulatory"`
- `"OtherAgentFn"` → `"OtherInvokerFn"`, name `"invoke-other"`

Each gets `extra_env={"AGENTCORE_AGENT_ID": "PLACEHOLDER"}` (patched post-deploy).

- [ ] **Step 3: Create infra/stacks/agentcore_stack.py**

```python
"""
infra/stacks/agentcore_stack.py — AgentCore MCP tools Lambda.
AgentCore Runtime agents and Gateway are registered post-deploy via AWS CLI
in deploy.sh (CDK L2 constructs for AgentCore Runtime not yet available).
"""
from __future__ import annotations
import aws_cdk as cdk
from aws_cdk import aws_iam as iam, aws_lambda as lambda_, aws_logs as logs, Duration, RemovalPolicy
from constructs import Construct
from cdk_nag import NagSuppressions
from .foundation_stack import DfmeaFoundationStack
from .data_stack import DfmeaDataStack


class DfmeaAgentCoreStack(cdk.Stack):
    def __init__(self, scope, construct_id, foundation: DfmeaFoundationStack,
                 data: DfmeaDataStack, neptune=None, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        cmk = foundation.cmk
        vpc = foundation.vpc
        private = cdk.aws_ec2.SubnetSelection(subnet_type=cdk.aws_ec2.SubnetType.PRIVATE_WITH_EGRESS)

        role = iam.Role(self, "McpRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaVPCAccessExecutionRole")])
        role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/dfmea-*"]))
        for t in [data.findings_table, data.risk_scores_table, data.reviews_table, data.retrieval_table]:
            t.grant_read_write_data(role)
        for b in [data.processed_bucket, data.reports_bucket, data.ml_models_bucket, data.ontology_bucket]:
            b.grant_read_write(role)
        role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel","bedrock:Retrieve","bedrock:RetrieveAndGenerate"],
            resources=["*"]))
        if neptune:
            role.add_to_policy(iam.PolicyStatement(
                actions=["neptune-db:connect","neptune-db:ReadDataViaQuery"],
                resources=[f"arn:aws:neptune-db:{self.region}:{self.account}:{neptune.cluster.attr_cluster_resource_id}/*"]))
        cmk.grant_encrypt_decrypt(role)

        NagSuppressions.add_resource_suppressions(role, [
            {"id":"AwsSolutions-IAM5","reason":"Bedrock+logs require wildcard; logs scoped to /aws/lambda/dfmea-*"},
            {"id":"AwsSolutions-IAM4","reason":"AWSLambdaVPCAccessExecutionRole required for VPC Lambda",
             "appliesTo":["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"]},
        ], apply_to_children=True)

        env = {
            "REVIEWS_TABLE_NAME":     data.reviews_table.table_name,
            "FINDINGS_TABLE_NAME":    data.findings_table.table_name,
            "RISK_SCORES_TABLE_NAME": data.risk_scores_table.table_name,
            "PROCESSED_BUCKET_NAME":  data.processed_bucket.bucket_name,
            "REPORTS_BUCKET_NAME":    data.reports_bucket.bucket_name,
            "ML_MODELS_BUCKET_NAME":  data.ml_models_bucket.bucket_name,
            "BEDROCK_MODEL_ID":       "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "LOG_LEVEL":              "INFO",
        }
        if neptune:
            env["NEPTUNE_ENDPOINT"] = neptune.cluster.attr_endpoint
            env["NEPTUNE_PORT"]     = "8182"

        self.mcp_tools_fn = lambda_.Function(self, "McpToolsFn",
            function_name="dfmea-mcp-tools",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambdas/agentcore_tools/handler.handler",
            code=lambda_.Code.from_asset("..", exclude=["cdk.out",".venv","**/__pycache__",
                "**/*.pyc","ui/node_modules","ui/dist",".git","**/.pytest_cache"]),
            role=role, environment=env, environment_encryption=cmk,
            timeout=Duration.seconds(120), memory_size=1024,
            vpc=vpc, vpc_subnets=private,
            log_retention=logs.RetentionDays.ONE_MONTH,
            tracing=lambda_.Tracing.ACTIVE)
        NagSuppressions.add_resource_suppressions(self.mcp_tools_fn,
            [{"id":"AwsSolutions-L1","reason":"Python 3.12 is latest available runtime"}])

        cdk.CfnOutput(self,"McpToolsFnArn", value=self.mcp_tools_fn.function_arn, export_name="DfmeaMcpToolsFnArn")
        cdk.CfnOutput(self,"McpToolsFnName",value=self.mcp_tools_fn.function_name,export_name="DfmeaMcpToolsFnName")

        NagSuppressions.add_stack_suppressions(self,[
            {"id":"AwsSolutions-IAM4","reason":"CDK LogRetention uses AWSLambdaBasicExecutionRole"},
            {"id":"AwsSolutions-IAM5","reason":"CDK LogRetention DefaultPolicy requires wildcard"},
        ])
```

- [ ] **Step 4: Update infra/app.py**

Add import:
```python
from stacks.agentcore_stack import DfmeaAgentCoreStack
```

Add after `knowledge_base`:
```python
agentcore = DfmeaAgentCoreStack(
    app, "DfmeaAgentCoreStack",
    foundation=foundation, data=data, neptune=neptune, env=env,
)
```

- [ ] **Step 5: CDK synth all 13 stacks**

```bash
cd /mnt/c/Users/mahasrid/Downloads/EXT-Repo/DFMEA_Demo/dfmea-prototype/infra
cdk synth --all --quiet
```
Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add infra/stacks/auth_stack.py infra/stacks/agent_stack.py infra/stacks/agentcore_stack.py infra/app.py
git commit -m "feat(infra): M2M Cognito pool; thin invoker Lambdas; AgentCoreStack with MCP tools Lambda"
```

---

## Task 11: Rewrite deploy.sh

**Files:**
- Modify: `scripts/deploy.sh`

- [ ] **Step 1: Rewrite deploy.sh**

Key changes from current version:

**(a) Add Step 5 — Synthetic data generation** (before ML training):
```bash
step "5 / Generate synthetic training data"
if [[ "$SKIP_SEED" != "1" ]]; then
  cd "$REPO_ROOT"
  python3 sample-data/scripts/generate_base.py
  python3 sample-data/scripts/generate_variants.py
  python3 sample-data/scripts/expand_base.py
  ok "Synthetic data generated in sample-data/variants/"
fi
```

**(b) Update Step 5 (now Step 6) — ML training + upload** to always run training:
```bash
step "6 / Train ML models + upload to S3"
ML_MODELS_BUCKET="dfmea-ml-models-${CDK_DEFAULT_ACCOUNT}"
cd "$REPO_ROOT"
python3 ml/train.py
aws s3 sync "$REPO_ROOT/sample-data/models/" "s3://${ML_MODELS_BUCKET}/v1/" \
  --include "*.joblib" --include "*.json"
ok "ML models trained and uploaded"
```

**(c) Update bundle step (Step 6.5) with new deps and Lambda names**:
```bash
pip install scikit-learn==1.5.2 joblib==1.4.2 numpy==1.26.4 \
  pyshacl==0.26.0 rdflib==7.1.1 \
  opensearch-py==2.8.0 requests-aws4auth==1.3.1 \
  openpyxl jsonschema \
  -t /tmp/dfmea-agent-bundle \
  --python-version 3.12 --platform manylinux2014_x86_64 \
  --implementation cp --only-binary=:all: -q
# Lambda names changed from dfmea-agent-* to dfmea-invoke-* + dfmea-mcp-tools
for fn in dfmea-invoke-analyst dfmea-invoke-failure-mode dfmea-invoke-structural \
          dfmea-invoke-regulatory dfmea-invoke-other dfmea-mcp-tools; do
  aws lambda update-function-code --function-name "$fn" \
    --zip-file fileb:///tmp/dfmea-agents.zip --output text --query 'FunctionName' >/dev/null
done
```

**(d) Add ML env var patch in Step 7**:
```bash
# Patch ML_MODELS_BUCKET_NAME and RISK_SCORES_TABLE_NAME on all invoker + mcp-tools Lambdas
for fn in dfmea-invoke-analyst dfmea-invoke-failure-mode dfmea-invoke-structural \
          dfmea-invoke-regulatory dfmea-invoke-other dfmea-mcp-tools; do
  CURR=$(aws lambda get-function-configuration --function-name "$fn" \
    --query 'Environment.Variables' --output json 2>/dev/null || echo '{}')
  UPDATED=$(echo "$CURR" | python3 -c "
import sys,json; e=json.load(sys.stdin)
e['ML_MODELS_BUCKET_NAME']='${ML_MODELS_BUCKET}'
e['RISK_SCORES_TABLE_NAME']='dfmea-risk-scores'
print(json.dumps({'Variables':e}))")
  aws lambda update-function-configuration --function-name "$fn" \
    --environment "$UPDATED" --output text --query 'FunctionName' >/dev/null
done
```

**(e) Add DfmeaAgentCoreStack to CDK deploy order** (after KnowledgeBase):
```bash
log "Stack 10/13 — DfmeaAgentCoreStack"
cdk deploy DfmeaAgentCoreStack --require-approval never
```

**(f) Add Step 7.5 — AgentCore Gateway + agent registration** (using AWS CLI):
```bash
step "7.5 / Register AgentCore Gateway and agents"
MCP_TOOLS_ARN=$(_cfn_output DfmeaAgentCoreStack DfmeaMcpToolsFnArn)

# Create AgentCore Gateway
GATEWAY_ID=$(aws bedrock-agentcore create-gateway \
  --name "dfmea-gateway" \
  --description "DFMEA MCP tools gateway" \
  --query 'gatewayId' --output text 2>/dev/null || echo "")

# Register each MCP tool
for tool_name in search_failure_mode_kb search_regulatory_kb get_review_context \
  get_cad_anchors validate_shacl write_finding get_component_failure_modes \
  get_component_standards get_ontology_subclasses get_ml_scores; do
  aws bedrock-agentcore create-gateway-tool \
    --gateway-id "$GATEWAY_ID" \
    --name "$tool_name" \
    --lambda-arn "$MCP_TOOLS_ARN" 2>/dev/null || warn "  Tool $tool_name registration skipped"
done

# Create 5 AgentCore Runtime agents
for agent_name in failure-mode structural regulatory schema analyst; do
  AGENT_ID=$(aws bedrock-agentcore create-agent \
    --name "dfmea-${agent_name}" \
    --model-id "us.anthropic.claude-haiku-4-5-20251001-v1:0" \
    --gateway-id "$GATEWAY_ID" \
    --query 'agentId' --output text 2>/dev/null || echo "")
  if [[ -n "$AGENT_ID" && "$AGENT_ID" != "None" ]]; then
    INVOKER_FN="dfmea-invoke-${agent_name}"
    CURR=$(aws lambda get-function-configuration --function-name "$INVOKER_FN" \
      --query 'Environment.Variables' --output json 2>/dev/null || echo '{}')
    UPDATED=$(echo "$CURR" | python3 -c "
import sys,json; e=json.load(sys.stdin)
e['AGENTCORE_AGENT_ID']='${AGENT_ID}'
print(json.dumps({'Variables':e}))")
    aws lambda update-function-configuration --function-name "$INVOKER_FN" \
      --environment "$UPDATED" --output text --query 'FunctionName' >/dev/null
    ok "  AgentCore agent dfmea-${agent_name} registered, ID=${AGENT_ID}"
  else
    warn "  AgentCore agent dfmea-${agent_name} registration skipped — check AWS CLI version"
  fi
done
```

**(g) Update stack count** in all log messages from 12 to 13.

- [ ] **Step 2: Validate bash syntax**

```bash
bash -n scripts/deploy.sh && echo "syntax ok"
```
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add scripts/deploy.sh
git commit -m "fix(deploy): 16-step sequence — synthetic data gen, ML training, pyshacl bundle, AgentCore registration, 13 stacks"
```

---

## Task 12: Rewrite destroy.sh

**Files:**
- Modify: `scripts/destroy.sh`

- [ ] **Step 1: Replace the DynamoDB table list with correct names**

Change:
```bash
TABLES=(
  "dfmea-reviews"
  "dfmea-findings"
  "dfmea-ap-lookup"
  "dfmea-hitl-tasks"
  "dfmea-ws-connections"
  "dfmea-audit-log"
  "dfmea-ml-scores"
)
```
to:
```bash
TABLES=(
  "dfmea-reviews"
  "dfmea-structural-decomposition"
  "dfmea-retrieval-results"
  "dfmea-analysis-findings"
  "dfmea-synthesis-results"
  "dfmea-risk-scores"
  "dfmea-ap-lookup"
)
```

- [ ] **Step 2: Add dfmea-audit bucket to BUCKETS list**

Add `"dfmea-audit-${CDK_DEFAULT_ACCOUNT}"` to the BUCKETS array.

- [ ] **Step 3: Add notifier Lambda + role cleanup (new Step before CDK destroy)**

```bash
step "X / Delete dfmea-notifier Lambda and IAM role (created outside CDK)"
aws lambda delete-function --function-name dfmea-notifier 2>/dev/null \
  && ok "dfmea-notifier deleted" || warn "dfmea-notifier not found"
aws iam detach-role-policy --role-name dfmea-notifier-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole 2>/dev/null || true
aws iam delete-role-policy --role-name dfmea-notifier-role --policy-name AllowSES 2>/dev/null || true
aws iam delete-role --role-name dfmea-notifier-role 2>/dev/null \
  && ok "dfmea-notifier-role deleted" || warn "dfmea-notifier-role not found"
```

- [ ] **Step 4: Add SSM + Bedrock KB cleanup**

```bash
step "X / Delete SSM parameters and Bedrock Knowledge Bases"
FM_KB_ID=$(aws ssm get-parameter --name /dfmea/failure_mode_kb_id --query 'Parameter.Value' --output text 2>/dev/null || echo "")
REG_KB_ID=$(aws ssm get-parameter --name /dfmea/regulatory_kb_id --query 'Parameter.Value' --output text 2>/dev/null || echo "")
for param in /dfmea/failure_mode_kb_id /dfmea/regulatory_kb_id; do
  aws ssm delete-parameter --name "$param" 2>/dev/null && ok "Deleted $param" || warn "$param not found"
done
for kb_id in "$FM_KB_ID" "$REG_KB_ID"; do
  [[ -n "$kb_id" && "$kb_id" != "None" ]] && \
    aws bedrock-agent delete-knowledge-base --knowledge-base-id "$kb_id" 2>/dev/null \
    && ok "Deleted KB $kb_id" || true
done
```

- [ ] **Step 5: Add AgentCore cleanup**

```bash
step "X / Delete AgentCore Runtime agents and Gateway"
for agent_name in failure-mode structural regulatory schema analyst; do
  AGENT_ID=$(aws bedrock-agentcore list-agents --query "agents[?name=='dfmea-${agent_name}'].agentId | [0]" \
    --output text 2>/dev/null || echo "")
  [[ -n "$AGENT_ID" && "$AGENT_ID" != "None" ]] && \
    aws bedrock-agentcore delete-agent --agent-id "$AGENT_ID" 2>/dev/null \
    && ok "Deleted AgentCore agent dfmea-${agent_name}" || true
done
GATEWAY_ID=$(aws bedrock-agentcore list-gateways --query "gateways[?name=='dfmea-gateway'].gatewayId | [0]" \
  --output text 2>/dev/null || echo "")
[[ -n "$GATEWAY_ID" && "$GATEWAY_ID" != "None" ]] && \
  aws bedrock-agentcore delete-gateway --gateway-id "$GATEWAY_ID" 2>/dev/null \
  && ok "Deleted AgentCore Gateway" || true
```

- [ ] **Step 6: Add Neptune deletion_protection disable before CDK destroy**

```bash
step "X / Disable Neptune deletion_protection"
aws neptune modify-db-cluster \
  --db-cluster-identifier dfmea-neptune \
  --no-deletion-protection 2>/dev/null \
  && ok "Neptune deletion_protection disabled" || warn "Neptune cluster not found"
sleep 30
```

- [ ] **Step 7: Update CDK STACKS array to all 13 in correct reverse order**

```bash
STACKS=(
  DfmeaMonitoringStack
  DfmeaFrontendStack
  DfmeaApiStack
  DfmeaAgentCoreStack
  DfmeaKnowledgeBaseStack
  DfmeaSearchStack
  DfmeaOrchestrationStack
  DfmeaAgentStack
  DfmeaNeptuneStack
  DfmeaAuroraStack
  DfmeaDataStack
  DfmeaAuthStack
  DfmeaFoundationStack
)
```

- [ ] **Step 8: Add local artifact wipe at the end**

```bash
step "X / Wipe local artifacts"
rm -rf "$REPO_ROOT/sample-data/variants/"
rm -f  "$REPO_ROOT/sample-data/models/"*.joblib
rm -f  "$REPO_ROOT/sample-data/models/training_report.json"
rm -rf /tmp/dfmea-agent-bundle /tmp/dfmea-agents.zip
ok "Local artifacts wiped"
```

- [ ] **Step 9: Validate bash syntax**

```bash
bash -n scripts/destroy.sh && echo "syntax ok"
```
Expected: `syntax ok`

- [ ] **Step 10: Commit**

```bash
git add scripts/destroy.sh
git commit -m "fix(destroy): correct table names, all 13 stacks, Neptune deletion_protection, notifier+SSM+KB+AgentCore cleanup, local artifact wipe"
```

---

## Task 13: Final Verification

- [ ] **Step 1: Run full unit test suite**

```bash
cd /mnt/c/Users/mahasrid/Downloads/EXT-Repo/DFMEA_Demo/dfmea-prototype
pip install pytest pyshacl==0.26.0 rdflib==7.1.1 scikit-learn==1.5.2 joblib==1.4.2 numpy==1.26.4 -q
pytest tests/unit/ -v
```
Expected: 14 tests, all pass.

- [ ] **Step 2: CDK synth all 13 stacks**

```bash
cd infra && cdk synth --all --quiet
```
Expected: No errors.

- [ ] **Step 3: Validate both scripts**

```bash
bash -n scripts/deploy.sh && echo "deploy.sh ok"
bash -n scripts/destroy.sh && echo "destroy.sh ok"
```
Expected: both `ok`.

- [ ] **Step 4: Deploy to AWS**

```bash
export OPS_EMAIL=... ADMIN_EMAIL=... ADMIN_PASSWORD=... REVIEWER_EMAIL=...
bash scripts/deploy.sh
```

- [ ] **Step 5: End-to-end smoke test**

1. Navigate to the CloudFront URL printed at the end of deploy.sh
2. Upload `sample-data/base/b_pillar_base.json` via the UI
3. Approve Gate 1 (Intake) — verify review enters analysis stage
4. Approve Gate 2 (CAD)
5. Wait for Gate 3 activation — inspect findings in UI:
   - Verify findings with `[ML]` prefix appear (ML pre-screening results)
   - Verify findings referencing `buckling_under_load`, `weld_fracture`, `corrosion_at_joint` (ontology results)
6. Approve Gate 3 (Analysis)
7. Approve Gate 4 (Final) — verify PDF report downloads successfully
8. Check CloudWatch Logs for `dfmea-mcp-tools` Lambda — confirm `inbound_token` lines present

- [ ] **Step 6: Update MEMORY.md with any new patterns or issues found**

