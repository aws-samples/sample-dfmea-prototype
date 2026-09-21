# DFMEA — Ontology, ML, and AgentCore Migration Design
**Date:** 2026-07-12
**Status:** Approved (Sections 1–7)

---

## Problem Statement

The DFMEA Agentic Review System has three subsystems that are designed but never wired at runtime:

1. **Ontology** — `ontology/ontology.json-ld` and `ontology/shapes.ttl` exist but are never queried by agents; `ontology_client.py` has a namespace bug and is never imported
2. **ML models** — `ml/train.py` and `ml/features.py` exist but are never called; `dfmea-risk-scores` DynamoDB table is provisioned but never written to
3. **AgentCore** — agents run as heavy Strands SDK Lambda functions instead of managed Bedrock AgentCore Runtime agents

Additionally, `scripts/deploy.sh` and `scripts/destroy.sh` have critical gaps that leave AWS resources orphaned after teardown.

---

## Section 1 — Ontology Data Model Fixes

### 1.1 ontology/ontology.json-ld

**Add missing graph edges:**

- `subClassOf` triples for all 3 failure mode instances so SPARQL `subClassOf*` traversal returns results
- `hasFailureMode` edges connecting each component to its known failure modes
- `mustComplyWith` edges connecting components to applicable standards (FMVSS_214, FMVSS_216, ISO_26262)

**Component → FailureMode mappings:**
- `B-Pillar_InnerPanel` → `buckling_under_load`, `weld_fracture`
- `B-Pillar_OuterPanel` → `buckling_under_load`, `corrosion_at_joint`
- `B-Pillar_Reinforcement` → `buckling_under_load`
- `B-Pillar_Hinge` → `weld_fracture`, `corrosion_at_joint`

**Component → Standard mappings:**
- All 4 components → `FMVSS_214`, `FMVSS_216`, `ISO_26262`

### 1.2 ontology/shapes.ttl

Fix `actionPriority` constraint: change `sh:in ( "High" "Medium" "Low" )` to `sh:in ( "H" "M" "L" )` to match runtime AIAG-VDA values.

### 1.3 agents/shared/ontology_client.py

- Fix `ONTOLOGY_PREFIX`: `"https://dfmea.example.com/ontology#"` → `"https://dfmea.example.com/ontology/v0.1.0#"`
- Add `get_failure_modes_for_component(component_name)` querying `ont:hasFailureMode` directly (avoids broken `subClassOf*` path)
- Keep existing `get_related_standards()`, `get_subclasses()` (fixed by prefix correction)

### 1.4 agents/shared/tools.py — replace validate_shacl stub

Replace hardcoded Python checks with real pyshacl+rdflib implementation:

```python
def validate_shacl(rows: list[dict]) -> dict:
    from rdflib import Graph, Literal, URIRef, Namespace
    from pyshacl import validate as shacl_validate

    ONT = Namespace("https://dfmea.example.com/ontology/v0.1.0#")
    data_graph = Graph()
    # Build RDF triples from each normalised row
    for row in rows:
        node = URIRef(f"{ONT}row_{uuid.uuid4().hex[:8]}")
        data_graph.add((node, RDF.type, ONT.FailureMode))
        data_graph.add((node, ONT.severity, Literal(row.get("severity"))))
        data_graph.add((node, ONT.occurrence, Literal(row.get("occurrence"))))
        data_graph.add((node, ONT.detection, Literal(row.get("detection"))))
        if row.get("action_priority"):
            data_graph.add((node, ONT.actionPriority, Literal(row["action_priority"])))

    shapes_path = Path(__file__).parent.parent.parent / "ontology" / "shapes.ttl"
    conforms, _, report_text = shacl_validate(
        data_graph, shacl_graph=str(shapes_path), inference="rdfs"
    )
    return {"conforms": conforms, "violations": [] if conforms else _parse_shacl_report(report_text)}
```

### 1.5 agents/shared/ontology_tools.py (new file)

`@tool`-decorated wrappers calling `ontology_client.py`:

- `get_component_failure_modes(component_name: str)` — calls `get_failure_modes_for_component()`
- `get_component_standards(component_name: str)` — calls `get_related_standards()`
- `get_ontology_subclasses(class_name: str)` — calls `get_subclasses()`

