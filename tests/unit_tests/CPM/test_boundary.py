"""
Pass 5 — Boundary / stress cases.

Verifies expected behaviour at graph extremes and scheduling edge cases.

Cases:
  1. Empty task list        → makespan = 0, n_activities = 0
  2. Single task            → starts at h=0, ends at h=duration
  3. Fully serial chain     → makespan = sum of durations; all slack = 0
  4. Fully parallel         → makespan = max(duration); unconstrained resources
  5. Tight resource         → complete serialization; makespan = sum of bottleneck durations
  6. Near-deadlock          → depleted consumable pool; warning logged; partial schedule
"""

import logging
import pytest
from datetime import datetime, timedelta

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    ResourcePool, EquipmentPool, LocationPool,
    ResourceAvailability, ConsumablePool,
)

TOL = 1e-6


# ---------------------------------------------------------------------------
# Shared helpers (mirror test_invariants.py conventions)
# ---------------------------------------------------------------------------

def _pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _build(fwd, start_time=None, pools=None):
    p = Pert(graph=fwd)
    rp, ep, lp = pools or _pools()
    p.crew_pool      = rp
    p.equipment_pool = ep
    p.location_pool  = lp
    p.startTime = start_time or datetime(2026, 1, 1)
    p.generateInfo()
    return p


def _make_crew_pool(skill: str, count: int, start_time: datetime) -> ResourcePool:
    rp = ResourcePool()
    rp.resources[skill] = ResourceAvailability(
        skill,
        [{'start_date': start_time,
          'end_date':   start_time + timedelta(days=365),
          'available_count': count}],
        resource_type='renewable',
    )
    return rp


# ===========================================================================
# Case 1 — Empty task list
# ===========================================================================

class TestEmptyTaskList:
    """
    An empty graph must schedule instantly with makespan = 0 and
    n_activities = 0.  No exceptions, no validator violations.
    """

    def test_returns_zero_makespan(self):
        p = _build({})
        result = p.calculateScheduleWithResources()
        assert result['scheduled_duration'] == 0.0
        assert result['cpm_duration'] == 0.0

    def test_n_activities_and_n_completed_are_zero(self):
        p = _build({})
        result = p.calculateScheduleWithResources()
        assert result['n_activities'] == 0
        assert result['n_completed'] == 0

    def test_no_validator_violations(self):
        p = _build({})
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "empty graph")


# ===========================================================================
# Case 2 — Single task, no predecessors
# ===========================================================================

class TestSingleTask:
    """
    A single activity (no START/END wrapper) must start at project start,
    end at project start + duration, and produce a valid schedule.
    """

    def test_starts_at_project_start(self):
        T0 = datetime(2026, 1, 1)
        a = Activity("TASK", 5.0)
        p = _build({a: []}, start_time=T0)
        p.calculateScheduleWithResources()
        st, et = a.returnAbsTimes()
        assert st == T0, f"Expected start=T0, got {st}"
        assert et == T0 + timedelta(hours=5.0), f"Expected end=T0+5h, got {et}"

    def test_makespan_equals_duration(self):
        a = Activity("TASK", 7.5)
        p = _build({a: []})
        result = p.calculateScheduleWithResources()
        assert abs(result['scheduled_duration'] - 7.5) < TOL
        assert abs(result['cpm_duration']        - 7.5) < TOL

    def test_cpm_equals_scheduled(self):
        """Unconstrained single task: no resource stretch possible."""
        a = Activity("TASK", 3.0)
        p = _build({a: []})
        result = p.calculateScheduleWithResources()
        assert abs(result['scheduled_duration'] - result['cpm_duration']) < TOL

    def test_single_task_valid_schedule(self):
        a = Activity("TASK", 4.0)
        p = _build({a: []})
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "single task")


# ===========================================================================
# Case 3 — Fully serial chain (all slack = 0)
# ===========================================================================

