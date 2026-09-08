"""Repro: time-varying resource availability drop MID-activity vs the sparse grid."""
import sys, logging
from datetime import datetime, timedelta
logging.disable(logging.CRITICAL)

import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, ResourceAvailability, EquipmentPool, LocationPool
from CPM.schedule_validator import validate_schedule

START = datetime(2026, 1, 1, 0, 0)

def make_pool():
    rp = ResourcePool()
    rp.resources['MECH'] = ResourceAvailability('MECH', [
        {'start_date': START,                      'end_date': START + timedelta(hours=4),  'available_count': 4},
        {'start_date': START + timedelta(hours=4), 'end_date': START + timedelta(days=365), 'available_count': 2},
    ])
    return rp

start = Activity('START', 0.0)
a = Activity('A', 6.0, required_resources=[{'skill_type': 'MECH', 'crew_count': 4}])
end = Activity('END', 0.0)
fwd = {start: [a], a: [end], end: []}

p = Pert(graph=fwd)
p.crew_pool = make_pool()
p.equipment_pool = EquipmentPool()
p.location_pool = LocationPool()
p.startTime = START
p.generateInfo()

result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')

print(f"n_completed={result['n_completed']} / {result['n_activities']}  scheduled_duration={result['scheduled_duration']}")
print(f"A.status={a.status!r}  in completed={a in p.completed}  in ongoing={a in p.ongoing}  in wait={a in p.wait}")
print(f"A abs times={a.returnAbsTimes()}   A._actual_resources={getattr(a,'_actual_resources',None)}")
print("schedule_log:")
for row in p.schedule_log:
    print("  ", row['time'].strftime('%H:%M'), "selected=", row['selected'], "candidates=", row['candidates'])

# Proper over-commit audit: replay COMPLETED activities.
print("\nHour-by-hour over-commit audit (replay completed+ongoing):")
overbooked = False
active = list(p.completed) + list(p.ongoing)
for h_off in range(7):
    h = START + timedelta(hours=h_off)
    consumed = 0
    for act in active:
        s, e = act.returnAbsTimes()
        if s and e and s <= h < e:
            actual = getattr(act, '_actual_resources', None)
            consumed += (actual or {}).get('MECH', 0) if actual else sum(
                r['crew_count'] for r in act.getRequiredResources() if r['skill_type']=='MECH')
    avail = p.crew_pool.get_availability('MECH', h)
    flag = '  <-- OVERBOOK' if consumed > avail else ''
    if consumed > avail: overbooked = True
    print(f"  h+{h_off}: consumed={consumed} available={avail}{flag}")

vr = validate_schedule(p)
crew_viol = [v for v in vr.violations if v.type == 'crew']
print(f"\nvalidator: is_feasible={vr.is_feasible} violations={len(vr.violations)} crew={len(crew_viol)}")
for v in vr.violations:
    print("   ", v)

print("\n=== VERDICT ===")
# This repro exercises TWO entangled bugs at once:
#   C2  — scheduler over-commits A (consumed 4 > available 2 at h+4..5) because
#         the sparse capacity grid omits the pool availability breakpoint at h=4.
#   C2b — the validator reports crew=0 violations despite that over-commit,
#         because it samples availability only at demand-start events.
#   C3  — (formerly) END (zero-crew sink) was reported "never scheduled" and
#         blamed on the max_use_res_ranked early-break.  C3 is now FIXED (see
#         repro_earlybreak.py for the isolated, unambiguous C3 evidence).  Here,
#         once C2 is fixed A is *correctly* refused (no 6h window with 4 MECH),
#         so END — whose sole predecessor is A — is correctly blocked by
#         PRECEDENCE, not starved.  The clause below distinguishes the two.
if overbooked and not crew_viol:
    print("CONFIRMED C2 + C2b: A is over-committed (see OVERBOOK rows above) yet the "
          "validator reports crew=0 violations — sparse grid AND validator both miss "
          "the mid-activity availability drop.")
elif overbooked and crew_viol:
    print("C2 over-commit present AND validator flagged it (unexpected — validator blind "
          "spot may have been fixed).")
else:
    print("NOT REPRODUCED (no over-commit detected).")
if result['n_completed'] != result['n_activities']:
    end_ready = all(pred in p.completed for pred in p.backwardDict.get(end, []))
    if end in p.completed:
        pass
    elif not end_ready:
        print("EXPECTED (C3 fixed): END unscheduled only because its predecessor A is "
              "genuinely infeasible — a correct PRECEDENCE block, not early-break "
              "starvation (n_completed={}/{}).".format(
                  result['n_completed'], result['n_activities']))
    else:
        print("UNEXPECTED: END is ready (preds complete) yet unscheduled — possible "
              "starvation regression (n_completed={}/{}).".format(
                  result['n_completed'], result['n_activities']))
