"""
agents/shared/ontology_client.py — IAM-authenticated Neptune SPARQL client.

Queries use a SigV4-signed POST body so the bytes signed by botocore are the
same bytes sent by urllib. Query/authentication failures propagate to the MCP
handler instead of being misreported as valid empty ontology results.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

NEPTUNE_ENDPOINT = os.environ.get("NEPTUNE_ENDPOINT", "")
NEPTUNE_PORT = int(os.environ.get("NEPTUNE_PORT", "8182"))
ONTOLOGY_PREFIX = "https://dfmea.example.com/ontology/v0.1.0#"

_SPARQL_URL = f"https://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/sparql"
_LOCAL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")


class OntologyClientError(RuntimeError):
    """Raised when an ontology query cannot be executed or decoded."""


def _validated_local_name(value: str, parameter: str) -> str:
    candidate = str(value or "").strip()
    if not _LOCAL_NAME.fullmatch(candidate):
        raise ValueError(
            f"{parameter} must start with a letter and contain only letters, "
            "numbers, underscores, or hyphens"
        )
    return candidate


def _resource_iri(resource_type: str, local_name: str) -> str:
    name = _validated_local_name(local_name, f"{resource_type.lower()}_name")
    return f"{ONTOLOGY_PREFIX}{resource_type}#{name}"


def _class_iri(class_name: str) -> str:
    return f"{ONTOLOGY_PREFIX}{_validated_local_name(class_name, 'class_name')}"


def _aws_region(session) -> str:
    region = (
        session.region_name
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
    )
    if not region:
        raise OntologyClientError("AWS region is unavailable for Neptune SigV4 signing")
    return region


def _sparql_query(sparql: str) -> dict:
    """Execute SPARQL SELECT via a SigV4-signed form POST."""
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    if not NEPTUNE_ENDPOINT:
        raise OntologyClientError("NEPTUNE_ENDPOINT is not configured")

    session = boto3.session.Session()
    credentials = session.get_credentials()
    if credentials is None:
        raise OntologyClientError("AWS credentials are unavailable for Neptune SigV4 signing")

    body = urllib.parse.urlencode({"query": sparql}).encode("utf-8")
    request = AWSRequest(
        method="POST",
        url=_SPARQL_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/sparql-results+json",
        },
    )
    SigV4Auth(
        credentials.get_frozen_credentials(),
        "neptune-db",
        _aws_region(session),
    ).add_auth(request)

    # urllib adds Host itself. Preserve every other signed header, especially
    # X-Amz-Security-Token from Lambda's temporary role credentials.
    headers = {key: value for key, value in request.headers.items() if key.lower() != "host"}
    http_request = urllib.request.Request(
        _SPARQL_URL,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", errors="replace").replace("\n", " ").strip()
        suffix = f": {detail}" if detail else ""
        raise OntologyClientError(
            f"Neptune SPARQL HTTP {exc.code} {exc.reason}{suffix}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OntologyClientError(f"Neptune SPARQL request failed: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise OntologyClientError(f"Neptune returned an invalid SPARQL response: {exc}") from exc


def get_failure_modes_for_class(class_name: str) -> list[dict]:
    """Return failure modes that are rdfs:subClassOf the given ontology class."""
    class_iri = _class_iri(class_name)
    response = _sparql_query(f"""
    PREFIX onto: <{ONTOLOGY_PREFIX}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?fm ?label WHERE {{
        ?fm a onto:FailureMode ;
            rdfs:subClassOf* <{class_iri}> .
        OPTIONAL {{ ?fm rdfs:label ?label }}
    }}
    LIMIT 50
    """)
    return [
        {
            "name": binding["fm"]["value"].rsplit("#", 1)[-1],
            "label": binding.get("label", {}).get(
                "value", binding["fm"]["value"].rsplit("#", 1)[-1]
            ),
        }
        for binding in response["results"]["bindings"]
    ]


def get_failure_modes_for_component(component_name: str) -> list[dict]:
    """Return failure modes linked to a component via onto:hasFailureMode."""
    component_iri = _resource_iri("Component", component_name)
    response = _sparql_query(f"""
    PREFIX onto: <{ONTOLOGY_PREFIX}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?fm ?label WHERE {{
        <{component_iri}> onto:hasFailureMode ?fm .
        OPTIONAL {{ ?fm rdfs:label ?label }}
    }}
    LIMIT 50
    """)
    return [
        {
            "name": binding["fm"]["value"].rsplit("#", 1)[-1],
            "label": binding.get("label", {}).get(
                "value", binding["fm"]["value"].rsplit("#", 1)[-1]
            ),
        }
        for binding in response["results"]["bindings"]
    ]


def get_standards_for_component(component_name: str) -> list[dict]:
    """Return standards linked to a component via onto:mustComplyWith."""
    component_iri = _resource_iri("Component", component_name)
    response = _sparql_query(f"""
    PREFIX onto: <{ONTOLOGY_PREFIX}>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?std ?label ?clause WHERE {{
        <{component_iri}> onto:mustComplyWith ?std .
        OPTIONAL {{ ?std rdfs:label ?label }}
        OPTIONAL {{ ?std onto:clause ?clause }}
    }}
    LIMIT 20
    """)
    return [
        {
            "standard": binding["std"]["value"].rsplit("#", 1)[-1],
            "label": binding.get("label", {}).get(
                "value", binding["std"]["value"].rsplit("#", 1)[-1]
            ),
            "clause": binding.get("clause", {}).get("value", ""),
        }
        for binding in response["results"]["bindings"]
    ]


def get_subclasses(class_name: str) -> list[dict]:
    """Return direct rdfs:subClassOf resources for an ontology class."""
    class_iri = _class_iri(class_name)
    response = _sparql_query(f"""
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?sub ?label WHERE {{
        ?sub rdfs:subClassOf <{class_iri}> .
        OPTIONAL {{ ?sub rdfs:label ?label }}
    }}
    LIMIT 50
    """)
    return [
        {
            "name": binding["sub"]["value"].rsplit("#", 1)[-1],
            "label": binding.get("label", {}).get(
                "value", binding["sub"]["value"].rsplit("#", 1)[-1]
            ),
        }
        for binding in response["results"]["bindings"]
    ]
