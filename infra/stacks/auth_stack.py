# infra/stacks/auth_stack.py
from __future__ import annotations
import aws_cdk as cdk
from aws_cdk import (
    aws_cognito as cognito,
    RemovalPolicy,
)
from constructs import Construct
from cdk_nag import NagSuppressions
from .foundation_stack import DfmeaFoundationStack


class DfmeaAuthStack(cdk.Stack):
    """Cognito User Pool (email + password, no MFA) + app client for React SPA."""

    def __init__(self, scope: Construct, construct_id: str, foundation: DfmeaFoundationStack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── User Pool ─────────────────────────────────────────────────────────
        self.user_pool = cognito.UserPool(
            self, "DfmeaUserPool",
            user_pool_name="dfmea-user-pool",
            self_sign_up_enabled=False,                      # admin-created users only
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            mfa=cognito.Mfa.OFF,
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_uppercase=True,
                require_lowercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=cdk.Duration.days(1),
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.DESTROY,
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=False),
            ),
        )

        # ── App Client (React SPA — no secret) ───────────────────────────────
        self.app_client = self.user_pool.add_client(
            "DfmeaWebClient",
            user_pool_client_name="dfmea-web-client",
            generate_secret=False,
            prevent_user_existence_errors=True,
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=True, admin_user_password=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.EMAIL, cognito.OAuthScope.OPENID, cognito.OAuthScope.PROFILE],
            ),
            access_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.hours(8),
            id_token_validity=cdk.Duration.hours(1),
            enable_token_revocation=True,
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
        )

        # ── User Pool Domain (for hosted UI if needed) ────────────────────────
        self.user_pool.add_domain(
            "DfmeaDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"dfmea-{self.account}"
            ),
        )

        # ── M2M User Pool (AgentCore → Gateway authentication) ───────────────
        self.m2m_user_pool = cognito.UserPool(
            self, "DfmeaM2MUserPool",
            user_pool_name="dfmea-m2m-userpool",
            self_sign_up_enabled=False,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Resource server — represents the AgentCore Gateway
        self.m2m_resource_server = self.m2m_user_pool.add_resource_server(
            "DfmeaGatewayResourceServer",
            identifier="dfmea-gateway",
            scopes=[cognito.ResourceServerScope(scope_name="tools.invoke", scope_description="Invoke MCP tools via AgentCore Gateway")],
        )

        # M2M app client — client_credentials flow, no user auth
        # auth_flows is omitted: client_credentials is an OAuth flow, not an AuthFlow
        self.m2m_client = self.m2m_user_pool.add_client(
            "DfmeaM2MClient",
            user_pool_client_name="dfmea-agentcore-m2m",
            generate_secret=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[cognito.OAuthScope.resource_server(self.m2m_resource_server, cognito.ResourceServerScope(scope_name="tools.invoke", scope_description="Invoke MCP tools via AgentCore Gateway"))],
            ),
            access_token_validity=cdk.Duration.hours(1),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
        )

        # M2M domain — required for the OAuth2 client_credentials token endpoint
        # that AgentCore Runtimes call to mint Gateway JWTs.
        self.m2m_domain_prefix = f"dfmea-m2m-{self.account}"
        self.m2m_user_pool.add_domain(
            "DfmeaM2MDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=self.m2m_domain_prefix),
        )
        self.m2m_token_url = (
            f"https://{self.m2m_domain_prefix}.auth.{self.region}.amazoncognito.com/oauth2/token"
        )

        # KMS-encrypted Secrets Manager secret for M2M client credentials
        from aws_cdk import aws_secretsmanager as secretsmanager
        self.m2m_secret = secretsmanager.Secret(
            self, "DfmeaM2MClientSecret",
            secret_name="dfmea/m2m-client-secret",
            description="AgentCore M2M Cognito client ID and secret for Gateway authentication",
            encryption_key=foundation.cmk,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── NAG suppressions ─────────────────────────────────────────────────
        NagSuppressions.add_resource_suppressions(
            self.user_pool,
            [
                {"id": "AwsSolutions-COG2", "reason": "MFA intentionally disabled — simple email+password login required"},
                {"id": "AwsSolutions-COG3", "reason": "AdvancedSecurityMode (PLUS tier) intentionally omitted — not required for this deployment"},
            ],
        )

        NagSuppressions.add_resource_suppressions(
            self.m2m_user_pool,
            [
                {"id": "AwsSolutions-COG1", "reason": "M2M pool has no human users — password policy not applicable for service-to-service auth"},
                {"id": "AwsSolutions-COG2", "reason": "M2M pool — MFA not applicable for service-to-service auth"},
                {"id": "AwsSolutions-COG3", "reason": "AdvancedSecurityMode not required for M2M pool"},
            ],
        )
        NagSuppressions.add_resource_suppressions(
            self.m2m_secret,
            [
                {"id": "AwsSolutions-SMG4", "reason": "M2M client secret rotation not required — rotated manually via AgentCore re-registration; automatic rotation would require a custom Lambda rotation function"},
            ],
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id, export_name="DfmeaUserPoolId")
        cdk.CfnOutput(self, "UserPoolClientId", value=self.app_client.user_pool_client_id, export_name="DfmeaUserPoolClientId")
        cdk.CfnOutput(self, "M2MUserPoolId", value=self.m2m_user_pool.user_pool_id, export_name="DfmeaM2MUserPoolId")
        cdk.CfnOutput(self, "M2MClientId", value=self.m2m_client.user_pool_client_id, export_name="DfmeaM2MClientId")
        cdk.CfnOutput(self, "M2MSecretArn", value=self.m2m_secret.secret_arn, export_name="DfmeaM2MSecretArn")
        cdk.CfnOutput(self, "M2MTokenUrl", value=self.m2m_token_url, export_name="DfmeaM2MTokenUrl")
