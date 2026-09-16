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
import dataclasses
import io
import json
from datetime import datetime, timezone

from prismGui.app import main as app_main
from prismGui.domain.issues import IssueCode, Severity
from prismGui.domain.plan import PatchAction
from prismGui.domain.results import (
    DiagnosticsDTO,
    FitnessDTO,
    FloatClass,
    Freshness,
    Provenance,
    ResourceUtilizationDTO,
    RunResult,
    RunResultStatus,
    ScheduledActivityDTO,
    ScheduleDTO,
    SkillUtilizationSeries,
    UtilizationInterval,
    classify_float,
)
from prismGui.domain.disposition import ScheduleSummary, compute_disposition
from prismGui.domain.hashing import hash_scenario, scenario_payload
from prismGui.domain.replan import build_replan_inputs, replan_preflight
from prismGui.domain.scenario import (
    DependencySuppression, DurationOverride, EquipmentChange, HoldPointReleaseOverride,
    LocationChange, ResourceChange, Scenario, TaskSuppression,
)
from prismGui.domain.plan import Dependency, Task
from prismGui.domain.versions import APP_VERSION, CANON_VERSION, SCHEMA_VERSION


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

    def test_windows_modes_demo_is_valid_and_exercises_both_features(self):
        """The review-enablement demo sample is discovered, schema-valid, and actually
        declares BOTH newest-feature shapes so neither Phase-4 view demos as an empty
        state: ≥1 task with a regulatory time window (Results → Time windows) and ≥1
        multi-mode task (Results → Sweep → Modes). Pure — validator + payload inspection
        via the GUI's own predicate, no PRISM run. Deliberately NON-BRITTLE: it pins no
        makespan / task-count / window-bound (those numbers live on example_10 and the
        smoke oracle); it guards only presence, so tuning the demo's timing never breaks it."""
        samples = app_main.discover_samples()
        assert "example_windows_modes" in samples
        with open(samples["example_windows_modes"], encoding="utf-8") as fh:
            payload = json.load(fh)

        # schema-valid with no ERROR-severity issue (warnings are allowed through)
        load = app_main.services.load_and_validate(
            "example_windows_modes", payload, app_main.build_validator())
        assert load.ok, [i.message for i in load.issues]

        # ≥1 windowed task -> the Time-windows pre-flight has content to render
        windowed = [t for t in payload["tasks"] if t.get("time_windows")]
        assert windowed, "demo must declare at least one task with time_windows"

        # ≥1 multi-mode task -> the Mode-sweep axis has a choice to sweep (the GUI's own
        # `len(modes) > 1` predicate, so this tracks exactly what the picker will register)
        assert app_main._mode_options(payload), "demo must declare at least one multi-mode task"


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

    def test_provenance_rows_include_as_of_hour_for_replan(self, run_result):
        """A replan provenance (``checkpoint_hour`` set) appends an 11th 'As-of hour' row; a
        normal run (``checkpoint_hour`` None, the fixture default) keeps its ten. The row is
        conditional, so every existing from-hour-0 run is unchanged (the ten-fields test above
        stays green)."""
        assert run_result.provenance.checkpoint_hour is None
        assert len(app_main._provenance_rows(run_result.provenance)) == 10   # normal run
        replan_prov = dataclasses.replace(run_result.provenance, checkpoint_hour=40.0)
        rows = app_main._provenance_rows(replan_prov)
        assert len(rows) == 11
        assert all(set(r) == {"field", "value"} for r in rows)
        asof = next(r for r in rows if r["field"] == "As-of hour")
        assert asof["value"] == "40 h"                       # {:g} drops the trailing .0

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
        baseline). A single delta of ANY family makes it non-empty — checking only a subset
        would let an equipment/location-only overlay silently run the plain baseline."""
        assert app_main._scenario_is_empty(None) is True
        empty = app_main._new_scenario_for(baseline)
        assert app_main._scenario_is_empty(empty) is True
        with_dur = app_main._add_duration_override(None, baseline, "B", 9.0)
        assert app_main._scenario_is_empty(with_dur) is False
        with_res = app_main._add_resource_change(None, baseline, "MECH", 48.0, 1)
        assert app_main._scenario_is_empty(with_res) is False
        with_eqp = app_main._add_equipment_change(None, baseline, "CRANE", 48.0, 1)
        assert app_main._scenario_is_empty(with_eqp) is False
        with_loc = app_main._add_location_change(None, baseline, "BAY1", 48.0, 1)
        assert app_main._scenario_is_empty(with_loc) is False
        # Increment-C activity families: an add-only or a remove-only overlay is NOT empty
        with_task = app_main._add_emergent_task(None, baseline, "E1", 3.0)
        assert app_main._scenario_is_empty(with_task) is False
        with_dep = app_main._add_emergent_dependency(None, baseline, "A", "B")
        assert app_main._scenario_is_empty(with_dep) is False
        with_tsup = app_main._add_task_suppression(None, baseline, "A")
        assert app_main._scenario_is_empty(with_tsup) is False
        with_dsup = app_main._add_dependency_suppression(None, baseline, "A", "B")
        assert app_main._scenario_is_empty(with_dsup) is False

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

    def test_add_resource_change_carries_bounded_window(self, baseline):
        """A resource what-if can carry a bounded ``[from, to)`` window: ``to_hour`` rides onto
        the ``ResourceChange`` (None stays open-ended). Last-write-wins is still keyed on
        (skill_type, from_hour) only, so re-authoring the same start replaces the window too."""
        scn = app_main._add_resource_change(None, baseline, "MECH", 24.0, 1, to_hour=72.0)
        assert scn.resource_changes == (ResourceChange("MECH", 24.0, 1, to_hour=72.0),)
        # re-authoring the same (skill, from_hour) replaces the window, never duplicates
        scn = app_main._add_resource_change(scn, baseline, "MECH", 24.0, 0)
        assert scn.resource_changes == (ResourceChange("MECH", 24.0, 0, to_hour=None),)

    def test_add_remove_equipment_change(self, baseline):
        """Equipment what-ifs bind to the baseline, are last-write-wins per (equipment_id,
        from_hour) with an optional window, and remove-by-index empties back to None."""
        scn = app_main._add_equipment_change(None, baseline, "CRANE", 24.0, 0, to_hour=72.0)
        assert scn.base_plan_hash == baseline.plan_hash
        assert scn.equipment_changes == (EquipmentChange("CRANE", 24.0, 0, to_hour=72.0),)
        # same (id, from_hour) replaces
        scn = app_main._add_equipment_change(scn, baseline, "CRANE", 24.0, 1)
        assert scn.equipment_changes == (EquipmentChange("CRANE", 24.0, 1, to_hour=None),)
        # a different hour appends, then remove-by-index drops it
        scn = app_main._add_equipment_change(scn, baseline, "CRANE", 80.0, 2)
        assert len(scn.equipment_changes) == 2
        scn = app_main._remove_equipment_change(scn, baseline, 1)
        assert scn.equipment_changes == (EquipmentChange("CRANE", 24.0, 1, to_hour=None),)
        scn = app_main._remove_equipment_change(scn, baseline, 0)
        assert scn.equipment_changes is None

    def test_add_remove_location_change(self, baseline):
        """Location what-ifs bind to the baseline; a None worker cap is preserved as None (leave
        the baseline cap untouched), a value rides through. Last-write-wins per (location_id,
        from_hour); remove-by-index empties back to None."""
        scn = app_main._add_location_change(None, baseline, "BAY1", 24.0, 1)
        assert scn.location_changes == (
            LocationChange("BAY1", 24.0, 1, to_hour=None, new_max_concurrent_workers=None),)
        # both caps + window, replacing the same (id, from_hour)
        scn = app_main._add_location_change(scn, baseline, "BAY1", 24.0, 2, to_hour=72.0,
                                            new_max_concurrent_workers=3)
        assert scn.location_changes == (
            LocationChange("BAY1", 24.0, 2, to_hour=72.0, new_max_concurrent_workers=3),)
        scn = app_main._remove_location_change(scn, baseline, 0)
        assert scn.location_changes is None

    def test_equipment_and_location_rows_shape(self, baseline):
        """The equipment / location row shapers surface each change with its row index, window
        bound, and value(s) — the shape the Replan panel renders and removes by."""
        scn = app_main._add_equipment_change(None, baseline, "CRANE", 24.0, 0, to_hour=72.0)
        scn = app_main._add_location_change(scn, baseline, "BAY1", 12.0, 1,
                                            new_max_concurrent_workers=2)
        assert app_main._scenario_equipment_rows(scn) == [
            {"index": 0, "equipment_id": "CRANE", "from_hour": 24.0, "to_hour": 72.0,
             "new_quantity": 0}]
        assert app_main._scenario_location_rows(scn) == [
            {"index": 0, "location_id": "BAY1", "from_hour": 12.0, "to_hour": None,
             "new_max_concurrent_tasks": 1, "new_max_concurrent_workers": 2}]
        # None reads as no rows (never crashes)
        assert app_main._scenario_equipment_rows(None) == []
        assert app_main._scenario_location_rows(None) == []

    def test_scenario_rows_shape(self, baseline):
        """The display/removal row shapers surface each override / change with its row index.
        A resource row carries ``to_hour`` — None for an open-ended change, the final hour for a
        bounded ``[from, to)`` window — so the panel can render the window it authored."""
        scn = app_main._add_duration_override(None, baseline, "B", 9.0)
        scn = app_main._add_resource_change(scn, baseline, "MECH", 48.0, 1)          # open-ended
        scn = app_main._add_resource_change(scn, baseline, "MECH", 72.0, 0, to_hour=96.0)  # bounded
        assert app_main._scenario_duration_rows(scn) == [
            {"index": 0, "task_id": "B", "duration_hours": 9.0}]
        assert app_main._scenario_resource_rows(scn) == [
            {"index": 0, "skill_type": "MECH", "from_hour": 48.0, "to_hour": None, "new_count": 1},
            {"index": 1, "skill_type": "MECH", "from_hour": 72.0, "to_hour": 96.0, "new_count": 0}]
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

    # --- Increment-C activity what-if helpers: emergent add + suppression remove ------------

    def test_add_emergent_task_shapes_and_forces_no_hold_point(self, baseline):
        """An emergent task binds to the baseline and carries exactly the fields materialize
        writes; ``hold_point`` is forced None (so materialize writes ``is_hold_point:False`` and no
        ``hold_point_type``). required_resources / required_equipment come from (skill,count) /
        (id,qty) pairs; last-write-wins per ``task_id``."""
        scn = app_main._add_emergent_task(
            None, baseline, "E1", 3.0, description="emergent", location_id="BAY1",
            required_resources=[("MECH", 2)], required_equipment=[("CRANE", 1)])
        assert scn.base_plan_hash == baseline.plan_hash
        (t,) = scn.emergent_tasks
        assert t.task_id == "E1" and t.duration == 3.0 and t.description == "emergent"
        assert t.location_id == "BAY1" and t.hold_point is None
        assert [(r.skill_type, r.crew_count) for r in t.required_resources] == [("MECH", 2)]
        assert [(e.equipment_id, e.quantity_needed) for e in t.required_equipment] == [("CRANE", 1)]
        # re-authoring the same id replaces (never a silent duplicate)
        scn = app_main._add_emergent_task(scn, baseline, "E1", 8.0)
        assert len(scn.emergent_tasks) == 1 and scn.emergent_tasks[0].duration == 8.0

    def test_remove_emergent_task_empties_to_none(self, baseline):
        """Removing the only emergent task empties ``emergent_tasks`` back to None."""
        scn = app_main._add_emergent_task(None, baseline, "E1", 3.0)
        scn = app_main._remove_emergent_task(scn, baseline, "E1")
        assert scn.emergent_tasks is None
        assert app_main._scenario_is_empty(scn) is True

    def test_emergent_task_rows_shape(self, baseline):
        """The emergent-task row shaper surfaces each added task with its nested requirement rows."""
        scn = app_main._add_emergent_task(
            None, baseline, "E1", 3.0, required_resources=[("MECH", 2)])
        assert app_main._scenario_emergent_task_rows(scn) == [
            {"index": 0, "task_id": "E1", "description": None, "duration": 3.0,
             "location_id": None, "required_resources": [{"skill_type": "MECH", "crew_count": 2}],
             "required_equipment": []}]
        assert app_main._scenario_emergent_task_rows(None) == []

    def test_add_remove_emergent_dependency(self, baseline):
        """Emergent dependencies bind to the baseline, are last-write-wins per
        (predecessor, successor) with an optional lag, and remove-by-index empties back to None."""
        scn = app_main._add_emergent_dependency(None, baseline, "A", "E1", lag_hours=2.0)
        assert scn.emergent_dependencies == (Dependency("A", "E1", lag_hours=2.0),)
        # same endpoints replace (updates the lag), never duplicate
        scn = app_main._add_emergent_dependency(scn, baseline, "A", "E1")
        assert scn.emergent_dependencies == (Dependency("A", "E1", lag_hours=0.0),)
        # a different edge appends, then remove-by-index drops it
        scn = app_main._add_emergent_dependency(scn, baseline, "B", "E1")
        assert len(scn.emergent_dependencies) == 2
        scn = app_main._remove_emergent_dependency(scn, baseline, 1)
        assert scn.emergent_dependencies == (Dependency("A", "E1", lag_hours=0.0),)
        assert app_main._scenario_emergent_dependency_rows(scn) == [
            {"index": 0, "predecessor_id": "A", "successor_id": "E1", "lag_hours": 0.0}]

    def test_add_remove_task_suppression_is_idempotent(self, baseline):
        """Suppressing a task binds to the baseline and is idempotent (never listed twice);
        lifting it (by task_id) empties ``task_suppressions`` back to None."""
        scn = app_main._add_task_suppression(None, baseline, "A")
        assert scn.task_suppressions == (TaskSuppression("A"),)
        scn = app_main._add_task_suppression(scn, baseline, "A")          # idempotent
        assert scn.task_suppressions == (TaskSuppression("A"),)
        assert app_main._scenario_task_suppression_rows(scn) == [{"index": 0, "task_id": "A"}]
        scn = app_main._remove_task_suppression(scn, baseline, "A")
        assert scn.task_suppressions is None

    def test_add_remove_dependency_suppression(self, baseline):
        """Suppressing an edge binds to the baseline, is idempotent per (predecessor, successor),
        and remove-by-index empties back to None."""
        scn = app_main._add_dependency_suppression(None, baseline, "A", "B")
        assert scn.dependency_suppressions == (DependencySuppression("A", "B"),)
        scn = app_main._add_dependency_suppression(scn, baseline, "A", "B")   # idempotent
        assert scn.dependency_suppressions == (DependencySuppression("A", "B"),)
        assert app_main._scenario_dependency_suppression_rows(scn) == [
            {"index": 0, "predecessor_id": "A", "successor_id": "B"}]
        scn = app_main._remove_dependency_suppression(scn, baseline, 0)
        assert scn.dependency_suppressions is None


class TestReplanScenarioBuilders:
    """The Phase-5 checkpoint builders (``_add_checkpoint_hour`` / ``_remove_checkpoint_hour``).
    The as-of hour rides the SAME scenario draft/save lifecycle as the delta families but is a
    scalar run parameter, NOT a delta family — so a checkpoint-only scenario must still read as
    empty (the sidebar 'Run schedule' treats it as the plain baseline; only the Replan page's
    'Run replan' consumes the checkpoint). Built off the `baseline` fixture. No ``st.*``."""

    def test_add_checkpoint_hour_binds_to_baseline_and_sets_the_hour(self, baseline):
        """From no scenario, staging an as-of hour builds a fresh Scenario bound to THIS baseline
        carrying the checkpoint (coerced to float)."""
        scn = app_main._add_checkpoint_hour(None, baseline, 40.0)
        assert scn.base_plan_id == baseline.plan_id
        assert scn.base_plan_hash == baseline.plan_hash
        assert scn.checkpoint_hour == 40.0
        assert isinstance(scn.checkpoint_hour, float)

    def test_remove_checkpoint_hour_clears_to_none(self, baseline):
        """Clearing the as-of hour empties ``checkpoint_hour`` back to None (the no-replan shape)
        while preserving the baseline binding."""
        scn = app_main._add_checkpoint_hour(None, baseline, 40.0)
        scn = app_main._remove_checkpoint_hour(scn, baseline)
        assert scn.checkpoint_hour is None
        assert scn.base_plan_hash == baseline.plan_hash

    def test_checkpoint_only_scenario_still_reads_empty(self, baseline):
        """A checkpoint is a scalar run parameter, not a delta family: a scenario carrying ONLY
        a checkpoint is still ``_scenario_is_empty`` (so the sidebar Run treats it as the plain
        baseline). ``checkpoint_hour`` deliberately stays out of ``_scenario_is_empty``."""
        scn = app_main._add_checkpoint_hour(None, baseline, 40.0)
        assert app_main._scenario_is_empty(scn) is True

    def test_staging_a_checkpoint_makes_the_draft_dirty(self, baseline):
        """Frozen-dataclass equality makes staging a checkpoint flip a stored scenario to a
        distinct draft — so the panel's existing Save/Discard govern it with no extra plumbing."""
        stored = app_main._new_scenario_for(baseline)
        draft = app_main._add_checkpoint_hour(stored, baseline, 40.0)
        assert draft != stored
        # clearing it back returns to an equal (clean) scenario
        assert app_main._remove_checkpoint_hour(draft, baseline) == stored


