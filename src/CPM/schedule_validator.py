"""
schedule_validator.py — Post-schedule feasibility checker for the Pert class.

Runs after ``calculateScheduleWithResources()`` and interrogates the actual
startTime / endTime values on every completed activity against every constraint
type the scheduler is responsible for enforcing.

Usage
-----
    # Via the Pert instance (preferred)
    result = pert.validate_schedule()
    if not result.is_feasible:
        for v in result.violations:
            print(v)
    print(result.summary())

    # Standalone
    from CPM.schedule_validator import validate_schedule
    result = validate_schedule(pert)

Return value
------------
    ValidationResult
        .is_feasible  : bool — True only when violations list is empty
        .violations   : List[Violation] — hard constraint breaches
        .warnings     : List[Violation] — soft quality issues
        .summary()    : str  — human-readable report

Violation types
---------------
    completeness      All activities scheduled and have times
    duration          endTime − startTime ≠ activity.duration
    precedence        Successor starts before predecessor finishes (+ lag)
    time_window       Activity starts/finishes outside its allowed window
    hold_point        Blocked task started before hold-point completion
    crew              Skill demand exceeds pool capacity at some instant
    equipment         Equipment demand exceeds pool capacity at some instant
    equipment_zone    Zone-locked equipment used by out-of-zone activity
    location          Location concurrency limit exceeded (tasks or workers)
    consumable        Consumable inventory goes negative during the schedule
    shift_calendar    Activity executes outside the shift window
    dose              Cumulative dose exceeds tracker budget
    system_state      Two simultaneous activities require incompatible states
"""

from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .pert import Pert

# Floating-point / scheduling tolerance for time comparisons
_PREC_TOL   = timedelta(seconds=60)   # 1-minute grace for precedence / window checks
_DUR_TOL    = timedelta(seconds=60)   # 1-minute grace for duration consistency


# ===========================================================================
# Data structures
# ===========================================================================

@dataclass
class Violation:
    """Single constraint breach or quality warning."""
    type:      str            # violation category (see module docstring)
    activity:  str            # activity name, or 'a → b' for pairwise checks
    detail:    str            # human-readable description
    severity:  str            # 'error' | 'warning'
    excess:    float = 0.0   # quantitative excess (hours, workers, mRem, …)

    def __str__(self) -> str:
        tag = 'ERROR' if self.severity == 'error' else 'WARN '
        excess_str = f'  [excess={self.excess:.2f}]' if self.excess else ''
        return f'[{tag}] [{self.type:<13}] {self.activity}: {self.detail}{excess_str}'


@dataclass
class ValidationResult:
    """Aggregated result of ``validate_schedule()``."""
    is_feasible: bool
    violations:  List[Violation] = field(default_factory=list)
    warnings:    List[Violation] = field(default_factory=list)

    def summary(self) -> str:
        lines = ['Schedule Validation Report', '=' * 50]
        status = 'FEASIBLE' if self.is_feasible else 'INFEASIBLE'
        lines.append(f'Status     : {status}')
        lines.append(f'Violations : {len(self.violations)}')
        lines.append(f'Warnings   : {len(self.warnings)}')
        if self.violations:
            lines.append('')
            lines.append('--- Violations ---')
            for v in self.violations:
                lines.append(f'  {v}')
        if self.warnings:
            lines.append('')
            lines.append('--- Warnings ---')
            for w in self.warnings:
                lines.append(f'  {w}')
        lines.append('=' * 50)
        return '\n'.join(lines)

    def __repr__(self) -> str:
        return (f'ValidationResult(feasible={self.is_feasible}, '
                f'violations={len(self.violations)}, '
                f'warnings={len(self.warnings)})')


# ===========================================================================
# Internal helpers
# ===========================================================================

def _crew_demand(act) -> dict:
    """Return {skill: workers} for an activity, preferring actual assignment."""
    actual = getattr(act, '_actual_resources', None)
    if actual:
        return dict(actual)
    return {req['skill_type']: req['crew_count']
            for req in act.getRequiredResources()}


def _eq_demand(act) -> dict:
    """Return {equipment_id: quantity} for an activity."""
    return {req['equipment_id']: req['quantity_needed']
            for req in act.getRequiredEquipment()}


# ===========================================================================
# Individual check functions
# ===========================================================================

