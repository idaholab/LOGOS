"""
Unit tests for equipment zone-affinity (Option B).

Coverage:
- EquipmentAvailability: zone_id field defaults to None; set via constructor
- EquipmentPool.from_json: parses zone_id; None when absent
- EquipmentPool.get_zone_id: returns zone_id or None for unknown equipment
- Backward compatibility: unzoned equipment is unconstrained
- Backward compatibility: activity with no zone_ids is unconstrained
- Scheduler enforcement: activity in correct zone is allowed
- Scheduler enforcement: activity in wrong zone is blocked
- Scheduler enforcement: unzoned equipment is never blocked
- Scheduler enforcement: activity with no zones uses any equipment
- validate_data_consistency: unknown zone_id raises error; valid pass
- Schema: zone_id field present in equipment properties
"""

import json
import pytest
from datetime import datetime, timedelta

from conftest import assert_valid_schedule
from CPM.outage_data import (
    EquipmentAvailability, EquipmentPool, LocationPool,
    ResourcePool, OutageData
)
from CPM.activity import Activity
from CPM.pert import Pert

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_START = datetime(2026, 1, 1)
_END   = datetime(2026, 12, 31)

_PERIOD = [{'start_date': _START, 'end_date': _END, 'quantity_available': 2}]
_PERIOD_JSON = [{'start_date': _START.isoformat(), 'end_date': _END.isoformat(),
                 'quantity_available': 2}]


def _eq(eq_id, zone_id=None):
    """Build an EquipmentAvailability directly."""
    return EquipmentAvailability(eq_id, f'desc-{eq_id}', _PERIOD, zone_id=zone_id)


def _pool_from_json(entries):
    return EquipmentPool.from_json(entries)


def _act(name, duration=4.0, eq_reqs=None, zone_ids=None):
    a = Activity(name, duration)
    a.required_resources = []
    if eq_reqs:
        a.required_equipment = eq_reqs
    if zone_ids is not None:
        a.zone_ids = zone_ids
    return a


def _loc_pool(*loc_ids):
    lp = LocationPool()
    from CPM.outage_data import LocationAvailability
    for lid in loc_ids:
        lp.locations[lid] = LocationAvailability(
            lid, f'desc-{lid}',
            [{'start_date': _START, 'end_date': _END,
              'max_concurrent_tasks': 10, 'max_concurrent_workers': None}]
        )
    return lp


def _build_pert(fwd, eq_pool=None, loc_pool=None):
    p = Pert(graph=fwd)
    p.crew_pool    = ResourcePool()
    p.equipment_pool   = eq_pool or EquipmentPool()
    p.location_pool    = loc_pool or LocationPool()
    p.consumable_pool  = None
    p.system_state_pool = None
    p.startTime = _START
    p.generateInfo()
    return p


# ===========================================================================
# EquipmentAvailability — field tests
# ===========================================================================

class TestEquipmentAvailabilityZoneId:

    def test_default_zone_id_is_none(self):
        ea = _eq('PUMP_A')
        assert ea.zone_id is None

    def test_zone_id_stored(self):
        ea = _eq('PUMP_A', zone_id='ZONE_1')
        assert ea.zone_id == 'ZONE_1'

    def test_zone_id_does_not_affect_availability(self):
        ea = _eq('PUMP_A', zone_id='ZONE_1')
        assert ea.get_availability_at(_START) == 2

    def test_zone_id_none_explicit(self):
        ea = EquipmentAvailability('X', 'desc', _PERIOD, zone_id=None)
        assert ea.zone_id is None


# ===========================================================================
# EquipmentPool.from_json — parsing
# ===========================================================================

class TestEquipmentPoolFromJson:

    def _entry(self, eq_id, zone_id=None):
        d = {'equipment_id': eq_id, 'description': f'desc-{eq_id}',
             'availability_periods': _PERIOD_JSON}
        if zone_id is not None:
            d['zone_id'] = zone_id
        return d

    def test_no_zone_id_in_json(self):
        pool = _pool_from_json([self._entry('EQ1')])
        assert pool.get_zone_id('EQ1') is None

    def test_zone_id_parsed(self):
        pool = _pool_from_json([self._entry('EQ1', 'ZONE_A')])
        assert pool.get_zone_id('EQ1') == 'ZONE_A'

    def test_mixed_zoned_unzoned(self):
        pool = _pool_from_json([
            self._entry('EQ_ZONED',   'ZONE_A'),
            self._entry('EQ_FREE'),
        ])
        assert pool.get_zone_id('EQ_ZONED') == 'ZONE_A'
        assert pool.get_zone_id('EQ_FREE')  is None

    def test_empty_pool(self):
        pool = _pool_from_json([])
        assert pool.get_zone_id('MISSING') is None


# ===========================================================================
# EquipmentPool.get_zone_id
# ===========================================================================