class TestBuildReplanInputs:
    """The pure canonical-payload → ``pert.replan()``-kwargs projection (``build_replan_inputs``).
    Fed the SAME ``hashing.scenario_payload`` dict the SnapshotStore persists and the adapter
    resolves back, so these pin the exact transform the adapter relies on. No ``st.*``, no PRISM."""

    def test_resource_changes_rename_to_hour_to_until_hour(self, baseline):
        """``resource_changes`` project to ``resource_updates`` renaming ``to_hour → until_hour``
        (replan's key); an open-ended change carries ``until_hour: None``."""
        scn = Scenario(
            scenario_id="s", base_plan_id=baseline.plan_id, base_plan_hash=baseline.plan_hash,
            resource_changes=(ResourceChange("MECH", 24.0, 1, to_hour=72.0),
                              ResourceChange("MECH", 48.0, 2)))
        ri = build_replan_inputs(scenario_payload(scn))
        assert ri.resource_updates == (
            {"skill_type": "MECH", "from_hour": 24.0, "new_count": 1, "until_hour": 72.0},
            {"skill_type": "MECH", "from_hour": 48.0, "new_count": 2, "until_hour": None})

    def test_equipment_changes_rename_to_hour_to_until_hour(self, baseline):
        """``equipment_changes`` project to ``equipment_updates`` with the same ``to_hour →
        until_hour`` rename."""
        scn = Scenario(
            scenario_id="s", base_plan_id=baseline.plan_id, base_plan_hash=baseline.plan_hash,
            equipment_changes=(EquipmentChange("CRANE", 10.0, 0, to_hour=50.0),))
        ri = build_replan_inputs(scenario_payload(scn))
        assert ri.equipment_updates == (
            {"equipment_id": "CRANE", "from_hour": 10.0, "new_quantity": 0, "until_hour": 50.0},)

    def test_duration_overrides_pass_through_as_task_to_hours(self, baseline):
        """``duration_overrides`` pass through unchanged as ``{task_id: hours}`` (replan mutates
        REMAINING time as of T with exactly this map) and the checkpoint rides onto the inputs."""
        scn = Scenario(
            scenario_id="s", base_plan_id=baseline.plan_id, base_plan_hash=baseline.plan_hash,
            checkpoint_hour=40.0,
            duration_overrides=(DurationOverride("B", 9.0),))
        ri = build_replan_inputs(scenario_payload(scn))
        assert ri.duration_overrides == {"B": 9.0}
        assert ri.checkpoint_hour == 40.0

    def test_emergent_task_and_dependency_wiring(self, baseline):
        """Each emergent task becomes a ``from_json``-ready spec carrying ``is_hold_point: False``
        and its OUT-edges as ``successors``; an existing→new edge lands in ``predecessor_wiring``;
        an existing→existing edge (unwireable by the engine) is dropped entirely."""
        scn = app_main._add_emergent_task(None, baseline, "E1", 3.0)
        scn = app_main._add_emergent_dependency(scn, baseline, "E1", "B")   # new → existing (out-edge)
        scn = app_main._add_emergent_dependency(scn, baseline, "A", "E1")   # existing → new (wiring)
        scn = app_main._add_emergent_dependency(scn, baseline, "A", "B")    # existing → existing (drop)
        ri = build_replan_inputs(scenario_payload(scn))

        assert len(ri.new_task_specs) == 1
        spec = ri.new_task_specs[0]
        assert spec["task_id"] == "E1"
        assert spec["is_hold_point"] is False                # per outage-schema hold-point rule
        assert spec["successors"] == [{"task_id": "B", "lag_hours": 0.0}]
        assert ri.predecessor_wiring == {"E1": ["A"]}
        # the existing→existing edge left no trace anywhere (dropped, not mis-wired)
        assert "B" not in ri.predecessor_wiring


