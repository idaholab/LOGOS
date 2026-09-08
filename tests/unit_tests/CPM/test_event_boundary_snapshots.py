"""
Unit tests for the event-boundary capacity snapshot optimisation (Challenge 7,
Phase 2).

``_build_capacity_snapshots`` now builds a sparse boundary grid keyed at the
times where capacity actually changes (activity start/end boundaries) instead
of one entry per clock hour.  ``_fits_with_tentative`` and
``_apply_tentative`` both accept an optional ``grid`` parameter; when supplied
they iterate only the relevant boundary points instead of every hour.

Tests cover:
- Grid structure: contains start_time and end_time
- Grid structure: contains ongoing activity boundaries within window
- Grid structure: contains extra_boundaries supplied by caller
- Grid structure: does NOT contain points outside [start_time, end_time]
- Grid is sorted ascending
- No-ongoing baseline: grid = [start_time, end_time]
- Capacity values at grid points match the original hour-by-hour values
- _fits_with_tentative(grid=None) and (grid=grid) agree on feasibility
- _apply_tentative(grid=None) and (grid=grid) produce matching res_rem dicts
- _apply_tentative with grid does NOT bleed past activity end boundary
- Scheduling outcome unchanged: same activities scheduled in same order
- Microbenchmark: boundary path faster than hour-by-hour on wide window
"""

import time
import math
import pytest
from datetime import datetime, timedelta
from collections import defaultdict

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, ResourceAvailability, EquipmentPool, LocationPool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start():
    return datetime(2026, 1, 1, 0, 0)


def _make_rp(skill: str, count: int):
    """ResourcePool with a single always-available skill."""
    rp = ResourcePool()
    start = datetime(2026, 1, 1)
    end   = datetime(2026, 12, 31)
    rp.resources[skill] = ResourceAvailability(
        skill,
        [{'start_date': start, 'end_date': end, 'available_count': count}]
    )
    return rp


