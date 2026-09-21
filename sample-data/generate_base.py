#!/usr/bin/env python3
"""
generate_base.py — Generates 5 base DFMEA JSON documents (deterministic, seed=42).

Each document covers a structural automotive assembly with realistic AIAG-VDA 2019 rows.
Output: sample-data/base/<component>_base.json
"""
from __future__ import annotations
import json, os, random
from dataclasses import dataclass, asdict
from typing import Optional

BASE_DIR = os.path.join(os.path.dirname(__file__), "base")
RANDOM_SEED = 42


def _ap(s: int, o: int, d: int) -> str:
    """AIAG-VDA 2019 deterministic AP rules."""
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


@dataclass
class DfmeaRow:
    item_number: str
    part_name: str
    function: str
    failure_mode: str
    effect: str
    severity: int
    cause: str
    occurrence: int
    current_prevention_controls: str
    current_detection_controls: str
    detection: int
    action_priority: str
    functional_category: str   # structural | thermal | corrosion | fatigue | dimensional | chemical
    component_type: str        # panel | bracket | joint | reinforcement | hinge | mount
    cause_type: str            # material | design | manufacturing | environmental | operational


def _row(
    item: str, part: str, fn: str, fm: str, effect: str,
    s: int, cause: str, o: int, prev: str, det_ctrl: str, d: int,
    fn_cat: str, comp_type: str, cause_type: str,
) -> DfmeaRow:
    return DfmeaRow(
        item_number=item, part_name=part, function=fn,
        failure_mode=fm, effect=effect,
        severity=s, cause=cause, occurrence=o,
        current_prevention_controls=prev,
        current_detection_controls=det_ctrl,
        detection=d, action_priority=_ap(s, o, d),
        functional_category=fn_cat,
        component_type=comp_type, cause_type=cause_type,
    )