class TestReplanPreflight:
    """The WARNING-only preflight (``replan_preflight``): names what a replan will silently drop
    but NEVER blocks (warn-and-run). Every issue is a REPLAN_UNSUPPORTED WARNING; a fully
    supported scenario yields the empty tuple. Built off the `baseline` fixture. No ``st.*``."""

    def _assert_all_warnings(self, issues):
        assert issues                                        # non-empty
        assert all(i.code is IssueCode.REPLAN_UNSUPPORTED for i in issues)
        assert all(i.severity is Severity.WARNING for i in issues)

    def test_location_change_warns(self, baseline):
        scn = app_main._add_location_change(None, baseline, "BAY1", 48.0, 1)
        self._assert_all_warnings(replan_preflight(scn, baseline))

    def test_task_suppression_warns(self, baseline):
        scn = app_main._add_task_suppression(None, baseline, "A")
        self._assert_all_warnings(replan_preflight(scn, baseline))

    def test_hold_point_release_override_warns(self, baseline):
        scn = Scenario(
            scenario_id="s", base_plan_id=baseline.plan_id, base_plan_hash=baseline.plan_hash,
            hold_point_release_overrides=(HoldPointReleaseOverride("A", 12.0),))
        self._assert_all_warnings(replan_preflight(scn, baseline))

    def test_dependency_suppression_warns(self, baseline):
        scn = app_main._add_dependency_suppression(None, baseline, "A", "B")
        self._assert_all_warnings(replan_preflight(scn, baseline))

    def test_existing_to_existing_emergent_dep_warns(self, baseline):
        """An emergent edge whose BOTH endpoints already exist in the baseline is unwireable by
        ``_inject_activities`` — the preflight warns it will be dropped."""
        scn = app_main._add_emergent_dependency(None, baseline, "A", "B")   # both pre-exist
        self._assert_all_warnings(replan_preflight(scn, baseline))

    def test_nonzero_lag_existing_to_new_dep_warns(self, baseline):
        """An existing→new emergent edge is wired lag-0; a non-zero authored lag is dropped, and
        the preflight warns (a zero-lag edge is silent — nothing is lost)."""
        scn = app_main._add_emergent_task(None, baseline, "E1", 3.0)
        scn = app_main._add_emergent_dependency(scn, baseline, "A", "E1", lag_hours=5.0)
        self._assert_all_warnings(replan_preflight(scn, baseline))

    def test_fully_supported_scenario_yields_empty_tuple(self, baseline):
        """A scenario carrying only supported deltas (a duration override + a resource change +
        a checkpoint) drops nothing — the preflight is the empty tuple."""
        scn = app_main._add_duration_override(None, baseline, "B", 9.0)
        scn = app_main._add_resource_change(scn, baseline, "MECH", 48.0, 1)
        scn = app_main._add_checkpoint_hour(scn, baseline, 40.0)
        assert replan_preflight(scn, baseline) == ()


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

    def test_mint_scenario_derived_from_defaults_none_and_is_settable(self, baseline):
        """The lineage label is None by default (a scenario minted off the baseline is a root)
        and is carried through when supplied."""
        assert app_main._mint_scenario(baseline).derived_from is None
        assert (app_main._mint_scenario(baseline, derived_from="scn-parent").derived_from
                == "scn-parent")

    def _source_with_every_delta(self, baseline) -> Scenario:
        """A scenario carrying a delta in every family (values need not be materialize-valid —
        cloning only copies the tuples), bound to ``baseline`` so a clone re-binds consistently."""
        return Scenario(
            scenario_id="scn-src", base_plan_id=baseline.plan_id,
            base_plan_hash=baseline.plan_hash, name="Src",
            duration_overrides=(DurationOverride("A", 7.0),),
            resource_changes=(ResourceChange("MECH", 24.0, 2),),
            equipment_changes=(EquipmentChange("CRANE", 24.0, 0),),
            location_changes=(LocationChange("BAY1", 24.0, 1),),
            hold_point_release_overrides=(HoldPointReleaseOverride("A", 12.0),),
            emergent_tasks=(Task(task_id="NEW", duration=3.0),),
            emergent_dependencies=(Dependency("A", "NEW"),),
            task_suppressions=(TaskSuppression("B"),),
            dependency_suppressions=(DependencySuppression("A", "B"),),
        )

    def test_clone_scenario_copies_every_delta_family_and_sets_lineage(self, baseline):
        """Branching off a source yields a NEW scenario (unique id, bound to THIS baseline) that
        copies ALL of the source's staged delta families verbatim and records
        ``derived_from == source.scenario_id`` — a snapshot, not a live link."""
        source = self._source_with_every_delta(baseline)
        clone = app_main._clone_scenario(source, baseline, existing_ids=(source.scenario_id,))

        assert clone.scenario_id != source.scenario_id
        assert clone.scenario_id.startswith("scn-")
        assert clone.base_plan_id == baseline.plan_id
        assert clone.base_plan_hash == baseline.plan_hash
        assert clone.derived_from == source.scenario_id
        assert clone.name == "Src (copy)"
        for f in (
            "duration_overrides", "resource_changes", "equipment_changes", "location_changes",
            "hold_point_release_overrides", "emergent_tasks", "emergent_dependencies",
            "task_suppressions", "dependency_suppressions",
        ):
            assert getattr(clone, f) == getattr(source, f), f
        assert app_main._scenario_is_empty(clone) is False   # it carries the copied deltas

    def test_clone_scenario_honours_an_explicit_name(self, baseline):
        """A caller-supplied name wins over the "<source> (copy)" default; lineage still set."""
        source = self._source_with_every_delta(baseline)
        clone = app_main._clone_scenario(source, baseline, name="Winter branch")
        assert clone.name == "Winter branch"
        assert clone.derived_from == source.scenario_id

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


