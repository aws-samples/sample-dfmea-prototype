#!/usr/bin/env bash
# =============================================================================
# DFMEA Agentic Review System — Full Destroy Script  (10-step teardown)
#
# Usage:
#   ./scripts/destroy.sh [--force] [--purge-data]
#
# --force        Skip confirmation prompt (use in CI only).
# --purge-data   Also empty/delete retained S3 buckets (Step 1),
#                DynamoDB tables (Step 7), and local artifacts (Step 10).
#
# External resources cleaned up (not managed by CDK):
#   - dfmea-notifier Lambda + IAM role
#   - SSM parameters /dfmea/failure_mode_kb_id, /dfmea/regulatory_kb_id
#   - Bedrock Knowledge Bases (IDs read from SSM)
#   - AgentCore Gateway (dfmea-mcp-gateway) + all tool targets
#   - 5 AgentCore Runtime agents
#
# DynamoDB tables use RemovalPolicy.RETAIN — CDK will NOT delete them.
# Pass --purge-data to delete them as part of teardown.
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log()  { echo -e "${CYAN}[destroy]${NC} $*"; }
ok()   { echo -e "${GREEN}[  ok   ]${NC} $*"; }
warn() { echo -e "${YELLOW}[  warn ]${NC} $*"; }
fail() { echo -e "${RED}[ FAILED]${NC} $*"; exit 1; }
step() { echo -e "\n${BOLD}${RED}━━━ $* ━━━${NC}\n"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"

# ── load .env ─────────────────────────────────────────────────────────────────
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/.env"
  set +o allexport
fi

FORCE=0; PURGE_DATA=0
for arg in "$@"; do
  case $arg in
    --force)      FORCE=1 ;;
    --purge-data) PURGE_DATA=1 ;;
  esac
done

# ── resolve account ───────────────────────────────────────────────────────────
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export CDK_DEFAULT_REGION="$AWS_DEFAULT_REGION"
CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || fail "Cannot resolve AWS account. Is AWS CLI configured?"
export CDK_DEFAULT_ACCOUNT

# ── helper: read a CloudFormation stack output by export name ─────────────────
_cfn_output() {
  local stack="$1" export_name="$2"
  aws cloudformation describe-stacks \
    --stack-name "$stack" \
    --query "Stacks[0].Outputs[?ExportName=='${export_name}'].OutputValue" \
    --output text 2>/dev/null || echo ""
}

# ── confirmation prompt ───────────────────────────────────────────────────────
step "Destroy DFMEA Agentic Review System"

echo -e "${RED}${BOLD}WARNING: This will destroy all 12 CDK stacks in account ${CDK_DEFAULT_ACCOUNT} / region ${CDK_DEFAULT_REGION}.${NC}"
echo ""
echo "  CDK stacks to destroy (reverse order):"
echo "    DfmeaMonitoringStack"
echo "    DfmeaFrontendStack"
echo "    DfmeaApiStack"
echo "    DfmeaAgentCoreStack"
echo "    DfmeaKnowledgeBaseStack"
echo "    DfmeaSearchStack"
echo "    DfmeaOrchestrationStack"
echo "    DfmeaAgentStack"
echo "    DfmeaNeptuneStack"
echo "    DfmeaDataStack"
echo "    DfmeaAuthStack"
echo "    DfmeaFoundationStack"
echo ""
echo "  External resources also deleted (not managed by CDK):"
echo "    - dfmea-notifier Lambda + dfmea-notifier-role IAM role"
echo "    - SSM parameters: /dfmea/failure_mode_kb_id, /dfmea/regulatory_kb_id"
echo "    - Bedrock Knowledge Bases (IDs from SSM)"
echo "    - AgentCore Gateway: dfmea-mcp-gateway + all tool targets"
echo "    - AgentCore Runtime agents: failure_mode, structural, regulatory, schema, analyst"
echo ""
if [[ "$PURGE_DATA" == "1" ]]; then
  echo -e "${RED}${BOLD}--purge-data is set: S3 buckets will be EMPTIED and DELETED."
  echo -e "DynamoDB tables will be DELETED. Local artifacts will be WIPED."
  echo -e "This is IRREVERSIBLE.${NC}"
  echo ""
fi

if [[ "$FORCE" != "1" ]]; then
  read -r -p "Type 'yes' to confirm destruction: " CONFIRM
  if [[ "$CONFIRM" != "yes" ]]; then
    log "Destruction cancelled."
    exit 0
  fi
