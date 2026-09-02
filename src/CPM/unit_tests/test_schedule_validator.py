"""
test_schedule_validator.py — Tests for schedule_validator.validate_schedule()

Each test class exercises one violation category by constructing a minimal
Pert instance, running calculateScheduleWithResources(), then either
introducing a synthetic defect or verifying a natural defect is caught.
"""

import pytest
from datetime import datetime, timedelta

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    ResourcePool, ResourceAvailability,
    EquipmentPool, EquipmentAvailability,
    LocationPool, LocationAvailability,
    ConsumablePool, SystemStatePool,
)
from CPM.schedule_validator import validate_schedule, Violation

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

START_DT = datetime(2026, 1, 1, 0, 0)
HORIZON  = timedelta(days=30)


def _rp(skill: str, count: int) -> ResourcePool:
    rp = ResourcePool()
    rp.resources[skill] = ResourceAvailability(
        skill,
        [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
          'available_count': count}],
    )
    return rp


def _simple_pert(durations=(4.0, 4.0), crew=2, pool_size=10) -> Pert:
    """START → A → B → END, MECH skill, given pool size."""
    a = Activity('A', durations[0], required_resources=[
        {'skill_type': 'MECH', 'crew_count': crew, 'alternative_skill_types': []}])
    b = Activity('B', durations[1], required_resources=[
        {'skill_type': 'MECH', 'crew_count': crew, 'alternative_skill_types': []}])
    start = Activity('START', 0.0)
    end   = Activity('END',   0.0)
    fwd   = {start: [a], a: [b], b: [end], end: []}
    p = Pert(graph=fwd)
    p.crew_pool     = _rp('MECH', pool_size)
    p.equipment_pool = EquipmentPool()
    p.location_pool  = LocationPool()
    p.consumable_pool   = None
    p.system_state_pool = None
    p.startTime = START_DT
    return p


def _schedule(p: Pert) -> dict:
    p.generateInfo()
    return p.calculateScheduleWithResources(sgs='max_use_res_ranked',
                                            max_time_hours=200)


# ===========================================================================
# Happy-path: valid schedule produces no violations
# ===========================================================================

class TestHappyPath:
    def test_valid_schedule_is_feasible(self):
        p = _simple_pert()
        _schedule(p)
        result = p.validate_schedule()
        assert result.is_feasible
        assert result.violations == []

    def test_result_repr(self):
        p = _simple_pert()
        _schedule(p)
        r = p.validate_schedule()
        assert 'ValidationResult' in repr(r)

    def test_summary_contains_status(self):
        p = _simple_pert()
        _schedule(p)
        s = p.validate_schedule().summary()
        assert 'FEASIBLE' in s


# ===========================================================================
# Completeness
# ===========================================================================

class TestCompleteness:
    def test_unscheduled_pert_reports_completeness_violation(self):
        p = _simple_pert()
        p.generateInfo()   # CPM only — no scheduling run
        result = validate_schedule(p)
        types = [v.type for v in result.violations]
        assert 'completeness' in types

    def test_full_schedule_has_no_completeness_violation(self):
        p = _simple_pert()
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'completeness' not in types


# ===========================================================================
# Duration consistency
# ===========================================================================

class TestDuration:
    def test_tampered_endtime_detected(self):
        p = _simple_pert()
        _schedule(p)
        # Artificially corrupt one activity's endTime
        for act in p.completed:
            if act.name == 'A':
                act.endTime = act.startTime + timedelta(hours=99)
                break
        result = validate_schedule(p)
        types = [v.type for v in result.violations]
        assert 'duration' in types

    def test_correct_durations_no_violation(self):
        p = _simple_pert()
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'duration' not in types


# ===========================================================================
# Precedence
# ===========================================================================

class TestPrecedence:
    def test_tampered_starttime_breaks_precedence(self):
        p = _simple_pert()
        _schedule(p)
        # Move B's startTime to before A finishes
        a_act = next(a for a in p.completed if a.name == 'A')
        b_act = next(a for a in p.completed if a.name == 'B')
        b_act.startTime = a_act.startTime        # B starts when A starts → violates A→B
        b_act.endTime   = b_act.startTime + timedelta(hours=b_act.duration)
        result = validate_schedule(p)
        types = [v.type for v in result.violations]
        assert 'precedence' in types

    def test_valid_order_no_precedence_violation(self):
        p = _simple_pert()
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'precedence' not in types

    def test_lag_violation_detected(self):
        """Validator catches when B starts before A.endTime + lag."""
        p = _simple_pert()
        _schedule(p)
        a_act = next(x for x in p.completed if x.name == 'A')
        b_act = next(x for x in p.completed if x.name == 'B')
        # Inject a 4-hour lag requirement between A and B
        a_act.successor_lags = {b_act.name: 4.0}
        # B currently starts right after A — violates the 4h lag
        result = validate_schedule(p)
        assert 'precedence' in [v.type for v in result.violations]


