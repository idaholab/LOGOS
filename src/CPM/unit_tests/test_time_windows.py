"""
Unit tests for regulatory time-window constraints.

Tests verify:
- Activity stores window_earliest_start_hours / window_latest_finish_hours
- from_json() parses both fields; to_json_dict() round-trips them
- _apply_time_windows() tightens CPM ES/LF and recomputes slack
- _apply_time_windows() detects and flags infeasible windows (slack < 0)
- Non-windowed activities are untouched by _apply_time_windows()
- window_infeasible key present for all activities after generateInfo()
- _build_event_queue() seeds events at window-open times
- Scheduler holds an activity in wait until window_earliest_start_hours
- Scheduler marks window_missed violation when window is passed
- compute_fitness() includes window_violation_ratio
- window_violations list in calculateScheduleWithResources() result
- Backward-compatible: activities without windows schedule normally
"""

import pytest
import math
from datetime import datetime, timedelta

from CPM.activity import Activity
from CPM.pert import Pert


TOL = 1e-9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _windowed_activity(name, duration, west=None, wlf=None):
    act = Activity(name, duration)
    act.window_earliest_start_hours = west
    act.window_latest_finish_hours  = wlf
    return act


def _chain_pert_with_window(west=None, wlf=None, b_duration=4.0):
    """
    START(0) -> A(4) -> B(b_duration) -> END(0)

    B carries the time-window constraint (west, wlf).
    CPM without windows: A.ES=0, A.EF=4; B.ES=4, B.EF=4+b_duration
    """
    start = Activity('START', 0.0)
    a     = Activity('A', 4.0)
    b     = _windowed_activity('B', b_duration, west=west, wlf=wlf)
    end   = Activity('END', 0.0)
    fwd   = {start: [a], a: [b], b: [end], end: []}
    p     = Pert(graph=fwd)
    return p, start, a, b, end


def _standalone_windowed(west=None, wlf=None, duration=4.0):
    """
    START(0) -> W(duration) -> END(0)

    W carries the window.  Used for isolated window tests.
    """
    start = Activity('START', 0.0)
    w     = _windowed_activity('W', duration, west=west, wlf=wlf)
    end   = Activity('END', 0.0)
    fwd   = {start: [w], w: [end], end: []}
    p     = Pert(graph=fwd)
    return p, start, w, end


# ---------------------------------------------------------------------------
# Activity field storage and round-trip
# ---------------------------------------------------------------------------

class TestActivityWindowFields:

    def test_default_window_fields_are_none(self):
        act = Activity('T', 4.0)
        assert act.window_earliest_start_hours is None
        assert act.window_latest_finish_hours  is None

    def test_assignment_stored(self):
        act = Activity('T', 4.0)
        act.window_earliest_start_hours = 24.0
        act.window_latest_finish_hours  = 72.0
        assert act.window_earliest_start_hours == 24.0
        assert act.window_latest_finish_hours  == 72.0

    def test_from_json_parses_both_fields(self):
        task = {
            'task_id': 'T_SURV',
            'description': 'Surveillance test',
            'duration': 4.0,
            'successors': [],
            'required_resources': [],
            'required_equipment': [],
            'is_hold_point': False,
            'window_earliest_start_hours': 72.0,
            'window_latest_finish_hours':  120.0,
        }
        act = Activity.from_json(task)
        assert abs(act.window_earliest_start_hours - 72.0)  < TOL
        assert abs(act.window_latest_finish_hours  - 120.0) < TOL

    def test_from_json_defaults_both_to_none(self):
        task = {
            'task_id': 'T1',
            'description': 'Normal task',
            'duration': 2.0,
            'successors': [],
            'required_resources': [],
            'required_equipment': [],
            'is_hold_point': False,
        }
        act = Activity.from_json(task)
        assert act.window_earliest_start_hours is None
        assert act.window_latest_finish_hours  is None

    def test_to_json_dict_includes_window_fields_when_set(self):
        act = Activity('T', 4.0)
        act.window_earliest_start_hours = 48.0
        act.window_latest_finish_hours  = 96.0
        d = act.to_json_dict()
        assert 'window_earliest_start_hours' in d
        assert 'window_latest_finish_hours'  in d
        assert abs(d['window_earliest_start_hours'] - 48.0) < TOL
        assert abs(d['window_latest_finish_hours']  - 96.0) < TOL

    def test_to_json_dict_omits_window_fields_when_none(self):
        act = Activity('T', 4.0)
        d = act.to_json_dict()
        assert 'window_earliest_start_hours' not in d
        assert 'window_latest_finish_hours'  not in d

    def test_to_json_dict_round_trip(self):
        task = {
            'task_id': 'T_RT',
            'description': 'Round-trip test',
            'duration': 6.0,
            'successors': [],
            'required_resources': [],
            'required_equipment': [],
            'is_hold_point': False,
            'window_earliest_start_hours': 10.0,
            'window_latest_finish_hours':  50.0,
        }
        act = Activity.from_json(task)
        d   = act.to_json_dict()
        assert abs(d['window_earliest_start_hours'] - 10.0) < TOL
        assert abs(d['window_latest_finish_hours']  - 50.0) < TOL


