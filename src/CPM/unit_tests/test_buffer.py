"""
Unit tests for CCPM Proactive Robustness Buffering (Challenge 9).

Project Buffers absorb disruptions anywhere on the resource-constrained
critical chain.  Feeding Buffers protect merge points where non-chain
activities join the critical chain.

Tests cover:
- Pert._size_buffer: 'half' method, 'ssq' method, empty input, unknown method
- Activity.buffer_type: default None, set explicitly, preserved by reset()
- insert_project_buffer: RuntimeError before scheduling
- insert_project_buffer: buffer created with buffer_type='project'
- insert_project_buffer: size computed correctly (half method)
- insert_project_buffer: size computed correctly (ssq method)
- insert_project_buffer: graph wired correctly (terminal→PB→successors)
- insert_project_buffer: idempotent (second call returns same buffer)
- insert_feeding_buffers: RuntimeError before scheduling
- insert_feeding_buffers: returns empty list on purely linear chain
- insert_feeding_buffers: buffer created at merge point
- insert_feeding_buffers: buffer has buffer_type='feeding'
- insert_feeding_buffers: graph wired (non-chain-pred→FB→merge)
- insert_feeding_buffers: multiple independent merge points each get FB
- insert_feeding_buffers: idempotent (second call doesn't add extra buffers)
- get_buffer_status: returns empty dict before startTime set
- get_buffer_status: returns status dict after insertion
- get_buffer_status: consumed=0 when chain ran exactly on plan
- compute_fitness: buffer activities excluded from criticality_ratio
"""

import math
import pytest
from datetime import datetime
from pathlib import Path

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool

TOL = 1e-6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start():
    return datetime(2026, 1, 1)


def _empty_pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _make_pert(fwd: dict, start=None) -> Pert:
    """Build a Pert from a forward-dict, attach empty pools."""
    rp, ep, lp = _empty_pools()
    p = Pert(graph=fwd)
    p.crew_pool = rp
    p.equipment_pool = ep
    p.location_pool = lp
    p.startTime = start or _start()
    p.generateInfo()
    return p


def _linear_chain(*durations):
    """Build START(0)->A(d0)->B(d1)->…->END(0), return (pert, [A,B,…]).

    The returned Pert has NOT been scheduled via calculateScheduleWithResources,
    so constrained_chain_list is still empty.
    """
    acts = [Activity(chr(ord('A') + i), float(d)) for i, d in enumerate(durations)]
    start = Activity('START', 0.0)
    end = Activity('END', 0.0)
    fwd = {}
    fwd[start] = [acts[0]]
    for i in range(len(acts) - 1):
        fwd[acts[i]] = [acts[i + 1]]
    fwd[acts[-1]] = [end]
    fwd[end] = []
    p = _make_pert(fwd)
    return p, acts


def _schedule_linear_chain(*durations):
    """Build and schedule a linear chain; return (pert, [A,B,…]).

    After calling calculateScheduleWithResources the constrained_chain_list
    will include all non-zero activities in the chain.
    """
    p, acts = _linear_chain(*durations)
    p.calculateScheduleWithResources(sgs='max_use_res_ranked')
    assert_valid_schedule(p)
    return p, acts


def _fork_merge(*durations_per_branch, merge_duration=2.0):
    """Build a fork-merge topology:

        START → (branch acts) → MERGE → END

    Each element of ``durations_per_branch`` is a list of durations for one
    branch.  MERGE needs all branches to complete before it starts.

    Returns (pert, branches, merge_act) where ``branches`` is a list of lists.
    """
    start = Activity('START', 0.0)
    end = Activity('END', 0.0)
    merge = Activity('MERGE', float(merge_duration))

    fwd = {start: [], merge: [end], end: []}
    branches = []
    for b_idx, durs in enumerate(durations_per_branch):
        branch_acts = [
            Activity(f'B{b_idx}_{i}', float(d))
            for i, d in enumerate(durs)
        ]
        branches.append(branch_acts)
        # Wire inside branch
        fwd[branch_acts[0]] = [branch_acts[1]] if len(branch_acts) > 1 else [merge]
        for i in range(1, len(branch_acts) - 1):
            fwd[branch_acts[i]] = [branch_acts[i + 1]]
        fwd[branch_acts[-1]] = [merge]
        # Wire START → first of branch
        fwd[start].append(branch_acts[0])

    p = _make_pert(fwd)
    return p, branches, merge