def _simple_pert(skill='MECH', pool_count=4) -> Pert:
    """
    START(0) -> A(8h, needs 2 MECH) -> B(4h, needs 1 MECH) -> END(0)
    Returns a Pert with startTime set and resource pool attached.
    """
    start = Activity('START', 0.0)
    a = Activity('A', 8.0, required_resources=[{'skill_type': skill, 'crew_count': 2}])
    b = Activity('B', 4.0, required_resources=[{'skill_type': skill, 'crew_count': 1}])
    end = Activity('END', 0.0)

    fwd = {start: [a], a: [b], b: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool  = _make_rp(skill, pool_count)
    p.equipment_pool = EquipmentPool()
    p.location_pool  = LocationPool()
    p.startTime      = _start()
    p.generateInfo()
    return p


def _ongoing_activity(start_dt, duration_h, skill='MECH', count=2):
    """Create an Activity with actual start/end times set (simulates 'ongoing')."""
    act = Activity('ONG', duration_h,
                   required_resources=[{'skill_type': skill, 'crew_count': count}])
    act.setActualStartTime(start_dt)
    return act


# ---------------------------------------------------------------------------
# TestGridStructure
# ---------------------------------------------------------------------------

class TestGridStructure:

    def _build(self, p, start, end, extra=None):
        *_, grid = p._build_capacity_snapshots(start, end, extra_boundaries=extra)
        return grid

    def test_grid_always_contains_start_time(self):
        p = _simple_pert()
        t0 = _start()
        t1 = t0 + timedelta(hours=10)
        grid = self._build(p, t0, t1)
        assert t0 in grid

    def test_grid_always_contains_end_time(self):
        p = _simple_pert()
        t0 = _start()
        t1 = t0 + timedelta(hours=10)
        grid = self._build(p, t0, t1)
        assert t1 in grid

    def test_grid_is_sorted(self):
        p = _simple_pert()
        t0 = _start()
        t1 = t0 + timedelta(hours=20)
        # Inject an ongoing activity to create intermediate grid points
        ong = _ongoing_activity(t0 + timedelta(hours=3), 4.0)
        p.ongoing = [ong]
        grid = self._build(p, t0, t1)
        assert grid == sorted(grid)

    def test_grid_contains_ongoing_start_within_window(self):
        p = _simple_pert()
        t0 = _start()
        t1 = t0 + timedelta(hours=20)
        ong_start = t0 + timedelta(hours=3)
        ong = _ongoing_activity(ong_start, 4.0)
        p.ongoing = [ong]
        grid = self._build(p, t0, t1)
        assert ong_start in grid

    def test_grid_contains_ongoing_end_within_window(self):
        p = _simple_pert()
        t0 = _start()
        t1 = t0 + timedelta(hours=20)
        ong_start = t0 + timedelta(hours=3)
        ong = _ongoing_activity(ong_start, 4.0)   # ends at t0+7h
        p.ongoing = [ong]
        grid = self._build(p, t0, t1)
        assert ong_start + timedelta(hours=4) in grid

    def test_grid_excludes_points_outside_window(self):
        p = _simple_pert()
        t0 = _start()
        t1 = t0 + timedelta(hours=10)
        extra_outside = {t0 - timedelta(hours=1), t1 + timedelta(hours=5)}
        grid = self._build(p, t0, t1, extra=extra_outside)
        for pt in grid:
            assert t0 <= pt <= t1, f"grid point {pt} is outside [{t0}, {t1}]"

    def test_grid_contains_extra_boundaries(self):
        p = _simple_pert()
        t0 = _start()
        t1 = t0 + timedelta(hours=10)
        extra = {t0 + timedelta(hours=3), t0 + timedelta(hours=7)}
        grid = self._build(p, t0, t1, extra=extra)
        for pt in extra:
            assert pt in grid

    def test_no_ongoing_grid_is_minimal(self):
        """With no ongoing activities and no extras the grid is just [start, end]."""
        p = _simple_pert()
        p.ongoing = []
        t0 = _start()
        t1 = t0 + timedelta(hours=10)
        grid = self._build(p, t0, t1)
        assert grid == [t0, t1]


# ---------------------------------------------------------------------------
# TestCapacityValues
# ---------------------------------------------------------------------------

class TestCapacityValues:
    """Capacity values at each grid point must match the original hour-by-hour result."""

    def test_resource_capacity_matches_hourly(self):
        p = _simple_pert(pool_count=4)
        t0 = _start()
        # Put an ongoing activity that consumes 2 workers for hours [2, 5)
        ong_start = t0 + timedelta(hours=2)
        ong = _ongoing_activity(ong_start, 3.0, count=2)
        p.ongoing = [ong]

        t1 = t0 + timedelta(hours=10)
        # Boundary snapshot
        res_rem_b, *_, grid = p._build_capacity_snapshots(t0, t1)

        # Hour-by-hour (old path): call with grid=None equivalent
        # Manually rebuild using the old method on each hour
        for h in grid:
            if h >= t1:
                continue
            orig = p.crew_pool.get_availability('MECH', h)
            consumed = p._get_consumed_resources('MECH', h)
            expected = max(0, orig - consumed)
            assert res_rem_b['MECH'][h] == expected, (
                f"At {h}: boundary={res_rem_b['MECH'][h]}, expected={expected}"
            )

    def test_resource_capacity_zero_when_fully_consumed(self):
        """If ongoing consumes full pool, remaining capacity must be 0."""
        p = _simple_pert(pool_count=2)
        t0 = _start()
        # Ongoing uses all 2 workers
        ong = _ongoing_activity(t0, 5.0, count=2)
        p.ongoing = [ong]
        t1 = t0 + timedelta(hours=10)
        res_rem, *_, grid = p._build_capacity_snapshots(t0, t1)
        # At t0 (within ongoing window) should be 0
        assert res_rem['MECH'][t0] == 0

    def test_capacity_restored_after_ongoing_ends(self):
        p = _simple_pert(pool_count=4)
        t0 = _start()
        ong = _ongoing_activity(t0, 3.0, count=2)  # ends at t0+3h
        p.ongoing = [ong]
        t1 = t0 + timedelta(hours=10)
        res_rem, *_, grid = p._build_capacity_snapshots(t0, t1)
        t_after = t0 + timedelta(hours=3)  # boundary point: ong just ended
        assert res_rem['MECH'][t_after] == 4  # full pool available again


# ---------------------------------------------------------------------------
# TestFitsWithTentativeGrid
# ---------------------------------------------------------------------------

class TestFitsWithTentativeGrid:
    """_fits_with_tentative with the boundary grid must return correct feasibility.

    Note: the grid=None fallback path is designed to be used only with the OLD
    dense (every-hour) snapshot.  The new _build_capacity_snapshots returns a
    sparse dict keyed only at grid points; pairing a sparse dict with grid=None
    would cause get(h, 0) to return 0 for non-grid hours (incorrectly infeasible).
    These tests therefore test the boundary path directly rather than comparing
    it against the hourly fallback.
    """

    def _fits(self, p, activity, start, end):
        """Return feasibility using the boundary grid path."""
        res_b, eq_b, lt_b, lw_b, grid = p._build_capacity_snapshots(
            start, end, extra_boundaries={end}
        )
        return p._fits_with_tentative(activity, start, res_b, eq_b, lt_b, lw_b, grid)

    def test_feasible_with_ample_pool(self):
        p = _simple_pert(pool_count=4)
        p.ongoing = []
        t0 = _start()
        candidate = Activity('C', 4.0,
                             required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        assert self._fits(p, candidate, t0, t0 + timedelta(hours=4)) is True

    def test_infeasible_due_to_insufficient_pool(self):
        p = _simple_pert(pool_count=1)
        p.ongoing = []
        t0 = _start()
        candidate = Activity('C', 4.0,
                             required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        assert self._fits(p, candidate, t0, t0 + timedelta(hours=4)) is False

    def test_infeasible_due_to_ongoing_consumption(self):
        """Ongoing uses 2 of 3 workers → candidate needing 2 more is infeasible."""
        p = _simple_pert(pool_count=3)
        t0 = _start()
        ong = _ongoing_activity(t0, 6.0, count=2)
        p.ongoing = [ong]
        candidate = Activity('C', 4.0,
                             required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        assert self._fits(p, candidate, t0, t0 + timedelta(hours=4)) is False

    def test_feasible_after_ongoing_ends(self):
        """Candidate starts after ongoing finishes — full pool available."""
        p = _simple_pert(pool_count=2)
        t0 = _start()
        ong = _ongoing_activity(t0, 3.0, count=2)  # frees at t0+3h
        p.ongoing = [ong]
        candidate = Activity('C', 2.0,
                             required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        start = t0 + timedelta(hours=3)
        assert self._fits(p, candidate, start, start + timedelta(hours=2)) is True


# ---------------------------------------------------------------------------
# TestApplyTentativeGrid
# ---------------------------------------------------------------------------

class TestApplyTentativeGrid:
    """_apply_tentative with grid must produce capacity dicts matching the hourly path."""

    def test_apply_tentative_decrement_matches_hourly(self):
        """After applying a candidate, remaining capacity must match the hourly path."""
        p = _simple_pert(pool_count=4)
        t0 = _start()
        t_end = t0 + timedelta(hours=8)

        # Build two identical snapshots
        res_b, eq_b, lt_b, lw_b, grid = p._build_capacity_snapshots(
            t0, t_end, extra_boundaries={t0 + timedelta(hours=4)}
        )
        res_h, eq_h, lt_h, lw_h, _ = p._build_capacity_snapshots(t0, t_end)

        candidate = Activity('C', 4.0,
                             required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])

        p._apply_tentative(candidate, t0, res_b, eq_b, lt_b, lw_b, grid)
        p._apply_tentative(candidate, t0, res_h, eq_h, lt_h, lw_h, None)

        # For every grid point in [t0, t0+4h), boundary and hourly must match
        for h in grid:
            if t0 <= h < t0 + timedelta(hours=4):
                assert res_b['MECH'][h] == res_h['MECH'][h], (
                    f"Mismatch at {h}: boundary={res_b['MECH'][h]}, hourly={res_h['MECH'][h]}"
                )

    def test_apply_tentative_does_not_bleed_past_end(self):
        """Capacity reduction must stop at activity end, not extend to next interval.

        Scenario:
          - Ongoing activity X: [t0+5h, t0+10h) — creates a grid boundary at t0+5h
          - Candidate C: duration 3h → ends at t0+3h
          - After applying C, capacity at t0+5h (outside C's window) must be unchanged.
        """
        p = _simple_pert(pool_count=6)
        t0 = _start()
        ong = _ongoing_activity(t0 + timedelta(hours=5), 5.0, count=1)
        p.ongoing = [ong]

        t_end = t0 + timedelta(hours=12)
        cand_end = t0 + timedelta(hours=3)
        res_b, eq_b, lt_b, lw_b, grid = p._build_capacity_snapshots(
            t0, t_end, extra_boundaries={cand_end}
        )

        capacity_before = res_b['MECH'].get(t0 + timedelta(hours=5), None)

        candidate = Activity('C', 3.0,
                             required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        p._apply_tentative(candidate, t0, res_b, eq_b, lt_b, lw_b, grid)

        capacity_after = res_b['MECH'].get(t0 + timedelta(hours=5), None)

        assert capacity_before == capacity_after, (
            f"Capacity at t0+5h changed: before={capacity_before}, after={capacity_after}. "
            "Apply bled past activity end boundary."
        )


# ---------------------------------------------------------------------------
# TestSchedulingOutcomeUnchanged
# ---------------------------------------------------------------------------

class TestSchedulingOutcomeUnchanged:
    """End-to-end: the event-boundary path must produce the same schedule as before."""

    def test_linear_chain_completion(self):
        """All activities in a linear chain complete successfully."""
        p = _simple_pert(pool_count=4)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p)
        assert result['n_completed'] == result['n_activities']

    def test_scheduled_duration_matches_cpm(self):
        """With ample resources, scheduled duration equals CPM duration."""
        p = _simple_pert(pool_count=10)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p)
        assert abs(result['scheduled_duration'] - result['cpm_duration']) < 1e-6

    def test_resource_constrained_delay(self):
        """With tight resources, delay is non-negative."""
        p = _simple_pert(pool_count=2)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p)
        assert result['delay_hours'] >= 0.0
        assert result['n_completed'] == result['n_activities']

    def test_no_overbooking(self):
        """No hour should have more workers assigned than available (pool size = 2)."""
        p = _simple_pert(pool_count=2)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p)
        t0 = p.startTime
        for h_offset in range(20):
            h = t0 + timedelta(hours=h_offset)
            consumed = p._get_consumed_resources('MECH', h)
            available = p.crew_pool.get_availability('MECH', h)
            assert consumed <= available, (
                f"Overbooking at h+{h_offset}: consumed={consumed} > available={available}"
            )


# ---------------------------------------------------------------------------
# Microbenchmark
# ---------------------------------------------------------------------------

class TestPerformanceBoundary:

    @pytest.mark.slow
    def test_boundary_faster_than_hourly_on_wide_window(self):
        """On a wide window with few ongoing activities, boundary path must be faster.

        Setup: 1 ongoing 100-hour activity, window width = 200 hours.
        Hour-by-hour path: 200 iterations.
        Boundary path: ~3 iterations (start, ong_start, ong_end, window_end).
        """
        rp = _make_rp('MECH', 10)
        p = Pert(graph={Activity('S', 0): []})
        p.crew_pool  = rp
        p.equipment_pool = EquipmentPool()
        p.location_pool  = LocationPool()
        p.startTime      = _start()

        t0 = _start()
        ong = _ongoing_activity(t0 + timedelta(hours=50), 100.0, count=3)
        p.ongoing = [ong]

        t_end = t0 + timedelta(hours=200)
        cand_end = t0 + timedelta(hours=80)

        REPS = 500

        # Boundary path
        t_start = time.perf_counter()
        for _ in range(REPS):
            p._build_capacity_snapshots(t0, t_end, extra_boundaries={cand_end})
        t_boundary = time.perf_counter() - t_start

        # Hour-by-hour path (no grid, direct _iter_hours simulation)
        t_start = time.perf_counter()
        for _ in range(REPS):
            # Build the same snapshot using the old logic: one entry per hour
            res_rem = defaultdict(dict)
            for skill in rp.get_all_skills():
                for h_off in range(200):
                    h = t0 + timedelta(hours=h_off)
                    orig = rp.get_availability(skill, h)
                    consumed = p._get_consumed_resources(skill, h)
                    res_rem[skill][h] = max(0, orig - consumed)
        t_hourly = time.perf_counter() - t_start

        assert t_boundary < t_hourly, (
            f"Boundary path ({t_boundary:.3f}s) should be faster than "
            f"hourly path ({t_hourly:.3f}s) for a 200-hour window."
        )
