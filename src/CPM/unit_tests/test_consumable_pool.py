"""
Unit tests for ConsumablePool and its integration with the RCPSP scheduler.

Coverage:
- ConsumablePool unit (from_json, fits, consume, restocks, reset)
- Activity.getRequiredConsumables, from_json, to_json_dict, reset, set_mode
- Scheduler integration: deduction on start, blocking when exhausted, restock
- Replanning: _partial_reset replays consumable consumption correctly
- Schema: consumables array and required_consumables task field validate
"""

import math
import json
import pytest
from datetime import datetime

from conftest import assert_valid_schedule
from CPM.outage_data import ConsumablePool
from CPM.activity import Activity
from CPM.pert import Pert

TOL = 1e-9

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _pool_from_list(entries):
    return ConsumablePool.from_json(entries)

def _simple_pool(item_id='AC_SUIT', total=10.0):
    return _pool_from_list([{'item_id': item_id, 'description': 'Test item',
                             'total_quantity': total}])

def _make_pert_with_consumable(acts_fwd, consumable_pool=None):
    """Build a Pert with minimal pools and an optional ConsumablePool."""
    from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool
    p = Pert(graph=acts_fwd)
    p.crew_pool  = ResourcePool()
    p.equipment_pool = EquipmentPool()
    p.location_pool  = LocationPool()
    p.consumable_pool = consumable_pool
    p.startTime = datetime(2026, 1, 1)
    p.generateInfo()
    return p

def _act(name, duration=4.0, consumables=None):
    a = Activity(name, duration)
    if consumables:
        a.required_consumables = consumables
    return a

def _linear_with_consumable(n=3, duration=4.0, consumables=None):
    acts = [_act(str(i), duration, consumables=consumables) for i in range(n)]
    fwd = {}
    for i in range(n - 1):
        fwd[acts[i]] = [acts[i + 1]]
    fwd[acts[-1]] = []
    return acts, fwd

# ===========================================================================
# ConsumablePool unit tests
# ===========================================================================

class TestConsumablePoolInit:

    def test_empty_pool(self):
        p = ConsumablePool()
        assert p.items == {}
        assert p.remaining == {}
        assert p.get_all_item_ids() == []

    def test_from_json_basic(self):
        p = _pool_from_list([
            {'item_id': 'SUIT', 'description': 'AC suit', 'total_quantity': 20.0}
        ])
        assert p.items['SUIT'] == 20.0
        assert p.remaining['SUIT'] == 20.0
        assert p.description['SUIT'] == 'AC suit'

    def test_from_json_multiple_items(self):
        p = _pool_from_list([
            {'item_id': 'A', 'description': 'a', 'total_quantity': 5.0},
            {'item_id': 'B', 'description': 'b', 'total_quantity': 3.0},
        ])
        assert set(p.get_all_item_ids()) == {'A', 'B'}

    def test_from_json_with_restocks(self):
        p = _pool_from_list([{
            'item_id': 'SEAL', 'description': 'Seal kit', 'total_quantity': 4.0,
            'restocks': [{'delivery_hour': 10.0, 'quantity': 4.0},
                         {'delivery_hour': 5.0, 'quantity': 2.0}],
        }])
        # restocks should be sorted by delivery_hour
        hours = [r[0] for r in p.restocks['SEAL']]
        assert hours == sorted(hours)

    def test_has_item(self):
        p = _simple_pool('X')
        assert p.has_item('X') is True
        assert p.has_item('Y') is False

    def test_get_remaining(self):
        p = _simple_pool('X', total=7.0)
        assert abs(p.get_remaining('X') - 7.0) < TOL
        assert p.get_remaining('MISSING') == 0.0


class TestConsumablePoolFitsConsume:

    def test_fits_within_budget(self):
        p = _simple_pool(total=10.0)
        assert p.fits('AC_SUIT', 5.0) is True

    def test_fits_exact(self):
        p = _simple_pool(total=10.0)
        assert p.fits('AC_SUIT', 10.0) is True

    def test_fits_over_budget(self):
        p = _simple_pool(total=10.0)
        assert p.fits('AC_SUIT', 10.1) is False

    def test_fits_unknown_item_permissive(self):
        p = _simple_pool()
        assert p.fits('UNKNOWN', 999.0) is True

    def test_consume_deducts(self):
        p = _simple_pool(total=10.0)
        p.consume('AC_SUIT', 3.0)
        assert abs(p.remaining['AC_SUIT'] - 7.0) < TOL

    def test_consume_floors_at_zero(self):
        p = _simple_pool(total=5.0)
        p.consume('AC_SUIT', 100.0)
        assert p.remaining['AC_SUIT'] == 0.0

    def test_consume_unknown_item_silent(self):
        p = _simple_pool()
        p.consume('NOTHING', 999.0)   # must not raise

    def test_consume_then_fits_false(self):
        p = _simple_pool(total=5.0)
        p.consume('AC_SUIT', 5.0)
        assert p.fits('AC_SUIT', 1.0) is False


