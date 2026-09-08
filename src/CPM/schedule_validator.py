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
    substitution      Committed crew breakdown is not a legal/conserving resolution
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
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .pert import Pert

# Floating-point / scheduling tolerance for time comparisons.
# _PREC_TOL is the quantization grace on precedence / window / hold-point
# checks: actual start/end times are accumulated microsecond-quantized
# `timedelta`s, so a genuinely-satisfied constraint can miss by a few
# microseconds of float<->timedelta rounding.  It is kept at the SAME scale as
# the engine's Pert._EVENT_EPSILON (1 ms) so the oracle tolerates only that
# quantization noise — not a real sub-minute precedence violation.  It was
# 60 s, which masked exactly such violations: a 56.25 s successor overlap read
# as feasible (RCPSP_ROBUSTNESS_2026-09-07.md §9).
_PREC_TOL   = timedelta(milliseconds=1)   # quantization grace; matches Pert._EVENT_EPSILON
_DUR_TOL    = timedelta(seconds=60)   # 1-minute grace for duration consistency


# ===========================================================================
# Data structures
# ===========================================================================

@dataclass
class Violation:
    """
    Single constraint breach or quality warning.

    Attributes
    ----------
    type : str
        Violation category (see the module docstring).
    activity : str
        Activity name, or ``'a → b'`` for pairwise checks.
    detail : str
        Human-readable description.
    severity : str
        Either ``'error'`` or ``'warning'``.
    excess : float
        Quantitative excess (hours, workers, mRem, …).  Defaults to ``0.0``.
    """
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
    """
    Aggregated result of ``validate_schedule()``.

    Attributes
    ----------
    is_feasible : bool
        ``True`` only when the ``violations`` list is empty.
    violations : list of Violation
        Hard constraint breaches.
    warnings : list of Violation
        Soft quality issues.
    """
    is_feasible: bool
    violations:  List[Violation] = field(default_factory=list)
    warnings:    List[Violation] = field(default_factory=list)

    def summary(self) -> str:
        """
        Build a human-readable multi-line validation report.

        Returns
        -------
        str
            Report listing the feasibility status, violation and warning
            counts, and the individual violation and warning messages.
        """
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
    """
    Return the crew demand of an activity, preferring the actual assignment.

    Parameters
    ----------
    act : Activity
        The activity to inspect.

    Returns
    -------
    dict
        Mapping of ``skill_type`` to worker count.  Uses ``_actual_resources``
        when the scheduler has committed an assignment, otherwise the
        activity's declared ``required_resources``.
    """
    actual = getattr(act, '_actual_resources', None)
    if actual:
        return dict(actual)
    return {req['skill_type']: req['crew_count']
            for req in act.getRequiredResources()}


def _eq_demand(act) -> dict:
    """
    Return the equipment demand of an activity.

    Parameters
    ----------
    act : Activity
        The activity to inspect.

    Returns
    -------
    dict
        Mapping of ``equipment_id`` to the quantity needed.
    """
    return {req['equipment_id']: req['quantity_needed']
            for req in act.getRequiredEquipment()}


# ---------------------------------------------------------------------------
# Gap-1 decoupling (ORACLE_COMPLETENESS_2026-09-07.md §6.4)
#
# The oracle used to answer "how much crew / equipment / location capacity is
# available over [start, end)?" and "what are this activity's time windows?"
# by calling the *engine's own* primitives
# (``crew_pool.get_availability_in_range``,
# ``equipment_pool.get_availability_in_range``,
# ``location_pool.get_capacity_in_range``, ``Pert._resolve_windows``).  That
# made a bug *inside* one of those primitives invisible to the oracle: it asked
# the engine the same question and got back the same wrong answer — a
# correlated blind spot.  The helpers below reimplement the min-over-overlap
# reduction and the window resolution directly from the raw declared data (the
# availability objects' ``.periods`` and the activity's window fields).  The
# scheduler never mutates those periods on any scheduled path
# (``update_from_hour`` / ``snapshot`` / ``restore`` are replan-only APIs, never
# called from pert.py), so the oracle still validates against exactly the
# availability the engine used, but a buggy engine primitive can no longer hide
# an infeasibility.  Each helper is an exact mirror of its engine counterpart
# on correct/pristine data (see the referenced outage_data.py / pert.py lines),
# so this change is behaviour-preserving on every feasible schedule.
# ---------------------------------------------------------------------------

