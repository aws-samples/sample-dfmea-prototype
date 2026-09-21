"""
lambdas/api/handler.py — REST API Lambda.

Single Lambda function backing API Gateway REST endpoints.
Routes:
  POST   /reviews                              — start a new review
  GET    /reviews/{id}                         — get review status
  GET    /reviews/{id}/findings                — list findings for a review
  GET    /reviews/{id}/report                  — get pre-signed URL to final report PDF
  POST   /reviews/{id}/hitl                    — approve / reject HITL gate
  GET    /reviews                              — list recent reviews (paginated)
  DELETE /reviews/{id}                         — cancel / archive a review
  GET    /health                               — health check (no auth)
  GET    /admin/metrics                        — AP-level counts + trends across all reviews
  GET    /admin/top-failure-modes              — top-7 failure modes by max RPN
  GET    /admin/findings-over-time             — AP counts bucketed by week (last 8 weeks)
  GET    /admin/severity-occurrence-matrix     — 2-D heatmap of Sev × Occ bands
  GET    /admin/hitl-decisions                 — last 23 HITL gate decisions
  GET    /admin/agent-activity                 — last 20 review-pipeline events
  GET    /ontology/health                      — Neptune ontology entity/relation counts
  GET    /ontology/graph                       — SPARQL-driven graph nodes + edges
"""
from __future__ import annotations
import datetime
import decimal
import json
import os
import re
import uuid
import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError

REVIEWS_TABLE    = os.environ.get("REVIEWS_TABLE_NAME", "dfmea-reviews")
FINDINGS_TABLE   = os.environ.get("FINDINGS_TABLE_NAME", "dfmea-analysis-findings")
REPORTS_BUCKET   = os.environ.get("REPORTS_BUCKET_NAME", "dfmea-reports")
UPLOADS_BUCKET   = os.environ.get("UPLOADS_BUCKET_NAME", "dfmea-uploads")
PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET_NAME", "dfmea-processed")
REVIEW_SM_ARN  = os.environ.get("REVIEW_SM_ARN", "")
AOSS_ENDPOINT  = os.environ.get("AOSS_ENDPOINT", "")
AOSS_INDEX     = os.environ.get("AOSS_INDEX", "regulatory-docs")
HITL_GATE_FN   = os.environ.get("HITL_GATE_FN_NAME", "dfmea-hitl-gate")
NEPTUNE_ENDPOINT = os.environ.get("NEPTUNE_ENDPOINT", "")
NEPTUNE_PORT     = int(os.environ.get("NEPTUNE_PORT", "8182"))
ONTOLOGY_PREFIX  = "https://dfmea.example.com/ontology/v0.1.0#"

dynamodb      = boto3.resource("dynamodb")
s3_client     = boto3.client("s3", config=Config(signature_version="s3v4"))
sfn_client    = boto3.client("stepfunctions")
lambda_client = boto3.client("lambda")


_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Api-Key",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
}


def _decimal_default(obj):
    if isinstance(obj, decimal.Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _ok(body: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "X-Content-Type-Options": "nosniff", **_CORS},
        "body": json.dumps(body, default=_decimal_default),
    }


def _err(message: str, status: int = 400) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **_CORS},
        "body": json.dumps({"error": message}),
    }


