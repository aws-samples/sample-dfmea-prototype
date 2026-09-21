#!/usr/bin/env python3
"""
scripts/create_bedrock_kb.py

Creates AOSS vector indexes, Bedrock Knowledge Bases, and S3 data sources
for DFMEA.  Called automatically by deploy.sh after DfmeaKnowledgeBaseStack.

Pattern: opensearch-py + AWSV4SignerAuth (same as paint-shop-prediction).

Prerequisites:
  - DfmeaSearchStack deployed     (AOSS collection live)
  - DfmeaKnowledgeBaseStack deployed  (IAM KB role created)
  - pip install opensearch-py
"""
import boto3
import json
import os
import time

from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth

REGION              = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
COLLECTION_NAME     = "dfmea-regulatory"

FAILURE_MODE_INDEX  = "dfmea-failure-mode-index"
REGULATORY_INDEX    = "dfmea-regulatory-index"

FAILURE_MODE_KB     = "dfmea-failure-mode-kb"
REGULATORY_KB       = "dfmea-regulatory-kb"

FAILURE_MODE_DS     = "dfmea-failure-mode-ds"
REGULATORY_DS       = "dfmea-regulatory-ds"

EMBEDDING_MODEL_ARN = (
    f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"
)

# Index body — fields Bedrock KB requires
_INDEX_BODY = {
    "settings": {"index.knn": True},
    "mappings": {
        "properties": {
            "bedrock-knowledge-base-default-vector": {
                "type": "knn_vector",
                "dimension": 1024,
                "method": {
                    "name": "hnsw",
                    "engine": "faiss",
                    "space_type": "l2",
                    "parameters": {"ef_construction": 512, "m": 16},
                },
            },
            "AMAZON_BEDROCK_TEXT_CHUNK": {"type": "text"},
            "AMAZON_BEDROCK_METADATA":   {"type": "text", "index": False},
        }
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cfn_output(stack_name: str, export_name: str) -> str:
    cf = boto3.client("cloudformation", region_name=REGION)
    resp = cf.describe_stacks(StackName=stack_name)
    for o in resp["Stacks"][0].get("Outputs", []):
        if o.get("ExportName") == export_name:
            return o["OutputValue"]
    raise RuntimeError(f"Export '{export_name}' not found in stack '{stack_name}'")


def _get_collection(name: str):
    """Return (host, arn) for the named AOSS collection."""
    aoss = boto3.client("opensearchserverless", region_name=REGION)
    resp = aoss.batch_get_collection(names=[name])
    details = resp.get("collectionDetails", [])
    if not details:
        raise RuntimeError(
            f"Collection '{name}' not found. Deploy DfmeaSearchStack first."
        )
    col    = details[0]
    status = col["status"]
    if status != "ACTIVE":
        raise RuntimeError(
            f"Collection '{name}' is not ACTIVE (status={status}). "
            "Wait for DfmeaSearchStack deploy to complete."
        )
    endpoint = col["collectionEndpoint"]
    host     = endpoint.replace("https://", "")
    return host, col["arn"]


def _opensearch_client(host: str) -> OpenSearch:
    session     = boto3.Session(region_name=REGION)
    credentials = session.get_credentials()
    auth        = AWSV4SignerAuth(credentials, REGION, service="aoss")
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=300,
    )


def _create_index(client: OpenSearch, index_name: str):
    if client.indices.exists(index=index_name):
        print(f"  Index '{index_name}' already exists — skipping.")
        return
    print(f"  Creating index '{index_name}' ...")
    resp = client.indices.create(index=index_name, body=_INDEX_BODY)
    print(f"  Created: {resp}")
    print("  Waiting 30s for index to be ready ...")
    time.sleep(30)


def _wait_for_kb_active(bdr, kb_id: str, kb_name: str) -> str:
    """Wait for a newly created or reused knowledge base to become ACTIVE."""
    for _ in range(30):
        status = bdr.get_knowledge_base(
            knowledgeBaseId=kb_id
        )["knowledgeBase"]["status"]
        if status == "ACTIVE":
            print("  Status: ACTIVE")
            return kb_id
        if status in {"FAILED", "DELETE_UNSUCCESSFUL"}:
            raise RuntimeError(
                f"Knowledge base '{kb_name}' entered terminal status {status}."
            )
        print(f"  Status: {status} ...")
        time.sleep(5)
    raise RuntimeError(
        f"Knowledge base '{kb_name}' did not become ACTIVE within 2.5 min."
    )


def _create_kb(collection_arn: str, kb_role_arn: str, kb_name: str,
               index_name: str, description: str) -> str:
    """Create (or reuse) a Bedrock KB. Returns an ACTIVE kb_id."""
    bdr = boto3.client("bedrock-agent", region_name=REGION)

    for kb in bdr.list_knowledge_bases(maxResults=100).get("knowledgeBaseSummaries", []):
        if kb["name"] == kb_name:
            kb_id = kb["knowledgeBaseId"]
            print(f"  Knowledge base '{kb_name}' already exists: {kb_id}")
            return _wait_for_kb_active(bdr, kb_id, kb_name)

    print(f"  Creating knowledge base '{kb_name}' ...")
    resp = bdr.create_knowledge_base(
        name=kb_name,
        description=description,
        roleArn=kb_role_arn,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": EMBEDDING_MODEL_ARN,
            },
        },
        storageConfiguration={
            "type": "OPENSEARCH_SERVERLESS",
            "opensearchServerlessConfiguration": {
                "collectionArn": collection_arn,
                "vectorIndexName": index_name,
                "fieldMapping": {
                    "vectorField":   "bedrock-knowledge-base-default-vector",
                    "textField":     "AMAZON_BEDROCK_TEXT_CHUNK",
                    "metadataField": "AMAZON_BEDROCK_METADATA",
                },
            },
        },
    )
    kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
    print(f"  Knowledge base created: {kb_id}")
    return _wait_for_kb_active(bdr, kb_id, kb_name)


