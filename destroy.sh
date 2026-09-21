#!/usr/bin/env bash
# =============================================================================
# destroy.sh — Convenience wrapper around scripts/destroy.sh
#
# The full, canonical teardown logic lives in scripts/destroy.sh. This wrapper
# exists so `bash destroy.sh` from the repo root Just Works. It forwards all
# arguments to the canonical script.
#
# What the canonical script does (see README.md):
#   - Destroys all 12 CDK stacks in reverse dependency order
#   - Deletes external (non-CDK) resources: dfmea-notifier Lambda + IAM role,
#     SSM KB-id parameters, Bedrock Knowledge Bases, the AgentCore Gateway
#     (dfmea-gateway) and its tool targets, and the 5 AgentCore runtimes
#   - Disables deletion protection on the Neptune (and legacy Aurora) clusters
#
# By default, retained data (S3 buckets, DynamoDB tables with
# RemovalPolicy.RETAIN, local build artifacts) is preserved.
#
# Usage:
#   bash destroy.sh                       # tear down stacks, keep retained data
#   bash destroy.sh --force               # skip the confirmation prompt (CI)
#   bash destroy.sh --force --purge-data  # also empty/delete retained data
#
# Requires: AWS CLI, CDK CLI (npm install -g aws-cdk), Python 3.12, jq
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL="${REPO_DIR}/scripts/destroy.sh"

if [ ! -f "${CANONICAL}" ]; then
  echo "ERROR: canonical destroy script not found at ${CANONICAL}" >&2
  exit 1
fi

echo "==> Delegating to scripts/destroy.sh (canonical teardown)"
exec bash "${CANONICAL}" "$@"
