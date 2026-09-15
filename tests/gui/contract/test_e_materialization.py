"""Contract group E — Materialization & preparation (pure slice).

``prepare_run`` (persisting snapshots + stamping provenance into a RunRequest) needs the
SnapshotStore port and lives in the application layer (Step 6), so its two tests are
deferred here rather than stub-skipped. The five pure tests below pin materialize's
behavior and signature and validate_run_config's mode check:
  * scenario=None mirrors the baseline exactly (distinct effective-plan hash kind);
  * a base_plan_hash mismatch is flagged PROV_HASH_MISMATCH;
  * valid-alone-but-invalid-combined surfaces MATERIALIZE_CONFLICT;
  * materialize takes only (reference_plan, scenario) — mode validity is not its job;
  * validate_run_config catches a mode selection for a task absent from the plan.
"""

from __future__ import annotations

import copy
import inspect
import json

import pytest

from prismGui.domain import serialization as ser
from prismGui.domain.issues import IssueCode, Severity
from prismGui.domain.materialize import materialize, validate_run_config
from prismGui.domain.plan import Dependency, ResourceReq, Task
from prismGui.domain.scenario import (
    DependencySuppression, EquipmentChange, LocationChange, ResourceChange, Scenario,
    TaskSuppression,
)
from prismGui.domain.versions import SCHEMA_VERSION


class TestMaterialization:

    def test_materialize_with_none_scenario_mirrors_baseline(self, baseline):
        """materialize(baseline, None) -> ok, an effective plan whose content mirrors the
        baseline; the effective hash is derived under the distinct ``effective_plan``
        envelope kind, so it never collides with the reference hash of identical bytes."""
        outcome = materialize(baseline, None)
        assert outcome.ok
        assert outcome.issues == ()
        effective = outcome.effective_plan
        assert effective is not None
        assert effective.base_plan_id == baseline.plan_id
        assert effective.base_plan_hash == baseline.plan_hash
        assert effective.content == baseline.content          # exact mirror
        assert effective.effective_plan_hash != baseline.plan_hash

    def test_base_hash_mismatch_is_flagged(self, baseline, scenario_with_stale_hash):
        """A scenario whose base_plan_hash != baseline.plan_hash -> PROV_HASH_MISMATCH
        and no effective plan."""
        outcome = materialize(baseline, scenario_with_stale_hash)
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.PROV_HASH_MISMATCH and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_valid_alone_invalid_combined_is_caught(self, baseline, scenario_emergent_bad_ref):
        """Baseline valid; scenario valid alone; but the emergent task references a skill
        absent from the baseline -> MATERIALIZE_CONFLICT (caught only on combination)."""
        outcome = materialize(baseline, scenario_emergent_bad_ref)
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT for i in outcome.issues)

    def test_materialize_does_not_receive_run_config(self):
        """Signature contract: materialize takes exactly (reference_plan, scenario).
        Mode validity is validate_run_config's job, not materialize's."""
        params = list(inspect.signature(materialize).parameters)
        assert params == ["reference_plan", "scenario"]

    def test_validate_run_config_catches_mode_for_missing_task(self, effective_plan, run_config_bad_mode):
        """A mode_selection naming a task absent from the effective plan -> INVALID_MODE."""
        issues = validate_run_config(effective_plan, run_config_bad_mode)
        assert any(i.code is IssueCode.INVALID_MODE and i.severity is Severity.ERROR
                   for i in issues)


def _res_scenario(baseline, *changes: ResourceChange) -> Scenario:
    """A Scenario carrying only resource_changes, bound to `baseline`."""
    return Scenario(
        scenario_id="scn-res", base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash, resource_changes=tuple(changes))


def _periods(effective, skill: str = "MECH") -> list[dict]:
    """The (canonicalized) availability periods of `skill`'s pool in the effective payload."""
    payload = json.loads(effective.raw_snapshot)["payload"]
    pool = next(r for r in payload["resources"] if r["skill_type"] == skill)
    return pool["availability_periods"]