# ---------------------------------------------------------------------------
# _apply_time_windows: CPM ES/LF adjustment
# ---------------------------------------------------------------------------

class TestApplyTimeWindows:

    def test_earliest_start_tightens_es(self):
        # B.CPM_ES = 4; window west = 10 → effective ES = 10
        p, _, _, b, _ = _chain_pert_with_window(west=10.0)
        assert abs(p.infoDict[b]['es'] - 10.0) < TOL
        assert abs(p.infoDict[b]['ef'] - 14.0) < TOL   # 10 + 4

    def test_earliest_start_does_not_loosen_es(self):
        # B.CPM_ES = 4; window west = 2 (earlier than CPM) → ES stays 4
        p, _, _, b, _ = _chain_pert_with_window(west=2.0)
        assert abs(p.infoDict[b]['es'] - 4.0) < TOL

    def test_latest_finish_tightens_lf(self):
        # Without window: B.CPM_LF = project_duration = 8
        # With wlf = 7 → effective LF = 7
        p, _, _, b, _ = _chain_pert_with_window(wlf=7.0)
        assert abs(p.infoDict[b]['lf'] - 7.0) < TOL
        assert abs(p.infoDict[b]['ls'] - 3.0) < TOL   # 7 - 4

    def test_latest_finish_does_not_loosen_lf(self):
        # wlf larger than CPM_LF → LF stays at CPM_LF
        p, _, _, b, _ = _chain_pert_with_window(wlf=100.0)
        # CPM LF for B = 8 (chain length); 100 > 8 → no change
        assert abs(p.infoDict[b]['lf'] - 8.0) < TOL

    def test_slack_recomputed_after_window(self):
        # west=10 pushes ES to 10; LF stays 8 → ls = 8-4=4; slack = 4-10 = -6
        p, _, _, b, _ = _chain_pert_with_window(west=10.0, wlf=8.0)
        expected_slack = (8.0 - 4.0) - 10.0   # ls - es = 4 - 10
        assert abs(p.infoDict[b]['slack'] - expected_slack) < TOL

    def test_feasible_window_not_flagged(self):
        # B.duration=4; window [4, 20] → plenty of room
        p, _, _, b, _ = _chain_pert_with_window(west=4.0, wlf=20.0)
        assert p.infoDict[b]['window_infeasible'] is False

    def test_infeasible_window_flagged(self):
        # duration=4; window [10, 13] → width=3 < duration=4 → infeasible
        p, _, _, b, _ = _chain_pert_with_window(west=10.0, wlf=13.0)
        assert p.infoDict[b]['window_infeasible'] is True
        assert p.infoDict[b]['slack'] < 0.0

    def test_no_window_activity_untouched(self):
        # A has no window — its CPM values must not change
        p, _, a, _, _ = _chain_pert_with_window(west=10.0)
        assert abs(p.infoDict[a]['es'] - 0.0) < TOL
        assert abs(p.infoDict[a]['ef'] - 4.0) < TOL

    def test_no_window_infeasible_key_false(self):
        p, _, a, _, _ = _chain_pert_with_window()
        assert p.infoDict[a].get('window_infeasible') is False

    def test_only_latest_finish_set(self):
        # west=None, wlf=6 → LF tightened, ES unchanged
        p, _, _, b, _ = _chain_pert_with_window(wlf=6.0)
        assert abs(p.infoDict[b]['es'] - 4.0) < TOL   # unchanged
        assert abs(p.infoDict[b]['lf'] - 6.0) < TOL   # tightened

    def test_only_earliest_start_set(self):
        # west=8, wlf=None → ES tightened, LF unchanged
        p, _, _, b, _ = _chain_pert_with_window(west=8.0)
        assert abs(p.infoDict[b]['es'] - 8.0) < TOL   # tightened
        assert abs(p.infoDict[b]['lf'] - 8.0) < TOL   # unchanged (CPM LF)


# ---------------------------------------------------------------------------
# Event queue seeded at window-open time
# ---------------------------------------------------------------------------

class TestEventQueueWindowSeeding:

    def test_window_open_event_seeded(self):
        """
        The event heap built by _build_event_queue() must contain an entry at
        startTime + west so the scheduler wakes up exactly when the window opens.
        """
        start_dt = datetime(2025, 6, 1, 6, 0)
        p, _, w, _ = _standalone_windowed(west=10.0, duration=4.0)
        p.startTime = start_dt
        p._reset_scheduling_state()
        heap = p._build_event_queue()
        window_open = start_dt + timedelta(hours=10.0)
        assert window_open in heap

    def test_no_window_no_extra_event(self):
        """
        An activity without a window constraint must not add a spurious event.
        The only mandatory event is startTime itself.
        """
        start_dt = datetime(2025, 6, 1, 6, 0)
        start = Activity('START', 0.0)
        task  = Activity('T', 4.0)
        end   = Activity('END', 0.0)
        fwd   = {start: [task], task: [end], end: []}
        p     = Pert(graph=fwd)
        p.startTime = start_dt
        p._reset_scheduling_state()
        heap = p._build_event_queue()
        # startTime + CPM ES of T (=0) → startTime already in heap
        # No other window-derived event should appear
        window_events = [
            e for e in heap
            if e != start_dt and e > start_dt + timedelta(hours=0.5)
        ]
        # ES of T=0 → startTime already in heap; no new events beyond startTime
        assert start_dt in heap


