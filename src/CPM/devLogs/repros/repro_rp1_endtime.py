"""Repro: RP1 — duration override on an in-progress activity leaves endTime stale.

B and D share the sole WELDER (cap 1).  Baseline serializes B[0,10], D[10,16].
A replan at t=2 with duration_overrides={'B': 20} extends B's true span to
[0,20], but the override updated only duration/_remaining_duration and left
endTime at start+OLD 10h.  Everything that releases resources reads endTime, so
the welder frees at 10 and D is rescheduled into [10,16] — a 6h double-booking
of a 1-unit resource that the (also endTime-blind) validator cannot see.
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
        {'start_date': START, 'end_date': FAR, 'available_count': 1}])
    s = Activity('START', 0.0)
    b = Activity('B', 10.0, required_resources=[{'skill_type': 'WELDER', 'crew_count': 1}])
    d = Activity('D', 6.0, required_resources=[{'skill_type': 'WELDER', 'crew_count': 1}])
    e = Activity('END', 0.0)
    fwd = {s: [b, d], b: [e], d: [e], e: []}
    p = Pert(graph=fwd, priorities={'START': 0.9, 'B': 1.0, 'D': 0.2, 'END': 0.1})
    p.crew_pool = rp
    p.equipment_pool = EquipmentPool()
    p.location_pool = LocationPool()
    p.startTime = START
    p.generateInfo()
    return p, b, d


p, b, d = make_pert()
p.calculateScheduleWithResources(sgs='max_use_res_ranked')
print("baseline: B", b.returnAbsTimes(), " D", d.returnAbsTimes())

p.replan(current_time_hours=2.0, duration_overrides={'B': 20.0})
sb, eb = b.returnAbsTimes()
sd, ed = d.returnAbsTimes()
true_end = sb + timedelta(hours=b.duration)
print("after replan(t=2, B->20): B.duration =", b.duration)
print("  B.endTime attribute :", eb, " (physical end = start + duration =", true_end, ")")
print("  D scheduled          :", sd, "->", ed)

overlap = sd < true_end
endtime_stale = eb != true_end

print("\n=== VERDICT ===")
if endtime_stale or overlap:
    print("CONFIRMED RP1: B.endTime is stale (%s vs physical %s); D starts at %s, "
          "overlapping B's true span until %s -> double-booking of the sole WELDER."
          % (eb, true_end, sd, true_end))
else:
    print("NOT REPRODUCED (fixed): B.endTime = %s matches physical end; D starts at "
          "%s, at or after B's true release %s -> no double-booking." % (eb, sd, true_end))