class TestMaterializeResourceChanges:
    """The Phase-2 resource-availability applier folded into materialize (Increment 8). The
    `baseline` fixture ships one MECH pool with a single period spanning hours 0..96
    (2025-01-01 → 2025-01-05) at available_count 3, so a change's ``from_hour`` in [0, 96)
    is in range. Clip-and-split rewrites only the named pool; the three error paths trip the
    ERROR gate (ok=False, no effective plan)."""

    def test_interior_split_produces_two_periods(self, baseline):
        """A change at an interior hour splits the one period in two: the segment before
        ``from_hour`` keeps the old count, the segment at/after it takes the new count. No
        zero-length period is emitted (start_date < end_date holds)."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", 48.0, 1)))
        assert outcome.ok and outcome.effective_plan is not None
        periods = _periods(outcome.effective_plan)
        assert len(periods) == 2
        assert [p["available_count"] for p in periods] == [3, 1]
        assert all(p["start_date"] < p["end_date"] for p in periods)

    def test_whole_period_lowering_keeps_one_period(self, baseline):
        """A change at ``from_hour <= start`` rewrites the whole period in place — the count
        drops with no split (one period remains)."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", 0.0, 1)))
        assert outcome.ok and outcome.effective_plan is not None
        periods = _periods(outcome.effective_plan)
        assert len(periods) == 1
        assert periods[0]["available_count"] == 1

    def test_temporary_outage_as_two_changes(self, baseline):
        """A temporary outage is authored as two changes — drop to 0 at hour 24, restore to 3
        at hour 72 — yielding three contiguous periods with counts [3, 0, 3]."""
        outcome = materialize(baseline, _res_scenario(
            baseline, ResourceChange("MECH", 24.0, 0), ResourceChange("MECH", 72.0, 3)))
        assert outcome.ok and outcome.effective_plan is not None
        periods = _periods(outcome.effective_plan)
        assert [p["available_count"] for p in periods] == [3, 0, 3]
        # contiguous / non-overlapping: each period's end abuts the next period's start
        assert all(periods[i]["end_date"] == periods[i + 1]["start_date"]
                   for i in range(len(periods) - 1))

    def test_ghost_skill_is_blocking_conflict(self, baseline):
        """A change targeting a skill absent from the baseline resources ->
        MATERIALIZE_CONFLICT + ok=False, no effective plan."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("GHOST", 24.0, 0)))
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_duplicate_hour_for_same_skill_is_rejected(self, baseline):
        """Two changes sharing a (skill_type, from_hour) is the scenario contract's
        invalid case -> INVALID_AVAILABILITY_INTERVAL + ok=False."""
        outcome = materialize(baseline, _res_scenario(
            baseline, ResourceChange("MECH", 24.0, 1), ResourceChange("MECH", 24.0, 2)))
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.INVALID_AVAILABILITY_INTERVAL and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_out_of_range_from_hour_is_rejected(self, baseline):
        """A ``from_hour`` at or beyond the pool's last period end (here 96) would change
        nothing, so it is rejected up front -> INVALID_AVAILABILITY_INTERVAL."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", 200.0, 1)))
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.INVALID_AVAILABILITY_INTERVAL
                   for i in outcome.issues)

    def test_negative_from_hour_is_rejected(self, baseline):
        """A negative ``from_hour`` is out of range -> INVALID_AVAILABILITY_INTERVAL."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", -1.0, 1)))
        assert not outcome.ok
        assert outcome.effective_plan is None
        assert any(i.code is IssueCode.INVALID_AVAILABILITY_INTERVAL
                   for i in outcome.issues)

    def test_effective_payload_revalidates_after_rewrite(self, baseline, validator_adapter):
        """The rewritten effective payload is a schema-valid plan: fed back through the real
        ValidationPort, the split periods raise no ERROR (valid ISO dates, start < end)."""
        outcome = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", 48.0, 1)))
        assert outcome.ok and outcome.effective_plan is not None
        effective_payload = json.loads(outcome.effective_plan.raw_snapshot)["payload"]
        issues = validator_adapter.validate_plan(effective_payload)
        assert not any(i.severity is Severity.ERROR for i in issues), \
            [(i.code.name, i.message) for i in issues if i.severity is Severity.ERROR]


class TestMaterializeBoundedWindow:
    """Increment-A bounded ``[from, to)`` windows on the shared clip-and-split (skills exercise
    the shared helper). The `baseline` ships one MECH period 0..96 @ available_count 3."""

    def test_bounded_window_three_way_split(self, baseline):
        """A change with both bounds inside the period splits it three ways —
        ``[0,from)``@old, ``[from,to)``@new, ``[to,96)``@old — and the pieces stay contiguous."""
        outcome = materialize(baseline, _res_scenario(
            baseline, ResourceChange("MECH", 24.0, 1, to_hour=72.0)))
        assert outcome.ok and outcome.effective_plan is not None
        periods = _periods(outcome.effective_plan)
        assert [p["available_count"] for p in periods] == [3, 1, 3]
        assert all(periods[i]["end_date"] == periods[i + 1]["start_date"]
                   for i in range(len(periods) - 1))

    def test_open_ended_equals_bounded_at_period_end(self, baseline):
        """``to_hour=None`` (open-ended) reproduces the old single-boundary behavior exactly, and
        a bounded window whose ``to`` sits at the period end trims nothing on the right — the two
        are byte-identical (same effective-plan hash). This is the `to_hour` back-compat proof."""
        open_ended = materialize(baseline, _res_scenario(baseline, ResourceChange("MECH", 48.0, 1)))
        to_end = materialize(baseline, _res_scenario(
            baseline, ResourceChange("MECH", 48.0, 1, to_hour=96.0)))
        assert open_ended.ok and to_end.ok
        assert [p["available_count"] for p in _periods(open_ended.effective_plan)] == [3, 1]
        assert (open_ended.effective_plan.effective_plan_hash
                == to_end.effective_plan.effective_plan_hash)

    def test_empty_window_is_rejected(self, baseline):
        """A ``to_hour <= from_hour`` is an empty window that would change nothing ->
        INVALID_AVAILABILITY_INTERVAL + ok=False."""
        outcome = materialize(baseline, _res_scenario(
            baseline, ResourceChange("MECH", 48.0, 1, to_hour=48.0)))
        assert not outcome.ok and outcome.effective_plan is None
        assert any(i.code is IssueCode.INVALID_AVAILABILITY_INTERVAL for i in outcome.issues)


# --- equipment / location appliers need a baseline carrying those entities -------------------

@pytest.fixture
def baseline_rich(raw_plan):
    """`raw_plan` extended with one equipment item (CRANE, quantity_available 2 over 0..96) and
    one location (BAY1, max_concurrent_tasks 2 / max_concurrent_workers 4 over 0..96), so the
    equipment and location appliers have entities to rewrite. Same outage window (2025-01-01..05)."""
    plan = copy.deepcopy(raw_plan)
    plan["equipment"] = [{
        "equipment_id": "CRANE", "description": "mobile crane",
        "availability_periods": [
            {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
             "quantity_available": 2, "reason": "base"}]}]
    plan["locations"] = [{
        "location_id": "BAY1", "description": "work bay",
        "availability_periods": [
            {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
             "max_concurrent_tasks": 2, "max_concurrent_workers": 4, "reason": "base"}]}]
    return ser.build_reference_plan("baseline-rich", plan, schema_version=SCHEMA_VERSION)


def _eqp_scenario(baseline, *changes: EquipmentChange) -> Scenario:
    return Scenario(scenario_id="scn-eqp", base_plan_id=baseline.plan_id,
                    base_plan_hash=baseline.plan_hash, equipment_changes=tuple(changes))


def _loc_scenario(baseline, *changes: LocationChange) -> Scenario:
    return Scenario(scenario_id="scn-loc", base_plan_id=baseline.plan_id,
                    base_plan_hash=baseline.plan_hash, location_changes=tuple(changes))


def _eqp_periods(effective, equipment_id: str = "CRANE") -> list[dict]:
    payload = json.loads(effective.raw_snapshot)["payload"]
    item = next(e for e in payload["equipment"] if e["equipment_id"] == equipment_id)
    return item["availability_periods"]


def _loc_periods(effective, location_id: str = "BAY1") -> list[dict]:
    payload = json.loads(effective.raw_snapshot)["payload"]
    item = next(lo for lo in payload["locations"] if lo["location_id"] == location_id)
    return item["availability_periods"]


class TestMaterializeEquipmentChanges:
    """The Increment-A equipment applier (the formerly-deferred path — now writes
    ``quantity_available`` through the shared clip-and-split). `baseline_rich` ships one CRANE
    with a single period 0..96 @ quantity_available 2."""

    def test_windowed_outage_three_way_split(self, baseline_rich):
        """A crane out of service for a window -> three periods [2, 0, 2]."""
        outcome = materialize(baseline_rich, _eqp_scenario(
            baseline_rich, EquipmentChange("CRANE", 24.0, 0, to_hour=72.0)))
        assert outcome.ok and outcome.effective_plan is not None
        assert [p["quantity_available"] for p in _eqp_periods(outcome.effective_plan)] == [2, 0, 2]

    def test_open_ended_lowering(self, baseline_rich):
        """An open-ended reduction splits into [2, 1] at ``from_hour``."""
        outcome = materialize(baseline_rich, _eqp_scenario(
            baseline_rich, EquipmentChange("CRANE", 48.0, 1)))
        assert outcome.ok and outcome.effective_plan is not None
        assert [p["quantity_available"] for p in _eqp_periods(outcome.effective_plan)] == [2, 1]

    def test_ghost_equipment_is_conflict(self, baseline_rich):
        """A change targeting equipment absent from the baseline -> MATERIALIZE_CONFLICT."""
        outcome = materialize(baseline_rich, _eqp_scenario(
            baseline_rich, EquipmentChange("GHOST", 24.0, 0)))
        assert not outcome.ok and outcome.effective_plan is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_effective_payload_revalidates(self, baseline_rich, validator_adapter):
        """The rewritten equipment periods stay schema-valid through the real ValidationPort."""
        outcome = materialize(baseline_rich, _eqp_scenario(
            baseline_rich, EquipmentChange("CRANE", 48.0, 1)))
        assert outcome.ok and outcome.effective_plan is not None
        payload = json.loads(outcome.effective_plan.raw_snapshot)["payload"]
        issues = validator_adapter.validate_plan(payload)
        assert not any(i.severity is Severity.ERROR for i in issues), \
            [(i.code.name, i.message) for i in issues if i.severity is Severity.ERROR]


class TestMaterializeLocationChanges:
    """The Increment-A location applier: writes ``max_concurrent_tasks`` always and
    ``max_concurrent_workers`` only when the change names one (a None worker cap leaves the
    baseline's untouched). `baseline_rich` ships BAY1 with one period 0..96 @ tasks 2 / workers 4."""

    def test_task_cap_cut_leaves_worker_cap(self, baseline_rich):
        """An open-ended task-cap cut splits into [2, 1] tasks; the worker cap (4), which the
        change did not name, is preserved on every piece."""
        outcome = materialize(baseline_rich, _loc_scenario(
            baseline_rich, LocationChange("BAY1", 48.0, 1)))
        assert outcome.ok and outcome.effective_plan is not None
        periods = _loc_periods(outcome.effective_plan)
        assert [p["max_concurrent_tasks"] for p in periods] == [2, 1]
        assert all(p["max_concurrent_workers"] == 4 for p in periods)

    def test_both_caps_windowed(self, baseline_rich):
        """A windowed change naming both caps splits three ways, writing both in the window only."""
        outcome = materialize(baseline_rich, _loc_scenario(
            baseline_rich,
            LocationChange("BAY1", 24.0, 1, to_hour=72.0, new_max_concurrent_workers=2)))
        assert outcome.ok and outcome.effective_plan is not None
        periods = _loc_periods(outcome.effective_plan)
        assert [p["max_concurrent_tasks"] for p in periods] == [2, 1, 2]
        assert [p["max_concurrent_workers"] for p in periods] == [4, 2, 4]

    def test_ghost_location_is_conflict(self, baseline_rich):
        """A change targeting a location absent from the baseline -> MATERIALIZE_CONFLICT."""
        outcome = materialize(baseline_rich, _loc_scenario(
            baseline_rich, LocationChange("GHOST", 24.0, 1)))
        assert not outcome.ok and outcome.effective_plan is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_effective_payload_revalidates(self, baseline_rich, validator_adapter):
        """The rewritten location periods stay schema-valid through the real ValidationPort."""
        outcome = materialize(baseline_rich, _loc_scenario(
            baseline_rich, LocationChange("BAY1", 48.0, 1, new_max_concurrent_workers=2)))
        assert outcome.ok and outcome.effective_plan is not None
        payload = json.loads(outcome.effective_plan.raw_snapshot)["payload"]
        issues = validator_adapter.validate_plan(payload)
        assert not any(i.severity is Severity.ERROR for i in issues), \
            [(i.code.name, i.message) for i in issues if i.severity is Severity.ERROR]


# --- activity suppression (removal) appliers -------------------------------------------------
#     The `baseline` fixture ships two tasks: A (successors: ["B"]) and B (no successors), one
#     MECH pool. So suppressing B exercises dangling-edge cleanup (A's edge to B must vanish), and
#     suppressing the edge A->B exercises pure edge removal (both tasks kept).

def _sup_scenario(baseline, *, tasks=(), deps=()) -> Scenario:
    """A Scenario carrying only task / dependency suppressions, bound to `baseline`."""
    return Scenario(
        scenario_id="scn-sup", base_plan_id=baseline.plan_id, base_plan_hash=baseline.plan_hash,
        task_suppressions=tuple(tasks) or None, dependency_suppressions=tuple(deps) or None)


def _tasks(effective) -> list[dict]:
    return json.loads(effective.raw_snapshot)["payload"]["tasks"]


def _task_ids(effective) -> list[str]:
    return [t["task_id"] for t in _tasks(effective)]


def _successors_of(effective, task_id: str) -> list:
    t = next(t for t in _tasks(effective) if t["task_id"] == task_id)
    return t.get("successors") or []


class TestMaterializeSuppressions:
    """Increment-C activity removal: ``task_suppressions`` drops a task AND strips its id from every
    remaining task's successors (no dangling edge); ``dependency_suppressions`` drops one edge but
    keeps both tasks. Unknown task / non-existent edge is a blocking MATERIALIZE_CONFLICT."""

    def test_task_suppression_removes_task_and_strips_dangling_edges(self, baseline):
        """Suppressing B removes it from ``tasks`` and, because A pointed at B, strips B from A's
        successors — so the effective plan has no edge dangling to a removed task."""
        outcome = materialize(baseline, _sup_scenario(baseline, tasks=(TaskSuppression("B"),)))
        assert outcome.ok and outcome.effective_plan is not None
        assert _task_ids(outcome.effective_plan) == ["A"]
        assert _successors_of(outcome.effective_plan, "A") == []

    def test_dependency_suppression_removes_edge_keeps_tasks(self, baseline):
        """Suppressing the edge A->B drops B from A's successors while KEEPING both tasks."""
        outcome = materialize(baseline, _sup_scenario(
            baseline, deps=(DependencySuppression("A", "B"),)))
        assert outcome.ok and outcome.effective_plan is not None
        assert set(_task_ids(outcome.effective_plan)) == {"A", "B"}
        assert _successors_of(outcome.effective_plan, "A") == []

    def test_unknown_task_suppression_is_conflict(self, baseline):
        """Suppressing a task absent from the plan -> MATERIALIZE_CONFLICT, no effective plan."""
        outcome = materialize(baseline, _sup_scenario(baseline, tasks=(TaskSuppression("GHOST"),)))
        assert not outcome.ok and outcome.effective_plan is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_nonexistent_edge_suppression_is_conflict(self, baseline):
        """Suppressing an edge that is not present (B->A never existed) -> MATERIALIZE_CONFLICT."""
        outcome = materialize(baseline, _sup_scenario(
            baseline, deps=(DependencySuppression("B", "A"),)))
        assert not outcome.ok and outcome.effective_plan is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT and i.severity is Severity.ERROR
                   for i in outcome.issues)

    def test_dependency_suppression_unknown_predecessor_is_conflict(self, baseline):
        """An edge whose predecessor is not a task -> MATERIALIZE_CONFLICT."""
        outcome = materialize(baseline, _sup_scenario(
            baseline, deps=(DependencySuppression("GHOST", "A"),)))
        assert not outcome.ok and outcome.effective_plan is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT for i in outcome.issues)

    def test_suppressed_payload_revalidates(self, baseline, validator_adapter):
        """After suppressing B (and its dangling edge), the effective payload is still schema-valid
        through the real ValidationPort — no edge references the removed task."""
        outcome = materialize(baseline, _sup_scenario(baseline, tasks=(TaskSuppression("B"),)))
        assert outcome.ok and outcome.effective_plan is not None
        payload = json.loads(outcome.effective_plan.raw_snapshot)["payload"]
        issues = validator_adapter.validate_plan(payload)
        assert not any(i.severity is Severity.ERROR for i in issues), \
            [(i.code.name, i.message) for i in issues if i.severity is Severity.ERROR]

    def test_suppression_handles_object_form_successors(self, raw_plan):
        """A raw ``successors`` entry may be a bare id string OR the schema's object form
        ``{"task_id", "lag_hours"}`` (outage_schema oneOf; real plans like npp_outage use both).
        Both suppression appliers must read the object form: suppressing B strips A's *lag-carrying*
        edge to B, and suppressing the edge A->B likewise drops the object entry — not crash on it."""
        raw = copy.deepcopy(raw_plan)
        raw["tasks"][0]["successors"] = [{"task_id": "B", "lag_hours": 24}]   # A -B (lag 24)
        base = ser.build_reference_plan("obj-succ", raw, schema_version=SCHEMA_VERSION)

        by_task = materialize(base, _sup_scenario(base, tasks=(TaskSuppression("B"),)))
        assert by_task.ok and by_task.effective_plan is not None
        assert _task_ids(by_task.effective_plan) == ["A"]
        assert _successors_of(by_task.effective_plan, "A") == []           # object edge stripped

        by_edge = materialize(base, _sup_scenario(base, deps=(DependencySuppression("A", "B"),)))
        assert by_edge.ok and by_edge.effective_plan is not None
        assert set(_task_ids(by_edge.effective_plan)) == {"A", "B"}        # both tasks kept
        assert _successors_of(by_edge.effective_plan, "A") == []           # object edge dropped