def _create_data_source(kb_id: str, ds_name: str, bucket_name: str,
                        prefix: str) -> str:
    """Create (or reuse) an S3 data source. Returns ds_id."""
    bdr = boto3.client("bedrock-agent", region_name=REGION)

    for ds in bdr.list_data_sources(knowledgeBaseId=kb_id, maxResults=100).get("dataSourceSummaries", []):
        if ds["name"] == ds_name:
            ds_id = ds["dataSourceId"]
            print(f"  Data source '{ds_name}' already exists: {ds_id}")
            return ds_id

    bucket_arn = f"arn:aws:s3:::{bucket_name}"
    print(f"  Creating data source '{ds_name}' → s3://{bucket_name}/{prefix} ...")
    resp = bdr.create_data_source(
        knowledgeBaseId=kb_id,
        name=ds_name,
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": bucket_arn,
                "inclusionPrefixes": [prefix],
            },
        },
        vectorIngestionConfiguration={
            "chunkingConfiguration": {
                "chunkingStrategy": "FIXED_SIZE",
                "fixedSizeChunkingConfiguration": {
                    "maxTokens": 512,
                    "overlapPercentage": 20,
                },
            },
        },
    )
    ds_id = resp["dataSource"]["dataSourceId"]
    print(f"  Data source created: {ds_id}")
    return ds_id


