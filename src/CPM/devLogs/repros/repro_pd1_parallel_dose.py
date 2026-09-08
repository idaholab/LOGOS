"""Repro: PD1 — parallel SGS over-commits the dose budget within one time-step.

The parallel selection loop tentatively decrements a shared capacity snapshot
(crew / equipment / location) as each candidate is picked, so later candidates
in the *same* time-step see the reduced availability.  Dose was the exception:
_apply_tentative never charged the tracker, so every candidate's dose check read
the same (pre-time-step) consumed_mrem.

Here three 400 mRem activities are all ready at t=0.  Crew admits all three
(6 MECHANIC, 2 each = 6), but the dose budget (100/worker × 6 = 600 mRem) admits
only ONE.  The buggy loop placed all three — 1200 mRem against a 600 mRem budget,
a genuinely INFEASIBLE schedule.  The fix threads a transient per-time-step dose
overlay through the tentative snapshot so the second candidate's check sees the
first's tentative draw and is correctly rejected.
"""
import sys, os, logging
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from datetime import datetime
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool, ResourceAvailability


def make_pert():
    start_dt = datetime(2025, 6, 1, 6, 0)
    rp = ResourcePool()
    # consumable skill: budget_per_worker=100 × peak 6 = 600 mRem total budget.
    # Crew (6) admits all three activities (2 each); dose admits only one.
    rp.resources['MECHANIC'] = ResourceAvailability(
        'MECHANIC',
        [{'start_date': datetime(2025, 1, 1), 'end_date': datetime(2025, 12, 31),
          'available_count': 6}],
        resource_type='consumable',
        dose_budget_per_worker_mrem=100.0,
    )

    def act(name):
        a = Activity(name, 4.0, required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
        a.dose_rate_mrem_per_hour = 50.0   # 50 × 2 × 4 = 400 mRem per activity
        return a

    A, B, C = act('A'), act('B'), act('C')
    S, E = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {S: [A, B, C], A: [E], B: [E], C: [E], E: []}

    p = Pert(graph=fwd)
    p.crew_pool = rp
    p.equipment_pool = EquipmentPool()
    p.location_pool = LocationPool()
    p.dose_trackers = rp.build_dose_trackers()
    p._precompute_availability_events()
    p.generateInfo()
    p.startTime = start_dt
    return p


BUDGET = 100.0 * 6           # 600 mRem
PER_ACT_DOSE = 50.0 * 2 * 4  # 400 mRem

print("Total dose budget      :", BUDGET, "mRem")
print("Dose per dose-activity :", PER_ACT_DOSE, "mRem  (crew admits 3, budget admits 1)")
print()

results = {}
for sgs in ('max_use_res_ranked', 'max_use_res_shuffled'):
    p = make_pert()
    p.calculateScheduleWithResources(sgs=sgs)
    tr = p.dose_trackers['MECHANIC']
    placed = sorted(a.name for a in p.completed
                    if getattr(a, 'dose_rate_mrem_per_hour', 0) > 0)
    results[sgs] = (placed, tr.consumed_mrem, tr.total_budget_mrem)
    print("%-22s placed: %-15s consumed = %s / %s mRem"
          % (sgs, placed, tr.consumed_mrem, tr.total_budget_mrem))

print("\n=== VERDICT ===")
over = any(consumed > budget for _, consumed, budget in results.values())
multi = any(len(placed) > 1 for placed, _, _ in results.values())
if over or multi:
    worst = max(results.items(), key=lambda kv: kv[1][1])
    print("CONFIRMED PD1: %s placed %d dose activities consuming %s / %s mRem — "
          "same-time-step dose over-commit yields an infeasible schedule."
          % (worst[0], len(worst[1][0]), worst[1][1], worst[1][2]))
else:
    print("NOT REPRODUCED (fixed): every strategy placed exactly 1 dose activity "
          "(%s mRem ≤ %s budget); the tentative dose overlay blocks the "
          "same-time-step over-commit." % (PER_ACT_DOSE, BUDGET))