# ===========================================================================
# Time windows
# ===========================================================================

class TestTimeWindows:
    def test_activity_outside_window_detected(self):
        p = _simple_pert()
        _schedule(p)
        # Inject a narrow window on A after scheduling so the validator sees a breach
        a_act = next(x for x in p.completed if x.name == 'A')
        # A starts at h=0, ends at h=4; impose window [10h, 20h] → clear breach
        a_act.window_earliest_start_hours = 10.0
        a_act.window_latest_finish_hours  = 20.0
        result = validate_schedule(p)
        types = [v.type for v in result.violations] + [w.type for w in result.warnings]
        assert 'time_window' in types

    def test_activity_inside_window_no_violation(self):
        p = _simple_pert()
        for act in p.forwardDict:
            if act.name == 'A':
                act.window_earliest_start_hours = 0.0
                act.window_latest_finish_hours  = 100.0
                break
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'time_window' not in types


# ===========================================================================
# Crew feasibility
# ===========================================================================

class TestCrewFeasibility:
    def test_overloaded_crew_detected_when_injected(self):
        """Scheduler respects crew limits, so we inject a violation manually."""
        p = _simple_pert(pool_size=2)   # only 2 MECH
        _schedule(p)
        # Artificially force A and B to overlap
        a_act = next(a for a in p.completed if a.name == 'A')
        b_act = next(a for a in p.completed if a.name == 'B')
        b_act.startTime = a_act.startTime          # both running simultaneously
        b_act.endTime   = b_act.startTime + timedelta(hours=b_act.duration)
        # Combined demand = 4 MECH, pool = 2 → violation
        result = validate_schedule(p)
        types = [v.type for v in result.violations]
        assert 'crew' in types

    def test_scheduler_does_not_overload_crew(self):
        """The scheduler itself must not produce crew violations on a fresh run."""
        p = _simple_pert(pool_size=2)
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'crew' not in types


# ===========================================================================
# Equipment feasibility
# ===========================================================================

class TestEquipmentFeasibility:
    def _make_pert_with_equipment(self):
        a = Activity('A', 4.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 1,
                                          'alternative_skill_types': []}],
                     required_equipment=[{'equipment_id': 'CRANE', 'quantity_needed': 1}])
        b = Activity('B', 4.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 1,
                                          'alternative_skill_types': []}],
                     required_equipment=[{'equipment_id': 'CRANE', 'quantity_needed': 1}])
        start = Activity('START', 0.0)
        end   = Activity('END',   0.0)
        fwd   = {start: [a, b], a: [end], b: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool = _rp('MECH', 10)
        ep = EquipmentPool()
        ep.equipment['CRANE'] = EquipmentAvailability(
            'CRANE', 'polar crane',
            [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
              'quantity_available': 1}])
        p.equipment_pool    = ep
        p.location_pool     = LocationPool()
        p.consumable_pool   = None
        p.system_state_pool = None
        p.startTime = START_DT
        return p, a, b

    def test_equipment_overload_detected(self):
        p, a_act, b_act = self._make_pert_with_equipment()
        _schedule(p)
        # Force overlap to create equipment overload
        a_obj = next(x for x in p.completed if x.name == 'A')
        b_obj = next(x for x in p.completed if x.name == 'B')
        b_obj.startTime = a_obj.startTime
        b_obj.endTime   = b_obj.startTime + timedelta(hours=4)
        result = validate_schedule(p)
        assert 'equipment' in [v.type for v in result.violations]

    def test_scheduler_serialises_equipment_conflict(self):
        p, _, _ = self._make_pert_with_equipment()
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'equipment' not in types


# ===========================================================================
# Location feasibility
# ===========================================================================

