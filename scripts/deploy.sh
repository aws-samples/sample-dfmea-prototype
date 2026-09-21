#!/usr/bin/env bash
# =============================================================================
# DFMEA Agentic Review System — Full Deployment Script
#
# Usage:
#   ./scripts/deploy.sh [--skip-cdk] [--skip-frontend] [--skip-seed]
#                       [--frontend-only]
#
# Required env vars (or set in scripts/.env):
#   OPS_EMAIL         — CloudWatch/budget alarm recipient
#   ADMIN_EMAIL       — First Cognito admin user email
#   ADMIN_PASSWORD    — First Cognito admin user password (min 8 chars, upper+lower+digit+symbol)
#   REVIEWER_EMAIL    — HITL reviewer SNS email
#
# Optional env vars:
#   AWS_DEFAULT_REGION   (default: us-east-1)
#   MONTHLY_BUDGET_USD   (default: 500)
#   SKIP_CDK_BOOTSTRAP   set to 1 if already bootstrapped this account
# =============================================================================
set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${CYAN}[deploy]${NC} $*"; }
ok()   { echo -e "${GREEN}[  ok  ]${NC} $*"; }
warn() { echo -e "${YELLOW}[ warn ]${NC} $*"; }
fail() { echo -e "${RED}[FAILED]${NC} $*"; exit 1; }
step() { echo -e "\n${BOLD}${BLUE}━━━ $* ━━━${NC}\n"; }

# ── temp-file registry + EXIT cleanup ────────────────────────────────────────
BUNDLE_DIR="" BUNDLE_ZIP="" BUILD_DIR=""

_cleanup() {
  [[ -n "${BUNDLE_DIR:-}" ]] && rm -rf "$BUNDLE_DIR"
  [[ -n "${BUNDLE_ZIP:-}" ]] && rm -f "$BUNDLE_ZIP"
  [[ -n "${BUILD_DIR:-}" ]] && rm -rf "$BUILD_DIR"
}
trap '_cleanup' EXIT

# ── locate repo root ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
UI_DIR="$REPO_ROOT/ui"

# ── load .env if present ──────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  log "Loading $SCRIPT_DIR/.env"
  set -o allexport
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/.env"
  set +o allexport
fi

# ── parse flags ───────────────────────────────────────────────────────────────
SKIP_CDK=0; SKIP_FRONTEND=0; SKIP_SEED=0; FRONTEND_ONLY=0
for arg in "$@"; do
  case $arg in
    --skip-cdk)       SKIP_CDK=1 ;;
    --skip-frontend)  SKIP_FRONTEND=1 ;;
    --skip-seed)      SKIP_SEED=1 ;;
    --frontend-only)  FRONTEND_ONLY=1; SKIP_SEED=1 ;;
  esac
done

# helper used in --frontend-only (also redefined later for full deploy)
_cfn_out() {
  local stack="$1" export_name="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack" \
    --query "Stacks[0].Outputs[?ExportName=='${export_name}'].OutputValue" \
    --output text
}

# --frontend-only: redeploy only DfmeaApiStack + frontend, skip everything else
if [[ "$FRONTEND_ONLY" == "1" ]]; then
  step "frontend-only / Deploy DfmeaApiStack + rebuild frontend"
  cd "$REPO_ROOT"
  source .venv/bin/activate 2>/dev/null || { python3 -m venv .venv && source .venv/bin/activate; }
  pip install -q -r "$INFRA_DIR/requirements.txt"

  export CDK_DEFAULT_REGION="${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}"
  export CDK_DEFAULT_ACCOUNT="${CDK_DEFAULT_ACCOUNT:-$(aws sts get-caller-identity --query Account --output text)}"

  cd "$INFRA_DIR"
  cdk deploy DfmeaApiStack --require-approval never
  cd "$REPO_ROOT"

  USER_POOL_ID=$(_cfn_out DfmeaAuthStack DfmeaUserPoolId)
  USER_POOL_CLIENT_ID=$(_cfn_out DfmeaAuthStack DfmeaUserPoolClientId)
  REST_API_URL=$(_cfn_out DfmeaApiStack DfmeaRestApiUrl)
  WS_API_URL=$(_cfn_out DfmeaApiStack DfmeaWsApiUrl)
  HOSTING_BUCKET=$(_cfn_out DfmeaFrontendStack DfmeaHostingBucket)
  DISTRIBUTION_ID=$(_cfn_out DfmeaFrontendStack DfmeaDistributionId)

  BUILD_DIR="/tmp/dfmea-ui-build-$$"
  rsync -a --exclude='node_modules' --exclude='dist' "$UI_DIR/" "$BUILD_DIR/"
  cd "$BUILD_DIR"
  npm install --legacy-peer-deps --silent
  npm run build
  cat > dist/config.json << EOF
{
  "userPoolId": "${USER_POOL_ID}",
  "userPoolClientId": "${USER_POOL_CLIENT_ID}",
  "region": "${CDK_DEFAULT_REGION}",
  "restApiUrl": "${REST_API_URL}",
  "wsApiUrl": "${WS_API_URL}"
}
EOF
  aws s3 sync dist/ "s3://${HOSTING_BUCKET}/" --delete \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "index.html" --exclude "config.json"
  aws s3 cp dist/index.html "s3://${HOSTING_BUCKET}/index.html" \
    --cache-control "no-cache, no-store, must-revalidate"
  aws s3 cp dist/config.json "s3://${HOSTING_BUCKET}/config.json" \
    --cache-control "no-cache, no-store, must-revalidate"
  aws cloudfront create-invalidation \
    --distribution-id "$DISTRIBUTION_ID" --paths "/*" \
    --output text --query 'Invalidation.Id' > /dev/null
  rm -rf "$BUILD_DIR"
  ok "Frontend redeployed"
  exit 0
fi

# ── required env checks ───────────────────────────────────────────────────────
step "Pre-flight checks"

for var in OPS_EMAIL ADMIN_EMAIL ADMIN_PASSWORD REVIEWER_EMAIL; do
  if [[ -z "${!var:-}" ]]; then
    fail "$var is not set. Set it in the environment or in scripts/.env"
  fi
done

export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export CDK_DEFAULT_REGION="$AWS_DEFAULT_REGION"
export OPS_EMAIL
export MONTHLY_BUDGET_USD="${MONTHLY_BUDGET_USD:-500}"
# Silence the CDK/JSII "Node 20 end-of-life" deprecation banner (cosmetic only).
export JSII_SILENCE_WARNING_DEPRECATED_NODE_VERSION=1

# Resolve AWS account
CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || fail "Cannot resolve AWS account. Is AWS CLI configured?"
export CDK_DEFAULT_ACCOUNT

log "Account : $CDK_DEFAULT_ACCOUNT"
log "Region  : $CDK_DEFAULT_REGION"
log "Ops     : $OPS_EMAIL"
log "Reviewer: $REVIEWER_EMAIL"
log "Budget  : \$$MONTHLY_BUDGET_USD/month"

# ── Auto-install Node.js if missing ──────────────────────────────────────────
if ! command -v node &>/dev/null; then
  log "node not found — installing Node.js 20.x"
  if command -v apt-get &>/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - \
      && sudo apt-get install -y nodejs
  elif command -v yum &>/dev/null; then
    curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash - \
      && sudo yum install -y nodejs
  else
    export NVM_DIR="$HOME/.nvm"
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    # shellcheck disable=SC1090
    source "$NVM_DIR/nvm.sh"
    nvm install 20 && nvm use 20
  fi
  ok "Node.js $(node --version) installed"
fi

# ── Auto-install CDK CLI if missing ──────────────────────────────────────────
if ! command -v cdk &>/dev/null; then
  log "cdk not found — installing aws-cdk globally"
  if npm install -g aws-cdk 2>/dev/null; then
    ok "CDK $(cdk --version) installed globally"
  else
    npm install -g aws-cdk --prefix "$HOME/.local"
    export PATH="$HOME/.local/bin:$PATH"
    grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc" \
      || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    ok "CDK $(cdk --version) installed to ~/.local/bin"
  fi