# ── B-Pillar ──────────────────────────────────────────────────────────────────
B_PILLAR_ROWS: list[DfmeaRow] = [
    _row("1.1","B-Pillar Inner Panel","Maintain side-impact load path","Buckling under lateral load","Reduced occupant protection in side crash",9,"Insufficient material thickness",4,"Material spec per UHSS 1500 MPa","Dimensional inspection CMM",6,"structural","panel","material"),
    _row("1.2","B-Pillar Inner Panel","Maintain side-impact load path","Fracture at base radius","Door intrusion exceeds FMVSS 214 limit",9,"Stress concentration at press-formed radius",3,"Radius per draw die specification","Tensile coupon test lot",5,"structural","panel","design"),
    _row("1.3","B-Pillar Inner Panel","Maintain side-impact load path","Delamination of adhesive bond","Loss of stiffness at inner-outer interface",7,"Incorrect adhesive bead width",4,"Adhesive dispensing robot with vision check","Peel test every shift",5,"structural","joint","manufacturing"),
    _row("1.4","B-Pillar Inner Panel","Resist corrosion","Crevice corrosion at spot weld","Long-term rust perforation reducing structural integrity",7,"Galvanic couple at weld nugget",5,"E-coat coverage spec; weld sequence control","Salt spray 1000h sample",6,"corrosion","joint","environmental"),
    _row("1.5","B-Pillar Inner Panel","Resist corrosion","Coating disbondment at hemmed flange","Premature corrosion visible to customer",5,"Inadequate pre-treatment phosphate weight",4,"Phosphate bath SPC","Cross-cut adhesion test weekly",4,"corrosion","panel","manufacturing"),
    _row("2.1","B-Pillar Outer Panel","Provide Class-A surface continuity","Dent/oil-can under door slam load","Customer quality concern; perceived poor quality",6,"Insufficient panel stiffness (thin gauge)",5,"Anti-flutter adhesive bead; panel stiffness FEA","Dent resistance testing",7,"structural","panel","design"),
    _row("2.2","B-Pillar Outer Panel","Provide Class-A surface continuity","Surface distortion from weld heat","Visible read-through reducing paint quality",5,"Excessive weld heat input",6,"Weld parameter SPC; minimum pitch control","Wavescan post-paint audit",6,"dimensional","panel","manufacturing"),
    _row("2.3","B-Pillar Outer Panel","Resist side intrusion (FMVSS 214)","Progressive collapse instead of load transfer","Inadequate energy absorption in MDB test",9,"Insufficient cross-section in middle zone",3,"Section property review vs simulation","Sled test with MDB",5,"structural","panel","design"),
    _row("3.1","B-Pillar Reinforcement","Distribute hinge loads","Fatigue crack at lower hinge weld","Door drop / hinge separation after duty cycle",7,"Weld toe stress concentration",4,"Weld geometry per WPS; fatigue FEA approval","Durability hinge cycle test",5,"fatigue","reinforcement","design"),
    _row("3.2","B-Pillar Reinforcement","Distribute hinge loads","Weld fracture at upper hinge","Loss of door retention",8,"Under-sized fillet weld leg",3,"Weld inspection AQL","Destructive weld macro quarterly",4,"structural","joint","manufacturing"),
    _row("3.3","B-Pillar Reinforcement","Distribute latch loads","Pull-out of latch reinforcement","Door cannot latch; entry/exit impaired",7,"Insufficient clinch nut engagement",4,"Clinch nut installation torque SPC","Pull test 1 per 100",4,"structural","reinforcement","manufacturing"),
    _row("4.1","Spot Weld Array (Upper)","Transfer load between inner and outer panels","Weld button pull-out","Separation of inner/outer pillar under crash",9,"Weld current below spec",4,"Weld SPC; scheduled electrode dress","Destructive weld test per shift",4,"structural","joint","manufacturing"),
    _row("4.2","Spot Weld Array (Upper)","Transfer load between inner and outer panels","Weld splash / expulsion","Weld strength reduction; contamination risk",6,"Excessive weld current or worn electrode",5,"Electrode dress interval control","Visual and chisel test",5,"structural","joint","manufacturing"),
    _row("4.3","Spot Weld Array (Lower)","Transfer load between inner and outer panels","Edge weld — insufficient edge distance","Weld splits under crash load",8,"Blank part positioned incorrectly in fixture",3,"Part present sensors in fixture","First article inspection",5,"structural","joint","manufacturing"),
    _row("5.1","Adhesive Bond (Inner-Outer)","Supplement weld stiffness","Voids in adhesive bead","Reduced NVH stiffness; potential corrosion ingress",5,"Adhesive dispensing failure",4,"Flow meter on dispenser; vision check","Ultrasonic void inspection",6,"structural","joint","manufacturing"),
    _row("5.2","Adhesive Bond (Inner-Outer)","Supplement weld stiffness","Adhesive not cured (missed oven)","Zero bond strength on affected assembly",9,"Part bypassed oven conveyor",2,"Oven temperature monitoring with alarm","Destructive bond pull test",3,"structural","joint","operational"),
    _row("6.1","B-Pillar to Sill Interface","Transfer crash load to floor structure","Weld fracture at pillar foot","Loss of load path; excessive floor intrusion",9,"Stress concentration at joint geometry",4,"Joint geometry per CAE sign-off","Crash sled test",6,"structural","joint","design"),
    _row("6.2","B-Pillar to Sill Interface","Transfer crash load to floor structure","Corrosion undercutting interface welds","Structural weakening only detected post-corrosion",7,"Water trap at joint; insufficient drain hole",5,"Drain hole size per design spec","Corrosion audit 4-year vehicles",8,"corrosion","joint","design"),
    _row("7.1","B-Pillar Upper Extension","Connect pillar to roof rail","Buckling of upper section under roof crush","Exceeds FMVSS 216 roof crush force",8,"Insufficient section depth",3,"Section geometry review; FEA","Quasi-static roof crush test",5,"structural","panel","design"),
    _row("7.2","B-Pillar Upper Extension","Connect pillar to roof rail","Fatigue crack at roof rail interface","Creaking noise; potential structural degradation",6,"Stress concentration at access hole",5,"Access hole radius per spec","Durability cycle test",6,"fatigue","panel","design"),
    _row("8.1","Hinge Reinforcement Bracket","Maintain door hinge geometry","Deformation under door weight","Door sag below tolerance affecting sealing",6,"Bracket gauge insufficient",4,"Bracket FEA; gauge per spec","Hinge geometry check CMM",5,"structural","bracket","design"),
    _row("8.2","Hinge Reinforcement Bracket","Maintain door hinge geometry","Fatigue crack at nut boss","Loss of hinge attachment",8,"Cyclic door opening stress on boss",3,"Boss geometry per fatigue spec","Life test 100k cycles",4,"fatigue","bracket","design"),
    _row("9.1","UHSS Material (1500 MPa)","Maintain high-strength structural integrity","Hydrogen embrittlement cracking","Sudden brittle fracture under crash",9,"Hydrogen uptake during e-coat process",2,"Bake-out cycle after e-coat","In-process hydrogen check",4,"structural","panel","material"),
    _row("9.2","UHSS Material (1500 MPa)","Maintain high-strength structural integrity","Softening in HAZ from welding","Reduced strength in heat-affected zone",7,"Weld heat input exceeds UHSS limit",4,"Weld WPS qualification for UHSS","Hardness survey after weld",5,"structural","joint","manufacturing"),
    _row("10.1","Anti-corrosion Coating","Protect from underbody corrosion","Incomplete e-coat coverage in cavity","Internal corrosion in enclosed section",6,"Hang angle prevents coating penetration",4,"Drain hole size and position per spec","Cavity inspection borescope",6,"corrosion","panel","design"),
]


