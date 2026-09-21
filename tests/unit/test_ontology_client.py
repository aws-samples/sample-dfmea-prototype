"""Unit tests for the IAM-authenticated Neptune ontology client."""
import io
import os
import sys
import urllib.error
import urllib.parse
import unittest.mock as mock

import pytest
from botocore.credentials import Credentials

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import agents.shared.ontology_client as oc


def test_prefix_is_correct():
    assert oc.ONTOLOGY_PREFIX == "https://dfmea.example.com/ontology/v0.1.0#"


def test_sparql_transport_uses_signed_post_and_session_token():
    session = mock.MagicMock(region_name="us-east-1")
    session.get_credentials.return_value = Credentials("AKID", "SECRET", "TOKEN")
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = b'{"results":{"bindings":[]}}'

    with mock.patch("boto3.session.Session", return_value=session), \
         mock.patch.object(oc, "NEPTUNE_ENDPOINT", "cluster.example"), \
         mock.patch.object(oc, "_SPARQL_URL", "https://cluster.example:8182/sparql"), \
         mock.patch.object(oc.urllib.request, "urlopen", return_value=response) as urlopen:
        result = oc._sparql_query("SELECT * WHERE { ?s ?p ?o }")

    request = urlopen.call_args.args[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert request.get_method() == "POST"
    assert "?" not in request.full_url
    assert request.data == urllib.parse.urlencode(
        {"query": "SELECT * WHERE { ?s ?p ?o }"}
    ).encode("utf-8")
    assert headers["content-type"] == "application/x-www-form-urlencoded"
    assert headers["x-amz-security-token"] == "TOKEN"
    assert "/us-east-1/neptune-db/aws4_request" in headers["authorization"]
    assert result == {"results": {"bindings": []}}


def test_query_uses_loaded_component_iri_and_has_failure_mode():
    with mock.patch.object(oc, "_sparql_query", return_value={"results": {"bindings": []}}) as query:
        oc.get_failure_modes_for_component("B-Pillar_InnerPanel")
    sparql = query.call_args.args[0]
    assert "hasFailureMode" in sparql
    assert "<https://dfmea.example.com/ontology/v0.1.0#Component#B-Pillar_InnerPanel>" in sparql


def test_bindings_are_mapped():
    bindings = [{
        "fm": {"value": "https://dfmea.example.com/ontology/v0.1.0#FailureMode#buckling_under_load"},
        "label": {"value": "Buckling Under Load"},
    }]
    with mock.patch.object(oc, "_sparql_query", return_value={"results": {"bindings": bindings}}):
        result = oc.get_failure_modes_for_component("B-Pillar_InnerPanel")
    assert result == [{"name": "buckling_under_load", "label": "Buckling Under Load"}]


def test_network_error_propagates():
    with mock.patch.object(oc, "_sparql_query", side_effect=oc.OntologyClientError("network")):
        with pytest.raises(oc.OntologyClientError, match="network"):
            oc.get_failure_modes_for_component("B-Pillar_InnerPanel")


def test_failure_mode_class_uses_rdfs_hierarchy():
    with mock.patch.object(oc, "_sparql_query", return_value={"results": {"bindings": []}}) as query:
        oc.get_failure_modes_for_class("FailureMode")
    assert "rdfs:subClassOf*" in query.call_args.args[0]
    assert "<https://dfmea.example.com/ontology/v0.1.0#FailureMode>" in query.call_args.args[0]


def test_component_standards_use_must_comply_with():
    bindings = [{
        "std": {"value": "https://dfmea.example.com/ontology/v0.1.0#Standard#FMVSS_214"},
        "label": {"value": "FMVSS 214 Side Impact"},
    }]
    with mock.patch.object(oc, "_sparql_query", return_value={"results": {"bindings": bindings}}) as query:
        result = oc.get_standards_for_component("B-Pillar_InnerPanel")
    assert "mustComplyWith" in query.call_args.args[0]
    assert result[0]["standard"] == "FMVSS_214"
    assert result[0]["label"] == "FMVSS 214 Side Impact"


def test_subclasses_use_rdfs_predicates():
    bindings = [{"sub": {"value": "https://dfmea.example.com/ontology/v0.1.0#FailureMode#weld_fracture"}}]
    with mock.patch.object(oc, "_sparql_query", return_value={"results": {"bindings": bindings}}) as query:
        result = oc.get_subclasses("FailureMode")
    assert "rdfs:subClassOf" in query.call_args.args[0]
    assert result[0]["name"] == "weld_fracture"


def test_http_error_exposes_bounded_detail():
    error = urllib.error.HTTPError(
        "https://cluster.example:8182/sparql", 403, "Forbidden", {}, io.BytesIO(b"denied" * 200)
    )
    session = mock.MagicMock(region_name="us-east-1")
    session.get_credentials.return_value = Credentials("AKID", "SECRET", "TOKEN")
    with mock.patch("boto3.session.Session", return_value=session), \
         mock.patch.object(oc, "NEPTUNE_ENDPOINT", "cluster.example"), \
         mock.patch.object(oc, "_SPARQL_URL", "https://cluster.example:8182/sparql"), \
         mock.patch.object(oc.urllib.request, "urlopen", side_effect=error):
        with pytest.raises(oc.OntologyClientError) as caught:
            oc._sparql_query("SELECT * WHERE { ?s ?p ?o }")
    assert "HTTP 403 Forbidden" in str(caught.value)
    assert len(str(caught.value)) < 600


def test_invalid_local_name_is_rejected():
    with pytest.raises(ValueError):
        oc.get_failure_modes_for_component("B-Pillar> ?s ?p ?o")
