#!/usr/bin/env python3
"""
scripts/deploy_agentcore_runtimes.py — Deploy the 5 DFMEA agents to Amazon
Bedrock AgentCore Runtime using the bedrock_agentcore_starter_toolkit.

Mirrors the RFQ multi-agent reference (RfqAgent_Strands_MultiAgent):
  * bedrock_agentcore_starter_toolkit.Runtime().configure(...).launch()
  * auto_create_ecr=True  -> the toolkit builds & pushes the container image
    (CodeBuild) — NO hand-written Dockerfile required.
  * Config is baked into the generated entrypoint (runtime_main.py) the same way
    the reference bakes config into its agent file, so it does not depend on a
    toolkit-specific environment-variable kwarg.

Inbound auth (invoker Lambda -> runtime) is IAM/SigV4 by default (robust for the
long-running, HITL-gated pipeline). The runtime -> AgentCore Gateway hop uses a
Cognito M2M client_credentials JWT minted at runtime (see agents/shared/gateway_client.py).
Pass --inbound-auth jwt to instead require a Cognito customJWTAuthorizer.

For each agent it builds an isolated context dir containing the whole `agents/`
package plus a thin root entrypoint (`runtime_main.py`) that bakes config and
imports the agent's `app`, so `agents.shared.*` imports resolve in the container.

It also (idempotently) creates one AgentCore Memory for the analyst.

Outputs a JSON map to agentcore_runtimes_deployment.json and stdout.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
import tempfile
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# agent_label -> (runtime agent module, AgentCore runtime name)
AGENTS = {
    # AgentCore runtime names allow ONLY letters/numbers/underscores (no hyphens),
    # must start with a letter, and be 1-48 chars.
    "failure_mode": ("agents.failure_mode.agent", "dfmea_failure_mode"),
    "structural":   ("agents.structural.agent",   "dfmea_structural"),
    "regulatory":   ("agents.regulatory.agent",   "dfmea_regulatory"),
    "other":        ("agents.other.agent",        "dfmea_schema"),
    "analyst":      ("agents.analyst.agent",      "dfmea_analyst"),
}

RUNTIME_MAIN_TEMPLATE = '''"""Auto-generated AgentCore Runtime entrypoint for {label}."""
import os

# Config baked at deploy time (RFQ-reference pattern).
{env_lines}

from {module} import app

if __name__ == "__main__":
    app.run()
'''


def _env_lines(env: dict) -> str:
    return "\n".join(
        f'os.environ.setdefault({k!r}, {v!r})' for k, v in env.items() if v
    )


def _build_context(label: str, module: str, env: dict) -> str:
    """Temp build dir: agents/ package + requirements.txt + runtime_main.py."""
    build_dir = tempfile.mkdtemp(prefix=f"dfmea-rt-{label}-")
    shutil.copytree(
        os.path.join(REPO_ROOT, "agents"),
        os.path.join(build_dir, "agents"),
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy(
        os.path.join(REPO_ROOT, "agents", "requirements-runtime.txt"),
        os.path.join(build_dir, "requirements.txt"),
    )
    with open(os.path.join(build_dir, "runtime_main.py"), "w", encoding="utf-8") as f:
        f.write(RUNTIME_MAIN_TEMPLATE.format(
            label=label, module=module, env_lines=_env_lines(env),
        ))
    return build_dir


def _create_memory(region: str) -> str:
    """Idempotently create an AgentCore Memory for the analyst. Returns id or ''."""
    try:
        from bedrock_agentcore.memory import MemoryClient
    except Exception as exc:
        print(f"[deploy-rt] MemoryClient unavailable ({exc}); analyst runs without memory")
        return ""
    try:
        client = MemoryClient(region_name=region)
        name = f"DFMEA_Analyst_{int(time.time())}"
        result = client.create_memory_and_wait(
            name=name,
            description="DFMEA analyst synthesis conversational memory",
            strategies=[],
            event_expiry_days=30,
            max_wait=300,
            poll_interval=10,
        )
        mem_id = result.get("id") or result.get("memoryId") or ""
        print(f"[deploy-rt] created AgentCore Memory: {mem_id}")
        return mem_id
    except Exception as exc:
        print(f"[deploy-rt] memory creation failed ({exc}); analyst runs without memory")
        return ""


def _deploy_one(label: str, module: str, runtime_name: str, args, env: dict) -> str:
    from bedrock_agentcore_starter_toolkit import Runtime

    build_dir = _build_context(label, module, env)
    print(f"\n[deploy-rt] === {label} ({runtime_name}) ===")
    cwd = os.getcwd()
    try:
        os.chdir(build_dir)
        runtime = Runtime()
        configure_kwargs = dict(
            entrypoint="runtime_main.py",
            execution_role=args.execution_role_arn,
            auto_create_ecr=True,
            requirements_file="requirements.txt",
            region=args.region,
            agent_name=runtime_name,
        )
        if args.inbound_auth == "jwt" and args.cognito_user_pool_id and args.cognito_client_id:
            discovery_url = (
                f"https://cognito-idp.{args.region}.amazonaws.com/"
                f"{args.cognito_user_pool_id}/.well-known/openid-configuration"
            )
            configure_kwargs["authorizer_configuration"] = {
                "customJWTAuthorizer": {
                    "discoveryUrl": discovery_url,
                    "allowedClients": [args.cognito_client_id],
                }
            }
        runtime.configure(**configure_kwargs)
        launch_result = runtime.launch(auto_update_on_conflict=True)
        arn = getattr(launch_result, "agent_arn", "") or ""
        if not arn:
            raise RuntimeError(f"{runtime_name} launch returned no runtime ARN")
        print(f"[deploy-rt] {label} launched: {arn}")
        return arn
    finally:
        os.chdir(cwd)
        shutil.rmtree(build_dir, ignore_errors=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    p.add_argument("--execution-role-arn", required=True)
    p.add_argument("--gateway-url", default=os.environ.get("GATEWAY_URL", ""))
    p.add_argument("--cognito-token-url", default=os.environ.get("COGNITO_TOKEN_URL", ""))
    p.add_argument("--gateway-scope", default="dfmea-gateway/tools.invoke")
    p.add_argument("--m2m-secret-id", default="dfmea/m2m-client-secret")
    p.add_argument("--model-id", default="us.anthropic.claude-haiku-4-5-20251001-v1:0")
    p.add_argument("--inbound-auth", choices=["iam", "jwt"], default="iam")
    p.add_argument("--cognito-user-pool-id", default="")
    p.add_argument("--cognito-client-id", default="")
    args = p.parse_args()

    if not args.gateway_url:
        print("[deploy-rt] --gateway-url is required", file=sys.stderr)
        return 2
    if not args.cognito_token_url:
        print("[deploy-rt] --cognito-token-url is required", file=sys.stderr)
        return 2

    memory_id = _create_memory(args.region)

    base_env = {
        "GATEWAY_URL": args.gateway_url,
        "COGNITO_TOKEN_URL": args.cognito_token_url,
        "GATEWAY_SCOPE": args.gateway_scope,
        "M2M_SECRET_ID": args.m2m_secret_id,
        "BEDROCK_MODEL_ID": args.model_id,
        "AWS_REGION": args.region,
    }

    arns: dict = {}
    for label, (module, runtime_name) in AGENTS.items():
        env = dict(base_env)
        if label == "analyst" and memory_id:
            env["DFMEA_MEMORY_ID"] = memory_id
        try:
            arns[label] = _deploy_one(label, module, runtime_name, args, env)
        except Exception as exc:
            print(f"[deploy-rt] {label} deployment FAILED: {exc}")
            arns[label] = ""

    out = {"region": args.region, "memory_id": memory_id, "runtimes": arns}
    with open(os.path.join(REPO_ROOT, "agentcore_runtimes_deployment.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("\n[deploy-rt] deployment summary:")
    print(json.dumps(out, indent=2))

    failed = [label for label, arn in arns.items() if not arn]
    if failed:
        print(
            f"[deploy-rt] deployment incomplete; missing runtime ARNs: {', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
