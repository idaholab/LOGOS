"""Contract group N — the pure resource-utilization timeline builder (pure job).

``build_resource_utilization`` mirrors the engine's ``plot_resource_utilization`` as a
PURE, breakpoint-exact function of the neutral schedule + pool DTOs — it never touches a
live ``Pert``. These tests hand-build tiny schedules whose demand-vs-capacity curve can be
read off by hand, and pin the resulting step timeline exactly:

  * demand is summed from each activity's resolved ``actual_resources`` over its half-open
    [start, end); capacity is the count of the pool period containing an interval's midpoint;
  * the timeline is cut at every activity/period breakpoint, so consecutive intervals tile
    [0, horizon) contiguously and each carries a single (demand, available) pair;
  * an activity carrying no resources contributes 0 demand; a pool used by no activity still
    yields a real capacity curve at 0 demand.

These are pure (no marker = required Phase-1 contract). The same builder over the REAL PRISM
adapter on example_10 is pinned in ``tests/gui/integration/test_prism_adapter.py``.
"""

from __future__ import annotations

from prismGui.domain.plan import ResourceAvailability, ResourcePool
from prismGui.domain.resource_util import build_resource_utilization
from prismGui.domain.results import (
    ActualResource,
    ScheduleDTO,
    ScheduledActivityDTO,
    classify_float,
)


def _activity(task_id: str, start: float, end: float, resources) -> ScheduledActivityDTO:
    return ScheduledActivityDTO(
        task_id=task_id, start_hour=start, end_hour=end, duration=end - start,
        delay_hours=0.0, on_constrained_chain=False,
        float_class=classify_float(0.0, False), description=task_id,
        tf_actual_hours=0.0, actual_resources=resources)


def _schedule(activities) -> ScheduleDTO:
    """A minimal ScheduleDTO wrapper — only ``activities`` matter to the builder (it derives
    the horizon from the activity end times, not from ``makespan_hours``)."""
    return ScheduleDTO(
        makespan_hours=max((a.end_hour for a in activities), default=0.0),
        cpm_lower_bound_hours=0.0, optimism_gap_hours=0.0,
        activities=tuple(activities), constrained_chain=(), cpm_critical_path=())


class TestBuildResourceUtilization:

    def test_two_activities_one_pool_stepwise_demand_and_capacity(self):
        """A [0,4] crew 2 and B [2,6] crew 1 over a pool that steps 4→5 at hour 3 cut the
        timeline at {0,2,3,4,6}: demand steps 2,3,3,1 and capacity 4,4,5,5, and the four
        intervals tile [0,6] contiguously."""
        a = _activity("A", 0.0, 4.0, (ActualResource(skill_type="MECHANIC", crew_count=2),))
        b = _activity("B", 2.0, 6.0, (ActualResource(skill_type="MECHANIC", crew_count=1),))
        pool = ResourcePool(skill_type="MECHANIC", availability_periods=(
            ResourceAvailability(start=0.0, end=3.0, count=4),
            ResourceAvailability(start=3.0, end=10.0, count=5)))

        util = build_resource_utilization(_schedule([a, b]), (pool,))

        assert util.horizon_hours == 6.0
        assert len(util.series) == 1
        series = util.series[0]
        assert series.skill_type == "MECHANIC"

        ivs = series.intervals
        assert ivs[0].start_hour == 0.0
        assert ivs[-1].end_hour == 6.0
        for prev, nxt in zip(ivs, ivs[1:]):        # contiguous, no gaps or overlaps
            assert prev.end_hour == nxt.start_hour
        assert [iv.demand for iv in ivs] == [2, 3, 3, 1]
        assert [iv.available for iv in ivs] == [4, 4, 5, 5]
        assert all(iv.demand >= 0 and iv.available >= 0 for iv in ivs)

    def test_activity_without_resources_contributes_zero_demand(self):
        """An activity with an empty ``actual_resources`` adds nothing to demand — the
        curve is flat at 0 while capacity tracks the pool."""
        a = _activity("A", 0.0, 4.0, ())
        pool = ResourcePool(skill_type="MECHANIC", availability_periods=(
            ResourceAvailability(start=0.0, end=10.0, count=3),))

        util = build_resource_utilization(_schedule([a]), (pool,))

        series = util.series[0]
        assert series.intervals                        # non-empty (the [0,4] span)
        assert all(iv.demand == 0 for iv in series.intervals)
        assert all(iv.available == 3 for iv in series.intervals)

    def test_pool_used_by_no_activity_is_zero_demand_with_real_capacity(self):
        """One series per pool in ``pools`` order, capacity always defined: an ELECTRICIAN
        pool that no activity draws on still yields a real capacity curve at 0 demand."""
        a = _activity("A", 0.0, 4.0, (ActualResource(skill_type="MECHANIC", crew_count=2),))
        mech = ResourcePool(skill_type="MECHANIC", availability_periods=(
            ResourceAvailability(start=0.0, end=10.0, count=5),))
        elec = ResourcePool(skill_type="ELECTRICIAN", availability_periods=(
            ResourceAvailability(start=0.0, end=10.0, count=2),))

        util = build_resource_utilization(_schedule([a]), (mech, elec))

        assert [s.skill_type for s in util.series] == ["MECHANIC", "ELECTRICIAN"]
        elec_series = util.series[1]
        assert all(iv.demand == 0 for iv in elec_series.intervals)
        assert any(iv.available == 2 for iv in elec_series.intervals)

    def test_empty_schedule_yields_zero_horizon_and_empty_series(self):
        """No scheduled activities -> a zero horizon and an empty-interval series per pool
        (the builder never fabricates an interval on an empty timeline)."""
        pool = ResourcePool(skill_type="MECHANIC", availability_periods=(
            ResourceAvailability(start=0.0, end=10.0, count=3),))

        util = build_resource_utilization(_schedule([]), (pool,))

        assert util.horizon_hours == 0.0
        assert len(util.series) == 1
        assert util.series[0].intervals == ()
