"""
Pass 4 — Constraint interaction tests.

Combinations most likely to produce wrong answers silently, exercised together
in the same schedule so that one constraint cannot mask another.

Coverage:
  1. Lag + time window          — lag-driven ES meets window boundary; LF window
                                   propagates backward through lag to predecessor
  2. Mode switch + consumables  — consumable requirement updates when mode changes;
                                   feasibility check uses post-switch requirement
  3. Replan + window violations — pre-replan violations do not contaminate the
                                   post-replan fitness score
  4. System state + equipment zone — activity holding a state lock while using
                                   zone-locked equipment; both constraints enforced
  5. Shift calendar + lag (multi-day) — lag spanning multiple off-shift periods;
                                   successor snaps to the correct shift opening
"""

import pytest
from datetime import datetime, timedelta

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    ResourcePool, EquipmentPool, LocationPool,
    EquipmentAvailability, SystemStatePool, ConsumablePool,
)

TOL = 1e-6   # hours
TOL_S = 2    # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _build(fwd, lag_dict=None, start_time=None, pools=None,
           consumable_pool=None, system_state_pool=None,
           working_hours_per_day=None, shift_start_hour=None):
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
    if system_state_pool is not None:
        p.system_state_pool = system_state_pool
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


def _offset_h(act, t0):
    """Return (start_h, end_h) as offsets from t0."""
    st, et = act.returnAbsTimes()
    if st is None:
        return None, None
    return ((st - t0).total_seconds() / 3600.0,
            (et - t0).total_seconds() / 3600.0)


# ===========================================================================
# 1 — Lag + time-window interaction
# ===========================================================================

class TestLagWindowInteraction:
    """
    Tests where lag and time-window constraints interact — not exercised by
    either constraint alone.
    """

    def test_lag_hits_window_open_exactly(self):
        """
        A(4) --[lag=4]--> B(3).  B.window = [8h, 15h].

        Lag-driven ES_B = 4+4 = 8 = window_earliest.
        The two constraints agree to the hour — no infeasibility.

        CPM expected: ES_B = 8, EF_B = 11, slack = 0, window_infeasible = False.
        Scheduler expected: B starts T0+8h.
        """
        T0 = datetime(2026, 1, 1)
        start = _act("START", 0.0)
        a     = _act("A",     4.0)
        b     = _act("B",     3.0)
        b.time_windows = [{'earliest': 8.0, 'latest': 15.0}]
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd, lag_dict={(a, b): 4.0}, start_time=T0)

        ib = p.infoDict[b]
        assert abs(ib['es'] - 8.0) < TOL,  f"ES_B expected 8.0, got {ib['es']}"
        assert abs(ib['ef'] - 11.0) < TOL, f"EF_B expected 11.0, got {ib['ef']}"
        assert not ib['window_infeasible'],  "window should be feasible (lag == west)"

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "lag hits window open")
        sh, eh = _offset_h(b, T0)
        assert abs(sh - 8.0) < 1 / 3600, f"B start expected 8.0h, got {sh:.4f}"

    def test_lag_pushes_past_window_close_propagates_to_predecessor(self):
        """
        A(4) --[lag=8]--> B(3).  B.window_latest_finish = 10h.

        Lag gives ES_B = 12h.  CPM backward: LF_B = 10, LS_B = 7.
        Backward sweep propagates through the lag to A:
            LF_A = LS_B − lag = 7 − 8 = −1

        Both A and B end up with large negative slack, signalling that meeting
        B's deadline requires A to finish before the project even starts — a
        truly infeasible schedule.
        """
        start = _act("START", 0.0)
        a     = _act("A",     4.0)
        b     = _act("B",     3.0, window_latest_finish_hours=10.0)
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd, lag_dict={(a, b): 8.0})

        ia, ib = p.infoDict[a], p.infoDict[b]

        # B: window tightens LF
        assert abs(ib['lf'] - 10.0) < TOL, f"LF_B expected 10.0, got {ib['lf']}"
        assert abs(ib['ls'] -  7.0) < TOL, f"LS_B expected 7.0, got {ib['ls']}"
        assert ib['window_infeasible'], "B must be flagged as window_infeasible"

        # A: backward-swept through lag — LF_A = LS_B − lag = 7 − 8 = −1
        assert abs(ia['lf'] - (-1.0)) < TOL, f"LF_A expected −1.0, got {ia['lf']}"
        assert ia['slack'] < 0, f"A must have negative slack, got {ia['slack']}"

    def test_window_earliest_and_lag_both_delay_start(self):
        """
        A(2) --[lag=3]--> B(3).  B.window_earliest = 8.

        Lag gives ES_B = 5; window pushes to 8 (window dominates).
        Both apply simultaneously — the effective ES is max(5, 8) = 8.

        Scheduler: B starts at T0+8h.
        """
        T0 = datetime(2026, 1, 1)
        start = _act("START", 0.0)
        a     = _act("A",     2.0)
        b     = _act("B",     3.0, window_earliest_start_hours=8.0)
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd, lag_dict={(a, b): 3.0}, start_time=T0)

        # CPM: ES_B = max(2+3, 8) = 8
        assert abs(p.infoDict[b]['es'] - 8.0) < TOL

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "window + lag both delay start")
        sh, _ = _offset_h(b, T0)
        assert abs(sh - 8.0) < 1 / 3600, f"B start expected 8.0h, got {sh:.4f}"


