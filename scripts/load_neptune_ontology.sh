#!/bin/bash
# dfmea-prototype/scripts/load_neptune_ontology.sh
# Upload ontology JSON-LD to S3, then trigger Neptune bulk loader.
# Usage: NEPTUNE_ENDPOINT=xxx NEPTUNE_LOADER_ROLE=arn:... S3_BUCKET=dfmea-ontology-... bash load_neptune_ontology.sh

set -euo pipefail

NEPTUNE_ENDPOINT="${NEPTUNE_ENDPOINT:?must set NEPTUNE_ENDPOINT}"
NEPTUNE_LOADER_ROLE="${NEPTUNE_LOADER_ROLE:?must set NEPTUNE_LOADER_ROLE}"
S3_BUCKET="${S3_BUCKET:?must set S3_BUCKET}"
ONTOLOGY_FILE="${ONTOLOGY_FILE:-sample-data/ontology/ontology.json-ld}"
S3_KEY="ontology/ontology.json-ld"

# Upload to S3
echo "Uploading $ONTOLOGY_FILE to s3://$S3_BUCKET/$S3_KEY..."
aws s3 cp "$ONTOLOGY_FILE" "s3://$S3_BUCKET/$S3_KEY" --content-type "application/ld+json"

# Trigger Neptune loader (requires VPC access — skipped gracefully if unreachable)
echo "Triggering Neptune bulk loader..."
LOADER_RESPONSE=$(curl -s --connect-timeout 10 -X POST \
  "https://${NEPTUNE_ENDPOINT}:8182/loader" \
  -H "Content-Type: application/json" \
  -d "{
    \"source\": \"s3://${S3_BUCKET}/${S3_KEY}\",
    \"format\": \"json-ld\",
    \"iamRoleArn\": \"${NEPTUNE_LOADER_ROLE}\",
    \"region\": \"${AWS_DEFAULT_REGION:-us-east-1}\",
    \"failOnError\": \"FALSE\",
    \"parallelism\": \"MEDIUM\"
  }" 2>/dev/null || true)

if [[ -z "$LOADER_RESPONSE" ]]; then
  echo "WARNING: Neptune endpoint unreachable from outside VPC."
  echo "  The ontology file has been uploaded to s3://${S3_BUCKET}/${S3_KEY}"
  echo "  Trigger the loader manually from a host inside the VPC:"
  echo "  curl -X POST https://${NEPTUNE_ENDPOINT}:8182/loader -H 'Content-Type: application/json' \\"
  echo "    -d '{\"source\":\"s3://${S3_BUCKET}/${S3_KEY}\",\"format\":\"json-ld\",\"iamRoleArn\":\"${NEPTUNE_LOADER_ROLE}\",\"region\":\"${AWS_DEFAULT_REGION:-us-east-1}\",\"failOnError\":\"FALSE\"}'"
else
  echo "$LOADER_RESPONSE" | python3 -m json.tool || echo "$LOADER_RESPONSE"
  echo "Done. Poll GET https://${NEPTUNE_ENDPOINT}:8182/loader/<loadId> for status."
fi
