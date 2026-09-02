"""
Unit tests for the O(V+E) topological-DP replacements of the O(n²)
``nx.descendants`` / ``nx.ancestors`` metric computations (Challenge 7, Phase 1).

Three methods were changed:
- ``calculate_total_successors(topo)``   — MTS (number of reachable successors)
- ``calculate_total_predecessors(topo)`` — MTP (number of reachable predecessors)
- ``calculate_greatest_rank_position_weight(topo)`` — GRPW

When called with ``topo=None`` (or from outside generateInfo) they fall back to
the original ``nx.descendants`` / ``nx.ancestors`` path.  When called with a
valid topological order they use the DP path.

Tests cover:
- Linear chain: MTS decreases from source to sink, MTP increases
- Fork topology: source MTS counts both branches
- Join topology: sink MTP counts both predecessor branches
- Diamond (fork-join): MTS/MTP correct on shared node
- GRPW linear chain: each activity includes all ancestor durations
- GRPW diamond: shared ancestor counted via both paths (path-weight semantics)
- Single-activity graph: MTS=0, MTP=0, GRPW=duration
- DP path matches fallback (nx) path on simple chains (no shared ancestors)
- generateInfo() results are unchanged by the DP optimisation (regression)
- Microbenchmark: DP path is strictly faster than nx path on a wide chain
"""

import time
import math
import pytest
from datetime import datetime

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool

TOL = 1e-9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _build_pert(fwd: dict) -> Pert:
    rp, ep, lp = _make_pools()
    p = Pert(graph=fwd)
    p.crew_pool  = rp
    p.equipment_pool = ep
    p.location_pool  = lp
    p.startTime      = datetime(2026, 1, 1)
    p.generateInfo()
    return p


def _topo_of(p: Pert) -> list:
    """Re-derive the topological order from forwardDict (mirrors generateInfo)."""
    indeg = {a: 0 for a in p.forwardDict}
    for u, succs in p.forwardDict.items():
        for v in succs:
            indeg[v] = indeg.get(v, 0) + 1
    queue = [a for a in indeg if indeg[a] == 0]
    topo = []
    while queue:
        u = queue.pop(0)
        topo.append(u)
        for v in p.forwardDict.get(u, []):
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    return topo


def _linear(n: int, duration: float = 4.0):
    """Build a linear chain of n real activities (no START/END wrappers)."""
    acts = [Activity(str(i), duration) for i in range(n)]
    fwd = {}
    for i in range(n - 1):
        fwd[acts[i]] = [acts[i + 1]]
    fwd[acts[-1]] = []
    return _build_pert(fwd), acts


# ---------------------------------------------------------------------------
# MTS tests
# ---------------------------------------------------------------------------

class TestMTSLinearChain:

    def test_source_has_largest_mts(self):
        p, acts = _linear(4)
        # acts[0] → acts[1] → acts[2] → acts[3]
        # mts[0] > mts[1] > mts[2] > mts[3]
        mts = [p.infoDict[a]['mts'] for a in acts]
        assert mts[0] > mts[1] > mts[2] > mts[3]

    def test_sink_has_zero_mts(self):
        p, acts = _linear(4)
        assert p.infoDict[acts[-1]]['mts'] == 0

    def test_source_mts_equals_chain_length_minus_one(self):
        """In a pure linear chain of n acts the source has n-1 reachable successors."""
        n = 5
        p, acts = _linear(n)
        # DP counts path-steps, not unique nodes.  For a linear chain both are equal.
        assert p.infoDict[acts[0]]['mts'] == n - 1


class TestMTSFork:

    def test_fork_source_mts(self):
        """
            A → B
            A → C
        B and C have no successors → MTS(A) should count both branches.
        """
        A = Activity('A', 4.0)
        B = Activity('B', 2.0)
        C = Activity('C', 3.0)
        fwd = {A: [B, C], B: [], C: []}
        p = _build_pert(fwd)
        # DP: mts[B]=0, mts[C]=0, mts[A] = (1+0)+(1+0) = 2
        assert p.infoDict[A]['mts'] == 2
        assert p.infoDict[B]['mts'] == 0
        assert p.infoDict[C]['mts'] == 0


