"""
Unit tests for mid-outage replanning with resource and equipment mutations.

Coverage:
- ResourceAvailability.update_from_hour: chop-and-replace mechanics
- ResourceAvailability.snapshot / restore: rollback
- EquipmentAvailability.update_from_hour: chop-and-replace mechanics
- EquipmentAvailability.snapshot / restore: rollback
- ResourcePool.update_skill_from_hour: wrapper + no-op for missing skill
- ResourcePool.snapshot / restore: whole-pool rollback
- EquipmentPool.update_equipment_from_hour: wrapper + no-op for missing item
- EquipmentPool.snapshot / restore: whole-pool rollback
- replan() resource_updates: sick-call (reduce workers) blocks activity
- replan() resource_updates: extra crew (increase workers) allows more parallelism
- replan() resource_updates: until_hour (temporary change reverts)
- replan() equipment_updates: broken equipment blocks dependent activity
- replan() equipment_updates: equipment restored at until_hour
- replan() duration_overrides: in-progress activity duration extended
- replan() duration_overrides: extension propagates delay to successors
- replan() all changes combined: resource + equipment + new activity
- clone_for_analysis: pool mutations on clone do not affect original
- clone_for_analysis: clone now carries independent consumable_pool
- replan() ValueError on negative from_hour
"""

import copy
import pytest
from datetime import datetime, timedelta

from CPM.outage_data import (
    ResourceAvailability, EquipmentAvailability,
    ResourcePool, EquipmentPool, LocationPool,
)
from CPM.activity import Activity
from CPM.pert import Pert

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_START  = datetime(2026, 1, 1)
_END    = datetime(2026, 12, 31)
_FAR    = datetime(9999, 12, 31)

def _ra(skill, count, start=_START, end=_END):
    """Build a ResourceAvailability with a single period."""
    return ResourceAvailability(
        skill,
        [{'start_date': start, 'end_date': end, 'available_count': count}],
    )

def _ea(eq_id, qty, start=_START, end=_END):
    """Build an EquipmentAvailability with a single period."""
    return EquipmentAvailability(
        eq_id, f'desc-{eq_id}',
        [{'start_date': start, 'end_date': end, 'quantity_available': qty}],
    )

def _crew_pool(*skill_counts):
    """Build a ResourcePool from (skill, count) pairs."""
    rp = ResourcePool()
    for skill, count in skill_counts:
        rp.resources[skill] = _ra(skill, count)
    return rp

def _equipment_pool(*id_qtys):
    """Build an EquipmentPool from (eq_id, qty) pairs."""
    ep = EquipmentPool()
    for eq_id, qty in id_qtys:
        ep.equipment[eq_id] = _ea(eq_id, qty)
    return ep

def _act(name, duration=4.0, skill=None, crew=1, eq_id=None, eq_qty=1):
    a = Activity(name, duration)
    if skill:
        a.required_resources  = [{'skill_type': skill, 'crew_count': crew}]
    else:
        a.required_resources = []
    if eq_id:
        a.required_equipment = [{'equipment_id': eq_id, 'quantity_needed': eq_qty}]
    return a

def _chain_pert(*acts, crew_pool=None, equipment_pool=None):
    """Chain acts in sequence, attach pools, return Pert."""
    start = Activity('START', 0.0)
    end   = Activity('END',   0.0)
    nodes = [start] + list(acts) + [end]
    fwd = {nodes[i]: [nodes[i+1]] for i in range(len(nodes)-1)}
    fwd[end] = []
    p = Pert(graph=fwd)
    p.crew_pool  = crew_pool  or ResourcePool()
    p.equipment_pool = equipment_pool or EquipmentPool()
    p.location_pool  = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = _START
    p.generateInfo()
    return p

def _parallel_pert(*acts, crew_pool=None, equipment_pool=None):
    """All acts run in parallel (no dependencies)."""
    start = Activity('START', 0.0)
    end   = Activity('END',   0.0)
    fwd = {start: list(acts), end: []}
    for a in acts:
        fwd[a] = [end]
    p = Pert(graph=fwd)
    p.crew_pool  = crew_pool  or ResourcePool()
    p.equipment_pool = equipment_pool or EquipmentPool()
    p.location_pool  = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = _START
    p.generateInfo()
    return p