class TestFullySerialChain:
    """
    A strictly serial chain has zero total float on every activity.
    Makespan must equal the sum of durations regardless of resource constraints
    (resources are unconstrained here — purely precedence-driven delay).
    """

    def _serial(self, durations):
        """Build a chain T0→T1→…→Tn-1 without START/END wrappers."""
        acts = [Activity(f"T{i}", d) for i, d in enumerate(durations)]
        fwd = {}
        for i, a in enumerate(acts):
            fwd[a] = [acts[i + 1]] if i + 1 < len(acts) else []
        return acts, _build(fwd)

    def test_makespan_equals_sum_of_durations(self):
        durations = [4.0, 3.0, 5.0, 2.0]
        acts, p = self._serial(durations)
        result = p.calculateScheduleWithResources()
        expected = sum(durations)
        assert abs(result['scheduled_duration'] - expected) < TOL, (
            f"Serial chain: expected makespan={expected}h, "
            f"got {result['scheduled_duration']:.4f}h"
        )

    def test_all_activities_have_zero_slack(self):
        durations = [3.0, 2.0, 4.0]
        acts, p = self._serial(durations)
        p.calculateScheduleWithResources()
        for act in acts:
            slack = p.infoDict[act]['slack']
            assert abs(slack) < TOL, (
                f"Activity {act.name}: expected slack=0, got {slack:.4f}"
            )

    def test_cpm_equals_makespan(self):
        """Serial chain is unconstrained — CPM must equal the scheduled duration."""
        durations = [2.0, 6.0, 1.0]
        acts, p = self._serial(durations)
        result = p.calculateScheduleWithResources()
        assert abs(result['cpm_duration'] - result['scheduled_duration']) < TOL

    def test_serial_activities_do_not_overlap(self):
        """Each activity must start exactly when the previous one ends."""
        durations = [3.0, 2.0, 4.0]
        acts, p = self._serial(durations)
        p.calculateScheduleWithResources()
        T0 = p.startTime
        cursor = T0
        for act in acts:
            st, et = act.returnAbsTimes()
            delta = abs((st - cursor).total_seconds())
            assert delta < 1.0, (
                f"{act.name}: expected start at {cursor}, got {st} "
                f"(Δ={delta:.1f}s)"
            )
            cursor = et

    def test_serial_valid_schedule(self):
        durations = [4.0, 3.0, 2.0]
        acts, p = self._serial(durations)
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "serial chain")


# ===========================================================================
# Case 4 — Fully parallel, no precedence between branches
# ===========================================================================

class TestFullyParallel:
    """
    All branches independent (START→[B…]→END).  With unlimited resources
    all branches run simultaneously → makespan = max(branch durations).
    """

    def _parallel(self, durations):
        T0 = datetime(2026, 1, 1)
        s = Activity("START", 0.0)
        e = Activity("END",   0.0)
        branches = [Activity(f"B{i}", d) for i, d in enumerate(durations)]
        fwd = {s: list(branches), e: []}
        for b in branches:
            fwd[b] = [e]
        return branches, _build(fwd, start_time=T0)

    def test_makespan_equals_max_duration(self):
        durations = [4.0, 3.0, 7.0, 2.0, 5.0]
        branches, p = self._parallel(durations)
        result = p.calculateScheduleWithResources()
        expected = max(durations)
        assert abs(result['scheduled_duration'] - expected) < TOL, (
            f"Parallel: expected makespan={expected}h, "
            f"got {result['scheduled_duration']:.4f}h"
        )

    def test_all_branches_start_at_project_start(self):
        T0 = datetime(2026, 1, 1)
        durations = [3.0, 5.0, 4.0]
        branches, p = self._parallel(durations)
        p.calculateScheduleWithResources()
        for b in branches:
            st, _ = b.returnAbsTimes()
            assert st == T0, f"Branch {b.name}: expected start=T0, got {st}"

    def test_cpm_equals_makespan_unconstrained(self):
        """No resource constraints → no stretch beyond CPM."""
        durations = [2.0, 4.0, 3.0]
        branches, p = self._parallel(durations)
        result = p.calculateScheduleWithResources()
        assert abs(result['cpm_duration'] - result['scheduled_duration']) < TOL

    def test_non_critical_branches_have_positive_slack(self):
        """Branches shorter than the longest have positive total float."""
        durations = [2.0, 7.0, 4.0]   # B1 is critical (7h)
        branches, p = self._parallel(durations)
        p.calculateScheduleWithResources()
        # B0 (2h) and B2 (4h) should have slack > 0; B1 (7h) slack = 0
        slacks = {b.name: p.infoDict[b]['slack'] for b in branches}
        assert abs(slacks['B1']) < TOL, f"Critical branch B1 has slack {slacks['B1']}"
        assert slacks['B0'] > TOL,      f"Non-critical B0 has zero slack"
        assert slacks['B2'] > TOL,      f"Non-critical B2 has zero slack"

    def test_parallel_valid_schedule(self):
        durations = [5.0, 3.0, 4.0]
        branches, p = self._parallel(durations)
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "fully parallel")


# ===========================================================================
# Case 5 — Tight resource — complete serialization
# ===========================================================================

