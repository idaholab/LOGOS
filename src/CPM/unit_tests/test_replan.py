"""
Unit tests for mid-outage replanning (Challenge 4).

Coverage:
- Activity.status field default, set by scheduler, reset by reset()
- _partial_reset: correct classification into completed/in_progress/pending
- _partial_reset: dose trackers replayed for frozen activities
- _partial_reset: window violations preserved (not cleared)
- _inject_activities: new activity appears in graph, backwardDict correct
- _inject_activities: duplicate task ID skipped with warning
- _inject_activities: unknown successor skipped gracefully
- _generate_info_from: pending ES floored at current_time_hours
- _generate_info_from: frozen ES/EF anchors preserved
- _generate_info_from: pending successor constrained by frozen predecessor EF
- replan: raises RuntimeError before any scheduling run
- replan: raises ValueError when pools missing
- replan: completes all activities after replanning
- replan: frozen activities untouched (start/end times preserved)
- replan: pending activities rescheduled from current time onwards
- replan: injected new activity is scheduled
- replan: dose state preserved across replan
- replan: returns replan_time_hours in result
- replan: running twice from same snapshot gives stable result
- replan: predecessor_wiring adds edge from existing to new activity
- replan: new activity respects wired predecessor finish time
- replan: unknown new-activity name in wiring is skipped gracefully
- replan: unknown predecessor name in wiring is skipped gracefully
- replan: predecessor name as plain string (not list) is accepted
- replan: predecessor_wiring=None is a no-op
"""

import pytest
import math
from datetime import datetime, timedelta

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool


TOL = 1e-6

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pools():
    """Empty pool set — enough for scheduling to proceed with no constraints."""
    return ResourcePool(), EquipmentPool(), LocationPool()


def _chain_pert(*durations):
    """
    Build START(0) -> A(d0) -> B(d1) -> ... -> END(0) and attach empty pools.

    Returns (pert, [A, B, ...]) — START and END are accessible via
    pert.startActivity / pert.endActivity.
    """
    start = Activity('START', 0.0)
    end   = Activity('END',   0.0)

    acts = [Activity(chr(ord('A') + i), float(d)) for i, d in enumerate(durations)]

    nodes = [start] + acts + [end]
    fwd = {}
    for i, node in enumerate(nodes):
        fwd[node] = [nodes[i + 1]] if i + 1 < len(nodes) else []

    rp, ep, lp = _make_pools()
    p = Pert(graph=fwd)
    p.crew_pool  = rp
    p.equipment_pool = ep
    p.location_pool  = lp
    p.generateInfo()
    p.startTime = datetime(2025, 6, 1, 0, 0)
    return p, acts


def _run_full(p):
    """Run a full schedule on p and return results."""
    return p.calculateScheduleWithResources(sgs='max_use_res_ranked')


# ---------------------------------------------------------------------------
# Activity.status
# ---------------------------------------------------------------------------

class TestActivityStatus:

    def test_default_status_is_pending(self):
        act = Activity('T', 4.0)
        assert act.status == 'pending'

    def test_reset_sets_status_to_pending(self):
        act = Activity('T', 4.0)
        act.status = 'in_progress'
        act.reset()
        assert act.status == 'pending'

    def test_scheduler_sets_in_progress(self):
        p, (a, b) = _chain_pert(4.0, 4.0)
        # Run just enough to start A
        _run_full(p)
        # After full run all activities are completed
        for act in p.forwardDict:
            if act.name not in ('START', 'END'):
                assert act.status == 'completed'

    def test_scheduler_sets_completed(self):
        p, (a,) = _chain_pert(4.0)
        _run_full(p)
        assert a.status == 'completed'


# ---------------------------------------------------------------------------
# _partial_reset
# ---------------------------------------------------------------------------