class TestEquipmentPoolGetZoneId:

    def test_unknown_equipment_returns_none(self):
        pool = EquipmentPool()
        assert pool.get_zone_id('DOES_NOT_EXIST') is None

    def test_known_equipment_no_zone(self):
        pool = EquipmentPool()
        pool.equipment['EQ1'] = _eq('EQ1')
        assert pool.get_zone_id('EQ1') is None

    def test_known_equipment_with_zone(self):
        pool = EquipmentPool()
        pool.equipment['EQ1'] = _eq('EQ1', zone_id='ROOM_A')
        assert pool.get_zone_id('EQ1') == 'ROOM_A'


# ===========================================================================
# Scheduler: zone-affinity enforcement
# ===========================================================================

class TestSchedulerEquipmentZone:
    """Integration tests exercising _fits_with_tentative zone-affinity check."""

    def _schedule(self, acts, eq_pool, loc_pool=None):
        """Run SGS and return (set of scheduled activity names, pert)."""
        # Pert expects {activity_object: [successors_list]}
        fwd = {a: [] for a in acts}
        p = _build_pert(fwd, eq_pool=eq_pool,
                        loc_pool=loc_pool or _loc_pool('ZONE_A', 'ZONE_B'))
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        return {a.name for a in p.completed if a.name not in ('Source', 'Sink')}, p

    # --- backward-compat: unzoned equipment --------------------------------

    def test_unzoned_equipment_allowed_any_activity(self):
        """Equipment with no zone_id must never block any activity."""
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP')                # no zone
        act = _act('A', eq_reqs=[{'equipment_id': 'PUMP', 'quantity_needed': 1}],
                   zone_ids=['ZONE_A'])
        scheduled, p = self._schedule([act], ep)
        assert_valid_schedule(p)
        assert 'A' in scheduled

    def test_unzoned_equipment_allowed_no_zone_activity(self):
        """Unzoned equipment + activity with no zones → no constraint."""
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP')
        act = _act('A', eq_reqs=[{'equipment_id': 'PUMP', 'quantity_needed': 1}])
        # act has no zone_ids — act_zones will be empty → guard fires → unconstrained
        scheduled, p = self._schedule([act], ep)
        assert_valid_schedule(p)
        assert 'A' in scheduled

    # --- backward-compat: activity with no zones ---------------------------

    def test_zoned_equipment_allowed_when_activity_has_no_zones(self):
        """Activity with no zone_ids is unconstrained even if equipment has zone_id."""
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP', zone_id='ZONE_A')
        act = _act('A', eq_reqs=[{'equipment_id': 'PUMP', 'quantity_needed': 1}])
        # act_zones empty → guard fires → activity is unconstrained
        scheduled, p = self._schedule([act], ep)
        assert_valid_schedule(p)
        assert 'A' in scheduled

    # --- enforcement -------------------------------------------------------

    def test_correct_zone_is_allowed(self):
        """Activity in matching zone must be allowed."""
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP', zone_id='ZONE_A')
        act = _act('A', eq_reqs=[{'equipment_id': 'PUMP', 'quantity_needed': 1}],
                   zone_ids=['ZONE_A'])
        scheduled, p = self._schedule([act], ep)
        assert_valid_schedule(p)
        assert 'A' in scheduled

    def test_wrong_zone_is_blocked(self):
        """Activity in a different zone must be blocked when equipment is zone-locked."""
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP', zone_id='ZONE_A')
        act = _act('A', eq_reqs=[{'equipment_id': 'PUMP', 'quantity_needed': 1}],
                   zone_ids=['ZONE_B'])
        scheduled, _ = self._schedule([act], ep)
        assert 'A' not in scheduled

    def test_multi_zone_activity_allowed_when_one_matches(self):
        """Activity declaring multiple zones is allowed if one matches the equipment zone."""
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP', zone_id='ZONE_A')
        act = _act('A', eq_reqs=[{'equipment_id': 'PUMP', 'quantity_needed': 1}],
                   zone_ids=['ZONE_B', 'ZONE_A'])
        scheduled, p = self._schedule([act], ep)
        assert_valid_schedule(p)
        assert 'A' in scheduled

    def test_two_activities_only_correct_one_scheduled(self):
        """Given zoned equipment, only the activity in the right zone is scheduled."""
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP', zone_id='ZONE_A')
        act_ok  = _act('OK',  eq_reqs=[{'equipment_id': 'PUMP', 'quantity_needed': 1}],
                       zone_ids=['ZONE_A'])
        act_bad = _act('BAD', eq_reqs=[{'equipment_id': 'PUMP', 'quantity_needed': 1}],
                       zone_ids=['ZONE_B'])
        scheduled, _ = self._schedule([act_ok, act_bad], ep)
        assert 'OK'  in scheduled
        assert 'BAD' not in scheduled

    def test_zone_check_independent_of_quantity(self):
        """Zone mismatch blocks even when there is capacity remaining."""
        ep = EquipmentPool()
        # Ample capacity (2 units), but wrong zone
        ep.equipment['PUMP'] = _eq('PUMP', zone_id='ZONE_A')
        act = _act('A', eq_reqs=[{'equipment_id': 'PUMP', 'quantity_needed': 1}],
                   zone_ids=['ZONE_B'])
        scheduled, _ = self._schedule([act], ep)
        assert 'A' not in scheduled

    def test_multiple_equipment_one_zoned_mismatch_blocks(self):
        """If any equipment fails the zone check, the whole activity is blocked."""
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP', zone_id='ZONE_A')
        ep.equipment['CRANE'] = _eq('CRANE')  # no zone constraint
        act = _act('A',
                   eq_reqs=[{'equipment_id': 'PUMP',  'quantity_needed': 1},
                             {'equipment_id': 'CRANE', 'quantity_needed': 1}],
                   zone_ids=['ZONE_B'])
        scheduled, _ = self._schedule([act], ep)
        assert 'A' not in scheduled

    def test_multiple_equipment_all_zones_match(self):
        """Activity is allowed when every piece of zoned equipment matches."""
        ep = EquipmentPool()
        ep.equipment['PUMP']  = _eq('PUMP',  zone_id='ZONE_A')
        ep.equipment['CRANE'] = _eq('CRANE', zone_id='ZONE_A')
        act = _act('A',
                   eq_reqs=[{'equipment_id': 'PUMP',  'quantity_needed': 1},
                             {'equipment_id': 'CRANE', 'quantity_needed': 1}],
                   zone_ids=['ZONE_A'])
        scheduled, p = self._schedule([act], ep)
        assert_valid_schedule(p)
        assert 'A' in scheduled