def _start_and_wait_ingestion(
    kb_id: str,
    ds_id: str,
    timeout_seconds: int = 1800,
) -> None:
    """Start (or join) a KB ingestion job and fail unless it completes."""
    bdr = boto3.client("bedrock-agent", region_name=REGION)
    active_statuses = {"STARTING", "IN_PROGRESS"}

    summaries = bdr.list_ingestion_jobs(
        knowledgeBaseId=kb_id,
        dataSourceId=ds_id,
        maxResults=10,
    ).get("ingestionJobSummaries", [])
    active = next((job for job in summaries if job.get("status") in active_statuses), None)

    if active:
        job_id = active["ingestionJobId"]
        print(f"  Joining active ingestion job {job_id} for KB {kb_id}")
    else:
        job = bdr.start_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            description="DFMEA deployment knowledge-base refresh",
        )["ingestionJob"]
        job_id = job["ingestionJobId"]
        print(f"  Started ingestion job {job_id} for KB {kb_id}")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = bdr.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            ingestionJobId=job_id,
        )["ingestionJob"]
        status = job["status"]
        if status == "COMPLETE":
            stats = job.get("statistics", {})
            print(f"  Ingestion {job_id}: COMPLETE {json.dumps(stats, default=str)}")
            return
        if status in {"FAILED", "STOPPED"}:
            reasons = "; ".join(job.get("failureReasons", [])) or "no reason supplied"
            raise RuntimeError(f"Ingestion {job_id} ended as {status}: {reasons}")
        print(f"  Ingestion {job_id}: {status} ...")
        time.sleep(10)

    raise TimeoutError(
        f"Ingestion {job_id} did not complete within {timeout_seconds} seconds"
    )


def _write_ssm(params: dict):
    ssm = boto3.client("ssm", region_name=REGION)
    for name, value in params.items():
        ssm.put_parameter(
            Name=name, Value=value, Type="String",
            Overwrite=True, Description=f"DFMEA Bedrock KB — {name}",
        )
        print(f"  SSM {name} = {value}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Step 1: Get AOSS collection info ===")
    host, collection_arn = _get_collection(COLLECTION_NAME)
    print(f"  host={host}")
    print(f"  arn={collection_arn}")

    print("\n=== Step 2: Get KB IAM role ARN ===")
    kb_role_arn = _cfn_output("DfmeaKnowledgeBaseStack", "DfmeaKbRoleArn")
    print(f"  KB role: {kb_role_arn}")

    print("\n=== Step 3: Get KB docs S3 bucket ===")
    kb_bucket = _cfn_output("DfmeaKnowledgeBaseStack", "DfmeaKbDocsBucket")
    print(f"  Bucket: {kb_bucket}")

    print("\n=== Step 4: Create AOSS vector indexes ===")
    os_client = _opensearch_client(host)
    _create_index(os_client, FAILURE_MODE_INDEX)
    _create_index(os_client, REGULATORY_INDEX)

    print("\n=== Step 5: Create Bedrock Knowledge Bases ===")
    fm_kb_id = _create_kb(
        collection_arn, kb_role_arn,
        FAILURE_MODE_KB, FAILURE_MODE_INDEX,
        "DFMEA failure mode patterns and historical failure data",
    )
    reg_kb_id = _create_kb(
        collection_arn, kb_role_arn,
        REGULATORY_KB, REGULATORY_INDEX,
        "DFMEA regulatory documents and compliance standards",
    )

    print("\n=== Step 6: Create S3 data sources ===")
    fm_ds_id = _create_data_source(
        fm_kb_id, FAILURE_MODE_DS, kb_bucket, "failure-mode-kb/"
    )
    reg_ds_id = _create_data_source(
        reg_kb_id, REGULATORY_DS, kb_bucket, "regulatory-kb/"
    )

    print("\n=== Step 7: Start and wait for ingestion ===")
    _start_and_wait_ingestion(fm_kb_id, fm_ds_id)
    _start_and_wait_ingestion(reg_kb_id, reg_ds_id)

    print("\n=== Step 8: Write SSM parameters ===")
    _write_ssm({
        "/dfmea/failure_mode_kb_id": fm_kb_id,
        "/dfmea/regulatory_kb_id":   reg_kb_id,
    })

    print(f"\nDone.  Failure-mode KB: {fm_kb_id}  Regulatory KB: {reg_kb_id}")


if __name__ == "__main__":
    main()
