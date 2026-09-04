"""
Pass 2 — Known-answer tests per constraint.

For each feature, at least one test where the **exact** expected output is
computed analytically and asserted precisely (not just "it scheduled").

Items covered (in priority order):
  1. Lag + time-window interaction          — TestLagWindowCPM, TestLagWindowScheduler
  2. Replan correctness                     — TestReplanCorrectness
  3. Critical chain after mode switch       — TestCriticalChainAfterModeSwitch
  4. Consumable restock cursor              — TestConsumableRestockCursor
  5. Multi-mode CPM                         — TestMultiModeCPM
  6. Shift calendar + lag                   — TestShiftCalendarLag
  7. System state + equipment zone          — TestSystemStateEquipmentZone
  8. Hold-point sequencing                  — TestHoldPointSequencing
"""

import pytest
from datetime import datetime, timedelta

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    ResourcePool, ResourceAvailability, EquipmentPool, EquipmentAvailability,
    LocationPool, ConsumablePool, SystemStatePool, OutageData,
)

TOL = 1e-9   # absolute tolerance for float comparisons


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _build(fwd, lag_dict=None, start_time=None, pools=None,
           consumable_pool=None, working_hours_per_day=None,
           shift_start_hour=None):
    """Build a Pert with minimal pools, optional consumable pool and shift calendar."""
    p = Pert(graph=fwd)
    rp, ep, lp = pools or _pools()
    p.crew_pool      = rp
    p.equipment_pool = ep
    p.location_pool  = lp
    if lag_dict:
        p.lag_dict = lag_dict
    p.startTime = start_time or datetime(2026, 1, 1)
    if consumable_pool is not None:
        p.consumable_pool = consumable_pool
    if working_hours_per_day is not None:
        p.working_hours_per_day = working_hours_per_day
    if shift_start_hour is not None:
        p.shift_start_hour = shift_start_hour
    p.generateInfo()
    return p


def _act(name, dur, **kwargs):
    a = Activity(name, dur)
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


def _offset_hours(act, start_time):
    """Return (start_h, end_h) of an activity as hours from start_time."""
    st, et = act.returnAbsTimes()
    if st is None:
        return None, None
    sh = (st - start_time).total_seconds() / 3600.0
    eh = (et - start_time).total_seconds() / 3600.0
    return sh, eh


# ===========================================================================
# Item 1  —  Lag + time-window interaction (CPM layer)
# ===========================================================================

