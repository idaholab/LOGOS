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
import json

from prismGui.app import main as app_main
from prismGui.domain.issues import IssueCode
from prismGui.domain.plan import PatchAction
from prismGui.domain.results import (
    FloatClass,
    ResourceUtilizationDTO,
    RunResultStatus,
    ScheduledActivityDTO,
    ScheduleDTO,
    SkillUtilizationSeries,
    UtilizationInterval,
    classify_float,
)
from prismGui.domain.scenario import DurationOverride, ResourceChange, Scenario


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

    def test_evaluation_weights_defaults_collapse_to_none(self):
        """The four fitness-weight sidebar inputs at their prefilled defaults (1.0/0.5/0.3/2.0)
        assemble to ``None`` — so a GUI "defaults" run stays byte-identical to the no-weights
        path (same run_config_hash, same engine fitness); the feature is invisible until used."""
        assert app_main._evaluation_weights(1.0, 0.5, 0.3, 2.0) is None

    def test_evaluation_weights_custom_carries_the_passed_values(self):
        """Any non-default input yields an explicit ``EvaluationWeights`` carrying the four
        passed values verbatim (no clamping / normalization — weights are unvalidated)."""
        w = app_main._evaluation_weights(2.0, 0.5, 0.3, 2.0)
        assert isinstance(w, app_main.EvaluationWeights)
        assert (w.alpha, w.beta, w.gamma, w.delta) == (2.0, 0.5, 0.3, 2.0)

    def test_evaluation_weights_non_default_in_any_position_is_not_none(self):
        """A customized value in any one of the four positions (the other three left at their
        defaults) is enough to produce a non-``None`` weights object — the "defaults → None"
        collapse is an all-four-equal test, not a per-field one."""
        assert app_main._evaluation_weights(9.0, 0.5, 0.3, 2.0) is not None   # α
        assert app_main._evaluation_weights(1.0, 9.0, 0.3, 2.0) is not None   # β
        assert app_main._evaluation_weights(1.0, 0.5, 9.0, 2.0) is not None   # γ
        assert app_main._evaluation_weights(1.0, 0.5, 0.3, 9.0) is not None   # δ


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

    # -------------------------------------------------------------------------
    # Increment 6: task requirement wiring. Point a task at the entities the
    # other tabs author — location_id / required_equipment / required_consumables /
    # required_system_states / required_resources (incl. alternative_skill_types).
    # -------------------------------------------------------------------------

    @staticmethod
    def _tree_with_wired_task() -> dict:
        """A tiny raw tree with one FULLY WIRED task (task index 0): a location, a resource
        requirement carrying an alternative skill, an equipment requirement, a consumable
        requirement and a system-state requirement — plus a bare task (index 1) whose optional
        requirement sub-arrays are ABSENT, for the create-vs-append branch. Inline like the
        consumable/system helper; the pools are present so the readers have data to return."""
        return {
            "tasks": [
                {"task_id": "A", "description": "task a", "duration": 4, "successors": [],
                 "location_id": "ZONE-1",
                 "required_resources": [{"skill_type": "MECH", "crew_count": 2,
                                         "alternative_skill_types": ["ELEC"]}],
                 "required_equipment": [{"equipment_id": "CRANE-1", "quantity_needed": 1}],
                 "required_consumables": [{"item_id": "N2-CYL", "quantity_needed": 2.0}],
                 "required_system_states": [{"system_id": "RCS", "required_state": "ISOLATED"}]},
                {"task_id": "B", "description": "task b", "duration": 2, "successors": [],
                 "required_resources": [{"skill_type": "MECH", "crew_count": 1}],
                 "required_equipment": []},
            ],
            "resources": [], "equipment": [], "locations": [], "consumables": [],
            "plant_systems": [],
        }

    # per-task requirement readers

    def test_task_requirement_readers_shape(self):
        tree = self._tree_with_wired_task()
        assert app_main._task_location(tree, 0) == "ZONE-1"
        assert app_main._task_location(tree, 1) is None      # key absent -> None
        assert app_main._task_at(tree, 9) is None            # out of range -> None
        assert app_main._task_equipment_reqs(tree, 0) == [
            {"index": 0, "equipment_id": "CRANE-1", "quantity_needed": 1}]
        assert app_main._task_consumable_reqs(tree, 0) == [
            {"index": 0, "item_id": "N2-CYL", "quantity_needed": 2.0}]
        assert app_main._task_system_state_reqs(tree, 0) == [
            {"index": 0, "system_id": "RCS", "required_state": "ISOLATED"}]
        assert app_main._task_resource_reqs(tree, 0) == [
            {"index": 0, "skill_type": "MECH", "crew_count": 2, "alternatives": ["ELEC"]}]
        # a task whose optional sub-arrays are absent reads as empty (never crashes)
        assert app_main._task_consumable_reqs(tree, 1) == []
        assert app_main._task_system_state_reqs(tree, 1) == []
        assert app_main._task_resource_reqs(tree, 1) == [
            {"index": 0, "skill_type": "MECH", "crew_count": 1, "alternatives": []}]
        assert app_main._task_equipment_reqs(tree, 9) == []  # out of range -> empty

    # location_id — SET is ADD (set-or-create), CLEAR is REMOVE (null can't be patched)

    def test_task_location_set_and_clear_patches(self):
        set_op = app_main._task_location_patch(0, "ZONE-9")
        assert (set_op.action, set_op.path, set_op.value) == (
            PatchAction.ADD, "/tasks/0/location_id", "ZONE-9")
        clear_op = app_main._task_location_clear_patch(0)
        assert (clear_op.action, clear_op.path) == (PatchAction.REMOVE, "/tasks/0/location_id")

    # required_equipment (REF_MISSING-bound) — always present, so append; guard covers a raw draft

    def test_add_task_equipment_appends_and_removes(self):
        tree = self._tree_with_wired_task()
        op = app_main._add_task_equipment_patch(tree, 0, "FORKLIFT", 3)
        assert (op.action, op.path, op.value) == (
            PatchAction.ADD, "/tasks/0/required_equipment/-",
            {"equipment_id": "FORKLIFT", "quantity_needed": 3})
        rem = app_main._remove_task_equipment_patch(0, 0)
        assert (rem.action, rem.path) == (PatchAction.REMOVE, "/tasks/0/required_equipment/0")

    # required_consumables (NOT ref-validated) — optional, so create-vs-append

    def test_add_task_consumable_creates_when_absent_appends_when_present(self):
        tree = self._tree_with_wired_task()
        # task 1 has no required_consumables key -> the first add CREATES the array
        created = app_main._add_task_consumable_patch(tree, 1, "SEAL", 1.5)
        assert (created.action, created.path, created.value) == (
            PatchAction.ADD, "/tasks/1/required_consumables",
            [{"item_id": "SEAL", "quantity_needed": 1.5}])
        # task 0 already carries one -> APPEND via /-
        appended = app_main._add_task_consumable_patch(tree, 0, "SEAL", 4)
        assert (appended.action, appended.path, appended.value) == (
            PatchAction.ADD, "/tasks/0/required_consumables/-",
            {"item_id": "SEAL", "quantity_needed": 4.0})
        rem = app_main._remove_task_consumable_patch(0, 0)
        assert (rem.action, rem.path) == (PatchAction.REMOVE, "/tasks/0/required_consumables/0")

    # required_system_states (NOT ref-validated) — optional, so create-vs-append

    def test_add_task_system_state_creates_when_absent_appends_when_present(self):
        tree = self._tree_with_wired_task()
        created = app_main._add_task_system_state_patch(tree, 1, "CVCS", "RUNNING")
        assert (created.action, created.path, created.value) == (
            PatchAction.ADD, "/tasks/1/required_system_states",
            [{"system_id": "CVCS", "required_state": "RUNNING"}])
        appended = app_main._add_task_system_state_patch(tree, 0, "CVCS", "RUNNING")
        assert (appended.action, appended.path, appended.value) == (
            PatchAction.ADD, "/tasks/0/required_system_states/-",
            {"system_id": "CVCS", "required_state": "RUNNING"})
        rem = app_main._remove_task_system_state_patch(0, 0)
        assert (rem.action, rem.path) == (PatchAction.REMOVE, "/tasks/0/required_system_states/0")

    # required_resources — add/remove/edit beyond the initial add

    def test_add_and_remove_task_resource_patches(self):
        tree = self._tree_with_wired_task()
        op = app_main._add_task_resource_patch(tree, 0, "ELEC", 3)
        assert (op.action, op.path, op.value) == (
            PatchAction.ADD, "/tasks/0/required_resources/-",
            {"skill_type": "ELEC", "crew_count": 3})
        rem = app_main._remove_task_resource_patch(0, 1)
        assert (rem.action, rem.path) == (PatchAction.REMOVE, "/tasks/0/required_resources/1")

    def test_task_resource_crew_and_skill_replace_patches(self):
        crew = app_main._task_resource_crew_patch(0, 0, 5)
        assert (crew.action, crew.path, crew.value) == (
            PatchAction.REPLACE, "/tasks/0/required_resources/0/crew_count", 5)
        skill = app_main._task_resource_skill_patch(0, 0, "ELEC")
        assert (skill.action, skill.path, skill.value) == (
            PatchAction.REPLACE, "/tasks/0/required_resources/0/skill_type", "ELEC")

    # alternative_skill_types (NOT ref-validated) — create-vs-append, blank/dup rejected up-front

    def test_add_task_alt_skill_creates_when_absent_appends_when_present(self):
        tree = self._tree_with_wired_task()
        # task 1 resource 0 has no alternative_skill_types key -> the first alt CREATES the list
        created, issues = app_main._add_task_alt_skill_patch(tree, 1, 0, "HVAC")
        assert issues == []
        assert (created.action, created.path, created.value) == (
            PatchAction.ADD, "/tasks/1/required_resources/0/alternative_skill_types", ["HVAC"])
        # task 0 resource 0 already carries ["ELEC"] -> APPEND via /-
        appended, issues = app_main._add_task_alt_skill_patch(tree, 0, 0, "HVAC")
        assert issues == []
        assert (appended.action, appended.path, appended.value) == (
            PatchAction.ADD, "/tasks/0/required_resources/0/alternative_skill_types/-", "HVAC")

    def test_add_task_alt_skill_rejects_blank_or_duplicate(self):
        tree = self._tree_with_wired_task()
        blank_op, issues = app_main._add_task_alt_skill_patch(tree, 0, 0, "   ")
        assert blank_op is None and [i.code for i in issues] == [IssueCode.DUP_ID]
        # "ELEC" is already an alternative on task 0 resource 0 -> duplicate, rejected up-front
        dup_op, issues = app_main._add_task_alt_skill_patch(tree, 0, 0, "ELEC")
        assert dup_op is None and [i.code for i in issues] == [IssueCode.DUP_ID]

    def test_remove_task_alt_skill_patch(self):
        rem = app_main._remove_task_alt_skill_patch(0, 0, 0)
        assert (rem.action, rem.path) == (
            PatchAction.REMOVE, "/tasks/0/required_resources/0/alternative_skill_types/0")

    # -------------------------------------------------------------------------
    # Increment 7: task scheduling attributes. Author a task's hold point
    # (is_hold_point + hold_point_type, managed as a pair so no misuse state is
    # ever emitted), its time_windows[], and its execution modes[] (mode CRUD +
    # per-mode nested required_resources/required_equipment + optional per-mode
    # dose_rate_mrem_per_hour / mobilization_lead_hours overrides).
    # -------------------------------------------------------------------------

    @staticmethod
    def _tree_with_task_scheduling() -> dict:
        """A tiny raw tree for the scheduling attributes: task 0 ("A") carries a hold point (with a
        blocks_tasks list), one time window, and two modes -- mode 0 ("FAST") fully populated
        (nested crew + equipment, dose + mobilization overrides), mode 1 ("SLOW") with the nested
        arrays ABSENT (the create-vs-append branch and the tolerant-reader case). Task 1 ("B") is
        bare: no is_hold_point / time_windows / modes keys at all (the vacuous-False + create
        branches). Pools are present so the mode sub-editors have skills/equipment to offer."""
        return {
            "tasks": [
                {"task_id": "A", "description": "task a", "duration": 4, "successors": [],
                 "is_hold_point": True, "hold_point_type": "NRC", "blocks_tasks": ["B"],
                 "time_windows": [{"earliest": 0.0, "latest": 48.0}],
                 "modes": [
                     {"mode_id": "FAST", "duration": 4.0,
                      "required_resources": [{"skill_type": "MECH", "crew_count": 2}],
                      "required_equipment": [{"equipment_id": "CRANE-1", "quantity_needed": 1}],
                      "dose_rate_mrem_per_hour": 5.0, "mobilization_lead_hours": 2.0},
                     {"mode_id": "SLOW", "duration": 8.0},
                 ]},
                {"task_id": "B", "description": "task b", "duration": 2, "successors": []},
            ],
            "resources": [], "equipment": [], "locations": [], "consumables": [],
            "plant_systems": [],
        }

    # per-task scheduling readers

    def test_task_scheduling_readers_shape(self):
        tree = self._tree_with_task_scheduling()
        # hold point: present on task 0, vacuous-False (key absent) on the bare task 1 / out of range
        assert app_main._task_hold_point(tree, 0) == {
            "is_hold_point": True, "hold_point_type": "NRC"}
        assert app_main._task_hold_point(tree, 1) == {
            "is_hold_point": False, "hold_point_type": None}
        assert app_main._task_hold_point(tree, 9) == {
            "is_hold_point": False, "hold_point_type": None}
        # time windows: one row on task 0, empty on the bare task 1 / out of range
        assert app_main._task_time_windows(tree, 0) == [
            {"index": 0, "earliest": 0.0, "latest": 48.0}]
        assert app_main._task_time_windows(tree, 1) == []
        assert app_main._task_time_windows(tree, 9) == []
        # modes: FAST fully populated (nested crew + equipment, dose + mob); SLOW read tolerantly
        # (nested arrays absent -> empty, overrides absent -> None)
        modes = app_main._task_modes(tree, 0)
        assert modes[0] == {
            "index": 0, "mode_id": "FAST", "duration": 4.0,
            "dose_rate": 5.0, "mobilization_lead_hours": 2.0,
            "resources": [{"index": 0, "skill_type": "MECH", "crew_count": 2}],
            "equipment": [{"index": 0, "equipment_id": "CRANE-1", "quantity_needed": 1}]}
        assert modes[1] == {
            "index": 1, "mode_id": "SLOW", "duration": 8.0,
            "dose_rate": None, "mobilization_lead_hours": None,
            "resources": [], "equipment": []}
        assert app_main._task_modes(tree, 1) == []       # bare task -> no modes
        assert app_main._task_modes(tree, 9) == []       # out of range -> empty

    # hold point — the paired conditional: SET is two ADDs, CLEAR is flag-False + guarded REMOVEs

    def test_task_hold_point_set_and_clear_patches(self):
        tree = self._tree_with_task_scheduling()
        # SET: ADD (set-or-create) both keys, never REPLACE -- works whether or not they preexist
        set_ops = app_main._task_hold_point_set_patch(0, "QA")
        assert [(o.action, o.path, o.value) for o in set_ops] == [
            (PatchAction.ADD, "/tasks/0/is_hold_point", True),
            (PatchAction.ADD, "/tasks/0/hold_point_type", "QA")]
        # CLEAR on task 0 (hold_point_type AND blocks_tasks present) -> flag False + both REMOVEs,
        # so the GUI never leaves the false+type HOLD_POINT_MISUSE state (nor a dangling block list)
        clear_full = app_main._task_hold_point_clear_patch(tree, 0)
        assert [(o.action, o.path, o.value) for o in clear_full] == [
            (PatchAction.ADD, "/tasks/0/is_hold_point", False),
            (PatchAction.REMOVE, "/tasks/0/hold_point_type", None),
            (PatchAction.REMOVE, "/tasks/0/blocks_tasks", None)]
        # CLEAR on the bare task 1 (neither key present) -> only the flag ADD; the REMOVEs are
        # guarded on presence (a REMOVE of an absent key would fail at commit)
        clear_bare = app_main._task_hold_point_clear_patch(tree, 1)
        assert [(o.action, o.path, o.value) for o in clear_bare] == [
            (PatchAction.ADD, "/tasks/1/is_hold_point", False)]

    # time_windows (NOT ref-validated) — create-vs-append, edit both bounds, remove

    def test_add_task_time_window_creates_when_absent_appends_when_present(self):
        tree = self._tree_with_task_scheduling()
        # task 1 has no time_windows key -> the first add CREATES the array
        created = app_main._add_task_time_window_patch(tree, 1, 6, 12)
        assert (created.action, created.path, created.value) == (
            PatchAction.ADD, "/tasks/1/time_windows", [{"earliest": 6.0, "latest": 12.0}])
        # task 0 already carries one -> APPEND via /- (bounds coerced to float)
        appended = app_main._add_task_time_window_patch(tree, 0, 24, 72)
        assert (appended.action, appended.path, appended.value) == (
            PatchAction.ADD, "/tasks/0/time_windows/-", {"earliest": 24.0, "latest": 72.0})

    def test_task_time_window_edit_and_remove_patches(self):
        edit = app_main._task_time_window_edit_patch(0, 0, 3, 30)
        assert [(o.action, o.path, o.value) for o in edit] == [
            (PatchAction.REPLACE, "/tasks/0/time_windows/0/earliest", 3.0),
            (PatchAction.REPLACE, "/tasks/0/time_windows/0/latest", 30.0)]
        rem = app_main._remove_task_time_window_patch(0, 0)
        assert (rem.action, rem.path) == (PatchAction.REMOVE, "/tasks/0/time_windows/0")

    # execution modes — create-vs-append (all four keys, empty nested arrays), remove, blank/dup

    def test_add_task_mode_creates_when_absent_appends_when_present(self):
        tree = self._tree_with_task_scheduling()
        # task 1 has no modes key -> CREATE the array with a schema-complete mode: all four
        # required keys, the two nested arrays seeded []
        created, issues = app_main._add_task_mode_patch(tree, 1, "SOLO", 5)
        assert issues == []
        assert (created.action, created.path, created.value) == (
            PatchAction.ADD, "/tasks/1/modes",
            [{"mode_id": "SOLO", "duration": 5.0,
              "required_resources": [], "required_equipment": []}])
        # task 0 already carries modes -> APPEND via /- (duration coerced to float)
        appended, issues = app_main._add_task_mode_patch(tree, 0, "MEDIUM", 6)
        assert issues == []
        assert (appended.action, appended.path, appended.value) == (
            PatchAction.ADD, "/tasks/0/modes/-",
            {"mode_id": "MEDIUM", "duration": 6.0,
             "required_resources": [], "required_equipment": []})

    def test_add_task_mode_rejects_blank_or_duplicate(self):
        tree = self._tree_with_task_scheduling()
        blank_op, issues = app_main._add_task_mode_patch(tree, 0, "  ", 4)
        assert blank_op is None and [i.code for i in issues] == [IssueCode.DUP_ID]
        # "FAST" already exists on task 0 -> duplicate mode_id, rejected up-front (uniqueItems)
        dup_op, issues = app_main._add_task_mode_patch(tree, 0, "FAST", 4)
        assert dup_op is None and [i.code for i in issues] == [IssueCode.DUP_ID]

    def test_remove_task_mode_patch(self):
        rem = app_main._remove_task_mode_patch(0, 1)
        assert (rem.action, rem.path) == (PatchAction.REMOVE, "/tasks/0/modes/1")

    # per-mode duration REPLACE + the optional dose / mobilization overrides (ADD set-or-create,
    # REMOVE to clear back to the task-level value)

    def test_task_mode_duration_dose_and_mob_patches(self):
        dur = app_main._task_mode_duration_patch(0, 0, 9)
        assert (dur.action, dur.path, dur.value) == (
            PatchAction.REPLACE, "/tasks/0/modes/0/duration", 9.0)

        dose = app_main._task_mode_dose_patch(0, 0, 7.5)
        assert (dose.action, dose.path, dose.value) == (
            PatchAction.ADD, "/tasks/0/modes/0/dose_rate_mrem_per_hour", 7.5)
        dose_clear = app_main._task_mode_dose_clear_patch(0, 0)
        assert (dose_clear.action, dose_clear.path) == (
            PatchAction.REMOVE, "/tasks/0/modes/0/dose_rate_mrem_per_hour")

        mob = app_main._task_mode_mob_patch(0, 0, 3)
        assert (mob.action, mob.path, mob.value) == (
            PatchAction.ADD, "/tasks/0/modes/0/mobilization_lead_hours", 3.0)
        mob_clear = app_main._task_mode_mob_clear_patch(0, 0)
        assert (mob_clear.action, mob_clear.path) == (
            PatchAction.REMOVE, "/tasks/0/modes/0/mobilization_lead_hours")

    # per-mode required_resources / required_equipment (NOT ref-validated -- the asymmetry):
    # create-vs-append + remove pointers, one level shallower than the task-level editor

    def test_add_and_remove_task_mode_resource_patches(self):
        tree = self._tree_with_task_scheduling()
        # mode 1 ("SLOW") has no required_resources key -> the first add CREATES the array
        created = app_main._add_task_mode_resource_patch(tree, 0, 1, "ELEC", 2)
        assert (created.action, created.path, created.value) == (
            PatchAction.ADD, "/tasks/0/modes/1/required_resources",
            [{"skill_type": "ELEC", "crew_count": 2}])
        # mode 0 ("FAST") already carries one -> APPEND via /-
        appended = app_main._add_task_mode_resource_patch(tree, 0, 0, "ELEC", 3)
        assert (appended.action, appended.path, appended.value) == (
            PatchAction.ADD, "/tasks/0/modes/0/required_resources/-",
            {"skill_type": "ELEC", "crew_count": 3})
        rem = app_main._remove_task_mode_resource_patch(0, 0, 0)
        assert (rem.action, rem.path) == (
            PatchAction.REMOVE, "/tasks/0/modes/0/required_resources/0")

    def test_add_and_remove_task_mode_equipment_patches(self):
        tree = self._tree_with_task_scheduling()
        # mode 1 ("SLOW") has no required_equipment key -> the first add CREATES the array
        created = app_main._add_task_mode_equipment_patch(tree, 0, 1, "FORKLIFT", 1)
        assert (created.action, created.path, created.value) == (
            PatchAction.ADD, "/tasks/0/modes/1/required_equipment",
            [{"equipment_id": "FORKLIFT", "quantity_needed": 1}])
        # mode 0 ("FAST") already carries one -> APPEND via /-
        appended = app_main._add_task_mode_equipment_patch(tree, 0, 0, "FORKLIFT", 2)
        assert (appended.action, appended.path, appended.value) == (
            PatchAction.ADD, "/tasks/0/modes/0/required_equipment/-",
            {"equipment_id": "FORKLIFT", "quantity_needed": 2})
        rem = app_main._remove_task_mode_equipment_patch(0, 0, 0)
        assert (rem.action, rem.path) == (
            PatchAction.REMOVE, "/tasks/0/modes/0/required_equipment/0")

    # -----------------------------------------------------------------------------
    # Advanced-pool editors (Phase-2 close-out): equipment zone-affinity, task zone_ids,
    # resource dose-budget, task-level dose-rate. All four fields are OPTIONAL and shipping
    # samples omit them, so each SET is an ADD (set-or-create, the _resource_type_patch /
    # _task_mode_dose_patch precedent) and each CLEAR a REMOVE (a set-to-null is impossible;
    # apply_patch rejects a None value). Numerics are float()-coerced (range left to commit);
    # readers are DEDICATED (not keys on the exact-shape option readers a contract test pins).
    # GUI-only wire-up — no engine/schema/domain change.
    # -----------------------------------------------------------------------------

    def test_equipment_zone_set_and_clear_patches(self):
        set_op = app_main._equipment_zone_patch(0, "ZONE-A")
        assert (set_op.action, set_op.path, set_op.value) == (
            PatchAction.ADD, "/equipment/0/zone_id", "ZONE-A")
        clear_op = app_main._equipment_zone_clear_patch(0)
        assert (clear_op.action, clear_op.path) == (PatchAction.REMOVE, "/equipment/0/zone_id")

    def test_equipment_zone_reader(self):
        tree = self._tree_with_equipment_and_location()
        assert app_main._equipment_zone(tree, 0) is None     # key absent -> None
        assert app_main._equipment_zone(tree, 9) is None     # out of range -> None
        tree["equipment"][0]["zone_id"] = "ZONE-A"
        assert app_main._equipment_zone(tree, 0) == "ZONE-A"

    def test_equipment_zone_apply_patch_round_trip(self):
        """The scalar SET (ADD create-branch) then CLEAR (REMOVE) feed straight through
        domain.apply_patch: after the set the reader sees the value; after the clear it is gone."""
        from prismGui.domain.plan import PlanDraft, apply_patch
        draft = PlanDraft(base_plan_id="t", raw_working_tree=self._tree_with_equipment_and_location())
        assert apply_patch(draft, app_main._equipment_zone_patch(0, "ZONE-A")).ok
        assert app_main._equipment_zone(draft.raw_working_tree, 0) == "ZONE-A"
        assert apply_patch(draft, app_main._equipment_zone_clear_patch(0)).ok
        assert app_main._equipment_zone(draft.raw_working_tree, 0) is None

    def test_task_zones_set_and_clear_patches(self):
        set_op = app_main._task_zones_patch(0, ["ZONE-A", "ZONE-B"])
        assert (set_op.action, set_op.path, set_op.value) == (
            PatchAction.ADD, "/tasks/0/zone_ids", ["ZONE-A", "ZONE-B"])
        clear_op = app_main._task_zones_clear_patch(0)
        assert (clear_op.action, clear_op.path) == (PatchAction.REMOVE, "/tasks/0/zone_ids")

    def test_task_zones_patch_stringifies_entries(self):
        """Every entry is coerced to str — the multiselect yields declared location_id strings,
        but the builder never trusts the caller's element type."""
        op = app_main._task_zones_patch(0, ["ZONE-A", 7])
        assert op.value == ["ZONE-A", "7"]

    def test_task_zones_reader(self):
        tree = self._tree_with_wired_task()
        assert app_main._task_zones(tree, 0) == []       # key absent -> []
        assert app_main._task_zones(tree, 9) == []       # out of range -> []
        tree["tasks"][0]["zone_ids"] = ["ZONE-1", "ZONE-2"]
        assert app_main._task_zones(tree, 0) == ["ZONE-1", "ZONE-2"]

    def test_task_zones_apply_patch_round_trip(self):
        """The whole-list SET (ADD create-branch) then CLEAR (REMOVE) round-trip through
        domain.apply_patch; an absent zone_ids reads as the empty list either side of the clear."""
        from prismGui.domain.plan import PlanDraft, apply_patch
        draft = PlanDraft(base_plan_id="t", raw_working_tree=self._tree_with_wired_task())
        assert apply_patch(draft, app_main._task_zones_patch(0, ["ZONE-1", "ZONE-2"])).ok
        assert app_main._task_zones(draft.raw_working_tree, 0) == ["ZONE-1", "ZONE-2"]
        assert apply_patch(draft, app_main._task_zones_clear_patch(0)).ok
        assert app_main._task_zones(draft.raw_working_tree, 0) == []

    def test_resource_dose_budget_set_and_clear_patches(self):
        """A bare int coerces to float (the 2000 -> 2000.0 convention); the optional pool field
        is ADDed (samples omit it) and cleared via REMOVE."""
        set_op = app_main._resource_dose_budget_patch(0, 2000)
        assert (set_op.action, set_op.path, set_op.value) == (
            PatchAction.ADD, "/resources/0/dose_budget_per_worker_mrem", 2000.0)
        clear_op = app_main._resource_dose_budget_clear_patch(0)
        assert (clear_op.action, clear_op.path) == (
            PatchAction.REMOVE, "/resources/0/dose_budget_per_worker_mrem")

    def test_resource_dose_budget_reader(self, raw_plan):
        assert app_main._resource_dose_budget(raw_plan, 0) is None    # key absent -> None
        assert app_main._resource_dose_budget(raw_plan, 9) is None    # out of range -> None
        raw_plan["resources"][0]["dose_budget_per_worker_mrem"] = 1500.0
        assert app_main._resource_dose_budget(raw_plan, 0) == 1500.0

    def test_task_dose_set_and_clear_patches(self):
        set_op = app_main._task_dose_patch(0, 7.5)
        assert (set_op.action, set_op.path, set_op.value) == (
            PatchAction.ADD, "/tasks/0/dose_rate_mrem_per_hour", 7.5)
        clear_op = app_main._task_dose_clear_patch(0)
        assert (clear_op.action, clear_op.path) == (
            PatchAction.REMOVE, "/tasks/0/dose_rate_mrem_per_hour")

    def test_task_dose_reader(self):
        tree = self._tree_with_wired_task()
        assert app_main._task_dose(tree, 0) is None      # key absent -> None
        assert app_main._task_dose(tree, 9) is None      # out of range -> None
        tree["tasks"][0]["dose_rate_mrem_per_hour"] = 3.25
        assert app_main._task_dose(tree, 0) == 3.25


