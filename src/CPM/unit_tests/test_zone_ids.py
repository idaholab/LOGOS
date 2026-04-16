"""
Unit tests for Option C: zone_ids multi-zone support.

Coverage:
- Activity.getZoneIds() — backward compat (location_id fallback), explicit zone_ids,
  both fields, no location
- Activity.from_json / to_json_dict roundtrip for zone_ids
- Old JSON (no zone_ids key) loads without error
- LocationAvailability: zone_type field (default 'physical', explicit 'permit')
- LocationPool.from_json parses zone_type; get_zone_type() helper
- Scheduler: activity blocked when permit zone is at capacity
- Scheduler: multi-zone activity blocked when any one zone is at capacity
- Scheduler: single-zone activity is NOT blocked by a different zone being full
- Replan: _build_capacity_snapshots and _fits_with_tentative respect zone_ids
"""

import json
import pytest
from datetime import datetime, timedelta

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.outage_data import LocationAvailability, LocationPool, ResourcePool, EquipmentPool
from CPM.pert import Pert

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_START = datetime(2026, 1, 1)
_END   = datetime(2026, 12, 31)


def _period(max_tasks=2, max_workers=None, start=_START, end=_END):
    p = {
        'start_date': start.isoformat(),
        'end_date':   end.isoformat(),
        'max_concurrent_tasks': max_tasks,
    }
    if max_workers is not None:
        p['max_concurrent_workers'] = max_workers
    return p


def _loc_json(loc_id, max_tasks=2, zone_type='physical', max_workers=None):
    return {
        'location_id': loc_id,
        'description': loc_id,
        'zone_type':   zone_type,
        'availability_periods': [_period(max_tasks, max_workers)],
    }


def _act(name, duration=4.0, location_id=None, zone_ids=None,
         res_count=0, skill='MECH'):
    """Create an Activity.  res_count=0 means no resource requirement (default)."""
    a = Activity(name, duration)
    a.location_id = location_id
    if zone_ids is not None:
        a.zone_ids = zone_ids
    if res_count > 0:
        a.required_resources = [{'skill_type': skill, 'crew_count': res_count}]
    else:
        a.required_resources = []
    return a


def _build_pert(fwd, locations_json=None, mech_count=0):
    """Build a Pert with minimal pools and optional location/resource data.

    mech_count: if > 0, add a MECH resource availability entry to the pool
    so activities that require MECH workers can be scheduled.
    """
    from CPM.outage_data import ResourceAvailability
    p = Pert(graph=fwd)
    p.crew_pool  = ResourcePool()
    if mech_count > 0:
        periods = [{'start_date': _START, 'end_date': _END,
                    'available_count': mech_count}]
        p.crew_pool.resources['MECH'] = ResourceAvailability(
            'MECH', periods, resource_type='renewable'
        )
    p.equipment_pool = EquipmentPool()
    if locations_json:
        p.location_pool = LocationPool.from_json(locations_json)
    else:
        p.location_pool = LocationPool()
    p.consumable_pool = None
    p.startTime = _START
    p.generateInfo()
    return p


# ===========================================================================
# Activity.getZoneIds() — unit tests
# ===========================================================================

class TestGetZoneIds:

    def test_no_location_returns_empty(self):
        a = Activity('T', 4.0)
        assert a.getZoneIds() == []

    def test_location_id_only_falls_back(self):
        a = Activity('T', 4.0, location_id='ROOM_A')
        assert a.getZoneIds() == ['ROOM_A']

    def test_explicit_zone_ids_returned(self):
        a = Activity('T', 4.0)
        a.zone_ids = ['PERMIT_1', 'ROOM_A']
        assert a.getZoneIds() == ['PERMIT_1', 'ROOM_A']

    def test_zone_ids_takes_precedence_over_location_id(self):
        """When both are set, zone_ids wins (explicit always beats implicit)."""
        a = Activity('T', 4.0, location_id='OLD')
        a.zone_ids = ['PERMIT_1']
        assert a.getZoneIds() == ['PERMIT_1']

    def test_returns_copy_not_reference(self):
        a = Activity('T', 4.0)
        a.zone_ids = ['Z1', 'Z2']
        result = a.getZoneIds()
        result.append('Z3')
        assert a.zone_ids == ['Z1', 'Z2']

    def test_empty_zone_ids_falls_back_to_location_id(self):
        a = Activity('T', 4.0, location_id='ROOM_A')
        a.zone_ids = []  # explicitly empty
        assert a.getZoneIds() == ['ROOM_A']


# ===========================================================================
# Activity.from_json / to_json_dict roundtrip
# ===========================================================================