class TestLagWindowCPM:
    """
    Network for all tests: START(0) → A(4) --[lag=2]--> B(3) → END(0)

    Unconstrained CPM values:
        A: ES=0, EF=4, LS=0, LF=4, slack=0
        B: ES=6, EF=9, LS=6, LF=9, slack=0   (lag pushes ES to 4+2=6)
        Project duration = 9 h
    """

    def _chain(self, b_west=None, b_wlf=None):
        start = _act("START", 0.0)
        a     = _act("A",     4.0)
        b     = _act("B",     3.0)
        end   = _act("END",   0.0)
        if b_west is not None:
            b.window_earliest_start_hours = b_west
        if b_wlf is not None:
            b.window_latest_finish_hours = b_wlf
        fwd = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd, lag_dict={(a, b): 2.0})
        return p, a, b

    def test_lag_dominates_when_window_earlier(self):
        """
        window_earliest = 3 < lag-driven ES = 6.

        The lag constraint is tighter; the window has no effect on ES.
        Expected: ES_B = 6, slack = 0, window_infeasible = False.
        """
        p, a, b = self._chain(b_west=3.0)
        ib = p.infoDict[b]
        assert abs(ib['es'] - 6.0) < TOL,   f"ES_B expected 6.0, got {ib['es']}"
        assert abs(ib['ef'] - 9.0) < TOL,   f"EF_B expected 9.0, got {ib['ef']}"
        assert abs(ib['slack'] - 0.0) < TOL, f"slack_B expected 0.0, got {ib['slack']}"
        assert ib['window_infeasible'] is False

    def test_window_tightens_es_when_later_than_lag(self):
        """
        window_earliest = 8 > lag-driven ES = 6.

        The window is a pure release date (no deadline), so it dominates ES and
        simply pushes the whole project later — it is NOT infeasible.  After the
        C1 fix, ``_apply_time_windows`` re-runs a forward relaxation (raising
        B's ES to 8, EF to 11, and propagating to END) and a backward pass
        re-anchored to the window-extended project end (11), so LS_B follows ES_B
        and slack_B is 0, not spuriously negative.

        Expected: ES_B = 8, EF_B = 11, LS_B = 8, slack = 0, window_infeasible = False.
        (Before C1, LS_B stayed at 6 from the un-extended backward pass, giving a
        bogus slack = 6 − 8 = −2 and a false infeasibility flag on a release date.)
        """
        p, a, b = self._chain(b_west=8.0)
        ib = p.infoDict[b]
        assert abs(ib['es'] - 8.0)  < TOL, f"ES_B expected 8.0, got {ib['es']}"
        assert abs(ib['ef'] - 11.0) < TOL, f"EF_B expected 11.0, got {ib['ef']}"
        assert abs(ib['ls'] - 8.0)  < TOL, f"LS_B expected 8.0, got {ib['ls']}"
        assert abs(ib['slack'] - 0.0) < TOL, f"slack_B expected 0.0, got {ib['slack']}"
        assert ib['window_infeasible'] is False

    def test_window_lf_propagates_backward_through_lag(self):
        """
        window_latest_finish = 8 (< unconstrained LF = 9).

        LF_B tightened to 8 → LS_B = 5.
        Backward sweep propagates through the lag:
            LF_A = LS_B − lag = 5 − 2 = 3   (< unconstrained LF = 4)
            LS_A = 3 − 4 = −1, slack_A = −1

        Both A and B should have negative slack after propagation.
        """
        p, a, b = self._chain(b_wlf=8.0)
        ia = p.infoDict[a]
        ib = p.infoDict[b]

        # B
        assert abs(ib['lf'] - 8.0) < TOL,   f"LF_B expected 8.0, got {ib['lf']}"
        assert abs(ib['ls'] - 5.0) < TOL,   f"LS_B expected 5.0, got {ib['ls']}"
        assert abs(ib['slack'] - (-1.0)) < TOL, f"slack_B expected -1.0, got {ib['slack']}"
        assert ib['window_infeasible'] is True

        # A — backward-swept through the lag
        assert abs(ia['lf'] - 3.0) < TOL,   f"LF_A expected 3.0, got {ia['lf']}"
        assert abs(ia['ls'] - (-1.0)) < TOL, f"LS_A expected -1.0, got {ia['ls']}"
        assert abs(ia['slack'] - (-1.0)) < TOL, f"slack_A expected -1.0, got {ia['slack']}"


# ===========================================================================
# Item 1  —  Lag + time-window interaction (scheduler layer)
# ===========================================================================

class TestLagWindowScheduler:
    """
    Verify that the scheduler's actual start time equals max(lag-driven ES, window_earliest).
    """

    def test_window_dominates_lag_exact_start(self):
        """
        A(4) --[lag=2]--> B(3).  B.window_earliest = 8.

        Lag alone would give B.start = T0 + 6h.
        Window pushes it to T0 + 8h.

        Expected: B starts at T0+8h, ends at T0+11h.
        """
        T0 = datetime(2026, 1, 1)
        start = _act("START", 0.0)
        a     = _act("A",     4.0)
        b     = _act("B",     3.0, window_earliest_start_hours=8.0)
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd, lag_dict={(a, b): 2.0}, start_time=T0)

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "lag+window scheduler")

        sh, eh = _offset_hours(b, T0)
        assert abs(sh - 8.0) < 1 / 3600, f"B start expected 8.0 h, got {sh:.4f}"
        assert abs(eh - 11.0) < 1 / 3600, f"B end expected 11.0 h, got {eh:.4f}"

    def test_lag_dominates_window_exact_start(self):
        """
        A(4) --[lag=2]--> B(3).  B.window_earliest = 3.

        Window (3h) is earlier than lag-driven ES (6h).
        Scheduler must respect the lag and start B at T0+6h, not T0+3h.

        Expected: B starts at T0+6h, ends at T0+9h.
        """
        T0 = datetime(2026, 1, 1)
        start = _act("START", 0.0)
        a     = _act("A",     4.0)
        b     = _act("B",     3.0, window_earliest_start_hours=3.0)
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd, lag_dict={(a, b): 2.0}, start_time=T0)

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "lag dominates window scheduler")

        sh, eh = _offset_hours(b, T0)
        assert abs(sh - 6.0) < 1 / 3600, f"B start expected 6.0 h, got {sh:.4f}"
        assert abs(eh - 9.0) < 1 / 3600, f"B end expected 9.0 h, got {eh:.4f}"


