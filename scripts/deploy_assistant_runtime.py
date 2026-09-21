#!/usr/bin/env python3
"""Package, deploy, and smoke-test the DFMEA AgentCore assistant runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# Account-agnostic: values below are resolved at deploy time from STS / SSM so
# the same script works in any target account (no SOURCE-account hardcoding).
EXPECTED_ACCOUNT = ""          # optional guard; empty = accept the STS-resolved account
DEFAULT_REGION = "us-east-1"
DEFAULT_PROFILE = ""           # empty = use the ambient/default credential chain
MEMORY_NAME = "dfmea_assistant_memory"
RUNTIME_NAME = "dfmea_assistant_runtime"
CODE_BUCKET = ""               # derived: dfmea-agentcore-code-<account>-<region>
RUNTIME_ROLE_ARN = ""          # derived: arn:aws:iam::<account>:role/dfmea-assistant-runtime-role
FAILURE_MODE_KB_ID = ""        # resolved from SSM /dfmea/failure_mode_kb_id if unset
REGULATORY_KB_ID = ""          # resolved from SSM /dfmea/regulatory_kb_id if unset
MODEL_ID = "us.anthropic.claude-sonnet-4-6"
READY_MEMORY_STATES = {"ACTIVE"}
READY_RUNTIME_STATES = {"READY"}
FAILED_STATES = {
    "CREATE_FAILED",
    "UPDATE_FAILED",
    "FAILED",
    "DELETING",
    "DELETE_FAILED",
}
CONFIG = Config(
    retries={"max_attempts": 5, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=300,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--expected-account", default=EXPECTED_ACCOUNT)
    parser.add_argument("--package-only", action="store_true")
    parser.add_argument("--skip-smoke-test", action="store_true")
    parser.add_argument("--failure-mode-kb-id", default="",
                        help="Failure-mode KB id (default: SSM /dfmea/failure_mode_kb_id)")
    parser.add_argument("--regulatory-kb-id", default="",
                        help="Regulatory KB id (default: SSM /dfmea/regulatory_kb_id)")
    parser.add_argument("--model-id", default="",
                        help="Bedrock model id for the assistant runtime "
                             "(default: %s). Pass an already-enabled model to avoid "
                             "a manual Bedrock model-access step." % MODEL_ID)
    return parser.parse_args()


def _ssm_get(ssm: Any, name: str) -> str:
    """Best-effort read of an SSM String parameter; returns '' if absent."""
    try:
        return str(ssm.get_parameter(Name=name)["Parameter"]["Value"])
    except (BotoCoreError, ClientError):
        return ""


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_checked(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def package_runtime(root: Path) -> tuple[Path, str, int, int]:
    source_dir = root / "agents" / "assistant_runtime"
    requirements = source_dir / "requirements.txt"
    main_file = source_dir / "main.py"
    build_dir = source_dir / ".build"
    dist_dir = source_dir / "dist"
    zip_path = dist_dir / "dfmea-assistant-runtime.zip"
    uv = shutil.which("uv") or "/opt/homebrew/bin/uv"
    if not Path(uv).exists():
        raise RuntimeError("uv is required to package the AgentCore Runtime")
    if not requirements.is_file() or not main_file.is_file():
        raise RuntimeError("Assistant runtime source files are missing")

    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True)
    dist_dir.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            uv,
            "pip",
            "install",
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            "3.13",
            "--target",
            str(build_dir),
            "--only-binary=:all:",
            "-r",
            str(requirements),
        ],
        root,
    )
    shutil.copy2(main_file, build_dir / "main.py")

    for path in build_dir.rglob("*"):
        if path.name == "__pycache__" or path.suffix == ".pyc":
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
    for path in build_dir.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)

    if zip_path.exists():
        zip_path.unlink()
    files = sorted(path for path in build_dir.rglob("*") if path.is_file())
    directories = sorted(path for path in build_dir.rglob("*") if path.is_dir())
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for directory in directories:
            relative = directory.relative_to(build_dir).as_posix().rstrip("/") + "/"
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFDIR | 0o755) << 16
            archive.writestr(info, b"")
        for path in files:
            relative = path.relative_to(build_dir).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    zipped_size = zip_path.stat().st_size
    unzipped_size = sum(path.stat().st_size for path in files)
    if zipped_size > 250 * 1024 * 1024:
        raise RuntimeError("Runtime package exceeds the 250 MB compressed limit")
    if unzipped_size > 750 * 1024 * 1024:
        raise RuntimeError("Runtime package exceeds the 750 MB uncompressed limit")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    shutil.rmtree(build_dir)
    return zip_path, digest, zipped_size, unzipped_size


def paginate_items(client: Any, operation: str, result_key: str) -> Iterable[dict[str, Any]]:
    paginator = client.get_paginator(operation)
    for page in paginator.paginate(PaginationConfig={"PageSize": 100}):
        yield from page.get(result_key, [])


def resource_body(response: dict[str, Any], key: str) -> dict[str, Any]:
    value = response.get(key)
    return value if isinstance(value, dict) else response


def resource_id(resource: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = resource.get(key)
        if value:
            return str(value)
    raise RuntimeError(f"AWS response did not include any of: {', '.join(keys)}")


def wait_for(
    loader: Any,
    ready_states: set[str],
    description: str,
    timeout_seconds: int = 1200,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        resource = loader()
        status = str(resource.get("status") or "UNKNOWN")
        print(f"{description}: {status}", file=sys.stderr)
        if status in ready_states:
            return resource
        if status in FAILED_STATES or status.endswith("_FAILED"):
            reason = resource.get("failureReason") or resource.get("failureReasons") or "not provided"
            raise RuntimeError(f"{description} entered {status}: {reason}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for {description}; last status was {status}")
        time.sleep(10)


def desired_memory_strategies() -> list[dict[str, Any]]:
    return [
        {
            "summaryMemoryStrategy": {
                "name": "DfmeaSessionSummary",
                "description": "Summarizes each authenticated DFMEA assistant conversation",
                "namespaces": ["/summaries/{actorId}/{sessionId}/"],
            }
        },
        {
            "userPreferenceMemoryStrategy": {
                "name": "DfmeaUserPreferences",
                "description": "Retains stable DFMEA assistant interaction preferences",
                "namespaces": ["/users/{actorId}/preferences/"],
            }
        },
    ]


def ensure_memory(control: Any) -> dict[str, Any]:
    memory_details = []
    for summary in paginate_items(control, "list_memories", "memories"):
        listed_id = resource_id(summary, "id", "memoryId")
        memory_details.append(resource_body(control.get_memory(memoryId=listed_id), "memory"))
    matches = [item for item in memory_details if item.get("name") == MEMORY_NAME]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple AgentCore Memory resources are named {MEMORY_NAME}")
    if matches:
        memory_id = resource_id(matches[0], "id", "memoryId")
    else:
        response = control.create_memory(
            clientToken=str(uuid.uuid4()),
            name=MEMORY_NAME,
            description="Short-term conversation and long-term preference memory for the global DFMEA assistant",
            eventExpiryDuration=90,
            memoryStrategies=desired_memory_strategies(),
            tags={"application": "dfmea", "component": "assistant", "managed-by": "deploy-script"},
        )
        memory_id = resource_id(resource_body(response, "memory"), "id", "memoryId")

    def load() -> dict[str, Any]:
        return resource_body(control.get_memory(memoryId=memory_id), "memory")

    memory = wait_for(load, READY_MEMORY_STATES, f"AgentCore Memory {memory_id}")
    if int(memory.get("eventExpiryDuration") or 0) != 90:
        raise RuntimeError("Existing AgentCore Memory does not use the required 90-day event expiry")
    strategy_names = {
        str(strategy.get("name"))
        for strategy in memory.get("strategies", [])
        if isinstance(strategy, dict)
    }
    expected_names = {"DfmeaSessionSummary", "DfmeaUserPreferences"}
    if strategy_names != expected_names:
        raise RuntimeError(f"Existing AgentCore Memory strategy drift: {sorted(strategy_names)}")
    memory["id"] = memory_id
    return memory


def upload_package(s3: Any, zip_path: Path, digest: str, account_id: str) -> str:
    key = f"dfmea-assistant-runtime/{digest}.zip"
    s3.head_bucket(Bucket=CODE_BUCKET, ExpectedBucketOwner=account_id)
    s3.upload_file(
        str(zip_path),
        CODE_BUCKET,
        key,
        ExtraArgs={
            "ContentType": "application/zip",
            "ExpectedBucketOwner": account_id,
        },
    )
    metadata = s3.head_object(Bucket=CODE_BUCKET, Key=key, ExpectedBucketOwner=account_id)
    if int(metadata["ContentLength"]) != zip_path.stat().st_size:
        raise RuntimeError("Uploaded runtime package size does not match the local package")
    return key


def runtime_configuration(memory_id: str, key: str) -> dict[str, Any]:
    return {
        "agentRuntimeArtifact": {
            "codeConfiguration": {
                "code": {"s3": {"bucket": CODE_BUCKET, "prefix": key}},
                "runtime": "PYTHON_3_13",
                "entryPoint": ["main.py"],
            }
        },
        "roleArn": RUNTIME_ROLE_ARN,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 300, "maxLifetime": 1800},
        "environmentVariables": {
            "AWS_REGION": DEFAULT_REGION,
            "BEDROCK_MODEL_ID": MODEL_ID,
            "REVIEWS_TABLE_NAME": "dfmea-reviews",
            "FINDINGS_TABLE_NAME": "dfmea-analysis-findings",
            "FAILURE_MODE_KB_ID": FAILURE_MODE_KB_ID,
            "REGULATORY_KB_ID": REGULATORY_KB_ID,
            "AGENTCORE_MEMORY_ID": memory_id,
        },
    }


def ensure_runtime(control: Any, memory_id: str, key: str) -> dict[str, Any]:
    matches = [
        item
        for item in paginate_items(control, "list_agent_runtimes", "agentRuntimes")
        if item.get("agentRuntimeName") == RUNTIME_NAME
    ]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple AgentCore Runtimes are named {RUNTIME_NAME}")
    configuration = runtime_configuration(memory_id, key)
    if matches:
        runtime_id = resource_id(matches[0], "agentRuntimeId", "id")
        current = resource_body(control.get_agent_runtime(agentRuntimeId=runtime_id), "agentRuntime")
        current_s3 = (((current.get("agentRuntimeArtifact") or {}).get("codeConfiguration") or {}).get("code") or {}).get("s3") or {}
        expected_s3 = configuration["agentRuntimeArtifact"]["codeConfiguration"]["code"]["s3"]
        if current_s3 != expected_s3 or current.get("environmentVariables") != configuration["environmentVariables"]:
            control.update_agent_runtime(
                agentRuntimeId=runtime_id,
                clientToken=str(uuid.uuid4()),
                **configuration,
            )
    else:
        response = control.create_agent_runtime(
            agentRuntimeName=RUNTIME_NAME,
            description="Production global DFMEA assistant with live application data, verified KB evidence, and AgentCore Memory",
            clientToken=str(uuid.uuid4()),
            tags={"application": "dfmea", "component": "assistant", "managed-by": "deploy-script"},
            **configuration,
        )
        runtime_id = resource_id(resource_body(response, "agentRuntime"), "agentRuntimeId", "id")

    def load() -> dict[str, Any]:
        return resource_body(control.get_agent_runtime(agentRuntimeId=runtime_id), "agentRuntime")

    runtime = wait_for(load, READY_RUNTIME_STATES, f"AgentCore Runtime {runtime_id}")
    runtime["agentRuntimeId"] = runtime_id
    return runtime


def put_identifiers(ssm: Any, memory_id: str, runtime_arn: str) -> None:
    ssm.put_parameter(
        Name="/dfmea/assistant_memory_id",
        Description="AgentCore Memory ID for the production DFMEA assistant",
        Value=memory_id,
        Type="String",
        Overwrite=True,
        Tier="Standard",
    )
    ssm.put_parameter(
        Name="/dfmea/assistant_runtime_arn",
        Description="AgentCore Runtime ARN for the production DFMEA assistant",
        Value=runtime_arn,
        Type="String",
        Overwrite=True,
        Tier="Standard",
    )


def invoke_and_verify(data: Any, runtime_arn: str, memory_id: str) -> dict[str, Any]:
    actor_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    payload = {
        "prompt": "What is the current DFMEA review portfolio status? Take me to the reviews page.",
        "actor_id": actor_id,
        "conversation_id": session_id,
        "route_context": {"route_id": "DASHBOARD"},
        "response_contract": {
            "answer": "string",
            "citations": "array",
            "suggested_prompts": "array",
            "navigation_actions": "array",
        },
    }
    stop_result: dict[str, Any] = {}
    try:
        response = data.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps(payload),
            qualifier="DEFAULT",
        )
        with response["response"] as body:
            result = json.loads(body.read())
        required = {"answer", "citations", "suggested_prompts", "navigation_actions"}
        if set(result) != required or not isinstance(result.get("answer"), str) or not result["answer"].strip():
            raise RuntimeError(f"Runtime returned an invalid response contract: {sorted(result)}")
        if not isinstance(result.get("citations"), list) or not result["citations"]:
            raise RuntimeError("Runtime smoke response did not include a verified citation")
        for action in result.get("navigation_actions", []):
            if action.get("route_id") not in {"DASHBOARD", "REVIEWS", "UPLOAD", "ONTOLOGY"}:
                raise RuntimeError(f"Runtime returned an unsafe dashboard navigation action: {action}")

        memory_event_count = 0
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            events = data.list_events(
                memoryId=memory_id,
                actorId=actor_id,
                sessionId=session_id,
                includePayloads=True,
                maxResults=20,
            ).get("events", [])
            if events:
                memory_event_count = len(events)
                break
            time.sleep(5)
        if memory_event_count == 0:
            raise RuntimeError("No AgentCore Memory event was found after the verified response")
        return {
            "actor_id": actor_id,
            "session_id": session_id,
            "answer": result["answer"],
            "citations": result["citations"],
            "navigation_actions": result["navigation_actions"],
            "memory_event_count": memory_event_count,
        }
    finally:
        try:
            stop_result = data.stop_runtime_session(
                agentRuntimeArn=runtime_arn,
                runtimeSessionId=session_id,
                qualifier="DEFAULT",
            )
        except data.exceptions.ResourceNotFoundException:
            stop_result = {"status": "already-stopped"}
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"ResourceNotFoundException", "ConflictException"}:
                raise
            stop_result = {"status": exc.response.get("Error", {}).get("Code")}
        print(f"Stopped test Runtime session {session_id}: {stop_result.get('status', 'requested')}", file=sys.stderr)


def deploy(args: argparse.Namespace) -> dict[str, Any]:
    root = project_root()
    zip_path, digest, zipped_size, unzipped_size = package_runtime(root)
    package_result = {
        "path": str(zip_path),
        "sha256": digest,
        "compressed_bytes": zipped_size,
        "uncompressed_bytes": unzipped_size,
    }
    if args.package_only:
        return {"package": package_result}

    session = boto3.Session(profile_name=(args.profile or None), region_name=args.region)
    sts = session.client("sts", config=CONFIG)
    identity = sts.get_caller_identity()
    account_id = str(identity["Account"])
    # Optional guard only when an expected account is explicitly provided.
    expected = args.expected_account or EXPECTED_ACCOUNT
    if expected and account_id != expected:
        raise RuntimeError(
            f"Refusing deployment: resolved account {account_id} != expected {expected}"
        )

    # Resolve account-specific identifiers at deploy time (no hardcoding).
    global CODE_BUCKET, RUNTIME_ROLE_ARN, FAILURE_MODE_KB_ID, REGULATORY_KB_ID, MODEL_ID
    CODE_BUCKET = f"dfmea-agentcore-code-{account_id}-{args.region}"
    RUNTIME_ROLE_ARN = f"arn:aws:iam::{account_id}:role/dfmea-assistant-runtime-role"
    if args.model_id:
        MODEL_ID = args.model_id

    ssm = session.client("ssm", config=CONFIG)
    FAILURE_MODE_KB_ID = (args.failure_mode_kb_id or FAILURE_MODE_KB_ID
                          or _ssm_get(ssm, "/dfmea/failure_mode_kb_id"))
    REGULATORY_KB_ID = (args.regulatory_kb_id or REGULATORY_KB_ID
                        or _ssm_get(ssm, "/dfmea/regulatory_kb_id"))
    print(
        f"Resolved: account={account_id} region={args.region} "
        f"code_bucket={CODE_BUCKET} fm_kb={FAILURE_MODE_KB_ID or '(none)'} "
        f"reg_kb={REGULATORY_KB_ID or '(none)'}",
        file=sys.stderr,
    )

    iam = session.client("iam", config=CONFIG)
    role = iam.get_role(RoleName=RUNTIME_ROLE_ARN.rsplit("/", 1)[-1])["Role"]
    if role["Arn"] != RUNTIME_ROLE_ARN:
        raise RuntimeError("Runtime role ARN does not match the required role")

    control = session.client("bedrock-agentcore-control", config=CONFIG)
    data = session.client("bedrock-agentcore", config=CONFIG)
    s3 = session.client("s3", config=CONFIG)

    memory = ensure_memory(control)
    memory_id = resource_id(memory, "id", "memoryId")
    key = upload_package(s3, zip_path, digest, account_id)
    runtime = ensure_runtime(control, memory_id, key)
    runtime_arn = resource_id(runtime, "agentRuntimeArn", "arn")
    put_identifiers(ssm, memory_id, runtime_arn)
    smoke = None if args.skip_smoke_test else invoke_and_verify(data, runtime_arn, memory_id)
    return {
        "identity": {"account": account_id, "arn": identity["Arn"], "region": args.region},
        "package": package_result,
        "s3_uri": f"s3://{CODE_BUCKET}/{key}",
        "memory": {"id": memory_id, "status": memory.get("status")},
        "runtime": {
            "id": resource_id(runtime, "agentRuntimeId", "id"),
            "arn": runtime_arn,
            "status": runtime.get("status"),
            "version": runtime.get("agentRuntimeVersion"),
        },
        "ssm_parameters": ["/dfmea/assistant_memory_id", "/dfmea/assistant_runtime_arn"],
        "smoke_test": smoke,
    }


def main() -> int:
    args = parse_args()
    if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        print("AWS_BEARER_TOKEN_BEDROCK must be unset for this deployment", file=sys.stderr)
        return 2
    try:
        print(json.dumps(deploy(args), indent=2, default=str))
        return 0
    except (BotoCoreError, ClientError, OSError, RuntimeError, TimeoutError, subprocess.CalledProcessError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
