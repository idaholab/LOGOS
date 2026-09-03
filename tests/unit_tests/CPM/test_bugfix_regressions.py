"""
Regression tests pinning the pert.py bug fixes recorded in
``devLogs/CHANGELOG.md`` → "Bug Fixes — pert.py".

Why this file exists
--------------------
The full CPM suite is green, but a coverage-guided audit (2026-09-03, see
``devLogs/BRANCH_ASSESSMENT_2026-09-03.md``) showed that several of the
*already-fixed* bugs live on code paths that no existing test exercises.  A
green suite therefore did not actually prove those fixes stay in place — the
fix lines were dark.  Each test below drives one fix line directly, so a
regression flips a specific test red.

Bug → test map (numbering follows the CHANGELOG "Bug Fixes — pert.py" list):

  1. Lag support in ``check_dependency_violations``     → TestLagInDependencyCheck
  2. ``_effective_duration`` remaining-vs-full          → TestEffectiveDuration
  4. ``_build_augmented_graph`` None-pool guards        → TestAugmentedGraphNonePools
  5. Cycle detection in ``_longest_path_in_augmented``  → TestLongestPathCycleGuard

Bugs 3 (``_window_violations`` per-run isolation) and 6 (``_apply_tentative``
``eq_rem`` KeyError) are already covered by the replan/interaction and
scheduling tests respectively (see the assessment doc's bug-to-test
traceability table), so they are not duplicated here.
"""

import logging
import pytest
from datetime import datetime, timedelta

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    ResourcePool, EquipmentPool, LocationPool,
    ResourceAvailability, EquipmentAvailability, LocationAvailability,
)
from CPM.schedule_validator import (
    _check_crew_feasibility,
    _check_equipment_feasibility,
    _check_location_feasibility,
)


# ---------------------------------------------------------------------------
# Helpers (mirror the _build / _pools convention in test_boundary.py)
# ---------------------------------------------------------------------------

def _pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _build(fwd, start_time=None, pools=None):
    """Construct a Pert, attach empty pools + a start time.

    ``Pert.__init__`` already runs ``generateInfo`` when a graph is supplied,
    so no explicit call is needed here.
    """
    p = Pert(graph=fwd)
    rp, ep, lp = pools or _pools()
    p.crew_pool = rp
    p.equipment_pool = ep
    p.location_pool = lp
    p.startTime = start_time or datetime(2026, 1, 1)
    return p


# ===========================================================================
# Bug 2 — _effective_duration: remaining duration during replan
# ===========================================================================

class TestEffectiveDuration:
    """``_effective_duration`` must return the *remaining* duration for an
    in-progress activity during replanning, not the original planned duration
    (pert.py ~3429-3433).  Previously ``infoDict[v]['duration']`` always
    returned the full planned value, over-booking capacity for the already
    elapsed portion of a task."""

    def _pert(self):
        # The method reads only its activity argument; any valid graph works.
        s, e = Activity("START", 0.0), Activity("END", 0.0)
        return _build({s: [e], e: []})

    def test_pending_activity_returns_full_duration(self):
        p = self._pert()
        act = Activity("A", 10.0)
        assert p._effective_duration(act) == 10.0

    def test_in_progress_returns_remaining(self):
        p = self._pert()
        act = Activity("A", 10.0)
        act.status = "in_progress"
        act._remaining_duration = 3.0
        assert p._effective_duration(act) == 3.0

    def test_remaining_clamped_at_zero(self):
        p = self._pert()
        act = Activity("A", 10.0)
        act.status = "in_progress"
        act._remaining_duration = -2.0
        assert p._effective_duration(act) == 0.0

    def test_remaining_ignored_when_not_in_progress(self):
        """A stale ``_remaining_duration`` on a non-in-progress activity must
        not leak: the full planned duration wins."""
        p = self._pert()
        act = Activity("A", 10.0)
        act._remaining_duration = 3.0   # set, but status left at default
        assert p._effective_duration(act) == 10.0


# ===========================================================================
# Bug 1 — check_dependency_violations honours lag_dict
# ===========================================================================

class TestLagInDependencyCheck:
    """``check_dependency_violations`` must honour ``lag_dict``.  A successor
    that starts exactly at its predecessor's finish is a violation once a
    mandatory finish-to-start lag is declared (pert.py ~4952-4963).  The bug
    was that the method ignored ``lag_dict`` entirely."""

    def _scheduled_chain(self):
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        b = Activity("B", 3.0)
        end = Activity("END", 0.0)
        fwd = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd)
        p.calculateScheduleWithResources()
        return p, a, b

    def test_no_lag_is_feasible(self):
        p, _a, _b = self._scheduled_chain()
        violations, feasible = p.check_dependency_violations()
        assert feasible
        assert violations == []

    def test_injected_lag_is_detected(self):
        p, a, b = self._scheduled_chain()
        # B was scheduled immediately after A (no lag).  Declare a 2 h FS lag
        # after the fact — the recorded schedule now violates it.
        p.lag_dict[(a, b)] = 2.0
        violations, feasible = p.check_dependency_violations()
        assert not feasible
        assert len(violations) == 1
        v = violations[0]
        assert v['predecessor'] == 'A'
        assert v['successor'] == 'B'
        assert v['lag_hours'] == 2.0
        # B starts at A-end, so it is a full 2 h early relative to A-end + lag.
        assert v['overlap_hours'] == pytest.approx(2.0, abs=1e-6)

    def test_raises_before_scheduling(self):
        """The method requires a completed schedule (guards on
        ``self.completed``)."""
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        end = Activity("END", 0.0)
        p = _build({start: [a], a: [end], end: []})
        with pytest.raises(ValueError):
            p.check_dependency_violations()


# ===========================================================================
# Bug 4 — _build_augmented_graph tolerates missing pools
# ===========================================================================

class TestAugmentedGraphNonePools:
    """``_build_augmented_graph`` must tolerate a Pert with no
    location/crew/equipment pool (valid for unit fixtures) instead of raising
    ``AttributeError`` on ``pool.get_all_*()`` (pert.py ~5088-5091, 5121-5122)."""

    def test_no_pools_no_attributeerror(self):
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        b = Activity("B", 3.0)
        end = Activity("END", 0.0)
        fwd = {start: [a], a: [b], b: [end], end: []}

        p = Pert(graph=fwd)                 # pools deliberately left as None
        p.startTime = datetime(2026, 1, 1)
        # Precondition: the None-pool path is the one under test.
        assert p.location_pool is None
        assert p.crew_pool is None
        assert p.equipment_pool is None

        augmented = p._build_augmented_graph()   # must not raise

        assert isinstance(augmented, dict)
        # No pools → no resource-flow arcs → augmented is a copy of precedence.
        assert set(augmented.keys()) == set(fwd.keys())
        for node, succs in fwd.items():
            assert augmented[node] == succs


# ===========================================================================
# Bug 5 — cycle detection in _longest_path_in_augmented
# ===========================================================================

class TestLongestPathCycleGuard:
    """A resource-flow arc that closes a cycle leaves Kahn's sort short of the
    full node count.  ``_longest_path_in_augmented`` must detect this, log a
    warning, and still run the DP over *all* nodes rather than silently
    dropping the stuck ones (pert.py ~5229-5239)."""

    def _pert_and_nodes(self):
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        b = Activity("B", 3.0)
        c = Activity("C", 2.0)
        end = Activity("END", 0.0)
        fwd = {start: [a], a: [b], b: [c], c: [end], end: []}
        p = _build(fwd)   # forwardDict is a valid DAG (generateInfo is happy)
        return p, start, a, b, c, end

    def test_cycle_is_detected_and_logged(self, caplog):
        p, start, a, b, c, end = self._pert_and_nodes()
        # Corrupt a *separate* adjacency dict with a back-edge C→A so that
        # A→B→C→A forms a cycle; the Pert's own forwardDict stays acyclic.
        augmented = {k: list(v) for k, v in p.forwardDict.items()}
        augmented[c].append(a)

        with caplog.at_level(logging.WARNING, logger='CPM.pert'):
            path = p._longest_path_in_augmented(augmented)

        msgs = [rec.getMessage() for rec in caplog.records]
        assert any('cycle detected' in m.lower() for m in msgs), (
            f"Expected a 'cycle detected' warning; got: {msgs}"
        )
        # Must still return a path (list of Activities) rather than raising.
        assert isinstance(path, list)
        assert len(path) >= 1

    def test_acyclic_graph_logs_no_cycle_warning(self, caplog):
        """Control: a clean DAG must not trip the cycle guard."""
        p, start, a, b, c, end = self._pert_and_nodes()
        augmented = {k: list(v) for k, v in p.forwardDict.items()}

        with caplog.at_level(logging.WARNING, logger='CPM.pert'):
            path = p._longest_path_in_augmented(augmented)

        msgs = [rec.getMessage() for rec in caplog.records]
        assert not any('cycle detected' in m.lower() for m in msgs)
        # START → A → B → C → END is the unique longest path.
        assert [n.returnName() for n in path] == ['START', 'A', 'B', 'C', 'END']


