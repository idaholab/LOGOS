"""Repro: B1 (+B3) — _longest_path_in_augmented returns the wrong critical chain.

Two SOURCES (P, Q) feed a common sink C, with NO unifying START milestone.
The old DP seeded only ``self.startActivity`` (None here) / ``topo[0]`` with its
own duration and left every *other* source at 0, so the longest path that
originates at the non-topo[0] source P lost P's entire 100 h duration.  Insertion
order Q,P,C makes Q become topo[0], so the buggy code seeds Q(1) and reports the
Q→C chain (length 2) instead of the true P→C chain (length 101).

The fix seeds *every* in-degree-0 source with its own duration (-inf elsewhere),
so each source contributes its full length and the true longest path wins.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from CPM.pert import Pert
from CPM.activity import Activity

# Two SOURCES feeding a common sink; NO node named START/END.
P = Activity(name='P', duration=100)   # long source
Q = Activity(name='Q', duration=1)     # short source (inserted first -> becomes topo[0])
C = Activity(name='C', duration=1)

# Insertion order Q,P,C so the Kahn queue yields Q as topo[0]
graph = {Q:[C], P:[C], C:[]}
p = Pert(graph=graph)
print("startActivity:", p.startActivity, "endActivity:", p.endActivity)

aug = p._build_augmented_graph()
chain = p._longest_path_in_augmented(aug)
names = [a.name for a in chain]
length = sum(p._effective_duration(a) for a in chain)
print("returned chain:", names)
print("chain length (sum eff durations):", length)
print("TRUE longest path is P->C with length 101")

print("\n=== VERDICT ===")
if names == ['P', 'C'] and length == 101:
    print("NOT REPRODUCED (fixed): the DP seeds every source with its own "
          "duration, so P->C (101 h) wins over Q->C (2 h).")
else:
    print("CONFIRMED B1: returned %s (length %s) instead of the true longest "
          "path P->C (101 h) — a non-topo[0] source lost its duration."
          % (names, length))