# ── Door Ring ─────────────────────────────────────────────────────────────────
DOOR_RING_ROWS: list[DfmeaRow] = [
    _row("1.1","A-Pillar Lower","Maintain frontal crash load path","Buckling under offset crash load","Excessive intrusion; injury risk",9,"Insufficient section modulus",4,"Section design review; NCAP FEA","Full vehicle crash test",6,"structural","panel","design"),
    _row("1.2","A-Pillar Lower","Maintain frontal crash load path","Fracture at firewall attachment weld","Loss of A-pillar attachment to firewall",9,"Weld under-fill at critical joint",3,"Weld inspection per ITP","Macro section quarterly",4,"structural","joint","manufacturing"),
    _row("1.3","A-Pillar Upper","Support windshield frame","Twist distortion during assembly","Windshield gap/flush out of spec",5,"Insufficient jig support during assembly",6,"Assembly jig with locating pins","Optical gap-flush gauge",4,"dimensional","panel","manufacturing"),
    _row("2.1","B-Pillar (Door Ring Segment)","Provide door strike attachment","Pull-out of striker weld nut","Door cannot close; safety risk",8,"Projection weld nut not seated",3,"Vision detection for nut presence","Destructive pull test 1/100",4,"structural","joint","manufacturing"),
    _row("2.2","B-Pillar (Door Ring Segment)","Provide door strike attachment","Corrosion at striker hole","Striker pull-out load reduction over time",6,"Water ingress at unsealed hole",5,"Rubber grommet sealing spec","Corrosion scan at 3yr audit",7,"corrosion","joint","environmental"),
    _row("3.1","Roof Rail","Carry roof crush load (FMVSS 216)","Buckling at joint with B-pillar","Excessive roof deflection",8,"Insufficient reinforcement at junction",3,"Joint reinforcement per FEA","Quasi-static roof crush",5,"structural","reinforcement","design"),
    _row("3.2","Roof Rail","Carry roof crush load (FMVSS 216)","Corrosion of inner roof rail","Section loss reducing crush resistance",6,"Water trap in roof ditch",5,"Drain holes per design spec","CT scan roof rail at 5yr",8,"corrosion","panel","design"),
    _row("4.1","Rocker Sill (Ring)","Transfer door ring loads to floor","Fatigue crack at notch","Loss of structural integrity in rear crash",8,"Sharp notch at tooling access hole",4,"Notch radius per fatigue guideline","Fatigue simulation FEA",5,"fatigue","panel","design"),
    _row("4.2","Rocker Sill (Ring)","Transfer door ring loads to floor","Corrosion from road spray","Perforation affecting crash performance",7,"Inadequate underbody coating in wheel arch area",5,"Coating thickness spec; anti-chip primer","4yr vehicle audit",7,"corrosion","panel","environmental"),
    _row("5.1","Door Ring Assembly Weld","Maintain ring dimensional integrity","Out-of-position weld shrinkage","Door opening dimensional error; poor sealing",5,"Uncontrolled weld sequence",5,"Weld sequence procedure; jig hold","Post-weld optical scan",4,"dimensional","joint","manufacturing"),
    _row("5.2","Door Ring Assembly Weld","Maintain ring dimensional integrity","Weld porosity at critical joint","Fatigue life reduction",7,"Moisture in weld wire",3,"Wire storage environment control","Ultrasonic test weld joints",4,"structural","joint","manufacturing"),
    _row("6.1","Hinge Pillar Reinforcement","Maintain hinge load path","Deformation under repeated door slam","Hinge geometry drift; door sag",6,"Reinforcement gauge insufficient",4,"Gauge review per hinge FEA","Door sag test 10k cycles",5,"fatigue","reinforcement","design"),
    _row("6.2","Hinge Pillar Reinforcement","Maintain hinge load path","Corrosion reducing hinge attachment strength","Hinge failure in service",7,"Coating discontinuity at inner face",4,"Wax injection of hinge pillar cavity","Destructive corrosion coupon",6,"corrosion","reinforcement","environmental"),
    _row("7.1","Latch Pillar Area","Provide door latch retention","Crushing of latch reinforcement","Door opens in side crash",9,"Insufficient latch reinforcement gauge",3,"Gauge per regulation requirement FEA","Side impact sled test",5,"structural","reinforcement","design"),
    _row("7.2","Latch Pillar Area","Provide door latch retention","Thread stripping on latch bolt","Door latch cannot be secured",7,"Under-torque on assembly line",3,"Torque SPC; angle monitoring","Torque audit daily",3,"structural","bracket","manufacturing"),
    _row("8.1","Door Opening Seal Face","Provide sealing surface for door","Surface waviness from weld distortion","Water ingress; wind noise",5,"Weld sequence distortion",6,"Weld sequence simulation","Waviness measurement post-weld",5,"dimensional","panel","manufacturing"),
    _row("8.2","Door Opening Seal Face","Provide sealing surface for door","Corrosion pitting on seal face","Seal degradation; water ingress",5,"Surface finish below spec",5,"Surface roughness spec on seal face","Profilometer check",4,"corrosion","panel","manufacturing"),
    _row("9.1","Ring-to-Floor Attachment Welds","Transfer ring loads to floor structure","Incomplete fusion at floor weld","Structural separation in crash",8,"Contamination on weld surface",3,"Pre-weld clean spec; degreasing step","Macro section sampling",4,"structural","joint","manufacturing"),
    _row("9.2","Ring-to-Floor Attachment Welds","Transfer ring loads to floor structure","Weld cracking under fatigue","Progressive loss of joint integrity",7,"Residual stress from fit-up mismatch",5,"Fit-up spec; tack sequence control","Fatigue test ring assembly",5,"fatigue","joint","manufacturing"),
    _row("10.1","Door Ring Dimensional Integrity","Maintain door opening geometry","Springback distortion in A-pillar","Door opens dimension out of spec",5,"Springback from UHSS A-pillar blank",6,"Springback compensation in die","100% CMM check at SOP",4,"dimensional","panel","material"),
]


