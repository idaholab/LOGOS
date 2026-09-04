"""Repro: RP2 — clone_for_analysis drops availability-boundary events.

WELDER is unavailable [0,10h], then 2 units.  A (needs 1 WELDER) can only start
once the pool opens at h=10.  The original Pert (events precomputed after pools
are attached) schedules A[10,15] -> 3/3.  clone_for_analysis() hard-set
_availability_events to an empty frozenset and nothing repopulated it, so the
clone's event-driven scheduler never wakes at h=10 and dead-locks at 1/3.
"""
import sys, os, logging
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from datetime import datetime, timedelta
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool, ResourceAvailability

START = datetime(2026, 1, 1)
FAR = START + timedelta(days=365)


def make_pert():
    rp = ResourcePool()
    rp.resources['WELDER'] = ResourceAvailability('WELDER', [
        {'start_date': START, 'end_date': START + timedelta(hours=10), 'available_count': 0},
        {'start_date': START + timedelta(hours=10), 'end_date': FAR, 'available_count': 2}])
    s = Activity('START', 0.0)
    a = Activity('A', 5.0, required_resources=[{'skill_type': 'WELDER', 'crew_count': 1}])
    e = Activity('END', 0.0)
    fwd = {s: [a], a: [e], e: []}
    p = Pert(graph=fwd)
    p.crew_pool = rp
    p.equipment_pool = EquipmentPool()
    p.location_pool = LocationPool()
    p.startTime = START
    p._precompute_availability_events()   # pools attached post-__init__
    p.generateInfo()
    return p


p = make_pert()
res = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
print("original: n_completed =", res['n_completed'], "/", res['n_activities'],
      "| events =", len(p._availability_events))

clone = p.clone_for_analysis()
print("clone._availability_events size :", len(clone._availability_events))
clone.generateInfo()
cres = clone.calculateScheduleWithResources(sgs='max_use_res_ranked')
print("clone:    n_completed =", cres['n_completed'], "/", cres['n_activities'])

print("\n=== VERDICT ===")
if not clone._availability_events or cres['n_completed'] != cres['n_activities']:
    print("CONFIRMED RP2: clone lost its availability events (%d) and completed "
          "only %d/%d — the h=10 WELDER wake-up was dropped."
          % (len(clone._availability_events), cres['n_completed'], cres['n_activities']))
else:
    print("NOT REPRODUCED (fixed): clone has %d availability events and completed "
          "%d/%d, matching the original." % (len(clone._availability_events),
                                             cres['n_completed'], cres['n_activities']))
