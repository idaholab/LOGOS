"""Repro B4: _build_augmented_graph's resource-binding skip gate samples pool
availability at self.startTime, but the actual per-pair binding test samples it
at the pair's overlap_start.  When a pool is *lower* during an overlap than at
startTime, the gate (2*max_demand >= avail_at_startTime) can wrongly close and
skip the entire O(n^2) pair scan — dropping a genuine resource-flow arc, so the
resource-constrained critical chain comes out too short.

Setup (times set directly, then _build_augmented_graph() called in isolation):
  Pool MECH = 5 for [0h, 4h), then 4 for [4h, +inf).
  START(0) -> A(2 MECH), START -> B(2 MECH), A -> END, B -> END.
  A and B both scheduled [4h, 14h): they overlap, and at overlap_start=4h the
  pool is 4, so combined demand 2+2=4 >= 4 -> the pair IS binding (a saturating
  overlap a real scheduler would produce).

  Skip gate at startTime (t=0, avail=5): 2*max_demand = 4 < 5 -> gate CLOSES ->
  scan skipped -> the A<->B binding arc is never added (BUG).
  Correct: use the minimum availability over the scheduled horizon (=4) ->
  4 >= 4 -> gate opens -> arc added.

Question: is the binding arc present in the augmented graph?
"""
import sys, logging
from datetime import datetime, timedelta
logging.disable(logging.CRITICAL)
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, ResourceAvailability, EquipmentPool, LocationPool

START = datetime(2026, 1, 1, 0, 0)
FAR = START + timedelta(days=365)


def mech_pool(high=5, low=4, drop_h=4):
    rp = ResourcePool()
    rp.resources['MECH'] = ResourceAvailability('MECH', [
        {'start_date': START, 'end_date': START + timedelta(hours=drop_h),
         'available_count': high},
        {'start_date': START + timedelta(hours=drop_h), 'end_date': FAR,
         'available_count': low},
    ])
    return rp


start = Activity('START', 0.0)
a = Activity('A', 10.0, required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
b = Activity('B', 10.0, required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
end = Activity('END', 0.0)
fwd = {start: [a], a: [end], b: [end], end: []}
fwd[start].append(b)

p = Pert(graph=fwd)
p.startTime = START
p.crew_pool = mech_pool()
p.equipment_pool = EquipmentPool()
p.location_pool = LocationPool()

# Place A and B overlapping during the LOW window [4h, 14h).
for act in (a, b):
    act.startTime = START + timedelta(hours=4)
    act.endTime = START + timedelta(hours=14)
start.startTime = START; start.endTime = START
end.startTime = START + timedelta(hours=14); end.endTime = START + timedelta(hours=14)

avail_t0 = p.crew_pool.get_availability('MECH', START)
avail_overlap = p.crew_pool.get_availability('MECH', START + timedelta(hours=4))
print("=== Availability ===")
print(f"MECH at startTime (0h) = {avail_t0}   (skip gate samples HERE)")
print(f"MECH at overlap  (4h)  = {avail_overlap}   (binding test samples HERE)")
print(f"combined demand A+B    = 4 ; binding iff combined >= avail_overlap "
      f"-> {4 >= avail_overlap}")
print(f"skip gate closes iff 2*max_demand < avail_sampled : "
      f"at t0 {4 < avail_t0} (skips!) ; at overlap {4 < avail_overlap}")

aug = p._build_augmented_graph()
arc_ab = b in aug.get(a, [])
arc_ba = a in aug.get(b, [])
print("\n=== Augmented graph ===")
print(f"A -> {[n.returnName() for n in aug.get(a, [])]}")
print(f"B -> {[n.returnName() for n in aug.get(b, [])]}")
print(f"resource-flow arc between A and B present: {arc_ab or arc_ba}")

print("\n=== VERDICT ===")
if not (arc_ab or arc_ba):
    print("REPRODUCED: binding arc dropped — skip gate used startTime availability "
          f"({avail_t0}) and closed, though the pair saturates the pool ({avail_overlap}) "
          "at their overlap (B4).")
else:
    print("NOT REPRODUCED (fixed): binding arc present — skip gate used the "
          "horizon-minimum availability.")