# ── Roof Rail ─────────────────────────────────────────────────────────────────
ROOF_RAIL_ROWS: list[DfmeaRow] = [
    _row("1.1","Outer Roof Rail","Carry roof crush load (FMVSS 216)","Buckling under crush load","Excessive roof deflection; head injury",8,"Insufficient wall thickness",3,"Section review per roof crush FEA","Quasi-static roof crush test",5,"structural","panel","design"),
    _row("1.2","Outer Roof Rail","Carry roof crush load (FMVSS 216)","Corrosion of outer rail","Section loss reducing crush resistance",7,"Water ingress at roof ditch",5,"Drain hole sizing per design","5yr corrosion audit",8,"corrosion","panel","environmental"),
    _row("1.3","Outer Roof Rail","Support roof glass/panel","Distortion reducing glass fit","Wind noise; water leak around glass",5,"Thermal distortion in paint oven",5,"Thermal compensated fixture","Leakage test post-paint",4,"dimensional","panel","manufacturing"),
    _row("2.1","Inner Roof Rail","Reinforce outer rail section","Fatigue crack at crossmember bracket","Creaking noise; crack propagation",6,"Stress concentration at bracket weld",4,"Bracket geometry optimised; FEA","Durability roof bounce test",5,"fatigue","reinforcement","design"),
    _row("2.2","Inner Roof Rail","Reinforce outer rail section","Weld fracture at rail end","Loss of B-pillar to roof joint integrity",8,"Weld toe at high-stress location",3,"Weld geometry per WPS","Destructive macro coupon",4,"structural","joint","manufacturing"),
    _row("3.1","Crossmember Attachment Bracket","Transfer crossmember loads to rail","Bracket deformation under roof load","Headliner support failure",5,"Bracket gauge insufficient",4,"Gauge per load case","Bracket load test",4,"structural","bracket","design"),
    _row("3.2","Crossmember Attachment Bracket","Transfer crossmember loads to rail","Fatigue failure at weld","Bracket separation in service",7,"Cyclic load on thin bracket",4,"Fatigue simulation before sign-off","Bracket fatigue rig test",5,"fatigue","bracket","design"),
    _row("4.1","Roof Bow (Crossmember)","Support roof skin and headliner","Bowing from thermal expansion","Headliner contact with roof; noise",4,"Insufficient bow preload",5,"Bow height spec; simulation","Thermal cycle test",5,"dimensional","reinforcement","design"),
    _row("4.2","Roof Bow (Crossmember)","Support roof skin and headliner","Fracture of spot weld at end","Bow detaches; headliner falls",7,"Edge weld insufficient edge distance",4,"Edge distance per weld spec","Chisel test per shift",4,"structural","joint","manufacturing"),
    _row("5.1","Sunroof Reinforcement Ring","Provide sunroof aperture stiffness","Torsional twist of ring","Sunroof seal gap and water leak",6,"Insufficient ring torsional stiffness",4,"Ring section property per NVH FEA","Torsion test on BIW",5,"structural","reinforcement","design"),
    _row("5.2","Sunroof Reinforcement Ring","Provide sunroof aperture stiffness","Corrosion inside ring section","Structural integrity reduction",6,"Water trap inside closed section",4,"Drain provision per design","Drain check at prototype",7,"corrosion","reinforcement","design"),
    _row("6.1","Roof Rail to A-Pillar Joint","Maintain front-end roof load path","Weld fracture at A-pillar top","Loss of frontal crash load path",9,"Weld quality below spec",3,"Weld SPC; regular electrode check","Destructive weld test",4,"structural","joint","manufacturing"),
    _row("6.2","Roof Rail to A-Pillar Joint","Maintain front-end roof load path","Corrosion at joint","Section loss at critical joint",7,"Water trap at A-pillar/roof junction",4,"Drain hole + wax injection","CT scan 5yr vehicle",7,"corrosion","joint","environmental"),
    _row("7.1","Roof Rail to B-Pillar Joint","Transfer side impact load to roof","Separation under side impact","Roof separation; safety risk",9,"Weld area insufficient",3,"Weld area per side-impact FEA","Side impact pole test",5,"structural","joint","design"),
    _row("7.2","Roof Rail to B-Pillar Joint","Transfer side impact load to roof","Fatigue crack at joint","Progressive joint weakening",7,"Cyclic NVH vibration at joint",5,"Vibration analysis; damping pad","NVH durability test",5,"fatigue","joint","design"),
    _row("8.1","Roof Skin Attachment (hem)","Attach roof skin to rail","Hemmed joint delamination","Water ingress under roof skin",5,"Insufficient hem engagement length",4,"Hem length per spec","Peel test each shift",5,"structural","joint","manufacturing"),
    _row("8.2","Roof Skin Attachment (hem)","Attach roof skin to rail","Corrosion at hem","Rust bleed-through in roof edge",6,"Inadequate sealer in hem gap",4,"Sealer bead spec; vision check","Corrosion test panel",6,"corrosion","joint","manufacturing"),
    _row("9.1","Roof Rail Coating","Protect rail from corrosion","E-coat sag in rail cavity","Uncoated area; corrosion initiation",5,"Hang angle causes sag before cure",4,"Hang angle spec; drain hole","Coating pull-out inspection",6,"corrosion","panel","manufacturing"),
    _row("9.2","Roof Rail Coating","Protect rail from corrosion","Stone chip exposing base metal","Corrosion in wheel arch adjacent area",5,"Insufficient chip resistance coating",5,"Anti-chip primer spec","Stone chip test 10 cycles",6,"corrosion","panel","design"),
    _row("10.1","Roof Ditch Sealant","Seal roof panel to body","Sealant void leaving gap","Water leak into cabin",6,"Robot sealant break",4,"Sealant bead vision system","Water leak test 100%",3,"corrosion","joint","manufacturing"),
]


