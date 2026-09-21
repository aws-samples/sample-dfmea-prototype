"""
lambdas/cad_extraction/handler.py — S0 CAD Extraction Lambda.

Triggered by Step Functions or S3 event. Extracts component labels from
an engineering drawing (PDF/image) using Amazon Textract, stores anchor
map to the processed bucket as cad-extraction-result.json.
"""
from __future__ import annotations
import json
import os
import re
import boto3

PROCESSED_BUCKET = os.environ.get("PROCESSED_BUCKET_NAME", "dfmea-processed")
UPLOADS_BUCKET = os.environ.get("UPLOADS_BUCKET_NAME", "dfmea-uploads")

textract = boto3.client("textract")
s3_client = boto3.client("s3")


# Heuristic patterns that indicate a CAD component label
_COMPONENT_RE = re.compile(
    r"^[A-Z][A-Z0-9\-_/ ]{2,40}$"   # uppercase labels
    r"|^\d{3,8}$"                      # part numbers
    r"|(?:BRACKET|REINF|PANEL|RAIL|PILLAR|SILL|BEAM|TUBE|GUSSET|WELD|NUT|BOLT|CLIP|HINGE|SEAL)",
    re.IGNORECASE,
)


def _extract_labels_textract(bucket: str, key: str) -> list[dict]:
    """Call Textract DetectDocumentText on a PDF/image stored in S3."""
    response = textract.detect_document_text(
        Document={"S3Object": {"Bucket": bucket, "Name": key}}
    )
    anchors = []
    for block in response.get("Blocks", []):
        if block["BlockType"] == "WORD":
            text = block.get("Text", "").strip()
            if _COMPONENT_RE.search(text):
                bbox = block.get("Geometry", {}).get("BoundingBox", {})
                anchors.append(
                    {
                        "label": text,
                        "x": round(bbox.get("Left", 0.0), 4),
                        "y": round(bbox.get("Top", 0.0), 4),
                        "w": round(bbox.get("Width", 0.0), 4),
                        "h": round(bbox.get("Height", 0.0), 4),
                        "confidence": round(block.get("Confidence", 0.0) / 100.0, 3),
                    }
                )
    # Deduplicate by label (keep highest confidence)
    seen: dict[str, dict] = {}
    for a in anchors:
        if a["label"] not in seen or a["confidence"] > seen[a["label"]]["confidence"]:
            seen[a["label"]] = a
    return sorted(seen.values(), key=lambda x: x["confidence"], reverse=True)


def handler(event: dict, context) -> dict:
    """
    Lambda entry point.
    event: {review_id, cad_key}  — cad_key is the S3 key of the drawing file.
    """
    review_id = event.get("review_id", "unknown")
    cad_key = event.get("cad_key")

    if not cad_key:
        # No drawing provided — return empty anchor map
        anchors: list[dict] = []
    else:
        try:
            anchors = _extract_labels_textract(UPLOADS_BUCKET, cad_key)
        except Exception as exc:
            print(f"[cad_extraction] Textract failed for {cad_key}: {exc}")
            anchors = []

    result = {
        "review_id": review_id,
        "cad_key": cad_key,
        "anchor_count": len(anchors),
        "anchors": anchors,
    }

    # Persist anchor map
    out_key = f"reviews/{review_id}/cad-extraction-result.json"
    s3_client.put_object(
        Bucket=PROCESSED_BUCKET,
        Key=out_key,
        Body=json.dumps(result, indent=2),
        ContentType="application/json",
    )

    print(f"[cad_extraction] review_id={review_id} anchors={len(anchors)}")
    return {"review_id": review_id, "anchor_count": len(anchors), "output_key": out_key}
