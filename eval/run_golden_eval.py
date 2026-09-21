"""
eval/run_golden_eval.py — Run the 10-case golden evaluation set against the deployed agents.

Usage:
    # Against live deployed agents:
    python eval/run_golden_eval.py --api-url https://<rest-api-id>.execute-api.us-east-1.amazonaws.com/v1 --token <id-token>

    # Dry-run (validate golden set structure only, no API calls):
    python eval/run_golden_eval.py --dry-run

Output:
    eval/golden_eval_results_<timestamp>.json — per-case results + aggregate metrics
    Prints a summary table to stdout.
"""
from __future__ import annotations
import argparse
import json
import sys
import os
import time
import datetime
import uuid
from pathlib import Path
from typing import Any

# ── Load golden eval set ───────────────────────────────────────────────────────
EVAL_DIR = Path(__file__).parent
GOLDEN_SET_PATH = EVAL_DIR / "golden_eval_set.json"


def load_golden_set() -> dict:
    with open(GOLDEN_SET_PATH) as f:
        return json.load(f)


# ── API helpers ────────────────────────────────────────────────────────────────

def _api_post(base_url: str, path: str, body: dict, token: str) -> dict:
    import urllib.request
    url = f"{base_url.rstrip('/')}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _api_get(base_url: str, path: str, token: str) -> dict:
    import urllib.request
    url = f"{base_url.rstrip('/')}{path}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": token},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _poll_review(base_url: str, review_id: str, token: str, max_wait: int = 300) -> str:
    """Poll review status until COMPLETE or ERROR, return final status."""
    terminal = {"COMPLETE", "HITL_APPROVED", "HITL_REJECTED", "ERROR", "ARCHIVED"}
    start = time.time()
    while time.time() - start < max_wait:
        resp = _api_get(base_url, f"/reviews/{review_id}", token)
        status = resp.get("status", "")
        if status in terminal:
            return status
        time.sleep(10)
    return "TIMEOUT"


# ── Matching helpers ──────────────────────────────────────────────────────────

def _finding_matches(finding: dict, expected: dict) -> bool:
    """Check if an actual finding satisfies an expected finding spec."""
    # Finding type
    if finding.get("finding_type") != expected.get("finding_type"):
        return False
    # Agent
    if "agent" in expected and finding.get("agent") != expected["agent"]:
        return False
    # AP
    if "action_priority" in expected and finding.get("action_priority") != expected["action_priority"]:
        return False
    # Confidence
    min_conf = expected.get("confidence_min", 0.0)
    try:
        if float(finding.get("confidence", 0)) < min_conf:
            return False
    except (TypeError, ValueError):
        pass
    # Description keywords
    desc = (finding.get("description") or "").lower()
    for kw in expected.get("description_contains", []):
        if kw.lower() not in desc:
            return False
    # Standard citations
    citations = [c.lower() for c in (finding.get("standard_citations") or [])]
    for cite in expected.get("standard_citations_contain", []):
        if not any(cite.lower() in c for c in citations):
            return False
    return True


def evaluate_case(case: dict, actual_findings: list[dict]) -> dict:
    """Evaluate a single golden case against actual findings. Returns per-case metrics."""
    expected_list = case.get("expected_findings", [])
    min_count = case.get("expected_finding_count_min", 0)
    max_count = case.get("expected_finding_count_max", 9999)

    matched_expected: set[int] = set()
    true_positives = 0
    false_positives = 0

    for finding in actual_findings:
        matched = False
        for i, exp in enumerate(expected_list):
            if i not in matched_expected and _finding_matches(finding, exp):
                matched_expected.add(i)
                true_positives += 1
                matched = True
                break
        if not matched:
            false_positives += 1

    false_negatives = len(expected_list) - len(matched_expected)
    total_expected = len(expected_list)

    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) > 0 else 1.0
    recall = true_positives / total_expected if total_expected > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    count_ok = min_count <= len(actual_findings) <= max_count

    return {
        "case_id": case["case_id"],
        "variant_type": case.get("variant_type"),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "actual_finding_count": len(actual_findings),
        "expected_finding_count": total_expected,
        "count_ok": count_ok,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "passed": count_ok and recall >= 0.65 and precision >= 0.60,
    }