Wired into: `agents/failure_mode/agent.py`, `agents/structural/agent.py`, `agents/regulatory/agent.py`

---

## Section 2 — ML Pre-screening Integration

### 2.1 agents/shared/ml_scorer.py (new file)

```
score_rows(rows, review_id)
  → _load_models()            # lazy load RF + IsolationForest from S3 ml-models bucket
  → _derive_features(row)     # bridge missing fields from normalised schema
  → RF.predict()              # valid / under_scored / over_scored per row
  → IsolationForest.predict() # anomaly flag per row
  → write to dfmea-risk-scores DynamoDB
```

**Feature derivation bridge** (`_derive_features`):
- `functional_category`: keyword match on `function` field ("support"→structural, "seal"→NVH, "transmit"→load_path, "protect"→barrier)
- `component_type`: keyword match on `part_name` ("panel"→panel, "weld"→joining, "adhesive"→bonding, "reinforcement"→structural)
- `cause_type`: keyword match on `cause` ("corrosion"→material, "fatigue"→loading, "tolerance"→manufacturing, "impact"→loading)

**Graceful degradation**: any exception → log warning, return without raising; intake proceeds normally.

### 2.2 lambdas/intake/handler.py

After `_normalise_rows()`, before writing `normalised.json` to S3:

```python
try:
    from agents.shared.ml_scorer import score_rows
    score_rows(rows, review_id)
except Exception as e:
    logger.warning(f"ML pre-screen skipped: {e}")
```

### 2.3 agents/other/agent.py

Read `dfmea-risk-scores` for the review at agent start. Append ML-flagged rows as structured findings prefixed with `[ML]`:
- `under_scored` RF prediction → finding: "ML classifier flagged row as under-scored AP"
- `over_scored` RF prediction → finding: "ML classifier flagged row as over-scored AP"
- anomaly IsolationForest flag → finding: "ML anomaly detector: unusual S/O/D combination"

Analyst synthesises ML findings alongside LLM findings normally.

---

## Section 3 — Bedrock AgentCore Migration

### 3.1 Architecture

**Old:**
```
SFN → LambdaInvoke → dfmea-agent-{name} (Strands SDK, full agent loop in Lambda)
```

**New:**
```
SFN → LambdaInvoke → dfmea-invoke-{name} (thin Lambda, VPC)
                          → bedrock-agentcore:InvokeAgent
                              → AgentCore Runtime (agent loop, managed)
                                  → AgentCore Gateway (MCP)
                                      → dfmea-mcp-tools Lambda (VPC)
```

### 3.2 Agents (5 total)

| AgentCore Agent | Replaces | System Prompt Source |
|-----------------|----------|----------------------|
| `dfmea-failure-mode` | `agents/failure_mode/agent.py` | Migrated from Strands system_prompt |
| `dfmea-structural` | `agents/structural/agent.py` | Migrated |
| `dfmea-regulatory` | `agents/regulatory/agent.py` | Migrated |
| `dfmea-schema` | `agents/other/agent.py` | Migrated |
| `dfmea-analyst` | `agents/analyst/agent.py` run_synthesis stage | Migrated |

**Not migrated to AgentCore:** `run_report` stage in `agents/analyst/agent.py` — pure Python PDF builder, stays as Lambda.

### 3.3 MCP Tools (10 total)

All exposed via AgentCore Gateway, backed by single `dfmea-mcp-tools` Lambda (VPC private subnets):

| Tool | Source |
|------|--------|
| `search_failure_mode_kb` | `agents/shared/tools.py` |
| `search_regulatory_kb` | `agents/shared/tools.py` |
| `get_review_context` | `agents/shared/tools.py` |
| `get_cad_anchors` | `agents/shared/tools.py` |
| `write_finding` | `agents/shared/tools.py` |
| `get_component_failure_modes` | `agents/shared/ontology_tools.py` |
| `get_component_standards` | `agents/shared/ontology_tools.py` |
| `get_ontology_subclasses` | `agents/shared/ontology_tools.py` |
| `validate_shacl` | `agents/shared/tools.py` |
| `get_ml_scores` | `agents/shared/ml_scorer.py` |

### 3.4 Authentication

