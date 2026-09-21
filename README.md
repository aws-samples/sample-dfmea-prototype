# DFMEA Agentic Review System

An end-to-end, Human-in-the-Loop (HITL) pipeline that ingests a Design Failure Mode and Effects Analysis (DFMEA) spreadsheet, runs parallel AI agents on Amazon Bedrock to analyse it, routes decisions through four human approval gates, and produces a final PDF report — all orchestrated by AWS Step Functions and surfaced through a React portal.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Pipeline Stages](#pipeline-stages)
- [HITL Gates](#hitl-gates)
- [AI Agents](#ai-agents)
- [AWS Services](#aws-services)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Deployment](#deployment)
- [Teardown](#teardown)
- [Frontend Portal](#frontend-portal)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Running Tests](#running-tests)

---

## Overview

DFMEA (Design Failure Mode and Effects Analysis) is a structured process used in automotive and safety-critical engineering to identify potential failure modes, their causes, and their effects. This system automates the analysis phase while keeping human engineers in control through four sequential approval gates.

**What happens when you upload a DFMEA spreadsheet or JSON file:**

1. The file is parsed, normalised, and evaluated by the ML pre-screen (Gate 1 — human confirms the intake)
2. A component summary is derived from the normalised DFMEA rows (Gate 2 — human confirms component coverage)
3. Four parallel AI agents analyse the DFMEA rows against industry standards (Gate 3 — human reviews findings)
4. A synthesis agent consolidates findings and computes AIAG-VDA 2019 Action Priority ratings (Gate 4 — human approves the final report)
5. A PDF report is generated and made available for download

---

## Architecture

![Diagram of an AWS cloud-based DFMEA review system showing AI-assisted design-risk analysis with human approval gates. A reviewer uses a CloudFront-hosted application secured by Amazon Cognito; API Gateway and Lambda start an AWS Step Functions workflow that parses and pre-screens DFMEA data, invokes four specialist Bedrock AgentCore agents, synthesises their findings, and generates a PDF report. DynamoDB, S3, OpenSearch, Neptune, Bedrock Knowledge Bases, AgentCore Memory, WebSocket updates, and Amazon SNS support storage, retrieval, status updates, ontology queries, and reviewer notifications.](docs/architecture.png)

[Open the editable draw.io source](docs/architecture-polished.drawio)

All state transitions are managed by **AWS Step Functions** using the `waitForTaskToken` callback pattern for HITL gates. The React portal polls gate status and receives live updates via **API Gateway WebSocket**.

---

## Pipeline Stages

| Stage | Lambda | Description |
|-------|--------|-------------|
| S1 | `dfmea-intake` | Parses the uploaded XLSX or JSON file, normalises DFMEA fields and S/O/D scores, runs ML pre-screening, stores normalised data in S3, and updates the review record in DynamoDB |
| S3 | Parallel AgentCore agents | Four specialist agents run concurrently on Bedrock AgentCore Runtime, invoked by the thin `dfmea-invoke-*` Lambdas; each writes structured findings to DynamoDB |
| S4 | `dfmea-invoke-analyst` | Synthesis agent (AgentCore) consolidates all agent findings, deduplicates overlaps, validates SHACL ontology constraints, computes AIAG-VDA 2019 AP ratings |
| S5 | `dfmea-hitl-waiter` | Stores the Step Functions task token and sets `HITL_PENDING` status, pausing the pipeline |
| S6 | Report generator (within analyst) | Generates the final PDF report and writes it to the reports S3 bucket |

---

## HITL Gates

Each gate pauses the Step Functions execution and waits for a human decision via the portal. Approving or rejecting a gate sends `SendTaskSuccess` or `SendTaskFailure` to resume or halt the pipeline.

| Gate | Triggered After | What the Reviewer Checks |
|------|----------------|--------------------------|
| **Gate 1 — Intake Validation** | File parsed and pre-screened | Assembly name, row count, and source-file correctness |
| **Gate 2 — Component Coverage Review** | Component summary prepared | Unique component or part names and their DFMEA row counts for completeness |
| **Gate 3 — Agent Analysis Review** | All 4 agents complete | Raw agent findings, S/O/D scores, hallucinations |
| **Gate 4 — Final Human Approval** | Synthesis complete | Consolidated findings, AP ratings, report readiness |

When a gate becomes active, an HTML email is sent via **SES** (through the `dfmea-notifier` Lambda) with a pipeline progress summary and a direct link to the portal.

---

## AI Agents

All agents run on **Amazon Bedrock AgentCore Runtime** using **Claude Haiku** (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) with cross-region inference. Each agent is fronted by a thin invoker Lambda (`dfmea-invoke-*`) that calls the AgentCore runtime; the agents reach their data and knowledge sources through the active tools registered on the **AgentCore MCP Gateway** (`dfmea-gateway`), which routes to the `dfmea-mcp-tools` Lambda.

| Agent | Invoker Lambda | AgentCore Runtime | Focus |
|-------|----------------|-------------------|-------|
| **Failure Mode** | `dfmea-invoke-failure-mode` | `dfmea-failure-mode` | Scans AIAG-VDA failure mode knowledge base; identifies missing or incorrect failure modes |
| **Structural** | `dfmea-invoke-structural` | `dfmea-structural` | Validates BOM hierarchy and interface matrix completeness against DFMEA rows |
| **Regulatory** | `dfmea-invoke-regulatory` | `dfmea-regulatory` | Cross-checks FMVSS 214/216 and ISO 26262 citations using a Bedrock Knowledge Base |
| **Schema** | `dfmea-invoke-other` | `dfmea-schema` | Verifies DFMEA field completeness, S/O/D rating ranges, action priority consistency, and ML anomaly scores |
| **Analyst** | `dfmea-invoke-analyst` | `dfmea-analyst` | Synthesis agent — consolidates findings from all four agents, deduplicates, and generates the final report |

Agents output structured `FINDINGS_JSON: [...] END_FINDINGS_JSON` blocks which are parsed and written to the `dfmea-analysis-findings` DynamoDB table.

**MCP tools used by the active workflow (9):** `search_failure_mode_kb`, `search_regulatory_kb`, `get_review_context`, `write_finding`, `validate_shacl`, `get_component_failure_modes`, `get_component_standards`, `get_ontology_subclasses`, `get_ml_scores`.

**How it runs (AWS-native):**
- Each agent is a **Strands Agent** hosted in an **AgentCore Runtime** (`BedrockAgentCoreApp` entrypoint), deployed with the `bedrock_agentcore_starter_toolkit` (`configure`/`launch`, `auto_create_ecr=True`) — no hand-written Dockerfile. See `scripts/deploy_agentcore_runtimes.py`.
- At runtime the agent loads its tools from the **AgentCore Gateway via an MCP client** (`agents/shared/gateway_client.py`), authenticated with a **Cognito M2M `client_credentials` JWT** (`Authorization: Bearer`) that the Gateway validates.
- The `dfmea-invoke-*` Lambdas are **thin clients** that call `bedrock-agentcore:InvokeAgentRuntime` (IAM/SigV4 inbound — robust across the 72h HITL gates).
- The **analyst** runtime uses **AgentCore Memory** (session per `review_id`, RFQ-reference pattern); its deterministic S6 PDF report stays in the invoker Lambda (`agents/analyst/report.py`) so AIAG-VDA output remains reproducible.
- `write_finding` recomputes Action Priority deterministically server-side, so findings persisted via the Gateway tool are always authoritative.

---

## AWS Services

| Service | Usage |
|---------|-------|
| **AWS Step Functions** | Orchestrates the end-to-end pipeline with `waitForTaskToken` HITL gates |
| **Amazon Bedrock** | Claude Haiku for all AI agents; Bedrock Knowledge Bases for failure mode and regulatory corpora |
| **Amazon Bedrock AgentCore** | Runtime execution of the 5 specialist agents; MCP Gateway (`dfmea-gateway`) routing active tools to `dfmea-mcp-tools` |
| **Amazon Neptune Serverless** | DFMEA ontology graph; agents query it via SPARQL for component failure modes and standards |
| **AWS Lambda** | Compute for intake, agent invokers, MCP tools, API, WebSocket handlers, and notifications |
| **Amazon DynamoDB** | Stores reviews, gate statuses, task tokens, findings, ML risk scores, and WebSocket connections |
| **Amazon S3** | Stores uploaded DFMEA files, normalised review data, ML models, ontology data, and final PDF reports |
| **Amazon API Gateway** | REST API (Cognito-authorised) + WebSocket API for live status updates |
| **Amazon Cognito** | User authentication for the portal + M2M pool (client_credentials) for AgentCore Gateway auth |
| **AWS Secrets Manager** | Stores the M2M client secret used by the invoker/MCP Lambdas |
| **Amazon SNS** | Publishes HITL gate notifications to the notifier Lambda |
| **Amazon SES** | Sends HTML email notifications to reviewers |
| **Amazon CloudFront + S3** | Hosts the React frontend |
| **Amazon OpenSearch Serverless** | Vector store backing the Bedrock Knowledge Bases + regulatory document search |
| **scikit-learn on Lambda** | ML pre-screening at intake (RandomForest classifier + IsolationForest anomaly detection) |
| **AWS CDK** | Infrastructure as code (Python, 12 stacks) |

---

## Tech Stack

**Backend**
- Python 3.12
- aws-cdk-lib 2.200.0
- Amazon Bedrock AgentCore Runtime + MCP Gateway (agents; no in-repo agent SDK)
- scikit-learn 1.5.2, joblib, numpy (ML pre-screen)
- rdflib 7.1.1, pyshacl 0.26.0 (ontology + SHACL validation)
- opensearch-py, openpyxl, jsonschema
- boto3 / botocore (Lambda runtime)

**Frontend**
- React 18 + TypeScript
- Vite + Tailwind CSS
- Zustand (state management)
- Recharts (dashboard charts)
- AWS Amplify (Cognito auth)

---

## Project Structure

```
dfmea-prototype/
├── agents/                     # Agent prompts/logic invoked on Bedrock AgentCore
│   ├── analyst/
│   ├── failure_mode/
│   ├── regulatory/
│   ├── structural/
│   ├── other/                  # Schema agent
│   └── shared/                 # Shared tools, ML scorer, ontology client
├── lambdas/
│   ├── api/                    # REST API Lambda (all routes)
│   ├── intake/                 # S1: parse + normalise DFMEA file + ML pre-screen
│   ├── agentcore_tools/        # dfmea-mcp-tools — MCP tool backend
│   ├── hitl_gate/              # Gate 1-4 decision handler
│   ├── hitl_waiter/            # Gate 4 task token store
│   ├── hitl_callback/          # Legacy HITL callback
│   ├── notifier/               # SNS → SES HTML email notifications
│   ├── audit/                  # Audit trail endpoint
│   ├── search_indexer/         # OpenSearch indexer
│   ├── websocket_connect/      # WebSocket $connect handler
│   └── websocket_fanout/       # WebSocket message broadcast
├── infra/
│   ├── app.py                  # CDK app entry point (12 stacks)
│   └── stacks/
│       ├── foundation_stack.py # VPC, KMS, S3 buckets
│       ├── auth_stack.py       # Cognito user pool + M2M pool + Secrets Manager
│       ├── data_stack.py       # DynamoDB tables, S3 buckets
│       ├── neptune_stack.py    # Neptune Serverless ontology graph
│       ├── agent_stack.py      # Agent invoker Lambdas, SQS
│       ├── orchestration_stack.py  # Step Functions, WebSocket API
│       ├── search_stack.py     # OpenSearch Serverless
│       ├── knowledge_base_stack.py # Bedrock Knowledge Bases
│       ├── agentcore_stack.py  # AgentCore Gateway + MCP tools Lambda
│       ├── api_stack.py        # REST API Gateway
│       ├── frontend_stack.py   # CloudFront + S3 hosting
│       └── monitoring_stack.py # CloudWatch dashboards + alarms
├── ml/                         # ML training (RandomForest + IsolationForest)
├── eval/                       # Golden eval set + precision/recall metrics
├── ui/                         # React frontend
│   └── src/
│       ├── pages/
│       │   ├── UploadPage.tsx
│       │   ├── ReviewsPage.tsx
│       │   ├── ReviewDetailPage.tsx
│       │   ├── FindingsPage.tsx
│       │   ├── DashboardPage.tsx
│       │   └── AdminPage.tsx
│       └── components/
│           ├── GateCard.tsx        # HITL gate with streaming progress log
│           ├── GateReviewDrawer.tsx # Slide-out review panel per gate
│           ├── PipelineStepper.tsx # Visual pipeline progress bar
│           ├── StatusBadge.tsx
│           └── SearchWidget.tsx
├── schemas/                    # JSON schemas for DFMEA validation
├── ontology/                   # SHACL ontology for graph validation
├── sample-data/                # Sample DFMEA spreadsheets
├── tests/                      # Unit + integration tests (pytest)
├── scripts/
│   ├── deploy.sh               # Canonical deploy script (16 steps)
│   ├── destroy.sh              # Canonical teardown script
│   ├── create_bedrock_kb.py    # Bedrock Knowledge Base creation + ingestion
│   ├── load_neptune_ontology.sh
│   └── .env.example
├── deploy.sh                   # Wrapper → scripts/deploy.sh
└── destroy.sh                  # Wrapper → scripts/destroy.sh
```

---

## Prerequisites

- AWS CLI configured with sufficient IAM permissions
- Python 3.12
- Node.js 20+ and npm
- `jq` (used by the deploy/destroy scripts)
- AWS CDK CLI: `npm install -g aws-cdk` (the deploy script auto-installs Node/CDK if missing)
- Amazon Bedrock model access enabled for Claude Haiku (`us.anthropic.claude-haiku-4-5-*`) in the target region
- Amazon Bedrock AgentCore available in the target region (default: `us-east-1`)
- SES identity verified for the sender/recipient email address

---

## Deployment

### Full deployment (recommended)

```bash
cd dfmea-prototype

# Set required variables
export OPS_EMAIL=ops@yourcompany.com
export ADMIN_EMAIL=admin@yourcompany.com
export ADMIN_PASSWORD="YourPassword2026!"
export REVIEWER_EMAIL=reviewer@yourcompany.com

bash scripts/deploy.sh
```

The script will:
1. Install Python (infra + agents + ML + `bedrock-agentcore` / `bedrock-agentcore-starter-toolkit`) and Node dependencies
2. Bootstrap CDK
3. Deploy all 12 CDK stacks in dependency order
4. Generate synthetic sample data and train the ML pre-screen models, then upload them to S3
5. Build and deploy the Lambda bundle (scikit-learn, pyshacl, rdflib, opensearch-py — no agent SDK) to all functions
6. Patch post-deploy environment variables (`REVIEW_SM_ARN`, `WEBSOCKET_ENDPOINT`, `ML_MODELS_BUCKET_NAME`, `NEPTUNE_ENDPOINT`, KB IDs, `UPLOADS_BUCKET_NAME`)
7. Load the Neptune ontology (JSON-LD → N-Triples → SPARQL insert)
8. Create the Bedrock Knowledge Bases and start ingestion
9. Create the `dfmea-gateway` MCP Gateway + register the 10 tools, then build & launch the 5 AgentCore Runtimes via the `bedrock_agentcore_starter_toolkit` (`scripts/deploy_agentcore_runtimes.py`, `auto_create_ecr=True`) and wire each runtime ARN onto its `dfmea-invoke-*` Lambda
10. Propagate the M2M client secret to the invoker/MCP Lambdas
11. Deploy the `dfmea-notifier` Lambda and subscribe it to the HITL SNS topic
12. Create the Cognito admin user (and `admins` group)
13. Build the React frontend and sync to CloudFront

### Simplified entrypoint

`deploy.sh` at the repo root is a thin wrapper that forwards to `scripts/deploy.sh`, so it requires the same four environment variables:

```bash
export OPS_EMAIL=ops@yourcompany.com
export ADMIN_EMAIL=admin@yourcompany.com
export ADMIN_PASSWORD="YourPassword2026!"
export REVIEWER_EMAIL=reviewer@yourcompany.com
bash deploy.sh
```

### Frontend-only redeploy

```bash
bash scripts/deploy.sh --frontend-only
```

### Skip CDK (redeploy Lambda code and frontend only)

```bash
bash scripts/deploy.sh --skip-cdk
```

### Bedrock Knowledge Bases

Knowledge Base source upload, creation, and ingestion are automated by `scripts/deploy.sh`. The deployment uploads the AIAG/BOM references to `failure-mode-kb/`, uploads the FMVSS references to `regulatory-kb/`, starts both ingestion jobs, and waits for them to complete. The resulting KB IDs are stored in SSM at `/dfmea/failure_mode_kb_id` and `/dfmea/regulatory_kb_id` and wired onto the MCP tools Lambda and relevant invokers.

To refresh both KBs after adding or changing source documents, rerun the normal deployment, or run the helper after uploading documents to the existing prefixes:

```bash
python3 scripts/create_bedrock_kb.py
```

The helper is idempotent: it reuses existing knowledge bases/data sources, starts ingestion, waits for completion, and only then publishes their IDs to SSM.

### Remaining manual step

The reviewer (`REVIEWER_EMAIL`) must confirm the SNS subscription email before HITL gate notifications will be delivered.

---

## Teardown

```bash
bash destroy.sh                       # tear down stacks, keep retained data
bash destroy.sh --force --purge-data  # also empty/delete retained S3 + DynamoDB data
```

`destroy.sh` at the repo root is a thin wrapper that forwards to `scripts/destroy.sh`. The canonical script:
1. Destroys all 12 CDK stacks in reverse dependency order
2. Deletes external (non-CDK) resources: the `dfmea-notifier` Lambda + IAM role, SSM KB-id parameters, Bedrock Knowledge Bases, the AgentCore Gateway (`dfmea-gateway`) and its tool targets, and the 5 AgentCore runtimes
3. Disables deletion protection on the Neptune cluster before deletion
4. With `--purge-data`: empties and deletes retained S3 buckets, deletes retained DynamoDB tables, and wipes local build artifacts

Without `--purge-data`, S3 buckets and DynamoDB tables (created with `RemovalPolicy.RETAIN`) are preserved.

---

## Frontend Portal

The React portal is hosted on CloudFront. After deploy, the URL is printed at the end of the deploy script.

**Login credentials** are set by `ADMIN_EMAIL` / `ADMIN_PASSWORD` during deployment.

### Pages

| Page | Route | Description |
|------|-------|-------------|
| **Upload** | `/upload` | Upload a DFMEA spreadsheet (`.xlsx`) or JSON file to start a new review |
| **Reviews** | `/reviews` | List all reviews with status badges |
| **Review Detail** | `/reviews/:id` | Pipeline stepper, HITL gate cards, approve/reject controls |
| **Findings** | `/reviews/:id/findings` | Filterable findings table with S/O/D scores and AP ratings |
| **Dashboard** | `/dashboard` | Action Priority distribution chart across all reviews |
| **Admin** | `/admin` | System-level AP counts; regulatory document search |

### Gate Review Drawer

Each gate card has a **"Review data before deciding"** button that opens a slide-out panel showing:
- **Gate 1**: Parsed intake summary (assembly name, row count, submission time)
- **Gate 2**: Component coverage summary — unique part names and their DFMEA row counts
- **Gate 3 & 4**: Full findings table with AP badges, agent filter tabs, S/O/D scores

### Live Pipeline Log

After approving a gate, a terminal-style streaming log appears showing each pipeline step as it executes. The log keeps a spinner running until the backend confirms the next gate is active.

---

## API Reference

All endpoints require a Cognito ID token in the `Authorization` header (except `/health`).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (no auth) |
| `POST` | `/reviews` | Start a new review: `{assembly_name, file_key}` |
| `GET` | `/reviews` | List recent reviews |
| `GET` | `/reviews/{id}` | Get review status and metadata |
| `DELETE` | `/reviews/{id}` | Archive a review |
| `GET` | `/reviews/{id}/gates` | Get all gate statuses and pending tokens |
| `POST` | `/reviews/{id}/gate/{n}` | Submit gate decision: `{action: "approve"\|"reject", comment}` |
| `GET` | `/reviews/{id}/findings` | List findings (optional `?min_ap=H\|M`) |
| `GET` | `/reviews/{id}/cad` | Get the DFMEA component summary derived from normalised rows; the route name is retained for compatibility and does not indicate a CAD-file upload |
| `GET` | `/reviews/{id}/report` | Get pre-signed URL to final PDF report |
| `GET` | `/upload-url?filename=foo.xlsx` | Get pre-signed S3 PUT URL for file upload |
| `GET` | `/admin/metrics` | AP-level counts across all reviews |
| `GET` | `/search?q=<query>` | Semantic search against regulatory document corpus |

---

## Configuration

The frontend reads `/config.json` at runtime (served from S3/CloudFront, never bundled into the JS build). It is written by the deploy script and contains:

```json
{
  "userPoolId": "us-east-1_XXXXXXXXX",
  "userPoolClientId": "XXXXXXXXXXXXXXXXXXXXXXXXXX",
  "region": "us-east-1",
  "restApiUrl": "https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/v1/",
  "wsApiUrl": "wss://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com/v1"
}
```

### Key Lambda environment variables

| Lambda | Variable | Description |
|--------|----------|-------------|
| `dfmea-intake` | `REVIEW_SM_ARN` | Step Functions state machine ARN (set post-deploy) |
| `dfmea-intake` | `ML_MODELS_BUCKET_NAME` | S3 bucket holding the ML pre-screen models |
| `dfmea-ws-fanout` | `WEBSOCKET_ENDPOINT` | API Gateway WebSocket management endpoint |
| `dfmea-invoke-*` (thin invokers) | `AGENT_RUNTIME_ARN` | ARN of the AgentCore Runtime this Lambda invokes (set post-deploy by `deploy.sh` step 11 from the runtime deploy manifest) |
| `dfmea-invoke-*` | `AGENT_LABEL` | Logical agent name (session id / logging) |
| AgentCore Runtime (all) | `GATEWAY_URL` | AgentCore MCP Gateway endpoint the runtime calls for tools |
| AgentCore Runtime (all) | `COGNITO_TOKEN_URL` | Cognito M2M OAuth2 token endpoint (mint Gateway JWT) |
| AgentCore Runtime (all) | `M2M_SECRET_ID` | Secrets Manager id for the M2M client id/secret |
| AgentCore Runtime `dfmea-analyst` | `DFMEA_MEMORY_ID` | AgentCore Memory id (session per review) |
| `dfmea-api` | `NEPTUNE_ENDPOINT` | Neptune cluster endpoint for ontology SPARQL queries |
| `dfmea-notifier` | `SES_SENDER` | Verified SES sender email address |
| `dfmea-notifier` | `SES_RECIPIENT` | HITL reviewer email address |
| `dfmea-notifier` | `PORTAL_URL` | CloudFront URL included in notification emails |

---

## Running Tests

```bash
cd dfmea-prototype
python -m pytest tests/unit/ -v
```

Unit tests cover CDK stacks, Lambda handlers, agent logic, and React components (via Vitest).

```bash
# Frontend tests
cd ui
npm test
```

---

## Action Priority (AP) Ratings

The system uses the **AIAG-VDA 2019** Action Priority methodology:

| Rating | Meaning | Threshold |
|--------|---------|-----------|
| **H** (High) | Immediate action required | Severity × Occurrence × Detection above high threshold |
| **M** (Medium) | Action recommended | Moderate risk score |
| **L** (Low) | Action at discretion | Low risk score |

AP ratings are computed by the analyst synthesis agent and displayed throughout the portal with colour-coded badges (red / yellow / green).
