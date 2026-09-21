"""
infra/stacks/agent_stack.py — Agent Stack.

Provisions:
  - 5 Lambda functions (one per Strands agent): analyst, failure_mode, structural, regulatory, other
  - SQS A2A queue for inter-agent messaging
  - IAM roles with least-privilege policies
  - AgentCore thin invoker Lambdas for all 5 specialist agents
  - Lambda layers for agent runtime dependencies
"""
from __future__ import annotations
import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_sqs as sqs,
    aws_logs as logs,
    Duration,
    RemovalPolicy,
)
from constructs import Construct
from cdk_nag import NagSuppressions

from .foundation_stack import DfmeaFoundationStack
from .data_stack import DfmeaDataStack


class DfmeaAgentStack(cdk.Stack):
    """AgentCore thin invoker Lambdas (dfmea-invoke-*) + SQS A2A queue."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        foundation: DfmeaFoundationStack,
        data: DfmeaDataStack,
        neptune=None,  # DfmeaNeptuneStack | None
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cmk = foundation.cmk
        vpc = foundation.vpc

        # ── Shared agent environment variables ────────────────────────────────
        common_env = {
            "REVIEWS_TABLE_NAME": data.reviews_table.table_name,
            "FINDINGS_TABLE_NAME": data.findings_table.table_name,
            "RETRIEVAL_TABLE_NAME": data.retrieval_table.table_name,
            "AP_LOOKUP_TABLE_NAME": data.ap_lookup_table.table_name,
            "UPLOADS_BUCKET_NAME":   data.uploads_bucket.bucket_name,
            "PROCESSED_BUCKET_NAME": data.processed_bucket.bucket_name,
            "REPORTS_BUCKET_NAME": data.reports_bucket.bucket_name,
            "ML_MODELS_BUCKET_NAME": data.ml_models_bucket.bucket_name,
            "BEDROCK_MODEL_ID": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "POWERTOOLS_SERVICE_NAME": "dfmea-agents",
            "LOG_LEVEL": "INFO",
        }

        # ── Shared IAM role for all agent Lambdas ─────────────────────────────
        agent_role = iam.Role(
            self,
            "AgentLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
            description="Execution role for DFMEA agent Lambdas",
        )

        # CloudWatch Logs + active X-Ray tracing
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/dfmea-*"],
            )
        )
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                resources=["*"],
            )
        )

        # DynamoDB access (all DFMEA tables)
        for table in [
            data.reviews_table,
            data.findings_table,
            data.retrieval_table,
            data.ap_lookup_table,
            data.structural_table,
            data.synthesis_table,
            data.risk_scores_table,
        ]:
            table.grant_read_write_data(agent_role)

        # S3 access — uploads read-only (rows fetch), others read-write
        data.uploads_bucket.grant_read(agent_role)
        for bucket in [data.processed_bucket, data.reports_bucket, data.ontology_bucket, data.ml_models_bucket]:
            bucket.grant_read_write(agent_role)

        # Bedrock
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Retrieve",
                    "bedrock:RetrieveAndGenerate",
                    "bedrock-agentcore:InvokeAgentRuntime",
                ],
                resources=["*"],
            )
        )

        # KMS
        cmk.grant_encrypt_decrypt(agent_role)

        # Suppress CDK NAG wildcard Bedrock (no resource-level ARNs for Bedrock)
        NagSuppressions.add_resource_suppressions(
            agent_role,
            [
                {"id": "AwsSolutions-IAM5", "reason": "Bedrock InvokeModel requires wildcard resource; logs wildcard scoped to /aws/lambda/dfmea-*"},
                {"id": "AwsSolutions-IAM4", "reason": "AWSLambdaVPCAccessExecutionRole required for VPC-enabled Lambda",
                 "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"]},
            ],
            apply_to_children=True,
        )

        # ── SQS A2A queue ─────────────────────────────────────────────────────
        a2a_dlq = sqs.Queue(
            self,
            "A2ADlq",
            queue_name="dfmea-a2a-dlq",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=cmk,
            retention_period=Duration.days(14),
        )
        self.a2a_queue = sqs.Queue(
            self,
            "A2AQueue",
            queue_name="dfmea-a2a-tasks",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=cmk,
            visibility_timeout=Duration.seconds(900),
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=a2a_dlq),
        )
        self.a2a_queue.grant_send_messages(agent_role)
        self.a2a_queue.grant_consume_messages(agent_role)

        # Enforce SSL on both queues (AwsSolutions-SQS4)
        for _q in [a2a_dlq, self.a2a_queue]:
            _q.add_to_resource_policy(iam.PolicyStatement(
                sid="DenyNonSSL",
                effect=iam.Effect.DENY,
                principals=[iam.AnyPrincipal()],
                actions=["sqs:*"],
                resources=[_q.queue_arn],
                conditions={"Bool": {"aws:SecureTransport": "false"}},
            ))

        common_env["A2A_QUEUE_URL"] = self.a2a_queue.queue_url

        # ── Optional Neptune env injection ────────────────────────────────────
        if neptune is not None:
            common_env["NEPTUNE_ENDPOINT"] = neptune.cluster.attr_endpoint
            common_env["NEPTUNE_PORT"]     = "8182"
            # Inline policy avoids cross-stack cycle
            agent_role.add_to_policy(iam.PolicyStatement(
                actions=[
                    "neptune-db:connect",
                    "neptune-db:ReadDataViaQuery",
                ],
                resources=[
                    f"arn:aws:neptune-db:{self.region}:{self.account}:"
                    f"{neptune.cluster.attr_cluster_resource_id}/*"
                ],
            ))

        # ── Lambda helper ─────────────────────────────────────────────────────
        private_subnets = cdk.aws_ec2.SubnetSelection(
            subnet_type=cdk.aws_ec2.SubnetType.PRIVATE_WITH_EGRESS
        )

        def _agent_lambda(
            logical_id: str,
            name: str,
            handler_path: str,
            extra_env: dict | None = None,
            timeout_seconds: int = 600,
        ) -> lambda_.Function:
            # AGENT_RUNTIME_ARN is empty at synth time; deploy.sh patches it
            # post-deploy once the AgentCore Runtimes are launched.
            env = {**common_env, "AGENT_RUNTIME_ARN": "", **(extra_env or {})}
            fn = lambda_.Function(
                self,
                logical_id,
                function_name=f"dfmea-{name}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler=handler_path,
                code=lambda_.Code.from_asset("..", exclude=["cdk.out", ".venv", "**/__pycache__", "**/*.pyc", "ui/node_modules", "ui/dist", ".git", "**/.pytest_cache", "**/*.egg-info"]),  # project root
                role=agent_role,
                environment=env,
                timeout=Duration.seconds(timeout_seconds),
                memory_size=1024,
                vpc=vpc,
                vpc_subnets=private_subnets,
                log_retention=logs.RetentionDays.ONE_MONTH,
                environment_encryption=cmk,
                tracing=lambda_.Tracing.ACTIVE,
            )
            NagSuppressions.add_resource_suppressions(
                fn,
                [{"id": "AwsSolutions-L1", "reason": "Python 3.12 is the latest available runtime"}],
            )
            return fn

        # ── Five thin invoker Lambda functions ────────────────────────────────
        # Each calls its AgentCore Runtime via bedrock-agentcore:InvokeAgentRuntime.
        # The analyst invoker also runs the deterministic S6 PDF report in-Lambda.
        self.analyst_fn = _agent_lambda(
            "AnalystAgentFn", "invoke-analyst",
            "lambdas/agent_invoker/analyst_handler.handler",
            extra_env={"AGENT_LABEL": "analyst"},
        )
        self.failure_mode_fn = _agent_lambda(
            "FailureModeAgentFn", "invoke-failure-mode",
            "lambdas/agent_invoker/handler.handler",
            extra_env={"AGENT_LABEL": "failure_mode"},
        )
        self.structural_fn = _agent_lambda(
            "StructuralAgentFn", "invoke-structural",
            "lambdas/agent_invoker/handler.handler",
            extra_env={"AGENT_LABEL": "structural"},
        )
        self.regulatory_fn = _agent_lambda(
            "RegulatoryAgentFn", "invoke-regulatory",
            "lambdas/agent_invoker/handler.handler",
            extra_env={"AGENT_LABEL": "regulatory"},
        )
        self.other_fn = _agent_lambda(
            "OtherAgentFn", "invoke-other",
            "lambdas/agent_invoker/handler.handler",
            extra_env={"AGENT_LABEL": "other"},
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
        cdk.CfnOutput(self, "A2AQueueUrl", value=self.a2a_queue.queue_url, export_name="DfmeaA2AQueueUrl")
        cdk.CfnOutput(self, "AnalystFnArn", value=self.analyst_fn.function_arn, export_name="DfmeaAnalystFnArn")