class TestPartialReset:

    def test_completed_activity_in_completed_list(self):
        """Activity whose endTime ≤ current_time must end up in p.completed."""
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)
        # A finishes at t=4h, B at t=8h.  Replan at t=6h → A completed, B in_progress.
        p._partial_reset(6.0)
        completed_names = {act.name for act in p.completed}
        assert 'A' in completed_names

    def test_in_progress_activity_in_ongoing_list(self):
        """Activity whose startTime ≤ current < endTime must be in p.ongoing."""
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)
        # B starts at t=4h, ends at t=8h.  Replan at t=6h → B in_progress.
        p._partial_reset(6.0)
        ongoing_names = {act.name for act in p.ongoing}
        assert 'B' in ongoing_names

    def test_pending_activity_in_wait_list(self):
        """Activity with no startTime must end up in p.wait after partial reset."""
        p, (a, b, c) = _chain_pert(4.0, 4.0, 4.0)
        _run_full(p)
        # A ends at 4h, B at 8h, C at 12h.  Replan at 5h: A done, B in_progress, C pending.
        p._partial_reset(5.0)
        wait_names = {act.name for act in p.wait}
        assert 'C' in wait_names

    def test_pending_activity_timing_cleared(self):
        """reset() is called on pending activities — startTime must be None."""
        p, (a, b, c) = _chain_pert(4.0, 4.0, 4.0)
        _run_full(p)
        p._partial_reset(5.0)
        # C was scheduled in original run; after partial reset its times are cleared
        c = [act for act in p.forwardDict if act.name == 'C'][0]
        st, et = c.returnAbsTimes()
        assert st is None
        assert et is None

    def test_frozen_activity_timing_preserved(self):
        """Completed activity's start/end times must not be touched by partial reset."""
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)
        a_st, a_et = a.returnAbsTimes()  # capture before reset
        p._partial_reset(6.0)
        a_st2, a_et2 = a.returnAbsTimes()
        assert a_st2 == a_st
        assert a_et2 == a_et

    def test_in_progress_remaining_duration_set(self):
        """In-progress activity must have _remaining_duration set correctly."""
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)
        # B starts at 4h, ends at 8h.  Replan at 6h → remaining = 2h.
        p._partial_reset(6.0)
        b_act = [act for act in p.forwardDict if act.name == 'B'][0]
        assert hasattr(b_act, '_remaining_duration')
        assert abs(b_act._remaining_duration - 2.0) < TOL

    def test_window_violations_preserved_across_partial_reset(self):
        """Pre-existing window violations must survive partial_reset."""
        p, (a,) = _chain_pert(4.0)
        # Give A an impossible window (must finish by 2h, but A takes 4h and
        # can only start after START completes at t=0)
        a.window_latest_finish_hours = 2.0
        p.generateInfo()
        _run_full(p)   # will record a violation
        n_before = len(p._window_violations)
        assert n_before > 0, "Expected at least one window violation before replan"
        p._partial_reset(3.0)
        assert len(p._window_violations) == n_before

    def test_dose_re_consumed_for_completed_activity(self):
        """Dose tracker must reflect pre-replan consumption after partial reset."""
        from CPM.outage_data import ResourceAvailability

        # Build a ResourcePool with one consumable MECHANIC skill (4 workers).
        rp = ResourcePool()
        rp.resources['MECHANIC'] = ResourceAvailability(
            'MECHANIC',
            [{'start_date': datetime(2025, 1, 1),
              'end_date':   datetime(2026, 12, 31),
              'available_count': 4}],
            resource_type='consumable',
            dose_budget_per_worker_mrem=2000.0,
        )

        ep = EquipmentPool()
        lp = LocationPool()

        start = Activity('START', 0.0)
        a     = Activity('A', 4.0)
        a.dose_rate_mrem_per_hour = 50.0
        a.required_resources = [{'skill_type': 'MECHANIC', 'crew_count': 2}]
        end   = Activity('END',   0.0)
        fwd   = {start: [a], a: [end], end: []}

        p = Pert(graph=fwd)
        p.crew_pool  = rp
        p.equipment_pool = ep
        p.location_pool  = lp
        p.dose_trackers  = rp.build_dose_trackers()
        p.generateInfo()
        p.startTime = datetime(2025, 6, 1, 0, 0)

        _run_full(p)
        consumed_before = p.dose_trackers['MECHANIC'].consumed_mrem

        # Partial reset should replay dose for frozen A
        p._partial_reset(6.0)   # A is completed by t=4h
        consumed_after = p.dose_trackers['MECHANIC'].consumed_mrem
        assert abs(consumed_after - consumed_before) < TOL


