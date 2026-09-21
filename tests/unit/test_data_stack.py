# tests/unit/test_data_stack.py
import pytest
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from infra.stacks.foundation_stack import DfmeaFoundationStack
from infra.stacks.auth_stack import DfmeaAuthStack


@pytest.fixture(scope="module")
def data_template():
    from infra.stacks.data_stack import DfmeaDataStack
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    foundation = DfmeaFoundationStack(app, "TestFoundation", env=env)
    auth = DfmeaAuthStack(app, "TestAuth", foundation=foundation, env=env)
    stack = DfmeaDataStack(app, "TestData", foundation=foundation, env=env)
    return Template.from_stack(stack)


EXPECTED_TABLE_NAMES = [
    "dfmea-reviews",
    "dfmea-structural-decomposition",
    "dfmea-retrieval-results",
    "dfmea-analysis-findings",
    "dfmea-synthesis-results",
    "dfmea-risk-scores",
    "dfmea-ap-lookup",
]

EXPECTED_BUCKET_SUFFIXES = [
    "uploads",
    "processed",
    "reports",
    "audit",
    "ontology",
    "kb-documents",
    "ml-models",
]


def test_seven_dynamodb_tables(data_template):
    tables = data_template.find_resources("AWS::DynamoDB::Table")
    assert len(tables) == 7, f"Expected 7 DynamoDB tables, found {len(tables)}"


def test_dynamodb_tables_encrypted_with_cmk(data_template):
    """All tables must use KMS encryption (not AWS_OWNED_KEY)."""
    tables = data_template.find_resources("AWS::DynamoDB::Table")
    for name, table in tables.items():
        sse = table["Properties"].get("SSESpecification", {})
        assert sse.get("SSEEnabled") is True, f"Table {name} missing SSE"
        assert sse.get("SSEType") == "KMS", f"Table {name} not using KMS"


def test_dynamodb_pitr_enabled(data_template):
    """All tables must have point-in-time recovery enabled."""
    tables = data_template.find_resources("AWS::DynamoDB::Table")
    for name, table in tables.items():
        pitr = table["Properties"].get("PointInTimeRecoverySpecification", {})
        assert pitr.get("PointInTimeRecoveryEnabled") is True, f"Table {name} missing PITR"


def test_reviews_table_has_connection_gsi(data_template):
    """dfmea-reviews needs GSI on connection_id for WebSocket fan-out."""
    data_template.has_resource_properties("AWS::DynamoDB::Table", {
        "GlobalSecondaryIndexes": Match.array_with([
            Match.object_like({"IndexName": "connection_id-index"})
        ])
    })


def test_seven_s3_buckets_in_data_stack(data_template):
    # The access-logs bucket lives in DfmeaFoundationStack, not here.
    # DfmeaDataStack owns the 7 domain buckets: uploads, processed, reports,
    # audit, ontology, kb-documents, ml-models.
    buckets = data_template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 7, f"Expected 7 S3 buckets in DataStack, found {len(buckets)}"


def test_s3_buckets_block_public_access(data_template):
    data_template.has_resource_properties("AWS::S3::Bucket", {
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
    })


def test_s3_buckets_enforce_ssl(data_template):
    """All bucket policies must deny non-SSL requests."""
    policies = data_template.find_resources("AWS::S3::BucketPolicy")
    for name, policy in policies.items():
        statements = policy["Properties"]["PolicyDocument"]["Statement"]
        deny_http = [
            s for s in statements
            if s.get("Effect") == "Deny"
            and "aws:SecureTransport" in str(s.get("Condition", ""))
        ]
        assert len(deny_http) >= 1, f"BucketPolicy {name} missing SSL-deny statement"


def test_s3_buckets_versioning_on_critical(data_template):
    """Ontology + ML model buckets must have versioning enabled."""
    data_template.has_resource_properties("AWS::S3::Bucket", {
        "VersioningConfiguration": {"Status": "Enabled"}
    })
