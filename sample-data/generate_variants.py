#!/usr/bin/env python3
"""
generate_variants.py — Generates 30 variants per base DFMEA = 150 total documents.

Variant types (5 of each per base, with slight row count variation):
  1. missing_failure_mode  — gap injection: removes 15-25% of rows
  2. under_scored          — severity/occurrence under-rated on 20-30% of rows
  3. over_scored           — severity/occurrence over-rated on 20-30% of rows
  4. missing_citation      — removes regulatory citation from prevention controls
  5. structural_gap        — injects phantom component rows not in base
  6. anomalous_sod         — S/O/D combinations that violate expected patterns

Output:
  sample-data/variants/<assembly>_v<N>.json  (N = 01..30)
  sample-data/metadata.json                  (ground-truth labels for all rows)
"""
from __future__ import annotations
import copy, json, os, random
from typing import Any

BASE_DIR     = os.path.join(os.path.dirname(__file__), "base")
VARIANTS_DIR = os.path.join(os.path.dirname(__file__), "variants")
META_PATH    = os.path.join(os.path.dirname(__file__), "metadata.json")

VARIANTS_PER_BASE = 30          # 5 types × 6 each
VARIANTS_PER_TYPE = 5           # updated: 5 types × 6 = 30


def _ap(s: int, o: int, d: int) -> str:
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


def _clamp(v: int, lo: int = 1, hi: int = 10) -> int:
    return max(lo, min(hi, v))