class TestTightResourceSerialization:
    """
    When a single bottleneck skill has capacity 1 and all parallel branches
    each require 1 unit, the scheduler is forced to serialize them.
    Makespan = sum of branch durations.
    """

    def _constrained_parallel(self, durations, n_workers):
        T0 = datetime(2026, 1, 1)
        s = Activity("START", 0.0)
        e = Activity("END",   0.0)
        branches = [Activity(f"B{i}", d) for i, d in enumerate(durations)]
        for b in branches:
            b.required_resources = [{'skill_type': 'WELDER', 'crew_count': 1}]
        fwd = {s: list(branches), e: []}
        for b in branches:
            fwd[b] = [e]
        rp = _make_crew_pool('WELDER', n_workers, T0)
        return branches, _build(fwd, start_time=T0,
                                pools=(rp, EquipmentPool(), LocationPool()))

    def test_fully_serialized_makespan(self):
        """1 WELDER, 4 branches → makespan = sum of durations."""
        durations = [4.0, 3.0, 2.0, 5.0]
        branches, p = self._constrained_parallel(durations, n_workers=1)
        result = p.calculateScheduleWithResources()
        expected = sum(durations)
        assert abs(result['scheduled_duration'] - expected) < TOL, (
            f"Fully serialized: expected makespan={expected}h, "
            f"got {result['scheduled_duration']:.4f}h"
        )

    def test_no_two_branches_overlap(self):
        """No two branches may run concurrently when only 1 worker is available."""
        durations = [3.0, 2.0, 4.0]
        branches, p = self._constrained_parallel(durations, n_workers=1)
        p.calculateScheduleWithResources()
        times = [b.returnAbsTimes() for b in branches]
        for i in range(len(times)):
            for j in range(i + 1, len(times)):
                st_i, et_i = times[i]
                st_j, et_j = times[j]
                overlap = st_i < et_j and st_j < et_i
                assert not overlap, (
                    f"Branches {branches[i].name} and {branches[j].name} overlap: "
                    f"({st_i}, {et_i}) vs ({st_j}, {et_j})"
                )

    def test_tight_resource_serialized_valid(self):
        durations = [3.0, 2.0, 4.0]
        branches, p = self._constrained_parallel(durations, n_workers=1)
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "tight resource serialization")

    def test_two_workers_reduces_makespan(self):
        """2 WELDERs: at least two branches can run in parallel → makespan < sum."""
        durations = [4.0, 3.0, 2.0, 5.0]
        _, p1 = self._constrained_parallel(durations, n_workers=1)
        _, p2 = self._constrained_parallel(durations, n_workers=2)
        r1 = p1.calculateScheduleWithResources()
        r2 = p2.calculateScheduleWithResources()
        assert r2['scheduled_duration'] < r1['scheduled_duration'] - TOL, (
            f"2 workers should reduce makespan: "
            f"1-worker={r1['scheduled_duration']:.3f}h "
            f"2-worker={r2['scheduled_duration']:.3f}h"
        )


# ===========================================================================
# Case 6 — Near-deadlock: depleted consumable pool, no restock
# ===========================================================================

class TestNearDeadlock:
    """
    When the consumable pool is permanently depleted (zero inventory, no
    restock) the activity can never start.  The event queue drains and the
    scheduler emits a deadlock warning then returns with a partial schedule.
    """

    def _depleted_setup(self):
        """Single activity that needs SEAL; pool has 0 inventory, no restock."""
        T0 = datetime(2026, 1, 1)
        a = Activity("TASK", 3.0)
        a.required_consumables = [{'item_id': 'SEAL', 'quantity_needed': 1.0}]

        cp = ConsumablePool()
        cp.items['SEAL']            = 0.0
        cp.remaining['SEAL']        = 0.0
        cp.description['SEAL']      = 'Specialty seal'
        cp._restock_cursor['SEAL']  = -1.0
        cp.restocks['SEAL']         = []

        p = _build({a: []}, start_time=T0)
        p.consumable_pool = cp
        return a, p

    def test_deadlock_warning_is_logged(self, caplog):
        """Scheduler must log a warning about exhausted event queue / deadlock."""
        _, p = self._depleted_setup()
        with caplog.at_level(logging.WARNING, logger='CPM.pert'):
            p.calculateScheduleWithResources()
        msgs = [rec.message for rec in caplog.records]
        assert any(
            'deadlock' in m.lower() or 'exhausted' in m.lower()
            for m in msgs
        ), f"Expected deadlock/exhausted warning; got: {msgs}"

    def test_partial_schedule_on_deadlock(self):
        """n_completed must be less than n_activities when consumable is depleted."""
        _, p = self._depleted_setup()
        result = p.calculateScheduleWithResources()
        assert result['n_completed'] < result['n_activities'], (
            f"Expected partial schedule: n_completed={result['n_completed']} "
            f"n_activities={result['n_activities']}"
        )

    def test_task_never_starts_on_deadlock(self):
        """The blocked activity must have no actual start time."""
        a, p = self._depleted_setup()
        p.calculateScheduleWithResources()
        st, _ = a.returnAbsTimes()
        assert st is None, f"TASK should not have started; got startTime={st}"
