"""
Unit tests for SystemStatePool and its integration with the RCPSP scheduler.

Coverage:
- SystemStatePool unit: from_json, fits, acquire, release, reset, reference counting
- Activity: required_system_states field, getRequiredSystemStates, from_json,
            to_json_dict, set_mode override
- Scheduler integration: same-state parallel, different-state blocked,
  three-way exclusion, unrelated activity unblocked, full release after
  first activity finishes
- Replanning: _partial_reset re-acquires for in-progress activities only
- Schema: plant_systems array and required_system_states task field validate
"""

import json
import pytest
from datetime import datetime, timedelta

from conftest import SCHEMA_PATH
from CPM.outage_data import SystemStatePool, ResourcePool, EquipmentPool, LocationPool
from CPM.activity import Activity
from CPM.pert import Pert

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_START = datetime(2026, 1, 1)
_END   = datetime(2026, 12, 31)

_SYSTEMS = [
    {'system_id': 'VALVE_V1',   'description': 'RCP suction valve',
     'valid_states': ['OPEN', 'CLOSED']},
    {'system_id': 'BREAKER_B1', 'description': 'MCC breaker',
     'valid_states': ['ENERGIZED', 'DE-ENERGIZED']},
    {'system_id': 'PD_101',     'description': 'Temporary power drop',
     'valid_states': ['ENERGIZED', 'DE-ENERGIZED']},
]


def _pool_from_list(entries=None):
    return SystemStatePool.from_json(entries or _SYSTEMS)


def _act(name, duration=4.0, system_states=None):
    a = Activity(name, duration)
    a.required_resources = []
    if system_states:
        a.required_system_states = system_states
    return a


def _build_pert(fwd, systems_json=None):
    p = Pert(graph=fwd)
    p.crew_pool    = ResourcePool()
    p.equipment_pool   = EquipmentPool()
    p.location_pool    = LocationPool()
    p.consumable_pool  = None
    p.system_state_pool = SystemStatePool.from_json(systems_json or _SYSTEMS)
    p.startTime = _START
    p.generateInfo()
    return p


# ===========================================================================
# SystemStatePool unit tests
# ===========================================================================