def _now() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _sparql_query(sparql: str) -> dict:
    """Execute SPARQL SELECT via POST against Neptune with SigV4 auth.
    POST is used (not GET) to avoid SigV4 query-string canonicalisation issues.
    """
    import urllib.request, urllib.parse, urllib.error
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    session = boto3.session.Session()
    creds   = session.get_credentials().get_frozen_credentials()
    body    = urllib.parse.urlencode({"query": sparql}).encode()
    url     = f"https://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/sparql"
    req     = AWSRequest(method="POST", url=url, data=body,
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "Accept":       "application/sparql-results+json"})
    SigV4Auth(creds, "neptune-db", session.region_name).add_auth(req)
    headers = {k: v for k, v in req.headers.items() if k.lower() != "host"}
    http_req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(http_req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_txt = exc.read().decode("utf-8", errors="replace")[:500]
        print(f"[api:_sparql_query] Neptune {exc.code} {exc.reason}: {body_txt}")
        raise


# ── Route handlers ─────────────────────────────────────────────────────────────

def post_reviews(event: dict) -> dict:
    """POST /reviews — initiate review. Body: {assembly_name, file_key}"""
    body = json.loads(event.get("body") or "{}")
    assembly_name = body.get("assembly_name", "")
    file_key = body.get("file_key", "")
    if not assembly_name or not file_key:
        return _err("assembly_name and file_key are required")

    review_id = str(uuid.uuid4())
    now = _now()
    item = {
        "review_id": review_id,
        "assembly_name": assembly_name,
        "file_key": file_key,
        "status": "SUBMITTED",
        "created_at": now,
        "updated_at": now,
    }
    dynamodb.Table(REVIEWS_TABLE).put_item(Item=item)

    # Start the review state machine. Do not persist the frontend user JWT in
    # execution history; Runtime-to-Gateway authentication is Cognito M2M.
    if REVIEW_SM_ARN:
        sfn_client.start_execution(
            stateMachineArn=REVIEW_SM_ARN,
            name=review_id,
            input=json.dumps({
                "review_id": review_id,
                "file_key": file_key,
            }),
        )

    return _ok({"review_id": review_id, "status": "SUBMITTED"}, status=201)


def get_review(review_id: str) -> dict:
    table = dynamodb.Table(REVIEWS_TABLE)
    resp = table.get_item(Key={"review_id": review_id})
    item = resp.get("Item")
    if not item:
        return _err(f"Review {review_id} not found", 404)
    # Don't expose internal HITL task token
    item.pop("hitl_task_token", None)
    return _ok(item)


def list_reviews(event: dict) -> dict:
    qs = event.get("queryStringParameters") or {}
    limit = min(int(qs.get("limit", "20")), 100)
    table = dynamodb.Table(REVIEWS_TABLE)
    resp = table.scan(Limit=limit)
    items = resp.get("Items", [])
    for item in items:
        item.pop("hitl_task_token", None)
    return _ok({"reviews": items, "count": len(items)})


def get_findings(review_id: str, event: dict) -> dict:
    qs = event.get("queryStringParameters") or {}
    min_ap = qs.get("min_ap", "")
    table = dynamodb.Table(FINDINGS_TABLE)
    resp = table.query(
        KeyConditionExpression=Key("review_id").eq(review_id),
        Limit=200,
    )
    items = resp.get("Items", [])
    if min_ap in ("H", "M"):
        ap_priority = {"H": 3, "M": 2, "L": 1}
        threshold = ap_priority[min_ap]
        items = [i for i in items if ap_priority.get(i.get("action_priority", "L"), 1) >= threshold]
    return _ok({"review_id": review_id, "findings": items, "count": len(items)})


def get_report(review_id: str) -> dict:
    report_key = f"reviews/{review_id}/final-report.pdf"
    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": REPORTS_BUCKET, "Key": report_key},
            ExpiresIn=3600,
        )
        return _ok({"review_id": review_id, "url": url, "expires_in": 3600})
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return _err(f"Report for review {review_id} not ready", 404)
        raise


def delete_review(review_id: str) -> dict:
    table = dynamodb.Table(REVIEWS_TABLE)
    resp = table.get_item(Key={"review_id": review_id})
    if not resp.get("Item"):
        return _err(f"Review {review_id} not found", 404)
    table.update_item(
        Key={"review_id": review_id},
        UpdateExpression="SET #s = :s, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "ARCHIVED", ":t": _now()},
    )
    return _ok({"review_id": review_id, "status": "ARCHIVED"})


def get_upload_url(event: dict) -> dict:
    """GET /upload-url?filename=foo.json — returns presigned S3 PUT URL."""
    qs = event.get("queryStringParameters") or {}
    filename = qs.get("filename", "upload.bin")
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    key = f"uploads/{uuid.uuid4()}.{ext}"
    url = s3_client.generate_presigned_url(
        "put_object",
        Params={"Bucket": UPLOADS_BUCKET, "Key": key},
        ExpiresIn=300,
    )
    return _ok({"url": url, "key": key})


def get_health() -> dict:
    return _ok({"status": "healthy", "timestamp": _now()})


def _scan_all(table, **kwargs) -> list[dict]:
    """Read every DynamoDB scan page for dashboard-wide aggregates."""
    items: list[dict] = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


