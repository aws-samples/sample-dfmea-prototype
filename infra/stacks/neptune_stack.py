"""
infra/stacks/neptune_stack.py — Neptune Serverless Graph Store Stack.

Provisions:
  - Neptune Serverless cluster (IAM auth, encrypted with CMK)
  - Neptune DBSubnetGroup in VPC private subnets
  - Security Group (port 8182, VPC-only, no public ingress)
  - IAM Role for bulk loader (rds.amazonaws.com principal, S3 read)
  - IAM ManagedPolicy for Neptune query access (neptune-db:connect + ReadDataViaQuery)
  - CfnOutputs: NeptuneEndpoint, NeptunePort, NeptuneLoaderRoleArn
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_neptune as neptune_l1,
    RemovalPolicy,
)
from constructs import Construct
from cdk_nag import NagSuppressions

from .foundation_stack import DfmeaFoundationStack


class DfmeaNeptuneStack(cdk.Stack):
    """Neptune Serverless graph store — ontology and DFMEA relationship layer."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        foundation: DfmeaFoundationStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc = foundation.vpc
        cmk = foundation.cmk

        # ── Security Group ────────────────────────────────────────────────────
        self.neptune_sg = ec2.SecurityGroup(
            self, "NeptuneSg",
            vpc=vpc,
            description="Neptune graph DB - allow port 8182 from within VPC",
            allow_all_outbound=False,
        )
        self.neptune_sg.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(8182),
            "Neptune Gremlin/SPARQL from VPC",
        )

        # ── Subnet Group ──────────────────────────────────────────────────────
        private_subnet_ids = vpc.select_subnets(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        ).subnet_ids

        subnet_group = neptune_l1.CfnDBSubnetGroup(
            self, "NeptuneSubnetGroup",
            db_subnet_group_description="DFMEA Neptune private subnet group",
            subnet_ids=private_subnet_ids,
            db_subnet_group_name="dfmea-neptune-subnet-group",
        )

        # ── Neptune Serverless Cluster ─────────────────────────────────────────
        self.cluster = neptune_l1.CfnDBCluster(
            self, "NeptuneCluster",
            storage_encrypted=True,
            kms_key_id=cmk.key_arn,
            iam_auth_enabled=True,
            db_subnet_group_name=subnet_group.ref,
            vpc_security_group_ids=[self.neptune_sg.security_group_id],
            deletion_protection=False,
            backup_retention_period=7,
            serverless_scaling_configuration=neptune_l1.CfnDBCluster.ServerlessScalingConfigurationProperty(
                min_capacity=1,
                max_capacity=8,
            ),
        )
        self.cluster.apply_removal_policy(RemovalPolicy.DESTROY)
        # Ensure subnet group is created before the cluster
        self.cluster.add_dependency(subnet_group)

        # ── Neptune Serverless DB Instance ────────────────────────────────────
        # A Neptune cluster has no live endpoint until at least one instance exists.
        # db.serverless is required for the serverless_scaling_configuration above.
        neptune_instance = neptune_l1.CfnDBInstance(
            self, "NeptuneInstance",
            db_instance_class="db.serverless",
            db_cluster_identifier=self.cluster.ref,
            auto_minor_version_upgrade=True,
        )
        neptune_instance.apply_removal_policy(RemovalPolicy.DESTROY)
        neptune_instance.add_dependency(self.cluster)

        # ── IAM: Bulk Loader Role (rds.amazonaws.com → S3 read) ───────────────
        self.loader_role = iam.Role(
            self, "NeptuneLoaderRole",
            role_name="dfmea-neptune-loader",
            assumed_by=iam.ServicePrincipal("rds.amazonaws.com"),
            description="Neptune bulk loader role - reads from S3",
        )
        self.loader_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:ListBucket"],
                resources=["*"],
            )
        )

        NagSuppressions.add_resource_suppressions(
            self.loader_role,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Neptune bulk loader requires broad S3 read; "
                              "scoped to read-only actions (GetObject, ListBucket)",
                }
            ],
            apply_to_children=True,
        )

        # ── IAM: Neptune Access Managed Policy (used by agents / Lambda) ──────
        self.neptune_access_policy = iam.ManagedPolicy(
            self, "NeptuneAccessPolicy",
            managed_policy_name="dfmea-neptune-access",
            description="Allow Neptune graph query access for DFMEA agents",
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "neptune-db:connect",
                        "neptune-db:ReadDataViaQuery",
                    ],
                    resources=[
                        f"arn:aws:neptune-db:{self.region}:{self.account}:"
                        f"{self.cluster.attr_cluster_resource_id}/*"
                    ],
                )
            ],
        )

        NagSuppressions.add_resource_suppressions(
            self.neptune_access_policy,
            [{"id": "AwsSolutions-IAM5", "reason": "Neptune IAM auth requires cluster-resource-id/* wildcard — this is the correct and only supported Neptune IAM policy resource format"}],
        )

        # ── Public endpoint attribute (used by agent_stack wiring) ────────────
        self.neptune_endpoint = self.cluster.attr_endpoint

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self, "NeptuneEndpoint",
            value=self.cluster.attr_endpoint,
            export_name="DfmeaNeptuneEndpoint",
        )
        cdk.CfnOutput(
            self, "NeptunePort",
            value=self.cluster.attr_port,
            export_name="DfmeaNeptunePort",
        )
        cdk.CfnOutput(
            self, "NeptuneLoaderRoleArn",
            value=self.loader_role.role_arn,
            export_name="DfmeaNeptuneLoaderRoleArn",
        )