class TestScenarioPanelBuilders:
    """The streamlit-free scenario-overlay helpers behind the Increment-8 run-panel what-if
    section. Each folds a single what-if into a new frozen ``Scenario`` bound to the baseline
    revision, with no ``st.*`` — the ``_render_scenario_panel`` wrapper is the only ``st.*``
    site. Built off the `baseline` fixture (tasks A,B; one MECH pool). The domain (materialize)
    is the arbiter of validity; these only shape and bind the delta."""

    def test_intent_labels_and_whatif_routing(self):
        """Two intents, what-if first (the default). ``_is_whatif`` recognizes only it — a
        baseline correction is not a what-if (it routes to the editor, staging no overlay)."""
        assert app_main._SCN_INTENTS == ("What-if (scenario)", "Baseline correction")
        assert app_main._is_whatif(app_main._SCN_INTENTS[0]) is True
        assert app_main._is_whatif(app_main._SCN_INTENTS[1]) is False

    def test_scenario_is_empty(self, baseline):
        """None, and a scenario touching nothing, are both empty (the run then uses the plain
        baseline). A single override or resource change makes it non-empty."""
        assert app_main._scenario_is_empty(None) is True
        empty = app_main._new_scenario_for(baseline)
        assert app_main._scenario_is_empty(empty) is True
        with_dur = app_main._add_duration_override(None, baseline, "B", 9.0)
        assert app_main._scenario_is_empty(with_dur) is False
        with_res = app_main._add_resource_change(None, baseline, "MECH", 48.0, 1)
        assert app_main._scenario_is_empty(with_res) is False

    def test_add_duration_override_binds_to_baseline(self, baseline):
        """From no scenario, adding a duration override builds a fresh Scenario bound to THIS
        baseline (base_plan_id + base_plan_hash) carrying the one override."""
        scn = app_main._add_duration_override(None, baseline, "B", 9.0)
        assert scn.base_plan_id == baseline.plan_id
        assert scn.base_plan_hash == baseline.plan_hash
        assert scn.duration_overrides == (DurationOverride(task_id="B", duration_hours=9.0),)

    def test_add_duration_override_is_last_write_wins_per_task(self, baseline):
        """Re-authoring an override for the same task replaces it (never a duplicate) — so the
        UI cannot stage two overrides for one task."""
        scn = app_main._add_duration_override(None, baseline, "B", 9.0)
        scn = app_main._add_duration_override(scn, baseline, "B", 12.0)
        assert scn.duration_overrides == (DurationOverride(task_id="B", duration_hours=12.0),)
        # a different task appends rather than replaces
        scn = app_main._add_duration_override(scn, baseline, "A", 5.0)
        assert {ov.task_id for ov in scn.duration_overrides} == {"A", "B"}

    def test_remove_duration_override_empties_to_none(self, baseline):
        """Removing the only override empties ``duration_overrides`` back to None (the
        overlay-absent shape), so the scenario reads as empty again."""
        scn = app_main._add_duration_override(None, baseline, "B", 9.0)
        scn = app_main._remove_duration_override(scn, baseline, "B")
        assert scn.duration_overrides is None
        assert app_main._scenario_is_empty(scn) is True

    def test_add_resource_change_binds_and_last_write_wins(self, baseline):
        """A resource what-if binds to the baseline and is last-write-wins per
        (skill_type, from_hour) — the UI can never author the duplicate-hour case
        materialize would reject."""
        scn = app_main._add_resource_change(None, baseline, "MECH", 48.0, 1)
        assert scn.base_plan_hash == baseline.plan_hash
        assert scn.resource_changes == (ResourceChange("MECH", 48.0, 1),)
        # same (skill, from_hour) replaces the count
        scn = app_main._add_resource_change(scn, baseline, "MECH", 48.0, 2)
        assert scn.resource_changes == (ResourceChange("MECH", 48.0, 2),)
        # a different hour appends
        scn = app_main._add_resource_change(scn, baseline, "MECH", 72.0, 0)
        assert len(scn.resource_changes) == 2

    def test_remove_resource_change_by_index_empties_to_none(self, baseline):
        """Removing a resource change by its row index drops it; removing the last empties
        ``resource_changes`` back to None."""
        scn = app_main._add_resource_change(None, baseline, "MECH", 48.0, 1)
        scn = app_main._add_resource_change(scn, baseline, "MECH", 72.0, 0)
        scn = app_main._remove_resource_change(scn, baseline, 0)
        assert scn.resource_changes == (ResourceChange("MECH", 72.0, 0),)
        scn = app_main._remove_resource_change(scn, baseline, 0)
        assert scn.resource_changes is None

    def test_scenario_rows_shape(self, baseline):
        """The display/removal row shapers surface each override / change with its row index."""
        scn = app_main._add_duration_override(None, baseline, "B", 9.0)
        scn = app_main._add_resource_change(scn, baseline, "MECH", 48.0, 1)
        assert app_main._scenario_duration_rows(scn) == [
            {"index": 0, "task_id": "B", "duration_hours": 9.0}]
        assert app_main._scenario_resource_rows(scn) == [
            {"index": 0, "skill_type": "MECH", "from_hour": 48.0, "new_count": 1}]
        # None reads as no rows (never crashes)
        assert app_main._scenario_duration_rows(None) == []
        assert app_main._scenario_resource_rows(None) == []

    def test_scenario_bound_to_superseded_revision_is_rebuilt(self, baseline):
        """A scenario bound to a different (superseded) baseline revision is rebuilt against
        the current baseline when extended — its stale overrides never leak across the change,
        and the new scenario carries the current base_plan_hash."""
        stale = Scenario(
            scenario_id="scn-old", base_plan_id="old", base_plan_hash="f" * 64,
            duration_overrides=(DurationOverride(task_id="A", duration_hours=99.0),))
        assert stale.base_plan_hash != baseline.plan_hash
        rebuilt = app_main._add_duration_override(stale, baseline, "B", 9.0)
        assert rebuilt.base_plan_hash == baseline.plan_hash
        # the stale override for A is gone; only the freshly-authored one remains
        assert rebuilt.duration_overrides == (DurationOverride(task_id="B", duration_hours=9.0),)