# ===========================================================================
# Item 2  —  Replan correctness
# ===========================================================================

class TestReplanCorrectness:
    """
    Network: START(0) → A(4) → B(3) → END(0), no resource constraints.
    startTime = T0.  Initial schedule: A=[T0, T0+4], B=[T0+4, T0+7].
    """

    def _build_chain(self):
        T0    = datetime(2026, 1, 1)
        start = _act("START", 0.0)
        a     = _act("A",     4.0)
        b     = _act("B",     3.0)
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p     = _build(fwd, start_time=T0)
        return p, T0, start, a, b, end

    def test_remaining_duration_anchoring(self):
        """
        Replan at t=2h: A was started 2h ago, has 2h remaining.

        _partial_reset must set A._remaining_duration = 2.0.
        The rescheduled portion (A finishes at t=4h, B follows) must produce
        scheduled_duration = 7.0h.
        """
        p, T0, start, a, b, end = self._build_chain()
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "initial schedule")

        # Verify initial schedule is as expected
        a_sh, a_eh = _offset_hours(a, T0)
        b_sh, b_eh = _offset_hours(b, T0)
        assert abs(a_sh - 0.0) < TOL, f"A initial start expected 0h, got {a_sh}"
        assert abs(a_eh - 4.0) < TOL, f"A initial end expected 4h, got {a_eh}"
        assert abs(b_sh - 4.0) < TOL, f"B initial start expected 4h, got {b_sh}"

        # Replan at t=2h (A in progress, B pending)
        result = p.replan(current_time_hours=2.0)

        # A._remaining_duration must be 2.0 (set by _partial_reset)
        assert abs(a._remaining_duration - 2.0) < TOL, \
            f"A._remaining_duration expected 2.0, got {a._remaining_duration}"

        # After replan: A finishes at t=4h, B starts t=4h, ends t=7h
        b_sh2, b_eh2 = _offset_hours(b, T0)
        assert abs(b_sh2 - 4.0) < TOL, \
            f"B post-replan start expected 4.0h, got {b_sh2}"
        assert abs(b_eh2 - 7.0) < TOL, \
            f"B post-replan end expected 7.0h, got {b_eh2}"

        # Project duration from outage start = 7.0h
        assert abs(result['scheduled_duration'] - 7.0) < TOL, \
            f"scheduled_duration expected 7.0h, got {result['scheduled_duration']}"

    def test_window_baseline_isolation(self):
        """
        The replan result's window_violations must contain only violations
        generated *during* that replan run, not those from the initial schedule.

        Mechanism: _partial_reset captures len(_window_violations) into
        _window_violations_baseline before the rescheduling loop runs.
        calculateScheduleWithResources_from slices result['window_violations']
        from that baseline index.

        Setup: no windows → 0 violations in both runs.  We verify that after
        two successive replans, each result independently reports its own
        (empty) violation list and _window_violations_baseline tracks correctly.
        """
        p, T0, start, a, b, end = self._build_chain()

        # Initial run: no violations
        result1 = p.calculateScheduleWithResources()
        assert result1['window_violations'] == [], "Initial run should have 0 violations"
        assert p._window_violations_baseline == 0

        # First replan at t=2h: no violations
        result2 = p.replan(current_time_hours=2.0)
        assert result2['window_violations'] == [], "Replan 1 should have 0 violations"
        # Baseline was captured at start of _partial_reset (from empty list)
        assert p._window_violations_baseline == 0, \
            f"_window_violations_baseline expected 0, got {p._window_violations_baseline}"

        # Second replan at t=5h (A done, B done): no violations
        result3 = p.replan(current_time_hours=5.0)
        assert result3['window_violations'] == [], "Replan 2 should have 0 violations"
        # Baseline must equal the violation count at the start of this replan
        assert len(result3['window_violations']) == 0
        # Overall _window_violations list should still be empty (no violations at all)
        assert p._window_violations == []


