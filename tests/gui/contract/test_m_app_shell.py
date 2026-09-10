"""Contract group M — the app shell's streamlit-free seam (pure job).

``app/main.py`` is the composition root and the only module that imports Streamlit, but
its import is *guarded* and every ``st.*`` call lives inside ``main()`` / ``_render_*``.
The wiring itself — sample discovery and the ``load → prepare → run`` pipeline — is
factored into streamlit-free helpers so it can be exercised headlessly.

These tests run in the PURE job (no Streamlit, no PRISM):
  * the module imports cleanly whether or not Streamlit is installed;
  * ``discover_samples`` finds the shipping examples;
  * ``run_pipeline`` drives load → prepare → run to a COMPLETED result using the
    FakeExecutor, and blocks at the right stage on a rejected plan (no fabricated result).

The same ``run_pipeline`` over the REAL PRISM executor on example_10 is the
adapter_integration backstop in ``tests/gui/integration/test_app_wiring.py``.
"""

from __future__ import annotations

import csv
import io

from prismGui.app import main as app_main
from prismGui.domain.issues import IssueCode
from prismGui.domain.plan import PatchAction
from prismGui.domain.results import (
    ResourceUtilizationDTO,
    RunResultStatus,
    SkillUtilizationSeries,
    UtilizationInterval,
)


class TestImportSeam:

    def test_module_imports_without_streamlit(self):
        """The guarded Streamlit import lets the module load in a Streamlit-less env; the
        streamlit-free helpers are present regardless."""
        assert isinstance(app_main._HAS_STREAMLIT, bool)
        assert callable(app_main.discover_samples)
        assert callable(app_main.run_pipeline)
        assert callable(app_main.build_validator)

    def test_discover_samples_finds_the_shipping_examples(self):
        """Sample discovery resolves the canonical doc/demos/rcpsp/examples/ dir."""
        samples = app_main.discover_samples()
        assert "example_10" in samples
        assert samples["example_10"].endswith("example_10.json")


class TestRunPipeline:

    def test_pipeline_loads_prepares_and_runs_with_fake_executor(
        self, raw_plan, validator_adapter, snapshot_store, fake_executor
    ):
        """The app's own pipeline function drives load → prepare → run to a terminal
        COMPLETED result — the exact sequence the Run button triggers, minus PRISM."""
        rc = app_main.RunConfig(run_config_id="rc-m", priority_rule="lf", seed=42)
        outcome = app_main.run_pipeline(
            raw_plan, "p1", rc,
            validator=validator_adapter, store=snapshot_store, executor=fake_executor)
        assert outcome.ok
        assert outcome.stage == "run"
        assert outcome.result is not None
        assert outcome.result.status is RunResultStatus.COMPLETED
        assert outcome.reference_plan is not None

    def test_pipeline_blocks_at_load_on_a_rejected_plan(
        self, baseline_invalid, validator_adapter, snapshot_store, fake_executor
    ):
        """A schema-invalid plan blocks at the load stage: no result is fabricated and the
        blocking issues are carried back for the panel to render."""
        rc = app_main.RunConfig(run_config_id="rc-m", priority_rule="lf", seed=42)
        outcome = app_main.run_pipeline(
            baseline_invalid, "bad", rc,
            validator=validator_adapter, store=snapshot_store, executor=fake_executor)
        assert not outcome.ok
        assert outcome.stage == "load"
        assert outcome.result is None
        assert outcome.issues