class TestStageBScheduleHelpers:
    """The streamlit-free helpers Stage B adds for the multi-scenario workflow: ``_mint_scenario``
    (a NEW named scenario with a UNIQUE id, so several coexist — unlike ``_new_scenario_for``'s
    fixed per-baseline id) and ``_current_schedule_payload`` (the effective plan payload the Data
    viewer / activity graph render for whichever schedule is current). Built off the `baseline`
    fixture (tasks A dur 4, B dur 6; one MECH pool). No ``st.*``."""

    def test_mint_scenario_gives_unique_ids_and_binds_to_baseline(self, baseline):
        """Each mint yields a distinct ``scn-…`` id (so scenarios never overwrite each other)
        bound to THIS baseline revision, and starts empty (no overlay staged yet)."""
        a = app_main._mint_scenario(baseline)
        b = app_main._mint_scenario(baseline, existing_ids=(a.scenario_id,))
        assert a.scenario_id != b.scenario_id
        assert a.scenario_id.startswith("scn-") and b.scenario_id.startswith("scn-")
        for scn in (a, b):
            assert scn.base_plan_id == baseline.plan_id
            assert scn.base_plan_hash == baseline.plan_hash
            assert app_main._scenario_is_empty(scn) is True

    def test_mint_scenario_auto_names_by_existing_count(self, baseline):
        """With no explicit name the auto label follows how many scenarios already exist —
        the first is "Scenario A", the second "Scenario B", …."""
        first = app_main._mint_scenario(baseline, existing_ids=())
        assert first.name == "Scenario A"
        second = app_main._mint_scenario(baseline, existing_ids=("scn-x",))
        assert second.name == "Scenario B"
        # past the alphabet it falls back to a number rather than a non-letter glyph
        far = app_main._mint_scenario(baseline, existing_ids=tuple(f"scn-{i}" for i in range(26)))
        assert far.name == "Scenario 27"

    def test_mint_scenario_honours_an_explicit_name(self, baseline):
        """A caller-supplied name wins over the auto label."""
        scn = app_main._mint_scenario(baseline, name="Winter re-plan")
        assert scn.name == "Winter re-plan"

    def test_mint_scenario_avoids_a_clashing_id(self, baseline):
        """``existing_ids`` guards the (vanishingly unlikely) id clash — the minted id is
        never one already taken."""
        taken = tuple(f"scn-{i:08x}" for i in range(50))
        scn = app_main._mint_scenario(baseline, existing_ids=taken)
        assert scn.scenario_id not in taken

    def test_current_schedule_payload_none_is_the_plain_baseline(self, baseline):
        """No current scenario (Baseline is current) → the plain baseline payload, no warning."""
        payload, warning = app_main._current_schedule_payload(baseline, None)
        assert warning is None
        durations = {t["task_id"]: t["duration"] for t in payload["tasks"]}
        assert durations == {"A": 4, "B": 6}

    def test_current_schedule_payload_materializes_a_valid_overlay(self, baseline):
        """A valid duration-override scenario → the payload with the overlay folded in (B's
        duration is the override, 9.0, not the baseline's 6), no warning."""
        scn = app_main._add_duration_override(None, baseline, "B", 9.0)
        payload, warning = app_main._current_schedule_payload(baseline, scn)
        assert warning is None
        durations = {t["task_id"]: t["duration"] for t in payload["tasks"]}
        assert durations["B"] == 9.0
        assert durations["A"] == 4               # untouched tasks pass through unchanged

    def test_current_schedule_payload_falls_back_to_baseline_on_stale_scenario(self, baseline):
        """A scenario bound to a superseded revision can't be materialized (a hash mismatch),
        so the plain baseline payload is returned WITH a warning — the caller still renders."""
        stale = Scenario(
            scenario_id="scn-stale", base_plan_id="old", base_plan_hash="f" * 64,
            duration_overrides=(DurationOverride(task_id="B", duration_hours=99.0),))
        assert stale.base_plan_hash != baseline.plan_hash
        payload, warning = app_main._current_schedule_payload(baseline, stale)
        assert warning is not None                # a human-readable fallback notice
        durations = {t["task_id"]: t["duration"] for t in payload["tasks"]}
        assert durations["B"] == 6                # the stale 99.0 override never leaked in


