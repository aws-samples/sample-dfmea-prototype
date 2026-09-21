"""
eval/precision_recall.py — Agent finding evaluation utilities.

Used to evaluate how well the specialist agents detect injected gaps in
variant documents against the ground-truth metadata labels.

Usage:
    from eval.precision_recall import evaluate_findings
    metrics = evaluate_findings(predicted_finding_ids, ground_truth_metadata, variant_doc)
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field


@dataclass
class EvalMetrics:
    true_positives: int  = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float     = 0.0
    recall: float        = 0.0
    f1: float            = 0.0
    details: list[dict]  = field(default_factory=list)


def evaluate_findings(
    predicted_item_numbers: list[str],
    metadata_labels: list[dict],
    gap_label_values: tuple[str, ...] = ("gap", "missing_citation", "structural_gap", "bom_gap"),
) -> EvalMetrics:
    """
    Compare predicted gap item numbers against ground-truth metadata.

    Args:
        predicted_item_numbers: item_number values of rows the agent flagged as gaps.
        metadata_labels: list of label dicts from metadata.json for a single variant.
        gap_label_values: label values that count as a true gap.

    Returns:
        EvalMetrics with precision, recall, F1 and per-item detail.
    """
    true_gap_items = {m["item_number"] for m in metadata_labels if m.get("label") in gap_label_values}
    predicted_set  = set(predicted_item_numbers)

    tp = true_gap_items & predicted_set
    fp = predicted_set - true_gap_items
    fn = true_gap_items - predicted_set

    prec   = len(tp) / max(1, len(predicted_set))
    recall = len(tp) / max(1, len(true_gap_items))
    f1     = 2 * prec * recall / max(1e-9, prec + recall)

    details = (
        [{"item": i, "outcome": "TP"} for i in sorted(tp)] +
        [{"item": i, "outcome": "FP"} for i in sorted(fp)] +
        [{"item": i, "outcome": "FN"} for i in sorted(fn)]
    )

    return EvalMetrics(
        true_positives=len(tp),
        false_positives=len(fp),
        false_negatives=len(fn),
        precision=round(prec, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        details=details,
    )


def aggregate_metrics(metrics_list: list[EvalMetrics]) -> dict:
    """Average precision, recall, F1 across multiple eval runs."""
    if not metrics_list:
        return {}
    n = len(metrics_list)
    return {
        "n_docs":         n,
        "avg_precision":  round(sum(m.precision for m in metrics_list) / n, 4),
        "avg_recall":     round(sum(m.recall    for m in metrics_list) / n, 4),
        "avg_f1":         round(sum(m.f1        for m in metrics_list) / n, 4),
        "total_tp":       sum(m.true_positives  for m in metrics_list),
        "total_fp":       sum(m.false_positives for m in metrics_list),
        "total_fn":       sum(m.false_negatives for m in metrics_list),
    }


if __name__ == "__main__":
    # Quick self-test
    meta = [
        {"item_number": "1.1", "label": "gap"},
        {"item_number": "1.2", "label": "valid"},
        {"item_number": "1.3", "label": "gap"},
        {"item_number": "1.4", "label": "valid"},
    ]
    predicted = ["1.1", "1.4"]   # TP=1.1, FP=1.4, FN=1.3
    m = evaluate_findings(predicted, meta)
    print(f"Precision={m.precision} Recall={m.recall} F1={m.f1}")
    assert m.true_positives == 1
    assert m.false_positives == 1
    assert m.false_negatives == 1
    print("Self-test passed.")