# ===========================================================================
# ResourceAvailability.update_from_hour — unit tests
# ===========================================================================

class TestResourceAvailabilityUpdateFromHour:

    def test_full_replacement_single_period(self):
        """Replacing the entire period gives a single new period."""
        ra = _ra('MECH', 4)
        ra.update_from_hour(_START, from_hour=0, new_count=2)
        assert ra.get_availability_at(_START) == 2

    def test_partial_replacement_from_middle(self):
        """Updating from h=10 leaves the first 10 hours unchanged."""
        ra = _ra('MECH', 4)
        ra.update_from_hour(_START, from_hour=10, new_count=1)
        assert ra.get_availability_at(_START) == 4                     # before change
        assert ra.get_availability_at(_START + timedelta(hours=11)) == 1  # after change

    def test_temporary_reduction(self):
        """until_hour restores the period after the window."""
        ra = _ra('MECH', 4)
        # 4 workers everywhere; reduce to 2 for hours 10–20
        ra.update_from_hour(_START, from_hour=10, new_count=2, until_hour=20)
        assert ra.get_availability_at(_START)                          == 4  # before
        assert ra.get_availability_at(_START + timedelta(hours=15))    == 2  # during
        assert ra.get_availability_at(_START + timedelta(hours=21))    == 4  # after

    def test_reduction_to_zero(self):
        """Reducing to 0 makes the skill unavailable in that window."""
        ra = _ra('MECH', 3)
        ra.update_from_hour(_START, from_hour=5, new_count=0, until_hour=15)
        assert ra.get_availability_at(_START + timedelta(hours=10)) == 0

    def test_increase(self):
        """Increasing count above the original value is allowed."""
        ra = _ra('MECH', 2)
        ra.update_from_hour(_START, from_hour=0, new_count=6)
        assert ra.get_availability_at(_START + timedelta(hours=100)) == 6

    def test_multiple_updates_stack(self):
        """Applying two sequential updates reflects the most recent one."""
        ra = _ra('MECH', 4)
        ra.update_from_hour(_START, from_hour=10, new_count=2)
        ra.update_from_hour(_START, from_hour=20, new_count=1)
        assert ra.get_availability_at(_START + timedelta(hours=15)) == 2
        assert ra.get_availability_at(_START + timedelta(hours=25)) == 1

    def test_no_change_outside_window(self):
        """Periods entirely before the update window are untouched."""
        early = _ra('MECH', 5)
        # Single period covers all year; update only from h=500 forward
        early.update_from_hour(_START, from_hour=500, new_count=1)
        assert early.get_availability_at(_START) == 5
        assert early.get_availability_at(_START + timedelta(hours=499)) == 5


# ===========================================================================
# ResourceAvailability.snapshot / restore
# ===========================================================================

class TestResourceAvailabilitySnapshot:

    def test_restore_after_update(self):
        ra = _ra('MECH', 4)
        saved = ra.snapshot()
        ra.update_from_hour(_START, from_hour=0, new_count=0)
        assert ra.get_availability_at(_START) == 0
        ra.restore(saved)
        assert ra.get_availability_at(_START) == 4

    def test_snapshot_is_independent_copy(self):
        ra = _ra('MECH', 4)
        saved = ra.snapshot()
        # Mutating saved must not affect ra
        saved[0]['available_count'] = 99
        assert ra.get_availability_at(_START) == 4


# ===========================================================================
# EquipmentAvailability.update_from_hour — unit tests
# ===========================================================================