def _check_completeness(pert: 'Pert',
                        violations: list, warnings: list) -> None:
    """All activities must be completed with valid start/end times."""
    n_total = len(pert.forwardDict)
    n_done  = len(pert.completed)

    if n_done < n_total:
        missing = [a.name for a in pert.forwardDict
                   if a not in pert._completed_set
                   and a.name not in ('START', 'END')]
        violations.append(Violation(
            type='completeness',
            activity='schedule',
            detail=(f'{n_total - n_done} of {n_total} activities not scheduled '
                    f'(first 5: {missing[:5]})'),
            severity='error',
            excess=float(n_total - n_done),
        ))

    for act in pert.completed:
        st, et = act.returnAbsTimes()
        if st is None or et is None:
            violations.append(Violation(
                type='completeness',
                activity=act.name,
                detail='completed activity has no startTime or endTime',
                severity='error',
            ))


def _check_durations(pert: 'Pert',
                     violations: list, warnings: list) -> None:
    """endTime − startTime must match activity.duration within tolerance.

    Activities that were in-progress at replan time have ``_remaining_duration``
    set and a stale ``endTime`` from before the replan.  Their duration field
    may also have been updated by a ``duration_override``.  These activities
    cannot be checked by a simple ``endTime − startTime == duration`` test, so
    they are skipped.
    """
    for act in pert.completed:
        # Skip activities frozen mid-execution by a replan: their endTime was
        # recorded before the duration override took effect and is stale.
        if getattr(act, '_remaining_duration', None) is not None:
            continue
        st, et = act.returnAbsTimes()
        if st is None or et is None:
            continue
        expected = timedelta(hours=act.duration)
        actual   = et - st
        delta    = abs(actual - expected)
        if delta > _DUR_TOL:
            delta_h = delta.total_seconds() / 3600.0
            violations.append(Violation(
                type='duration',
                activity=act.name,
                detail=(f'endTime − startTime = {actual} '
                        f'but duration = {act.duration:.2f} h'),
                severity='error',
                excess=delta_h,
            ))


def _check_precedence(pert: 'Pert',
                      violations: list, warnings: list) -> None:
    """Every predecessor must finish (+ lag) before its successor starts."""
    name_map = {a.name: a for a in pert.forwardDict}
    completed_set = pert._completed_set

    for pred in pert.forwardDict:
        _, pred_et = pred.returnAbsTimes()
        if pred_et is None:
            continue
        lags = getattr(pred, 'successor_lags', {})
        for succ in pert.forwardDict.get(pred, []):
            succ_st, _ = succ.returnAbsTimes()
            if succ_st is None:
                continue
            lag_h  = lags.get(succ.name, 0.0)
            latest_start = pred_et + timedelta(hours=lag_h)
            if succ_st < latest_start - _PREC_TOL:
                excess_h = (latest_start - succ_st).total_seconds() / 3600.0
                lag_note = f' (lag={lag_h:.1f} h)' if lag_h else ''
                violations.append(Violation(
                    type='precedence',
                    activity=f'{pred.name} → {succ.name}',
                    detail=(f'successor starts at {succ_st} before '
                            f'predecessor finishes at {pred_et}{lag_note}'),
                    severity='error',
                    excess=excess_h,
                ))


def _check_time_windows(pert: 'Pert',
                        violations: list, warnings: list) -> None:
    """Activities must start and finish within their allowed time windows."""
    for act in pert.completed:
        windows = pert._resolve_windows(act)
        if not windows:
            continue
        st, et = act.returnAbsTimes()
        if st is None or et is None:
            continue
        start_h = (st - pert.startTime).total_seconds() / 3600.0
        end_h   = (et - pert.startTime).total_seconds() / 3600.0
        tol_h   = _PREC_TOL.total_seconds() / 3600.0

        # Activity must fit in at least one window
        fits = False
        for west_h, wlf_h in windows:
            if start_h >= west_h - tol_h and end_h <= wlf_h + tol_h:
                fits = True
                break

        if not fits:
            win_str = ', '.join(f'[{w[0]:.1f}h–{w[1]:.1f}h]' for w in windows)
            violations.append(Violation(
                type='time_window',
                activity=act.name,
                detail=(f'scheduled [{start_h:.1f}h–{end_h:.1f}h] '
                        f'does not fit any window: {win_str}'),
                severity='error',
            ))


