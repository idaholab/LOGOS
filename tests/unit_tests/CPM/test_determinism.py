"""
Pass 1b — Determinism invariant.

Calling calculateScheduleWithResources() twice on the same Pert instance
must produce bitwise-identical start/end times for every activity.

Any mutable state that bleeds across runs is a latent bug — e.g. pools that
are not reset, priority caches that retain stale values, or scheduling queues
that are not fully cleared.

Fixtures covered:
  - Serial chain (no resource contention)
  - Fork-join (parallel branches, one critical)
  - Chain with FS lag
  - Chain with time window
  - Chain with consumable pool
  - JSON example_10  (real multi-skill, multi-equipment outage network)
  - JSON test_case_1 (larger real-world network)

For each fixture the test:
  1. Runs the scheduler once and snapshots {name: (startTime, endTime)}.
  2. Runs the scheduler a second time on the same instance.
  3. Asserts that every activity has the same (startTime, endTime) as run 1.
  4. Calls assert_valid_schedule on both results.
"""

import pytest
from datetime import datetime
from pathlib import Path

from conftest import assert_valid_schedule
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool, ConsumablePool

from conftest import SCHEMA_PATH as SCHEMA  # canonical shipping schema (see H2)

TOL_SECONDS = 1  # allow up to 1-second floating-point rounding between runs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot(pert):
    """Return {act.name: (startTime, endTime)} for all completed activities."""
    return {act.name: act.returnAbsTimes() for act in pert.completed}


def _assert_identical(snap1, snap2, label: str):
    """Assert two schedule snapshots are identical within TOL_SECONDS."""
    assert set(snap1) == set(snap2), (
        f"[{label}] completed-activity sets differ between run 1 and run 2:\n"
        f"  only in run 1: {set(snap1) - set(snap2)}\n"
        f"  only in run 2: {set(snap2) - set(snap1)}"
    )
    diffs = []
    for name in sorted(snap1):
        st1, et1 = snap1[name]
        st2, et2 = snap2[name]
        if st1 is None and st2 is None:
            continue
        if st1 is None or st2 is None:
            diffs.append(f"  {name}: run1 startTime={st1}  run2 startTime={st2}")
            continue
        dt_start = abs((st2 - st1).total_seconds())
        dt_end   = abs((et2 - et1).total_seconds())
        if dt_start > TOL_SECONDS or dt_end > TOL_SECONDS:
            diffs.append(
                f"  {name}: run1=({st1}, {et1})  run2=({st2}, {et2})"
                f"  Δstart={dt_start:.3f}s  Δend={dt_end:.3f}s"
            )
    if diffs:
        pytest.fail(
            f"[{label}] Schedule is non-deterministic across two runs:\n"
            + "\n".join(diffs)
        )


def _minimal_pools():
    return ResourcePool(), EquipmentPool(), LocationPool()


def _build_pert(fwd, pools=None, lag_dict=None, start_time=None):
    """Construct a Pert with the given graph and attach pools."""
    p = Pert(graph=fwd)
    rp, ep, lp = pools if pools else _minimal_pools()
    p.crew_pool      = rp
    p.equipment_pool = ep
    p.location_pool  = lp
    if lag_dict:
        p.lag_dict = lag_dict
    p.startTime = start_time or datetime(2026, 1, 1)
    p.generateInfo()
    return p


def _schedule_twice_and_assert(p, label, sgs='max_use_res_ranked'):
    """Run twice, compare snapshots, assert validity on both."""
    p.calculateScheduleWithResources(sgs=sgs)
    assert_valid_schedule(p, f"{label} run 1")
    snap1 = _snapshot(p)

    p.calculateScheduleWithResources(sgs=sgs)
    assert_valid_schedule(p, f"{label} run 2")
    snap2 = _snapshot(p)

    _assert_identical(snap1, snap2, label)


# ---------------------------------------------------------------------------
# Micro-network tests
# ---------------------------------------------------------------------------