def _min_avail_over(periods, count_key: str,
                    start: datetime, end: datetime) -> int:
    """
    Minimum availability over the half-open range ``[start, end)``.

    Independent reimplementation of the crew / equipment availability reduction
    (``ResourceAvailability.get_availability_in_range`` and
    ``EquipmentAvailability.get_availability_in_range`` in outage_data.py): the
    minimum ``count_key`` value across every period that overlaps the range, or
    ``0`` when no period overlaps (or ``periods`` is empty).

    Parameters
    ----------
    periods : list of dict
        Raw availability periods (``ResourceAvailability.get_all_periods()`` or
        ``EquipmentAvailability.get_all_periods()``), each carrying
        ``start_date``, ``end_date`` and the ``count_key`` count.
    count_key : str
        Period key holding the count — ``'available_count'`` for crew,
        ``'quantity_available'`` for equipment.
    start : datetime
        Range start (inclusive).
    end : datetime
        Range end (exclusive).

    Returns
    -------
    int
        Minimum count over overlapping periods, else ``0``.
    """
    min_avail = float('inf')
    for period in periods:
        ps, pe = period['start_date'], period['end_date']
        if ps < end and start < pe:   # half-open overlap: [ps, pe) ∩ [start, end)
            min_avail = min(min_avail, period[count_key])
    return min_avail if min_avail != float('inf') else 0


def _min_location_cap_over(periods, start: datetime, end: datetime) -> dict:
    """
    Minimum location capacity over the half-open range ``[start, end)``.

    Independent reimplementation of ``LocationAvailability.get_capacity_in_range``
    (outage_data.py): the minimum ``max_concurrent_tasks`` and — where any
    overlapping period declares one — ``max_concurrent_workers`` across every
    overlapping period.  ``max_workers`` is ``None`` when no overlapping period
    declares a worker limit (an unconstrained worker dimension), mirroring the
    engine's ``seen_workers_limit`` logic exactly.

    Parameters
    ----------
    periods : list of dict
        Raw location periods (``LocationAvailability.get_all_periods()``), each
        carrying ``start_date``, ``end_date``, ``max_concurrent_tasks`` and an
        optional ``max_concurrent_workers`` (``None`` = no worker limit).
    start : datetime
        Range start (inclusive).
    end : datetime
        Range end (exclusive).

    Returns
    -------
    dict
        ``{'max_tasks': int, 'max_workers': int | None}`` — ``max_tasks`` is
        ``0`` when no period overlaps; ``max_workers`` is ``None`` when no
        overlapping period constrains workers.
    """
    min_tasks   = float('inf')
    min_workers = float('inf')
    seen_workers_limit = False
    for period in periods:
        ps, pe = period['start_date'], period['end_date']
        if ps < end and start < pe:   # half-open overlap
            min_tasks = min(min_tasks, period['max_concurrent_tasks'])
            worker_limit = period.get('max_concurrent_workers')
            if worker_limit is not None:
                seen_workers_limit = True
                min_workers = min(min_workers, worker_limit)
    return {
        'max_tasks': min_tasks if min_tasks != float('inf') else 0,
        'max_workers': (min_workers
                        if (min_workers != float('inf') and seen_workers_limit)
                        else None),
    }


def _resolve_windows_indep(act) -> list:
    """
    Independent resolution of an activity's time windows.

    Exact mirror of ``Pert._resolve_windows`` (pert.py): returns a list of
    ``(earliest_h, latest_h)`` float tuples in project-hours.  Prefers the
    multi-window ``act.time_windows`` list; otherwise falls back to the legacy
    single-window scalar fields ``window_earliest_start_hours`` /
    ``window_latest_finish_hours`` (defaulting an absent earliest to ``0.0`` and
    an absent latest to ``inf``); returns ``[]`` when the activity declares no
    window constraint.

    Parameters
    ----------
    act : Activity
        Activity whose window constraints are resolved.

    Returns
    -------
    list of tuple of float
        One ``(earliest_h, latest_h)`` tuple per window; empty when the activity
        is unconstrained.
    """
    tw = getattr(act, 'time_windows', [])
    if tw:
        return [(float(w['earliest']), float(w['latest'])) for w in tw]
    west = getattr(act, 'window_earliest_start_hours', None)
    wlf  = getattr(act, 'window_latest_finish_hours',  None)
    if west is not None or wlf is not None:
        return [(
            float(west) if west is not None else 0.0,
            float(wlf)  if wlf  is not None else float('inf'),
        )]
    return []


