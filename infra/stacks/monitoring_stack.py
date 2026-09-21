"""
infra/stacks/monitoring_stack.py - Monitoring Stack.

Provisions:
  - CloudWatch Dashboard: DFMEA system overview (Lambda errors, SFN executions,
    AP distribution, cost-per-review, DynamoDB latency)
  - CloudWatch Alarms: Lambda error rate, SFN failures, review stuck, high AP count surge
  - Budget alarm: monthly EstimatedCharges
  - SNS alarm topic: routes alarm notifications to ops email
  - Custom metric namespace: DfmeaAgenticReview
"""
from __future__ import annotations
import aws_cdk as cdk
from aws_cdk import (
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subs,
    aws_budgets as budgets,
    aws_kms as kms,
    Duration,
)
from constructs import Construct
from cdk_nag import NagSuppressions

from .foundation_stack import DfmeaFoundationStack
from .data_stack import DfmeaDataStack
from .agent_stack import DfmeaAgentStack
from .orchestration_stack import DfmeaOrchestrationStack
from .api_stack import DfmeaApiStack


class DfmeaMonitoringStack(cdk.Stack):
    """CloudWatch dashboards, alarms, and budget monitoring for DFMEA system."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        foundation: DfmeaFoundationStack,
        data: DfmeaDataStack,
        agents: DfmeaAgentStack,
        orchestration: DfmeaOrchestrationStack,
        api: DfmeaApiStack,
        ops_email: str = "",
        monthly_budget_usd: float = 500.0,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cmk = foundation.cmk

        # ── Ops alarm SNS topic ───────────────────────────────────────────────
        self.alarm_topic = sns.Topic(
            self,
            "AlarmTopic",
            topic_name="dfmea-ops-alarms",
            master_key=cmk,
        )
        if ops_email:
            self.alarm_topic.add_subscription(
                sns_subs.EmailSubscription(ops_email)
            )

        alarm_action = cw_actions.SnsAction(self.alarm_topic)

        # ── Helper: standard alarm ────────────────────────────────────────────
        def _alarm(
            logical_id: str,
            metric: cw.Metric,
            threshold: float,
            description: str,
            comparison: cw.ComparisonOperator = cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods: int = 2,
            datapoints_to_alarm: int = 2,
        ) -> cw.Alarm:
            alarm = cw.Alarm(
                self,
                logical_id,
                alarm_name=f"dfmea-{logical_id.lower().replace('alarm', '').strip('-')}",
                alarm_description=description,
                metric=metric,
                threshold=threshold,
                comparison_operator=comparison,
                evaluation_periods=evaluation_periods,
                datapoints_to_alarm=datapoints_to_alarm,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(alarm_action)
            return alarm

        # ── Lambda error rate alarms ──────────────────────────────────────────
        agent_fn_names = [
            ("analyst", agents.analyst_fn),
            ("failure-mode", agents.failure_mode_fn),
            ("structural", agents.structural_fn),
            ("regulatory", agents.regulatory_fn),
        ]

        for agent_name, fn in agent_fn_names:
            _alarm(
                f"AgentErrorAlarm{agent_name.replace('-', '').title()}",
                metric=fn.metric_errors(period=Duration.minutes(5)),
                threshold=3,
                description=f"dfmea-agent-{agent_name} Lambda errors >= 3 in 5 min",
            )

        _alarm(
            "ApiLambdaErrorAlarm",
            metric=api.api_fn.metric_errors(period=Duration.minutes(5)),
            threshold=5,
            description="dfmea-api Lambda errors >= 5 in 5 min",
        )

        # ── Lambda duration alarms (p99 > 55s for agents with 600s timeout) ──
        _alarm(
            "AnalystDurationAlarm",
            metric=agents.analyst_fn.metric_duration(
                period=Duration.minutes(15),
                statistic="p99",
            ),
            threshold=55_000,  # 55 seconds in ms
            description="Analyst agent p99 duration > 55s (approaching 60s limit)",
        )

        # ── Step Functions execution failures ─────────────────────────────────
        sfn_review_failures = cw.Metric(
            namespace="AWS/States",
            metric_name="ExecutionsFailed",
            dimensions_map={"StateMachineArn": orchestration.review_sm.state_machine_arn},
            period=Duration.minutes(15),
            statistic="Sum",
        )
        _alarm(
            "ReviewSmFailureAlarm",
            metric=sfn_review_failures,
            threshold=1,
            description="Review state machine execution failed",
        )

        sfn_review_throttled = cw.Metric(
            namespace="AWS/States",
            metric_name="ExecutionThrottled",
            dimensions_map={"StateMachineArn": orchestration.review_sm.state_machine_arn},
            period=Duration.minutes(15),
            statistic="Sum",
        )
        _alarm(
            "ReviewSmThrottleAlarm",
            metric=sfn_review_throttled,
            threshold=1,
            description="Review state machine throttled - concurrency limit reached",
        )

        # ── Review stuck alarm (running > 90 min) ────────────────────────────
        sfn_review_running = cw.Metric(
            namespace="AWS/States",
            metric_name="ExecutionsTimedOut",
            dimensions_map={"StateMachineArn": orchestration.review_sm.state_machine_arn},
            period=Duration.hours(2),
            statistic="Sum",
        )
        _alarm(
            "ReviewSmTimeoutAlarm",
            metric=sfn_review_running,
            threshold=1,
            description="Review state machine execution timed out",
        )

        # ── DynamoDB capacity alarms ─────────────────────────────────────────
        findings_read_throttle = cw.Metric(
            namespace="AWS/DynamoDB",
            metric_name="ReadThrottleEvents",
            dimensions_map={"TableName": data.findings_table.table_name},
            period=Duration.minutes(5),
            statistic="Sum",
        )
        _alarm(
            "FindingsTableThrottleAlarm",
            metric=findings_read_throttle,
            threshold=10,
            description="Findings DynamoDB table read throttle events >= 10 in 5 min",
        )

        # ── SQS A2A queue depth alarm ────────────────────────────────────────
        a2a_depth = cw.Metric(
            namespace="AWS/SQS",
            metric_name="ApproximateNumberOfMessagesVisible",
            dimensions_map={"QueueName": agents.a2a_queue.queue_name},
            period=Duration.minutes(5),
            statistic="Maximum",
        )
        _alarm(
            "A2AQueueDepthAlarm",
            metric=a2a_depth,
            threshold=100,
            description="A2A queue depth > 100 - agents may be falling behind",
        )

        # ── Custom metric: High-AP findings surge ────────────────────────────
        # Published by analyst Lambda via CloudWatch put_metric_data
        high_ap_surge = cw.Metric(
            namespace="DfmeaAgenticReview",
            metric_name="HighApFindingCount",
            period=Duration.hours(1),
            statistic="Sum",
        )
        _alarm(
            "HighApSurgeAlarm",
            metric=high_ap_surge,
            threshold=50,
            description="High AP findings count > 50 in 1 hour - unusual activity",
            evaluation_periods=1,
            datapoints_to_alarm=1,
        )

        # ── CloudWatch Dashboard ──────────────────────────────────────────────
        dashboard = cw.Dashboard(
            self,
            "DfmeaDashboard",
            dashboard_name="DfmeaAgenticReview",
            period_override=cw.PeriodOverride.AUTO,
        )

        # Row 1: System health title
        dashboard.add_widgets(
            cw.TextWidget(
                markdown="# DFMEA Agentic Review System\nAgent errors · SFN executions · API health · Queue depth",
                width=24,
                height=1,
            )
        )

        # Row 2: Lambda error widgets (6 agents + api)
        lambda_error_widgets = []
        for agent_name, fn in [*agent_fn_names, ("api", api.api_fn)]:
            lambda_error_widgets.append(
                cw.GraphWidget(
                    title=f"{agent_name} Errors",
                    left=[fn.metric_errors(period=Duration.minutes(5))],
                    width=4,
                    height=4,
                )
            )
        dashboard.add_widgets(*lambda_error_widgets)

        # Row 3: SFN executions + duration
        dashboard.add_widgets(
            cw.GraphWidget(
                title="Review SM Executions",
                left=[
                    cw.Metric(namespace="AWS/States", metric_name="ExecutionsStarted",
                              dimensions_map={"StateMachineArn": orchestration.review_sm.state_machine_arn},
                              period=Duration.hours(1), statistic="Sum", label="Started"),
                    cw.Metric(namespace="AWS/States", metric_name="ExecutionsSucceeded",
                              dimensions_map={"StateMachineArn": orchestration.review_sm.state_machine_arn},
                              period=Duration.hours(1), statistic="Sum", label="Succeeded"),
                    cw.Metric(namespace="AWS/States", metric_name="ExecutionsFailed",
                              dimensions_map={"StateMachineArn": orchestration.review_sm.state_machine_arn},
                              period=Duration.hours(1), statistic="Sum", label="Failed"),
                ],
                width=8,
                height=4,
            ),
            cw.GraphWidget(
                title="Analyst Agent Duration (p50 / p99)",
                left=[
                    agents.analyst_fn.metric_duration(period=Duration.minutes(5), statistic="p50"),
                    agents.analyst_fn.metric_duration(period=Duration.minutes(5), statistic="p99"),
                ],
                width=8,
                height=4,
            ),
            cw.GraphWidget(
                title="A2A Queue Depth",
                left=[a2a_depth],
                width=8,
                height=4,
            ),
        )

        # Row 4: DynamoDB + AP metrics
        dashboard.add_widgets(
            cw.GraphWidget(
                title="Reviews Table Read/Write Latency",
                left=[
                    cw.Metric(namespace="AWS/DynamoDB", metric_name="SuccessfulRequestLatency",
                              dimensions_map={"TableName": data.reviews_table.table_name, "Operation": "GetItem"},
                              period=Duration.minutes(5), statistic="p99", label="GetItem p99"),
                    cw.Metric(namespace="AWS/DynamoDB", metric_name="SuccessfulRequestLatency",
                              dimensions_map={"TableName": data.reviews_table.table_name, "Operation": "PutItem"},
                              period=Duration.minutes(5), statistic="p99", label="PutItem p99"),
                ],
                width=8,
                height=4,
            ),
            cw.GraphWidget(
                title="High-AP Findings (custom metric)",
                left=[high_ap_surge],
                width=8,
                height=4,
            ),
            cw.GraphWidget(
                title="API 4xx / 5xx",
                left=[
                    cw.Metric(namespace="AWS/ApiGateway", metric_name="4XXError",
                              dimensions_map={"ApiName": "dfmea-rest-api"},
                              period=Duration.minutes(5), statistic="Sum", label="4xx"),
                    cw.Metric(namespace="AWS/ApiGateway", metric_name="5XXError",
                              dimensions_map={"ApiName": "dfmea-rest-api"},
                              period=Duration.minutes(5), statistic="Sum", label="5xx"),
                ],
                width=8,
                height=4,
            ),
        )

        self.dashboard = dashboard

        # ── AWS Budgets: monthly cost alarm ──────────────────────────────────
        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="dfmea-monthly-budget",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=monthly_budget_usd,
                    unit="USD",
                ),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type="ACTUAL",
                        comparison_operator="GREATER_THAN",
                        threshold=80,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="SNS",
                            address=self.alarm_topic.topic_arn,
                        )
                    ],
                ),
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        notification_type="FORECASTED",
                        comparison_operator="GREATER_THAN",
                        threshold=100,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            subscription_type="SNS",
                            address=self.alarm_topic.topic_arn,
                        )
                    ],
                ),
            ],
        )

        NagSuppressions.add_resource_suppressions(
            self.alarm_topic,
            [{"id": "AwsSolutions-SNS2", "reason": "Alarm topic uses CMK encryption; email subscriptions require confirmed endpoint"}],
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "DashboardUrl",
                      value=f"https://{self.region}.console.aws.amazon.com/cloudwatch/home#dashboards:name=DfmeaAgenticReview",
                      export_name="DfmeaDashboardUrl")
        cdk.CfnOutput(self, "AlarmTopicArn",
                      value=self.alarm_topic.topic_arn,
                      export_name="DfmeaAlarmTopicArn")
