"""Repro: B2 — _splice_buffer_activity drops the finish-to-start lag.

A finish-to-start lag of 5 h sits on edge A->B (project EF = 0+10+5+10 = 25).
Splicing a zero-duration buffer between A and B removes the direct A->B edge and
wires A->BUF->B, but the old code never touched ``lag_dict``: the (A,B) entry was
orphaned (its edge gone, so the CPM passes — which read lag by the exact
(pred,succ) key — skip it) and B started 5 h too early.  Project EF collapsed
25 -> 20, silently losing the lag.

The fix moves the lag onto the matching buffer edge (pred->buffer for a feeding
splice), so EF stays 25 and no lag is lost.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
from CPM.pert import Pert
from CPM.activity import Activity

START = Activity(name='START', duration=0)
A     = Activity(name='A', duration=10)
B     = Activity(name='B', duration=10)
END   = Activity(name='END', duration=0)

graph = {START:[A], A:[B], B:[END], END:[]}
p = Pert(graph=graph)
# add a finish-to-start lag of 5h on edge A->B
p.lag_dict[(A,B)] = 5.0
p.generateInfo()

dur_before = p.infoDict[END]['ef']
print("project EF before splice (expect 0+10+5+10 = 25):", dur_before)

# Splice a ZERO-duration buffer between A and B (the edge that carries the lag).
buf = Activity(name='BUF', duration=0)
buf.buffer_type = 'feeding'
p._splice_buffer_activity(buf, predecessors=[A], successors=[B])

dur_after = p.infoDict[END]['ef']
delta = dur_before - dur_after
print("project EF after splice of 0-dur buffer (should stay 25):", dur_after)
print("lag_dict still has orphan (A,B)?", (A,B) in p.lag_dict, "->", p.lag_dict.get((A,B)))
print("lag on new edges A->BUF, BUF->B:", p.lag_dict.get((A,buf)), p.lag_dict.get((buf,B)))
print("DELTA (hours of lag silently lost):", delta)

print("\n=== VERDICT ===")
if delta == 0.0 and (A, B) not in p.lag_dict and p.lag_dict.get((A, buf)) == 5.0:
    print("NOT REPRODUCED (fixed): the 5 h lag moved onto A->BUF, the orphan "
          "(A,B) entry is gone, and project EF stays 25.")
else:
    print("CONFIRMED B2: %.1f h of lag silently lost across the splice "
          "(EF %s -> %s); orphan (A,B) present=%s."
          % (delta, dur_before, dur_after, (A, B) in p.lag_dict))
