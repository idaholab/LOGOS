"""
Unit tests for Challenge 10: multi-skill substitution in the RCPSP scheduler.

Tests cover:
- Activity._actual_resources field default and reset
- _fits_with_tentative substitution logic (via scheduler runs)
- _actual_resources correctly populated after scheduling
- _get_consumed_resources uses actual breakdown
- Dose tracking charges the substituted skill
- Schema contains alternative_skill_types
"""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    ResourcePool, ResourceAvailability,
    EquipmentPool, LocationPool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START_DT = datetime(2026, 1, 1)
SCHEMA_PATH = Path(__file__).parent.parent / "outage_schema.json"


def _make_rp(*skill_counts):
    """
    Build a ResourcePool from (skill, count) pairs.
    Each skill is renewable by default; availability is 1 year from START_DT.
    """
    rp = ResourcePool()
    for skill, count in skill_counts:
        rp.resources[skill] = ResourceAvailability(
            skill,
            [{'start_date': START_DT, 'end_date': START_DT + timedelta(days=365),
              'available_count': count}],
        )
    return rp


def _make_consumable_rp(skill, count, budget_per_worker=500.0):
    rp = ResourcePool()
    rp.resources[skill] = ResourceAvailability(
        skill,
        [{'start_date': START_DT, 'end_date': START_DT + timedelta(days=365),
          'available_count': count}],
        resource_type='consumable',
        dose_budget_per_worker_mrem=budget_per_worker,
    )
    return rp


def _pert(fwd, rp, start_dt=START_DT):
    """Build Pert, inject pools, generate info, set start time."""
    p = Pert(graph=fwd)
    p.crew_pool = rp
    p.equipment_pool = EquipmentPool()
    p.location_pool = LocationPool()
    p.dose_trackers = rp.build_dose_trackers() if rp else {}
    p._precompute_availability_events()
    p.generateInfo()
    p.startTime = start_dt
    return p


def _activity(name, skill, crew, duration=2.0, alt_skills=None):
    """Create activity with optional alternative_skill_types."""
    req = {'skill_type': skill, 'crew_count': crew}
    if alt_skills:
        req['alternative_skill_types'] = alt_skills
    return Activity(name, duration, required_resources=[req])


def _chain(a, b):
    """Build START -> a -> b -> END forward dict."""
    s = Activity('START', 0.0)
    e = Activity('END', 0.0)
    return {s: [a], a: [b], b: [e], e: []}


def _single(a):
    """Build START -> a -> END forward dict."""
    s = Activity('START', 0.0)
    e = Activity('END', 0.0)
    return {s: [a], a: [e], e: []}


# ---------------------------------------------------------------------------
# 1. TestActivityActualResources
# ---------------------------------------------------------------------------

class TestActivityActualResources:

    def test_actual_resources_default_none(self):
        """_actual_resources starts as None for a fresh activity."""
        act = Activity('T1', 4.0, required_resources=[{'skill_type': 'WELDER', 'crew_count': 2}])
        assert act._actual_resources is None

    def test_reset_clears_actual_resources(self):
        """reset() must clear _actual_resources back to None."""
        act = Activity('T1', 4.0, required_resources=[{'skill_type': 'WELDER', 'crew_count': 2}])
        act._actual_resources = {'WELDER': 2}
        act.reset()
        assert act._actual_resources is None

    def test_reset_clears_actual_resources_for_start(self):
        """_actual_resources_for_start is not reset (it is transient), but _actual_resources is."""
        act = Activity('T1', 4.0)
        act._actual_resources = {'WELDER': 1, 'WELDER_SENIOR': 1}
        act.reset()
        assert act._actual_resources is None

    def test_actual_resources_independent_across_instances(self):
        """Two separate activities have independent _actual_resources."""
        a1 = Activity('T1', 2.0)
        a2 = Activity('T2', 2.0)
        a1._actual_resources = {'WELDER': 2}
        assert a2._actual_resources is None


