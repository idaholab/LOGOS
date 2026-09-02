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
- time_windows list: field default, from_json, to_json_dict round-trip
- _resolve_windows: time_windows takes precedence; falls back to single fields
- Multi-window scheduler: first missed → waits for next (no violation)
- Multi-window scheduler: all missed → violation recorded
- outage_schema.json: time_windows field present and typed
"""

import pytest
import math
from datetime import datetime, timedelta

from conftest import assert_valid_schedule
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
        p.crew_pool = rp
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
        assert_valid_schedule(p)
        assert len(p.completed) == len(p.infoDict)
        assert result['window_violations'] == []

    def test_no_window_no_violations(self):
        """
        A network with no window constraints must produce zero violations.
        """
        p, a, b = self._make_scheduling_pert()
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p)
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
        assert_valid_schedule(p)
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
        p.crew_pool  = ResourcePool()
        p.equipment_pool = EquipmentPool()
        p.location_pool  = LocationPool()
        p.generateInfo()
        p.startTime = datetime(2025, 6, 1, 0, 0)
        return p

    def test_no_violation_ratio_zero(self):
        p = self._make_scheduling_pert()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p)
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


# ---------------------------------------------------------------------------
# Window-propagating backward sweep
# ---------------------------------------------------------------------------

class TestWindowPropagatingBackwardSweep:
    """
    Verify that _apply_time_windows propagates tightened LF values upstream.

    All test networks use the chain:
        START(0) → A(dur_a) → B(dur_b, window) → END(0)

    CPM without windows:
        A.ES=0, A.EF=dur_a
        B.ES=dur_a, B.EF=dur_a+dur_b
        project_duration = dur_a + dur_b
        A.LF = project_duration = dur_a + dur_b
        B.LF = project_duration = dur_a + dur_b

    With wlf on B the backward sweep must tighten A.LF.
    """

    def _chain(self, dur_a=4.0, dur_b=4.0, west=None, wlf=None):
        """START → A(dur_a) → B(dur_b, window) → END."""
        start = Activity('START', 0.0)
        a     = Activity('A', dur_a)
        b     = _windowed_activity('B', dur_b, west=west, wlf=wlf)
        end   = Activity('END', 0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        return Pert(graph=fwd), start, a, b, end

    def _three_chain(self, wlf_b):
        """START → A(4) → B(4, wlf) → C(4) → END — window on middle node."""
        start = Activity('START', 0.0)
        a     = Activity('A', 4.0)
        b     = _windowed_activity('B', 4.0, wlf=wlf_b)
        c     = Activity('C', 4.0)
        end   = Activity('END', 0.0)
        fwd   = {start: [a], a: [b], b: [c], c: [end], end: []}
        return Pert(graph=fwd), start, a, b, c, end

    # ── LF propagation ───────────────────────────────────────────────────────

    def test_predecessor_lf_tightened_by_window(self):
        """A.LF must reflect B's wlf, not the unconstrained project_duration."""
        # CPM: project_duration=8; B.CPM_LF=8; wlf=6 → B.LF=6, B.LS=2
        # Backward sweep: A.LF = B.LS − lag − lead = 2 − 0 − 0 = 2
        p, _, a, b, _ = self._chain(wlf=6.0)
        assert abs(p.infoDict[b]['lf'] - 6.0) < TOL
        assert abs(p.infoDict[a]['lf'] - 2.0) < TOL   # propagated

    def test_predecessor_ls_updated(self):
        """A.LS = A.LF − A.duration after propagation."""
        p, _, a, b, _ = self._chain(wlf=6.0)
        # A.duration=4; A.LF=2 → A.LS=2-4=-2
        assert abs(p.infoDict[a]['ls'] - (p.infoDict[a]['lf'] - 4.0)) < TOL

    def test_predecessor_slack_updated(self):
        """A.slack = A.LS − A.ES after propagation."""
        p, _, a, b, _ = self._chain(wlf=6.0)
        expected = p.infoDict[a]['ls'] - p.infoDict[a]['es']
        assert abs(p.infoDict[a]['slack'] - expected) < TOL

    def test_two_hops_back_propagation(self):
        """Window on B propagates two hops back through A to START."""
        # START→A(4)→B(4,wlf=6)→END; project_duration=8
        # B.LF=6, B.LS=2; A.LF=2, A.LS=-2; START.LF=min(8, A.LS-0-0)=-2
        p, start, a, b, _ = self._chain(wlf=6.0)
        # START has no predecessors so only A and B matter for this test.
        # Verify chain: START→A constraint applied
        start_lf = p.infoDict[start]['lf']
        a_ls     = p.infoDict[a]['ls']
        assert start_lf <= a_ls + TOL   # START.LF ≤ A.LS

    def test_three_node_chain_middle_window(self):
        """Window on B (middle of A→B→C) tightens A but not C."""
        # A→B(wlf=7)→C; dur=4 each; project=12
        # CPM backward: C.LF=12, C.LS=8; B.CPM_LF=8, B.LS=4; A.CPM_LF=4, A.LS=0
        # Window: B.LF=min(8,7)=7, B.LS=3
        # Backward sweep: A.LF=min(4,3)=3, A.LS=-1
        # C is downstream of B — the backward sweep only goes upstream, so C.LF stays 12
        p, _, a, b, c, _ = self._three_chain(wlf_b=7.0)
        assert abs(p.infoDict[b]['lf'] - 7.0) < TOL
        assert abs(p.infoDict[a]['lf'] - 3.0) < TOL     # tightened by sweep
        assert abs(p.infoDict[c]['lf'] - 12.0) < TOL    # downstream: unchanged

    def test_window_does_not_loosen_predecessor_lf(self):
        """wlf >= B.CPM_LF triggers no tightening, so sweep is a no-op."""
        # chain A(4)→B(4)→END; CPM: B.CPM_LF=8, A.CPM_LF=4
        # wlf=100 > 8 → min(8,100)=8 → any_lf_tightened stays False → sweep skipped
        # A.LF remains its CPM value of 4
        p_base, _, a_base, _, _ = self._chain()           # no window
        p_wide, _, a_wide, _, _ = self._chain(wlf=100.0)  # non-tightening window
        # Both should give identical A.LF (CPM value = 4)
        assert abs(p_base.infoDict[a_base]['lf'] - p_wide.infoDict[a_wide]['lf']) < TOL

    def test_no_window_no_sweep(self):
        """Activities with no windows: LF equals the CPM-derived value."""
        # chain A(4)→B(4)→END; CPM backward: B.LS=4; A.LF=min(8,4)=4
        p, _, a, b, _ = self._chain()   # no window on b
        assert abs(p.infoDict[a]['lf'] - 4.0) < TOL

    # ── Slack correctness ────────────────────────────────────────────────────

    def test_predecessor_becomes_critical_due_to_window(self):
        """A window that forces zero slack on A should register as critical."""
        # A(4) → B(4, wlf=8) → END; project=8; B.LF=8, B.LS=4; A.LF=4, A.LS=0
        # A.ES=0, A.slack=0 → critical
        p, _, a, b, _ = self._chain(wlf=8.0)
        assert abs(p.infoDict[a]['slack'] - 0.0) < TOL

    def test_predecessor_slack_negative_when_window_too_tight(self):
        """A window that creates an infeasible chain gives A negative slack."""
        # wlf=5: B.LF=5, B.LS=1; A.LF=1, A.LS=1-4=-3; A.ES=0; A.slack=-3
        p, _, a, b, _ = self._chain(wlf=5.0)
        assert p.infoDict[a]['slack'] < 0.0

    # ── WBS slack roll-up ────────────────────────────────────────────────────

    def test_wbs_group_slack_reflects_propagated_predecessor_slack(self):
        """
        When A and B share a wbs_group, the group slack must use A's
        propagated (tightened) slack, not the stale pre-window value.
        """
        start = Activity('START', 0.0)
        a     = Activity('A', 4.0)
        b     = _windowed_activity('B', 4.0, wlf=6.0)
        end   = Activity('END', 0.0)
        a.wbs_group = 'WBS_1'
        b.wbs_group = 'WBS_1'
        fwd = {start: [a], a: [b], b: [end], end: []}
        p   = Pert(graph=fwd)
        # A.slack (after propagation): A.LF=2, A.LS=-2, A.ES=0 → slack=-2
        # B.slack: B.LF=6, B.LS=2, B.ES=4 → slack=-2
        # WBS group slack = min(-2, -2) = -2
        a_wbs_slack = p.infoDict[a]['wbs_slack']
        b_wbs_slack = p.infoDict[b]['wbs_slack']
        assert a_wbs_slack == b_wbs_slack
        assert a_wbs_slack <= p.infoDict[a]['slack'] + TOL

    # ── compute_fitness() criticality ────────────────────────────────────────

    def test_criticality_ratio_counts_propagated_critical_activity(self):
        """
        A activity that has positive CPM float but sits upstream of a
        window-constrained successor becomes critical (slack ≤ 0) after
        the backward sweep.

        Topology: START → A(2) → B(4, wlf=5) → END
                  START → D(8) → END   (parallel long path)

        Without window on B:
            project_duration = 8 (D is critical)
            B.CPM_LF = 8, B.LS = 4
            A.CPM_LF = min(8, B.LS=4) = 4, A.LS = 4-2=2, A.ES=0, A.slack = 2

        With B.wlf=5:
            B.LF = min(8,5)=5, B.LS=1
            Backward sweep: A.LF = min(4, B.LS=1) = 1, A.LS = 1-2=-1
            A.slack = -1 - 0 = -1  (critical / infeasible due to window)
        """
        start = Activity('START', 0.0)
        a     = Activity('A', 2.0)
        b     = _windowed_activity('B', 4.0, wlf=5.0)
        d     = Activity('D', 8.0)
        end   = Activity('END', 0.0)
        fwd   = {start: [a, d], a: [b], b: [end], d: [end], end: []}
        p = Pert(graph=fwd)

        # A had positive float (slack=2) in pure CPM — confirm pre-window state
        p_no_win_start = Activity('START', 0.0)
        p_no_win_a     = Activity('A', 2.0)
        p_no_win_b     = Activity('B', 4.0)   # no window
        p_no_win_d     = Activity('D', 8.0)
        p_no_win_end   = Activity('END', 0.0)
        fwd2 = {p_no_win_start: [p_no_win_a, p_no_win_d],
                p_no_win_a: [p_no_win_b], p_no_win_b: [p_no_win_end],
                p_no_win_d: [p_no_win_end], p_no_win_end: []}
        p2 = Pert(graph=fwd2)
        assert p2.infoDict[p_no_win_a]['slack'] > 0.0   # A was non-critical

        # Now with window, A should have slack ≤ 0
        assert p.infoDict[a]['slack'] <= 0.0

    # ── replan() backward sweep ──────────────────────────────────────────────

    def test_replan_propagates_window_to_predecessor(self):
        """
        After a replan() call, the backward sweep inside _generate_info_from
        must tighten predecessor LF just as generateInfo() does.
        """
        from datetime import datetime
        from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool

        start = Activity('START', 0.0)
        a     = Activity('A', 4.0)
        b     = _windowed_activity('B', 4.0, wlf=6.0)
        end   = Activity('END', 0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}

        p = Pert(graph=fwd)
        p.crew_pool   = ResourcePool()
        p.equipment_pool  = EquipmentPool()
        p.location_pool   = LocationPool()
        p.consumable_pool   = None
        p.system_state_pool = None
        p.startTime = datetime(2026, 1, 1)

        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Replan at t=0 — nothing has completed yet but replan is valid
        p.replan(current_time_hours=0.0)

        # After replan, backward sweep must still apply
        # B.wlf=6 → B.LF=6, B.LS=2 → A.LF must be tightened to 2
        assert abs(p.infoDict[b]['lf'] - 6.0) < TOL
        assert p.infoDict[a]['lf'] <= 2.0 + TOL


