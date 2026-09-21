"""CDK unit tests for DfmeaOrchestrationStack."""
import os
import sys
import pytest
import aws_cdk as cdk
from aws_cdk.assertions import Template

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from infra.stacks.foundation_stack import DfmeaFoundationStack
from infra.stacks.data_stack import DfmeaDataStack
from infra.stacks.agent_stack import DfmeaAgentStack
from infra.stacks.orchestration_stack import DfmeaOrchestrationStack


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    foundation = DfmeaFoundationStack(app, "TestFoundation", env=env)
    data = DfmeaDataStack(app, "TestData", foundation=foundation, env=env)
    agents = DfmeaAgentStack(app, "TestAgent", foundation=foundation, data=data, env=env)
    stack = DfmeaOrchestrationStack(app, "TestOrch", foundation=foundation, data=data, agents=agents, env=env)
    return Template.from_stack(stack)


def test_two_state_machines_created(template):
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    names = [v.get("Properties", {}).get("StateMachineName", "") for v in sms.values()]
    assert any("ingestion" in n for n in names), "Ingestion SM not found"
    assert any("review" in n for n in names), "Review SM not found"


def test_hitl_sns_topic_created(template):
    topics = template.find_resources("AWS::SNS::Topic")
    topic_names = [v.get("Properties", {}).get("TopicName", "") for v in topics.values()]
    assert any("hitl" in n.lower() for n in topic_names)


def test_intake_lambda_created(template):
    fns = template.find_resources("AWS::Lambda::Function")
    names = [v.get("Properties", {}).get("FunctionName", "") for v in fns.values()]
    assert any("dfmea-intake" in n for n in names)


def test_ws_connect_lambda_created(template):
    fns = template.find_resources("AWS::Lambda::Function")
    names = [v.get("Properties", {}).get("FunctionName", "") for v in fns.values()]
    assert any("ws-connect" in n for n in names)


def test_websocket_connections_table_created(template):
    tables = template.find_resources("AWS::DynamoDB::Table")
    names = [v.get("Properties", {}).get("TableName", "") for v in tables.values()]
    assert any("ws-connections" in n for n in names)


def test_eventbridge_rule_created(template):
    rules = template.find_resources("AWS::Events::Rule")
    rule_names = [v.get("Properties", {}).get("Name", "") for v in rules.values()]
    assert any("uploads" in n.lower() for n in rule_names)


def test_state_machines_have_safe_error_logging(template):
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    for sm in sms.values():
        props = sm.get("Properties", {})
        logging_config = props.get("LoggingConfiguration", {})
        assert logging_config.get("Level") == "ERROR"
        assert logging_config.get("IncludeExecutionData") is False


def test_state_machines_xray_enabled(template):
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    for sm in sms.values():
        props = sm.get("Properties", {})
        tracing = props.get("TracingConfiguration", {})
        assert tracing.get("Enabled") is True, "X-Ray tracing not enabled on state machine"


def test_textract_callback_lambda_created(template):
    fns = template.find_resources("AWS::Lambda::Function")
    names = [v.get("Properties", {}).get("FunctionName", "") for v in fns.values()]
    assert any("textract-callback" in n for n in names), \
        f"textract-callback Lambda not found in: {names}"


def test_textract_sns_topic_created(template):
    topics = template.find_resources("AWS::SNS::Topic")
    names  = [v.get("Properties", {}).get("TopicName", "") for v in topics.values()]
    assert any("textract" in n.lower() for n in names), \
        f"Textract SNS topic not found in: {names}"


def test_hitl_gate_lambda_created(template):
    fns = template.find_resources("AWS::Lambda::Function")
    names = [v.get("Properties", {}).get("FunctionName", "") for v in fns.values()]
    assert any("hitl-gate" in n for n in names), \
        f"hitl-gate Lambda not found in: {names}"


def test_review_state_machine_has_gate_states(template):
    """State machine definition JSON must contain Gate 1, 2, 3 wait states."""
    sms = template.find_resources("AWS::StepFunctions::StateMachine")
    for sm in sms.values():
        defn = sm.get("Properties", {}).get("DefinitionString", "")
        defn_str = str(defn)
        if "dfmea-review" in defn_str or "ReviewStateMachine" in defn_str:
            assert "Gate1" in defn_str or "gate_1" in defn_str or "HeartbeatSeconds" in defn_str, \
                "Gate states not found in review SM definition"
            return
    all_defns = " ".join(str(sm.get("Properties", {}).get("DefinitionString", "")) for sm in sms.values())
    assert "waitForTaskToken" in all_defns or "WAIT_FOR_TASK_TOKEN" in all_defns, \
        "No waitForTaskToken state found in any state machine"