class TestRelationGraphData:
    """The streamlit-free builder behind the Graphs tab's baseline → scenarios picture. Pure
    node/edge/position data (a manual star) — the ``_render_relation_graph`` wrapper is the only
    ``st.*``/Plotly site. Built off the `baseline` fixture."""

    def test_baseline_only_is_a_single_current_node(self, baseline):
        """No scenarios → just the baseline node, flagged current (None pointer == baseline),
        no edges."""
        data = app_main._relation_graph_data(baseline, (), None)
        assert len(data["nodes"]) == 1 and data["edges"] == []
        node = data["nodes"][0]
        assert node["kind"] == "baseline" and node["is_current"] is True
        assert node["label"] == baseline.plan_id

    def test_one_edge_per_scenario_and_exactly_one_current(self, baseline):
        """Each scenario adds a node and a baseline→scenario edge; the pointer flags exactly
        one node current, and each scenario node carries its staged overlay count."""
        a = app_main._mint_scenario(baseline, name="A")
        b = app_main._add_duration_override(
            app_main._mint_scenario(baseline, existing_ids=(a.scenario_id,), name="B"),
            baseline, "B", 9.0)                       # b carries one duration override
        data = app_main._relation_graph_data(baseline, (a, b), b.scenario_id)

        assert len(data["nodes"]) == 3                # baseline + 2 scenarios
        assert len(data["edges"]) == 2
        assert all(src == app_main._BASELINE_NODE_ID for src, _ in data["edges"])
        assert {dst for _, dst in data["edges"]} == {a.scenario_id, b.scenario_id}

        by_id = {n["id"]: n for n in data["nodes"]}
        assert [n["id"] for n in data["nodes"] if n["is_current"]] == [b.scenario_id]
        assert by_id[app_main._BASELINE_NODE_ID]["is_current"] is False
        assert by_id[a.scenario_id]["overlay_count"] == 0
        assert by_id[b.scenario_id]["overlay_count"] == 1


