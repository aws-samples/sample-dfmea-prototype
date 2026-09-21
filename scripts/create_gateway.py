#!/usr/bin/env python3
"""
scripts/create_gateway.py — Create the DFMEA AgentCore Gateway + tool target.

Modeled on the working AgentCore Gateway reference
(sample-rfq-agent-strands-multi-agent/.../monitoring_agent/gateway_creation/create_gateway.py):

  * create an IAM role trusted by bedrock-agentcore.amazonaws.com
  * bedrock-agentcore-control.create_gateway(protocolType=MCP, authorizerType=CUSTOM_JWT,
    authorizerConfiguration={customJWTAuthorizer:{allowedClients, discoveryUrl}})
  * wait for ACTIVE/READY
  * create_gateway_target with a single Lambda target exposing all 10 DFMEA tools
    (mcp.lambda.toolSchema.inlinePayload) using credentialProviderType GATEWAY_IAM_ROLE

Inbound auth = Cognito M2M (client_credentials). The runtime agents mint an M2M
JWT and call the gateway MCP endpoint; the gateway validates it against the
Cognito discovery URL + allowedClients.

Idempotent: reuses an existing gateway/target of the same name.

Usage (called by deploy.sh step 11):
  python scripts/create_gateway.py \
      --region us-east-1 \
      --gateway-name dfmea-gateway \
      --mcp-tools-arn arn:aws:lambda:...:function:dfmea-mcp-tools \
      --m2m-user-pool-id us-east-1_XXXX \
      --m2m-client-id XXXX

Writes agentcore_gateway_deployment.json {gateway_id, gateway_url} and prints
GATEWAY_URL=<url> on the last line for easy shell capture.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time

import boto3

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# The 10 DFMEA MCP tools (names + inputSchema) exposed via the gateway target.
TOOLS = [
    {"name": "search_failure_mode_kb",
     "description": "Search the failure-mode knowledge base for relevant failure patterns",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Search query"},
         "max_results": {"type": "integer", "description": "Max results (default 5)"}},
         "required": ["query"]}},
    {"name": "search_regulatory_kb",
     "description": "Search the regulatory knowledge base for applicable standards",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Search query"},
         "standard_keywords": {"type": "string", "description": "Optional standard keywords"},
         "max_results": {"type": "integer", "description": "Max results (default 5)"}},
         "required": ["query"]}},
    {"name": "get_review_context",
     "description": "Retrieve the current DFMEA review context, rows, and components",
     "inputSchema": {"type": "object", "properties": {
         "review_id": {"type": "string", "description": "DFMEA review id"}},
         "required": ["review_id"]}},
    {"name": "get_cad_anchors",
     "description": "Retrieve CAD component labels for a review (or components derived from rows)",
     "inputSchema": {"type": "object", "properties": {
         "review_id": {"type": "string", "description": "DFMEA review id"}},
         "required": ["review_id"]}},
    {"name": "write_finding",
     "description": "Persist a DFMEA finding. Action Priority is recomputed from S/O/D server-side.",
     "inputSchema": {"type": "object", "properties": {
         "review_id": {"type": "string"},
         "agent": {"type": "string"},
         "finding_type": {"type": "string"},
         "description": {"type": "string"},
         "affected_component": {"type": "string"},
         "suggested_failure_mode": {"type": "string"},
         "severity": {"type": "integer"},
         "occurrence": {"type": "integer"},
         "detection": {"type": "integer"},
         "confidence": {"type": "number"},
         "standard_citations": {"type": "array", "items": {"type": "string"}},
         "gap_source": {"type": "string"}},
         "required": ["review_id", "agent", "finding_type", "description"]}},
    {"name": "validate_shacl",
     "description": "Validate DFMEA rows against the SHACL ontology shapes",
     "inputSchema": {"type": "object", "properties": {
         "rows": {"type": "array", "items": {"type": "object"}, "description": "DFMEA rows"}},
         "required": ["rows"]}},
    {"name": "get_component_failure_modes",
     "description": "Retrieve known failure modes for a component from the ontology",
     "inputSchema": {"type": "object", "properties": {
         "component_name": {"type": "string"}}, "required": ["component_name"]}},
    {"name": "get_component_standards",
     "description": "Retrieve applicable regulatory standards for a component from the ontology",
     "inputSchema": {"type": "object", "properties": {
         "component_name": {"type": "string"}}, "required": ["component_name"]}},
    {"name": "get_ontology_subclasses",
     "description": "Retrieve ontology subclass hierarchy for a given class name",
     "inputSchema": {"type": "object", "properties": {
         "class_name": {"type": "string"}}, "required": ["class_name"]}},
    {"name": "get_ml_scores",
     "description": "Get ML pre-screen risk scores for the DFMEA rows of a review",
     "inputSchema": {"type": "object", "properties": {
         "review_id": {"type": "string"}}, "required": ["review_id"]}},
]

GATEWAY_TARGET_NAME = "dfmea-tools"


def _gateway_role(role_name: str, mcp_tools_arn: str, region: str, account: str) -> str:
    iam = boto3.client("iam")
    assume = {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AssumeRolePolicy",
            "Effect": "Allow",
            "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account},
                "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:*"},
            },
        }],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:*",
                "agent-credential-provider:*",
                "iam:PassRole",
                "secretsmanager:GetSecretValue",
                "lambda:InvokeFunction",
            ],
            "Resource": "*",
        }],
    }
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        print(f"[gateway] using existing role {role_name}")
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume),
            Description="AgentCore Gateway service role for DFMEA",
        )["Role"]
        print(f"[gateway] created role {role_name}; waiting for propagation...")
        time.sleep(10)
    iam.put_role_policy(
        RoleName=role_name, PolicyName="DfmeaGatewayAccess",
        PolicyDocument=json.dumps(policy),
    )
    return role["Arn"]


def _find_gateway(client, name: str) -> str:
    try:
        resp = client.list_gateways(maxResults=100)
        for gw in resp.get("items", []):
            if gw.get("name") == name:
                return gw.get("gatewayId", "")
    except Exception as exc:
        print(f"[gateway] list_gateways failed: {exc}")
    return ""


def _wait_active(client, gateway_id: str, max_wait: int = 300, interval: int = 10) -> str:
    elapsed = 0
    while elapsed < max_wait:
        status = client.get_gateway(gatewayIdentifier=gateway_id)["status"]
        print(f"[gateway] status: {status}")
        if status in ("ACTIVE", "READY"):
            return status
        if status in ("FAILED", "DELETING", "DELETED"):
            raise RuntimeError(f"gateway status {status}")
        time.sleep(interval)
        elapsed += interval
    raise TimeoutError("gateway did not reach ACTIVE/READY in time")


def _target_exists(client, gateway_id: str, target_name: str) -> bool:
    try:
        resp = client.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100)
        return any(t.get("name") == target_name for t in resp.get("items", []))
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    p.add_argument("--gateway-name", default="dfmea-gateway")
    p.add_argument("--mcp-tools-arn", required=True)
    p.add_argument("--m2m-user-pool-id", required=True)
    p.add_argument("--m2m-client-id", required=True)
    p.add_argument("--role-name", default="agentcore-dfmea-gateway-role")
    args = p.parse_args()

    account = boto3.client("sts").get_caller_identity()["Account"]
    role_arn = _gateway_role(args.role_name, args.mcp_tools_arn, args.region, account)

    client = boto3.client("bedrock-agentcore-control", region_name=args.region)
    discovery_url = (
        f"https://cognito-idp.{args.region}.amazonaws.com/"
        f"{args.m2m_user_pool_id}/.well-known/openid-configuration"
    )
    auth_config = {"customJWTAuthorizer": {
        "allowedClients": [args.m2m_client_id],
        "discoveryUrl": discovery_url,
    }}

    gateway_id = _find_gateway(client, args.gateway_name)
    if gateway_id:
        print(f"[gateway] exists: {gateway_id}")
    else:
        resp = client.create_gateway(
            name=args.gateway_name,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration=auth_config,
            description="MCP Gateway routing 10 DFMEA tools to dfmea-mcp-tools Lambda",
            exceptionLevel="DEBUG",
        )
        gateway_id = resp["gatewayId"]
        print(f"[gateway] created: {gateway_id}")

    _wait_active(client, gateway_id)
    gateway_url = client.get_gateway(gatewayIdentifier=gateway_id)["gatewayUrl"]

    if _target_exists(client, gateway_id, GATEWAY_TARGET_NAME):
        print(f"[gateway] target '{GATEWAY_TARGET_NAME}' already exists — skipping")
    else:
        target_config = {"mcp": {"lambda": {
            "lambdaArn": args.mcp_tools_arn,
            "toolSchema": {"inlinePayload": TOOLS},
        }}}
        client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=GATEWAY_TARGET_NAME,
            description="DFMEA tools backed by dfmea-mcp-tools Lambda",
            targetConfiguration=target_config,
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        print(f"[gateway] registered target '{GATEWAY_TARGET_NAME}' with {len(TOOLS)} tools")

    out = {"gateway_id": gateway_id, "gateway_url": gateway_url, "role_arn": role_arn}
    with open(os.path.join(REPO_ROOT, "agentcore_gateway_deployment.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    # Last line: easy shell capture
    print(f"GATEWAY_URL={gateway_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