# ===========================================================================
# 2 — Mode switch + consumables
# ===========================================================================

class TestModeSwitchConsumables:
    """
    An activity's consumable requirement must update when its mode changes.
    The feasibility check must use the post-switch requirement.
    """

    def _build_chain(self, pool):
        T0    = datetime(2026, 1, 1)
        start = Activity("START", 0.0)
        a     = Activity("A",     4.0)   # default: heavy mode (5 units)
        a.modes = [
            {
                'mode_id': 'heavy',
                'duration': 4.0,
                'required_resources': [],
                'required_equipment': [],
                'required_consumables': [{'item_id': 'SEAL', 'quantity_needed': 5.0}],
            },
            {
                'mode_id': 'light',
                'duration': 6.0,
                'required_resources': [],
                'required_equipment': [],
                'required_consumables': [{'item_id': 'SEAL', 'quantity_needed': 2.0}],
            },
        ]
        # Activate heavy mode explicitly so required_consumables are loaded
        a.set_mode('heavy')
        end   = Activity("END",   0.0)
        fwd   = {start: [a], a: [end], end: []}
        p = _build(fwd, start_time=T0, consumable_pool=pool)
        return p, a

    def _pool(self, total=3.0):
        return ConsumablePool.from_json([{
            'item_id': 'SEAL',
            'description': 'Test seal',
            'total_quantity': total,
        }])

    def test_heavy_mode_exceeds_pool(self):
        """Heavy mode needs 5 units; pool has 3 → infeasible."""
        pool = self._pool(3.0)
        assert not pool.fits('SEAL', 5.0), \
            "heavy mode (5 units) should NOT fit in pool of 3"

    def test_light_mode_fits_pool(self):
        """Light mode needs 2 units; pool has 3 → feasible."""
        pool = self._pool(3.0)
        assert pool.fits('SEAL', 2.0), \
            "light mode (2 units) should fit in pool of 3"

    def test_consumable_requirement_updates_after_mode_switch(self):
        """
        After set_modes('light'), A.required_consumables reflects the
        light-mode value (2 units, not 5).
        """
        pool = self._pool(3.0)
        p, a = self._build_chain(pool)

        # Confirm heavy mode is active
        reqs_before = a.getRequiredConsumables()
        assert any(r['quantity_needed'] == 5.0 for r in reqs_before), \
            "heavy mode should require 5 units before switch"

        p.set_modes({'A': 'light'})

        reqs_after = a.getRequiredConsumables()
        assert any(r['quantity_needed'] == 2.0 for r in reqs_after), \
            "light mode should require 2 units after switch"
        assert all(r['quantity_needed'] != 5.0 for r in reqs_after), \
            "heavy-mode requirement (5) must not persist after mode switch"

    def test_scheduler_uses_post_switch_consumable_requirement(self):
        """
        After switching to 'light' mode, A schedules successfully with a
        pool of 3 units (which heavy mode would have exhausted).
        """
        pool = self._pool(3.0)
        p, a = self._build_chain(pool)
        p.set_modes({'A': 'light'})

        result = p.calculateScheduleWithResources()
        assert_valid_schedule(p, "mode-switch consumable schedule")

        assert result['n_completed'] == len(p.infoDict), \
            "All activities must complete when light mode fits the pool"
        # Pool has 3; light mode consumes 2 — 1 unit should remain
        assert abs(pool.remaining.get('SEAL', 0.0) - 1.0) < TOL, \
            f"Expected 1 SEAL remaining after light-mode run, got {pool.remaining.get('SEAL')}"


# ===========================================================================
# 3 — Replan + window violations
# ===========================================================================