class TestZoneIdsRoundtrip:

    def _task_dict(self, **kwargs):
        base = {
            'task_id': 'T1',
            'description': 'Test task',
            'duration': 4.0,
            'successors': [],
            'required_resources': [],
            'required_equipment': [],
        }
        base.update(kwargs)
        return base

    def test_from_json_no_zone_ids_field(self):
        """Old JSON without zone_ids loads cleanly with empty list."""
        d = self._task_dict(location_id='ROOM_A')
        a = Activity.from_json(d)
        assert a.zone_ids == []
        # Backward compat: getZoneIds still returns ['ROOM_A']
        assert a.getZoneIds() == ['ROOM_A']

    def test_from_json_with_zone_ids(self):
        d = self._task_dict(zone_ids=['PERMIT_1', 'ROOM_A'])
        a = Activity.from_json(d)
        assert a.zone_ids == ['PERMIT_1', 'ROOM_A']
        assert a.getZoneIds() == ['PERMIT_1', 'ROOM_A']

    def test_to_json_dict_omits_zone_ids_when_empty(self):
        d = self._task_dict(location_id='ROOM_A')
        a = Activity.from_json(d)
        out = a.to_json_dict()
        assert 'zone_ids' not in out

    def test_to_json_dict_includes_zone_ids_when_set(self):
        d = self._task_dict(zone_ids=['P1', 'R1'])
        a = Activity.from_json(d)
        out = a.to_json_dict()
        assert out['zone_ids'] == ['P1', 'R1']

    def test_roundtrip_preserves_zone_ids(self):
        original = ['PERMIT_1', 'ROOM_A', 'AUX_ZONE']
        d = self._task_dict(location_id='ROOM_A', zone_ids=original)
        a = Activity.from_json(d)
        out = a.to_json_dict()
        a2 = Activity.from_json(out)
        assert a2.zone_ids == original

    def test_from_json_zone_ids_is_list_copy(self):
        original = ['P1', 'P2']
        d = self._task_dict(zone_ids=original)
        a = Activity.from_json(d)
        original.append('P3')
        assert a.zone_ids == ['P1', 'P2']


# ===========================================================================
# LocationAvailability zone_type
# ===========================================================================

class TestLocationAvailabilityZoneType:

    def _make_loc(self, zone_type='physical'):
        periods = [{
            'start_date': _START,
            'end_date':   _END,
            'max_concurrent_tasks': 2,
        }]
        return LocationAvailability('LOC', 'desc', periods, zone_type=zone_type)

    def test_default_zone_type_is_physical(self):
        la = self._make_loc()
        assert la.zone_type == 'physical'

    def test_explicit_permit_zone_type(self):
        la = self._make_loc(zone_type='permit')
        assert la.zone_type == 'permit'

    def test_repr_includes_zone_type(self):
        la = self._make_loc(zone_type='permit')
        assert 'permit' in repr(la)


# ===========================================================================
# LocationPool.from_json and get_zone_type()
# ===========================================================================

class TestLocationPoolZoneType:

    def test_from_json_defaults_to_physical(self):
        locs = [_loc_json('ROOM_A')]
        pool = LocationPool.from_json(locs)
        assert pool.locations['ROOM_A'].zone_type == 'physical'

    def test_from_json_parses_permit(self):
        locs = [_loc_json('PERMIT_1', zone_type='permit')]
        pool = LocationPool.from_json(locs)
        assert pool.locations['PERMIT_1'].zone_type == 'permit'

    def test_get_zone_type_known_physical(self):
        locs = [_loc_json('ROOM_A', zone_type='physical')]
        pool = LocationPool.from_json(locs)
        assert pool.get_zone_type('ROOM_A') == 'physical'

    def test_get_zone_type_known_permit(self):
        locs = [_loc_json('PERMIT_1', zone_type='permit')]
        pool = LocationPool.from_json(locs)
        assert pool.get_zone_type('PERMIT_1') == 'permit'

    def test_get_zone_type_unknown_returns_physical(self):
        pool = LocationPool()
        assert pool.get_zone_type('NONEXISTENT') == 'physical'

    def test_mixed_zone_types_in_pool(self):
        locs = [
            _loc_json('ROOM_A',    zone_type='physical'),
            _loc_json('PERMIT_1',  zone_type='permit'),
        ]
        pool = LocationPool.from_json(locs)
        assert pool.get_zone_type('ROOM_A')   == 'physical'
        assert pool.get_zone_type('PERMIT_1') == 'permit'


# ===========================================================================
# Scheduler integration
# ===========================================================================

