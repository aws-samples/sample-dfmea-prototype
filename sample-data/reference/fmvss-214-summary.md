# FMVSS 214 — Side Impact Protection: Summary for DFMEA Reference

**Standard:** Federal Motor Vehicle Safety Standard No. 214
**Title:** Side Impact Protection
**Authority:** NHTSA (National Highway Traffic Safety Administration)
**Scope:** Passenger cars, MPVs, trucks, and buses with GVWR ≤ 10,000 lb
**Relevant to:** B-Pillar, Door Ring, Sill Assembly, Door Panel, Side Airbag systems

---

## Key Requirements

### 1. Movable Deformable Barrier (MDB) Test
- **Test speed:** 54 km/h (33.5 mph)
- **Target area:** Side of vehicle at 50th percentile male occupant position
- **Metric:** Thoracic Trauma Index (TTI) ≤ 85 g (standard cab) / ≤ 90 g (extended/crew)
- **B-pillar relevance:** Inner must maintain residual space; reinforcement must limit intrusion < 150mm at hip point

### 2. Pole Impact Test (FMVSS 214 Upgraded 2011)
- **Test speed:** 32 km/h (20 mph) at 75° oblique angle
- **Pole diameter:** 254 mm (10 inch)
- **HIC limit:** Head Injury Criterion ≤ 1000 over any 15 ms interval
- **B-pillar relevance:** Door inner panel and B-pillar must channel pole force into sill without skull contact

### 3. Structural Integrity After Impact
- Door latches must remain closed during and after test
- Doors must be openable after impact without tools

---

## Failure Modes with FMVSS 214 Relevance

| Failure Mode | Component | S (typical) | Regulatory trigger |
|---|---|---|---|
| B-pillar column buckling | B-Pillar Reinforcement | 9–10 | Intrusion exceeds 150mm → TTI violation |
| Weld failure at sill junction | B-Pillar lower flange | 9 | Load path broken → energy absorbed by occupant |
| Door latch failure | Door striker | 9 | Door opens in crash → ejection |
| Side airbag non-deployment | Air bag module | 8 | HIC > 1000 in pole test |
| Sill collapse | Sill Inner | 8 | Sill intrusion > 50mm → leg injury |

---

## DFMEA Checklist for FMVSS 214 Compliance

- [ ] B-pillar inner panel intrusion < 150mm at hip validated by CAE
- [ ] Weld patterns at all load-path joints specified with minimum weld count
- [ ] Door latch pull strength > 4 kN static
- [ ] Side airbag activation thresholds verified against pole scenario
- [ ] TTI calculation documented per SAE J1727

---

## Key Test Documents and Standards
- SAE J1727 — Calculation of Thoracic Trauma Index
- SAE J2522 — Accelerometer calibration
- 49 CFR Part 572 — Anthropomorphic test devices (dummies)
- ECE R95 — European equivalent (similar but not identical requirements)

---

*This summary is for DFMEA agent reference only. Always consult the current FMVSS 214 text for compliance decisions.*