def _check_hold_points(pert: 'Pert',
                       violations: list, warnings: list) -> None:
    """No blocked task may start before its hold-point activity completes."""
    name_map = {a.name: a for a in pert.forwardDict}
    for act in pert.completed:
        if not act.is_hold_point:
            continue
        _, hp_et = act.returnAbsTimes()
        if hp_et is None:
            continue
        for blocked_name in act.blocks_tasks:
            blocked = name_map.get(blocked_name)
            if blocked is None:
                continue
            b_st, _ = blocked.returnAbsTimes()
            if b_st is None:
                continue
            if b_st < hp_et - _PREC_TOL:
                excess_h = (hp_et - b_st).total_seconds() / 3600.0
                violations.append(Violation(
                    type='hold_point',
                    activity=f'{act.name} → {blocked_name}',
                    detail=(f'blocked task starts at {b_st} before '
                            f'hold point releases at {hp_et}'),
                    severity='error',
                    excess=excess_h,
                ))


def _check_crew_feasibility(pert: 'Pert',
                            violations: list, warnings: list) -> None:
    """Crew demand must never exceed pool availability at any instant."""
    if not pert.crew_pool:
        return

    # Build sweep events per skill: (time, delta, activity_name)
    # Ends sort before starts at same timestamp (half-open interval [st, et))
    events_by_skill: dict[str, list] = defaultdict(list)

    for act in pert.completed:
        st, et = act.returnAbsTimes()
        if st is None or et is None:
            continue
        for skill, workers in _crew_demand(act).items():
            if workers <= 0:
                continue
            events_by_skill[skill].append((st,  +1, workers, act.name))
            events_by_skill[skill].append((et,  -1, workers, act.name))

    for skill, events in events_by_skill.items():
        # Sort: earlier time first; at same time ends (-1) before starts (+1)
        events.sort(key=lambda e: (e[0], e[1]))
        current_demand = 0
        reported_times: set = set()

        for t, sign, workers, name in events:
            current_demand += sign * workers
            if sign == +1:   # check after a start event
                avail = pert.crew_pool.get_availability(skill, t)
                if avail > 0 and current_demand > avail:
                    if t not in reported_times:
                        reported_times.add(t)
                        excess = current_demand - avail
                        violations.append(Violation(
                            type='crew',
                            activity=skill,
                            detail=(f'demand={current_demand} exceeds '
                                    f'availability={avail} at {t}'),
                            severity='error',
                            excess=float(excess),
                        ))


def _check_equipment_feasibility(pert: 'Pert',
                                 violations: list, warnings: list) -> None:
    """Equipment demand must never exceed pool availability at any instant."""
    if not pert.equipment_pool:
        return

    events_by_eq: dict[str, list] = defaultdict(list)

    for act in pert.completed:
        st, et = act.returnAbsTimes()
        if st is None or et is None:
            continue
        for eq_id, qty in _eq_demand(act).items():
            if qty <= 0:
                continue
            events_by_eq[eq_id].append((st, +1, qty, act.name))
            events_by_eq[eq_id].append((et, -1, qty, act.name))

    for eq_id, events in events_by_eq.items():
        events.sort(key=lambda e: (e[0], e[1]))
        current_demand = 0
        reported_times: set = set()

        for t, sign, qty, name in events:
            current_demand += sign * qty
            if sign == +1:
                avail = pert.equipment_pool.get_availability(eq_id, t)
                if avail > 0 and current_demand > avail:
                    if t not in reported_times:
                        reported_times.add(t)
                        excess = current_demand - avail
                        violations.append(Violation(
                            type='equipment',
                            activity=eq_id,
                            detail=(f'demand={current_demand} exceeds '
                                    f'availability={avail} at {t}'),
                            severity='error',
                            excess=float(excess),
                        ))