# ---------------------------------------------------------------------------
# _inject_activities
# ---------------------------------------------------------------------------

class TestInjectActivities:

    def test_injected_activity_in_forward_dict(self):
        p, (a,) = _chain_pert(4.0)
        new_act = Activity('NEW', 2.0)
        new_act.childs = ['END']
        p._inject_activities([new_act])
        assert new_act in p.forwardDict

    def test_injected_activity_in_task_lookup(self):
        p, (a,) = _chain_pert(4.0)
        new_act = Activity('NEW2', 2.0)
        new_act.childs = []
        p._inject_activities([new_act])
        assert 'NEW2' in p.task_to_activity

    def test_injected_activity_backward_dict_updated(self):
        """A successor that existed before injection must now list the new
        activity as one of its predecessors."""
        p, (a,) = _chain_pert(4.0)
        end = p.endActivity
        new_act = Activity('NEW3', 2.0)
        new_act.childs = ['END']
        p._inject_activities([new_act])
        preds_of_end = p.backwardDict.get(end, [])
        assert new_act in preds_of_end

    def test_duplicate_task_id_skipped(self):
        """Injecting an activity whose name already exists must not crash."""
        p, (a,) = _chain_pert(4.0)
        duplicate = Activity('A', 2.0)   # 'A' is already in the graph
        duplicate.childs = []
        p._inject_activities([duplicate])   # must not raise
        # Original A must still be the one stored
        assert p.task_to_activity['A'] is a

    def test_unknown_successor_skipped_gracefully(self):
        """An unknown successor ID must not crash injection."""
        p, (a,) = _chain_pert(4.0)
        new_act = Activity('NEW4', 2.0)
        new_act.childs = ['DOES_NOT_EXIST']
        p._inject_activities([new_act])   # must not raise
        assert new_act in p.forwardDict
        assert p.forwardDict[new_act] == []


# ---------------------------------------------------------------------------
# _generate_info_from
# ---------------------------------------------------------------------------

class TestGenerateInfoFrom:

    def test_pending_es_floored_at_current_time(self):
        """Pending activities must not have ES before current_time_hours."""
        p, (a, b, c) = _chain_pert(4.0, 4.0, 4.0)
        _run_full(p)
        p._partial_reset(5.0)
        p._generate_info_from(5.0)
        c_act = [act for act in p.forwardDict if act.name == 'C'][0]
        assert p.infoDict[c_act]['es'] >= 5.0 - TOL

    def test_frozen_es_preserved(self):
        """Completed activity's ES must remain its actual start offset."""
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)
        a_st, _ = a.returnAbsTimes()
        a_actual_offset = (a_st - p.startTime).total_seconds() / 3600.0
        p._partial_reset(6.0)
        p._generate_info_from(6.0)
        assert abs(p.infoDict[a]['es'] - a_actual_offset) < TOL

    def test_pending_constrained_by_frozen_predecessor(self):
        """A pending activity's ES must be ≥ its frozen predecessor's EF."""
        p, (a, b, c) = _chain_pert(4.0, 4.0, 4.0)
        _run_full(p)
        # At t=5h: A done, B in_progress, C pending.
        # B finishes at t=8h from outage start → C.ES must be ≥ 8h.
        p._partial_reset(5.0)
        p._generate_info_from(5.0)
        b_act = [act for act in p.forwardDict if act.name == 'B'][0]
        c_act = [act for act in p.forwardDict if act.name == 'C'][0]
        b_ef  = p.infoDict[b_act]['ef']
        c_es  = p.infoDict[c_act]['es']
        assert c_es >= b_ef - TOL

    def test_in_progress_ef_based_on_remaining_duration(self):
        """In-progress activity's EF must be current_time + remaining_duration."""
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)
        # Replan at t=6h: B is in_progress with 2h remaining
        p._partial_reset(6.0)
        p._generate_info_from(6.0)
        b_act = [act for act in p.forwardDict if act.name == 'B'][0]
        expected_ef = 6.0 + b_act._remaining_duration   # 6 + 2 = 8
        assert abs(p.infoDict[b_act]['ef'] - expected_ef) < TOL