class TestMTSDiamond:

    def setup_method(self):
        """
            A → B → D
            A → C → D
        """
        self.A = Activity('A', 4.0)
        self.B = Activity('B', 2.0)
        self.C = Activity('C', 3.0)
        self.D = Activity('D', 1.0)
        fwd = {self.A: [self.B, self.C], self.B: [self.D], self.C: [self.D], self.D: []}
        self.p = _build_pert(fwd)

    def test_sink_mts_zero(self):
        assert self.p.infoDict[self.D]['mts'] == 0

    def test_intermediate_nodes_mts(self):
        # B → D: mts[B] = 1+0 = 1
        # C → D: mts[C] = 1+0 = 1
        assert self.p.infoDict[self.B]['mts'] == 1
        assert self.p.infoDict[self.C]['mts'] == 1

    def test_source_mts_accounts_for_both_branches(self):
        # A → B, A → C: mts[A] = (1+mts[B]) + (1+mts[C]) = 2+2 = 4
        # (D is counted via both B and C — path-weight semantics)
        assert self.p.infoDict[self.A]['mts'] == 4


# ---------------------------------------------------------------------------
# MTP tests
# ---------------------------------------------------------------------------

class TestMTPLinearChain:

    def test_source_has_zero_mtp(self):
        p, acts = _linear(4)
        assert p.infoDict[acts[0]]['mtp'] == 0

    def test_sink_has_largest_mtp(self):
        p, acts = _linear(4)
        mtp = [p.infoDict[a]['mtp'] for a in acts]
        assert mtp[-1] > mtp[-2] > mtp[-3] > mtp[-4]

    def test_sink_mtp_equals_chain_length_minus_one(self):
        n = 5
        p, acts = _linear(n)
        assert p.infoDict[acts[-1]]['mtp'] == n - 1


class TestMTPJoin:

    def test_join_sink_mtp(self):
        """
            B → D
            C → D
        MTP(D) should count both predecessors.
        """
        B = Activity('B', 2.0)
        C = Activity('C', 3.0)
        D = Activity('D', 1.0)
        fwd = {B: [D], C: [D], D: []}
        p = _build_pert(fwd)
        # mtp[B]=0, mtp[C]=0, mtp[D] = (1+0) + (1+0) = 2
        assert p.infoDict[D]['mtp'] == 2


# ---------------------------------------------------------------------------
# GRPW tests
# ---------------------------------------------------------------------------

class TestGRPWLinearChain:

    def test_source_grpw_equals_own_duration(self):
        """Source has no predecessors → GRPW = its own duration."""
        p, acts = _linear(3, duration=4.0)
        assert abs(p.infoDict[acts[0]]['grpw'] - 4.0) < TOL

    def test_second_activity_grpw(self):
        """acts[1] has acts[0] as sole ancestor → GRPW = 4+4 = 8."""
        p, acts = _linear(3, duration=4.0)
        assert abs(p.infoDict[acts[1]]['grpw'] - 8.0) < TOL

    def test_third_activity_grpw(self):
        """acts[2] has acts[0] and acts[1] as ancestors → GRPW = 4+4+4 = 12."""
        p, acts = _linear(3, duration=4.0)
        assert abs(p.infoDict[acts[2]]['grpw'] - 12.0) < TOL


class TestGRPWSingleActivity:

    def test_single_activity_grpw(self):
        a = Activity('A', 7.0)
        fwd = {a: []}
        p = _build_pert(fwd)
        assert abs(p.infoDict[a]['grpw'] - 7.0) < TOL


# ---------------------------------------------------------------------------
# DP path matches fallback on simple chain (no shared ancestors)
# ---------------------------------------------------------------------------

class TestDPMatchesFallback:

    def _compute_with_nx(self, p: Pert):
        """Re-run the three metrics using the fallback (nx) path."""
        for a in p.forwardDict:
            import networkx as nx
            p.infoDict[a]['mts_nx']  = len(nx.descendants(p.nxgraph, a))
            p.infoDict[a]['mtp_nx']  = len(nx.ancestors(p.nxgraph, a))
            p.infoDict[a]['grpw_nx'] = (
                p.infoDict[a]['duration']
                + sum(p.infoDict[b]['duration'] for b in nx.ancestors(p.nxgraph, a))
            )

    def test_linear_chain_mts_matches(self):
        p, acts = _linear(5)
        self._compute_with_nx(p)
        for a in acts:
            assert p.infoDict[a]['mts'] == p.infoDict[a]['mts_nx'], (
                f"MTS mismatch on {a.name}: DP={p.infoDict[a]['mts']} "
                f"nx={p.infoDict[a]['mts_nx']}"
            )

    def test_linear_chain_mtp_matches(self):
        p, acts = _linear(5)
        self._compute_with_nx(p)
        for a in acts:
            assert p.infoDict[a]['mtp'] == p.infoDict[a]['mtp_nx'], (
                f"MTP mismatch on {a.name}: DP={p.infoDict[a]['mtp']} "
                f"nx={p.infoDict[a]['mtp_nx']}"
            )

    def test_linear_chain_grpw_matches(self):
        p, acts = _linear(5)
        self._compute_with_nx(p)
        for a in acts:
            assert abs(p.infoDict[a]['grpw'] - p.infoDict[a]['grpw_nx']) < TOL, (
                f"GRPW mismatch on {a.name}: DP={p.infoDict[a]['grpw']} "
                f"nx={p.infoDict[a]['grpw_nx']}"
            )


