# dfmea-prototype/tests/unit/test_neptune_stack.py
import os, sys, pytest
import aws_cdk as cdk
from aws_cdk.assertions import Template

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from infra.stacks.foundation_stack import DfmeaFoundationStack
from infra.stacks.neptune_stack import DfmeaNeptuneStack


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    foundation = DfmeaFoundationStack(app, "TestFoundation", env=env)
    stack = DfmeaNeptuneStack(app, "TestNeptune", foundation=foundation, env=env)
    return Template.from_stack(stack)


def test_neptune_cluster_created(template):
    clusters = template.find_resources("AWS::Neptune::DBCluster")
    assert len(clusters) >= 1, "No Neptune cluster found"


def test_neptune_subnet_group_created(template):
    groups = template.find_resources("AWS::Neptune::DBSubnetGroup")
    assert len(groups) >= 1, "No Neptune subnet group found"


def test_neptune_security_group_no_public(template):
    sgs = template.find_resources("AWS::EC2::SecurityGroup")
    for sg in sgs.values():
        props = sg.get("Properties", {})
        for rule in props.get("SecurityGroupIngress", []):
            assert rule.get("CidrIp") != "0.0.0.0/0", "Neptune SG allows public ingress"


def test_neptune_storage_encrypted(template):
    clusters = template.find_resources("AWS::Neptune::DBCluster")
    for c in clusters.values():
        props = c["Properties"]
        assert props.get("StorageEncrypted") is True, "Neptune storage not encrypted"


def test_neptune_iam_auth_enabled(template):
    clusters = template.find_resources("AWS::Neptune::DBCluster")
    for c in clusters.values():
        props = c["Properties"]
        assert props.get("IamAuthEnabled") is True, "Neptune IAM auth not enabled"


def test_loader_role_created(template):
    roles = template.find_resources("AWS::IAM::Role")
    role_names = [v.get("Properties", {}).get("RoleName", "") for v in roles.values()]
    assert any("loader" in n.lower() or "neptune" in n.lower() for n in role_names), \
        f"No Neptune loader role found in: {role_names}"


def test_outputs_exported(template):
    outputs = template.to_json().get("Outputs", {})
    export_names = [v.get("Export", {}).get("Name", "") for v in outputs.values()]
    assert any("Neptune" in n for n in export_names), "No Neptune outputs found"
