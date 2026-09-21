# FMVSS 216 — Roof Crush Resistance: Summary for DFMEA Reference

**Standard:** Federal Motor Vehicle Safety Standard No. 216a
**Title:** Roof Crush Resistance
**Authority:** NHTSA
**Scope:** Passenger cars, MPVs, trucks ≤ 6,000 lb GVWR (Phase I); ≤ 10,000 lb (Phase II)
**Relevant to:** Roof Rail, A-Pillar, B-Pillar upper, Roof Panel, Cant Rail

---

## Key Requirements

### Static Crush Test — Phase I (≤ 6,000 lb GVWR)
- Applied force plate: 762 × 1829 mm (30 × 72 in) rigid plate
- Test angle: 5° from horizontal, 25° from vehicle longitudinal axis
- **Load:** 1.5 × unloaded vehicle weight applied to front roof corner
- **Limit:** Plate travel ≤ 127 mm (5 in) before reaching required load
- **Residual occupant space:** Sufficient for 50th percentile occupant

### Static Crush Test — Phase II (≤ 10,000 lb GVWR, added 2009)
- **Load:** 2.5 × unloaded vehicle weight
- Driver side AND passenger side must each meet requirement independently
- Minimum force resistance before 127mm: 2.5 × UVW

---

## Structural Design Requirements (FMEA-relevant)

| Component | Function | Critical Failure Mode | Min load path |
|---|---|---|---|
| Roof Rail | Primary crush ring | Lateral buckling | Must carry 2.5× UVW with < 127mm travel |
| A-Pillar | Front crush path | Column fracture | Yield but don't fracture (ductile failure preferred) |
| B-Pillar upper | Side continuity | Separation from roof rail | Must maintain connection under full load |
| Cant Rail (header/rear) | Complete ring | Bending failure | Must close the crush ring |
| Roof Panel | Contribution to ring | Wrinkle propagation | Acceptable to wrinkle; must not tear |

---

## Common DFMEA Gaps for FMVSS 216

1. **Missing citation:** Roof panel failure modes with S≥7 often lack FMVSS 216 citation
2. **Missing failure mode:** Cant rail separation from pillar not included
3. **Under-scored:** Lateral buckling of roof rail scored M when CAE shows intrusion > 100mm → should be H
4. **Structural gap:** No failure mode for asymmetric loading (passenger side test)

---

## Related Standards
- ECE R21 — European roof crush equivalent
- IIHS Roof Strength Protocol — More stringent: SWR ≥ 4.0 for Good rating
- SAE J1614 — Rollover test procedure for measurement

---

*This summary is for DFMEA agent reference only.*