**AgentCore → Gateway (M2M):**
- New Cognito M2M user pool (`dfmea-m2m-userpool`)
- Resource server: `dfmea-gateway` with scope `tools.invoke`
- App client: `client_credentials` grant, no user auth
- Client ID + secret stored in Secrets Manager, KMS-encrypted with foundation CMK
- AgentCore Runtime retrieves JWT via client_credentials flow before calling Gateway

**Frontend → Pipeline (inbound auth):**
- Frontend Cognito ID token extracted in `lambdas/api/handler.py`
- Passed in SFN execution input as `frontend_jwt`
- Thin invoker Lambdas pass it as AgentCore `sessionAttributes.inbound_token`
- `dfmea-mcp-tools` Lambda logs `inbound_token` in CloudWatch for audit trail

### 3.5 CDK changes

- New `infra/stacks/agentcore_stack.py`: AgentCore Runtime agents (5), Gateway, tool registrations, `dfmea-mcp-tools` Lambda (VPC)
- `infra/stacks/auth_stack.py`: add M2M pool, resource server, app client, KMS-encrypted Secrets Manager secret
- `infra/stacks/agent_stack.py`: 5 full agent Lambdas → 5 thin invoker Lambdas (VPC private subnets), add `ML_MODELS_BUCKET_NAME` to `common_env`
- Stack deploy order: ...Agents → AgentCore → Orchestration...

---

## Section 5 — Lambda Bundle Changes

**Bundle additions to `scripts/deploy.sh` Step 7:**

```bash
pip install \
  strands-agents==0.1.7 \        # existing (removed for AgentCore migration)
  scikit-learn==1.5.2 \
  joblib==1.4.2 \
  numpy==1.26.4 \
  pyshacl==0.26.0 \
  rdflib==7.1.1 \
  opensearch-py==2.8.0 \
  requests-aws4auth==1.3.1 \
  -t bundle/
```

Note: `strands-agents` is removed from the bundle after AgentCore migration is complete. Only `dfmea-mcp-tools` Lambda needs the tool implementations; thin invoker Lambdas need only `boto3`.

`agent_stack.py` `common_env` addition:
```python
"ML_MODELS_BUCKET_NAME": data.ml_models_bucket.bucket_name,
```

---

## Section 6 — deploy.sh Complete Fix

**Sequence (linear, no skippable steps):**

```
Step 1:  pip install -r requirements.txt -r requirements-ml.txt
Step 2:  npm install (ui/)
Step 3:  cdk bootstrap
Step 4:  CDK deploy 13 stacks in dependency order
           Foundation→Auth→Data→Aurora→Neptune→Agents→
           Orchestration→Search→KnowledgeBase→AgentCore→API→Frontend→Monitoring
Step 5:  Synthetic data generation
           python sample-data/scripts/generate_base.py
           python sample-data/scripts/generate_variants.py
           python sample-data/scripts/expand_base.py
Step 6:  ML training + upload
           python ml/train.py
           aws s3 cp sample-data/models/ s3://dfmea-ml-models-{ACCOUNT}/ --recursive
Step 7:  Build Lambda bundle + deploy all Lambda functions
           Includes: scikit-learn, joblib, numpy, pyshacl, rdflib, opensearch-py, requests-aws4auth
Step 8:  Post-deploy env var patches
           REVIEW_SM_ARN              → dfmea-intake, dfmea-api
           WEBSOCKET_ENDPOINT         → dfmea-ws-fanout
           ML_MODELS_BUCKET_NAME      → all invoker Lambdas + dfmea-mcp-tools
           AGENTCORE_AGENT_ID         → each thin invoker Lambda (one env var per Lambda,
                                         value = AgentCore Runtime agent ID for that agent)
           GATEWAY_URL                → dfmea-mcp-tools Lambda
Step 9:  Neptune ontology load
Step 10: Bedrock KB ingestion jobs
Step 11: AgentCore Gateway tool registration (10 tools → dfmea-mcp-tools ARN)
Step 12: Retrieve M2M client secret → set on dfmea-mcp-tools + invoker Lambdas
Step 13: Deploy dfmea-notifier Lambda + SNS subscription
Step 14: Create Cognito admin user
Step 15: Build React frontend + sync to CloudFront
Step 16: Print portal URL
```

---

## Section 7 — destroy.sh Complete Fix

