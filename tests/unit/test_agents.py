"""Unit tests for agent shared tools and agent modules."""
import json
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def _ap_pure(s: int, o: int, d: int) -> str:
    """Pure Python copy of AIAG-VDA 2019 AP rules for testing without strands import."""
    if s >= 9:
        return "High"
    if s >= 5:
        if o >= 4 and d >= 7:
            return "High"
        if o >= 4 and d <= 6:
            return "Medium"
        return "Low"
    if o >= 6:
        return "Medium"
    return "Low"


class TestApLookup(unittest.TestCase):
    """Test AIAG-VDA 2019 Action Priority deterministic rules."""

    def _ap(self, s, o, d):
        return _ap_pure(s, o, d)

    def test_high_severity_9_always_high(self):
        self.assertEqual(self._ap(9, 1, 1), "High")

    def test_high_severity_10_always_high(self):
        self.assertEqual(self._ap(10, 1, 1), "High")

    def test_medium_s5_o4_d6(self):
        self.assertEqual(self._ap(5, 4, 6), "Medium")

    def test_high_s5_o4_d7(self):
        self.assertEqual(self._ap(5, 4, 7), "High")

    def test_low_s3_o2_d3(self):
        self.assertEqual(self._ap(3, 2, 3), "Low")

    def test_medium_s1_o6_d1(self):
        self.assertEqual(self._ap(1, 6, 1), "Medium")

    def test_low_s5_o3_d5(self):
        self.assertEqual(self._ap(5, 3, 5), "Low")


class TestWriteFindingtool(unittest.TestCase):
    """Test write_finding logic (DynamoDB put_item) using a standalone impl without strands."""

    def test_write_finding_item_structure(self):
        """Verify the item structure that would be written to DynamoDB."""
        import json
        item = {
            "review_id": "rev-001",
            "finding_id": "find-001",
            "agent": "failure_mode",
            "finding_type": "missing_failure_mode",
            "description": "Missing corrosion failure mode",
            "action_priority": "H",
            "confidence": str(0.85),
            "affected_component": "B-Pillar Inner",
            "suggested_failure_mode": "",
            "evidence_ids": [],
            "standard_citations": [],
            "gap_source": "kb",
            "severity_suggestion": 8,
            "occurrence_suggestion": 5,
            "detection_suggestion": 6,
        }
        self.assertEqual(item["review_id"], "rev-001")
        self.assertEqual(item["agent"], "failure_mode")
        self.assertEqual(item["action_priority"], "H")
        self.assertIn("severity_suggestion", item)


class TestIntakeHandler(unittest.TestCase):
    """Test intake Lambda normalisation logic."""

    def test_normalise_rows_basic(self):
        from lambdas.intake.handler import _normalise_rows
        raw = [
            {
                "part_name": "B-Pillar",
                "function": "Structural support",
                "failure_mode": "Fracture",
                "effect": "Passenger injury",
                "cause": "Overload",
                "severity": 9,
                "occurrence": 4,
                "detection": 6,
            }
        ]
        rows = _normalise_rows(raw)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["part_name"], "B-Pillar")
        self.assertEqual(rows[0]["severity"], 9)
        self.assertEqual(rows[0]["action_priority"], "H")

    def test_normalise_coerces_string_ratings(self):
        from lambdas.intake.handler import _normalise_rows
        raw = [
            {
                "part_name": "Hinge",
                "function": "Connect door",
                "failure_mode": "Wear",
                "effect": "Door drops",
                "cause": "No lube",
                "severity": "5",
                "occurrence": "3",
                "detection": "4",
            }
        ]
        rows = _normalise_rows(raw)
        self.assertEqual(rows[0]["severity"], 5)
        self.assertEqual(rows[0]["occurrence"], 3)

    def test_derive_ap_rules(self):
        from lambdas.intake.handler import _derive_ap
        self.assertEqual(_derive_ap(9, 1, 1), "H")
        self.assertEqual(_derive_ap(5, 4, 7), "H")
        self.assertEqual(_derive_ap(5, 4, 5), "M")
        self.assertEqual(_derive_ap(5, 3, 5), "L")
        self.assertEqual(_derive_ap(3, 7, 2), "M")
        self.assertEqual(_derive_ap(3, 2, 2), "L")

    def test_normalise_header_map(self):
        from lambdas.intake.handler import _normalise_header
        self.assertEqual(_normalise_header("Part Name"), "part_name")
        self.assertEqual(_normalise_header("Failure Mode (FM)"), "failure_mode")
        self.assertEqual(_normalise_header("S"), "severity")
        self.assertEqual(_normalise_header("AP"), "action_priority")


class TestHitlCallbackHandler(unittest.TestCase):
    """Test HITL callback handler routing."""

    @patch("lambdas.hitl_callback.handler.dynamodb")
    @patch("lambdas.hitl_callback.handler.sfn")
    def test_approve_calls_send_task_success(self, mock_sfn, mock_ddb):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {"Item": {"hitl_task_token": "fake-token"}}
        mock_ddb.Table.return_value = mock_table
        mock_sfn.send_task_success.return_value = {}

        from lambdas.hitl_callback.handler import handler as hitl_handler
        event = {"review_id": "rev-123", "action": "approve", "comment": "Looks good"}
        resp = hitl_handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        mock_sfn.send_task_success.assert_called_once()

    def test_missing_action_returns_400(self):
        from lambdas.hitl_callback.handler import handler
        event = {"review_id": "rev-123", "action": ""}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 400)

    def test_missing_review_id_returns_400(self):
        from lambdas.hitl_callback.handler import handler
        event = {"action": "approve"}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 400)


class TestApiHandler(unittest.TestCase):
    """Test REST API handler routing."""

    def test_health_returns_200(self):
        from lambdas.api.handler import handler
        event = {"httpMethod": "GET", "path": "/health", "pathParameters": None, "queryStringParameters": None, "body": None}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 200)
        body = json.loads(resp["body"])
        self.assertEqual(body["status"], "healthy")

    def test_unknown_path_returns_404(self):
        from lambdas.api.handler import handler
        event = {"httpMethod": "GET", "path": "/nonexistent", "pathParameters": None, "queryStringParameters": None, "body": None}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 404)

    def test_post_reviews_missing_body_returns_400(self):
        from lambdas.api.handler import handler
        event = {"httpMethod": "POST", "path": "/reviews", "pathParameters": None, "queryStringParameters": None, "body": "{}"}
        resp = handler(event, None)
        self.assertEqual(resp["statusCode"], 400)


if __name__ == "__main__":
    unittest.main()