def _make_missing_failure_mode(rows: list[dict], rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Remove 15-30% of rows (gap injection). Labels: gap=True for removed rows (not present)."""
    n_remove = max(1, int(len(rows) * rng.uniform(0.15, 0.30)))
    to_remove = set(rng.sample(range(len(rows)), n_remove))
    kept, labels = [], []
    for i, row in enumerate(rows):
        if i not in to_remove:
            kept.append(copy.deepcopy(row))
            labels.append({**_row_label(row, i), "variant_type": "missing_failure_mode", "label": "valid", "injected_gap": False})
        else:
            labels.append({**_row_label(row, i), "variant_type": "missing_failure_mode", "label": "gap", "injected_gap": True})
    return kept, labels


def _make_under_scored(rows: list[dict], rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Reduce severity and/or occurrence on 20-30% of rows. Label: under_scored."""
    result, labels = [], []
    n_affect = max(1, int(len(rows) * rng.uniform(0.20, 0.30)))
    affected = set(rng.sample(range(len(rows)), n_affect))
    for i, row in enumerate(rows):
        r = copy.deepcopy(row)
        if i in affected:
            r["severity"]   = _clamp(r["severity"] - rng.randint(2, 3))
            r["occurrence"] = _clamp(r["occurrence"] - rng.randint(1, 2))
            r["action_priority"] = _ap(r["severity"], r["occurrence"], r["detection"])
            labels.append({**_row_label(row, i), "variant_type": "under_scored", "label": "under_scored",
                           "true_severity": row["severity"], "true_occurrence": row["occurrence"]})
        else:
            labels.append({**_row_label(row, i), "variant_type": "under_scored", "label": "valid",
                           "true_severity": row["severity"], "true_occurrence": row["occurrence"]})
        result.append(r)
    return result, labels


def _make_over_scored(rows: list[dict], rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Inflate severity and/or occurrence on 20-30% of rows. Label: over_scored."""
    result, labels = [], []
    n_affect = max(1, int(len(rows) * rng.uniform(0.20, 0.30)))
    affected = set(rng.sample(range(len(rows)), n_affect))
    for i, row in enumerate(rows):
        r = copy.deepcopy(row)
        if i in affected:
            r["severity"]   = _clamp(r["severity"] + rng.randint(2, 3))
            r["occurrence"] = _clamp(r["occurrence"] + rng.randint(1, 2))
            r["action_priority"] = _ap(r["severity"], r["occurrence"], r["detection"])
            labels.append({**_row_label(row, i), "variant_type": "over_scored", "label": "over_scored",
                           "true_severity": row["severity"], "true_occurrence": row["occurrence"]})
        else:
            labels.append({**_row_label(row, i), "variant_type": "over_scored", "label": "valid",
                           "true_severity": row["severity"], "true_occurrence": row["occurrence"]})
        result.append(r)
    return result, labels


def _make_missing_citation(rows: list[dict], rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Blank out regulatory references from prevention controls on 20-40% of rows."""
    result, labels = [], []
    keywords = ["FMVSS", "ISO", "AIAG", "IEC", "SAE", "IATF", "VDA", "ECE"]
    n_affect = max(1, int(len(rows) * rng.uniform(0.20, 0.40)))
    affected = set(rng.sample(range(len(rows)), n_affect))
    for i, row in enumerate(rows):
        r = copy.deepcopy(row)
        if i in affected and any(kw in r.get("current_prevention_controls", "") for kw in keywords):
            r["current_prevention_controls"] = "Standard inspection per internal procedure"
            labels.append({**_row_label(row, i), "variant_type": "missing_citation",
                           "label": "missing_citation", "citation_removed": True})
        else:
            labels.append({**_row_label(row, i), "variant_type": "missing_citation",
                           "label": "valid", "citation_removed": False})
        result.append(r)
    return result, labels


_PHANTOM_COMPONENTS = {
    "b_pillar":      [("P.1","B-Pillar Load Distribution Plate","Transfer lateral load","Deformation under crash","Reduced crash performance",8,"Insufficient plate gauge",4,"Gauge per load spec","CMM check",5,"structural","reinforcement","design")],
    "door_ring":     [("P.1","Door Ring Diagonal Brace","Maintain ring stiffness","Buckling under torsion","NVH increase",6,"Brace gauge insufficient",4,"Brace analysis","Modal test",5,"structural","bracket","design")],
    "roof_rail":     [("P.1","Roof Rail End Cap","Seal rail cavity","Corrosion from water ingress","Internal rust",6,"End cap not fully sealed",5,"Sealer spec","Leak test",5,"corrosion","panel","manufacturing")],
    "sill_assembly": [("P.1","Sill Drainage Baffle","Prevent water accumulation","Blocked by assembly sealant","Water trap; corrosion",6,"Sealant encroaches baffle",4,"Mask spec for baffle","Flow check",5,"corrosion","panel","manufacturing")],
    "strut_tower":   [("P.1","Strut Tower Acoustic Barrier","Reduce airborne noise","Barrier detachment","Noise increase",4,"Adhesive failure",4,"Adhesive spec","Peel test",5,"structural","bracket","manufacturing")],
}

def _make_structural_gap(rows: list[dict], assembly: str, rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Inject phantom component rows and remove 10-15% of existing to create BOM gaps."""
    n_remove = max(1, int(len(rows) * rng.uniform(0.10, 0.15)))
    to_remove = set(rng.sample(range(len(rows)), n_remove))
    result, labels = [], []
    for i, row in enumerate(rows):
        if i not in to_remove:
            result.append(copy.deepcopy(row))
            labels.append({**_row_label(row, i), "variant_type": "structural_gap", "label": "valid", "is_phantom": False})
        else:
            labels.append({**_row_label(row, i), "variant_type": "structural_gap", "label": "bom_gap", "is_phantom": False})

    # Inject phantom rows
    for t in _PHANTOM_COMPONENTS.get(assembly, []):
        phantom = {
            "item_number": t[0], "part_name": t[1], "function": t[2],
            "failure_mode": t[3], "effect": t[4], "severity": t[5],
            "cause": t[6], "occurrence": t[7],
            "current_prevention_controls": t[8],
            "current_detection_controls": t[9],
            "detection": t[10], "action_priority": _ap(t[5], t[7], t[10]),
            "functional_category": t[11], "component_type": t[12], "cause_type": t[13],
        }
        result.append(phantom)
        labels.append({**_row_label(phantom, len(result)-1), "variant_type": "structural_gap",
                       "label": "structural_gap", "is_phantom": True})
    return result, labels


def _make_anomalous_sod(rows: list[dict], rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Inject statistically improbable S/O/D combos on 15-25% of rows (Isolation Forest training signal)."""
    result, labels = [], []
    n_affect = max(1, int(len(rows) * rng.uniform(0.15, 0.25)))
    affected = set(rng.sample(range(len(rows)), n_affect))
    # Anomalous patterns: e.g., S=9 with O=1,D=1 but AP=Low (deliberate mismatch)
    anomaly_patterns = [(9, 1, 1), (8, 1, 1), (10, 1, 2), (7, 1, 1), (9, 2, 1)]
    for i, row in enumerate(rows):
        r = copy.deepcopy(row)
        if i in affected:
            s, o, d = rng.choice(anomaly_patterns)
            r["severity"], r["occurrence"], r["detection"] = s, o, d
            r["action_priority"] = _ap(s, o, d)
            labels.append({**_row_label(row, i), "variant_type": "anomalous_sod", "label": "anomalous",
                           "anomaly_sod": [s, o, d], "original_sod": [row["severity"], row["occurrence"], row["detection"]]})
        else:
            labels.append({**_row_label(row, i), "variant_type": "anomalous_sod", "label": "valid"})
        result.append(r)
    return result, labels


def _row_label(row: dict, idx: int) -> dict:
    return {
        "item_number": row.get("item_number", str(idx)),
        "part_name": row.get("part_name", ""),
        "failure_mode": row.get("failure_mode", ""),
        "severity": row.get("severity"), "occurrence": row.get("occurrence"),
        "detection": row.get("detection"),
        "true_ap": _ap(row.get("severity", 5), row.get("occurrence", 5), row.get("detection", 5)),
    }


VARIANT_FUNCS = [
    ("missing_failure_mode", _make_missing_failure_mode),
    ("under_scored",         _make_under_scored),
    ("over_scored",          _make_over_scored),
    ("missing_citation",     _make_missing_citation),
    ("structural_gap",       _make_structural_gap),
    ("anomalous_sod",        _make_anomalous_sod),
]


def main() -> None:
    os.makedirs(VARIANTS_DIR, exist_ok=True)

    all_metadata: list[dict] = []
    total_rows = 0
    total_docs = 0

    for base_file in sorted(os.listdir(BASE_DIR)):
        if not base_file.endswith("_base.json"):
            continue
        assembly = base_file.replace("_base.json", "")
        with open(os.path.join(BASE_DIR, base_file)) as f:
            base_doc = json.load(f)
        base_rows: list[dict] = base_doc["rows"]

        doc_idx = 1
        for type_name, func in VARIANT_FUNCS:
            for repeat in range(5):   # 5 repeats per type = 30 total
                seed = hash(f"{assembly}_{type_name}_{repeat}") & 0xFFFFFFFF
                rng = random.Random(seed)

                if type_name == "structural_gap":
                    variant_rows, labels = func(base_rows, assembly, rng)
                else:
                    variant_rows, labels = func(base_rows, rng)

                variant_id = f"{assembly}_v{doc_idx:02d}"
                out = {
                    "assembly": assembly,
                    "variant_id": variant_id,
                    "variant_type": type_name,
                    "repeat": repeat,
                    "seed": seed,
                    "row_count": len(variant_rows),
                    "rows": variant_rows,
                }
                out_path = os.path.join(VARIANTS_DIR, f"{variant_id}.json")
                with open(out_path, "w") as f:
                    json.dump(out, f, indent=2)

                for lbl in labels:
                    lbl["document_id"] = variant_id
                    lbl["assembly"] = assembly
                all_metadata.extend(labels)
                total_rows += len(variant_rows)
                total_docs += 1
                doc_idx += 1

        print(f"  {assembly}: 30 variants generated")

    # Save metadata
    with open(META_PATH, "w") as f:
        json.dump(all_metadata, f, indent=2)

    print(f"\nTotal: {total_docs} documents, {total_rows} rows")
    print(f"Metadata: {len(all_metadata)} label entries -> {META_PATH}")


if __name__ == "__main__":
    main()