class TestMaterializeEmergentThenSuppress:
    """The Increment-C apply ORDERING: emergent tasks -> emergent dependencies -> task suppressions
    -> dependency suppressions. An emergent task can be added and linked, and a same-scenario
    suppression is applied AFTER the adds against the live working tree."""

    def test_emergent_task_and_dependency_are_added(self, baseline):
        """An emergent task E1 (requiring the existing MECH skill) plus an edge A->E1 materialize:
        E1 appears in ``tasks`` with ``is_hold_point:False`` and no ``hold_point_type`` key, and A
        gains E1 as a successor."""
        emergent = Task(task_id="E1", duration=3.0, description="emergent",
                        required_resources=(ResourceReq(skill_type="MECH", crew_count=1),))
        scn = Scenario(
            scenario_id="scn-emg", base_plan_id=baseline.plan_id, base_plan_hash=baseline.plan_hash,
            emergent_tasks=(emergent,), emergent_dependencies=(Dependency("A", "E1"),))
        outcome = materialize(baseline, scn)
        assert outcome.ok and outcome.effective_plan is not None
        e1 = next(t for t in _tasks(outcome.effective_plan) if t["task_id"] == "E1")
        assert e1["is_hold_point"] is False and "hold_point_type" not in e1
        assert "E1" in _successors_of(outcome.effective_plan, "A")

    def test_emergent_dependency_dedups_against_object_form_edge(self, raw_plan):
        """Re-authoring an edge that already exists in the baseline's object form
        ``{"task_id", "lag_hours"}`` must not append a duplicate bare-string successor: the
        de-dup guard reads the object form, so A's successors stay a single B entry."""
        raw = copy.deepcopy(raw_plan)
        raw["tasks"][0]["successors"] = [{"task_id": "B", "lag_hours": 24}]   # A -B (lag 24)
        base = ser.build_reference_plan("obj-dedup", raw, schema_version=SCHEMA_VERSION)
        scn = Scenario(
            scenario_id="scn-dedup", base_plan_id=base.plan_id, base_plan_hash=base.plan_hash,
            emergent_dependencies=(Dependency("A", "B"),))
        outcome = materialize(base, scn)
        assert outcome.ok and outcome.effective_plan is not None
        succ = _successors_of(outcome.effective_plan, "A")
        assert [s if isinstance(s, str) else s["task_id"] for s in succ] == ["B"]   # no duplicate

    def test_emergent_add_then_suppress_same_scenario(self, baseline):
        """Adding E1 and suppressing baseline task B in one scenario: E1 is present, B is gone, and
        A's dangling edge to B is stripped — suppression runs against the post-add working tree."""
        emergent = Task(task_id="E1", duration=3.0,
                        required_resources=(ResourceReq(skill_type="MECH", crew_count=1),))
        scn = Scenario(
            scenario_id="scn-emg2", base_plan_id=baseline.plan_id,
            base_plan_hash=baseline.plan_hash, emergent_tasks=(emergent,),
            task_suppressions=(TaskSuppression("B"),))
        outcome = materialize(baseline, scn)
        assert outcome.ok and outcome.effective_plan is not None
        assert set(_task_ids(outcome.effective_plan)) == {"A", "E1"}
        assert _successors_of(outcome.effective_plan, "A") == []