# ===========================================================================
# Item 3  —  Critical chain after mode switch
# ===========================================================================

class TestCriticalChainAfterModeSwitch:
    """
    Audit verdict (Pass 2 Item 3): CORRECT — a mode switch propagates the new
    duration through generateInfo() (CPM), the scheduler (_effective_duration
    returns act.duration for pending activities), and
    _compute_resource_constrained_chain().  The skip-reason's "stale act.duration"
    worry applies only to in-progress replan, not a pre-schedule mode switch
    (all activities are pending, so _remaining_duration is unused).

    A binding single-unit resource forces A and B to serialize, so the
    resource-constrained critical chain is driven by the *longer* of the two —
    a case distinct from the CPM-only coverage in TestMultiModeCPM.

        Network:  START(0) → {A, B} → END(0)
                  A and B each need 1 unit of R; the pool holds exactly 1, so
                  they cannot overlap.  A has modes slow(6)/fast(2); B is 3h.

    Slow (A=6):  A dominates → constrained chain = START→A→END (6h);
                 serialization makespan = 6+3 = 9h.
    Fast (A=2):  B now dominates → constrained chain flips to START→B→END (3h);
                 makespan = 2+3 = 5h.

    The chain *identity flips* A→B and the makespan drops 9h→5h only if the
    switch reaches every layer.  A stale act.duration would leave A dominant
    and the makespan at 9h.
    """

    _T0 = datetime(2026, 1, 1)

    def _build_serialized(self):
        rp = ResourcePool()
        rp.resources['R'] = ResourceAvailability(
            'R', [{'start_date': self._T0, 'end_date': datetime(2026, 2, 1),
                   'available_count': 1}])
        start = Activity("START", 0.0)
        a = Activity("A", 6.0,
                     required_resources=[{'skill_type': 'R', 'crew_count': 1}])
        a.modes = [
            {'mode_id': 'slow', 'duration': 6.0,
             'required_resources': [{'skill_type': 'R', 'crew_count': 1}],
             'required_equipment': []},
            {'mode_id': 'fast', 'duration': 2.0,
             'required_resources': [{'skill_type': 'R', 'crew_count': 1}],
             'required_equipment': []},
        ]
        b = Activity("B", 3.0,
                     required_resources=[{'skill_type': 'R', 'crew_count': 1}])
        end = Activity("END", 0.0)
        fwd = {start: [a, b], a: [end], b: [end], end: []}
        p = _build(fwd, start_time=self._T0,
                   pools=(rp, EquipmentPool(), LocationPool()))
        return p, a, b

    def _chain_names(self, p):
        return [x.returnName() for x in p.constrained_chain_list]

    def _chain_duration(self, p):
        return sum(p._effective_duration(x) for x in p.constrained_chain_list)

    def test_slow_mode_constrained_chain_driven_by_A(self):
        """Baseline slow mode: chain is START→A→END (6h), makespan 9h."""
        p, a, b = self._build_serialized()
        r = p.calculateScheduleWithResources()
        assert abs(r['scheduled_duration'] - 9.0) < TOL, \
            f"slow makespan expected 9.0h, got {r['scheduled_duration']}"
        names = self._chain_names(p)
        assert 'A' in names and 'B' not in names, \
            f"slow constrained chain expected to include A (6h) not B (3h); got {names}"
        assert abs(self._chain_duration(p) - 6.0) < TOL, \
            f"slow constrained-chain duration expected 6.0h, got {self._chain_duration(p)}"

    def test_fast_mode_flips_chain_to_B_and_shortens_makespan(self):
        """After set_modes({'A':'fast'}) the chain flips to B (3h) and makespan → 5h."""
        p, a, b = self._build_serialized()
        p.calculateScheduleWithResources()          # baseline (slow)
        p.set_modes({'A': 'fast'})                   # <-- the mode switch
        r = p.calculateScheduleWithResources()
        assert abs(r['scheduled_duration'] - 5.0) < TOL, \
            f"fast makespan expected 5.0h, got {r['scheduled_duration']}"
        names = self._chain_names(p)
        assert 'B' in names and 'A' not in names, \
            f"after A→fast the constrained chain must flip to B (3h > A 2h); got {names}"
        assert abs(self._chain_duration(p) - 3.0) < TOL, \
            f"fast constrained-chain duration expected 3.0h, got {self._chain_duration(p)}"