# ===========================================================================
# Round 2 — Cluster 1: time-varying availability sampled at a single instant
# ---------------------------------------------------------------------------
# Findings C2 / C2b (devLogs/PERT_MANUAL_REVIEW_2026-09-03.md).  The scheduler's
# sparse capacity grid and the independent validator both read availability at
# one instant (the activity/demand *start*), so an availability DROP that lands
# inside an already-running activity is invisible — the scheduler over-commits
# and the validator fails to catch it.  The fix reinterprets each grid cell /
# demand interval as a *range* and checks the MINIMUM availability across it via
# the get_availability_in_range / get_capacity_in_range primitives that already
# live in outage_data.py.
#
# Repro seed: devLogs/repros/repro_timevarying.py
# ===========================================================================

_START = datetime(2026, 1, 1, 0, 0)
_FAR = _START + timedelta(days=365)


def _dropping_crew_pool(skill='MECH', high=4, low=2, drop_h=4):
    """MECH pool = `high` for [START, START+drop_h), then `low` forever."""
    rp = ResourcePool()
    rp.resources[skill] = ResourceAvailability(skill, [
        {'start_date': _START,
         'end_date': _START + timedelta(hours=drop_h),
         'available_count': high},
        {'start_date': _START + timedelta(hours=drop_h),
         'end_date': _FAR,
         'available_count': low},
    ])
    return rp


class TestTimeVaryingCrewNoOvercommit:
    """C2 — the scheduler must not admit an activity using availability that
    holds only at its start.  An activity needing 4 MECH for 6 h against a pool
    that steps 4→2 at hour 4 has *no* feasible 6 h window, so it must never be
    placed over an interval where its demand exceeds the minimum availability.

    Pre-fix: ``_build_capacity_snapshots`` seeded ``res_rem[MECH][0] = 4`` (the
    point value at the start), so the activity was admitted at t=0 and ran
    [0,6) consuming 4 while only 2 were available on [4,6)."""

    def _pert(self):
        start = Activity('START', 0.0)
        a = Activity('A', 6.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 4}])
        end = Activity('END', 0.0)
        p = _build({start: [a], a: [end], end: []})
        p.crew_pool = _dropping_crew_pool()
        return p, a

    def test_no_completed_activity_is_overcommitted(self):
        p, a = self._pert()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        # Core C2 invariant: every completed activity must be feasible across
        # its WHOLE run, not just at its start instant.
        for act in p.completed:
            s, e = act.returnAbsTimes()
            if s is None or e is None or e <= s:
                continue
            for req in act.getRequiredResources():
                skill, need = req['skill_type'], req['crew_count']
                if need <= 0:
                    continue
                min_avail = p.crew_pool.get_availability_in_range(skill, s, e)
                assert min_avail >= need, (
                    f"{act.returnName()} scheduled [{s},{e}) needs {need} {skill} "
                    f"but only {min_avail} available at the interval minimum "
                    f"(over-commit — C2)."
                )

    def test_infeasible_activity_is_not_placed(self):
        """A that needs 4 MECH for 6 h can never run (max 2 after hour 4), so a
        correct scheduler refuses it rather than producing an infeasible plan."""
        p, a = self._pert()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert a not in p.completed


# ---------------------------------------------------------------------------
# C2b — the independent validator shares the same start-only blind spot on all
# three dimensions (crew / equipment / location).  Each test hand-places an
# activity across an availability drop and asserts the validator now reports it.
# Control cases (constant availability) must stay clean so the interval check
# does not manufacture false positives on the common time-invariant path.
# ---------------------------------------------------------------------------

class TestValidatorCatchesMidActivityCrewDrop:

    def _pert_with_completed(self, pool, act):
        s, e = Activity('START', 0.0), Activity('END', 0.0)
        p = _build({s: [e], e: []})
        p.crew_pool = pool
        act.setActualStartTime(_START)
        act.status = 'completed'
        p.completed = [act]
        return p

    def test_mid_activity_drop_is_flagged(self):
        a = Activity('A', 6.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 4}])
        p = self._pert_with_completed(_dropping_crew_pool(), a)
        violations, warnings = [], []
        _check_crew_feasibility(p, violations, warnings)
        crew = [v for v in violations if v.type == 'crew']
        assert crew, ("validator missed the 4>2 crew over-commit on [4,6) "
                      "(mid-activity availability drop — C2b).")

    def test_constant_pool_no_false_positive(self):
        """Control: with a flat pool of 4, an activity needing 4 is feasible and
        must NOT be reported — the interval check must match the old point check
        on time-invariant pools."""
        rp = ResourcePool()
        rp.resources['MECH'] = ResourceAvailability('MECH', [
            {'start_date': _START, 'end_date': _FAR, 'available_count': 4}])
        a = Activity('A', 6.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 4}])
        p = self._pert_with_completed(rp, a)
        violations, warnings = [], []
        _check_crew_feasibility(p, violations, warnings)
        assert [v for v in violations if v.type == 'crew'] == []


class TestValidatorCatchesMidActivityEquipmentDrop:

    def _pert_with_completed(self, pool, act):
        s, e = Activity('START', 0.0), Activity('END', 0.0)
        p = _build({s: [e], e: []})
        p.equipment_pool = pool
        act.setActualStartTime(_START)
        act.status = 'completed'
        p.completed = [act]
        return p

    def _dropping_equipment_pool(self):
        ep = EquipmentPool()
        ep.equipment['CRANE'] = EquipmentAvailability('CRANE', 'desc', [
            {'start_date': _START, 'end_date': _START + timedelta(hours=4),
             'quantity_available': 2},
            {'start_date': _START + timedelta(hours=4), 'end_date': _FAR,
             'quantity_available': 1},
        ])
        return ep

    def test_mid_activity_drop_is_flagged(self):
        a = Activity('A', 6.0)
        a.required_equipment = [{'equipment_id': 'CRANE', 'quantity_needed': 2}]
        p = self._pert_with_completed(self._dropping_equipment_pool(), a)
        violations, warnings = [], []
        _check_equipment_feasibility(p, violations, warnings)
        assert [v for v in violations if v.type == 'equipment'], (
            "validator missed the 2>1 equipment over-commit on [4,6) (C2b).")

    def test_constant_pool_no_false_positive(self):
        ep = EquipmentPool()
        ep.equipment['CRANE'] = EquipmentAvailability('CRANE', 'desc', [
            {'start_date': _START, 'end_date': _FAR, 'quantity_available': 2}])
        a = Activity('A', 6.0)
        a.required_equipment = [{'equipment_id': 'CRANE', 'quantity_needed': 2}]
        p = self._pert_with_completed(ep, a)
        violations, warnings = [], []
        _check_equipment_feasibility(p, violations, warnings)
        assert [v for v in violations if v.type == 'equipment'] == []