class TestShapingHelpers:
    """The streamlit-free row/CSV shapers behind the Increment-2 views. Each is exercised
    off the `run_result` fixture (2 activities A,B; empty diagnostics) with real assertions,
    no ``st.*`` — the render helpers that wrap them are the only ``st.*`` sites."""

    def test_gantt_rows_one_row_per_activity(self, run_result):
        rows = app_main._gantt_rows(run_result.schedule)
        assert len(rows) == 2
        assert {"task", "start", "end", "duration", "delay",
                "float_class", "on_chain", "description"} <= set(rows[0])
        first = rows[0]
        assert first["task"] == "A"
        assert first["start"] == 0.0 and first["end"] == 4.0
        assert isinstance(first["on_chain"], bool) and first["on_chain"] is True
        assert isinstance(first["float_class"], str)

    def test_resource_util_rows_none_is_empty(self, run_result):
        """A run whose diagnostics carry no utilization timeline (the fixture default) maps
        to ``[]`` — the render helper then shows a caption, not an empty chart."""
        assert run_result.diagnostics.resource_utilization is None
        assert app_main._resource_util_rows(None) == []
        assert app_main._resource_util_rows(
            run_result.diagnostics.resource_utilization) == []

    def test_resource_util_rows_flatten_series_and_intervals(self):
        """A populated DTO flattens to one row per (series, interval), carrying the skill and
        the interval's numeric hours + demand/available."""
        util = ResourceUtilizationDTO(
            horizon_hours=6.0,
            series=(
                SkillUtilizationSeries("MECHANIC", (
                    UtilizationInterval(0.0, 4.0, demand=2, available=4),
                    UtilizationInterval(4.0, 6.0, demand=1, available=5))),
                SkillUtilizationSeries("ELECTRICIAN", (
                    UtilizationInterval(0.0, 6.0, demand=0, available=2),)),
            ))
        rows = app_main._resource_util_rows(util)
        assert len(rows) == 3
        assert {"skill", "start_hour", "end_hour", "demand", "available"} == set(rows[0])
        assert rows[0] == {"skill": "MECHANIC", "start_hour": 0.0, "end_hour": 4.0,
                           "demand": 2, "available": 4}
        assert rows[2]["skill"] == "ELECTRICIAN" and rows[2]["demand"] == 0

    def test_disposition_rows_are_the_six_indicators(self, run_result):
        rows = app_main._disposition_rows(run_result.disposition)
        assert len(rows) == 6
        assert all(set(r) == {"indicator", "state"} for r in rows)
        # every state is a tri-state string value (true / false / unknown), never a raw enum
        assert all(isinstance(r["state"], str) for r in rows)

    def test_provenance_rows_ten_fields_timestamp_is_string(self, run_result):
        rows = app_main._provenance_rows(run_result.provenance)
        assert len(rows) == 10
        assert all(set(r) == {"field", "value"} for r in rows)
        assert all(isinstance(r["value"], str) for r in rows)
        # the timestamp field renders as an ISO string, not a datetime repr
        ts = next(r["value"] for r in rows if r["field"] == "Timestamp")
        assert ts == run_result.provenance.timestamp.isoformat()

    def test_schedule_csv_reparses_to_header_plus_one_row_per_activity(self, run_result):
        text = app_main._schedule_csv(run_result.schedule)
        parsed = list(csv.reader(io.StringIO(text)))
        assert parsed[0] == [
            "task_id", "start_hour", "end_hour", "duration", "delay_hours",
            "float_class", "on_constrained_chain", "tf_actual_hours", "wbs_group",
            "actual_resources", "description"]
        assert len(parsed) == 1 + len(run_result.schedule.activities)   # header + A + B
        # spot-check the first data row against the fixture activity A
        row = dict(zip(parsed[0], parsed[1]))
        assert row["task_id"] == "A"
        assert row["actual_resources"] == "MECH:1"        # skill:crew, ';'-joined
        assert row["description"] == "task a"