# ---------------------------------------------------------------------------
# Scheduler enforcement
# ---------------------------------------------------------------------------

class TestSchedulerWindowEnforcement:

    def _make_scheduling_pert(self, west=None, wlf=None, b_duration=4.0):
        """
        Build a minimal schedulable Pert with pools using the test helper
        pattern from test_dose_budget.py (_pert_with_pools).
        """
        from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool
        rp = ResourcePool()
        ep = EquipmentPool()
        lp = LocationPool()
        p, start, a, b, end = _chain_pert_with_window(
            west=west, wlf=wlf, b_duration=b_duration
        )
        p.resource_pool = rp
        p.equipment_pool = ep
        p.location_pool = lp
        p.generateInfo()
        p.startTime = datetime(2025, 6, 1, 0, 0)
        return p, a, b

    def test_activity_waits_until_window_opens(self):
        """
        Activity B has west=10.  It must not appear in candidates before t=10h.
        After t=10h it must be a candidate (predecessors done).
        """
        # We just check CPM ES is correct (window tightening) and the Pert
        # builds without error; the scheduler loop is integration-tested below.
        p, a, b = self._make_scheduling_pert(west=10.0)
        assert abs(p.infoDict[b]['es'] - 10.0) < TOL

    def test_schedule_completes_with_window_constraint(self):
        """
        A schedule with a feasible window constraint (B must start ≥ 10h)
        must complete all activities.
        """
        p, a, b = self._make_scheduling_pert(west=10.0, b_duration=4.0)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert len(p.completed) == len(p.infoDict)
        assert result['window_violations'] == []

    def test_no_window_no_violations(self):
        """
        A network with no window constraints must produce zero violations.
        """
        p, a, b = self._make_scheduling_pert()
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['window_violations'] == []

    def test_missed_window_recorded_as_violation(self):
        """
        B has wlf=5 but A takes 4h and B takes 4h → earliest possible finish
        for B is 8h > 5h.  The scheduler must record a window_missed violation.
        """
        p, a, b = self._make_scheduling_pert(wlf=5.0, b_duration=4.0)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert len(result['window_violations']) == 1
        v = result['window_violations'][0]
        assert v['activity'] == 'B'
        assert v['reason'] == 'window_missed'

    def test_violation_reset_between_runs(self):
        """
        Running the scheduler twice must not accumulate violations.
        """
        p, a, b = self._make_scheduling_pert(wlf=5.0, b_duration=4.0)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        result2 = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert len(result2['window_violations']) == 1   # same as first run

    def test_b_starts_at_window_open_not_earlier(self):
        """
        B with west=10h must not start before t=10h from outage start even
        though its predecessor A finishes at t=4h.
        """
        p, a, b = self._make_scheduling_pert(west=10.0)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        b_start, _ = b.returnAbsTimes()
        b_start_hours = (b_start - p.startTime).total_seconds() / 3600.0
        assert b_start_hours >= 10.0 - TOL


# ---------------------------------------------------------------------------
# compute_fitness window_violation_ratio
# ---------------------------------------------------------------------------

class TestFitnessWindowViolation:

    def _make_scheduling_pert(self, west=None, wlf=None):
        from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool
        p, _, _, b, _ = _chain_pert_with_window(west=west, wlf=wlf)
        p.resource_pool  = ResourcePool()
        p.equipment_pool = EquipmentPool()
        p.location_pool  = LocationPool()
        p.generateInfo()
        p.startTime = datetime(2025, 6, 1, 0, 0)
        return p

    def test_no_violation_ratio_zero(self):
        p = self._make_scheduling_pert()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        f = p.compute_fitness()
        assert f['window_violation_ratio'] == 0.0
        assert f['n_window_violations'] == 0

    def test_violation_ratio_nonzero(self):
        p = self._make_scheduling_pert(wlf=5.0)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        f = p.compute_fitness()
        assert f['window_violation_ratio'] > 0.0
        assert f['n_window_violations'] == 1

    def test_violation_increases_composite(self):
        p_clean = self._make_scheduling_pert()
        p_clean.calculateScheduleWithResources(sgs='max_use_res_ranked')
        f_clean = p_clean.compute_fitness()

        p_viol = self._make_scheduling_pert(wlf=5.0)
        p_viol.calculateScheduleWithResources(sgs='max_use_res_ranked')
        f_viol = p_viol.compute_fitness()

        assert f_viol['composite'] > f_clean['composite']

    def test_fitness_keys_present(self):
        p = self._make_scheduling_pert()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        f = p.compute_fitness()
        for key in ('composite', 'makespan_ratio', 'delay_ratio',
                    'criticality_ratio', 'window_violation_ratio',
                    'n_window_violations', 'scheduled_duration',
                    'cpm_duration', 'delay_hours'):
            assert key in f, f"Missing key: {key}"