class TestValidatorCatchesMidActivityLocationDrop:

    def _pert_with_completed(self, pool, act):
        s, e = Activity('START', 0.0), Activity('END', 0.0)
        p = _build({s: [e], e: []})
        p.location_pool = pool
        act.setActualStartTime(_START)
        act.status = 'completed'
        p.completed = [act]
        return p

    def _dropping_location_pool(self):
        lp = LocationPool()
        lp.locations['Z1'] = LocationAvailability('Z1', 'desc', [
            {'start_date': _START, 'end_date': _START + timedelta(hours=4),
             'max_concurrent_tasks': 5, 'max_concurrent_workers': 4},
            {'start_date': _START + timedelta(hours=4), 'end_date': _FAR,
             'max_concurrent_tasks': 5, 'max_concurrent_workers': 2},
        ])
        return lp

    def test_mid_activity_worker_drop_is_flagged(self):
        # 4 workers in Z1; worker cap steps 4→2 at hour 4.
        a = Activity('A', 6.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 4}])
        a.zone_ids = ['Z1']
        p = self._pert_with_completed(self._dropping_location_pool(), a)
        violations, warnings = [], []
        _check_location_feasibility(p, violations, warnings)
        assert [v for v in violations if v.type == 'location'], (
            "validator missed the 4>2 worker-capacity over-commit on [4,6) (C2b).")

    def test_constant_pool_no_false_positive(self):
        lp = LocationPool()
        lp.locations['Z1'] = LocationAvailability('Z1', 'desc', [
            {'start_date': _START, 'end_date': _FAR,
             'max_concurrent_tasks': 5, 'max_concurrent_workers': 4}])
        a = Activity('A', 6.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 4}])
        a.zone_ids = ['Z1']
        p = self._pert_with_completed(lp, a)
        violations, warnings = [], []
        _check_location_feasibility(p, violations, warnings)
        assert [v for v in violations if v.type == 'location'] == []


# ===========================================================================
# Round 2 — Cluster 2: max_use_res_ranked early-break starves zero-crew /
# other-skill candidates (finding C3, devLogs/PERT_MANUAL_REVIEW_2026-09-03.md).
# ---------------------------------------------------------------------------
# ``_compute_univ_skill_min`` labels a skill "universal" from the skill-requiring
# activities only (zero-requirement activities are skipped from the
# intersection).  The ``max_use_res_ranked`` / ``max_use_res_shuffled``
# early-break then ``break``s the candidate scan the instant that skill is
# exhausted — abandoning EVERY remaining candidate this step, including zero-crew
# activities (milestones, inspections, START/END sinks) and activities needing
# only OTHER skills.  Those never consume the scarce skill and could legally
# start now, so the schedule is needlessly delayed (worse makespan, spurious
# deadline misses) and — if the skill never recovers — such candidates can be
# starved forever (incomplete schedule).
#
# Fix: gate each candidate individually.  Skip only a candidate that itself
# requires a universal skill whose remaining capacity has fallen below the
# minimum such-requiring demand (provably infeasible); never gate a candidate
# that does not require the exhausted skill.
#
# Repro seed: devLogs/repros/repro_earlybreak.py
# ===========================================================================

def _flat_crew_pool(skill='MECH', count=2):
    rp = ResourcePool()
    rp.resources[skill] = ResourceAvailability(skill, [
        {'start_date': _START, 'end_date': _FAR, 'available_count': count}])
    return rp


