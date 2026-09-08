"""Repro C3: max_use_res_ranked early-break starves zero-crew candidates.

Constant pool (no time-varying) to isolate from C2.

Graph:  START -> A(6h, needs 2 MECH) -> END
                \-> M(4h, needs 0 crew) -/
Pool: MECH = 2 (constant).
External priorities: A high (1.0), M low (0.1) so A is ranked before M.

Correct: M needs no crew, so it should start at t=0 alongside A. Makespan = 6h.
Buggy:   A selected first at t=0, consumes all MECH; early-break then stops the
         scan (res_rem[MECH][0]=0 < univ_min[MECH]=2) so M is skipped at t=0 and
         delayed until A frees MECH at t=6. M runs [6,10]. Makespan = 10h.
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

def make_pool(count):
    rp = ResourcePool()
    rp.resources['MECH'] = ResourceAvailability('MECH', [
        {'start_date': START, 'end_date': START + timedelta(days=365), 'available_count': count}])
    return rp

start = Activity('START', 0.0)
a = Activity('A', 6.0, required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
m = Activity('M', 4.0, required_resources=[])   # zero-crew milestone/inspection
end = Activity('END', 0.0)
fwd = {start: [a, m], a: [end], m: [end], end: []}

p = Pert(graph=fwd, priorities={'START': 0.9, 'A': 1.0, 'M': 0.1, 'END': 0.5})
p.crew_pool = make_pool(2)
p.equipment_pool = EquipmentPool()
p.location_pool = LocationPool()
p.startTime = START
p.generateInfo()

result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')

sa, ea = a.returnAbsTimes()
sm, em = m.returnAbsTimes()
print(f"_univ_skill_min = {p._univ_skill_min}")
print(f"A: {sa} -> {ea}   (needs 2 MECH)")
print(f"M: {sm} -> {em}   (needs 0 crew; correct start = t=0)")
print(f"scheduled_duration = {result['scheduled_duration']}  n_completed={result['n_completed']}/{result['n_activities']}")

m_start_h = (sm - START).total_seconds()/3600.0 if sm else None
print("\n=== VERDICT ===")
if m_start_h and m_start_h > 0.01:
    print(f"CONFIRMED C3: zero-crew M was delayed to h={m_start_h:.0f} (should be 0). "
          f"Makespan {result['scheduled_duration']:.0f}h vs optimal 6h. Early-break starved it.")
else:
    print(f"NOT REPRODUCED: M started at h={m_start_h}.")