class TestSchedulerZoneIds:
    """
    Verify that the parallel SGS correctly enforces location capacity
    when activities use zone_ids instead of location_id.
    """

    def test_backward_compat_location_id_still_enforced(self):
        """
        Room capacity max_tasks=1: two activities share the same room via
        location_id (no zone_ids).  They must run sequentially.
        """
        a = _act('A', 4.0, location_id='ROOM_A')
        b = _act('B', 4.0, location_id='ROOM_A')
        fwd = {a: [], b: []}
        locs = [_loc_json('ROOM_A', max_tasks=1)]
        p = _build_pert(fwd, locs)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        assert_valid_schedule(p)
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        # Non-overlapping: one must finish before the other starts
        assert a_et <= b_st or b_et <= a_st

    def test_zone_ids_single_zone_enforces_capacity(self):
        """
        Same as above but using zone_ids=['ROOM_A'] instead of location_id.
        """
        a = _act('A', 4.0)
        b = _act('B', 4.0)
        a.zone_ids = ['ROOM_A']
        b.zone_ids = ['ROOM_A']
        fwd = {a: [], b: []}
        locs = [_loc_json('ROOM_A', max_tasks=1)]
        p = _build_pert(fwd, locs)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        assert_valid_schedule(p)
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        assert a_et <= b_st or b_et <= a_st

    def test_multi_zone_blocked_by_permit_zone(self):
        """
        Activity A occupies both PERMIT_1 (max_tasks=1) and ROOM_A (max_tasks=2).
        Activity B also occupies PERMIT_1.
        Because the permit zone serialises them, A and B must not overlap.
        """
        a = _act('A', 4.0)
        b = _act('B', 4.0)
        a.zone_ids = ['PERMIT_1', 'ROOM_A']
        b.zone_ids = ['PERMIT_1']
        fwd = {a: [], b: []}
        locs = [
            _loc_json('PERMIT_1', max_tasks=1, zone_type='permit'),
            _loc_json('ROOM_A',   max_tasks=2, zone_type='physical'),
        ]
        p = _build_pert(fwd, locs)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        assert_valid_schedule(p)
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        assert a_et <= b_st or b_et <= a_st

    def test_multi_zone_blocked_by_room_capacity(self):
        """
        Activity A occupies PERMIT_1 (max_tasks=2) and ROOM_A (max_tasks=1).
        Activity B occupies ROOM_A only.
        ROOM_A serialises them.
        """
        a = _act('A', 4.0)
        b = _act('B', 4.0)
        a.zone_ids = ['PERMIT_1', 'ROOM_A']
        b.zone_ids = ['ROOM_A']
        fwd = {a: [], b: []}
        locs = [
            _loc_json('PERMIT_1', max_tasks=2, zone_type='permit'),
            _loc_json('ROOM_A',   max_tasks=1, zone_type='physical'),
        ]
        p = _build_pert(fwd, locs)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        assert_valid_schedule(p)
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        assert a_et <= b_st or b_et <= a_st

    def test_different_zones_do_not_block_each_other(self):
        """
        Activity A occupies ROOM_A (max_tasks=1).
        Activity B occupies ROOM_B (max_tasks=1).
        They share no zone so can run in parallel.
        """
        a = _act('A', 4.0)
        b = _act('B', 4.0)
        a.zone_ids = ['ROOM_A']
        b.zone_ids = ['ROOM_B']
        fwd = {a: [], b: []}
        locs = [
            _loc_json('ROOM_A', max_tasks=1),
            _loc_json('ROOM_B', max_tasks=1),
        ]
        p = _build_pert(fwd, locs)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        assert_valid_schedule(p)
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        # Both finish — zones are independent so either order is valid
        assert a_st is not None and b_st is not None

    def test_permit_zone_worker_density_enforced(self):
        """
        PERMIT_1 max_workers=2.  Activity A uses 2 workers, B uses 2 workers.
        They share the permit zone so B must wait until A finishes.
        Pool has 4 MECH workers so resources alone don't serialise them —
        the worker-density limit in the permit zone does.
        """
        a = _act('A', 4.0, res_count=2)
        b = _act('B', 4.0, res_count=2)
        a.zone_ids = ['PERMIT_1']
        b.zone_ids = ['PERMIT_1']
        fwd = {a: [], b: []}
        locs = [_loc_json('PERMIT_1', max_tasks=4, max_workers=2, zone_type='permit')]
        # 4 MECH workers available — resources alone allow parallel execution
        p = _build_pert(fwd, locs, mech_count=4)
        result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert result['n_completed'] == 2
        assert_valid_schedule(p)
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        assert a_et <= b_st or b_et <= a_st


# ===========================================================================
# _build_capacity_snapshots and _fits_with_tentative
# ===========================================================================