# ---------------------------------------------------------------------------
# Gap-1 decoupling, touch-point 5 (ORACLE_COMPLETENESS_2026-09-07.md §6.4)
#
# The crew sweep reads each activity's committed substitution breakdown
# ``act._actual_resources`` ({skill: workers}) as its per-skill demand.  That
# breakdown is a *scheduling decision* — which legal skill the engine drew from
# — so the oracle cannot re-derive it independently (two legal breakdowns can
# differ).  But the oracle used to trust it *blindly*, which hid two engine
# bugs:
#
#   * non-conservation — the recorded workers sum to less than the declared
#     demand (a substitution the engine forgot to record); the sweep then
#     validates against a too-low demand and misses a real over-allocation, and
#   * illegal substitution — a skill is charged that no requirement legally
#     allows (not its primary ``skill_type``, not in ``alternative_skill_types``);
#     the declared requirement was never legally satisfiable, yet the sweep
#     validates against the illegal skills and passes.
#
# The check below (``_check_substitution_legality``) closes both by
# *independently verifying* — from the declared ``required_resources`` only,
# never an engine primitive — that the recorded breakdown is a legal,
# demand-conserving resolution of the requirements.  This is the right
# independence boundary: read the committed split (raw observable state, like
# ``.periods``) but certify its validity rather than assume it.  On every
# correct engine schedule the resolution is legal and conserving by construction
# (an activity only starts once its full demand is met from the primary skill
# plus declared alternatives), so the check is silent — behaviour-preserving,
# like the availability/window decoupling above.
#
# Legality is a bipartite (skill -> requirement) feasibility, NOT a per-skill
# membership test: allowed skill sets can overlap across requirements, so a
# breakdown can charge only "allowed" skills and still admit no legal routing
# (e.g. required [{MECH,1},{ELEC,1}] with recorded {MECH:2} — MECH is allowed
# and the total matches, yet the ELEC requirement is unfillable).  A max-flow
# saturating both sides is the exact test.
# ---------------------------------------------------------------------------

def _allowed_skills(req) -> set:
    """
    Skills that may legally staff a requirement: its primary plus alternatives.

    Parameters
    ----------
    req : dict
        One ``required_resources`` entry: ``{'skill_type': str, 'crew_count':
        int, 'alternative_skill_types': [str, ...]}`` (alternatives optional).

    Returns
    -------
    set of str
        ``{skill_type} ∪ alternative_skill_types``.
    """
    return {req['skill_type']} | set(req.get('alternative_skill_types', []))


