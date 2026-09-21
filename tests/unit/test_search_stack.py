# dfmea-prototype/tests/unit/test_search_stack.py
import os, sys, pytest
import aws_cdk as cdk
from aws_cdk.assertions import Template

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from infra.stacks.foundation_stack import DfmeaFoundationStack
from infra.stacks.data_stack import DfmeaDataStack
from infra.stacks.search_stack import DfmeaSearchStack


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    foundation = DfmeaFoundationStack(app, "TestFoundation", env=env)
    data = DfmeaDataStack(app, "TestData", foundation=foundation, env=env)
    stack = DfmeaSearchStack(app, "TestSearch", foundation=foundation, data=data, env=env)
    return Template.from_stack(stack)


def test_aoss_collection_created(template):
    collections = template.find_resources("AWS::OpenSearchServerless::Collection")
    assert len(collections) >= 1, "No AOSS collection found"


def test_aoss_encryption_policy(template):
    policies = template.find_resources("AWS::OpenSearchServerless::SecurityPolicy")
    types = [v.get("Properties", {}).get("Type", "") for v in policies.values()]
    assert "encryption" in types, "No AOSS encryption policy found"


def test_aoss_network_policy(template):
    policies = template.find_resources("AWS::OpenSearchServerless::SecurityPolicy")
    types = [v.get("Properties", {}).get("Type", "") for v in policies.values()]
    assert "network" in types, "No AOSS network policy found"


def test_aoss_data_access_policy(template):
    policies = template.find_resources("AWS::OpenSearchServerless::AccessPolicy")
    assert len(policies) >= 1, "No AOSS data access policy found"


def test_search_indexer_lambda_created(template):
    fns = template.find_resources("AWS::Lambda::Function")
    names = [v.get("Properties", {}).get("FunctionName", "") for v in fns.values()]
    assert any("search-indexer" in n for n in names), \
        f"search-indexer Lambda not found in: {names}"


def test_outputs_exported(template):
    outputs = template.to_json().get("Outputs", {})
    export_names = [v.get("Export", {}).get("Name", "") for v in outputs.values()]
    assert any("Search" in n or "Aoss" in n for n in export_names), \
        f"No Search outputs: {export_names}"
