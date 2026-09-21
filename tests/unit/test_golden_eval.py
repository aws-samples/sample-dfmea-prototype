"""Unit tests for the golden evaluation set and run_golden_eval logic."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

EVAL_DIR = Path(__file__).parent.parent.parent / "eval"


class TestGoldenEvalSet(unittest.TestCase):
    """Validate golden_eval_set.json structure and content."""

    @classmethod
    def setUpClass(cls):
        with open(EVAL_DIR / "golden_eval_set.json") as f:
            cls.golden = json.load(f)
        cls.cases = cls.golden["cases"]

    def test_golden_set_loads(self):
        self.assertIn("cases", self.golden)
        self.assertIn("version", self.golden)

    def test_ten_cases_present(self):
        self.assertEqual(len(self.cases), 10)

    def test_all_cases_have_required_fields(self):
        for case in self.cases:
            for field in ["case_id", "description", "assembly", "variant_type",
                          "input_rows", "expected_findings"]:
                self.assertIn(field, case, f"Case {case.get('case_id')} missing {field}")

    def test_all_input_rows_have_sod(self):
        for case in self.cases:
            for row in case["input_rows"]:
                for field in ["severity", "occurrence", "detection"]:
                    self.assertIn(field, row, f"Row in {case['case_id']} missing {field}")
                    v = row[field]
                    self.assertGreaterEqual(int(v), 1)
                    self.assertLessEqual(int(v), 10)

    def test_ap_values_valid(self):
        for case in self.cases:
            for row in case["input_rows"]:
                if "action_priority" in row:
                    self.assertIn(row["action_priority"], ("H", "M", "L"))

    def test_expected_findings_have_required_fields(self):
        for case in self.cases:
            for exp in case["expected_findings"]:
                self.assertIn("finding_type", exp)
                self.assertIn("agent", exp)
                self.assertIn("confidence_min", exp)
                self.assertGreaterEqual(exp["confidence_min"], 0.0)
                self.assertLessEqual(exp["confidence_min"], 1.0)

    def test_all_six_variant_types_covered(self):
        variant_types = {case["variant_type"] for case in self.cases}
        expected_types = {"missing_failure_mode", "under_scored", "over_scored",
                          "missing_citation", "structural_gap", "anomalous_sod", "valid"}
        for vt in {"missing_failure_mode", "under_scored", "over_scored",
                   "missing_citation", "structural_gap", "anomalous_sod"}:
            self.assertIn(vt, variant_types, f"Variant type {vt} not covered in golden eval")

    def test_evaluation_thresholds_present(self):
        thresholds = self.golden.get("evaluation_thresholds", {})
        for key in ["precision_min", "recall_min", "f1_min", "false_positive_rate_max"]:
            self.assertIn(key, thresholds)

    def test_valid_case_expects_zero_findings(self):
        valid_cases = [c for c in self.cases if c["variant_type"] == "valid"]
        self.assertGreaterEqual(len(valid_cases), 1)
        for case in valid_cases:
            self.assertEqual(len(case["expected_findings"]), 0,
                             "Valid case should expect zero findings")


class TestGoldenEvalLogic(unittest.TestCase):
    """Test evaluation matching logic."""

    def setUp(self):
        from eval.run_golden_eval import evaluate_case, aggregate_metrics
        self.evaluate_case = evaluate_case
        self.aggregate_metrics = aggregate_metrics

    def test_perfect_match(self):
        case = {
            "case_id": "TEST-001",
            "variant_type": "missing_failure_mode",
            "expected_findings": [
                {"finding_type": "missing_failure_mode", "agent": "failure_mode",
                 "action_priority": "H", "confidence_min": 0.5,
                 "description_contains": ["corrosion"]}
            ],
            "expected_finding_count_min": 1,
        }
        actual = [
            {"finding_type": "missing_failure_mode", "agent": "failure_mode",
             "action_priority": "H", "confidence": 0.8,
             "description": "Missing corrosion failure mode on inner panel"}
        ]
        result = self.evaluate_case(case, actual)
        self.assertEqual(result["true_positives"], 1)
        self.assertEqual(result["false_positives"], 0)
        self.assertEqual(result["false_negatives"], 0)
        self.assertEqual(result["precision"], 1.0)
        self.assertEqual(result["recall"], 1.0)

    def test_false_negative(self):
        case = {
            "case_id": "TEST-002",
            "variant_type": "under_scored",
            "expected_findings": [
                {"finding_type": "under_scored", "agent": "failure_mode",
                 "action_priority": "H", "confidence_min": 0.5,
                 "description_contains": []}
            ],
            "expected_finding_count_min": 1,
        }
        result = self.evaluate_case(case, [])  # no actual findings
        self.assertEqual(result["true_positives"], 0)
        self.assertEqual(result["false_negatives"], 1)
        self.assertEqual(result["recall"], 0.0)

    def test_false_positive(self):
        case = {
            "case_id": "TEST-003",
            "variant_type": "valid",
            "expected_findings": [],
            "expected_finding_count_min": 0,
            "expected_finding_count_max": 1,
        }
        actual = [
            {"finding_type": "missing_failure_mode", "agent": "failure_mode",
             "action_priority": "M", "confidence": 0.6, "description": "Spurious finding"}
        ]
        result = self.evaluate_case(case, actual)
        self.assertEqual(result["false_positives"], 1)
        self.assertEqual(result["true_positives"], 0)

    def test_aggregate_metrics(self):
        results = [
            {"precision": 1.0, "recall": 1.0, "f1": 1.0,
             "true_positives": 2, "false_positives": 0, "false_negatives": 0, "passed": True},
            {"precision": 0.5, "recall": 0.5, "f1": 0.5,
             "true_positives": 1, "false_positives": 1, "false_negatives": 1, "passed": False},
        ]
        agg = self.aggregate_metrics(results)
        self.assertEqual(agg["cases_total"], 2)
        self.assertEqual(agg["cases_passed"], 1)
        self.assertAlmostEqual(agg["avg_precision"], 0.75, places=2)

    def test_dry_run_passes(self):
        """Golden eval set passes structural validation."""
        from eval.run_golden_eval import dry_run
        exit_code = dry_run()
        self.assertEqual(exit_code, 0, "dry_run() should return 0 for valid golden eval set")


class TestAuditHandler(unittest.TestCase):
    """Unit tests for audit package Lambda."""

    def test_missing_review_id_returns_400(self):
        from lambdas.audit.handler import handler
        resp = handler({"pathParameters": None}, None)
        self.assertEqual(resp["statusCode"], 400)

    def test_build_manifest_structure(self):
        from lambdas.audit.handler import _build_manifest
        files = {
            "findings.json": b'{"findings": []}',
            "review-metadata.json": b'{"review_id": "r1"}',
        }
        manifest_bytes = _build_manifest(files, "r1")
        manifest = json.loads(manifest_bytes)
        self.assertEqual(manifest["review_id"], "r1")
        self.assertIn("findings.json", manifest["files"])
        self.assertIn("sha256", manifest["files"]["findings.json"])
        self.assertEqual(manifest["files"]["findings.json"]["size_bytes"], len(files["findings.json"]))

    def test_build_zip_produces_valid_zip(self):
        import zipfile, io
        from lambdas.audit.handler import _build_zip
        files = {
            "manifest.json": b'{"test": true}',
            "findings.json": b'[]',
        }
        zip_bytes = _build_zip(files)
        buf = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
        self.assertIn("manifest.json", names)
        self.assertIn("findings.json", names)


if __name__ == "__main__":
    unittest.main()