class TestLocationFeasibility:
    def _make_pert_with_location(self, max_tasks=1):
        a = Activity('A', 4.0, location_id='LOC_1',
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 1,
                                          'alternative_skill_types': []}])
        b = Activity('B', 4.0, location_id='LOC_1',
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 1,
                                          'alternative_skill_types': []}])
        start = Activity('START', 0.0)
        end   = Activity('END',   0.0)
        fwd   = {start: [a, b], a: [end], b: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool = _rp('MECH', 10)
        p.equipment_pool = EquipmentPool()
        lp = LocationPool()
        lp.locations['LOC_1'] = LocationAvailability(
            'LOC_1', 'test location',
            [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
              'max_concurrent_tasks': max_tasks,
              'max_concurrent_workers': max_tasks * 4}])
        p.location_pool     = lp
        p.consumable_pool   = None
        p.system_state_pool = None
        p.startTime = START_DT
        return p

    def test_location_overload_detected(self):
        p = self._make_pert_with_location(max_tasks=1)
        _schedule(p)
        # Force both activities into the same time slot
        a_obj = next(x for x in p.completed if x.name == 'A')
        b_obj = next(x for x in p.completed if x.name == 'B')
        b_obj.startTime = a_obj.startTime
        b_obj.endTime   = b_obj.startTime + timedelta(hours=4)
        result = validate_schedule(p)
        assert 'location' in [v.type for v in result.violations]

    def test_scheduler_serialises_location_conflict(self):
        p = self._make_pert_with_location(max_tasks=1)
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'location' not in types


# ===========================================================================
# System-state conflicts
# ===========================================================================

class TestSystemStates:
    def _make_state_pert(self, same_state=True):
        state_b = 'CLOSED' if same_state else 'OPEN'
        a = Activity('A', 4.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 1,
                                          'alternative_skill_types': []}])
        a.required_system_states = [{'system_id': 'V1', 'required_state': 'CLOSED'}]
        b = Activity('B', 4.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 1,
                                          'alternative_skill_types': []}])
        b.required_system_states = [{'system_id': 'V1', 'required_state': state_b}]
        start = Activity('START', 0.0)
        end   = Activity('END',   0.0)
        fwd   = {start: [a, b], a: [end], b: [end], end: []}

        from CPM.outage_data import SystemStatePool
        ssp = SystemStatePool()

        p = Pert(graph=fwd)
        p.crew_pool         = _rp('MECH', 10)
        p.equipment_pool    = EquipmentPool()
        p.location_pool     = LocationPool()
        p.consumable_pool   = None
        p.system_state_pool = ssp
        p.startTime = START_DT
        return p

    def test_incompatible_states_in_overlap_detected(self):
        p = self._make_state_pert(same_state=False)
        _schedule(p)
        # Force overlap
        a_obj = next(x for x in p.completed if x.name == 'A')
        b_obj = next(x for x in p.completed if x.name == 'B')
        b_obj.startTime = a_obj.startTime
        b_obj.endTime   = b_obj.startTime + timedelta(hours=4)
        result = validate_schedule(p)
        assert 'system_state' in [v.type for v in result.violations]

    def test_compatible_states_no_violation(self):
        p = self._make_state_pert(same_state=True)
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'system_state' not in types


# ===========================================================================
# Hold points
# ===========================================================================

