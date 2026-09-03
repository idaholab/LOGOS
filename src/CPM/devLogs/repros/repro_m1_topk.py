"""Repro M-1: _rank_by_value_top_k estimates the top-k cutoff (max_slots) from
crew availability at self.startTime, not at the current scheduling time.  When
availability GROWS after startTime, max_slots is under-counted, k = max_slots*8
is too small, and heapq.nlargest(k) truncates candidates that are actually
placeable NOW — deferring them to a later event and inflating the makespan.

Setup (full scheduler, sgs='max_use_res_ranked'):
  MECH pool = 1 for [0h, 10h), then 30 for [10h, +inf).
  START -> P(10h, 1 MECH) -> {A1..A20}(5h, 1 MECH each) -> END.
  P runs [0,10) (avail 1 is enough).  At t=10h all 20 A's become ready and the
  pool is 30 — so all 20 can start at once (optimal makespan = 10 + 5 = 15h).

  max_slots is computed from availability at startTime (t=0, avail=1) -> 1,
  so k = 1*8 = 8 < 20 candidates -> only 8 A's are seen at t=10h.  The other
  12 wait for the next event (t=15h), and 4 more wait again -> makespan 25h.
  Correct: sample availability at t=10h (=30) -> k = 240 >= 20 -> all placed.

Question: does the truncation actually inflate the makespan?
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
N = 20


def mech_pool(low=1, high=30, rise_h=10):
    rp = ResourcePool()
    rp.resources['MECH'] = ResourceAvailability('MECH', [
        {'start_date': START, 'end_date': START + timedelta(hours=rise_h),
         'available_count': low},
        {'start_date': START + timedelta(hours=rise_h), 'end_date': FAR,
         'available_count': high},
    ])
    return rp


start = Activity('START', 0.0)
p_act = Activity('P', 10.0, required_resources=[{'skill_type': 'MECH', 'crew_count': 1}])
end = Activity('END', 0.0)
kids = [Activity(f'A{i}', 5.0, required_resources=[{'skill_type': 'MECH', 'crew_count': 1}])
        for i in range(N)]

fwd = {start: [p_act], p_act: list(kids), end: []}
for k in kids:
    fwd[k] = [end]

p = Pert(graph=fwd)
p.startTime = START
p.crew_pool = mech_pool()
p.equipment_pool = EquipmentPool()
p.location_pool = LocationPool()

res = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
makespan = res['scheduled_duration']

# How many distinct start times among the 20 kids? (1 => all launched together)
kid_starts = sorted({k.returnAbsTimes()[0] for k in kids})
waves = len(kid_starts)

print("=== Availability ===")
print(f"MECH at startTime (0h)  = {p.crew_pool.get_availability('MECH', START)}  (max_slots sampled HERE)")
print(f"MECH at kids-ready (10h)= {p.crew_pool.get_availability('MECH', START + timedelta(hours=10))}")
print("\n=== Schedule ===")
print(f"kid start-time waves = {waves}  (optimal = 1: all 20 start together at 10h)")
for i, t in enumerate(kid_starts):
    n_here = sum(1 for k in kids if k.returnAbsTimes()[0] == t)
    print(f"  wave {i+1}: {n_here} activities start at "
          f"{(t - START).total_seconds()/3600.0:.0f}h")
print(f"makespan = {makespan:.1f}h  (optimal = 15h)")

print("\n=== VERDICT ===")
if makespan > 15.0 + 1e-6 or waves > 1:
    print(f"REPRODUCED: top-k truncated the candidate set (max_slots from startTime "
          f"availability=1), so the 20 ready activities launched in {waves} waves and "
          f"the makespan is {makespan:.1f}h instead of the optimal 15h (M-1).")
else:
    print("NOT REPRODUCED (fixed): all 20 launched at once; makespan 15h.")