# ===========================================================================
# validate_data_consistency — zone_id reference validation
# ===========================================================================

class TestValidateEquipmentZone:

    def _minimal_data(self, equipment=None, locations=None, tasks=None):
        """Build a minimal OutageData for validation testing."""
        from CPM.outage_data import ConsumablePool, SystemStatePool
        od = OutageData(
            outage_config={'outage_id': 'TEST', 'start_date': '2026-01-01',
                           'working_hours_per_day': 24},
            tasks=tasks or [],
            crew_pool=ResourcePool(),
            equipment_pool=equipment or EquipmentPool(),
            location_pool=locations or LocationPool(),
            consumable_pool=ConsumablePool(),
            system_state_pool=SystemStatePool(),
        )
        return od

    def test_valid_zone_id_passes(self):
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP', zone_id='ZONE_A')
        lp = _loc_pool('ZONE_A')
        od = self._minimal_data(equipment=ep, locations=lp)
        valid, errors = od.validate_data_consistency()
        assert valid
        assert errors == []

    def test_unknown_zone_id_raises_error(self):
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP', zone_id='NONEXISTENT')
        od = self._minimal_data(equipment=ep)
        valid, errors = od.validate_data_consistency()
        assert not valid
        assert any("NONEXISTENT" in e for e in errors)

    def test_no_zone_id_passes(self):
        ep = EquipmentPool()
        ep.equipment['PUMP'] = _eq('PUMP')   # no zone
        od = self._minimal_data(equipment=ep)
        valid, errors = od.validate_data_consistency()
        assert valid
        assert errors == []

    def test_mixed_valid_invalid(self):
        ep = EquipmentPool()
        ep.equipment['PUMP']  = _eq('PUMP',  zone_id='ZONE_A')
        ep.equipment['CRANE'] = _eq('CRANE', zone_id='GHOST')
        lp = _loc_pool('ZONE_A')
        od = self._minimal_data(equipment=ep, locations=lp)
        valid, errors = od.validate_data_consistency()
        assert not valid
        assert any('CRANE' in e for e in errors)
        assert not any('PUMP' in e for e in errors)


# ===========================================================================
# Schema: zone_id field present in equipment properties
# ===========================================================================

class TestSchemaEquipmentZone:

    def _load_schema(self):
        import os
        schema_path = os.path.join(
            os.path.dirname(__file__), '..', 'outage_schema.json'
        )
        with open(schema_path) as f:
            return json.load(f)

    def test_zone_id_field_in_equipment_schema(self):
        schema = self._load_schema()
        eq_props = schema['properties']['equipment']['items']['properties']
        assert 'zone_id' in eq_props

    def test_zone_id_is_string_type(self):
        schema = self._load_schema()
        eq_props = schema['properties']['equipment']['items']['properties']
        assert eq_props['zone_id']['type'] == 'string'

    def test_zone_id_not_required(self):
        schema = self._load_schema()
        eq_required = schema['properties']['equipment']['items'].get('required', [])
        assert 'zone_id' not in eq_required