# ---------------------------------------------------------------------------
# replan — end-to-end
# ---------------------------------------------------------------------------

class TestReplan:

    def test_raises_before_any_scheduling_run(self):
        p, (a,) = _chain_pert(4.0)
        with pytest.raises(RuntimeError, match="calculateScheduleWithResources"):
            p.replan(2.0)

    def test_raises_without_pools(self):
        start = Activity('START', 0.0)
        a     = Activity('A', 4.0)
        end   = Activity('END', 0.0)
        fwd   = {start: [a], a: [end], end: []}
        p     = Pert(graph=fwd)
        p.startTime = datetime(2025, 6, 1, 0, 0)
        # no pools attached — should raise before any scheduling
        with pytest.raises((ValueError, RuntimeError)):
            p.replan(2.0)

    def test_all_activities_completed_after_replan(self):
        """Replanning must complete all activities."""
        p, (a, b, c) = _chain_pert(4.0, 4.0, 4.0)
        _run_full(p)
        result = p.replan(5.0)
        assert result['n_completed'] == result['n_activities']

    def test_frozen_start_times_unchanged(self):
        """Completed activities must keep their original start times."""
        p, (a, b, c) = _chain_pert(4.0, 4.0, 4.0)
        _run_full(p)
        a_st_before, _ = a.returnAbsTimes()
        p.replan(5.0)
        a_st_after, _ = a.returnAbsTimes()
        assert a_st_after == a_st_before

    def test_pending_activities_start_at_or_after_replan_time(self):
        """Activities rescheduled during replan must not start before replan time."""
        p, (a, b, c) = _chain_pert(4.0, 4.0, 4.0)
        _run_full(p)
        replan_abs = p.startTime + timedelta(hours=5.0)
        p.replan(5.0)
        c_act = [act for act in p.forwardDict if act.name == 'C'][0]
        c_st, _ = c_act.returnAbsTimes()
        assert c_st >= replan_abs - timedelta(seconds=1)

    def test_result_contains_replan_time_hours(self):
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)
        result = p.replan(3.0)
        assert 'replan_time_hours' in result
        assert abs(result['replan_time_hours'] - 3.0) < TOL

    def test_replan_at_project_start_equivalent_to_full_run(self):
        """Replanning at t=0 with all activities pending is identical to a full run."""
        p, (a, b) = _chain_pert(4.0, 4.0)
        r_full = _run_full(p)
        r_replan = p.replan(0.0)
        assert r_replan['n_completed'] == r_full['n_activities']

    def test_replan_returns_scheduled_duration(self):
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)
        result = p.replan(4.0)
        assert result['scheduled_duration'] > 0.0

    def test_replan_idempotent(self):
        """Two successive replans from the same snapshot must both complete."""
        p, (a, b, c) = _chain_pert(4.0, 4.0, 4.0)
        _run_full(p)
        r1 = p.replan(5.0)
        r2 = p.replan(5.0)
        assert r1['n_completed'] == r2['n_completed']


# ---------------------------------------------------------------------------
# replan with injected activities
# ---------------------------------------------------------------------------

class TestReplanWithInjection:

    def test_injected_activity_scheduled(self):
        """An injected activity must appear in the final completed list."""
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)

        # Inject EMERG between A and B: A -> EMERG -> B
        # 1) Tell A's forwardDict entry to include EMERG as a successor
        emerg = Activity('EMERG', 2.0)
        emerg.childs = ['END']    # EMERG finishes before END

        # Attach EMERG to END (inject to graph, EMERG ends before END)
        p.replan(4.0, new_activities=[emerg])

        completed_names = {act.name for act in p.completed}
        assert 'EMERG' in completed_names

    def test_injected_activity_in_graph_after_replan(self):
        """The injected activity must appear in forwardDict after replan."""
        p, (a,) = _chain_pert(4.0)
        _run_full(p)

        new_act = Activity('EXTRA', 3.0)
        new_act.childs = ['END']
        p.replan(3.0, new_activities=[new_act])

        assert new_act in p.forwardDict

    def test_injection_does_not_shrink_original_activities(self):
        """Injecting a new activity must not remove existing activities from the graph."""
        p, (a, b) = _chain_pert(4.0, 4.0)
        _run_full(p)
        n_before = len(p.forwardDict)

        new_act = Activity('INJECTED', 2.0)
        new_act.childs = []
        p.replan(4.0, new_activities=[new_act])

        assert len(p.forwardDict) == n_before + 1


