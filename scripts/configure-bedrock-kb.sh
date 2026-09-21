#!/usr/bin/env bash
# =============================================================================
# DFMEA — Configure Bedrock Knowledge Base IDs on agent Lambdas
#
# Run this AFTER manually creating the two Bedrock KBs in AWS Console
# (or programmatically) and noting their IDs.
#
# Usage:
#   ./scripts/configure-bedrock-kb.sh <failure-mode-kb-id> <regulatory-kb-id>
#
# Example:
#   ./scripts/configure-bedrock-kb.sh ABCDE12345 FGHIJ67890
# =============================================================================
set -euo pipefail

CYAN='\033[0;36m'; GREEN='\033[0;32m'; NC='\033[0m'
log() { echo -e "${CYAN}[bedrock-kb]${NC} $*"; }
ok()  { echo -e "${GREEN}[  ok     ]${NC} $*"; }

FAILURE_MODE_KB_ID="${1:-}"
REGULATORY_KB_ID="${2:-}"

[[ -z "$FAILURE_MODE_KB_ID" ]] && { echo "Usage: $0 <failure-mode-kb-id> <regulatory-kb-id>"; exit 1; }
[[ -z "$REGULATORY_KB_ID"   ]] && { echo "Usage: $0 <failure-mode-kb-id> <regulatory-kb-id>"; exit 1; }

# Patch failure-mode agent Lambda
log "Setting FAILURE_MODE_KB_ID=$FAILURE_MODE_KB_ID on dfmea-agent-failure-mode"
CURRENT=$(aws lambda get-function-configuration \
  --function-name dfmea-agent-failure-mode \
  --query 'Environment.Variables' --output json 2>/dev/null || echo '{}')
UPDATED=$(echo "$CURRENT" | python3 -c \
  "import sys,json; e=json.load(sys.stdin); e['FAILURE_MODE_KB_ID']='${FAILURE_MODE_KB_ID}'; print(json.dumps({'Variables':e}))")
aws lambda update-function-configuration \
  --function-name dfmea-agent-failure-mode \
  --environment "$UPDATED" \
  --output text --query 'FunctionName' > /dev/null
ok "dfmea-agent-failure-mode updated"

# Patch regulatory agent Lambda
log "Setting REGULATORY_KB_ID=$REGULATORY_KB_ID on dfmea-agent-regulatory"
CURRENT=$(aws lambda get-function-configuration \
  --function-name dfmea-agent-regulatory \
  --query 'Environment.Variables' --output json 2>/dev/null || echo '{}')
UPDATED=$(echo "$CURRENT" | python3 -c \
  "import sys,json; e=json.load(sys.stdin); e['REGULATORY_KB_ID']='${REGULATORY_KB_ID}'; print(json.dumps({'Variables':e}))")
aws lambda update-function-configuration \
  --function-name dfmea-agent-regulatory \
  --environment "$UPDATED" \
  --output text --query 'FunctionName' > /dev/null
ok "dfmea-agent-regulatory updated"

echo ""
echo "Knowledge Base IDs configured. Agents will use the KBs on next invocation."
