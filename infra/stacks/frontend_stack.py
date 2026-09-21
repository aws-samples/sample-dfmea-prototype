"""
infra/stacks/frontend_stack.py — Frontend Stack.

Provisions:
  - S3 hosting bucket (private, KMS-encrypted)
  - CloudFront distribution with Origin Access Control (OAC)
  - WAF WebACL on CloudFront (CLOUDFRONT scope — must be us-east-1)
  - Response headers policy (security headers)
  - CloudFront Function for SPA routing (rewrite all paths → /index.html)
"""
from __future__ import annotations
import aws_cdk as cdk
from aws_cdk import (
    aws_s3 as s3,
    aws_cloudfront as cf,
    aws_cloudfront_origins as cf_origins,
    aws_wafv2 as wafv2,
    aws_kms as kms,
    RemovalPolicy,
)
from constructs import Construct
from cdk_nag import NagSuppressions


class DfmeaFrontendStack(cdk.Stack):
    """React SPA hosting via S3 + CloudFront + WAF."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # WAF for CloudFront MUST be in us-east-1 — deploy this stack with env region=us-east-1
        # ── WAF WebACL (CloudFront scope) ─────────────────────────────────────
        cf_waf = wafv2.CfnWebACL(
            self,
            "CfWebACL",
            name="dfmea-cf-waf",
            scope="CLOUDFRONT",
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
                        metric_name="CfCommonRuleSet",
                        sampled_requests_enabled=True,
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesKnownBadInputsRuleSet",
                    priority=2,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS",
                            name="AWSManagedRulesKnownBadInputsRuleSet",
                        )
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="CfBadInputsRuleSet",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="dfmea-cf-waf",
                sampled_requests_enabled=True,
            ),
        )

        # ── S3 hosting bucket ─────────────────────────────────────────────────
        self.hosting_bucket = s3.Bucket(
            self,
            "HostingBucket",
            bucket_name=f"dfmea-frontend-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=False,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        NagSuppressions.add_resource_suppressions(
            self.hosting_bucket,
            [
                {"id": "AwsSolutions-S1", "reason": "Frontend hosting bucket; access logging via CloudFront access logs"},
                {"id": "AwsSolutions-S10", "reason": "SSL enforced via enforce_ssl=True"},
            ],
        )

        # ── CloudFront Origin Access Control ─────────────────────────────────
        oac = cf.S3OriginAccessControl(
            self,
            "HostingOac",
            description="OAC for DFMEA frontend S3 bucket",
        )

        # ── Security response headers policy ─────────────────────────────────
        headers_policy = cf.ResponseHeadersPolicy(
            self,
            "SecurityHeadersPolicy",
            response_headers_policy_name="dfmea-security-headers",
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=cdk.Duration.days(730),
                    include_subdomains=True,
                    preload=True,
                    override=True,
                ),
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cf.ResponseHeadersFrameOptions(
                    frame_option=cf.HeadersFrameOption.DENY,
                    override=True,
                ),
                xss_protection=cf.ResponseHeadersXSSProtection(
                    protection=True,
                    mode_block=True,
                    override=True,
                ),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
            ),
        )

        # ── CloudFront Function: SPA rewrite ──────────────────────────────────
        spa_rewrite_fn = cf.Function(
            self,
            "SpaRewriteFn",
            function_name="dfmea-spa-rewrite",
            code=cf.FunctionCode.from_inline(
                """
function handler(event) {
    var request = event.request;
    var uri = request.uri;
    // Rewrite non-file paths to /index.html for SPA routing
    if (!uri.includes('.')) {
        request.uri = '/index.html';
    }
    return request;
}
"""
            ),
            runtime=cf.FunctionRuntime.JS_2_0,
            comment="Rewrite SPA routes to index.html",
        )

        # ── CloudFront access-log bucket (explicit, DESTROY, auto-emptied) ─────
        # Provide our own so the CDK-auto-created RETAIN log bucket (which would
        # be orphaned on teardown) is never created. CloudFront standard logging
        # writes objects with ACLs, so object ownership must permit ACLs.
        cf_log_bucket = s3.Bucket(
            self,
            "CfLogBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        NagSuppressions.add_resource_suppressions(
            cf_log_bucket,
            [{"id": "AwsSolutions-S1", "reason": "This IS the CloudFront access-log bucket; recursive access logging not applicable"}],
        )

        # ── CloudFront distribution ───────────────────────────────────────────
        self.distribution = cf.Distribution(
            self,
            "CfDistribution",
            comment="DFMEA Frontend SPA",
            default_behavior=cf.BehaviorOptions(
                origin=cf_origins.S3BucketOrigin.with_origin_access_control(
                    self.hosting_bucket,
                    origin_access_control=oac,
                ),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
                response_headers_policy=headers_policy,
                function_associations=[
                    cf.FunctionAssociation(
                        function=spa_rewrite_fn,
                        event_type=cf.FunctionEventType.VIEWER_REQUEST,
                    )
                ],
            ),
            default_root_object="index.html",
            error_responses=[
                cf.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=cdk.Duration.seconds(0),
                ),
                cf.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=cdk.Duration.seconds(0),
                ),
            ],
            web_acl_id=cf_waf.attr_arn,
            minimum_protocol_version=cf.SecurityPolicyProtocol.TLS_V1_2_2021,
            enable_logging=True,
            log_bucket=cf_log_bucket,
        )

        NagSuppressions.add_resource_suppressions(
            self.distribution,
            [
                {"id": "AwsSolutions-CFR4", "reason": "TLSv1.2_2021 is enforced; default cert used for initial deployment"},
                {"id": "AwsSolutions-S1", "reason": "CDK-auto-created CloudFront logging bucket does not need its own access logs (recursive logging)"},
                {"id": "AwsSolutions-S10", "reason": "CDK-auto-created CloudFront logging bucket; SSL enforced on the hosting bucket and distribution"},
            ],
            apply_to_children=True,
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "DistributionUrl",
                      value=f"https://{self.distribution.distribution_domain_name}",
                      export_name="DfmeaFrontendUrl")
        cdk.CfnOutput(self, "HostingBucketName",
                      value=self.hosting_bucket.bucket_name,
                      export_name="DfmeaHostingBucket")
        cdk.CfnOutput(self, "DistributionId",
                      value=self.distribution.distribution_id,
                      export_name="DfmeaDistributionId")
