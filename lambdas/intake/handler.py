"""
lambdas/intake/handler.py — S1 Intake Lambda.

Triggered by S3 PUT on the uploads bucket. Parses the uploaded DFMEA
(Excel or JSON), normalises rows to the dfmea-row schema, writes a review
record to DynamoDB, and stores the normalised JSON to the processed bucket.
"""
from __future__ import annotations
import json
import os
import uuid
import datetime
import boto3

REVIEWS_TABLE         = os.environ.get("REVIEWS_TABLE_NAME", "dfmea-reviews")
PROCESSED_BUCKET      = os.environ.get("PROCESSED_BUCKET_NAME", "dfmea-processed")
TEXTRACT_SNS_TOPIC_ARN = os.environ.get("TEXTRACT_SNS_TOPIC_ARN", "")
TEXTRACT_ROLE_ARN      = os.environ.get("TEXTRACT_ROLE_ARN", "")

dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")


def _get_textract():
    return boto3.client("textract")


# ── Column name normalisers ────────────────────────────────────────────────────
_COL_MAP = {
    "part name": "part_name",
    "part_name": "part_name",
    "component": "part_name",
    "function": "function",
    "failure mode": "failure_mode",
    "failure_mode": "failure_mode",
    "failure mode (fm)": "failure_mode",
    "effect": "effect",
    "effects of failure": "effect",
    "cause": "cause",
    "cause of failure": "cause",
    "severity": "severity",
    "s": "severity",
    "occurrence": "occurrence",
    "o": "occurrence",
    "detection": "detection",
    "d": "detection",
    "rpn": "rpn",
    "action priority": "action_priority",
    "ap": "action_priority",
    "current prevention controls": "current_prevention_controls",
    "current detection controls": "current_detection_controls",
    "recommended action": "recommended_action",
    "standard citations": "standard_citations",
    "notes": "notes",
}


def _normalise_header(raw: str) -> str:
    return _COL_MAP.get(raw.strip().lower(), raw.strip().lower().replace(" ", "_"))


def _parse_excel(body: bytes) -> list[dict]:
    """Parse XLSX using openpyxl (available in Lambda layer)."""
    import io
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl not available; include it in the Lambda layer.")
    wb = openpyxl.load_workbook(io.BytesIO(body), data_only=True)
    ws = wb.active
    rows = list(ws.values)
    if not rows:
        return []
    headers = [_normalise_header(str(h or "")) for h in rows[0]]
    result = []
    for raw_row in rows[1:]:
        if all(v is None for v in raw_row):
            continue
        row_dict = {}
        for h, v in zip(headers, raw_row):
            row_dict[h] = v
        result.append(row_dict)
    return result


def _parse_json(body: bytes) -> list[dict]:
    data = json.loads(body)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    return [data]


def _coerce_rating(val) -> int:
    """Return int in 1-10 range or 5 as default."""
    try:
        v = int(val)
        return max(1, min(10, v))
    except (TypeError, ValueError):
        return 5


def _derive_ap(s: int, o: int, d: int) -> str:
    """AIAG-VDA 2019 Action Priority rules (deterministic)."""
    if s >= 9:
        return "H"
    if s >= 5 and o >= 4 and d >= 7:
        return "H"
    if s >= 5 and o >= 4 and d <= 6:
        return "M"
    if s >= 5 and o <= 3:
        return "L"
    if s <= 4 and o >= 6:
        return "M"
    return "L"


def _normalise_rows(raw_rows: list[dict]) -> list[dict]:
    normalised = []
    for i, r in enumerate(raw_rows):
        sev = _coerce_rating(r.get("severity", 5))
        occ = _coerce_rating(r.get("occurrence", 5))
        det = _coerce_rating(r.get("detection", 5))
        ap = r.get("action_priority") or _derive_ap(sev, occ, det)
        row = {
            "row_index": i,
            "part_name": str(r.get("part_name", "") or ""),
            "function": str(r.get("function", "") or ""),
            "failure_mode": str(r.get("failure_mode", "") or ""),
            "effect": str(r.get("effect", "") or ""),
            "cause": str(r.get("cause", "") or ""),
            "severity": sev,
            "occurrence": occ,
            "detection": det,
            "action_priority": str(ap).upper(),
            "current_prevention_controls": str(r.get("current_prevention_controls", "") or ""),
            "current_detection_controls": str(r.get("current_detection_controls", "") or ""),
            "recommended_action": str(r.get("recommended_action", "") or ""),
            "standard_citations": r.get("standard_citations", []),
            "notes": str(r.get("notes", "") or ""),
        }
        normalised.append(row)
    return normalised