class TestReplanWindowViolations:
    """
    Violations accumulated during the initial scheduling run must not inflate
    the violation count reported for a subsequent replan.

    Mechanism: _partial_reset captures len(_window_violations) into
    _window_violations_baseline.  calculateScheduleWithResources_from slices
    result['window_violations'] from that baseline.  compute_fitness() reads
    from _last_schedule_result which is also sliced.
    """

    def _build_with_impossible_window(self):
        """
        START → A(2) → B(2) → END.
        B.window_latest_finish = 1h — impossible: B can start no earlier than
        t=2h (after A), so it would end at t=4h which exceeds the wlf of 1h.
        """
        T0    = datetime(2026, 1, 1)
        start = _act("START", 0.0)
        a     = _act("A",     2.0)
        b     = _act("B",     2.0, window_latest_finish_hours=1.0)
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = _build(fwd, start_time=T0)
        return p, T0, a, b

    def test_initial_run_records_one_violation(self):
        """Initial schedule: B misses its window → exactly 1 violation."""
        p, T0, a, b = self._build_with_impossible_window()
        result = p.calculateScheduleWithResources()
        assert len(result['window_violations']) == 1, \
            f"Expected 1 window violation in initial run, got {len(result['window_violations'])}"
        assert result['window_violations'][0]['activity'] == 'B'

    def test_replan_violation_count_is_isolated_from_initial_run(self):
        """
        After the initial run (1 violation), replan at t=1h.
        B is still pending with the same impossible window → 1 new violation.
        result_replan['window_violations'] must have exactly 1 entry (the replan's
        violation), NOT 2 (which it would be if the baseline weren't captured).
        """
        p, T0, a, b = self._build_with_impossible_window()

        result_initial = p.calculateScheduleWithResources()
        assert len(result_initial['window_violations']) == 1

        # Replan at t=1h: A is in progress (started t=0, ends t=2), B pending
        result_replan = p.replan(current_time_hours=1.0)

        assert len(result_replan['window_violations']) == 1, (
            f"Replan must report its own violation only (1), "
            f"got {len(result_replan['window_violations'])}"
        )

    def test_fitness_after_replan_counts_only_new_violations(self):
        """
        compute_fitness() after a replan must count only the violations
        produced during the replan (1), not the accumulated total (2).
        """
        p, T0, a, b = self._build_with_impossible_window()
        p.calculateScheduleWithResources()
        p.replan(current_time_hours=1.0)

        fitness = p.compute_fitness()
        assert fitness['n_window_violations'] == 1, (
            f"Fitness must count only replan violations; "
            f"expected 1, got {fitness['n_window_violations']}"
        )

    def test_baseline_set_at_replan_start(self):
        """
        _window_violations_baseline must equal len(_window_violations) at the
        moment _partial_reset is called — i.e. the length after the initial run.
        """
        p, T0, a, b = self._build_with_impossible_window()
        p.calculateScheduleWithResources()
        n_initial = len(p._window_violations)   # 1 after initial run

        p.replan(current_time_hours=1.0)

        assert p._window_violations_baseline == n_initial, (
            f"Baseline expected {n_initial}, got {p._window_violations_baseline}"
        )


# ===========================================================================
# 4 — System state + equipment zone
# ===========================================================================

