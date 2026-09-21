# infra/stacks/foundation_stack.py
from __future__ import annotations
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_kms as kms,
    aws_s3 as s3,
    aws_logs as logs,
    RemovalPolicy,
)
from constructs import Construct
from cdk_nag import NagSuppressions


class DfmeaFoundationStack(cdk.Stack):
    """VPC, KMS CMK, S3 access-log bucket, VPC endpoints for all AWS services used."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── KMS Customer-Managed Key ──────────────────────────────────────────
        self.cmk = kms.Key(
            self, "DfmeaCmk",
            description="DFMEA system customer-managed key",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
        )
        cdk.Tags.of(self.cmk).add("Project", "DfmeaAgenticReview")

        # ── S3 Access Log Bucket (no access logging on itself) ────────────────
        self.access_log_bucket = s3.Bucket(
            self, "AccessLogBucket",
            bucket_name=f"dfmea-access-logs-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=False,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-after-1y",
                    expiration=cdk.Duration.days(365),
                    enabled=True,
                )
            ],
        )
        NagSuppressions.add_resource_suppressions(
            self.access_log_bucket,
            [{"id": "AwsSolutions-S1", "reason": "This bucket IS the access log bucket; circular logging not applicable"}],
        )

        # ── VPC ───────────────────────────────────────────────────────────────
        self.vpc = ec2.Vpc(
            self, "DfmeaVpc",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="Private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24),
                ec2.SubnetConfiguration(name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
            ],
            flow_logs={
                "FlowLogsToCloudWatch": ec2.FlowLogOptions(
                    destination=ec2.FlowLogDestination.to_cloud_watch_logs(
                        log_group=logs.LogGroup(
                            self, "VpcFlowLogs",
                            retention=logs.RetentionDays.ONE_YEAR,
                            removal_policy=RemovalPolicy.DESTROY,
                        )
                    ),
                    traffic_type=ec2.FlowLogTrafficType.ALL,
                )
            },
        )

        # ── VPC Gateway Endpoints ─────────────────────────────────────────────
        self.vpc.add_gateway_endpoint("S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3)
        self.vpc.add_gateway_endpoint("DynamoDbEndpoint", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB)

        # ── VPC Interface Endpoints ───────────────────────────────────────────
        private_subnets = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)
        endpoint_sg = ec2.SecurityGroup(
            self, "VpcEndpointSg",
            vpc=self.vpc,
            description="Allow HTTPS from within VPC to interface endpoints",
            allow_all_outbound=False,
        )
        endpoint_sg.add_ingress_rule(ec2.Peer.ipv4(self.vpc.vpc_cidr_block), ec2.Port.tcp(443))

        interface_services = [
            ("Bedrock", ec2.InterfaceVpcEndpointAwsService.BEDROCK),
            ("BedrockRuntime", ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME),
            ("StepFunctions", ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS),
            ("EventBridge", ec2.InterfaceVpcEndpointAwsService.EVENTBRIDGE),
            ("Sqs", ec2.InterfaceVpcEndpointAwsService.SQS),
            ("Sns", ec2.InterfaceVpcEndpointAwsService.SNS),
            ("Kms", ec2.InterfaceVpcEndpointAwsService.KMS),
            ("CloudwatchLogs", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS),
            ("Rds", ec2.InterfaceVpcEndpointAwsService.RDS),
            ("SecretsManager", ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER),
        ]
        for name, service in interface_services:
            ec2.InterfaceVpcEndpoint(
                self, f"{name}Endpoint",
                vpc=self.vpc,
                service=service,
                subnets=private_subnets,
                security_groups=[endpoint_sg],
                private_dns_enabled=True,
            )

        # NOTE: Neptune does not have a VPC Interface Endpoint service.
        # Neptune clusters run inside the VPC and are accessed directly
        # via their cluster endpoint on port 8182 — no endpoint needed.

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "VpcId", value=self.vpc.vpc_id, export_name="DfmeaVpcId")
        cdk.CfnOutput(self, "CmkArn", value=self.cmk.key_arn, export_name="DfmeaCmkArn")
        cdk.CfnOutput(self, "AccessLogBucketName", value=self.access_log_bucket.bucket_name, export_name="DfmeaAccessLogBucket")