class TestScenarioChangeLinesAndDiff:
    """The streamlit-free helpers behind the Replan draft's itemized "staged changes" list and the
    Scenarios page's overlay diff: ``_scenario_change_lines`` (every delta as a (family, detail)
    pair, apply order) and ``_scenario_diff`` (row-per-delta union with per-side flags), plus
    ``_cleared_overlay`` (empty every family, keep identity). All compare INPUT overlays, not run
    outputs. Built off the `baseline` fixture (tasks A dur 4, B dur 6; one MECH pool). No ``st.*``."""

    def _every_family(self, baseline) -> Scenario:
        """A scenario carrying one delta in each family (values need not be materialize-valid — the
        formatters only read fields), bound to ``baseline``."""
        return Scenario(
            scenario_id="scn-src", base_plan_id=baseline.plan_id,
            base_plan_hash=baseline.plan_hash, name="Src",
            duration_overrides=(DurationOverride("A", 7.0),),
            resource_changes=(ResourceChange("MECH", 24.0, 2),),
            equipment_changes=(EquipmentChange("CRANE", 24.0, 0),),
            location_changes=(LocationChange("BAY1", 24.0, 1),),
            hold_point_release_overrides=(HoldPointReleaseOverride("A", 12.0),),
            emergent_tasks=(Task(task_id="NEW", duration=3.0),),
            emergent_dependencies=(Dependency("A", "NEW", 0.0),),
            task_suppressions=(TaskSuppression("B"),),
            dependency_suppressions=(DependencySuppression("A", "B"),))

    def test_change_lines_empty_scenario_is_empty_list(self, baseline):
        """No scenario / an empty overlay stages nothing — an empty change list, never a crash."""
        assert app_main._scenario_change_lines(None) == []
        assert app_main._scenario_change_lines(app_main._mint_scenario(baseline)) == []

    def test_change_lines_one_row_per_delta_in_apply_order(self, baseline):
        """Every staged delta becomes one ``(family_label, detail)`` pair across all nine families,
        ordered as materialize applies them (durations → skills → … → dependency suppressions)."""
        lines = app_main._scenario_change_lines(self._every_family(baseline))
        assert [fam for fam, _detail in lines] == [
            "duration", "skill", "equipment", "location", "hold-point release",
            "added task", "added dependency", "removed task", "removed dependency"]
        detail = dict(lines)
        assert detail["duration"] == "A → 7h"
        assert detail["removed task"] == "B"
        assert detail["removed dependency"] == "A → B"
        assert detail["added task"] == "NEW (3h)"

    def test_change_lines_count_two_of_a_family(self, baseline):
        """Two deltas in one family yield two rows (no collapsing)."""
        scn = Scenario(
            scenario_id="scn-2", base_plan_id=baseline.plan_id, base_plan_hash=baseline.plan_hash,
            duration_overrides=(DurationOverride("A", 7.0), DurationOverride("B", 9.0)))
        lines = app_main._scenario_change_lines(scn)
        assert [d for _f, d in lines] == ["A → 7h", "B → 9h"]

    def test_diff_identical_overlays_have_no_one_sided_rows(self, baseline):
        """Two scenarios staging the same deltas: every row is present on BOTH sides (no diff)."""
        a = self._every_family(baseline)
        b = dataclasses.replace(a, scenario_id="scn-b", name="B")   # same deltas, different identity
        rows = app_main._scenario_diff(a, b)
        assert rows and all(r["in_a"] and r["in_b"] for r in rows)

    def test_diff_same_target_different_value_is_two_one_sided_rows(self, baseline):
        """A delta that differs only in value (A→7h vs A→9h) is the honest "what differs": two rows,
        each present on one side only — never a single merged row that hides the change."""
        a = Scenario(scenario_id="scn-a", base_plan_id=baseline.plan_id,
                     base_plan_hash=baseline.plan_hash,
                     duration_overrides=(DurationOverride("A", 7.0),))
        b = dataclasses.replace(a, scenario_id="scn-b",
                                duration_overrides=(DurationOverride("A", 9.0),))
        rows = app_main._scenario_diff(a, b)
        assert {(r["detail"], r["in_a"], r["in_b"]) for r in rows} == {
            ("A → 7h", True, False), ("A → 9h", False, True)}

    def test_diff_against_empty_lists_all_of_one_sides_deltas(self, baseline):
        """Diffing a scenario against an empty overlay (or None) lists all of its deltas as in_a
        only — the "everything this scenario adds over the baseline" view."""
        a = self._every_family(baseline)
        rows = app_main._scenario_diff(a, None)
        assert len(rows) == 9
        assert all(r["in_a"] and not r["in_b"] for r in rows)

    def test_cleared_overlay_empties_every_family_but_keeps_identity(self, baseline):
        """``_cleared_overlay`` blanks all nine delta families (→ an empty overlay) while preserving
        identity/lineage (id, name, derived_from, base binding) — the Replan "Clear all" action."""
        src = dataclasses.replace(self._every_family(baseline), derived_from="scn-parent")
        cleared = app_main._cleared_overlay(src)
        assert app_main._scenario_is_empty(cleared) is True
        assert (cleared.scenario_id, cleared.name, cleared.derived_from,
                cleared.base_plan_hash) == (src.scenario_id, src.name, "scn-parent",
                                            src.base_plan_hash)


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

    def test_derived_scenario_edges_to_its_parent_not_the_baseline(self, baseline):
        """A scenario branched from another (``derived_from`` set to a PRESENT scenario) draws its
        edge to that parent, not the baseline; the parent stays a root off the baseline. This is
        the lineage tree baseline → A → A'."""
        a = app_main._mint_scenario(baseline, name="A")
        child = app_main._clone_scenario(a, baseline, existing_ids=(a.scenario_id,))
        data = app_main._relation_graph_data(baseline, (a, child), None)

        edges = set(data["edges"])
        assert (app_main._BASELINE_NODE_ID, a.scenario_id) in edges       # A is a root off baseline
        assert (a.scenario_id, child.scenario_id) in edges                # child hangs off A
        assert (app_main._BASELINE_NODE_ID, child.scenario_id) not in edges

        by_id = {n["id"]: n for n in data["nodes"]}
        assert by_id[a.scenario_id]["derived_from"] is None
        assert by_id[child.scenario_id]["derived_from"] == a.scenario_id

    def test_orphaned_lineage_falls_back_to_the_baseline(self, baseline):
        """A ``derived_from`` pointing at a scenario NOT in the set (e.g. the parent was deleted)
        falls the edge back to the baseline so the node never floats disconnected, and the node's
        exposed ``derived_from`` is cleared to None."""
        orphan = app_main._mint_scenario(baseline, name="Orphan", derived_from="scn-ghost")
        data = app_main._relation_graph_data(baseline, (orphan,), None)
        assert data["edges"] == [(app_main._BASELINE_NODE_ID, orphan.scenario_id)]
        by_id = {n["id"]: n for n in data["nodes"]}
        assert by_id[orphan.scenario_id]["derived_from"] is None


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

    def test_layer_by_schedule_x_start_hour_y_float_band(self, baseline):
        """The default ``schedule`` layout puts start time on x (as EVENLY-SPACED ordinal columns —
        the true hour rides on ``x_value``) and the FLOAT CLASS on y as bands: critical anchors the
        centre spine (y=0); a positive-float task sits in a band above it (y strictly greater). This
        is the layout that spreads the equal-ES left-hand pile along start order and rows by color."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        data = app_main._activity_graph_enriched(raw, self._schedule(), layer_by="schedule")
        by_id = {n["id"]: n for n in data["nodes"]}
        # x is the ORDINAL column (A starts earlier → column 0; B later → column 1)…
        assert by_id["A"]["x"] == 0.0 and by_id["B"]["x"] == 1.0
        # …while the TRUE start hour is preserved on x_value (0h and 4h) for hover / tick labels.
        assert by_id["A"]["x_value"] == 0.0 and by_id["B"]["x_value"] == 4.0
        assert by_id["A"]["y"] == 0.0                                  # critical → centre spine
        assert by_id["B"]["y"] > by_id["A"]["y"]                       # positive-float band above
        # Ordinal columns are 0..k-1 and the tick map carries the real hours back.
        assert data["x_columns"] == [0.0, 1.0]
        assert dict(zip(data["x_ticks"]["vals"], data["x_ticks"]["text"])) == {0.0: "0", 1.0: "4"}

    def test_layer_by_es_keeps_proportional_time_no_ordinal_remap(self, baseline):
        """Only ``schedule`` remaps x to even ordinals; ``es`` keeps PROPORTIONAL CPM time (x = the
        real ES hour) and carries no ``x_ticks`` — the escape hatch for true-distance reading."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        data = app_main._activity_graph_enriched(raw, self._schedule(), layer_by="es")
        by_id = {n["id"]: n for n in data["nodes"]}
        assert by_id["A"]["x"] == 0.0 and by_id["B"]["x"] == 4.0       # x = real ES hour, not ordinal
        assert data["x_ticks"] is None                                 # proportional axis, no relabel
        assert "x_value" not in by_id["B"]                             # only schedule stamps x_value

    def test_layer_by_schedule_unscheduled_falls_back_to_depth(self, baseline):
        """An un-scheduled node (no matching DTO, no ``start_hour``) keeps its structural depth as
        the underlying x value (``x_value``) even in schedule mode, and lands in the grey band BELOW
        the critical spine (y < 0)."""
        raw = json.loads(baseline.raw_snapshot)["payload"]
        a = ScheduledActivityDTO(
            task_id="A", start_hour=0.0, end_hour=4.0, duration=4.0, delay_hours=0.0,
            on_constrained_chain=True, float_class=classify_float(0.0, True),
            es_hours=0.0, ls_hours=0.0, cpm_slack_hours=0.0)
        sched = ScheduleDTO(makespan_hours=4.0, cpm_lower_bound_hours=4.0, optimism_gap_hours=0.0,
                            activities=(a,), constrained_chain=("A",), cpm_critical_path=("A",))
        by_id = {n["id"]: n for n in
                 app_main._activity_graph_enriched(raw, sched, layer_by="schedule")["nodes"]}
        assert by_id["B"]["x_value"] == by_id["B"]["depth"]  # no start_hour → underlying x = depth
        assert by_id["B"]["y"] < 0.0                         # grey / unscheduled band below the spine

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


class TestRunComparison:
    """The two streamlit-free builders behind the Phase-4.1 run-comparison view (Results-page
    "Compare runs" segment): ``_scenario_hash_labels`` (forward-hash the live scenarios into
    ``{hash_scenario(s): name}`` — the "match, don't decode" resolution, since provenance hashes
    are one-way and omit human names) and ``_comparison_rows`` (one streamlit-free row per stored
    ``RunResult``, diffing makespan / CPM bound / gap / fitness / disposition / status / freshness,
    each labeled by resolving its ``scenario_delta_hash`` forward). Both pure: stdlib + domain DTOs
    only, no ``st`` — freshness arrives as a plain ``{run_id: Freshness}`` dict the render helper
    (which owns the ``services`` dependency) computes, so ``view_data`` stays services/st-free.
    Fixtures are built inline in the ``run_result``-fixture style (no executor)."""

    # ---- inline fixture builders (the run_result-fixture style: DTOs constructed directly) ----
    @staticmethod
    def _prov(run_id, scenario_delta_hash):
        return Provenance(
            baseline_snapshot_hash="base-hash", effective_plan_hash="eff-hash",
            run_config_hash="cfg-hash", schema_version=SCHEMA_VERSION,
            canonicalization_version=CANON_VERSION, app_version=APP_VERSION,
            prism_version="test", run_id=run_id,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            scenario_delta_hash=scenario_delta_hash)

    @staticmethod
    def _sched(makespan):
        # cpm_lower_bound = makespan - 2, optimism_gap = 2 (arbitrary but distinct per field).
        return ScheduleDTO(
            makespan_hours=makespan, cpm_lower_bound_hours=makespan - 2.0,
            optimism_gap_hours=2.0, activities=(), constrained_chain=())

    def _make(self):
        """Three stored runs sharing one live scenario 'Winter outage':
          * run-base — COMPLETED, no scenario (Baseline), makespan 10, ``FitnessDTO`` on diagnostics;
          * run-scn  — COMPLETED, ``scenario_delta_hash`` == the live scenario's hash, makespan 12,
                       bare ``DiagnosticsDTO()`` (fitness None);
          * run-fail — FAILED, an unmatched scenario hash, ``schedule``/``diagnostics`` None.
        Returns ``(results, labels, disposition, scenario)``."""
        scn = Scenario(scenario_id="scn-1", base_plan_id="p", base_plan_hash="h",
                       name="Winter outage")
        labels = app_main._scenario_hash_labels([scn])
        fit = FitnessDTO(composite=0.42, makespan_ratio=1.0, delay_ratio=0.1,
                         criticality_ratio=0.2, window_violation_ratio=0.0, n_window_violations=0)
        disp = compute_disposition(
            ScheduleSummary(produced=True, n_unscheduled=0, audit_ran=True), ())
        base = RunResult(
            run_id="run-base", status=RunResultStatus.COMPLETED,
            provenance=self._prov("run-base", None), disposition=disp,
            schedule=self._sched(10.0), diagnostics=DiagnosticsDTO(fitness=fit))
        scnrun = RunResult(
            run_id="run-scn", status=RunResultStatus.COMPLETED,
            provenance=self._prov("run-scn", hash_scenario(scn)), disposition=disp,
            schedule=self._sched(12.0), diagnostics=DiagnosticsDTO())
        fail = RunResult(
            run_id="run-fail", status=RunResultStatus.FAILED,
            provenance=self._prov("run-fail", "f" * 64), disposition=None,
            schedule=None, diagnostics=None)
        return [base, scnrun, fail], labels, disp, scn

    # ---- _scenario_hash_labels -----------------------------------------------------------------
    def test_scenario_hash_labels_maps_forward_to_name_or_id(self):
        """Each live scenario's ``hash_scenario`` maps to its ``name`` (or ``scenario_id`` when the
        name is None — the same fallback the relation graph / schedule label already use)."""
        named = Scenario(scenario_id="scn-1", base_plan_id="p", base_plan_hash="h",
                         name="Winter outage")
        unnamed = Scenario(scenario_id="scn-2", base_plan_id="p", base_plan_hash="h")  # name None
        assert app_main._scenario_hash_labels([named, unnamed]) == {
            hash_scenario(named): "Winter outage",
            hash_scenario(unnamed): "scn-2",
        }

    def test_scenario_hash_labels_empty(self):
        """No live scenarios → empty map (every run then resolves to Baseline or a hash prefix)."""
        assert app_main._scenario_hash_labels([]) == {}

    # ---- _comparison_rows ----------------------------------------------------------------------
    def test_rows_one_per_run_in_order_with_stable_keys(self):
        """One row per run, in the order given, each carrying exactly the nine comparison keys."""
        results, labels, _disp, _scn = self._make()
        rows = app_main._comparison_rows(results, labels, {})
        assert len(rows) == len(results) == 3
        assert [r["run_id"] for r in rows] == ["run-base", "run-scn", "run-fail"]
        keys = {"run_id", "scenario", "status", "makespan_hours", "cpm_lower_bound_hours",
                "optimism_gap_hours", "fitness", "disposition", "freshness"}
        assert all(set(r) == keys for r in rows)

    def test_scenario_label_baseline_matched_and_unmatched(self):
        """``scenario_delta_hash is None`` → 'Baseline'; a hash present in the live-scenario map →
        its name; an unmatched hash (scenario edited/deleted since the run) → 'scenario '+8-char
        prefix (the match-not-decode fallback)."""
        results, labels, _disp, _scn = self._make()
        rows = {r["run_id"]: r for r in app_main._comparison_rows(results, labels, {})}
        assert rows["run-base"]["scenario"] == "Baseline"
        assert rows["run-scn"]["scenario"] == "Winter outage"
        assert rows["run-fail"]["scenario"] == "scenario ffffffff"

    def test_status_is_enum_value(self):
        """The status cell is the ``RunResultStatus`` value (explains any blank metric cells)."""
        results, labels, *_ = self._make()
        rows = {r["run_id"]: r for r in app_main._comparison_rows(results, labels, {})}
        assert rows["run-base"]["status"] == "completed"
        assert rows["run-fail"]["status"] == "failed"

    def test_schedule_metrics_present_for_completed_none_for_failed(self):
        """Makespan / CPM bound / optimism gap come straight off ``result.schedule`` for a
        COMPLETED run and are all None for the FAILED run (schedule=None → 'no data → None cell')."""
        results, labels, *_ = self._make()
        rows = {r["run_id"]: r for r in app_main._comparison_rows(results, labels, {})}
        assert (rows["run-base"]["makespan_hours"], rows["run-base"]["cpm_lower_bound_hours"],
                rows["run-base"]["optimism_gap_hours"]) == (10.0, 8.0, 2.0)
        assert rows["run-scn"]["makespan_hours"] == 12.0
        assert rows["run-fail"]["makespan_hours"] is None
        assert rows["run-fail"]["cpm_lower_bound_hours"] is None
        assert rows["run-fail"]["optimism_gap_hours"] is None

    def test_disposition_is_overall_value_else_none(self):
        """The disposition cell is ``disposition.overall.value`` when present, else None."""
        results, labels, disp, _scn = self._make()
        rows = {r["run_id"]: r for r in app_main._comparison_rows(results, labels, {})}
        assert rows["run-base"]["disposition"] == disp.overall.value
        assert rows["run-fail"]["disposition"] is None

    def test_fitness_composite_when_present_else_none(self):
        """Fitness is the composite when a ``FitnessDTO`` rides ``diagnostics``; None on a bare
        ``DiagnosticsDTO()`` (the common case) and when ``diagnostics`` itself is None."""
        results, labels, *_ = self._make()
        rows = {r["run_id"]: r for r in app_main._comparison_rows(results, labels, {})}
        assert rows["run-base"]["fitness"] == 0.42
        assert rows["run-scn"]["fitness"] is None
        assert rows["run-fail"]["fitness"] is None

    def test_freshness_maps_through_label_and_blank_when_absent(self):
        """Freshness maps through ``_FRESHNESS_LABEL`` from the passed ``{run_id: Freshness}`` dict
        and is '' for a run id absent from that map (the builder never computes freshness itself)."""
        results, labels, *_ = self._make()
        freshness = {"run-base": Freshness.CURRENT, "run-scn": Freshness.DIFFERENT_CONFIG}
        rows = {r["run_id"]: r for r in app_main._comparison_rows(results, labels, freshness)}
        assert rows["run-base"]["freshness"] == app_main._FRESHNESS_LABEL[Freshness.CURRENT]
        assert rows["run-scn"]["freshness"] == app_main._FRESHNESS_LABEL[Freshness.DIFFERENT_CONFIG]
        assert rows["run-fail"]["freshness"] == ""