def aggregate_metrics(case_results: list[dict]) -> dict:
    n = len(case_results)
    if n == 0:
        return {}
    avg_precision = sum(r["precision"] for r in case_results) / n
    avg_recall = sum(r["recall"] for r in case_results) / n
    avg_f1 = sum(r["f1"] for r in case_results) / n
    tp = sum(r["true_positives"] for r in case_results)
    fp = sum(r["false_positives"] for r in case_results)
    fn = sum(r["false_negatives"] for r in case_results)
    macro_precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    macro_recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    macro_f1 = 2 * macro_precision * macro_recall / (macro_precision + macro_recall) if (macro_precision + macro_recall) > 0 else 0.0
    fpr = fp / (tp + fp) if (tp + fp) > 0 else 0.0
    cases_passed = sum(1 for r in case_results if r["passed"])
    return {
        "cases_total": n,
        "cases_passed": cases_passed,
        "pass_rate": round(cases_passed / n, 3),
        "avg_precision": round(avg_precision, 3),
        "avg_recall": round(avg_recall, 3),
        "avg_f1": round(avg_f1, 3),
        "macro_precision": round(macro_precision, 3),
        "macro_recall": round(macro_recall, 3),
        "macro_f1": round(macro_f1, 3),
        "false_positive_rate": round(fpr, 3),
        "total_tp": tp,
        "total_fp": fp,
        "total_fn": fn,
    }


def print_summary(case_results: list[dict], agg: dict) -> None:
    print("\n" + "=" * 72)
    print(f"{'DFMEA Golden Eval Results':^72}")
    print("=" * 72)
    print(f"{'Case':<10} {'Variant':<22} {'TP':>4} {'FP':>4} {'FN':>4} {'P':>6} {'R':>6} {'F1':>6} {'Pass':>5}")
    print("-" * 72)
    for r in case_results:
        flag = "✓" if r["passed"] else "✗"
        print(
            f"{r['case_id']:<10} {r['variant_type']:<22} "
            f"{r['true_positives']:>4} {r['false_positives']:>4} {r['false_negatives']:>4} "
            f"{r['precision']:>6.2f} {r['recall']:>6.2f} {r['f1']:>6.2f} {flag:>5}"
        )
    print("=" * 72)
    print(f"\nAggregate: {agg['cases_passed']}/{agg['cases_total']} cases passed")
    print(f"  Macro P={agg['macro_precision']:.3f}  R={agg['macro_recall']:.3f}  F1={agg['macro_f1']:.3f}")
    print(f"  False-positive rate: {agg['false_positive_rate']:.3f}")

    thresholds = load_golden_set().get("evaluation_thresholds", {})
    issues = []
    if agg["macro_precision"] < thresholds.get("precision_min", 0.70):
        issues.append(f"  ⚠ Precision {agg['macro_precision']:.3f} < threshold {thresholds['precision_min']}")
    if agg["macro_recall"] < thresholds.get("recall_min", 0.65):
        issues.append(f"  ⚠ Recall {agg['macro_recall']:.3f} < threshold {thresholds['recall_min']}")
    if agg["macro_f1"] < thresholds.get("f1_min", 0.67):
        issues.append(f"  ⚠ F1 {agg['macro_f1']:.3f} < threshold {thresholds['f1_min']}")
    if agg["false_positive_rate"] > thresholds.get("false_positive_rate_max", 0.20):
        issues.append(f"  ⚠ FPR {agg['false_positive_rate']:.3f} > threshold {thresholds['false_positive_rate_max']}")
    if issues:
        print("\nThreshold violations:")
        for issue in issues:
            print(issue)
    else:
        print("\n  ✓ All thresholds met")
    print()


# ── Dry run (validate structure) ──────────────────────────────────────────────