def _parse_iso_datetime(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _finding_rpn(item: dict) -> int | None:
    severity = _number(item.get("severity"))
    occurrence = _number(item.get("occurrence"))
    detection = _number(item.get("detection"))
    if severity is None or occurrence is None or detection is None:
        return None
    return int(severity * occurrence * detection)


def _start_of_week(value: datetime.datetime) -> datetime.datetime:
    utc_value = value.astimezone(datetime.timezone.utc)
    return (utc_value - datetime.timedelta(days=utc_value.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def get_admin_metrics() -> dict:
    findings = _scan_all(
        dynamodb.Table(FINDINGS_TABLE),
        ProjectionExpression="review_id, action_priority, confidence, severity, occurrence, detection",
    )
    reviews = _scan_all(
        dynamodb.Table(REVIEWS_TABLE),
        ProjectionExpression=(
            "review_id, assembly_name, created_at, row_count, material, #s, "
            "gate_1_status, gate_2_status, gate_3_status, gate_4_status, hitl_decision"
        ),
        ExpressionAttributeNames={"#s": "status"},
    )

    counts = {"H": 0, "M": 0, "L": 0}
    confidence_values: list[float] = []
    findings_by_review: dict[str, list[dict]] = {}
    for finding in findings:
        review_id = str(finding.get("review_id", ""))
        findings_by_review.setdefault(review_id, []).append(finding)
        action_priority = finding.get("action_priority")
        if action_priority in counts:
            counts[action_priority] += 1
        confidence = _number(finding.get("confidence"))
        if confidence is not None:
            confidence_values.append(confidence)

    now = datetime.datetime.now(datetime.timezone.utc)
    current_week_start = _start_of_week(now)
    previous_week_start = current_week_start - datetime.timedelta(days=7)
    current_week_counts = {"H": 0, "M": 0, "L": 0}
    previous_week_counts = {"H": 0, "M": 0, "L": 0}
    review_dates = {
        str(review.get("review_id", "")): _parse_iso_datetime(review.get("created_at"))
        for review in reviews
    }
    for finding in findings:
        action_priority = finding.get("action_priority")
        review_date = review_dates.get(str(finding.get("review_id", "")))
        if action_priority not in counts or review_date is None:
            continue
        review_week = _start_of_week(review_date)
        if review_week == current_week_start:
            current_week_counts[action_priority] += 1
        elif review_week == previous_week_start:
            previous_week_counts[action_priority] += 1

    cutoff_30 = now - datetime.timedelta(days=30)
    reviews_30d = sum(
        1 for review in reviews
        if (created := _parse_iso_datetime(review.get("created_at"))) and created >= cutoff_30
    )
    active_reviews = sum(
        1 for review in reviews
        if review.get("status") not in ("COMPLETE", "ARCHIVED", "FAILED")
    )
    complete_reviews = sum(1 for review in reviews if review.get("status") == "COMPLETE")

    review_summaries = []
    for review in reviews:
        review_id = str(review.get("review_id", ""))
        related_findings = findings_by_review.get(review_id, [])
        rpns = [rpn for finding in related_findings if (rpn := _finding_rpn(finding)) is not None]
        review_summaries.append({
            "review_id": review_id,
            "assembly_name": review.get("assembly_name", ""),
            "status": review.get("status", ""),
            "created_at": review.get("created_at"),
            "row_count": review.get("row_count"),
            "material": review.get("material"),
            "finding_count": len(related_findings),
            "max_rpn": max(rpns) if rpns else None,
            "gates": {
                "1": review.get("gate_1_status"),
                "2": review.get("gate_2_status"),
                "3": review.get("gate_3_status"),
                "4": review.get("gate_4_status") or review.get("hitl_decision"),
            },
        })
    review_summaries.sort(key=lambda review: review.get("created_at") or "", reverse=True)

    confidence_total = len(confidence_values)
    confidence_distribution = {
        "high": sum(value >= 0.85 for value in confidence_values),
        "medium": sum(0.60 <= value < 0.85 for value in confidence_values),
        "low": sum(value < 0.60 for value in confidence_values),
        "total": confidence_total,
        "average": (
            round(sum(confidence_values) / confidence_total, 4)
            if confidence_total else None
        ),
    }

    return _ok({
        "ap_counts": counts,
        "ap_trends": {
            action_priority: current_week_counts[action_priority] - previous_week_counts[action_priority]
            for action_priority in ("H", "M", "L")
        },
        "trend_basis": "review_created_at",
        "current_week_counts": current_week_counts,
        "previous_week_counts": previous_week_counts,
        "total": sum(counts.values()),
        "reviews_30d": reviews_30d,
        "active_reviews": active_reviews,
        "complete_reviews": complete_reviews,
        "agents_online": None,
        "confidence_distribution": confidence_distribution,
        "reviews": review_summaries,
    })


def get_admin_top_failure_modes() -> dict:
    items = _scan_all(
        dynamodb.Table(FINDINGS_TABLE),
        ProjectionExpression=(
            "suggested_failure_mode, description, severity, occurrence, detection, action_priority"
        ),
    )
    failure_modes: dict[str, dict] = {}
    for item in items:
        rpn = _finding_rpn(item)
        if rpn is None:
            continue
        name = item.get("suggested_failure_mode") or item.get("description")
        if not name:
            continue
        entry = failure_modes.setdefault(str(name), {
            "failure_mode": str(name),
            "rpn": 0,
            "count": 0,
            "action_priority": None,
        })
        entry["count"] += 1
        if rpn > entry["rpn"]:
            entry["rpn"] = rpn
            entry["action_priority"] = item.get("action_priority")

    top_modes = sorted(
        failure_modes.values(),
        key=lambda mode: (-mode["rpn"], -mode["count"], mode["failure_mode"]),
    )[:7]
    return _ok({"failure_modes": top_modes})


def get_admin_findings_over_time() -> dict:
    findings = _scan_all(
        dynamodb.Table(FINDINGS_TABLE),
        ProjectionExpression="review_id, action_priority",
    )
    reviews = _scan_all(
        dynamodb.Table(REVIEWS_TABLE),
        ProjectionExpression="review_id, created_at",
    )
    review_dates = {
        str(review.get("review_id", "")): _parse_iso_datetime(review.get("created_at"))
        for review in reviews
    }

    now = datetime.datetime.now(datetime.timezone.utc)
    current_week = _start_of_week(now)
    weeks: dict[str, dict] = {}
    for weeks_ago in range(7, -1, -1):
        week_start = current_week - datetime.timedelta(weeks=weeks_ago)
        key = week_start.date().isoformat()
        weeks[key] = {
            "week": week_start.strftime("Wk %W"),
            "week_start": key,
            "H": 0,
            "M": 0,
            "L": 0,
        }

    for finding in findings:
        review_date = review_dates.get(str(finding.get("review_id", "")))
        action_priority = finding.get("action_priority")
        if review_date is None or action_priority not in ("H", "M", "L"):
            continue
        key = _start_of_week(review_date).date().isoformat()
        if key in weeks:
            weeks[key][action_priority] += 1

    return _ok({
        "basis": "review_created_at",
        "weeks": list(weeks.values()),
    })


def get_admin_severity_occurrence_matrix() -> dict:
    items = _scan_all(
        dynamodb.Table(FINDINGS_TABLE),
        ProjectionExpression="severity, occurrence",
    )
    severity_bands = ["Sev 1-3", "Sev 4-6", "Sev 7-10"]
    occurrence_bands = ["Occ 1-3", "Occ 4-5", "Occ 6-7", "Occ 8-10"]
    matrix = {severity: {occurrence: 0 for occurrence in occurrence_bands} for severity in severity_bands}

    def severity_band(value: int) -> str:
        if value <= 3:
            return "Sev 1-3"
        if value <= 6:
            return "Sev 4-6"
        return "Sev 7-10"

    def occurrence_band(value: int) -> str:
        if value <= 3:
            return "Occ 1-3"
        if value <= 5:
            return "Occ 4-5"
        if value <= 7:
            return "Occ 6-7"
        return "Occ 8-10"

    for item in items:
        severity = _number(item.get("severity"))
        occurrence = _number(item.get("occurrence"))
        if severity is None or occurrence is None:
            continue
        matrix[severity_band(int(severity))][occurrence_band(int(occurrence))] += 1

    return _ok({
        "matrix": [{"sev_band": severity, **matrix[severity]} for severity in severity_bands],
        "occurrence_bands": occurrence_bands,
    })


def get_admin_hitl_decisions() -> dict:
    reviews = _scan_all(dynamodb.Table(REVIEWS_TABLE))
    decisions = []
    for review in reviews:
        gate_fields = [
            ("1", "gate_1_status", "gate_1_comment", "gate_1_reviewer", "gate_1_decided_at"),
            ("2", "gate_2_status", "gate_2_comment", "gate_2_reviewer", "gate_2_decided_at"),
            ("3", "gate_3_status", "gate_3_comment", "gate_3_reviewer", "gate_3_decided_at"),
            ("4", "gate_4_status", "gate_4_comment", "gate_4_reviewer", "gate_4_decided_at"),
        ]
        for gate, status_field, comment_field, reviewer_field, timestamp_field in gate_fields:
            decision = review.get(status_field)
            if gate == "4" and not decision:
                decision = review.get("hitl_decision")
            if decision not in ("APPROVED", "REJECTED"):
                continue
            decision_timestamp = (
                review.get(timestamp_field)
                or review.get(f"gate_{gate}_timestamp")
                or (review.get("hitl_timestamp") if gate == "4" else None)
            )
            decisions.append({
                "review_id": review.get("review_id"),
                "assembly_name": review.get("assembly_name", ""),
                "gate": gate,
                "decision": decision,
                "comment": review.get(comment_field) or (review.get("hitl_comment") if gate == "4" else ""),
                "reviewer": review.get(reviewer_field) or (review.get("hitl_reviewer") if gate == "4" else None),
                "decision_timestamp": decision_timestamp,
                "record_updated_at": review.get("updated_at"),
                "finding": None,
                "agent_confidence": None,
            })

    decisions.sort(
        key=lambda decision: decision.get("decision_timestamp") or decision.get("record_updated_at") or "",
        reverse=True,
    )
    return _ok({"decisions": decisions[:23], "total": len(decisions)})


def get_admin_agent_activity() -> dict:
    reviews = _scan_all(
        dynamodb.Table(REVIEWS_TABLE),
        ProjectionExpression="assembly_name, #s, updated_at, review_id",
        ExpressionAttributeNames={"#s": "status"},
    )
    reviews.sort(key=lambda review: review.get("updated_at") or "", reverse=True)
    stage_labels = {
        "SUBMITTED": "Review submitted",
        "INTAKE_COMPLETE": "Intake parsing complete",
        "HITL_PENDING": "Awaiting HITL review",
        "COMPLETE": "Review complete - PDF generated",
        "FAILED": "Review failed",
    }
    activity = [{
        "timestamp": review.get("updated_at"),
        "assembly_name": review.get("assembly_name", ""),
        "event": stage_labels.get(review.get("status"), review.get("status", "")),
        "review_id": review.get("review_id", ""),
    } for review in reviews[:20]]
    return _ok({"activity": activity, "detail_available": False})


def get_ontology_health() -> dict:
    empty_response = {
        "entities": 0,
        "relations": 0,
        "materials": 0,
        "processes": 0,
        "failure_mechs": 0,
        "version": None,
        "recent_updates": [],
        "available": False,
    }
    if not NEPTUNE_ENDPOINT:
        return _ok(empty_response)
    try:
        counts_sparql = f"""
        PREFIX onto: <{ONTOLOGY_PREFIX}>
        SELECT ?entities ?relations ?materials ?processes ?failure_mechs WHERE {{
          {{ SELECT (COUNT(DISTINCT ?entity) AS ?entities) WHERE {{
              ?entity ?predicate ?object .
              FILTER(STRSTARTS(STR(?entity), "{ONTOLOGY_PREFIX}"))
          }} }}
          {{ SELECT (COUNT(DISTINCT ?predicate) AS ?relations) WHERE {{
              ?subject ?predicate ?object .
              FILTER(STRSTARTS(STR(?subject), "{ONTOLOGY_PREFIX}"))
          }} }}
          {{ SELECT (COUNT(DISTINCT ?material) AS ?materials) WHERE {{
              ?material a onto:Material .
          }} }}
          {{ SELECT (COUNT(DISTINCT ?process) AS ?processes) WHERE {{
              ?process a onto:Process .
          }} }}
          {{ SELECT (COUNT(DISTINCT ?failure) AS ?failure_mechs) WHERE {{
              ?failure a onto:FailureMode .
          }} }}
        }}
        """
        result = _sparql_query(counts_sparql)
        bindings = result["results"]["bindings"]
        binding = bindings[0] if bindings else {}
        return _ok({
            "entities": int(binding.get("entities", {}).get("value", 0)),
            "relations": int(binding.get("relations", {}).get("value", 0)),
            "materials": int(binding.get("materials", {}).get("value", 0)),
            "processes": int(binding.get("processes", {}).get("value", 0)),
            "failure_mechs": int(binding.get("failure_mechs", {}).get("value", 0)),
            "version": None,
            "recent_updates": [],
            "available": True,
        })
    except Exception as exc:
        print(f"[api:ontology_health] {exc}")
        return _ok(empty_response)


def _sparql_update(sparql: str) -> None:
    """Execute SPARQL UPDATE (INSERT/DELETE) against Neptune with SigV4 auth."""
    import urllib.request, urllib.parse, urllib.error
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    session = boto3.session.Session()
    creds   = session.get_credentials().get_frozen_credentials()
    body    = urllib.parse.urlencode({"update": sparql}).encode()
    url     = f"https://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/sparql"
    req     = AWSRequest(method="POST", url=url, data=body,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    SigV4Auth(creds, "neptune-db", session.region_name).add_auth(req)
    headers = {k: v for k, v in req.headers.items() if k.lower() != "host"}
    http_req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(http_req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        body_txt = exc.read().decode("utf-8", errors="replace")[:500]
        print(f"[api:_sparql_update] Neptune {exc.code} {exc.reason}: {body_txt}")
        raise


def post_ontology_load() -> dict:
    """POST /ontology/load — read N-Triples from S3, insert into Neptune via SPARQL UPDATE."""
    if not NEPTUNE_ENDPOINT:
        return _err("Neptune endpoint not configured", 503)
    ONTOLOGY_BUCKET = os.environ.get("ONTOLOGY_BUCKET_NAME",
                                     f"dfmea-ontology-{boto3.client('sts').get_caller_identity()['Account']}")
    try:
        # Fetch N-Triples from S3
        obj = s3_client.get_object(Bucket=ONTOLOGY_BUCKET, Key="ontology/ontology.nt")
        nt_data = obj["Body"].read().decode("utf-8")
        # Build SPARQL INSERT DATA from N-Triples lines
        triples = [line.strip() for line in nt_data.splitlines()
                   if line.strip() and not line.startswith("#")]
        if not triples:
            return _ok({"status": "nothing_to_insert", "triples": 0})
        # Insert in batches of 50 to avoid query size limits
        batch_size = 50
        inserted = 0
        for i in range(0, len(triples), batch_size):
            batch = triples[i:i + batch_size]
            # Insert into default graph — SPARQL SELECT queries don't specify a named graph
            sparql_update = "INSERT DATA {\n" + "\n".join(batch) + "\n}"
            _sparql_update(sparql_update)
            inserted += len(batch)
        return _ok({"status": "complete", "triples_inserted": inserted})
    except Exception as exc:
        print(f"[api:ontology_load] {exc}")
        return _err(str(exc), 500)


def get_ontology_load_status(load_id: str) -> dict:
    """GET /ontology/load/{load_id} — poll Neptune bulk loader job status."""
    if not NEPTUNE_ENDPOINT:
        return _err("Neptune endpoint not configured", 503)
    try:
        import urllib.request
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        session = boto3.session.Session()
        creds   = session.get_credentials().get_frozen_credentials()
        url = f"https://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/loader/{load_id}"
        req = AWSRequest(method="GET", url=url, headers={})
        SigV4Auth(creds, "neptune-db", session.region_name).add_auth(req)
        http_req = urllib.request.Request(url, headers=dict(req.headers))
        with urllib.request.urlopen(http_req, timeout=15) as resp:
            return _ok(json.loads(resp.read()))
    except Exception as exc:
        print(f"[api:ontology_load_status] {exc}")
        return _err(str(exc), 500)


def get_ontology_graph(event: dict) -> dict:
    if not NEPTUNE_ENDPOINT:
        return _ok({"nodes": [], "edges": [], "available": False})
    qs        = event.get("queryStringParameters") or {}
    component = qs.get("component", "")
    try:
        if component:
            component = component.strip()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,127}", component):
                return _err("Invalid ontology component name", 400)
            component_iri = f"{ONTOLOGY_PREFIX}Component#{component}"
            sparql = f"""
            PREFIX onto: <{ONTOLOGY_PREFIX}>
            SELECT ?s ?p ?o ?stype ?otype WHERE {{
                {{
                    <{component_iri}> ?p ?o .
                    BIND(<{component_iri}> AS ?s)
                }} UNION {{
                    ?s ?p <{component_iri}> .
                    BIND(<{component_iri}> AS ?o)
                }} UNION {{
                    ?s onto:hasFailureMode ?fm .
                    ?fm ?p ?o .
                    BIND(?fm AS ?s)
                    FILTER(STRSTARTS(STR(?s), "{ONTOLOGY_PREFIX}"))
                }}
                OPTIONAL {{ ?s a ?stype }}
                OPTIONAL {{ ?o a ?otype }}
                FILTER(STRSTARTS(STR(?s), "{ONTOLOGY_PREFIX}") || STRSTARTS(STR(?o), "{ONTOLOGY_PREFIX}"))
            }} LIMIT 200
            """
        else:
            sparql = f"""
            PREFIX onto: <{ONTOLOGY_PREFIX}>
            SELECT ?s ?p ?o ?stype ?otype WHERE {{
                ?s ?p ?o .
                OPTIONAL {{ ?s a ?stype }}
                OPTIONAL {{ ?o a ?otype }}
                FILTER(STRSTARTS(STR(?s), "{ONTOLOGY_PREFIX}"))
                FILTER(STRSTARTS(STR(?o), "{ONTOLOGY_PREFIX}"))
            }} LIMIT 500
            """
        result = _sparql_query(sparql)
        nodes_map: dict = {}
        edges: list     = []
        TYPE_MAP = {
            "Component":       "Component",
            "Material":        "Material",
            "Process":         "Process",
            "FailureMode":     "FailureMode",
            "Effect":          "Effect",
            "DetectionMethod": "DetectionMethod",
            "CorrectiveAction":"CorrectiveAction",
            "Standard":        "Standard",
        }
        def _node_id(uri: str) -> str:
            return uri.split("#")[-1] if "#" in uri else uri.split("/")[-1]
        def _node_type(type_uri: str) -> str:
            if not type_uri:
                return "Unknown"
            local = type_uri.split("#")[-1]
            return TYPE_MAP.get(local, local)
        for b in result["results"]["bindings"]:
            s_uri = b["s"]["value"]; o_uri = b["o"]["value"]
            p_uri = b["p"]["value"]
            s_id  = _node_id(s_uri); o_id = _node_id(o_uri)
            s_type = _node_type(b.get("stype", {}).get("value", ""))
            o_type = _node_type(b.get("otype", {}).get("value", ""))
            if s_id not in nodes_map:
                nodes_map[s_id] = {"id": s_id, "label": s_id.replace("_", " "), "type": s_type}
            if o_id not in nodes_map:
                nodes_map[o_id] = {"id": o_id, "label": o_id.replace("_", " "), "type": o_type}
            p_label = p_uri.split("#")[-1] if "#" in p_uri else p_uri.split("/")[-1]
            edges.append({"source": s_id, "target": o_id, "label": p_label})
        return _ok({"nodes": list(nodes_map.values()), "edges": edges, "available": True})
    except Exception as exc:
        print(f"[api:ontology_graph] {exc}")
        return _ok({"nodes": [], "edges": [], "available": False, "error": str(exc)})


def get_cad_result(review_id: str) -> dict:
    """GET /reviews/{id}/cad — return CAD extraction result + DFMEA component list."""
    # Try CAD extraction result (only populated when a drawing file was uploaded)
    cad_data: dict = {"review_id": review_id, "anchor_count": 0, "anchors": [], "cad_key": None}
    try:
        obj = s3_client.get_object(Bucket=PROCESSED_BUCKET, Key=f"reviews/{review_id}/cad-extraction-result.json")
        cad_data = json.loads(obj["Body"].read())
    except ClientError:
        pass

    # Derive unique components from normalised DFMEA rows (always available)
    dfmea_components: list[dict] = []
    try:
        obj = s3_client.get_object(Bucket=PROCESSED_BUCKET, Key=f"reviews/{review_id}/normalised.json")
        normalised = json.loads(obj["Body"].read())
        rows = normalised.get("rows", [])
        seen: dict[str, int] = {}
        for row in rows:
            part = str(row.get("part_name") or "").strip()
            if part:
                seen[part] = seen.get(part, 0) + 1
        # Sort by frequency descending
        dfmea_components = [{"part_name": p, "row_count": c} for p, c in sorted(seen.items(), key=lambda x: -x[1])]
    except ClientError:
        pass

    cad_data["dfmea_components"] = dfmea_components
    return _ok(cad_data)


def get_search(event: dict) -> dict:
    """GET /search?q=<query>&size=10 — hybrid search against AOSS regulatory corpus."""
    params = event.get("queryStringParameters") or {}
    query  = params.get("q", "").strip()
    size   = min(int(params.get("size", 10)), 50)
    if not query:
        return _err("q parameter is required")
    if not AOSS_ENDPOINT:
        return _err("Search not configured", 503)
    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from requests_aws4auth import AWS4Auth
        import boto3 as _boto3
        session  = _boto3.session.Session()
        creds    = session.get_credentials()
        region   = session.region_name or "us-east-1"
        awsauth  = AWS4Auth(creds.access_key, creds.secret_key, region, "aoss",
                            session_token=creds.token)
        host     = AOSS_ENDPOINT.replace("https://", "").rstrip("/")
        client   = OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
        )
        resp = client.search(
            index=AOSS_INDEX,
            body={"query": {"multi_match": {"query": query, "fields": ["content", "key"]}}, "size": size},
        )
        hits = [
            {"key": h["_source"].get("key", ""), "excerpt": h["_source"].get("content", "")[:300],
             "score": h["_score"]}
            for h in resp["hits"]["hits"]
        ]
        return _ok({"query": query, "hits": hits, "total": resp["hits"]["total"]["value"]})
    except Exception as exc:
        print(f"[api:search] {exc}")
        return _err(str(exc), 500)


def get_gates(event: dict) -> dict:
    """GET /reviews/{review_id}/gates — return canonical gate statuses."""
    path_params = event.get("pathParameters") or {}
    review_id   = path_params.get("review_id", "")
    if not review_id:
        return _err("review_id is required")
    resp = dynamodb.Table(REVIEWS_TABLE).get_item(Key={"review_id": review_id})
    item = resp.get("Item")
    if not item:
        return _err(f"Review {review_id} not found", 404)

    gates = {}
    for gate_number in range(1, 4):
        status = item.get(f"gate_{gate_number}_status", "NOT_STARTED")
        token_present = bool(item.get(f"gate_{gate_number}_task_token"))
        gates[str(gate_number)] = {
            "status": status,
            "comment": item.get(f"gate_{gate_number}_comment", ""),
            "pending": token_present and status not in ("APPROVED", "REJECTED"),
        }

    # Prefer the generalized gate_4 fields. Fall back to legacy hitl_* fields
    # for reviews created before the handlers were unified.
    gate4_status = item.get("gate_4_status") or item.get("hitl_decision", "")
    if gate4_status not in ("PENDING", "APPROVED", "REJECTED"):
        gate4_status = "PENDING" if item.get("status") == "HITL_PENDING" else "NOT_STARTED"
    gate4_token_present = bool(item.get("hitl_task_token"))
    gates["4"] = {
        "status": gate4_status,
        "comment": item.get("gate_4_comment") or item.get("hitl_comment", ""),
        "pending": gate4_token_present and gate4_status not in ("APPROVED", "REJECTED"),
    }
    return _ok({"review_id": review_id, "gates": gates})


def post_gate(event: dict) -> dict:
    """POST /reviews/{review_id}/gate/{gate_number} — approve or reject a gate."""
    path_params = event.get("pathParameters") or {}
    review_id   = path_params.get("review_id", "")
    gate_number = path_params.get("gate_number", "")
    body        = json.loads(event.get("body") or "{}")

    payload = json.dumps({
        "review_id": review_id,
        "gate":      gate_number,
        "action":    body.get("action", ""),
        "comment":   body.get("comment", ""),
    })
    response = lambda_client.invoke(
        FunctionName=HITL_GATE_FN,
        InvocationType="RequestResponse",
        Payload=payload.encode(),
    )
    result = json.loads(response["Payload"].read())
    status = result.get("statusCode", 200)
    body   = json.loads(result.get("body", "{}"))
    if status >= 400:
        return _err(body.get("error", "Gate action failed"), status=status)
    return _ok(body)


# ── Main router ────────────────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}
    review_id = path_params.get("review_id", "")

    # Assistant requests are handled by a dedicated module so the existing
    # review API stays independent from the runtime adapter.
    if path == "/assistant/conversations" or path.startswith("/assistant/conversations/"):
        from lambdas.api.assistant import route_assistant_request
        return route_assistant_request(event)

    # Health check (no auth required)
    if path == "/health" and method == "GET":
        return get_health()

    # Presigned upload URL
    if path == "/upload-url" and method == "GET":
        return get_upload_url(event)

    # Admin metrics
    if path == "/admin/metrics" and method == "GET":
        return get_admin_metrics()

    # List reviews
    if path == "/reviews" and method == "GET":
        return list_reviews(event)

    # Create review
    if path == "/reviews" and method == "POST":
        return post_reviews(event)

    # Single review operations
    if review_id:
        if method == "GET" and path.endswith(f"/{review_id}"):
            return get_review(review_id)
        if method == "DELETE":
            return delete_review(review_id)
        if method == "GET" and path.endswith("/findings"):
            return get_findings(review_id, event)
        if method == "GET" and path.endswith("/cad"):
            return get_cad_result(review_id)
        if method == "GET" and path.endswith("/report"):
            return get_report(review_id)
        if method == "POST" and path.endswith("/hitl"):
            HITL_CB_FN = os.environ.get("HITL_CALLBACK_FN_NAME", "dfmea-hitl-callback")
            body_raw = event.get("body") or "{}"
            payload = json.dumps({
                "body": body_raw,
                "pathParameters": {"review_id": review_id},
            })
            response = lambda_client.invoke(
                FunctionName=HITL_CB_FN,
                InvocationType="RequestResponse",
                Payload=payload.encode(),
            )
            result = json.loads(response["Payload"].read())
            status = result.get("statusCode", 200)
            resp_body = json.loads(result.get("body", "{}"))
            if status >= 400:
                return _err(resp_body.get("error", "HITL action failed"), status=status)
            return _ok(resp_body)

    # Search
    if method == "GET" and path == "/search":
        return get_search(event)

    # Gate status list and gate decision
    if review_id:
        if method == "GET" and path.endswith("/gates"):
            return get_gates(event)
        if method == "POST" and path_params.get("gate_number"):
            return post_gate(event)

    # Admin dashboard endpoints
    if path == "/admin/top-failure-modes" and method == "GET":
        return get_admin_top_failure_modes()
    if path == "/admin/findings-over-time" and method == "GET":
        return get_admin_findings_over_time()
    if path == "/admin/severity-occurrence-matrix" and method == "GET":
        return get_admin_severity_occurrence_matrix()
    if path == "/admin/hitl-decisions" and method == "GET":
        return get_admin_hitl_decisions()
    if path == "/admin/agent-activity" and method == "GET":
        return get_admin_agent_activity()
    # Ontology endpoints
    if path == "/ontology/health" and method == "GET":
        return get_ontology_health()
    if path == "/ontology/graph" and method == "GET":
        return get_ontology_graph(event)
    if path == "/ontology/load" and method == "POST":
        return post_ontology_load()
    if method == "GET" and path.startswith("/ontology/load/"):
        load_id = path.split("/ontology/load/")[-1]
        return get_ontology_load_status(load_id)

    return _err("Not found", 404)
