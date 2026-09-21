#!/usr/bin/env python3
"""
expand_base.py — Expands each base DFMEA from ~20 rows to ~130 rows
by systematically generating additional failure mode / cause / effect
combinations per component. This brings total across 150 variants to ~18,000+ rows.

Deterministic (seed per assembly). Overwrites <assembly>_base.json in-place.
"""
from __future__ import annotations
import copy, json, os, random

BASE_DIR = os.path.join(os.path.dirname(__file__), "base")
TARGET_ROWS = 130
SEED = 99


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


# Additional failure mode templates by functional_category
_FM_TEMPLATES = {
    "structural": [
        ("Yield under sustained load", "Permanent deformation exceeds tolerance", 7, "Underestimated sustained load case", 4, "Design load review", "Static load test", 5),
        ("Crack propagation under vibration", "Progressive structural failure", 8, "Vibration amplitude exceeds design limit", 3, "Vibration FEA", "Resonance test", 4),
        ("Weld toe fatigue crack", "Joint failure in service", 7, "Insufficient weld toe radius", 4, "Weld WPS per fatigue spec", "Macro coupon", 5),
        ("Insufficient joint overlap", "Separation under load", 7, "Stamping tolerance stack-up", 4, "Overlap per assembly spec", "CMM first article", 5),
        ("Stress corrosion cracking", "Brittle fracture in corrosive environment", 8, "High residual stress + corrosion", 3, "Stress relief + coating spec", "SCC coupon test", 4),
        ("Galvanic corrosion at joint", "Accelerated material loss", 6, "Dissimilar metals in contact", 5, "Isolation coating on contact face", "Galvanic test", 6),
        ("Overload fracture at stress raiser", "Catastrophic failure", 9, "Undetected notch from machining", 2, "Surface quality spec; no sharp notches", "Magnetic particle inspection", 3),
        ("Section loss from thinning", "Below minimum wall thickness", 7, "Over-trimmed during stamping", 4, "Trimming die control", "Wall thickness gauging", 4),
    ],
    "corrosion": [
        ("Pitting corrosion on exposed surface", "Surface degradation; customer complaint", 5, "Road salt exposure without adequate coating", 5, "Chip primer + e-coat spec", "Stone chip test", 6),
        ("Galvanic corrosion in hem flange", "Metal loss at bi-metallic joint", 6, "Aluminium-steel contact", 4, "Sealer in hem gap", "6-week salt spray", 6),
        ("Cosmetic corrosion on paint surface", "Warranty claim", 4, "Micro-holiday in paint", 6, "Paint film thickness spec", "Cross-cut adhesion test", 5),
        ("Underbody perforation from road spray", "Structural corrosion; MoT failure", 7, "Insufficient coating in weld seam area", 5, "Cavity wax injection spec", "6yr vehicle audit", 7),
        ("Fillet corrosion at weld bead toe", "Accelerated fatigue crack initiation", 7, "Weld scale not removed", 4, "Post-weld cleaning spec", "Corrosion then fatigue test", 6),
        ("Intergranular corrosion in heat treat zone", "Material property loss", 7, "Sensitisation during welding", 3, "Controlled heat input + quench", "Metallurgical cross-section", 4),
    ],
    "fatigue": [
        ("High-cycle fatigue crack at drilled hole", "Hole enlargement; fastener loss", 7, "Hole edge stress concentration", 4, "Hole radius and edge quality spec", "Fatigue coupon test", 4),
        ("Thermomechanical fatigue at brazed joint", "Joint separation from thermal cycling", 7, "Excessive thermal delta T at joint", 4, "Braze alloy specification", "Thermal fatigue test", 5),
        ("Resonant fatigue from engine vibration", "NVH failure; crack propagation", 6, "Natural frequency coincides with engine order", 5, "Modal analysis; add damper", "Swept sine test", 5),
        ("Low-cycle fatigue under crash pulse", "Permanent set; seal failure", 7, "Insufficient ductility of base material", 4, "Material ductility spec", "Crash test", 4),
        ("Fretting fatigue at clamped interface", "Surface damage; crack initiation", 6, "Micro-slip at bolted joint", 4, "Friction grip bolt spec", "Fretting test rig", 5),
    ],
    "dimensional": [
        ("Springback exceeding tolerance", "Assembly gap out of spec", 5, "UHSS springback not fully compensated", 5, "Springback compensation in die", "CMM check at SOP", 5),
        ("Thermal distortion from paint oven", "Panel fit and flush deviation", 4, "Insufficient jig support in oven", 6, "Thermal simulation; add oven support", "Fit check post-oven", 4),
        ("Weld distortion causing misalignment", "Downstream assembly error", 5, "Weld sequence not optimised", 6, "Weld sequence per distortion analysis", "Post-weld CMM scan", 4),
        ("Creep under sustained clamping load", "Loss of preload in time", 5, "Material creep in elevated temp zone", 4, "Creep-resistant material spec", "Relaxation test at 100°C", 5),
        ("Tolerance stack-up at assembly", "Gaps/overlaps in sealing surfaces", 5, "Three-way tolerance accumulation", 4, "Statistical tolerance analysis", "Assembly gauge check", 4),
    ],
    "chemical": [
        ("Adhesive degradation in high-temp zone", "Reduced bond strength", 6, "Service temperature exceeds adhesive limit", 4, "Adhesive Tg specification", "High-temp lap shear test", 5),
        ("Sealant embrittlement from UV", "Cracking; water ingress", 5, "UV exposure on exterior seam", 5, "UV-stable sealant spec", "UV aging test 1000h", 5),
        ("Flux corrosion residue from brazing", "Localised corrosion under flux", 6, "Incomplete flux removal", 4, "Post-braze cleaning spec", "Fluorescent inspection", 4),
    ],
    "thermal": [
        ("Thermal fatigue in weld zone", "Micro-cracking from temp cycling", 6, "High delta-T near exhaust/engine", 4, "Thermal shielding spec", "Thermal cycle test 1000h", 5),
        ("Heat distortion reducing seal gap", "Loss of sealing under engine heat", 5, "Insufficient clearance to heat source", 5, "Clearance analysis in hot condition", "Thermal imaging under load", 4),
        ("Oxidation of coating at high temp", "Coating failure; corrosion", 5, "Peak temperature exceeds coating spec", 4, "High-temp coating spec", "Oven aging test", 5),
    ],
}


