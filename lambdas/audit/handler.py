"""
lambdas/audit/handler.py — Audit Package Lambda.

GET /reviews/{id}/audit

Collects all stage artifacts from S3 for a given review_id, assembles them
into a ZIP archive (stored in the reports bucket), and returns a pre-signed URL
valid for 1 hour.

Audit package contents:
  - normalised.json          (S1 intake output)
  - cad-extraction-result.json  (S0 CAD output)
  - findings.json            (all agent findings from DynamoDB)
  - synthesis-result.json    (analyst synthesis, if present)
  - final-report.pdf         (S6 final report, if present)
  - review-metadata.json     (review DynamoDB record)
  - manifest.json            (audit package manifest: hashes, timestamps)
"""
from __future__ import annotations
import hashlib
import io
import json
import os
import zipfile
import datetime
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

REVIEWS_TABLE = os.environ.get("REVIEWS_TABLE_NAME", "dfmea-reviews")
FINDINGS_TABLE = os.environ.get("FINDINGS_TABLE_NAME", "dfmea-analysis-findings")
PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET_NAME", "dfmea-processed")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET_NAME", "dfmea-reports")

dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")


def _s3_get_safe(bucket: str, key: str) -> bytes | None:
    """Return object bytes or None if not found."""
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise


def _get_review(review_id: str) -> dict | None:
    table = dynamodb.Table(REVIEWS_TABLE)
    resp = table.get_item(Key={"review_id": review_id})
    item = resp.get("Item")
    if item:
        item.pop("hitl_task_token", None)  # never include in audit export
    return item


def _get_all_findings(review_id: str) -> list[dict]:
    table = dynamodb.Table(FINDINGS_TABLE)
    resp = table.query(
        KeyConditionExpression=Key("review_id").eq(review_id),
        Limit=500,
    )
    return resp.get("Items", [])


def _build_manifest(files: dict[str, bytes], review_id: str) -> bytes:
    manifest = {
        "review_id": review_id,
        "package_created_at": datetime.datetime.utcnow().isoformat() + "Z",
        "standard": "AIAG-VDA 2019",
        "files": {
            name: {
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "present": True,
            }
            for name, content in files.items()
        },
    }
    return json.dumps(manifest, indent=2).encode("utf-8")


def _build_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def handler(event: dict, context) -> dict:
    """Lambda entry point. Can be called directly or via API Gateway proxy."""
    path_params = event.get("pathParameters") or {}
    review_id = path_params.get("review_id") or event.get("review_id", "")

    if not review_id:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "review_id is required"}),
        }

    # 1. Fetch review metadata
    review = _get_review(review_id)
    if not review:
        return {
            "statusCode": 404,
            "body": json.dumps({"error": f"Review {review_id} not found"}),
        }

    # 2. Collect all stage artifacts
    files: dict[str, bytes] = {}

    # Review metadata
    files["review-metadata.json"] = json.dumps(review, indent=2, default=str).encode("utf-8")

    # S0 CAD extraction
    cad = _s3_get_safe(PROCESSED_BUCKET, f"reviews/{review_id}/cad-extraction-result.json")
    if cad:
        files["cad-extraction-result.json"] = cad

    # S1 normalised DFMEA
    normalised = _s3_get_safe(PROCESSED_BUCKET, f"reviews/{review_id}/normalised.json")
    if normalised:
        files["normalised.json"] = normalised

    # Agent findings from DynamoDB
    findings = _get_all_findings(review_id)
    files["findings.json"] = json.dumps(
        {"review_id": review_id, "findings": findings, "count": len(findings)},
        indent=2,
        default=str,
    ).encode("utf-8")

    # Synthesis result (analyst S4)
    synthesis = _s3_get_safe(PROCESSED_BUCKET, f"reviews/{review_id}/synthesis-result.json")
    if synthesis:
        files["synthesis-result.json"] = synthesis

    # Final PDF report (S6)
    report_pdf = _s3_get_safe(REPORTS_BUCKET, f"reviews/{review_id}/final-report.pdf")
    if report_pdf:
        files["final-report.pdf"] = report_pdf

    # SVG overlay (if present)
    svg_files = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=REPORTS_BUCKET, Prefix=f"reviews/{review_id}/overlay-"):
            for obj in page.get("Contents", []):
                body = _s3_get_safe(REPORTS_BUCKET, obj["Key"])
                if body:
                    fname = obj["Key"].split("/")[-1]
                    files[fname] = body
    except Exception:
        pass

    # 3. Build manifest (includes hashes of all files)
    files["manifest.json"] = _build_manifest(files, review_id)

    # 4. Build ZIP
    zip_bytes = _build_zip(files)

    # 5. Store ZIP in reports bucket
    now = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    zip_key = f"reviews/{review_id}/audit-package-{now}.zip"
    s3_client.put_object(
        Bucket=REPORTS_BUCKET,
        Key=zip_key,
        Body=zip_bytes,
        ContentType="application/zip",
        Metadata={
            "review_id": review_id,
            "file_count": str(len(files)),
            "finding_count": str(len(findings)),
        },
    )

    # 6. Generate pre-signed URL (1 hour)
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": REPORTS_BUCKET, "Key": zip_key},
        ExpiresIn=3600,
    )

    print(f"[audit] review_id={review_id} files={len(files)} zip_size={len(zip_bytes)} key={zip_key}")

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "review_id": review_id,
            "url": url,
            "expires_in": 3600,
            "file_count": len(files),
            "finding_count": len(findings),
            "zip_size_bytes": len(zip_bytes),
        }),
    }
