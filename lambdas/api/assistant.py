"""Authenticated conversation and AgentCore Runtime adapter for the DFMEA assistant."""
from __future__ import annotations

import datetime
import json
import os
import re
import uuid
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

ASSISTANT_TABLE = os.environ.get("ASSISTANT_TABLE_NAME", "dfmea-assistant")
ASSISTANT_RUNTIME_ARN = os.environ.get("ASSISTANT_RUNTIME_ARN", "")
ASSISTANT_RUNTIME_QUALIFIER = os.environ.get("ASSISTANT_RUNTIME_QUALIFIER", "DEFAULT")
ASSISTANT_RETENTION_DAYS = max(1, int(os.environ.get("ASSISTANT_RETENTION_DAYS", "90")))

_ALLOWED_ROUTE_IDS = {
    "DASHBOARD",
    "REVIEWS",
    "REVIEW_DETAIL",
    "REVIEW_FINDINGS",
    "UPLOAD",
    "ONTOLOGY",
}
_REVIEW_ROUTES = {"REVIEW_DETAIL", "REVIEW_FINDINGS"}
_ALLOWED_SOURCE_TYPES = {
    "APPLICATION_HELP",
    "REGULATORY",
    "FAILURE_MODE",
    "LIVE_DATA",
}
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Api-Key",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
}

_dynamodb = boto3.resource("dynamodb")


def _json_default(value: Any):
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)} is not JSON serializable")


def _response(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "X-Content-Type-Options": "nosniff",
            **_CORS,
        },
        "body": json.dumps(body, default=_json_default),
    }


def _error(message: str, status: int) -> dict:
    return _response({"error": message}, status)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _expires_at() -> int:
    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        days=ASSISTANT_RETENTION_DAYS
    )
    return int(expires.timestamp())


def _user_sub(event: dict) -> str:
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    claims = authorizer.get("claims") or {}
    subject = str(claims.get("sub") or "").strip()
    return subject if _SAFE_IDENTIFIER.fullmatch(subject) else ""


def _body(event: dict) -> dict | None:
    try:
        parsed = json.loads(event.get("body") or "{}")
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