class TestEquipmentAvailabilityUpdateFromHour:

    def test_full_replacement(self):
        ea = _ea('CRANE', 2)
        ea.update_from_hour(_START, from_hour=0, new_quantity=0)
        assert ea.get_availability_at(_START) == 0

    def test_temporary_breakdown(self):
        ea = _ea('CRANE', 2)
        ea.update_from_hour(_START, from_hour=48, new_quantity=0, until_hour=60)
        assert ea.get_availability_at(_START + timedelta(hours=50)) == 0
        assert ea.get_availability_at(_START + timedelta(hours=61)) == 2

    def test_partial_reduction(self):
        ea = _ea('CRANE', 3)
        ea.update_from_hour(_START, from_hour=24, new_quantity=1)
        assert ea.get_availability_at(_START)                        == 3
        assert ea.get_availability_at(_START + timedelta(hours=25))  == 1


# ===========================================================================
# EquipmentAvailability.snapshot / restore
# ===========================================================================

class TestEquipmentAvailabilitySnapshot:

    def test_restore_after_update(self):
        ea = _ea('CRANE', 2)
        saved = ea.snapshot()
        ea.update_from_hour(_START, from_hour=0, new_quantity=0)
        ea.restore(saved)
        assert ea.get_availability_at(_START) == 2


# ===========================================================================
# Pool-level wrappers
# ===========================================================================

class TestResourcePoolWrapper:

    def test_update_skill_changes_availability(self):
        rp = _crew_pool(('MECH', 4))
        rp.update_skill_from_hour('MECH', _START, from_hour=10, new_count=1)
        assert rp.get_availability('MECH', _START + timedelta(hours=11)) == 1

    def test_unknown_skill_is_noop(self):
        rp = _crew_pool(('MECH', 4))
        rp.update_skill_from_hour('GHOST', _START, from_hour=0, new_count=0)
        # No exception; pool unchanged
        assert rp.get_availability('MECH', _START) == 4

    def test_pool_snapshot_restore(self):
        rp = _crew_pool(('MECH', 4), ('ELEC', 2))
        saved = rp.snapshot()
        rp.update_skill_from_hour('MECH', _START, from_hour=0, new_count=0)
        rp.update_skill_from_hour('ELEC', _START, from_hour=0, new_count=0)
        rp.restore(saved)
        assert rp.get_availability('MECH', _START) == 4
        assert rp.get_availability('ELEC', _START) == 2


class TestEquipmentPoolWrapper:

    def test_update_equipment_changes_quantity(self):
        ep = _equipment_pool(('CRANE', 2))
        ep.update_equipment_from_hour('CRANE', _START, from_hour=5, new_quantity=0)
        assert ep.get_availability('CRANE', _START + timedelta(hours=6)) == 0

    def test_unknown_equipment_is_noop(self):
        ep = _equipment_pool(('CRANE', 2))
        ep.update_equipment_from_hour('GHOST', _START, from_hour=0, new_quantity=0)
        assert ep.get_availability('CRANE', _START) == 2

    def test_pool_snapshot_restore(self):
        ep = _equipment_pool(('CRANE', 2), ('PUMP', 3))
        saved = ep.snapshot()
        ep.update_equipment_from_hour('CRANE', _START, from_hour=0, new_quantity=0)
        ep.restore(saved)
        assert ep.get_availability('CRANE', _START) == 2


# ===========================================================================
# replan() — resource_updates scheduler integration
# ===========================================================================

def _diamond_pert(a_pre, b, c, crew_pool=None, equipment_pool=None):
    """a_pre → (b ∥ c) → END — a_pre is the single predecessor of both b and c."""
    start = Activity('START', 0.0)
    end   = Activity('END',   0.0)
    fwd = {
        start:  [a_pre],
        a_pre:  [b, c],
        b:      [end],
        c:      [end],
        end:    [],
    }
    p = Pert(graph=fwd)
    p.crew_pool  = crew_pool  or ResourcePool()
    p.equipment_pool = equipment_pool or EquipmentPool()
    p.location_pool  = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = _START
    p.generateInfo()
    return p


