"""
lambdas/textract_callback/handler.py — Textract async-job callback Lambda.

Triggered by SNS when an asynchronous Textract StartDocumentAnalysis job
completes. Retrieves all blocks, extracts table rows, enriches with
Comprehend NER, normalises numeric fields, writes results to S3, and
updates the DFMEA reviews DynamoDB table.
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REVIEWS_TABLE_NAME = os.environ.get("REVIEWS_TABLE_NAME", "dfmea-reviews")
PROCESSED_BUCKET_NAME = os.environ.get("PROCESSED_BUCKET_NAME", "dfmea-processed")

# ---------------------------------------------------------------------------
# Lazy boto3 getters — module-level clients are intentionally avoided so that
# unit tests can patch boto3.client / boto3.resource before import.
# ---------------------------------------------------------------------------
_textract_client = None
_comprehend_client = None
_s3_client = None
_dynamodb_resource = None


def _get_textract():
    global _textract_client
    if _textract_client is None:
        _textract_client = boto3.client("textract")
    return _textract_client


def _get_comprehend():
    global _comprehend_client
    if _comprehend_client is None:
        _comprehend_client = boto3.client("comprehend")
    return _comprehend_client


def _get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _get_dynamodb():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


# ---------------------------------------------------------------------------
# Textract helpers
# ---------------------------------------------------------------------------

def _get_all_blocks(job_id: str) -> list[dict]:
    """Paginate through all blocks for a completed Textract job."""
    blocks: list[dict] = []
    kwargs: dict[str, Any] = {"JobId": job_id}
    while True:
        response = _get_textract().get_document_analysis(**kwargs)
        blocks.extend(response.get("Blocks", []))
        next_token = response.get("NextToken")
        if not next_token:
            break
        kwargs["NextToken"] = next_token
    return blocks


def _extract_table_rows(blocks: list[dict]) -> list[dict[tuple[int, int], str]]:
    """
    Return one dict per TABLE block mapping (row_index, col_index) -> text.

    Only CELL blocks with a TEXT or WORD relationship are considered.
    Blocks that are not TABLE or CELL are ignored.
    """
    # Build a lookup of block id -> block
    id_map: dict[str, dict] = {b["Id"]: b for b in blocks if "Id" in b}

    # Group CELL blocks by their parent TABLE id
    table_cells: dict[str, list[dict]] = {}
    for block in blocks:
        if block.get("BlockType") == "TABLE":
            table_cells[block["Id"]] = []

    # Associate CELL children with each TABLE
    for block in blocks:
        if block.get("BlockType") == "TABLE":
            for rel in block.get("Relationships", []):
                if rel.get("Type") == "CHILD":
                    for cid in rel.get("Ids", []):
                        child = id_map.get(cid, {})
                        if child.get("BlockType") == "CELL":
                            table_cells[block["Id"]].append(child)

    # If no TABLE→CELL relationships found, collect all CELL blocks as one table
    if not any(table_cells.values()):
        loose_cells = [b for b in blocks if b.get("BlockType") == "CELL"]
        if loose_cells:
            table_cells["__loose__"] = loose_cells

    tables: list[dict[tuple[int, int], str]] = []
    for tbl_id, cells in table_cells.items():
        if not cells:
            continue
        row_map: dict[tuple[int, int], str] = {}
        for cell in cells:
            row_idx = cell.get("RowIndex", 0)
            col_idx = cell.get("ColumnIndex", 0)
            # Prefer direct Text attribute; fall back to WORD children
            text = cell.get("Text", "")
            if not text:
                for rel in cell.get("Relationships", []):
                    if rel.get("Type") == "CHILD":
                        word_texts = [
                            id_map[wid].get("Text", "")
                            for wid in rel.get("Ids", [])
                            if wid in id_map and id_map[wid].get("BlockType") == "WORD"
                        ]
                        text = " ".join(word_texts)
            row_map[(row_idx, col_idx)] = text.strip()
        tables.append(row_map)

    return tables


def _rows_to_dicts(table_rows: dict[tuple[int, int], str]) -> list[dict]:
    """
    Convert a (row, col)->text map into a list of dicts.

    Row 1 is treated as the header; subsequent rows become data records.
    If there is only one row, return it as a single dict with integer keys.
    """
    if not table_rows:
        return []

    row_indices = sorted({r for (r, _) in table_rows})
    col_indices = sorted({c for (_, c) in table_rows})

    if len(row_indices) < 2:
        # No header row — just return one dict with column indices as keys
        return [{str(c): table_rows.get((row_indices[0], c), "") for c in col_indices}]

    header_row = row_indices[0]
    headers = {c: table_rows.get((header_row, c), f"col_{c}") for c in col_indices}

    result: list[dict] = []
    for r in row_indices[1:]:
        record = {headers[c]: table_rows.get((r, c), "") for c in col_indices}
        result.append(record)
    return result


# ---------------------------------------------------------------------------
# Comprehend NER enrichment
# ---------------------------------------------------------------------------

def _enrich_with_ner(rows: list[dict]) -> list[dict]:
    """
    Call Comprehend detect_entities on a concatenation of all row values
    (capped at 4 500 chars) and add the `ner_entities` field to every row.
    Failures are silently ignored to keep the pipeline moving.
    """
    if not rows:
        return rows

    combined_text = " | ".join(
        " ".join(str(v) for v in row.values()) for row in rows
    )[:4500]

    entities: list[dict] = []
    try:
        response = _get_comprehend().detect_entities(
            Text=combined_text, LanguageCode="en"
        )
        entities = response.get("Entities", [])
    except Exception as exc:
        print(f"[textract_callback] Comprehend NER failed (non-fatal): {exc}")

    for row in rows:
        row["ner_entities"] = entities

    return rows


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_NUMERIC_FIELDS = ("severity", "occurrence", "detection", "rpn",
                   "Severity", "Occurrence", "Detection", "RPN",
                   "sev", "occ", "det")


def _normalise_rows(raw_rows: list[dict]) -> list[dict]:
    """
    Coerce severity / occurrence / detection fields to integers 1–10.
    Values outside [1, 10] are clamped. Non-numeric values default to 1.
    """
    for row in raw_rows:
        for field in _NUMERIC_FIELDS:
            if field in row:
                try:
                    val = int(float(str(row[field]).strip()))
                    row[field] = max(1, min(10, val))
                except (ValueError, TypeError):
                    row[field] = 1
    return raw_rows


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------

def _get_review(review_id: str) -> dict:
    table = _get_dynamodb().Table(REVIEWS_TABLE_NAME)
    response = table.get_item(Key={"review_id": review_id})
    return response.get("Item", {})


def _update_review_status(review_id: str, status: str, extra: dict | None = None) -> None:
    table = _get_dynamodb().Table(REVIEWS_TABLE_NAME)
    update_expr = "SET #st = :status"
    expr_attr_names = {"#st": "status"}
    expr_attr_values: dict[str, Any] = {":status": status}

    if extra:
        for k, v in extra.items():
            placeholder = f"#{k}"
            val_placeholder = f":{k}"
            update_expr += f", {placeholder} = {val_placeholder}"
            expr_attr_names[placeholder] = k
            expr_attr_values[val_placeholder] = v

    table.update_item(
        Key={"review_id": review_id},
        UpdateExpression=update_expr,
        ExpressionAttributeNames=expr_attr_names,
        ExpressionAttributeValues=expr_attr_values,
    )


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    """
    SNS → Lambda callback for async Textract jobs.

    SNS message payload (JSON string):
      {
        "JobId":   "<textract-job-id>",
        "Status":  "SUCCEEDED" | "FAILED" | ...,
        "API":     "StartDocumentAnalysis",
        "JobTag":  "<review_id>"        ← set when starting the job
      }
    """
    # Reset module-level lazy clients so tests can inject fresh mocks
    global _textract_client, _comprehend_client, _s3_client, _dynamodb_resource
    _textract_client = None
    _comprehend_client = None
    _s3_client = None
    _dynamodb_resource = None

    record = event["Records"][0]["Sns"]
    msg: dict = json.loads(record["Message"])

    job_id: str = msg.get("JobId", "")
    status: str = msg.get("Status", "UNKNOWN")
    review_id: str = msg.get("JobTag", "")

    print(f"[textract_callback] job_id={job_id} status={status} review_id={review_id}")

    # ------------------------------------------------------------------
    # Handle FAILED jobs
    # ------------------------------------------------------------------
    if status != "SUCCEEDED":
        error_msg = msg.get("StatusMessage", status)
        print(f"[textract_callback] Job failed: {error_msg}")
        if review_id:
            _update_review_status(
                review_id,
                "TEXTRACT_FAILED",
                extra={"textract_error": error_msg},
            )
        return {"statusCode": 200, "body": f"Job {job_id} failed — status recorded"}

    # ------------------------------------------------------------------
    # Retrieve review metadata
    # ------------------------------------------------------------------
    review_item: dict = {}
    if review_id:
        review_item = _get_review(review_id)

    # ------------------------------------------------------------------
    # Fetch all Textract blocks
    # ------------------------------------------------------------------
    blocks = _get_all_blocks(job_id)
    print(f"[textract_callback] Retrieved {len(blocks)} blocks for job {job_id}")

    # ------------------------------------------------------------------
    # Extract → enrich → normalise
    # ------------------------------------------------------------------
    table_maps = _extract_table_rows(blocks)
    all_rows: list[dict] = []
    for tmap in table_maps:
        all_rows.extend(_rows_to_dicts(tmap))

    all_rows = _enrich_with_ner(all_rows)
    all_rows = _normalise_rows(all_rows)

    # ------------------------------------------------------------------
    # Persist to S3
    # ------------------------------------------------------------------
    out_key = f"reviews/{review_id}/textract-tables.json"
    payload = {
        "review_id": review_id,
        "job_id": job_id,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    _get_s3().put_object(
        Bucket=PROCESSED_BUCKET_NAME,
        Key=out_key,
        Body=json.dumps(payload, default=str),
        ContentType="application/json",
    )
    print(f"[textract_callback] Wrote {len(all_rows)} rows to s3://{PROCESSED_BUCKET_NAME}/{out_key}")

    # ------------------------------------------------------------------
    # Update DynamoDB
    # ------------------------------------------------------------------
    if review_id:
        _update_review_status(
            review_id,
            "INTAKE_COMPLETE",
            extra={"textract_output_key": out_key, "row_count": len(all_rows)},
        )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"review_id": review_id, "job_id": job_id, "row_count": len(all_rows)}
        ),
    }