def _schedule_fork_merge(*durations_per_branch, merge_duration=2.0):
    p, branches, merge = _fork_merge(*durations_per_branch, merge_duration=merge_duration)
    p.calculateScheduleWithResources(sgs='max_use_res_ranked')
    assert_valid_schedule(p)
    return p, branches, merge


# ---------------------------------------------------------------------------
# TestSizeBuffer
# ---------------------------------------------------------------------------

class TestSizeBuffer:

    def test_empty_input_returns_zero(self):
        assert Pert._size_buffer([], 'half', 0.5) == 0.0

    def test_empty_input_ssq_returns_zero(self):
        assert Pert._size_buffer([], 'ssq', 0.5) == 0.0

    def test_half_method_single_value(self):
        result = Pert._size_buffer([10.0], 'half', 0.5)
        assert abs(result - 5.0) < TOL

    def test_half_method_multiple_values(self):
        # 0.5 * (4 + 6 + 8) = 9
        result = Pert._size_buffer([4.0, 6.0, 8.0], 'half', 0.5)
        assert abs(result - 9.0) < TOL

    def test_half_method_fraction_one(self):
        result = Pert._size_buffer([4.0, 6.0], 'half', 1.0)
        assert abs(result - 10.0) < TOL

    def test_ssq_method_single_value(self):
        # sqrt((4*0.5)^2) = 2.0
        result = Pert._size_buffer([4.0], 'ssq', 0.5)
        assert abs(result - 2.0) < TOL

    def test_ssq_method_multiple_values(self):
        # sqrt((4*0.5)^2 + (3*0.5)^2) = sqrt(4 + 2.25) = sqrt(6.25) = 2.5
        result = Pert._size_buffer([4.0, 3.0], 'ssq', 0.5)
        assert abs(result - 2.5) < TOL

    def test_ssq_less_than_half_for_multiple_activities(self):
        """SSQ always produces a smaller buffer than half for >1 activity."""
        durations = [10.0, 8.0, 6.0]
        half = Pert._size_buffer(durations, 'half', 0.5)
        ssq = Pert._size_buffer(durations, 'ssq', 0.5)
        assert ssq < half

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError, match="unknown method"):
            Pert._size_buffer([4.0], 'median', 0.5)


# ---------------------------------------------------------------------------
# TestActivityBufferType
# ---------------------------------------------------------------------------

class TestActivityBufferType:

    def test_default_is_none(self):
        a = Activity('T', 4.0)
        assert getattr(a, 'buffer_type', None) is None

    def test_explicit_project(self):
        a = Activity('PB', 3.0)
        a.buffer_type = 'project'
        assert a.buffer_type == 'project'

    def test_explicit_feeding(self):
        a = Activity('FB_X', 2.0)
        a.buffer_type = 'feeding'
        assert a.buffer_type == 'feeding'

    def test_reset_preserves_buffer_type(self):
        """buffer_type is structural metadata — reset() must not clear it."""
        a = Activity('PB', 3.0)
        a.buffer_type = 'project'
        a.reset()
        assert a.buffer_type == 'project'

    def test_none_buffer_type_reset_preserves_none(self):
        a = Activity('T', 4.0)
        a.reset()
        # After reset, buffer_type should still be absent or None
        assert getattr(a, 'buffer_type', None) is None


# ---------------------------------------------------------------------------
# TestInsertProjectBuffer
# ---------------------------------------------------------------------------