def _bipartite_saturates(supply: dict, demand: list, allowed: list) -> bool:
    """
    Can the recorded per-skill ``supply`` exactly staff every requirement?

    Feasibility of a transportation problem on a tiny bipartite graph — skills
    on one side, requirements on the other — solved by max-flow: a source feeds
    each skill up to ``supply[skill]``; each skill connects (uncapped) to every
    requirement it may legally staff; each requirement drains ``demand[i]`` to a
    sink.  The assignment is legal iff the max-flow saturates the sink — i.e.
    every requirement's ``crew_count`` is met using only allowed skills without
    drawing more of any skill than was recorded.

    A per-skill membership test is *not* sufficient: allowed sets can overlap
    across requirements, so an all-"allowed" breakdown may still admit no legal
    routing.  Graphs here are tiny (a handful of skills and requirements), so a
    plain BFS-augmenting (Edmonds–Karp) max-flow is more than fast enough.

    Parameters
    ----------
    supply : dict
        ``{skill: workers}`` recorded for the activity (the flow available from
        each skill).  Zero or absent counts contribute no flow.
    demand : list of int
        ``crew_count`` per requirement, index-aligned with ``allowed``.
    allowed : list of set
        Per-requirement set of legally-usable skills, index-aligned with
        ``demand`` (see :func:`_allowed_skills`).

    Returns
    -------
    bool
        ``True`` iff a feasible assignment saturating every requirement exists.
    """
    total_demand = sum(demand)
    if total_demand == 0:
        return True
    # Node ids: 0 = source, 1..S = skills, then R requirements, sink last.
    skills = [s for s, w in supply.items() if w > 0]
    skill_idx = {s: 1 + i for i, s in enumerate(skills)}
    n_skills = len(skills)
    n_reqs = len(demand)
    src = 0
    req_base = 1 + n_skills
    sink = req_base + n_reqs
    n = sink + 1
    INF = total_demand   # no edge need carry more than the whole demand
    cap = [[0] * n for _ in range(n)]
    for s in skills:
        cap[src][skill_idx[s]] = supply[s]
    for j in range(n_reqs):
        cap[req_base + j][sink] = demand[j]
        for s in skills:
            if s in allowed[j]:
                cap[skill_idx[s]][req_base + j] = INF
    # Edmonds–Karp: repeatedly augment along a BFS shortest path.
    max_flow = 0
    while True:
        parent = [-1] * n
        parent[src] = src
        q = deque([src])
        while q:
            u = q.popleft()
            for v in range(n):
                if parent[v] == -1 and cap[u][v] > 0:
                    parent[v] = u
                    if v == sink:
                        q.clear()
                        break
                    q.append(v)
        if parent[sink] == -1:
            break
        bottleneck = INF
        v = sink
        while v != src:
            u = parent[v]
            bottleneck = min(bottleneck, cap[u][v])
            v = u
        v = sink
        while v != src:
            u = parent[v]
            cap[u][v] -= bottleneck
            cap[v][u] += bottleneck
            v = u
        max_flow += bottleneck
    return max_flow == total_demand


def _substitution_is_feasible(required, actual) -> bool:
    """
    Is a recorded substitution breakdown a legal, demand-conserving resolution?

    Independent verification (declared data only) that ``actual`` — the engine's
    committed ``{skill: workers}`` for an activity — is a valid resolution of the
    activity's declared ``required_resources``: the recorded workers must (a)
    **conserve demand** (sum to the declared total ``crew_count``) and (b) admit
    a **legal assignment** to the requirements using only each requirement's
    allowed skills (:func:`_bipartite_saturates`).

    Parameters
    ----------
    required : list of dict
        The activity's declared ``required_resources``.
    actual : dict
        The engine-committed ``{skill: workers}`` breakdown
        (``Activity._actual_resources``).

    Returns
    -------
    bool
        ``True`` iff ``actual`` conserves the declared demand and is legally
        assignable; ``False`` for any non-conserving or illegal breakdown.
    """
    demand = [int(r['crew_count']) for r in required]
    if sum(int(w) for w in actual.values()) != sum(demand):
        return False
    allowed = [_allowed_skills(r) for r in required]
    return _bipartite_saturates(actual, demand, allowed)


# ===========================================================================
# Individual check functions
# ===========================================================================

