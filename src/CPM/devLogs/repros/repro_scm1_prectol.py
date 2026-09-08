"""Repro SC-m1: Pert.check_dependency_violations uses a STRICT comparison
(`succ_start < pred_end + lag`) with no time tolerance, while the authoritative
schedule_validator._check_precedence allows a 1-minute grace (`_PREC_TOL`).

So the two precedence surfaces disagree at the margin: a successor that starts
a sub-minute amount before pred_end+lag — the kind of gap that hour→timedelta
float arithmetic can produce — is reported INFEASIBLE by
check_dependency_violations() but FEASIBLE by validate_schedule()'s precedence
check.  Same schedule, two different feasibility verdicts.

Setup: START(0) -> A(4h) -> B(3h) -> END(0).  Place A at [0h,4h].  Place B to
start 30 seconds BEFORE A's finish (inside the 60-second grace).

Question: do the two checks disagree?
"""
import sys, os, logging
from datetime import datetime, timedelta
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool
from CPM.schedule_validator import _check_precedence

START = datetime(2026, 1, 1, 0, 0)

start = Activity('START', 0.0)
A = Activity('A', 4.0)
B = Activity('B', 3.0)
end = Activity('END', 0.0)
fwd = {start: [A], A: [B], B: [end], end: []}

p = Pert(graph=fwd)
p.startTime = START
p.crew_pool = ResourcePool()
p.equipment_pool = EquipmentPool()
p.location_pool = LocationPool()

# Hand-place a schedule with a 30-second early successor (inside the 60s grace).
start.startTime, start.endTime = START, START
A.startTime, A.endTime = START, START + timedelta(hours=4)
B.startTime = A.endTime - timedelta(seconds=30)          # 30s early
B.endTime = B.startTime + timedelta(hours=3)
end.startTime = end.endTime = B.endTime

p.completed = [start, A, B, end]
p._completed_set = set(p.completed)

# 1) Pert's own dependency check (strict, no tolerance)
violations, dep_feasible = p.check_dependency_violations()

# 2) Authoritative validator precedence check (60s grace)
val_violations = []
_check_precedence(p, val_violations, [])
val_feasible = len(val_violations) == 0

gap_s = (A.endTime - B.startTime).total_seconds()
print("=== Setup ===")
print(f"A finishes at {A.endTime};  B starts at {B.startTime}  ({gap_s:.0f}s early)")
print(f"_PREC_TOL grace = 60s\n")
print("=== Verdicts ===")
print(f"check_dependency_violations(): feasible = {dep_feasible}  "
      f"({len(violations)} violation(s))")
print(f"validate_schedule precedence : feasible = {val_feasible}  "
      f"({len(val_violations)} violation(s))")

print("\n=== VERDICT ===")
if dep_feasible != val_feasible:
    print("REPRODUCED: check_dependency_violations flags a sub-minute gap the "
          "validator tolerates — the two precedence surfaces disagree (SC-m1).")
else:
    print("NOT REPRODUCED (fixed): both surfaces agree within the 60s grace.")