# ── Sill Assembly ─────────────────────────────────────────────────────────────
SILL_ROWS: list[DfmeaRow] = [
    _row("1.1","Outer Sill Panel","Provide Class-A show surface","Dents from road debris","Customer quality concern",5,"Insufficient panel stiffness",5,"Panel stiffness FEA; anti-flutter adhesive","Dent resistance test",6,"structural","panel","design"),
    _row("1.2","Outer Sill Panel","Provide Class-A show surface","Corrosion perforation from road spray","Rust visible; structural concern",7,"Insufficient underbody coating",5,"Chip-resistant primer + e-coat","4yr audit",7,"corrosion","panel","environmental"),
    _row("2.1","Inner Sill Reinforcement","Transfer longitudinal crash load","Progressive plastic hinge failure","Excessive floor pan intrusion",9,"Insufficient reinforcement section",3,"Section property per crash FEA","Full vehicle crash IIHS",5,"structural","reinforcement","design"),
    _row("2.2","Inner Sill Reinforcement","Transfer longitudinal crash load","Weld failure at reinforcement end","Loss of load path continuity",8,"Weld toe cracking under crash pulse",3,"Weld geometry FEA review","Crash sled test",4,"structural","joint","design"),
    _row("3.1","Sill Closeout Panel","Seal sill cavity","Water ingress through gap","Internal corrosion in sill cavity",6,"Gap in closeout mastic",4,"Mastic bead continuity spec","Leak test per shift",5,"corrosion","panel","manufacturing"),
    _row("3.2","Sill Closeout Panel","Seal sill cavity","Panel deformation sealing gap","Loss of seal integrity",5,"Panel too thin for local pressure",5,"Gauge review","Assembly leak check",5,"dimensional","panel","design"),
    _row("4.1","Floor Pan Attachment Weld","Attach sill to floor structure","Weld crack from floor vibration","NVH squeak and rattle",5,"Weld fatigue from floor modes",5,"Mode shape analysis; weld schedule","Durability road noise test",5,"fatigue","joint","design"),
    _row("4.2","Floor Pan Attachment Weld","Attach sill to floor structure","Weld porosity","Reduced static strength",7,"Wire moisture contamination",3,"Wire storage control","UT weld audit",4,"structural","joint","manufacturing"),
    _row("5.1","Rocker to B-Pillar Joint","Transfer door ring to sill loads","Separation under side crash","Loss of sill structural contribution",9,"Under-sized weld area at junction",3,"Weld area per CAE","Side impact MDB test",5,"structural","joint","design"),
    _row("5.2","Rocker to B-Pillar Joint","Transfer door ring to sill loads","Corrosion at joint","Progressive section loss",7,"Water trap in joint geometry",5,"Drain hole and sealer spec","4yr audit",8,"corrosion","joint","design"),
    _row("6.1","Step Pad Mounting Surface","Provide step entry surface","Thread pull-out from step load","Step pad detaches; injury risk",6,"Insufficient thread engagement in thin sill",4,"Thread length per static load req","Pull test each batch",4,"structural","bracket","design"),
    _row("6.2","Step Pad Mounting Surface","Provide step entry surface","Surface corrosion under step pad","Corrosion progresses under concealed area",5,"Water trap under pad attachment",5,"Sealer around mounting holes","5yr audit",8,"corrosion","bracket","environmental"),
    _row("7.1","UHSS Sill Insert","Provide high-strength side crash barrier","Softening of insert in welding HAZ","Reduced strength in side crash",8,"Excessive heat from adjacent weld",4,"Weld sequence and heat input control","Hardness survey after weld",5,"structural","reinforcement","manufacturing"),
    _row("7.2","UHSS Sill Insert","Provide high-strength side crash barrier","Corrosion between insert and outer sill","Galvanic corrosion reducing insert strength",6,"Dissimilar metal contact",4,"Isolation coating on insert face","Galvanic corrosion test",6,"corrosion","reinforcement","material"),
    _row("8.1","Sill Hinge/Latch Area Reinf","Distribute door hinge loads","Fatigue crack at lower hinge","Hinge separation",7,"Cyclic door load on thin wall",4,"Local reinforcement per fatigue FEA","Hinge fatigue test 200k",5,"fatigue","reinforcement","design"),
    _row("8.2","Sill Hinge/Latch Area Reinf","Distribute door hinge loads","Deformation from door slam","Door sag below geometry tolerance",5,"Reinforcement gauge insufficient",4,"Gauge per sag analysis","Door sag test per SOP",5,"structural","reinforcement","design"),
    _row("9.1","E-coat on Sill Assembly","Protect sill from corrosion","Insufficient e-coat thickness in pocket","Corrosion initiation in enclosed section",6,"Insufficient coverage due to part geometry",5,"Drain holes and hang angle spec","Film thickness gauge check",6,"corrosion","panel","manufacturing"),
    _row("9.2","E-coat on Sill Assembly","Protect sill from corrosion","Holiday (pinhole) in e-coat","Localised corrosion blister",5,"Contamination prior to dip",4,"Pre-treatment SPC; cleanliness spec","Holiday detector",4,"corrosion","panel","manufacturing"),
    _row("10.1","Sill Drainage System","Drain water from sill cavity","Blocked drain hole","Water accumulation; accelerated corrosion",6,"Assembly sealant plugging drain",5,"Drain hole protection mask spec","Post-assembly drain flow check",5,"corrosion","panel","manufacturing"),
    _row("10.2","Sill to Floor Pan Interface","Maintain sealant at floor joint","Sealant crack from thermal cycling","Water ingress; floor corrosion",5,"Sealant not flexible enough for thermal range",4,"Sealant specification with temperature range","Thermal cycle test",5,"corrosion","joint","design"),
]


