"""
Property-based (Hypothesis) tests for the RCPSP engine.

Generates random *valid* activity-on-node DAGs and asserts the invariants
already codified in test_invariants.py, over inputs no hand-written fixture
would cover.  Hypothesis shrinks any failure to a minimal counterexample; when
it finds one, freeze the shrunk instance into test_bugfix_regressions.py.

Phases (see src/CPM/devLogs/RCPSP_ROBUSTNESS_2026-09-07.md):
  0.  smoke       — the generator + scheduler run without blowing up
  1a. equality    — unlimited resources => makespan == CPM (and schedule feasible),
                    including finish-to-start lags on a random subset of edges
  1b. metamorphic — scaling every duration (and lag) by k scales CPM by k
  2.  resources   — a renewable crew skill: makespan >= CPM, schedule feasible,
                    and more capacity never lengthens the schedule (monotonicity)

Run profiles (10.4):
  The suite registers two Hypothesis profiles and loads one from the
  HYPOTHESIS_PROFILE env var (default "ci"):
    - "ci":       150 examples, derandomized so a CI failure reproduces exactly
    - "thorough": 2000 examples, for deep local exploration
  Deep run:  HYPOTHESIS_PROFILE=thorough pytest tests/unit_tests/CPM/test_property_based.py

  CI always runs "cold": the .hypothesis example database is not committed, so
  every CI run explores fresh examples from scratch (a warm local DB is much
  faster and is NOT representative of CI cost).  The "ci" example count and the
  generator's default max_activities (30) are together sized so the whole CPM
  suite finishes cold in ~15 s locally, well inside the RAVEN test max_time.
  Bumping either knob re-times the cold run before it lands in CI.

The module self-skips where Hypothesis is not installed (mirrors the
ravenframework guard in test_raven_interface.py), so the suite still collects
cleanly in environments without it.  Install with: pip install hypothesis
"""
import os
import math
from datetime import datetime

import pytest

hypothesis = pytest.importorskip("hypothesis")  # skip cleanly if not installed
from hypothesis import given, settings, strategies as st, HealthCheck

from conftest import assert_valid_schedule, make_crew_pool
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
# Hypothesis profiles (10.4)
# ---------------------------------------------------------------------------
# CI runs the deterministic "ci" profile so a failing example is reproducible
# from the recorded seed; local deep runs select "thorough" via
# HYPOTHESIS_PROFILE=thorough.  Registering here (rather than per @settings)
# keeps example counts in one place and lets one env var scale the whole suite.