class TestRunAugmentation:
    """The two streamlit-free builders behind the Phase-4.2 guided resource-augmentation view
    (Replan-page "Guided resource augmentation" section): ``_augmentation_candidates`` (rank a run's resource
    pools by aggregate pressure and read each pool's hour-0 crew count — the guidance that seeds the
    bump; the SAME heuristic demand ≥ available signal as ``_saturated_skills``, not a per-task binding
    resource) and ``_augmentation_delta`` (the before/after headline-metric diff — makespan / optimism
    gap / fitness composite — with Δ and Δ%). Both pure: stdlib + domain DTOs only, no ``st`` — the
    render helper owns the run/store/freshness. Fixtures are built inline in the run_result style."""

    @staticmethod
    def _prov(run_id):
        return Provenance(
            baseline_snapshot_hash="base-hash", effective_plan_hash="eff-hash",
            run_config_hash="cfg-hash", schema_version=SCHEMA_VERSION,
            canonicalization_version=CANON_VERSION, app_version=APP_VERSION,
            prism_version="test", run_id=run_id,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc))

    @staticmethod
    def _run(run_id, makespan, gap, composite=None, completed=True):
        """A RunResult with a schedule (makespan / optimism gap) and, when ``composite`` is given, a
        ``FitnessDTO`` on diagnostics. ``completed=False`` → FAILED with schedule/diagnostics None."""
        sched = None
        if completed:
            sched = ScheduleDTO(
                makespan_hours=makespan, cpm_lower_bound_hours=makespan - gap,
                optimism_gap_hours=gap, activities=(), constrained_chain=())
        diag = None
        if composite is not None:
            diag = DiagnosticsDTO(fitness=FitnessDTO(
                composite=composite, makespan_ratio=1.0, delay_ratio=0.0,
                criticality_ratio=0.0, window_violation_ratio=0.0, n_window_violations=0))
        return RunResult(
            run_id=run_id,
            status=RunResultStatus.COMPLETED if completed else RunResultStatus.FAILED,
            provenance=TestRunAugmentation._prov(run_id), schedule=sched, diagnostics=diag)

    @staticmethod
    def _util(series):
        """Build a ``ResourceUtilizationDTO`` from ``[(skill, [(start, end, demand, available), …]), …]``."""
        return ResourceUtilizationDTO(
            horizon_hours=max((iv[1] for _s, ivs in series for iv in ivs), default=0.0),
            series=tuple(
                SkillUtilizationSeries(
                    skill_type=s,
                    intervals=tuple(UtilizationInterval(a, b, d, av) for (a, b, d, av) in ivs))
                for s, ivs in series))

    # ---- _augmentation_candidates --------------------------------------------------------------
    def test_candidates_rank_bottleneck_first_with_fields(self):
        """Pools rank by saturation (tightest first); ``current_count`` is the hour-0 interval's
        availability, ``saturated_hours`` sums the demand ≥ available intervals, ``peak_shortfall`` is
        the largest positive ``demand - available`` (0 when never tight), and ``time_varying`` flags a
        pool whose availability changes across intervals."""
        util = self._util([
            ("ELEC", [(0.0, 20.0, 2, 5)]),                      # never saturated, flat
            ("MECH", [(0.0, 10.0, 5, 4), (10.0, 20.0, 3, 6)]),  # tight early, availability varies
        ])
        cands = app_main._augmentation_candidates(util)
        assert [c["skill_type"] for c in cands] == ["MECH", "ELEC"]   # saturated pool leads
        keys = {"skill_type", "current_count", "saturated_hours", "peak_shortfall", "time_varying"}
        assert all(set(c) == keys for c in cands)
        mech, elec = cands[0], cands[1]
        assert mech["current_count"] == 4          # interval covering hour 0
        assert mech["saturated_hours"] == 10.0     # only [0,10) has demand >= available
        assert mech["peak_shortfall"] == 1         # max(5-4, 3-6) clamped at 0
        assert mech["time_varying"] is True        # available 4 then 6
        assert elec["current_count"] == 5
        assert elec["saturated_hours"] == 0
        assert elec["peak_shortfall"] == 0         # never short → clamped to 0
        assert elec["time_varying"] is False

    def test_candidates_none_util_is_empty(self):
        """No utilization DTO (a FAILED run, a bare diagnostics) → no candidates."""
        assert app_main._augmentation_candidates(None) == []

    # ---- _augmentation_delta -------------------------------------------------------------------
    def test_delta_numeric_rows_with_delta_and_pct(self):
        """One row per headline metric with ``delta = after - before`` and ``pct = delta / before``."""
        before = self._run("before", 85.0, 20.0, composite=0.5)
        after = self._run("after", 71.0, 6.0, composite=0.75)
        rows = app_main._augmentation_delta(before, after)
        assert [r["metric"] for r in rows] == [
            "makespan_hours", "optimism_gap_hours", "fitness_composite"]
        assert all(set(r) == {"metric", "before", "after", "delta", "pct"} for r in rows)
        by = {r["metric"]: r for r in rows}
        assert (by["makespan_hours"]["before"], by["makespan_hours"]["after"]) == (85.0, 71.0)
        assert by["makespan_hours"]["delta"] == -14.0
        assert by["makespan_hours"]["pct"] == (71.0 - 85.0) / 85.0
        assert by["optimism_gap_hours"]["delta"] == -14.0
        assert by["fitness_composite"]["delta"] == 0.25
        assert by["fitness_composite"]["pct"] == 0.5

    def test_delta_failed_after_yields_none_cells(self):
        """A FAILED ``after`` (schedule/diagnostics None) → None after/delta/pct, no exception."""
        before = self._run("before", 85.0, 20.0, composite=0.5)
        after = self._run("after", 0.0, 0.0, completed=False)
        by = {r["metric"]: r for r in app_main._augmentation_delta(before, after)}
        assert by["makespan_hours"]["before"] == 85.0
        assert by["makespan_hours"]["after"] is None
        assert by["makespan_hours"]["delta"] is None
        assert by["makespan_hours"]["pct"] is None
        assert by["fitness_composite"]["after"] is None
        assert by["fitness_composite"]["delta"] is None

    def test_delta_pct_none_when_before_zero(self):
        """A zero ``before`` yields a delta but no percentage (never a division by zero)."""
        before = self._run("before", 0.0, 0.0, composite=0.0)
        after = self._run("after", 5.0, 5.0, composite=0.5)
        by = {r["metric"]: r for r in app_main._augmentation_delta(before, after)}
        assert by["makespan_hours"]["delta"] == 5.0
        assert by["makespan_hours"]["pct"] is None


