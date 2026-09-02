"""
Pass 3 — Invariant properties.

Mathematical properties that must hold for **any** valid schedule.
Parameterised over multiple fixtures so violations are easy to localise.

Invariants tested:
  1. makespan >= CPM_duration           (scheduler never beats physics)
  2. Unconstrained → makespan == CPM_duration  (no spurious waits)
  3. Tighter resource → makespan_tight >= makespan_loose  (monotonicity)
  4. replan(t=0) ≡ full schedule        (replan at hour 0 matches fresh run)
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    ResourcePool, EquipmentPool, LocationPool,
    ResourceAvailability,
)

DATA_DIR = Path(__file__).parent.parent
SCHEMA   = str(DATA_DIR / "outage_schema.json")

TOL          = 1e-6   # hours — absolute tolerance for float comparisons
TOL_SECONDS  = 2      # seconds — tolerance for datetime comparisons


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _build(fwd, lag_dict=None, start_time=None, pools=None):
    p = Pert(graph=fwd)
    rp, ep, lp = pools or _pools()
    p.crew_pool      = rp
    p.equipment_pool = ep
    p.location_pool  = lp
    if lag_dict:
        p.lag_dict = lag_dict
    p.startTime = start_time or datetime(2026, 1, 1)
    p.generateInfo()
    return p


def _snapshot(pert):
    """Return {name: (startTime, endTime)} for all completed activities."""
    return {act.name: act.returnAbsTimes() for act in pert.completed}


def _make_crew_pool(skill: str, count: int, start_time: datetime) -> ResourcePool:
    """ResourcePool with one renewable skill available from start_time for a year."""
    rp = ResourcePool()
    rp.resources[skill] = ResourceAvailability(
        skill,
        [{
            'start_date':     start_time,
            'end_date':       start_time + timedelta(days=365),
            'available_count': count,
        }],
        resource_type='renewable',
    )
    return rp


# ---------------------------------------------------------------------------
# Parametrize helpers: build the micro-network fixtures inline
# ---------------------------------------------------------------------------

def _chain_pert():
    """START→A(4)→B(3)→C(2)→END (no resource contention)."""
    s = Activity("START", 0.0)
    a = Activity("A",     4.0)
    b = Activity("B",     3.0)
    c = Activity("C",     2.0)
    e = Activity("END",   0.0)
    return _build({s: [a], a: [b], b: [c], c: [e], e: []})


def _fork_join_pert():
    """START→A(4)→C(2)→END; START→B(6)→C."""
    s = Activity("START", 0.0)
    a = Activity("A",     4.0)
    b = Activity("B",     6.0)
    c = Activity("C",     2.0)
    e = Activity("END",   0.0)
    return _build({s: [a, b], a: [c], b: [c], c: [e], e: []})


def _lag_pert():
    """START→A(4)--[lag=2]-->B(3)→END."""
    s = Activity("START", 0.0)
    a = Activity("A",     4.0)
    b = Activity("B",     3.0)
    e = Activity("END",   0.0)
    p = _build({s: [a], a: [b], b: [e], e: []}, lag_dict={(a, b): 2.0})
    return p


def _window_pert():
    """START→A(4)→B(3)→END; B has window_earliest=8h."""
    s = Activity("START", 0.0)
    a = Activity("A",     4.0)
    b = Activity("B",     3.0)
    b.window_earliest_start_hours = 8.0
    e = Activity("END",   0.0)
    return _build({s: [a], a: [b], b: [e], e: []})


MICRO_FIXTURES = [
    pytest.param(_chain_pert,     id="chain"),
    pytest.param(_fork_join_pert, id="fork_join"),
    pytest.param(_lag_pert,       id="lag"),
    pytest.param(_window_pert,    id="window"),
]


def _json_example_10():
    return Pert.from_json_file(str(DATA_DIR / "example_10.json"),  SCHEMA)


def _json_test_case_1():
    return Pert.from_json_file(str(DATA_DIR / "test_case_1.json"), SCHEMA)


JSON_FIXTURES = [
    pytest.param(_json_example_10,  id="example_10"),
    pytest.param(_json_test_case_1, id="test_case_1"),
]

ALL_FIXTURES = MICRO_FIXTURES + JSON_FIXTURES


# ===========================================================================
# Invariant 1 — makespan >= CPM_duration
# ===========================================================================

class TestMakespanGEQCPM:
    """
    Resource constraints can only stretch the schedule, never compress it.
    scheduled_duration >= cpm_duration must hold for every fixture.
    """

    @pytest.mark.parametrize("factory", ALL_FIXTURES)
    def test_makespan_at_least_cpm(self, factory):
        p = factory()
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p, factory.__name__ if callable(factory) else str(factory))

        sched = result['scheduled_duration']
        cpm   = result['cpm_duration']
        assert sched >= cpm - TOL, (
            f"Invariant violated: makespan ({sched:.3f}h) < CPM ({cpm:.3f}h)"
        )


# ===========================================================================
# Invariant 2 — Unconstrained → makespan == CPM_duration
# ===========================================================================

class TestUnconstrainedMakespanEqualsC:
    """
    When all resource pools are empty (no constraints), the scheduler must
    produce a schedule with makespan == CPM_duration.

    Pools are empty by construction for the micro-networks (ResourcePool(),
    EquipmentPool(), LocationPool() with no data = unlimited capacity).

    Note: the window fixture is excluded — time-window constraints are
    legitimate scheduling constraints that can push makespan beyond CPM
    (B.window_earliest=8h forces B to start later than its CPM ES).
    This invariant only covers resource constraints.
    """

    UNCONSTRAINED = [f for f in MICRO_FIXTURES
                     if f.values[0].__name__ != '_window_pert']

    @pytest.mark.parametrize("factory", UNCONSTRAINED)
    def test_unconstrained_makespan_equals_cpm(self, factory):
        p = factory()
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p, f"unconstrained/{factory}")

        sched = result['scheduled_duration']
        cpm   = result['cpm_duration']
        assert abs(sched - cpm) < TOL, (
            f"Unconstrained network should have makespan == CPM, "
            f"got makespan={sched:.4f}h  CPM={cpm:.4f}h  diff={sched-cpm:.4f}h"
        )


# ===========================================================================
# Invariant 3 — Monotonicity: tighter resource → longer makespan
# ===========================================================================

class TestMonotonicity:
    """
    Reducing crew availability must not decrease makespan.

    Network:
        START(0) → A(4)[1 WELDER] → END(0)
                 → B(3)[1 WELDER] →  ↑
        (fork — both branches join at END)

    CPM: project = max(4,3) = 4h (A is the critical path).

    With 2 WELDERs: A and B run in parallel → makespan = 4h (CPM-optimal).
    With 1 WELDER:  A and B serialized      → makespan = 7h (4+3).

    Monotonicity: makespan(1 WELDER) >= makespan(2 WELDERs).
    """

    def _fork_with_welders(self, n_welders: int) -> Pert:
        T0 = datetime(2026, 1, 1)
        s = Activity("START", 0.0)
        a = Activity("A",     4.0)
        b = Activity("B",     3.0)
        e = Activity("END",   0.0)
        a.required_resources = [{'skill_type': 'WELDER', 'crew_count': 1}]
        b.required_resources = [{'skill_type': 'WELDER', 'crew_count': 1}]
        fwd = {s: [a, b], a: [e], b: [e], e: []}
        rp = _make_crew_pool('WELDER', n_welders, T0)
        return _build(fwd, start_time=T0, pools=(rp, EquipmentPool(), LocationPool()))

    def test_two_welders_parallel(self):
        """2 WELDERs: A and B run in parallel → makespan == CPM == 4h."""
        p = self._fork_with_welders(2)
        result = p.calculateScheduleWithResources()
        assert_valid_schedule(p, "2-welder parallel")

        assert abs(result['scheduled_duration'] - 4.0) < TOL, (
            f"2 welders: expected makespan=4h, got {result['scheduled_duration']:.4f}h"
        )

    def test_one_welder_serial(self):
        """1 WELDER: A and B serialized → makespan = 4+3 = 7h."""
        p = self._fork_with_welders(1)
        result = p.calculateScheduleWithResources()
        assert_valid_schedule(p, "1-welder serial")

        assert abs(result['scheduled_duration'] - 7.0) < TOL, (
            f"1 welder: expected makespan=7h, got {result['scheduled_duration']:.4f}h"
        )

    def test_monotonicity(self):
        """makespan(1 WELDER) >= makespan(2 WELDERs)."""
        tight = self._fork_with_welders(1)
        loose = self._fork_with_welders(2)
        r_tight = tight.calculateScheduleWithResources()
        r_loose = loose.calculateScheduleWithResources()

        assert r_tight['scheduled_duration'] >= r_loose['scheduled_duration'] - TOL, (
            f"Monotonicity violated: tight makespan ({r_tight['scheduled_duration']:.3f}h) "
            f"< loose makespan ({r_loose['scheduled_duration']:.3f}h)"
        )

    @pytest.mark.parametrize("n_tight,n_loose", [(1, 2), (2, 4), (1, 3)])
    def test_monotonicity_parametric(self, n_tight, n_loose):
        """makespan(n_tight) >= makespan(n_loose) for various crew reductions."""
        tight = self._fork_with_welders(n_tight)
        loose = self._fork_with_welders(n_loose)
        r_tight = tight.calculateScheduleWithResources()
        r_loose = loose.calculateScheduleWithResources()

        assert r_tight['scheduled_duration'] >= r_loose['scheduled_duration'] - TOL, (
            f"Monotonicity violated: {n_tight} workers → {r_tight['scheduled_duration']:.3f}h "
            f"vs {n_loose} workers → {r_loose['scheduled_duration']:.3f}h"
        )

    def test_json_fixture_monotonicity(self, json_example_10):
        """
        Reduce one skill's crew count in example_10 by half.
        Makespan of reduced schedule must be >= original.
        """
        import copy
        p_orig = Pert.from_json_file(json_example_10, SCHEMA)
        r_orig = p_orig.calculateScheduleWithResources()

        # Halve crew for the first skill found in the pool
        p_tight = Pert.from_json_file(json_example_10, SCHEMA)
        skills = p_tight.crew_pool.get_all_skills()
        if not skills:
            pytest.skip("example_10 has no renewable crew skills to reduce")

        skill = skills[0]
        for period in p_tight.crew_pool.resources[skill].get_all_periods():
            period['available_count'] = max(1, period['available_count'] // 2)
        # Rebuild availability snapshots after mutation
        p_tight._precompute_availability_events()

        r_tight = p_tight.calculateScheduleWithResources()

        assert r_tight['scheduled_duration'] >= r_orig['scheduled_duration'] - TOL, (
            f"Monotonicity violated after halving '{skill}' crew: "
            f"tight={r_tight['scheduled_duration']:.3f}h "
            f"orig={r_orig['scheduled_duration']:.3f}h"
        )


# ===========================================================================
# Invariant 4 — replan(t=0) ≡ full schedule
# ===========================================================================

class TestReplanAtZeroEquivalence:
    """
    A replan triggered at t=0h must reproduce the same start/end times as
    a fresh calculateScheduleWithResources() call.

    At t=0, only START (duration=0) has completed; all real activities are
    either in-progress (if they begin at t=0) or pending.
    _generate_info_from(0) produces the same CPM values as generateInfo(),
    so the scheduling loop sees an identical state.
    """

    def _check_equivalence(self, p: Pert, label: str):
        """Run twice and assert start/end times are identical."""
        # Run 1: full schedule
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p, f"{label} run-1")
        snap1 = _snapshot(p)

        # Run 2: replan at t=0
        result2 = p.replan(current_time_hours=0.0)
        assert_valid_schedule(p, f"{label} replan(t=0)")
        snap2 = _snapshot(p)

        # Compare snapshots
        diffs = []
        for name in sorted(snap1):
            st1, et1 = snap1[name]
            st2, et2 = snap2.get(name, (None, None))
            if st1 is None and st2 is None:
                continue
            if st1 is None or st2 is None:
                diffs.append(f"  {name}: run1 start={st1}  replan start={st2}")
                continue
            d_start = abs((st2 - st1).total_seconds())
            d_end   = abs((et2 - et1).total_seconds())
            if d_start > TOL_SECONDS or d_end > TOL_SECONDS:
                diffs.append(
                    f"  {name}: run1=({st1}, {et1})  "
                    f"replan=({st2}, {et2})  "
                    f"Δstart={d_start:.1f}s  Δend={d_end:.1f}s"
                )
        if diffs:
            pytest.fail(
                f"[{label}] replan(t=0) produced different schedule:\n"
                + "\n".join(diffs)
            )

    def test_chain(self):
        """Linear chain: replan(t=0) must reproduce CPM-optimal schedule."""
        p = _chain_pert()
        self._check_equivalence(p, "chain")

    def test_fork_join(self):
        """Fork-join: replan(t=0) preserves both branch start times."""
        p = _fork_join_pert()
        self._check_equivalence(p, "fork_join")

    def test_lag(self):
        """Chain with FS lag: replan(t=0) preserves lag-driven start."""
        p = _lag_pert()
        self._check_equivalence(p, "lag")

    def test_example_10(self, json_example_10):
        """Real outage network: replan(t=0) must equal full schedule."""
        p = Pert.from_json_file(json_example_10, SCHEMA)
        self._check_equivalence(p, "example_10")

    def test_resource_constrained(self):
        """Resource-constrained fork: serialization order preserved by replan(t=0)."""
        T0 = datetime(2026, 1, 1)
        s = Activity("START", 0.0)
        a = Activity("A",     4.0)
        b = Activity("B",     3.0)
        e = Activity("END",   0.0)
        a.required_resources = [{'skill_type': 'WELDER', 'crew_count': 1}]
        b.required_resources = [{'skill_type': 'WELDER', 'crew_count': 1}]
        fwd = {s: [a, b], a: [e], b: [e], e: []}
        rp = _make_crew_pool('WELDER', 1, T0)  # 1 welder → forced serialization
        p = _build(fwd, start_time=T0, pools=(rp, EquipmentPool(), LocationPool()))
        self._check_equivalence(p, "resource_constrained")
