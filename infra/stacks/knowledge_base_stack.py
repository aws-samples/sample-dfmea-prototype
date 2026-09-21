"""
infra/stacks/knowledge_base_stack.py — Bedrock Knowledge Base IAM Stack.

Provisions:
  - IAM role for the Bedrock KB service principal (fixed name: dfmea-bedrock-kb-role)

The AOSS vector indexes, Bedrock Knowledge Bases, and S3 data sources are
created by scripts/create_bedrock_kb.py (run automatically by deploy.sh after
this stack deploys) using opensearch-py with AWSV4SignerAuth — the same
pattern proven in the paint-shop-prediction reference project.

The AOSS data access policy lives in DfmeaSearchStack (dfmea-regulatory-data)
and uses arn:aws:iam::{account}:root as a principal so the deploy script
caller can create indexes without any Lambda Custom Resource.
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from constructs import Construct
from cdk_nag import NagSuppressions

from .foundation_stack import DfmeaFoundationStack
from .data_stack import DfmeaDataStack
from .search_stack import DfmeaSearchStack

_EMBEDDING_MODEL_ARN = (
    "arn:aws:bedrock:{region}::foundation-model/amazon.titan-embed-text-v2:0"
)


class DfmeaKnowledgeBaseStack(cdk.Stack):
    """IAM role for DFMEA Bedrock Knowledge Bases (indexes/KBs created by script)."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        foundation: DfmeaFoundationStack,
        data: DfmeaDataStack,
        search: DfmeaSearchStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        collection_arn      = search.collection.attr_arn
        embedding_model_arn = _EMBEDDING_MODEL_ARN.format(region=self.region)

        # ── IAM role trusted by Bedrock KB service ────────────────────────────
        # Fixed name so scripts/create_bedrock_kb.py can look it up by name.
        self.kb_role = iam.Role(
            self,
            "BedrockKbRole",
            role_name="dfmea-bedrock-kb-role",
            assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock:{self.region}:{self.account}:knowledge-base/*"
                    },
                },
            ),
            description="Execution role for DFMEA Bedrock Knowledge Bases",
        )

        # AOSS data plane access
        self.kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["aoss:APIAccessAll"],
                resources=[collection_arn],
            )
        )

        # S3 read for KB source documents
        data.kb_docs_bucket.grant_read(self.kb_role)

        # Bedrock embedding model
        self.kb_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[embedding_model_arn],
            )
        )

        # KMS decrypt for encrypted S3 objects
        foundation.cmk.grant_decrypt(self.kb_role)

        # ── NAG suppressions ─────────────────────────────────────────────────
        NagSuppressions.add_resource_suppressions(
            self.kb_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "S3 grant_read adds wildcard object key; AOSS APIAccessAll requires collection-level resource",
                },
            ],
            apply_to_children=True,
        )

        # ── Outputs consumed by scripts/create_bedrock_kb.py ─────────────────
        cdk.CfnOutput(
            self,
            "KbRoleArn",
            value=self.kb_role.role_arn,
            export_name="DfmeaKbRoleArn",
        )
        cdk.CfnOutput(
            self,
            "KbDocsbucket",
            value=data.kb_docs_bucket.bucket_name,
            export_name="DfmeaKbDocsBucket",
        )