class TestConsumablePoolReset:

    def test_reset_restores_remaining(self):
        p = _simple_pool(total=10.0)
        p.consume('AC_SUIT', 8.0)
        p.reset()
        assert abs(p.remaining['AC_SUIT'] - 10.0) < TOL

    def test_reset_clears_restock_cursor(self):
        p = _pool_from_list([{
            'item_id': 'G', 'description': 'Gas', 'total_quantity': 2.0,
            'restocks': [{'delivery_hour': 5.0, 'quantity': 2.0}],
        }])
        p.apply_restocks_up_to(10.0)   # applies the restock
        p.reset()
        # After reset, restock should not have been applied yet
        assert abs(p.remaining['G'] - 2.0) < TOL  # back to initial total
        # And applying again at hour 10 should re-apply it
        p.apply_restocks_up_to(10.0)
        assert abs(p.remaining['G'] - 4.0) < TOL


class TestConsumablePoolRestocks:

    def test_restock_applied_at_delivery_hour(self):
        p = _pool_from_list([{
            'item_id': 'X', 'description': 'X', 'total_quantity': 2.0,
            'restocks': [{'delivery_hour': 10.0, 'quantity': 3.0}],
        }])
        p.consume('X', 2.0)   # exhaust
        p.apply_restocks_up_to(10.0)
        assert abs(p.remaining['X'] - 3.0) < TOL

    def test_restock_not_applied_before_delivery(self):
        p = _pool_from_list([{
            'item_id': 'X', 'description': 'X', 'total_quantity': 2.0,
            'restocks': [{'delivery_hour': 10.0, 'quantity': 3.0}],
        }])
        p.consume('X', 2.0)
        p.apply_restocks_up_to(9.0)   # before delivery
        assert p.remaining['X'] == 0.0

    def test_restock_idempotent(self):
        p = _pool_from_list([{
            'item_id': 'X', 'description': 'X', 'total_quantity': 0.0,
            'restocks': [{'delivery_hour': 5.0, 'quantity': 4.0}],
        }])
        p.apply_restocks_up_to(10.0)
        p.apply_restocks_up_to(10.0)   # second call — must not double-add
        assert abs(p.remaining['X'] - 4.0) < TOL

    def test_multiple_restocks_applied_in_order(self):
        p = _pool_from_list([{
            'item_id': 'X', 'description': 'X', 'total_quantity': 1.0,
            'restocks': [
                {'delivery_hour': 5.0,  'quantity': 2.0},
                {'delivery_hour': 10.0, 'quantity': 3.0},
            ],
        }])
        p.apply_restocks_up_to(7.0)   # only first delivery applies
        assert abs(p.remaining['X'] - 3.0) < TOL
        p.apply_restocks_up_to(12.0)  # second delivery also applies
        assert abs(p.remaining['X'] - 6.0) < TOL

    def test_fits_with_at_hour_applies_restock(self):
        p = _pool_from_list([{
            'item_id': 'X', 'description': 'X', 'total_quantity': 0.0,
            'restocks': [{'delivery_hour': 8.0, 'quantity': 5.0}],
        }])
        # At hour 7, no restock yet — not enough
        assert p.fits('X', 3.0, at_hour=7.0) is False
        # At hour 8, restock applied — enough
        assert p.fits('X', 3.0, at_hour=8.0) is True


# ===========================================================================
# Activity changes
# ===========================================================================