class TestSystemStateEquipmentZone:
    """
    An activity that simultaneously requires:
      - A specific plant-system state (SystemStatePool)
      - Zone-locked equipment (EquipmentPool + activity.zone_ids)

    Both constraints must be checked independently; neither should mask the
    other.
    """

    _START = datetime(2026, 1, 1)
    _END_DT = datetime(2026, 12, 31)
    _PERIOD = [{'start_date': _START, 'end_date': _END_DT, 'quantity_available': 1}]

    def _make_ep_with_zone(self, eq_id, zone_id):
        ep = EquipmentPool()
        ep.equipment[eq_id] = EquipmentAvailability(
            eq_id, f'desc-{eq_id}', self._PERIOD, zone_id=zone_id
        )
        return ep

    def _make_ssp(self, system_id, valid_states):
        return SystemStatePool.from_json([{
            'system_id':   system_id,
            'description': f'System {system_id}',
            'valid_states': valid_states,
        }])

    def test_single_activity_satisfies_both_constraints(self):
        """
        Activity A needs S1='ISOLATED' AND zone-locked equipment E1.
        With the correct system state and matching zone, A must start and
        complete.  Validator must report no violations.
        """
        T0 = self._START
        ep  = self._make_ep_with_zone('E1', 'ZONE_A')
        ssp = self._make_ssp('S1', ['ISOLATED', 'ENERGIZED'])

        start = _act("START", 0.0)
        a     = _act("A",     4.0,
                     required_equipment=[{'equipment_id': 'E1', 'quantity_needed': 1}],
                     required_system_states=[{'system_id': 'S1', 'required_state': 'ISOLATED'}],
                     zone_ids=['ZONE_A'])
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [end], end: []}
        p = _build(fwd, start_time=T0,
                   pools=(ResourcePool(), ep, LocationPool()),
                   system_state_pool=ssp)

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "state+zone single activity")
        assert len(p.completed) == len(p.infoDict), "A must complete"

    def test_conflicting_state_blocks_second_activity(self):
        """
        A needs S1='ISOLATED'; B needs S1='ENERGIZED'.

        Both start at t=0 with no precedence constraint and enough equipment.
        Since they require conflicting states, only one can run at a time.

        The scheduler picks one (A by TF priority); B waits until A completes.
        Expected: B starts no earlier than A.endTime.
        """
        T0 = self._START
        # Give each activity its own equipment so equipment isn't the bottleneck
        ep  = EquipmentPool()
        ep.equipment['E1'] = EquipmentAvailability('E1', 'E1', self._PERIOD)
        ep.equipment['E2'] = EquipmentAvailability('E2', 'E2', self._PERIOD)
        ssp = self._make_ssp('S1', ['ISOLATED', 'ENERGIZED'])

        start = _act("START", 0.0)
        a     = _act("A",     4.0,
                     required_equipment=[{'equipment_id': 'E1', 'quantity_needed': 1}],
                     required_system_states=[{'system_id': 'S1', 'required_state': 'ISOLATED'}])
        b     = _act("B",     3.0,
                     required_equipment=[{'equipment_id': 'E2', 'quantity_needed': 1}],
                     required_system_states=[{'system_id': 'S1', 'required_state': 'ENERGIZED'}])
        end   = _act("END",   0.0)
        fwd   = {start: [a, b], a: [end], b: [end], end: []}
        p = _build(fwd, start_time=T0,
                   pools=(ResourcePool(), ep, LocationPool()),
                   system_state_pool=ssp)

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "state conflict serialization")
        assert len(p.completed) == len(p.infoDict)

        # B must not start before A completes (state conflict forces serialization)
        _, a_et = a.returnAbsTimes()
        b_st, _ = b.returnAbsTimes()
        assert b_st >= a_et - timedelta(seconds=TOL_S), (
            f"B must wait for A to release S1 before starting; "
            f"B.start={b_st}  A.end={a_et}"
        )

    def test_compatible_state_allows_concurrency(self):
        """
        A and B both need S1='ISOLATED' — compatible shared lock.
        With enough equipment (2 units each), both can run simultaneously.

        Expected: both start at t=0.
        """
        T0 = self._START
        period_2 = [{'start_date': T0, 'end_date': self._END_DT,
                      'quantity_available': 2}]
        ep  = EquipmentPool()
        ep.equipment['E1'] = EquipmentAvailability('E1', 'E1', period_2)
        ssp = self._make_ssp('S1', ['ISOLATED'])

        start = _act("START", 0.0)
        a     = _act("A",     4.0,
                     required_equipment=[{'equipment_id': 'E1', 'quantity_needed': 1}],
                     required_system_states=[{'system_id': 'S1', 'required_state': 'ISOLATED'}])
        b     = _act("B",     3.0,
                     required_equipment=[{'equipment_id': 'E1', 'quantity_needed': 1}],
                     required_system_states=[{'system_id': 'S1', 'required_state': 'ISOLATED'}])
        end   = _act("END",   0.0)
        fwd   = {start: [a, b], a: [end], b: [end], end: []}
        p = _build(fwd, start_time=T0,
                   pools=(ResourcePool(), ep, LocationPool()),
                   system_state_pool=ssp)

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "compatible state concurrency")

        a_st, _ = a.returnAbsTimes()
        b_st, _ = b.returnAbsTimes()
        # Both should start at T0 (no blocker)
        assert abs((a_st - T0).total_seconds()) < TOL_S, \
            f"A should start at T0, got offset={(a_st-T0).total_seconds():.1f}s"
        assert abs((b_st - T0).total_seconds()) < TOL_S, \
            f"B should start at T0, got offset={(b_st-T0).total_seconds():.1f}s"

    def test_zone_locked_equipment_and_state_both_enforced(self):
        """
        A needs zone-locked E1 (ZONE_A) AND state S1='ISOLATED'.
        B needs same E1 (zone=ZONE_A) but with state S1='ENERGIZED'.

        E1 has qty=1 → at most one can hold it.
        S1 state is conflicting → also at most one can hold it.
        Both constraints push toward serialization; neither alone is sufficient.

        Validator must pass (both constraints correctly tracked through completion).
        """
        T0  = self._START
        ep  = self._make_ep_with_zone('E1', 'ZONE_A')
        ssp = self._make_ssp('S1', ['ISOLATED', 'ENERGIZED'])

        start = _act("START", 0.0)
        a     = _act("A",     4.0,
                     required_equipment=[{'equipment_id': 'E1', 'quantity_needed': 1}],
                     required_system_states=[{'system_id': 'S1', 'required_state': 'ISOLATED'}],
                     zone_ids=['ZONE_A'])
        b     = _act("B",     3.0,
                     required_equipment=[{'equipment_id': 'E1', 'quantity_needed': 1}],
                     required_system_states=[{'system_id': 'S1', 'required_state': 'ENERGIZED'}],
                     zone_ids=['ZONE_A'])
        end   = _act("END",   0.0)
        fwd   = {start: [a, b], a: [end], b: [end], end: []}
        p = _build(fwd, start_time=T0,
                   pools=(ResourcePool(), ep, LocationPool()),
                   system_state_pool=ssp)

        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "zone+state double constraint")
        assert len(p.completed) == len(p.infoDict)

        # B must not overlap A
        _, a_et = a.returnAbsTimes()
        b_st, _ = b.returnAbsTimes()
        assert b_st >= a_et - timedelta(seconds=TOL_S), (
            f"B must wait for A to release both E1 and S1; "
            f"B.start={b_st}  A.end={a_et}"
        )