# ── Strut Tower ───────────────────────────────────────────────────────────────
STRUT_TOWER_ROWS: list[DfmeaRow] = [
    _row("1.1","Strut Tower Shell","Transmit suspension loads to body","Fatigue crack at strut hole rim","Suspension failure; handling loss; safety risk",9,"High cyclic bending stress at hole rim",4,"Rim radius per fatigue spec; FEA verified","Fatigue rig test 500k cycles",5,"fatigue","panel","design"),
    _row("1.2","Strut Tower Shell","Transmit suspension loads to body","Buckling of tower under curb drop","Loss of suspension geometry",8,"Insufficient wall gauge",3,"Wall thickness per crash and load FEA","Curb drop simulation test",4,"structural","panel","design"),
    _row("1.3","Strut Tower Shell","Transmit suspension loads to body","Corrosion weakening tower wall","Fatigue cracks initiate earlier",7,"Water ingress through engine bay wash",5,"Drainage and sealing spec; e-coat","5yr corrosion audit",7,"corrosion","panel","environmental"),
    _row("2.1","Strut Mount Plate (Top)","Provide strut bearing mounting surface","Crack around mount stud holes","Strut bearing fails to locate; noise",8,"Fretting fatigue from micro-slip at mount",3,"Surface treatment; preload spec","500k cycle strut fatigue test",4,"fatigue","mount","design"),
    _row("2.2","Strut Mount Plate (Top)","Provide strut bearing mounting surface","Corrosion seizing mount studs","Strut cannot be serviced; risk of stuck nut",5,"Water pooling on mount face",5,"Drain provisions; rust-resistant finish","Service trial at 5yr",7,"corrosion","mount","environmental"),
    _row("2.3","Strut Mount Plate (Top)","Provide strut bearing mounting surface","Thread stripping on mount nuts","Strut bearing loose; noise and safety risk",8,"Under-torque at assembly",3,"Torque SPC; angle monitoring","Torque audit daily",3,"structural","mount","manufacturing"),
    _row("3.1","Strut Tower Reinforcement Ring","Distribute radial loads from strut","Weld cracking at ring-to-tower boundary","Crack propagation into tower",8,"Stress concentration at weld toe",4,"Weld toe geometry per fatigue spec","Fatigue test 500k",4,"fatigue","reinforcement","design"),
    _row("3.2","Strut Tower Reinforcement Ring","Distribute radial loads from strut","Delamination of reinforcement adhesive","Reduced ring stiffness; vibration",6,"Adhesive bond failure under cyclic shear",4,"Adhesive peel and fatigue spec","Adhesive shear test",5,"structural","joint","manufacturing"),
    _row("4.1","Firewall Attachment Weld","Transfer strut tower loads to firewall","Weld fracture at firewall junction","Firewall separation; noise and structural risk",8,"Weld under-fill at high-stress area",3,"Weld inspection ITP; 100% visual","Macro coupon quarterly",4,"structural","joint","manufacturing"),
    _row("4.2","Firewall Attachment Weld","Transfer strut tower loads to firewall","Corrosion at weld toe","Crack initiation earlier than design life",7,"Water trap at firewall junction",4,"Sealer and drain provision","4yr audit",7,"corrosion","joint","environmental"),
    _row("5.1","Suspension Sub-frame Mounting","Attach sub-frame to tower base","Thread stripping on sub-frame bolt","Sub-frame misalignment; tyre wear",7,"Over-torque on assembly line",4,"Torque SPC; angle monitoring","Destructive thread check 1/500",4,"structural","mount","manufacturing"),
    _row("5.2","Suspension Sub-frame Mounting","Attach sub-frame to tower base","Fatigue crack at mounting boss","Boss fracture; sub-frame loosens",8,"Cyclic shear load on boss",3,"Boss geometry per fatigue FEA","Fatigue test 500k cycles",4,"fatigue","mount","design"),
    _row("6.1","Shock Absorber Mounting Bracket","Provide upper shock mounting","Deformation of bracket under bump","Shock geometry change; handling degradation",6,"Bracket gauge insufficient",4,"Bracket load case review","Static load test",5,"structural","bracket","design"),
    _row("6.2","Shock Absorber Mounting Bracket","Provide upper shock mounting","Fatigue failure of bracket","Shock detaches; safety risk",9,"Cyclic bump loads exceed fatigue limit",3,"Bracket fatigue analysis; gauge increase","500k cycle fatigue test",4,"fatigue","bracket","design"),
    _row("7.1","Engine Mount Attachment Reinforcement","Resist engine torque and crash loads","Weld fracture under crash","Engine intrusion risk",9,"Weld area insufficient for crash load",3,"Weld area per crash FEA","Frontal barrier crash test",4,"structural","joint","design"),
    _row("7.2","Engine Mount Attachment Reinforcement","Resist engine torque and crash loads","Fatigue crack at reinforcement edge","Noise from crack; structural concern",6,"Stress concentration at notch",4,"Notch radius per fatigue guideline","200k cycle fatigue test",5,"fatigue","reinforcement","design"),
    _row("8.1","Strut Tower-to-Body Joint","Maintain body torsional rigidity","Insufficient weld area reducing stiffness","NVH degradation; handling sensitivity",6,"Weld count below design intent",5,"Weld count per NVH FEA","Torsion stiffness BIW test",5,"structural","joint","design"),
    _row("8.2","Strut Tower-to-Body Joint","Maintain body torsional rigidity","Weld porosity at joint","Reduced joint strength",7,"Moisture contamination in weld gas",3,"Shielding gas purity spec","UT audit",4,"structural","joint","manufacturing"),
    _row("9.1","E-coat on Strut Tower","Protect from road salt corrosion","E-coat void at complex geometry","Corrosion initiation in wheel arch area",7,"Complex geometry prevents e-coat penetration",5,"Drain holes and hang angle per spec","Film thickness check with probe",6,"corrosion","panel","manufacturing"),
    _row("9.2","E-coat on Strut Tower","Protect from road salt corrosion","Stone chip in wheel arch area","Corrosion blister near arch",6,"Road stone impact",6,"Stone chip resistant primer","Stone chip test per VDA 621-422",5,"corrosion","panel","environmental"),
    _row("10.1","Strut Tower Stiffening Gusset","Improve local stiffness","Gusset weld failure","Local tower deformation under heavy loads",6,"Weld quality below spec",5,"Weld SPC; electrode dress schedule","Chisel test per shift",5,"structural","reinforcement","manufacturing"),
]


ALL_ASSEMBLIES = {
    "b_pillar":      B_PILLAR_ROWS,
    "door_ring":     DOOR_RING_ROWS,
    "roof_rail":     ROOF_RAIL_ROWS,
    "sill_assembly": SILL_ROWS,
    "strut_tower":   STRUT_TOWER_ROWS,
}


def main() -> None:
    os.makedirs(BASE_DIR, exist_ok=True)
    random.seed(RANDOM_SEED)

    for name, rows in ALL_ASSEMBLIES.items():
        out_path = os.path.join(BASE_DIR, f"{name}_base.json")
        data = {
            "assembly": name,
            "version": "base",
            "row_count": len(rows),
            "rows": [asdict(r) for r in rows],
        }
        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  {name}_base.json — {len(rows)} rows")

    print(f"\nGenerated {len(ALL_ASSEMBLIES)} base documents in {BASE_DIR}")


if __name__ == "__main__":
    main()
