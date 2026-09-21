"""
agents/shared/gateway_client.py — AgentCore Gateway MCP client + M2M JWT auth.

Runtime agents call the DFMEA tools through the Amazon Bedrock AgentCore
Gateway (MCP protocol over streamable HTTP). Every Runtime-to-Gateway call uses
a Cognito client_credentials token with scope ``dfmea-gateway/tools.invoke``.
The client id/secret are loaded from Secrets Manager and the token is injected
as an ``Authorization: Bearer <jwt>`` header. Frontend user tokens are never
forwarded to the M2M-only Gateway authorizer.

Environment variables (baked in at deploy time or provided by the runtime):
  GATEWAY_URL          MCP endpoint, e.g. https://<gw-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
  COGNITO_TOKEN_URL    Cognito OAuth2 token endpoint, e.g. https://<domain>.auth.<region>.amazoncognito.com/oauth2/token
  GATEWAY_SCOPE        OAuth scope (default: dfmea-gateway/tools.invoke)
  M2M_SECRET_ID        Secrets Manager id holding {"client_id","client_secret"} (default: dfmea/m2m-client-secret)
  AWS_REGION           region
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request

import boto3

REGION            = os.getenv("AWS_REGION", "us-east-1")
GATEWAY_URL       = os.getenv("GATEWAY_URL", "")
COGNITO_TOKEN_URL = os.getenv("COGNITO_TOKEN_URL", "")
GATEWAY_SCOPE     = os.getenv("GATEWAY_SCOPE", "dfmea-gateway/tools.invoke")
M2M_SECRET_ID     = os.getenv("M2M_SECRET_ID", "dfmea/m2m-client-secret")

# Simple in-process token cache: {"token": str, "expires_at": epoch_seconds}
_token_cache: dict = {}
_secrets_client = None


def _get_secrets():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager", region_name=REGION)
    return _secrets_client


def _load_m2m_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret) from Secrets Manager, or env fallback."""
    cid = os.getenv("M2M_CLIENT_ID", "")
    csecret = os.getenv("M2M_CLIENT_SECRET", "")
    if cid and csecret:
        return cid, csecret
    try:
        resp = _get_secrets().get_secret_value(SecretId=M2M_SECRET_ID)
        data = json.loads(resp["SecretString"])
        return data.get("client_id", ""), data.get("client_secret", "")
    except Exception as exc:  # pragma: no cover - depends on live AWS
        print(f"[gateway_client] could not load M2M credentials: {exc}")
        return "", ""


def get_m2m_token() -> str:
    """Fetch and cache the Cognito M2M token required by the Gateway."""
    now = time.time()
    cached = _token_cache.get("token")
    if cached and _token_cache.get("expires_at", 0) > now + 60:
        return cached

    if not COGNITO_TOKEN_URL:
        raise RuntimeError("COGNITO_TOKEN_URL not set — cannot mint M2M token")

    client_id, client_secret = _load_m2m_credentials()
    if not client_id or not client_secret:
        raise RuntimeError("M2M client credentials unavailable")

    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": GATEWAY_SCOPE,
    }).encode()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(
        COGNITO_TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
        token = payload.get("access_token", "")
        if not token:
            raise RuntimeError("Cognito response did not contain an access_token")
        ttl = int(payload.get("expires_in", 3600))
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + ttl
        return token
    except Exception as exc:  # pragma: no cover - depends on live AWS
        raise RuntimeError(f"M2M token request failed: {exc}") from exc


def resolve_gateway_token() -> str:
    """Mint the Cognito M2M token used by every Runtime-to-Gateway call."""
    return get_m2m_token()


def gateway_mcp_client(token: str):
    """
    Build a Strands MCPClient bound to the AgentCore Gateway over streamable
    HTTP, authenticated with the required Cognito M2M bearer token.
    """
    if not GATEWAY_URL:
        raise RuntimeError("GATEWAY_URL not configured")
    if not token:
        raise RuntimeError("Gateway M2M token is empty")

    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient

    headers = {"Authorization": f"Bearer {token}"}
    return MCPClient(lambda: streamablehttp_client(GATEWAY_URL, headers=headers))