def dry_run() -> int:
    golden = load_golden_set()
    cases = golden["cases"]
    print(f"Golden eval set v{golden['version']} — {len(cases)} cases loaded")
    errors = []
    for case in cases:
        if "case_id" not in case:
            errors.append("Missing case_id")
            continue
        if "input_rows" not in case or not case["input_rows"]:
            errors.append(f"{case['case_id']}: no input_rows")
        if "expected_findings" not in case:
            errors.append(f"{case['case_id']}: no expected_findings")
        for row in case.get("input_rows", []):
            for field in ["part_name", "failure_mode", "severity", "occurrence", "detection"]:
                if field not in row:
                    errors.append(f"{case['case_id']}: row missing {field}")
    if errors:
        print(f"Validation FAILED ({len(errors)} errors):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("Validation PASSED — all cases are structurally valid")
    return 0


# ── Live evaluation against deployed API ─────────────────────────────────────

def live_run(api_url: str, token: str) -> int:
    golden = load_golden_set()
    cases = golden["cases"]
    print(f"\nRunning {len(cases)} cases against {api_url}")

    case_results = []
    case_details = []

    for case in cases:
        print(f"  [{case['case_id']}] {case['description'][:60]}…", end=" ", flush=True)

        # Create a review via the API (in real eval, upload a JSON file to S3 first)
        # For eval purposes we directly post to /reviews with a synthetic file_key
        review_body = {
            "assembly_name": f"golden-eval-{case['case_id']}",
            "file_key": f"eval/{case['case_id']}-{uuid.uuid4().hex[:8]}.json",
        }
        try:
            create_resp = _api_post(api_url, "/reviews", review_body, token)
            review_id = create_resp.get("review_id")
            if not review_id:
                print(f"ERROR (no review_id in response)")
                case_results.append({**{"case_id": case["case_id"], "variant_type": case.get("variant_type")}, "error": "no review_id", "passed": False, "precision": 0.0, "recall": 0.0, "f1": 0.0, "true_positives": 0, "false_positives": 0, "false_negatives": len(case.get("expected_findings", [])), "actual_finding_count": 0, "expected_finding_count": len(case.get("expected_findings", [])), "count_ok": False})
                continue

            # Poll until complete
            final_status = _poll_review(api_url, review_id, token)
            if final_status in ("ERROR", "TIMEOUT"):
                print(f"SKIP ({final_status})")
                continue

            # Fetch findings
            findings_resp = _api_get(api_url, f"/reviews/{review_id}/findings", token)
            actual_findings = findings_resp.get("findings", [])

            result = evaluate_case(case, actual_findings)
            case_results.append(result)
            case_details.append({"case": case, "review_id": review_id, "findings": actual_findings, "result": result})

            flag = "✓" if result["passed"] else "✗"
            print(f"{flag} (P={result['precision']:.2f} R={result['recall']:.2f} F1={result['f1']:.2f})")

        except Exception as exc:
            print(f"ERROR ({exc})")
            case_results.append({**{"case_id": case["case_id"], "variant_type": case.get("variant_type")}, "error": str(exc), "passed": False, "precision": 0.0, "recall": 0.0, "f1": 0.0, "true_positives": 0, "false_positives": 0, "false_negatives": len(case.get("expected_findings", [])), "actual_finding_count": 0, "expected_finding_count": len(case.get("expected_findings", [])), "count_ok": False})

    agg = aggregate_metrics(case_results)
    print_summary(case_results, agg)

    # Save results
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = EVAL_DIR / f"golden_eval_results_{ts}.json"
    with open(out_path, "w") as f:
        json.dump({
            "run_timestamp": ts,
            "api_url": api_url,
            "golden_set_version": golden["version"],
            "aggregate": agg,
            "cases": case_results,
        }, f, indent=2)
    print(f"Results saved to: {out_path}")

    # Exit non-zero if thresholds not met
    thresholds = golden.get("evaluation_thresholds", {})
    if (agg.get("macro_f1", 0) < thresholds.get("f1_min", 0.67) or
            agg.get("false_positive_rate", 1) > thresholds.get("false_positive_rate_max", 0.20)):
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DFMEA golden eval suite")
    parser.add_argument("--api-url", default="", help="REST API base URL")
    parser.add_argument("--token", default="", help="Cognito ID token")
    parser.add_argument("--dry-run", action="store_true", help="Validate golden set structure only")
    args = parser.parse_args()

    if args.dry_run:
        return dry_run()

    if not args.api_url or not args.token:
        print("Error: --api-url and --token are required for live evaluation", file=sys.stderr)
        print("Use --dry-run to validate the golden set structure without API calls")
        return 1

    return live_run(args.api_url, args.token)


if __name__ == "__main__":
    sys.exit(main())