def _check_location_feasibility(pert: 'Pert',
                                violations: list, warnings: list) -> None:
    """Location concurrency limits must never be exceeded (tasks and workers)."""
    if not pert.location_pool:
        return

    for loc_id in pert.location_pool.get_all_location_ids():
        # Build events: each activity contributes (time, delta_tasks, delta_workers)
        events: list = []
        for act in pert.completed:
            if loc_id not in act.getZoneIds():
                continue
            st, et = act.returnAbsTimes()
            if st is None or et is None:
                continue
            # worker count = total crew demand across all skills for this activity
            worker_count = sum(_crew_demand(act).values())
            events.append((st, +1, +worker_count, act.name))
            events.append((et, -1, -worker_count, act.name))

        if not events:
            continue

        events.sort(key=lambda e: (e[0], e[1]))
        current_tasks = 0
        current_workers = 0
        reported_times: set = set()

        for t, delta_tasks, delta_workers, name in events:
            current_tasks += delta_tasks
            current_workers += delta_workers
            if delta_tasks == +1:   # check on each start event
                cap = pert.location_pool.get_capacity(loc_id, t)
                max_tasks   = cap.get('max_tasks', cap.get('max_concurrent_tasks', 9999))
                max_workers = cap.get('max_workers')

                if max_tasks and current_tasks > max_tasks:
                    if (t, 'tasks') not in reported_times:
                        reported_times.add((t, 'tasks'))
                        violations.append(Violation(
                            type='location',
                            activity=loc_id,
                            detail=(f'concurrent tasks={current_tasks} exceeds '
                                    f'max_concurrent_tasks={max_tasks} at {t}'),
                            severity='error',
                            excess=float(current_tasks - max_tasks),
                        ))

                if max_workers is not None and current_workers > max_workers:
                    if (t, 'workers') not in reported_times:
                        reported_times.add((t, 'workers'))
                        violations.append(Violation(
                            type='location',
                            activity=loc_id,
                            detail=(f'concurrent workers={current_workers} exceeds '
                                    f'max_concurrent_workers={max_workers} at {t}'),
                            severity='error',
                            excess=float(current_workers - max_workers),
                        ))


def _check_consumables(pert: 'Pert',
                       violations: list, warnings: list) -> None:
    """Consumable inventory must never go negative when replaying the schedule.

    Uses a deep-copy of the pool reset to initial quantities so that the live
    pool state is never modified.  Activities are processed in start-time order;
    restocks are applied lazily as the replay cursor advances.
    """
    if not pert.consumable_pool:
        return
    pool = pert.consumable_pool
    if not pool.items:
        return

    # Work on a reset copy so the live pool is untouched
    sim = copy.deepcopy(pool)
    sim.reset()

    # Sort completed activities by start time
    acts_with_times = []
    for act in pert.completed:
        st, _ = act.returnAbsTimes()
        if st is None:
            continue
        consumables = act.getRequiredConsumables()
        if not consumables:
            continue
        acts_with_times.append((st, act, consumables))
    acts_with_times.sort(key=lambda x: x[0])

    for st, act, consumables in acts_with_times:
        hour = (st - pert.startTime).total_seconds() / 3600.0
        sim.apply_restocks_up_to(hour)
        for req in consumables:
            item_id = req['item_id']
            qty = float(req.get('quantity_needed', 0))
            if qty <= 0 or not sim.has_item(item_id):
                continue
            available = sim.get_remaining(item_id)
            if available < qty - 1e-9:
                shortage = qty - available
                violations.append(Violation(
                    type='consumable',
                    activity=act.name,
                    detail=(f'item {item_id!r}: needs {qty:.2f} but only '
                            f'{available:.2f} remaining at hour {hour:.1f}'),
                    severity='error',
                    excess=shortage,
                ))
            # Always deduct (even if overdrawn) to propagate shortfall
            sim.consume(item_id, qty)


def _check_equipment_zone_affinity(pert: 'Pert',
                                   violations: list, warnings: list) -> None:
    """Equipment with a zone_id must only be used by activities in that zone."""
    if not pert.equipment_pool:
        return

    for act in pert.completed:
        st, _ = act.returnAbsTimes()
        if st is None:
            continue
        act_zones = set(act.getZoneIds())
        if not act_zones:
            continue  # unconstrained activity — no zone check needed
        for req in act.getRequiredEquipment():
            eq_id = req['equipment_id']
            zone_id = pert.equipment_pool.get_zone_id(eq_id)
            if zone_id is None:
                continue  # unconstrained equipment — no zone check needed
            if zone_id not in act_zones:
                violations.append(Violation(
                    type='equipment_zone',
                    activity=act.name,
                    detail=(f'equipment {eq_id!r} is zone-locked to {zone_id!r} '
                            f'but activity zones are {sorted(act_zones) or ["(none)"]}'),
                    severity='error',
                ))