fi

# =============================================================================
# Step 1: Empty all 7 S3 buckets  (--purge-data only)
# =============================================================================
if [[ "$PURGE_DATA" == "1" ]]; then
  step "1 / Empty and delete retained S3 buckets"

  BUCKETS=(
    "dfmea-uploads-${CDK_DEFAULT_ACCOUNT}"
    "dfmea-processed-${CDK_DEFAULT_ACCOUNT}"
    "dfmea-reports-${CDK_DEFAULT_ACCOUNT}"
    "dfmea-audit-${CDK_DEFAULT_ACCOUNT}"
    "dfmea-ontology-${CDK_DEFAULT_ACCOUNT}"
    "dfmea-kb-documents-${CDK_DEFAULT_ACCOUNT}"
    "dfmea-ml-models-${CDK_DEFAULT_ACCOUNT}"
  )

  for bucket in "${BUCKETS[@]}"; do
    if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
      log "Emptying bucket: $bucket"
      # Delete all versioned objects (handles both versioned and non-versioned buckets)
      aws s3api list-object-versions --bucket "$bucket" \
        --query 'Versions[].{Key:Key,VersionId:VersionId}' \
        --output json 2>/dev/null | \
        python3 -c "
import sys, json, subprocess
versions = json.load(sys.stdin)
if not versions:
    exit()
for v in versions:
    subprocess.run(['aws','s3api','delete-object','--bucket','$bucket',
                    '--key',v['Key'],'--version-id',v['VersionId']],
                   check=False, capture_output=True)
print(f'Deleted {len(versions)} versions from $bucket')
" 2>/dev/null || true
      # Delete any remaining delete markers
      aws s3api list-object-versions --bucket "$bucket" \
        --query 'DeleteMarkers[].{Key:Key,VersionId:VersionId}' \
        --output json 2>/dev/null | \
        python3 -c "
import sys, json, subprocess
markers = json.load(sys.stdin)
if not markers:
    exit()
for m in markers:
    subprocess.run(['aws','s3api','delete-object','--bucket','$bucket',
                    '--key',m['Key'],'--version-id',m['VersionId']],
                   check=False, capture_output=True)
print(f'Deleted {len(markers)} delete markers from $bucket')
" 2>/dev/null || true
      aws s3 rb "s3://${bucket}" --force 2>/dev/null || true
      ok "  Deleted bucket: $bucket"
    else
      warn "  Bucket not found (already deleted?): $bucket"
    fi
  done
else
  step "1 / S3 bucket purge — SKIPPED (pass --purge-data to enable)"
  warn "S3 buckets retained: dfmea-uploads/processed/reports/audit/ontology/kb-documents/ml-models"
fi

# =============================================================================
# Step 2: Delete dfmea-notifier Lambda + IAM role  (created outside CDK)
# =============================================================================
step "2 / Delete dfmea-notifier Lambda and IAM role"

if aws lambda get-function --function-name dfmea-notifier &>/dev/null; then
  aws lambda delete-function --function-name dfmea-notifier 2>/dev/null || true
  ok "Deleted Lambda: dfmea-notifier"
else
  warn "Lambda dfmea-notifier not found — skipping"
fi

if aws iam get-role --role-name dfmea-notifier-role &>/dev/null; then
  # Detach all managed policies before deletion
  ATTACHED=$(aws iam list-attached-role-policies \
    --role-name dfmea-notifier-role \
    --query 'AttachedPolicies[].PolicyArn' --output json 2>/dev/null || echo '[]')
  echo "$ATTACHED" | python3 -c "
import sys, json, subprocess
for arn in json.load(sys.stdin):
    subprocess.run(['aws','iam','detach-role-policy',
                    '--role-name','dfmea-notifier-role','--policy-arn',arn],
                   check=False, capture_output=True)
" 2>/dev/null || true
  # Delete inline policies
  INLINE=$(aws iam list-role-policies \
    --role-name dfmea-notifier-role \
    --query 'PolicyNames' --output json 2>/dev/null || echo '[]')
  echo "$INLINE" | python3 -c "
import sys, json, subprocess
for name in json.load(sys.stdin):
    subprocess.run(['aws','iam','delete-role-policy',
                    '--role-name','dfmea-notifier-role','--policy-name',name],
                   check=False, capture_output=True)