# ===========================================================================
# Item 4  —  Consumable restock cursor
# ===========================================================================

class TestConsumableRestockCursor:
    """
    Pool: SEAL with 2 units initially, restock +4 units delivered at t=6h.

    Network: START(0) → A(4) → B(3) → END(0)
        A needs 2 SEAL units   (starts t=0, consumes inventory to 0)
        B needs 4 SEAL units   (blocked until restock at t=6h)

    Expected:
        B starts at T0+6h (first moment pool has ≥4 units)
        B ends  at T0+9h
    """

    def test_activity_waits_for_restock(self):
        T0 = datetime(2026, 1, 1)
        pool = ConsumablePool.from_json([{
            'item_id': 'SEAL',
            'description': 'Test seal',
            'total_quantity': 2.0,
            'restocks': [{'delivery_hour': 6.0, 'quantity': 4.0}],
        }])

        start = _act("START", 0.0)
        a     = _act("A",     4.0,
                     required_consumables=[{'item_id': 'SEAL', 'quantity_needed': 2.0}])
        b     = _act("B",     3.0,
                     required_consumables=[{'item_id': 'SEAL', 'quantity_needed': 4.0}])
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd, start_time=T0, consumable_pool=pool)

        p.calculateScheduleWithResources()

        # All activities must complete
        assert len(p.completed) == len(p.infoDict), \
            f"Not all activities completed: {len(p.completed)}/{len(p.infoDict)}"

        assert_valid_schedule(p, "consumable restock cursor")

        # B must have waited for the t=6h restock
        b_sh, b_eh = _offset_hours(b, T0)
        assert abs(b_sh - 6.0) < 1 / 3600, \
            f"B start expected 6.0h (after restock), got {b_sh:.4f}"
        assert abs(b_eh - 9.0) < 1 / 3600, \
            f"B end expected 9.0h, got {b_eh:.4f}"

    def test_restock_arrives_exactly_at_start_time(self):
        """
        Restock arrives at the same hour B becomes eligible (t=4h, right when A finishes).
        No extra wait — B starts immediately at t=4h.
        """
        T0 = datetime(2026, 1, 1)
        pool = ConsumablePool.from_json([{
            'item_id': 'BOLT',
            'description': 'Bolt kit',
            'total_quantity': 0.0,          # starts empty
            'restocks': [{'delivery_hour': 4.0, 'quantity': 3.0}],
        }])
        start = _act("START", 0.0)
        a     = _act("A",     4.0)          # no consumables
        b     = _act("B",     2.0,
                     required_consumables=[{'item_id': 'BOLT', 'quantity_needed': 3.0}])
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd, start_time=T0, consumable_pool=pool)

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "restock at eligibility time")

        b_sh, b_eh = _offset_hours(b, T0)
        assert abs(b_sh - 4.0) < 1 / 3600, \
            f"B start expected 4.0h (restock arrives exactly when B eligible), got {b_sh:.4f}"
        assert abs(b_eh - 6.0) < 1 / 3600, \
            f"B end expected 6.0h, got {b_eh:.4f}"


# ===========================================================================
# Item 5  —  Multi-mode CPM
# ===========================================================================