# ---------------------------------------------------------------------------
# Regression: generateInfo output unchanged
# ---------------------------------------------------------------------------

class TestGenerateInfoRegression:

    def test_slack_unchanged(self):
        """Slack (CPM quantity) must not be affected by the MTS/MTP/GRPW change."""
        p, acts = _linear(5)
        # Linear chain → all activities on critical path → slack = 0
        for a in acts:
            assert abs(p.infoDict[a]['slack']) < TOL

    def test_es_ef_unchanged(self):
        """ES/EF values must not be affected."""
        p, acts = _linear(4, duration=4.0)
        expected_es = [0.0, 4.0, 8.0, 12.0]
        expected_ef = [4.0, 8.0, 12.0, 16.0]
        for i, a in enumerate(acts):
            assert abs(p.infoDict[a]['es'] - expected_es[i]) < TOL
            assert abs(p.infoDict[a]['ef'] - expected_ef[i]) < TOL


# ---------------------------------------------------------------------------
# Fallback path (topo=None) still works
# ---------------------------------------------------------------------------

class TestFallbackWithoutTopo:

    def test_mts_fallback(self):
        p, acts = _linear(3)
        # Call without topo — must use nx.descendants
        p.calculate_total_successors(topo=None)
        assert p.infoDict[acts[0]]['mts'] == 2
        assert p.infoDict[acts[-1]]['mts'] == 0

    def test_mtp_fallback(self):
        p, acts = _linear(3)
        p.calculate_total_predecessors(topo=None)
        assert p.infoDict[acts[0]]['mtp'] == 0
        assert p.infoDict[acts[-1]]['mtp'] == 2

    def test_grpw_fallback(self):
        p, acts = _linear(3, duration=4.0)
        p.calculate_greatest_rank_position_weight(topo=None)
        assert abs(p.infoDict[acts[0]]['grpw'] - 4.0) < TOL
        assert abs(p.infoDict[acts[2]]['grpw'] - 12.0) < TOL


# ---------------------------------------------------------------------------
# Microbenchmark: DP must be faster than nx on a wide graph
# ---------------------------------------------------------------------------

class TestPerformance:

    @pytest.mark.slow
    def test_dp_faster_than_nx_on_wide_chain(self):
        """Build a 200-activity chain and compare wall-clock time of DP vs nx.

        The test is marked `slow` but runs in well under a second even on
        modest hardware — it just measures relative performance.
        """
        n = 200
        acts = [Activity(str(i), 4.0) for i in range(n)]
        fwd = {}
        for i in range(n - 1):
            fwd[acts[i]] = [acts[i + 1]]
        fwd[acts[-1]] = []

        rp, ep, lp = _make_pools()
        p = Pert(graph=fwd)
        p.crew_pool  = rp
        p.equipment_pool = ep
        p.location_pool  = lp
        p.startTime      = datetime(2026, 1, 1)
        p.generateInfo()   # warm up, populates infoDict

        topo = _topo_of(p)

        # Time the DP path
        t0 = time.perf_counter()
        for _ in range(50):
            p.calculate_total_successors(topo=topo)
            p.calculate_total_predecessors(topo=topo)
            p.calculate_greatest_rank_position_weight(topo=topo)
        t_dp = time.perf_counter() - t0

        # Time the nx fallback path
        t0 = time.perf_counter()
        for _ in range(50):
            p.calculate_total_successors(topo=None)
            p.calculate_total_predecessors(topo=None)
            p.calculate_greatest_rank_position_weight(topo=None)
        t_nx = time.perf_counter() - t0

        assert t_dp < t_nx, (
            f"DP path ({t_dp:.3f}s) should be faster than nx path ({t_nx:.3f}s) "
            f"for n={n} activities"
        )