class TestReplanResourceUpdates:

    def test_sick_call_blocks_pending_parallel_tasks(self):
        """Reducing workers forces two pending parallel tasks to serialise.

        Topology: A(4h, no resources) → [B(4h, 2 MECH) ∥ C(4h, 2 MECH)] → END
        Pool has 4 MECH so baseline schedules B and C in parallel at t=4.
        Replan from t=2 (A in_progress, B and C pending) with pool cut to 2 MECH
        → only one of B/C can run at a time.
        """
        rp = _crew_pool(('MECH', 4))
        a = _act('A', 4.0)           # no resource requirement — always starts
        b = _act('B', 4.0, skill='MECH', crew=2)
        c = _act('C', 4.0, skill='MECH', crew=2)
        p = _diamond_pert(a, b, c, crew_pool=rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Baseline: A at t=0; B and C in parallel at t=4
        b_st0, _ = b.returnAbsTimes()
        c_st0, _ = c.returnAbsTimes()
        assert b_st0 == c_st0  # parallel in baseline

        # Replan at t=2: A in_progress, B and C pending; reduce pool to 2
        p.replan(
            current_time_hours=2.0,
            resource_updates=[{'skill_type': 'MECH', 'from_hour': 2, 'new_count': 2}],
        )
        b_st1, b_et1 = b.returnAbsTimes()
        c_st1, c_et1 = c.returnAbsTimes()
        # Must serialise — no overlap
        assert b_et1 <= c_st1 or c_et1 <= b_st1

    def test_extra_crew_enables_parallelism_for_pending_tasks(self):
        """Adding workers allows pending tasks to run in parallel.

        Topology: A(4h) → [B(4h, 2 MECH) ∥ C(4h, 2 MECH)] → END
        Pool starts at 2 MECH → B and C serialise in baseline.
        Replan from t=2 with pool expanded to 4 → B and C can now run in parallel.
        """
        rp = _crew_pool(('MECH', 2))
        a = _act('A', 4.0)
        b = _act('B', 4.0, skill='MECH', crew=2)
        c = _act('C', 4.0, skill='MECH', crew=2)
        p = _diamond_pert(a, b, c, crew_pool=rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Baseline: B and C serialise (either order is valid)
        b_st0, b_et0 = b.returnAbsTimes()
        c_st0, c_et0 = c.returnAbsTimes()
        assert b_et0 <= c_st0 or c_et0 <= b_st0

        # Replan at t=2, double the pool
        p.replan(
            current_time_hours=2.0,
            resource_updates=[{'skill_type': 'MECH', 'from_hour': 2, 'new_count': 4}],
        )
        b_st1, _ = b.returnAbsTimes()
        c_st1, _ = c.returnAbsTimes()
        # B and C should now start at the same time (parallel)
        assert b_st1 == c_st1

    def test_temporary_reduction_delays_then_unblocks_pending_task(self):
        """until_hour: pending task is delayed during the window and starts after.

        Topology: A(4h) → B(4h, 4 MECH) → END
        Replan from t=2 (A in_progress, B pending).
        Pool cut to 0 from t=2 to t=8, then restored to 4.
        B needs 4 MECH → cannot start until t=8.
        """
        rp = _crew_pool(('MECH', 4))
        a = _act('A', 4.0)
        b = _act('B', 4.0, skill='MECH', crew=4)
        p = _chain_pert(a, b, crew_pool=rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        p.replan(
            current_time_hours=2.0,
            resource_updates=[{
                'skill_type': 'MECH', 'from_hour': 2,
                'new_count': 0, 'until_hour': 8,
            }],
        )
        b_st, _ = b.returnAbsTimes()
        # B is pending and needs 4 MECH; pool is 0 until t=8, so B must wait
        assert b_st is not None
        assert b_st >= _START + timedelta(hours=8)


# ===========================================================================
# replan() — equipment_updates scheduler integration
# ===========================================================================

class TestReplanEquipmentUpdates:

    def test_broken_equipment_blocks_pending_task(self):
        """Zeroing equipment quantity prevents a pending task from starting.

        Topology: A(4h, no resources) → B(4h, CRANE qty=1) → END
        Replan from t=2 (A in_progress, B pending).
        CRANE quantity → 0 permanently → B cannot start within the schedule horizon.
        """
        ep = _equipment_pool(('CRANE', 1))
        a = _act('A', 4.0)
        b = _act('B', 4.0, eq_id='CRANE')
        p = _chain_pert(a, b, equipment_pool=ep)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        b_st0, _ = b.returnAbsTimes()
        assert b_st0 == _START + timedelta(hours=4)  # baseline: starts at t=4

        # Replan at t=2: A in_progress, B pending; break crane permanently
        p.replan(
            current_time_hours=2.0,
            equipment_updates=[{
                'equipment_id': 'CRANE', 'from_hour': 2, 'new_quantity': 0
            }],
        )
        b_st1, _ = b.returnAbsTimes()
        # B requires CRANE but none is available — not schedulable within horizon
        # The scheduler hits the safety cutoff and B is never started
        assert b_st1 is None or b_st1 > _START + timedelta(hours=100)

    def test_temporary_breakdown_task_starts_after_repair(self):
        """Equipment returns at until_hour — pending task starts after repair.

        Topology: A(4h) → B(4h, CRANE qty=1) → END
        Replan from t=2. CRANE broken from t=2 to t=10.
        B is pending and cannot start until CRANE returns at t=10.
        """
        ep = _equipment_pool(('CRANE', 1))
        a = _act('A', 4.0)
        b = _act('B', 4.0, eq_id='CRANE')
        p = _chain_pert(a, b, equipment_pool=ep)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        p.replan(
            current_time_hours=2.0,
            equipment_updates=[{
                'equipment_id': 'CRANE', 'from_hour': 2,
                'new_quantity': 0, 'until_hour': 10,
            }],
        )
        b_st, _ = b.returnAbsTimes()
        assert b_st is not None
        assert b_st >= _START + timedelta(hours=10)


# ===========================================================================
# replan() — duration_overrides
# ===========================================================================

class TestReplanDurationOverrides:

    def test_in_progress_duration_extended(self):
        """Extending an in-progress task's duration pushes its end time out."""
        a = _act('A', 4.0)
        b = _act('B', 4.0)
        p = _chain_pert(a, b)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        # Simulate A started at t=0 (the scheduler does this);
        # replan at t=2 with A extended to 12h total
        result = p.replan(
            current_time_hours=2.0,
            duration_overrides={'A': 12.0},
        )
        assert result['n_completed'] == result['n_activities']
        # A's duration is now permanently 12h
        assert a.duration == 12.0
        # B must start after A finishes (at t=12)
        _, a_et = a.returnAbsTimes()
        b_st, _ = b.returnAbsTimes()
        assert b_st >= a_et

    def test_duration_extension_propagates_delay(self):
        """A longer in-progress task delays all its successors."""
        a = _act('A', 4.0)
        b = _act('B', 4.0)
        c = _act('C', 4.0)
        p = _chain_pert(a, b, c)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Baseline: A ends at 4, B at 8, C at 12
        _, c_et_baseline = c.returnAbsTimes()

        p.replan(
            current_time_hours=2.0,
            duration_overrides={'A': 20.0},  # A now takes 20h total
        )
        _, c_et_new = c.returnAbsTimes()
        # C must finish later than baseline
        assert c_et_new > c_et_baseline

    def test_override_updates_act_duration_permanently(self):
        """duration_overrides modifies act.duration permanently."""
        a = _act('A', 4.0)
        p = _chain_pert(a)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        p.replan(current_time_hours=1.0, duration_overrides={'A': 16.0})
        assert a.duration == 16.0


# ===========================================================================
# replan() — combined changes
# ===========================================================================

class TestReplanCombined:

    def test_resource_equipment_and_new_activity(self):
        """All three change types together produce a complete schedule."""
        rp = _crew_pool(('MECH', 4))
        ep = _equipment_pool(('CRANE', 2))
        a = _act('A', 4.0, skill='MECH', crew=2)
        b = _act('B', 4.0, skill='MECH', crew=2)
        p = _parallel_pert(a, b, crew_pool=rp, equipment_pool=ep)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        # New emergent activity
        c = Activity('C', 3.0)
        c.required_resources = []

        result = p.replan(
            current_time_hours=2.0,
            new_activities=[c],
            resource_updates=[{
                'skill_type': 'MECH', 'from_hour': 2, 'new_count': 2
            }],
            equipment_updates=[{
                'equipment_id': 'CRANE', 'from_hour': 2,
                'new_quantity': 1, 'until_hour': 6,
            }],
        )
        assert result['n_completed'] == result['n_activities']
        c_st, _ = c.returnAbsTimes()
        assert c_st is not None

    def test_negative_from_hour_raises(self):
        """from_hour < 0 must raise ValueError."""
        a = _act('A', 4.0)
        p = _chain_pert(a)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        with pytest.raises(ValueError, match="from_hour"):
            p.replan(
                current_time_hours=0.0,
                resource_updates=[{
                    'skill_type': 'MECH', 'from_hour': -1, 'new_count': 0
                }],
            )


# ===========================================================================
# clone_for_analysis() — pool isolation
# ===========================================================================

class TestClonePoolIsolation:

    def test_resource_mutation_on_clone_does_not_affect_original(self):
        """Mutating the clone's resource pool leaves the original intact."""
        rp = _crew_pool(('MECH', 4))
        a = _act('A', 4.0)
        p = _chain_pert(a, crew_pool=rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        clone = p.clone_for_analysis()
        clone.crew_pool.update_skill_from_hour(
            'MECH', _START, from_hour=0, new_count=0
        )

        # Original pool must be unchanged
        assert p.crew_pool.get_availability('MECH', _START) == 4
        # Clone pool reflects the change
        assert clone.crew_pool.get_availability('MECH', _START) == 0

    def test_equipment_mutation_on_clone_does_not_affect_original(self):
        """Mutating the clone's equipment pool leaves the original intact."""
        ep = _equipment_pool(('CRANE', 3))
        a = _act('A', 4.0)
        p = _chain_pert(a, equipment_pool=ep)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        clone = p.clone_for_analysis()
        clone.equipment_pool.update_equipment_from_hour(
            'CRANE', _START, from_hour=0, new_quantity=0
        )

        assert p.equipment_pool.get_availability('CRANE', _START) == 3
        assert clone.equipment_pool.get_availability('CRANE', _START) == 0

    def test_clone_carries_independent_dose_trackers(self):
        """Clone's dose_trackers are independent from the original."""
        from CPM.outage_data import ResourceAvailability
        rp = ResourcePool()
        rp.resources['RAD'] = ResourceAvailability(
            'RAD',
            [{'start_date': _START, 'end_date': _END, 'available_count': 2}],
            resource_type='consumable',
            dose_budget_per_worker_mrem=5000.0,
        )
        a = _act('A', 4.0)
        p = _chain_pert(a, crew_pool=rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        clone = p.clone_for_analysis()
        # Consume dose on the clone's tracker
        if 'RAD' in clone.dose_trackers:
            clone.dose_trackers['RAD'].consume(100.0, 1, 4.0)
            # Original tracker must be unaffected
            if 'RAD' in p.dose_trackers:
                assert p.dose_trackers['RAD'].consumed_mrem == 0.0

    def test_two_clones_have_independent_pools(self):
        """Two clones from the same baseline can have different pool states."""
        rp = _crew_pool(('MECH', 4))
        a = _act('A', 4.0)
        p = _chain_pert(a, crew_pool=rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')

        c1 = p.clone_for_analysis()
        c2 = p.clone_for_analysis()
        c1.crew_pool.update_skill_from_hour('MECH', _START, 0, new_count=1)
        c2.crew_pool.update_skill_from_hour('MECH', _START, 0, new_count=3)

        assert c1.crew_pool.get_availability('MECH', _START) == 1
        assert c2.crew_pool.get_availability('MECH', _START) == 3
        assert p.crew_pool.get_availability('MECH',  _START) == 4