class TestEditorFormBuilders:
    """The streamlit-free builders behind the structured editor forms. Each maps raw-tree
    data to a PatchOp (or `(op_or_None, issues)`) with no ``st.*`` — the ``_render_*`` wrappers
    that call them are the only ``st.*`` sites. Built off the `raw_plan` fixture (tasks A,B;
    one MECH pool with a single availability period). PatchOps target the schema-shaped raw
    keys, so each feeds straight through ``domain.apply_patch``."""

    def test_task_options_shape(self, raw_plan):
        options = app_main._task_options(raw_plan)
        assert [o["task_id"] for o in options] == ["A", "B"]
        assert set(options[0]) == {"index", "task_id", "duration", "description"}
        assert options[0]["index"] == 0 and options[0]["duration"] == 4
        assert options[0]["description"] == "task a"

    def test_duration_patch_replaces_by_index(self):
        op = app_main._duration_patch(0, 8.0)
        assert (op.action, op.path, op.value) == (PatchAction.REPLACE, "/tasks/0/duration", 8.0)

    def test_available_count_patch_replaces_nested_period(self):
        op = app_main._available_count_patch(0, 0, 5)
        assert op.action is PatchAction.REPLACE
        assert op.path == "/resources/0/availability_periods/0/available_count"
        assert op.value == 5

    def test_resource_type_patch_adds_optional_key(self):
        """resource_type is an optional pool key the samples omit, so the builder ADDs it
        (set-or-create) rather than REPLACE-ing a key that may be absent."""
        op = app_main._resource_type_patch(0, "consumable")
        assert (op.action, op.path, op.value) == (
            PatchAction.ADD, "/resources/0/resource_type", "consumable")

    def test_add_dependency_bare_string_at_lag_zero(self, raw_plan):
        """A lag-0 edge appends a bare task-id string to the predecessor's successors —
        the shape lag-free plans already use (B is task index 1, its successors empty)."""
        op, issues = app_main._add_dependency_patch(raw_plan, "B", "A", 0)
        assert issues == []
        assert (op.action, op.path, op.value) == (
            PatchAction.ADD, "/tasks/1/successors/-", "A")

    def test_add_dependency_object_at_positive_lag(self, raw_plan):
        op, issues = app_main._add_dependency_patch(raw_plan, "B", "A", 5)
        assert issues == []
        assert op.action is PatchAction.ADD and op.path == "/tasks/1/successors/-"
        assert op.value == {"task_id": "A", "lag_hours": 5.0}

    def test_add_dependency_unknown_endpoint_is_rejected(self, raw_plan):
        op, issues = app_main._add_dependency_patch(raw_plan, "A", "Z", 0)
        assert op is None
        assert [i.code for i in issues] == [IssueCode.REF_MISSING]

    def test_remove_dependency_finds_the_edge_index(self, raw_plan):
        """A (existing) edge A->B removes by its index within A's successors list (index 0)."""
        op, issues = app_main._remove_dependency_patch(raw_plan, "A", "B")
        assert issues == []
        assert (op.action, op.path) == (PatchAction.REMOVE, "/tasks/0/successors/0")

    def test_remove_dependency_absent_edge_is_rejected(self, raw_plan):
        op, issues = app_main._remove_dependency_patch(raw_plan, "B", "A")
        assert op is None
        assert [i.code for i in issues] == [IssueCode.REF_MISSING]

    def test_dependency_options_read_both_successor_forms(self, raw_plan):
        """The edge scanner reads a bare-string successor (lag 0) and, when present, the
        object form's lag."""
        import copy
        rows = app_main._dependency_options(raw_plan)
        assert rows == [{"predecessor": "A", "successor": "B", "lag_hours": 0.0, "pred_index": 0}]
        lagged = copy.deepcopy(raw_plan)
        lagged["tasks"][0]["successors"] = [{"task_id": "B", "lag_hours": 5}]
        rows = app_main._dependency_options(lagged)
        assert rows[0]["successor"] == "B" and rows[0]["lag_hours"] == 5.0

    def test_resource_options_defaults_type_to_renewable(self, raw_plan):
        """A pool with no resource_type key reports the renewable default (the schema
        default) — never a missing key that would break the type selector."""
        rows = app_main._resource_options(raw_plan)
        assert len(rows) == 1
        assert set(rows[0]) == {"index", "skill_type", "resource_type", "n_periods"}
        assert rows[0]["skill_type"] == "MECH"
        assert rows[0]["resource_type"] == "renewable"
        assert rows[0]["n_periods"] == 1

    def test_availability_options_shape(self, raw_plan):
        rows = app_main._availability_options(raw_plan, 0)
        assert len(rows) == 1
        assert set(rows[0]) == {"index", "start_date", "end_date", "available_count"}
        assert rows[0]["available_count"] == 3
        assert app_main._availability_options(raw_plan, 9) == []   # out-of-range -> empty

    # --- Increment 3: structural CRUD + availability-window date builders ---

    def test_add_task_patch_builds_schema_complete_task(self, raw_plan):
        """A new task appends to /tasks/- with every schema-required key present and the
        collections empty by default — so the committed rehydrate never trips on a missing key.
        ``is_hold_point`` is emitted False so the schema's ``if is_hold_point==true then require
        hold_point_type`` conditional (whose ``if`` omits ``required:[is_hold_point]``, matching
        vacuously on an absent flag) does not fire at commit and force a hold-point type."""
        op, issues = app_main._add_task_patch(raw_plan, "C", 5, "task c")
        assert issues == []
        assert op.action is PatchAction.ADD and op.path == "/tasks/-"
        assert set(op.value) == {"task_id", "description", "duration", "successors",
                                 "required_resources", "required_equipment", "is_hold_point"}
        assert op.value["task_id"] == "C" and op.value["duration"] == 5.0
        assert op.value["description"] == "task c"
        assert op.value["successors"] == [] and op.value["required_resources"] == []
        assert op.value["required_equipment"] == []
        assert op.value["is_hold_point"] is False

    def test_add_task_patch_seeds_a_crew_entry_when_a_skill_is_given(self, raw_plan):
        """Passing a skill makes the task schedulable: one {skill_type, crew_count} entry."""
        op, issues = app_main._add_task_patch(raw_plan, "C", 5, "task c",
                                              skill_type="MECH", crew_count=2)
        assert issues == []
        assert op.value["required_resources"] == [{"skill_type": "MECH", "crew_count": 2}]

    def test_add_task_patch_rejects_duplicate_or_blank_id(self, raw_plan):
        """Reusing an existing id (A) or a blank id fails fast with DUP_ID — never staged."""
        op, issues = app_main._add_task_patch(raw_plan, "A", 5, "dup")
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]
        op, issues = app_main._add_task_patch(raw_plan, "   ", 5, "blank")
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]

    def test_remove_task_patch_by_index_and_unknown(self, raw_plan):
        op, issues = app_main._remove_task_patch(raw_plan, "B")
        assert issues == []
        assert (op.action, op.path) == (PatchAction.REMOVE, "/tasks/1")
        op, issues = app_main._remove_task_patch(raw_plan, "ZZZ")
        assert op is None and [i.code for i in issues] == [IssueCode.REF_MISSING]

    def test_add_resource_pool_patch_builds_pool_with_initial_period(self, raw_plan):
        op, issues = app_main._add_resource_pool_patch(
            raw_plan, "ELEC", "renewable", "2025-01-01T00:00:00", "2025-01-05T00:00:00", 2)
        assert issues == []
        assert op.action is PatchAction.ADD and op.path == "/resources/-"
        assert op.value["skill_type"] == "ELEC" and op.value["resource_type"] == "renewable"
        assert op.value["availability_periods"] == [
            {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
             "available_count": 2}]

    def test_add_resource_pool_patch_rejects_duplicate_or_blank_skill(self, raw_plan):
        op, issues = app_main._add_resource_pool_patch(
            raw_plan, "MECH", "renewable", "2025-01-01T00:00:00", "2025-01-05T00:00:00", 1)
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]
        op, issues = app_main._add_resource_pool_patch(
            raw_plan, "", "renewable", "2025-01-01T00:00:00", "2025-01-05T00:00:00", 1)
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]

    def test_remove_resource_pool_patch_by_index(self):
        op = app_main._remove_resource_pool_patch(0)
        assert (op.action, op.path) == (PatchAction.REMOVE, "/resources/0")

    def test_add_and_remove_availability_period_patch(self):
        op = app_main._add_availability_period_patch(
            0, "2025-02-01T00:00:00", "2025-02-03T00:00:00", 4)
        assert op.action is PatchAction.ADD
        assert op.path == "/resources/0/availability_periods/-"
        assert op.value == {"start_date": "2025-02-01T00:00:00",
                            "end_date": "2025-02-03T00:00:00", "available_count": 4}
        rem = app_main._remove_availability_period_patch(0, 1)
        assert (rem.action, rem.path) == (
            PatchAction.REMOVE, "/resources/0/availability_periods/1")

    def test_availability_window_patch_replaces_both_dates(self):
        ops = app_main._availability_window_patch(
            0, 0, "2025-03-01T00:00:00", "2025-03-10T00:00:00")
        assert [(o.action, o.path, o.value) for o in ops] == [
            (PatchAction.REPLACE, "/resources/0/availability_periods/0/start_date",
             "2025-03-01T00:00:00"),
            (PatchAction.REPLACE, "/resources/0/availability_periods/0/end_date",
             "2025-03-10T00:00:00")]

    def test_iso_date_value_and_as_date_helpers(self):
        import datetime as _dt
        assert app_main._iso_date_value(_dt.date(2025, 1, 7)) == "2025-01-07T00:00:00"
        # _as_date parses the date part of a stored ISO string ...
        assert app_main._as_date("2025-01-07T00:00:00", _dt.date(2000, 1, 1)) == _dt.date(2025, 1, 7)
        assert app_main._as_date("2025-01-07T00:00:00.000Z", _dt.date(2000, 1, 1)) == _dt.date(2025, 1, 7)
        # ... and falls back on a bad / empty / non-string mid-draft value.
        fallback = _dt.date(2000, 1, 1)
        assert app_main._as_date("not-a-date", fallback) == fallback
        assert app_main._as_date("", fallback) == fallback
        assert app_main._as_date(None, fallback) == fallback

    # --- Increment 4: equipment & location entity CRUD builders ---

    @staticmethod
    def _tree_with_equipment_and_location() -> dict:
        """A tiny raw tree with one equipment item and one location (each with one availability
        period) for the options / remove / period / window / capacity builders that read existing
        entities. Inline (like the lag test) so no conftest fixture change is needed. The location
        period omits ``max_concurrent_workers`` — the no-worker-cap shape the loader tolerates."""
        return {
            "tasks": [], "resources": [],
            "equipment": [{
                "equipment_id": "CRANE-1", "description": "mobile crane",
                "availability_periods": [
                    {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
                     "quantity_available": 2}],
            }],
            "locations": [{
                "location_id": "ZONE-A", "description": "reactor bay",
                "availability_periods": [
                    {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
                     "max_concurrent_tasks": 3}],
            }],
        }

    def test_window_replace_ops_two_replaces(self):
        """The shared window helper emits the two date REPLACEs under any base path (here the
        equipment path) — the refactored spine under the resource/equipment/location builders."""
        ops = app_main._window_replace_ops(
            "/equipment/0/availability_periods/0", "2025-03-01T00:00:00", "2025-03-10T00:00:00")
        assert [(o.action, o.path, o.value) for o in ops] == [
            (PatchAction.REPLACE, "/equipment/0/availability_periods/0/start_date",
             "2025-03-01T00:00:00"),
            (PatchAction.REPLACE, "/equipment/0/availability_periods/0/end_date",
             "2025-03-10T00:00:00")]

    # equipment

    def test_add_equipment_patch_builds_item_with_initial_period(self, raw_plan):
        """A new equipment item appends to /equipment/- with the schema-required equipment_id /
        description and one seeded {start_date, end_date, quantity_available} period."""
        op, issues = app_main._add_equipment_patch(
            raw_plan, "CRANE-1", "mobile crane",
            "2025-01-01T00:00:00", "2025-01-05T00:00:00", 2)
        assert issues == []
        assert op.action is PatchAction.ADD and op.path == "/equipment/-"
        assert op.value["equipment_id"] == "CRANE-1"
        assert op.value["description"] == "mobile crane"
        assert op.value["availability_periods"] == [
            {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
             "quantity_available": 2}]

    def test_add_equipment_patch_rejects_duplicate_or_blank_id(self):
        tree = self._tree_with_equipment_and_location()
        op, issues = app_main._add_equipment_patch(
            tree, "CRANE-1", "dup", "2025-01-01T00:00:00", "2025-01-05T00:00:00", 1)
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]
        op, issues = app_main._add_equipment_patch(
            tree, "  ", "blank", "2025-01-01T00:00:00", "2025-01-05T00:00:00", 1)
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]

    def test_equipment_options_and_availability_options(self):
        tree = self._tree_with_equipment_and_location()
        assert app_main._equipment_options(tree) == [
            {"index": 0, "equipment_id": "CRANE-1", "description": "mobile crane", "n_periods": 1}]
        rows = app_main._equipment_availability_options(tree, 0)
        assert len(rows) == 1
        assert set(rows[0]) == {"index", "start_date", "end_date", "quantity_available"}
        assert rows[0]["quantity_available"] == 2
        assert app_main._equipment_availability_options(tree, 9) == []   # out-of-range -> empty

    def test_equipment_remove_period_quantity_and_window_patches(self):
        rem_item = app_main._remove_equipment_patch(0)
        assert (rem_item.action, rem_item.path) == (PatchAction.REMOVE, "/equipment/0")
        add = app_main._add_equipment_availability_patch(
            0, "2025-02-01T00:00:00", "2025-02-03T00:00:00", 4)
        assert (add.action, add.path) == (PatchAction.ADD, "/equipment/0/availability_periods/-")
        assert add.value == {"start_date": "2025-02-01T00:00:00",
                             "end_date": "2025-02-03T00:00:00", "quantity_available": 4}
        rem = app_main._remove_equipment_availability_patch(0, 1)
        assert (rem.action, rem.path) == (
            PatchAction.REMOVE, "/equipment/0/availability_periods/1")
        qty = app_main._equipment_quantity_patch(0, 0, 5)
        assert (qty.action, qty.path, qty.value) == (
            PatchAction.REPLACE, "/equipment/0/availability_periods/0/quantity_available", 5)
        win = app_main._equipment_window_patch(0, 0, "2025-03-01T00:00:00", "2025-03-10T00:00:00")
        assert [(o.action, o.path) for o in win] == [
            (PatchAction.REPLACE, "/equipment/0/availability_periods/0/start_date"),
            (PatchAction.REPLACE, "/equipment/0/availability_periods/0/end_date")]

    # locations

    def test_add_location_patch_omits_worker_cap_when_none(self, raw_plan):
        """No worker cap (the default) OMITS ``max_concurrent_workers`` from the seeded period —
        the null-tolerant shape the _load_locations fix lets commit."""
        op, issues = app_main._add_location_patch(
            raw_plan, "ZONE-A", "reactor bay", "2025-01-01T00:00:00", "2025-01-05T00:00:00", 3)
        assert issues == []
        assert op.action is PatchAction.ADD and op.path == "/locations/-"
        assert op.value["location_id"] == "ZONE-A" and op.value["description"] == "reactor bay"
        period = op.value["availability_periods"][0]
        assert period["max_concurrent_tasks"] == 3
        assert "max_concurrent_workers" not in period

    def test_add_location_patch_includes_worker_cap_when_given(self, raw_plan):
        op, issues = app_main._add_location_patch(
            raw_plan, "ZONE-A", "reactor bay",
            "2025-01-01T00:00:00", "2025-01-05T00:00:00", 3, max_workers=6)
        assert issues == []
        assert op.value["availability_periods"][0]["max_concurrent_workers"] == 6

    def test_add_location_patch_rejects_duplicate_or_blank_id(self):
        tree = self._tree_with_equipment_and_location()
        op, issues = app_main._add_location_patch(
            tree, "ZONE-A", "dup", "2025-01-01T00:00:00", "2025-01-05T00:00:00", 1)
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]
        op, issues = app_main._add_location_patch(
            tree, "", "blank", "2025-01-01T00:00:00", "2025-01-05T00:00:00", 1)
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]

    def test_location_options_and_availability_options_read_optional_workers(self):
        tree = self._tree_with_equipment_and_location()
        assert app_main._location_options(tree) == [
            {"index": 0, "location_id": "ZONE-A", "description": "reactor bay", "n_periods": 1}]
        rows = app_main._location_availability_options(tree, 0)
        assert len(rows) == 1
        assert set(rows[0]) == {"index", "start_date", "end_date",
                                "max_concurrent_tasks", "max_concurrent_workers"}
        assert rows[0]["max_concurrent_tasks"] == 3
        assert rows[0]["max_concurrent_workers"] is None   # optional, absent -> None
        assert app_main._location_availability_options(tree, 9) == []   # out-of-range -> empty

    def test_location_capacity_patch_replaces_tasks_and_adds_optional_workers(self):
        # no worker cap -> only the required-tasks REPLACE
        assert [(o.action, o.path, o.value)
                for o in app_main._location_capacity_patch(0, 0, 4)] == [
            (PatchAction.REPLACE, "/locations/0/availability_periods/0/max_concurrent_tasks", 4)]
        # a given cap -> REPLACE tasks + ADD (set-or-create) the optional workers key
        assert [(o.action, o.path, o.value)
                for o in app_main._location_capacity_patch(0, 0, 4, max_workers=6)] == [
            (PatchAction.REPLACE, "/locations/0/availability_periods/0/max_concurrent_tasks", 4),
            (PatchAction.ADD, "/locations/0/availability_periods/0/max_concurrent_workers", 6)]

    def test_location_remove_period_and_window_patches(self):
        rem_zone = app_main._remove_location_patch(0)
        assert (rem_zone.action, rem_zone.path) == (PatchAction.REMOVE, "/locations/0")
        add = app_main._add_location_availability_patch(
            0, "2025-02-01T00:00:00", "2025-02-03T00:00:00", 2)
        assert (add.action, add.path) == (PatchAction.ADD, "/locations/0/availability_periods/-")
        assert add.value["max_concurrent_tasks"] == 2
        assert "max_concurrent_workers" not in add.value          # cap omitted by default
        add_capped = app_main._add_location_availability_patch(
            0, "2025-02-01T00:00:00", "2025-02-03T00:00:00", 2, max_workers=5)
        assert add_capped.value["max_concurrent_workers"] == 5
        rem = app_main._remove_location_availability_patch(0, 1)
        assert (rem.action, rem.path) == (
            PatchAction.REMOVE, "/locations/0/availability_periods/1")
        win = app_main._location_window_patch(0, 0, "2025-03-01T00:00:00", "2025-03-10T00:00:00")
        assert [(o.action, o.path) for o in win] == [
            (PatchAction.REPLACE, "/locations/0/availability_periods/0/start_date"),
            (PatchAction.REPLACE, "/locations/0/availability_periods/0/end_date")]

    # --- Increment 5: consumable & plant-system entity CRUD builders ---

    @staticmethod
    def _tree_with_consumable_and_system() -> dict:
        """A tiny raw tree with one consumable (carrying one restock) and one plant system
        (carrying one valid state) for the options / remove / edit builders that read existing
        entities. Inline like the equipment/location helper; both keys are PRESENT here — the
        create-vs-append branch is exercised separately against a tree (``raw_plan``) that OMITS
        them, the shape every shipping sample has."""
        return {
            "tasks": [], "resources": [], "equipment": [], "locations": [],
            "consumables": [{
                "item_id": "N2-CYL", "description": "nitrogen cylinder", "total_quantity": 10.0,
                "restocks": [{"delivery_hour": 24.0, "quantity": 5.0}],
            }],
            "plant_systems": [{
                "system_id": "RCS", "description": "reactor coolant system",
                "valid_states": ["ISOLATED"],
            }],
        }

    # _array_add_op — the create-or-append primitive

    def test_array_add_op_creates_when_absent_appends_when_present(self):
        """Absent list -> ADD {pointer} with a one-element list (dict-parent ADD creates it);
        present list (even empty) -> ADD {pointer}/- (list-parent trailing-``-`` appends). This is
        the primitive the not-root-required consumables/plant_systems arrays need."""
        created = app_main._array_add_op("/consumables", False, {"item_id": "X"})
        assert (created.action, created.path, created.value) == (
            PatchAction.ADD, "/consumables", [{"item_id": "X"}])
        appended = app_main._array_add_op("/consumables", True, {"item_id": "X"})
        assert (appended.action, appended.path, appended.value) == (
            PatchAction.ADD, "/consumables/-", {"item_id": "X"})

    # consumables

    def test_add_consumable_patch_creates_array_when_absent(self, raw_plan):
        """raw_plan carries no ``consumables`` key (every sample's shape), so the first add CREATES
        the array: ADD /consumables with a one-element list holding the schema-required
        {item_id, description, total_quantity} (no restocks)."""
        assert "consumables" not in raw_plan
        op, issues = app_main._add_consumable_patch(raw_plan, "N2-CYL", "nitrogen", 10)
        assert issues == []
        assert op.action is PatchAction.ADD and op.path == "/consumables"
        assert op.value == [{"item_id": "N2-CYL", "description": "nitrogen",
                             "total_quantity": 10.0}]

    def test_add_consumable_patch_appends_when_present(self):
        """With the key present, the add APPENDS via /consumables/-."""
        tree = self._tree_with_consumable_and_system()
        op, issues = app_main._add_consumable_patch(tree, "SEAL", "one-use seal", 4)
        assert issues == []
        assert op.action is PatchAction.ADD and op.path == "/consumables/-"
        assert op.value == {"item_id": "SEAL", "description": "one-use seal",
                            "total_quantity": 4.0}

    def test_add_consumable_patch_rejects_duplicate_or_blank_id(self):
        tree = self._tree_with_consumable_and_system()
        op, issues = app_main._add_consumable_patch(tree, "N2-CYL", "dup", 1)
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]
        op, issues = app_main._add_consumable_patch(tree, "  ", "blank", 1)
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]

    def test_consumable_options_and_restock_options(self):
        tree = self._tree_with_consumable_and_system()
        assert app_main._consumable_options(tree) == [
            {"index": 0, "item_id": "N2-CYL", "description": "nitrogen cylinder",
             "total_quantity": 10.0, "n_restocks": 1}]
        rows = app_main._restock_options(tree, 0)
        assert rows == [{"index": 0, "delivery_hour": 24.0, "quantity": 5.0}]
        assert app_main._restock_options(tree, 9) == []   # out-of-range -> empty

    def test_consumable_total_and_remove_patches(self):
        total = app_main._consumable_total_patch(0, 12)
        assert (total.action, total.path, total.value) == (
            PatchAction.REPLACE, "/consumables/0/total_quantity", 12.0)
        rem = app_main._remove_consumable_patch(0)
        assert (rem.action, rem.path) == (PatchAction.REMOVE, "/consumables/0")

    def test_restock_add_creates_or_appends_remove_and_edit(self):
        tree = self._tree_with_consumable_and_system()
        # present restocks -> append via /restocks/-
        add = app_main._add_restock_patch(tree, 0, 48, 3)
        assert (add.action, add.path) == (PatchAction.ADD, "/consumables/0/restocks/-")
        assert add.value == {"delivery_hour": 48.0, "quantity": 3.0}
        # a consumable with NO restocks key -> the first restock CREATES the list
        tree["consumables"].append(
            {"item_id": "SEAL", "description": "seal", "total_quantity": 2.0})
        add_first = app_main._add_restock_patch(tree, 1, 12, 1)
        assert (add_first.action, add_first.path) == (PatchAction.ADD, "/consumables/1/restocks")
        assert add_first.value == [{"delivery_hour": 12.0, "quantity": 1.0}]
        rem = app_main._remove_restock_patch(0, 0)
        assert (rem.action, rem.path) == (PatchAction.REMOVE, "/consumables/0/restocks/0")
        edit = app_main._restock_edit_patch(0, 0, 30, 7)
        assert [(o.action, o.path, o.value) for o in edit] == [
            (PatchAction.REPLACE, "/consumables/0/restocks/0/delivery_hour", 30.0),
            (PatchAction.REPLACE, "/consumables/0/restocks/0/quantity", 7.0)]

    # plant systems

    def test_add_system_patch_creates_array_when_absent(self, raw_plan):
        assert "plant_systems" not in raw_plan
        op, issues = app_main._add_system_patch(raw_plan, "RCS", "reactor coolant system")
        assert issues == []
        assert op.action is PatchAction.ADD and op.path == "/plant_systems"
        assert op.value == [{"system_id": "RCS", "description": "reactor coolant system"}]

    def test_add_system_patch_appends_when_present_and_rejects_dup_or_blank(self):
        tree = self._tree_with_consumable_and_system()
        op, issues = app_main._add_system_patch(tree, "CVCS", "chemical volume control")
        assert issues == []
        assert op.action is PatchAction.ADD and op.path == "/plant_systems/-"
        assert op.value == {"system_id": "CVCS", "description": "chemical volume control"}
        op, issues = app_main._add_system_patch(tree, "RCS", "dup")
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]
        op, issues = app_main._add_system_patch(tree, " ", "blank")
        assert op is None and [i.code for i in issues] == [IssueCode.DUP_ID]

    def test_system_options_and_state_options(self):
        tree = self._tree_with_consumable_and_system()
        assert app_main._system_options(tree) == [
            {"index": 0, "system_id": "RCS", "description": "reactor coolant system",
             "n_states": 1}]
        assert app_main._system_state_options(tree, 0) == [{"index": 0, "state": "ISOLATED"}]
        assert app_main._system_state_options(tree, 9) == []   # out-of-range -> empty

    def test_add_system_state_creates_or_appends_and_rejects_blank_or_dup(self):
        tree = self._tree_with_consumable_and_system()
        # present valid_states -> append the new state string via /valid_states/-
        op, issues = app_main._add_system_state_patch(tree, 0, "DRAINED")
        assert issues == []
        assert (op.action, op.path, op.value) == (
            PatchAction.ADD, "/plant_systems/0/valid_states/-", "DRAINED")
        # a system with NO valid_states key -> the first state CREATES the list
        tree["plant_systems"].append({"system_id": "CVCS", "description": "cvcs"})
        op_first, issues = app_main._add_system_state_patch(tree, 1, "RUNNING")
        assert issues == []
        assert (op_first.action, op_first.path, op_first.value) == (
            PatchAction.ADD, "/plant_systems/1/valid_states", ["RUNNING"])
        # blank and duplicate states are rejected up-front (DUP_ID-coded), never staged
        op_blank, issues = app_main._add_system_state_patch(tree, 0, "  ")
        assert op_blank is None and [i.code for i in issues] == [IssueCode.DUP_ID]
        op_dup, issues = app_main._add_system_state_patch(tree, 0, "ISOLATED")
        assert op_dup is None and [i.code for i in issues] == [IssueCode.DUP_ID]

    def test_remove_system_and_state_patches(self):
        rem_sys = app_main._remove_system_patch(0)
        assert (rem_sys.action, rem_sys.path) == (PatchAction.REMOVE, "/plant_systems/0")
        rem_state = app_main._remove_system_state_patch(0, 1)
        assert (rem_state.action, rem_state.path) == (
            PatchAction.REMOVE, "/plant_systems/0/valid_states/1")