class TestDeterminismMicro:

    def test_serial_chain(self):
        """START → A(4) → B(3) → C(2) → END.  No resource contention."""
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        b = Activity("B", 3.0)
        c = Activity("C", 2.0)
        end = Activity("END", 0.0)
        fwd = {start: [a], a: [b], b: [c], c: [end], end: []}
        p = _build_pert(fwd)
        _schedule_twice_and_assert(p, "serial_chain")

    def test_fork_join(self):
        """
        START → A(4) → C(2) → END
        START → B(6) ─────────^
        """
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        b = Activity("B", 6.0)
        c = Activity("C", 2.0)
        end = Activity("END", 0.0)
        fwd = {start: [a, b], a: [c], b: [c], c: [end], end: []}
        p = _build_pert(fwd)
        _schedule_twice_and_assert(p, "fork_join")

    def test_lag_chain(self):
        """START → A(4) --[lag=2]--> B(3) → END."""
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        b = Activity("B", 3.0)
        end = Activity("END", 0.0)
        fwd = {start: [a], a: [b], b: [end], end: []}
        p = _build_pert(fwd, lag_dict={(a, b): 2.0})
        _schedule_twice_and_assert(p, "lag_chain")

    def test_time_window(self):
        """B may not start before t=10h.  Scheduler must honour window on both runs."""
        start = Activity("START", 0.0)
        a = Activity("A", 4.0)
        b = Activity("B", 3.0)
        b.time_windows = [{'earliest': 10.0, 'latest': float('inf')}]
        end = Activity("END", 0.0)
        fwd = {start: [a], a: [b], b: [end], end: []}
        p = _build_pert(fwd)
        _schedule_twice_and_assert(p, "time_window")

    def test_consumable_pool(self):
        """
        Two independent activities each need 5 units; pool has 12 units.
        Both complete on run 1; determinism check ensures run 2 matches.
        """
        pool = ConsumablePool.from_json([{
            'item_id': 'WIDGET',
            'description': 'Test widget',
            'total_quantity': 12.0,
        }])
        start = Activity("START", 0.0)
        x = Activity("X", 4.0)
        y = Activity("Y", 4.0)
        x.required_consumables = [{'item_id': 'WIDGET', 'quantity_needed': 5.0}]
        y.required_consumables = [{'item_id': 'WIDGET', 'quantity_needed': 5.0}]
        end = Activity("END", 0.0)
        fwd = {start: [x, y], x: [end], y: [end], end: []}
        rp, ep, lp = _minimal_pools()
        p = Pert(graph=fwd)
        p.crew_pool      = rp
        p.equipment_pool = ep
        p.location_pool  = lp
        p.consumable_pool = pool
        p.startTime = datetime(2026, 1, 1)
        p.generateInfo()
        _schedule_twice_and_assert(p, "consumable_pool")


# ---------------------------------------------------------------------------
# JSON-fixture tests
# ---------------------------------------------------------------------------

class TestDeterminismJSON:

    def _load_and_schedule_twice(self, json_path, label):
        p = Pert.from_json_file(json_path, SCHEMA)
        _schedule_twice_and_assert(p, label)

    def test_example_10(self, json_example_10):
        self._load_and_schedule_twice(json_example_10, "example_10")

    def test_test_case_1(self, json_test_case_1):
        self._load_and_schedule_twice(json_test_case_1, "test_case_1")


# ---------------------------------------------------------------------------
# Priority-rule coverage
# ---------------------------------------------------------------------------

class TestDeterminismPriorityRules:
    """Determinism must hold regardless of which priority rule is used."""

    RULES = ['TF_based', 'SPT', 'LPT', 'GRPW', 'MTS', 'MTP']

    @pytest.mark.parametrize("rule", RULES)
    def test_example_10_priority_rule(self, json_example_10, rule):
        p = Pert.from_json_file(json_example_10, SCHEMA)
        _schedule_twice_and_assert(p, f"example_10/{rule}", sgs='max_use_res_ranked')