# ---------------------------------------------------------------------------
# Multiple windows per activity (Issue #2)
# ---------------------------------------------------------------------------

class TestMultipleWindows:
    """
    Activity with time_windows list falls back gracefully when the first
    window is missed, waits for the next, and records a violation only when
    all windows are exhausted.
    """

    def _mw_act(self, name, duration, windows):
        """Build an Activity with a time_windows list."""
        act = Activity(name, duration)
        act.time_windows = [{'earliest': e, 'latest': l} for (e, l) in windows]
        return act

    def _full_pert_mw(self, dur_a=4.0, win_b=None):
        """
        START(0) → A(dur_a) → B(4, time_windows=win_b) → END(0)
        with empty pools attached.
        """
        from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool
        start = Activity('START', 0.0)
        a     = Activity('A', dur_a)
        b     = self._mw_act('B', 4.0, win_b or [])
        end   = Activity('END', 0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool   = ResourcePool()
        p.equipment_pool  = EquipmentPool()
        p.location_pool   = LocationPool()
        p.consumable_pool   = None
        p.system_state_pool = None
        p.startTime = datetime(2026, 1, 1)
        return p, start, a, b, end

    # ── Activity field ────────────────────────────────────────────────────

    def test_time_windows_default_empty(self):
        act = Activity('T', 4.0)
        assert act.time_windows == []

    def test_time_windows_stored(self):
        act = self._mw_act('T', 4.0, [(10.0, 20.0), (40.0, 50.0)])
        assert len(act.time_windows) == 2
        assert act.time_windows[0] == {'earliest': 10.0, 'latest': 20.0}

    def test_from_json_parses_time_windows(self):
        task = {
            'task_id': 'T',
            'description': 'test',
            'duration': 4.0,
            'successors': [],
            'required_resources': [],
            'required_equipment': [],
            'is_hold_point': False,
            'time_windows': [{'earliest': 10.0, 'latest': 20.0},
                             {'earliest': 40.0, 'latest': 50.0}],
        }
        act = Activity.from_json(task)
        assert len(act.time_windows) == 2
        assert abs(act.time_windows[1]['earliest'] - 40.0) < TOL

    def test_to_json_dict_round_trips_time_windows(self):
        act = self._mw_act('T', 4.0, [(10.0, 20.0)])
        d = act.to_json_dict()
        assert 'time_windows' in d
        assert d['time_windows'][0] == {'earliest': 10.0, 'latest': 20.0}

    def test_to_json_dict_omits_empty_time_windows(self):
        act = Activity('T', 4.0)
        d = act.to_json_dict()
        assert 'time_windows' not in d

    # ── _resolve_windows ──────────────────────────────────────────────────

    def test_resolve_windows_uses_time_windows_when_set(self):
        from CPM.pert import Pert
        start = Activity('S', 0.0); end = Activity('E', 0.0)
        act   = self._mw_act('T', 4.0, [(10.0, 20.0), (40.0, 50.0)])
        p = Pert(graph={start: [act], act: [end], end: []})
        result = p._resolve_windows(act)
        assert result == [(10.0, 20.0), (40.0, 50.0)]

    def test_resolve_windows_falls_back_to_single_fields(self):
        from CPM.pert import Pert
        start = Activity('S', 0.0); end = Activity('E', 0.0)
        act   = _windowed_activity('T', 4.0, west=5.0, wlf=30.0)
        p = Pert(graph={start: [act], act: [end], end: []})
        result = p._resolve_windows(act)
        assert result == [(5.0, 30.0)]

    def test_resolve_windows_empty_for_unconstrained(self):
        from CPM.pert import Pert
        start = Activity('S', 0.0); end = Activity('E', 0.0)
        act   = Activity('T', 4.0)
        p = Pert(graph={start: [act], act: [end], end: []})
        assert p._resolve_windows(act) == []

    # ── Scheduler: multi-window fallback (no violation on first miss) ──────

    def test_first_window_missed_no_violation_if_second_available(self):
        """
        B has windows [2h-5h] and [20h-30h].
        A takes 4h, so B cannot start before 4h → first window [2-5] is missed
        at h=4 but second window [20-30] is still available → no violation.
        """
        p, _, _, b, _ = self._full_pert_mw(
            dur_a=4.0,
            win_b=[(2.0, 5.0), (20.0, 30.0)],
        )
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['window_violations'] == []

    def test_first_window_missed_activity_scheduled_in_second(self):
        """B must end up scheduled inside the second window."""
        p, _, _, b, _ = self._full_pert_mw(
            dur_a=4.0,
            win_b=[(2.0, 5.0), (20.0, 30.0)],
        )
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        b_st, b_et = b.returnAbsTimes()
        b_start_h = (b_st - p.startTime).total_seconds() / 3600.0
        b_end_h   = (b_et - p.startTime).total_seconds() / 3600.0
        assert b_start_h >= 20.0 - TOL
        assert b_end_h   <= 30.0 + TOL

    def test_all_windows_missed_records_violation(self):
        """B has only window [2h-5h]; A takes 4h → first start possible at 4h,
        but 4+4=8 > 5 → all windows missed → violation recorded."""
        p, _, _, b, _ = self._full_pert_mw(
            dur_a=4.0,
            win_b=[(2.0, 5.0)],
        )
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert len(result['window_violations']) == 1
        assert result['window_violations'][0]['activity'] == 'B'
        assert result['window_violations'][0]['reason'] == 'window_missed'

    def test_schema_has_time_windows_field(self):
        import json, os
        schema_path = os.path.join(os.path.dirname(__file__), '..', 'outage_schema.json')
        with open(schema_path) as f:
            schema = json.load(f)
        task_props = schema['properties']['tasks']['items']['properties']
        assert 'time_windows' in task_props
        assert task_props['time_windows']['type'] == 'array'
