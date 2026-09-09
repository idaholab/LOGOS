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
