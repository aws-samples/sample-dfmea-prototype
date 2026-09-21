# AIAG-VDA 2019 FMEA — Action Priority Rules: Reference Summary

**Standard:** AIAG-VDA Failure Mode and Effects Analysis, 1st Edition 2019
**Section:** 5.5 Action Priority (AP) Determination
**Replaces:** RPN (Risk Priority Number) ranking for prioritisation decisions

---

## Action Priority Table (condensed)

The AP is determined from S (Severity), O (Occurrence), D (Detection) using the following priority rules:

| Severity | Occurrence | Detection | Action Priority |
|---|---|---|---|
| 9–10 (Catastrophic) | Any | Any | **High (H)** |
| 5–8 (High/Moderate) | 4–10 | 7–10 | **High (H)** |
| 5–8 (High/Moderate) | 4–10 | 1–6 | **Medium (M)** |
| 5–8 (High/Moderate) | 1–3 | Any | **Low (L)** |
| 1–4 (Low/Negligible) | 6–10 | Any | **Medium (M)** |
| 1–4 (Low/Negligible) | 1–5 | Any | **Low (L)** |

> **Rule 1 — Severity dominates:** S=9 or S=10 always results in High AP regardless of O and D.
> **Rule 2 — High O overrides low D:** If S≥5 and O≥4, the detection rating decides H vs M.
> **Rule 3 — Low O reduces priority:** If S≥5 but O≤3, result is always Low.

---

## Key Differences from Legacy RPN

| Aspect | RPN (legacy) | AP (AIAG-VDA 2019) |
|---|---|---|
| Formula | S × O × D | Lookup table (no multiplication) |
| Severity role | Equal weight with O and D | Severity 9-10 always triggers High |
| Threshold | Arbitrary cutoffs (e.g. RPN > 100) | Explicit H/M/L categories |
| Regulatory acceptance | Varies by OEM | Harmonized across AIAG-VDA members |

---

## Rating Scale Definitions (S)

| S | Description |
|---|---|
| 10 | No warning, affects safe operation — regulatory non-compliance |
| 9 | Affects safe operation with warning |
| 7–8 | Partial loss of vehicle function; customer very dissatisfied |
| 5–6 | Reduced performance; customer dissatisfied |
| 3–4 | Minor function degradation; customer notices |
| 1–2 | Slight annoyance; barely perceptible |

---

## Rating Scale Definitions (O)

| O | Description | Failure rate |
|---|---|---|
| 10 | Very high — almost inevitable | ≥1 in 2 |
| 8–9 | High | 1 in 8–20 |
| 6–7 | Moderate | 1 in 80–400 |
| 4–5 | Occasional | 1 in 2,000–15,000 |
| 2–3 | Low | 1 in 150,000–1,500,000 |
| 1 | Very low — near impossible | ≤ 1 in 1,500,000 |

---

## Rating Scale Definitions (D)

| D | Description |
|---|---|
| 10 | No detection possible |
| 8–9 | Very remote chance of detection |
| 6–7 | Remote to low detection probability |
| 4–5 | Moderate detection probability |
| 2–3 | High detection probability |
| 1 | Almost certain detection |

---

## Common DFMEA Errors Caught by AIAG-VDA AP Rules

1. **RPN-style over-scoring:** S=2, O=10, D=10 → RPN=200 (looks severe), AP=Medium (O-driven but S low)
2. **Missing H designation:** S=9, O=2, D=2 → RPN=36 (looks low), AP=**High** (severity rule)
3. **Inconsistent AP:** Row states AP=M but S=9 → should be H (clear error to flag)
4. **Under-documented detection:** D=1 does not absolve High severity (S=9, O=5, D=1 → still High)

---

*This summary is for DFMEA agent reference only. Always consult the full AIAG-VDA 2019 document.*