class TestActivityConsumables:

    def test_default_required_consumables(self):
        a = Activity('T', 4.0)
        assert a.required_consumables == []

    def test_get_required_consumables_empty(self):
        a = Activity('T', 4.0)
        assert a.getRequiredConsumables() == []

    def test_from_json_with_consumables(self):
        a = Activity.from_json({
            'task_id': 'T', 'description': 'X', 'duration': 4.0,
            'successors': [], 'required_resources': [], 'required_equipment': [],
            'required_consumables': [{'item_id': 'SUIT', 'quantity_needed': 2}],
        })
        assert a.required_consumables == [{'item_id': 'SUIT', 'quantity_needed': 2}]

    def test_from_json_without_consumables(self):
        a = Activity.from_json({
            'task_id': 'T', 'description': 'X', 'duration': 4.0,
            'successors': [], 'required_resources': [], 'required_equipment': [],
        })
        assert a.required_consumables == []

    def test_to_json_dict_includes_consumables(self):
        a = Activity('T', 4.0)
        a.required_consumables = [{'item_id': 'SEAL', 'quantity_needed': 1}]
        d = a.to_json_dict()
        assert 'required_consumables' in d
        assert d['required_consumables'] == [{'item_id': 'SEAL', 'quantity_needed': 1}]

    def test_to_json_dict_omits_empty_consumables(self):
        a = Activity('T', 4.0)
        d = a.to_json_dict()
        assert 'required_consumables' not in d

    def test_round_trip(self):
        a = Activity.from_json({
            'task_id': 'T', 'description': 'X', 'duration': 4.0,
            'successors': [], 'required_resources': [], 'required_equipment': [],
            'required_consumables': [{'item_id': 'BOTTLE', 'quantity_needed': 3}],
        })
        d = a.to_json_dict()
        a2 = Activity.from_json(d)
        assert a2.required_consumables == [{'item_id': 'BOTTLE', 'quantity_needed': 3}]

    def test_reset_preserves_consumables(self):
        a = Activity('T', 4.0)
        a.required_consumables = [{'item_id': 'SUIT', 'quantity_needed': 2}]
        a.reset()
        assert a.required_consumables == [{'item_id': 'SUIT', 'quantity_needed': 2}]

    def test_set_mode_overrides_consumables(self):
        a = Activity('T', 4.0)
        a.modes = [{
            'mode_id': 'clean',
            'duration': 6.0,
            'required_resources': [],
            'required_equipment': [],
            'required_consumables': [{'item_id': 'SEAL', 'quantity_needed': 5}],
        }]
        a.set_mode('clean')
        assert a.required_consumables == [{'item_id': 'SEAL', 'quantity_needed': 5}]

    def test_set_mode_no_consumables_field_leaves_existing(self):
        a = Activity('T', 4.0)
        a.required_consumables = [{'item_id': 'OLD', 'quantity_needed': 1}]
        a.modes = [{
            'mode_id': 'basic',
            'duration': 3.0,
            'required_resources': [],
            'required_equipment': [],
            # no required_consumables key
        }]
        a.set_mode('basic')
        # existing consumables preserved when mode doesn't override them
        assert a.required_consumables == [{'item_id': 'OLD', 'quantity_needed': 1}]


# ===========================================================================
# Scheduler integration
# ===========================================================================

