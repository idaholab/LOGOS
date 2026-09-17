"""app/main.py — the PRISM GUI composition root (Streamlit shell entry point).

Run it with::

    streamlit run src/prismGui/app/main.py

This module is the composition root: it bootstraps ``sys.path``, wires the concrete adapters
(``OutageValidatorAdapter``, ``InMemorySnapshotStore``, ``InProcessPrismExecutor``,
``InMemoryRepository``) into the application services, runs the shared sidebar prologue, and
builds the four workflow pages via ``st.navigation``. It owns no scheduling logic and no policy.

Since Phase 2 the GUI is split into cohesive ``app`` submodules — pure builders
(``pipeline`` / ``edit_model`` / ``scenario_model`` / ``view_data``), the Streamlit session
facade (``session``), the shared prologue and render bits (``sidebar`` / ``components``), and the
four workflow pages under ``app.pages``. The Streamlit import is guarded once in
``app._streamlit`` and shared by every view module; the ``app`` package is the only layer that
imports Streamlit — the domain, application, ports, and infrastructure layers never do, and all
``st.*`` calls live inside function bodies so every module imports cleanly headless. This file
re-exports the pure builders below so ``prismGui.app.main.<name>`` stays the stable surface the
GUI test suite imports.
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Optional

# `streamlit run src/prismGui/app/main.py` executes this file directly, so the src/ root is
# NOT on sys.path the way the pytest island puts it there (pytest.ini's pythonpath=../../src).
# Bootstrap it before the first `prismGui.*` import so the app resolves no matter which
# directory it is launched from. Harmless under pytest / a headless import — if src/ is
# already importable the entry is simply skipped.
_SRC_ROOT = Path(__file__).resolve().parents[2]      # .../src
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from prismGui.app._streamlit import st, _HAS_STREAMLIT

from prismGui.application import services
from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity
from prismGui.domain.plan import (
    PatchAction,
    PatchOp,
    ResourceType,
    apply_patch,
    discard_draft,
    open_draft,
)
from prismGui.domain.materialize import materialize
from prismGui.domain.results import DispositionOverall, Freshness, RunResultStatus
from prismGui.domain.run_config import EvaluationWeights, ModeSelection, PRIORITY_RULES, RunConfig, SGSVariant
from prismGui.domain.scenario import (
    DependencySuppression, DurationOverride, EquipmentChange, LocationChange, ResourceChange,
    Scenario, TaskSuppression,
)
from prismGui.infrastructure.memory_repository import InMemoryRepository
from prismGui.infrastructure.memory_snapshot_store import InMemorySnapshotStore
from prismGui.infrastructure.validation_adapter import OutageValidatorAdapter

# ---------------------------------------------------------------------------------------------
# Phase-2 re-export hub: the pure builders were lifted into cohesive submodules, but the two GUI
# test files import them as ``prismGui.app.main.<name>`` — so re-export every lifted pure name
# here to keep that surface stable. (The Streamlit render/session/page layer moves freely; only
# the names the composition root below actually calls are imported for wiring, not re-export.)
# ---------------------------------------------------------------------------------------------
from prismGui.app.pipeline import (
    REPO_ROOT, SCHEMA_PATH, EXAMPLES_DIR, discover_samples, PipelineResult, run_pipeline,
    run_replan_pipeline, build_validator, _make_executor
)
from prismGui.app.edit_model import (
    _HOLD_POINT_TYPES, _NO_VALUE, _add_availability_period_patch, _add_consumable_patch,
    _add_dependency_patch, _add_equipment_availability_patch, _add_equipment_patch,
    _add_location_availability_patch, _add_location_patch, _add_resource_pool_patch,
    _add_restock_patch, _add_system_patch, _add_system_state_patch, _add_task_alt_skill_patch,
    _add_task_consumable_patch, _add_task_equipment_patch, _add_task_mode_equipment_patch,
    _add_task_mode_patch, _add_task_mode_resource_patch, _add_task_patch,
    _add_task_resource_patch, _add_task_system_state_patch, _add_task_time_window_patch,
    _array_add_op, _as_date, _as_float, _as_str, _availability_options,
    _availability_window_patch, _available_count_patch, _consumable_options,
    _consumable_total_patch, _dependency_options, _description_patch, _dup_id,
    _duration_patch, _equipment_availability_options, _equipment_options,
    _equipment_quantity_patch, _equipment_window_patch, _equipment_zone,
    _equipment_zone_clear_patch, _equipment_zone_patch, _find_task_index, _iso_date_value,
    _location_availability_options, _location_capacity_patch, _location_options,
    _location_period_value, _location_window_patch, _mode_options, _patch_rows, _ref_missing,
    _remove_availability_period_patch, _remove_consumable_patch, _remove_dependency_patch,
    _remove_equipment_availability_patch, _remove_equipment_patch,
    _remove_location_availability_patch, _remove_location_patch, _remove_resource_pool_patch,
    _remove_restock_patch, _remove_system_patch, _remove_system_state_patch,
    _remove_task_alt_skill_patch, _remove_task_consumable_patch, _remove_task_equipment_patch,
    _remove_task_mode_equipment_patch, _remove_task_mode_patch,
    _remove_task_mode_resource_patch, _remove_task_patch, _remove_task_resource_patch,
    _remove_task_system_state_patch, _remove_task_time_window_patch, _resource_dose_budget,
    _resource_dose_budget_clear_patch, _resource_dose_budget_patch, _resource_options,
    _resource_type_patch, _restock_edit_patch, _restock_options, _system_options,
    _system_state_options, _task_at, _task_consumable_reqs, _task_dose,
    _task_dose_clear_patch, _task_dose_patch, _task_equipment_reqs,
    _task_hold_point, _task_hold_point_clear_patch, _task_hold_point_set_patch, _task_ids,
    _task_location, _task_location_clear_patch, _task_location_patch,
    _task_mode_dose_clear_patch, _task_mode_dose_patch, _task_mode_duration_patch,
    _task_mode_mob_clear_patch, _task_mode_mob_patch, _task_modes, _task_options,
    _task_resource_crew_patch, _task_resource_reqs, _task_resource_skill_patch,
    _task_system_state_reqs, _task_time_window_edit_patch, _task_time_windows, _task_zones,
    _task_zones_clear_patch, _task_zones_patch, _window_replace_ops
)
from prismGui.app.scenario_model import (
    _SCN_INTENTS, _is_whatif, _scenario_is_empty, _new_scenario_for, _mint_scenario,
    _clone_scenario,
    _scenario_base, _add_checkpoint_hour, _remove_checkpoint_hour,
    _scenario_duration_rows, _scenario_resource_rows, _add_duration_override,
    _remove_duration_override, _add_resource_change, _remove_resource_change,
    _scenario_equipment_rows, _scenario_location_rows, _add_equipment_change,
    _remove_equipment_change, _add_location_change, _remove_location_change,
    _scenario_emergent_task_rows, _add_emergent_task, _remove_emergent_task,
    _scenario_emergent_dependency_rows, _add_emergent_dependency, _remove_emergent_dependency,
    _scenario_task_suppression_rows, _add_task_suppression, _remove_task_suppression,
    _scenario_dependency_suppression_rows, _add_dependency_suppression,
    _remove_dependency_suppression,
    _current_schedule_payload, _schedule_label,
    _cleared_overlay, _scenario_change_lines, _scenario_diff,
)
from prismGui.app.plan_of_record_model import (
    _por_is_empty, _por_next_asof_floor, _por_chain_run_ids, _run_is_adoptable, _adopt_step,
    _revert_last, _next_scenario_after_adopt, _por_timeline_rows,
)
from prismGui.app.view_data import (
    _SEVERITY_ORDER, _issue_rows, _gantt_rows, _resource_util_rows, _FLOAT_CLASS_COLORS,
    _step_series, _schedule_csv, _DISPOSITION_INDICATOR_LABELS, _disposition_rows,
    _FRESHNESS_LABEL, _FRESHNESS_REASON_LABEL, _PROVENANCE_FIELD_LABELS, _provenance_rows,
    _scenario_hash_labels, _comparison_rows, _augmentation_candidates, _augmentation_delta,
    _sweep_rows, _makespan_bar_rows, _multi_gantt_rows, _mode_sweep_variants,
    _data_viewer_rows, _BASELINE_NODE_ID, _OVERLAY_FIELDS, _overlay_count,
    _relation_graph_data, _activity_graph_data, _dag_node_color, _dag_hover,
    _activity_graph_enriched, _graph_layout, _cpm_path_edges, _cpm_path_label,
    _evaluation_weights, _task_slip, _task_neighbors, _saturated_skills, _chain_sets,
    _WINDOW_TOL_HOURS, _window_preflight, _replan_diff, _default_original_run_id,
    _buffer_burn
)

# view-layer wiring used by main() / the page closures below
from prismGui.app.session import StreamlitSessionState
from prismGui.app.components import _render_issues
from prismGui.app.sidebar import (
    _pick_run_config, _pick_source, _render_schedule_selector, _render_validation_badge,
    _source_key,
)
from prismGui.app.pages.plan import _render_plan_page
from prismGui.app.pages.results import _render_results_page
from prismGui.app.pages.replan import _render_replan
from prismGui.app.pages.scenarios import _render_scenarios


_RUN_ERROR = "prism_run_error"     # transient: (stage, issues) of the last blocked run, popped once shown
_RUN_BACKEND = "prism_run_backend" # persisted (store, executor, repository) — MUST outlive a rerun

def _run_and_store(session, payload, plan_id, run_config, validator, store, executor,
                   repository, scenario) -> None:
    """Run-button ``on_click`` callback: run the pipeline and store + select the result (or stash
    the block reason) in ``session_state``.

    Wired as a callback rather than ``if st.sidebar.button(): ...`` so the run fires on the FIRST
    click. Streamlit runs on_click callbacks BEFORE the script body on the click's rerun, so the
    result is stored + selected before ``main()`` resolves the selected result below and the
    Results page paints it. The plain-button form lost its ``True`` return across the navigation /
    rerun churn on the first click (an ``st.rerun`` in its success branch never fired, because the
    branch was never entered), so results appeared only on a second click. A callback sidesteps
    the return value entirely."""
    st.session_state.pop(_RUN_ERROR, None)
    # Run the CURRENT session baseline's payload — a committed edit is what runs — with the session
    # scenario (if any) materialized into the effective plan by prepare_run.
    outcome = run_pipeline(
        payload, plan_id, run_config,
        validator=validator, store=store, executor=executor, repository=repository,
        scenario=scenario)
    if outcome.ok:
        session.add_run_result(outcome.result)
        session.set_selected_result_id(outcome.result.run_id)
    else:
        st.session_state[_RUN_ERROR] = (outcome.stage, list(outcome.issues))

def main() -> None:
    if not _HAS_STREAMLIT:  # pragma: no cover - guarded entry
        raise SystemExit(
            "PRISM GUI requires Streamlit. Install it and launch the app:\n"
            "    pip install streamlit\n"
            "    streamlit run src/prismGui/app/main.py"
        )

    st.set_page_config(page_title="PRISM Scheduler", layout="wide")
    st.title("PRISM — Outage Schedule")
    st.caption("Load a plan and configure the run in the sidebar, then click **Run schedule** — the "
               "schedule and plots appear here on **Results** (the landing page). Switch to **Plan** "
               "to view / edit the baseline, **Replan** for scenario what-ifs, and **Scenarios** for "
               "the library + relation graph.")

    session = StreamlitSessionState()
    validator = build_validator()
    # Run backend (snapshot store + PRISM executor + repository), persisted across reruns in session
    # state — NOT rebuilt each script run. ``run_id`` is a per-executor counter (prism-run-1, -2, …),
    # so a fresh executor every rerun would reset it to prism-run-1; since run results are stored in a
    # dict keyed by run_id, each new run would then silently OVERWRITE the previous one, leaving
    # Compare runs / Buffer burn permanently seeing "fewer than two runs". The store must likewise
    # outlive a rerun so a later replan can read an earlier run's frozen-prefix snapshot.
    if _RUN_BACKEND not in st.session_state:
        _store = InMemorySnapshotStore()
        st.session_state[_RUN_BACKEND] = (_store, _make_executor(_store), InMemoryRepository())
    store, executor, repository = st.session_state[_RUN_BACKEND]

    raw, plan_id = _pick_source()
    # Defense in depth: the durable-memo re-seed in _pick_source keeps the pick across a run / page
    # switch, but if the source read still comes back empty while a plan is ALREADY loaded, keep
    # operating on that stored baseline instead of dumping the user back to the prompt (and losing
    # their run). Only a genuinely fresh session — no baseline yet — shows the prompt.
    from_source = raw is not None
    if not from_source:
        loaded = session.get_baseline()
        if loaded is None:
            st.info("Choose a sample project or upload a plan JSON to begin.")
            return
        raw = json.loads(loaded.raw_snapshot)["payload"]
        plan_id = loaded.plan_id

    # --- validation: compact badge in the sidebar, full issue list in the main column ---
    load = services.load_and_validate(plan_id, raw, validator)
    _render_validation_badge(load)
    if not load.ok:
        st.subheader("Validation")
        st.error("Plan has blocking errors; fix them before running.")
        _render_issues(load.issues, empty_msg="Plan is valid — no issues.")
        return
    with st.expander("Validation details", expanded=False):
        _render_issues(load.issues, empty_msg="Plan is valid — no issues.")

    # Source-signature guard: seed the session baseline from the file only when the selected
    # source changes. A committed edit (which sets the baseline to the new revision) then
    # survives Streamlit's top-to-bottom rerun instead of being overwritten by this reload.
    # Skip it entirely on the stored-baseline fallback above: that raw was reconstructed from the
    # baseline's own (possibly edited) snapshot, so recomputing the source signature from it could
    # later discard a committed edit — the baseline is already resolved, leave its signature alone.
    if from_source:
        source_key = _source_key(load.reference_plan)
        if session.get_source_key() != source_key:
            session.set_source_key(source_key)
            # Point the session at the new baseline and resolve anything bound to a prior
            # revision: a stale draft OR scenario is cleared, a compatible one is kept (the
            # pure services.resolve_for_new_baseline the group-J contract drives headlessly).
            services.resolve_for_new_baseline(session, load.reference_plan)

    baseline = session.get_baseline()

    # Current-schedule selector (baseline or a scenario) — read the pointer within this same
    # rerun so every page below and the Run button operate on the selected schedule.
    _render_schedule_selector(session, baseline)

    run_config = _pick_run_config(plan_id)
    # Fold in the execution-mode picks (Replan page) — kept on the RunConfig, so they ride
    # the run-config hash / freshness. The picker persists them to the session on each render, so
    # this reads the latest picks. Empty (the shipping-sample case) leaves the default ().
    run_config = replace(run_config, mode_selections=session.get_mode_selections())
    session.set_run_config(run_config)

    # --- run controls live in the sidebar (below the run configuration) ---
    # Wired as an on_click callback (see _run_and_store) so the run fires + stores on the FIRST
    # click, before the body resolves the result below — not the fragile `if st.button(): ...`
    # form whose True was lost to rerun churn, needing a second click.
    run_payload = json.loads(baseline.raw_snapshot)["payload"]
    st.sidebar.button(
        "Run schedule", type="primary",
        on_click=_run_and_store,
        args=(session, run_payload, baseline.plan_id, run_config, validator,
              store, executor, repository, session.get_scenario()))
    # A blocked run stashes its reason; render it once (popped) here in the main column + sidebar.
    run_error = st.session_state.pop(_RUN_ERROR, None)
    if run_error is not None:
        stage, issues = run_error
        st.sidebar.error(f"Cannot run — blocked at {stage}.")
        st.error(f"Run blocked at {stage}.")
        _render_issues(issues)

    # --- resolve the selected run result; its header + views now live on the Results page ---
    result = None
    selected_id = session.get_selected_result_id()
    if selected_id:
        result = session.get_run_result(selected_id)

    # --- navigation: workflow pages over the shared prologue. Each st.Page callable is zero-arg
    #     (Streamlit's contract) and closes over the prologue locals resolved above; only the
    #     selected page's body runs on a rerun, so the load-bearing prologue must stay ABOVE
    #     pg.run() — every page still sees a fresh source load, schedule pick, and run-config. ---
    # The Results page's guided augmentation (4.2) and priority-rule sweep (4.3) both RUN schedules from
    # the page; hand them ONE seam closing over the SAME store+executor the sidebar Run uses (so run ids
    # increment, never collide) — the page layer stays free of infrastructure imports, the composition
    # root remains the wirer. 4.2 varies the scenario (run_config defaults to the live one); 4.3 varies
    # the run_config (a different priority_rule per run). The param aliases the live config so a caller
    # that omits run_config still runs with the sidebar's selection.
    live_run_config = run_config
    def _run_plan(run_config=None, scenario=None) -> PipelineResult:
        payload = json.loads(baseline.raw_snapshot)["payload"]
        return run_pipeline(
            payload, baseline.plan_id, run_config or live_run_config,
            validator=validator, store=store, executor=executor, repository=repository,
            scenario=scenario)

    # Replan seam (Phase 5): the Replan page's "Run replan" reschedules from the scenario's
    # as-of hour T (services.prepare_replan → engine replan()). Runs on the SAME
    # store+executor as the sidebar Run (ids increment, never collide) and, on success,
    # stores + selects the result so it lands on Results like any run. Returns the
    # PipelineResult so the page can render preflight warnings / a block reason inline.
    def _run_replan(scenario) -> PipelineResult:
        payload = json.loads(baseline.raw_snapshot)["payload"]
        # Chain from the session's rolling plan of record (if any adopted steps): the adapter
        # replays the adopted chain before this candidate so it freezes the adopted schedule,
        # not the from-hour-0 baseline. None/empty ⇒ a plain hub-and-spoke replan.
        outcome = run_replan_pipeline(
            payload, baseline.plan_id, live_run_config,
            validator=validator, store=store, executor=executor, repository=repository,
            scenario=scenario, plan_of_record=session.get_plan_of_record())
        if outcome.ok:
            session.add_run_result(outcome.result)
            session.set_selected_result_id(outcome.result.run_id)
        return outcome

    def _page_plan() -> None:
        _render_plan_page(session, baseline, validator)

    def _page_results() -> None:
        _render_results_page(session, baseline, result, run_config, run_plan=_run_plan)

    def _page_replan() -> None:
        _render_replan(session, baseline, run_replan=_run_replan)

    def _page_scenarios() -> None:
        _render_scenarios(session, baseline)

    # Land on Results by default: the "Run schedule" button lives in the shared sidebar, but a run's
    # output (header + plots + DAG) only renders on the Results page. Landing here means the basic
    # load-a-plan-then-Run flow shows the result on the FIRST click — a plain button rerun keeps the
    # active page, so the just-stored, just-selected result resolves and renders in place. (The tabs
    # keep their build→run→what-if→library order; only the default landing changes.)
    pg = st.navigation([
        st.Page(_page_plan,      title="Plan"),
        st.Page(_page_results,   title="Results", default=True),
        st.Page(_page_replan,    title="Replan"),
        st.Page(_page_scenarios, title="Scenarios"),
    ])
    pg.run()


if __name__ == "__main__":
    main()