class TestActivityGraphData:
    """The streamlit-free builder behind the Graphs tab's activity/dependency DAG. Nodes =
    tasks, edges = precedence links (via ``_dependency_options``), positions = a longest-path
    layered layout. Built off the `baseline` fixture's tiny A→B chain."""

    def test_nodes_edges_and_layered_depth(self, baseline):
        """The 2-task A→B baseline yields two nodes and the single A→B edge, with B one depth
        layer to the right of A (x = longest-path depth), and no cycle."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        data = app_main._activity_graph_data(raw)
        assert data["has_cycle"] is False
        assert {n["id"] for n in data["nodes"]} == {"A", "B"}
        assert data["edges"] == [("A", "B")]
        by_id = {n["id"]: n for n in data["nodes"]}
        assert by_id["A"]["depth"] == 0 and by_id["B"]["depth"] == 1
        assert by_id["B"]["x"] > by_id["A"]["x"]      # laid out left→right by depth
        assert by_id["A"]["duration"] == 4 and by_id["B"]["duration"] == 6

    def test_edges_to_unknown_tasks_are_dropped(self, baseline):
        """A successor naming a task not in the plan is not drawn (no dangling edge / KeyError)
        — the builder never trusts an edge endpoint it has no node for."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        raw["tasks"][0]["successors"] = ["B", "GHOST"]
        data = app_main._activity_graph_data(raw)
        assert data["edges"] == [("A", "B")]          # the GHOST edge is dropped
        assert {n["id"] for n in data["nodes"]} == {"A", "B"}

    def test_cycle_degrades_without_raising(self, baseline):
        """A cycle (A→B→A) is reported via ``has_cycle`` and the builder still returns every
        node/edge rather than raising — a valid baseline is acyclic, but the graph never crashes
        the tab on a mid-edit tree."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        raw["tasks"][0]["successors"] = ["B"]
        raw["tasks"][1]["successors"] = ["A"]
        data = app_main._activity_graph_data(raw)
        assert data["has_cycle"] is True
        assert {n["id"] for n in data["nodes"]} == {"A", "B"}
        assert set(data["edges"]) == {("A", "B"), ("B", "A")}


class TestActivityGraphEnriched:
    """The streamlit-free builder behind the RUN-AWARE activity DAG: it overlays a selected run's
    analytics (chain coloring, CPM ES/LS/slack, CPM-critical / constrained flags, resource-
    contention arcs) onto the same structural nodes/edges as ``_activity_graph_data``, keyed by
    task_id. Built off the `baseline` A→B chain with a hand-built ScheduleDTO."""

    @staticmethod
    def _schedule():
        # A on the constrained chain (critical/red); B off-chain with positive float (green).
        a = ScheduledActivityDTO(
            task_id="A", start_hour=0.0, end_hour=4.0, duration=4.0, delay_hours=0.0,
            on_constrained_chain=True, float_class=classify_float(0.0, True),
            es_hours=0.0, ls_hours=0.0, cpm_slack_hours=0.0)
        b = ScheduledActivityDTO(
            task_id="B", start_hour=4.0, end_hour=10.0, duration=6.0, delay_hours=0.0,
            on_constrained_chain=False, float_class=classify_float(5.0, False),
            es_hours=4.0, ls_hours=6.0, cpm_slack_hours=2.0)
        return ScheduleDTO(
            makespan_hours=10.0, cpm_lower_bound_hours=10.0, optimism_gap_hours=0.0,
            activities=(a, b), constrained_chain=("A",), cpm_critical_path=("A",),
            contention_edges=(("A", "B"), ("B", "GHOST")))

    def test_color_by_float_class(self):
        """``_dag_node_color`` maps each float class to the Gantt's shared color; None → grey."""
        assert app_main._dag_node_color(FloatClass.CRITICAL) == "#e74c3c"
        assert app_main._dag_node_color(FloatClass.ZERO_FLOAT) == "#f39c12"
        assert app_main._dag_node_color(FloatClass.POSITIVE_FLOAT) == "#2ecc71"
        assert app_main._dag_node_color(None) == "#7f8c8d"

    def test_enriched_nodes_carry_color_cpm_and_flags(self, baseline):
        """Each node gains the run overlay keyed by task_id: chain color, cpm_critical membership
        (from ``cpm_critical_path``), the constrained flag, and the CPM ES/LS/slack passthrough."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        data = app_main._activity_graph_enriched(raw, self._schedule(), layer_by="topo")
        assert data["enriched"] is True and data["layer_by"] == "topo"
        by_id = {n["id"]: n for n in data["nodes"]}
        assert by_id["A"]["color"] == "#e74c3c" and by_id["A"]["cpm_critical"] is True
        assert by_id["A"]["on_constrained_chain"] is True and by_id["A"]["scheduled"] is True
        assert by_id["B"]["color"] == "#2ecc71" and by_id["B"]["cpm_critical"] is False
        assert by_id["B"]["es_hours"] == 4.0 and by_id["B"]["ls_hours"] == 6.0
        assert by_id["B"]["cpm_slack_hours"] == 2.0

    def test_layer_by_es_and_ls_set_x_from_cpm(self, baseline):
        """``layer_by`` moves the x axis onto the CPM time values; ``topo`` keeps longest-path depth."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        by_es = {n["id"]: n for n in
                 app_main._activity_graph_enriched(raw, self._schedule(), layer_by="es")["nodes"]}
        assert by_es["A"]["x"] == 0.0 and by_es["B"]["x"] == 4.0        # x = ES
        by_ls = {n["id"]: n for n in
                 app_main._activity_graph_enriched(raw, self._schedule(), layer_by="ls")["nodes"]}
        assert by_ls["A"]["x"] == 0.0 and by_ls["B"]["x"] == 6.0        # x = LS
        by_topo = {n["id"]: n for n in
                   app_main._activity_graph_enriched(raw, self._schedule(), layer_by="topo")["nodes"]}
        assert by_topo["B"]["x"] == by_topo["B"]["depth"]              # x = longest-path depth

    def test_contention_edges_filtered_to_known_endpoints(self, baseline):
        """Resource-contention arcs are drawn only when both endpoints are nodes (the ``GHOST``
        arc is dropped, exactly like a dangling precedence edge)."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        data = app_main._activity_graph_enriched(raw, self._schedule(), layer_by="topo")
        assert data["contention_edges"] == [("A", "B")]

    def test_cpm_path_edges_consecutive_pairs(self):
        """The pure CPM-path edge builder yields consecutive (pred, succ) pairs; a path shorter
        than two nodes has no edges. No id filtering (the enriched builder guards that)."""
        assert app_main._cpm_path_edges(("A", "B", "C")) == [("A", "B"), ("B", "C")]
        assert app_main._cpm_path_edges(("A",)) == []
        assert app_main._cpm_path_edges(()) == []

    def test_cpm_path_label_joins_with_arrow(self):
        """The header readout renders the ordered CPM path as ``A → B → C``; empty → ``""``."""
        assert app_main._cpm_path_label(("A", "B", "C")) == "A → B → C"
        assert app_main._cpm_path_label(()) == ""

    def test_enriched_carries_cpm_path_edges_filtered(self, baseline):
        """The enriched graph exposes ``cpm_path_edges`` for the DAG's gold overlay, filtered to
        known nodes so a synthetic CPM START/END id (``GHOST``) drops its touching segment — the
        same guard the contention overlay uses."""
        from dataclasses import replace
        raw = json.loads(baseline.raw_snapshot)["payload"]
        sched = replace(self._schedule(), cpm_critical_path=("A", "B", "GHOST"))
        data = app_main._activity_graph_enriched(raw, sched, layer_by="topo")
        assert data["cpm_path_edges"] == [("A", "B")]

    def test_unscheduled_task_stays_structural(self, baseline):
        """A node with no matching DTO keeps structural defaults (grey, ``scheduled=False``) rather
        than raising — the DTO's synthetic START/END and un-timed tasks never crash the merge; an
        es/ls layout falls back to that node's topo depth."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        a = ScheduledActivityDTO(
            task_id="A", start_hour=0.0, end_hour=4.0, duration=4.0, delay_hours=0.0,
            on_constrained_chain=True, float_class=classify_float(0.0, True),
            es_hours=0.0, ls_hours=0.0, cpm_slack_hours=0.0)
        sched = ScheduleDTO(makespan_hours=4.0, cpm_lower_bound_hours=4.0, optimism_gap_hours=0.0,
                            activities=(a,), constrained_chain=("A",), cpm_critical_path=("A",))
        data = app_main._activity_graph_enriched(raw, sched, layer_by="es")
        by_id = {n["id"]: n for n in data["nodes"]}
        assert by_id["B"]["scheduled"] is False and by_id["B"]["color"] == "#7f8c8d"
        assert by_id["B"]["x"] == by_id["B"]["depth"]      # no ES → es-layout falls back to depth

    def test_hover_scheduled_node_labels_cpm_and_flags(self, baseline):
        """A scheduled node's tooltip carries the CPM timing (labeled '(CPM)' to separate it from
        the wall-clock start/end), the float class + actual TF, and the CPM/Constrained flags."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        data = app_main._activity_graph_enriched(raw, self._schedule(), layer_by="topo")
        by_id = {n["id"]: n for n in data["nodes"]}
        hov = app_main._dag_hover(by_id["A"])
        assert "ES (CPM)" in hov and "LS (CPM)" in hov and "slack" in hov
        assert "wall-clock" in hov                     # start/end line, distinct from CPM
        assert "critical" in hov                       # float_class value
        assert "CPM-critical" in hov and "Constrained" in hov   # A is both
        # B is off-chain and not CPM-critical → neither flag appears.
        assert "Constrained" not in app_main._dag_hover(by_id["B"])

    def test_hover_structural_node_falls_back_to_duration_depth(self, baseline):
        """A pre-run / unscheduled node (no DTO) keeps the plain duration/depth tooltip — no CPM
        line, no float — so the structural graph reads exactly as it did before enrichment."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        structural = app_main._activity_graph_data(raw)["nodes"][0]
        hov = app_main._dag_hover(structural)
        assert "duration:" in hov and "depth:" in hov
        assert "CPM" not in hov and "float:" not in hov


