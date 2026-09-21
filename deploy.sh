#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Convenience wrapper around scripts/deploy.sh
#
# The full, canonical deployment logic lives in scripts/deploy.sh. This wrapper
# exists so `bash deploy.sh` from the repo root Just Works. It forwards all
# arguments and environment variables to the canonical script.
#
# Architecture deployed (see README.md):
#   - 12 CDK stacks (Foundation, Auth, Data, Neptune, Agent, Orchestration,
#     Search, KnowledgeBase, AgentCore, Api, Frontend, Monitoring)
#   - 5 specialist agents on Amazon Bedrock AgentCore Runtime (Claude Haiku),
#     invoked by thin dfmea-invoke-* Lambdas
#   - AgentCore Gateway (dfmea-gateway) routing 10 MCP tools to dfmea-mcp-tools
#   - Bedrock Knowledge Bases, Neptune ontology, ML pre-screen models
#
# Usage:
#   export OPS_EMAIL=ops@yourcompany.com
#   export ADMIN_EMAIL=admin@yourcompany.com
#   export ADMIN_PASSWORD='YourPassword2026!'
#   export REVIEWER_EMAIL=reviewer@yourcompany.com
#   bash deploy.sh                 # full deploy
#   bash deploy.sh --frontend-only # rebuild + redeploy the React frontend only
#   bash deploy.sh --skip-cdk      # redeploy Lambda code + frontend, skip CDK
#   bash deploy.sh --skip-seed     # skip synthetic data, ML training, KB, ontology
#
# Requires: AWS CLI, CDK CLI (npm install -g aws-cdk), Node 20+, Python 3.12, jq
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL="${REPO_DIR}/scripts/deploy.sh"

if [ ! -f "${CANONICAL}" ]; then
  echo "ERROR: canonical deploy script not found at ${CANONICAL}" >&2
  exit 1
fi

echo "==> Delegating to scripts/deploy.sh (canonical deploy)"
exec bash "${CANONICAL}" "$@"