def _check_shift_calendar(pert: 'Pert',
                          violations: list, warnings: list) -> None:
    """Activities must only execute during scheduled shift hours.

    Checks that each activity's start and end fall within the repeating daily
    shift window ``[shift_start_hour, shift_start_hour + working_hours_per_day)``.
    Skipped when ``working_hours_per_day >= 24`` (continuous operations).
    """
    wpd = getattr(pert, 'working_hours_per_day', 24)
    if wpd is None or wpd >= 24:
        return  # 24-h operations — no shift constraint

    shift_start = getattr(pert, 'shift_start_hour', 0) or 0
    shift_end   = shift_start + wpd   # within-day offset (may exceed 24 if crosses midnight)

    for act in pert.completed:
        st, et = act.returnAbsTimes()
        if st is None or et is None or pert.startTime is None:
            continue
        # Absolute clock hour-of-day (matches _is_work_time logic).
        # Using project-offset % 24 is wrong when the outage starts mid-day:
        # an activity at offset=0h would show hour-of-day=0.0 instead of the
        # actual clock hour, causing false violations for outages that begin
        # partway through a day.
        start_hod = st.hour + st.minute / 60.0 + st.second / 3600.0
        end_hod   = et.hour + et.minute / 60.0 + et.second / 3600.0

        # Activities with zero duration are instantaneous — skip end check
        duration_h = (et - st).total_seconds() / 3600.0

        outside = False
        detail_parts = []

        if start_hod < shift_start - 1e-6 or start_hod > shift_end + 1e-6:
            outside = True
            detail_parts.append(
                f'starts at day-hour {start_hod:.2f} outside shift '
                f'[{shift_start}h–{shift_end}h]'
            )
        if duration_h > 1e-6 and (end_hod < shift_start - 1e-6 or end_hod > shift_end + 1e-6):
            # Allow end exactly at shift boundary (end == shift_end is valid)
            if abs(end_hod - shift_end) > 1e-6:
                outside = True
                detail_parts.append(
                    f'ends at day-hour {end_hod:.2f} outside shift '
                    f'[{shift_start}h–{shift_end}h]'
                )

        if outside:
            violations.append(Violation(
                type='shift_calendar',
                activity=act.name,
                detail='; '.join(detail_parts),
                severity='error',
            ))


def _check_dose_budgets(pert: 'Pert',
                        violations: list, warnings: list) -> None:
    """Cumulative dose committed during the schedule must not exceed budgets."""
    if not pert.dose_trackers:
        return

    for skill, tracker in pert.dose_trackers.items():
        if tracker.total_budget_mrem <= 0:
            continue
        if tracker.consumed_mrem > tracker.total_budget_mrem + 1e-6:
            excess = tracker.consumed_mrem - tracker.total_budget_mrem
            violations.append(Violation(
                type='dose',
                activity=skill,
                detail=(f'consumed={tracker.consumed_mrem:.1f} mRem exceeds '
                        f'budget={tracker.total_budget_mrem:.1f} mRem'),
                severity='error',
                excess=excess,
            ))
        elif tracker.consumed_mrem > 0.9 * tracker.total_budget_mrem:
            pct = 100.0 * tracker.consumed_mrem / tracker.total_budget_mrem
            warnings.append(Violation(
                type='dose',
                activity=skill,
                detail=f'dose budget {pct:.1f}% consumed ({tracker.consumed_mrem:.1f}/{tracker.total_budget_mrem:.1f} mRem)',
                severity='warning',
            ))


def _check_system_states(pert: 'Pert',
                         violations: list, warnings: list) -> None:
    """No two simultaneously active activities may require incompatible system states."""
    if not pert.system_state_pool:
        return

    # Only consider activities that declare system-state requirements
    state_acts = [
        (act, act.returnAbsTimes())
        for act in pert.completed
        if act.getRequiredSystemStates()
    ]

    for i, (a1, (st1, et1)) in enumerate(state_acts):
        if st1 is None or et1 is None:
            continue
        states1 = {req['system_id']: req['required_state']
                   for req in a1.getRequiredSystemStates()}

        for a2, (st2, et2) in state_acts[i + 1:]:
            if st2 is None or et2 is None:
                continue
            # Half-open interval overlap
            if st1 >= et2 or st2 >= et1:
                continue
            states2 = {req['system_id']: req['required_state']
                       for req in a2.getRequiredSystemStates()}

            for sys_id, state1 in states1.items():
                state2 = states2.get(sys_id)
                if state2 is not None and state2 != state1:
                    violations.append(Violation(
                        type='system_state',
                        activity=f'{a1.name} ‖ {a2.name}',
                        detail=(f'system {sys_id}: {a1.name} requires '
                                f'{state1!r} but {a2.name} requires {state2!r} '
                                f'during overlap [{max(st1,st2)}, {min(et1,et2)})'),
                        severity='error',
                    ))


# ===========================================================================
# Quality warnings (non-fatal)
# ===========================================================================