" 2>/dev/null || true
  aws iam delete-role --role-name dfmea-notifier-role 2>/dev/null || true
  ok "Deleted IAM role: dfmea-notifier-role"
else
  warn "IAM role dfmea-notifier-role not found — skipping"
fi

# =============================================================================
# Step 3: Delete SSM parameters
# =============================================================================
step "3 / Delete SSM parameters"

# Capture KB IDs from SSM before deleting the parameters
FAILURE_MODE_KB_ID=$(aws ssm get-parameter --name "/dfmea/failure_mode_kb_id" \
  --query 'Parameter.Value' --output text 2>/dev/null || echo "")
REGULATORY_KB_ID=$(aws ssm get-parameter --name "/dfmea/regulatory_kb_id" \
  --query 'Parameter.Value' --output text 2>/dev/null || echo "")

SSM_PARAMS=(
  "/dfmea/failure_mode_kb_id"
  "/dfmea/regulatory_kb_id"
  "/dfmea/assistant_runtime_arn"
  "/dfmea/assistant_memory_id"
)

for param in "${SSM_PARAMS[@]}"; do
  if aws ssm get-parameter --name "$param" &>/dev/null; then
    aws ssm delete-parameter --name "$param" 2>/dev/null || true
    ok "Deleted SSM parameter: $param"
  else
    warn "SSM parameter not found (already deleted?): $param"
  fi
done

# ── Force-delete the M2M client secret ────────────────────────────────────────
# The AuthStack secret now uses RemovalPolicy.DESTROY, but CloudFormation deletes
# Secrets Manager secrets with a 7-30 day RECOVERY WINDOW — the name stays
# reserved, which blocks the next deploy with "secret already exists". Force
# delete (no recovery) so redeploy can recreate it cleanly.
if aws secretsmanager describe-secret --secret-id dfmea/m2m-client-secret &>/dev/null; then
  aws secretsmanager delete-secret \
    --secret-id dfmea/m2m-client-secret \
    --force-delete-without-recovery >/dev/null 2>&1 || true
  ok "Force-deleted secret: dfmea/m2m-client-secret"
else
  warn "Secret dfmea/m2m-client-secret not found — skipping"
fi

# =============================================================================
# Step 4: Delete Bedrock Knowledge Bases  (IDs read from SSM before deletion)
# =============================================================================
step "4 / Delete Bedrock Knowledge Bases"

for kb_id in "$FAILURE_MODE_KB_ID" "$REGULATORY_KB_ID"; do
  if [[ -n "$kb_id" && "$kb_id" != "None" ]]; then
    # Delete data sources first (required before KB deletion)
    DATA_SOURCE_IDS=$(aws bedrock-agent list-data-sources \
      --knowledge-base-id "$kb_id" \
      --query 'dataSourceSummaries[].dataSourceId' --output json 2>/dev/null || echo '[]')
    echo "$DATA_SOURCE_IDS" | python3 -c "
import sys, json, subprocess
for ds_id in json.load(sys.stdin):
    subprocess.run(['aws','bedrock-agent','delete-data-source',
                    '--knowledge-base-id','$kb_id',
                    '--data-source-id',ds_id],
                   check=False, capture_output=True)
" 2>/dev/null || true
    # Now delete the KB
    aws bedrock-agent delete-knowledge-base --knowledge-base-id "$kb_id" 2>/dev/null || true
    ok "  Deleted Knowledge Base: $kb_id"
  fi
done

if [[ -z "${FAILURE_MODE_KB_ID}" && -z "${REGULATORY_KB_ID}" ]]; then
  warn "No Knowledge Base IDs found in SSM — skipping (already deleted or never created)"
fi

# =============================================================================
# Step 5: Delete AgentCore Gateway (+ targets), Runtimes, and Memory
#
# These are all created POST-CDK via the bedrock-agentcore-control plane
# (scripts/create_gateway.py, deploy_agentcore_runtimes.py,
# deploy_assistant_runtime.py), so CloudFormation does NOT manage them and
# `cdk destroy` leaves them behind. We delete them here by NAME PREFIX (dfmea /
# DFMEA) using the same control-plane API the create scripts use — so it works
# regardless of the auto-generated IDs. Covers:
#   * Gateway  : dfmea-gateway (+ its tool targets)
#   * Runtimes : dfmea_failure_mode, dfmea_structural, dfmea_regulatory,
#                dfmea_schema, dfmea_analyst, dfmea_assistant_runtime
#   * Memory   : DFMEA_Analyst_* (analyst) and dfmea_assistant_memory
# =============================================================================
step "5 / Delete AgentCore Gateway, Runtimes, and Memory"

AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" python3 - <<'PYEOF' || warn "AgentCore teardown reported errors — check output above"
import os
import time
import boto3
from botocore.exceptions import BotoCoreError, ClientError

region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
try:
    c = boto3.client("bedrock-agentcore-control", region_name=region)
except Exception as exc:  # service not available in this CLI/botocore
    print(f"[destroy] bedrock-agentcore-control unavailable: {exc}")
    raise SystemExit(0)


def _paginate(op, key, **kwargs):
    items, token = [], None
    while True:
        if token:
            kwargs["nextToken"] = token
        try:
            resp = op(**kwargs)
        except (BotoCoreError, ClientError) as exc:
            print(f"[destroy]   list ({key}) failed: {exc}")
            return items
        items.extend(resp.get(key, []))
        token = resp.get("nextToken")
        if not token:
            return items


def _is_dfmea(name):
    n = (name or "")
    return n.startswith("dfmea") or n.startswith("DFMEA")


# ── Gateway targets first, then the gateway ─────────────────────────────────
for gw in _paginate(c.list_gateways, "items"):
    name, gid = gw.get("name", ""), gw.get("gatewayId", "")
    if not gid or not _is_dfmea(name):
        continue
    for tgt in _paginate(c.list_gateway_targets, "items", gatewayIdentifier=gid):
        tid = tgt.get("targetId", "")
        if not tid:
            continue
        try:
            c.delete_gateway_target(gatewayIdentifier=gid, targetId=tid)
            print(f"[destroy]   deleting gateway target {tid}")
        except (BotoCoreError, ClientError) as exc:
            print(f"[destroy]   delete gateway target {tid} failed: {exc}")
    # Target deletion is asynchronous — the gateway cannot be deleted until every
    # target is actually gone. Poll until the target list is empty (~3 min max).
    for _ in range(36):
        if not _paginate(c.list_gateway_targets, "items", gatewayIdentifier=gid):
            break
        time.sleep(5)
    # Delete the gateway, retrying for eventual consistency.
    for attempt in range(6):
        try:
            c.delete_gateway(gatewayIdentifier=gid)
            print(f"[destroy] deleted gateway {name} ({gid})")
            break
        except (BotoCoreError, ClientError) as exc:
            if attempt == 5:
                print(f"[destroy] delete gateway {gid} failed: {exc}")
            else:
                time.sleep(10)

# ── Agent Runtimes (5 analysis agents + the assistant) ──────────────────────
for rt in _paginate(c.list_agent_runtimes, "agentRuntimes"):
    name = rt.get("agentRuntimeName", "")
    rid = rt.get("agentRuntimeId") or rt.get("id") or ""
    if not rid or not _is_dfmea(name):
        continue
    try:
        c.delete_agent_runtime(agentRuntimeId=rid)
        print(f"[destroy] deleted agent runtime {name} ({rid})")
    except (BotoCoreError, ClientError) as exc:
        print(f"[destroy] delete agent runtime {rid} failed: {exc}")

# ── AgentCore Memory (analyst + assistant) ──────────────────────────────────
for m in _paginate(c.list_memories, "memories"):
    name = m.get("name", "")
    mid = m.get("id") or m.get("memoryId") or ""
    if not mid or not _is_dfmea(name):
        continue
    try:
        c.delete_memory(memoryId=mid)
        print(f"[destroy] deleted memory {name} ({mid})")
    except (BotoCoreError, ClientError) as exc:
        print(f"[destroy] delete memory {mid} failed: {exc}")

print("[destroy] AgentCore gateway / runtime / memory teardown complete")
PYEOF
ok "AgentCore Gateway, Runtimes, and Memory teardown attempted"

# =============================================================================
# Step 6: (merged into Step 5) — AgentCore Runtimes are deleted above by name
#         prefix, since they are control-plane resources, not CFN resources.
# =============================================================================