class TestInsertProjectBuffer:

    def test_raises_before_scheduling(self):
        """Must raise RuntimeError if constrained_chain_list is empty."""
        p, _ = _linear_chain(4.0, 6.0)
        with pytest.raises(RuntimeError, match="calculateScheduleWithResources"):
            p.insert_project_buffer()

    def test_pb_activity_created(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        assert pb is not None

    def test_pb_buffer_type_is_project(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        assert pb.buffer_type == 'project'

    def test_pb_name_is_pb(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        assert pb.name == 'PB'

    def test_pb_size_half_method(self):
        """PB half-method: 0.5 * sum of chain real activity durations."""
        p, acts = _schedule_linear_chain(4.0, 6.0)
        chain_real = [a for a in p.constrained_chain_list
                      if getattr(a, 'buffer_type', None) is None]
        expected = 0.5 * sum(a.duration for a in chain_real)
        pb = p.insert_project_buffer(method='half', fraction=0.5)
        assert abs(pb.duration - expected) < TOL

    def test_pb_size_ssq_method(self):
        """PB ssq-method: sqrt(sum((d*f)^2)) for chain real activities."""
        p, acts = _schedule_linear_chain(4.0, 6.0)
        chain_real = [a for a in p.constrained_chain_list
                      if getattr(a, 'buffer_type', None) is None]
        expected = math.sqrt(sum((a.duration * 0.5) ** 2 for a in chain_real))
        pb = p.insert_project_buffer(method='ssq', fraction=0.5)
        assert abs(pb.duration - expected) < TOL

    def test_pb_positive_duration(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        assert pb.duration > 0.0

    def test_pb_is_in_forward_dict(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        assert pb in p.forwardDict

    def test_pb_predecessor_is_chain_terminal(self):
        """PB's predecessor should be the last activity in the constrained chain."""
        p, _ = _schedule_linear_chain(4.0, 6.0)
        terminal = p.constrained_chain_list[-1]
        pb = p.insert_project_buffer()
        assert pb in p.backwardDict
        assert terminal in p.backwardDict[pb]

    def test_pb_is_new_terminal(self):
        """After insertion, PB is the last activity in the graph (terminal has no
        successors; the original chain terminal now points to PB)."""
        p, _ = _schedule_linear_chain(4.0, 6.0)
        terminal = p.constrained_chain_list[-1]
        pb = p.insert_project_buffer()
        # The original terminal must now point to PB
        assert pb in p.forwardDict.get(terminal, [])

    def test_idempotent(self):
        """Calling insert_project_buffer twice returns the same Activity object."""
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb1 = p.insert_project_buffer()
        pb2 = p.insert_project_buffer()
        assert pb1 is pb2

    def test_idempotent_no_duplicate_in_forward_dict(self):
        """Idempotency: forwardDict has exactly one PB key after two calls."""
        p, _ = _schedule_linear_chain(4.0, 6.0)
        p.insert_project_buffer()
        p.insert_project_buffer()
        pb_list = [a for a in p.forwardDict if getattr(a, 'buffer_type', None) == 'project']
        assert len(pb_list) == 1

    def test_cpm_updated_after_insertion(self):
        """generateInfo is called inside _splice_buffer_activity; infoDict must contain PB."""
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        assert pb in p.infoDict


# ---------------------------------------------------------------------------
# TestInsertFeedingBuffers
# ---------------------------------------------------------------------------

class TestInsertFeedingBuffers:

    def test_raises_before_scheduling(self):
        p, _ = _linear_chain(4.0, 6.0)
        with pytest.raises(RuntimeError, match="calculateScheduleWithResources"):
            p.insert_feeding_buffers()

    def test_empty_list_on_linear_chain(self):
        """A purely linear chain has no merge points — no feeding buffers needed."""
        p, _ = _schedule_linear_chain(4.0, 6.0, 2.0)
        fbs = p.insert_feeding_buffers()
        assert fbs == []

    def test_fb_created_at_merge_point(self):
        """Fork-merge topology: non-chain branch should get a Feeding Buffer."""
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        fbs = p.insert_feeding_buffers()
        # At least one feeding buffer should have been inserted
        assert len(fbs) >= 1

    def test_fb_buffer_type_is_feeding(self):
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        fbs = p.insert_feeding_buffers()
        assert len(fbs) >= 1
        for fb in fbs:
            assert fb.buffer_type == 'feeding'

    def test_fb_is_in_forward_dict(self):
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        fbs = p.insert_feeding_buffers()
        assert len(fbs) >= 1
        for fb in fbs:
            assert fb in p.forwardDict

    def test_fb_successor_is_merge_act(self):
        """FB should point toward the merge activity on the critical chain."""
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        fbs = p.insert_feeding_buffers()
        assert len(fbs) >= 1
        fb = fbs[0]
        fb_succs = p.forwardDict.get(fb, [])
        assert merge in fb_succs

    def test_fb_predecessor_is_non_chain_terminal(self):
        """The non-chain branch terminal should feed into FB, not directly into MERGE."""
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        chain_set = p.constrained_chain_set
        fbs = p.insert_feeding_buffers()
        assert len(fbs) >= 1
        fb = fbs[0]
        fb_preds = p.backwardDict.get(fb, [])
        # All predecessors of FB must be non-chain activities
        for pred in fb_preds:
            assert pred not in chain_set

    def test_fb_positive_duration(self):
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        fbs = p.insert_feeding_buffers()
        assert len(fbs) >= 1
        for fb in fbs:
            assert fb.duration > 0.0

    def test_idempotent(self):
        """Second call to insert_feeding_buffers must not add extra buffers."""
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        fbs1 = p.insert_feeding_buffers()
        n1 = len(fbs1)
        fbs2 = p.insert_feeding_buffers()
        n2_total = len([a for a in p.forwardDict
                        if getattr(a, 'buffer_type', None) == 'feeding'])
        assert n2_total == n1  # no new buffers added

    def test_cpm_updated_after_insertion(self):
        """infoDict must contain every newly inserted FB."""
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        fbs = p.insert_feeding_buffers()
        for fb in fbs:
            assert fb in p.infoDict


# ---------------------------------------------------------------------------
# TestGetBufferStatus
# ---------------------------------------------------------------------------

class TestGetBufferStatus:

    def test_returns_empty_when_no_start_time(self):
        """If startTime is not set, return empty dict."""
        rp, ep, lp = _empty_pools()
        start = Activity('START', 0.0)
        end = Activity('END', 0.0)
        a = Activity('A', 4.0)
        fwd = {start: [a], a: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool = rp
        p.equipment_pool = ep
        p.location_pool = lp
        # Do NOT set p.startTime
        result = p.get_buffer_status()
        assert result == {}

    def test_returns_empty_before_buffers_inserted(self):
        """No buffers in graph → status dict is empty."""
        p, _ = _schedule_linear_chain(4.0, 6.0)
        status = p.get_buffer_status()
        assert status == {}

    def test_status_has_pb_key_after_insertion(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        status = p.get_buffer_status()
        assert pb.name in status

    def test_status_dict_keys(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        status = p.get_buffer_status()
        entry = status[pb.name]
        required_keys = {'buffer_type', 'size_hours', 'cpm_start_hours',
                         'actual_start_hours', 'consumed_hours', 'utilization_pct'}
        assert required_keys.issubset(entry.keys())

    def test_status_buffer_type_correct(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        status = p.get_buffer_status()
        assert status[pb.name]['buffer_type'] == 'project'

    def test_status_size_hours_matches_duration(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        pb = p.insert_project_buffer()
        status = p.get_buffer_status()
        assert abs(status[pb.name]['size_hours'] - pb.duration) < TOL

    def test_consumed_hours_nonnegative(self):
        """consumed_hours must be ≥ 0 (clipped)."""
        p, _ = _schedule_linear_chain(4.0, 6.0)
        p.insert_project_buffer()
        status = p.get_buffer_status()
        for entry in status.values():
            assert entry['consumed_hours'] >= 0.0

    def test_utilization_pct_between_0_and_100(self):
        p, _ = _schedule_linear_chain(4.0, 6.0)
        p.insert_project_buffer()
        status = p.get_buffer_status()
        for entry in status.values():
            assert 0.0 <= entry['utilization_pct'] <= 100.0

    def test_feeding_buffer_in_status(self):
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        fbs = p.insert_feeding_buffers()
        if fbs:
            status = p.get_buffer_status()
            for fb in fbs:
                assert fb.name in status
                assert status[fb.name]['buffer_type'] == 'feeding'

    def test_both_buffers_in_status(self):
        """After inserting both PB and FBs, all appear in status."""
        p, branches, merge = _schedule_fork_merge([6.0], [2.0], merge_duration=2.0)
        pb = p.insert_project_buffer()
        fbs = p.insert_feeding_buffers()
        status = p.get_buffer_status()
        assert pb.name in status
        for fb in fbs:
            assert fb.name in status


# ---------------------------------------------------------------------------
# TestFitnessExcludesBuffers
# ---------------------------------------------------------------------------

class TestFitnessExcludesBuffers:

    def test_criticality_ratio_unchanged_by_buffer_insertion(self):
        """Buffer activities must not be counted in compute_fitness()
        criticality ratio — inserting a PB must not inflate the ratio."""
        p, _ = _schedule_linear_chain(4.0, 6.0)
        fitness_before = p.compute_fitness()

        # Insert PB — if buffers were incorrectly counted they would
        # inflate the total activity count relative to critical ones.
        p.insert_project_buffer()
        fitness_after = p.compute_fitness()

        # criticality_ratio should remain unchanged (buffers excluded)
        assert abs(fitness_before['criticality_ratio'] - fitness_after['criticality_ratio']) < TOL

    def test_compute_fitness_returns_finite_after_buffer_insertion(self):
        """compute_fitness() must still return a valid result after buffer insertion."""
        import math as _math
        p, _ = _schedule_linear_chain(4.0, 6.0)
        p.insert_project_buffer()
        result = p.compute_fitness()
        for key, val in result.items():
            if isinstance(val, float):
                assert _math.isfinite(val), f"{key} is not finite: {val}"
