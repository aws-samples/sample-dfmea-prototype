"""
infra/stacks/orchestration_stack.py — Orchestration Stack.

Provisions:
  - Lambda functions: intake, cad_extraction, websocket_connect, websocket_fanout, hitl_callback
  - Step Functions state machines: ingestion pipeline (S0-S2) + review pipeline (S3-S7)
  - EventBridge rule: triggers ingestion pipeline on S3 upload
  - SNS topic: HITL notifications
  - SQS queue: findings fan-out to synthesis
"""
from __future__ import annotations
import json
import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_events,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as sfn_tasks,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_sqs as sqs,
    aws_sns as sns,
    aws_sns_subscriptions,
    aws_logs as logs,
    aws_dynamodb as dynamodb,
    Duration,
    RemovalPolicy,
)
from constructs import Construct
from cdk_nag import NagSuppressions

from .foundation_stack import DfmeaFoundationStack
from .data_stack import DfmeaDataStack
from .agent_stack import DfmeaAgentStack


class DfmeaOrchestrationStack(cdk.Stack):
    """Step Functions pipelines, EventBridge, SNS/SQS, and intake Lambdas."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        foundation: DfmeaFoundationStack,
        data: DfmeaDataStack,
        agents: DfmeaAgentStack,
        portal_url: str = "",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        _portal = portal_url or "https://portal.example.com  (re-deploy with PORTAL_URL=<cloudfront-url>)"

        cmk = foundation.cmk
        vpc = foundation.vpc

        # ── Shared orchestration IAM role ─────────────────────────────────────
        orch_role = iam.Role(
            self,
            "OrchLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        )
        orch_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/dfmea-*"],
            )
        )
        orch_role.add_to_policy(
            iam.PolicyStatement(
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                resources=["*"],
            )
        )
        for table in [data.reviews_table, data.findings_table, data.synthesis_table, data.risk_scores_table]:
            table.grant_read_write_data(orch_role)
        for bucket in [data.uploads_bucket, data.processed_bucket, data.reports_bucket]:
            bucket.grant_read_write(orch_role)
        data.ml_models_bucket.grant_read(orch_role)
        cmk.grant_encrypt_decrypt(orch_role)

        # Step Functions SendTaskSuccess/Failure for HITL callback
        orch_role.add_to_policy(
            iam.PolicyStatement(
                actions=["states:SendTaskSuccess", "states:SendTaskFailure"],
                resources=["*"],
            )
        )

        # Textract for CAD extraction
        orch_role.add_to_policy(
            iam.PolicyStatement(
                actions=["textract:DetectDocumentText"],
                resources=["*"],
            )
        )

        NagSuppressions.add_resource_suppressions(
            orch_role,
            [
                {"id": "AwsSolutions-IAM5", "reason": "Textract, Comprehend, X-Ray telemetry, StepFunctions task token callbacks, and Lambda log groups require wildcard resources; scoped by service boundary"},
                {"id": "AwsSolutions-IAM4", "reason": "AWSLambdaVPCAccessExecutionRole required for VPC-enabled Lambda",
                 "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"]},
            ],
            apply_to_children=True,
        )

        private_subnets = cdk.aws_ec2.SubnetSelection(
            subnet_type=cdk.aws_ec2.SubnetType.PRIVATE_WITH_EGRESS
        )

        common_env = {
            "REVIEWS_TABLE_NAME": data.reviews_table.table_name,
            "FINDINGS_TABLE_NAME": data.findings_table.table_name,
            "PROCESSED_BUCKET_NAME": data.processed_bucket.bucket_name,
            "UPLOADS_BUCKET_NAME": data.uploads_bucket.bucket_name,
            "REPORTS_BUCKET_NAME": data.reports_bucket.bucket_name,
            "ML_MODELS_BUCKET_NAME": data.ml_models_bucket.bucket_name,
            "RISK_SCORES_TABLE_NAME": data.risk_scores_table.table_name,
        }

        def _lambda(
            logical_id: str,
            name: str,
            module_path: str,
            extra_env: dict | None = None,
            timeout_seconds: int = 300,
        ) -> lambda_.Function:
            fn = lambda_.Function(
                self,
                logical_id,
                function_name=f"dfmea-{name}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler=f"lambdas/{module_path}",
                code=lambda_.Code.from_asset("..", exclude=["cdk.out", ".venv", "**/__pycache__", "**/*.pyc", "ui/node_modules", "ui/dist", ".git", "**/.pytest_cache", "**/*.egg-info"]),
                role=orch_role,
                environment={**common_env, **(extra_env or {})},
                timeout=Duration.seconds(timeout_seconds),
                memory_size=512,
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

        # ── Textract SNS notification topic ──────────────────────────────────────
        self.textract_topic = sns.Topic(
            self, "TextractTopic",
            topic_name="dfmea-textract-notifications",
            master_key=cmk,
        )

        textract_publish_role = iam.Role(
            self, "TextractPublishRole",
            assumed_by=iam.ServicePrincipal("textract.amazonaws.com"),
        )
        textract_publish_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                resources=[self.textract_topic.topic_arn],
            )
        )

        # Grant Textract + Comprehend access to the orchestration role
        orch_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "textract:StartDocumentAnalysis",
                    "textract:GetDocumentAnalysis",
                ],
                resources=["*"],
            )
        )
        orch_role.add_to_policy(
            iam.PolicyStatement(
                actions=["comprehend:DetectEntities"],
                resources=["*"],
            )
        )

        # Add Textract env vars to common_env BEFORE intake_fn is created
        common_env["TEXTRACT_SNS_TOPIC_ARN"] = self.textract_topic.topic_arn
        common_env["TEXTRACT_ROLE_ARN"]      = textract_publish_role.role_arn

        # ── Lambda functions ──────────────────────────────────────────────────
        self.intake_fn = _lambda("IntakeFn", "intake", "intake/handler.handler")
        self.cad_fn = _lambda("CadExtractionFn", "cad-extraction", "cad_extraction/handler.handler")
        self.hitl_fn = _lambda("HitlCallbackFn", "hitl-callback", "hitl_callback/handler.handler")
        self.hitl_gate_fn = _lambda("HitlGateFn", "hitl-gate", "hitl_gate/handler.handler")
        self.hitl_waiter_fn = _lambda("HitlWaiterFn", "hitl-waiter", "hitl_waiter/handler.handler")

        # ── Textract Callback Lambda ──────────────────────────────────────────────────
        self.textract_callback_fn = _lambda(
            "TextractCallbackFn",
            "textract-callback",
            "textract_callback/handler.handler",
        )

        self.textract_topic.add_subscription(
            aws_sns_subscriptions.LambdaSubscription(self.textract_callback_fn)
        )

        # ── WebSocket connections table (separate, not in DataStack) ──────────
        ws_connections_table = dynamodb.Table(
            self,
            "WsConnectionsTable",
            table_name="dfmea-ws-connections",
            partition_key=dynamodb.Attribute(name="connection_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=cmk,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )
        ws_connections_table.add_global_secondary_index(
            index_name="review_id-index",
            partition_key=dynamodb.Attribute(name="review_id", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.KEYS_ONLY,
        )
        self.ws_connections_table = ws_connections_table

        ws_role = iam.Role(
            self, "WsLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ],
        )
        ws_connections_table.grant_read_write_data(ws_role)
        ws_role.add_to_policy(
            iam.PolicyStatement(
                actions=["execute-api:ManageConnections"],
                resources=[f"arn:aws:execute-api:{self.region}:{self.account}:*/@connections/*"],
            )
        )
        ws_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/dfmea-*"],
            )
        )
        cmk.grant_encrypt_decrypt(ws_role)

        self.ws_connect_fn = lambda_.Function(
            self, "WsConnectFn",
            function_name="dfmea-ws-connect",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambdas/websocket_connect/handler.handler",
            code=lambda_.Code.from_asset("..", exclude=["cdk.out", ".venv", "**/__pycache__", "**/*.pyc", "ui/node_modules", "ui/dist", ".git", "**/.pytest_cache", "**/*.egg-info"]),
            role=ws_role,
            environment={"CONNECTIONS_TABLE_NAME": ws_connections_table.table_name},
            timeout=Duration.seconds(29),
            memory_size=256,
            vpc=vpc,
            vpc_subnets=private_subnets,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment_encryption=cmk,
        )
        self.ws_fanout_fn = lambda_.Function(
            self, "WsFanoutFn",
            function_name="dfmea-ws-fanout",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambdas/websocket_fanout/handler.handler",
            code=lambda_.Code.from_asset("..", exclude=["cdk.out", ".venv", "**/__pycache__", "**/*.pyc", "ui/node_modules", "ui/dist", ".git", "**/.pytest_cache", "**/*.egg-info"]),
            role=ws_role,
            environment={"CONNECTIONS_TABLE_NAME": ws_connections_table.table_name},
            timeout=Duration.seconds(60),
            memory_size=256,
            vpc=vpc,
            vpc_subnets=private_subnets,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment_encryption=cmk,
        )
        NagSuppressions.add_resource_suppressions(
            ws_role,
            [
                {"id": "AwsSolutions-IAM5", "reason": "WebSocket management requires wildcard connection ARN; logs scoped to dfmea-*"},
                {"id": "AwsSolutions-IAM4", "reason": "AWSLambdaVPCAccessExecutionRole required for VPC-enabled Lambda",
                 "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"]},
            ],
            apply_to_children=True,
        )
        for _fn in [self.ws_connect_fn, self.ws_fanout_fn]:
            NagSuppressions.add_resource_suppressions(
                _fn,
                [{"id": "AwsSolutions-L1", "reason": "Python 3.12 is the latest available runtime"}],
            )

        # DynamoDB Stream → websocket fanout
        self.ws_fanout_fn.add_event_source(
            lambda_events.DynamoEventSource(
                data.reviews_table,
                starting_position=lambda_.StartingPosition.LATEST,
                filters=[
                    lambda_.FilterCriteria.filter({"eventName": lambda_.FilterRule.is_equal("MODIFY")})
                ],
                batch_size=10,
                retry_attempts=2,
            )
        )
        # Enable stream on reviews table (we need to grant read)
        data.reviews_table.grant_stream_read(ws_role)

        # ── SNS HITL notification topic ───────────────────────────────────────
        self.hitl_topic = sns.Topic(
            self,
            "HitlTopic",
            topic_name="dfmea-hitl-notifications",
            master_key=cmk,
        )

        # ── Step Functions: Ingestion State Machine (S0-S2) ───────────────────
        sfn_role = iam.Role(
            self,
            "SfnRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
        )
        sfn_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    self.intake_fn.function_arn,
                    self.cad_fn.function_arn,
                    agents.analyst_fn.function_arn,
                    agents.failure_mode_fn.function_arn,
                    agents.structural_fn.function_arn,
                    agents.regulatory_fn.function_arn,
                    agents.other_fn.function_arn,
                    self.hitl_fn.function_arn,
                    self.hitl_gate_fn.function_arn,
                    self.hitl_waiter_fn.function_arn,
                ],
            )
        )
        sfn_role.add_to_policy(
            iam.PolicyStatement(
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                resources=["*"],
            )
        )
        sfn_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogDelivery", "logs:PutLogEvents",
                         "logs:CreateLogGroup", "logs:DescribeLogGroups",
                         "logs:DescribeResourcePolicies"],
                resources=["*"],
            )
        )
        sfn_role.add_to_policy(
            iam.PolicyStatement(
                actions=["sns:Publish"],
                resources=[self.hitl_topic.topic_arn],
            )
        )
        sfn_role.add_to_policy(
            iam.PolicyStatement(
                actions=["dynamodb:UpdateItem"],
                resources=[data.reviews_table.table_arn],
            )
        )
        cmk.grant_encrypt_decrypt(sfn_role)

        NagSuppressions.add_resource_suppressions(
            sfn_role,
            [{"id": "AwsSolutions-IAM5", "reason": "X-Ray and CloudWatch Logs delivery require wildcard resources; Lambda ARNs are explicit"}],
            apply_to_children=True,
        )

        # ── Ingestion State Machine definition ────────────────────────────────
        # S0: CAD extraction via Step Functions; S1 intake is triggered by S3 event (not SFN)
        s0_cad = sfn_tasks.LambdaInvoke(
            self, "S0CadExtraction",
            lambda_function=self.cad_fn,
            output_path="$.Payload",
            comment="S0 — CAD component extraction",
        )
        ingestion_log_group = logs.LogGroup(
            self, "IngestionSfnLogs",
            log_group_name="/aws/states/dfmea-ingestion",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.ingestion_sm = sfn.StateMachine(
            self, "IngestionStateMachine",
            state_machine_name="dfmea-ingestion",
            definition_body=sfn.DefinitionBody.from_chainable(s0_cad),
            role=sfn_role,
            tracing_enabled=True,
            logs=sfn.LogOptions(
                destination=ingestion_log_group,
                level=sfn.LogLevel.ERROR,
                include_execution_data=False,
            ),
        )

        # ── Review State Machine definition (S3-S7) ───────────────────────────
        # S3 — parallel specialist analysis
        s3_parallel = sfn.Parallel(
            self, "S3SpecialistAnalysis",
            comment="S3 — Parallel specialist agents",
            result_path="$.parallel_results",
        )
        s3_parallel.branch(
            sfn_tasks.LambdaInvoke(self, "S3FailureMode",
                lambda_function=agents.failure_mode_fn, output_path="$.Payload")
        )
        s3_parallel.branch(
            sfn_tasks.LambdaInvoke(self, "S3Structural",
                lambda_function=agents.structural_fn, output_path="$.Payload")
        )
        s3_parallel.branch(
            sfn_tasks.LambdaInvoke(self, "S3Regulatory",
                lambda_function=agents.regulatory_fn, output_path="$.Payload")
        )
        s3_parallel.branch(
            sfn_tasks.LambdaInvoke(self, "S3Other",
                lambda_function=agents.other_fn, output_path="$.Payload")
        )

        # S4 — synthesis (analyst)
        s4_synthesis = sfn_tasks.LambdaInvoke(
            self, "S4Synthesis",
            lambda_function=agents.analyst_fn,
            output_path="$.Payload",
            comment="S4 — Analyst synthesis",
        )

        # S5 — HITL wait (callback pattern)
        s5_hitl_notify = sfn_tasks.SnsPublish(
            self, "S5HitlNotify",
            topic=self.hitl_topic,
            message=sfn.TaskInput.from_object({
                "review_id.$": "$.review_id",
                "gate": 4,
                "stage": "GATE_4_FINAL_APPROVAL",
                "subject": "Action Required: DFMEA Review Awaiting Final Approval",
                "body": (
                    "A DFMEA review has completed AI analysis and is ready for your final approval.\n\n"
                    "All four specialist agents (failure-mode, structural, regulatory, schema) have "
                    "submitted their findings. The synthesis agent has consolidated the results and "
                    "computed AIAG-VDA 2019 Action Priority ratings.\n\n"
                    "Action: Log in to the DFMEA portal, open the review, and click Approve on Gate 4 "
                    "to generate the final PDF report.\n\n"
                    f"Portal: {_portal}"
                ),
            }),
            subject="Action Required: DFMEA Review Awaiting Final Approval",
            comment="S5 — Notify HITL reviewer",
            result_path=sfn.JsonPath.DISCARD,
        )
        s5_hitl_wait = sfn_tasks.LambdaInvoke(
            self, "S5HitlWait",
            lambda_function=self.hitl_waiter_fn,
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            payload=sfn.TaskInput.from_object({
                "review_id.$": "$.review_id",
                "taskToken": sfn.JsonPath.task_token,
            }),
            heartbeat=Duration.hours(72),
            result_path="$.hitl_result",
            comment="S5 — Gate 4: wait for human approval (waitForTaskToken)",
        )

        # S6 — final report (analyst again with report=true)
        s6_report = sfn_tasks.LambdaInvoke(
            self, "S6FinalReport",
            lambda_function=agents.analyst_fn,
            payload=sfn.TaskInput.from_object({
                "review_id.$": "$.review_id",
                "stage": "S6_REPORT",
            }),
            output_path="$.Payload",
            comment="S6 — Generate final report",
        )

        # ── S1: Intake parsing — parse uploaded file and write normalised.json ──
        s1_intake = sfn_tasks.LambdaInvoke(
            self, "S1IntakeParse",
            lambda_function=self.intake_fn,
            payload=sfn.TaskInput.from_object({
                "review_id.$": "$.review_id",
                "file_key.$":  "$.file_key",
            }),
            result_path=sfn.JsonPath.DISCARD,
            comment="S1 — parse uploaded DFMEA file and write normalised.json",
        )

        # ── Gate 1: Intake validation ─────────────────────────────────────────
        s1_gate_notify = sfn_tasks.SnsPublish(
            self, "S1Gate1Notify",
            topic=self.hitl_topic,
            message=sfn.TaskInput.from_object({
                "review_id.$": "$.review_id",
                "gate": 1,
                "stage": "GATE_1_INTAKE_VALIDATION",
                "subject": "Action Required: DFMEA Gate 1 — Intake Validation Pending",
                "body": (
                    "A new DFMEA file has been uploaded and parsed. Gate 1 (Intake Validation) "
                    "requires your review.\n\n"
                    "The intake Lambda has parsed the uploaded file and normalised the row data. "
                    "Please review the submission and approve to proceed with CAD extraction.\n\n"
                    f"Portal: {_portal}"
                ),
            }),
            subject="Action Required: DFMEA Gate 1 — Intake Validation Pending",
            result_path=sfn.JsonPath.DISCARD,
        )
        s1_gate_wait = sfn_tasks.CallAwsService(
            self, "S1Gate1Wait",
            service="dynamodb",
            action="updateItem",
            parameters={
                "TableName": data.reviews_table.table_name,
                "Key": {"review_id": {"S.$": "$.review_id"}},
                "UpdateExpression": "SET gate_1_task_token = :tok",
                "ExpressionAttributeValues": {":tok": {"S.$": "$$.Task.Token"}},
            },
            iam_resources=[data.reviews_table.table_arn],
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            heartbeat=Duration.hours(72),
            comment="Gate 1 — wait for intake approval",
            result_path=sfn.JsonPath.DISCARD,
        )

        # CAD review (distinct state from ingestion s0_cad; same Lambda)
        s0_cad_review = sfn_tasks.LambdaInvoke(
            self, "S0CadReview",
            lambda_function=self.cad_fn,
            output_path="$.Payload",
            comment="CAD extraction in review pipeline",
        )

        # ── Gate 2: CAD extraction verification ──────────────────────────────
        s2_gate_notify = sfn_tasks.SnsPublish(
            self, "S2Gate2Notify",
            topic=self.hitl_topic,
            message=sfn.TaskInput.from_object({
                "review_id.$": "$.review_id",
                "gate": 2,
                "stage": "GATE_2_CAD_VERIFICATION",
                "subject": "Action Required: DFMEA Gate 2 — CAD Verification Pending",
                "body": (
                    "Gate 1 has been approved. Gate 2 (CAD Extraction Verification) now requires "
                    "your review.\n\n"
                    "The CAD extraction Lambda has analysed the assembly hierarchy and structural "
                    "connections. Please verify the BOM cross-check results and approve to dispatch "
                    "the parallel AI analysis agents.\n\n"
                    f"Portal: {_portal}"
                ),
            }),
            subject="Action Required: DFMEA Gate 2 — CAD Verification Pending",
            result_path=sfn.JsonPath.DISCARD,
        )
        s2_gate_wait = sfn_tasks.CallAwsService(
            self, "S2Gate2Wait",
            service="dynamodb",
            action="updateItem",
            parameters={
                "TableName": data.reviews_table.table_name,
                "Key": {"review_id": {"S.$": "$.review_id"}},
                "UpdateExpression": "SET gate_2_task_token = :tok",
                "ExpressionAttributeValues": {":tok": {"S.$": "$$.Task.Token"}},
            },
            iam_resources=[data.reviews_table.table_arn],
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            heartbeat=Duration.hours(72),
            comment="Gate 2 — wait for CAD verification",
            result_path=sfn.JsonPath.DISCARD,
        )

        # ── Gate 3: Post-analysis approval ────────────────────────────────────
        s3_gate_notify = sfn_tasks.SnsPublish(
            self, "S3Gate3Notify",
            topic=self.hitl_topic,
            message=sfn.TaskInput.from_object({
                "review_id.$": "$.review_id",
                "gate": 3,
                "stage": "GATE_3_AGENT_ANALYSIS_REVIEW",
                "subject": "Action Required: DFMEA Gate 3 — Agent Analysis Review Pending",
                "body": (
                    "Gate 2 has been approved. Gate 3 (Agent Analysis Review) now requires your "
                    "review.\n\n"
                    "All four parallel AI agents have completed their analysis:\n"
                    "  - Failure-mode agent: AIAG-VDA failure mode knowledge base scan\n"
                    "  - Structural agent: BOM and interface matrix completeness\n"
                    "  - Regulatory agent: FMVSS 214 / 216 / ISO 26262 citations\n"
                    "  - Schema agent: DFMEA field completeness and S/O/D ratings\n\n"
                    "Please review the agent findings and approve to proceed to final HITL gate.\n\n"
                    f"Portal: {_portal}"
                ),
            }),
            subject="Action Required: DFMEA Gate 3 — Agent Analysis Review Pending",
            result_path=sfn.JsonPath.DISCARD,
        )
        s3_gate_wait = sfn_tasks.CallAwsService(
            self, "S3Gate3Wait",
            service="dynamodb",
            action="updateItem",
            parameters={
                "TableName": data.reviews_table.table_name,
                "Key": {"review_id": {"S.$": "$.review_id"}},
                "UpdateExpression": "SET gate_3_task_token = :tok",
                "ExpressionAttributeValues": {":tok": {"S.$": "$$.Task.Token"}},
            },
            iam_resources=[data.reviews_table.table_arn],
            integration_pattern=sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
            heartbeat=Duration.hours(72),
            comment="Gate 3 — wait for per-agent analysis approval",
            result_path=sfn.JsonPath.DISCARD,
        )

        review_log_group = logs.LogGroup(
            self, "ReviewSfnLogs",
            log_group_name="/aws/states/dfmea-review",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.review_sm = sfn.StateMachine(
            self, "ReviewStateMachine",
            state_machine_name="dfmea-review",
            definition_body=sfn.DefinitionBody.from_chainable(
                s1_intake
                .next(s1_gate_notify)
                .next(s1_gate_wait)
                .next(s0_cad_review)
                .next(s2_gate_notify)
                .next(s2_gate_wait)
                .next(s3_parallel)
                .next(s3_gate_notify)
                .next(s3_gate_wait)
                .next(s4_synthesis)
                .next(s5_hitl_notify)
                .next(s5_hitl_wait)
                .next(s6_report)
            ),
            role=sfn_role,
            tracing_enabled=True,
            logs=sfn.LogOptions(
                destination=review_log_group,
                level=sfn.LogLevel.ERROR,
                include_execution_data=False,
            ),
        )

        # AwsSolutions-SF1 recommends ALL execution logging. These state
        # machines intentionally log errors without execution data because
        # callback inputs contain task tokens and review payloads. X-Ray remains
        # enabled for operational tracing without exposing those values.
        for state_machine in (self.ingestion_sm, self.review_sm):
            NagSuppressions.add_resource_suppressions(
                state_machine,
                [{
                    "id": "AwsSolutions-SF1",
                    "reason": (
                        "ERROR-only logs with IncludeExecutionData=false prevent "
                        "Step Functions callback task tokens and DFMEA payloads from "
                        "being written to CloudWatch; X-Ray tracing remains enabled."
                    ),
                }],
                apply_to_children=True,
            )

        # NOTE: REVIEW_SM_ARN env var must be set on dfmea-intake Lambda post-deploy
        # (see DEPLOYMENT.md) to avoid cross-resource cyclic dependencies in CDK.
        # self.review_sm.grant_start_execution(orch_role) — done via post-deploy script

        # ── EventBridge rule: S3 upload → ingestion SM ───────────────────────
        uploads_rule = events.Rule(
            self, "UploadsRule",
            rule_name="dfmea-uploads-trigger",
            description="Trigger ingestion SM when a file lands in the uploads bucket",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [data.uploads_bucket.bucket_name]},
                },
            ),
        )
        uploads_rule.add_target(
            events_targets.SfnStateMachine(
                self.ingestion_sm,
                input=events.RuleTargetInput.from_event_path("$.detail"),
            )
        )

        # Ensure EventBridge can invoke the SM
        self.ingestion_sm.grant_start_execution(
            iam.ServicePrincipal("events.amazonaws.com")
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
        cdk.CfnOutput(self, "IngestionSmArn", value=self.ingestion_sm.state_machine_arn, export_name="DfmeaIngestionSmArn")
        cdk.CfnOutput(self, "ReviewSmArn", value=self.review_sm.state_machine_arn, export_name="DfmeaReviewSmArn")
        cdk.CfnOutput(self, "HitlTopicArn", value=self.hitl_topic.topic_arn, export_name="DfmeaHitlTopicArn")
        cdk.CfnOutput(self, "WsConnectionsTableName", value=ws_connections_table.table_name, export_name="DfmeaWsConnectionsTable")