class TestRunSweep:
    """The streamlit-free builder behind the Phase-4.3 config sweep (Results-page "Sweep" segment,
    axis picker: priority rule / SGS variant / seed): ``_sweep_rows`` turns the sweep's re-run
    ``RunResult`` DTOs into a ranked leaderboard — one row per run, shortest makespan first. The swept
    value is NOT stored on a RunResult (only its opaque ``run_config_hash``), so the sweep tracks
    ``{run_id: label}`` at run time and the builder reads the label from that map — never decoding the
    hash. ``label_key`` names the label column (defaults to ``"priority_rule"``, the 4.3 axis) and is
    also the sort tiebreak, so labels are always strings (seed → ``str(seed)``). Freshness likewise
    arrives as a ``{run_id: Freshness}`` dict; the render helper owns the run/store/services. Pure:
    stdlib + domain DTOs only, no ``st``. Fixtures reuse ``TestRunAugmentation``'s inline builders."""

    _run = staticmethod(TestRunAugmentation._run)

    def test_sweep_rows_rank_shortest_makespan_first(self):
        """Three completed runs ranked shortest-makespan-first; ``delta_vs_best`` is hours above the
        winner (0 for the winner), and every row carries the 7-key set."""
        results = [
            self._run("r-lf", 85.0, 20.0),
            self._run("r-ls", 71.0, 6.0),
            self._run("r-ef", 90.0, 25.0),
        ]
        rule_by_run = {"r-lf": "lf", "r-ls": "ls", "r-ef": "ef"}
        rows = app_main._sweep_rows(results, rule_by_run, {})
        assert [r["priority_rule"] for r in rows] == ["ls", "lf", "ef"]   # 71 < 85 < 90
        keys = {"priority_rule", "status", "makespan_hours", "optimism_gap_hours",
                "fitness", "delta_vs_best", "freshness"}
        assert all(set(r) == keys for r in rows)
        by = {r["priority_rule"]: r for r in rows}
        assert by["ls"]["delta_vs_best"] == 0.0     # the winner
        assert by["lf"]["delta_vs_best"] == 14.0    # 85 - 71
        assert by["ef"]["delta_vs_best"] == 19.0    # 90 - 71
        assert by["lf"]["status"] == "completed"
        assert by["lf"]["optimism_gap_hours"] == 20.0

    def test_sweep_rows_failed_run_sorts_last_with_none_cells(self):
        """A FAILED run (schedule None) sorts to the bottom with None makespan / optimism gap /
        delta, and ``status == 'failed'`` — never raising on the missing schedule."""
        results = [
            self._run("r-ok", 60.0, 5.0),
            self._run("r-bad", 0.0, 0.0, completed=False),
        ]
        rows = app_main._sweep_rows(results, {"r-ok": "lf", "r-bad": "ls"}, {})
        assert [r["priority_rule"] for r in rows] == ["lf", "ls"]   # failed run last
        bad = rows[-1]
        assert bad["status"] == "failed"
        assert bad["makespan_hours"] is None
        assert bad["optimism_gap_hours"] is None
        assert bad["delta_vs_best"] is None

    def test_sweep_rows_rule_label_from_map(self):
        """``priority_rule`` is pulled from ``rule_by_run``; a run absent from the map → ``""``
        (the builder never tries to recover the rule from the stored run)."""
        results = [self._run("r-1", 50.0, 5.0), self._run("r-2", 55.0, 5.0)]
        rows = app_main._sweep_rows(results, {"r-1": "duration"}, {})
        by = {r["makespan_hours"]: r for r in rows}
        assert by[50.0]["priority_rule"] == "duration"
        assert by[55.0]["priority_rule"] == ""     # unmapped run → empty label

    def test_sweep_rows_fitness_and_freshness(self):
        """``fitness`` is the composite only when a ``FitnessDTO`` is present (else None); ``freshness``
        maps through ``_FRESHNESS_LABEL`` from the supplied ``{run_id: Freshness}`` dict, empty → ``""``."""
        results = [
            self._run("r-fit", 70.0, 5.0, composite=0.8),
            self._run("r-nofit", 72.0, 5.0),
        ]
        freshness = {"r-fit": Freshness.CURRENT, "r-nofit": Freshness.DIFFERENT_CONFIG}
        rows = app_main._sweep_rows(results, {"r-fit": "lf", "r-nofit": "ls"}, freshness)
        by = {r["priority_rule"]: r for r in rows}
        assert by["lf"]["fitness"] == 0.8
        assert by["ls"]["fitness"] is None
        assert by["lf"]["freshness"] == app_main._FRESHNESS_LABEL[Freshness.CURRENT]
        assert by["ls"]["freshness"] == app_main._FRESHNESS_LABEL[Freshness.DIFFERENT_CONFIG]
        # A run with no freshness entry falls back to the empty label.
        rows2 = app_main._sweep_rows(results, {"r-fit": "lf", "r-nofit": "ls"}, {})
        assert all(r["freshness"] == "" for r in rows2)

    def test_sweep_rows_empty(self):
        """No runs → no rows (the pre-sweep state)."""
        assert app_main._sweep_rows([], {}, {}) == []

    def test_sweep_rows_label_key_renames_column(self):
        """``label_key="sgs"`` (the SGS-variant axis) names the label column ``"sgs"`` — not
        ``"priority_rule"`` — while ranking and ``delta_vs_best`` stay makespan-driven. The 7-key set
        renames only the label column; SGS labels are the raw ``.value`` strings."""
        results = [
            self._run("r-ranked", 85.0, 20.0),
            self._run("r-first", 71.0, 6.0),
        ]
        label_by_run = {"r-ranked": "max_use_res_ranked", "r-first": "first"}
        rows = app_main._sweep_rows(results, label_by_run, {}, label_key="sgs")
        keys = {"sgs", "status", "makespan_hours", "optimism_gap_hours",
                "fitness", "delta_vs_best", "freshness"}
        assert all(set(r) == keys for r in rows)
        assert all("priority_rule" not in r for r in rows)
        assert [r["sgs"] for r in rows] == ["first", "max_use_res_ranked"]   # 71 < 85
        by = {r["sgs"]: r for r in rows}
        assert by["first"]["delta_vs_best"] == 0.0      # the winner
        assert by["max_use_res_ranked"]["delta_vs_best"] == 14.0    # 85 - 71

    def test_sweep_rows_label_key_seed_sorts_and_tiebreaks(self):
        """``label_key="seed"`` with string labels: a makespan TIE falls back to the ``seed``-label
        tiebreak (ascending string), and a FAILED run still sinks last with None cells — proving the
        parameterized tiebreak references the ``seed`` column, not a hardcoded ``priority_rule``."""
        results = [
            self._run("r-44", 80.0, 5.0),
            self._run("r-42", 80.0, 5.0),      # ties r-44 on makespan → seed tiebreak decides
            self._run("r-43", 0.0, 0.0, completed=False),
        ]
        label_by_run = {"r-44": "44", "r-42": "42", "r-43": "43"}
        rows = app_main._sweep_rows(results, label_by_run, {}, label_key="seed")
        assert [r["seed"] for r in rows] == ["42", "44", "43"]   # tie → "42" < "44"; failed last
        bad = rows[-1]
        assert bad["seed"] == "43"
        assert bad["status"] == "failed"
        assert bad["makespan_hours"] is None
        assert bad["delta_vs_best"] is None


class TestModeSweep:
    """The streamlit-free enumerator behind the Phase-4.3 **Modes** sweep axis (Results-page "Sweep"
    segment, fourth axis): ``_mode_sweep_variants`` fans the CARTESIAN PRODUCT of the selected multi-mode
    tasks' modes out into ready-to-run ``RunConfig.mode_selections`` tuples, holding every UNSELECTED
    multi-mode task at its current pick (base-merge) so a swept run never silently reverts a task to its
    default mode. Labels name only the swept tasks; the product is capped (refuse, never truncate) because
    each combination is a full solve. Pure — ``itertools`` + the ``ModeSelection`` domain record only.
    ``mode_options`` is built inline in the ``_mode_options(payload)`` shape (no plan needed); the run-config
    hash already folds ``{task_id: mode_name}`` so each distinct combination is a distinct verified run."""

    _T1_T2 = [
        {"task_id": "T1", "modes": [{"mode_name": "normal", "duration": 10},
                                    {"mode_name": "crash", "duration": 6}]},
        {"task_id": "T2", "modes": [{"mode_name": "slow", "duration": 9},
                                    {"mode_name": "fast", "duration": 5}]},
    ]

    @staticmethod
    def _as_map(selection):
        """A variant's merged ``tuple[ModeSelection]`` → ``{task_id: mode_name}`` (order-irrelevant, as the
        hash folds it) so a test can assert coverage without depending on tuple order."""
        return {ms.task_id: ms.mode_name for ms in selection}

    def test_mode_sweep_variants_cartesian_product_over_selected(self):
        """Two multi-mode tasks (2 modes each), both selected → the full 2×2 product: total 4, not capped,
        four variants whose labels are exactly the product and whose merged selections each cover BOTH
        tasks."""
        built = app_main._mode_sweep_variants(self._T1_T2, ["T1", "T2"], cap=24)
        assert built["total"] == 4
        assert built["capped"] is False
        assert len(built["variants"]) == 4
        labels = {lbl for lbl, _ in built["variants"]}
        assert labels == {"T1=normal, T2=slow", "T1=normal, T2=fast",
                          "T1=crash, T2=slow", "T1=crash, T2=fast"}
        for _lbl, selection in built["variants"]:
            assert set(self._as_map(selection)) == {"T1", "T2"}   # every combo pins both tasks

    def test_mode_sweep_variants_holds_unselected_at_base(self):
        """Three multi-mode tasks; sweep ONLY T1 while T2/T3 have current picks → two variants (T1's
        modes), each merged selection carries the base T2=fast + T3=slow AND the varying T1; the labels
        mention only the swept T1 (short/comparable)."""
        opts = self._T1_T2 + [{"task_id": "T3", "modes": [{"mode_name": "slow", "duration": 8},
                                                          {"mode_name": "fast", "duration": 4}]}]
        base = (app_main.ModeSelection(task_id="T2", mode_name="fast"),
                app_main.ModeSelection(task_id="T3", mode_name="slow"))
        built = app_main._mode_sweep_variants(opts, ["T1"], base_selections=base, cap=24)
        assert built["total"] == 2
        assert len(built["variants"]) == 2
        for lbl, selection in built["variants"]:
            assert lbl.startswith("T1=") and "T2" not in lbl and "T3" not in lbl
            m = self._as_map(selection)
            assert m["T2"] == "fast" and m["T3"] == "slow"    # unselected tasks held at base
            assert m["T1"] in {"normal", "crash"}             # swept task varies
        assert {self._as_map(s)["T1"] for _l, s in built["variants"]} == {"normal", "crash"}

    def test_mode_sweep_variants_cap_refuses(self):
        """Product (4) over the cap (3) → capped True, empty variants (the render branch refuses to run),
        no exception; ``total`` still reports the pre-cap size so the warning can name it."""
        built = app_main._mode_sweep_variants(self._T1_T2, ["T1", "T2"], cap=3)
        assert built["total"] == 4
        assert built["capped"] is True
        assert built["variants"] == []

    def test_mode_sweep_variants_base_label_matches_current_picks(self):
        """``base_label`` is the combination equal to the current picks — a swept task with a base pick
        (T1=crash) keeps it, a swept task WITHOUT one falls back to its FIRST mode (T2=slow) — and it is
        always one of the variant labels so the winner banner can compare against it."""
        base = (app_main.ModeSelection(task_id="T1", mode_name="crash"),)
        built = app_main._mode_sweep_variants(self._T1_T2, ["T1", "T2"], base_selections=base, cap=24)
        assert built["base_label"] == "T1=crash, T2=slow"     # T2 unset → first mode fallback
        assert built["base_label"] in {lbl for lbl, _ in built["variants"]}

    def test_mode_sweep_variants_skips_unknown_and_empty_selection(self):
        """A selected id absent from ``mode_options`` is skipped defensively; an EMPTY selection is the
        empty product → total 1 with a single empty-combo variant (the render branch's "select at least
        one" / ``len(variants) < 2`` guards keep it off the UI)."""
        built = app_main._mode_sweep_variants(self._T1_T2, ["T1", "ZZZ"], cap=24)
        assert built["total"] == 2                            # ZZZ ignored → only T1 varies
        assert {self._as_map(s)["T1"] for _l, s in built["variants"]} == {"normal", "crash"}
        assert all(set(self._as_map(s)) == {"T1"} for _l, s in built["variants"])

        empty = app_main._mode_sweep_variants(self._T1_T2, [], cap=24)
        assert empty["total"] == 1
        assert len(empty["variants"]) == 1
        assert empty["variants"][0][1] == ()                  # single empty-combo variant