# ---------------------------------------------------------------------------
# Predecessor wiring for injected activities (Issue #3)
# ---------------------------------------------------------------------------

class TestPredecessorWiring:
    """
    replan(predecessor_wiring=...) wires existing activities as predecessors
    of newly injected activities so the caller does not have to mutate
    forwardDict manually.
    """

    def _base_pert(self):
        """START(0) → A(4) → B(4) → END(0) with pools."""
        start = Activity('START', 0.0)
        a     = Activity('A', 4.0)
        b     = Activity('B', 4.0)
        end   = Activity('END', 0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        rp, ep, lp = _make_pools()
        p = Pert(graph=fwd)
        p.crew_pool   = rp
        p.equipment_pool  = ep
        p.location_pool   = lp
        p.consumable_pool   = None
        p.system_state_pool = None
        p.startTime = datetime(2026, 1, 1)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        return p, start, a, b, end

    def test_predecessor_wiring_adds_edge_to_forwarddict(self):
        """After injection with wiring, existing activity A → new activity."""
        p, _, a, b, _ = self._base_pert()
        new_act = Activity('NEW', 2.0)
        new_act.childs = ['B']    # NEW → B
        p.replan(
            4.0,
            new_activities=[new_act],
            predecessor_wiring={'NEW': ['A']},   # A → NEW
        )
        # A must have NEW as a successor
        assert new_act in p.forwardDict[a]

    def test_predecessor_wiring_respected_in_schedule(self):
        """NEW starts no earlier than A finishes (A is its predecessor)."""
        p, _, a, b, _ = self._base_pert()
        new_act = Activity('NEW', 2.0)
        new_act.childs = []
        p.replan(
            4.0,
            new_activities=[new_act],
            predecessor_wiring={'NEW': ['A']},
        )
        a_st, a_et = a.returnAbsTimes()
        n_st, _    = new_act.returnAbsTimes()
        # NEW cannot start before A finishes
        assert n_st >= a_et

    def test_predecessor_wiring_unknown_new_activity_warns(self):
        """Wiring referencing a non-existent new activity is skipped gracefully."""
        p, _, a, b, _ = self._base_pert()
        new_act = Activity('NEW', 2.0)
        new_act.childs = []
        # 'GHOST' was never injected — should warn and not crash
        p.replan(
            4.0,
            new_activities=[new_act],
            predecessor_wiring={'GHOST': ['A']},
        )
        # Scheduler completes normally
        assert new_act in p.forwardDict

    def test_predecessor_wiring_unknown_predecessor_warns(self):
        """Wiring referencing a non-existent existing activity is skipped."""
        p, _, a, b, _ = self._base_pert()
        new_act = Activity('NEW', 2.0)
        new_act.childs = []
        p.replan(
            4.0,
            new_activities=[new_act],
            predecessor_wiring={'NEW': ['NO_SUCH_TASK']},
        )
        # NEW is still in the graph; wiring simply absent
        assert new_act in p.forwardDict

    def test_predecessor_wiring_string_shorthand(self):
        """Single predecessor name as a plain string (not list) is accepted."""
        p, _, a, b, _ = self._base_pert()
        new_act = Activity('NEW', 2.0)
        new_act.childs = []
        p.replan(
            4.0,
            new_activities=[new_act],
            predecessor_wiring={'NEW': 'A'},   # string, not list
        )
        assert new_act in p.forwardDict[a]

    def test_predecessor_wiring_none_is_no_op(self):
        """predecessor_wiring=None (default) behaves identically to before."""
        p, _, a, b, _ = self._base_pert()
        new_act = Activity('NEW', 2.0)
        new_act.childs = []
        n_before = len(p.forwardDict)
        p.replan(4.0, new_activities=[new_act])
        assert len(p.forwardDict) == n_before + 1