**Sequence:**

```
Step 1:  Empty all 7 S3 buckets (versioned + non-versioned)
           dfmea-uploads-{ACCOUNT}
           dfmea-processed-{ACCOUNT}
           dfmea-reports-{ACCOUNT}
           dfmea-audit-{ACCOUNT}
           dfmea-ontology-{ACCOUNT}
           dfmea-kb-documents-{ACCOUNT}
           dfmea-ml-models-{ACCOUNT}

Step 2:  Delete dfmea-notifier Lambda + dfmea-notifier-role IAM role
           (created outside CDK — not destroyed by CDK destroy)

Step 3:  Delete SSM parameters
           /dfmea/failure_mode_kb_id
           /dfmea/regulatory_kb_id

Step 4:  Delete Bedrock Knowledge Bases
           (created by create_bedrock_kb.py outside CDK)

Step 5:  Deregister AgentCore Gateway MCP tools + delete Gateway
Step 6:  Delete AgentCore Runtime agents (5)

Step 7:  Delete DynamoDB tables explicitly (RemovalPolicy.RETAIN — CDK will not delete)
           dfmea-reviews
           dfmea-structural-decomposition
           dfmea-retrieval-results
           dfmea-analysis-findings
           dfmea-synthesis-results
           dfmea-risk-scores
           dfmea-ap-lookup

Step 8:  Disable Neptune deletion_protection
           aws neptune modify-db-cluster \
             --db-cluster-identifier dfmea-neptune \
             --no-deletion-protection

Step 9:  CDK destroy 13 stacks in reverse order
           Monitoring→Frontend→API→AgentCore→KnowledgeBase→
           Search→Orchestration→Agents→Neptune→Aurora→
           Data→Auth→Foundation

Step 10: Wipe local artifacts
           rm -rf sample-data/variants/
           rm -rf sample-data/models/*.joblib
           rm -rf sample-data/models/training_report.json
           rm -rf bundle/
```

---

## Key Constraints (Operational Rules)

1. Local code changes first → deploy.sh/destroy.sh updated → deploy to AWS → test
2. Every Lambda in VPC private subnets
3. Every S3 bucket logs to foundation access log bucket
4. Every secret in Secrets Manager with KMS CMK encryption
5. AWS Security best practices enforced (cdk_nag AwsSolutionsChecks)
6. Latest stable package versions
7. deploy.sh and destroy.sh are single points of deployment and teardown — no manual steps

---

## Files Changed Summary

| File | Change Type |
|------|-------------|
| `ontology/ontology.json-ld` | Fix — add subClassOf, hasFailureMode, mustComplyWith edges |
| `ontology/shapes.ttl` | Fix — actionPriority values H/M/L |
| `agents/shared/ontology_client.py` | Fix — namespace prefix, add get_failure_modes_for_component |
| `agents/shared/tools.py` | Fix — replace validate_shacl stub with pyshacl |
| `agents/shared/ontology_tools.py` | New — @tool wrappers for ontology queries |
| `agents/shared/ml_scorer.py` | New — ML inference + DynamoDB write |
| `agents/failure_mode/agent.py` | Update — import ontology_tools |
| `agents/structural/agent.py` | Update — import ontology_tools |
| `agents/regulatory/agent.py` | Update — import ontology_tools |
| `agents/other/agent.py` | Update — read ML scores, emit ML findings |
| `agents/analyst/agent.py` | Update — synthesis stage → AgentCore; report stage stays |
| `lambdas/intake/handler.py` | Update — add ml_scorer.score_rows() call |
| `lambdas/api/handler.py` | Update — extract + pass frontend JWT to SFN input |
| `lambdas/agentcore_tools/handler.py` | New — MCP tools Lambda handler (deployed as `dfmea-mcp-tools`) |
| `infra/stacks/agentcore_stack.py` | New — AgentCore Runtime, Gateway, tools Lambda |
| `infra/stacks/auth_stack.py` | Update — M2M pool, resource server, KMS secret |
| `infra/stacks/agent_stack.py` | Update — thin invoker Lambdas, ML_MODELS_BUCKET_NAME |
| `scripts/deploy.sh` | Fix — full 16-step sequence |
| `scripts/destroy.sh` | Fix — all gaps, correct names, local cleanup |
