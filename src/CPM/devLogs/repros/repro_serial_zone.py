import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from datetime import datetime
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (ResourcePool, EquipmentPool, EquipmentAvailability,
                             LocationPool, LocationAvailability)

_START, _END = datetime(2026, 1, 1), datetime(2026, 12, 31)


def make_pert():
    # Equipment EQ1 is zone-locked to ZONE_1, 2 units available.
    ep = EquipmentPool()
    ep.equipment['EQ1'] = EquipmentAvailability(
        'EQ1', 'desc',
        [{'start_date': _START, 'end_date': _END, 'quantity_available': 2}],
        zone_id='ZONE_1')

    # Location pool: ZONE_2 exists with generous capacity.
    lp = LocationPool()
    for lid in ('ZONE_1', 'ZONE_2'):
        lp.locations[lid] = LocationAvailability(
            lid, 'desc',
            [{'start_date': _START, 'end_date': _END,
              'max_concurrent_tasks': 10, 'max_concurrent_workers': None}])

    rp = ResourcePool()  # no crew needed

    # Activity A uses zone-locked EQ1 but sits in ZONE_2 (wrong zone) -> illegal.
    A = Activity('A', 4.0)
    A.required_resources = []
    A.required_equipment = [{'equipment_id': 'EQ1', 'quantity_needed': 1}]
    A.zone_ids = ['ZONE_2']
    S, E = Activity('START', 0.0), Activity('END', 0.0)
    fwd = {S: [A], A: [E], E: []}

    p = Pert(graph=fwd)
    p.crew_pool = rp; p.equipment_pool = ep; p.location_pool = lp
    p.consumable_pool = None; p.system_state_pool = None
    p._precompute_availability_events()
    p.generateInfo()
    p.startTime = _START
    return p


print("EQ1 is zone-locked to ZONE_1; activity A declares ZONE_2 -> A may NOT use EQ1.\n")

p = make_pert()
p.calculateScheduleWithResources(sgs='max_use_res_ranked')
print("PARALLEL placed A? ", 'A' in [a.name for a in p.completed],
      "(correctly refused: zone-affinity check)")

p2 = make_pert()
p2.calculateSerialScheduleWithResources(priority_rule='lf')
placed = 'A' in [a.name for a in p2.completed]
print("SERIAL   placed A? ", placed, "(zone-affinity check now enforced in _serial_check_feasibility)")

r = p2.validate_schedule()
zone_viol = [v for v in r.violations if v.type == 'equipment_zone']
print("SERIAL validate_schedule.is_feasible =", r.is_feasible,
      "| equipment_zone violations =", len(zone_viol))
print()
# SC2 is FIXED: serial refuses the wrong-zone activity, matching the parallel path.
if not placed and not zone_viol:
    print("FIXED (SC2): serial refused A (wrong zone for zone-locked EQ1) — "
          "no equipment_zone violation, matching parallel.")
else:
    print("BUG: serial placed A in the wrong zone / produced a zone violation.")