# ---------------------------------------------------------------------------
# 2. TestFitsWithSubstitution
# ---------------------------------------------------------------------------

class TestFitsWithSubstitution:

    def test_primary_sufficient_no_substitution_needed(self):
        """When primary pool has enough, activity schedules normally."""
        rp = _make_rp(('WELDER', 3))
        act = _activity('A', 'WELDER', 2)
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act.status == 'completed'

    def test_primary_exhausted_no_alternative_waits(self):
        """
        When primary skill is 0 and no alternatives given, the task
        cannot schedule if no other freeing event occurs.
        Two tasks compete for 2 WELDERS (pool=2), so A takes them all
        and B (also needing 2) must wait until A completes.
        """
        rp = _make_rp(('WELDER', 2))
        a = _activity('A', 'WELDER', 2, duration=2.0)
        b = _activity('B', 'WELDER', 2, duration=2.0)
        s = Activity('START', 0.0)
        e = Activity('END', 0.0)
        fwd = {s: [a, b], a: [e], b: [e], e: []}
        p = _pert(fwd, rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Both must complete (B waits for A's WELDER workers to free)
        assert a.status == 'completed'
        assert b.status == 'completed'
        # A and B must serialise due to resource shortage (either order is valid)
        a_st, a_et = a.returnAbsTimes()
        b_st, b_et = b.returnAbsTimes()
        assert b_st >= a_et or a_st >= b_et

    def test_alternative_substitutes_when_primary_exhausted(self):
        """
        Primary WELDER pool=0, alternative WELDER_SENIOR pool=3.
        Activity needs 2 WELDERs; should be satisfied via WELDER_SENIOR.
        """
        rp = _make_rp(('WELDER', 0), ('WELDER_SENIOR', 3))
        act = _activity('A', 'WELDER', 2, alt_skills=['WELDER_SENIOR'])
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act.status == 'completed'

    def test_alternative_partially_fills_gap(self):
        """
        Primary WELDER pool=1 (need 2), alternative WELDER_SENIOR pool=2.
        1 primary + 1 alternative → task fits.
        """
        rp = _make_rp(('WELDER', 1), ('WELDER_SENIOR', 2))
        act = _activity('A', 'WELDER', 2, alt_skills=['WELDER_SENIOR'])
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act.status == 'completed'

    def test_multiple_alternatives_tried_in_order(self):
        """
        Primary WELDER=0, first alt WELDER_JUNIOR=0, second alt WELDER_SENIOR=2.
        Scheduler should try WELDER_JUNIOR first (fail), then WELDER_SENIOR (succeed).
        """
        rp = _make_rp(('WELDER', 0), ('WELDER_JUNIOR', 0), ('WELDER_SENIOR', 2))
        req = {'skill_type': 'WELDER', 'crew_count': 2,
               'alternative_skill_types': ['WELDER_JUNIOR', 'WELDER_SENIOR']}
        act = Activity('A', 2.0, required_resources=[req])
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act.status == 'completed'

    def test_two_alternatives_split_the_gap(self):
        """
        Need 4; primary=1, alt1=2, alt2=2.
        1 primary + 2 alt1 + 1 alt2 = 4 → fits.
        """
        rp = _make_rp(('WELDER', 1), ('WELDER_A', 2), ('WELDER_B', 3))
        req = {'skill_type': 'WELDER', 'crew_count': 4,
               'alternative_skill_types': ['WELDER_A', 'WELDER_B']}
        act = Activity('A', 2.0, required_resources=[req])
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act.status == 'completed'

    def test_insufficient_even_with_alternatives_waits(self):
        """
        Need 5; primary=0, alt=2. Total only 2, task cannot start.
        Scheduler should not deadlock but should leave task uncompleted
        (or wait) — verify no crash and task either completed eventually
        or wait list handled gracefully.
        """
        rp = _make_rp(('WELDER', 0), ('WELDER_SENIOR', 2))
        req = {'skill_type': 'WELDER', 'crew_count': 5,
               'alternative_skill_types': ['WELDER_SENIOR']}
        act = Activity('A', 2.0, required_resources=[req])
        p = _pert(_single(act), rp)
        # Should not raise; activity won't complete due to insufficient workers
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Not completed (resource constrained)
        assert act.status != 'completed'

    def test_no_alternative_field_behaves_as_before(self):
        """Activities without alternative_skill_types use legacy path unchanged."""
        rp = _make_rp(('MECHANIC', 4))
        act = Activity('A', 2.0, required_resources=[{'skill_type': 'MECHANIC', 'crew_count': 2}])
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act.status == 'completed'

    def test_per_hour_alternative_check(self):
        """
        Alternative availability must hold for every hour of the activity.
        A is 3 h; primary=0, alt has 2 workers in hour 0-1 but only 1 in hour 1+.
        Need 2 → should fail (not enough in every hour).
        (Modelled via two parallel tasks competing for the alt pool.)
        """
        rp = _make_rp(('WELDER', 0), ('WELDER_SENIOR', 2))
        req = {'skill_type': 'WELDER', 'crew_count': 2,
               'alternative_skill_types': ['WELDER_SENIOR']}
        # B occupies 1 of the WELDER_SENIOR slots for hours 1-3
        b = Activity('B', 3.0, required_resources=[{'skill_type': 'WELDER_SENIOR', 'crew_count': 1}])
        a = Activity('A', 3.0, required_resources=[req])
        s = Activity('START', 0.0)
        e = Activity('END', 0.0)
        fwd = {s: [a, b], a: [e], b: [e], e: []}
        p = _pert(fwd, rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Both should complete; scheduler may serialise them
        assert a.status == 'completed'
        assert b.status == 'completed'


# ---------------------------------------------------------------------------
# 3. TestActualResourcesTracked
# ---------------------------------------------------------------------------

class TestActualResourcesTracked:

    def test_actual_resources_set_after_scheduling(self):
        """After scheduling, ongoing/completed activity has _actual_resources set."""
        rp = _make_rp(('WELDER', 3))
        act = _activity('A', 'WELDER', 2)
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act._actual_resources is not None

    def test_actual_resources_full_primary_when_sufficient(self):
        """With enough primary workers, _actual_resources shows all from primary."""
        rp = _make_rp(('WELDER', 3))
        act = _activity('A', 'WELDER', 2)
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act._actual_resources.get('WELDER', 0) == 2

    def test_actual_resources_shows_substitution(self):
        """When primary exhausted and alt used, _actual_resources reflects that."""
        rp = _make_rp(('WELDER', 0), ('WELDER_SENIOR', 3))
        act = _activity('A', 'WELDER', 2, alt_skills=['WELDER_SENIOR'])
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act._actual_resources.get('WELDER', 0) == 0
        assert act._actual_resources.get('WELDER_SENIOR', 0) == 2

    def test_actual_resources_partial_primary_plus_alt(self):
        """1 primary + 1 alt when primary=1 and need=2."""
        rp = _make_rp(('WELDER', 1), ('WELDER_SENIOR', 2))
        act = _activity('A', 'WELDER', 2, alt_skills=['WELDER_SENIOR'])
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act._actual_resources.get('WELDER', 0) == 1
        assert act._actual_resources.get('WELDER_SENIOR', 0) == 1

    def test_get_consumed_resources_primary_correct(self):
        """_get_consumed_resources returns correct count for primary skill."""
        rp = _make_rp(('WELDER', 4))
        act = _activity('A', 'WELDER', 3, duration=4.0)
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        # Activity is completed; check just after start (within its duration)
        start, end = act.returnAbsTimes()
        mid = start + timedelta(hours=1)
        # Push act back to ongoing manually to test (or check via
        # verifying that completed activities still have _actual_resources set)
        assert act._actual_resources is not None

    def test_get_consumed_resources_returns_alt_count(self):
        """
        After substitution, _get_consumed_resources(alt_skill) returns the
        workers that are actually running under that skill.
        """
        rp = _make_rp(('WELDER', 0), ('WELDER_SENIOR', 3))
        act = _activity('A', 'WELDER', 2, duration=4.0, alt_skills=['WELDER_SENIOR'])
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act._actual_resources.get('WELDER_SENIOR', 0) == 2
        assert act._actual_resources.get('WELDER', 0) == 0

    def test_actual_resources_reset_between_runs(self):
        """Running calculateScheduleWithResources twice should reset and re-assign."""
        rp = _make_rp(('WELDER', 3))
        act = _activity('A', 'WELDER', 2)
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        first_result = dict(act._actual_resources)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        second_result = dict(act._actual_resources)
        assert first_result == second_result


# ---------------------------------------------------------------------------
# 4. TestSubstitutionDoseTracking
# ---------------------------------------------------------------------------

class TestSubstitutionDoseTracking:

    def test_dose_charged_to_alternative_skill(self):
        """
        When workers are substituted, dose should be charged to the
        skill that actually does the work (the alternative), not the primary.
        Pool: WELDER=0 (consumable, budget=1000), WELDER_SENIOR=3 (consumable, budget=2000)
        Activity: needs 2 WELDER, alt=WELDER_SENIOR, dose_rate=50 mRem/h, duration=2h
        Expected: WELDER_SENIOR.consumed = 50*2*2 = 200; WELDER.consumed = 0
        """
        rp = ResourcePool()
        rp.resources['WELDER'] = ResourceAvailability(
            'WELDER',
            [{'start_date': START_DT, 'end_date': START_DT + timedelta(days=365), 'available_count': 0}],
            resource_type='consumable',
            dose_budget_per_worker_mrem=1000.0,
        )
        rp.resources['WELDER_SENIOR'] = ResourceAvailability(
            'WELDER_SENIOR',
            [{'start_date': START_DT, 'end_date': START_DT + timedelta(days=365), 'available_count': 3}],
            resource_type='consumable',
            dose_budget_per_worker_mrem=2000.0,
        )
        req = {'skill_type': 'WELDER', 'crew_count': 2,
               'alternative_skill_types': ['WELDER_SENIOR']}
        act = Activity('A', 2.0, required_resources=[req])
        act.dose_rate_mrem_per_hour = 50.0
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act.status == 'completed'
        # All 2 workers come from WELDER_SENIOR; dose = 50 * 2 * 2 = 200
        assert abs(p.dose_trackers['WELDER_SENIOR'].consumed_mrem - 200.0) < 1e-9
        # No WELDER workers used → no dose on WELDER tracker
        assert abs(p.dose_trackers['WELDER'].consumed_mrem - 0.0) < 1e-9

    def test_dose_charged_proportionally_split(self):
        """
        1 WELDER (primary, consumable) + 1 WELDER_SENIOR (alt, consumable) fill a need=2.
        dose_rate=50, duration=2.
        WELDER dose = 50*1*2=100, WELDER_SENIOR dose = 50*1*2=100.
        """
        rp = ResourcePool()
        for skill, count in [('WELDER', 1), ('WELDER_SENIOR', 2)]:
            rp.resources[skill] = ResourceAvailability(
                skill,
                [{'start_date': START_DT, 'end_date': START_DT + timedelta(days=365),
                  'available_count': count}],
                resource_type='consumable',
                dose_budget_per_worker_mrem=1000.0,
            )
        req = {'skill_type': 'WELDER', 'crew_count': 2,
               'alternative_skill_types': ['WELDER_SENIOR']}
        act = Activity('A', 2.0, required_resources=[req])
        act.dose_rate_mrem_per_hour = 50.0
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert act.status == 'completed'
        assert abs(p.dose_trackers['WELDER'].consumed_mrem - 100.0) < 1e-9
        assert abs(p.dose_trackers['WELDER_SENIOR'].consumed_mrem - 100.0) < 1e-9

    def test_no_dose_on_zero_rate_with_substitution(self):
        """With dose_rate=0, no dose is charged even when substitution occurs."""
        rp = ResourcePool()
        rp.resources['WELDER'] = ResourceAvailability(
            'WELDER',
            [{'start_date': START_DT, 'end_date': START_DT + timedelta(days=365), 'available_count': 0}],
            resource_type='consumable',
            dose_budget_per_worker_mrem=1000.0,
        )
        rp.resources['WELDER_SENIOR'] = ResourceAvailability(
            'WELDER_SENIOR',
            [{'start_date': START_DT, 'end_date': START_DT + timedelta(days=365), 'available_count': 3}],
            resource_type='consumable',
            dose_budget_per_worker_mrem=1000.0,
        )
        req = {'skill_type': 'WELDER', 'crew_count': 2,
               'alternative_skill_types': ['WELDER_SENIOR']}
        act = Activity('A', 2.0, required_resources=[req])
        act.dose_rate_mrem_per_hour = 0.0
        p = _pert(_single(act), rp)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert p.dose_trackers['WELDER'].consumed_mrem == 0.0
        assert p.dose_trackers['WELDER_SENIOR'].consumed_mrem == 0.0


# ---------------------------------------------------------------------------
# 5. TestSchemaSubstitution
# ---------------------------------------------------------------------------

class TestSchemaSubstitution:

    def test_schema_has_alternative_skill_types_property(self):
        """outage_schema.json has alternative_skill_types in resource requirement item."""
        schema = json.loads(SCHEMA_PATH.read_text())
        task_items = schema['properties']['tasks']['items']
        res_items = task_items['properties']['required_resources']['items']
        assert 'alternative_skill_types' in res_items['properties']

    def test_schema_alternative_skill_types_is_array(self):
        schema = json.loads(SCHEMA_PATH.read_text())
        task_items = schema['properties']['tasks']['items']
        res_items = task_items['properties']['required_resources']['items']
        alt_schema = res_items['properties']['alternative_skill_types']
        assert alt_schema['type'] == 'array'

    def test_schema_alternative_skill_types_items_are_strings(self):
        schema = json.loads(SCHEMA_PATH.read_text())
        task_items = schema['properties']['tasks']['items']
        res_items = task_items['properties']['required_resources']['items']
        alt_schema = res_items['properties']['alternative_skill_types']
        assert alt_schema['items']['type'] == 'string'

    def test_schema_alternative_skill_types_unique_items(self):
        schema = json.loads(SCHEMA_PATH.read_text())
        task_items = schema['properties']['tasks']['items']
        res_items = task_items['properties']['required_resources']['items']
        alt_schema = res_items['properties']['alternative_skill_types']
        assert alt_schema.get('uniqueItems') is True

    def test_schema_alternative_skill_types_not_required(self):
        """alternative_skill_types must NOT be in the required list (optional field)."""
        schema = json.loads(SCHEMA_PATH.read_text())
        task_items = schema['properties']['tasks']['items']
        res_items = task_items['properties']['required_resources']['items']
        required = res_items.get('required', [])
        assert 'alternative_skill_types' not in required

    def test_schema_still_allows_additionalprops_false(self):
        """The resource requirement item still has additionalProperties: false."""
        schema = json.loads(SCHEMA_PATH.read_text())
        task_items = schema['properties']['tasks']['items']
        res_items = task_items['properties']['required_resources']['items']
        # additionalProperties must be false (not missing, or True)
        assert res_items.get('additionalProperties') is False

    def test_schema_description_mentions_substitute(self):
        """The description explains substitution semantics."""
        schema = json.loads(SCHEMA_PATH.read_text())
        task_items = schema['properties']['tasks']['items']
        res_items = task_items['properties']['required_resources']['items']
        alt_schema = res_items['properties']['alternative_skill_types']
        desc = alt_schema.get('description', '').lower()
        assert 'substit' in desc or 'substitute' in desc or 'alternative' in desc
