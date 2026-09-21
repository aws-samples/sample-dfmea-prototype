# tests/integration/conftest.py
"""
Session-scoped fixtures for integration tests.
Auto-fetches API URL and Cognito config from CloudFormation outputs.
Creates a temporary Cognito test user and cleans it up after all tests.
"""
import uuid
import pytest
import boto3
import requests
from botocore.exceptions import ClientError


# ── CloudFormation helpers ─────────────────────────────────────────────────

def _cfn_output(stack_name: str, output_key: str) -> str:
    """Return a CloudFormation stack output value by CDK logical ID."""
    cf = boto3.client("cloudformation")
    try:
        resp = cf.describe_stacks(StackName=stack_name)
    except ClientError as e:
        if "does not exist" in str(e):
            pytest.skip(f"Stack {stack_name!r} not deployed — skipping integration tests")
        raise
    outputs = {o["OutputKey"]: o["OutputValue"] for o in resp["Stacks"][0].get("Outputs", [])}
    if output_key not in outputs:
        pytest.skip(f"Output {output_key!r} not found in {stack_name} — skipping")
    return outputs[output_key]


# ── Session fixtures ───────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def api_url() -> str:
    """REST API base URL from DfmeaApiStack CloudFormation output."""
    url = _cfn_output("DfmeaApiStack", "RestApiUrl")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def cognito_ids() -> dict:
    """User Pool ID and App Client ID from DfmeaAuthStack."""
    return {
        "user_pool_id":  _cfn_output("DfmeaAuthStack", "UserPoolId"),
        "client_id":     _cfn_output("DfmeaAuthStack", "UserPoolClientId"),
    }


@pytest.fixture(scope="session")
def test_user(cognito_ids, request) -> tuple[str, str]:
    """
    Creates a temporary Cognito user for the test session.
    Cleans up via finalizer even if tests fail.
    """
    uid = uuid.uuid4().hex[:8]
    username = f"dfmea-inttest-{uid}@test.invalid"
    password = f"Inttest-{uid}-Aa1!"  # meets Cognito complexity requirements

    idp = boto3.client("cognito-idp")
    pool_id = cognito_ids["user_pool_id"]

    idp.admin_create_user(
        UserPoolId=pool_id,
        Username=username,
        TemporaryPassword=password,
        MessageAction="SUPPRESS",
    )
    idp.admin_set_user_password(
        UserPoolId=pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )

    def cleanup():
        try:
            idp.admin_delete_user(UserPoolId=pool_id, Username=username)
        except ClientError as e:
            if e.response["Error"]["Code"] != "UserNotFoundException":
                raise

    request.addfinalizer(cleanup)
    return username, password


@pytest.fixture(scope="session")
def auth_token(test_user, cognito_ids) -> str:
    """ID token for the test user, obtained via USER_PASSWORD_AUTH."""
    username, password = test_user
    idp = boto3.client("cognito-idp")
    resp = idp.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
        ClientId=cognito_ids["client_id"],
    )
    return resp["AuthenticationResult"]["IdToken"]


# ── ApiClient helper ───────────────────────────────────────────────────────

class ApiClient:
    """Thin wrapper around requests with base URL and auth header pre-set."""

    def __init__(self, base_url: str, token: str):
        self._base = base_url
        self._headers = {"Authorization": token, "Content-Type": "application/json"}

    def get(self, path: str, **kwargs):
        return requests.get(f"{self._base}{path}", headers=self._headers, **kwargs)

    def post(self, path: str, **kwargs):
        return requests.post(f"{self._base}{path}", headers=self._headers, **kwargs)

    def delete(self, path: str, **kwargs):
        return requests.delete(f"{self._base}{path}", headers=self._headers, **kwargs)

    def get_no_auth(self, path: str, **kwargs):
        return requests.get(f"{self._base}{path}", **kwargs)


@pytest.fixture(scope="session")
def api(api_url, auth_token) -> ApiClient:
    return ApiClient(api_url, auth_token)