class TestEarlyBreakDoesNotStarveZeroCrew:
    """C3 — a zero-crew activity ranked *below* a pool-exhausting one must still
    start as soon as precedence allows.  The ranked early-break must not abandon
    it merely because the crew skill is momentarily exhausted by the higher-
    ranked activity."""

    def _pert(self):
        start = Activity('START', 0.0)
        a = Activity('A', 6.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        m = Activity('M', 4.0, required_resources=[])       # zero-crew milestone
        end = Activity('END', 0.0)
        fwd = {start: [a, m], a: [end], m: [end], end: []}
        # A ranked strictly above M, so A is selected first and drains MECH→0.
        p = Pert(graph=fwd,
                 priorities={'START': 0.9, 'A': 1.0, 'M': 0.1, 'END': 0.5})
        p.crew_pool = _flat_crew_pool('MECH', 2)
        p.equipment_pool = EquipmentPool()
        p.location_pool = LocationPool()
        p.startTime = _START
        p.generateInfo()
        return p, a, m

    def test_universal_min_still_computed(self):
        """Precondition: MECH is the universal no-alternative skill (min 2), so
        the early-break path is genuinely exercised — the fix is not vacuous."""
        p, _a, _m = self._pert()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert p._univ_skill_min == {'MECH': 2}

    def test_zero_crew_activity_starts_at_time_zero(self):
        p, _a, m = self._pert()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        sm, _ = m.returnAbsTimes()
        assert sm == _START, (
            f"zero-crew M started at {sm}, not t=0 — the ranked early-break "
            f"abandoned it when MECH was exhausted by A (starvation — C3).")

    def test_makespan_is_optimal(self):
        p, _a, _m = self._pert()
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # A(6h) and M(4h) both start at t=0 → makespan 6h, not the buggy 10h.
        assert result['scheduled_duration'] == pytest.approx(6.0)

    def test_skill_requiring_candidate_still_gated(self):
        """Guard: dropping the blanket break must NOT let the second MECH hog
        run concurrently.  Two activities each needing all 2 MECH cannot overlap
        — the per-candidate feasibility check must still reject the second."""
        start = Activity('START', 0.0)
        a = Activity('A', 6.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        b = Activity('B', 6.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        end = Activity('END', 0.0)
        fwd = {start: [a, b], a: [end], b: [end], end: []}
        p = Pert(graph=fwd,
                 priorities={'START': 0.9, 'A': 1.0, 'B': 0.5, 'END': 0.2})
        p.crew_pool = _flat_crew_pool('MECH', 2)
        p.equipment_pool = EquipmentPool()
        p.location_pool = LocationPool()
        p.startTime = _START
        p.generateInfo()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        sa, ea = a.returnAbsTimes()
        sb, eb = b.returnAbsTimes()
        # Only 2 MECH exist: B may not start before A releases them.
        assert sb >= ea, (
            f"A[{sa},{ea}) and B[{sb},{eb}) overlap despite sharing all 2 MECH "
            f"— fix must gate, not admit, the skill-requiring candidate.")


# ===========================================================================
# SC1 — Serial SGS must enforce (and record) the cumulative dose budget
# ===========================================================================

# Per activity: 50 mRem/h × 2 workers × 4 h = 400 mRem.  Budget = 125 × 4 = 500.
# Only ONE of three identical dose activities fits within budget.
_SER_DOSE = 50.0 * 2 * 4      # 400 mRem
_SER_DOSE_BUDGET = 125.0 * 4  # 500 mRem


def _dose_crew_pool(skill='MECHANIC', count=4, per_worker=125.0):
    rp = ResourcePool()
    rp.resources[skill] = ResourceAvailability(
        skill,
        [{'start_date': datetime(2025, 1, 1), 'end_date': datetime(2025, 12, 31),
          'available_count': count}],
        resource_type='consumable',
        dose_budget_per_worker_mrem=per_worker,
    )
    return rp


class TestSerialEnforcesDoseBudget:
    """SC1 — the Serial SGS silently ignored the cumulative dose budget: it had
    no dose feasibility check *and* never called ``tracker.consume`` on commit,
    so an arbitrary number of dose activities could be placed and the tracker
    (which the validator reads) stayed at zero, hiding the violation.

    With three identical 400 mRem activities against a 500 mRem budget, only
    one may be scheduled; the tracker must reflect exactly that one."""

    def _pert(self):
        rp = _dose_crew_pool()

        def act(name):
            a = Activity(name, 4.0,
                         required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
            a.dose_rate_mrem_per_hour = 50.0
            return a

        A, B, C = act('A'), act('B'), act('C')
        S, E = Activity('START', 0.0), Activity('END', 0.0)
        fwd = {S: [A, B, C], A: [E], B: [E], C: [E], E: []}

        p = Pert(graph=fwd)
        p.crew_pool = rp
        p.equipment_pool = EquipmentPool()
        p.location_pool = LocationPool()
        p.dose_trackers = rp.build_dose_trackers()
        p._precompute_availability_events()
        p.generateInfo()
        p.startTime = datetime(2025, 6, 1, 6, 0)
        return p

    def test_only_budget_many_dose_activities_scheduled(self):
        p = self._pert()
        p.calculateSerialScheduleWithResources(priority_rule='lf')
        placed = [a.name for a in p.completed
                  if getattr(a, 'dose_rate_mrem_per_hour', 0.0) > 0.0]
        assert len(placed) == 1, (
            f"serial placed {len(placed)} dose activities ({sorted(placed)}); "
            f"only 1 of 3 fits the {_SER_DOSE_BUDGET} mRem budget "
            f"({_SER_DOSE} mRem each) — dose budget not enforced (SC1).")

    def test_dose_tracker_populated_on_serial_commit(self):
        p = self._pert()
        p.calculateSerialScheduleWithResources(priority_rule='lf')
        tr = p.dose_trackers['MECHANIC']
        assert tr.consumed_mrem == pytest.approx(_SER_DOSE), (
            f"tracker.consumed_mrem={tr.consumed_mrem}; the serial commit must "
            f"call tracker.consume so the validator can see accrued dose (SC1).")

    def test_never_over_budget(self):
        p = self._pert()
        p.calculateSerialScheduleWithResources(priority_rule='lf')
        tr = p.dose_trackers['MECHANIC']
        assert tr.consumed_mrem <= tr.total_budget_mrem, (
            f"consumed {tr.consumed_mrem} > budget {tr.total_budget_mrem} mRem.")

    def test_within_budget_activity_still_scheduled(self):
        """Guard: a single within-budget dose activity must still be placed —
        the dose check rejects only what genuinely exceeds the budget."""
        rp = _dose_crew_pool()
        A = Activity('A', 4.0,
                     required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
        A.dose_rate_mrem_per_hour = 50.0       # 400 mRem < 500 budget
        S, E = Activity('START', 0.0), Activity('END', 0.0)
        fwd = {S: [A], A: [E], E: []}
        p = Pert(graph=fwd)
        p.crew_pool = rp
        p.equipment_pool = EquipmentPool()
        p.location_pool = LocationPool()
        p.dose_trackers = rp.build_dose_trackers()
        p._precompute_availability_events()
        p.generateInfo()
        p.startTime = datetime(2025, 6, 1, 6, 0)
        p.calculateSerialScheduleWithResources(priority_rule='lf')
        assert 'A' in [a.name for a in p.completed]


# ===========================================================================
# PD1 — Parallel SGS must not over-commit the dose budget within one time-step
# ===========================================================================

# Parallel analog of SC1.  The parallel selection loop decrements a shared
# capacity snapshot (crew / equipment / location) as each candidate is
# tentatively selected, so later candidates in the *same* time-step see the
# reduced availability.  Dose was the exception: _apply_tentative never charged
# the tracker, so every candidate's dose check read the same (pre-time-step)
# consumed_mrem.  With N dose activities all ready at t=0 and crew to spare,
# the loop selected all of them — committing N × per-activity dose against a
# budget that admits only one — producing a genuinely INFEASIBLE schedule.
#
# Pool: 6 MECHANIC (crew admits all 3 activities: 3 × 2 = 6), budget 600 mRem
# (100 × 6).  Each activity draws 400 mRem (50/h × 2 × 4h), so crew allows 3
# simultaneous starts but dose allows exactly ONE.  Placing >1 is pure dose
# over-commit — crew is deliberately non-binding.
_PAR_DOSE = 50.0 * 2 * 4        # 400 mRem per activity
_PAR_DOSE_BUDGET = 100.0 * 6    # 600 mRem total (per_worker × peak count)


class TestParallelEnforcesDoseBudget:
    """PD1 — the parallel SGS tentatively decrements crew/equipment/location as
    it selects candidates in a single time-step, but never tentatively charged
    dose.  Three 400 mRem activities, all ready at t=0, with crew for all three
    and a 600 mRem budget that admits only one: the loop placed all three
    (1200 mRem) because each dose check saw the untouched tracker.  The fix
    threads a transient per-time-step dose overlay through the tentative
    snapshot so the second candidate's check sees the first's tentative draw."""

    def _pert(self):
        rp = _dose_crew_pool(count=6, per_worker=100.0)

        def act(name):
            a = Activity(name, 4.0,
                         required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
            a.dose_rate_mrem_per_hour = 50.0
            return a

        A, B, C = act('A'), act('B'), act('C')
        S, E = Activity('START', 0.0), Activity('END', 0.0)
        fwd = {S: [A, B, C], A: [E], B: [E], C: [E], E: []}

        p = Pert(graph=fwd)
        p.crew_pool = rp
        p.equipment_pool = EquipmentPool()
        p.location_pool = LocationPool()
        p.dose_trackers = rp.build_dose_trackers()
        p._precompute_availability_events()
        p.generateInfo()
        p.startTime = datetime(2025, 6, 1, 6, 0)
        return p

    @pytest.mark.parametrize('sgs', ['max_use_res_ranked', 'max_use_res_shuffled'])
    def test_only_budget_many_dose_activities_scheduled(self, sgs):
        p = self._pert()
        p.calculateScheduleWithResources(sgs=sgs)
        placed = [a.name for a in p.completed
                  if getattr(a, 'dose_rate_mrem_per_hour', 0.0) > 0.0]
        assert len(placed) == 1, (
            f"[{sgs}] parallel placed {len(placed)} dose activities "
            f"({sorted(placed)}); crew admits 3 but only 1 of 3 fits the "
            f"{_PAR_DOSE_BUDGET} mRem budget ({_PAR_DOSE} mRem each) — "
            f"same-time-step dose over-commit (PD1).")

    @pytest.mark.parametrize('sgs', ['max_use_res_ranked', 'max_use_res_shuffled'])
    def test_never_over_budget(self, sgs):
        p = self._pert()
        p.calculateScheduleWithResources(sgs=sgs)
        tr = p.dose_trackers['MECHANIC']
        assert tr.consumed_mrem <= tr.total_budget_mrem, (
            f"[{sgs}] consumed {tr.consumed_mrem} > budget "
            f"{tr.total_budget_mrem} mRem — infeasible schedule (PD1).")

    def test_within_budget_activities_still_scheduled(self):
        """Guard: when the budget genuinely admits every ready dose activity in
        one time-step, all of them must still be placed — the overlay rejects
        only what would exceed the budget, never a within-budget candidate."""
        # Budget = 100 × 6 = 600; two 200 mRem activities (25/h × 2 × 4h) = 400
        # total, both fit crew (4 of 6) and dose (400 ≤ 600).
        rp = _dose_crew_pool(count=6, per_worker=100.0)

        def act(name):
            a = Activity(name, 4.0,
                         required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
            a.dose_rate_mrem_per_hour = 25.0   # 200 mRem each
            return a

        A, B = act('A'), act('B')
        S, E = Activity('START', 0.0), Activity('END', 0.0)
        fwd = {S: [A, B], A: [E], B: [E], E: []}
        p = Pert(graph=fwd)
        p.crew_pool = rp
        p.equipment_pool = EquipmentPool()
        p.location_pool = LocationPool()
        p.dose_trackers = rp.build_dose_trackers()
        p._precompute_availability_events()
        p.generateInfo()
        p.startTime = datetime(2025, 6, 1, 6, 0)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        placed = {a.name for a in p.completed
                  if getattr(a, 'dose_rate_mrem_per_hour', 0.0) > 0.0}
        assert placed == {'A', 'B'}, (
            f"both within-budget dose activities must be placed; got {placed}.")
        # And they must start in the SAME time-step (t=0) — proving the overlay
        # admits multiple candidates when the budget allows.
        sa, _ = A.returnAbsTimes()
        sb, _ = B.returnAbsTimes()
        assert sa == sb == p.startTime, (
            f"both should start at t=0 (same step); A={sa}, B={sb}.")


# ===========================================================================
# SC2 — Serial SGS must enforce equipment zone affinity
# ===========================================================================

class TestSerialEnforcesEquipmentZone:
    """SC2 — the Serial SGS equipment check was count-only: it never verified
    that zone-locked equipment is used from a compatible zone.  An activity in
    ZONE_2 could therefore consume equipment locked to ZONE_1."""

    def _pert(self, act_zone='ZONE_2'):
        _s, _e = datetime(2026, 1, 1), datetime(2026, 12, 31)
        ep = EquipmentPool()
        ep.equipment['EQ1'] = EquipmentAvailability(
            'EQ1', 'desc',
            [{'start_date': _s, 'end_date': _e, 'quantity_available': 2}],
            zone_id='ZONE_1')
        lp = LocationPool()
        for lid in ('ZONE_1', 'ZONE_2'):
            lp.locations[lid] = LocationAvailability(
                lid, 'desc',
                [{'start_date': _s, 'end_date': _e,
                  'max_concurrent_tasks': 10, 'max_concurrent_workers': None}])
        A = Activity('A', 4.0)
        A.required_resources = []
        A.required_equipment = [{'equipment_id': 'EQ1', 'quantity_needed': 1}]
        A.zone_ids = [act_zone]
        S, E = Activity('START', 0.0), Activity('END', 0.0)
        fwd = {S: [A], A: [E], E: []}
        p = Pert(graph=fwd)
        p.crew_pool = ResourcePool()
        p.equipment_pool = ep
        p.location_pool = lp
        p.consumable_pool = None
        p.system_state_pool = None
        p._precompute_availability_events()
        p.generateInfo()
        p.startTime = _s
        return p

    def test_wrong_zone_activity_refused(self):
        p = self._pert(act_zone='ZONE_2')
        p.calculateSerialScheduleWithResources(priority_rule='lf')
        assert 'A' not in [a.name for a in p.completed], (
            "serial placed A in ZONE_2 using EQ1 locked to ZONE_1 — "
            "equipment zone affinity not enforced (SC2).")

    def test_no_zone_violation_reported(self):
        p = self._pert(act_zone='ZONE_2')
        p.calculateSerialScheduleWithResources(priority_rule='lf')
        r = p.validate_schedule()
        zone_viol = [v for v in r.violations if v.type == 'equipment_zone']
        assert not zone_viol, (
            f"serial produced {len(zone_viol)} equipment_zone violation(s) — "
            f"the illegal placement was committed rather than skipped (SC2).")

    def test_matching_zone_activity_still_scheduled(self):
        """Guard: an activity in the equipment's own zone must still be placed."""
        p = self._pert(act_zone='ZONE_1')
        p.calculateSerialScheduleWithResources(priority_rule='lf')
        assert 'A' in [a.name for a in p.completed]


# ===========================================================================
# RP1 — duration override on an in-progress activity must refresh endTime
# ===========================================================================

class TestReplanDurationOverrideRefreshesEndTime:
    """RP1 — ``_partial_reset`` applies a ``duration_overrides`` entry for an
    in-progress activity by updating ``duration`` / ``_remaining_duration`` but
    left ``endTime`` at ``start + OLD duration``.  Everything that drives actual
    completion and resource release reads ``endTime``, so the task freed its
    resources at the stale old end time — double-booking a shared resource with
    the successor rescheduled into the gap."""

    def _baseline(self):
        # B and D both need the sole WELDER (cap 1) → they must serialize.
        rp = ResourcePool()
        rp.resources['WELDER'] = ResourceAvailability('WELDER', [
            {'start_date': _START, 'end_date': _FAR, 'available_count': 1}])

        start = Activity('START', 0.0)
        b = Activity('B', 10.0,
                     required_resources=[{'skill_type': 'WELDER', 'crew_count': 1}])
        d = Activity('D', 6.0,
                     required_resources=[{'skill_type': 'WELDER', 'crew_count': 1}])
        end = Activity('END', 0.0)
        fwd = {start: [b, d], b: [end], d: [end], end: []}
        # B ranked above D → baseline runs B[0,10] then D[10,16].
        p = Pert(graph=fwd,
                 priorities={'START': 0.9, 'B': 1.0, 'D': 0.2, 'END': 0.1})
        p.crew_pool = rp
        p.equipment_pool = EquipmentPool()
        p.location_pool = LocationPool()
        p.startTime = _START
        p.generateInfo()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        return p, b, d

    def test_baseline_serializes(self):
        """Precondition: baseline places B[0,10] then D[10,16] (WELDER shared)."""
        p, b, d = self._baseline()
        sb, eb = b.returnAbsTimes()
        sd, _ = d.returnAbsTimes()
        assert sb == _START and eb == _START + timedelta(hours=10)
        assert sd == _START + timedelta(hours=10)

    def test_endtime_reflects_overridden_duration(self):
        p, b, _d = self._baseline()
        p.replan(current_time_hours=2.0, duration_overrides={'B': 20.0})
        sb, eb = b.returnAbsTimes()
        # B started at t=0; its new total is 20h → it must end at t=20, not the
        # stale t=10 (start + old 10h duration).
        assert b.duration == pytest.approx(20.0)
        assert eb == sb + timedelta(hours=b.duration), (
            f"B.endTime={eb} is inconsistent with start {sb} + duration "
            f"{b.duration}h — override did not refresh endTime (RP1).")
        assert eb == _START + timedelta(hours=20)

    def test_successor_not_double_booked(self):
        p, b, d = self._baseline()
        p.replan(current_time_hours=2.0, duration_overrides={'B': 20.0})
        sb, _eb = b.returnAbsTimes()
        sd, _ed = d.returnAbsTimes()
        # Compare against B's PHYSICAL end (start + overridden duration), NOT its
        # endTime attribute — the bug leaves endTime stale, so comparing against
        # it would spuriously pass.  B truly holds the sole WELDER until t=20.
        b_true_end = sb + timedelta(hours=b.duration)
        assert sd >= b_true_end, (
            f"D started at {sd} but B (sole WELDER) physically runs until "
            f"{b_true_end} — 6h double-booking of a 1-unit resource (RP1).")


# ===========================================================================
# RP2 — clone_for_analysis must repopulate availability-boundary events
# ===========================================================================

class TestCloneRepopulatesAvailabilityEvents:
    """RP2 — ``clone_for_analysis`` hard-set ``_availability_events`` to an empty
    frozenset and nothing repopulated it (``_precompute_availability_events`` runs
    only from ``__init__`` and from ``replan`` when pools change).  A clone with a
    time-varying pool therefore lost every availability wake-up and dead-locked
    into an incomplete schedule."""

    def _baseline(self):
        # WELDER unavailable [0,10h], then 2 units.  A needs 1 WELDER, so it can
        # only start once the pool opens at h=10.
        rp = ResourcePool()
        rp.resources['WELDER'] = ResourceAvailability('WELDER', [
            {'start_date': _START, 'end_date': _START + timedelta(hours=10),
             'available_count': 0},
            {'start_date': _START + timedelta(hours=10), 'end_date': _FAR,
             'available_count': 2}])

        start = Activity('START', 0.0)
        a = Activity('A', 5.0,
                     required_resources=[{'skill_type': 'WELDER', 'crew_count': 1}])
        end = Activity('END', 0.0)
        fwd = {start: [a], a: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool = rp
        p.equipment_pool = EquipmentPool()
        p.location_pool = LocationPool()
        p.startTime = _START
        # Pools are attached AFTER __init__ (which already ran
        # _precompute_availability_events on the then-empty pools), so recompute
        # now — this is exactly what the clone must also do (RP2).
        p._precompute_availability_events()
        p.generateInfo()
        return p

    def test_baseline_completes_all(self):
        """Precondition: the original (with events precomputed in __init__)
        schedules A once the WELDER opens at h=10 → 3/3 complete."""
        p = self._baseline()
        res = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert res['n_completed'] == res['n_activities'] == 3

    def test_clone_has_nonempty_events(self):
        p = self._baseline()
        clone = p.clone_for_analysis()
        assert clone._availability_events, (
            "clone._availability_events is empty — the h=10 WELDER boundary was "
            "dropped, so the event-driven scheduler cannot wake up for it (RP2).")
        # The h=10 open boundary must be among them.
        assert (_START + timedelta(hours=10)) in clone._availability_events

    def test_clone_completes_all(self):
        p = self._baseline()
        clone = p.clone_for_analysis()
        clone.generateInfo()
        res = clone.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert res['n_completed'] == res['n_activities'] == 3, (
            f"clone completed {res['n_completed']}/{res['n_activities']} — the "
            f"empty availability-event set starved A of its h=10 wake-up (RP2).")


# ===========================================================================
# B1 — _longest_path_in_augmented must seed EVERY source with its duration
# ===========================================================================

class TestLongestPathMultiSourceDuration:
    """B1 — the longest-path DP seeded only ``self.startActivity`` / ``topo[0]``
    with its own duration and left every *other* in-degree-0 source at 0.  A
    longest path that originates at a non-``topo[0]`` source therefore lost that
    source's entire duration, so the wrong (shorter) chain was returned.  The
    fix seeds *all* sources with their own duration and every other node with
    ``-inf`` (pert.py ``_longest_path_in_augmented``)."""

    def _multi_source_pert(self):
        # Two sources feeding a common sink, NO unifying START milestone.
        # Insertion order Q, P, C makes Q (the SHORT source) become topo[0], so
        # the buggy seed lands on the wrong source.
        q = Activity("Q", 1.0)
        p_long = Activity("P", 100.0)
        c = Activity("C", 1.0)
        fwd = {q: [c], p_long: [c], c: []}
        return Pert(graph=fwd), p_long, q, c

    def test_longest_path_uses_the_true_long_source(self):
        p, p_long, q, c = self._multi_source_pert()
        chain = p._longest_path_in_augmented(p._build_augmented_graph())
        names = [n.returnName() for n in chain]
        length = sum(p._effective_duration(n) for n in chain)
        assert names == ['P', 'C'], (
            f"expected the P->C chain (the long source); got {names} (B1)")
        assert length == 101.0, (
            f"expected chain length 101 (100+1); got {length} — a non-topo[0] "
            f"source lost its duration (B1)")

    def test_single_short_source_still_correct(self):
        """Control: with only the short source present the chain is Q->C and
        length 2 — the fix must not distort the simple single-source case."""
        q = Activity("Q", 1.0)
        c = Activity("C", 1.0)
        fwd = {q: [c], c: []}
        p = Pert(graph=fwd)
        chain = p._longest_path_in_augmented(p._build_augmented_graph())
        assert [n.returnName() for n in chain] == ['Q', 'C']
        assert sum(p._effective_duration(n) for n in chain) == 2.0


# ===========================================================================
# B3 — _longest_path_in_augmented must retain leading zero-duration nodes
# ===========================================================================

class TestLongestPathRetainsZeroDurationPrefix:
    """B3 — with the old 0-init ``dist`` and a strict ``>`` relaxation, a
    ``START(0) -> M(0)`` edge produced ``cand == dist[M] == 0``, which is not
    ``> 0``; ``parent[M]`` was never set and the reconstruction dropped the
    leading zero-duration node(s).  Seeding sources with their duration and
    every other node with ``-inf`` guarantees the first predecessor to relax a
    node always sets a parent, so zero-duration prefixes are retained."""

    def test_zero_duration_prefix_retained(self):
        start = Activity("START", 0.0)
        m = Activity("M", 0.0)      # zero-duration node right after START
        a = Activity("A", 5.0)
        end = Activity("END", 0.0)
        fwd = {start: [m], m: [a], a: [end], end: []}
        p = Pert(graph=fwd)
        chain = p._longest_path_in_augmented(p._build_augmented_graph())
        assert [n.returnName() for n in chain] == ['START', 'M', 'A', 'END'], (
            f"leading zero-duration node dropped from the chain: "
            f"{[n.returnName() for n in chain]} (B3)")


# ===========================================================================
# B2 — _splice_buffer_activity must preserve finish-to-start lag
# ===========================================================================

class TestSpliceBufferPreservesLag:
    """B2 — ``_splice_buffer_activity`` rerouted every ``pred -> succ`` edge
    through the buffer but never moved the finish-to-start lag stored in
    ``lag_dict``.  The orphaned ``(pred, succ)`` key no longer matched any edge,
    so the forward/backward CPM passes ignored it and the successor started
    ``lag`` hours too early.  The fix moves each lag onto the matching buffer
    edge — ``pred -> buffer`` for a feeding splice (fan-in, N preds >= 1 succ),
    ``buffer -> succ`` for a project splice (fan-out, 1 pred < M succs)."""

    def _linear_with_lag(self, lag=5.0):
        start = Activity("START", 0.0)
        a = Activity("A", 10.0)
        b = Activity("B", 10.0)
        end = Activity("END", 0.0)
        fwd = {start: [a], a: [b], b: [end], end: []}
        p = Pert(graph=fwd)
        p.lag_dict[(a, b)] = lag
        p.generateInfo()
        return p, start, a, b, end

    def test_feeding_splice_preserves_project_finish(self):
        p, start, a, b, end = self._linear_with_lag(lag=5.0)
        assert p.infoDict[end]['ef'] == 25.0  # 0 + 10 + 5(lag) + 10

        buf = Activity("BUF", 0.0)
        buf.buffer_type = 'feeding'
        p._splice_buffer_activity(buf, predecessors=[a], successors=[b])

        assert p.infoDict[end]['ef'] == 25.0, (
            f"project finish moved to {p.infoDict[end]['ef']} — the 5h lag was "
            f"lost across the feeding splice (B2)")
        assert (a, b) not in p.lag_dict, (
            "orphaned (A,B) lag entry not removed after splice (B2)")
        # fan-in (1 pred >= 1 succ) -> lag lives on pred -> buffer
        assert p.lag_dict.get((a, buf)) == 5.0, (
            "feeding-splice lag not moved onto the A->BUF edge (B2)")

    def test_project_splice_attaches_lag_to_buffer_succ(self):
        # 1 pred -> 2 succs (fan-out): the lag on A->B must land on BUF->B.
        start = Activity("START", 0.0)
        a = Activity("A", 10.0)
        b = Activity("B", 10.0)
        c = Activity("C", 10.0)
        end = Activity("END", 0.0)
        fwd = {start: [a], a: [b, c], b: [end], c: [end], end: []}
        p = Pert(graph=fwd)
        p.lag_dict[(a, b)] = 5.0
        p.generateInfo()
        ef_before = p.infoDict[end]['ef']       # A->B path drives it: 25
        assert ef_before == 25.0

        buf = Activity("PB", 0.0)
        buf.buffer_type = 'project'
        p._splice_buffer_activity(buf, predecessors=[a], successors=[b, c])

        assert (a, b) not in p.lag_dict, (
            "orphaned (A,B) lag entry not removed after project splice (B2)")
        # fan-out (1 pred < 2 succs) -> lag lives on buffer -> succ
        assert p.lag_dict.get((buf, b)) == 5.0, (
            "project-splice lag not moved onto the PB->B edge (B2)")
        assert p.infoDict[end]['ef'] == ef_before, (
            f"project finish moved from {ef_before} to {p.infoDict[end]['ef']} "
            f"— lag lost across the project splice (B2)")


# ===========================================================================
# C1 — _apply_time_windows: window-raised ES must propagate + re-anchor
# ===========================================================================

class TestWindowEarliestStartPropagates:
    """C1 — a time window that raises an activity's ES was tightened only
    locally: the raised EF never reached successors (they kept an ES computed
    from the predecessor's *pre-window* finish), and the backward pass was never
    re-anchored to the window-extended project end.  Consequences: successors
    reported too-early ES/EF, the project duration was understated, and a plain
    release date (window ``[west, ∞]``, no deadline) produced spurious negative
    slack and a false ``window_infeasible`` flag.

    The fix re-runs a forward relaxation (propagating the raised EF to
    successors) and a backward pass re-anchored to the new project end."""

    def _win_chain(self, west=None, wlf=None, on='B'):
        """START(0) -> A(4) -> B(3) -> END(0); window on the named activity."""
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        b = Activity("B", 3.0)
        end = Activity("END", 0.0)
        target = {'A': a, 'B': b}[on]
        if west is not None:
            target.window_earliest_start_hours = west
        if wlf is not None:
            target.window_latest_finish_hours = wlf
        fwd = {start: [a], a: [b], b: [end], end: []}
        p = Pert(graph=fwd)
        return p, start, a, b, end

    def test_raised_es_propagates_to_successor(self):
        # Window on A pushes A.ES 0 -> 10, A.EF 4 -> 14.  B (no window) must
        # start at 14, not at its pre-window ES of 4.  END must reflect it too.
        p, start, a, b, end = self._win_chain(west=10.0, on='A')
        assert p.infoDict[a]['es'] == 10.0
        assert p.infoDict[a]['ef'] == 14.0
        assert p.infoDict[b]['es'] == 14.0, (
            f"successor ES not propagated: expected 14, got {p.infoDict[b]['es']} "
            f"— window-raised EF never reached B (C1)")
        assert p.infoDict[b]['ef'] == 17.0
        assert p.infoDict[end]['ef'] == 17.0, (
            f"project end understated: expected 17, got {p.infoDict[end]['ef']} "
            f"(C1)")

    def test_release_date_only_is_feasible(self):
        # Window [8, inf) on B is a pure release date: it delays B (and the
        # project) but can never be infeasible.  The re-anchored backward pass
        # extends B.LF to the new project end (11), giving slack 0 — not the old
        # spurious slack = LS(4) - ES(8) = -4 with a false infeasibility flag.
        p, start, a, b, end = self._win_chain(west=8.0, on='B')
        ib = p.infoDict[b]
        assert ib['es'] == 8.0
        assert ib['ef'] == 11.0
        assert ib['lf'] == 11.0, (
            f"LF not re-anchored to extended project end: expected 11, got "
            f"{ib['lf']} (C1)")
        assert abs(ib['slack'] - 0.0) < 1e-9, (
            f"release date wrongly given negative slack {ib['slack']} (C1)")
        assert ib['window_infeasible'] is False, (
            "release-date-only window wrongly flagged infeasible (C1)")

    def test_genuine_deadline_still_flagged_infeasible(self):
        # Control: window [10, 12] on B has width 2 < duration 3 → genuinely
        # infeasible.  The fix must NOT suppress this: slack < 0, flag True.
        p, start, a, b, end = self._win_chain(west=10.0, wlf=12.0, on='B')
        ib = p.infoDict[b]
        assert ib['es'] == 10.0
        assert ib['slack'] < 0.0, (
            f"genuine deadline should give negative slack, got {ib['slack']} (C1)")
        assert ib['window_infeasible'] is True, (
            "genuine deadline infeasibility wrongly suppressed (C1)")


# ===========================================================================
# B4 — _build_augmented_graph skip gate sampled availability at startTime
# ===========================================================================

def _dropping_mech(high=5, low=4, drop_h=4):
    """MECH pool = `high` for [START, START+drop_h), then `low` forever."""
    rp = ResourcePool()
    rp.resources['MECH'] = ResourceAvailability('MECH', [
        {'start_date': _START, 'end_date': _START + timedelta(hours=drop_h),
         'available_count': high},
        {'start_date': _START + timedelta(hours=drop_h), 'end_date': _FAR,
         'available_count': low},
    ])
    return rp


class TestAugmentedGraphSkipGateHorizon:
    """B4 — `_build_augmented_graph`'s resource-binding skip gate
    (`2*max_demand >= avail`) sampled availability at `self.startTime`, while the
    actual per-pair binding test samples it at each pair's `overlap_start`.  When
    a pool dips *below* its startTime level during an overlap, the gate could
    close on the higher startTime value and skip the whole pair scan — dropping a
    genuine resource-flow arc and shortening the resource-constrained chain.
    The fix samples the *minimum* availability over the scheduled horizon."""

    def _two_overlapping(self, pool):
        start = Activity("START", 0.0)
        a = Activity("A", 10.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        b = Activity("B", 10.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 2}])
        end = Activity("END", 0.0)
        fwd = {start: [a, b], a: [end], b: [end], end: []}
        p = _build(fwd)
        p.crew_pool = pool
        # A and B overlap during the LOW window [4h, 14h): saturating pair.
        for act in (a, b):
            act.startTime = _START + timedelta(hours=4)
            act.endTime = _START + timedelta(hours=14)
        start.startTime = start.endTime = _START
        end.startTime = end.endTime = _START + timedelta(hours=14)
        return p, a, b

    def test_binding_arc_added_when_pool_dips_below_starttime(self):
        # avail 5 at startTime, 4 during the overlap; combined demand 4 saturates
        # the pool at overlap (4 >= 4 → binding) but the startTime gate (4 < 5)
        # would skip it.
        p, a, b = self._two_overlapping(_dropping_mech(high=5, low=4, drop_h=4))
        aug = p._build_augmented_graph()
        assert (b in aug[a]) or (a in aug[b]), (
            "resource-flow arc between the saturating overlapping pair was "
            "dropped — skip gate sampled availability at startTime (B4)")

    def test_no_spurious_arc_when_pool_high_everywhere(self):
        # Control: pool is 5 everywhere; combined demand 4 < 5 is never binding,
        # so no arc must be added (the fix must not over-connect).
        p, a, b = self._two_overlapping(_dropping_mech(high=5, low=5, drop_h=4))
        aug = p._build_augmented_graph()
        assert b not in aug[a] and a not in aug[b], (
            "spurious resource-flow arc added for a non-binding pair (B4 control)")


# ===========================================================================
# M-1 — _rank_by_value_top_k cutoff estimated from startTime availability
# ===========================================================================

def _rising_mech(low=1, high=30, rise_h=10):
    """MECH pool = `low` for [START, START+rise_h), then `high` forever."""
    rp = ResourcePool()
    rp.resources['MECH'] = ResourceAvailability('MECH', [
        {'start_date': _START, 'end_date': _START + timedelta(hours=rise_h),
         'available_count': low},
        {'start_date': _START + timedelta(hours=rise_h), 'end_date': _FAR,
         'available_count': high},
    ])
    return rp


class TestTopKUsesCurrentTimeAvailability:
    """M-1 — `_rank_by_value_top_k` estimated its cutoff `max_slots` from crew
    availability at `self.startTime`.  When availability grows after startTime,
    `max_slots` is under-counted and `heapq.nlargest(k)` truncates candidates
    that are placeable *now*, deferring them to a later event and inflating the
    makespan.  The fix samples availability at the current scheduling time."""

    def _fan(self, n=20):
        start = Activity("START", 0.0)
        p_act = Activity("P", 10.0,
                         required_resources=[{'skill_type': 'MECH', 'crew_count': 1}])
        end = Activity("END", 0.0)
        kids = [Activity(f"A{i}", 5.0,
                         required_resources=[{'skill_type': 'MECH', 'crew_count': 1}])
                for i in range(n)]
        fwd = {start: [p_act], p_act: list(kids), end: []}
        for k in kids:
            fwd[k] = [end]
        p = _build(fwd)
        p.crew_pool = _rising_mech()
        return p, kids

    def test_all_ready_activities_launch_when_availability_grew(self):
        # P finishes at 10h; all 20 kids become ready when MECH has risen to 30,
        # so all 20 can (and should) start at once → makespan 10 + 5 = 15h.
        p, kids = self._fan(n=20)
        res = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        starts = {k.returnAbsTimes()[0] for k in kids}
        assert len(starts) == 1, (
            f"20 ready activities launched in {len(starts)} waves instead of 1 — "
            f"top-k cutoff under-counted slots from startTime availability (M-1)")
        assert abs(res['scheduled_duration'] - 15.0) < 1e-6, (
            f"makespan {res['scheduled_duration']}h inflated above the optimal 15h "
            f"by top-k truncation (M-1)")


# ===========================================================================
# SC-m1 — check_dependency_violations must match the validator's precedence
#         tolerance (_PREC_TOL), not use a strict comparison
# ===========================================================================

class TestDependencyCheckPrecTolerance:
    """SC-m1 — `check_dependency_violations` compared `succ_start <
    pred_end + lag` strictly, with no time tolerance, while the authoritative
    `schedule_validator._check_precedence` allows a 1-minute grace (`_PREC_TOL`).
    A sub-minute gap from hour→timedelta float arithmetic was therefore reported
    infeasible by one surface and feasible by the other.  The fix shares
    `_PREC_TOL` so the two agree."""

    def _placed(self, succ_offset):
        """START(0)→A(4h)→B(3h)→END(0), with B started `succ_offset` before A.end."""
        s, a, b, e = (Activity("START", 0.0), Activity("A", 4.0),
                      Activity("B", 3.0), Activity("END", 0.0))
        fwd = {s: [a], a: [b], b: [e], e: []}
        p = _build(fwd)
        t0 = p.startTime
        s.startTime = s.endTime = t0
        a.startTime, a.endTime = t0, t0 + timedelta(hours=4)
        b.startTime = a.endTime - succ_offset
        b.endTime = b.startTime + timedelta(hours=3)
        e.startTime = e.endTime = b.endTime
        p.completed = [s, a, b, e]
        p._completed_set = set(p.completed)
        return p

    def test_subminute_gap_tolerated_like_validator(self):
        # B starts 30s before A finishes — inside the 60s _PREC_TOL grace.
        from CPM.schedule_validator import _check_precedence
        p = self._placed(timedelta(seconds=30))
        _, dep_feasible = p.check_dependency_violations()
        val_viol = []
        _check_precedence(p, val_viol, [])
        val_feasible = len(val_viol) == 0
        assert val_feasible, "sanity: validator should tolerate a 30s gap"
        assert dep_feasible == val_feasible, (
            "check_dependency_violations disagrees with the validator on a "
            "sub-minute precedence gap (SC-m1)")

    def test_real_violation_still_flagged(self):
        # B starts 2h early — well beyond any tolerance; must still be flagged.
        p = self._placed(timedelta(hours=2))
        violations, dep_feasible = p.check_dependency_violations()
        assert not dep_feasible and len(violations) == 1, (
            "a genuine 2h precedence violation must still be reported (SC-m1 "
            "tolerance must not swallow real violations)")


# ===========================================================================
# SC3 — Serial SGS must apply skill substitution like the parallel path,
#       in BOTH the feasibility check and the commit/consumption accounting
# ===========================================================================

def _sub_pools(elec=0, mech=1):
    """ELEC and MECH pools, constant over the horizon."""
    START = datetime(2026, 1, 1)
    FAR = START + timedelta(days=365)
    rp = ResourcePool()
    rp.resources['ELEC'] = ResourceAvailability(
        'ELEC', [{'start_date': START, 'end_date': FAR, 'available_count': elec}])
    rp.resources['MECH'] = ResourceAvailability(
        'MECH', [{'start_date': START, 'end_date': FAR, 'available_count': mech}])
    return rp, EquipmentPool(), LocationPool()


class TestSerialAppliesSkillSubstitution:
    """SC3 — the Serial SGS feasibility check counted only the exact declared
    skill, so an activity feasible only via ``alternative_skill_types`` was
    dropped/delayed (the parallel path substitutes).  The fix mirrors the
    parallel path: substitution in the feasibility check AND a substitution-
    resolved consumption breakdown recorded at commit, so overlapping-usage
    sums reflect the skill that actually performs the work (no over-commit)."""

    def test_serial_schedules_substitution_feasible_activity(self):
        # A needs 1 ELEC (alt=[MECH]); ELEC=0, MECH=1.  Serial must borrow MECH
        # and schedule A at t0 — not drop it.  (Guards the check-side fallback.)
        s, a, e = (Activity("START", 0.0),
                   Activity("A", 5.0, required_resources=[
                       {'skill_type': 'ELEC', 'crew_count': 1,
                        'alternative_skill_types': ['MECH']}]),
                   Activity("END", 0.0))
        fwd = {s: [a], a: [e], e: []}
        p = _build(fwd, pools=_sub_pools(elec=0, mech=1))
        p.calculateSerialScheduleWithResources()
        assert a.returnAbsTimes()[0] == p.startTime, (
            "serial dropped/delayed an activity that skill substitution "
            "(ELEC→MECH) makes feasible at t0 (SC3)")

    def test_serial_substitution_does_not_overcommit_shared_skill(self):
        # A needs 1 ELEC (alt=[MECH]) → borrows MECH.  B needs 1 MECH (primary).
        # Only 1 MECH exists, so A and B must serialize.  If consumption isn't
        # substitution-resolved, A is booked as ELEC and B sees MECH free →
        # both start at t0, over-committing the single MECH.  (Guards the
        # consumption-accounting side.)
        s = Activity("START", 0.0)
        a = Activity("A", 5.0, required_resources=[
            {'skill_type': 'ELEC', 'crew_count': 1, 'alternative_skill_types': ['MECH']}])
        b = Activity("B", 5.0, required_resources=[
            {'skill_type': 'MECH', 'crew_count': 1}])
        e = Activity("END", 0.0)
        fwd = {s: [a, b], a: [e], b: [e], e: []}
        p = _build(fwd, pools=_sub_pools(elec=0, mech=1))
        p.calculateSerialScheduleWithResources()

        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        assert a_st is not None and b_st is not None, (
            "both A (via ELEC→MECH substitution) and B must be scheduled (SC3)")
        # They share the single MECH unit → intervals must not overlap.
        non_overlapping = a_et <= b_st or b_et <= a_st
        assert non_overlapping, (
            "A (substituted onto MECH) and B (needs MECH) overlap on a single "
            "MECH unit — serial consumption wasn't substitution-resolved and "
            "over-committed the pool (SC3)")


# ===========================================================================
# Round 2 — Cluster 11: augmented-graph location arcs (all overlapping pairs)
# ---------------------------------------------------------------------------
# Finding B5 (devLogs/PERT_MANUAL_REVIEW_2026-09-03.md).  `_build_augmented_graph`
# adds a serialization arc for two tasks that overlap at a max_tasks==1 zone, but
# the scan visited only *consecutive* start-sorted pairs.  A non-adjacent
# overlapping pair (a long activity spanning a short one, then a third that
# overlaps the long one but not the short one) got no arc — directly or
# transitively — so the constrained critical-chain / total-float analytics
# (_compute_actual_tf_proxy) under-connect the graph.  The fix scans all pairs
# per zone (still earlier-start → later-start, so the DAG stays acyclic).
#
# Latent behind a correct scheduler (which never overlaps tasks at a
# max_tasks==1 zone); the test drives the function directly.
# Repro seed: devLogs/repros/repro_b5_locpairs.py
# ===========================================================================

def _single_task_zone_pool(zone='ZONE1'):
    """LocationPool with one max_tasks==1 zone spanning the whole horizon."""
    lp = LocationPool()
    lp.locations[zone] = LocationAvailability(
        zone, 'single-task zone',
        [{'start_date': _START, 'end_date': _FAR, 'max_concurrent_tasks': 1}])
    return lp


def _reachable(augmented, src, dst):
    """DFS over the augmented adjacency map: is dst reachable from src?"""
    seen, stack = set(), [src]
    while stack:
        u = stack.pop()
        for v in augmented.get(u, []):
            if v is dst:
                return True
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return False


class TestAugmentedGraphSerializesAllZonePairs:
    """B5 — every pair of tasks that overlaps at a max_tasks==1 zone must be
    serialized in the augmented graph (directly or transitively), not just the
    start-adjacent ones."""

    def _place(self, act, h0, h1, zone='ZONE1'):
        act.zone_ids = [zone]
        act.startTime = _START + timedelta(hours=h0)
        act.endTime = _START + timedelta(hours=h1)

    def _pert_abc(self):
        # A[0,10] spans B[1,2] and overlaps C[3,13]; B and C do NOT overlap.
        # A,B,C are parallel in precedence, so the only arcs among them come
        # from the location-binding block.
        s = Activity("START", 0.0)
        a, b, c = Activity("A", 10.0), Activity("B", 1.0), Activity("C", 10.0)
        e = Activity("END", 0.0)
        fwd = {s: [a, b, c], a: [e], b: [e], c: [e], e: []}
        p = _build(fwd, pools=(ResourcePool(), EquipmentPool(),
                               _single_task_zone_pool()))
        self._place(a, 0, 10)
        self._place(b, 1, 2)
        self._place(c, 3, 13)
        return p, a, b, c

    def test_nonadjacent_overlap_is_serialized(self):
        # The load-bearing assertion: A and C overlap at ZONE1 (max_tasks==1)
        # but are the non-adjacent pair the consecutive-only scan skipped.
        p, a, b, c = self._pert_abc()
        augmented = p._build_augmented_graph()
        assert _reachable(augmented, a, c), (
            "A and C overlap at a max_tasks==1 zone but no serialization arc "
            "(direct or transitive) connects them — the location scan missed "
            "the non-adjacent (A,C) pair (B5)")

    def test_consecutive_overlap_still_serialized(self):
        # Control: the basic adjacent-overlap case must keep its arc, and the
        # arc direction must stay earlier-start → later-start (DAG preserved).
        p, a, b, c = self._pert_abc()
        augmented = p._build_augmented_graph()
        assert b in augmented.get(a, []), "A→B (adjacent overlap) arc lost"
        # No reverse arc: later-start C must not point back to earlier-start A.
        assert a not in augmented.get(c, []), "reverse arc C→A would break the DAG"
