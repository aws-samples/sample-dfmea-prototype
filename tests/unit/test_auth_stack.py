# tests/unit/test_auth_stack.py
import pytest
import aws_cdk as cdk
from aws_cdk.assertions import Template, Match
from infra.stacks.foundation_stack import DfmeaFoundationStack


@pytest.fixture(scope="module")
def auth_template():
    from infra.stacks.auth_stack import DfmeaAuthStack
    app = cdk.App()
    env = cdk.Environment(account="123456789012", region="us-east-1")
    foundation = DfmeaFoundationStack(app, "TestFoundation", env=env)
    stack = DfmeaAuthStack(app, "TestAuth", foundation=foundation, env=env)
    return Template.from_stack(stack)


def test_user_pool_no_self_signup(auth_template):
    """R8: No self-registration allowed."""
    auth_template.has_resource_properties("AWS::Cognito::UserPool", {
        "AdminCreateUserConfig": {
            "AllowAdminCreateUserOnly": True,
        }
    })


def test_user_pool_mfa_required(auth_template):
    auth_template.has_resource_properties("AWS::Cognito::UserPool", {
        "MfaConfiguration": "ON",
        "EnabledMfas": Match.array_with(["SOFTWARE_TOKEN_MFA"]),
    })


def test_user_pool_password_policy(auth_template):
    auth_template.has_resource_properties("AWS::Cognito::UserPool", {
        "Policies": {
            "PasswordPolicy": {
                "MinimumLength": Match.any_value(),
                "RequireUppercase": True,
                "RequireLowercase": True,
                "RequireNumbers": True,
                "RequireSymbols": True,
            }
        }
    })


def test_user_pool_advanced_security(auth_template):
    auth_template.has_resource_properties("AWS::Cognito::UserPool", {
        "UserPoolAddOns": {
            "AdvancedSecurityMode": "ENFORCED",
        }
    })


def test_user_pool_client_no_secret(auth_template):
    """Public SPA client must not have a client secret."""
    auth_template.has_resource_properties("AWS::Cognito::UserPoolClient", {
        "GenerateSecret": False,
        "PreventUserExistenceErrors": "ENABLED",
    })


def test_user_pool_client_token_validity(auth_template):
    # CDK 2.x stores Duration.hours() as minutes in the CFN template
    auth_template.has_resource_properties("AWS::Cognito::UserPoolClient", {
        "AccessTokenValidity": 60,      # 1 hour in minutes
        "RefreshTokenValidity": 480,    # 8 hours in minutes
        "TokenValidityUnits": {
            "AccessToken": "minutes",
            "RefreshToken": "minutes",
        }
    })
