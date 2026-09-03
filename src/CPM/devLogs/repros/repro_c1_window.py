"""Repro C1: _apply_time_windows tightens a windowed activity's ES/EF but never
propagates the raised EF forward to successors.

Graph: START -> A(4h, window_earliest_start=10h) -> B(4h) -> END
CPM (no window): A.ES=0 A.EF=4 ; B.ES=4 B.EF=8 ; project=8
Correct w/ window: A.ES=10 A.EF=14 ; B.ES=14 B.EF=18 ; project=18
Bug:               A.ES=10 A.EF=14 ; B.ES=4  B.EF=8  ; project reported=14 (< true 18)
Question: does the ACTUAL schedule also come out wrong, or only reported CPM values?
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

start = Activity('START', 0.0)
a = Activity('A', 4.0)
a.time_windows = [{'earliest': 10.0, 'latest': float('inf')}]   # release date at h=10
b = Activity('B', 4.0)
end = Activity('END', 0.0)
fwd = {start: [a], a: [b], b: [end], end: []}

p = Pert(graph=fwd)
p.startTime = START
p.generateInfo()

def es(x): return p.infoDict[x]['es']
def ef(x): return p.infoDict[x]['ef']
def sl(x): return p.infoDict[x]['slack']

print("=== Reported CPM values after generateInfo (+_apply_time_windows) ===")
print(f"A: ES={es(a)} EF={ef(a)} slack={sl(a)}")
print(f"B: ES={es(b)} EF={ef(b)} slack={sl(b)}   <-- correct ES=14 EF=18")
print(f"reported project duration = {p.projectDuration if hasattr(p,'projectDuration') else 'n/a'}")
print(f"max EF across nodes = {max(ef(x) for x in fwd)}")

print("\n=== Precedence consistency in REPORTED values ===")
if es(b) < ef(a):
    print(f"INCONSISTENT: B.ES={es(b)} < A.EF={ef(a)} (successor starts before predecessor finishes on paper)")
else:
    print("consistent")

# Now the ACTUAL resource schedule (no resources, pure precedence + window).
p.crew_pool = ResourcePool(); p.equipment_pool = EquipmentPool(); p.location_pool = LocationPool()
res = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
sa, ea = a.returnAbsTimes(); sb, eb = b.returnAbsTimes()
h = lambda t: (t - START).total_seconds()/3600.0 if t else None
print("\n=== ACTUAL schedule ===")
print(f"A: {h(sa)} -> {h(ea)}")
print(f"B: {h(sb)} -> {h(eb)}   (correct: 14 -> 18)")
print(f"scheduled_duration = {res['scheduled_duration']}")

print("\n=== VERDICT ===")
report_wrong = es(b) < ef(a) - 1e-9
sched_ok = sb is not None and abs(h(sb) - 14.0) < 1e-6
if report_wrong and sched_ok:
    print("CONFIRMED C1 (reported-values only): CPM ES/EF/slack + reported project duration are wrong "
          "for successors of a windowed activity, but the actual schedule enforces precedence correctly.")
elif report_wrong and not sched_ok:
    print(f"CONFIRMED C1 (SCHEDULE AFFECTED): B actually scheduled at h={h(sb)} (should be 14).")
else:
    print("NOT REPRODUCED.")
