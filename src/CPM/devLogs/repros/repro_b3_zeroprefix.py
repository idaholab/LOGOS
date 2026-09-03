"""Repro: B3 — _longest_path_in_augmented drops leading zero-duration nodes.

Chain: START(0) -> M(0) -> A(5) -> END(0).  The old DP used a 0-init dist and a
strict '>' relaxation, so the START(0) -> M(0) edge produced cand = 0, which is
NOT > the already-0 dist[M]; parent[M] was never set and the reconstruction
walked back only as far as M, dropping START.  Any zero-cumulative-gain prefix
(a START milestone followed by a zero-duration node) was silently truncated from
the critical chain.

The fix seeds sources with their own duration and every other node with -inf, so
the first predecessor to relax a node ALWAYS sets a parent — leading zero-
duration nodes are retained.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from CPM.pert import Pert
from CPM.activity import Activity

START = Activity(name='START', duration=0)
M     = Activity(name='M', duration=0)   # zero-duration node right after START
A     = Activity(name='A', duration=5)
END   = Activity(name='END', duration=0)

graph = {START:[M], M:[A], A:[END], END:[]}
p = Pert(graph=graph)

aug = p._build_augmented_graph()
chain = p._longest_path_in_augmented(aug)
names = [a.name for a in chain]
print("returned chain:", names)
print("expected full chain: ['START', 'M', 'A', 'END']")

print("\n=== VERDICT ===")
if names == ['START', 'M', 'A', 'END']:
    print("NOT REPRODUCED (fixed): the zero-duration prefix START->M is retained "
          "in the reconstructed chain.")
else:
    print("CONFIRMED B3: returned %s — a leading zero-duration node was dropped "
          "from the critical chain." % (names,))