class TestChainSets:
    """The streamlit-free ``_chain_sets`` builder behind the Results **Chain sets** segment: it
    overlaps a single run's three criticality sets — the CPM logical critical path, the resource-
    constrained chain, and the zero-float set — computed from a ``ScheduleDTO`` alone, and partitions
    CPM vs constrained (only_cpm / only_constrained / both) so the resource-driven leverage points
    (on the constrained chain but off the CPM path) are named. Built with hand-made DTOs."""

    @staticmethod
    def _act(task_id, on_chain, tf=0.0):
        # float_class is derived through the SHARED rule so zero_tf tracks classify_float exactly.
        return ScheduledActivityDTO(
            task_id=task_id, start_hour=0.0, end_hour=1.0, duration=1.0, delay_hours=0.0,
            on_constrained_chain=on_chain, float_class=classify_float(tf, on_chain),
            tf_actual_hours=tf)

    @staticmethod
    def _sched(activities, constrained, cpm):
        return ScheduleDTO(
            makespan_hours=1.0, cpm_lower_bound_hours=1.0, optimism_gap_hours=0.0,
            activities=tuple(activities), constrained_chain=tuple(constrained),
            cpm_critical_path=tuple(cpm))

    def test_chain_sets_partitions_cpm_vs_constrained(self):
        """CPM path (A,B,C) and constrained chain (A,B,D) overlapping partly → both={A,B},
        only_cpm={C}, only_constrained={D}; sizes count DISTINCT members."""
        sched = self._sched(
            [self._act("A", True), self._act("B", True), self._act("C", False, tf=0.0),
             self._act("D", True)],
            constrained=("A", "B", "D"), cpm=("A", "B", "C"))
        cs = app_main._chain_sets(sched)
        assert cs["both"] == ("A", "B")
        assert cs["only_cpm"] == ("C",)
        assert cs["only_constrained"] == ("D",)
        assert cs["n_cpm"] == 3 and cs["n_constrained"] == 3

    def test_chain_sets_leverage_points_are_only_constrained(self):
        """A task on the constrained chain but NOT the CPM path is the resource leverage point
        (only_constrained); a task on BOTH chains is not."""
        sched = self._sched(
            [self._act("X", True), self._act("Y", False, tf=0.0), self._act("Z", True)],
            constrained=("X", "Z"), cpm=("X", "Y"))
        cs = app_main._chain_sets(sched)
        assert "Z" in cs["only_constrained"]          # constrained-only ⇒ resource-driven
        assert "X" not in cs["only_constrained"]       # on both ⇒ not a resource-only leverage point
        assert cs["both"] == ("X",)

    def test_chain_sets_zero_tf_superset_of_constrained(self):
        """zero_tf = float_class ∈ {CRITICAL, ZERO_FLOAT}: a CRITICAL chain task and a ZERO_FLOAT
        off-chain task are in, a POSITIVE_FLOAT task is out, and constrained ⊆ zero_tf."""
        sched = self._sched(
            [self._act("A", True), self._act("Z", False, tf=0.0), self._act("P", False, tf=5.0)],
            constrained=("A",), cpm=())
        cs = app_main._chain_sets(sched)
        assert set(cs["zero_tf"]) == {"A", "Z"}
        assert "P" not in cs["zero_tf"]
        assert set(cs["constrained"]).issubset(set(cs["zero_tf"]))

    def test_chain_sets_membership_rows_cover_union(self):
        """rows cover exactly set(cpm) | set(constrained) | zero_tf, sorted by task_id, each row's
        three booleans + float_class/tf_actual_hours matching the source activity (a task in NO set
        — positive-float, off both chains — is absent)."""
        sched = self._sched(
            [self._act("A", True), self._act("B", False, tf=3.0), self._act("C", True),
             self._act("Z", False, tf=0.0), self._act("P", False, tf=5.0)],
            constrained=("A", "C"), cpm=("A", "B"))
        cs = app_main._chain_sets(sched)
        assert [r["task_id"] for r in cs["rows"]] == ["A", "B", "C", "Z"]     # sorted; P excluded
        by_id = {r["task_id"]: r for r in cs["rows"]}
        assert by_id["B"] == {"task_id": "B", "on_cpm": True, "on_constrained": False,
                              "zero_tf": False, "float_class": "positive_float", "tf_actual_hours": 3.0}
        assert by_id["C"] == {"task_id": "C", "on_cpm": False, "on_constrained": True,
                              "zero_tf": True, "float_class": "critical", "tf_actual_hours": 0.0}
        assert by_id["Z"] == {"task_id": "Z", "on_cpm": False, "on_constrained": False,
                              "zero_tf": True, "float_class": "zero_float", "tf_actual_hours": 0.0}

    def test_chain_sets_all_rows_cover_every_activity(self):
        """all_rows lists EVERY scheduled activity (sorted by task_id) — the superset the view's
        "All activities" filter draws on — unlike rows (union only): the positive-float off-chain
        task P, deliberately absent from rows, appears in all_rows with all three flags False so the
        filter can surface it. rows stays union-only (the two keys are distinct)."""
        sched = self._sched(
            [self._act("A", True), self._act("B", False, tf=3.0), self._act("C", True),
             self._act("Z", False, tf=0.0), self._act("P", False, tf=5.0)],
            constrained=("A", "C"), cpm=("A", "B"))
        cs = app_main._chain_sets(sched)
        assert [r["task_id"] for r in cs["all_rows"]] == ["A", "B", "C", "P", "Z"]   # every activity
        by_id = {r["task_id"]: r for r in cs["all_rows"]}
        assert by_id["P"] == {"task_id": "P", "on_cpm": False, "on_constrained": False,
                              "zero_tf": False, "float_class": "positive_float", "tf_actual_hours": 5.0}
        assert "P" not in {r["task_id"] for r in cs["rows"]}      # rows still union-only, P excluded

    def test_chain_sets_empty_cpm_path(self):
        """cpm_critical_path=() (the DTO default when the engine emits none): n_cpm=0, both=() and
        only_cpm=(), and only_constrained is the whole constrained chain — no crash."""
        sched = self._sched(
            [self._act("A", True), self._act("B", True)],
            constrained=("A", "B"), cpm=())
        cs = app_main._chain_sets(sched)
        assert cs["n_cpm"] == 0
        assert cs["both"] == () and cs["only_cpm"] == ()
        assert set(cs["only_constrained"]) == {"A", "B"}