def _route_context(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    route_id = str(value.get("route_id") or "")
    if route_id not in _ALLOWED_ROUTE_IDS:
        return None

    context = {"route_id": route_id}
    review_id = str(value.get("review_id") or "")
    if route_id in _REVIEW_ROUTES:
        if not _SAFE_IDENTIFIER.fullmatch(review_id):
            return None
        context["review_id"] = review_id
    return context


def _public_conversation(item: dict) -> dict:
    result = {
        "conversation_id": item["conversation_id"],
        "title": item["title"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }
    if item.get("route_context"):
        result["route_context"] = item["route_context"]
    return result


def _public_message(item: dict) -> dict:
    result = {
        "message_id": item["message_id"],
        "conversation_id": item["conversation_id"],
        "role": item["role"],
        "content": item["content"],
        "created_at": item["created_at"],
    }
    for field in ("citations", "suggested_prompts", "navigation_actions"):
        if item.get(field):
            result[field] = item[field]
    return result


def _conversation_key(conversation_id: str) -> str:
    return f"CONVERSATION#{conversation_id}"


def _message_key(conversation_id: str, created_at: str, message_id: str) -> str:
    return f"MESSAGE#{conversation_id}#{created_at}#{message_id}"


def _conversation(table, user_sub: str, conversation_id: str) -> dict | None:
    if not _SAFE_IDENTIFIER.fullmatch(conversation_id):
        return None
    response = table.get_item(
        Key={
            "user_sub": user_sub,
            "entity_key": _conversation_key(conversation_id),
        }
    )
    return response.get("Item")


def _query_all(table, **kwargs) -> list[dict]:
    items: list[dict] = []
    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


def _safe_citations(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    citations = []
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "")
        label = str(item.get("label") or "").strip()[:200]
        if source_type not in _ALLOWED_SOURCE_TYPES or not label:
            continue
        citation = {"source_type": source_type, "label": label}
        for field in ("source_id", "retrieved_at"):
            field_value = str(item.get(field) or "").strip()[:200]
            if field_value:
                citation[field] = field_value
        citations.append(citation)
    return citations


def _safe_navigation_actions(value: Any, route_context: dict) -> list[dict]:
    if not isinstance(value, list):
        return []
    actions = []
    for item in value[:4]:
        if not isinstance(item, dict) or item.get("type") != "OPEN_ROUTE":
            continue
        route_id = str(item.get("route_id") or "")
        label = str(item.get("label") or "").strip()[:100]
        if route_id not in _ALLOWED_ROUTE_IDS or not label:
            continue

        parameters = item.get("parameters") or {}
        action = {"type": "OPEN_ROUTE", "route_id": route_id, "label": label}
        if route_id in _REVIEW_ROUTES:
            review_id = str(parameters.get("review_id") or "")
            if (
                not _SAFE_IDENTIFIER.fullmatch(review_id)
                or review_id != route_context.get("review_id")
            ):
                continue
            action["parameters"] = {"review_id": review_id}
        actions.append(action)
    return actions


def _validated_runtime_response(value: Any, route_context: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Runtime response must be a JSON object")
    answer = str(value.get("answer") or "").strip()
    if not answer:
        raise ValueError("Runtime response is missing answer")

    suggestions = [
        str(prompt).strip()[:200]
        for prompt in value.get("suggested_prompts", [])[:4]
        if isinstance(prompt, str) and prompt.strip()
    ] if isinstance(value.get("suggested_prompts", []), list) else []

    return {
        "answer": answer[:20000],
        "citations": _safe_citations(value.get("citations")),
        "suggested_prompts": suggestions,
        "navigation_actions": _safe_navigation_actions(
            value.get("navigation_actions"), route_context
        ),
    }


def _invoke_runtime(
    prompt: str,
    user_sub: str,
    conversation_id: str,
    route_context: dict,
) -> dict:
    client = boto3.client(
        "bedrock-agentcore",
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )
    payload = json.dumps(
        {
            "prompt": prompt,
            "actor_id": user_sub,
            "conversation_id": conversation_id,
            "route_context": route_context,
            "response_contract": {
                "answer": "string",
                "citations": "array",
                "suggested_prompts": "array",
                "navigation_actions": "array",
            },
        }
    )
    response = client.invoke_agent_runtime(
        agentRuntimeArn=ASSISTANT_RUNTIME_ARN,
        runtimeSessionId=conversation_id,
        payload=payload,
        qualifier=ASSISTANT_RUNTIME_QUALIFIER,
    )
    response_body = response["response"].read()
    return _validated_runtime_response(json.loads(response_body), route_context)


def list_conversations(user_sub: str) -> dict:
    table = _dynamodb.Table(ASSISTANT_TABLE)
    items = _query_all(
        table,
        KeyConditionExpression=(
            Key("user_sub").eq(user_sub)
            & Key("entity_key").begins_with("CONVERSATION#")
        ),
    )
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return _response(
        {"conversations": [_public_conversation(item) for item in items[:100]]}
    )


def create_conversation(user_sub: str, event: dict) -> dict:
    body = _body(event)
    if body is None:
        return _error("Request body must be a JSON object", 400)
    context = _route_context(body.get("route_context"))
    if context is None:
        return _error("A valid route_context is required", 400)

    title = " ".join(str(body.get("title") or "New conversation").split())[:80]
    conversation_id = str(uuid.uuid4())
    created_at = _now()
    item = {
        "user_sub": user_sub,
        "entity_key": _conversation_key(conversation_id),
        "entity_type": "CONVERSATION",
        "conversation_id": conversation_id,
        "title": title or "New conversation",
        "route_context": context,
        "created_at": created_at,
        "updated_at": created_at,
        "expires_at": _expires_at(),
    }
    _dynamodb.Table(ASSISTANT_TABLE).put_item(Item=item)
    return _response({"conversation": _public_conversation(item)}, 201)


def list_messages(user_sub: str, conversation_id: str) -> dict:
    table = _dynamodb.Table(ASSISTANT_TABLE)
    if not _conversation(table, user_sub, conversation_id):
        return _error("Conversation not found", 404)
    items = _query_all(
        table,
        KeyConditionExpression=(
            Key("user_sub").eq(user_sub)
            & Key("entity_key").begins_with(f"MESSAGE#{conversation_id}#")
        ),
    )
    return _response({"messages": [_public_message(item) for item in items]})


def send_message(user_sub: str, conversation_id: str, event: dict) -> dict:
    if not ASSISTANT_RUNTIME_ARN:
        return _error(
            "The DFMEA assistant runtime is not configured yet. No answer was generated.",
            503,
        )

    body = _body(event)
    if body is None:
        return _error("Request body must be a JSON object", 400)
    prompt = str(body.get("prompt") or "").strip()
    if not prompt or len(prompt) > 4000:
        return _error("prompt must contain between 1 and 4000 characters", 400)
    context = _route_context(body.get("route_context"))
    if context is None:
        return _error("A valid route_context is required", 400)

    table = _dynamodb.Table(ASSISTANT_TABLE)
    conversation = _conversation(table, user_sub, conversation_id)
    if not conversation:
        return _error("Conversation not found", 404)

    try:
        runtime_response = _invoke_runtime(
            prompt, user_sub, conversation_id, context
        )
    except (BotoCoreError, ClientError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        error_code = "RuntimeError"
        if isinstance(exc, ClientError):
            error_code = exc.response.get("Error", {}).get("Code", error_code)
        print(
            f"[assistant] runtime invocation failed conversation={conversation_id} "
            f"error={error_code}"
        )
        return _error(
            "The DFMEA assistant could not retrieve a verified answer. Please try again.",
            502,
        )

    user_created_at = _now()
    assistant_created_at = _now()
    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    expires_at = _expires_at()

    user_message = {
        "user_sub": user_sub,
        "entity_key": _message_key(
            conversation_id, user_created_at, user_message_id
        ),
        "entity_type": "MESSAGE",
        "message_id": user_message_id,
        "conversation_id": conversation_id,
        "role": "user",
        "content": prompt,
        "created_at": user_created_at,
        "expires_at": expires_at,
    }
    assistant_message = {
        "user_sub": user_sub,
        "entity_key": _message_key(
            conversation_id, assistant_created_at, assistant_message_id
        ),
        "entity_type": "MESSAGE",
        "message_id": assistant_message_id,
        "conversation_id": conversation_id,
        "role": "assistant",
        "content": runtime_response["answer"],
        "citations": runtime_response["citations"],
        "suggested_prompts": runtime_response["suggested_prompts"],
        "navigation_actions": runtime_response["navigation_actions"],
        "created_at": assistant_created_at,
        "expires_at": expires_at,
    }

    with table.batch_writer() as batch:
        batch.put_item(Item=user_message)
        batch.put_item(Item=assistant_message)

    table.update_item(
        Key={
            "user_sub": user_sub,
            "entity_key": _conversation_key(conversation_id),
        },
        UpdateExpression=(
            "SET updated_at = :updated, route_context = :context, "
            "expires_at = :expires"
        ),
        ExpressionAttributeValues={
            ":updated": assistant_created_at,
            ":context": context,
            ":expires": expires_at,
        },
    )
    conversation.update(
        {
            "updated_at": assistant_created_at,
            "route_context": context,
            "expires_at": expires_at,
        }
    )
    return _response(
        {
            "user_message": _public_message(user_message),
            "assistant_message": _public_message(assistant_message),
            "conversation": _public_conversation(conversation),
        },
        201,
    )


def delete_conversation(user_sub: str, conversation_id: str) -> dict:
    table = _dynamodb.Table(ASSISTANT_TABLE)
    if not _conversation(table, user_sub, conversation_id):
        return _error("Conversation not found", 404)

    message_items = _query_all(
        table,
        KeyConditionExpression=(
            Key("user_sub").eq(user_sub)
            & Key("entity_key").begins_with(f"MESSAGE#{conversation_id}#")
        ),
        ProjectionExpression="user_sub, entity_key",
    )
    with table.batch_writer() as batch:
        for item in message_items:
            batch.delete_item(
                Key={"user_sub": item["user_sub"], "entity_key": item["entity_key"]}
            )
        batch.delete_item(
            Key={
                "user_sub": user_sub,
                "entity_key": _conversation_key(conversation_id),
            }
        )
    return _response({"conversation_id": conversation_id, "deleted": True})


def route_assistant_request(event: dict) -> dict:
    """Route only Cognito-authenticated assistant requests."""
    user_sub = _user_sub(event)
    if not user_sub:
        return _error("Authenticated user identity is required", 401)

    method = event.get("httpMethod", "")
    path = event.get("path", "")
    path_parameters = event.get("pathParameters") or {}
    conversation_id = str(path_parameters.get("conversation_id") or "")

    if path == "/assistant/conversations":
        if method == "GET":
            return list_conversations(user_sub)
        if method == "POST":
            return create_conversation(user_sub, event)

    if conversation_id and path.endswith("/messages"):
        if method == "GET":
            return list_messages(user_sub, conversation_id)
        if method == "POST":
            return send_message(user_sub, conversation_id, event)

    if conversation_id and method == "DELETE":
        return delete_conversation(user_sub, conversation_id)

    return _error("Not found", 404)