def _check_completeness(pert: 'Pert',
                        violations: list, warnings: list) -> None:
    """
    Check that all activities were scheduled with valid start and end times.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
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
    """
    Check that ``endTime − startTime`` matches ``activity.duration`` within tolerance.

    Activities that were in-progress at replan time have ``_remaining_duration``
    set and a stale ``endTime`` from before the replan.  Their duration field
    may also have been updated by a ``duration_override``.  These activities
    cannot be checked by a simple ``endTime − startTime == duration`` test, so
    they are skipped.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
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
    """
    Check that every predecessor finishes (plus lag) before its successor starts.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
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
    """
    Check that activities start and finish within their allowed time windows.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
    for act in pert.completed:
        # Gap 1 / §6.4: resolve windows independently of the engine primitive so
        # a bug in Pert._resolve_windows cannot hide a window violation.
        windows = _resolve_windows_indep(act)
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
    """
    Check that no blocked task starts before its hold-point activity completes.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
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
    """
    Check that crew demand never exceeds pool availability at any instant.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
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

        for idx, (t, sign, workers, name) in enumerate(events):
            current_demand += sign * workers
            # `current_demand` holds over [t, next_event_time).  Check the
            # MINIMUM availability across that whole interval — not just the
            # point value at t — so an availability drop that lands mid-interval
            # with no demand event at that instant is still caught (finding
            # C2b).  Every interval is checked (not only start events) because a
            # drop can bind on an interval that opens at an *end* event while
            # other activities keep demand high.
            nxt = events[idx + 1][0] if idx + 1 < len(events) else t
            # Ignore over-demand intervals narrower than the quantization
            # tolerance.  The engine's epsilon-tolerant completion gate
            # (pert.py ~5601) can complete a predecessor up to _EVENT_EPSILON
            # before its actual finish, so a resource-competing successor may
            # start a few microseconds before the predecessor frees the
            # resource — a sub-millisecond sliver that is pure float<->timedelta
            # noise, not a real concurrent over-allocation.  The precedence
            # check already tolerates the same slip via _PREC_TOL; without the
            # matching grace here the resource oracle would be stricter than the
            # precedence oracle and flag a phantom over-allocation on a plain
            # A→B chain (RCPSP_ROBUSTNESS_2026-09-07.md bug-family #5).  A
            # genuine overlap spans a meaningful fraction of an activity, far
            # above the 1 ms tolerance, so it is still caught.
            if (nxt - t) <= _PREC_TOL or current_demand <= 0:
                continue
            # Gap 1 / §6.4: recompute the minimum availability over [t, nxt)
            # from the skill's raw periods instead of calling
            # crew_pool.get_availability_in_range, so a bug in that reduction
            # cannot mask a crew over-allocation from the oracle.  A missing
            # skill has no availability (0), matching the pool method.
            ra = pert.crew_pool.resources.get(skill)
            avail = (_min_avail_over(ra.get_all_periods(), 'available_count', t, nxt)
                     if ra else 0)
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


