"""
infra/stacks/agentcore_stack.py — Amazon Bedrock AgentCore Stack.

Provisions:
  - dfmea-mcp-tools Lambda (VPC private subnets) — MCP tool backend
  - AgentCore Runtime agents (5) via CfnResource
  - AgentCore Gateway via CfnResource
  - IAM role for mcp-tools Lambda
  - Exports AgentCore agent IDs for thin invoker Lambdas
"""
from __future__ import annotations
import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_ec2 as ec2,
    aws_s3 as s3,
    Duration,
    RemovalPolicy,
)
from constructs import Construct
from cdk_nag import NagSuppressions

from .foundation_stack import DfmeaFoundationStack
from .data_stack import DfmeaDataStack
from .auth_stack import DfmeaAuthStack


class DfmeaAgentCoreStack(cdk.Stack):
    """AgentCore Runtime agents, Gateway, and MCP tools Lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        foundation: DfmeaFoundationStack,
        data: DfmeaDataStack,
        auth: DfmeaAuthStack,
        neptune=None,  # DfmeaNeptuneStack | None
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cmk = foundation.cmk
        vpc = foundation.vpc
        private_subnets = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)

        # ── Assistant AgentCore Runtime prerequisites ──────────────────────────
        # The global DFMEA assistant is a pure AgentCore Runtime deployed by
        # scripts/deploy_assistant_runtime.py (bedrock-agentcore-control
        # create_agent_runtime, S3 code artifact). It needs a versioned code
        # bucket + a least-privilege execution role that AgentCore assumes.
        self.assistant_code_bucket = s3.Bucket(
            self,
            "AssistantCodeBucket",
            bucket_name=f"dfmea-agentcore-code-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=cmk,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                    noncurrent_version_expiration=Duration.days(30),
                )
            ],
        )

        assistant_runtime_principal = iam.ServicePrincipal(
            "bedrock-agentcore.amazonaws.com",
            conditions={
                "StringEquals": {"aws:SourceAccount": self.account},
                "ArnLike": {
                    "aws:SourceArn": (
                        f"arn:{self.partition}:bedrock-agentcore:"
                        f"{self.region}:{self.account}:*"
                    )
                },
            },
        )
        self.assistant_runtime_role = iam.Role(
            self,
            "AssistantRuntimeRole",
            role_name="dfmea-assistant-runtime-role",
            assumed_by=assistant_runtime_principal,
            description="Least-privilege execution role for the DFMEA AgentCore assistant Runtime",
        )
        self.assistant_code_bucket.grant_read(self.assistant_runtime_role)
        # Use an identity-policy statement for KMS decrypt (NOT cmk.grant_decrypt),
        # which would mutate the Foundation CMK key policy to reference this role
        # and create a Foundation<->AgentCore cyclic reference. Same cycle-safe
        # pattern as the AgentCore runtime role below.
        self.assistant_runtime_role.add_to_policy(iam.PolicyStatement(
            sid="DecryptWithFoundationKey",
            actions=["kms:Decrypt", "kms:DescribeKey"],
            resources=[cmk.key_arn],
        ))

        self.assistant_runtime_role.add_to_policy(iam.PolicyStatement(
            sid="ReadLiveDfmeaData",
            actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan", "dynamodb:DescribeTable"],
            resources=[data.reviews_table.table_arn, data.findings_table.table_arn],
        ))
        self.assistant_runtime_role.add_to_policy(iam.PolicyStatement(
            sid="InvokeAssistantModel",
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                f"arn:{self.partition}:bedrock:*::foundation-model/*",
                f"arn:{self.partition}:bedrock:{self.region}:{self.account}:inference-profile/*",
            ],
        ))
        # KB IDs are created post-CDK (scripts/create_bedrock_kb.py) and differ per
        # account, so scope retrieval to any knowledge base in this account/region.
        self.assistant_runtime_role.add_to_policy(iam.PolicyStatement(
            sid="RetrieveVerifiedKnowledge",
            actions=["bedrock:Retrieve"],
            resources=[
                f"arn:{self.partition}:bedrock:{self.region}:{self.account}:knowledge-base/*",
            ],
        ))
        self.assistant_runtime_role.add_to_policy(iam.PolicyStatement(
            sid="UseAssistantMemory",
            actions=[
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:RetrieveMemoryRecords",
            ],
            resources=[
                f"arn:{self.partition}:bedrock-agentcore:{self.region}:{self.account}:memory/*"
            ],
        ))
        self.assistant_runtime_role.add_to_policy(iam.PolicyStatement(
            sid="WriteRuntimeLogs",
            actions=[
                "logs:CreateLogGroup",
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
                "logs:PutResourcePolicy",
            ],
            resources=[
                f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*",
                f"arn:{self.partition}:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
            ],
        ))
        self.assistant_runtime_role.add_to_policy(iam.PolicyStatement(
            sid="PublishRuntimeTelemetry",
            actions=[
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
                "xray:GetSamplingRules",
                "xray:GetSamplingTargets",
            ],
            resources=["*"],
        ))
        self.assistant_runtime_role.add_to_policy(iam.PolicyStatement(
            sid="PublishRuntimeMetrics",
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
            conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
        ))

        NagSuppressions.add_resource_suppressions(
            self.assistant_code_bucket,
            [{
                "id": "AwsSolutions-S1",
                "reason": "Versioned deployment artifact bucket; Runtime access is audited in AgentCore and CloudTrail.",
            }],
        )
        NagSuppressions.add_resource_suppressions(
            self.assistant_runtime_role,
            [{
                "id": "AwsSolutions-IAM5",
                "reason": (
                    "AgentCore log streams, model variants, knowledge bases, and the "
                    "tagged Memory resource require scoped wildcards; actions remain "
                    "read-only except Memory events."
                ),
            }],
            apply_to_children=True,
        )

        # ── IAM role for dfmea-mcp-tools Lambda ────────────────────────────────
        mcp_tools_role = iam.Role(
            self, "McpToolsRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
            description="Execution role for dfmea-mcp-tools Lambda",
        )

        mcp_tools_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/dfmea-*"],
        ))
        mcp_tools_role.add_to_policy(iam.PolicyStatement(
            actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
            resources=["*"],
        ))

        # DynamoDB read/write (all tables needed by tools)
        for table in [
            data.findings_table,
            data.reviews_table,
            data.retrieval_table,
            data.ap_lookup_table,
            data.structural_table,
            data.risk_scores_table,
        ]:
            table.grant_read_write_data(mcp_tools_role)

        # S3 access (processed + KB docs)
        for bucket in [data.processed_bucket, data.ontology_bucket, data.ml_models_bucket]:
            bucket.grant_read(mcp_tools_role)

        # Bedrock KB retrieval
        mcp_tools_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:Retrieve", "bedrock:RetrieveAndGenerate"],
            resources=["*"],
        ))

        # Ontology queries execute in this Lambda, not in the thin invokers.
        if neptune is not None:
            mcp_tools_role.add_to_policy(iam.PolicyStatement(
                actions=["neptune-db:connect", "neptune-db:ReadDataViaQuery"],
                resources=[
                    f"arn:{self.partition}:neptune-db:{self.region}:{self.account}:"
                    f"{neptune.cluster.attr_cluster_resource_id}/*"
                ],
            ))

        # KMS
        cmk.grant_encrypt_decrypt(mcp_tools_role)

        # Suppress CDK NAG wildcard
        NagSuppressions.add_resource_suppressions(
            mcp_tools_role,
            [
                {"id": "AwsSolutions-IAM5", "reason": "Bedrock retrieval, X-Ray telemetry, Neptune cluster queries, and Lambda logs require service-scoped wildcards"},
                {"id": "AwsSolutions-IAM4", "reason": "AWSLambdaVPCAccessExecutionRole required for VPC Lambda",
                 "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"]},
            ],
            apply_to_children=True,
        )

        # ── dfmea-mcp-tools Lambda ──────────────────────────────────────────────
        self.mcp_tools_fn = lambda_.Function(
            self, "McpToolsFn",
            function_name="dfmea-mcp-tools",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambdas/agentcore_tools/handler.handler",
            code=lambda_.Code.from_asset("..", exclude=[
                "cdk.out", ".venv", "**/__pycache__", "**/*.pyc",
                "ui/node_modules", "ui/dist", ".git",
                "**/.pytest_cache", "**/*.egg-info",
            ]),
            role=mcp_tools_role,
            environment={
                "FINDINGS_TABLE_NAME":    data.findings_table.table_name,
                "REVIEWS_TABLE_NAME":     data.reviews_table.table_name,
                "PROCESSED_BUCKET_NAME":  data.processed_bucket.bucket_name,
                "ML_MODELS_BUCKET_NAME":  data.ml_models_bucket.bucket_name,
                "RISK_SCORES_TABLE_NAME": data.risk_scores_table.table_name,
                "AP_LOOKUP_TABLE_NAME":   data.ap_lookup_table.table_name,
                **({
                    "NEPTUNE_ENDPOINT": neptune.cluster.attr_endpoint,
                    "NEPTUNE_PORT": "8182",
                } if neptune is not None else {}),
                # FAILURE_MODE_KB_ID / REGULATORY_KB_ID are patched onto this
                # Lambda at runtime by scripts/deploy.sh (step 8h), because the
                # Bedrock Knowledge Bases are created post-CDK by
                # scripts/create_bedrock_kb.py and their IDs land in SSM
                # (/dfmea/failure_mode_kb_id, /dfmea/regulatory_kb_id) only then.
            },
            timeout=Duration.seconds(300),
            memory_size=1024,
            vpc=vpc,
            vpc_subnets=private_subnets,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment_encryption=cmk,
            tracing=lambda_.Tracing.ACTIVE,
        )

        NagSuppressions.add_resource_suppressions(
            self.mcp_tools_fn,
            [{"id": "AwsSolutions-L1", "reason": "Python 3.12 is the latest available runtime"}],
        )

        # ── AgentCore Runtime execution role ───────────────────────────────────
        # Passed to bedrock_agentcore_starter_toolkit Runtime().configure(execution_role=...)
        # in scripts/deploy_agentcore_runtimes.py. Trusted by the AgentCore service;
        # grants model access, memory, the M2M secret (for Gateway JWT), and logs.
        runtime_role = iam.Role(
            self, "AgentCoreRuntimeRole",
            role_name="dfmea-agentcore-runtime-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            description="Execution role for DFMEA AgentCore Runtime agents",
        )
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
            ],
            resources=["*"],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:GetEvent",
                "bedrock-agentcore:GetMemory",
                "bedrock-agentcore:ListMemories",
                "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
            ],
            resources=[
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/*",
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/*",
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/*",
            ],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                     "logs:DescribeLogGroups", "logs:DescribeLogStreams"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/*"],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            sid="PublishRuntimeTelemetry",
            actions=[
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
                "xray:GetSamplingRules",
                "xray:GetSamplingTargets",
                "xray:GetSamplingStatisticSummaries",
            ],
            resources=["*"],
        ))
        # ECR pull — AgentCore Runtime pulls the agent container image using this role.
        # (Required by CreateAgentRuntime: GetAuthorizationToken/BatchGetImage/GetDownloadUrlForLayer.)
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["ecr:GetAuthorizationToken"],
            resources=["*"],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchCheckLayerAvailability",
            ],
            resources=[f"arn:aws:ecr:{self.region}:{self.account}:repository/bedrock-agentcore-*"],
        ))
        # Read the M2M client secret to mint Gateway JWTs + decrypt it.
        # NOTE: use identity-policy statements referencing ARNs (not grant_read /
        # grant_decrypt helpers). The helpers mutate the Foundation CMK key policy
        # and the Auth secret to reference this role, which creates a
        # Foundation/Auth -> AgentCore dependency and a cyclic reference (the
        # AgentCore stack already depends on Foundation for the VPC/CMK). Referencing
        # the ARNs only creates the safe AgentCore -> Foundation/Auth direction.
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
            resources=[auth.m2m_secret.secret_arn],
        ))
        runtime_role.add_to_policy(iam.PolicyStatement(
            actions=["kms:Decrypt"],
            resources=[cmk.key_arn],
        ))

        NagSuppressions.add_resource_suppressions(
            runtime_role,
            [
                {"id": "AwsSolutions-IAM5", "reason": "Bedrock model invoke and AgentCore memory/runtime/workload require wildcard resources; logs scoped to /aws/bedrock-agentcore/*"},
            ],
            apply_to_children=True,
        )

        # NOTE: AgentCore Runtime agents + Gateway are provisioned post-deploy via
        # the bedrock_agentcore_starter_toolkit (scripts/deploy_agentcore_runtimes.py)
        # and AWS CLI (deploy.sh step 11). AWS::BedrockAgentCore::* CloudFormation
        # resource types are not yet available in all regions. Runtime ARNs are
        # patched onto the invoker Lambda AGENT_RUNTIME_ARN env vars post-deploy.
        self.runtime_role = runtime_role

        # ── NAG suppression for CDK-generated LogRetention Lambda ────────────────
        NagSuppressions.add_stack_suppressions(
            self,
            [
                {"id": "AwsSolutions-IAM4", "reason": "CDK-generated LogRetention Lambda uses AWSLambdaBasicExecutionRole"},
                {"id": "AwsSolutions-IAM5", "reason": "CDK-generated LogRetention Lambda requires wildcard permissions"},
            ],
        )

        # ── Outputs ──────────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "McpToolsFnArn", value=self.mcp_tools_fn.function_arn, export_name="DfmeaMcpToolsFnArn")
        cdk.CfnOutput(self, "AgentCoreRuntimeRoleArn", value=runtime_role.role_arn, export_name="DfmeaAgentCoreRuntimeRoleArn")
        cdk.CfnOutput(self, "AssistantCodeBucketName", value=self.assistant_code_bucket.bucket_name, export_name="DfmeaAssistantCodeBucketName")
        cdk.CfnOutput(self, "AssistantRuntimeRoleArn", value=self.assistant_runtime_role.role_arn, export_name="DfmeaAssistantRuntimeRoleArn")
