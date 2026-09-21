"""CDK unit tests for DfmeaMonitoringStack."""
import os
import sys
import pytest
import aws_cdk as cdk
from aws_cdk.assertions import Template

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from infra.stacks.foundation_stack import DfmeaFoundationStack
from infra.stacks.auth_stack import DfmeaAuthStack
from infra.stacks.data_stack import DfmeaDataStack
from infra.stacks.agent_stack import DfmeaAgentStack
from infra.stacks.orchestration_stack import DfmeaOrchestrationStack
from infra.stacks.api_stack import DfmeaApiStack
from infra.stacks.monitoring_stack import DfmeaMonitoringStack


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    foundation = DfmeaFoundationStack(app, "TestFoundation", env=env)
    auth = DfmeaAuthStack(app, "TestAuth", foundation=foundation, env=env)
    data = DfmeaDataStack(app, "TestData", foundation=foundation, env=env)
    agents = DfmeaAgentStack(app, "TestAgent", foundation=foundation, data=data, env=env)
    orch = DfmeaOrchestrationStack(app, "TestOrch", foundation=foundation, data=data, agents=agents, env=env)
    api = DfmeaApiStack(app, "TestApi", foundation=foundation, auth=auth, data=data, orchestration=orch, env=env)
    stack = DfmeaMonitoringStack(
        app, "TestMonitoring",
        foundation=foundation, data=data, agents=agents, orchestration=orch, api=api,
        ops_email="ops@example.com", monthly_budget_usd=500.0, env=env,
    )
    return Template.from_stack(stack)


def test_cloudwatch_dashboard_created(template):
    dashboards = template.find_resources("AWS::CloudWatch::Dashboard")
    assert len(dashboards) >= 1
    names = [v.get("Properties", {}).get("DashboardName", "") for v in dashboards.values()]
    assert any("DfmeaAgenticReview" in n for n in names)


def test_cloudwatch_alarms_created(template):
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    assert len(alarms) >= 5, f"Expected >= 5 alarms, got {len(alarms)}"


def test_lambda_error_alarms_created(template):
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    alarm_names = [v.get("Properties", {}).get("AlarmName", "") for v in alarms.values()]
    assert any("error" in n.lower() for n in alarm_names)


def test_sfn_failure_alarm_created(template):
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    alarm_names = [v.get("Properties", {}).get("AlarmName", "") for v in alarms.values()]
    assert any("failure" in n.lower() or "sfn" in n.lower() or "sm" in n.lower() for n in alarm_names)


def test_sns_alarm_topic_created(template):
    topics = template.find_resources("AWS::SNS::Topic")
    names = [v.get("Properties", {}).get("TopicName", "") for v in topics.values()]
    assert any("alarm" in n.lower() or "ops" in n.lower() for n in names)


def test_budget_created(template):
    budgets = template.find_resources("AWS::Budgets::Budget")
    assert len(budgets) >= 1
    for budget in budgets.values():
        props = budget.get("Properties", {})
        budget_data = props.get("Budget", {})
        assert budget_data.get("BudgetType") == "COST"
        assert budget_data.get("TimeUnit") == "MONTHLY"


def test_budget_has_notifications(template):
    budgets = template.find_resources("AWS::Budgets::Budget")
    for budget in budgets.values():
        props = budget.get("Properties", {})
        notifications = props.get("NotificationsWithSubscribers", [])
        assert len(notifications) >= 1, "Budget must have at least one notification"


def test_alarms_have_sns_action(template):
    alarms = template.find_resources("AWS::CloudWatch::Alarm")
    for alarm in alarms.values():
        props = alarm.get("Properties", {})
        actions = props.get("AlarmActions", [])
        assert len(actions) >= 1, f"Alarm {props.get('AlarmName')} has no actions"