# ===========================================================================
# 5 — Shift calendar + lag (multi-day)
# ===========================================================================

class TestShiftCalendarLagMultiDay:
    """
    Lags that span multiple shift cycles.  The successor must snap to the
    correct shift opening across day boundaries.
    """

    def _build_shift(self, lag_h, T0=None):
        """START → A(2) --[lag_h]--> B(3) → END, 12h shift starting at 06:00."""
        if T0 is None:
            T0 = datetime(2026, 1, 1, 6, 0)
        start = _act("START", 0.0)
        a     = _act("A",     2.0)
        b     = _act("B",     3.0)
        end   = _act("END",   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        return _build(fwd, lag_dict={(a, b): lag_h}, start_time=T0,
                      working_hours_per_day=12, shift_start_hour=6), T0, b

    def test_lag_spans_two_off_shift_nights(self):
        """
        A(2) --[lag=36]--> B(3).  Shift: 12h/day (06:00–18:00).
        startTime = 2026-01-01 06:00.

        A ends at 08:00.  Lag end: 08:00 + 36h = 20:00 on 2026-01-02 (off-shift).
        Next shift open: 06:00 on 2026-01-03 = T0 + 48h.

        Expected: B starts at T0+48h, ends at T0+51h.
        """
        p, T0, b = self._build_shift(lag_h=36.0)
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "multi-day lag")

        sh, eh = _offset_h(b, T0)
        assert abs(sh - 48.0) < 1 / 60, \
            f"B start expected T0+48h (day-3 shift open), got T0+{sh:.4f}h"
        assert abs(eh - 51.0) < 1 / 60, \
            f"B end expected T0+51h, got T0+{eh:.4f}h"

    def test_lag_ends_at_shift_open_exact(self):
        """
        A(2) --[lag=22]--> B(3).  Lag end: 08:00 + 22h = 06:00 next day = T0+24h.
        Lag end falls exactly at shift open — B must start immediately.

        Expected: B starts at T0+24h, ends at T0+27h.
        """
        p, T0, b = self._build_shift(lag_h=22.0)
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "lag ends at shift open")

        sh, eh = _offset_h(b, T0)
        assert abs(sh - 24.0) < 1 / 60, \
            f"B start expected T0+24h (lag end == shift open), got T0+{sh:.4f}h"
        assert abs(eh - 27.0) < 1 / 60, \
            f"B end expected T0+27h, got T0+{eh:.4f}h"

    def test_lag_within_same_shift(self):
        """
        A(2) --[lag=2]--> B(3).  Lag end: 08:00 + 2h = 10:00 (same shift).
        No shift snap required — B starts at T0+4h.

        Expected: B starts at T0+4h, ends at T0+7h.
        """
        p, T0, b = self._build_shift(lag_h=2.0)
        p.calculateScheduleWithResources()
        assert_valid_schedule(p, "lag within shift")

        sh, eh = _offset_h(b, T0)
        assert abs(sh - 4.0) < 1 / 60, \
            f"B start expected T0+4h (no snap needed), got T0+{sh:.4f}h"
        assert abs(eh - 7.0) < 1 / 60, \
            f"B end expected T0+7h, got T0+{eh:.4f}h"