class TestMultiModeCPM:
    """
    Network: START(0) → A → B(3) → END(0)
    A has two modes: 'fast' (duration=2), 'slow' (duration=6).
    Initial mode is 'slow' (A.duration=6 at construction).

    Slow-mode CPM:  project = 0+6+3 = 9h
    Fast-mode CPM:  project = 0+2+3 = 5h
    """

    def _build_multimode_chain(self):
        start = Activity("START", 0.0)
        a     = Activity("A",     6.0)   # default: slow
        a.modes = [
            {'mode_id': 'slow', 'duration': 6.0, 'required_resources': [],
             'required_equipment': []},
            {'mode_id': 'fast', 'duration': 2.0, 'required_resources': [],
             'required_equipment': []},
        ]
        b   = Activity("B",     3.0)
        end = Activity("END",   0.0)
        fwd = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd)
        return p, start, a, b, end

    def test_slow_mode_project_duration(self):
        """Default slow mode: project duration = 9h."""
        p, start, a, b, end = self._build_multimode_chain()
        assert abs(p.getProjectDuration() - 9.0) < TOL, \
            f"Slow project duration expected 9.0h, got {p.getProjectDuration()}"

    def test_fast_mode_reduces_project_duration(self):
        """After set_modes({'A': 'fast'}): project duration = 5h."""
        p, start, a, b, end = self._build_multimode_chain()
        p.set_modes({'A': 'fast'})
        assert abs(p.getProjectDuration() - 5.0) < TOL, \
            f"Fast project duration expected 5.0h, got {p.getProjectDuration()}"

    def test_fast_mode_cpm_values(self):
        """
        After set_modes({'A': 'fast'}):
            A: ES=0, EF=2, LS=0, LF=2, slack=0
            B: ES=2, EF=5, LS=2, LF=5, slack=0
        """
        p, start, a, b, end = self._build_multimode_chain()
        p.set_modes({'A': 'fast'})

        ia = p.infoDict[a]
        ib = p.infoDict[b]

        assert abs(ia['ef'] - 2.0) < TOL,   f"A EF expected 2.0, got {ia['ef']}"
        assert abs(ia['slack']) < TOL,        f"A slack expected 0, got {ia['slack']}"
        assert abs(ib['es'] - 2.0) < TOL,   f"B ES expected 2.0, got {ib['es']}"
        assert abs(ib['ef'] - 5.0) < TOL,   f"B EF expected 5.0, got {ib['ef']}"
        assert abs(ib['slack']) < TOL,        f"B slack expected 0, got {ib['slack']}"

    def test_slow_mode_restored_after_reset(self):
        """
        set_modes('fast') then set_modes('slow') restores original project duration.
        """
        p, start, a, b, end = self._build_multimode_chain()
        p.set_modes({'A': 'fast'})
        assert abs(p.getProjectDuration() - 5.0) < TOL

        p.set_modes({'A': 'slow'})
        assert abs(p.getProjectDuration() - 9.0) < TOL, \
            f"Restored slow mode: project expected 9.0h, got {p.getProjectDuration()}"

    def test_fast_mode_schedule_matches_cpm(self):
        """
        After set_modes('fast') the scheduler must produce the CPM duration (5h)
        when resources are unconstrained.

        Invariant: makespan == CPM_duration for unconstrained networks.
        """
        p, start, a, b, end = self._build_multimode_chain()
        p.set_modes({'A': 'fast'})
        result = p.calculateScheduleWithResources()
        assert_valid_schedule(p, "fast mode schedule")
        assert abs(result['scheduled_duration'] - 5.0) < TOL, \
            f"Fast-mode scheduled_duration expected 5.0h, got {result['scheduled_duration']}"


# ===========================================================================
# Item 6  —  Shift calendar + lag
# ===========================================================================

