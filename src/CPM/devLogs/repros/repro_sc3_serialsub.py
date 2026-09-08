"""Repro SC3: the Serial SGS feasibility check performs NO skill substitution,
while the parallel path (_fits_with_tentative) draws a shortfall from
`alternative_skill_types`.  So an activity that is feasible only via substitution
is scheduled by the parallel scheduler but over-delayed (here: never scheduled)
by the serial scheduler.

Setup: START -> A(5h, needs 1 ELEC, alt=[MECH]) -> END.
  ELEC pool = 0 everywhere;  MECH pool = 1 everywhere.
  Parallel: primary ELEC=0 < 1, borrow 1 MECH -> feasible, A runs [0,5h].
  Serial : _serial_check_feasibility counts ELEC only (0 < 1) -> infeasible at
           every start -> A is skipped.

Question: does the serial path drop a substitution-feasible activity that the
parallel path schedules?
"""
import sys, os, logging
from datetime import datetime, timedelta
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, ResourceAvailability, EquipmentPool, LocationPool

START = datetime(2026, 1, 1, 0, 0)
FAR = START + timedelta(days=365)


def pools():
    rp = ResourcePool()
    rp.resources['ELEC'] = ResourceAvailability('ELEC', [
        {'start_date': START, 'end_date': FAR, 'available_count': 0}])
    rp.resources['MECH'] = ResourceAvailability('MECH', [
        {'start_date': START, 'end_date': FAR, 'available_count': 1}])
    return rp, EquipmentPool(), LocationPool()


def build():
    s = Activity('START', 0.0)
    a = Activity('A', 5.0, required_resources=[
        {'skill_type': 'ELEC', 'crew_count': 1, 'alternative_skill_types': ['MECH']}])
    e = Activity('END', 0.0)
    fwd = {s: [a], a: [e], e: []}
    p = Pert(graph=fwd)
    p.startTime = START
    p.crew_pool, p.equipment_pool, p.location_pool = pools()
    return p, a


# Parallel
p_par, a_par = build()
p_par.calculateScheduleWithResources(sgs='max_use_res_ranked')
par_start = a_par.returnAbsTimes()[0]

# Serial
p_ser, a_ser = build()
p_ser.calculateSerialScheduleWithResources()
ser_start = a_ser.returnAbsTimes()[0]

print("=== A ('needs 1 ELEC' with alt=[MECH]; ELEC=0, MECH=1) ===")
print(f"parallel: A.start = {par_start}  "
      f"({'scheduled' if par_start is not None else 'NOT scheduled'})")
print(f"serial  : A.start = {ser_start}  "
      f"({'scheduled' if ser_start is not None else 'NOT scheduled'})")

print("\n=== VERDICT ===")
if par_start is not None and ser_start is None:
    print("REPRODUCED: parallel substitutes ELEC->MECH and schedules A; serial "
          "counts ELEC only, finds it infeasible, and drops A (SC3).")
elif par_start is not None and ser_start is not None and ser_start > par_start:
    print(f"REPRODUCED: serial delayed A to {ser_start} vs parallel {par_start} "
          "(serial ignored the ELEC->MECH substitution) (SC3).")
else:
    print("NOT REPRODUCED (fixed): serial applies substitution like the parallel path.")