def _handle_pdf(bucket: str, key: str) -> None:
    """Start an async Textract document-analysis job for a PDF upload."""
    review_id = str(uuid.uuid4())
    now       = datetime.datetime.utcnow().isoformat() + "Z"
    resp = _get_textract().start_document_analysis(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["TABLES", "FORMS"],
        NotificationChannel={
            "SNSTopicArn": TEXTRACT_SNS_TOPIC_ARN,
            "RoleArn":     TEXTRACT_ROLE_ARN,
        },
        JobTag=review_id,
    )
    job_id = resp["JobId"]
    dynamodb.Table(REVIEWS_TABLE).put_item(Item={
        "review_id":       review_id,
        "source_key":      key,
        "source_bucket":   bucket,
        "status":          "TEXTRACT_IN_PROGRESS",
        "textract_job_id": job_id,
        "created_at":      now,
        "updated_at":      now,
    })
    print(f"[intake] pdf review_id={review_id} job_id={job_id} key={key}")


def _sfn_intake(event: dict) -> dict:
    """
    Called directly by Step Functions as the first step of the review SM.
    Uses the review_id and file_key from the SFN input so normalised.json
    is written under the correct review path.
    """
    review_id   = event["review_id"]
    file_key    = event["file_key"]
    uploads_bkt = os.environ.get("UPLOADS_BUCKET_NAME", "dfmea-uploads")

    obj  = s3_client.get_object(Bucket=uploads_bkt, Key=file_key)
    body = obj["Body"].read()

    if file_key.lower().endswith((".xlsx", ".xls")):
        raw_rows = _parse_excel(body)
    elif file_key.lower().endswith(".pdf"):
        # PDF needs async Textract — skip row parsing here
        raw_rows = []
    else:
        raw_rows = _parse_json(body)

    rows = _normalise_rows(raw_rows)

    # ML pre-screening is required. Do not advance intake with missing scores.
    from agents.shared.ml_scorer import score_rows
    ml_score_count = score_rows(rows, review_id)
    if ml_score_count != len(rows):
        raise RuntimeError(
            f"ML pre-screen count mismatch for review {review_id}: "
            f"expected {len(rows)}, wrote {ml_score_count}"
        )

    now  = datetime.datetime.utcnow().isoformat() + "Z"

    # Update review record with row count and INTAKE_COMPLETE status
    dynamodb.Table(REVIEWS_TABLE).update_item(
        Key={"review_id": review_id},
        UpdateExpression="SET #s = :s, row_count = :rc, updated_at = :t",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "INTAKE_COMPLETE", ":rc": len(rows), ":t": now},
    )

    # Write normalised JSON under the SFN review_id
    processed_key = f"reviews/{review_id}/normalised.json"
    s3_client.put_object(
        Bucket=PROCESSED_BUCKET,
        Key=processed_key,
        Body=json.dumps({"review_id": review_id, "rows": rows}, indent=2),
        ContentType="application/json",
    )

    print(
        f"[intake:sfn] review_id={review_id} rows={len(rows)} "
        f"ml_scores={ml_score_count} key={file_key}"
    )
    return {"review_id": review_id, "row_count": len(rows), "file_key": file_key}


def handler(event: dict, context) -> dict:
    """Lambda entry point.

    Supports two invocation modes:
    - SFN task: event = {review_id, file_key}  → _sfn_intake()
    - S3 trigger: event = {Records: [...]}      → parse-and-create-review path
    """
    # Step Functions direct invocation
    if "review_id" in event and "file_key" in event:
        return _sfn_intake(event)

    # S3 trigger
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        # PDF branch: start async Textract job and skip synchronous parsing
        if key.lower().endswith(".pdf"):
            _handle_pdf(bucket, key)
            continue

        # Download object
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()

        # Parse
        if key.lower().endswith((".xlsx", ".xls")):
            raw_rows = _parse_excel(body)
        else:
            raw_rows = _parse_json(body)

        # Normalise
        rows = _normalise_rows(raw_rows)

        # ML pre-screening is required. A failed or partial score write aborts intake.
        review_id = str(uuid.uuid4())
        from agents.shared.ml_scorer import score_rows
        ml_score_count = score_rows(rows, review_id)
        if ml_score_count != len(rows):
            raise RuntimeError(
                f"ML pre-screen count mismatch for review {review_id}: "
                f"expected {len(rows)}, wrote {ml_score_count}"
            )

        now = datetime.datetime.utcnow().isoformat() + "Z"
        review_record = {
            "review_id": review_id,
            "source_key": key,
            "source_bucket": bucket,
            "status": "INTAKE_COMPLETE",
            "row_count": len(rows),
            "created_at": now,
            "updated_at": now,
        }

        # Write DynamoDB review record
        table = dynamodb.Table(REVIEWS_TABLE)
        table.put_item(Item=review_record)

        # Write normalised JSON to processed bucket
        processed_key = f"reviews/{review_id}/normalised.json"
        s3_client.put_object(
            Bucket=PROCESSED_BUCKET,
            Key=processed_key,
            Body=json.dumps({"review_id": review_id, "rows": rows}, indent=2),
            ContentType="application/json",
        )

        print(f"[intake] review_id={review_id} rows={len(rows)} key={key}")

    return {"statusCode": 200, "body": "OK"}