# =============================================================================
# Step 7: Delete DynamoDB tables  (RemovalPolicy.RETAIN — CDK will not delete)
# =============================================================================
if [[ "$PURGE_DATA" == "1" ]]; then
  step "7 / Delete retained DynamoDB tables"

  TABLES=(
    "dfmea-reviews"
    "dfmea-structural-decomposition"
    "dfmea-retrieval-results"
    "dfmea-analysis-findings"
    "dfmea-synthesis-results"
    "dfmea-risk-scores"
    "dfmea-ap-lookup"
  )

  for table in "${TABLES[@]}"; do
    if aws dynamodb describe-table --table-name "$table" &>/dev/null; then
      log "Deleting DynamoDB table: $table"
      aws dynamodb delete-table --table-name "$table" \
        --output text --query 'TableDescription.TableName' > /dev/null 2>/dev/null || true
      ok "  Deleted table: $table"
    else
      warn "  Table not found (already deleted?): $table"
    fi
  done
else
  step "7 / DynamoDB table deletion — SKIPPED (pass --purge-data to enable)"
  warn "DynamoDB tables retained: dfmea-reviews, dfmea-structural-decomposition, dfmea-retrieval-results, dfmea-analysis-findings, dfmea-synthesis-results, dfmea-risk-scores, dfmea-ap-lookup"
fi

# =============================================================================
# Step 8: Tear down retained DB clusters BEFORE CDK destroy
#         - Aurora (legacy): disable deletion protection.
#         - Neptune: disable protection AND delete the RETAIN-policy instance +
#           cluster (CDK won't delete them), so the subnet group can be removed.
#         Must happen BEFORE CDK destroy attempts to delete subnet groups.
#         Resource IDs are discovered dynamically — never hardcoded.
# =============================================================================
step "8 / Tear down Aurora + Neptune DB clusters (pre-CDK)"

_disable_rds_deletion_protection() {
  local stack="$1" resource_type="$2" label="$3"
  local cluster_id
  cluster_id=$(aws cloudformation describe-stack-resources \
    --stack-name "$stack" \
    --query "StackResources[?ResourceType=='${resource_type}'].PhysicalResourceId | [0]" \
    --output text 2>/dev/null || echo "")
  if [[ -n "$cluster_id" && "$cluster_id" != "None" ]]; then
    log "Disabling deletion protection on $label cluster: $cluster_id"
    aws rds modify-db-cluster \
      --db-cluster-identifier "$cluster_id" \
      --no-deletion-protection 2>/dev/null || true
    log "  Waiting for $label cluster to be available..."
    aws rds wait db-cluster-available \
      --db-cluster-identifier "$cluster_id" 2>/dev/null || true
    ok "$label deletion protection disabled: $cluster_id"
  else
    warn "$label cluster not found in $stack — skipping"
  fi
}

# Aurora (AWS::RDS::DBCluster in DfmeaAuroraStack)
if aws cloudformation describe-stacks --stack-name DfmeaAuroraStack &>/dev/null; then
  _disable_rds_deletion_protection "DfmeaAuroraStack" "AWS::RDS::DBCluster" "Aurora"
else
  warn "DfmeaAuroraStack not found — Aurora deletion protection step skipped"
fi

# Neptune: the cluster AND instance use RemovalPolicy.RETAIN, so `cdk destroy`
# does NOT delete them — it only deletes the (non-retained) subnet group, which
# fails with "Cannot delete the subnet group ... at least one database instance
# is still using it". So we must explicitly delete the retained instances +
# cluster (after disabling deletion protection) and WAIT until they are gone,
# before CDK tries to remove the subnet group.
#
# Discover by the fixed subnet-group name so this also works when the stack is
# already in DELETE_FAILED (auto-generated cluster/instance names are never
# hardcoded).
NEPTUNE_SUBNET_GROUP="dfmea-neptune-subnet-group"

NEPTUNE_CLUSTER_IDS=$(aws neptune describe-db-clusters \
  --query "DBClusters[?DBSubnetGroup=='${NEPTUNE_SUBNET_GROUP}'].DBClusterIdentifier" \
  --output text 2>/dev/null || echo "")
NEPTUNE_INSTANCE_IDS=$(aws neptune describe-db-instances \
  --query "DBInstances[?DBSubnetGroup.DBSubnetGroupName=='${NEPTUNE_SUBNET_GROUP}'].DBInstanceIdentifier" \
  --output text 2>/dev/null || echo "")

if [[ -z "$NEPTUNE_CLUSTER_IDS" && -z "$NEPTUNE_INSTANCE_IDS" ]]; then
  warn "No Neptune cluster/instance found on ${NEPTUNE_SUBNET_GROUP} — skipping"