settings.register_profile(
    "ci",
    max_examples=150,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.register_profile(
    "thorough",
    max_examples=2000,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


# ---------------------------------------------------------------------------
# Instance generator
# ---------------------------------------------------------------------------

@st.composite
def rcpsp_dag(draw, max_activities=30, max_duration=20.0, max_lag=10.0,
              with_lags=True):
    """A random acyclic instance: n activities, durations, forward edges, lags.

    Acyclic by construction — an edge i->j is only ever proposed for i < j, so
    the topological order is the index order and no cycle can form.  Zero
    durations and isolated nodes are allowed on purpose: those are exactly the
    degenerate cases that fed bug-family #3 (multi-source, zero-duration,
    disconnected) in the 2026-09-03 review.

    When ``with_lags`` is set, a finish-to-start lag in [0, max_lag] is drawn for
    a random subset of edges.  Lags participate in the CPM forward/backward pass
    (pert.py:750/775) and in the scheduler's earliest-start (pert.py:6962), so
    the equality invariant below now exercises lag arithmetic on both paths.
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
    edges = sorted(edges)
    lags = {}
    if with_lags:
        for e in edges:
            if draw(st.booleans()):
                lags[e] = draw(st.floats(min_value=0.0, max_value=max_lag,
                                         allow_nan=False, allow_infinity=False))
    return n, durations, edges, lags


def build_pert(n, durations, edges, lags=None):
    """Wrap a generated instance in START/END and build a schedulable Pert.

    Every generated activity is anchored to START (if it has no predecessor)
    and to END (if it has no successor), so the graph always has a single
    source and single sink and is fully connected.  Empty resource pools mean
    unlimited capacity, so the resource-constrained makespan must equal the CPM
    length for these instances.  Lags are written straight into ``lag_dict``
    (the representation the engine schedules against — see pert.py:750); this
    mirrors test_invariants._build and is not overwritten by generateInfo() on
    the graph= construction path.
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
    if lags:
        p.lag_dict = {(acts[i], acts[j]): float(L) for (i, j), L in lags.items()}
    p.startTime = datetime(2026, 1, 1)
    p.generateInfo()
    return p


# ---------------------------------------------------------------------------
# Phase 0 — the generator + scheduler don't blow up
# ---------------------------------------------------------------------------

@given(rcpsp_dag())
def test_generator_smoke(inst):
    p = build_pert(*inst)
    r = p.calculateScheduleWithResources(sgs=SGS)
    assert r["scheduled_duration"] >= 0.0


# ---------------------------------------------------------------------------
# Phase 1a — unlimited resources => makespan == CPM, and schedule is feasible
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sgs", ALL_SGS)
@given(rcpsp_dag())
def test_unconstrained_makespan_equals_cpm(sgs, inst):
    p = build_pert(*inst)
    r = p.calculateScheduleWithResources(sgs=sgs)
    assert_valid_schedule(p)
    assert abs(r["scheduled_duration"] - r["cpm_duration"]) < TOL, (
        f"[{sgs}] makespan {r['scheduled_duration']:.4f} != "
        f"CPM {r['cpm_duration']:.4f}")


# ---------------------------------------------------------------------------
# Phase 1b — metamorphic: scaling every duration (and lag) by k scales CPM by k
# ---------------------------------------------------------------------------

@given(rcpsp_dag(), st.floats(min_value=1.1, max_value=5.0,
                              allow_nan=False, allow_infinity=False))
def test_duration_scaling_scales_cpm(inst, k):
    n, durations, edges, lags = inst
    # Lags are on the critical path too, so scaling durations alone would break
    # the k-relation; scale both to keep CPM homogeneous of degree 1.
    scaled_lags = {e: L * k for e, L in lags.items()}
    base = build_pert(n, durations, edges, lags) \
        .calculateScheduleWithResources(sgs=SGS)
    scaled = build_pert(n, [d * k for d in durations], edges, scaled_lags) \
        .calculateScheduleWithResources(sgs=SGS)
    assert math.isclose(scaled["cpm_duration"], base["cpm_duration"] * k,
                        rel_tol=1e-6, abs_tol=1e-6), (
        f"CPM {base['cpm_duration']:.4f} * {k:.4f} != scaled CPM "
        f"{scaled['cpm_duration']:.4f}")


# ---------------------------------------------------------------------------
# Phase 2 — resource-constrained invariants (single renewable crew skill)
# ---------------------------------------------------------------------------
# Capacity is always >= the largest single-activity demand, so every activity
# can eventually run (renewable => no cumulative exhaustion) and the instance is
# guaranteed feasible.  The properties then hold unconditionally:
#   - resources can only delay:  makespan >= CPM
#   - the schedule validates and every activity is scheduled
#   - monotonicity: more crew never lengthens the schedule
# Lags are omitted here so a failure points unambiguously at the resource logic
# rather than at lag arithmetic (Phase 1 already covers lags).

CREW = "CREW"


@st.composite
def rcpsp_crew_instance(draw, max_activities=30, max_demand=3):
    """A DAG plus a per-activity crew demand and two capacities cap_low<=cap_high.

    ``floor`` is the largest single-activity demand (>=1), so both capacities
    admit every activity and the schedule is always feasible.
    """
    n, durations, edges, _ = draw(
        rcpsp_dag(max_activities=max_activities, with_lags=False))
    demands = draw(st.lists(st.integers(min_value=0, max_value=max_demand),
                            min_size=n, max_size=n))
    floor = max(demands + [1])
    cap_low = floor + draw(st.integers(min_value=0, max_value=3))
    cap_high = cap_low + draw(st.integers(min_value=0, max_value=3))
    return n, durations, edges, demands, cap_low, cap_high


def build_resource_pert(n, durations, edges, demands, capacity):
    """Like build_pert but with one renewable CREW skill of the given capacity."""
    acts = [Activity(f"A{i}", float(durations[i])) for i in range(n)]
    for i in range(n):
        if demands[i] > 0:
            acts[i].required_resources = [
                {'skill_type': CREW, 'crew_count': int(demands[i])}]
    start, end = Activity("START", 0.0), Activity("END", 0.0)

    succ = {a: [] for a in acts}
    for i, j in edges:
        succ[acts[i]].append(acts[j])

    has_in = {j for _, j in edges}
    has_out = {i for i, _ in edges}
    sources = [acts[i] for i in range(n) if i not in has_in]
    sinks = [acts[i] for i in range(n) if i not in has_out]

    fwd = {start: list(sources)}
    fwd.update(succ)
    for s in sinks:
        fwd[s].append(end)
    fwd[end] = []

    T0 = datetime(2026, 1, 1)
    p = Pert(graph=fwd)
    p.crew_pool = make_crew_pool(CREW, int(capacity), T0)
    p.equipment_pool = EquipmentPool()
    p.location_pool = LocationPool()
    p.startTime = T0
    p.generateInfo()
    return p


@pytest.mark.parametrize("sgs", ALL_SGS)
@given(rcpsp_crew_instance())
def test_resource_makespan_at_least_cpm(sgs, inst):
    """Resources can only delay: makespan >= CPM, schedule feasible, all scheduled."""
    n, durations, edges, demands, cap_low, _cap_high = inst
    p = build_resource_pert(n, durations, edges, demands, cap_low)
    r = p.calculateScheduleWithResources(sgs=sgs)
    assert_valid_schedule(p, f"[{sgs}] resource-constrained (cap={cap_low})")
    assert r["n_completed"] == r["n_activities"], (
        f"[{sgs}] only {r['n_completed']}/{r['n_activities']} activities scheduled")
    assert r["scheduled_duration"] >= r["cpm_duration"] - TOL, (
        f"[{sgs}] makespan {r['scheduled_duration']:.4f} < "
        f"CPM {r['cpm_duration']:.4f}")


@pytest.mark.parametrize("sgs", ALL_SGS)
@given(rcpsp_crew_instance())
def test_resource_capacity_monotonic(sgs, inst):
    """More crew never lengthens the schedule: makespan(cap_low) >= makespan(cap_high)."""
    n, durations, edges, demands, cap_low, cap_high = inst
    tight = build_resource_pert(n, durations, edges, demands, cap_low)
    loose = build_resource_pert(n, durations, edges, demands, cap_high)
    r_tight = tight.calculateScheduleWithResources(sgs=sgs)
    r_loose = loose.calculateScheduleWithResources(sgs=sgs)
    assert_valid_schedule(tight, f"[{sgs}] tight cap={cap_low}")
    assert_valid_schedule(loose, f"[{sgs}] loose cap={cap_high}")
    assert r_tight["scheduled_duration"] >= r_loose["scheduled_duration"] - TOL, (
        f"[{sgs}] makespan(cap={cap_low})={r_tight['scheduled_duration']:.4f} < "
        f"makespan(cap={cap_high})={r_loose['scheduled_duration']:.4f}")