class TestSchedulerConsumableIntegration:

    def _build(self, fwd, pool):
        return _make_pert_with_consumable(fwd, consumable_pool=pool)

    def test_no_consumable_pool_schedules_normally(self):
        acts, fwd = _linear_with_consumable(3)
        p = self._build(fwd, None)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 3
        assert_valid_schedule(p)

    def test_activity_with_zero_consumables_schedules(self):
        acts, fwd = _linear_with_consumable(2, consumables=[])
        pool = _simple_pool(total=0.0)
        p = self._build(fwd, pool)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        assert_valid_schedule(p)

    def test_deducts_inventory_on_start(self):
        a = _act('A', 4.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 3}])
        b = _act('B', 4.0)
        fwd = {a: [b], b: []}
        pool = _simple_pool('SUIT', total=10.0)
        p = self._build(fwd, pool)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert_valid_schedule(p)
        # After both activities scheduled, A consumed 3 suits (B has none)
        assert abs(p.consumable_pool.remaining['SUIT'] - 7.0) < TOL

    def test_blocks_when_pool_exhausted(self):
        """
        Two independent activities both need 6 suits, pool has 10.
        Consumables are non-renewable: once A takes 6, only 4 remain
        permanently — B needs 6 but can never start (deadlock).
        Only 1 activity completes; 4 suits remain unused.
        """
        a = _act('A', 2.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 6}])
        b = _act('B', 2.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 6}])
        fwd = {a: [], b: []}
        pool = _simple_pool('SUIT', total=10.0)
        p = self._build(fwd, pool)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Only A completes; B is permanently blocked (10 - 6 = 4 < 6 needed)
        assert result['n_completed'] == 1
        # 4 suits remain (A took 6; B never started)
        assert p.consumable_pool.remaining['SUIT'] == 4.0

    def test_sequential_fits_when_pool_is_sufficient(self):
        """
        Two independent activities both need 6 suits, pool has 12.
        After A takes 6 suits (6 remain), B can start with the remaining 6.
        Both complete; pool is fully depleted.
        """
        a = _act('A', 2.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 6}])
        b = _act('B', 2.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 6}])
        fwd = {a: [], b: []}
        pool = _simple_pool('SUIT', total=12.0)
        p = self._build(fwd, pool)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        assert_valid_schedule(p)
        assert p.consumable_pool.remaining['SUIT'] == 0.0

    def test_activity_blocked_until_restock(self):
        """
        Pool starts with 0 suits. Restock of 5 at hour 8.
        Activity needing 5 suits should be deferred until hour >= 8.
        """
        a = _act('A', 2.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 5}])
        b = _act('B', 8.0)   # no consumables, runs first
        fwd = {b: [a], a: []}
        pool = _pool_from_list([{
            'item_id': 'SUIT', 'description': 'AC suit', 'total_quantity': 0.0,
            'restocks': [{'delivery_hour': 8.0, 'quantity': 5.0}],
        }])
        p = self._build(fwd, pool)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        assert_valid_schedule(p)
        # A starts after B finishes (at 8h); restock is available by then
        assert p.consumable_pool.remaining['SUIT'] == 0.0

    def test_fits_with_tentative_blocks_when_exhausted(self):
        """Direct call to _fits_with_tentative returns False when pool is at 0."""
        a = _act('A', 4.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 1}])
        fwd = {a: []}
        pool = _simple_pool('SUIT', total=0.0)
        p = self._build(fwd, pool)
        p.calculateScheduleWithResources()   # populate infoDict and snapshot infra

        res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid = \
            p._build_capacity_snapshots(p.startTime, p.startTime, extra_boundaries=None)
        result = p._fits_with_tentative(
            a, p.startTime,
            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid=None,
        )
        assert result is False

    def test_fits_with_tentative_passes_with_inventory(self):
        """_fits_with_tentative returns True when pool has enough."""
        a = _act('A', 4.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 3}])
        fwd = {a: []}
        pool = _simple_pool('SUIT', total=10.0)
        p = self._build(fwd, pool)
        p.calculateScheduleWithResources()

        res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid = \
            p._build_capacity_snapshots(p.startTime, p.startTime, extra_boundaries=None)
        result = p._fits_with_tentative(
            a, p.startTime,
            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid=None,
        )
        assert result is True

    def test_reset_restores_inventory_for_second_run(self):
        a = _act('A', 2.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 4}])
        fwd = {a: []}
        pool = _simple_pool('SUIT', total=4.0)
        p = self._build(fwd, pool)
        p.calculateScheduleWithResources()
        assert p.consumable_pool.remaining['SUIT'] == 0.0
        # Run again — reset should restore inventory
        p.calculateScheduleWithResources()
        assert_valid_schedule(p)
        assert p.consumable_pool.remaining['SUIT'] == 0.0  # consumed again


# ===========================================================================
# Replanning
# ===========================================================================

class TestReplanConsumables:

    def _build_replan_pert(self, consumable_pool):
        from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool
        a = _act('A', 4.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 2}])
        b = _act('B', 4.0, consumables=[{'item_id': 'SUIT', 'quantity_needed': 2}])
        c = _act('C', 4.0)
        fwd = {a: [b], b: [c], c: []}
        p = Pert(graph=fwd)
        p.crew_pool  = ResourcePool()
        p.equipment_pool = EquipmentPool()
        p.location_pool  = LocationPool()
        p.consumable_pool = consumable_pool
        p.startTime = datetime(2026, 1, 1)
        p.generateInfo()
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        return p, a, b, c

    def test_partial_reset_replays_completed_consumption(self):
        pool = _simple_pool('SUIT', total=10.0)
        p, a, b, c = self._build_replan_pert(pool)
        # At t=5h: A is completed (ended at 4h), B is in-progress
        p.replan(current_time_hours=5.0, sgs='max_use_res_ranked')
        # A consumed 2 (completed), B consumed 2 (in-progress), C not yet started
        # remaining = 10 - 2 - 2 = 6 after replay (before C)
        # After full replan C also runs: 10 - 2 - 2 - 0 = 6 remaining
        # (C has no consumables)
        assert abs(p.consumable_pool.remaining['SUIT'] - 6.0) < TOL

    def test_replan_without_consumable_pool_is_safe(self):
        """replan() with consumable_pool=None must not raise."""
        from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool
        a = _act('A', 4.0)
        b = _act('B', 4.0)
        fwd = {a: [b], b: []}
        p = Pert(graph=fwd)
        p.crew_pool  = ResourcePool()
        p.equipment_pool = EquipmentPool()
        p.location_pool  = LocationPool()
        p.consumable_pool = None
        p.startTime = datetime(2026, 1, 1)
        p.generateInfo()
        p.calculateScheduleWithResources()
        # Should not raise
        p.replan(current_time_hours=5.0)