def expand_rows(rows: list[dict], assembly: str, target: int, rng: random.Random) -> list[dict]:
    """Generate additional rows based on existing component/function pairs and FM templates."""
    if len(rows) >= target:
        return rows

    expanded = list(rows)
    idx = len(rows) + 1

    # Build pool of (component, function, fn_cat, comp_type, cause_type) from existing rows
    component_pool = [
        (r["part_name"], r["function"], r["functional_category"], r["component_type"], r["cause_type"])
        for r in rows
    ]

    while len(expanded) < target:
        # Pick a random existing component
        part_name, function_, fn_cat, comp_type, cause_type = rng.choice(component_pool)

        # Pick a FM template matching fn_cat (fallback to structural)
        templates = _FM_TEMPLATES.get(fn_cat, _FM_TEMPLATES["structural"])
        tmpl = rng.choice(templates)
        fm, effect, s, cause, o, prev, det_ctrl, d = tmpl

        # Jitter S, O, D slightly for variety
        s = max(1, min(10, s + rng.randint(-1, 1)))
        o = max(1, min(10, o + rng.randint(-1, 1)))
        d = max(1, min(10, d + rng.randint(-1, 1)))

        new_row = {
            "item_number": f"X.{idx}",
            "part_name": part_name,
            "function": function_,
            "failure_mode": fm,
            "effect": effect,
            "severity": s,
            "cause": cause,
            "occurrence": o,
            "current_prevention_controls": prev,
            "current_detection_controls": det_ctrl,
            "detection": d,
            "action_priority": _ap(s, o, d),
            "functional_category": fn_cat,
            "component_type": comp_type,
            "cause_type": cause_type,
        }
        expanded.append(new_row)
        idx += 1

    return expanded[:target]


def main() -> None:
    rng = random.Random(SEED)
    for base_file in sorted(os.listdir(BASE_DIR)):
        if not base_file.endswith("_base.json"):
            continue
        path = os.path.join(BASE_DIR, base_file)
        with open(path) as f:
            doc = json.load(f)

        original_count = len(doc["rows"])
        assembly_rng = random.Random(hash(doc["assembly"]) & 0xFFFFFFFF)
        doc["rows"] = expand_rows(doc["rows"], doc["assembly"], TARGET_ROWS, assembly_rng)
        doc["row_count"] = len(doc["rows"])

        with open(path, "w") as f:
            json.dump(doc, f, indent=2)
        print(f"  {base_file}: {original_count} -> {len(doc['rows'])} rows")

    print(f"\nExpansion complete. All bases now at ~{TARGET_ROWS} rows.")


if __name__ == "__main__":
    main()
