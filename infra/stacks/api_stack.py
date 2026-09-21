"""
infra/stacks/api_stack.py — API Stack.

Provisions:
  - REST API Lambda (lambdas/api/handler.py)
  - API Gateway REST API with Cognito authorizer — 9 endpoints
  - API Gateway WebSocket API (connect/disconnect via orchestration Lambdas)
  - WAF WebACL on the REST API
  - Usage plan + API key
"""
from __future__ import annotations
import aws_cdk as cdk
from aws_cdk import (
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_cognito as cognito,
    aws_logs as logs,
    aws_wafv2 as wafv2,
    Duration,
    RemovalPolicy,
)
from constructs import Construct
from cdk_nag import NagSuppressions

from .foundation_stack import DfmeaFoundationStack
from .auth_stack import DfmeaAuthStack
from .data_stack import DfmeaDataStack
from .orchestration_stack import DfmeaOrchestrationStack


class DfmeaApiStack(cdk.Stack):
    """REST API + WebSocket API for DFMEA Agentic Review System."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        foundation: DfmeaFoundationStack,
        auth: DfmeaAuthStack,
        data: DfmeaDataStack,
        orchestration: DfmeaOrchestrationStack,
        search=None,
        neptune=None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cmk = foundation.cmk
        vpc = foundation.vpc

        # ── API Lambda IAM role ───────────────────────────────────────────────
        api_role = iam.Role(
            self,
            "ApiLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        )
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/dfmea-*"],
            )
        )
        for table in [data.reviews_table, data.findings_table, data.synthesis_table, data.risk_scores_table]:
            table.grant_read_write_data(api_role)
        for bucket in [data.reports_bucket, data.uploads_bucket]:
            bucket.grant_read_write(api_role)
        cmk.grant_encrypt_decrypt(api_role)
        api_role.add_to_policy(
            iam.PolicyStatement(
                actions=["states:StartExecution"],
                resources=[orchestration.review_sm.state_machine_arn],
            )
        )
        NagSuppressions.add_resource_suppressions(
            api_role,
            [
                {"id": "AwsSolutions-IAM5", "reason": "Logs wildcard scoped to dfmea-* prefix"},
                {"id": "AwsSolutions-IAM4", "reason": "AWSLambdaVPCAccessExecutionRole required for VPC-enabled Lambda",
                 "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"]},
            ],
            apply_to_children=True,
        )

        # Also grant read access to processed bucket for audit Lambda
        data.processed_bucket.grant_read(api_role)

        # ── REST API Lambda ───────────────────────────────────────────────────
        private_subnets = cdk.aws_ec2.SubnetSelection(
            subnet_type=cdk.aws_ec2.SubnetType.PRIVATE_WITH_EGRESS
        )
        common_api_env = {
            "REVIEWS_TABLE_NAME": data.reviews_table.table_name,
            "FINDINGS_TABLE_NAME": data.findings_table.table_name,
            "REPORTS_BUCKET_NAME": data.reports_bucket.bucket_name,
            "UPLOADS_BUCKET_NAME": data.uploads_bucket.bucket_name,
            "PROCESSED_BUCKET_NAME": data.processed_bucket.bucket_name,
            "REVIEW_SM_ARN": orchestration.review_sm.state_machine_arn,
        }
        if search is not None:
            common_api_env["AOSS_ENDPOINT"] = search.collection.attr_collection_endpoint
            common_api_env["AOSS_INDEX"]    = "regulatory-docs"
            # Inline policy avoids cross-stack cycle: attach_to_role modifies SearchStack's
            # ManagedPolicy to embed api_role ARN (SearchStack→ApiStack), while ApiStack
            # already references search.collection (ApiStack→SearchStack).
            api_role.add_to_policy(iam.PolicyStatement(
                actions=["aoss:APIAccessAll"],
                resources=[search.collection.attr_arn],
            ))
        if neptune is not None:
            common_api_env["NEPTUNE_ENDPOINT"] = neptune.cluster.attr_endpoint
            common_api_env["NEPTUNE_PORT"]     = "8182"
            api_role.add_to_policy(iam.PolicyStatement(
                actions=[
                    "neptune-db:connect",
                    "neptune-db:ReadDataViaQuery",
                    "neptune-db:WriteDataViaQuery",
                ],
                resources=[
                    f"arn:aws:neptune-db:{self.region}:{self.account}:"
                    f"{neptune.cluster.attr_cluster_resource_id}/*"
                ],
            ))
            # Grant api Lambda read access to the ontology S3 bucket (for /ontology/load)
            api_role.add_to_policy(iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=[
                    f"arn:aws:s3:::dfmea-ontology-{self.account}",
                    f"arn:aws:s3:::dfmea-ontology-{self.account}/*",
                ],
            ))

        if hasattr(orchestration, "hitl_gate_fn"):
            common_api_env["HITL_GATE_FN_NAME"] = orchestration.hitl_gate_fn.function_name
            orchestration.hitl_gate_fn.grant_invoke(api_role)
        if hasattr(orchestration, "hitl_fn"):
            common_api_env["HITL_CALLBACK_FN_NAME"] = orchestration.hitl_fn.function_name
            orchestration.hitl_fn.grant_invoke(api_role)

        def _api_lambda(logical_id: str, name: str, handler_path: str, timeout: int = 30, memory: int = 512) -> lambda_.Function:
            fn = lambda_.Function(
                self, logical_id,
                function_name=f"dfmea-{name}",
                runtime=lambda_.Runtime.PYTHON_3_12,
                handler=handler_path,
                code=lambda_.Code.from_asset("..", exclude=["cdk.out", ".venv", "**/__pycache__", "**/*.pyc", "ui/node_modules", "ui/dist", ".git", "**/.pytest_cache", "**/*.egg-info"]),
                role=api_role,
                environment=common_api_env,
                timeout=Duration.seconds(timeout),
                memory_size=memory,
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

        self.api_fn = _api_lambda("ApiFn", "api", "lambdas/api/handler.handler")

        # ── Assistant wiring (chat agent backend) ─────────────────────────────
        # The assistant conversation store + AgentCore Runtime invocation. The
        # runtime ARN is NOT known at synth (the runtime is created post-CDK by
        # scripts/deploy_assistant_runtime.py), so ASSISTANT_RUNTIME_ARN is patched
        # onto this Lambda at deploy time (deploy.sh) — the same pattern used for
        # AGENT_RUNTIME_ARN. The invoke permission uses a name-scoped wildcard so
        # it is valid regardless of the runtime's generated suffix.
        data.assistant_table.grant_read_write_data(api_role)
        self.api_fn.add_environment("ASSISTANT_TABLE_NAME", data.assistant_table.table_name)
        self.api_fn.add_environment("ASSISTANT_RUNTIME_QUALIFIER", "DEFAULT")
        self.api_fn.add_environment("ASSISTANT_RETENTION_DAYS", "90")
        api_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"],
            resources=[
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/dfmea_assistant_runtime-*",
                f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/dfmea_assistant_runtime-*/runtime-endpoint/*",
            ],
        ))
        NagSuppressions.add_resource_suppressions(
            api_role,
            [{"id": "AwsSolutions-IAM5", "reason": "InvokeAgentRuntime scoped to the dfmea_assistant_runtime name; the AgentCore-generated ID suffix requires a trailing wildcard"}],
            apply_to_children=True,
        )

        self.audit_fn = _api_lambda("AuditFn", "audit", "lambdas/audit/handler.handler", timeout=120, memory=1024)
        # ── WAF WebACL ────────────────────────────────────────────────────────
        waf_acl = wafv2.CfnWebACL(
            self,
            "RestApiWaf",
            name="dfmea-rest-api-waf",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=1,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesCommonRuleSet",
                        )
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="CommonRuleSet",
                        sampled_requests_enabled=True,
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitRule",
                    priority=2,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=2000,
                            aggregate_key_type="IP",
                        )
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="RateLimit",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="dfmea-rest-api-waf",
                sampled_requests_enabled=True,
            ),
        )

        # ── REST API Gateway ──────────────────────────────────────────────────
        rest_log_group = logs.LogGroup(
            self,
            "RestApiLogGroup",
            log_group_name="/aws/apigateway/dfmea-rest-api",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.rest_api = apigw.RestApi(
            self,
            "DfmeaRestApi",
            rest_api_name="dfmea-rest-api",
            description="DFMEA Agentic Review System REST API",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization", "X-Api-Key"],
            ),
            deploy_options=apigw.StageOptions(
                stage_name="v1",
                logging_level=apigw.MethodLoggingLevel.INFO,
                data_trace_enabled=False,
                metrics_enabled=True,
                access_log_destination=apigw.LogGroupLogDestination(rest_log_group),
                access_log_format=apigw.AccessLogFormat.json_with_standard_fields(
                    caller=True, http_method=True, ip=True,
                    protocol=True, request_time=True, resource_path=True,
                    response_length=True, status=True, user=True,
                ),
                tracing_enabled=True,
            ),
            endpoint_configuration=apigw.EndpointConfiguration(
                types=[apigw.EndpointType.REGIONAL]
            ),
        )

        # Associate WAF with REST API stage — explicit dep ensures stage exists first
        waf_assoc = wafv2.CfnWebACLAssociation(
            self,
            "WafAssociation",
            resource_arn=f"arn:aws:apigateway:{self.region}::/restapis/{self.rest_api.rest_api_id}/stages/{self.rest_api.deployment_stage.stage_name}",
            web_acl_arn=waf_acl.attr_arn,
        )
        waf_assoc.node.add_dependency(self.rest_api.deployment_stage)

        # Cognito authorizer
        cognito_authorizer = apigw.CognitoUserPoolsAuthorizer(
            self,
            "CognitoAuthorizer",
            cognito_user_pools=[auth.user_pool],
            authorizer_name="dfmea-cognito-auth",
            identity_source=apigw.IdentitySource.header("Authorization"),
        )

        lambda_integration = apigw.LambdaIntegration(
            self.api_fn,
            request_templates={"application/json": '{"statusCode": "200"}'},
        )

        def _method(resource: apigw.Resource, http_method: str, require_auth: bool = True) -> None:
            kwargs = {}
            if require_auth:
                kwargs = {
                    "authorization_type": apigw.AuthorizationType.COGNITO,
                    "authorizer": cognito_authorizer,
                }
            else:
                kwargs = {"authorization_type": apigw.AuthorizationType.NONE}
            resource.add_method(http_method, lambda_integration, **kwargs)

        # /health — no auth
        health = self.rest_api.root.add_resource("health")
        _method(health, "GET", require_auth=False)

        # /reviews
        reviews = self.rest_api.root.add_resource("reviews")
        _method(reviews, "GET")
        _method(reviews, "POST")

        # /reviews/{review_id}
        review = reviews.add_resource("{review_id}")
        _method(review, "GET")
        _method(review, "DELETE")

        # /reviews/{review_id}/findings
        findings = review.add_resource("findings")
        _method(findings, "GET")

        # /reviews/{review_id}/cad
        cad_res = review.add_resource("cad")
        _method(cad_res, "GET")

        # /reviews/{review_id}/report
        report = review.add_resource("report")
        _method(report, "GET")

        # /reviews/{review_id}/hitl
        hitl = review.add_resource("hitl")
        _method(hitl, "POST")

        # /reviews/{review_id}/gates
        gates_res = review.add_resource("gates")
        _method(gates_res, "GET")

        # /reviews/{review_id}/gate/{gate_number}
        gate_res        = review.add_resource("gate")
        gate_number_res = gate_res.add_resource("{gate_number}")
        _method(gate_number_res, "POST")

        # /reviews/{review_id}/audit  — dedicated audit Lambda (longer timeout, zips S3 objects)
        audit_integration = apigw.LambdaIntegration(self.audit_fn)
        audit_res = review.add_resource("audit")
        audit_res.add_method(
            "GET",
            audit_integration,
            authorization_type=apigw.AuthorizationType.COGNITO,
            authorizer=cognito_authorizer,
        )

        # /admin/* dashboard endpoints
        admin = self.rest_api.root.add_resource("admin")
        metrics_res = admin.add_resource("metrics")
        _method(metrics_res, "GET")
        _method(admin.add_resource("top-failure-modes"),        "GET")
        _method(admin.add_resource("findings-over-time"),       "GET")
        _method(admin.add_resource("severity-occurrence-matrix"),"GET")
        _method(admin.add_resource("hitl-decisions"),           "GET")
        _method(admin.add_resource("agent-activity"),           "GET")

        # /ontology/* endpoints
        ontology_res = self.rest_api.root.add_resource("ontology")
        _method(ontology_res.add_resource("health"), "GET")
        _method(ontology_res.add_resource("graph"),  "GET")
        ontology_load_res = ontology_res.add_resource("load")
        _method(ontology_load_res, "POST")
        _method(ontology_load_res.add_resource("{load_id}"), "GET")

        # /upload-url — presigned S3 PUT URL for browser uploads
        upload_url_res = self.rest_api.root.add_resource("upload-url")
        _method(upload_url_res, "GET")

        # /assistant/conversations and messages (chat agent)
        assistant_res = self.rest_api.root.add_resource("assistant")
        conversations_res = assistant_res.add_resource("conversations")
        _method(conversations_res, "GET")
        _method(conversations_res, "POST")
        conversation_res = conversations_res.add_resource("{conversation_id}")
        _method(conversation_res, "DELETE")
        conversation_messages_res = conversation_res.add_resource("messages")
        _method(conversation_messages_res, "GET")
        _method(conversation_messages_res, "POST")

        # /search
        search_resource = self.rest_api.root.add_resource("search")
        search_resource.add_method(
            "GET", lambda_integration,
            authorization_type=apigw.AuthorizationType.COGNITO,
            authorizer=cognito_authorizer,
        )

        # ── Consolidate API Gateway → Lambda invoke permissions ────────────────
        # apigw.LambdaIntegration adds one AWS::Lambda::Permission per method
        # (scoped under each Method construct). With this many routes on a single
        # Lambda, the function's resource policy exceeds Lambda's hard 20 KB limit
        # ("final policy size is bigger than the limit"). Remove the auto-generated
        # per-method permissions and replace them with ONE wildcard permission per
        # function covering every method/stage/path of this REST API — functionally
        # identical, but a single policy statement instead of ~30.
        for _method in self.rest_api.methods:
            for _child in list(_method.node.children):
                if _child.node.id.startswith("ApiPermission"):
                    _method.node.try_remove_child(_child.node.id)
        _api_source_arn = (
            f"arn:{self.partition}:execute-api:{self.region}:{self.account}:"
            f"{self.rest_api.rest_api_id}/*/*/*"
        )
        self.api_fn.add_permission(
            "ApiGatewayInvokeAll",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=_api_source_arn,
        )
        self.audit_fn.add_permission(
            "ApiGatewayInvokeAudit",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=_api_source_arn,
        )

        NagSuppressions.add_resource_suppressions(
            self.rest_api,
            [
                {"id": "AwsSolutions-APIG2", "reason": "Request validation is handled in Lambda; schema validation added per-method"},
                {"id": "AwsSolutions-COG4", "reason": "Cognito authorizer applied on all protected routes; /health intentionally unauthenticated"},
                {"id": "AwsSolutions-APIG4", "reason": "/health endpoint is intentionally unauthenticated for load balancer health checks"},
                {"id": "AwsSolutions-IAM4", "reason": "CDK-generated CloudWatch role for REST API logging uses AmazonAPIGatewayPushToCloudWatchLogs; required managed policy",
                 "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"]},
            ],
            apply_to_children=True,
        )

        # ── WebSocket API ─────────────────────────────────────────────────────
        self.ws_api = apigwv2.WebSocketApi(
            self,
            "DfmeaWsApi",
            api_name="dfmea-ws-api",
            description="DFMEA real-time progress WebSocket",
            connect_route_options=apigwv2.WebSocketRouteOptions(
                integration=apigwv2_integrations.WebSocketLambdaIntegration(
                    "WsConnectIntegration",
                    handler=orchestration.ws_connect_fn,
                )
            ),
            disconnect_route_options=apigwv2.WebSocketRouteOptions(
                integration=apigwv2_integrations.WebSocketLambdaIntegration(
                    "WsDisconnectIntegration",
                    handler=orchestration.ws_connect_fn,
                )
            ),
        )
        ws_stage = apigwv2.WebSocketStage(
            self,
            "WsStage",
            web_socket_api=self.ws_api,
            stage_name="v1",
            auto_deploy=True,
        )
        # NOTE: WEBSOCKET_ENDPOINT env var on ws_fanout_fn must be set post-deploy
        # (see DEPLOYMENT.md step 7) to avoid a cross-stack cyclic dependency.
        self.ws_callback_url = ws_stage.callback_url

        NagSuppressions.add_resource_suppressions(
            self.ws_api,
            [{"id": "AwsSolutions-APIG4", "reason": "WebSocket $connect/$disconnect are internal routes; Cognito token validated in connect Lambda handler"}],
            apply_to_children=True,
        )
        NagSuppressions.add_resource_suppressions(
            ws_stage,
            [{"id": "AwsSolutions-APIG1", "reason": "WebSocket stage access logging not supported via L2 construct; operational logging captured in fanout/connect Lambda CloudWatch logs"}],
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
        cdk.CfnOutput(self, "RestApiUrl", value=self.rest_api.url, export_name="DfmeaRestApiUrl")
        cdk.CfnOutput(self, "WsApiUrl", value=ws_stage.url, export_name="DfmeaWsApiUrl")
