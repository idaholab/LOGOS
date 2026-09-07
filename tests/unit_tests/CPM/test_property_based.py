"""
Property-based (Hypothesis) tests for the RCPSP engine.

Generates random *valid* activity-on-node DAGs and asserts the invariants
already codified in test_invariants.py, over inputs no hand-written fixture
would cover.  Hypothesis shrinks any failure to a minimal counterexample; when
it finds one, freeze the shrunk instance into test_bugfix_regressions.py.

Phases (see src/CPM/devLogs/RCPSP_ROBUSTNESS_2026-09-07.md):
  0. smoke      — the generator + scheduler run without blowing up
  1a. equality  — unlimited resources => makespan == CPM (and schedule feasible)
  1b. metamorphic — scaling every duration by k scales CPM by k

Phase 2 (resource monotonicity) is intentionally NOT here yet: wiring per-activity
crew demand must follow the API used by test_invariants.py / test_replan_resources.py,
not be guessed.  See the dev log.

The module self-skips where Hypothesis is not installed (mirrors the
ravenframework guard in test_raven_interface.py), so the suite still collects
cleanly in environments without it.  Install with: pip install hypothesis
"""
import math
from datetime import datetime

import pytest

hypothesis = pytest.importorskip("hypothesis")  # skip cleanly if not installed
from hypothesis import given, settings, strategies as st, HealthCheck

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool

TOL = 1e-6
SGS = "max_use_res_ranked"

# Every schedule-generation scheme must satisfy the unlimited-resource
# invariant makespan == CPM.  Pinning only the default let the `first`-strategy
# makespan-inflation bug (dev log §7) hide; parametrize the equality property
# across all five so a regression on any one strategy is caught.
ALL_SGS = [
    "first",
    "max_use_res_ranked",
    "max_use_res_shuffled",
    "md_knapsack",
    "look_ahead",
]


# ---------------------------------------------------------------------------
# Instance generator
# ---------------------------------------------------------------------------

@st.composite
def rcpsp_dag(draw, max_activities=90, max_duration=20.0):
    """A random acyclic instance: n activities, durations, forward edges.

    Acyclic by construction — an edge i->j is only ever proposed for i < j, so
    the topological order is the index order and no cycle can form.  Zero
    durations and isolated nodes are allowed on purpose: those are exactly the
    degenerate cases that fed bug-family #3 (multi-source, zero-duration,
    disconnected) in the 2026-09-03 review.
    """
    n = draw(st.integers(min_value=1, max_value=max_activities))
    durations = draw(st.lists(
        st.floats(min_value=0.0, max_value=max_duration,
                  allow_nan=False, allow_infinity=False),
        min_size=n, max_size=n))
    edges = set()
    for j in range(n):
        for i in range(j):
            if draw(st.booleans()):
                edges.add((i, j))
    return n, durations, sorted(edges)


def build_pert(n, durations, edges):
    """Wrap a generated instance in START/END and build a schedulable Pert.

    Every generated activity is anchored to START (if it has no predecessor)
    and to END (if it has no successor), so the graph always has a single
    source and single sink and is fully connected.  Empty resource pools mean
    unlimited capacity, so the resource-constrained makespan must equal the CPM
    length for these instances.
    """
    acts = [Activity(f"A{i}", float(durations[i])) for i in range(n)]
    start, end = Activity("START", 0.0), Activity("END", 0.0)

    succ = {a: [] for a in acts}
    for i, j in edges:
        succ[acts[i]].append(acts[j])

    has_in = {j for _, j in edges}
    has_out = {i for i, _ in edges}
    sources = [acts[i] for i in range(n) if i not in has_in]   # -> START
    sinks = [acts[i] for i in range(n) if i not in has_out]    # -> END

    fwd = {start: list(sources)}
    fwd.update(succ)
    for s in sinks:
        fwd[s].append(end)
    fwd[end] = []

    p = Pert(graph=fwd)
    p.crew_pool, p.equipment_pool, p.location_pool = (
        ResourcePool(), EquipmentPool(), LocationPool())       # empty = unlimited
    p.startTime = datetime(2026, 1, 1)
    p.generateInfo()
    return p


# ---------------------------------------------------------------------------
# Phase 0 — the generator + scheduler don't blow up
# ---------------------------------------------------------------------------

@settings(max_examples=500, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(rcpsp_dag())
def test_generator_smoke(inst):
    p = build_pert(*inst)
    r = p.calculateScheduleWithResources(sgs=SGS)
    assert r["scheduled_duration"] >= 0.0


# ---------------------------------------------------------------------------
# Phase 1a — unlimited resources => makespan == CPM, and schedule is feasible
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sgs", ALL_SGS)
@settings(max_examples=2000, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(rcpsp_dag())
def test_unconstrained_makespan_equals_cpm(sgs, inst):
    p = build_pert(*inst)
    r = p.calculateScheduleWithResources(sgs=sgs)
    assert_valid_schedule(p)
    assert abs(r["scheduled_duration"] - r["cpm_duration"]) < TOL, (
        f"[{sgs}] makespan {r['scheduled_duration']:.4f} != "
        f"CPM {r['cpm_duration']:.4f}")


# ---------------------------------------------------------------------------
# Phase 1b — metamorphic: scaling every duration by k scales CPM by k
# ---------------------------------------------------------------------------

@settings(max_examples=1000, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(rcpsp_dag(), st.floats(min_value=1.1, max_value=5.0,
                              allow_nan=False, allow_infinity=False))
def test_duration_scaling_scales_cpm(inst, k):
    n, durations, edges = inst
    base = build_pert(n, durations, edges).calculateScheduleWithResources(sgs=SGS)
    scaled = build_pert(n, [d * k for d in durations], edges) \
        .calculateScheduleWithResources(sgs=SGS)
    assert math.isclose(scaled["cpm_duration"], base["cpm_duration"] * k,
                        rel_tol=1e-6, abs_tol=1e-6), (
        f"CPM {base['cpm_duration']:.4f} * {k:.4f} != scaled CPM "
        f"{scaled['cpm_duration']:.4f}")