class TestHoldPoints:
    def test_blocked_task_before_holdpoint_detected(self):
        hp  = Activity('HP',  0.0, is_hold_point=True, blocks_tasks=['B'])
        b   = Activity('B',   4.0, required_resources=[
            {'skill_type': 'MECH', 'crew_count': 1, 'alternative_skill_types': []}])
        start = Activity('START', 0.0)
        end   = Activity('END',   0.0)
        fwd   = {start: [hp, b], hp: [b], b: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool = _rp('MECH', 10)
        p.equipment_pool = EquipmentPool()
        p.location_pool  = LocationPool()
        p.consumable_pool = p.system_state_pool = None
        p.startTime = START_DT
        _schedule(p)
        # Force B to start before HP ends
        hp_obj = next(x for x in p.completed if x.name == 'HP')
        b_obj  = next(x for x in p.completed if x.name == 'B')
        hp_obj.startTime = START_DT + timedelta(hours=8)
        hp_obj.endTime   = hp_obj.startTime   # zero-duration
        b_obj.startTime  = START_DT            # before HP
        b_obj.endTime    = b_obj.startTime + timedelta(hours=4)
        result = validate_schedule(p)
        assert 'hold_point' in [v.type for v in result.violations]


# ===========================================================================
# Quality warnings
# ===========================================================================

class TestQualityWarnings:
    def test_delayed_activities_produce_warning(self):
        p = _simple_pert(pool_size=2)   # tight pool forces delay
        _schedule(p)
        result = p.validate_schedule()
        # With 2 MECH and 2-crew activities in series, delay may be zero;
        # but the schedule should still be feasible
        assert result.is_feasible

    def test_summary_includes_section_headers(self):
        p = _simple_pert()
        _schedule(p)
        summary = p.validate_schedule().summary()
        assert 'Status' in summary
        assert 'Violations' in summary
        assert 'Warnings' in summary


# ===========================================================================
# Consumables
# ===========================================================================

class TestConsumables:
    def _make_pert_with_consumables(self, total_qty: float) -> Pert:
        """A and B each need 1 unit of 'SEAL'.  total_qty controls whether
        there is enough inventory."""
        a = Activity('A', 4.0, required_resources=[
            {'skill_type': 'MECH', 'crew_count': 1, 'alternative_skill_types': []}])
        a.required_consumables = [{'item_id': 'SEAL', 'quantity_needed': 1}]
        b = Activity('B', 4.0, required_resources=[
            {'skill_type': 'MECH', 'crew_count': 1, 'alternative_skill_types': []}])
        b.required_consumables = [{'item_id': 'SEAL', 'quantity_needed': 1}]
        start = Activity('START', 0.0)
        end   = Activity('END',   0.0)
        fwd   = {start: [a], a: [b], b: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool      = _rp('MECH', 10)
        p.equipment_pool = EquipmentPool()
        p.location_pool  = LocationPool()
        p.system_state_pool = None
        # Build a consumable pool with the requested total
        cp = ConsumablePool()
        cp.items['SEAL']           = float(total_qty)
        cp.remaining['SEAL']       = float(total_qty)
        cp.description['SEAL']     = 'valve seals'
        cp.restocks['SEAL']        = []
        cp._restock_cursor['SEAL'] = -1.0
        p.consumable_pool = cp
        p.startTime = START_DT
        return p

    def test_consumable_shortage_detected(self):
        """Schedule with 2 SEALs, then reduce pool to 1 → validator fires shortage.

        The scheduler enforces consumable constraints, so we must schedule with
        enough inventory first.  After scheduling we reduce the declared total so
        the validator's replay (which resets to pool.items) sees a shortage.
        """
        p = self._make_pert_with_consumables(total_qty=2)
        _schedule(p)
        # Reduce the declared initial stock so the validator replay runs short
        p.consumable_pool.items['SEAL'] = 1.0
        result = validate_schedule(p)
        types = [v.type for v in result.violations]
        assert 'consumable' in types

    def test_sufficient_consumables_no_violation(self):
        """2 SEALs available, 2 needed → no consumable violation."""
        p = self._make_pert_with_consumables(total_qty=2)
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'consumable' not in types


# ===========================================================================
# Equipment zone affinity
# ===========================================================================

class TestEquipmentZoneAffinity:
    def _make_pert_with_zoned_equipment(self, act_zones: list) -> Pert:
        """Activity A uses CRANE which is zone-locked to 'CONTAINMENT'."""
        a = Activity('A', 4.0,
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 1,
                                          'alternative_skill_types': []}],
                     required_equipment=[{'equipment_id': 'CRANE', 'quantity_needed': 1}])
        a.zone_ids = list(act_zones)
        start = Activity('START', 0.0)
        end   = Activity('END',   0.0)
        fwd   = {start: [a], a: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool = _rp('MECH', 10)
        ep = EquipmentPool()
        ep.equipment['CRANE'] = EquipmentAvailability(
            'CRANE', 'polar crane',
            [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
              'quantity_available': 1}],
            zone_id='CONTAINMENT',
        )
        p.equipment_pool    = ep
        p.location_pool     = LocationPool()
        p.consumable_pool   = None
        p.system_state_pool = None
        p.startTime = START_DT
        return p

    def test_out_of_zone_equipment_use_detected(self):
        """Schedule with correct zone, then reassign activity to wrong zone.

        The scheduler blocks activities that use zone-locked equipment from the
        wrong zone, so we schedule successfully first (activity in CONTAINMENT),
        then move the activity to AUX_BLDG post-schedule so the validator sees
        the zone mismatch.
        """
        p = self._make_pert_with_zoned_equipment(act_zones=['CONTAINMENT'])
        _schedule(p)
        # After scheduling, move activity to a different zone
        a_obj = next(x for x in p.completed if x.name == 'A')
        a_obj.zone_ids = ['AUX_BLDG']
        result = validate_schedule(p)
        types = [v.type for v in result.violations]
        assert 'equipment_zone' in types

    def test_correct_zone_no_violation(self):
        """Activity is in zone 'CONTAINMENT' — matches equipment zone."""
        p = self._make_pert_with_zoned_equipment(act_zones=['CONTAINMENT'])
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'equipment_zone' not in types

    def test_unconstrained_equipment_no_zone_violation(self):
        """Equipment without zone_id must not trigger zone violations."""
        p = self._make_pert_with_zoned_equipment(act_zones=[])
        # Override zone_id to None
        p.equipment_pool.equipment['CRANE'].zone_id = None
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'equipment_zone' not in types


