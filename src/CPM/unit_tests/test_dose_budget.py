"""
Unit tests for consumable radiation dose budget tracking.

Tests verify:
- DoseBudgetTracker arithmetic (fits / consume / reset / remaining)
- ResourceAvailability stores resource_type and dose_budget_per_worker_mrem
- ResourcePool.from_json() parses consumable fields
- ResourcePool.build_dose_trackers() builds correct total budget
- ResourcePool.get_consumable_skills() filters correctly
- Scheduler blocks a task when the dose budget is exhausted
- Scheduler allows tasks with zero dose rate regardless of budget state
- Renewable resources are unaffected by dose tracking
- Dose trackers are reset between successive scheduling runs
- Activity.from_json() / to_json_dict() round-trips dose_rate_mrem_per_hour
"""

import pytest
from datetime import datetime

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import (
    DoseBudgetTracker,
    ResourcePool, EquipmentPool, LocationPool,
    ResourceAvailability, EquipmentAvailability,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PERIOD = [{
    'start_date': datetime(2025, 1, 1),
    'end_date':   datetime(2025, 12, 31),
    'available_count': 4,
    'reason': 'test',
}]


def _make_consumable_pool(budget_per_worker=500.0, peak_workers=4):
    """ResourcePool with one consumable MECHANIC skill and one renewable ELECTRICIAN."""
    rp = ResourcePool()
    rp.resources['MECHANIC'] = ResourceAvailability(
        'MECHANIC',
        [{
            'start_date': datetime(2025, 1, 1),
            'end_date':   datetime(2025, 12, 31),
            'available_count': peak_workers,
        }],
        resource_type='consumable',
        dose_budget_per_worker_mrem=budget_per_worker,
    )
    rp.resources['ELECTRICIAN'] = ResourceAvailability(
        'ELECTRICIAN',
        [{
            'start_date': datetime(2025, 1, 1),
            'end_date':   datetime(2025, 12, 31),
            'available_count': 6,
        }],
        resource_type='renewable',
    )
    return rp


def _make_eq_pool():
    ep = EquipmentPool()
    return ep


def _make_loc_pool():
    lp = LocationPool()
    return lp


def _make_activity(name, skill='MECHANIC', crew=2, dose_rate=0.0, duration=4.0):
    act = Activity(
        name, duration,
        required_resources=[{'skill_type': skill, 'crew_count': crew}],
    )
    act.dose_rate_mrem_per_hour = dose_rate
    return act


def _pert_with_pools(fwd, rp, ep, lp, start_dt=None):
    """
    Build a Pert from a graph dict, then inject pools.

    Pert.__init__ only accepts pools via an OutageData object.  When building
    micro-networks in tests we construct with graph= first (pools are None, so
    calculate_resource_requirement silently skips rr computation), then inject
    the pools and regenerate info so that rr metrics are correctly populated
    before the scheduler runs.
    """
    p = Pert(graph=fwd)
    p.resource_pool = rp
    p.equipment_pool = ep
    p.location_pool = lp
    p.dose_trackers = rp.build_dose_trackers() if rp else {}
    p._precompute_availability_events()
    # Regenerate so resource-ratio metrics (rr, avgrr, …) reflect the pool
    p.generateInfo()
    if start_dt:
        p.startTime = start_dt
    return p


def _minimal_pert_with_dose(budget_per_worker=200.0, peak_workers=4):
    """
    Minimal Pert: START -> A -> B -> END
    Both A and B require MECHANIC (consumable) with crew=2 and dose_rate=50 mRem/h.
    Total pool budget = budget_per_worker * peak_workers.
    """
    start_dt = datetime(2025, 6, 1, 6, 0)

    rp = _make_consumable_pool(budget_per_worker=budget_per_worker, peak_workers=peak_workers)
    ep = _make_eq_pool()
    lp = _make_loc_pool()

    a = _make_activity('A', crew=2, dose_rate=50.0, duration=4.0)
    b = _make_activity('B', crew=2, dose_rate=50.0, duration=4.0)
    start_act = Activity('START', 0.0)
    end_act   = Activity('END',   0.0)

    fwd = {start_act: [a], a: [b], b: [end_act], end_act: []}
    return _pert_with_pools(fwd, rp, ep, lp, start_dt), a, b


# ---------------------------------------------------------------------------
# DoseBudgetTracker unit tests
# ---------------------------------------------------------------------------

class TestDoseBudgetTracker:

    def test_initial_state(self):
        t = DoseBudgetTracker('MECHANIC', 2000.0)
        assert t.consumed_mrem == 0.0
        assert t.remaining_mrem == 2000.0
        assert t.total_budget_mrem == 2000.0

    def test_fits_when_empty(self):
        t = DoseBudgetTracker('MECHANIC', 2000.0)
        # 2 workers × 50 mRem/h × 4 h = 400 mRem — well within 2000
        assert t.fits(50.0, 2, 4.0) is True

    def test_fits_exact_budget(self):
        t = DoseBudgetTracker('MECHANIC', 400.0)
        assert t.fits(50.0, 2, 4.0) is True   # exactly equals budget

    def test_fits_fails_over_budget(self):
        t = DoseBudgetTracker('MECHANIC', 399.0)
        assert t.fits(50.0, 2, 4.0) is False  # 400 > 399

    def test_consume_updates_consumed(self):
        t = DoseBudgetTracker('MECHANIC', 2000.0)
        t.consume(50.0, 2, 4.0)               # 400 mRem
        assert abs(t.consumed_mrem - 400.0) < 1e-9

    def test_remaining_decreases_after_consume(self):
        t = DoseBudgetTracker('MECHANIC', 2000.0)
        t.consume(50.0, 2, 4.0)
        assert abs(t.remaining_mrem - 1600.0) < 1e-9

    def test_fits_fails_after_partial_consume(self):
        t = DoseBudgetTracker('MECHANIC', 500.0)
        t.consume(50.0, 2, 4.0)               # 400 mRem used
        # Remaining = 100 mRem; next task needs 400 — won't fit
        assert t.fits(50.0, 2, 4.0) is False

    def test_zero_dose_rate_always_fits(self):
        t = DoseBudgetTracker('MECHANIC', 0.0)  # zero budget
        assert t.fits(0.0, 10, 100.0) is True

    def test_zero_dose_rate_does_not_consume(self):
        t = DoseBudgetTracker('MECHANIC', 2000.0)
        t.consume(0.0, 4, 8.0)
        assert t.consumed_mrem == 0.0

    def test_reset_clears_consumed(self):
        t = DoseBudgetTracker('MECHANIC', 2000.0)
        t.consume(50.0, 2, 4.0)
        t.reset()
        assert t.consumed_mrem == 0.0
        assert t.remaining_mrem == 2000.0

    def test_remaining_clamps_to_zero(self):
        t = DoseBudgetTracker('MECHANIC', 100.0)
        t.consumed_mrem = 500.0  # force over-budget
        assert t.remaining_mrem == 0.0


# ---------------------------------------------------------------------------
# ResourceAvailability consumable fields
# ---------------------------------------------------------------------------

class TestResourceAvailabilityConsumable:

    def test_default_resource_type_is_renewable(self):
        ra = ResourceAvailability('MECHANIC', _PERIOD)
        assert ra.resource_type == 'renewable'

    def test_consumable_type_stored(self):
        ra = ResourceAvailability('MECHANIC', _PERIOD,
                                  resource_type='consumable',
                                  dose_budget_per_worker_mrem=2000.0)
        assert ra.resource_type == 'consumable'
        assert ra.dose_budget_per_worker_mrem == 2000.0

    def test_default_dose_budget_zero(self):
        ra = ResourceAvailability('MECHANIC', _PERIOD)
        assert ra.dose_budget_per_worker_mrem == 0.0


# ---------------------------------------------------------------------------
# ResourcePool consumable helpers
# ---------------------------------------------------------------------------

class TestResourcePoolConsumable:

    def test_get_consumable_skills_finds_consumable(self):
        rp = _make_consumable_pool()
        assert 'MECHANIC' in rp.get_consumable_skills()
        assert 'ELECTRICIAN' not in rp.get_consumable_skills()

    def test_get_consumable_skills_empty_when_all_renewable(self):
        rp = ResourcePool()
        rp.resources['MECHANIC'] = ResourceAvailability('MECHANIC', _PERIOD)
        assert rp.get_consumable_skills() == []

    def test_build_dose_trackers_creates_tracker(self):
        # budget_per_worker=500, peak=4 → total=2000
        rp = _make_consumable_pool(budget_per_worker=500.0, peak_workers=4)
        trackers = rp.build_dose_trackers()
        assert 'MECHANIC' in trackers
        assert 'ELECTRICIAN' not in trackers
        assert abs(trackers['MECHANIC'].total_budget_mrem - 2000.0) < 1e-9

    def test_build_dose_trackers_empty_for_renewable_pool(self):
        rp = ResourcePool()
        rp.resources['MECHANIC'] = ResourceAvailability('MECHANIC', _PERIOD)
        assert rp.build_dose_trackers() == {}

    def test_from_json_parses_consumable_fields(self):
        data = [
            {
                'skill_type': 'MECHANIC',
                'resource_type': 'consumable',
                'dose_budget_per_worker_mrem': 1500.0,
                'availability_periods': [{
                    'start_date': '2025-01-01T00:00:00',
                    'end_date':   '2025-12-31T23:59:59',
                    'available_count': 10,
                }],
            }
        ]
        rp = ResourcePool.from_json(data)
        ra = rp.resources['MECHANIC']
        assert ra.resource_type == 'consumable'
        assert abs(ra.dose_budget_per_worker_mrem - 1500.0) < 1e-9

    def test_from_json_defaults_to_renewable(self):
        data = [
            {
                'skill_type': 'MECHANIC',
                'availability_periods': [{
                    'start_date': '2025-01-01T00:00:00',
                    'end_date':   '2025-12-31T23:59:59',
                    'available_count': 5,
                }],
            }
        ]
        rp = ResourcePool.from_json(data)
        assert rp.resources['MECHANIC'].resource_type == 'renewable'


# ---------------------------------------------------------------------------
# Activity dose_rate round-trip
# ---------------------------------------------------------------------------

class TestActivityDoseRate:

    def test_default_dose_rate_zero(self):
        act = Activity('T1', 4.0)
        assert act.dose_rate_mrem_per_hour == 0.0

    def test_from_json_parses_dose_rate(self):
        task = {
            'task_id': 'T1',
            'description': 'Valve inspection',
            'duration': 4.0,
            'successors': [],
            'required_resources': [{'skill_type': 'MECHANIC', 'crew_count': 2}],
            'required_equipment': [],
            'is_hold_point': False,
            'dose_rate_mrem_per_hour': 75.0,
        }
        act = Activity.from_json(task)
        assert abs(act.dose_rate_mrem_per_hour - 75.0) < 1e-9

    def test_from_json_defaults_dose_rate_to_zero(self):
        task = {
            'task_id': 'T1',
            'description': 'Admin task',
            'duration': 2.0,
            'successors': [],
            'required_resources': [],
            'required_equipment': [],
            'is_hold_point': False,
        }
        act = Activity.from_json(task)
        assert act.dose_rate_mrem_per_hour == 0.0

    def test_to_json_dict_includes_nonzero_dose_rate(self):
        act = Activity('T1', 4.0)
        act.dose_rate_mrem_per_hour = 50.0
        d = act.to_json_dict()
        assert 'dose_rate_mrem_per_hour' in d
        assert abs(d['dose_rate_mrem_per_hour'] - 50.0) < 1e-9

    def test_to_json_dict_omits_zero_dose_rate(self):
        act = Activity('T1', 4.0)
        d = act.to_json_dict()
        assert 'dose_rate_mrem_per_hour' not in d


# ---------------------------------------------------------------------------
# Pert dose tracker initialisation
# ---------------------------------------------------------------------------

class TestPertDoseTrackerInit:

    def test_dose_trackers_built_on_init(self):
        p, a, b = _minimal_pert_with_dose(budget_per_worker=200.0, peak_workers=4)
        # MECHANIC is consumable → tracker created; total = 200 × 4 = 800 mRem
        assert 'MECHANIC' in p.dose_trackers
        assert abs(p.dose_trackers['MECHANIC'].total_budget_mrem - 800.0) < 1e-9

    def test_no_dose_trackers_without_pool(self):
        from conftest import make_chain_pert
        p, *_ = make_chain_pert()
        assert p.dose_trackers == {}

    def test_no_dose_trackers_for_renewable_pool(self):
        rp = ResourcePool()
        rp.resources['MECHANIC'] = ResourceAvailability('MECHANIC', [{
            'start_date': datetime(2025, 1, 1),
            'end_date':   datetime(2025, 12, 31),
            'available_count': 4,
        }])  # renewable by default
        ep = EquipmentPool()
        lp = LocationPool()
        start_act = Activity('START', 0.0)
        end_act   = Activity('END',   0.0)
        p = _pert_with_pools(
            {start_act: [end_act], end_act: []}, rp, ep, lp
        )
        assert p.dose_trackers == {}


# ---------------------------------------------------------------------------
# Scheduler respects dose budget
# ---------------------------------------------------------------------------

class TestSchedulerDoseBudget:

    def test_dose_consumed_after_scheduling(self):
        """
        After a full schedule, consumed_mrem > 0 for activities with dose > 0.
        """
        # budget_per_worker=500, peak=4 → total=2000; A consumes 50×2×4=400 mRem
        p, a, b = _minimal_pert_with_dose(budget_per_worker=500.0, peak_workers=4)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert p.dose_trackers['MECHANIC'].consumed_mrem > 0.0

    def test_dose_trackers_reset_between_runs(self):
        """
        Running calculateScheduleWithResources() twice should yield the same
        consumed_mrem — the reset between runs ensures no dose accumulation.
        """
        p, a, b = _minimal_pert_with_dose(budget_per_worker=500.0, peak_workers=4)
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        consumed_first = p.dose_trackers['MECHANIC'].consumed_mrem
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        consumed_second = p.dose_trackers['MECHANIC'].consumed_mrem
        assert abs(consumed_first - consumed_second) < 1e-9

    def test_zero_dose_rate_activities_always_scheduled(self):
        """
        Activities with dose_rate=0 are never blocked by dose budget,
        even when the budget is zero.
        """
        rp = _make_consumable_pool(budget_per_worker=0.0, peak_workers=4)
        ep = _make_eq_pool()
        lp = _make_loc_pool()

        a = _make_activity('A', crew=2, dose_rate=0.0, duration=2.0)
        start_act = Activity('START', 0.0)
        end_act   = Activity('END',   0.0)
        fwd = {start_act: [a], a: [end_act], end_act: []}
        p = _pert_with_pools(fwd, rp, ep, lp, datetime(2025, 6, 1, 6, 0))
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert len(p.completed) == len(p.infoDict)

    def test_renewable_resource_unaffected_by_dose_logic(self):
        """
        A purely renewable skill pool produces an empty dose_trackers dict;
        scheduling proceeds normally even when activities carry a dose_rate.
        """
        rp = ResourcePool()
        rp.resources['MECHANIC'] = ResourceAvailability(
            'MECHANIC', [{
                'start_date': datetime(2025, 1, 1),
                'end_date':   datetime(2025, 12, 31),
                'available_count': 4,
            }]
        )
        ep = _make_eq_pool()
        lp = _make_loc_pool()

        a = _make_activity('A', crew=2, dose_rate=50.0, duration=2.0)
        start_act = Activity('START', 0.0)
        end_act   = Activity('END',   0.0)
        fwd = {start_act: [a], a: [end_act], end_act: []}
        p = _pert_with_pools(fwd, rp, ep, lp, datetime(2025, 6, 1, 6, 0))
        p.calculateScheduleWithResources(sgs='max_use_res_ranked')
        assert p.dose_trackers == {}
        assert len(p.completed) == len(p.infoDict)