class TestShiftCalendarLag:
    """
    Shift: 12h/day, start at 06:00 (works 06:00–18:00).
    startTime = T0 = 2026-01-01 06:00  (outage starts as shift opens).

    Network: START(0) → A(2) --[lag=14]--> B(3) → END(0)
        A: starts at T0+0h (06:00), ends T0+2h (08:00)
        Lag end: 08:00 + 14h = 22:00 → off-shift
        Next shift opens: next day 06:00 = T0+24h

    Expected: B starts at T0+24h, ends at T0+27h.
    """

    def test_lag_end_in_off_shift_waits_for_next_shift(self):
        T0 = datetime(2026, 1, 1, 6, 0)  # outage starts as shift opens
        start = _act("START", 0.0)
        a     = _act("A",     2.0)
        b     = _act("B",     3.0)
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}

        p = _build(
            fwd,
            lag_dict={(a, b): 14.0},
            start_time=T0,
            working_hours_per_day=12,
            shift_start_hour=6,
        )

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "shift+lag")

        b_sh, b_eh = _offset_hours(b, T0)

        # B cannot start before the next shift opens at T0+24h
        # Allow up to 1-minute tolerance for floating-point event boundaries
        assert abs(b_sh - 24.0) < 1 / 60, \
            f"B start expected 24.0h (next shift after lag), got {b_sh:.4f}"
        assert abs(b_eh - 27.0) < 1 / 60, \
            f"B end expected 27.0h, got {b_eh:.4f}"

    def test_lag_end_in_shift_starts_immediately(self):
        """
        Lag end falls within the current shift — no extra wait.

        A(2) --[lag=4]--> B(3).  A ends at T0+2h (08:00), lag end = T0+6h (12:00).
        12:00 is within the 06:00–18:00 shift.

        Expected: B starts at T0+6h, ends at T0+9h.
        """
        T0 = datetime(2026, 1, 1, 6, 0)
        start = _act("START", 0.0)
        a     = _act("A",     2.0)
        b     = _act("B",     3.0)
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}

        p = _build(
            fwd,
            lag_dict={(a, b): 4.0},
            start_time=T0,
            working_hours_per_day=12,
            shift_start_hour=6,
        )

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "lag within shift")

        b_sh, b_eh = _offset_hours(b, T0)
        assert abs(b_sh - 6.0) < 1 / 60, \
            f"B start expected 6.0h (lag end in shift), got {b_sh:.4f}"
        assert abs(b_eh - 9.0) < 1 / 60, \
            f"B end expected 9.0h, got {b_eh:.4f}"


# ===========================================================================
# Item 7  —  System state + equipment zone
# ===========================================================================

class TestSystemStateEquipmentZone:
    """
    Audit verdict (Pass 2 Item 7): CORRECT — the system-state and equipment-zone
    checks are independent (both parallel _fits_with_tentative and serial
    _serial_check_feasibility gate on each), so neither masks the other.

    Known answer: A needs S1='ISOLATED' AND zone-locked E1 (ZONE_A, qty 1);
    B needs S1='ENERGIZED' AND the same E1.  Both constraints independently
    force serialization → B starts exactly when A releases both at t=4h.

    The broader interplay (concurrency under compatible states, single-activity
    satisfaction, independent enforcement) is covered in
    tests/unit_tests/CPM/test_interactions.py::TestSystemStateEquipmentZone.
    """

    _T0 = datetime(2026, 1, 1)
    _PERIOD = [{'start_date': _T0, 'end_date': datetime(2026, 12, 31),
                'quantity_available': 1}]

    def test_activity_holding_state_and_zone_locked_equipment(self):
        ep = EquipmentPool()
        ep.equipment['E1'] = EquipmentAvailability('E1', 'E1', self._PERIOD,
                                                    zone_id='ZONE_A')
        ssp = SystemStatePool.from_json([{
            'system_id': 'S1', 'description': 'S1',
            'valid_states': ['ISOLATED', 'ENERGIZED']}])

        start = Activity("START", 0.0)
        a = _act("A", 4.0,
                 required_equipment=[{'equipment_id': 'E1', 'quantity_needed': 1}],
                 required_system_states=[{'system_id': 'S1', 'required_state': 'ISOLATED'}],
                 zone_ids=['ZONE_A'])
        b = _act("B", 3.0,
                 required_equipment=[{'equipment_id': 'E1', 'quantity_needed': 1}],
                 required_system_states=[{'system_id': 'S1', 'required_state': 'ENERGIZED'}],
                 zone_ids=['ZONE_A'])
        end = Activity("END", 0.0)
        fwd = {start: [a, b], a: [end], b: [end], end: []}
        p = _build(fwd, start_time=self._T0,
                   pools=(ResourcePool(), ep, LocationPool()))
        p.system_state_pool = ssp

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "state+zone serialization")
        assert len(p.completed) == len(p.infoDict), "both A and B must complete"

        _, a_et = a.returnAbsTimes()
        b_st, _ = b.returnAbsTimes()
        assert b_st >= a_et - timedelta(seconds=1e-6), \
            f"B must wait for A to release both E1 and S1; B.start={b_st} A.end={a_et}"
        h = (b_st - self._T0).total_seconds() / 3600.0
        assert abs(h - 4.0) < 1e-6, \
            f"B expected to start at t=4h (A's duration); got {h}h"