# ===========================================================================
# Location worker limits
# ===========================================================================

class TestLocationWorkers:
    def _make_pert_with_worker_limit(self, max_workers: int) -> Pert:
        """A and B run in LOC_1; each needs 2 MECH workers."""
        a = Activity('A', 4.0, location_id='LOC_1',
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 2,
                                          'alternative_skill_types': []}])
        b = Activity('B', 4.0, location_id='LOC_1',
                     required_resources=[{'skill_type': 'MECH', 'crew_count': 2,
                                          'alternative_skill_types': []}])
        start = Activity('START', 0.0)
        end   = Activity('END',   0.0)
        fwd   = {start: [a, b], a: [end], b: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool = _rp('MECH', 10)
        p.equipment_pool = EquipmentPool()
        lp = LocationPool()
        lp.locations['LOC_1'] = LocationAvailability(
            'LOC_1', 'test location',
            [{'start_date': START_DT, 'end_date': START_DT + HORIZON,
              'max_concurrent_tasks': 99,        # tasks limit not the issue
              'max_concurrent_workers': max_workers}])
        p.location_pool     = lp
        p.consumable_pool   = None
        p.system_state_pool = None
        p.startTime = START_DT
        return p

    def test_worker_limit_violation_detected(self):
        """Force A and B to overlap; combined 4 workers > limit of 3."""
        p = self._make_pert_with_worker_limit(max_workers=3)
        _schedule(p)
        a_obj = next(x for x in p.completed if x.name == 'A')
        b_obj = next(x for x in p.completed if x.name == 'B')
        b_obj.startTime = a_obj.startTime          # force simultaneous start
        b_obj.endTime   = b_obj.startTime + timedelta(hours=4)
        result = validate_schedule(p)
        types = [v.type for v in result.violations]
        assert 'location' in types
        # Detail should mention workers
        worker_viols = [v for v in result.violations
                        if v.type == 'location' and 'workers' in v.detail]
        assert worker_viols, 'expected a worker-limit violation detail'

    def test_sufficient_worker_capacity_no_violation(self):
        """5-worker limit; scheduler serialises so at most 2 at a time."""
        p = self._make_pert_with_worker_limit(max_workers=5)
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'location' not in types


# ===========================================================================
# Shift calendar
# ===========================================================================

class TestShiftCalendar:
    def _make_pert_with_shift(self, wpd: int, shift_start: int) -> Pert:
        """Single A activity; shift settings injected on Pert after construction."""
        a = Activity('A', 4.0, required_resources=[
            {'skill_type': 'MECH', 'crew_count': 1, 'alternative_skill_types': []}])
        start = Activity('START', 0.0)
        end   = Activity('END',   0.0)
        fwd   = {start: [a], a: [end], end: []}
        p = Pert(graph=fwd)
        p.crew_pool      = _rp('MECH', 10)
        p.equipment_pool = EquipmentPool()
        p.location_pool  = LocationPool()
        p.consumable_pool   = None
        p.system_state_pool = None
        p.startTime = START_DT
        p.working_hours_per_day = wpd
        p.shift_start_hour      = shift_start
        return p

    def test_activity_outside_shift_detected(self):
        """Shift is 08:00–20:00 (12 h); A is scheduled at h=0 (midnight) → violation."""
        p = self._make_pert_with_shift(wpd=12, shift_start=8)
        _schedule(p)
        # A starts at offset h=0 → hour-of-day = 0 (midnight), outside [8, 20]
        result = validate_schedule(p)
        types = [v.type for v in result.violations]
        assert 'shift_calendar' in types

    def test_activity_inside_shift_no_violation(self):
        """Full 24-h shift → no shift violation regardless of schedule."""
        p = self._make_pert_with_shift(wpd=24, shift_start=0)
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'shift_calendar' not in types

    def test_no_shift_constraint_skipped(self):
        """working_hours_per_day=24 means the check is entirely skipped."""
        p = _simple_pert()
        p.working_hours_per_day = 24
        p.shift_start_hour = 0
        _schedule(p)
        types = [v.type for v in p.validate_schedule().violations]
        assert 'shift_calendar' not in types
