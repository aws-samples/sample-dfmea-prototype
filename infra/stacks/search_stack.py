"""
infra/stacks/search_stack.py — OpenSearch Serverless (AOSS) Search Stack.

Provisions:
  - AOSS collection (VECTORSEARCH) for regulatory document embeddings
  - Encryption security policy (AWS-owned key)
  - Network security policy (public access, tightened post-deploy)
  - Data access policy granting indexer Lambda role CRUD + search access
  - search-indexer Lambda function (S3 event trigger wired post-deploy)
  - Managed IAM policy (aoss_query_policy) for consumers (agent Lambdas)
"""
from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_opensearchserverless as aoss,
    Duration,
    RemovalPolicy,
)
from constructs import Construct
from cdk_nag import NagSuppressions

from .foundation_stack import DfmeaFoundationStack
from .data_stack import DfmeaDataStack


class DfmeaSearchStack(cdk.Stack):
    """AOSS collection + search-indexer Lambda for DFMEA regulatory KB."""

    COLLECTION_NAME = "dfmea-regulatory"

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        foundation: DfmeaFoundationStack,
        data: DfmeaDataStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cmk = foundation.cmk
        vpc = foundation.vpc

        # ── IAM role for the indexer Lambda ───────────────────────────────────
        indexer_role = iam.Role(
            self,
            "SearchIndexerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
            description="Execution role for dfmea-search-indexer Lambda",
        )

        # CloudWatch Logs
        indexer_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/dfmea-*"
                ],
            )
        )

        # S3 read from KB docs bucket
        data.kb_docs_bucket.grant_read(indexer_role)

        # Bedrock for embedding model invocations
        indexer_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )

        # KMS
        cmk.grant_encrypt_decrypt(indexer_role)

        # ── AOSS Encryption Policy (AWS-owned key) ────────────────────────────
        encryption_policy_doc = json.dumps(
            {
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{self.COLLECTION_NAME}"],
                    }
                ],
                "AWSOwnedKey": True,
            }
        )
        aoss_encryption_policy = aoss.CfnSecurityPolicy(
            self,
            "AossEncryptionPolicy",
            name=f"{self.COLLECTION_NAME}-enc",
            type="encryption",
            policy=encryption_policy_doc,
        )

        # ── AOSS Network Policy (public — tightened post-deploy) ──────────────
        network_policy_doc = json.dumps(
            [
                {
                    "Rules": [
                        {
                            "ResourceType": "collection",
                            "Resource": [f"collection/{self.COLLECTION_NAME}"],
                        },
                        {
                            "ResourceType": "dashboard",
                            "Resource": [f"collection/{self.COLLECTION_NAME}"],
                        },
                    ],
                    "AllowFromPublic": True,
                }
            ]
        )
        aoss_network_policy = aoss.CfnSecurityPolicy(
            self,
            "AossNetworkPolicy",
            name=f"{self.COLLECTION_NAME}-net",
            type="network",
            policy=network_policy_doc,
        )

        # ── AOSS Collection ───────────────────────────────────────────────────
        self.collection = aoss.CfnCollection(
            self,
            "AossCollection",
            name=self.COLLECTION_NAME,
            type="VECTORSEARCH",
            description="DFMEA regulatory document vector search collection",
        )
        # Collection requires both policies to exist first
        self.collection.add_dependency(aoss_encryption_policy)
        self.collection.add_dependency(aoss_network_policy)

        # Convenience aliases for the collection ARN and endpoint
        collection_arn = self.collection.attr_arn
        collection_endpoint = self.collection.attr_collection_endpoint

        # ── AOSS Data Access Policy ───────────────────────────────────────────
        data_access_policy_doc = json.dumps(
            [
                {
                    "Rules": [
                        {
                            "ResourceType": "index",
                            "Resource": [
                                f"index/{self.COLLECTION_NAME}/*"
                            ],
                            "Permission": [
                                "aoss:CreateIndex",
                                "aoss:DeleteIndex",
                                "aoss:UpdateIndex",
                                "aoss:DescribeIndex",
                                "aoss:ReadDocument",
                                "aoss:WriteDocument",
                            ],
                        },
                        {
                            "ResourceType": "collection",
                            "Resource": [f"collection/{self.COLLECTION_NAME}"],
                            "Permission": [
                                "aoss:CreateCollectionItems",
                                "aoss:DeleteCollectionItems",
                                "aoss:UpdateCollectionItems",
                                "aoss:DescribeCollectionItems",
                            ],
                        },
                    ],
                    # NOTE: CfnAccessPolicy does NOT evaluate CloudFormation
                    # intrinsics in Policy — use plain string ARNs only.
                    # Account root covers the deploy-script runner and Bedrock KB
                    # service (which also needs the IAM aoss:APIAccessAll grant).
                    "Principal": [
                        indexer_role.role_arn,
                        f"arn:aws:iam::{self.account}:root",
                    ],
                    "Description": "dfmea-search-indexer data access",
                }
            ]
        )
        aoss.CfnAccessPolicy(
            self,
            "AossDataAccessPolicy",
            name=f"{self.COLLECTION_NAME}-data",
            type="data",
            policy=data_access_policy_doc,
        )

        # ── Search Indexer Lambda ─────────────────────────────────────────────
        private_subnets = cdk.aws_ec2.SubnetSelection(
            subnet_type=cdk.aws_ec2.SubnetType.PRIVATE_WITH_EGRESS
        )

        self.indexer_fn = lambda_.Function(
            self,
            "SearchIndexerFn",
            function_name="dfmea-search-indexer",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambdas/search_indexer/handler.handler",
            code=lambda_.Code.from_asset("..", exclude=["cdk.out", ".venv", "**/__pycache__", "**/*.pyc", "ui/node_modules", "ui/dist", ".git", "**/.pytest_cache", "**/*.egg-info"]),  # project root
            role=indexer_role,
            environment={
                "AOSS_ENDPOINT": collection_endpoint,
                "AOSS_INDEX": "regulatory-embeddings",
                "KB_DOCS_BUCKET": data.kb_docs_bucket.bucket_name,
                "POWERTOOLS_SERVICE_NAME": "dfmea-search-indexer",
                "LOG_LEVEL": "INFO",
            },
            timeout=Duration.seconds(900),
            memory_size=1024,
            vpc=vpc,
            vpc_subnets=private_subnets,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment_encryption=cmk,
            tracing=lambda_.Tracing.ACTIVE,
        )

        NagSuppressions.add_resource_suppressions(
            self.indexer_fn,
            [{"id": "AwsSolutions-L1", "reason": "Python 3.12 is the latest available runtime"}],
        )

        # ── Managed policy for AOSS query consumers ───────────────────────────
        self.aoss_query_policy = iam.ManagedPolicy(
            self,
            "AossQueryPolicy",
            managed_policy_name="dfmea-aoss-query-policy",
            description="Allow AOSS APIAccessAll on dfmea-regulatory collection",
            statements=[
                iam.PolicyStatement(
                    actions=["aoss:APIAccessAll"],
                    resources=[collection_arn],
                )
            ],
        )

        # ── NAG suppressions on indexer role ─────────────────────────────────
        NagSuppressions.add_resource_suppressions(
            indexer_role,
            [
                {"id": "AwsSolutions-IAM5", "reason": "Bedrock InvokeModel requires wildcard resource; logs wildcard scoped to /aws/lambda/dfmea-*"},
                {"id": "AwsSolutions-IAM4", "reason": "AWSLambdaVPCAccessExecutionRole required for VPC-enabled Lambda",
                 "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"]},
            ],
            apply_to_children=True,
        )

        # Suppress CDK-generated LogRetention Lambda role errors
        NagSuppressions.add_stack_suppressions(
            self,
            [
                {"id": "AwsSolutions-IAM4", "reason": "CDK-generated LogRetention Lambda uses AWSLambdaBasicExecutionRole; cannot be customised",
                 "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"]},
                {"id": "AwsSolutions-IAM5", "reason": "CDK-generated LogRetention Lambda DefaultPolicy requires wildcard permissions; cannot be restricted"},
            ],
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "AossCollectionEndpoint",
            value=collection_endpoint,
            export_name="DfmeaAossCollectionEndpoint",
        )
        cdk.CfnOutput(
            self,
            "AossCollectionArn",
            value=collection_arn,
            export_name="DfmeaAossCollectionArn",
        )