# ===========================================================================
# Item 8  —  Hold-point sequencing
# ===========================================================================

class TestHoldPointSequencing:
    """
    Audit verdict (Pass 2 Item 8): CORRECT (implemented) — hold points are
    enforced by a build-time precedence-edge injection in
    _build_graph_from_outage_data: for each hold point, a hold → blocked edge
    is added for every entry in blocks_tasks (cycle-guarded).  This makes the
    blocked task a graph successor of the hold point, so CPM and the scheduler
    naturally prevent it from starting early.  Post-schedule, the validator's
    _check_hold_points independently flags any violation.

    The injection only fires on the outage_data build path, so this test
    constructs the Pert from task dicts (not a raw graph).

        START(0) → {HP, B} → END(0)
        HP is a 2h hold point that blocks B (3h) via blocks_tasks=['B'].

    Known answer: the HP→B edge exists and B.start == HP.end == t=2h.
    """

    def _build_holdpoint_pert(self):
        tasks = [
            {'task_id': 'START', 'description': 's', 'duration': 0.0,
             'successors': ['HP', 'B'], 'required_resources': [], 'required_equipment': []},
            {'task_id': 'HP', 'description': 'hold', 'duration': 2.0,
             'successors': ['END'], 'required_resources': [], 'required_equipment': [],
             'is_hold_point': True, 'hold_point_type': 'NRC', 'blocks_tasks': ['B']},
            {'task_id': 'B', 'description': 'blocked', 'duration': 3.0,
             'successors': ['END'], 'required_resources': [], 'required_equipment': []},
            {'task_id': 'END', 'description': 'e', 'duration': 0.0,
             'successors': [], 'required_resources': [], 'required_equipment': []},
        ]
        od = OutageData(
            outage_config={'outage_id': 'T', 'start_date': '2026-01-01',
                           'working_hours_per_day': 24},
            tasks=tasks, crew_pool=ResourcePool(), equipment_pool=EquipmentPool(),
            location_pool=LocationPool(), consumable_pool=ConsumablePool(),
            system_state_pool=SystemStatePool())
        return Pert(outage_data=od)

    def test_hold_point_edge_injected(self):
        """The build-time injection wires HP→B from blocks_tasks."""
        p = self._build_holdpoint_pert()
        hp = p.task_to_activity['HP']
        b = p.task_to_activity['B']
        assert b in p.forwardDict[hp], \
            "hold-point injection must add the HP→B precedence edge from blocks_tasks"

    def test_blocked_tasks_wait_for_hold_point(self):
        """A real schedule places B no earlier than HP completes (t=2h)."""
        p = self._build_holdpoint_pert()
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "hold-point sequencing")
        hp = p.task_to_activity['HP']
        b = p.task_to_activity['B']
        _, hp_et = hp.returnAbsTimes()
        b_st, _ = b.returnAbsTimes()
        assert b_st >= hp_et - timedelta(seconds=1e-6), \
            f"blocked task B must not start before hold point HP releases; " \
            f"B.start={b_st} HP.end={hp_et}"
        h = (b_st - p.startTime).total_seconds() / 3600.0
        assert abs(h - 2.0) < 1e-6, f"B expected to start at t=2h (after HP); got {h}h"