class TestTaskInspector:
    """The three streamlit-free builders behind the per-task inspector ("why isn't this task
    starting sooner?"): ``_task_slip`` (lateness-vs-CPM-early-start decomposition), ``_task_neighbors``
    (predecessor/successor slack attribution off the plan's edges + the run's timing), and
    ``_saturated_skills`` (an AGGREGATE resource-pressure readout — deliberately NOT a per-task
    binding-resource attribution). All pure: stdlib + domain DTOs only, no ``st``, exercised through
    ``app_main`` off the ``run_result`` + ``baseline`` fixtures (A→B chain, A finishes at 4, B starts
    at 4). See the plan: the named binding resource stays a deferred Tier-B item."""

    def test_slip_zero_when_on_time(self, run_result):
        """A task that starts at its CPM early start with no resource delay decomposes to all zeros."""
        a = next(x for x in run_result.schedule.activities if x.task_id == "A")
        assert app_main._task_slip(a) == {
            "lateness_vs_es": 0.0, "contention_delay": 0.0, "other_gating": 0.0}

    def test_slip_splits_contention_from_other_gating(self):
        """Total lateness (start − ES) splits into the resource-contention portion (``delay_hours``)
        and the remainder (predecessor/window/calendar gating): start 10, ES 4, delay 2 → 6 h late =
        2 h contention + 4 h other gating."""
        act = ScheduledActivityDTO(
            task_id="X", start_hour=10.0, end_hour=16.0, duration=6.0, delay_hours=2.0,
            on_constrained_chain=False, float_class=classify_float(3.0, False), es_hours=4.0)
        assert app_main._task_slip(act) == {
            "lateness_vs_es": 6.0, "contention_delay": 2.0, "other_gating": 4.0}

    def test_slip_none_safe_without_cpm_early_start(self):
        """No CPM early start (``es_hours is None``) → lateness/other unavailable (None); the
        contention portion is still the raw ``delay_hours``."""
        act = ScheduledActivityDTO(
            task_id="X", start_hour=10.0, end_hour=16.0, duration=6.0, delay_hours=2.0,
            on_constrained_chain=False, float_class=classify_float(3.0, False))  # es_hours defaults None
        assert app_main._task_slip(act) == {
            "lateness_vs_es": None, "contention_delay": 2.0, "other_gating": None}

    def test_slip_other_gating_clamped_at_zero(self):
        """When ``delay_hours`` exceeds total lateness (calendar rounding), ``other_gating`` clamps at
        0 rather than going negative."""
        act = ScheduledActivityDTO(
            task_id="X", start_hour=5.0, end_hour=9.0, duration=4.0, delay_hours=7.0,
            on_constrained_chain=False, float_class=classify_float(0.0, False), es_hours=4.0)
        slip = app_main._task_slip(act)
        assert slip["lateness_vs_es"] == 1.0 and slip["contention_delay"] == 7.0
        assert slip["other_gating"] == 0.0

    def test_neighbors_predecessor_attribution_and_driver(self, baseline, run_result):
        """For B, the sole predecessor A is listed with its lag/finish/CPM-slack/TF/float and flagged
        as the finish-driving predecessor (``fs_ready = end + lag = 4``); B has no successors."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        n = app_main._task_neighbors(raw, run_result.schedule, "B")
        assert n["successors"] == []
        assert n["predecessors"] == [{
            "task_id": "A", "lag_hours": 0.0, "end_hour": 4.0, "fs_ready": 4.0,
            "cpm_slack_hours": 0.0, "tf_actual_hours": 0.0, "float_class": "critical",
            "is_driver": True}]

    def test_neighbors_successor_attribution(self, baseline, run_result):
        """For A, the sole successor B is listed with its lag/start/CPM-slack/TF/float; A has no
        predecessors."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        n = app_main._task_neighbors(raw, run_result.schedule, "A")
        assert n["predecessors"] == []
        assert n["successors"] == [{
            "task_id": "B", "lag_hours": 0.0, "start_hour": 4.0, "cpm_slack_hours": 0.0,
            "tf_actual_hours": 0.0, "float_class": "critical"}]

    def test_neighbors_untimed_predecessor_is_none_and_not_driver(self, baseline):
        """A predecessor with no matching scheduled DTO is still listed, with None timing and never
        the driver flag (an unknown finish can't be the finish that gated eligibility)."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        empty = ScheduleDTO(makespan_hours=0.0, cpm_lower_bound_hours=0.0, optimism_gap_hours=0.0,
                            activities=(), constrained_chain=())
        n = app_main._task_neighbors(raw, empty, "B")
        assert n["predecessors"] == [{
            "task_id": "A", "lag_hours": 0.0, "end_hour": None, "fs_ready": None,
            "cpm_slack_hours": None, "tf_actual_hours": None, "float_class": None,
            "is_driver": False}]

    @staticmethod
    def _util():
        # MECH: 0-4 h has slack (demand 2 of 4); 4-6 h is saturated (demand 5 of 5).
        return ResourceUtilizationDTO(horizon_hours=6.0, series=(
            SkillUtilizationSeries(skill_type="MECH", intervals=(
                UtilizationInterval(start_hour=0.0, end_hour=4.0, demand=2, available=4),
                UtilizationInterval(start_hour=4.0, end_hour=6.0, demand=5, available=5))),))

    def test_saturated_skills_reports_pools_at_capacity_over_window(self):
        """A pool counts when one of its intervals overlaps [lo, hi) with demand ≥ available: the
        saturated 4-6 h interval surfaces MECH over (4, 6)."""
        assert app_main._saturated_skills(self._util(), 4.0, 6.0) == ["MECH"]

    def test_saturated_skills_empty_when_window_only_hits_slack(self):
        """Over (0, 4) only the non-saturated interval overlaps → nothing reported (half-open: the
        4-6 interval starts exactly at hi=4 and does not count)."""
        assert app_main._saturated_skills(self._util(), 0.0, 4.0) == []

    def test_saturated_skills_empty_for_nonoverlapping_window_or_no_util(self):
        """A window past the horizon overlaps no interval, and a missing utilization DTO yields []."""
        assert app_main._saturated_skills(self._util(), 20.0, 24.0) == []
        assert app_main._saturated_skills(None, 0.0, 6.0) == []


class TestModeOptions:
    """The streamlit-free builder behind the run-time execution-mode picker. It offers only
    tasks that define MORE THAN ONE mode (mode_name == the schema's ``mode_id``); the render
    helper turns each row into a per-task selectbox and merges the picks into the RunConfig."""

    def test_empty_when_no_task_defines_multiple_modes(self, baseline):
        """The shipping-sample case: no task carries a multi-mode ``modes`` list, so there is
        nothing to pick (the picker shows its 'no multi-mode tasks' caption)."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        assert app_main._mode_options(raw) == []

    def test_single_mode_task_is_not_offered(self, baseline):
        """A task with exactly one mode offers no choice, so it is omitted (only >1 counts)."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        raw["tasks"][0]["modes"] = [{"mode_id": "normal", "duration": 4}]
        assert app_main._mode_options(raw) == []

    def test_multi_mode_task_yields_its_selectable_modes(self, baseline):
        """A task defining two+ modes is offered with each mode's name (its ``mode_id``) and
        duration — the exact rows the picker builds a selectbox from, and whose ``mode_name``s
        ``ModeSelection`` / ``validate_run_config`` accept."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        raw["tasks"][0]["modes"] = [
            {"mode_id": "normal", "duration": 4, "required_resources": []},
            {"mode_id": "crash", "duration": 2, "required_resources": []},
        ]
        options = app_main._mode_options(raw)
        assert len(options) == 1
        row = options[0]
        assert row["task_id"] == "A"
        assert [m["mode_name"] for m in row["modes"]] == ["normal", "crash"]
        assert [m["duration"] for m in row["modes"]] == [4, 2]
