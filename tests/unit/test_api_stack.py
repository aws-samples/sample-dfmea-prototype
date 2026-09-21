"""CDK unit tests for DfmeaApiStack."""
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


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    foundation = DfmeaFoundationStack(app, "TestFoundation", env=env)
    auth = DfmeaAuthStack(app, "TestAuth", foundation=foundation, env=env)
    data = DfmeaDataStack(app, "TestData", foundation=foundation, env=env)
    agents = DfmeaAgentStack(app, "TestAgent", foundation=foundation, data=data, env=env)
    orch = DfmeaOrchestrationStack(app, "TestOrch", foundation=foundation, data=data, agents=agents, env=env)
    stack = DfmeaApiStack(app, "TestApi", foundation=foundation, auth=auth, data=data, orchestration=orch, env=env)
    return Template.from_stack(stack)


def test_rest_api_created(template):
    apis = template.find_resources("AWS::ApiGateway::RestApi")
    assert len(apis) >= 1
    names = [v.get("Properties", {}).get("Name", "") for v in apis.values()]
    assert any("dfmea" in n.lower() for n in names)


def test_websocket_api_created(template):
    ws_apis = template.find_resources("AWS::ApiGatewayV2::Api")
    props_list = [v.get("Properties", {}) for v in ws_apis.values()]
    protocols = [p.get("ProtocolType", "") for p in props_list]
    assert "WEBSOCKET" in protocols


def test_waf_webacl_created(template):
    acls = template.find_resources("AWS::WAFv2::WebACL")
    assert len(acls) >= 1


def test_waf_associated_with_api(template):
    assocs = template.find_resources("AWS::WAFv2::WebACLAssociation")
    assert len(assocs) >= 1


def test_api_lambda_created(template):
    fns = template.find_resources("AWS::Lambda::Function")
    names = [v.get("Properties", {}).get("FunctionName", "") for v in fns.values()]
    assert any("dfmea-api" in n for n in names)


def test_api_lambda_in_vpc(template):
    fns = template.find_resources("AWS::Lambda::Function")
    for props in fns.values():
        if "dfmea-api" in props.get("Properties", {}).get("FunctionName", ""):
            assert "VpcConfig" in props["Properties"]


def test_cognito_authorizer_created(template):
    authorizers = template.find_resources("AWS::ApiGateway::Authorizer")
    assert len(authorizers) >= 1
    types = [v.get("Properties", {}).get("Type", "") for v in authorizers.values()]
    assert "COGNITO_USER_POOLS" in types


def test_rest_api_stage_logging_enabled(template):
    stages = template.find_resources("AWS::ApiGateway::Stage")
    for stage in stages.values():
        props = stage.get("Properties", {})
        if props.get("StageName") == "v1":
            # MetricsEnabled is nested inside MethodSettings in CDK 2.x
            method_settings = props.get("MethodSettings", [])
            metrics_enabled = any(ms.get("MetricsEnabled") is True for ms in method_settings)
            assert metrics_enabled, "MetricsEnabled not found in MethodSettings for v1 stage"


def test_search_endpoint_route_exists(template):
    """GET /search route must be configured on the REST API."""
    resources = template.find_resources("AWS::ApiGateway::Resource")
    path_parts = [v.get("Properties", {}).get("PathPart", "") for v in resources.values()]
    assert "search" in path_parts, f"No /search resource found. Parts: {path_parts}"


def test_gate_endpoint_resource_exists(template):
    resources = template.find_resources("AWS::ApiGateway::Resource")
    path_parts = [v.get("Properties", {}).get("PathPart", "") for v in resources.values()]
    assert "gate" in path_parts, f"/gate resource not found. Parts: {path_parts}"


def test_gates_list_endpoint_exists(template):
    resources = template.find_resources("AWS::ApiGateway::Resource")
    path_parts = [v.get("Properties", {}).get("PathPart", "") for v in resources.values()]
    assert "gates" in path_parts, f"/gates resource not found. Parts: {path_parts}"
