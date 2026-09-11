"""Streamlit sidebar prologue: source picker, validation badge, selectors, run config."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from prismGui.app._streamlit import st
from prismGui.domain.run_config import PRIORITY_RULES, RunConfig, SGSVariant
from prismGui.app.edit_model import _NO_VALUE
from prismGui.app.pipeline import discover_samples
from prismGui.app.scenario_model import _mint_scenario


def _render_validation_badge(load) -> None:
    """Compact sidebar badge for the load's validation outcome — blocked / warnings / valid.
    The full issue list stays in the main column (``_render_issues``); the sidebar is too
    narrow for a table."""
    n_warn = sum(1 for i in load.issues if i.severity.value == "warning")
    if not load.ok:
        n_err = sum(1 for i in load.issues if i.severity.value == "error")
        st.sidebar.error(f"⛔ Plan invalid — {n_err} blocking error(s)")
    elif n_warn:
        st.sidebar.warning(f"⚠️ Valid — {n_warn} warning(s)")
    else:
        st.sidebar.success("✅ Plan valid")

def _source_key(reference_plan) -> str:
    """A stable signature of the source a baseline was loaded from: its logical id plus
    the canonical content hash. Reselecting the same file yields the same key (edits are
    preserved); picking a different sample / uploading a new file changes it."""
    return f"{reference_plan.plan_id}::{reference_plan.plan_hash}"

def _pick_source():
    """Sidebar sample-selectbox + uploader. Returns (raw_plan_dict | None, plan_id)."""
    st.sidebar.header("Plan source")
    samples = discover_samples()
    names = list(samples)
    choice = st.sidebar.selectbox(
        "Sample project", ["— choose —", *names],
        help="Shipping RCPSP examples from doc/demos/rcpsp/examples/.")
    uploaded = st.sidebar.file_uploader("…or upload a plan (JSON)", type=["json"])

    if uploaded is not None:
        try:
            return json.load(uploaded), Path(uploaded.name).stem
        except json.JSONDecodeError as exc:
            st.sidebar.error(f"Not valid JSON: {exc}")
            return None, ""
    if choice in samples:
        return json.loads(Path(samples[choice]).read_text()), choice
    return None, ""

_SCHEDULE_PICK_WIDGET = "prism_current_schedule_pick"   # keyed selectbox

_SCHEDULE_PENDING = "prism_schedule_pending"            # a programmatic selection to apply next run

def _select_schedule_next_run(scenario_id: Optional[str]) -> None:
    """Queue a programmatic current-schedule selection (a scenario_id, or None == baseline) to
    apply on the NEXT run, then rerun. A keyed widget's value can only be set BEFORE its widget
    is instantiated, so New / rename / delete anywhere cannot poke ``_SCHEDULE_PICK_WIDGET``
    directly (the widget is already built by the time those buttons fire) — they stash the
    choice here and ``_render_schedule_selector`` applies it at the top of the next run."""
    st.session_state[_SCHEDULE_PENDING] = scenario_id
    st.rerun()

def _render_schedule_selector(session, baseline) -> None:
    """Sidebar 'current schedule' picker: the baseline or one of the stored scenarios. The
    pick is the INPUT axis — it drives which schedule every tab operates on (Data viewer,
    Graphs, edit target) and what the Run button runs; the selected RUN RESULT (output) is a
    separate axis. A [+ New scenario] button mints an empty named scenario bound to the
    current baseline and selects it.

    Rerun-safety: the selectbox is keyed, so its widget value persists across reruns
    independently of the session pointer. A queued programmatic selection is applied — and any
    stale stored value (a scenario dropped by a baseline switch or deleted) is popped — BEFORE
    the widget is created; a keyed selectbox whose stored value is outside its options raises
    ``StreamlitAPIException``."""
    st.sidebar.header("Current schedule")
    scenarios = session.list_scenarios()
    ids = [s.scenario_id for s in scenarios]
    options = [None, *ids]                       # None == the baseline
    names = {s.scenario_id: (s.name or s.scenario_id) for s in scenarios}

    # Apply a queued programmatic selection before the widget instantiates (only legal here).
    pending = st.session_state.pop(_SCHEDULE_PENDING, _NO_VALUE)
    if pending is not _NO_VALUE:
        st.session_state[_SCHEDULE_PICK_WIDGET] = pending

    current = session.get_current_scenario_id()
    if current is not None and current not in ids:      # pointer's scenario is gone
        session.set_current_scenario_id(None)
        current = None
    # Drop a stale keyed-widget value so the selectbox never sees a value outside its options.
    stored = st.session_state.get(_SCHEDULE_PICK_WIDGET, _NO_VALUE)
    if stored is not _NO_VALUE and stored not in options:
        st.session_state.pop(_SCHEDULE_PICK_WIDGET, None)

    picked = st.sidebar.selectbox(
        "Editing / running", options,
        format_func=lambda sid: "Baseline" if sid is None else names.get(sid, sid),
        key=_SCHEDULE_PICK_WIDGET,
        help="The schedule every tab operates on and the Run button runs — the baseline, or a "
             "what-if scenario. Create scenarios with the button below or in What-if / Edit.")
    if picked != current:                                # mirror the widget → the session pointer
        session.set_current_scenario_id(picked)

    if st.sidebar.button("➕ New scenario", key="prism_new_scenario_sidebar"):
        scn = _mint_scenario(baseline, existing_ids=ids)
        session.add_scenario(scn)
        _select_schedule_next_run(scn.scenario_id)

def _pick_run_config(plan_id: str) -> RunConfig:
    """Sidebar SGS + priority-rule + seed + horizon selectors -> a RunConfig. The rule list
    is the engine's own 22-key library, so an unknown key is impossible. The scheduling
    horizon maps to PRISM's ``max_time_hours=`` (0 -> None -> engine default) and is folded
    into the run-config hash, so it is part of provenance/freshness."""
    st.sidebar.header("Run configuration")
    sgs_value = st.sidebar.selectbox(
        "Schedule-generation scheme", [v.value for v in SGSVariant], index=0)
    rule = st.sidebar.selectbox(
        "Priority rule", list(PRIORITY_RULES),
        index=PRIORITY_RULES.index("lf"))
    seed = int(st.sidebar.number_input("Seed", min_value=0, value=42, step=1))
    horizon = st.sidebar.number_input(
        "Scheduling horizon (h)", min_value=0.0, value=0.0, step=1.0,
        help="Schedule-length cap passed to PRISM (max_time_hours). 0 = engine default.")
    return RunConfig(run_config_id=f"rc-{plan_id}", sgs=SGSVariant(sgs_value),
                     priority_rule=rule, seed=seed,
                     scheduling_horizon_hours=(horizon if horizon > 0 else None))
