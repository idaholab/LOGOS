"""Repro B5: `_build_augmented_graph`'s location-binding block scans only
*consecutive* start-sorted pairs at a `max_tasks == 1` zone, so a non-adjacent
overlapping pair gets no serialization arc and is not recovered transitively.

The augmented graph feeds `_compute_actual_tf_proxy` — the post-schedule
constrained critical-chain / total-float analytics (NOT feasibility).  A missing
location arc under-connects that graph, so the reported constrained chain / TF
can be wrong.

Setup (all three share ZONE1, max_tasks == 1; A/B/C parallel in precedence):
    A = [0h, 10h]   (long)
    B = [1h,  2h]   (short — starts inside A, ends before C starts)
    C = [3h, 13h]

Start-sorted order: A, B, C.
  consecutive (A,B): overlap (0<2 and 1<10) -> arc A->B added
  consecutive (B,C): overlap? 3 < 2 is false -> NO arc B->C
  non-adjacent (A,C): overlap (0<13 and 3<10) -> SHOULD serialize, but the
                      consecutive-only scan never inspects this pair, and there
                      is no B->C arc to carry it transitively.

So A and C both occupy a max_tasks==1 zone during [3h,10h] with NO ordering arc
between them in the augmented graph.

Question: after `_build_augmented_graph`, is C reachable from A (the arc the
block intends to add for every overlapping pair at a max_tasks==1 zone)?

NOTE ON REACHABILITY: the block only adds arcs when two tasks *overlap* at a
max_tasks==1 zone.  A correct SGS never schedules such an overlap, so this
pattern is not reachable through the public scheduling API with a correct
scheduler — the repro sets activity times directly to exercise the function in
isolation.  The defect is a genuine incompleteness of `_build_augmented_graph`
relative to its own docstring ("Location ordering when max_tasks == 1 AND two
tasks overlap"), latent behind a correct scheduler.
"""
import sys, os, logging
from datetime import datetime, timedelta
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    ResourcePool, EquipmentPool, LocationPool, LocationAvailability,
)

START = datetime(2026, 1, 1, 0, 0)
FAR = START + timedelta(days=365)


def loc_pool_single_task():
    lp = LocationPool()
    lp.locations['ZONE1'] = LocationAvailability(
        'ZONE1', 'single-task zone',
        [{'start_date': START, 'end_date': FAR, 'max_concurrent_tasks': 1}])
    return lp


def build():
    s = Activity('START', 0.0)
    a = Activity('A', 10.0)
    b = Activity('B', 1.0)
    c = Activity('C', 10.0)
    e = Activity('END', 0.0)
    # A, B, C are parallel in precedence, so the ONLY arcs among them come from
    # the location-binding block.
    fwd = {s: [a, b, c], a: [e], b: [e], c: [e], e: []}
    p = Pert(graph=fwd)
    p.startTime = START
    p.crew_pool, p.equipment_pool, p.location_pool = (
        ResourcePool(), EquipmentPool(), loc_pool_single_task())

    def place(act, h0, h1):
        act.zone_ids = ['ZONE1']
        act.startTime = START + timedelta(hours=h0)
        act.endTime = START + timedelta(hours=h1)

    place(a, 0, 10)
    place(b, 1, 2)
    place(c, 3, 13)
    return p, a, b, c


def reachable(augmented, src, dst):
    """DFS: is dst reachable from src in the augmented adjacency map?"""
    seen, stack = set(), [src]
    while stack:
        u = stack.pop()
        for v in augmented.get(u, []):
            if v is dst:
                return True
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


p, a, b, c = build()
augmented = p._build_augmented_graph()

a_to_b = b in augmented.get(a, [])
a_to_c_direct = c in augmented.get(a, [])
a_to_c_any = reachable(augmented, a, c)

print("=== ZONE1 max_tasks==1; A[0,10] B[1,2] C[3,13] (all overlap-checked) ===")
print(f"A overlaps B: True   A overlaps C: True   B overlaps C: False")
print(f"arc A->B present        : {a_to_b}")
print(f"arc A->C present (direct): {a_to_c_direct}")
print(f"C reachable from A (any) : {a_to_c_any}")

print("\n=== VERDICT ===")
if a_to_b and not a_to_c_any:
    print("REPRODUCED: A and C overlap at a max_tasks==1 zone but no serialization "
          "arc (direct or transitive) exists — the consecutive-only scan skipped "
          "the non-adjacent (A,C) pair (B5). Latent behind a correct scheduler.")
else:
    print("NOT REPRODUCED (fixed): every overlapping pair at a max_tasks==1 zone "
          "is serialized (A->C reachable).")
