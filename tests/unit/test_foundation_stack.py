# tests/unit/test_foundation_stack.py
import pytest
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match


@pytest.fixture(scope="module")
def foundation_template():
    from infra.stacks.foundation_stack import DfmeaFoundationStack
    app = cdk.App()
    stack = DfmeaFoundationStack(app, "TestFoundation", env=cdk.Environment(account="123456789012", region="us-east-1"))
    return Template.from_stack(stack)


def test_vpc_cidr(foundation_template):
    foundation_template.has_resource_properties("AWS::EC2::VPC", {
        "CidrBlock": "10.0.0.0/16",
        "EnableDnsHostnames": True,
        "EnableDnsSupport": True,
    })


def test_private_subnets_created(foundation_template):
    # Two private subnets (one per AZ)
    subnets = foundation_template.find_resources("AWS::EC2::Subnet")
    private = [s for s in subnets.values() if not s["Properties"].get("MapPublicIpOnLaunch", False)]
    assert len(private) >= 2, "Expected at least 2 private subnets"


def test_kms_key_with_rotation(foundation_template):
    foundation_template.has_resource_properties("AWS::KMS::Key", {
        "EnableKeyRotation": True,
        "KeyPolicy": Match.any_value(),
    })


def test_s3_access_log_bucket_encrypted(foundation_template):
    # Access log bucket must have server-side encryption
    foundation_template.has_resource_properties("AWS::S3::Bucket", {
        "BucketEncryption": Match.any_value(),
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        },
    })


def test_vpc_endpoint_s3_exists(foundation_template):
    # ServiceName is Fn::Join in CFN — check for Gateway endpoints and service name content
    endpoints = foundation_template.find_resources("AWS::EC2::VPCEndpoint")
    gateway_eps = [v for v in endpoints.values() if v["Properties"].get("VpcEndpointType") == "Gateway"]
    s3_eps = [
        ep for ep in gateway_eps
        if "s3" in str(ep["Properties"].get("ServiceName", "")).lower()
    ]
    assert len(s3_eps) >= 1, "No S3 Gateway VPC endpoint found"


def test_vpc_endpoint_dynamodb_exists(foundation_template):
    endpoints = foundation_template.find_resources("AWS::EC2::VPCEndpoint")
    gateway_eps = [v for v in endpoints.values() if v["Properties"].get("VpcEndpointType") == "Gateway"]
    ddb_eps = [
        ep for ep in gateway_eps
        if "dynamodb" in str(ep["Properties"].get("ServiceName", "")).lower()
    ]
    assert len(ddb_eps) >= 1, "No DynamoDB Gateway VPC endpoint found"


def test_rds_vpc_endpoint_created(foundation_template):
    endpoints = foundation_template.find_resources("AWS::EC2::VPCEndpoint")
    service_names = []
    for ep in endpoints.values():
        props = ep.get("Properties", {})
        sn = props.get("ServiceName", "")
        if isinstance(sn, str):
            service_names.append(sn)
        elif isinstance(sn, dict):
            service_names.append(str(sn))
    assert any("rds" in s.lower() for s in service_names), \
        f"No RDS VPC endpoint found in: {service_names}"


def test_neptune_vpc_endpoint_created(foundation_template):
    endpoints = foundation_template.find_resources("AWS::EC2::VPCEndpoint")
    service_names = []
    for ep in endpoints.values():
        props = ep.get("Properties", {})
        sn = props.get("ServiceName", "")
        service_names.append(str(sn))
    assert any("neptune" in s.lower() for s in service_names), \
        f"No Neptune VPC endpoint found in: {service_names}"