def _check_schedule_quality(pert: 'Pert',
                            violations: list, warnings: list) -> None:
    """Soft quality indicators: delay, float consumption, window violations."""

    # 1. Delay — activities that waited for resources
    total_delay_h = 0.0
    delayed = []
    for act in pert.completed:
        if act.delay and act.delay > 0.0:
            total_delay_h += act.delay
            delayed.append((act.name, act.delay))

    if delayed:
        delayed.sort(key=lambda x: x[1], reverse=True)
        top = ', '.join(f'{n}({d:.1f}h)' for n, d in delayed[:5])
        warnings.append(Violation(
            type='quality',
            activity='schedule',
            detail=(f'total resource-wait delay={total_delay_h:.1f} h across '
                    f'{len(delayed)} activities. Top: {top}'),
            severity='warning',
            excess=total_delay_h,
        ))

    # 2. Float consumption — activities that started later than CPM early start
    if pert.infoDict and pert.startTime:
        late_starts = []
        for act in pert.completed:
            st, _ = act.returnAbsTimes()
            if st is None:
                continue
            info = pert.infoDict.get(act)
            if info is None:
                continue
            cpm_es_abs = pert.startTime + timedelta(hours=info.get('es', 0.0))
            consumed_h = (st - cpm_es_abs).total_seconds() / 3600.0
            if consumed_h > 1.0:   # more than 1 hour beyond CPM early start
                late_starts.append((act.name, consumed_h))
        if late_starts:
            late_starts.sort(key=lambda x: x[1], reverse=True)
            top = ', '.join(f'{n}({h:.1f}h)' for n, h in late_starts[:5])
            warnings.append(Violation(
                type='quality',
                activity='schedule',
                detail=(f'{len(late_starts)} activities started beyond CPM early start. '
                        f'Top float consumers: {top}'),
                severity='warning',
                excess=late_starts[0][1] if late_starts else 0.0,
            ))

    # 3. Propagate existing window violation log
    for wv in getattr(pert, '_window_violations', []):
        warnings.append(Violation(
            type='time_window',
            activity=wv.get('activity', '?'),
            detail=(f"window missed: current_h={wv.get('current_hours', '?'):.1f}, "
                    f"duration={wv.get('duration_hours', '?'):.1f}h, "
                    f"windows={wv.get('windows', [])}"),
            severity='warning',
        ))

    # 4. Makespan vs CPM duration
    if pert.startTime and pert.completed:
        cpm_dur = pert.getProjectDuration()
        actual_end = pert.get_project_finish_actual()
        if actual_end and cpm_dur:
            actual_dur_h = (actual_end - pert.startTime).total_seconds() / 3600.0
            stretch = actual_dur_h - cpm_dur
            if stretch > 0.5:
                warnings.append(Violation(
                    type='quality',
                    activity='schedule',
                    detail=(f'actual makespan={actual_dur_h:.1f} h vs '
                            f'CPM bound={cpm_dur:.1f} h '
                            f'(+{stretch:.1f} h resource-induced stretch)'),
                    severity='warning',
                    excess=stretch,
                ))


# ===========================================================================
# Public entry point
# ===========================================================================

def validate_schedule(pert: 'Pert') -> ValidationResult:
    """Run all post-schedule feasibility checks on *pert*.

    Should be called after ``calculateScheduleWithResources()`` or
    ``calculateScheduleWithResources_from()``.  Safe to call on a Pert instance
    that has not been scheduled yet — returns a single completeness violation.

    Args:
        pert: A fully initialised and (ideally) scheduled Pert instance.

    Returns:
        ValidationResult with .is_feasible, .violations, .warnings, .summary()
    """
    violations: list[Violation] = []
    warnings:   list[Violation] = []

    _check_completeness(pert,              violations, warnings)
    _check_durations(pert,                 violations, warnings)
    _check_precedence(pert,                violations, warnings)
    _check_time_windows(pert,              violations, warnings)
    _check_hold_points(pert,               violations, warnings)
    _check_crew_feasibility(pert,          violations, warnings)
    _check_equipment_feasibility(pert,     violations, warnings)
    _check_equipment_zone_affinity(pert,   violations, warnings)
    _check_location_feasibility(pert,      violations, warnings)
    _check_consumables(pert,               violations, warnings)
    _check_shift_calendar(pert,            violations, warnings)
    _check_dose_budgets(pert,              violations, warnings)
    _check_system_states(pert,             violations, warnings)
    _check_schedule_quality(pert,          violations, warnings)

    return ValidationResult(
        is_feasible=len(violations) == 0,
        violations=violations,
        warnings=warnings,
    )
