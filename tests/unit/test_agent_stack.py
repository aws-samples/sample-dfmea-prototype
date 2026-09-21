"""CDK unit tests for DfmeaAgentStack."""
import os
import sys
import pytest
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from infra.stacks.foundation_stack import DfmeaFoundationStack
from infra.stacks.data_stack import DfmeaDataStack
from infra.stacks.agent_stack import DfmeaAgentStack


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    foundation = DfmeaFoundationStack(app, "TestFoundation", env=env)
    data = DfmeaDataStack(app, "TestData", foundation=foundation, env=env)
    stack = DfmeaAgentStack(app, "TestAgent", foundation=foundation, data=data, env=env)
    return Template.from_stack(stack)


def test_five_lambda_functions_created(template):
    fns = template.find_resources("AWS::Lambda::Function")
    fn_names = [
        v.get("Properties", {}).get("FunctionName", "")
        for v in fns.values()
    ]
    expected = ["dfmea-invoke-analyst", "dfmea-invoke-failure-mode",
                "dfmea-invoke-structural", "dfmea-invoke-regulatory", "dfmea-invoke-other"]
    for name in expected:
        assert any(name in n for n in fn_names), f"Lambda {name} not found"


def test_a2a_sqs_queue_created(template):
    queues = template.find_resources("AWS::SQS::Queue")
    queue_names = [v.get("Properties", {}).get("QueueName", "") for v in queues.values()]
    assert any("dfmea-a2a" in n for n in queue_names)


def test_agent_lambdas_in_vpc(template):
    fns = template.find_resources("AWS::Lambda::Function")
    for props in fns.values():
        if "dfmea-invoke" in props.get("Properties", {}).get("FunctionName", ""):
            assert "VpcConfig" in props["Properties"]


def test_agent_lambda_kms_encrypted_env(template):
    fns = template.find_resources("AWS::Lambda::Function")
    for props in fns.values():
        fn_name = props.get("Properties", {}).get("FunctionName", "")
        if "dfmea-invoke" in fn_name:
            assert "KmsKeyArn" in props["Properties"]


def test_dlq_created(template):
    queues = template.find_resources("AWS::SQS::Queue")
    queue_names = [v.get("Properties", {}).get("QueueName", "") for v in queues.values()]
    assert any("dlq" in n.lower() for n in queue_names)


def test_ml_models_bucket_env_var_present(template):
    fns = template.find_resources("AWS::Lambda::Function")
    for props in fns.values():
        fn_name = props.get("Properties", {}).get("FunctionName", "")
        if "dfmea-invoke" in fn_name:
            env_vars = props.get("Properties", {}).get("Environment", {}).get("Variables", {})
            assert "ML_MODELS_BUCKET_NAME" in env_vars, f"ML_MODELS_BUCKET_NAME missing from {fn_name}"