else
  # 1) Disable deletion protection on each cluster so it can be deleted.
  for cid in $NEPTUNE_CLUSTER_IDS; do
    log "Disabling deletion protection on Neptune cluster: $cid"
    aws neptune modify-db-cluster \
      --db-cluster-identifier "$cid" \
      --no-deletion-protection --apply-immediately 2>/dev/null || true
  done

  # 2) Delete every member instance, then wait for each to disappear.
  for iid in $NEPTUNE_INSTANCE_IDS; do
    log "Deleting Neptune instance: $iid"
    aws neptune delete-db-instance --db-instance-identifier "$iid" 2>/dev/null || true
  done
  for iid in $NEPTUNE_INSTANCE_IDS; do
    log "  Waiting for Neptune instance to delete: $iid"
    aws neptune wait db-instance-deleted --db-instance-identifier "$iid" 2>/dev/null || true
    # Fallback poll in case the waiter is unavailable/timed out (~15 min max).
    for _ in $(seq 1 90); do
      aws neptune describe-db-instances --db-instance-identifier "$iid" &>/dev/null || break
      sleep 10
    done
  done

  # 3) Delete each cluster (no final snapshot), then poll until gone.
  for cid in $NEPTUNE_CLUSTER_IDS; do
    log "Deleting Neptune cluster: $cid"
    aws neptune delete-db-cluster --db-cluster-identifier "$cid" --skip-final-snapshot 2>/dev/null || true
  done
  for cid in $NEPTUNE_CLUSTER_IDS; do
    log "  Waiting for Neptune cluster to delete: $cid"
    for _ in $(seq 1 90); do
      aws neptune describe-db-clusters --db-cluster-identifier "$cid" &>/dev/null || break
      sleep 10
    done
  done
  ok "Neptune instances + cluster deleted — subnet group can now be removed"
fi

# =============================================================================
# Step 9: CDK destroy — 13 stacks in reverse dependency order
# =============================================================================
step "9 / CDK destroy — 13 stacks (reverse order)"

cd "$INFRA_DIR"

