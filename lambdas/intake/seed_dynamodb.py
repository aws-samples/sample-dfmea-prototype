#!/usr/bin/env python3
"""
seed_dynamodb.py — Post-deployment seeder.

Run ONCE after `cdk deploy` to:
  1. Batch-write all 1000 AP lookup records to DynamoDB table `dfmea-ap-lookup`
  2. Upload ontology/ontology.json-ld  -> s3://dfmea-ontology-<account>/v0.1.0/ontology.json-ld
  3. Upload ontology/shapes.ttl        -> s3://dfmea-ontology-<account>/v0.1.0/shapes.ttl
  4. Upload ontology/ap_lookup_seed.json -> s3://dfmea-ontology-<account>/v0.1.0/ap_lookup_seed.json

Usage (run from dfmea-prototype/):
    AWS_DEFAULT_REGION=us-east-1 python3 lambdas/intake/seed_dynamodb.py

Environment variables:
    AWS_DEFAULT_REGION     (default: us-east-1)
    AWS_ACCOUNT_ID         (auto-detected via STS if not set)
    DFMEA_TABLE_PREFIX     (default: dfmea)
    DRY_RUN=1              Print what would be done without making AWS calls
"""
from __future__ import annotations
import json, os, sys

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
AP_SEED_PATH   = os.path.join(REPO_ROOT, "ontology", "ap_lookup_seed.json")
ONTOLOGY_PATH  = os.path.join(REPO_ROOT, "ontology", "ontology.json-ld")
SHAPES_PATH    = os.path.join(REPO_ROOT, "ontology", "shapes.ttl")

REGION         = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
TABLE_PREFIX   = os.environ.get("DFMEA_TABLE_PREFIX", "dfmea")
DRY_RUN        = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
BATCH_SIZE     = 25   # DynamoDB BatchWriteItem max


def get_account_id() -> str:
    acct = os.environ.get("AWS_ACCOUNT_ID", "")
    if acct:
        return acct
    import boto3
    return boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]


def seed_ap_lookup_table(account_id: str) -> None:
    table_name = f"{TABLE_PREFIX}-ap-lookup"
    print(f"\n[1/4] Seeding AP lookup table: {table_name}")

    with open(AP_SEED_PATH) as f:
        records = json.load(f)
    print(f"  Records to write: {len(records)}")

    if DRY_RUN:
        print(f"  DRY RUN — would batch-write {len(records)} items to {table_name}")
        return

    import boto3
    from boto3.dynamodb.conditions import Key
    dynamo = boto3.resource("dynamodb", region_name=REGION)
    table = dynamo.Table(table_name)

    written = 0
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        with table.batch_writer() as writer:
            for rec in batch:
                writer.put_item(Item={
                    "severity":            str(rec["severity"]),
                    "occurrence#detection": f"{rec['occurrence']}#{rec['detection']}",
                    "occurrence":          rec["occurrence"],
                    "detection":           rec["detection"],
                    "ap":                  rec["ap"],
                })
        written += len(batch)
        print(f"  Written {written}/{len(records)}...", end="\r")

    print(f"\n  Done — {written} records seeded.")


def upload_ontology_files(account_id: str) -> None:
    bucket = f"{TABLE_PREFIX}-ontology-{account_id}"
    files = [
        (ONTOLOGY_PATH, "v0.1.0/ontology.json-ld", "application/ld+json"),
        (SHAPES_PATH,   "v0.1.0/shapes.ttl",        "text/turtle"),
        (AP_SEED_PATH,  "v0.1.0/ap_lookup_seed.json","application/json"),
    ]

    print(f"\n[2/4] Uploading ontology files to s3://{bucket}/")
    for local_path, s3_key, content_type in files:
        if DRY_RUN:
            print(f"  DRY RUN — would upload {os.path.basename(local_path)} -> s3://{bucket}/{s3_key}")
            continue
        import boto3
        s3 = boto3.client("s3", region_name=REGION)
        with open(local_path, "rb") as fh:
            s3.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=fh.read(),
                ContentType=content_type,
                ServerSideEncryption="aws:kms",
            )
        print(f"  Uploaded -> s3://{bucket}/{s3_key}")

    print("  Ontology upload complete.")


def verify_seed(account_id: str) -> None:
    table_name = f"{TABLE_PREFIX}-ap-lookup"
    print(f"\n[3/4] Verifying AP lookup table item count...")

    if DRY_RUN:
        print("  DRY RUN — skipping verification")
        return

    import boto3
    dynamo = boto3.client("dynamodb", region_name=REGION)
    resp = dynamo.describe_table(TableName=table_name)
    item_count = resp["Table"].get("ItemCount", "unknown")
    print(f"  Table item count (approximate): {item_count}")
    if isinstance(item_count, int) and item_count < 900:
        print("  WARNING: item count looks low — seed may not have completed")
    else:
        print("  OK")


def print_summary(account_id: str) -> None:
    print(f"\n[4/4] Seed summary")
    print(f"  Region:    {REGION}")
    print(f"  Account:   {account_id}")
    print(f"  AP table:  {TABLE_PREFIX}-ap-lookup (1000 records)")
    print(f"  S3 bucket: {TABLE_PREFIX}-ontology-{account_id}")
    print("  Files:     v0.1.0/ontology.json-ld, v0.1.0/shapes.ttl, v0.1.0/ap_lookup_seed.json")
    print("\nSeeding complete.")


def main() -> None:
    print("DFMEA DynamoDB + S3 Seeder")
    print(f"  Region:  {REGION}")
    print(f"  Dry run: {DRY_RUN}")

    try:
        account_id = get_account_id()
        print(f"  Account: {account_id}")
    except Exception as e:
        print(f"  ERROR getting account ID: {e}")
        print("  Set AWS_ACCOUNT_ID env var or ensure AWS credentials are configured.")
        sys.exit(1)

    seed_ap_lookup_table(account_id)
    upload_ontology_files(account_id)
    verify_seed(account_id)
    print_summary(account_id)


if __name__ == "__main__":
    main()
