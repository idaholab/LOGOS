"""domain/resource_util.py — pure demand-vs-capacity timeline builder.

Mirrors the engine's ``plot_resource_utilization`` (pert.py:7656) but as a PURE,
breakpoint-exact algorithm over the neutral schedule + pool DTOs — it never touches a
live ``Pert``. One series per resource pool; within each series the timeline is cut at
every point where demand or capacity can change (activity starts/ends, pool-period
boundaries) and each resulting half-open interval carries the constant demand and the
available capacity sampled at its midpoint. Where the engine samples hourly, this samples
the exact breakpoints, so it is faithful without a fixed grid.

Invoked by the PRISM adapter (the sole translation boundary); the result hangs off
``DiagnosticsDTO`` and rides the immutable ``RunResult``. Because it is a deterministic
function of two deterministic inputs, the adapter's A→B→A isolation invariant extends to
it unchanged. Pure: stdlib + sibling domain modules only.
"""

from __future__ import annotations

from prismGui.domain.hashing import q
from prismGui.domain.plan import ResourceAvailability, ResourcePool
from prismGui.domain.results import (
    TF_ZERO_TOL,
    Hours,
    ResourceUtilizationDTO,
    ScheduledActivityDTO,
    ScheduleDTO,
    SkillUtilizationSeries,
    UtilizationInterval,
)


def _demand_at(activities: tuple[ScheduledActivityDTO, ...], skill: str, t: Hours) -> int:
    """Crew of ``skill`` active at instant ``t`` (half-open [start, end)), summed from the
    resolved ``actual_resources`` — the same post-substitution allocation the engine plot
    sums (``_actual_resources_for_start``, pert.py:7697), so demand matches without a Pert."""
    total = 0
    for a in activities:
        if a.start_hour <= t < a.end_hour:
            for ar in a.actual_resources:
                if ar.skill_type == skill:
                    total += ar.crew_count
    return total


def _available_at(periods: tuple[ResourceAvailability, ...], t: Hours) -> int:
    """Capacity at instant ``t``: the count of the half-open period containing it, else 0
    (mirrors ``ResourceAvailability.get_availability_at``, outage_data.py:663)."""
    for p in periods:
        if p.start <= t < p.end:
            return p.count
    return 0


def _breakpoints(
    activities: tuple[ScheduledActivityDTO, ...],
    periods: tuple[ResourceAvailability, ...],
    horizon: Hours,
) -> tuple[Hours, ...]:
    """Sorted-unique cut points on [0, horizon]: the horizon ends, every activity
    start/end, and every pool-period boundary, each clamped into range. Demand and
    capacity are constant between consecutive cuts, so sampling one interior point per
    gap is exact."""
    pts = {0.0, horizon}
    for a in activities:
        pts.add(a.start_hour)
        pts.add(a.end_hour)
    for p in periods:
        pts.add(min(max(p.start, 0.0), horizon))
        pts.add(min(max(p.end, 0.0), horizon))
    return tuple(sorted(v for v in pts if 0.0 <= v <= horizon))


def _series_for_pool(
    pool: ResourcePool,
    activities: tuple[ScheduledActivityDTO, ...],
    horizon: Hours,
) -> SkillUtilizationSeries:
    cuts = _breakpoints(activities, pool.availability_periods, horizon)
    intervals: list[UtilizationInterval] = []
    for t0, t1 in zip(cuts, cuts[1:]):
        if t1 - t0 <= TF_ZERO_TOL:          # drop sub-tolerance slivers (e.g. the 1 s gap)
            continue
        mid = (t0 + t1) / 2.0
        intervals.append(UtilizationInterval(
            start_hour=q(t0),
            end_hour=q(t1),
            demand=_demand_at(activities, pool.skill_type, mid),
            available=_available_at(pool.availability_periods, mid),
        ))
    return SkillUtilizationSeries(skill_type=pool.skill_type, intervals=tuple(intervals))


def build_resource_utilization(
    schedule: ScheduleDTO,
    pools: tuple[ResourcePool, ...],
) -> ResourceUtilizationDTO:
    """Build the demand-vs-capacity timeline for a completed schedule. Deterministic in
    both inputs: one series per pool in ``pools`` order (== ``PlanContent.resources``
    order), horizon = the makespan (max activity end_hour). An empty schedule yields a
    zero horizon and empty-interval series."""
    activities = schedule.activities
    horizon = max((a.end_hour for a in activities), default=0.0)
    series = tuple(_series_for_pool(p, activities, horizon) for p in pools)
    return ResourceUtilizationDTO(horizon_hours=q(horizon), series=series)