class TestWindowPreflight:
    """The streamlit-free ``_window_preflight`` builder behind the Results **Time windows** segment: it
    checks each scheduled task's start/end against its AUTHORED windows (payload ``time_windows`` rows
    ``{earliest, latest}``, hours from outage start), replicating the engine's rule — fit in AT LEAST ONE
    window (start ≥ earliest, finish ≤ latest) within a 1 ms grace. Built from a raw payload dict + a
    ``ScheduleDTO`` of hand-made activities; only windowed tasks appear in the rows."""

    @staticmethod
    def _act(task_id, start, end):
        return ScheduledActivityDTO(
            task_id=task_id, start_hour=float(start), end_hour=float(end),
            duration=float(end) - float(start), delay_hours=0.0,
            on_constrained_chain=False, float_class=FloatClass.POSITIVE_FLOAT)

    @staticmethod
    def _sched(activities):
        return ScheduleDTO(
            makespan_hours=1.0, cpm_lower_bound_hours=1.0, optimism_gap_hours=0.0,
            activities=tuple(activities), constrained_chain=(), cpm_critical_path=())

    @staticmethod
    def _payload(windows_by_task):
        # windows_by_task: {task_id: [(earliest, latest), ...]}; absent list ⇒ unconstrained task.
        return {"tasks": [
            {"task_id": tid,
             "time_windows": [{"earliest": lo, "latest": hi} for lo, hi in wins]}
            for tid, wins in windows_by_task.items()
        ]}

    def test_window_preflight_start_before_earliest(self):
        """A task scheduled to start before its window's earliest → violation, start_short > 0 and
        end_over == 0 (the finish is fine)."""
        sched = self._sched([self._act("A", start=5.0, end=20.0)])
        pf = app_main._window_preflight(self._payload({"A": [(10.0, 48.0)]}), sched)
        row = pf["rows"][0]
        assert row["fits"] is False and pf["violations"] == ("A",)
        assert row["start_short"] == 5.0 and row["end_over"] == 0.0

    def test_window_preflight_end_after_latest(self):
        """A task whose finish is past its window's latest → violation, end_over > 0 and
        start_short == 0."""
        sched = self._sched([self._act("A", start=12.0, end=60.0)])
        pf = app_main._window_preflight(self._payload({"A": [(10.0, 48.0)]}), sched)
        row = pf["rows"][0]
        assert row["fits"] is False and pf["violations"] == ("A",)
        assert row["end_over"] == 12.0 and row["start_short"] == 0.0

    def test_window_preflight_or_across_windows(self):
        """A task with TWO windows fits the SECOND (misses the first) → compliant (OR across windows),
        best_window points at the fitting one, not counted a violation."""
        sched = self._sched([self._act("A", start=80.0, end=90.0)])
        pf = app_main._window_preflight(self._payload({"A": [(0.0, 48.0), (72.0, 96.0)]}), sched)
        row = pf["rows"][0]
        assert row["fits"] is True and pf["violations"] == ()
        assert row["best_window"] == 1
        assert row["best_earliest"] == 72.0 and row["best_latest"] == 96.0

    def test_window_preflight_tolerance_grace(self):
        """A finish a hair past latest (< the 1 ms grace) still fits; a finish clearly past does not."""
        grace = app_main._WINDOW_TOL_HOURS
        within = self._sched([self._act("A", start=0.0, end=48.0 + grace / 2)])
        assert app_main._window_preflight(self._payload({"A": [(0.0, 48.0)]}), within)["n_violations"] == 0
        past = self._sched([self._act("A", start=0.0, end=48.0 + 1.0)])
        assert app_main._window_preflight(self._payload({"A": [(0.0, 48.0)]}), past)["n_violations"] == 1

    def test_window_preflight_unconstrained_excluded(self):
        """A task with no time_windows is excluded from rows and does not count toward n_windowed."""
        sched = self._sched([self._act("A", start=5.0, end=20.0), self._act("B", start=0.0, end=10.0)])
        pf = app_main._window_preflight(self._payload({"A": [(10.0, 48.0)], "B": []}), sched)
        assert pf["n_windowed"] == 1
        assert [r["task_id"] for r in pf["rows"]] == ["A"]

    def test_window_preflight_best_window_reports_closest(self):
        """A violation with two windows reports the window with the smaller total miss as best_window."""
        # start=60,end=70: window0 [0,48] misses by end_over=22; window1 [80,96] misses by start_short=20.
        sched = self._sched([self._act("A", start=60.0, end=70.0)])
        pf = app_main._window_preflight(self._payload({"A": [(0.0, 48.0), (80.0, 96.0)]}), sched)
        row = pf["rows"][0]
        assert row["fits"] is False
        assert row["best_window"] == 1                      # 20 h miss < 22 h miss
        assert row["best_earliest"] == 80.0 and row["start_short"] == 20.0

    def test_window_preflight_all_fit(self):
        """Every windowed task fits → n_violations == 0, violations == (), each row's miss edges 0.0."""
        sched = self._sched([self._act("A", start=10.0, end=40.0), self._act("B", start=72.0, end=90.0)])
        pf = app_main._window_preflight(
            self._payload({"A": [(0.0, 48.0)], "B": [(72.0, 96.0)]}), sched)
        assert pf["n_windowed"] == 2 and pf["n_violations"] == 0 and pf["violations"] == ()
        assert all(r["start_short"] == 0.0 and r["end_over"] == 0.0 for r in pf["rows"])
        assert all(r["fits"] for r in pf["rows"])


class TestChartLayer:
    """The two streamlit-free builders behind the deferred Phase-4 visual half — the overlaid makespan
    bar chart (all three orchestration views) and the aligned multi-run Gantt (Compare runs / Augment).
    ``_makespan_bar_rows`` turns ``(label, RunResult)`` pairs into stacked-bar rows (a CPM-floor + an
    optimism-gap segment that sum to the makespan, the shortest flagged ``is_best``); ``_multi_gantt_rows``
    tags each run's ``_gantt_rows`` with its label and concatenates them over the shared hour-0 axis.
    Both pure: stdlib + domain DTOs only, no ``st``/Plotly — the render helpers own the figure. Bar
    fixtures reuse ``TestRunAugmentation._run``; Gantt fixtures build a schedule WITH activities inline
    (``_run``'s schedule has ``activities=()``)."""

    _run = staticmethod(TestRunAugmentation._run)

    @staticmethod
    def _act(task_id, start, end, on_chain=False):
        return ScheduledActivityDTO(
            task_id=task_id, start_hour=start, end_hour=end, duration=end - start,
            delay_hours=0.0, on_constrained_chain=on_chain,
            float_class=classify_float(0.0, on_chain),
            es_hours=start, ls_hours=start, cpm_slack_hours=0.0)

    @staticmethod
    def _run_acts(run_id, acts):
        """A COMPLETED RunResult whose schedule carries ``acts`` (makespan = last activity end)."""
        makespan = max((a.end_hour for a in acts), default=0.0)
        sched = ScheduleDTO(
            makespan_hours=makespan, cpm_lower_bound_hours=makespan, optimism_gap_hours=0.0,
            activities=tuple(acts), constrained_chain=())
        return RunResult(
            run_id=run_id, status=RunResultStatus.COMPLETED,
            provenance=TestRunAugmentation._prov(run_id), schedule=sched, diagnostics=None)

    # ---- _makespan_bar_rows --------------------------------------------------------------------
    def test_makespan_bar_rows_one_per_run_in_order_with_keys(self):
        """One row per labeled run, in INPUT order (not ranked), each carrying the six bar keys; the
        CPM floor + optimism gap sum to the makespan (the stacked-bar identity)."""
        labeled = [
            ("lf", self._run("r-lf", 85.0, 20.0)),
            ("ls", self._run("r-ls", 71.0, 6.0)),
            ("ef", self._run("r-ef", 90.0, 30.0)),
        ]
        rows = app_main._makespan_bar_rows(labeled)
        assert [r["label"] for r in rows] == ["lf", "ls", "ef"]   # input order, NOT ranked
        keys = {"label", "status", "cpm_lower_bound_hours", "optimism_gap_hours",
                "makespan_hours", "is_best"}
        assert all(set(r) == keys for r in rows)
        for r in rows:
            assert r["cpm_lower_bound_hours"] + r["optimism_gap_hours"] == r["makespan_hours"]

    def test_makespan_bar_rows_is_best_marks_shortest(self):
        """``is_best`` is True on the shortest-makespan run and only there; a makespan tie flags BOTH
        tying runs (the render helper outlines every shortest bar)."""
        rows = {r["label"]: r for r in app_main._makespan_bar_rows([
            ("a", self._run("r-a", 85.0, 20.0)),
            ("b", self._run("r-b", 71.0, 6.0)),
            ("c", self._run("r-c", 90.0, 30.0)),
        ])}
        assert rows["b"]["is_best"] is True
        assert rows["a"]["is_best"] is False and rows["c"]["is_best"] is False
        tie = {r["label"]: r for r in app_main._makespan_bar_rows([
            ("x", self._run("r-x", 71.0, 6.0)),
            ("y", self._run("r-y", 71.0, 8.0)),      # ties x on makespan
            ("z", self._run("r-z", 90.0, 30.0)),
        ])}
        assert tie["x"]["is_best"] is True and tie["y"]["is_best"] is True
        assert tie["z"]["is_best"] is False

    def test_makespan_bar_rows_failed_run_none_cells(self):
        """A FAILED run (no schedule) → None cpm/gap/makespan and is_best False; when NO run completed,
        nothing is flagged best and nothing raises."""
        rows = {r["label"]: r for r in app_main._makespan_bar_rows([
            ("ok", self._run("r-ok", 71.0, 6.0)),
            ("bad", self._run("r-bad", 0.0, 0.0, completed=False)),
        ])}
        assert rows["bad"]["makespan_hours"] is None
        assert rows["bad"]["cpm_lower_bound_hours"] is None
        assert rows["bad"]["optimism_gap_hours"] is None
        assert rows["bad"]["is_best"] is False
        assert rows["bad"]["status"] == "failed"
        assert rows["ok"]["is_best"] is True
        allbad = app_main._makespan_bar_rows([
            ("f1", self._run("r-f1", 0.0, 0.0, completed=False)),
            ("f2", self._run("r-f2", 0.0, 0.0, completed=False)),
        ])
        assert all(r["is_best"] is False and r["makespan_hours"] is None for r in allbad)

    # ---- _multi_gantt_rows ---------------------------------------------------------------------
    def test_multi_gantt_rows_tags_label_and_preserves_run_order(self):
        """Every activity row is tagged with its ``run_label``; rows group by run in input order and
        preserve each schedule's activity order, carrying the ``_gantt_rows`` fields plus ``run_label``."""
        run1 = self._run_acts("r-1", [self._act("A", 0.0, 4.0, on_chain=True), self._act("B", 4.0, 8.0)])
        run2 = self._run_acts("r-2", [self._act("A", 0.0, 3.0)])
        rows = app_main._multi_gantt_rows([("first", run1), ("second", run2)])
        assert [r["run_label"] for r in rows] == ["first", "first", "second"]   # run order preserved
        assert [r["task"] for r in rows] == ["A", "B", "A"]                     # activity order kept
        gantt_keys = {"task", "start", "end", "duration", "delay", "float_class",
                      "on_chain", "description"}
        assert all(set(r) == {"run_label"} | gantt_keys for r in rows)
        assert isinstance(rows[0]["on_chain"], bool) and isinstance(rows[0]["start"], float)

    def test_multi_gantt_rows_skips_runs_without_schedule(self):
        """A FAILED run (schedule None) contributes zero rows; a completed run beside it still
        contributes its activities."""
        ok = self._run_acts("r-ok", [self._act("A", 0.0, 4.0)])
        bad = self._run("r-bad", 0.0, 0.0, completed=False)
        rows = app_main._multi_gantt_rows([("bad", bad), ("ok", ok)])
        assert {r["run_label"] for r in rows} == {"ok"}
        assert [r["task"] for r in rows] == ["A"]


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
