"""CDK unit tests for DfmeaFrontendStack."""
import os
import sys
import pytest
import aws_cdk as cdk
from aws_cdk.assertions import Template

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from infra.stacks.frontend_stack import DfmeaFrontendStack


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    stack = DfmeaFrontendStack(app, "TestFrontend", env=env)
    return Template.from_stack(stack)


def test_hosting_bucket_created(template):
    buckets = template.find_resources("AWS::S3::Bucket")
    bucket_names = [v.get("Properties", {}).get("BucketName", "") for v in buckets.values()]
    assert any("dfmea-frontend" in n for n in bucket_names)


def test_hosting_bucket_ssl_enforced(template):
    policies = template.find_resources("AWS::S3::BucketPolicy")
    found_ssl_deny = False
    for policy in policies.values():
        policy_doc = policy.get("Properties", {}).get("PolicyDocument", {})
        for stmt in policy_doc.get("Statement", []):
            if stmt.get("Effect") == "Deny" and "aws:SecureTransport" in str(stmt):
                found_ssl_deny = True
    assert found_ssl_deny, "S3 bucket policy should deny non-SSL requests"


def test_cloudfront_distribution_created(template):
    dists = template.find_resources("AWS::CloudFront::Distribution")
    assert len(dists) >= 1


def test_cloudfront_https_redirect(template):
    dists = template.find_resources("AWS::CloudFront::Distribution")
    for dist in dists.values():
        cfg = dist.get("Properties", {}).get("DistributionConfig", {})
        default_cache = cfg.get("DefaultCacheBehavior", {})
        protocol_policy = default_cache.get("ViewerProtocolPolicy", "")
        assert protocol_policy == "redirect-to-https"


def test_cloudfront_has_waf(template):
    dists = template.find_resources("AWS::CloudFront::Distribution")
    for dist in dists.values():
        cfg = dist.get("Properties", {}).get("DistributionConfig", {})
        assert "WebACLId" in cfg, "CloudFront distribution should have a WAF WebACL"


def test_waf_webacl_cloudfront_scope(template):
    acls = template.find_resources("AWS::WAFv2::WebACL")
    scopes = [v.get("Properties", {}).get("Scope", "") for v in acls.values()]
    assert "CLOUDFRONT" in scopes


def test_cloudfront_function_created(template):
    fns = template.find_resources("AWS::CloudFront::Function")
    assert len(fns) >= 1


def test_cloudfront_oac_created(template):
    # OAC appears as CloudFront::OriginAccessControl in CDK 2.x
    oacs = template.find_resources("AWS::CloudFront::OriginAccessControl")
    assert len(oacs) >= 1


def test_security_response_headers_policy(template):
    policies = template.find_resources("AWS::CloudFront::ResponseHeadersPolicy")
    assert len(policies) >= 1
    for policy in policies.values():
        props = policy.get("Properties", {})
        cfg = props.get("ResponseHeadersPolicyConfig", {})
        security = cfg.get("SecurityHeadersConfig", {})
        assert "StrictTransportSecurity" in security, "HSTS header not configured"