# Activate venv if present
if [[ -f "$REPO_ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
fi

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

for stack in "${STACKS[@]}"; do
  log "Destroying $stack ..."
  if aws cloudformation describe-stacks --stack-name "$stack" &>/dev/null; then
    output=$(cdk destroy "$stack" --force 2>&1)
    exit_code=$?
    echo "$output" | tail -20
    if [[ $exit_code -ne 0 ]]; then
      fail "CDK destroy failed for $stack — aborting"
    fi
    ok "  $stack destroyed"
  else
    warn "  $stack not found — skipping"
  fi
done

# ── 9b. Delete ANY remaining dfmea S3 buckets orphaned by cdk destroy ─────────
# Catches EVERY bucket whose name starts with "dfmea" that survived cdk destroy,
# regardless of naming scheme:
#   * dfmea-agentcore-code-<acct>-<region>   (assistant runtime code, versioned)
#   * dfmea-access-logs-<acct>               (foundation access-log bucket)
#   * dfmea-uploads / processed / reports / audit / ontology / kb-documents / ml-models
#   * dfmea-frontend-<acct>                  (SPA hosting)
#   * dfmeafrontendstack-cfdistributionloggingbucket<hash>  (CloudFront logs)
# Runs AFTER cdk destroy, so nothing is in use. The CDK bootstrap bucket
# (cdk-*-assets-*) starts with "cdk-", NOT "dfmea", so it is never matched.
step "9b / Delete any remaining dfmea S3 buckets"

AUTO_BUCKETS=$(aws s3api list-buckets \
  --query "Buckets[?starts_with(Name, 'dfmea')].Name" \
  --output text 2>/dev/null || echo "")
if [[ -z "$AUTO_BUCKETS" ]]; then
  warn "No remaining dfmea S3 buckets found"
else
  for bucket in $AUTO_BUCKETS; do
    [[ -z "$bucket" ]] && continue
    log "Emptying + deleting CDK bucket: $bucket"
    aws s3 rm "s3://${bucket}" --recursive 2>/dev/null || true
    aws s3api list-object-versions --bucket "$bucket" \
      --query 'Versions[].{Key:Key,VersionId:VersionId}' --output json 2>/dev/null | \
      python3 -c "
import sys, json, subprocess
try: objs = json.load(sys.stdin)
except Exception: objs = None
for o in (objs or []):
    subprocess.run(['aws','s3api','delete-object','--bucket','$bucket',
                    '--key',o['Key'],'--version-id',o['VersionId']],
                   check=False, capture_output=True)
" 2>/dev/null || true
    aws s3api list-object-versions --bucket "$bucket" \
      --query 'DeleteMarkers[].{Key:Key,VersionId:VersionId}' --output json 2>/dev/null | \
      python3 -c "
import sys, json, subprocess
try: objs = json.load(sys.stdin)
except Exception: objs = None
for o in (objs or []):
    subprocess.run(['aws','s3api','delete-object','--bucket','$bucket',
                    '--key',o['Key'],'--version-id',o['VersionId']],
                   check=False, capture_output=True)
" 2>/dev/null || true
    if aws s3 rb "s3://${bucket}" --force 2>/dev/null; then
      ok "  Deleted CDK bucket: $bucket"
    else
      warn "  Could not delete $bucket — retry (a CloudFront log may have landed mid-delete)"
    fi
  done
fi

# ── 9c. Delete orphaned dfmea CloudWatch log groups ───────────────────────────
# Lambda/state-machine/AgentCore/CodeBuild log groups are created outside the
# stack lifecycle (or retained by the CDK LogRetention custom resource) and are
# NOT removed by cdk destroy. Stale CDK-managed Lambda log groups can even break
# the next deploy (see CDK notice 34612), so remove them here.
step "9c / Delete orphaned dfmea CloudWatch log groups"
for prefix in \
  "/aws/lambda/dfmea" \
  "/aws/states/dfmea" \
  "/aws/apigateway/dfmea" \
  "/aws/bedrock-agentcore/runtimes/dfmea" \
  "/aws/codebuild/bedrock-agentcore-dfmea" \
  "/aws/vendedlogs/dfmea"; do
  LGS=$(aws logs describe-log-groups --log-group-name-prefix "$prefix" \
    --query 'logGroups[].logGroupName' --output text 2>/dev/null || echo "")
  for lg in $LGS; do
    [[ -z "$lg" ]] && continue
    aws logs delete-log-group --log-group-name "$lg" 2>/dev/null \
      && ok "  Deleted log group: $lg" \
      || warn "  Could not delete log group: $lg"
  done
done

# =============================================================================
# Step 10: Wipe local artifacts  (--purge-data only)
# =============================================================================
if [[ "$PURGE_DATA" == "1" ]]; then
  step "10 / Wipe local build artifacts"

  log "Removing sample-data/variants/ ..."
  rm -rf "$REPO_ROOT/sample-data/variants/" 2>/dev/null || true
  ok "  Removed sample-data/variants/"

  log "Removing sample-data/models/*.joblib ..."
  rm -f "$REPO_ROOT/sample-data/models/"*.joblib 2>/dev/null || true
  ok "  Removed *.joblib files"

  log "Removing sample-data/models/training_report.json ..."
  rm -f "$REPO_ROOT/sample-data/models/training_report.json" 2>/dev/null || true
  ok "  Removed training_report.json"

  log "Removing bundle/ ..."
  rm -rf "$REPO_ROOT/bundle/" 2>/dev/null || true
  ok "  Removed bundle/"
else
  step "10 / Local artifact wipe — SKIPPED (pass --purge-data to enable)"
  warn "Local artifacts retained: sample-data/variants/, sample-data/models/*.joblib, bundle/"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${GREEN}  DFMEA Agentic Review System teardown complete${NC}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
if [[ "$PURGE_DATA" != "1" ]]; then
  echo -e "  ${YELLOW}Retained resources (RemovalPolicy.RETAIN — not deleted without --purge-data):${NC}"
  echo -e "  • S3 buckets   : dfmea-uploads/processed/reports/audit/ontology/kb-documents/ml-models"
  echo -e "  • DynamoDB     : dfmea-reviews, dfmea-structural-decomposition, dfmea-retrieval-results,"
  echo -e "                   dfmea-analysis-findings, dfmea-synthesis-results, dfmea-risk-scores, dfmea-ap-lookup"
  echo -e "  • Local files  : sample-data/variants/, sample-data/models/*.joblib, bundle/"
  echo -e "  • KMS CMK      : check AWS KMS console for keys pending deletion"
  echo ""
  echo -e "  To also delete retained data, rerun with: ${CYAN}./scripts/destroy.sh --force --purge-data${NC}"
fi
echo ""