fi

# ── Check remaining prerequisites ────────────────────────────────────────────
for cmd in aws cdk node python3 npm; do
  command -v "$cmd" &>/dev/null || fail "Required command not found: $cmd"
done
ok "All prerequisite commands found"

# ── Reusable helper: update a single Lambda env var (idempotent) ──────────────
_update_lambda_env() {
  local fn_name="$1" key="$2" value="$3"
  local current_env
  current_env=$(aws lambda get-function-configuration \
    --function-name "$fn_name" \
    --query 'Environment.Variables' --output json 2>/dev/null || echo '{}')
  local env_json env_file
  env_json=$(echo "$current_env" | \
    _LAMBDA_ENV_KEY="${key}" _LAMBDA_ENV_VALUE="${value}" python3 -c "
import sys, json, os
e = json.load(sys.stdin)
e[os.environ['_LAMBDA_ENV_KEY']] = os.environ['_LAMBDA_ENV_VALUE']
print(json.dumps({'Variables': e}))
")
  # Write to temp file — avoids shell quoting issues with special chars in values
  env_file=$(mktemp /tmp/dfmea-env-XXXXXX.json)
  echo "$env_json" > "$env_file"
  aws lambda update-function-configuration \
    --function-name "$fn_name" \
    --environment "file://${env_file}" \
    --output text --query 'FunctionName' > /dev/null
  rm -f "$env_file"
  aws lambda wait function-updated --function-name "$fn_name" 2>/dev/null || true
}

# ── Reusable helper: read a CloudFormation stack output (ExportName or OutputKey) ─
_cfn_output() {
  local stack="$1" key="$2" val
  val=$(aws cloudformation describe-stacks \
    --stack-name "$stack" \
    --query "Stacks[0].Outputs[?ExportName=='${key}'].OutputValue | [0]" \
    --output text 2>/dev/null || echo "")
  if [[ -z "$val" || "$val" == "None" ]]; then
    val=$(aws cloudformation describe-stacks \
      --stack-name "$stack" \
      --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue | [0]" \
      --output text 2>/dev/null || echo "")
  fi
  echo "$val"
}

# ── Reusable helper: read SSM parameter ───────────────────────────────────────
_ssm_get() {
  aws ssm get-parameter --name "$1" --query 'Parameter.Value' --output text 2>/dev/null || echo ""
}

# =============================================================================
# STEP 1: Install Python dependencies (infra + agents + ML)
# =============================================================================
step "1 / Install Python dependencies"
cd "$REPO_ROOT"
if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r infra/requirements.txt
pip install -q -r requirements-agents.txt
if [[ -f "$REPO_ROOT/requirements-ml.txt" ]]; then
  pip install -q -r requirements-ml.txt
  ok "requirements-ml.txt installed"
else
  warn "requirements-ml.txt not found — skipping ML Python deps"
fi
# AgentCore Runtime deploy tooling (used in step 11 to build/launch the 5 runtimes)
pip install -q bedrock-agentcore bedrock-agentcore-starter-toolkit \
  && ok "AgentCore starter toolkit installed" \
  || warn "Could not install AgentCore starter toolkit — step 11 will retry"
# Ensure boto3/botocore are new enough for the bedrock-agentcore-control service
# model (gateway creation in step 11). Older pins (e.g. 1.35.x) lack it.
pip install -q -U 'boto3>=1.43.31' 'botocore>=1.43.31' \
  && ok "boto3/botocore upgraded for AgentCore" \
  || warn "Could not upgrade boto3/botocore — gateway creation may fail"
ok "Python dependencies installed"

# =============================================================================
# STEP 2: npm install in ui/
# =============================================================================
step "2 / Install frontend Node dependencies"
cd "$UI_DIR"
npm install --legacy-peer-deps --silent
cd "$REPO_ROOT"
ok "UI npm install complete"

# =============================================================================
# STEP 3: CDK bootstrap
# =============================================================================
step "3 / CDK bootstrap"
if [[ "${SKIP_CDK_BOOTSTRAP:-0}" == "1" ]]; then
  warn "Skipping CDK bootstrap (SKIP_CDK_BOOTSTRAP=1)"
else
  cd "$INFRA_DIR"
  # Note: `cdk bootstrap` does not accept --require-approval (deploy-only flag).
  cdk bootstrap "aws://${CDK_DEFAULT_ACCOUNT}/${CDK_DEFAULT_REGION}"
  ok "CDK bootstrapped"
fi

# =============================================================================
# STEP 4: Deploy CDK stacks (13 stacks in dependency order)
# =============================================================================
step "4 / Deploy CDK stacks (12 stacks)"
if [[ "$SKIP_CDK" == "1" ]]; then
  warn "Skipping CDK deploy (--skip-cdk)"
else
  cd "$INFRA_DIR"

  log "Stack 1/12 — DfmeaFoundationStack"
  cdk deploy DfmeaFoundationStack --require-approval never

  log "Stack 2/12 — DfmeaAuthStack"
  cdk deploy DfmeaAuthStack --require-approval never

  log "Stack 3/12 — DfmeaDataStack"
  cdk deploy DfmeaDataStack --require-approval never

  log "Stack 4/12 — DfmeaNeptuneStack"
  cdk deploy DfmeaNeptuneStack --require-approval never

  log "Stack 5/12 — DfmeaAgentStack"
  cdk deploy DfmeaAgentStack --require-approval never

  log "Stack 6/12 — DfmeaOrchestrationStack"
  cdk deploy DfmeaOrchestrationStack --require-approval never

  log "Stack 7/12 — DfmeaSearchStack"
  cdk deploy DfmeaSearchStack --require-approval never

  log "Stack 8/12 — DfmeaKnowledgeBaseStack"
  cdk deploy DfmeaKnowledgeBaseStack --require-approval never

  log "Stack 9/12 — DfmeaAgentCoreStack"
  cdk deploy DfmeaAgentCoreStack --require-approval never

  log "Stack 10/12 — DfmeaApiStack"
  cdk deploy DfmeaApiStack --require-approval never

  log "Stack 11/12 — DfmeaFrontendStack"
  cdk deploy DfmeaFrontendStack --require-approval never

  log "Stack 12/12 — DfmeaMonitoringStack"
  cdk deploy DfmeaMonitoringStack --require-approval never

  ok "All 12 CDK stacks deployed"
fi
cd "$REPO_ROOT"

# =============================================================================
# STEP 5: Synthetic data generation (before ML training)
# =============================================================================
step "5 / Synthetic data generation"
if [[ "$SKIP_SEED" == "1" ]]; then
  warn "Skipping synthetic data generation (--skip-seed)"
else
  cd "$REPO_ROOT"
  for script in \
    sample-data/scripts/generate_base.py \
    sample-data/scripts/generate_variants.py \
    sample-data/scripts/expand_base.py; do
    if [[ -f "$REPO_ROOT/$script" ]]; then
      log "Running $script"
      python3 "$REPO_ROOT/$script"
      ok "  $script complete"
    else
      warn "  $script not found — skipping"
    fi
  done
  ok "Synthetic data generation complete"
fi

# =============================================================================
# STEP 6: ML training + upload to S3
# =============================================================================
step "6 / ML training + S3 upload"
if [[ "$SKIP_SEED" == "1" ]]; then
  warn "Skipping ML training (--skip-seed)"
else
  cd "$REPO_ROOT"
  ML_MODELS_BUCKET_SEED="dfmea-ml-models-${CDK_DEFAULT_ACCOUNT}"
  if [[ -f "$REPO_ROOT/ml/train.py" ]]; then
    log "Running ml/train.py"
    python3 ml/train.py
    ok "ML training complete"
  else
    warn "ml/train.py not found — skipping training"
  fi
  if [[ -d "$REPO_ROOT/sample-data/models" ]]; then
    aws s3 cp "$REPO_ROOT/sample-data/models/" \
      "s3://${ML_MODELS_BUCKET_SEED}/" --recursive
    ok "ML models uploaded to s3://${ML_MODELS_BUCKET_SEED}/"
  else
    warn "sample-data/models/ not found after training — check ml/train.py output path"
  fi
fi

# =============================================================================
# STEP 7: Build Lambda bundle (no strands-agents) + deploy all Lambda functions
# =============================================================================
step "7 / Build Lambda bundle + deploy Lambda functions"

BUNDLE_DIR="/tmp/dfmea-lambda-bundle-$$"
BUNDLE_ZIP="/tmp/dfmea-lambda-bundle-$$.zip"

rm -rf "$BUNDLE_DIR" && mkdir "$BUNDLE_DIR"

log "Installing Lambda bundle packages (scikit-learn, pyshacl, rdflib, opensearch-py, ...)"
pip install \
  "scikit-learn==1.5.2" \
  "joblib==1.4.2" \
  "numpy==1.26.4" \
  "pyshacl==0.26.0" \
  "rdflib==7.1.1" \
  "opensearch-py==2.8.0" \
  "requests-aws4auth==1.3.1" \
  "openpyxl" \
  "jsonschema" \
  -t "$BUNDLE_DIR" \
  --python-version 3.12 --platform manylinux2014_x86_64 \
  --implementation cp --only-binary=:all: -q

# Remove bloat (boto3/botocore provided by Lambda runtime)
rm -rf "$BUNDLE_DIR"/{boto3,botocore,grpc,cryptography}

# Copy application source
cp -r "$REPO_ROOT/agents" "$BUNDLE_DIR/"
cp -r "$REPO_ROOT/lambdas" "$BUNDLE_DIR/"

cd "$BUNDLE_DIR"
zip -qr "$BUNDLE_ZIP" . --exclude "*/__pycache__/*" --exclude "*/__pycache__"

log "Deploying Lambda bundle to all functions"
LAMBDA_FUNCTIONS=(
  dfmea-intake
  dfmea-invoke-failure-mode
  dfmea-invoke-structural
  dfmea-invoke-regulatory
  dfmea-invoke-other
  dfmea-invoke-analyst
  dfmea-mcp-tools
  dfmea-api
  dfmea-hitl-gate
  dfmea-ws-fanout
  dfmea-cad-extraction
)

# Lambda direct upload limit is ~70 MB. If the ZIP exceeds 60 MB, upload via S3.
BUNDLE_SIZE_BYTES=$(stat -c%s "$BUNDLE_ZIP" 2>/dev/null || stat -f%z "$BUNDLE_ZIP" 2>/dev/null || echo 0)
BUNDLE_S3_KEY=""
if (( BUNDLE_SIZE_BYTES > 62914560 )); then  # > 60 MB
  DEPLOY_BUCKET="dfmea-processed-${CDK_DEFAULT_ACCOUNT}"
  BUNDLE_S3_KEY="lambda-bundles/dfmea-bundle-$$.zip"
  log "Bundle is $(( BUNDLE_SIZE_BYTES / 1048576 )) MB — uploading to s3://${DEPLOY_BUCKET}/${BUNDLE_S3_KEY}"
  aws s3 cp "$BUNDLE_ZIP" "s3://${DEPLOY_BUCKET}/${BUNDLE_S3_KEY}"
fi

for fn in "${LAMBDA_FUNCTIONS[@]}"; do
  if [[ -n "$BUNDLE_S3_KEY" ]]; then
    aws lambda update-function-code \
      --function-name "$fn" \
      --s3-bucket "dfmea-processed-${CDK_DEFAULT_ACCOUNT}" \
      --s3-key "$BUNDLE_S3_KEY" \
      --output text --query 'FunctionName' > /dev/null 2>&1 \
      && log "  Updated $fn (via S3)" \
      || warn "  Could not update $fn (may not exist yet)"
  else
    aws lambda update-function-code \
      --function-name "$fn" \
      --zip-file "fileb://${BUNDLE_ZIP}" \
      --output text --query 'FunctionName' > /dev/null 2>&1 \
      && log "  Updated $fn" \
      || warn "  Could not update $fn (may not exist yet)"
  fi
done

# Wait for all code updates to complete before step 8 calls update-function-configuration
# (avoids ResourceConflictException race condition)
log "Waiting for Lambda code updates to complete..."
for fn in "${LAMBDA_FUNCTIONS[@]}"; do
  aws lambda wait function-updated --function-name "$fn" 2>/dev/null || true
done

rm -rf "$BUNDLE_DIR" "$BUNDLE_ZIP"
cd "$REPO_ROOT"
ok "Lambda bundle deployed"

# =============================================================================
# STEP 8: Collect CloudFormation outputs
# =============================================================================
step "8 / Collect CloudFormation outputs"
cd "$REPO_ROOT"

USER_POOL_ID=$(_cfn_output DfmeaAuthStack DfmeaUserPoolId)
USER_POOL_CLIENT_ID=$(_cfn_output DfmeaAuthStack DfmeaUserPoolClientId)
M2M_USER_POOL_ID=$(_cfn_output DfmeaAuthStack DfmeaM2MUserPoolId)
M2M_CLIENT_ID=$(_cfn_output DfmeaAuthStack DfmeaM2MClientId)
REST_API_URL=$(_cfn_output DfmeaApiStack DfmeaRestApiUrl)
WS_API_URL=$(_cfn_output DfmeaApiStack DfmeaWsApiUrl)
HOSTING_BUCKET=$(_cfn_output DfmeaFrontendStack DfmeaHostingBucket)
DISTRIBUTION_ID=$(_cfn_output DfmeaFrontendStack DfmeaDistributionId)
CF_URL=$(_cfn_output DfmeaFrontendStack DfmeaFrontendUrl)
REVIEW_SM_ARN=$(_cfn_output DfmeaOrchestrationStack DfmeaReviewSmArn)
HITL_TOPIC_ARN=$(_cfn_output DfmeaOrchestrationStack DfmeaHitlTopicArn)
ML_MODELS_BUCKET=$(_cfn_output DfmeaDataStack MlModelsBucketName)
MCP_TOOLS_ARN=$(_cfn_output DfmeaAgentCoreStack DfmeaMcpToolsFnArn)
# GATEWAY_ID / GATEWAY_URL resolved dynamically in Step 11 via CLI
GATEWAY_URL=""
GATEWAY_ID=""

log "User Pool ID        : $USER_POOL_ID"
log "REST API URL        : $REST_API_URL"
log "WebSocket URL       : $WS_API_URL"
log "Frontend URL        : $CF_URL"
log "Review SM ARN       : $REVIEW_SM_ARN"
log "ML Models Bucket    : $ML_MODELS_BUCKET"
log "MCP Tools ARN       : $MCP_TOOLS_ARN"
ok "Outputs collected"

# =============================================================================
# STEP 8a (pre-patch): Re-deploy OrchestrationStack with correct portal URL
# The SNS HITL notification bodies embed the portal URL inside the Step
# Functions state machine definition.  That definition is baked into
# CloudFormation at CDK-synth time, so we must synth/deploy a second time
# now that CF_URL is known.
# =============================================================================
if [[ "${SKIP_CDK:-0}" != "1" && -n "${CF_URL:-}" && "$CF_URL" != "None" ]]; then
  step "8a / Re-deploy OrchestrationStack with portal URL"
  log "Portal URL: $CF_URL"
  (cd "$REPO_ROOT/infra" && PORTAL_URL="$CF_URL" cdk deploy DfmeaOrchestrationStack --require-approval never)
  ok "OrchestrationStack updated with portal URL"
else
  warn "Skipping OrchestrationStack portal URL patch (SKIP_CDK=1 or CF_URL not set)"
fi

# =============================================================================
# STEP 8 (post-deploy): Env var patches — cyclic dependency bypass
# =============================================================================
step "8 (post-deploy) / Lambda env var patches"

# 8a. REVIEW_SM_ARN on dfmea-intake + dfmea-api
log "8a. REVIEW_SM_ARN → dfmea-intake"
if [[ -n "$REVIEW_SM_ARN" && "$REVIEW_SM_ARN" != "None" ]]; then
  _update_lambda_env "dfmea-intake" "REVIEW_SM_ARN" "$REVIEW_SM_ARN"
  ok "  REVIEW_SM_ARN set on dfmea-intake"
  _update_lambda_env "dfmea-api" "REVIEW_SM_ARN" "$REVIEW_SM_ARN"
  ok "  REVIEW_SM_ARN set on dfmea-api"
else
  warn "  REVIEW_SM_ARN not resolved — skipping"
fi

# 8b. WEBSOCKET_ENDPOINT on dfmea-ws-fanout
log "8b. WEBSOCKET_ENDPOINT → dfmea-ws-fanout"
if [[ -n "$WS_API_URL" && "$WS_API_URL" != "None" ]]; then
  WS_MANAGEMENT_URL=$(echo "$WS_API_URL" | sed 's|wss://|https://|')
  _update_lambda_env "dfmea-ws-fanout" "WEBSOCKET_ENDPOINT" "$WS_MANAGEMENT_URL"
  ok "  WEBSOCKET_ENDPOINT set on dfmea-ws-fanout"
else
  warn "  WS_API_URL not resolved — skipping WEBSOCKET_ENDPOINT"
fi

# 8c. ML_MODELS_BUCKET_NAME on intake, all 5 invoker Lambdas, and dfmea-mcp-tools
log "8c. ML_MODELS_BUCKET_NAME → intake + invoker Lambdas + dfmea-mcp-tools"
if [[ -n "$ML_MODELS_BUCKET" && "$ML_MODELS_BUCKET" != "None" ]]; then
  for fn in dfmea-intake dfmea-invoke-failure-mode dfmea-invoke-structural dfmea-invoke-regulatory dfmea-invoke-other dfmea-invoke-analyst dfmea-mcp-tools; do
    _update_lambda_env "$fn" "ML_MODELS_BUCKET_NAME" "$ML_MODELS_BUCKET" \
      && log "    ML_MODELS_BUCKET_NAME set on $fn" \
      || warn "    Could not set ML_MODELS_BUCKET_NAME on $fn"
  done
  ok "  ML_MODELS_BUCKET_NAME patched"
else
  warn "  ML_MODELS_BUCKET not resolved — skipping"
fi

# 8d. (moved) AGENT_RUNTIME_ARN is wired onto each invoker Lambda in step 11
#     after the AgentCore Runtimes are launched by the starter toolkit.
log "8d. AGENT_RUNTIME_ARN wiring deferred to step 11 (post runtime launch)"

# 8e. GATEWAY_URL on dfmea-mcp-tools
log "8e. GATEWAY_URL → dfmea-mcp-tools"
if [[ -n "$GATEWAY_URL" && "$GATEWAY_URL" != "None" ]]; then
  _update_lambda_env "dfmea-mcp-tools" "GATEWAY_URL" "$GATEWAY_URL"
  ok "  GATEWAY_URL set on dfmea-mcp-tools"
else
  warn "  GATEWAY_URL not resolved — skipping"
fi

# 8f. HITL_GATE_FN_NAME on dfmea-api
log "8f. HITL_GATE_FN_NAME → dfmea-api"
_update_lambda_env "dfmea-api" "HITL_GATE_FN_NAME" "dfmea-hitl-gate"
ok "  HITL_GATE_FN_NAME set on dfmea-api"

# 8f2. NEPTUNE_ENDPOINT + NEPTUNE_PORT on ontology clients
log "8f2. NEPTUNE_ENDPOINT → dfmea-api + dfmea-mcp-tools"
_NEPTUNE_EP=$(_cfn_output DfmeaNeptuneStack DfmeaNeptuneEndpoint)
if [[ -n "$_NEPTUNE_EP" && "$_NEPTUNE_EP" != "None" ]]; then
  for fn in dfmea-api dfmea-mcp-tools; do
    _update_lambda_env "$fn" "NEPTUNE_ENDPOINT" "$_NEPTUNE_EP"
    _update_lambda_env "$fn" "NEPTUNE_PORT"     "8182"
  done
  ok "  NEPTUNE_ENDPOINT set on dfmea-api and dfmea-mcp-tools"
else
  warn "  DfmeaNeptuneEndpoint output not found — ontology endpoints will degrade gracefully"
fi

# 8g. Grant OrchLambdaRole StartExecution on Review SM
log "8g. Granting OrchLambdaRole StartExecution on Review SM"
ORCH_ROLE_NAME=$(aws cloudformation describe-stack-resources \
  --stack-name DfmeaOrchestrationStack \
  --query "StackResources[?starts_with(LogicalResourceId,'OrchLambdaRole') && ResourceType=='AWS::IAM::Role'].PhysicalResourceId | [0]" \
  --output text 2>/dev/null || echo "")
if [[ -n "$ORCH_ROLE_NAME" && "$ORCH_ROLE_NAME" != "None" && -n "${REVIEW_SM_ARN:-}" && "$REVIEW_SM_ARN" != "None" ]]; then
  aws iam put-role-policy \
    --role-name "$ORCH_ROLE_NAME" \
    --policy-name AllowStartReviewSM \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"states:StartExecution\",\"Resource\":\"${REVIEW_SM_ARN}\"}]}"
  ok "  IAM StartExecution policy attached to $ORCH_ROLE_NAME"
else
  warn "  Could not resolve OrchLambdaRole — attach manually (DEPLOYMENT.md §8c)"
fi

# 8h. Wire KB IDs into the MCP tools Lambda (where the search_*_kb tools execute)
#     and the invoker Lambdas (kept for the report/synthesis stages).
log "8h. KB IDs → dfmea-mcp-tools + invoker Lambdas"
FAILURE_MODE_KB_ID=$(_ssm_get "/dfmea/failure_mode_kb_id")
REGULATORY_KB_ID=$(_ssm_get "/dfmea/regulatory_kb_id")
log "  Failure Mode KB : $FAILURE_MODE_KB_ID"
log "  Regulatory KB   : $REGULATORY_KB_ID"

if [[ -n "$FAILURE_MODE_KB_ID" && "$FAILURE_MODE_KB_ID" != "None" ]]; then
  # dfmea-mcp-tools is where the search_failure_mode_kb MCP tool actually runs
  _update_lambda_env "dfmea-mcp-tools" "FAILURE_MODE_KB_ID" "$FAILURE_MODE_KB_ID"
  ok "    FAILURE_MODE_KB_ID set on dfmea-mcp-tools"
  _update_lambda_env "dfmea-invoke-failure-mode" "FAILURE_MODE_KB_ID" "$FAILURE_MODE_KB_ID"
  ok "    FAILURE_MODE_KB_ID set on dfmea-invoke-failure-mode"
else
  warn "    Could not resolve failure-mode KB ID — skipping"
fi
if [[ -n "$REGULATORY_KB_ID" && "$REGULATORY_KB_ID" != "None" ]]; then
  # dfmea-mcp-tools is where the search_regulatory_kb MCP tool actually runs
  _update_lambda_env "dfmea-mcp-tools" "REGULATORY_KB_ID" "$REGULATORY_KB_ID"
  ok "    REGULATORY_KB_ID set on dfmea-mcp-tools"
  _update_lambda_env "dfmea-invoke-regulatory" "REGULATORY_KB_ID" "$REGULATORY_KB_ID"
  ok "    REGULATORY_KB_ID set on dfmea-invoke-regulatory"
else
  warn "    Could not resolve regulatory KB ID — skipping"
fi

# 8i. UPLOADS_BUCKET_NAME on invoker Lambdas (used by the analyst report stage)
log "8i. UPLOADS_BUCKET_NAME → invoker Lambdas"
UPLOADS_BUCKET_NAME_VALUE="dfmea-uploads-${CDK_DEFAULT_ACCOUNT}"
for fn in dfmea-invoke-failure-mode dfmea-invoke-structural dfmea-invoke-regulatory dfmea-invoke-other dfmea-invoke-analyst; do
  _update_lambda_env "$fn" "UPLOADS_BUCKET_NAME" "$UPLOADS_BUCKET_NAME_VALUE" \
    && log "    UPLOADS_BUCKET_NAME set on $fn" \
    || warn "    Could not set UPLOADS_BUCKET_NAME on $fn"
done
ok "  UPLOADS_BUCKET_NAME patched"

ok "Post-deploy env var patches complete"

# =============================================================================
# STEP 9: Neptune ontology load
# Convert ontology JSON-LD → N-Triples, upload to S3, then invoke the
# dfmea-api Lambda directly (bypasses API Gateway — no JWT needed) to run
# SPARQL INSERT DATA from within the VPC.
# =============================================================================
step "9 / Neptune ontology load"
if [[ "$SKIP_SEED" == "1" ]]; then
  warn "Skipping Neptune load (--skip-seed)"
else
  ONTOLOGY_S3_BUCKET="dfmea-ontology-${CDK_DEFAULT_ACCOUNT}"
  ONTOLOGY_JSON_LD="$REPO_ROOT/ontology/ontology.json-ld"

  if [[ ! -f "$ONTOLOGY_JSON_LD" ]]; then
    warn "ontology.json-ld not found — skipping Neptune load"
  else
    # Convert JSON-LD → N-Triples using rdflib (installed in .venv)
    log "Converting ontology JSON-LD to N-Triples..."
    ONTOLOGY_NT="/tmp/dfmea-ontology-$$.nt"
    ONTOLOGY_JSON_LD="$ONTOLOGY_JSON_LD" ONTOLOGY_NT="$ONTOLOGY_NT" python3 - <<'PYEOF'
import sys, os
try:
    from rdflib import Graph
    src  = os.environ["ONTOLOGY_JSON_LD"]
    dest = os.environ["ONTOLOGY_NT"]
    g = Graph()
    g.parse(src, format="json-ld")
    g.serialize(destination=dest, format="nt", encoding="utf-8")
    print(f"[ontology] {len(g)} triples written to {dest}")
except Exception as e:
    print(f"[ontology] N-Triples conversion failed: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
    ok "N-Triples written to $ONTOLOGY_NT"

    # Upload to S3 (ontology bucket)
    aws s3 cp "$ONTOLOGY_NT" \
      "s3://${ONTOLOGY_S3_BUCKET}/ontology/ontology.nt" \
      --content-type "application/n-triples" > /dev/null
    rm -f "$ONTOLOGY_NT"
    ok "Ontology uploaded to s3://${ONTOLOGY_S3_BUCKET}/ontology/ontology.nt"

    # Invoke dfmea-api Lambda directly — Lambda is VPC-attached and can reach Neptune.
    # Ensure the function is fully deployed (not mid-update) before invoking, then
    # retry a few times: Neptune may be warming up, the api Lambda may still be
    # settling from the env-var patches, or IAM changes may need to propagate.
    log "Invoking dfmea-api Lambda to load ontology via SPARQL INSERT..."
    LOAD_PAYLOAD='{"httpMethod":"POST","path":"/ontology/load","requestContext":{"httpMethod":"POST"},"queryStringParameters":null,"body":null}'
    LOAD_OUT="/tmp/dfmea-ontology-load-result-$$.json"
    LOAD_ERR="/tmp/dfmea-ontology-load-err-$$.txt"
    aws lambda wait function-updated --function-name dfmea-api 2>/dev/null || true
    aws lambda wait function-active  --function-name dfmea-api 2>/dev/null || true

    LOAD_STATUS="unknown"
    for attempt in 1 2 3 4 5; do
      rm -f "$LOAD_OUT" "$LOAD_ERR"
      # Use boto3 instead of `aws lambda invoke`: the deployment host uses AWS
      # CLI v1, which does not support the v2-only --cli-binary-format option.
      # boto3 also avoids shell/base64 differences between CLI major versions.
      if LOAD_OUT="$LOAD_OUT" AWS_DEFAULT_REGION="$CDK_DEFAULT_REGION" python3 - <<'PYEOF' >"$LOAD_ERR" 2>&1
import json
import os
import sys
import boto3

payload = {
    "httpMethod": "POST",
    "path": "/ontology/load",
    "requestContext": {"httpMethod": "POST"},
    "queryStringParameters": None,
    "body": None,
}
try:
    response = boto3.client(
        "lambda", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    ).invoke(
        FunctionName="dfmea-api",
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    body = response["Payload"].read()
    with open(os.environ["LOAD_OUT"], "wb") as output:
        output.write(body)
    if response.get("FunctionError"):
        print(f"Lambda FunctionError: {response['FunctionError']}", file=sys.stderr)
        print(body.decode("utf-8", errors="replace"), file=sys.stderr)
        sys.exit(1)
except Exception as exc:
    print(f"Lambda invoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
      then
        LOAD_STATUS=$(python3 -c "
import json
try:
    d = json.load(open('${LOAD_OUT}'))
    body = json.loads(d.get('body','{}')) if isinstance(d.get('body'), str) else d.get('body', {})
    print((body or {}).get('status', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")
        if [[ "$LOAD_STATUS" == "complete" ]]; then
          break
        fi
        warn "  attempt $attempt/5: ontology load returned status '$LOAD_STATUS'"
        [[ -f "$LOAD_OUT" ]] && echo "      response: $(head -c 400 "$LOAD_OUT")"
      else
        warn "  attempt $attempt/5: boto3 Lambda invoke failed:"
        [[ -f "$LOAD_ERR" ]] && sed 's/^/        /' "$LOAD_ERR"
      fi
      [[ "$attempt" != "5" ]] && sleep 15
    done
    rm -f "$LOAD_OUT" "$LOAD_ERR"
    if [[ "$LOAD_STATUS" == "complete" ]]; then
      ok "Neptune ontology load complete"
    else
      warn "Neptune ontology load did not complete (last status: $LOAD_STATUS)."
      warn "  See the invoke errors above and the dfmea-api CloudWatch logs."
    fi
  fi
fi

# =============================================================================
# STEP 10: Bedrock KB ingestion jobs
# =============================================================================
step "10 / Bedrock Knowledge Base document upload + ingestion"
if [[ "$SKIP_SEED" == "1" ]]; then
  warn "Skipping KB document upload and ingestion (--skip-seed)"
else
  cd "$REPO_ROOT"
  if [[ -f "$REPO_ROOT/scripts/create_bedrock_kb.py" ]]; then
    KB_DOCS_BUCKET=$(_cfn_output DfmeaKnowledgeBaseStack DfmeaKbDocsBucket)
    if [[ -z "$KB_DOCS_BUCKET" || "$KB_DOCS_BUCKET" == "None" ]]; then
      fail "DfmeaKbDocsBucket output not resolved"
    fi

    log "Uploading failure-mode KB documents"
    aws s3 cp "$REPO_ROOT/sample-data/reference/aiag-vda-ap-rules-summary.md" \
      "s3://${KB_DOCS_BUCKET}/failure-mode-kb/aiag-vda-ap-rules-summary.md"
    aws s3 cp "$REPO_ROOT/sample-data/reference/b-pillar-bom.csv" \
      "s3://${KB_DOCS_BUCKET}/failure-mode-kb/b-pillar-bom.csv"

    log "Uploading regulatory KB documents"
    aws s3 cp "$REPO_ROOT/sample-data/reference/fmvss-214-summary.md" \
      "s3://${KB_DOCS_BUCKET}/regulatory-kb/fmvss-214-summary.md"
    aws s3 cp "$REPO_ROOT/sample-data/reference/fmvss-216-summary.md" \
      "s3://${KB_DOCS_BUCKET}/regulatory-kb/fmvss-216-summary.md"
    ok "KB source documents uploaded to s3://${KB_DOCS_BUCKET}/"

    python3 scripts/create_bedrock_kb.py
    ok "Bedrock Knowledge Bases ingested and ready"
  else
    fail "scripts/create_bedrock_kb.py not found"
  fi
fi

# =============================================================================
# STEP 10b: Wire KB IDs onto dfmea-mcp-tools + invoker Lambdas
#           MUST run AFTER Step 10, because the Bedrock Knowledge Bases (and
#           their SSM parameters /dfmea/failure_mode_kb_id, /dfmea/regulatory_kb_id)
#           are created in Step 10. Step 8h runs earlier — before the KBs exist —
#           so it can't resolve the IDs. dfmea-mcp-tools is where the
#           search_failure_mode_kb / search_regulatory_kb MCP tools execute, so it
#           MUST have these env vars or those tools return "KB not configured".
# =============================================================================
step "10b / Wire KB IDs → dfmea-mcp-tools + invoker Lambdas (post-KB-creation)"
if [[ "$SKIP_SEED" == "1" ]]; then
  warn "Skipping KB-ID wiring (--skip-seed)"
else
  FAILURE_MODE_KB_ID=$(_ssm_get "/dfmea/failure_mode_kb_id")
  REGULATORY_KB_ID=$(_ssm_get "/dfmea/regulatory_kb_id")
  log "  Failure Mode KB : ${FAILURE_MODE_KB_ID:-<unresolved>}"
  log "  Regulatory KB   : ${REGULATORY_KB_ID:-<unresolved>}"
  if [[ -n "$FAILURE_MODE_KB_ID" && "$FAILURE_MODE_KB_ID" != "None" ]]; then
    _update_lambda_env "dfmea-mcp-tools" "FAILURE_MODE_KB_ID" "$FAILURE_MODE_KB_ID" \
      && ok "  FAILURE_MODE_KB_ID set on dfmea-mcp-tools"
    _update_lambda_env "dfmea-invoke-failure-mode" "FAILURE_MODE_KB_ID" "$FAILURE_MODE_KB_ID" \
      && ok "  FAILURE_MODE_KB_ID set on dfmea-invoke-failure-mode"
  else
    warn "  failure-mode KB ID still unresolved — skipping"
  fi
  if [[ -n "$REGULATORY_KB_ID" && "$REGULATORY_KB_ID" != "None" ]]; then
    _update_lambda_env "dfmea-mcp-tools" "REGULATORY_KB_ID" "$REGULATORY_KB_ID" \
      && ok "  REGULATORY_KB_ID set on dfmea-mcp-tools"
    _update_lambda_env "dfmea-invoke-regulatory" "REGULATORY_KB_ID" "$REGULATORY_KB_ID" \
      && ok "  REGULATORY_KB_ID set on dfmea-invoke-regulatory"
  else
    warn "  regulatory KB ID still unresolved — skipping"
  fi
fi

# =============================================================================
# STEP 11: AgentCore Gateway tool registration (10 tools)
# =============================================================================
step "11 / AgentCore agents + Gateway + tool registration"

if [[ -z "$MCP_TOOLS_ARN" || "$MCP_TOOLS_ARN" == "None" ]]; then
  fail "MCP_TOOLS_ARN not resolved — AgentCore setup cannot continue"
else
  # ── 11a. AgentCore Runtimes are deployed after the Gateway (step 11d) via
  #         the bedrock_agentcore_starter_toolkit.
  # ── 11b/11c. Create AgentCore Gateway (CUSTOM_JWT / MCP) + register the 10
  #            tools as one Lambda target, via scripts/create_gateway.py
  #            (boto3 — mirrors the working AgentCore Gateway reference).
  log "11b. Creating AgentCore Gateway + tool target (CUSTOM_JWT / MCP)"
  GATEWAY_URL=""
  if [[ -z "$M2M_USER_POOL_ID" || "$M2M_USER_POOL_ID" == "None" || \
        -z "$M2M_CLIENT_ID" || "$M2M_CLIENT_ID" == "None" ]]; then
    fail "M2M pool/client not resolved — Gateway creation cannot continue"
  else
    GW_OUT=$(python3 "$REPO_ROOT/scripts/create_gateway.py" \
      --region "$CDK_DEFAULT_REGION" \
      --gateway-name "dfmea-gateway" \
      --mcp-tools-arn "$MCP_TOOLS_ARN" \
      --m2m-user-pool-id "$M2M_USER_POOL_ID" \
      --m2m-client-id "$M2M_CLIENT_ID" 2>&1) && GW_RC=0 || GW_RC=1
    echo "$GW_OUT"
    if [[ "$GW_RC" == "0" ]]; then
      GATEWAY_URL=$(echo "$GW_OUT" | sed -n 's/^GATEWAY_URL=//p' | tail -1)
      if [[ -n "$GATEWAY_URL" ]]; then
        _update_lambda_env "dfmea-mcp-tools" "GATEWAY_URL" "$GATEWAY_URL" >/dev/null 2>&1 \
          || fail "Could not set GATEWAY_URL on dfmea-mcp-tools"
        ok "AgentCore Gateway ready: $GATEWAY_URL"
      else
        fail "Gateway created but URL was not returned"
      fi
    else
      fail "Gateway creation failed — see output above"
    fi
  fi

  # ── 11d. Deploy the 5 AgentCore Runtimes via the starter toolkit ──────────
  log "11d. Deploying AgentCore Runtimes (bedrock_agentcore_starter_toolkit)"
  pip install -q bedrock_agentcore_starter_toolkit \
    || fail "Could not install bedrock_agentcore_starter_toolkit"

  # Ensure the runtime execution role can pull the agent container image from ECR.
  # (CreateAgentRuntime validates ecr:GetAuthorizationToken/BatchGetImage/GetDownloadUrlForLayer.)
  # Idempotent put-role-policy so a --skip-cdk re-run works without redeploying the stack.
  aws iam put-role-policy --role-name dfmea-agentcore-runtime-role \
    --policy-name DfmeaRuntimeEcrPull \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ecr:GetAuthorizationToken\"],\"Resource\":\"*\"},{\"Effect\":\"Allow\",\"Action\":[\"ecr:BatchGetImage\",\"ecr:GetDownloadUrlForLayer\",\"ecr:BatchCheckLayerAvailability\"],\"Resource\":\"arn:aws:ecr:${CDK_DEFAULT_REGION}:${CDK_DEFAULT_ACCOUNT}:repository/bedrock-agentcore-*\"}]}" \
    >/dev/null 2>&1 \
    && log "  ECR pull policy attached to dfmea-agentcore-runtime-role" \
    || warn "  Could not attach ECR pull policy — CreateAgentRuntime may fail"
  # Allow IAM to propagate before CreateAgentRuntime validates the role
  sleep 8

  RUNTIME_ROLE_ARN=$(_cfn_output DfmeaAgentCoreStack DfmeaAgentCoreRuntimeRoleArn)
  M2M_TOKEN_URL=$(_cfn_output DfmeaAuthStack DfmeaM2MTokenUrl)

  if [[ -z "$GATEWAY_URL" || "$GATEWAY_URL" == "None" ]]; then
    fail "GATEWAY_URL unavailable — refusing to deploy unusable runtimes"
  fi
  if [[ -z "$M2M_TOKEN_URL" || "$M2M_TOKEN_URL" == "None" ]]; then
    fail "M2M token URL not resolved — refusing to deploy unusable runtimes"
  fi
  if [[ -z "$RUNTIME_ROLE_ARN" || "$RUNTIME_ROLE_ARN" == "None" ]]; then
    fail "AgentCore runtime role ARN not resolved"
  fi

  RT_MANIFEST="$REPO_ROOT/agentcore_runtimes_deployment.json"
  rm -f "$RT_MANIFEST"
  GATEWAY_URL="$GATEWAY_URL" COGNITO_TOKEN_URL="$M2M_TOKEN_URL" \
  python3 "$REPO_ROOT/scripts/deploy_agentcore_runtimes.py" \
    --region "$CDK_DEFAULT_REGION" \
    --execution-role-arn "$RUNTIME_ROLE_ARN" \
    --gateway-url "$GATEWAY_URL" \
    --cognito-token-url "$M2M_TOKEN_URL" \
    --model-id "us.anthropic.claude-haiku-4-5-20251001-v1:0" \
    --inbound-auth iam

  # Wire AGENT_RUNTIME_ARN onto each invoker Lambda from the fresh manifest.
  [[ -f "$RT_MANIFEST" ]] || fail "Runtime deployment manifest was not created"
  for pair in \
    "failure_mode:dfmea-invoke-failure-mode" \
    "structural:dfmea-invoke-structural" \
    "regulatory:dfmea-invoke-regulatory" \
    "other:dfmea-invoke-other" \
    "analyst:dfmea-invoke-analyst"; do
    label="${pair%%:*}"; fn="${pair##*:}"
    arn=$(python3 -c "import json; d=json.load(open('$RT_MANIFEST')); print(d.get('runtimes',{}).get('$label',''))" 2>/dev/null || echo "")
    if [[ -z "$arn" || "$arn" == "None" ]]; then
      fail "No runtime ARN produced for $label"
    fi
    _update_lambda_env "$fn" "AGENT_RUNTIME_ARN" "$arn" \
      && log "  AGENT_RUNTIME_ARN set on $fn" \
      || fail "Could not set AGENT_RUNTIME_ARN on $fn"
  done
fi

# ── 11e. Deploy the global assistant AgentCore Runtime + wire the API Lambda ──
# The chat assistant is a pure AgentCore Runtime (bedrock-agentcore-control
# create_agent_runtime) with AgentCore Memory. It reads live DynamoDB + KB data
# and is invoked by dfmea-api via invoke_agent_runtime. Runs after the KBs exist
# (their IDs are read from SSM) and after CDK created the code bucket + role.
step "11e / Assistant AgentCore Runtime (chat agent)"
ASSISTANT_ROLE_ARN=$(_cfn_output DfmeaAgentCoreStack DfmeaAssistantRuntimeRoleArn)
if [[ -z "$ASSISTANT_ROLE_ARN" || "$ASSISTANT_ROLE_ARN" == "None" ]]; then
  warn "  Assistant runtime role ARN not resolved — skipping assistant runtime deploy"
else
  # 'uv' is required by scripts/deploy_assistant_runtime.py to package the runtime.
  command -v uv >/dev/null 2>&1 || pip install -q uv || warn "  could not install uv"
  # Use the same model already enabled for the 5 agents (claude-haiku-4-5) so the
  # assistant works without a separate manual Bedrock model-access step. Switch to
  # a higher-quality model (e.g. claude-sonnet-4-6) here once it is enabled in the
  # account for closer parity with SOURCE.
  python3 "$REPO_ROOT/scripts/deploy_assistant_runtime.py" \
    --region "$CDK_DEFAULT_REGION" \
    --model-id "us.anthropic.claude-haiku-4-5-20251001-v1:0" \
    --skip-smoke-test \
    || warn "  assistant runtime deploy reported errors — check output above"
  # deploy_assistant_runtime.py writes the ARN to SSM; patch it onto dfmea-api
  # (ASSISTANT_RUNTIME_ARN is intentionally not set at CDK synth — same pattern
  # as AGENT_RUNTIME_ARN — because the runtime is created post-CDK).
  ASSISTANT_RT_ARN=$(_ssm_get "/dfmea/assistant_runtime_arn")
  if [[ -n "$ASSISTANT_RT_ARN" && "$ASSISTANT_RT_ARN" != "None" ]]; then
    _update_lambda_env "dfmea-api" "ASSISTANT_RUNTIME_ARN" "$ASSISTANT_RT_ARN" \
      && ok "  ASSISTANT_RUNTIME_ARN set on dfmea-api" \
      || warn "  Could not set ASSISTANT_RUNTIME_ARN on dfmea-api"
  else
    warn "  assistant runtime ARN not in SSM — set ASSISTANT_RUNTIME_ARN on dfmea-api manually"
  fi
fi

# Bound retention for AgentCore-managed runtime logs, which can contain model
# prompts and tool payloads. Runtime log groups are service-created, so discover
# them after deployment and apply the same 30-day policy used by Lambda logs.
step "11f / AgentCore runtime log retention"
AGENTCORE_LOG_GROUPS=""
for attempt in 1 2 3 4 5 6; do
  AGENTCORE_LOG_GROUPS=$(aws logs describe-log-groups \
    --log-group-name-prefix "/aws/bedrock-agentcore/runtimes/" \
    --query 'logGroups[].logGroupName' --output text 2>/dev/null || echo "")
  [[ -n "$AGENTCORE_LOG_GROUPS" && "$AGENTCORE_LOG_GROUPS" != "None" ]] && break
  sleep 5
done
if [[ -z "$AGENTCORE_LOG_GROUPS" || "$AGENTCORE_LOG_GROUPS" == "None" ]]; then
  warn "No AgentCore runtime log groups found yet; retention must be applied after first invocation"
else
  for log_group in $AGENTCORE_LOG_GROUPS; do
    aws logs put-retention-policy \
      --log-group-name "$log_group" \
      --retention-in-days 30 \
      || fail "Could not set 30-day retention on $log_group"
  done
  ok "30-day retention applied to AgentCore runtime log groups"
fi

# =============================================================================
# STEP 12: Retrieve M2M client secret + propagate to Lambdas
# =============================================================================
step "12 / M2M client secret propagation"

# Fetch the actual client secret from Cognito and populate Secrets Manager.
# AgentCore Runtimes read this secret directly; do not copy credentials into
# Lambda environment variables.
if [[ -z "$M2M_USER_POOL_ID" || "$M2M_USER_POOL_ID" == "None" || \
      -z "$M2M_CLIENT_ID"    || "$M2M_CLIENT_ID"    == "None" ]]; then
  fail "M2M_USER_POOL_ID or M2M_CLIENT_ID not resolved"
fi

log "Fetching M2M client secret from Cognito"
M2M_RAW_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "$M2M_USER_POOL_ID" \
  --client-id    "$M2M_CLIENT_ID" \
  --query 'UserPoolClient.ClientSecret' --output text 2>/dev/null || echo "")
if [[ -z "$M2M_RAW_SECRET" || "$M2M_RAW_SECRET" == "None" ]]; then
  fail "Could not fetch M2M client secret from Cognito"
fi

_M2M_SECRET_JSON=$(M2M_CLIENT_ID="$M2M_CLIENT_ID" M2M_RAW_SECRET="$M2M_RAW_SECRET" \
  python3 -c 'import json,os; print(json.dumps({"client_id": os.environ["M2M_CLIENT_ID"], "client_secret": os.environ["M2M_RAW_SECRET"]}))')
aws secretsmanager put-secret-value \
  --secret-id "dfmea/m2m-client-secret" \
  --secret-string "$_M2M_SECRET_JSON" > /dev/null \
  || fail "Could not update dfmea/m2m-client-secret"
ok "Secrets Manager updated with AgentCore M2M credentials"

# =============================================================================
# STEP 13: Deploy dfmea-notifier Lambda + SNS subscription
# =============================================================================
step "13 / Deploy dfmea-notifier Lambda"

NOTIFIER_ROLE_NAME="dfmea-notifier-role"
NOTIFIER_ROLE_ARN=$(aws iam get-role --role-name "$NOTIFIER_ROLE_NAME" \
  --query 'Role.Arn' --output text 2>/dev/null || echo "")

if [[ -z "$NOTIFIER_ROLE_ARN" || "$NOTIFIER_ROLE_ARN" == "None" ]]; then
  log "Creating IAM role $NOTIFIER_ROLE_NAME"
  NOTIFIER_ROLE_ARN=$(aws iam create-role \
    --role-name "$NOTIFIER_ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --query 'Role.Arn' --output text)
  aws iam attach-role-policy --role-name "$NOTIFIER_ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  aws iam put-role-policy --role-name "$NOTIFIER_ROLE_NAME" \
    --policy-name AllowSES \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ses:SendEmail","ses:SendRawEmail"],"Resource":"*"}]}'
  sleep 15  # allow IAM to propagate
  ok "IAM role created: $NOTIFIER_ROLE_ARN"
fi

# Package notifier Lambda
NOTIFIER_ZIP="/tmp/dfmea-notifier-$$.zip"
cd "$REPO_ROOT"
zip -j "$NOTIFIER_ZIP" lambdas/notifier/handler.py > /dev/null

PORTAL_URL_VALUE="${PORTAL_URL:-${CF_URL:-}}"
if [[ -z "$PORTAL_URL_VALUE" || "$PORTAL_URL_VALUE" == "None" ]]; then
  PORTAL_URL_VALUE=$(aws cloudformation describe-stacks \
    --stack-name DfmeaFrontendStack \
    --query "Stacks[0].Outputs[?OutputKey=='DistributionUrl'].OutputValue" \
    --output text 2>/dev/null || echo '')
fi
NOTIFIER_ENV="Variables={SES_SENDER=${ADMIN_EMAIL},SES_RECIPIENT=${REVIEWER_EMAIL},PORTAL_URL=${PORTAL_URL_VALUE}}"

if aws lambda get-function --function-name dfmea-notifier >/dev/null 2>&1; then
  aws lambda update-function-code --function-name dfmea-notifier \
    --zip-file "fileb://${NOTIFIER_ZIP}" --output text --query 'FunctionName' > /dev/null
  aws lambda wait function-updated --function-name dfmea-notifier
  aws lambda update-function-configuration --function-name dfmea-notifier \
    --role "$NOTIFIER_ROLE_ARN" \
    --environment "$NOTIFIER_ENV" --output text --query 'FunctionName' > /dev/null
  ok "dfmea-notifier updated"
else
  aws lambda create-function \
    --function-name dfmea-notifier \
    --runtime python3.12 \
    --handler handler.handler \
    --role "$NOTIFIER_ROLE_ARN" \
    --zip-file "fileb://${NOTIFIER_ZIP}" \
    --environment "$NOTIFIER_ENV" \
    --timeout 30 \
    --output text --query 'FunctionName' > /dev/null
  ok "dfmea-notifier created"
fi

rm -f "$NOTIFIER_ZIP"

# Subscribe notifier Lambda to HITL SNS topic (idempotent)
NOTIFIER_ARN=$(aws lambda get-function --function-name dfmea-notifier \
  --query 'Configuration.FunctionArn' --output text)

if [[ -n "$HITL_TOPIC_ARN" && "$HITL_TOPIC_ARN" != "None" ]]; then
  EXISTING_SUB=$(aws sns list-subscriptions-by-topic --topic-arn "$HITL_TOPIC_ARN" \
    --query "Subscriptions[?Endpoint=='${NOTIFIER_ARN}'].SubscriptionArn" \
    --output text 2>/dev/null || echo "")
  if [[ -z "$EXISTING_SUB" || "$EXISTING_SUB" == "None" ]]; then
    aws sns subscribe \
      --topic-arn "$HITL_TOPIC_ARN" \
      --protocol lambda \
      --notification-endpoint "$NOTIFIER_ARN" \
      --output text --query 'SubscriptionArn' > /dev/null
    aws lambda add-permission \
      --function-name dfmea-notifier \
      --statement-id AllowSNSInvoke \
      --action lambda:InvokeFunction \
      --principal sns.amazonaws.com \
      --source-arn "$HITL_TOPIC_ARN" \
      --output text --query 'Statement' > /dev/null 2>&1 || true
    ok "dfmea-notifier subscribed to SNS topic"
  else
    ok "dfmea-notifier already subscribed to SNS topic"
  fi
else
  warn "HITL_TOPIC_ARN not resolved — SNS subscription skipped"
fi

# =============================================================================
# STEP 14: Create Cognito admin user
# =============================================================================
step "14 / Create Cognito admin user"
aws cognito-idp admin-create-user \
  --user-pool-id "$USER_POOL_ID" \
  --username "$ADMIN_EMAIL" \
  --temporary-password "Temp@2026!" \
  --user-attributes Name=email,Value="$ADMIN_EMAIL" Name=email_verified,Value=true \
  --message-action SUPPRESS \
  2>/dev/null || warn "  Admin user may already exist — skipping create"

aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username "$ADMIN_EMAIL" \
  --password "$ADMIN_PASSWORD" \
  --permanent
ok "Cognito admin user ready: $ADMIN_EMAIL"

# Create admins group (idempotent)
aws cognito-idp create-group \
  --user-pool-id "$USER_POOL_ID" \
  --group-name admins 2>/dev/null || true
aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$USER_POOL_ID" \
  --username "$ADMIN_EMAIL" \
  --group-name admins
ok "Admin user added to 'admins' group"

# =============================================================================
# STEP 15: Build React frontend + sync to CloudFront
# =============================================================================
step "15 / Build React frontend"
if [[ "$SKIP_FRONTEND" == "1" ]]; then
  warn "Skipping frontend build (--skip-frontend)"
else
  # Build in /tmp to avoid WSL2 NTFS rename errors
  BUILD_DIR="/tmp/dfmea-ui-build-$$"
  log "Copying UI source to ${BUILD_DIR}..."
  rsync -a --exclude='node_modules' --exclude='dist' "$UI_DIR/" "$BUILD_DIR/"
  cd "$BUILD_DIR"
  npm install --legacy-peer-deps --silent
  npm run build
  ok "React build complete"

  # Write runtime config.json — fetched by the app at startup
  cat > dist/config.json << EOF
{
  "userPoolId": "${USER_POOL_ID}",
  "userPoolClientId": "${USER_POOL_CLIENT_ID}",
  "region": "${CDK_DEFAULT_REGION}",
  "restApiUrl": "${REST_API_URL}",
  "wsApiUrl": "${WS_API_URL}"
}
EOF
  log "config.json written"

  log "Syncing to S3 hosting bucket: $HOSTING_BUCKET"
  aws s3 sync "$BUILD_DIR/dist/" "s3://${HOSTING_BUCKET}/" --delete \
    --cache-control "public, max-age=31536000, immutable" \
    --exclude "index.html" \
    --exclude "config.json"

  aws s3 cp "$BUILD_DIR/dist/index.html" "s3://${HOSTING_BUCKET}/index.html" \
    --cache-control "no-cache, no-store, must-revalidate"

  aws s3 cp "$BUILD_DIR/dist/config.json" "s3://${HOSTING_BUCKET}/config.json" \
    --cache-control "no-cache, no-store, must-revalidate"

  rm -rf "$BUILD_DIR"

  log "Invalidating CloudFront cache: $DISTRIBUTION_ID"
  aws cloudfront create-invalidation \
    --distribution-id "$DISTRIBUTION_ID" \
    --paths "/*" \
    --output text --query 'Invalidation.Id' > /dev/null
  ok "Frontend deployed and CDN cache invalidated"
fi

# =============================================================================
# STEP 16: Print portal URL + final summary
# =============================================================================
step "16 / Deployment complete"

# Optional health check
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${REST_API_URL}health" 2>/dev/null || echo "000")
if [[ "$HTTP_STATUS" == "200" ]]; then
  ok "REST API health: HTTP $HTTP_STATUS"
else
  warn "REST API health returned HTTP $HTTP_STATUS (may need a few seconds to warm up)"
fi

echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${GREEN}  DFMEA Agentic Review System — Deployment Complete${NC}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${CYAN}Frontend URL${NC}       : ${CF_URL:-<see DfmeaFrontendStack outputs>}"
echo -e "  ${CYAN}REST API URL${NC}       : $REST_API_URL"
echo -e "  ${CYAN}WebSocket URL${NC}      : $WS_API_URL"
echo -e "  ${CYAN}AgentCore Gateway${NC}  : ${GATEWAY_URL:-<see DfmeaAgentCoreStack outputs>}"
echo -e "  ${CYAN}Admin login${NC}        : $ADMIN_EMAIL"
echo -e "  ${CYAN}Failure Mode KB${NC}    : ${FAILURE_MODE_KB_ID:-see SSM /dfmea/failure_mode_kb_id}"
echo -e "  ${CYAN}Regulatory KB${NC}      : ${REGULATORY_KB_ID:-see SSM /dfmea/regulatory_kb_id}"
echo ""
echo -e "  ${YELLOW}Remaining manual step:${NC}"
echo -e "  1. Reviewer ($REVIEWER_EMAIL) must confirm SNS subscription email"
echo ""
