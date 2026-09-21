# infra/stacks/data_stack.py
from __future__ import annotations
import aws_cdk as cdk
from aws_cdk import (
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    RemovalPolicy,
)
from constructs import Construct
from .foundation_stack import DfmeaFoundationStack


class DfmeaDataStack(cdk.Stack):
    """8 DynamoDB tables + 7 S3 buckets. All encrypted with foundation CMK."""

    def __init__(self, scope: Construct, construct_id: str, foundation: DfmeaFoundationStack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cmk = foundation.cmk

        # ── Helper: standard table defaults ──────────────────────────────────
        def _table(logical_id: str, table_name: str, pk: str, sk: str | None = None, **extra) -> dynamodb.Table:
            sk_attr = dynamodb.Attribute(name=sk, type=dynamodb.AttributeType.STRING) if sk else None
            return dynamodb.Table(
                self, logical_id,
                table_name=table_name,
                partition_key=dynamodb.Attribute(name=pk, type=dynamodb.AttributeType.STRING),
                sort_key=sk_attr,
                encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
                encryption_key=cmk,
                point_in_time_recovery=True,
                billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
                removal_policy=RemovalPolicy.DESTROY,
                **extra,
            )

        # ── DynamoDB Tables ───────────────────────────────────────────────────

        # 1. Reviews — lifecycle state + WebSocket GSI + DynamoDB Stream for fan-out
        reviews = _table("ReviewsTable", "dfmea-reviews", pk="review_id",
                         stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES)
        reviews.add_global_secondary_index(
            index_name="connection_id-index",
            partition_key=dynamodb.Attribute(name="connection_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="review_id", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,
        )
        self.reviews_table = reviews

        # 2. Structural Decomposition
        self.structural_table = _table("StructuralTable", "dfmea-structural-decomposition", pk="review_id", sk="component_id")

        # 3. Retrieval Results
        self.retrieval_table = _table("RetrievalTable", "dfmea-retrieval-results", pk="review_id", sk="evidence_id")

        # 4. Analysis Findings
        self.findings_table = _table("FindingsTable", "dfmea-analysis-findings", pk="review_id", sk="finding_id")

        # 5. Synthesis Results
        self.synthesis_table = _table("SynthesisTable", "dfmea-synthesis-results", pk="review_id", sk="finding_id")

        # 6. Risk Scores
        self.risk_scores_table = _table("RiskScoresTable", "dfmea-risk-scores", pk="review_id", sk="finding_id")

        # 7. AP Lookup (deterministic AIAG-VDA 2019 lookup)
        self.ap_lookup_table = _table("ApLookupTable", "dfmea-ap-lookup", pk="severity", sk="occurrence#detection")

        # 8. Assistant conversations and messages, isolated by Cognito subject.
        #    Items expire after the configurable application retention period.
        self.assistant_table = _table(
            "AssistantTable",
            "dfmea-assistant",
            pk="user_sub",
            sk="entity_key",
            time_to_live_attribute="expires_at",
        )

        # ── Helper: standard bucket defaults ─────────────────────────────────
        def _bucket(logical_id: str, suffix: str, *, versioned: bool = False, retention_days: int | None = None) -> s3.Bucket:
            lifecycle = []
            if retention_days:
                lifecycle.append(s3.LifecycleRule(id=f"expire-{retention_days}d", expiration=cdk.Duration.days(retention_days), enabled=True))
            return s3.Bucket(
                self, logical_id,
                bucket_name=f"dfmea-{suffix}-{self.account}",
                encryption=s3.BucketEncryption.KMS,
                encryption_key=cmk,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                enforce_ssl=True,
                versioned=versioned,
                server_access_logs_bucket=foundation.access_log_bucket,
                server_access_logs_prefix=f"dfmea-{suffix}/",
                removal_policy=RemovalPolicy.DESTROY,
                auto_delete_objects=True,
                lifecycle_rules=lifecycle,
            )

        # ── S3 Buckets ────────────────────────────────────────────────────────
        # Uploads bucket uses S3-managed encryption (AES256) so presigned PUT URLs
        # work from the browser without requiring KMS credentials in the request.
        self.uploads_bucket = s3.Bucket(
            self, "UploadsBucket",
            bucket_name=f"dfmea-uploads-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            server_access_logs_bucket=foundation.access_log_bucket,
            server_access_logs_prefix="dfmea-uploads/",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[s3.LifecycleRule(id="expire-90d", expiration=cdk.Duration.days(90), enabled=True)],
            cors=[s3.CorsRule(
                allowed_methods=[s3.HttpMethods.PUT],
                allowed_origins=["*"],
                allowed_headers=["*"],
                max_age=3000,
            )],
        )
        self.processed_bucket = _bucket("ProcessedBucket", "processed",    retention_days=365)
        self.reports_bucket   = _bucket("ReportsBucket",   "reports",      retention_days=2555)   # 7 years
        self.audit_bucket     = _bucket("AuditBucket",     "audit",        retention_days=2555)   # 7 years
        self.ontology_bucket  = _bucket("OntologyBucket",  "ontology",     versioned=True)
        self.kb_docs_bucket   = _bucket("KbDocsBucket",    "kb-documents", retention_days=365)
        self.ml_models_bucket = _bucket("MlModelsBucket",  "ml-models",    versioned=True)

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "ReviewsTableName",  value=self.reviews_table.table_name,   export_name="DfmeaReviewsTable")
        cdk.CfnOutput(self, "ApLookupTableName", value=self.ap_lookup_table.table_name, export_name="DfmeaApLookupTable")
        cdk.CfnOutput(self, "AssistantTableName", value=self.assistant_table.table_name, export_name="DfmeaAssistantTable")
        cdk.CfnOutput(self, "UploadsBucketName", value=self.uploads_bucket.bucket_name, export_name="DfmeaUploadsBucket")
        cdk.CfnOutput(self, "ReportsBucketName", value=self.reports_bucket.bucket_name, export_name="DfmeaReportsBucket")
        cdk.CfnOutput(self, "ProcessedBucketName", value=self.processed_bucket.bucket_name)
        cdk.CfnOutput(self, "KbDocsBucketName",    value=self.kb_docs_bucket.bucket_name)
        cdk.CfnOutput(self, "OntologyBucketName",  value=self.ontology_bucket.bucket_name)
        cdk.CfnOutput(self, "MlModelsBucketName",  value=self.ml_models_bucket.bucket_name)