# ===========================================================================
# Schema validation
# ===========================================================================

class TestSchemaConsumables:

    def _schema(self):
        import os
        schema_path = os.path.join(
            os.path.dirname(__file__), '..', 'outage_schema.json'
        )
        with open(schema_path) as f:
            return json.load(f)

    def test_consumables_key_in_schema(self):
        schema = self._schema()
        assert 'consumables' in schema['properties']

    def test_consumables_item_requires_item_id_description_total_quantity(self):
        schema = self._schema()
        item_schema = schema['properties']['consumables']['items']
        assert set(item_schema['required']) == {'item_id', 'description', 'total_quantity'}

    def test_consumables_has_restocks_optional(self):
        schema = self._schema()
        item_props = schema['properties']['consumables']['items']['properties']
        assert 'restocks' in item_props
        # restocks is not in 'required'
        assert 'restocks' not in schema['properties']['consumables']['items'].get('required', [])

    def test_required_consumables_in_task_schema(self):
        schema = self._schema()
        task_props = schema['properties']['tasks']['items']['properties']
        assert 'required_consumables' in task_props

    def test_backward_compat_no_consumables_key(self):
        """JSON without 'consumables' key still loads into OutageData without error."""
        from CPM.outage_data import OutageData
        data = {
            'outage': {
                'outage_id': 'TEST', 'start_date': '2026-01-01',
                'working_hours_per_day': 24,
            },
            'tasks': [{
                'task_id': 'T1', 'description': 'Task', 'duration': 4.0,
                'successors': [], 'required_resources': [], 'required_equipment': [],
            }],
            'resources': [],
            'equipment': [],
            'locations': [],
            # no 'consumables' key
        }
        od = OutageData.from_dict(data)
        assert od.consumable_pool is not None
        assert od.consumable_pool.get_all_item_ids() == []

    def test_validate_consistency_catches_unknown_item_id(self):
        from CPM.outage_data import OutageData
        data = {
            'outage': {
                'outage_id': 'TEST', 'start_date': '2026-01-01',
                'working_hours_per_day': 24,
            },
            'tasks': [{
                'task_id': 'T1', 'description': 'Task', 'duration': 4.0,
                'successors': [], 'required_resources': [], 'required_equipment': [],
                'required_consumables': [{'item_id': 'GHOST', 'quantity_needed': 1}],
            }],
            'resources': [],
            'equipment': [],
            'locations': [],
            'consumables': [],   # GHOST not declared
        }
        od = OutageData.from_dict(data)
        valid, errors = od.validate_data_consistency()
        assert valid is False
        assert any('GHOST' in e for e in errors)

    def test_validate_consistency_passes_known_item_id(self):
        from CPM.outage_data import OutageData
        data = {
            'outage': {
                'outage_id': 'TEST', 'start_date': '2026-01-01',
                'working_hours_per_day': 24,
            },
            'tasks': [{
                'task_id': 'T1', 'description': 'Task', 'duration': 4.0,
                'successors': [], 'required_resources': [], 'required_equipment': [],
                'required_consumables': [{'item_id': 'SUIT', 'quantity_needed': 2}],
            }],
            'resources': [],
            'equipment': [],
            'locations': [],
            'consumables': [{'item_id': 'SUIT', 'description': 'AC suit',
                             'total_quantity': 10.0}],
        }
        od = OutageData.from_dict(data)
        valid, errors = od.validate_data_consistency()
        assert valid is True
        assert errors == []