class TestZoneIdsCapacitySnapshots:

    def test_build_snapshots_multi_zone_activity_decrements_both_zones(self):
        """
        After one activity is 'ongoing' with zone_ids=['Z1','Z2'],
        _build_capacity_snapshots should show reduced remaining in both zones.
        """
        a = _act('A', 4.0)
        a.zone_ids = ['Z1', 'Z2']
        a.required_resources = [{'skill_type': 'MECH', 'crew_count': 1}]
        # Put A in the 'ongoing' set
        fwd = {a: []}
        locs = [_loc_json('Z1', max_tasks=2), _loc_json('Z2', max_tasks=2)]
        p = _build_pert(fwd, locs)
        # Manually mark A as ongoing with times
        a.setActualStartTime(_START)
        p.ongoing = {a}
        p.done    = set()
        p.notReady = set()
        p.ready   = set()

        end_horizon = _START + timedelta(hours=4)
        _, _, loc_tasks_rem, _, _ = p._build_capacity_snapshots(
            _START, end_horizon
        )
        # Both zones should have been decremented
        assert 'Z1' in loc_tasks_rem
        assert 'Z2' in loc_tasks_rem
        h = _START
        assert loc_tasks_rem['Z1'][h] < 2  # decremented
        assert loc_tasks_rem['Z2'][h] < 2  # decremented

    def test_fits_with_tentative_false_when_zone_full(self):
        """
        Candidate activity needs zone Z1 (max_tasks=1).
        One task is already consuming Z1.
        _fits_with_tentative must return False.
        """
        occupant = _act('OCC', 4.0)
        occupant.zone_ids = ['Z1']
        occupant.required_resources = [{'skill_type': 'MECH', 'crew_count': 1}]

        candidate = _act('CAND', 4.0)
        candidate.zone_ids = ['Z1']
        candidate.required_resources = [{'skill_type': 'MECH', 'crew_count': 1}]

        fwd = {occupant: [candidate], candidate: []}
        locs = [_loc_json('Z1', max_tasks=1)]
        p = _build_pert(fwd, locs)
        # Mark occupant as ongoing
        occupant.setActualStartTime(_START)
        p.ongoing = {occupant}
        p.done    = set()
        p.notReady = set()
        p.ready   = {candidate}

        end_horizon = _START + timedelta(hours=4)
        res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid = \
            p._build_capacity_snapshots(_START, end_horizon)

        fits = p._fits_with_tentative(
            candidate, _START,
            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid=grid,
        )
        assert fits is False

    def test_fits_with_tentative_true_when_different_zones(self):
        """
        Occupant is in Z2, candidate needs Z1 only.
        They don't share a zone — candidate should be feasible.
        No resource requirements on either activity so the resource check
        is a no-op and the zone check is the sole decision gate.
        """
        occupant = _act('OCC', 4.0)
        occupant.zone_ids = ['Z2']
        # no required_resources — not testing resource constraints here

        candidate = _act('CAND', 4.0)
        candidate.zone_ids = ['Z1']
        # no required_resources — testing zone isolation only

        fwd = {occupant: [], candidate: []}
        locs = [_loc_json('Z1', max_tasks=1), _loc_json('Z2', max_tasks=1)]
        p = _build_pert(fwd, locs)
        occupant.setActualStartTime(_START)
        p.ongoing = {occupant}
        p.done    = set()
        p.notReady = set()
        p.ready   = {candidate}

        end_horizon = _START + timedelta(hours=4)
        res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid = \
            p._build_capacity_snapshots(_START, end_horizon)

        fits = p._fits_with_tentative(
            candidate, _START,
            res_rem, eq_rem, loc_tasks_rem, loc_workers_rem, grid=grid,
        )
        assert fits is True


# ===========================================================================
# Schema validation
# ===========================================================================

class TestZoneIdsSchema:

    def _load_schema(self):
        import os
        schema_path = os.path.join(
            os.path.dirname(__file__), '..', 'outage_schema.json'
        )
        with open(schema_path) as f:
            return json.load(f)

    def test_zone_ids_field_in_task_properties(self):
        schema = self._load_schema()
        task_props = schema['properties']['tasks']['items']['properties']
        assert 'zone_ids' in task_props

    def test_zone_ids_is_array_of_strings(self):
        schema = self._load_schema()
        task_props = schema['properties']['tasks']['items']['properties']
        z = task_props['zone_ids']
        assert z['type'] == 'array'
        assert z['items']['type'] == 'string'

    def test_zone_type_field_in_location_properties(self):
        schema = self._load_schema()
        loc_props = schema['properties']['locations']['items']['properties']
        assert 'zone_type' in loc_props

    def test_zone_type_enum_values(self):
        schema = self._load_schema()
        loc_props = schema['properties']['locations']['items']['properties']
        z = loc_props['zone_type']
        assert set(z['enum']) == {'physical', 'permit'}