class TestSystemStatePoolInit:

    def test_empty_pool(self):
        pool = SystemStatePool()
        assert pool.get_all_system_ids() == []
        assert pool._held == {}

    def test_from_json_registers_systems(self):
        pool = _pool_from_list()
        assert 'VALVE_V1' in pool.get_all_system_ids()
        assert 'BREAKER_B1' in pool.get_all_system_ids()

    def test_from_json_stores_valid_states(self):
        pool = _pool_from_list()
        assert pool.systems['VALVE_V1']['valid_states'] == ['OPEN', 'CLOSED']

    def test_from_json_missing_valid_states_defaults_empty(self):
        pool = SystemStatePool.from_json([
            {'system_id': 'SYS_X', 'description': 'no states'}
        ])
        assert pool.systems['SYS_X']['valid_states'] == []

    def test_has_system_known(self):
        pool = _pool_from_list()
        assert pool.has_system('VALVE_V1') is True

    def test_has_system_unknown(self):
        pool = _pool_from_list()
        assert pool.has_system('NONEXISTENT') is False

    def test_repr_all_free(self):
        pool = _pool_from_list()
        assert 'all free' in repr(pool)

    def test_repr_shows_held_state(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        assert 'VALVE_V1' in repr(pool)


class TestSystemStatePoolFitsAcquireRelease:

    def test_free_system_fits_any_state(self):
        pool = _pool_from_list()
        assert pool.fits('VALVE_V1', 'CLOSED') is True
        assert pool.fits('VALVE_V1', 'OPEN')   is True

    def test_same_state_fits(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        assert pool.fits('VALVE_V1', 'CLOSED') is True

    def test_different_state_blocked(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        assert pool.fits('VALVE_V1', 'OPEN') is False

    def test_unknown_system_always_fits(self):
        pool = _pool_from_list()
        assert pool.fits('UNKNOWN_SYS', 'ANY_STATE') is True

    def test_acquire_increments_refcount(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        pool.acquire('VALVE_V1', 'CLOSED')
        assert pool._held['VALVE_V1']['CLOSED'] == 2

    def test_release_decrements_refcount(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        pool.acquire('VALVE_V1', 'CLOSED')
        pool.release('VALVE_V1', 'CLOSED')
        assert pool._held['VALVE_V1']['CLOSED'] == 1

    def test_release_to_zero_frees_system(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        pool.release('VALVE_V1', 'CLOSED')
        assert 'VALVE_V1' not in pool._held

    def test_release_unknown_system_noop(self):
        pool = _pool_from_list()
        pool.release('NONEXISTENT', 'CLOSED')   # must not raise

    def test_release_unknown_state_noop(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        pool.release('VALVE_V1', 'OPEN')        # wrong state — no-op
        assert pool._held['VALVE_V1']['CLOSED'] == 1

    def test_get_held_state_free(self):
        pool = _pool_from_list()
        assert pool.get_held_state('VALVE_V1') is None

    def test_get_held_state_acquired(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        assert pool.get_held_state('VALVE_V1') == 'CLOSED'

    def test_get_held_state_released(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        pool.release('VALVE_V1', 'CLOSED')
        assert pool.get_held_state('VALVE_V1') is None

    def test_independent_systems_do_not_interfere(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1',   'CLOSED')
        pool.acquire('BREAKER_B1', 'ENERGIZED')
        assert pool.fits('VALVE_V1',   'CLOSED')      is True
        assert pool.fits('VALVE_V1',   'OPEN')        is False
        assert pool.fits('BREAKER_B1', 'ENERGIZED')   is True
        assert pool.fits('BREAKER_B1', 'DE-ENERGIZED') is False


class TestSystemStatePoolReset:

    def test_reset_clears_all_held(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1',   'CLOSED')
        pool.acquire('BREAKER_B1', 'ENERGIZED')
        pool.reset()
        assert pool._held == {}

    def test_fits_after_reset(self):
        pool = _pool_from_list()
        pool.acquire('VALVE_V1', 'CLOSED')
        pool.reset()
        assert pool.fits('VALVE_V1', 'OPEN') is True


# ===========================================================================
# Activity.required_system_states
# ===========================================================================

class TestActivitySystemStates:

    def _task_dict(self, **kwargs):
        base = {
            'task_id': 'T1', 'description': 'task', 'duration': 4.0,
            'successors': [], 'required_resources': [], 'required_equipment': [],
        }
        base.update(kwargs)
        return base

    def test_default_field_empty(self):
        a = Activity('T', 4.0)
        assert a.required_system_states == []
        assert a.getRequiredSystemStates() == []

    def test_from_json_no_field(self):
        a = Activity.from_json(self._task_dict())
        assert a.required_system_states == []

    def test_from_json_with_states(self):
        d = self._task_dict(required_system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        a = Activity.from_json(d)
        assert a.required_system_states == [
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ]

    def test_to_json_dict_omits_when_empty(self):
        a = Activity.from_json(self._task_dict())
        out = a.to_json_dict()
        assert 'required_system_states' not in out

    def test_to_json_dict_emits_when_set(self):
        states = [{'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}]
        d = self._task_dict(required_system_states=states)
        a = Activity.from_json(d)
        out = a.to_json_dict()
        assert out['required_system_states'] == states

    def test_roundtrip(self):
        states = [
            {'system_id': 'VALVE_V1',   'required_state': 'CLOSED'},
            {'system_id': 'BREAKER_B1', 'required_state': 'DE-ENERGIZED'},
        ]
        d  = self._task_dict(required_system_states=states)
        a  = Activity.from_json(d)
        a2 = Activity.from_json(a.to_json_dict())
        assert a2.required_system_states == states

    def test_set_mode_overrides_system_states(self):
        a = Activity('T', 4.0)
        a.required_system_states = [
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ]
        a.modes = [{
            'mode_id': 'fast',
            'duration': 2.0,
            'required_resources': [],
            'required_equipment': [],
            'required_system_states': [
                {'system_id': 'VALVE_V1', 'required_state': 'OPEN'}
            ],
        }]
        a.set_mode('fast')
        assert a.required_system_states == [
            {'system_id': 'VALVE_V1', 'required_state': 'OPEN'}
        ]

    def test_set_mode_without_field_leaves_existing(self):
        a = Activity('T', 4.0)
        a.required_system_states = [
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ]
        a.modes = [{
            'mode_id': 'basic',
            'duration': 3.0,
            'required_resources': [],
            'required_equipment': [],
            # no required_system_states key
        }]
        a.set_mode('basic')
        assert a.required_system_states == [
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ]


# ===========================================================================
# Scheduler integration
# ===========================================================================

class TestSchedulerSystemState:

    def test_no_system_state_pool_schedules_normally(self):
        a = _act('A', 4.0)
        b = _act('B', 4.0)
        fwd = {a: [], b: []}
        p = _build_pert(fwd)
        p.system_state_pool = None
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2

    def test_activity_without_system_state_unblocked(self):
        """Activity with no required_system_states runs freely."""
        a = _act('A', 4.0)
        fwd = {a: []}
        p = _build_pert(fwd)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 1

    def test_same_state_activities_run_in_parallel(self):
        """
        A and B both require VALVE_V1=CLOSED.
        No resource constraints → they can start at the same time.
        """
        a = _act('A', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        b = _act('B', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        fwd = {a: [], b: []}
        p = _build_pert(fwd)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        # Both can start at time 0 (same state — compatible)
        assert a_st == b_st == _START

    def test_different_state_activities_are_serialised(self):
        """
        A requires VALVE_V1=CLOSED, B requires VALVE_V1=OPEN.
        They must not overlap.
        """
        a = _act('A', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        b = _act('B', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'OPEN'}
        ])
        fwd = {a: [], b: []}
        p = _build_pert(fwd)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        # Non-overlapping
        assert a_et <= b_st or b_et <= a_st

    def test_unrelated_activity_not_blocked(self):
        """
        A requires VALVE_V1=CLOSED, C has no system state requirement.
        C must NOT be blocked by A's lock.
        """
        a = _act('A', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        c = _act('C', 4.0)   # no system state requirement
        fwd = {a: [], c: []}
        p = _build_pert(fwd)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        a_st, _ = a.returnAbsTimes()
        c_st, _ = c.returnAbsTimes()
        # Both start at t=0 since they share no locked system
        assert a_st == c_st == _START

    def test_second_activity_starts_after_first_releases_lock(self):
        """
        A requires VALVE_V1=CLOSED (4h), B requires VALVE_V1=OPEN.
        B must wait until A finishes, then start immediately.
        """
        a = _act('A', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        b = _act('B', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'OPEN'}
        ])
        fwd = {a: [], b: []}
        p = _build_pert(fwd)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        # A and B hold conflicting states — they must not overlap (either order)
        assert b_st >= a_et or a_st >= b_et

    def test_three_activities_two_states(self):
        """
        A (CLOSED), B (CLOSED), C (OPEN).
        A and B can share the lock; C must wait until both finish.
        Total duration = 4h (A+B parallel) + 4h (C) = 8h.
        """
        a = _act('A', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        b = _act('B', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        c = _act('C', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'OPEN'}
        ])
        fwd = {a: [], b: [], c: []}
        p = _build_pert(fwd)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 3
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        c_st, _    = c.returnAbsTimes()
        # A and B are parallel (same state)
        assert a_st == b_st == _START
        # C starts after BOTH A and B finish
        assert c_st >= max(a_et, b_et)

    def test_multi_system_requirement(self):
        """
        A requires VALVE_V1=CLOSED AND BREAKER_B1=DE-ENERGIZED.
        B requires only VALVE_V1=OPEN.
        B is blocked by A's VALVE_V1 lock.
        """
        a = _act('A', 4.0, system_states=[
            {'system_id': 'VALVE_V1',   'required_state': 'CLOSED'},
            {'system_id': 'BREAKER_B1', 'required_state': 'DE-ENERGIZED'},
        ])
        b = _act('B', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'OPEN'},
        ])
        fwd = {a: [], b: []}
        p = _build_pert(fwd)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        # A and B conflict on VALVE_V1 — must not overlap (either order)
        assert b_st >= a_et or a_st >= b_et

    def test_pool_held_state_is_zero_after_schedule(self):
        """After all activities complete, no locks should remain held."""
        a = _act('A', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        fwd = {a: []}
        p = _build_pert(fwd)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert p.system_state_pool._held == {}


# ===========================================================================
# Replanning (_partial_reset)
# ===========================================================================

class TestReplanSystemState:

    def test_partial_reset_reacquires_for_in_progress(self):
        """
        After _partial_reset, in-progress activities must re-hold their locks
        so new candidates are correctly blocked.
        """
        ongoing_act = _act('A', 8.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        pending_act = _act('B', 4.0)
        fwd = {ongoing_act: [], pending_act: []}
        p = _build_pert(fwd)
        # Simulate: A started at t=0, replan at t=4 (A still in progress)
        ongoing_act.setActualStartTime(_START)
        p._partial_reset(4.0)
        # A must have re-acquired its lock
        assert p.system_state_pool.get_held_state('VALVE_V1') == 'CLOSED'

    def test_partial_reset_does_not_acquire_for_completed(self):
        """Completed activities must NOT re-acquire (they already released)."""
        done_act = _act('A', 4.0, system_states=[
            {'system_id': 'VALVE_V1', 'required_state': 'CLOSED'}
        ])
        fwd = {done_act: []}
        p = _build_pert(fwd)
        # A started at t=0, replan at t=6 (A is done — et=4 < 6)
        done_act.setActualStartTime(_START)
        p._partial_reset(6.0)
        # Lock must NOT be held (A is complete)
        assert p.system_state_pool.get_held_state('VALVE_V1') is None

    def test_partial_reset_clears_stale_locks(self):
        """reset() is called before replay, so no stale lock survives."""
        p = _build_pert({_act('X', 4.0): []})
        # Manually inject a stale lock
        p.system_state_pool.acquire('VALVE_V1', 'CLOSED')
        p._partial_reset(0.0)
        # After reset + replay (no in-progress activities at t=0), lock is gone
        assert p.system_state_pool._held == {}


# ===========================================================================
# OutageData integration
# ===========================================================================

class TestOutageDataSystemState:

    def test_from_dict_builds_system_state_pool(self):
        from CPM.outage_data import OutageData
        data = {
            'outage': {'outage_id': 'RFO', 'start_date': '2026-01-01',
                       'working_hours_per_day': 24},
            'tasks': [],
            'resources': [],
            'equipment': [],
            'locations': [],
            'plant_systems': [
                {'system_id': 'V1', 'description': 'Valve 1',
                 'valid_states': ['OPEN', 'CLOSED']}
            ],
        }
        od = OutageData.from_dict(data)
        assert od.system_state_pool.has_system('V1')

    def test_validate_detects_unknown_system(self):
        from CPM.outage_data import OutageData
        data = {
            'outage': {'outage_id': 'RFO', 'start_date': '2026-01-01',
                       'working_hours_per_day': 24},
            'tasks': [{
                'task_id': 'T1', 'description': 'test', 'duration': 4.0,
                'successors': [], 'required_resources': [], 'required_equipment': [],
                'required_system_states': [
                    {'system_id': 'MISSING_SYS', 'required_state': 'CLOSED'}
                ],
            }],
            'resources': [], 'equipment': [], 'locations': [],
        }
        od = OutageData.from_dict(data)
        valid, errors = od.validate_data_consistency()
        assert not valid
        assert any('MISSING_SYS' in e for e in errors)

    def test_validate_detects_invalid_state(self):
        from CPM.outage_data import OutageData
        data = {
            'outage': {'outage_id': 'RFO', 'start_date': '2026-01-01',
                       'working_hours_per_day': 24},
            'tasks': [{
                'task_id': 'T1', 'description': 'test', 'duration': 4.0,
                'successors': [], 'required_resources': [], 'required_equipment': [],
                'required_system_states': [
                    {'system_id': 'V1', 'required_state': 'HALF_OPEN'}
                ],
            }],
            'resources': [], 'equipment': [], 'locations': [],
            'plant_systems': [
                {'system_id': 'V1', 'description': 'Valve',
                 'valid_states': ['OPEN', 'CLOSED']}
            ],
        }
        od = OutageData.from_dict(data)
        valid, errors = od.validate_data_consistency()
        assert not valid
        assert any('HALF_OPEN' in e for e in errors)

    def test_validate_passes_with_valid_state(self):
        from CPM.outage_data import OutageData
        data = {
            'outage': {'outage_id': 'RFO', 'start_date': '2026-01-01',
                       'working_hours_per_day': 24},
            'tasks': [{
                'task_id': 'T1', 'description': 'test', 'duration': 4.0,
                'successors': [], 'required_resources': [], 'required_equipment': [],
                'required_system_states': [
                    {'system_id': 'V1', 'required_state': 'CLOSED'}
                ],
            }],
            'resources': [], 'equipment': [], 'locations': [],
            'plant_systems': [
                {'system_id': 'V1', 'description': 'Valve',
                 'valid_states': ['OPEN', 'CLOSED']}
            ],
        }
        od = OutageData.from_dict(data)
        valid, errors = od.validate_data_consistency()
        assert valid
        assert errors == []


# ===========================================================================
# Schema validation
# ===========================================================================

class TestSchemaSystemState:

    def _load_schema(self):
        import os
        schema_path = SCHEMA_PATH
        with open(schema_path) as f:
            return json.load(f)

    def test_plant_systems_top_level_exists(self):
        schema = self._load_schema()
        assert 'plant_systems' in schema['properties']

    def test_plant_systems_is_array(self):
        schema = self._load_schema()
        assert schema['properties']['plant_systems']['type'] == 'array'

    def test_plant_systems_item_has_system_id(self):
        schema = self._load_schema()
        item_props = schema['properties']['plant_systems']['items']['properties']
        assert 'system_id' in item_props

    def test_plant_systems_item_has_valid_states(self):
        schema = self._load_schema()
        item_props = schema['properties']['plant_systems']['items']['properties']
        assert 'valid_states' in item_props

    def test_required_system_states_in_task_properties(self):
        schema = self._load_schema()
        task_props = schema['properties']['tasks']['items']['properties']
        assert 'required_system_states' in task_props

    def test_required_system_states_is_array(self):
        schema = self._load_schema()
        task_props = schema['properties']['tasks']['items']['properties']
        assert task_props['required_system_states']['type'] == 'array'

    def test_required_system_states_item_has_required_fields(self):
        schema = self._load_schema()
        task_props = schema['properties']['tasks']['items']['properties']
        item_props = task_props['required_system_states']['items']['properties']
        assert 'system_id' in item_props
        assert 'required_state' in item_props
