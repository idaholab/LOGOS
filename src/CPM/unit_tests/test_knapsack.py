"""
Unit tests for MDKnapsackScheduler — location-aware greedy selection.

Tests verify that:
- Location dimensions appear in the capacity dict
- Location consumption is included for activities with a location_id
- The greedy solver respects location task-slot limits
- Resource and equipment dimensions are unaffected by the location changes
"""

import pytest
from datetime import datetime

from CPM.activity import Activity
from CPM.pert import MDKnapsackScheduler
from CPM.outage_data import (
    ResourcePool, EquipmentPool, LocationPool,
    ResourceAvailability, EquipmentAvailability, LocationAvailability,
)


# ---------------------------------------------------------------------------
# Minimal pool builders
# Pools use no-arg __init__ + direct dict assignment (see outage_data.py).
# ---------------------------------------------------------------------------

def _make_pools(n_mechanics=4, crane_qty=1, max_tasks=2, max_workers=6):
    """Return (resource_pool, equipment_pool, location_pool) for tests."""
    resource_pool = ResourcePool()
    resource_pool.resources["MECHANIC"] = ResourceAvailability(
        skill_type="MECHANIC",
        periods=[{
            "start_date": datetime(2025, 1, 1),
            "end_date":   datetime(2025, 12, 31),
            "available_count": n_mechanics,
            "reason": "test",
        }],
    )

    equipment_pool = EquipmentPool()
    equipment_pool.equipment["EQ_CRANE"] = EquipmentAvailability(
        equipment_id="EQ_CRANE",
        description="Test crane",
        periods=[{
            "start_date":         datetime(2025, 1, 1),
            "end_date":           datetime(2025, 12, 31),
            "quantity_available": crane_qty,
            "reason": "test",
        }],
    )

    location_pool = LocationPool()
    location_pool.locations["LOC_A"] = LocationAvailability(
        location_id="LOC_A",
        description="Test zone",
        periods=[{
            "start_date":             datetime(2025, 1, 1),
            "end_date":               datetime(2025, 12, 31),
            "max_concurrent_tasks":   max_tasks,
            "max_concurrent_workers": max_workers,
            "reason": "test",
        }],
    )

    return resource_pool, equipment_pool, location_pool


def _make_activity(name, skill="MECHANIC", crew=2, equipment=None, location="LOC_A"):
    res = [{"skill_type": skill, "crew_count": crew}] if skill else []
    eq = [{"equipment_id": equipment, "quantity_needed": 1}] if equipment else []
    return Activity(name, 4.0, required_resources=res,
                    required_equipment=eq, location_id=location)


# ---------------------------------------------------------------------------
# _get_capacities — location dimensions present
# ---------------------------------------------------------------------------

class TestGetCapacities:

    def test_location_task_key_present(self):
        rp, ep, lp = _make_pools(max_tasks=3)
        act = _make_activity("A")
        cands = {act: {"value": 1.0}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        caps = ks._get_capacities()
        assert "LOC_TASKS_LOC_A" in caps

    def test_location_worker_key_present(self):
        rp, ep, lp = _make_pools(max_workers=8)
        act = _make_activity("A")
        cands = {act: {"value": 1.0}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        caps = ks._get_capacities()
        assert "LOC_WORKERS_LOC_A" in caps

    def test_location_capacity_values_correct(self):
        rp, ep, lp = _make_pools(max_tasks=3, max_workers=10)
        act = _make_activity("A")
        cands = {act: {"value": 1.0}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        caps = ks._get_capacities()
        assert caps["LOC_TASKS_LOC_A"] == 3
        assert caps["LOC_WORKERS_LOC_A"] == 10

    def test_resource_dimensions_still_present(self):
        rp, ep, lp = _make_pools(n_mechanics=6)
        act = _make_activity("A")
        cands = {act: {"value": 1.0}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        caps = ks._get_capacities()
        assert "RESOURCE_MECHANIC" in caps
        assert caps["RESOURCE_MECHANIC"] == 6

    def test_equipment_dimensions_still_present(self):
        rp, ep, lp = _make_pools(crane_qty=2)
        act = _make_activity("A", equipment="EQ_CRANE")
        cands = {act: {"value": 1.0}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        caps = ks._get_capacities()
        assert "EQUIPMENT_EQ_CRANE" in caps
        assert caps["EQUIPMENT_EQ_CRANE"] == 2


# ---------------------------------------------------------------------------
# _get_resource_consumption — location consumption present
# ---------------------------------------------------------------------------

class TestGetResourceConsumption:

    def test_location_task_slot_consumed(self):
        rp, ep, lp = _make_pools()
        act = _make_activity("A", crew=2, location="LOC_A")
        cands = {act: {"value": 1.0}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        consumption = ks._get_resource_consumption(act)
        assert "LOC_TASKS_LOC_A" in consumption
        assert consumption["LOC_TASKS_LOC_A"] == 1

    def test_location_worker_slots_consumed(self):
        rp, ep, lp = _make_pools()
        act = _make_activity("A", crew=3, location="LOC_A")
        cands = {act: {"value": 1.0}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        consumption = ks._get_resource_consumption(act)
        assert "LOC_WORKERS_LOC_A" in consumption
        assert consumption["LOC_WORKERS_LOC_A"] == 3

    def test_no_location_no_loc_keys(self):
        rp, ep, lp = _make_pools()
        act = _make_activity("A", location=None)
        cands = {act: {"value": 1.0}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        consumption = ks._get_resource_consumption(act)
        loc_keys = [k for k in consumption if k.startswith("LOC_")]
        assert loc_keys == []


# ---------------------------------------------------------------------------
# solve() — greedy selection respects location task-slot limit
# ---------------------------------------------------------------------------

class TestKnapsackSolve:

    def test_location_task_limit_respected(self):
        """
        Location LOC_A has max_tasks=1.
        Two candidates A and B both need LOC_A.
        Solver must select at most 1.
        """
        rp, ep, lp = _make_pools(n_mechanics=8, max_tasks=1, max_workers=100)
        a = _make_activity("A", crew=2, location="LOC_A")
        b = _make_activity("B", crew=2, location="LOC_A")
        cands = {a: {"value": 1.0}, b: {"value": 0.9}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        selected = ks.solve()
        assert len(selected) <= 1

    def test_resource_limit_respected(self):
        """
        4 mechanics available.  Two tasks each needing 3 mechanics.
        Only 1 can run simultaneously.
        """
        rp, ep, lp = _make_pools(n_mechanics=4, max_tasks=10)
        a = _make_activity("A", crew=3, location="LOC_A")
        b = _make_activity("B", crew=3, location="LOC_A")
        cands = {a: {"value": 1.0}, b: {"value": 0.9}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        selected = ks.solve()
        assert len(selected) <= 1

    def test_non_competing_tasks_both_selected(self):
        """
        6 mechanics, max_tasks=2. Two tasks each needing 2 mechanics.
        Both fit.
        """
        rp, ep, lp = _make_pools(n_mechanics=6, max_tasks=2, max_workers=10)
        a = _make_activity("A", crew=2, location="LOC_A")
        b = _make_activity("B", crew=2, location="LOC_A")
        cands = {a: {"value": 1.0}, b: {"value": 0.9}}
        ks = MDKnapsackScheduler(cands, rp, ep, lp, datetime(2025, 6, 1))
        selected = ks.solve()
        assert len(selected) == 2

    def test_empty_candidates_returns_empty(self):
        rp, ep, lp = _make_pools()
        ks = MDKnapsackScheduler({}, rp, ep, lp, datetime(2025, 6, 1))
        assert ks.solve() == []