def _check_substitution_legality(pert: 'Pert',
                                 violations: list, warnings: list) -> None:
    """
    Check that each activity's committed crew substitution is legal and conserving.

    For every completed activity carrying an engine-committed ``_actual_resources``
    breakdown, independently verify — against the *declared* ``required_resources``
    only, never an engine primitive — that the breakdown conserves the declared
    crew demand and assigns workers only to legally-usable skills
    (:func:`_substitution_is_feasible`).  A breakdown that under-records demand or
    charges a disallowed skill would otherwise slip past the crew sweep, which
    reads the same breakdown as its per-skill demand (Gap 1, touch-point 5 — see
    the comment block above :func:`_allowed_skills`).

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
    for act in pert.completed:
        actual = getattr(act, '_actual_resources', None)
        if not actual:
            # No committed substitution record → nothing to certify.  The crew
            # sweep uses the declared demand for such activities, which is
            # trivially legal.  (START/END and zero-crew activities land here.)
            continue
        required = act.getRequiredResources()
        if _substitution_is_feasible(required, actual):
            continue
        declared_total = sum(int(r['crew_count']) for r in required)
        recorded_total = sum(int(w) for w in actual.values())
        violations.append(Violation(
            type='substitution',
            activity=act.name,
            detail=(f'committed crew breakdown {dict(actual)} is not a legal, '
                    f'demand-conserving resolution of its requirements '
                    f'(recorded {recorded_total} workers vs declared '
                    f'{declared_total})'),
            severity='error',
            excess=float(abs(recorded_total - declared_total)),
        ))


def _check_equipment_feasibility(pert: 'Pert',
                                 violations: list, warnings: list) -> None:
    """
    Check that equipment demand never exceeds pool availability at any instant.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
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

        for idx, (t, sign, qty, name) in enumerate(events):
            current_demand += sign * qty
            # Minimum availability over [t, next_event_time) — see the crew
            # check above for the rationale (finding C2b).
            nxt = events[idx + 1][0] if idx + 1 < len(events) else t
            # Ignore over-demand intervals narrower than the quantization
            # tolerance.  The engine's epsilon-tolerant completion gate
            # (pert.py ~5601) can complete a predecessor up to _EVENT_EPSILON
            # before its actual finish, so a resource-competing successor may
            # start a few microseconds before the predecessor frees the
            # resource — a sub-millisecond sliver that is pure float<->timedelta
            # noise, not a real concurrent over-allocation.  The precedence
            # check already tolerates the same slip via _PREC_TOL; without the
            # matching grace here the resource oracle would be stricter than the
            # precedence oracle and flag a phantom over-allocation on a plain
            # A→B chain (RCPSP_ROBUSTNESS_2026-09-07.md bug-family #5).  A
            # genuine overlap spans a meaningful fraction of an activity, far
            # above the 1 ms tolerance, so it is still caught.
            if (nxt - t) <= _PREC_TOL or current_demand <= 0:
                continue
            # Gap 1 / §6.4: recompute from the item's raw periods instead of
            # equipment_pool.get_availability_in_range (see the crew check).
            # A missing equipment id has no availability (0), matching the pool.
            ea = pert.equipment_pool.equipment.get(eq_id)
            avail = (_min_avail_over(ea.get_all_periods(), 'quantity_available', t, nxt)
                     if ea else 0)
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
    """
    Check that location concurrency limits are never exceeded (tasks and workers).

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
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

        for idx, (t, delta_tasks, delta_workers, name) in enumerate(events):
            current_tasks += delta_tasks
            current_workers += delta_workers
            # Capacity constraints hold over [t, next_event_time); check the
            # MINIMUM capacity across that interval so a mid-interval capacity
            # drop is not missed (finding C2b).  Both dimensions are checked on
            # every interval rather than only on start events.
            nxt = events[idx + 1][0] if idx + 1 < len(events) else t
            # Ignore intervals narrower than the quantization tolerance — the
            # same sub-millisecond boundary sliver the crew/equipment sweeps
            # skip (see the note there and bug-family #5); a real concurrency
            # breach spans far more than 1 ms.
            if (nxt - t) <= _PREC_TOL:
                continue
            # Gap 1 / §6.4: recompute the minimum capacity over [t, nxt) from
            # the location's raw periods instead of
            # location_pool.get_capacity_in_range, so a bug in that reduction
            # cannot mask a concurrency breach.  loc_id is enumerated from the
            # pool above so it always exists; the fallback mirrors the pool's
            # unknown-location return just in case.
            la = pert.location_pool.locations.get(loc_id)
            cap = (_min_location_cap_over(la.get_all_periods(), t, nxt)
                   if la else {'max_tasks': 0, 'max_workers': None})
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
    """
    Check that consumable inventory never goes negative when replaying the schedule.

    Uses a deep-copy of the pool reset to initial quantities so that the live
    pool state is never modified.  Activities are processed in start-time order;
    restocks are applied lazily as the replay cursor advances.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
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
    """
    Check that zone-locked equipment is only used by activities in that zone.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
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
    """
    Check that activities only execute during scheduled shift hours.

    Checks that each activity's start and end fall within the repeating daily
    shift window ``[shift_start_hour, shift_start_hour + working_hours_per_day)``.
    Skipped when ``working_hours_per_day >= 24`` (continuous operations).

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
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
    """
    Check that cumulative dose committed during the schedule stays within budgets.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
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
    """
    Check that no two simultaneously active activities require incompatible states.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """
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
    """
    Record soft quality indicators: delay, float consumption, window violations.

    Reports resource-wait delay, activities that started beyond their CPM early
    start, propagated window-violation log entries, and makespan stretch over
    the CPM bound as warning-level :class:`Violation` objects.

    Parameters
    ----------
    pert : Pert
        The scheduled ``Pert`` instance to inspect.
    violations : list
        Accumulator for error-level :class:`Violation` objects.
    warnings : list
        Accumulator for warning-level :class:`Violation` objects.
    """

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
    """
    Run all post-schedule feasibility checks on ``pert``.

    Should be called after ``calculateScheduleWithResources()`` or
    ``calculateScheduleWithResources_from()``.  Safe to call on a Pert instance
    that has not been scheduled yet — returns a single completeness violation.

    Parameters
    ----------
    pert : Pert
        A fully initialised and (ideally) scheduled Pert instance.

    Returns
    -------
    ValidationResult
        Result exposing ``.is_feasible``, ``.violations``, ``.warnings`` and
        ``.summary()``.
    """
    violations: list[Violation] = []
    warnings:   list[Violation] = []

    _check_completeness(pert,              violations, warnings)
    _check_durations(pert,                 violations, warnings)
    _check_precedence(pert,                violations, warnings)
    _check_time_windows(pert,              violations, warnings)
    _check_hold_points(pert,               violations, warnings)
    _check_crew_feasibility(pert,          violations, warnings)
    _check_substitution_legality(pert,     violations, warnings)
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
