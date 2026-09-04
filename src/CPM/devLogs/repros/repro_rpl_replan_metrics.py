"""Repro RP-l: `_generate_info_from` (the partial-CPM step of replan) recomputes
ES/EF/LS/LF/slack but NOT the structural priority metrics (mts/mtp/grpw/grd/rr/
avgrr/maxrr/minrr).  Injecting new activities during replan runs
`resetInitialGraph()` → `resetInfo()`, which zeroes all of those metrics; the
partial CPM never restores them.  A subsequent schedule that sorts candidates by
one of those rules therefore sorts on all-zero values.

Two questions:
  PART A (defect): after the replan-internal sequence (_inject_activities +
                   _generate_info_from), are the structural metrics stale (0)
                   for pre-existing activities that had nonzero values?
  PART B (reach) : is this reachable through the PUBLIC API?  `replan()` takes
                   no priority_rule (always TF_based/external), but the metrics
                   stay zeroed afterwards — so does a later public
                   `calculateScheduleWithResources(priority_rule='grpw')` sort on
                   the stale zeros, or is something healing them?
"""
import sys, os, logging
from datetime import datetime, timedelta
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool

START = datetime(2026, 1, 1, 0, 0)
RULE = 'grpw'   # structural metric zeroed by resetInfo, read by priority_calculation


def build():
    # START -> A(4) -> B(3) -> C(2) -> END : distinct, nonzero grpw along the chain.
    s = Activity('START', 0.0)
    a, b, c = Activity('A', 4.0), Activity('B', 3.0), Activity('C', 2.0)
    e = Activity('END', 0.0)
    fwd = {s: [a], a: [b], b: [c], c: [e], e: []}
    p = Pert(graph=fwd)
    p.startTime = START
    p.crew_pool, p.equipment_pool, p.location_pool = (
        ResourcePool(), EquipmentPool(), LocationPool())
    return p, a, b, c


def grpw_of(p, act):
    return p.infoDict[act]['grpw']


# ── PART A — function-level defect ──────────────────────────────────────────
pA, a, b, c = build()
before = {n: grpw_of(pA, act) for n, act in (('A', a), ('B', b), ('C', c))}

# Replay the replan-internal steps directly (bypassing the public wrapper):
new = Activity('X', 6.0)
new.childs = ['C']                    # X -> C
pA._inject_activities([new])          # resetInitialGraph() -> resetInfo() zeroes metrics
pA._generate_info_from(1.0)           # partial CPM (does NOT recompute grpw/…)
after_partial = {n: grpw_of(pA, act) for n, act in (('A', a), ('B', b), ('C', c))}

# A full generateInfo() would restore them:
pA.generateInfo()
after_full = {n: grpw_of(pA, act) for n, act in (('A', a), ('B', b), ('C', c))}

print("=== PART A — metrics after replan-internal sequence (rule = grpw) ===")
print(f"grpw before inject         : {before}")
print(f"grpw after _generate_info_from: {after_partial}")
print(f"grpw after full generateInfo(): {after_full}")
defect = any(before[n] > 0 for n in before) and all(after_partial[n] == 0 for n in after_partial)
print(f"DEFECT (partial CPM leaves metrics stale/zero): {defect}")


# ── PART B — reachability through the public API ────────────────────────────
pB, a2, b2, c2 = build()
pB.calculateScheduleWithResources()                       # baseline (TF_based)
new2 = Activity('X', 6.0); new2.childs = ['C']
pB.replan(1.0, new_activities=[new2])                     # public replan → zeroes metrics

# A later PUBLIC full schedule that DOES request a structural rule.  Two possible
# manifestations of the stale infoDict:
#   (1) KeyError — resetInfo() never initialises the 'mehh_*'/'gphh'/… tie-break
#       keys that priority_calculation reads, and _generate_info_from doesn't add
#       them, so the rule's tie-breaker raises.
#   (2) silent all-zero sort — for a rule whose value is a zeroed metric with no
#       missing tie-break key.
public_crash = None
try:
    pB.calculateScheduleWithResources(priority_rule=RULE)
    after_public = {n: grpw_of(pB, act) for n, act in (('A', a2), ('B', b2), ('C', c2))}
    reachable_zeros = all(after_public[n] == 0 for n in after_public)
except KeyError as ex:
    public_crash = repr(ex)
    after_public = None
    reachable_zeros = False

print("\n=== PART B — public replan + calculateScheduleWithResources(priority_rule='grpw') ===")
if public_crash is not None:
    print(f"RAISED KeyError {public_crash} — stale infoDict is missing the "
          f"tie-break key resetInfo() never set and _generate_info_from never added")
else:
    print(f"grpw seen by the rule-based schedule: {after_public}")
    print(f"sorted on zeros: {reachable_zeros}")

reachable = public_crash is not None or reachable_zeros

print("\n=== VERDICT ===")
if defect and public_crash is not None:
    print("REPRODUCED (reachable — CRASH): _generate_info_from leaves the infoDict "
          "priority-metric block stale/incomplete after injection; a later public "
          "calculateScheduleWithResources(priority_rule=…) RAISES KeyError on a "
          "missing tie-break key (RP-l — reachable via a public two-call sequence, "
          "sharper than the documented latent 'sorts-on-zeros').")
elif defect and reachable_zeros:
    print("REPRODUCED (reachable): metrics stale after injection; the public "
          "rule-based schedule sorts candidates on zeroed values (RP-l reachable).")
elif defect and not reachable:
    print("REPRODUCED (latent): metrics stale after _generate_info_from, but the "
          "public path recomputed them before the rule-based schedule read them.")
else:
    print("NOT REPRODUCED (fixed): _generate_info_from recomputes the full priority-"
          "metric block, so replan + rule-based scheduling neither crashes nor "
          "sorts on stale values.")
