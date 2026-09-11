"""app/main.py — the Streamlit shell for the PRISM GUI thin slice.

Run it with::

    streamlit run src/prismGui/app/main.py

This is the ONLY module that imports Streamlit, and it is the composition root: it wires
the concrete adapters (``OutageValidatorAdapter``, ``InMemorySnapshotStore``,
``InProcessPrismExecutor``, ``InMemoryRepository``) into the application services and
renders the result. It owns no scheduling logic and no policy — every decision (validity,
disposition, freshness) comes from the domain via ``application.services``.

The thin-slice loop it presents (plan Increment 1):

    pick a sample / upload a plan  →  validation panel  →  SGS + priority-rule selectors
      →  Run  →  makespan / CPM lower-bound / optimism-gap metrics + schedule table
      +  disposition badge (+ freshness of the shown result).

Layering discipline: the Streamlit import is *guarded* so the module imports cleanly in an
environment without Streamlit (for a headless wiring check); all ``st.*`` calls live inside
``main()`` and the ``_render_*`` helpers, never at import time. The streamlit-free helpers
(``discover_samples``, ``run_pipeline``) are what the integration wiring test drives.
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

try:  # guarded: the module must import even where Streamlit is not installed.
    import streamlit as st
    _HAS_STREAMLIT = True
except ModuleNotFoundError:  # pragma: no cover - exercised only in a Streamlit-less env
    st = None  # type: ignore[assignment]
    _HAS_STREAMLIT = False

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
from prismGui.domain.run_config import ModeSelection, PRIORITY_RULES, RunConfig, SGSVariant
from prismGui.domain.scenario import DurationOverride, ResourceChange, Scenario
from prismGui.infrastructure.memory_repository import InMemoryRepository
from prismGui.infrastructure.memory_snapshot_store import InMemorySnapshotStore
from prismGui.infrastructure.validation_adapter import OutageValidatorAdapter

# app/main.py -> parents: [0]=app [1]=prismGui [2]=src [3]=repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "src" / "CPM" / "outage_schema.json"
EXAMPLES_DIR = REPO_ROOT / "doc" / "demos" / "rcpsp" / "examples"


# =============================================================================
# streamlit-free helpers (importable / testable without Streamlit)
# =============================================================================

def discover_samples() -> dict[str, str]:
    """Map each shipping sample project's display name to its absolute path (sorted).
    The canonical location the CPM suite reads — NOT the byte-identical tests/CPMmodel/
    copies."""
    if not EXAMPLES_DIR.is_dir():
        return {}
    return {p.stem: str(p) for p in sorted(EXAMPLES_DIR.glob("*.json"))}


@dataclass(frozen=True)
class PipelineResult:
    """Outcome of the app's load→prepare→run pipeline. ``ok`` is False if the load or the
    preparation blocked (``result`` is then None and ``issues`` explains why); otherwise
    ``result`` is the terminal ``RunResult``."""
    ok: bool
    stage: str                     # "load" | "prepare" | "run"
    issues: tuple
    result: Any = None
    reference_plan: Any = None


def run_pipeline(
    raw_plan: dict,
    plan_id: str,
    run_config: RunConfig,
    *,
    validator: OutageValidatorAdapter,
    store: InMemorySnapshotStore,
    executor,
    repository: Optional[InMemoryRepository] = None,
    scenario: Optional[Scenario] = None,
) -> PipelineResult:
    """The exact sequence the Run button triggers, factored out so it can be driven
    headlessly: load+validate → prepare_run → run. When a ``scenario`` is supplied it is
    materialized into the effective plan (baseline + delta) before the run; ``None`` runs
    the plain baseline. Blocking is reported per stage; a non-ok pipeline never fabricates
    a result."""
    load = services.load_and_validate(plan_id, raw_plan, validator)
    if not load.ok:
        return PipelineResult(ok=False, stage="load", issues=load.issues)

    prep = services.prepare_run(load.reference_plan, scenario, run_config, store, validator=validator)
    if not prep.ok:
        return PipelineResult(ok=False, stage="prepare", issues=prep.issues,
                              reference_plan=load.reference_plan)

    result = services.run(prep.run_request, executor, repository=repository)
    return PipelineResult(ok=True, stage="run", issues=result.issues, result=result,
                          reference_plan=load.reference_plan)


def build_validator() -> OutageValidatorAdapter:
    """The ValidationPort adapter over the shipping outage schema."""
    return OutageValidatorAdapter(str(SCHEMA_PATH))


def _make_executor(store: InMemorySnapshotStore):
    """Build the in-process PRISM executor (lazy import keeps the PRISM dependency inside
    the composition root's run path)."""
    from prismGui.infrastructure.prism_adapter import InProcessPrismExecutor
    return InProcessPrismExecutor(store)


# =============================================================================
# scenario authoring (streamlit-free): build/extend a Scenario overlay
# =============================================================================
# A Scenario is an immutable OVERLAY delta bound to a baseline revision by
# ``base_plan_hash``. These helpers fold a single what-if (a task duration override or a
# resource-availability change) into a new frozen Scenario, so the render panel stays a
# thin ``st.*`` shell over pure, unit-testable transforms — the same discipline the editor
# builders follow. The domain (materialize) is the arbiter of validity; these only shape
# the delta. An EMPTY scenario (no overrides, no changes) is treated as "no scenario".

# The resource-change intent radio: a resource-availability edit is canonically ambiguous,
# so the user tags it. A what-if becomes a Scenario overlay; a baseline correction is
# redirected to the editor (a baseline edit), never authored here.
_SCN_INTENTS = ("What-if (scenario)", "Baseline correction")


def _is_whatif(intent: str) -> bool:
    return intent == _SCN_INTENTS[0]


def _scenario_is_empty(scenario: Optional[Scenario]) -> bool:
    """True when the scenario touches nothing (no duration overrides, no resource changes),
    so the run should use the plain baseline (materialize's scenario=None mirror path)."""
    return scenario is None or (not scenario.duration_overrides and not scenario.resource_changes)


def _new_scenario_for(baseline) -> Scenario:
    """A fresh empty Scenario bound to the given baseline revision (by plan_hash), with a
    FIXED per-baseline id — the internal single-overlay rebuild target used by
    ``_scenario_base``. For a NEW named scenario the analyst keeps alongside others, use
    ``_mint_scenario`` (a unique id), never this."""
    return Scenario(
        scenario_id=f"scn-{baseline.plan_id}",
        base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash,
        name="session what-if",
    )


def _mint_scenario(baseline, existing_ids=(), name: Optional[str] = None) -> Scenario:
    """A fresh empty Scenario bound to ``baseline`` with a UNIQUE id, so several scenarios
    coexist (unlike ``_new_scenario_for``'s fixed per-baseline id, which would collide). When
    no ``name`` is given an auto label is chosen from how many already exist ("Scenario A",
    "Scenario B", …). ``existing_ids`` guards the (vanishingly unlikely) id clash."""
    existing = tuple(existing_ids)
    scenario_id = f"scn-{uuid.uuid4().hex[:8]}"
    while scenario_id in existing:
        scenario_id = f"scn-{uuid.uuid4().hex[:8]}"
    if not name:
        n = len(existing)
        name = f"Scenario {chr(ord('A') + n)}" if n < 26 else f"Scenario {n + 1}"
    return Scenario(
        scenario_id=scenario_id,
        base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash,
        name=name,
    )


def _scenario_base(scenario: Optional[Scenario], baseline) -> Scenario:
    """The Scenario to extend: the session's when it targets THIS baseline, else a fresh
    one — a scenario bound to a superseded revision is rebuilt so its overrides never leak
    across a baseline change (the session's resolve_for_new_baseline normally clears such a
    scenario first; this is the belt-and-suspenders guard)."""
    if scenario is None or scenario.base_plan_hash != baseline.plan_hash:
        return _new_scenario_for(baseline)
    return scenario


def _scenario_duration_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Duration overrides as display/removal rows ``{index, task_id, duration_hours}``."""
    if scenario is None:
        return []
    return [{"index": i, "task_id": ov.task_id, "duration_hours": ov.duration_hours}
            for i, ov in enumerate(scenario.duration_overrides or ())]


def _scenario_resource_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Resource changes as rows ``{index, skill_type, from_hour, new_count}``."""
    if scenario is None:
        return []
    return [{"index": i, "skill_type": rc.skill_type, "from_hour": rc.from_hour,
             "new_count": rc.new_count}
            for i, rc in enumerate(scenario.resource_changes or ())]


def _add_duration_override(scenario: Optional[Scenario], baseline, task_id: str,
                           duration_hours: float) -> Scenario:
    """New Scenario with a duration override for ``task_id`` (last-write-wins per task: an
    existing override for the same task is replaced, never duplicated)."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(ov for ov in (base.duration_overrides or ()) if ov.task_id != task_id)
    return replace(base, duration_overrides=kept + (
        DurationOverride(task_id=task_id, duration_hours=float(duration_hours)),))


def _remove_duration_override(scenario: Optional[Scenario], baseline, task_id: str) -> Scenario:
    """New Scenario with the override for ``task_id`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(ov for ov in (base.duration_overrides or ()) if ov.task_id != task_id)
    return replace(base, duration_overrides=kept or None)


def _add_resource_change(scenario: Optional[Scenario], baseline, skill_type: str,
                         from_hour: float, new_count: int) -> Scenario:
    """New Scenario with a resource change (last-write-wins per (skill_type, from_hour), so
    the UI can never author the duplicate-hour case materialize would reject)."""
    base = _scenario_base(scenario, baseline)
    fh = float(from_hour)
    kept = tuple(rc for rc in (base.resource_changes or ())
                 if not (rc.skill_type == skill_type and rc.from_hour == fh))
    return replace(base, resource_changes=kept + (
        ResourceChange(skill_type=skill_type, from_hour=fh, new_count=int(new_count)),))


def _remove_resource_change(scenario: Optional[Scenario], baseline, index: int) -> Scenario:
    """New Scenario with the resource change at ``index`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    changes = list(base.resource_changes or ())
    if 0 <= index < len(changes):
        del changes[index]
    return replace(base, resource_changes=tuple(changes) or None)


# =============================================================================
# Streamlit-backed session state (the app's SessionState implementation)
# =============================================================================

class StreamlitSessionState:
    """``services.SessionState`` over ``st.session_state`` — the ONLY code that knows the
    session-state keys. Constructed inside ``main()`` (touches ``st``)."""

    _BASELINE = "prism_baseline"
    _DRAFT = "prism_draft"
    _SCENARIOS = "prism_scenarios"                 # dict[scenario_id, Scenario], insertion-ordered
    _CURRENT_SCHEDULE = "prism_current_schedule"   # scenario_id, or None == the baseline
    _RUN_CONFIG = "prism_run_config"
    _RESULTS = "prism_results"
    _SELECTED = "prism_selected_id"
    _SOURCE_KEY = "prism_source_key"      # signature of the last file-reloaded source
    _MODE_SELECTIONS = "prism_mode_selections"   # tuple[ModeSelection, ...] merged into RunConfig

    def __init__(self) -> None:
        st.session_state.setdefault(self._BASELINE, None)
        st.session_state.setdefault(self._DRAFT, None)
        st.session_state.setdefault(self._SCENARIOS, {})
        st.session_state.setdefault(self._CURRENT_SCHEDULE, None)
        st.session_state.setdefault(self._RUN_CONFIG, None)
        st.session_state.setdefault(self._RESULTS, {})
        st.session_state.setdefault(self._SELECTED, None)
        st.session_state.setdefault(self._SOURCE_KEY, None)
        st.session_state.setdefault(self._MODE_SELECTIONS, ())

    def get_baseline(self):
        return st.session_state[self._BASELINE]

    def set_baseline(self, plan) -> None:
        st.session_state[self._BASELINE] = plan

    def get_draft(self):
        return st.session_state[self._DRAFT]

    def set_draft(self, draft) -> None:
        st.session_state[self._DRAFT] = draft

    def clear_draft(self) -> None:
        st.session_state[self._DRAFT] = None

    # Streamlit-only: not part of the SessionState Protocol. Tracks which source the
    # session baseline was last seeded from, so the top-of-main file reload overwrites
    # the baseline only when the selection changes — letting a committed edit survive
    # Streamlit's top-to-bottom rerun.
    def get_source_key(self):
        return st.session_state[self._SOURCE_KEY]

    def set_source_key(self, key) -> None:
        st.session_state[self._SOURCE_KEY] = key

    # Streamlit-only: not part of the SessionState Protocol. The run-time execution-mode
    # picks (task_id -> mode_name) merged into the RunConfig at Run — a run-config concern,
    # not a scenario delta (keeping it on RunConfig preserves the run-config hash / freshness).
    def get_mode_selections(self) -> tuple[ModeSelection, ...]:
        return tuple(st.session_state[self._MODE_SELECTIONS])

    def set_mode_selections(self, selections) -> None:
        st.session_state[self._MODE_SELECTIONS] = tuple(selections)

    def get_scenario(self):
        """The scenario the current-schedule pointer names, or None (== the baseline)."""
        current = st.session_state[self._CURRENT_SCHEDULE]
        if current is None:
            return None
        return st.session_state[self._SCENARIOS].get(current)

    def set_scenario(self, scenario) -> None:
        """Shim: None detaches to the baseline (keeps stored scenarios); a scenario is
        stored (add-or-update) and made current."""
        if scenario is None:
            st.session_state[self._CURRENT_SCHEDULE] = None
            return
        st.session_state[self._SCENARIOS][scenario.scenario_id] = scenario
        st.session_state[self._CURRENT_SCHEDULE] = scenario.scenario_id

    def list_scenarios(self) -> tuple:
        return tuple(st.session_state[self._SCENARIOS].values())

    def add_scenario(self, scenario) -> None:
        st.session_state[self._SCENARIOS][scenario.scenario_id] = scenario

    def remove_scenario(self, scenario_id: str) -> None:
        st.session_state[self._SCENARIOS].pop(scenario_id, None)

    def get_current_scenario_id(self):
        return st.session_state[self._CURRENT_SCHEDULE]

    def set_current_scenario_id(self, scenario_id) -> None:
        st.session_state[self._CURRENT_SCHEDULE] = scenario_id

    def get_run_config(self):
        return st.session_state[self._RUN_CONFIG]

    def set_run_config(self, run_config) -> None:
        st.session_state[self._RUN_CONFIG] = run_config

    def list_run_results(self) -> tuple:
        return tuple(st.session_state[self._RESULTS].values())

    def add_run_result(self, result) -> None:
        st.session_state[self._RESULTS][result.run_id] = result

    def get_run_result(self, run_id: str):
        return st.session_state[self._RESULTS].get(run_id)

    def get_selected_result_id(self):
        return st.session_state[self._SELECTED]

    def set_selected_result_id(self, run_id) -> None:
        st.session_state[self._SELECTED] = run_id


# =============================================================================
# rendering (all st.* lives below this line)
# =============================================================================

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _issue_rows(issues) -> list[dict]:
    rows = [
        {
            "severity": i.severity.value,
            "code": i.code_value,
            "category": i.category.value,
            "entity": f"{i.entity_type or ''}:{i.entity_id or ''}".strip(":"),
            "field": i.field_path or "",
            "message": i.message,
        }
        for i in issues
    ]
    rows.sort(key=lambda r: _SEVERITY_ORDER.get(r["severity"], 9))
    return rows


def _render_issues(issues, *, empty_msg: str = "No issues.") -> None:
    if not issues:
        st.caption(empty_msg)
        return
    n_err = sum(1 for i in issues if i.severity.value == "error")
    n_warn = sum(1 for i in issues if i.severity.value == "warning")
    st.caption(f"{n_err} error(s), {n_warn} warning(s)")
    st.dataframe(_issue_rows(issues), use_container_width=True, hide_index=True)


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


def _gantt_rows(schedule) -> list[dict]:
    """One streamlit-free row per scheduled activity for the Gantt: numeric hour-offsets
    plus the float-class / chain metadata that drive bar color and the chain highlight.
    Ordered as the schedule is (start-sorted), so the chart reads top-to-bottom by start."""
    return [
        {
            "task": a.task_id,
            "start": a.start_hour,
            "end": a.end_hour,
            "duration": a.duration,
            "delay": a.delay_hours,
            "float_class": a.float_class.value if a.float_class is not None else "",
            "on_chain": bool(a.on_constrained_chain),
            "description": a.description or "",
        }
        for a in schedule.activities
    ]


def _resource_util_rows(util) -> list[dict]:
    """Flatten a ResourceUtilizationDTO (series × intervals) into one streamlit-free row
    per interval for the step chart. A run without a utilization timeline (``util is
    None``) yields ``[]`` — the render helper then shows a caption, not an empty chart."""
    if util is None:
        return []
    return [
        {
            "skill": series.skill_type,
            "start_hour": iv.start_hour,
            "end_hour": iv.end_hour,
            "demand": iv.demand,
            "available": iv.available,
        }
        for series in util.series
        for iv in series.intervals
    ]


# Gantt bar color by float class (the old Altair chart auto-colored the categories; Plotly
# needs the mapping stated). Keys are FloatClass.value; unknown/empty falls back to grey.
_FLOAT_CLASS_COLORS = {
    "critical": "#e74c3c",
    "zero_float": "#f39c12",
    "positive_float": "#2ecc71",
    "": "#7f8c8d",
}


def _step_series(rows: list[dict]) -> tuple[list, list, list]:
    """Interval rows -> (x, demand, available) points for a step (``line_shape='hv'``) plot:
    one point per interval start (the level held until the next start) plus a closing point
    at the last interval's end, reproducing the former Altair ``step-after`` area."""
    rows = sorted(rows, key=lambda r: r["start_hour"])
    xs, dem, avail = [], [], []
    for r in rows:
        xs.append(r["start_hour"])
        dem.append(r["demand"])
        avail.append(r["available"])
    if rows:
        xs.append(rows[-1]["end_hour"])
        dem.append(rows[-1]["demand"])
        avail.append(rows[-1]["available"])
    return xs, dem, avail


def _render_plots(result) -> None:
    """Gantt + per-skill resource utilization on ONE shared, scrollable time axis.

    Builds a single Plotly figure (``make_subplots`` with ``shared_xaxes=True``) so the Gantt
    (row 1) and each skill's demand-vs-available step chart (rows 2..) line up perfectly on
    the same "hours since project start" x-axis; a range slider on the bottom axis gives
    horizontal scrolling for long-horizon schedules. Fed by the streamlit-free ``_gantt_rows``
    / ``_resource_util_rows`` builders; Plotly is imported lazily (the discipline the former
    Altair charts followed) so the module still imports with neither Streamlit nor Plotly
    installed. The Plots tab needs Plotly present in the run environment."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    schedule = result.schedule
    g_rows = _gantt_rows(schedule)
    util = result.diagnostics.resource_utilization if result.diagnostics is not None else None
    u_rows = _resource_util_rows(util)
    skills = sorted({r["skill"] for r in u_rows})

    if not g_rows:
        st.caption("No scheduled activities to chart.")
    else:
        n_rows = 1 + len(skills)
        row_heights = [0.5, *([0.5 / len(skills)] * len(skills))] if skills else [1.0]
        fig = make_subplots(
            rows=n_rows, cols=1, shared_xaxes=True, vertical_spacing=0.05,
            row_heights=row_heights,
            subplot_titles=["Gantt", *[f"{s} — crew" for s in skills]])

        # --- row 1: Gantt — one horizontal bar per activity ------------------
        for r in g_rows:
            fig.add_trace(
                go.Bar(
                    x=[r["end"] - r["start"]], base=[r["start"]], y=[r["task"]],
                    orientation="h", width=0.6,
                    marker_color=_FLOAT_CLASS_COLORS.get(r["float_class"], "#7f8c8d"),
                    marker_line_color="#111", marker_line_width=1.0,
                    opacity=1.0 if r["on_chain"] else 0.55, showlegend=False,
                    hovertemplate=(
                        f"<b>{r['task']}</b><br>start {r['start']:g}h · end {r['end']:g}h"
                        f" · dur {r['duration']:g}h<br>float {r['float_class'] or '—'}"
                        f"{' · on chain' if r['on_chain'] else ''}<extra></extra>")),
                row=1, col=1)
        fig.update_yaxes(autorange="reversed", row=1, col=1)  # first activity on top

        # --- rows 2..: one demand/available step chart per skill -------------
        for i, skill in enumerate(skills, start=2):
            xs, dem, avail = _step_series([r for r in u_rows if r["skill"] == skill])
            fig.add_trace(
                go.Scatter(x=xs, y=dem, mode="lines", line_shape="hv",
                           line_color="#3498db", fill="tozeroy",
                           fillcolor="rgba(52,152,219,0.35)", name="demand",
                           legendgroup="demand", showlegend=(i == 2)),
                row=i, col=1)
            fig.add_trace(
                go.Scatter(x=xs, y=avail, mode="lines", line_shape="hv",
                           line_color="#e67e22", line_dash="dash", name="available",
                           legendgroup="available", showlegend=(i == 2)),
                row=i, col=1)
            fig.update_yaxes(title_text="crew", rangemode="tozero", row=i, col=1)

        fig.update_xaxes(title_text="hours since project start", row=n_rows, col=1)
        fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.06), row=n_rows, col=1)
        fig.update_layout(
            height=260 + 150 * max(len(skills), 1), bargap=0.2,
            margin=dict(l=10, r=10, t=40, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Schedule**")
    _render_schedule_table(schedule)
    _render_schedule_export(schedule)


def _render_schedule_table(schedule) -> None:
    rows = [
        {
            "task": a.task_id,
            "start (h)": a.start_hour,
            "end (h)": a.end_hour,
            "duration (h)": a.duration,
            "delay (h)": a.delay_hours,
            "float": a.float_class.value if a.float_class is not None else "",
            "on chain": "★" if a.on_constrained_chain else "",
            "description": a.description or "",
        }
        for a in schedule.activities
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _schedule_csv(schedule) -> str:
    """The full schedule as CSV text (stdlib ``csv``), including the columns the on-screen
    table omits: ``tf_actual_hours``, ``wbs_group``, and the resolved ``actual_resources``
    serialized as ``MECHANIC:2;HP_TECH:1``. Streamlit-free — returns a string; the download
    button is the render helper."""
    header = [
        "task_id", "start_hour", "end_hour", "duration", "delay_hours",
        "float_class", "on_constrained_chain", "tf_actual_hours", "wbs_group",
        "actual_resources", "description",
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for a in schedule.activities:
        resources = ";".join(f"{r.skill_type}:{r.crew_count}" for r in a.actual_resources)
        writer.writerow([
            a.task_id, a.start_hour, a.end_hour, a.duration, a.delay_hours,
            a.float_class.value if a.float_class is not None else "",
            a.on_constrained_chain,
            "" if a.tf_actual_hours is None else a.tf_actual_hours,
            a.wbs_group or "",
            resources,
            a.description or "",
        ])
    return buf.getvalue()


def _render_schedule_export(schedule) -> None:
    st.download_button(
        "Download schedule (CSV)",
        data=_schedule_csv(schedule),
        file_name="prism_schedule.csv",
        mime="text/csv",
    )


_DISPOSITION_INDICATOR_LABELS = (
    ("input_valid", "Input valid"),
    ("schedule_complete", "Schedule complete"),
    ("hard_feasible", "Hard-constraint feasible"),
    ("has_unscheduled_tasks", "Has unscheduled tasks"),
    ("has_window_violations", "Has window violations"),
    ("audit_passed", "Audit passed"),
)


def _disposition_rows(disposition) -> list[dict]:
    """The 6 tri-state disposition indicators as streamlit-free rows ``{indicator, state}``
    (state == ``Tri.value``), in a stable display order."""
    ind = disposition.indicators
    return [
        {"indicator": label, "state": getattr(ind, attr).value}
        for attr, label in _DISPOSITION_INDICATOR_LABELS
    ]


def _render_disposition_badge(disposition) -> None:
    overall = disposition.overall
    if overall is DispositionOverall.READY:
        st.success("✅ READY")
    elif overall is DispositionOverall.READY_WITH_WARNINGS:
        st.warning("⚠️ READY — with warnings")
    else:
        st.error("⛔ BLOCKED")


def _render_disposition_panel(disposition) -> None:
    """The overall banner plus the 6-indicator breakdown as a proper grid (replaces the
    Increment-1 one-line caption)."""
    _render_disposition_badge(disposition)
    st.dataframe(_disposition_rows(disposition), use_container_width=True, hide_index=True)


_FRESHNESS_LABEL = {
    Freshness.CURRENT: "🟢 current",
    Freshness.STALE: "🔴 stale (inputs changed since this run)",
    Freshness.DIFFERENT_CONFIG: "🟠 different config than selected",
}

_FRESHNESS_REASON_LABEL = {
    "baseline_changed": "baseline plan changed since this run",
    "scenario_changed": "scenario changed since this run",
    "config_differs": "run config differs from the one now selected",
}

_PROVENANCE_FIELD_LABELS = (
    ("baseline_snapshot_hash", "Baseline snapshot"),
    ("effective_plan_hash", "Effective plan"),
    ("run_config_hash", "Run config"),
    ("schema_version", "Schema version"),
    ("canonicalization_version", "Canonicalization version"),
    ("app_version", "App version"),
    ("prism_version", "PRISM version"),
    ("run_id", "Run id"),
    ("timestamp", "Timestamp"),
    ("scenario_delta_hash", "Scenario delta"),
)


def _provenance_rows(provenance) -> list[dict]:
    """The 10 provenance fields as streamlit-free ``{field, value}`` rows in declaration
    order. The timestamp is rendered ISO-8601; a ``None`` scenario delta (no scenario
    applied) shows as an empty string, never the literal ``None``."""
    rows: list[dict] = []
    for attr, label in _PROVENANCE_FIELD_LABELS:
        value = getattr(provenance, attr)
        if attr == "timestamp":
            value = value.isoformat()
        elif value is None:
            value = ""
        rows.append({"field": label, "value": str(value)})
    return rows


def _render_provenance_freshness(result, freshness, reasons) -> None:
    """The freshness badge + the *why* (reason codes) as a caption, plus the full 10-field
    provenance record in a collapsed expander (an audit surface, not front-and-center)."""
    label = _FRESHNESS_LABEL.get(freshness, freshness.value)
    if reasons:
        why = "; ".join(_FRESHNESS_REASON_LABEL.get(r, r) for r in reasons)
        st.caption(f"Freshness: {label} — {why}")
    else:
        st.caption(f"Freshness: {label}")
    with st.expander("Provenance", expanded=False):
        st.dataframe(_provenance_rows(result.provenance),
                     use_container_width=True, hide_index=True)


def _render_results_header(result, session, baseline, run_config) -> None:
    """The persistent run summary shown above the tabs whenever a run is selected: disposition
    badge + headline metrics, with the 6-indicator disposition grid, provenance/freshness and
    audit findings tucked into expanders so the header stays compact. A non-COMPLETED run shows
    the failure and its diagnostics here (there is nothing to plot)."""
    st.subheader(f"Result — run `{result.run_id}`")
    if result.status is not RunResultStatus.COMPLETED:
        st.error(f"Run {result.status.value}.")
        _render_issues(result.issues, empty_msg="No diagnostics.")
        return

    s = result.schedule
    _render_disposition_badge(result.disposition)
    c1, c2, c3 = st.columns(3)
    c1.metric("Makespan (h)", f"{s.makespan_hours:g}")
    c2.metric("CPM lower bound (h)", f"{s.cpm_lower_bound_hours:g}")
    c3.metric("Optimism gap (h)", f"{s.optimism_gap_hours:g}")

    freshness, reasons = services.current_freshness_detail(
        result, baseline=baseline, scenario=session.get_scenario(), run_config=run_config)

    with st.expander("Disposition detail", expanded=False):
        st.dataframe(_disposition_rows(result.disposition),
                     use_container_width=True, hide_index=True)
    _render_provenance_freshness(result, freshness, reasons)  # freshness caption + provenance

    if result.issues:
        with st.expander(f"Audit findings ({len(result.issues)})", expanded=False):
            _render_issues(result.issues)

    if result.diagnostics is not None and result.diagnostics.fitness is not None:
        f = result.diagnostics.fitness
        st.caption(
            f"fitness: composite={f.composite:g} · makespan_ratio={f.makespan_ratio:g} · "
            f"delay_ratio={f.delay_ratio:g} · criticality_ratio={f.criticality_ratio:g} · "
            f"window_violations={f.n_window_violations}"
        )


def _data_viewer_rows(tasks: list[dict]) -> list[dict]:
    """One streamlit-free overview row per activity: scalar fields verbatim, nested/list fields
    summarized to a ``{n}`` / ``[n]`` count so the table stays scannable. Full per-field detail
    is the JSON expander's job (``_render_data_viewer``)."""
    rows: list[dict] = []
    for t in tasks:
        row: dict = {}
        for k, v in t.items():
            if isinstance(v, dict):
                row[k] = f"{{{len(v)}}}"
            elif isinstance(v, list):
                row[k] = f"[{len(v)}]"
            else:
                row[k] = v
        rows.append(row)
    return rows


def _current_schedule_payload(baseline, scenario) -> tuple[dict, Optional[str]]:
    """The effective payload for the current schedule (streamlit-free): the plain baseline when
    ``scenario`` is None, else the baseline with the scenario overlay MATERIALIZED in (duration
    overrides, resource what-ifs, emergent tasks). Returns ``(payload, warning)`` — ``warning``
    is set when the scenario cannot be materialized (a stale binding or a conflict), in which
    case the plain baseline payload is returned so the caller still renders something."""
    if scenario is None:
        return json.loads(baseline.raw_snapshot)["payload"], None
    outcome = materialize(baseline, scenario)
    if not outcome.ok or outcome.effective_plan is None:
        return (json.loads(baseline.raw_snapshot)["payload"],
                "This scenario could not be applied to the baseline; showing the baseline.")
    return json.loads(outcome.effective_plan.raw_snapshot)["payload"], None


def _schedule_label(session, baseline) -> str:
    """Human label for the current schedule — 'Baseline' or the scenario's name/id."""
    scenario = session.get_scenario()
    if scenario is None:
        return "Baseline"
    return f"Scenario “{scenario.name or scenario.scenario_id}”"


def _render_data_viewer(session, baseline) -> None:
    """Read-only tabular view of the CURRENT schedule's activities and their data fields — the
    plan INPUT, available before any run. Shows the baseline when Baseline is current, else the
    baseline with the selected scenario's overlay materialized in (so a duration override or an
    emergent task is visible here). Nested per-task detail is offered as raw JSON in a per-
    activity expander. Scheduled start/finish times are output — they live under Plots."""
    scenario = session.get_scenario()
    payload, warning = _current_schedule_payload(baseline, scenario)
    tasks = payload.get("tasks", [])
    if warning:
        st.warning(warning)
    st.caption(f"**{_schedule_label(session, baseline)}** — `{baseline.plan_id}` — "
               f"{len(tasks)} activit(ies). Plan input (baseline + any overlay); scheduled "
               f"times appear under **Plots** after a run.")
    if not tasks:
        st.info("This plan has no activities.")
        return
    st.dataframe(_data_viewer_rows(tasks), use_container_width=True, hide_index=True)
    with st.expander("Per-activity detail (all fields)", expanded=False):
        labels = [t.get("task_id", f"#{i}") for i, t in enumerate(tasks)]
        pick = st.selectbox("Activity", range(len(tasks)),
                            format_func=lambda k: labels[k], key="prism_dv_task")
        st.json(tasks[pick])


def _patch_rows(patches) -> list[dict]:
    """The pending patch log as streamlit-free ``{#, action, path, value}`` rows, in the
    order they were staged (the audit trail apply_patch appends to)."""
    return [
        {
            "#": n,
            "action": p.action.value,
            "path": p.path,
            "value": "" if p.value is None else json.dumps(p.value),
        }
        for n, p in enumerate(patches, start=1)
    ]


# -----------------------------------------------------------------------------
# structured editor form builders (streamlit-free, data -> PatchOp; unit-tested
# in test_m_app_shell.py exactly like _gantt_rows / _resource_util_rows). Every
# builder targets the RAW payload tree's schema-shaped keys, so each PatchOp feeds
# straight through domain.apply_patch. Pointer-engine rules (plan._apply): on a
# dict parent ADD sets-or-creates while REPLACE needs the key present; on a list,
# ADD with a trailing '-' appends. Hence REPLACE for always-present keys
# (duration, available_count), ADD for optional keys (resource_type, description)
# and list appends (successors/-).
# -----------------------------------------------------------------------------

def _as_float(value, default: float = 0.0) -> float:
    """Coerce a working-tree value to float for a number widget default; fall back to
    ``default`` if a non-numeric value was staged (e.g. via the raw editor) so a form
    re-render never crashes on a mid-draft bad value."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_str(value) -> str:
    """A text-widget-safe view of an optional working-tree string (None -> '')."""
    return "" if value is None else str(value)


def _task_ids(raw_tree) -> list[str]:
    """The task ids in document order (the successor endpoints a dependency may name)."""
    return [t.get("task_id", "") for t in (raw_tree.get("tasks") or [])]


def _find_task_index(raw_tree, task_id: str) -> Optional[int]:
    """Position of the task with ``task_id`` in the raw ``tasks`` list, or None if absent
    (the index a `/tasks/{i}/...` pointer needs)."""
    for i, t in enumerate(raw_tree.get("tasks") or []):
        if t.get("task_id") == task_id:
            return i
    return None


def _ref_missing(entity_id: str, message: str) -> Issue:
    """A REF_MISSING referential-integrity error for a dependency endpoint that is not a
    task in the plan (the lightweight editor's analogue of the validator's ref check)."""
    return Issue(code=IssueCode.REF_MISSING, severity=Severity.ERROR,
                 category=IssueCategory.REFERENTIAL_INTEGRITY, message=message,
                 entity_type="task", entity_id=entity_id)


def _task_options(raw_tree) -> list[dict]:
    """Per-task selector rows ``{index, task_id, duration, description}`` over the raw
    payload's ``tasks`` list; ``index`` is what the duration/description pointers address."""
    return [
        {
            "index": i,
            "task_id": t.get("task_id", ""),
            "duration": t.get("duration"),
            "description": t.get("description"),
        }
        for i, t in enumerate(raw_tree.get("tasks") or [])
    ]


def _duration_patch(task_index: int, new_duration: float) -> PatchOp:
    """REPLACE the always-present ``duration`` of task #task_index."""
    return PatchOp(action=PatchAction.REPLACE, path=f"/tasks/{task_index}/duration",
                   value=float(new_duration))


def _description_patch(task_index: int, text: str) -> PatchOp:
    """ADD (set-or-create) the optional ``description`` of task #task_index — ADD rather
    than REPLACE because the schema field is optional and a task may not carry it yet."""
    return PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/description",
                   value=text)


def _dependency_options(raw_tree) -> list[dict]:
    """Existing edges as ``{predecessor, successor, lag_hours, pred_index}`` rows, reading
    each successor entry in either schema form — a bare task-id string (lag 0) or the object
    ``{"task_id", "lag_hours"}`` (outage_schema.json successors.items oneOf)."""
    rows: list[dict] = []
    for i, t in enumerate(raw_tree.get("tasks") or []):
        pred = t.get("task_id", "")
        for succ in (t.get("successors") or []):
            if isinstance(succ, dict):
                rows.append({"predecessor": pred, "successor": succ.get("task_id", ""),
                             "lag_hours": float(succ.get("lag_hours", 0.0)), "pred_index": i})
            else:
                rows.append({"predecessor": pred, "successor": succ,
                             "lag_hours": 0.0, "pred_index": i})
    return rows


def _add_dependency_patch(raw_tree, predecessor_id: str, successor_id: str,
                          lag_hours: float) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the ADD op appending ``successor_id`` to ``predecessor_id``'s ``successors``.
    The value is a bare task-id string when the lag is 0 (the shape lag-free plans use) else
    the object form ``{"task_id", "lag_hours"}``. Returns ``(None, [issue])`` when either
    endpoint is not a task in the plan (REF_MISSING) — a nonsensical edge is never staged."""
    issues: list[Issue] = []
    pred_index = _find_task_index(raw_tree, predecessor_id)
    if pred_index is None:
        issues.append(_ref_missing(
            predecessor_id, f"dependency predecessor '{predecessor_id}' is not a task in the plan"))
    if _find_task_index(raw_tree, successor_id) is None:
        issues.append(_ref_missing(
            successor_id, f"dependency successor '{successor_id}' is not a task in the plan"))
    if issues:
        return None, issues
    lag = float(lag_hours)
    value: Any = successor_id if lag == 0 else {"task_id": successor_id, "lag_hours": lag}
    return PatchOp(action=PatchAction.ADD, path=f"/tasks/{pred_index}/successors/-",
                   value=value), []


def _remove_dependency_patch(raw_tree, predecessor_id: str,
                             successor_id: str) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the REMOVE op deleting ``successor_id`` from ``predecessor_id``'s ``successors``
    by its index within that list (matching a bare string or an object's ``task_id``).
    Returns ``(None, [issue])`` if the predecessor is unknown or the edge is not present."""
    pred_index = _find_task_index(raw_tree, predecessor_id)
    if pred_index is None:
        return None, [_ref_missing(
            predecessor_id, f"dependency predecessor '{predecessor_id}' is not a task in the plan")]
    successors = raw_tree["tasks"][pred_index].get("successors") or []
    for k, succ in enumerate(successors):
        succ_id = succ.get("task_id") if isinstance(succ, dict) else succ
        if succ_id == successor_id:
            return PatchOp(action=PatchAction.REMOVE,
                           path=f"/tasks/{pred_index}/successors/{k}"), []
    return None, [_ref_missing(
        successor_id, f"dependency '{predecessor_id}'->'{successor_id}' is not present to remove")]


# =============================================================================
# graph builders (streamlit-free): node/edge/position data for the Graphs tab
# =============================================================================
# Two pictures the analyst can explore before any run, both derived purely from the
# INPUT lineage: the current schedule's activity/dependency DAG (task precedence) and the
# baseline → scenarios relation graph (how each what-if hangs off the baseline). Both
# builders are pure — nodes, edges, AND positions in plain Python (no plotly/networkx, so
# the module still imports without either and group M can unit-test the layout). The two
# ``_render_*_graph`` helpers are the only ``st.*``/Plotly sites.

_BASELINE_NODE_ID = "__baseline__"      # synthetic id for the baseline node (never a task id)

# The scenario overlay families whose staged deltas make a scenario non-empty — the count
# shown on each relation-graph node (mirrors the Scenario dataclass's optional tuple fields).
_OVERLAY_FIELDS = (
    "duration_overrides", "resource_changes", "equipment_changes",
    "hold_point_release_overrides", "emergent_tasks", "emergent_dependencies",
)


def _overlay_count(scenario: Scenario) -> int:
    """How many individual deltas the scenario stages across all overlay families (0 == an
    empty overlay, i.e. a run would use the plain baseline)."""
    return sum(len(getattr(scenario, f) or ()) for f in _OVERLAY_FIELDS)


def _relation_graph_data(baseline, scenarios, current_id: Optional[str]) -> dict:
    """Nodes + edges + positions for the baseline → scenarios relation graph: one central
    baseline node and one node per scenario on a circle around it, an edge baseline→scenario
    each. ``current_id`` (the current-schedule pointer; None == the baseline) flags exactly
    one node ``is_current``. Pure — positions are a manual star, no layout library."""
    nodes = [{
        "id": _BASELINE_NODE_ID, "kind": "baseline", "label": baseline.plan_id,
        "is_current": current_id is None, "overlay_count": None, "x": 0.0, "y": 0.0,
    }]
    edges: list[tuple[str, str]] = []
    scenarios = tuple(scenarios)
    n = len(scenarios)
    for k, s in enumerate(scenarios):
        angle = 2.0 * math.pi * k / n if n else 0.0
        nodes.append({
            "id": s.scenario_id, "kind": "scenario", "label": s.name or s.scenario_id,
            "is_current": s.scenario_id == current_id, "overlay_count": _overlay_count(s),
            "x": math.cos(angle), "y": math.sin(angle),
        })
        edges.append((_BASELINE_NODE_ID, s.scenario_id))
    return {"nodes": nodes, "edges": edges}


def _activity_graph_data(raw_tree) -> dict:
    """Nodes + edges + positions for the current schedule's activity/dependency DAG: one node
    per task, one edge per precedence link (reusing ``_dependency_options``, so both successor
    schema forms are handled). Nodes are laid out left→right by longest-path DEPTH (a DAG feel)
    and spread vertically within each depth layer. Edges to/from a non-task id are dropped, and
    a cycle (never in a valid baseline) degrades gracefully — the offending nodes keep depth 0
    and ``has_cycle`` is set — rather than raising. Pure — no plotly/networkx."""
    from collections import deque

    tasks = _task_options(raw_tree)
    ids = [t["task_id"] for t in tasks]
    id_set = set(ids)
    edges = [(d["predecessor"], d["successor"]) for d in _dependency_options(raw_tree)
             if d["predecessor"] in id_set and d["successor"] in id_set]

    # Longest-path depth via a Kahn topological sweep (depth = longest chain of predecessors).
    succs: dict[str, list[str]] = {i: [] for i in ids}
    indeg: dict[str, int] = {i: 0 for i in ids}
    for p, s in edges:
        succs[p].append(s)
        indeg[s] += 1
    depth = {i: 0 for i in ids}
    queue = deque(i for i in ids if indeg[i] == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for s in succs[node]:
            depth[s] = max(depth[s], depth[node] + 1)
            indeg[s] -= 1
            if indeg[s] == 0:
                queue.append(s)
    has_cycle = visited < len(ids)

    # Position: x = depth; y spreads the layer's nodes, centered on 0 (document order within).
    layers: dict[int, list[str]] = {}
    for i in ids:
        layers.setdefault(depth[i], []).append(i)
    pos: dict[str, tuple[float, float]] = {}
    for d, layer in layers.items():
        offset = (len(layer) - 1) / 2.0
        for j, i in enumerate(layer):
            pos[i] = (float(d), float(j) - offset)

    dur_by_id = {t["task_id"]: t["duration"] for t in tasks}
    nodes = [{
        "id": i, "label": i, "duration": dur_by_id.get(i), "depth": depth[i],
        "x": pos[i][0], "y": pos[i][1],
    } for i in ids]
    return {"nodes": nodes, "edges": edges, "has_cycle": has_cycle}


def _mode_options(raw_tree) -> list[dict]:
    """Per-task execution-mode rows for the run-time mode picker — only tasks that define MORE
    THAN ONE mode (a task with zero or one mode offers no choice, so it is omitted). Each row is
    ``{task_id, modes}`` where ``modes`` is ``[{mode_name, duration}, …]`` (``mode_name`` is the
    schema's ``mode_id``, matching ``ModeSelection``/``validate_run_config``). Empty for every
    shipping sample (none defines modes). Streamlit-free."""
    rows: list[dict] = []
    for t in (raw_tree.get("tasks") or []):
        modes = t.get("modes") or []
        if len(modes) > 1:
            rows.append({
                "task_id": t.get("task_id", ""),
                "modes": [{"mode_name": m.get("mode_id", ""), "duration": m.get("duration")}
                          for m in modes],
            })
    return rows


def _resource_options(raw_tree) -> list[dict]:
    """Per-pool selector rows ``{index, skill_type, resource_type, n_periods}``;
    ``resource_type`` falls back to renewable (the schema default) when the pool omits it."""
    return [
        {
            "index": i,
            "skill_type": r.get("skill_type", ""),
            "resource_type": r.get("resource_type", ResourceType.RENEWABLE.value),
            "n_periods": len(r.get("availability_periods") or []),
        }
        for i, r in enumerate(raw_tree.get("resources") or [])
    ]


def _availability_options(raw_tree, resource_index: int) -> list[dict]:
    """Availability-period rows ``{index, start_date, end_date, available_count}`` for one
    pool (empty if the resource index is out of range)."""
    resources = raw_tree.get("resources") or []
    if resource_index < 0 or resource_index >= len(resources):
        return []
    periods = resources[resource_index].get("availability_periods") or []
    return [
        {
            "index": j,
            "start_date": p.get("start_date"),
            "end_date": p.get("end_date"),
            "available_count": p.get("available_count"),
        }
        for j, p in enumerate(periods)
    ]


def _available_count_patch(resource_index: int, period_index: int, count: int) -> PatchOp:
    """REPLACE the always-present ``available_count`` of one availability period."""
    return PatchOp(
        action=PatchAction.REPLACE,
        path=f"/resources/{resource_index}/availability_periods/{period_index}/available_count",
        value=int(count),
    )


def _resource_type_patch(resource_index: int, resource_type) -> PatchOp:
    """ADD (set-or-create) ``resources[i].resource_type``. ADD, not REPLACE: the field is
    optional and the shipping pools omit it (renewable is the default), so REPLACE would
    fail on a pool that never carried the key. Accepts a ``ResourceType`` or its str value."""
    value = resource_type.value if isinstance(resource_type, ResourceType) else str(resource_type)
    return PatchOp(action=PatchAction.ADD, path=f"/resources/{resource_index}/resource_type",
                   value=value)


# -----------------------------------------------------------------------------
# Increment 3: structural CRUD (add/remove whole tasks & resource pools, add/
# remove availability periods) + availability-window date editing. Same discipline:
# pure data -> PatchOp builders, feeding domain.apply_patch. Every op targets the
# tasks[...] / resources[...] loader paths Increment 2 already proves, so the commit
# rehydrate (build_reference_plan -> load_plan_content) stays safe; a nonsensical-but-
# well-formed edit stages here and is blocked by commit_draft with an already-tested
# code (DUP_ID / REF_MISSING / INVALID_AVAILABILITY_INTERVAL / a schema error). Dates
# come from st.date_input (always a real date), so every emitted start/end is a
# well-formed ISO string — the schema has NO format checker, so a malformed date would
# otherwise pass validation and crash iso_to_hours on the rehydrate.
# -----------------------------------------------------------------------------

def _dup_id(entity_type: str, entity_id: str, message: str) -> Issue:
    """A DUP_ID referential-integrity error for a new task/pool id that already exists (or
    is blank) — the lightweight editor's fail-fast analogue of the validator's dup check,
    so a doomed add is never staged."""
    return Issue(code=IssueCode.DUP_ID, severity=Severity.ERROR,
                 category=IssueCategory.REFERENTIAL_INTEGRITY, message=message,
                 entity_type=entity_type, entity_id=entity_id)


def _iso_date_value(d: date) -> str:
    """A date -> the ISO string shape the loader round-trips (midnight, no tz suffix), the
    value an availability start_date/end_date patch carries. Sourcing it from a real date
    keeps it well-formed by construction."""
    return d.strftime("%Y-%m-%dT00:00:00")


def _as_date(value, default: date) -> date:
    """A date-widget-safe view of a stored ISO string: parse its date part, falling back to
    ``default`` if a non-date value was staged (e.g. via the raw editor) — the date analogue
    of ``_as_float`` / ``_as_str``, so a form re-render never crashes on a mid-draft value."""
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return default
    return default


def _add_task_patch(raw_tree, task_id: str, duration: float, description: str, *,
                    skill_type: Optional[str] = None,
                    crew_count: int = 1) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the ADD op appending a schema-complete task to ``tasks``. The value carries every
    required key (``task_id``, ``description``, ``duration``, ``successors``, ``required_resources``,
    ``required_equipment``); ``successors``/``required_equipment`` start empty and
    ``required_resources`` carries one ``{skill_type, crew_count}`` entry when a skill is given
    (so the task is schedulable) else empty. Returns ``(None, [issue])`` with a DUP_ID when the
    id is blank or already a task in the plan — the sole nonsensical-up-front case (an empty
    description or a non-positive duration is left to the widget bounds + commit's schema check).

    ``is_hold_point`` is set explicitly to ``False``. The task schema carries a conditional
    (``if is_hold_point == true then require hold_point_type``) whose ``if`` clause omits
    ``required: ["is_hold_point"]`` — so an *absent* ``is_hold_point`` satisfies it vacuously and
    ``hold_point_type`` would become required at commit. Emitting ``is_hold_point: False`` (as the
    shipping samples and the ``raw_plan`` fixture do) makes the conditional not fire, so a plain
    task commits without a hold-point type — matching the escape every existing task relies on."""
    tid = (task_id or "").strip()
    if not tid:
        return None, [_dup_id("task", tid, "a new task needs a non-empty task_id")]
    if _find_task_index(raw_tree, tid) is not None:
        return None, [_dup_id("task", tid, f"task id '{tid}' already exists in the plan")]
    reqs: list = []
    if skill_type:
        reqs.append({"skill_type": skill_type, "crew_count": int(crew_count)})
    task = {
        "task_id": tid,
        "description": description,
        "duration": float(duration),
        "successors": [],
        "required_resources": reqs,
        "required_equipment": [],
        "is_hold_point": False,
    }
    return PatchOp(action=PatchAction.ADD, path="/tasks/-", value=task), []


def _remove_task_patch(raw_tree, task_id: str) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the REMOVE op deleting the task with ``task_id`` by its index in ``tasks``.
    Returns ``(None, [issue])`` (REF_MISSING) when the id is not a task in the plan. Inbound
    edges are NOT scrubbed here: a task still named as a successor elsewhere makes commit fail
    closed with REF_MISSING — a clean block, resolved by removing those edges first."""
    index = _find_task_index(raw_tree, task_id)
    if index is None:
        return None, [_ref_missing(task_id, f"task '{task_id}' is not in the plan to remove")]
    return PatchOp(action=PatchAction.REMOVE, path=f"/tasks/{index}"), []


def _add_resource_pool_patch(raw_tree, skill_type: str, resource_type, start_iso: str,
                             end_iso: str, count: int) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the ADD op appending a resource pool to ``resources`` with its schema-required
    ``skill_type`` and ``availability_periods`` (seeded with one initial ``{start_date, end_date,
    available_count}`` window). Returns ``(None, [issue])`` (DUP_ID) when the skill is blank or
    already a pool. A start >= end window stages and is blocked by commit
    (INVALID_AVAILABILITY_INTERVAL)."""
    skill = (skill_type or "").strip()
    if not skill:
        return None, [_dup_id("resource", skill, "a new resource pool needs a non-empty skill_type")]
    existing = {r.get("skill_type") for r in (raw_tree.get("resources") or [])}
    if skill in existing:
        return None, [_dup_id("resource", skill, f"a resource pool for '{skill}' already exists")]
    rtype = resource_type.value if isinstance(resource_type, ResourceType) else str(resource_type)
    pool = {
        "skill_type": skill,
        "resource_type": rtype,
        "availability_periods": [
            {"start_date": start_iso, "end_date": end_iso, "available_count": int(count)},
        ],
    }
    return PatchOp(action=PatchAction.ADD, path="/resources/-", value=pool), []


def _remove_resource_pool_patch(resource_index: int) -> PatchOp:
    """REMOVE the resource pool at ``resource_index`` (a valid index from the selector). If a task
    still requires that skill, commit BLOCKS with REF_MISSING (the skill is now undefined) — a
    clean block, resolved by dropping that requirement first. (A still-defined-but-undersupplied
    skill is the softer INSUFFICIENT_RESOURCE case; removing the pool entirely is the hard one.)"""
    return PatchOp(action=PatchAction.REMOVE, path=f"/resources/{resource_index}")


def _add_availability_period_patch(resource_index: int, start_iso: str, end_iso: str,
                                   count: int) -> PatchOp:
    """ADD (append) a ``{start_date, end_date, available_count}`` availability period to the pool
    at ``resource_index``. A start >= end window is blocked by commit (INVALID_AVAILABILITY_INTERVAL)."""
    return PatchOp(
        action=PatchAction.ADD,
        path=f"/resources/{resource_index}/availability_periods/-",
        value={"start_date": start_iso, "end_date": end_iso, "available_count": int(count)},
    )


def _remove_availability_period_patch(resource_index: int, period_index: int) -> PatchOp:
    """REMOVE one availability period (by index) from the pool at ``resource_index``."""
    return PatchOp(
        action=PatchAction.REMOVE,
        path=f"/resources/{resource_index}/availability_periods/{period_index}",
    )


def _window_replace_ops(base_path: str, start_iso: str, end_iso: str) -> list[PatchOp]:
    """The two REPLACE ops that move/resize an availability window by date: the always-present
    ``start_date`` and ``end_date`` under ``base_path`` (a ``…/availability_periods/{j}`` pointer).
    Shared by the resource / equipment / location window builders — a start >= end result stages
    and is blocked by commit (INVALID_AVAILABILITY_INTERVAL)."""
    return [
        PatchOp(action=PatchAction.REPLACE, path=f"{base_path}/start_date", value=start_iso),
        PatchOp(action=PatchAction.REPLACE, path=f"{base_path}/end_date", value=end_iso),
    ]


def _availability_window_patch(resource_index: int, period_index: int, start_iso: str,
                               end_iso: str) -> list[PatchOp]:
    """REPLACE the always-present ``start_date`` and ``end_date`` of one resource-pool availability
    period — moving/resizing the window by date. Two ops (one per key); a start >= end result is
    blocked by commit (INVALID_AVAILABILITY_INTERVAL)."""
    return _window_replace_ops(
        f"/resources/{resource_index}/availability_periods/{period_index}", start_iso, end_iso)


# -----------------------------------------------------------------------------
# Increment 4: equipment & location entity CRUD. Same discipline as Increment 3 —
# pure data -> PatchOp builders over the /equipment/- and /locations/- loader paths,
# fed through domain.apply_patch, blocked (never crashed) at commit on a bad edit.
# Equipment mirrors resource pools (per-period quantity_available; a required
# description); locations add an optional/nullable max_concurrent_workers, which a
# zone with no worker cap OMITS — the exact shape the _load_locations fix tolerates.
# -----------------------------------------------------------------------------

def _equipment_options(raw_tree) -> list[dict]:
    """Per-item selector rows ``{index, equipment_id, description, n_periods}``."""
    return [
        {
            "index": i,
            "equipment_id": e.get("equipment_id", ""),
            "description": e.get("description", ""),
            "n_periods": len(e.get("availability_periods") or []),
        }
        for i, e in enumerate(raw_tree.get("equipment") or [])
    ]


def _equipment_availability_options(raw_tree, equip_index: int) -> list[dict]:
    """Availability rows ``{index, start_date, end_date, quantity_available}`` for one equipment
    item (empty if the index is out of range)."""
    equipment = raw_tree.get("equipment") or []
    if equip_index < 0 or equip_index >= len(equipment):
        return []
    periods = equipment[equip_index].get("availability_periods") or []
    return [
        {
            "index": j,
            "start_date": p.get("start_date"),
            "end_date": p.get("end_date"),
            "quantity_available": p.get("quantity_available"),
        }
        for j, p in enumerate(periods)
    ]


def _add_equipment_patch(raw_tree, equipment_id: str, description: str, start_iso: str,
                         end_iso: str, quantity: int) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the ADD op appending a schema-complete equipment item to ``equipment`` with its
    required ``equipment_id`` / ``description`` and one seeded ``{start_date, end_date,
    quantity_available}`` availability period. Returns ``(None, [issue])`` (DUP_ID) when the id is
    blank or already an item. A start >= end window stages and is blocked by commit
    (INVALID_AVAILABILITY_INTERVAL)."""
    eid = (equipment_id or "").strip()
    if not eid:
        return None, [_dup_id("equipment", eid, "a new equipment item needs a non-empty equipment_id")]
    existing = {e.get("equipment_id") for e in (raw_tree.get("equipment") or [])}
    if eid in existing:
        return None, [_dup_id("equipment", eid, f"an equipment item '{eid}' already exists")]
    item = {
        "equipment_id": eid,
        "description": description,
        "availability_periods": [
            {"start_date": start_iso, "end_date": end_iso, "quantity_available": int(quantity)},
        ],
    }
    return PatchOp(action=PatchAction.ADD, path="/equipment/-", value=item), []


def _remove_equipment_patch(equip_index: int) -> PatchOp:
    """REMOVE the equipment item at ``equip_index`` (a valid index from the selector). If a task
    still requires it, commit BLOCKS (REF_MISSING) — a clean block, resolved by dropping that
    requirement first."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/equipment/{equip_index}")


def _add_equipment_availability_patch(equip_index: int, start_iso: str, end_iso: str,
                                      quantity: int) -> PatchOp:
    """ADD (append) a ``{start_date, end_date, quantity_available}`` period to the equipment item
    at ``equip_index``. A start >= end window is blocked by commit (INVALID_AVAILABILITY_INTERVAL)."""
    return PatchOp(
        action=PatchAction.ADD,
        path=f"/equipment/{equip_index}/availability_periods/-",
        value={"start_date": start_iso, "end_date": end_iso, "quantity_available": int(quantity)},
    )


def _remove_equipment_availability_patch(equip_index: int, period_index: int) -> PatchOp:
    """REMOVE one availability period (by index) from the equipment item at ``equip_index``."""
    return PatchOp(
        action=PatchAction.REMOVE,
        path=f"/equipment/{equip_index}/availability_periods/{period_index}",
    )


def _equipment_quantity_patch(equip_index: int, period_index: int, quantity: int) -> PatchOp:
    """REPLACE the always-present ``quantity_available`` of one equipment availability period."""
    return PatchOp(
        action=PatchAction.REPLACE,
        path=f"/equipment/{equip_index}/availability_periods/{period_index}/quantity_available",
        value=int(quantity),
    )


def _equipment_window_patch(equip_index: int, period_index: int, start_iso: str,
                            end_iso: str) -> list[PatchOp]:
    """Move/resize one equipment availability window by date (two REPLACE ops)."""
    return _window_replace_ops(
        f"/equipment/{equip_index}/availability_periods/{period_index}", start_iso, end_iso)


def _location_options(raw_tree) -> list[dict]:
    """Per-zone selector rows ``{index, location_id, description, n_periods}``."""
    return [
        {
            "index": i,
            "location_id": loc.get("location_id", ""),
            "description": loc.get("description", ""),
            "n_periods": len(loc.get("availability_periods") or []),
        }
        for i, loc in enumerate(raw_tree.get("locations") or [])
    ]


def _location_availability_options(raw_tree, loc_index: int) -> list[dict]:
    """Availability rows ``{index, start_date, end_date, max_concurrent_tasks,
    max_concurrent_workers}`` for one location (empty if the index is out of range).
    ``max_concurrent_workers`` is optional/nullable, so it is reported as-is (may be None)."""
    locations = raw_tree.get("locations") or []
    if loc_index < 0 or loc_index >= len(locations):
        return []
    periods = locations[loc_index].get("availability_periods") or []
    return [
        {
            "index": j,
            "start_date": p.get("start_date"),
            "end_date": p.get("end_date"),
            "max_concurrent_tasks": p.get("max_concurrent_tasks"),
            "max_concurrent_workers": p.get("max_concurrent_workers"),
        }
        for j, p in enumerate(periods)
    ]


def _location_period_value(start_iso: str, end_iso: str, max_tasks: int,
                           max_workers: Optional[int]) -> dict:
    """One location availability-period dict. ``max_concurrent_workers`` is included ONLY when
    ``max_workers`` is not None — a zone with no worker cap omits the optional/nullable key
    (int(None) would otherwise crash the commit rehydrate; the _load_locations fix tolerates it)."""
    period = {"start_date": start_iso, "end_date": end_iso,
              "max_concurrent_tasks": int(max_tasks)}
    if max_workers is not None:
        period["max_concurrent_workers"] = int(max_workers)
    return period


def _add_location_patch(raw_tree, location_id: str, description: str, start_iso: str,
                        end_iso: str, max_tasks: int,
                        max_workers: Optional[int] = None) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the ADD op appending a schema-complete location zone to ``locations`` with its
    required ``location_id`` / ``description`` and one seeded period. The period carries
    ``max_concurrent_workers`` only when ``max_workers`` is given (no worker cap omits it).
    Returns ``(None, [issue])`` (DUP_ID) when the id is blank or already a zone. A start >= end
    window stages and is blocked by commit (INVALID_AVAILABILITY_INTERVAL)."""
    lid = (location_id or "").strip()
    if not lid:
        return None, [_dup_id("location", lid, "a new location needs a non-empty location_id")]
    existing = {loc.get("location_id") for loc in (raw_tree.get("locations") or [])}
    if lid in existing:
        return None, [_dup_id("location", lid, f"a location '{lid}' already exists")]
    zone = {
        "location_id": lid,
        "description": description,
        "availability_periods": [_location_period_value(start_iso, end_iso, max_tasks, max_workers)],
    }
    return PatchOp(action=PatchAction.ADD, path="/locations/-", value=zone), []


def _remove_location_patch(loc_index: int) -> PatchOp:
    """REMOVE the location zone at ``loc_index`` (a valid index from the selector). If a task still
    names it, commit BLOCKS (REF_MISSING) — a clean block, resolved by dropping that reference."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/locations/{loc_index}")


def _add_location_availability_patch(loc_index: int, start_iso: str, end_iso: str, max_tasks: int,
                                     max_workers: Optional[int] = None) -> PatchOp:
    """ADD (append) an availability period to the location at ``loc_index`` (the worker cap is
    omitted when ``max_workers`` is None). A start >= end window is blocked by commit."""
    return PatchOp(
        action=PatchAction.ADD,
        path=f"/locations/{loc_index}/availability_periods/-",
        value=_location_period_value(start_iso, end_iso, max_tasks, max_workers),
    )


def _remove_location_availability_patch(loc_index: int, period_index: int) -> PatchOp:
    """REMOVE one availability period (by index) from the location at ``loc_index``."""
    return PatchOp(
        action=PatchAction.REMOVE,
        path=f"/locations/{loc_index}/availability_periods/{period_index}",
    )


def _location_capacity_patch(loc_index: int, period_index: int, max_tasks: int,
                             max_workers: Optional[int] = None) -> list[PatchOp]:
    """Edit one location period's capacity. Always REPLACE the required ``max_concurrent_tasks``;
    when ``max_workers`` is given, ADD (set-or-create) the optional/nullable ``max_concurrent_workers``
    (ADD not REPLACE — the key may be absent, per the ``_resource_type_patch`` precedent). A None
    ``max_workers`` leaves the worker cap untouched (only the tasks REPLACE is emitted)."""
    base = f"/locations/{loc_index}/availability_periods/{period_index}"
    ops = [PatchOp(action=PatchAction.REPLACE, path=f"{base}/max_concurrent_tasks",
                   value=int(max_tasks))]
    if max_workers is not None:
        ops.append(PatchOp(action=PatchAction.ADD, path=f"{base}/max_concurrent_workers",
                           value=int(max_workers)))
    return ops


def _location_window_patch(loc_index: int, period_index: int, start_iso: str,
                           end_iso: str) -> list[PatchOp]:
    """Move/resize one location availability window by date (two REPLACE ops)."""
    return _window_replace_ops(
        f"/locations/{loc_index}/availability_periods/{period_index}", start_iso, end_iso)


def _array_add_op(list_pointer: str, present: bool, item) -> PatchOp:
    """ADD an item to a JSON-Pointer-addressed list that MAY NOT EXIST YET. When the list is
    already present (even if empty) append with the trailing ``-``; otherwise create it with a
    one-element list. This is the create-or-append the not-root-required ``consumables`` /
    ``plant_systems`` arrays (and their optional ``restocks`` / ``valid_states`` sub-arrays) need —
    every shipping sample omits them, so an "add first item" cannot assume ``{pointer}/-`` resolves.
    Callers pass ``present = isinstance(<container>.get(<key>), list)``."""
    if present:
        return PatchOp(action=PatchAction.ADD, path=f"{list_pointer}/-", value=item)
    return PatchOp(action=PatchAction.ADD, path=list_pointer, value=[item])


def _consumable_options(raw_tree) -> list[dict]:
    """Per-consumable selector rows ``{index, item_id, description, total_quantity, n_restocks}``."""
    return [
        {
            "index": i,
            "item_id": c.get("item_id", ""),
            "description": c.get("description", ""),
            "total_quantity": c.get("total_quantity"),
            "n_restocks": len(c.get("restocks") or []),
        }
        for i, c in enumerate(raw_tree.get("consumables") or [])
    ]


def _restock_options(raw_tree, cons_index: int) -> list[dict]:
    """Restock-delivery rows ``{index, delivery_hour, quantity}`` for one consumable (empty if the
    index is out of range). ``delivery_hour`` is hours from outage start, not a date."""
    consumables = raw_tree.get("consumables") or []
    if cons_index < 0 or cons_index >= len(consumables):
        return []
    restocks = consumables[cons_index].get("restocks") or []
    return [
        {"index": j, "delivery_hour": d.get("delivery_hour"), "quantity": d.get("quantity")}
        for j, d in enumerate(restocks)
    ]


def _add_consumable_patch(raw_tree, item_id: str, description: str,
                          total_quantity: float) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the ADD op for a schema-complete consumable with its required ``item_id`` /
    ``description`` / ``total_quantity`` (no restocks initially). ``consumables`` is not
    root-required and every sample omits it, so create-or-append via ``_array_add_op``. Returns
    ``(None, [issue])`` (DUP_ID) when the id is blank or already an item. A ``total_quantity <= 0``
    stages and is blocked by commit (SCHEMA_RANGE_ERROR — the schema's ``exclusiveMinimum: 0``)."""
    iid = (item_id or "").strip()
    if not iid:
        return None, [_dup_id("consumable", iid, "a new consumable needs a non-empty item_id")]
    existing = {c.get("item_id") for c in (raw_tree.get("consumables") or [])}
    if iid in existing:
        return None, [_dup_id("consumable", iid, f"a consumable '{iid}' already exists")]
    item = {"item_id": iid, "description": description, "total_quantity": float(total_quantity)}
    present = isinstance(raw_tree.get("consumables"), list)
    return _array_add_op("/consumables", present, item), []


def _remove_consumable_patch(cons_index: int) -> PatchOp:
    """REMOVE the consumable at ``cons_index``. Unlike a resource pool / equipment / location, a
    task's ``required_consumables`` is NOT referentially validated by the CPM validator, so this
    never blocks with REF_MISSING even if a task still names the removed item."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/consumables/{cons_index}")


def _consumable_total_patch(cons_index: int, total_quantity: float) -> PatchOp:
    """REPLACE the always-present ``total_quantity`` of one consumable. A value <= 0 is blocked by
    commit (SCHEMA_RANGE_ERROR)."""
    return PatchOp(action=PatchAction.REPLACE, path=f"/consumables/{cons_index}/total_quantity",
                   value=float(total_quantity))


def _add_restock_patch(raw_tree, cons_index: int, delivery_hour: float,
                       quantity: float) -> PatchOp:
    """ADD a ``{delivery_hour, quantity}`` restock delivery to the consumable at ``cons_index``.
    ``restocks`` is optional and often absent, so create-or-append via ``_array_add_op``. A
    ``quantity <= 0`` (or ``delivery_hour < 0``) is blocked by commit (SCHEMA_RANGE_ERROR)."""
    consumables = raw_tree.get("consumables") or []
    present = (0 <= cons_index < len(consumables)
               and isinstance(consumables[cons_index].get("restocks"), list))
    return _array_add_op(f"/consumables/{cons_index}/restocks", present,
                         {"delivery_hour": float(delivery_hour), "quantity": float(quantity)})


def _remove_restock_patch(cons_index: int, restock_index: int) -> PatchOp:
    """REMOVE one restock delivery (by index) from the consumable at ``cons_index``."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/consumables/{cons_index}/restocks/{restock_index}")


def _restock_edit_patch(cons_index: int, restock_index: int, delivery_hour: float,
                        quantity: float) -> list[PatchOp]:
    """Edit one restock delivery: REPLACE its required ``delivery_hour`` and ``quantity`` (both are
    restock-required, so present -> REPLACE). A ``quantity <= 0`` / ``delivery_hour < 0`` is blocked
    by commit (SCHEMA_RANGE_ERROR)."""
    base = f"/consumables/{cons_index}/restocks/{restock_index}"
    return [
        PatchOp(action=PatchAction.REPLACE, path=f"{base}/delivery_hour",
                value=float(delivery_hour)),
        PatchOp(action=PatchAction.REPLACE, path=f"{base}/quantity", value=float(quantity)),
    ]


def _system_options(raw_tree) -> list[dict]:
    """Per-plant-system selector rows ``{index, system_id, description, n_states}``."""
    return [
        {
            "index": i,
            "system_id": s.get("system_id", ""),
            "description": s.get("description", ""),
            "n_states": len(s.get("valid_states") or []),
        }
        for i, s in enumerate(raw_tree.get("plant_systems") or [])
    ]


def _system_state_options(raw_tree, sys_index: int) -> list[dict]:
    """Valid-state rows ``{index, state}`` for one plant system (empty if the index is out of
    range)."""
    systems = raw_tree.get("plant_systems") or []
    if sys_index < 0 or sys_index >= len(systems):
        return []
    states = systems[sys_index].get("valid_states") or []
    return [{"index": j, "state": s} for j, s in enumerate(states)]


def _add_system_patch(raw_tree, system_id: str,
                      description: str) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the ADD op for a schema-complete plant system with its required ``system_id`` /
    ``description`` (no valid_states initially). ``plant_systems`` is not root-required and every
    sample omits it, so create-or-append via ``_array_add_op``. Returns ``(None, [issue])`` (DUP_ID)
    when the id is blank or already a system."""
    sid = (system_id or "").strip()
    if not sid:
        return None, [_dup_id("system", sid, "a new plant system needs a non-empty system_id")]
    existing = {s.get("system_id") for s in (raw_tree.get("plant_systems") or [])}
    if sid in existing:
        return None, [_dup_id("system", sid, f"a plant system '{sid}' already exists")]
    item = {"system_id": sid, "description": description}
    present = isinstance(raw_tree.get("plant_systems"), list)
    return _array_add_op("/plant_systems", present, item), []


def _remove_system_patch(sys_index: int) -> PatchOp:
    """REMOVE the plant system at ``sys_index``. Like consumables, a task's
    ``required_system_states`` is NOT referentially validated, so this never blocks with
    REF_MISSING even if a task still names the removed system."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/plant_systems/{sys_index}")


def _add_system_state_patch(raw_tree, sys_index: int,
                            state: str) -> tuple[Optional[PatchOp], list[Issue]]:
    """ADD one ``valid_states`` string to the plant system at ``sys_index``. ``valid_states`` is
    optional and may be absent, so create-or-append via ``_array_add_op``. Rejects a blank or
    duplicate state up-front (DUP_ID-coded, matching the blank-id precedent) so it never reaches
    the schema's ``uniqueItems`` / ``minLength`` check."""
    sval = (state or "").strip()
    systems = raw_tree.get("plant_systems") or []
    if not sval:
        return None, [_dup_id("system", sval, "a valid state must be a non-empty string")]
    if 0 <= sys_index < len(systems):
        existing = set(systems[sys_index].get("valid_states") or [])
        if sval in existing:
            return None, [_dup_id("system", sval, f"state '{sval}' is already listed")]
    present = (0 <= sys_index < len(systems)
               and isinstance(systems[sys_index].get("valid_states"), list))
    return _array_add_op(f"/plant_systems/{sys_index}/valid_states", present, sval), []


def _remove_system_state_patch(sys_index: int, state_index: int) -> PatchOp:
    """REMOVE one valid-state (by index) from the plant system at ``sys_index``."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/plant_systems/{sys_index}/valid_states/{state_index}")


# -----------------------------------------------------------------------------
# Increment 6: task requirement wiring. Point a task at the entities the other
# tabs author -- its location_id, required_equipment, required_consumables,
# required_system_states, and required_resources beyond the one seeded at add.
# Same discipline: pure data -> PatchOp builders over the /tasks/{i}/... loader
# paths, fed through domain.apply_patch, blocked (never crashed) at commit on a
# bad edit. THE REF_MISSING ASYMMETRY (validate_outage_data.py): the CPM validator
# checks only three of the five references -- location_id,
# required_equipment[].equipment_id and required_resources[].skill_type must
# resolve to a defined pool (else REF_MISSING at commit); required_consumables
# [].item_id, required_system_states[].system_id and alternative_skill_types are
# NOT validated, so a task may name a ghost of those without blocking. Builders do
# NOT re-check refs up-front (the wrappers' pickers offer only existing entities);
# the commit validator is the single arbiter, so a bad ref -- reached by removing
# a wired entity, or via the raw editor -- is what exercises the REF_MISSING
# binding for tasks. location_id is nullable, but apply_patch rejects a None value,
# so CLEARING it is a REMOVE (present-only), not a set-to-null; SETTING it is an
# ADD (set-or-create). The optional sub-arrays (required_consumables /
# required_system_states / alternative_skill_types) start absent, so adds go
# through the Inc-5 _array_add_op create-or-append helper.
# -----------------------------------------------------------------------------

def _task_at(raw_tree, task_index: int) -> Optional[dict]:
    """The task dict at ``task_index`` in the raw ``tasks`` list, or None if out of range -- the
    shared bounds guard the per-task requirement readers use (read-only; never mutated here)."""
    tasks = raw_tree.get("tasks") or []
    if task_index < 0 or task_index >= len(tasks):
        return None
    return tasks[task_index]


def _task_location(raw_tree, task_index: int) -> Optional[str]:
    """The current ``location_id`` of task #task_index (None if absent/null or out of range)."""
    task = _task_at(raw_tree, task_index)
    return task.get("location_id") if task else None


def _task_equipment_reqs(raw_tree, task_index: int) -> list[dict]:
    """``required_equipment`` rows ``{index, equipment_id, quantity_needed}`` for one task
    (empty if out of range)."""
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_equipment") or []
    return [
        {"index": j, "equipment_id": e.get("equipment_id", ""),
         "quantity_needed": e.get("quantity_needed")}
        for j, e in enumerate(reqs)
    ]


def _task_consumable_reqs(raw_tree, task_index: int) -> list[dict]:
    """``required_consumables`` rows ``{index, item_id, quantity_needed}`` for one task
    (empty if out of range)."""
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_consumables") or []
    return [
        {"index": j, "item_id": c.get("item_id", ""), "quantity_needed": c.get("quantity_needed")}
        for j, c in enumerate(reqs)
    ]


def _task_system_state_reqs(raw_tree, task_index: int) -> list[dict]:
    """``required_system_states`` rows ``{index, system_id, required_state}`` for one task
    (empty if out of range)."""
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_system_states") or []
    return [
        {"index": j, "system_id": s.get("system_id", ""),
         "required_state": s.get("required_state", "")}
        for j, s in enumerate(reqs)
    ]


def _task_resource_reqs(raw_tree, task_index: int) -> list[dict]:
    """``required_resources`` rows ``{index, skill_type, crew_count, alternatives}`` for one task
    (empty if out of range). ``alternatives`` is the entry's ``alternative_skill_types`` list."""
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_resources") or []
    return [
        {"index": j, "skill_type": r.get("skill_type", ""), "crew_count": r.get("crew_count"),
         "alternatives": list(r.get("alternative_skill_types") or [])}
        for j, r in enumerate(reqs)
    ]


# --- location_id (a nullable single value, not a list) ---

def _task_location_patch(task_index: int, location_id: str) -> PatchOp:
    """ADD (set-or-create) ``/tasks/{i}/location_id`` to a zone id. ADD, not REPLACE: the field is
    optional and a task may not carry it yet. REF_MISSING-bound -- if the zone is not a defined
    location, commit BLOCKS (REF_MISSING); the picker offers only existing zones, so a bad ref
    arises only by removing the zone later (or via the raw editor)."""
    return PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/location_id",
                   value=str(location_id))


def _task_location_clear_patch(task_index: int) -> PatchOp:
    """REMOVE ``/tasks/{i}/location_id`` -- the ONLY way to clear it, since ``location_id`` is
    nullable but ``apply_patch`` rejects a None value (a set-to-null is impossible). Valid only when
    the key is present (the wrapper gates on a current location)."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/tasks/{task_index}/location_id")


# --- required_equipment (REF_MISSING-bound) ---

def _add_task_equipment_patch(raw_tree, task_index: int, equipment_id: str,
                              quantity_needed: int) -> PatchOp:
    """Create-or-append a ``{equipment_id, quantity_needed}`` requirement to task #task_index's
    ``required_equipment`` (always present -- seeded ``[]`` by _add_task_patch -- but guarded via
    _array_add_op for a raw-edited draft). REF_MISSING-bound: a ghost equipment_id BLOCKS at commit."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("required_equipment"), list)
    return _array_add_op(f"/tasks/{task_index}/required_equipment", present,
                         {"equipment_id": str(equipment_id),
                          "quantity_needed": int(quantity_needed)})


def _remove_task_equipment_patch(task_index: int, req_index: int) -> PatchOp:
    """REMOVE one ``required_equipment`` entry (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/required_equipment/{req_index}")


# --- required_consumables (NOT referentially validated -- the asymmetry) ---

def _add_task_consumable_patch(raw_tree, task_index: int, item_id: str,
                               quantity_needed: float) -> PatchOp:
    """Create-or-append an ``{item_id, quantity_needed}`` requirement to task #task_index's
    ``required_consumables`` (optional; starts absent). NOT referentially validated -- a ghost
    item_id COMMITS cleanly (unlike equipment). A ``quantity_needed <= 0`` (schema exclusiveMinimum
    0) BLOCKS at commit (SCHEMA_RANGE_ERROR)."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("required_consumables"), list)
    return _array_add_op(f"/tasks/{task_index}/required_consumables", present,
                         {"item_id": str(item_id), "quantity_needed": float(quantity_needed)})


def _remove_task_consumable_patch(task_index: int, req_index: int) -> PatchOp:
    """REMOVE one ``required_consumables`` entry (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/required_consumables/{req_index}")


# --- required_system_states (NOT referentially validated -- the asymmetry) ---

def _add_task_system_state_patch(raw_tree, task_index: int, system_id: str,
                                 required_state: str) -> PatchOp:
    """Create-or-append a ``{system_id, required_state}`` requirement to task #task_index's
    ``required_system_states`` (optional; starts absent). NOT referentially validated -- a ghost
    system_id COMMITS cleanly. The wrapper picks required_state from the system's declared
    valid_states, so a blank required_state does not arise through the UI (a blank would otherwise
    hit the schema's minLength 1 at commit)."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("required_system_states"), list)
    return _array_add_op(f"/tasks/{task_index}/required_system_states", present,
                         {"system_id": str(system_id), "required_state": str(required_state)})


def _remove_task_system_state_patch(task_index: int, req_index: int) -> PatchOp:
    """REMOVE one ``required_system_states`` entry (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/required_system_states/{req_index}")


# --- required_resources (edit beyond the initial add) ---

def _add_task_resource_patch(raw_tree, task_index: int, skill_type: str,
                             crew_count: int) -> PatchOp:
    """Create-or-append a ``{skill_type, crew_count}`` resource requirement to task #task_index's
    ``required_resources`` (always present -- guarded via _array_add_op for a raw-edited draft).
    ``skill_type`` is REF_MISSING-bound: a ghost skill BLOCKS at commit."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("required_resources"), list)
    return _array_add_op(f"/tasks/{task_index}/required_resources", present,
                         {"skill_type": str(skill_type), "crew_count": int(crew_count)})


def _remove_task_resource_patch(task_index: int, req_index: int) -> PatchOp:
    """REMOVE one ``required_resources`` entry (by index) from task #task_index. A task with no
    resources is schema-valid (the array may be empty) but unschedulable -- a modeling choice, not
    a commit block."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/required_resources/{req_index}")


def _task_resource_crew_patch(task_index: int, req_index: int, crew_count: int) -> PatchOp:
    """REPLACE the always-present ``crew_count`` of one resource requirement (a value < 1 BLOCKS at
    commit -- schema minimum 1)."""
    return PatchOp(action=PatchAction.REPLACE,
                   path=f"/tasks/{task_index}/required_resources/{req_index}/crew_count",
                   value=int(crew_count))


def _task_resource_skill_patch(task_index: int, req_index: int, skill_type: str) -> PatchOp:
    """REPLACE the always-present ``skill_type`` of one resource requirement. REF_MISSING-bound: a
    ghost skill BLOCKS at commit."""
    return PatchOp(action=PatchAction.REPLACE,
                   path=f"/tasks/{task_index}/required_resources/{req_index}/skill_type",
                   value=str(skill_type))


def _add_task_alt_skill_patch(raw_tree, task_index: int, req_index: int,
                              skill_type: str) -> tuple[Optional[PatchOp], list[Issue]]:
    """Create-or-append one ``alternative_skill_types`` string to a resource requirement
    (``alternative_skill_types`` is optional and starts absent). Rejects a blank or duplicate value
    up-front (DUP_ID-coded, matching _add_system_state_patch) so it never reaches the schema's
    uniqueItems / minLength check. NOT referentially validated -- the validator checks only the
    primary skill_type, so a ghost alternative COMMITS cleanly."""
    alt = (skill_type or "").strip()
    if not alt:
        return None, [_dup_id("resource", alt, "an alternative skill must be a non-empty string")]
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_resources") or []
    present = False
    if 0 <= req_index < len(reqs):
        existing = set(reqs[req_index].get("alternative_skill_types") or [])
        if alt in existing:
            return None, [_dup_id("resource", alt, f"alternative skill '{alt}' is already listed")]
        present = isinstance(reqs[req_index].get("alternative_skill_types"), list)
    return _array_add_op(
        f"/tasks/{task_index}/required_resources/{req_index}/alternative_skill_types",
        present, alt), []


def _remove_task_alt_skill_patch(task_index: int, req_index: int, alt_index: int) -> PatchOp:
    """REMOVE one ``alternative_skill_types`` string (by index) from a resource requirement."""
    return PatchOp(
        action=PatchAction.REMOVE,
        path=f"/tasks/{task_index}/required_resources/{req_index}"
             f"/alternative_skill_types/{alt_index}")


# -----------------------------------------------------------------------------
# Increment 7 -- per-task SCHEDULING attributes: hold points, time windows, and
# execution modes (incl. each mode's nested required_resources / required_equipment
# and its optional dose_rate / mobilization_lead overrides). These three schema
# sub-structures were previously reachable only through the raw JSON-Pointer editor.
#
# The hold-point PAIR is managed together so the GUI never emits either state the
# schema/runtime reject: is_hold_point==true + a null type -> a WARNING; is_hold_point
# ==false + a type present -> a HOLD_POINT_MISUSE ERROR. A single selectbox drives it
# -- a real type SETS the pair, "none" CLEARS it (removing the type and any blocks_tasks).
#
# The emitted keys are the JSON schema names, NOT the domain names the loader bridges
# to: execution modes live under `modes` (Task.execution_modes) and a mode's id is
# `mode_id` (ExecutionMode.mode_name). A mode item is additionalProperties:false with
# required [mode_id, duration, required_resources, required_equipment], so an added mode
# MUST carry all four -- the two nested arrays seeded []. Both loaders are required-indexed
# (`float(w["earliest"])`, `m["mode_id"]`/`float(m["duration"])`), so a partial/blank row
# would crash the commit rehydrate; the atomic add forms collect every required field at
# once (number_inputs default to valid numbers), so no partial row is ever staged.
#
# ASYMMETRY (as with Inc-6 consumables/system-states): the CPM validator's referential
# checks do NOT descend into modes[].required_resources / required_equipment, so a ghost
# skill_type / equipment_id inside a mode COMMITS cleanly -- the pickers (offering only
# existing pools) are the only guard. time_windows and hold-point fields reference no ids.
# -----------------------------------------------------------------------------

_HOLD_POINT_TYPES = ("NRC", "QA", "Engineering", "Operations")   # the schema enum (minus null)


def _task_hold_point(raw_tree, task_index: int) -> dict:
    """The hold-point pair of task #task_index as ``{is_hold_point, hold_point_type}``
    (``{False, None}`` when absent or out of range). ``is_hold_point`` is coerced to a bool so
    an absent key reads False (the vacuous-conditional escape every plain task relies on);
    ``hold_point_type`` is the raw enum value or None."""
    task = _task_at(raw_tree, task_index) or {}
    return {"is_hold_point": bool(task.get("is_hold_point")),
            "hold_point_type": task.get("hold_point_type")}


def _task_time_windows(raw_tree, task_index: int) -> list[dict]:
    """``time_windows`` rows ``{index, earliest, latest}`` for one task (empty if out of range).
    Both bounds are hours from outage start, NOT dates."""
    windows = (_task_at(raw_tree, task_index) or {}).get("time_windows") or []
    return [
        {"index": j, "earliest": w.get("earliest"), "latest": w.get("latest")}
        for j, w in enumerate(windows)
    ]


def _task_modes(raw_tree, task_index: int) -> list[dict]:
    """``modes`` rows for one task (empty if out of range), each ``{index, mode_id, duration,
    dose_rate, mobilization_lead_hours, resources, equipment}`` where ``resources`` is
    ``[{index, skill_type, crew_count}]`` and ``equipment`` is ``[{index, equipment_id,
    quantity_needed}]``. The nested lists are read HERE (before the per-mode add buttons in the
    wrapper), so a just-added per-mode resource/equipment appears only on the next rerun -- the
    same benign one-run lag as the Inc-6 alt-skills sub-editor."""
    modes = (_task_at(raw_tree, task_index) or {}).get("modes") or []
    return [
        {
            "index": j,
            "mode_id": m.get("mode_id", ""),
            "duration": m.get("duration"),
            "dose_rate": m.get("dose_rate_mrem_per_hour"),
            "mobilization_lead_hours": m.get("mobilization_lead_hours"),
            "resources": [
                {"index": k, "skill_type": r.get("skill_type", ""),
                 "crew_count": r.get("crew_count")}
                for k, r in enumerate(m.get("required_resources") or [])
            ],
            "equipment": [
                {"index": k, "equipment_id": e.get("equipment_id", ""),
                 "quantity_needed": e.get("quantity_needed")}
                for k, e in enumerate(m.get("required_equipment") or [])
            ],
        }
        for j, m in enumerate(modes)
    ]


# --- hold points (the paired conditional -- managed together, never a misuse state) ---

def _task_hold_point_set_patch(task_index: int, hold_point_type: str) -> list[PatchOp]:
    """Make task #task_index a hold point of ``hold_point_type``: ADD (set-or-create)
    ``is_hold_point`` True AND ``hold_point_type``. ADD (not REPLACE) so it works whether or not
    the keys preexist (a sample task may omit ``is_hold_point``). A type outside the schema enum
    BLOCKS at commit (SCHEMA_TYPE_ERROR); the selectbox offers only enum members, so that arises
    only via the raw editor."""
    return [
        PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/is_hold_point", value=True),
        PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/hold_point_type",
                value=str(hold_point_type)),
    ]


def _task_hold_point_clear_patch(raw_tree, task_index: int) -> list[PatchOp]:
    """Clear task #task_index's hold point: ADD (set-or-create) ``is_hold_point`` False and
    REMOVE ``hold_point_type`` / ``blocks_tasks`` when present. Leaving the flag False while a
    type lingered would be a HOLD_POINT_MISUSE error, so the type (and any blocked-task list) is
    always removed with it -- the GUI never leaves the false+type misuse state. The REMOVEs are
    guarded on key presence (a REMOVE of an absent key would fail)."""
    task = _task_at(raw_tree, task_index) or {}
    ops = [PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/is_hold_point", value=False)]
    if "hold_point_type" in task:
        ops.append(PatchOp(action=PatchAction.REMOVE,
                           path=f"/tasks/{task_index}/hold_point_type"))
    if "blocks_tasks" in task:
        ops.append(PatchOp(action=PatchAction.REMOVE, path=f"/tasks/{task_index}/blocks_tasks"))
    return ops


# --- time windows (hour-offset execution windows; NOT referentially validated) ---

def _add_task_time_window_patch(raw_tree, task_index: int, earliest: float,
                                latest: float) -> PatchOp:
    """Create-or-append an ``{earliest, latest}`` execution window (hours from outage start) to
    task #task_index's ``time_windows`` (optional; starts absent). Both bounds are schema
    ``minimum: 0``; a negative bound BLOCKS at commit (SCHEMA_RANGE_ERROR), though the
    ``number_input``'s ``min_value=0`` keeps it non-negative through the UI. ``earliest <= latest``
    is enforced by neither schema nor validator (the scheduler's concern)."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("time_windows"), list)
    return _array_add_op(f"/tasks/{task_index}/time_windows", present,
                         {"earliest": float(earliest), "latest": float(latest)})


def _remove_task_time_window_patch(task_index: int, win_index: int) -> PatchOp:
    """REMOVE one ``time_windows`` entry (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/time_windows/{win_index}")


def _task_time_window_edit_patch(task_index: int, win_index: int, earliest: float,
                                 latest: float) -> list[PatchOp]:
    """Edit one window: REPLACE its required ``earliest`` and ``latest`` (both are
    window-required, so present -> REPLACE). A negative bound BLOCKS at commit
    (SCHEMA_RANGE_ERROR)."""
    base = f"/tasks/{task_index}/time_windows/{win_index}"
    return [
        PatchOp(action=PatchAction.REPLACE, path=f"{base}/earliest", value=float(earliest)),
        PatchOp(action=PatchAction.REPLACE, path=f"{base}/latest", value=float(latest)),
    ]


# --- execution modes (mode CRUD + nested crew/equipment + optional overrides) ---

def _add_task_mode_patch(raw_tree, task_index: int, mode_id: str,
                         duration: float) -> tuple[Optional[PatchOp], list[Issue]]:
    """Create-or-append a schema-complete execution mode to task #task_index's ``modes``
    (optional; starts absent). The mode carries all four required keys -- ``mode_id``,
    ``duration``, and the two nested arrays seeded ``[]`` (per-mode crew/equipment are added by
    their own sub-editors). Rejects a blank or duplicate ``mode_id`` up-front (DUP_ID, the
    ``uniqueItems`` analogue) so it never reaches the schema. A ``duration <= 0`` (exclusiveMinimum
    0) stages and BLOCKS at commit (SCHEMA_RANGE_ERROR)."""
    mid = (mode_id or "").strip()
    if not mid:
        return None, [_dup_id("mode", mid, "a new mode needs a non-empty mode_id")]
    existing = {m.get("mode_id")
                for m in ((_task_at(raw_tree, task_index) or {}).get("modes") or [])}
    if mid in existing:
        return None, [_dup_id("mode", mid, f"a mode '{mid}' already exists on this task")]
    item = {"mode_id": mid, "duration": float(duration),
            "required_resources": [], "required_equipment": []}
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("modes"), list)
    return _array_add_op(f"/tasks/{task_index}/modes", present, item), []


def _remove_task_mode_patch(task_index: int, mode_index: int) -> PatchOp:
    """REMOVE one execution mode (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/tasks/{task_index}/modes/{mode_index}")


def _task_mode_duration_patch(task_index: int, mode_index: int, duration: float) -> PatchOp:
    """REPLACE the always-present ``duration`` of one mode. A value <= 0 BLOCKS at commit
    (SCHEMA_RANGE_ERROR -- exclusiveMinimum 0)."""
    return PatchOp(action=PatchAction.REPLACE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/duration", value=float(duration))


def _task_mode_dose_patch(task_index: int, mode_index: int, dose_rate: float) -> PatchOp:
    """ADD (set-or-create) a mode's optional ``dose_rate_mrem_per_hour`` override. A negative
    value BLOCKS at commit (SCHEMA_RANGE_ERROR -- minimum 0)."""
    return PatchOp(action=PatchAction.ADD,
                   path=f"/tasks/{task_index}/modes/{mode_index}/dose_rate_mrem_per_hour",
                   value=float(dose_rate))


def _task_mode_dose_clear_patch(task_index: int, mode_index: int) -> PatchOp:
    """REMOVE a mode's ``dose_rate_mrem_per_hour`` override (so it inherits the task-level
    value). Valid only when the key is present (the wrapper gates on the current override)."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/dose_rate_mrem_per_hour")


def _task_mode_mob_patch(task_index: int, mode_index: int, lead_hours: float) -> PatchOp:
    """ADD (set-or-create) a mode's optional ``mobilization_lead_hours`` override. A negative
    value BLOCKS at commit (SCHEMA_RANGE_ERROR -- minimum 0)."""
    return PatchOp(action=PatchAction.ADD,
                   path=f"/tasks/{task_index}/modes/{mode_index}/mobilization_lead_hours",
                   value=float(lead_hours))


def _task_mode_mob_clear_patch(task_index: int, mode_index: int) -> PatchOp:
    """REMOVE a mode's ``mobilization_lead_hours`` override (so it inherits the task-level
    value). Valid only when the key is present."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/mobilization_lead_hours")


def _add_task_mode_resource_patch(raw_tree, task_index: int, mode_index: int, skill_type: str,
                                  crew_count: int) -> PatchOp:
    """Create-or-append a ``{skill_type, crew_count}`` requirement to mode #mode_index's
    ``required_resources`` (always present -- seeded ``[]`` by _add_task_mode_patch -- but guarded
    via _array_add_op for a raw-edited draft). NOT referentially validated: the CPM validator
    checks only task-level required_resources, so a ghost skill_type inside a mode COMMITS cleanly
    (the asymmetry). A ``crew_count < 1`` BLOCKS at commit (schema minimum 1)."""
    modes = (_task_at(raw_tree, task_index) or {}).get("modes") or []
    present = (0 <= mode_index < len(modes)
               and isinstance(modes[mode_index].get("required_resources"), list))
    return _array_add_op(f"/tasks/{task_index}/modes/{mode_index}/required_resources", present,
                         {"skill_type": str(skill_type), "crew_count": int(crew_count)})


def _remove_task_mode_resource_patch(task_index: int, mode_index: int,
                                     req_index: int) -> PatchOp:
    """REMOVE one ``required_resources`` entry (by index) from mode #mode_index of task
    #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/required_resources/{req_index}")


def _add_task_mode_equipment_patch(raw_tree, task_index: int, mode_index: int, equipment_id: str,
                                   quantity_needed: int) -> PatchOp:
    """Create-or-append an ``{equipment_id, quantity_needed}`` requirement to mode #mode_index's
    ``required_equipment`` (seeded ``[]``; guarded via _array_add_op). NOT referentially validated
    -- a ghost equipment_id inside a mode COMMITS cleanly (the asymmetry). A ``quantity_needed < 1``
    BLOCKS at commit (schema minimum 1)."""
    modes = (_task_at(raw_tree, task_index) or {}).get("modes") or []
    present = (0 <= mode_index < len(modes)
               and isinstance(modes[mode_index].get("required_equipment"), list))
    return _array_add_op(f"/tasks/{task_index}/modes/{mode_index}/required_equipment", present,
                         {"equipment_id": str(equipment_id),
                          "quantity_needed": int(quantity_needed)})


def _remove_task_mode_equipment_patch(task_index: int, mode_index: int,
                                      req_index: int) -> PatchOp:
    """REMOVE one ``required_equipment`` entry (by index) from mode #mode_index of task
    #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/required_equipment/{req_index}")


# -----------------------------------------------------------------------------
# structured editor form wrappers (the only st.* sites for editing) + the
# lifecycle composition. Each wrapper gathers widget inputs, calls a builder, and
# routes the result through the shared _apply_ops tail.
# -----------------------------------------------------------------------------

def _apply_ops(session, draft, ops, issues, *, success_msg: str) -> None:
    """Shared form tail: surface builder issues, else push each PatchOp through
    ``domain.apply_patch``, persist the draft, and report. A structural failure from
    apply_patch is rendered (never raised); the audit log is kept consistent by the domain."""
    if issues:
        _render_issues(tuple(issues))
        return
    if not ops:
        st.info("Nothing to apply.")
        return
    failed = False
    for op in ops:
        outcome = apply_patch(draft, op)
        if not outcome.ok:
            failed = True
            _render_issues(outcome.issues)
    session.set_draft(draft)
    if not failed:
        st.success(success_msg)


def _render_task_form(session, draft) -> None:
    """Task & duration tab: edit an existing task's duration/description, add a whole new task,
    or remove one. Add/remove reshape the ``tasks`` list; the edit controls change fields in place."""
    options = _task_options(draft.raw_working_tree)
    if options:
        labels = [f"{o['task_id']} (dur {o['duration']})" for o in options]
        pick = st.selectbox("Task", range(len(options)), format_func=lambda k: labels[k],
                            key="prism_task_pick")
        chosen = options[pick]
        new_dur = st.number_input("Duration (hours)", min_value=0.0,
                                  value=_as_float(chosen["duration"], 0.0), step=1.0,
                                  key="prism_task_duration")
        new_desc = st.text_input("Description", value=_as_str(chosen["description"]),
                                 key="prism_task_description")
        if st.button("Apply task edit", key="prism_task_apply"):
            ops = [_duration_patch(chosen["index"], new_dur)]
            if new_desc != _as_str(chosen["description"]):
                ops.append(_description_patch(chosen["index"], new_desc))
            _apply_ops(session, draft, ops, [],
                       success_msg=f"Staged edit to task {chosen['task_id']}.")
    else:
        st.caption("No tasks yet — add one below.")

    st.markdown("**Add a task**")
    c1, c2 = st.columns([1, 1])
    add_id = c1.text_input("New task id", key="prism_task_add_id", placeholder="C")
    add_dur = c2.number_input("Duration (hours)", min_value=0.01, value=1.0, step=1.0,
                              key="prism_task_add_duration")
    add_desc = st.text_input("Description", key="prism_task_add_description",
                             placeholder="what this task does")
    skills = sorted({r["skill_type"] for r in _resource_options(draft.raw_working_tree)})
    _NONE = "— none —"
    s1, s2 = st.columns([2, 1])
    add_skill = s1.selectbox("Crew skill (optional)", [_NONE, *skills], key="prism_task_add_skill")
    add_crew = s2.number_input("Crew count", min_value=1, value=1, step=1,
                               key="prism_task_add_crew")
    if st.button("Add task", key="prism_task_add"):
        op, issues = _add_task_patch(
            draft.raw_working_tree, add_id, add_dur, add_desc,
            skill_type=None if add_skill == _NONE else add_skill, crew_count=add_crew)
        _apply_ops(session, draft, [op] if op else [], issues,
                   success_msg=f"Staged new task {add_id.strip()}.")

    if options:
        st.markdown("**Remove a task**")
        st.caption("Remove any dependency naming this task first — a dangling reference blocks the commit.")
        ids = [o["task_id"] for o in options]
        rem_pick = st.selectbox("Task to remove", range(len(ids)),
                                format_func=lambda k: ids[k], key="prism_task_remove_pick")
        if st.button("Remove task", key="prism_task_remove"):
            op, issues = _remove_task_patch(draft.raw_working_tree, ids[rem_pick])
            _apply_ops(session, draft, [op] if op else [], issues,
                       success_msg=f"Staged removal of task {ids[rem_pick]}.")


def _render_dependency_form(session, draft) -> None:
    """Dependencies & lags tab: add an edge (with an optional finish-to-start lag) or remove
    an existing one, over the raw per-task ``successors`` lists."""
    ids = _task_ids(draft.raw_working_tree)
    if len(ids) < 2:
        st.caption("Need at least two tasks to define a dependency.")
        return
    st.markdown("**Add a dependency**")
    c1, c2, c3 = st.columns([2, 2, 1])
    pred = c1.selectbox("Predecessor", ids, key="prism_dep_pred")
    succ = c2.selectbox("Successor", ids, key="prism_dep_succ")
    lag = c3.number_input("Lag (hours)", min_value=0.0, value=0.0, step=1.0, key="prism_dep_lag")
    if st.button("Add dependency", key="prism_dep_add"):
        op, issues = _add_dependency_patch(draft.raw_working_tree, pred, succ, lag)
        _apply_ops(session, draft, [op] if op else [], issues,
                   success_msg=f"Staged dependency {pred} → {succ} (lag {lag:g}).")

    existing = _dependency_options(draft.raw_working_tree)
    if existing:
        st.markdown("**Remove a dependency**")
        labels = [f"{e['predecessor']} → {e['successor']} (lag {e['lag_hours']:g})"
                  for e in existing]
        pick = st.selectbox("Edge", range(len(existing)), format_func=lambda k: labels[k],
                            key="prism_dep_remove_pick")
        if st.button("Remove dependency", key="prism_dep_remove"):
            e = existing[pick]
            op, issues = _remove_dependency_patch(
                draft.raw_working_tree, e["predecessor"], e["successor"])
            _apply_ops(session, draft, [op] if op else [], issues,
                       success_msg=f"Staged removal of {e['predecessor']} → {e['successor']}.")


def _render_resource_form(session, draft) -> None:
    """Resources & availability tab: change a pool's resource_type or a period's available_count,
    move/resize a window by date, add/remove availability periods, and add/remove whole pools."""
    _DEFAULT_DAY = date(2025, 1, 1)
    type_values = [t.value for t in ResourceType]

    st.markdown("**Add a resource pool**")
    a1, a2 = st.columns([2, 1])
    add_skill = a1.text_input("Skill type", key="prism_res_add_skill", placeholder="ELEC")
    add_type = a2.selectbox("Resource type", type_values, key="prism_res_add_type")
    w1, w2, w3 = st.columns([2, 2, 1])
    add_start = w1.date_input("Available from", value=_DEFAULT_DAY, key="prism_res_add_start")
    add_end = w2.date_input("Available to", value=date(2025, 1, 5), key="prism_res_add_end")
    add_count = w3.number_input("Count", min_value=0, value=1, step=1, key="prism_res_add_count")
    if st.button("Add pool", key="prism_res_add"):
        op, issues = _add_resource_pool_patch(
            draft.raw_working_tree, add_skill, add_type,
            _iso_date_value(add_start), _iso_date_value(add_end), add_count)
        _apply_ops(session, draft, [op] if op else [], issues,
                   success_msg=f"Staged new resource pool {add_skill.strip()}.")

    resources = _resource_options(draft.raw_working_tree)
    if not resources:
        st.caption("No resource pools yet — add one above.")
        return
    r_labels = [f"{r['skill_type']} ({r['resource_type']}, {r['n_periods']} period(s))"
                for r in resources]
    r_pick = st.selectbox("Resource pool", range(len(resources)),
                          format_func=lambda k: r_labels[k], key="prism_res_pick")
    chosen = resources[r_pick]

    type_index = type_values.index(chosen["resource_type"]) \
        if chosen["resource_type"] in type_values else 0
    type_choice = st.selectbox("Resource type", type_values, index=type_index,
                               key="prism_res_type")
    tcol, rcol = st.columns([1, 1])
    if tcol.button("Apply resource type", key="prism_res_type_apply"):
        op = _resource_type_patch(chosen["index"], type_choice)
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged resource_type={type_choice} for {chosen['skill_type']}.")
    if rcol.button("Remove pool", key="prism_res_remove"):
        op = _remove_resource_pool_patch(chosen["index"])
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged removal of resource pool {chosen['skill_type']}.")

    periods = _availability_options(draft.raw_working_tree, chosen["index"])
    if periods:
        st.markdown("**Availability period**")
        p_labels = [f"[{p['start_date']} … {p['end_date']}) count {p['available_count']}"
                    for p in periods]
        p_pick = st.selectbox("Period", range(len(periods)), format_func=lambda k: p_labels[k],
                              key="prism_avail_pick")
        chosen_p = periods[p_pick]
        new_count = st.number_input("Available count", min_value=0,
                                    value=int(_as_float(chosen_p["available_count"], 0.0)),
                                    step=1, key="prism_avail_count")
        if st.button("Apply available count", key="prism_avail_apply"):
            op = _available_count_patch(chosen["index"], chosen_p["index"], new_count)
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged available_count={new_count} for {chosen['skill_type']}.")

        d1, d2 = st.columns([1, 1])
        win_start = d1.date_input("Window start", value=_as_date(chosen_p["start_date"], _DEFAULT_DAY),
                                  key="prism_avail_start")
        win_end = d2.date_input("Window end", value=_as_date(chosen_p["end_date"], _DEFAULT_DAY),
                                key="prism_avail_end")
        wcol, xcol = st.columns([1, 1])
        if wcol.button("Apply window dates", key="prism_avail_window"):
            ops = _availability_window_patch(
                chosen["index"], chosen_p["index"],
                _iso_date_value(win_start), _iso_date_value(win_end))
            _apply_ops(session, draft, ops, [],
                       success_msg=f"Staged window [{win_start} … {win_end}) for {chosen['skill_type']}.")
        if xcol.button("Remove period", key="prism_avail_remove"):
            op = _remove_availability_period_patch(chosen["index"], chosen_p["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of a period from {chosen['skill_type']}.")

    st.markdown("**Add an availability period**")
    n1, n2, n3 = st.columns([2, 2, 1])
    per_start = n1.date_input("From", value=_DEFAULT_DAY, key="prism_avail_add_start")
    per_end = n2.date_input("To", value=date(2025, 1, 5), key="prism_avail_add_end")
    per_count = n3.number_input("Count", min_value=0, value=1, step=1, key="prism_avail_add_count")
    if st.button("Add period", key="prism_avail_add"):
        op = _add_availability_period_patch(
            chosen["index"], _iso_date_value(per_start), _iso_date_value(per_end), per_count)
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged new availability period for {chosen['skill_type']}.")


def _render_equipment_form(session, draft) -> None:
    """Equipment tab: add/remove whole equipment items, edit a period's quantity_available,
    move/resize a window by date, and add/remove availability periods — the equipment analogue
    of the resource form (a required description; per-period quantity_available)."""
    _DEFAULT_DAY = date(2025, 1, 1)

    st.markdown("**Add equipment**")
    a1, a2 = st.columns([1, 2])
    add_id = a1.text_input("Equipment id", key="prism_equip_add_id", placeholder="CRANE-1")
    add_desc = a2.text_input("Description", key="prism_equip_add_desc",
                             placeholder="what this equipment is")
    w1, w2, w3 = st.columns([2, 2, 1])
    add_start = w1.date_input("Available from", value=_DEFAULT_DAY, key="prism_equip_add_start")
    add_end = w2.date_input("Available to", value=date(2025, 1, 5), key="prism_equip_add_end")
    add_qty = w3.number_input("Quantity", min_value=0, value=1, step=1, key="prism_equip_add_qty")
    if st.button("Add equipment", key="prism_equip_add"):
        op, issues = _add_equipment_patch(
            draft.raw_working_tree, add_id, add_desc,
            _iso_date_value(add_start), _iso_date_value(add_end), add_qty)
        _apply_ops(session, draft, [op] if op else [], issues,
                   success_msg=f"Staged new equipment {add_id.strip()}.")

    items = _equipment_options(draft.raw_working_tree)
    if not items:
        st.caption("No equipment yet — add one above.")
        return
    e_labels = [f"{e['equipment_id']} ({e['n_periods']} period(s))" for e in items]
    e_pick = st.selectbox("Equipment item", range(len(items)),
                          format_func=lambda k: e_labels[k], key="prism_equip_pick")
    chosen = items[e_pick]
    if st.button("Remove equipment", key="prism_equip_remove"):
        op = _remove_equipment_patch(chosen["index"])
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged removal of equipment {chosen['equipment_id']}.")

    periods = _equipment_availability_options(draft.raw_working_tree, chosen["index"])
    if periods:
        st.markdown("**Availability period**")
        p_labels = [f"[{p['start_date']} … {p['end_date']}) qty {p['quantity_available']}"
                    for p in periods]
        p_pick = st.selectbox("Period", range(len(periods)), format_func=lambda k: p_labels[k],
                              key="prism_equip_avail_pick")
        chosen_p = periods[p_pick]
        new_qty = st.number_input("Quantity available", min_value=0,
                                  value=int(_as_float(chosen_p["quantity_available"], 0.0)),
                                  step=1, key="prism_equip_qty")
        if st.button("Apply quantity", key="prism_equip_qty_apply"):
            op = _equipment_quantity_patch(chosen["index"], chosen_p["index"], new_qty)
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged quantity_available={new_qty} for {chosen['equipment_id']}.")

        d1, d2 = st.columns([1, 1])
        win_start = d1.date_input("Window start", value=_as_date(chosen_p["start_date"], _DEFAULT_DAY),
                                  key="prism_equip_start")
        win_end = d2.date_input("Window end", value=_as_date(chosen_p["end_date"], _DEFAULT_DAY),
                                key="prism_equip_end")
        wcol, xcol = st.columns([1, 1])
        if wcol.button("Apply window dates", key="prism_equip_window"):
            ops = _equipment_window_patch(
                chosen["index"], chosen_p["index"],
                _iso_date_value(win_start), _iso_date_value(win_end))
            _apply_ops(session, draft, ops, [],
                       success_msg=f"Staged window [{win_start} … {win_end}) for {chosen['equipment_id']}.")
        if xcol.button("Remove period", key="prism_equip_avail_remove"):
            op = _remove_equipment_availability_patch(chosen["index"], chosen_p["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of a period from {chosen['equipment_id']}.")

    st.markdown("**Add an availability period**")
    n1, n2, n3 = st.columns([2, 2, 1])
    per_start = n1.date_input("From", value=_DEFAULT_DAY, key="prism_equip_add_p_start")
    per_end = n2.date_input("To", value=date(2025, 1, 5), key="prism_equip_add_p_end")
    per_qty = n3.number_input("Quantity", min_value=0, value=1, step=1, key="prism_equip_add_p_qty")
    if st.button("Add period", key="prism_equip_avail_add"):
        op = _add_equipment_availability_patch(
            chosen["index"], _iso_date_value(per_start), _iso_date_value(per_end), per_qty)
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged new availability period for {chosen['equipment_id']}.")


def _render_location_form(session, draft) -> None:
    """Locations tab: add/remove whole location zones, edit a period's capacity
    (``max_concurrent_tasks``, plus an OPTIONAL ``max_concurrent_workers`` gated by a checkbox —
    unchecked means no worker cap), move/resize a window by date, and add/remove availability
    periods. Leaving the worker cap off omits the key, exercising the loader's null-tolerance."""
    _DEFAULT_DAY = date(2025, 1, 1)

    st.markdown("**Add a location**")
    a1, a2 = st.columns([1, 2])
    add_id = a1.text_input("Location id", key="prism_loc_add_id", placeholder="ZONE-A")
    add_desc = a2.text_input("Description", key="prism_loc_add_desc",
                             placeholder="what this location is")
    w1, w2, w3 = st.columns([2, 2, 1])
    add_start = w1.date_input("Available from", value=_DEFAULT_DAY, key="prism_loc_add_start")
    add_end = w2.date_input("Available to", value=date(2025, 1, 5), key="prism_loc_add_end")
    add_tasks = w3.number_input("Max tasks", min_value=0, value=1, step=1, key="prism_loc_add_tasks")
    cap1, cap2 = st.columns([1, 1])
    add_cap = cap1.checkbox("Cap concurrent workers", value=False, key="prism_loc_add_cap")
    add_workers = cap2.number_input("Max workers", min_value=0, value=1, step=1,
                                    key="prism_loc_add_workers", disabled=not add_cap)
    if st.button("Add location", key="prism_loc_add"):
        op, issues = _add_location_patch(
            draft.raw_working_tree, add_id, add_desc,
            _iso_date_value(add_start), _iso_date_value(add_end), add_tasks,
            max_workers=add_workers if add_cap else None)
        _apply_ops(session, draft, [op] if op else [], issues,
                   success_msg=f"Staged new location {add_id.strip()}.")

    zones = _location_options(draft.raw_working_tree)
    if not zones:
        st.caption("No locations yet — add one above.")
        return
    l_labels = [f"{z['location_id']} ({z['n_periods']} period(s))" for z in zones]
    l_pick = st.selectbox("Location", range(len(zones)),
                          format_func=lambda k: l_labels[k], key="prism_loc_pick")
    chosen = zones[l_pick]
    if st.button("Remove location", key="prism_loc_remove"):
        op = _remove_location_patch(chosen["index"])
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged removal of location {chosen['location_id']}.")

    periods = _location_availability_options(draft.raw_working_tree, chosen["index"])
    if periods:
        st.markdown("**Availability period**")
        p_labels = [
            f"[{p['start_date']} … {p['end_date']}) tasks {p['max_concurrent_tasks']}, "
            f"workers {p['max_concurrent_workers'] if p['max_concurrent_workers'] is not None else '—'}"
            for p in periods]
        p_pick = st.selectbox("Period", range(len(periods)), format_func=lambda k: p_labels[k],
                              key="prism_loc_avail_pick")
        chosen_p = periods[p_pick]
        cc1, cc2, cc3 = st.columns([1, 1, 1])
        new_tasks = cc1.number_input("Max tasks", min_value=0,
                                     value=int(_as_float(chosen_p["max_concurrent_tasks"], 0.0)),
                                     step=1, key="prism_loc_tasks")
        cap_workers = cc2.checkbox("Cap workers",
                                   value=chosen_p["max_concurrent_workers"] is not None,
                                   key="prism_loc_cap")
        new_workers = cc3.number_input(
            "Max workers", min_value=0,
            value=int(_as_float(chosen_p["max_concurrent_workers"], 0.0)),
            step=1, key="prism_loc_workers", disabled=not cap_workers)
        if st.button("Apply capacity", key="prism_loc_capacity_apply"):
            ops = _location_capacity_patch(
                chosen["index"], chosen_p["index"], new_tasks,
                max_workers=new_workers if cap_workers else None)
            _apply_ops(session, draft, ops, [],
                       success_msg=f"Staged capacity for {chosen['location_id']}.")

        d1, d2 = st.columns([1, 1])
        win_start = d1.date_input("Window start", value=_as_date(chosen_p["start_date"], _DEFAULT_DAY),
                                  key="prism_loc_start")
        win_end = d2.date_input("Window end", value=_as_date(chosen_p["end_date"], _DEFAULT_DAY),
                                key="prism_loc_end")
        wcol, xcol = st.columns([1, 1])
        if wcol.button("Apply window dates", key="prism_loc_window"):
            ops = _location_window_patch(
                chosen["index"], chosen_p["index"],
                _iso_date_value(win_start), _iso_date_value(win_end))
            _apply_ops(session, draft, ops, [],
                       success_msg=f"Staged window [{win_start} … {win_end}) for {chosen['location_id']}.")
        if xcol.button("Remove period", key="prism_loc_avail_remove"):
            op = _remove_location_availability_patch(chosen["index"], chosen_p["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of a period from {chosen['location_id']}.")

    st.markdown("**Add an availability period**")
    n1, n2, n3 = st.columns([2, 2, 1])
    per_start = n1.date_input("From", value=_DEFAULT_DAY, key="prism_loc_add_p_start")
    per_end = n2.date_input("To", value=date(2025, 1, 5), key="prism_loc_add_p_end")
    per_tasks = n3.number_input("Max tasks", min_value=0, value=1, step=1, key="prism_loc_add_p_tasks")
    pc1, pc2 = st.columns([1, 1])
    per_cap = pc1.checkbox("Cap concurrent workers", value=False, key="prism_loc_add_p_cap")
    per_workers = pc2.number_input("Max workers", min_value=0, value=1, step=1,
                                   key="prism_loc_add_p_workers", disabled=not per_cap)
    if st.button("Add period", key="prism_loc_avail_add"):
        op = _add_location_availability_patch(
            chosen["index"], _iso_date_value(per_start), _iso_date_value(per_end), per_tasks,
            max_workers=per_workers if per_cap else None)
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged new availability period for {chosen['location_id']}.")


def _render_consumable_form(session, draft) -> None:
    """Consumables tab: add/remove whole consumable items, edit the required ``total_quantity``,
    and add/remove/edit restock deliveries (``{delivery_hour, quantity}`` — plain hours from outage
    start, NOT date windows, so no window/interval concept). A consumable requires ``item_id`` /
    ``description`` / ``total_quantity`` (> 0, else a SCHEMA_RANGE_ERROR block); ``restocks`` is
    optional and starts absent (first add creates the list). Removing a consumable a task references
    does NOT block — ``required_consumables`` is not referentially validated."""
    st.markdown("**Add a consumable**")
    a1, a2, a3 = st.columns([1, 2, 1])
    add_id = a1.text_input("Item id", key="prism_cons_add_id", placeholder="N2-CYL")
    add_desc = a2.text_input("Description", key="prism_cons_add_desc",
                             placeholder="what this material is")
    add_total = a3.number_input("Total quantity", min_value=0.0, value=1.0, step=1.0,
                                key="prism_cons_add_total")
    if st.button("Add consumable", key="prism_cons_add"):
        op, issues = _add_consumable_patch(draft.raw_working_tree, add_id, add_desc, add_total)
        _apply_ops(session, draft, [op] if op else [], issues,
                   success_msg=f"Staged new consumable {add_id.strip()}.")

    items = _consumable_options(draft.raw_working_tree)
    if not items:
        st.caption("No consumables yet — add one above.")
        return
    c_labels = [f"{c['item_id']} (total {c['total_quantity']}, {c['n_restocks']} restock(s))"
                for c in items]
    c_pick = st.selectbox("Consumable", range(len(items)),
                          format_func=lambda k: c_labels[k], key="prism_cons_pick")
    chosen = items[c_pick]
    new_total = st.number_input("Total quantity", min_value=0.0,
                                value=_as_float(chosen["total_quantity"], 1.0), step=1.0,
                                key="prism_cons_total")
    tcol, xcol = st.columns([1, 1])
    if tcol.button("Apply total", key="prism_cons_total_apply"):
        op = _consumable_total_patch(chosen["index"], new_total)
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged total_quantity={new_total:g} for {chosen['item_id']}.")
    if xcol.button("Remove consumable", key="prism_cons_remove"):
        op = _remove_consumable_patch(chosen["index"])
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged removal of consumable {chosen['item_id']}.")

    restocks = _restock_options(draft.raw_working_tree, chosen["index"])
    if restocks:
        st.markdown("**Restock delivery**")
        r_labels = [f"hour {r['delivery_hour']} → qty {r['quantity']}" for r in restocks]
        r_pick = st.selectbox("Restock", range(len(restocks)), format_func=lambda k: r_labels[k],
                              key="prism_cons_restock_pick")
        chosen_r = restocks[r_pick]
        e1, e2 = st.columns([1, 1])
        edit_hour = e1.number_input("Delivery hour", min_value=0.0,
                                    value=_as_float(chosen_r["delivery_hour"], 0.0), step=1.0,
                                    key="prism_cons_restock_hour")
        edit_qty = e2.number_input("Quantity", min_value=0.0,
                                   value=_as_float(chosen_r["quantity"], 1.0), step=1.0,
                                   key="prism_cons_restock_qty")
        ecol, rcol = st.columns([1, 1])
        if ecol.button("Apply restock edit", key="prism_cons_restock_apply"):
            ops = _restock_edit_patch(chosen["index"], chosen_r["index"], edit_hour, edit_qty)
            _apply_ops(session, draft, ops, [],
                       success_msg=f"Staged restock edit for {chosen['item_id']}.")
        if rcol.button("Remove restock", key="prism_cons_restock_remove"):
            op = _remove_restock_patch(chosen["index"], chosen_r["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of a restock from {chosen['item_id']}.")

    st.markdown("**Add a restock delivery**")
    n1, n2 = st.columns([1, 1])
    add_hour = n1.number_input("Delivery hour", min_value=0.0, value=24.0, step=1.0,
                               key="prism_cons_restock_add_hour")
    add_qty = n2.number_input("Quantity", min_value=0.0, value=1.0, step=1.0,
                              key="prism_cons_restock_add_qty")
    if st.button("Add restock", key="prism_cons_restock_add"):
        op = _add_restock_patch(draft.raw_working_tree, chosen["index"], add_hour, add_qty)
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged new restock for {chosen['item_id']}.")


def _render_system_form(session, draft) -> None:
    """Systems tab: add/remove whole plant systems and add/remove their ``valid_states`` (a list of
    unique non-empty strings governing concurrent-activity compatibility). A system requires
    ``system_id`` / ``description``; ``valid_states`` is optional and starts absent (first add
    creates the list). A blank or duplicate state is rejected up-front (DUP_ID-coded). Removing a
    system a task references does NOT block — ``required_system_states`` is not referentially
    validated."""
    st.markdown("**Add a plant system**")
    a1, a2 = st.columns([1, 2])
    add_id = a1.text_input("System id", key="prism_sys_add_id", placeholder="RCS")
    add_desc = a2.text_input("Description", key="prism_sys_add_desc",
                             placeholder="what this system is")
    if st.button("Add system", key="prism_sys_add"):
        op, issues = _add_system_patch(draft.raw_working_tree, add_id, add_desc)
        _apply_ops(session, draft, [op] if op else [], issues,
                   success_msg=f"Staged new plant system {add_id.strip()}.")

    systems = _system_options(draft.raw_working_tree)
    if not systems:
        st.caption("No plant systems yet — add one above.")
        return
    s_labels = [f"{s['system_id']} ({s['n_states']} state(s))" for s in systems]
    s_pick = st.selectbox("Plant system", range(len(systems)),
                          format_func=lambda k: s_labels[k], key="prism_sys_pick")
    chosen = systems[s_pick]
    if st.button("Remove system", key="prism_sys_remove"):
        op = _remove_system_patch(chosen["index"])
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged removal of plant system {chosen['system_id']}.")

    states = _system_state_options(draft.raw_working_tree, chosen["index"])
    if states:
        st.markdown("**Valid states**")
        st_labels = [s["state"] for s in states]
        st_pick = st.selectbox("State", range(len(states)), format_func=lambda k: st_labels[k],
                               key="prism_sys_state_pick")
        if st.button("Remove state", key="prism_sys_state_remove"):
            op = _remove_system_state_patch(chosen["index"], states[st_pick]["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of state '{states[st_pick]['state']}' "
                                   f"from {chosen['system_id']}.")

    st.markdown("**Add a valid state**")
    add_state = st.text_input("State", key="prism_sys_state_add", placeholder="ISOLATED")
    if st.button("Add state", key="prism_sys_state_add_btn"):
        op, issues = _add_system_state_patch(draft.raw_working_tree, chosen["index"], add_state)
        _apply_ops(session, draft, [op] if op else [], issues,
                   success_msg=f"Staged new state for {chosen['system_id']}.")


def _render_task_requirements_form(session, draft) -> None:
    """Task requirements tab: point a task at the entities the other tabs author — its
    ``location_id`` (a single nullable zone), ``required_equipment``, ``required_consumables``,
    ``required_system_states``, and any ``required_resources`` beyond the one seeded at add
    (including each resource's ``alternative_skill_types``). Every picker offers only entities that
    already exist, so the commit validator is the single arbiter of referential integrity: wiring a
    task to a defined pool commits, and the REF_MISSING binding is reached through the removal story
    (wire CRANE-1, then remove equipment CRANE-1 -> commit blocks). NOTE the validator's ASYMMETRY --
    only ``location_id``, ``required_equipment[].equipment_id`` and ``required_resources[].skill_type``
    are referentially validated; ``required_consumables[].item_id``, ``required_system_states[]
    .system_id`` and ``alternative_skill_types`` are not, so a ghost of those commits cleanly."""
    tasks = _task_options(draft.raw_working_tree)
    if not tasks:
        st.caption("No tasks yet — add one on the Task & duration tab first.")
        return
    t_labels = [f"{t['task_id']} (dur {t['duration']})" for t in tasks]
    t_pick = st.selectbox("Task", range(len(tasks)), format_func=lambda k: t_labels[k],
                          key="prism_req_task_pick")
    task_index = tasks[t_pick]["index"]
    task_id = tasks[t_pick]["task_id"]
    _NONE = "— none —"

    # --- Location (a single nullable zone; SET is ADD, CLEAR is REMOVE — null can't be patched) ---
    st.markdown("**Location**")
    zone_ids = [o["location_id"] for o in _location_options(draft.raw_working_tree)]
    current_loc = _task_location(draft.raw_working_tree, task_index)
    st.caption(f"Current location: {current_loc if current_loc else _NONE}")
    loc_opts = [_NONE, *zone_ids]
    loc_default = loc_opts.index(current_loc) if current_loc in zone_ids else 0
    loc_choice = st.selectbox("Set location", loc_opts, index=loc_default, key="prism_req_loc")
    if st.button("Apply location", key="prism_req_loc_apply"):
        if loc_choice != _NONE:
            _apply_ops(session, draft, [_task_location_patch(task_index, loc_choice)], [],
                       success_msg=f"Staged location {loc_choice} for task {task_id}.")
        elif current_loc is not None:
            _apply_ops(session, draft, [_task_location_clear_patch(task_index)], [],
                       success_msg=f"Staged clearing the location of task {task_id}.")
        else:
            st.info("No location set — nothing to clear.")

    # --- Resource requirements (skill_type + crew_count, plus nested alternative_skill_types) ---
    st.markdown("**Resource requirements**")
    skills = sorted({r["skill_type"] for r in _resource_options(draft.raw_working_tree)})
    if skills:
        ra1, ra2 = st.columns([2, 1])
        add_skill = ra1.selectbox("Skill", skills, key="prism_req_res_add_skill")
        add_crew = ra2.number_input("Crew count", min_value=1, value=1, step=1,
                                    key="prism_req_res_add_crew")
        if st.button("Add resource requirement", key="prism_req_res_add"):
            op = _add_task_resource_patch(draft.raw_working_tree, task_index, add_skill, add_crew)
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged resource {add_skill} ×{add_crew} for task {task_id}.")
    else:
        st.caption("No resource pools yet — add one on the Resources tab first.")

    resources = _task_resource_reqs(draft.raw_working_tree, task_index)
    if resources:
        r_labels = [f"{r['skill_type']} ×{r['crew_count']}"
                    + (f" (alt: {', '.join(r['alternatives'])})" if r["alternatives"] else "")
                    for r in resources]
        r_pick = st.selectbox("Resource requirement", range(len(resources)),
                              format_func=lambda k: r_labels[k], key="prism_req_res_pick")
        chosen_r = resources[r_pick]
        e1, e2 = st.columns([1, 2])
        edit_crew = e1.number_input("Crew count", min_value=1,
                                    value=int(_as_float(chosen_r["crew_count"], 1.0)), step=1,
                                    key="prism_req_res_crew")
        skill_opts = skills or [chosen_r["skill_type"]]
        skill_idx = (skill_opts.index(chosen_r["skill_type"])
                     if chosen_r["skill_type"] in skill_opts else 0)
        edit_skill = e2.selectbox("Skill", skill_opts, index=skill_idx, key="prism_req_res_skill")
        ecol, rcol = st.columns([1, 1])
        if ecol.button("Apply resource edit", key="prism_req_res_apply"):
            ops = [_task_resource_crew_patch(task_index, chosen_r["index"], edit_crew)]
            if edit_skill != chosen_r["skill_type"]:
                ops.append(_task_resource_skill_patch(task_index, chosen_r["index"], edit_skill))
            _apply_ops(session, draft, ops, [],
                       success_msg=f"Staged edit to resource #{chosen_r['index']} of task {task_id}.")
        if rcol.button("Remove resource requirement", key="prism_req_res_remove"):
            op = _remove_task_resource_patch(task_index, chosen_r["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of a resource from task {task_id}.")

        # nested alternative_skill_types on the selected resource (NOT ref-validated).
        # The remove list is read here, BEFORE the add button below, so a just-added alternative
        # appears only on the next rerun (the benign one-run lag a live user never notices).
        st.markdown("**Alternative skills** (for the selected resource)")
        alts = chosen_r["alternatives"]
        if alts:
            alt_pick = st.selectbox("Alternative skill", range(len(alts)),
                                    format_func=lambda k: alts[k], key="prism_req_alt_pick")
            if st.button("Remove alternative skill", key="prism_req_alt_remove"):
                op = _remove_task_alt_skill_patch(task_index, chosen_r["index"], alt_pick)
                _apply_ops(session, draft, [op], [],
                           success_msg=f"Staged removal of alternative skill '{alts[alt_pick]}'.")
        if skills:
            alt_add = st.selectbox("Add alternative skill", skills, key="prism_req_alt_add")
            if st.button("Add alternative skill", key="prism_req_alt_add_btn"):
                op, issues = _add_task_alt_skill_patch(
                    draft.raw_working_tree, task_index, chosen_r["index"], alt_add)
                _apply_ops(session, draft, [op] if op else [], issues,
                           success_msg=f"Staged alternative skill '{alt_add}'.")

    # --- Equipment requirements (REF_MISSING-bound) ---
    st.markdown("**Equipment requirements**")
    equipment = _equipment_options(draft.raw_working_tree)
    if equipment:
        eq_labels = [e["equipment_id"] for e in equipment]
        ea1, ea2 = st.columns([2, 1])
        eq_add_pick = ea1.selectbox("Equipment", range(len(equipment)),
                                    format_func=lambda k: eq_labels[k],
                                    key="prism_req_equip_add_pick")
        eq_add_qty = ea2.number_input("Quantity needed", min_value=1, value=1, step=1,
                                      key="prism_req_equip_add_qty")
        if st.button("Add equipment requirement", key="prism_req_equip_add"):
            eid = equipment[eq_add_pick]["equipment_id"]
            op = _add_task_equipment_patch(draft.raw_working_tree, task_index, eid, eq_add_qty)
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged equipment {eid} for task {task_id}.")
    else:
        st.caption("No equipment yet — add some on the Equipment tab first.")

    eq_reqs = _task_equipment_reqs(draft.raw_working_tree, task_index)
    if eq_reqs:
        er_labels = [f"{e['equipment_id']} ×{e['quantity_needed']}" for e in eq_reqs]
        er_pick = st.selectbox("Equipment requirement", range(len(eq_reqs)),
                               format_func=lambda k: er_labels[k], key="prism_req_equip_pick")
        if st.button("Remove equipment requirement", key="prism_req_equip_remove"):
            op = _remove_task_equipment_patch(task_index, eq_reqs[er_pick]["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of equipment {eq_reqs[er_pick]['equipment_id']}.")

    # --- Consumable requirements (NOT ref-validated; quantity_needed <= 0 blocks at commit) ---
    st.markdown("**Consumable requirements**")
    consumables = _consumable_options(draft.raw_working_tree)
    if consumables:
        c_labels = [c["item_id"] for c in consumables]
        ca1, ca2 = st.columns([2, 1])
        cons_add_pick = ca1.selectbox("Consumable", range(len(consumables)),
                                      format_func=lambda k: c_labels[k],
                                      key="prism_req_cons_add_pick")
        cons_add_qty = ca2.number_input("Quantity needed", min_value=0.0, value=1.0, step=1.0,
                                        key="prism_req_cons_add_qty")
        if st.button("Add consumable requirement", key="prism_req_cons_add"):
            iid = consumables[cons_add_pick]["item_id"]
            op = _add_task_consumable_patch(draft.raw_working_tree, task_index, iid, cons_add_qty)
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged consumable {iid} for task {task_id}.")
    else:
        st.caption("No consumables yet — add some on the Consumables tab first.")

    cons_reqs = _task_consumable_reqs(draft.raw_working_tree, task_index)
    if cons_reqs:
        cr_labels = [f"{c['item_id']} ×{c['quantity_needed']}" for c in cons_reqs]
        cr_pick = st.selectbox("Consumable requirement", range(len(cons_reqs)),
                               format_func=lambda k: cr_labels[k], key="prism_req_cons_pick")
        if st.button("Remove consumable requirement", key="prism_req_cons_remove"):
            op = _remove_task_consumable_patch(task_index, cons_reqs[cr_pick]["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of consumable {cons_reqs[cr_pick]['item_id']}.")

    # --- System-state requirements (NOT ref-validated; required_state from declared valid_states) ---
    st.markdown("**System-state requirements**")
    systems = [s for s in _system_options(draft.raw_working_tree) if s["n_states"] > 0]
    if systems:
        sy_labels = [s["system_id"] for s in systems]
        sy_add_pick = st.selectbox("Plant system", range(len(systems)),
                                   format_func=lambda k: sy_labels[k], key="prism_req_sys_add_pick")
        chosen_sys = systems[sy_add_pick]
        state_opts = [row["state"] for row
                      in _system_state_options(draft.raw_working_tree, chosen_sys["index"])]
        sy_add_state = st.selectbox("Required state", state_opts, key="prism_req_sys_add_state")
        if st.button("Add system-state requirement", key="prism_req_sys_add"):
            op = _add_task_system_state_patch(
                draft.raw_working_tree, task_index, chosen_sys["system_id"], sy_add_state)
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged system state {chosen_sys['system_id']}={sy_add_state} "
                                   f"for task {task_id}.")
    else:
        st.caption("No plant systems with valid states yet — define states on the Systems tab first.")

    sys_reqs = _task_system_state_reqs(draft.raw_working_tree, task_index)
    if sys_reqs:
        sr_labels = [f"{s['system_id']} = {s['required_state']}" for s in sys_reqs]
        sr_pick = st.selectbox("System-state requirement", range(len(sys_reqs)),
                               format_func=lambda k: sr_labels[k], key="prism_req_sys_pick")
        if st.button("Remove system-state requirement", key="prism_req_sys_remove"):
            op = _remove_task_system_state_patch(task_index, sys_reqs[sr_pick]["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of system state {sys_reqs[sr_pick]['system_id']}.")


def _render_modes_scheduling_form(session, draft) -> None:
    """Modes & scheduling tab: author a task's three remaining schedule attributes — its hold
    point (``is_hold_point`` + ``hold_point_type``, managed as one pair so the misuse states the
    validator rejects are never emitted), its ``time_windows`` (hour-offset execution windows), and
    its execution ``modes`` (alternative crew/duration profiles, each with its own
    ``required_resources`` / ``required_equipment`` and optional ``dose_rate`` / ``mobilization_lead``
    overrides). As with the Task-requirements tab, pickers offer only existing pools, so the commit
    validator is the single arbiter of referential integrity — and NOTE the asymmetry: a mode's
    nested ``required_resources`` / ``required_equipment`` are NOT referentially validated, so a
    ghost skill / equipment inside a mode commits cleanly (only task-level refs block)."""
    tasks = _task_options(draft.raw_working_tree)
    if not tasks:
        st.caption("No tasks yet — add one on the Task & duration tab first.")
        return
    t_labels = [f"{t['task_id']} (dur {t['duration']})" for t in tasks]
    t_pick = st.selectbox("Task", range(len(tasks)), format_func=lambda k: t_labels[k],
                          key="prism_ms_task_pick")
    task_index = tasks[t_pick]["index"]
    task_id = tasks[t_pick]["task_id"]
    _NONE = "— none —"

    # --- Hold point (the is_hold_point / hold_point_type pair, driven by ONE selectbox) ---
    st.markdown("**Hold point**")
    hp = _task_hold_point(draft.raw_working_tree, task_index)
    current_type = hp["hold_point_type"] if hp["is_hold_point"] else None
    if hp["is_hold_point"]:
        st.caption(f"Current: hold point ({current_type or 'type not set'})")
    else:
        st.caption(f"Current: {_NONE} (not a hold point)")
    hp_opts = [_NONE, *_HOLD_POINT_TYPES]
    hp_default = hp_opts.index(current_type) if current_type in _HOLD_POINT_TYPES else 0
    hp_choice = st.selectbox("Hold point type", hp_opts, index=hp_default, key="prism_ms_hp_type")
    if st.button("Apply hold point", key="prism_ms_hp_apply"):
        if hp_choice != _NONE:
            _apply_ops(session, draft, _task_hold_point_set_patch(task_index, hp_choice), [],
                       success_msg=f"Staged hold point ({hp_choice}) for task {task_id}.")
        elif hp["is_hold_point"]:
            _apply_ops(session, draft,
                       _task_hold_point_clear_patch(draft.raw_working_tree, task_index), [],
                       success_msg=f"Staged clearing the hold point of task {task_id}.")
        else:
            st.info("Not a hold point — nothing to clear.")

    # --- Time windows (hour offsets from outage start; add / edit / remove) ---
    st.markdown("**Time windows** (hours from outage start)")
    wa1, wa2 = st.columns(2)
    tw_add_earliest = wa1.number_input("Earliest start (h)", min_value=0.0, value=0.0, step=1.0,
                                       key="prism_ms_tw_add_earliest")
    tw_add_latest = wa2.number_input("Latest finish (h)", min_value=0.0, value=0.0, step=1.0,
                                     key="prism_ms_tw_add_latest")
    if st.button("Add time window", key="prism_ms_tw_add"):
        op = _add_task_time_window_patch(draft.raw_working_tree, task_index,
                                         tw_add_earliest, tw_add_latest)
        _apply_ops(session, draft, [op], [],
                   success_msg=f"Staged time window [{tw_add_earliest}, {tw_add_latest}] h "
                               f"for task {task_id}.")

    windows = _task_time_windows(draft.raw_working_tree, task_index)
    if windows:
        w_labels = [f"[{w['earliest']}, {w['latest']}] h" for w in windows]
        w_pick = st.selectbox("Time window", range(len(windows)),
                              format_func=lambda k: w_labels[k], key="prism_ms_tw_pick")
        chosen_w = windows[w_pick]
        we1, we2 = st.columns(2)
        edit_earliest = we1.number_input("Earliest start (h)", min_value=0.0,
                                         value=_as_float(chosen_w["earliest"]), step=1.0,
                                         key="prism_ms_tw_earliest")
        edit_latest = we2.number_input("Latest finish (h)", min_value=0.0,
                                       value=_as_float(chosen_w["latest"]), step=1.0,
                                       key="prism_ms_tw_latest")
        wecol, wrcol = st.columns(2)
        if wecol.button("Apply time-window edit", key="prism_ms_tw_apply"):
            ops = _task_time_window_edit_patch(task_index, chosen_w["index"],
                                               edit_earliest, edit_latest)
            _apply_ops(session, draft, ops, [],
                       success_msg=f"Staged edit to time window #{chosen_w['index']} "
                                   f"of task {task_id}.")
        if wrcol.button("Remove time window", key="prism_ms_tw_remove"):
            op = _remove_task_time_window_patch(task_index, chosen_w["index"])
            _apply_ops(session, draft, [op], [],
                       success_msg=f"Staged removal of a time window from task {task_id}.")

    # --- Execution modes (add / edit-duration / optional overrides / remove + nested crew/equip).
    # `modes` is read AFTER the add button, so a just-added mode's edit widgets appear same-run;
    # but each mode's nested resource/equipment REMOVE lists come from that read (before their own
    # add buttons), so a just-added per-mode resource/equipment appears only on the next rerun. ---
    st.markdown("**Execution modes**")
    ma1, ma2 = st.columns([2, 1])
    mode_add_id = ma1.text_input("Mode id", key="prism_ms_mode_add_id",
                                 placeholder="normal / crash / reduced_crew")
    mode_add_dur = ma2.number_input("Duration (h)", min_value=0.0, value=1.0, step=1.0,
                                    key="prism_ms_mode_add_dur")
    if st.button("Add mode", key="prism_ms_mode_add"):
        op, issues = _add_task_mode_patch(draft.raw_working_tree, task_index,
                                          mode_add_id, mode_add_dur)
        _apply_ops(session, draft, [op] if op else [], issues,
                   success_msg=f"Staged mode '{mode_add_id.strip()}' for task {task_id}.")

    modes = _task_modes(draft.raw_working_tree, task_index)
    if modes:
        m_labels = [f"{m['mode_id']} (dur {m['duration']})" for m in modes]
        m_pick = st.selectbox("Mode", range(len(modes)),
                              format_func=lambda k: m_labels[k], key="prism_ms_mode_pick")
        chosen_m = modes[m_pick]
        mode_index = chosen_m["index"]

        edit_dur = st.number_input("Duration (h)", min_value=0.0,
                                   value=_as_float(chosen_m["duration"], 1.0), step=1.0,
                                   key="prism_ms_mode_dur")
        mdcol, mrcol = st.columns(2)
        if mdcol.button("Apply duration", key="prism_ms_mode_dur_apply"):
            _apply_ops(session, draft,
                       [_task_mode_duration_patch(task_index, mode_index, edit_dur)], [],
                       success_msg=f"Staged duration for mode '{chosen_m['mode_id']}'.")
        if mrcol.button("Remove mode", key="prism_ms_mode_remove"):
            _apply_ops(session, draft, [_remove_task_mode_patch(task_index, mode_index)], [],
                       success_msg=f"Staged removal of mode '{chosen_m['mode_id']}'.")

        # optional per-mode overrides -- a checkbox reveals a number_input (ADD) or, when a value
        # is already stored, a clear button (REMOVE, so the mode re-inherits the task-level value).
        dose_present = chosen_m["dose_rate"] is not None
        if st.checkbox("Override dose rate (mRem/h)", value=dose_present,
                       key="prism_ms_mode_dose_on"):
            dose_val = st.number_input("Dose rate (mRem/h)", min_value=0.0,
                                       value=_as_float(chosen_m["dose_rate"]), step=1.0,
                                       key="prism_ms_mode_dose_val")
            if st.button("Apply dose rate", key="prism_ms_mode_dose_apply"):
                _apply_ops(session, draft,
                           [_task_mode_dose_patch(task_index, mode_index, dose_val)], [],
                           success_msg=f"Staged dose-rate override for mode '{chosen_m['mode_id']}'.")
        elif dose_present and st.button("Clear dose-rate override", key="prism_ms_mode_dose_clear"):
            _apply_ops(session, draft, [_task_mode_dose_clear_patch(task_index, mode_index)], [],
                       success_msg=f"Staged clearing the dose-rate override of mode "
                                   f"'{chosen_m['mode_id']}'.")

        mob_present = chosen_m["mobilization_lead_hours"] is not None
        if st.checkbox("Override mobilization lead (h)", value=mob_present,
                       key="prism_ms_mode_mob_on"):
            mob_val = st.number_input("Mobilization lead (h)", min_value=0.0,
                                      value=_as_float(chosen_m["mobilization_lead_hours"]),
                                      step=1.0, key="prism_ms_mode_mob_val")
            if st.button("Apply mobilization lead", key="prism_ms_mode_mob_apply"):
                _apply_ops(session, draft,
                           [_task_mode_mob_patch(task_index, mode_index, mob_val)], [],
                           success_msg=f"Staged mobilization-lead override for mode "
                                       f"'{chosen_m['mode_id']}'.")
        elif mob_present and st.button("Clear mobilization-lead override",
                                       key="prism_ms_mode_mob_clear"):
            _apply_ops(session, draft, [_task_mode_mob_clear_patch(task_index, mode_index)], [],
                       success_msg=f"Staged clearing the mobilization-lead override of mode "
                                   f"'{chosen_m['mode_id']}'.")

        # nested per-mode resource requirements (NOT ref-validated -- the asymmetry). The remove
        # list is `chosen_m["resources"]`, read above (before this add button), so a just-added
        # per-mode resource appears only on the next rerun (the benign one-run lag).
        st.markdown("**Mode resources** (for the selected mode)")
        skills = sorted({r["skill_type"] for r in _resource_options(draft.raw_working_tree)})
        if skills:
            mr1, mr2 = st.columns([2, 1])
            mr_add_skill = mr1.selectbox("Skill", skills, key="prism_ms_mode_res_add_skill")
            mr_add_crew = mr2.number_input("Crew count", min_value=1, value=1, step=1,
                                           key="prism_ms_mode_res_add_crew")
            if st.button("Add mode resource", key="prism_ms_mode_res_add"):
                op = _add_task_mode_resource_patch(draft.raw_working_tree, task_index, mode_index,
                                                   mr_add_skill, mr_add_crew)
                _apply_ops(session, draft, [op], [],
                           success_msg=f"Staged resource {mr_add_skill} ×{mr_add_crew} for mode "
                                       f"'{chosen_m['mode_id']}'.")
        else:
            st.caption("No resource pools yet — add one on the Resources tab first.")

        mode_res = chosen_m["resources"]
        if mode_res:
            mr_labels = [f"{r['skill_type']} ×{r['crew_count']}" for r in mode_res]
            mr_pick = st.selectbox("Mode resource", range(len(mode_res)),
                                   format_func=lambda k: mr_labels[k], key="prism_ms_mode_res_pick")
            if st.button("Remove mode resource", key="prism_ms_mode_res_remove"):
                op = _remove_task_mode_resource_patch(task_index, mode_index,
                                                      mode_res[mr_pick]["index"])
                _apply_ops(session, draft, [op], [],
                           success_msg=f"Staged removal of a resource from mode "
                                       f"'{chosen_m['mode_id']}'.")

        # nested per-mode equipment requirements (NOT ref-validated -- the asymmetry).
        st.markdown("**Mode equipment** (for the selected mode)")
        equipment = _equipment_options(draft.raw_working_tree)
        if equipment:
            eq_labels = [e["equipment_id"] for e in equipment]
            me1, me2 = st.columns([2, 1])
            me_add_pick = me1.selectbox("Equipment", range(len(equipment)),
                                        format_func=lambda k: eq_labels[k],
                                        key="prism_ms_mode_equip_add_pick")
            me_add_qty = me2.number_input("Quantity needed", min_value=1, value=1, step=1,
                                          key="prism_ms_mode_equip_add_qty")
            if st.button("Add mode equipment", key="prism_ms_mode_equip_add"):
                eid = equipment[me_add_pick]["equipment_id"]
                op = _add_task_mode_equipment_patch(draft.raw_working_tree, task_index, mode_index,
                                                    eid, me_add_qty)
                _apply_ops(session, draft, [op], [],
                           success_msg=f"Staged equipment {eid} for mode '{chosen_m['mode_id']}'.")
        else:
            st.caption("No equipment yet — add some on the Equipment tab first.")

        mode_eq = chosen_m["equipment"]
        if mode_eq:
            meq_labels = [f"{e['equipment_id']} ×{e['quantity_needed']}" for e in mode_eq]
            meq_pick = st.selectbox("Mode equipment", range(len(mode_eq)),
                                    format_func=lambda k: meq_labels[k],
                                    key="prism_ms_mode_equip_pick")
            if st.button("Remove mode equipment", key="prism_ms_mode_equip_remove"):
                op = _remove_task_mode_equipment_patch(task_index, mode_index,
                                                       mode_eq[meq_pick]["index"])
                _apply_ops(session, draft, [op], [],
                           success_msg=f"Staged removal of equipment from mode "
                                       f"'{chosen_m['mode_id']}'.")


def _render_raw_patch_form(session, draft) -> None:
    """The Increment-1 raw JSON-Pointer editor, kept for power edits (and to keep the
    headless smoke valid). Same widget keys (``prism_patch_*``) and the same "Apply patch"
    button label as before — now inside the Advanced expander."""
    col_a, col_p, col_v = st.columns([1, 2, 2])
    action = col_a.selectbox("Action", [a.value for a in PatchAction], key="prism_patch_action")
    path = col_p.text_input("Path (JSON Pointer)", key="prism_patch_path",
                            placeholder="/tasks/0/duration")
    is_remove = action == PatchAction.REMOVE.value
    value_text = col_v.text_input("Value (JSON)", key="prism_patch_value",
                                  placeholder='8  or  "text"  or  [1,2]',
                                  disabled=is_remove)

    if st.button("Apply patch"):
        value = None
        if not is_remove:
            try:
                value = json.loads(value_text)
            except json.JSONDecodeError as exc:
                st.error(f"Value is not valid JSON: {exc}")
                value = _NO_VALUE
        if value is not _NO_VALUE:
            op = PatchOp(action=PatchAction(action), path=path, value=value)
            outcome = apply_patch(draft, op)
            session.set_draft(draft)
            if outcome.ok:
                st.success(f"Applied {action} {path}.")
            else:
                _render_issues(outcome.issues, empty_msg="No issues.")


def _render_editor(session, validator) -> None:
    """The editing lifecycle (§2b): open a draft from the current baseline, stage edits through
    structured forms (task/duration, task requirements, modes & scheduling, dependencies/lags,
    resources/availability, equipment, locations, consumables, systems) or the raw JSON-Pointer
    editor (Advanced), then commit (full schema + referential re-validation, minting a NEW
    immutable revision) or discard.

    Every form builds PatchOps and feeds them through the SAME ``domain.apply_patch``; the
    pending-patch log and the Commit / Discard controls are shared below the tabs. All state
    lives in the session draft, so this survives Streamlit reruns; a successful commit swaps
    the session baseline to the new plan and clears the draft, which the source-key guard in
    ``main`` then leaves in place across the rerun."""
    st.subheader("Edit plan")
    draft = session.get_draft()

    if draft is None:
        st.caption("Open a draft to stage edits against the current baseline.")
        if st.button("Open draft"):
            session.set_draft(open_draft(session.get_baseline()))
            st.rerun()
        return

    st.caption(f"Editing a draft of `{draft.base_plan_id}` — "
               f"{len(draft.pending_patches)} patch(es) staged.")

    (tab_task, tab_req, tab_sched, tab_dep, tab_res, tab_equip, tab_loc, tab_cons,
     tab_sys) = st.tabs(
        ["Task & duration", "Task requirements", "Modes & scheduling", "Dependencies & lags",
         "Resources & availability", "Equipment", "Locations", "Consumables", "Systems"])
    with tab_task:
        _render_task_form(session, draft)
    with tab_req:
        _render_task_requirements_form(session, draft)
    with tab_sched:
        _render_modes_scheduling_form(session, draft)
    with tab_dep:
        _render_dependency_form(session, draft)
    with tab_res:
        _render_resource_form(session, draft)
    with tab_equip:
        _render_equipment_form(session, draft)
    with tab_loc:
        _render_location_form(session, draft)
    with tab_cons:
        _render_consumable_form(session, draft)
    with tab_sys:
        _render_system_form(session, draft)

    with st.expander("Advanced — raw JSON-Pointer patch", expanded=False):
        _render_raw_patch_form(session, draft)

    if draft.pending_patches:
        st.markdown("**Pending patches**")
        st.dataframe(_patch_rows(draft.pending_patches),
                     use_container_width=True, hide_index=True)

    # --- commit / discard ---
    col_commit, col_discard = st.columns(2)
    if col_commit.button("Commit draft", type="primary", disabled=not draft.pending_patches):
        commit = services.commit_draft(draft, validator)
        if commit.ok:
            session.set_baseline(commit.plan)
            session.clear_draft()
            st.success(f"Committed new revision — plan_hash `{commit.plan.plan_hash}`.")
            _render_issues(commit.issues, empty_msg="Committed with no issues.")
            st.rerun()
        else:
            st.error("Commit blocked — the edited plan does not validate.")
            _render_issues(commit.issues)
    if col_discard.button("Discard draft"):
        discard_draft(draft)
        session.clear_draft()
        st.info("Draft discarded; baseline unchanged.")
        st.rerun()


def _source_key(reference_plan) -> str:
    """A stable signature of the source a baseline was loaded from: its logical id plus
    the canonical content hash. Reselecting the same file yields the same key (edits are
    preserved); picking a different sample / uploading a new file changes it."""
    return f"{reference_plan.plan_id}::{reference_plan.plan_hash}"


_NO_VALUE = object()   # sentinel: a value box that failed to parse (distinct from JSON null)


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


def _apply_scenario(session, new_scenario: Scenario, *, success_msg: str) -> None:
    """Update the CURRENT scenario overlay in place (add-or-update by id). The panel only
    authors when a scenario is the current schedule, so ``new_scenario`` keeps that id and the
    current-schedule pointer never moves (moving it would fight the keyed sidebar selector). A
    scenario is NOT a baseline edit: no draft, commit, or validator pass here — the domain
    re-validates the EFFECTIVE plan at Run time. An emptied overlay is kept as a named, empty
    scenario (the run then uses the plain baseline); true deletion lives in the scenario
    manager, not here."""
    session.add_scenario(new_scenario)
    if _scenario_is_empty(new_scenario):
        st.info("Scenario is now empty — the run will use the plain baseline.")
    else:
        st.success(success_msg)


def _render_scenario_panel(session, baseline) -> None:
    """Author a what-if scenario over the CURRENT session baseline: task duration overrides
    and resource-availability changes. A resource edit's intent is tagged — a *what-if*
    becomes a Scenario overlay (materialized into the effective plan at Run time and tracked
    by provenance freshness); a *baseline correction* is redirected to the editor's Resources
    tab, never authored here (the domain never guesses intent). Empty scenario == plain
    baseline."""
    with st.expander("What-if scenario (optional)", expanded=False):
        scenario = session.get_scenario()
        raw_tree = json.loads(baseline.raw_snapshot)["payload"]

        if not _scenario_is_empty(scenario) and scenario.base_plan_hash == baseline.plan_hash:
            st.caption(
                f"Active scenario: {len(scenario.duration_overrides or ())} duration "
                f"override(s), {len(scenario.resource_changes or ())} resource change(s).")
        else:
            st.caption("No scenario — the run uses the baseline as-is.")

        # --- task duration overrides ------------------------------------------
        st.markdown("**Task duration override**")
        tasks = _task_options(raw_tree)
        if not tasks:
            st.caption("No tasks to override.")
        else:
            t_labels = [f"{t['task_id']} (current {t['duration']}h)" for t in tasks]
            t_pick = st.selectbox("Task", range(len(tasks)), format_func=lambda k: t_labels[k],
                                  key="prism_scn_dur_task")
            chosen_t = tasks[t_pick]
            dur = st.number_input("Override duration (hours)", min_value=0.0,
                                  value=float(_as_float(chosen_t["duration"], 1.0)),
                                  step=1.0, key="prism_scn_dur_hours")
            if st.button("Add duration override", key="prism_scn_dur_add"):
                _apply_scenario(
                    session, _add_duration_override(scenario, baseline, chosen_t["task_id"], dur),
                    success_msg=f"Scenario: {chosen_t['task_id']} duration → {dur:g}h.")
                scenario = session.get_scenario()

        for row in _scenario_duration_rows(scenario):
            c1, c2 = st.columns([4, 1])
            c1.caption(f"• {row['task_id']} → {row['duration_hours']:g}h")
            if c2.button("Remove", key=f"prism_scn_dur_rm_{row['task_id']}"):
                _apply_scenario(
                    session, _remove_duration_override(scenario, baseline, row["task_id"]),
                    success_msg=f"Removed duration override for {row['task_id']}.")
                scenario = session.get_scenario()

        # --- resource-availability change (intent-tagged) ---------------------
        st.markdown("**Resource availability change**")
        intent = st.radio("This change is a…", _SCN_INTENTS, index=0, horizontal=True,
                          key="prism_scn_res_intent")
        pools = _resource_options(raw_tree)
        if not pools:
            st.caption("No resource pools in this plan.")
        else:
            r_labels = [f"{p['skill_type']} ({p['n_periods']} period(s))" for p in pools]
            r_pick = st.selectbox("Skill pool", range(len(pools)),
                                  format_func=lambda k: r_labels[k], key="prism_scn_res_pool")
            chosen_r = pools[r_pick]
            existing = _availability_options(raw_tree, chosen_r["index"])
            if existing:
                st.caption("Current availability: " + "; ".join(
                    f"[{p['start_date']} … {p['end_date']}) × {p['available_count']}"
                    for p in existing))
            c1, c2 = st.columns([1, 1])
            from_hour = c1.number_input("From hour", min_value=0.0, value=0.0, step=1.0,
                                        key="prism_scn_res_from")
            new_count = c2.number_input("New available count", min_value=0, value=0, step=1,
                                        key="prism_scn_res_count")
            if _is_whatif(intent):
                if st.button("Add resource what-if", key="prism_scn_res_add"):
                    _apply_scenario(
                        session,
                        _add_resource_change(scenario, baseline, chosen_r["skill_type"],
                                             from_hour, new_count),
                        success_msg=(f"Scenario: {chosen_r['skill_type']} → {int(new_count)} "
                                     f"from hour {from_hour:g}."))
                    scenario = session.get_scenario()
            else:
                st.info(
                    "A roster **correction** is a baseline change, not a what-if. Make it in the "
                    "**Resources & availability** tab of the plan editor above (open a draft → "
                    "change the pool's available count → commit). That keeps the correction in "
                    "your baseline, where scheduling and provenance expect it.")

        for row in _scenario_resource_rows(scenario):
            c1, c2 = st.columns([4, 1])
            c1.caption(f"• {row['skill_type']} → {row['new_count']} from hour {row['from_hour']:g}")
            if c2.button("Remove", key=f"prism_scn_res_rm_{row['index']}"):
                _apply_scenario(
                    session, _remove_resource_change(scenario, baseline, row["index"]),
                    success_msg=f"Removed resource change for {row['skill_type']}.")
                scenario = session.get_scenario()

        # --- reset overlay (keep the named scenario; empty its deltas) --------
        if not _scenario_is_empty(scenario):
            if st.button("Reset overlay", key="prism_scn_reset"):
                session.add_scenario(replace(
                    scenario, duration_overrides=None, resource_changes=None,
                    equipment_changes=None, hold_point_release_overrides=None,
                    emergent_tasks=None, emergent_dependencies=None))
                st.success("Overlay reset — this scenario is now empty (the run uses the plain "
                           "baseline). Delete it in **Scenarios** to remove it entirely.")
                st.rerun()


def _render_scenario_manager(session, baseline, *, allow_edit: bool) -> None:
    """Create / rename / delete scenarios. Creating a new scenario is always available; rename
    and delete act on the CURRENT scenario and are shown only when a scenario is selected
    (``allow_edit``). All three route their selection change through ``_select_schedule_next_run``
    / a rerun so the sidebar selector stays in sync (see its widget-reconcile note)."""
    with st.expander("Scenarios", expanded=False):
        scenarios = session.list_scenarios()
        ids = [s.scenario_id for s in scenarios]
        st.caption(f"{len(scenarios)} scenario(s) on this baseline. "
                   "Scenarios are thin overlays (durations + resource what-ifs) — structural "
                   "edits belong to the Baseline.")

        # --- create -----------------------------------------------------------
        new_name = st.text_input("New scenario name", key="prism_scn_new_name",
                                 placeholder="(auto-named if left blank)")
        if st.button("Create scenario", key="prism_scn_create"):
            scn = _mint_scenario(baseline, existing_ids=ids, name=new_name.strip() or None)
            session.add_scenario(scn)
            _select_schedule_next_run(scn.scenario_id)

        # --- rename / delete the current scenario -----------------------------
        if allow_edit:
            cur = session.get_scenario()
            if cur is not None:
                st.divider()
                # A per-scenario widget key: value= seeds each scenario's box once, so switching
                # scenarios shows the right name without a value/session-state collision warning.
                rename = st.text_input("Rename current scenario", value=cur.name or "",
                                       key=f"prism_scn_rename_{cur.scenario_id}")
                c1, c2 = st.columns(2)
                if c1.button("Rename", key="prism_scn_rename_btn"):
                    session.add_scenario(replace(cur, name=rename.strip() or None))
                    st.success(f"Renamed to “{rename.strip() or cur.scenario_id}”.")
                    st.rerun()
                if c2.button("Delete scenario", key="prism_scn_delete"):
                    session.remove_scenario(cur.scenario_id)
                    _select_schedule_next_run(None)   # detach to the baseline


def _render_mode_picker(session, baseline) -> None:
    """Pick an execution MODE per multi-mode task in the current schedule. The picks are held on
    the session and merged into the RunConfig at Run (in ``main()``), so they ride the run-config
    hash / freshness rather than becoming a scenario delta. Reads the MATERIALIZED current
    schedule, so an emergent multi-mode task would be offered too. No shipping sample defines
    modes → the caption path, and the stored selection is cleared so a stale pick never rides."""
    with st.expander("Execution modes (optional)", expanded=False):
        payload, _warning = _current_schedule_payload(baseline, session.get_scenario())
        options = _mode_options(payload)
        if not options:
            st.caption("No multi-mode tasks in this plan.")
            session.set_mode_selections(())
            return
        st.caption("Choose an execution mode per multi-mode task; the picks apply to the run.")
        existing = {ms.task_id: ms.mode_name for ms in session.get_mode_selections()}
        selections: list[ModeSelection] = []
        for row in options:
            names = [m["mode_name"] for m in row["modes"]]
            labels = {m["mode_name"]: f"{m['mode_name']} ({m['duration']}h)" for m in row["modes"]}
            default = existing.get(row["task_id"], names[0])
            idx = names.index(default) if default in names else 0
            pick = st.selectbox(
                f"Mode for {row['task_id']}", names, index=idx,
                format_func=lambda n: labels.get(n, n), key=f"prism_mode_{row['task_id']}")
            selections.append(ModeSelection(task_id=row["task_id"], mode_name=pick))
        session.set_mode_selections(tuple(selections))


def _graph_layout(fig, height: int) -> None:
    """Common styling for a network-scatter figure: hidden axes, tight margins, no legend —
    so the nodes/edges read as a graph, not a chart. Mutates ``fig`` in place."""
    fig.update_layout(
        height=height, showlegend=False, margin=dict(l=10, r=10, t=10, b=10),
        hovermode="closest", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    axis = dict(showgrid=False, zeroline=False, showticklabels=False)
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)


def _render_activity_graph(session, baseline) -> None:
    """The current schedule's activity/dependency DAG (Plotly network scatter): tasks as nodes
    laid out left→right by depth, precedence links as edges. Fed by the MATERIALIZED current
    schedule, so a scenario's duration override / emergent task is reflected here. Labels are
    dropped on large plans (kept hover-only) so the picture stays legible."""
    import plotly.graph_objects as go

    scenario = session.get_scenario()
    payload, warning = _current_schedule_payload(baseline, scenario)
    if warning:
        st.warning(warning)
    data = _activity_graph_data(payload)
    nodes = data["nodes"]
    if not nodes:
        st.info("This plan has no activities to graph.")
        return
    by_id = {n["id"]: n for n in nodes}

    ex: list = []
    ey: list = []
    for src, dst in data["edges"]:
        ex += [by_id[src]["x"], by_id[dst]["x"], None]
        ey += [by_id[src]["y"], by_id[dst]["y"], None]

    fig = go.Figure()
    if ex:
        fig.add_trace(go.Scatter(x=ex, y=ey, mode="lines", line=dict(color="#b0b7c3", width=1),
                                 hoverinfo="skip"))
    show_labels = len(nodes) <= 60          # cap labels; hover always carries the detail
    fig.add_trace(go.Scatter(
        x=[n["x"] for n in nodes], y=[n["y"] for n in nodes],
        mode="markers+text" if show_labels else "markers",
        text=[n["label"] for n in nodes] if show_labels else None,
        textposition="top center",
        marker=dict(size=18, color="#1f77b4", line=dict(color="#0d3b66", width=1)),
        hovertext=[f"{n['label']}<br>duration: {n['duration']}h<br>depth: {n['depth']}"
                   for n in nodes],
        hoverinfo="text"))
    _graph_layout(fig, height=460)
    if data["has_cycle"]:
        st.warning("The dependency graph contains a cycle — the layout is approximate.")
    st.plotly_chart(fig, use_container_width=True)


def _render_relation_graph(session, baseline) -> None:
    """The baseline → scenarios relation graph (Plotly network scatter): the baseline at the
    centre with each stored scenario on a ring around it, an edge to each. The baseline is a
    square, scenarios circles; the CURRENT schedule is highlighted. Hover shows each scenario's
    staged overlay count."""
    import plotly.graph_objects as go

    scenarios = session.list_scenarios()
    data = _relation_graph_data(baseline, scenarios, session.get_current_scenario_id())
    nodes = data["nodes"]
    by_id = {n["id"]: n for n in nodes}

    ex: list = []
    ey: list = []
    for src, dst in data["edges"]:
        ex += [by_id[src]["x"], by_id[dst]["x"], None]
        ey += [by_id[src]["y"], by_id[dst]["y"], None]

    fig = go.Figure()
    if ex:
        fig.add_trace(go.Scatter(x=ex, y=ey, mode="lines", line=dict(color="#b0b7c3", width=1),
                                 hoverinfo="skip"))

    def _hover(n: dict) -> str:
        if n["kind"] == "baseline":
            return f"Baseline<br>{n['label']}"
        return f"Scenario “{n['label']}”<br>{n['overlay_count']} overlay change(s)"

    fig.add_trace(go.Scatter(
        x=[n["x"] for n in nodes], y=[n["y"] for n in nodes],
        mode="markers+text", text=[n["label"] for n in nodes], textposition="top center",
        marker=dict(
            size=[30 if n["kind"] == "baseline" else 22 for n in nodes],
            symbol=["square" if n["kind"] == "baseline" else "circle" for n in nodes],
            color=["#d62728" if n["is_current"] else
                   ("#1f77b4" if n["kind"] == "baseline" else "#7f7f7f") for n in nodes],
            line=dict(color="#222", width=[3 if n["is_current"] else 1 for n in nodes])),
        hovertext=[_hover(n) for n in nodes], hoverinfo="text"))
    _graph_layout(fig, height=420)
    st.plotly_chart(fig, use_container_width=True)


def _render_graphs(session, baseline) -> None:
    """Graphs tab: the current schedule's activity/dependency DAG (top) and the baseline →
    scenarios relation graph (bottom). Both are input/lineage-derived, so available before any
    run."""
    st.subheader("Activity / dependency graph")
    st.caption(f"**{_schedule_label(session, baseline)}** — tasks and precedence links, laid "
               "out left→right by depth. Hover a node for its duration.")
    _render_activity_graph(session, baseline)

    st.divider()
    st.subheader("Baseline → scenarios")
    scenarios = session.list_scenarios()
    st.caption(f"{len(scenarios)} scenario(s) hang off this baseline as overlays; the current "
               "schedule is highlighted in red.")
    _render_relation_graph(session, baseline)


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


def main() -> None:
    if not _HAS_STREAMLIT:  # pragma: no cover - guarded entry
        raise SystemExit(
            "PRISM GUI requires Streamlit. Install it and launch the app:\n"
            "    pip install streamlit\n"
            "    streamlit run src/prismGui/app/main.py"
        )

    st.set_page_config(page_title="PRISM Scheduler", layout="wide")
    st.title("PRISM — Outage Schedule")
    st.caption("Load a plan and configure the run in the sidebar, then explore the selected "
               "schedule in the tabs: data, plots (Gantt + resource utilization), and "
               "what-if editing.")

    session = StreamlitSessionState()
    validator = build_validator()
    store = InMemorySnapshotStore()
    executor = _make_executor(store)
    repository = InMemoryRepository()

    raw, plan_id = _pick_source()
    if raw is None:
        st.info("Choose a sample project or upload a plan JSON to begin.")
        return

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
    source_key = _source_key(load.reference_plan)
    if session.get_source_key() != source_key:
        session.set_source_key(source_key)
        # Point the session at the new baseline and resolve anything bound to a prior
        # revision: a stale draft OR scenario is cleared, a compatible one is kept (the
        # pure services.resolve_for_new_baseline the group-J contract drives headlessly).
        services.resolve_for_new_baseline(session, load.reference_plan)

    baseline = session.get_baseline()

    # Current-schedule selector (baseline or a scenario) — read the pointer within this same
    # rerun so every tab below and the Run button operate on the selected schedule.
    _render_schedule_selector(session, baseline)

    run_config = _pick_run_config(plan_id)
    # Fold in the execution-mode picks (What-if / Edit tab) — kept on the RunConfig, so they ride
    # the run-config hash / freshness. The picker persists them to the session on each render, so
    # this reads the latest picks. Empty (the shipping-sample case) leaves the default ().
    run_config = replace(run_config, mode_selections=session.get_mode_selections())
    session.set_run_config(run_config)

    # --- run controls live in the sidebar (below the run configuration) ---
    if st.sidebar.button("Run schedule", type="primary"):
        # Run the CURRENT session baseline's payload — a committed edit is what runs — with
        # the session scenario (if any) materialized into the effective plan by prepare_run.
        payload = json.loads(baseline.raw_snapshot)["payload"]
        with st.spinner("Scheduling…"):
            outcome = run_pipeline(
                payload, baseline.plan_id, run_config,
                validator=validator, store=store, executor=executor, repository=repository,
                scenario=session.get_scenario())
        if not outcome.ok:
            st.sidebar.error(f"Cannot run — blocked at {outcome.stage}.")
            st.error(f"Run blocked at {outcome.stage}.")
            _render_issues(outcome.issues)
        else:
            session.add_run_result(outcome.result)
            session.set_selected_result_id(outcome.result.run_id)

    # --- persistent results header (only when a run is selected) ---
    result = None
    selected_id = session.get_selected_result_id()
    if selected_id:
        result = session.get_run_result(selected_id)
        if result is not None:
            _render_results_header(result, session, baseline, session.get_run_config())

    # --- the views of the current schedule (baseline or the selected scenario) ---
    tab_data, tab_plots, tab_graphs, tab_edit = st.tabs(
        ["Data viewer", "Plots", "Graphs", "What-if / Edit"])
    with tab_data:
        _render_data_viewer(session, baseline)
    with tab_plots:
        if result is not None and result.status is RunResultStatus.COMPLETED:
            _render_plots(result)
        elif result is not None:
            st.info("The selected run did not complete — see the failure above.")
        else:
            st.info("Run a schedule (sidebar) to see the Gantt and resource plots.")
    with tab_graphs:
        _render_graphs(session, baseline)
    with tab_edit:
        # Edit target follows the current-schedule selection (D1): Baseline -> the full
        # structural editor (add/remove tasks, constraints); a scenario -> the thin-overlay
        # what-if panel, with structural edits routed back to the Baseline.
        if session.get_current_scenario_id() is None:
            _render_editor(session, validator)
            _render_scenario_manager(session, baseline, allow_edit=False)
        else:
            st.info("A scenario is a thin overlay (task durations + resource what-ifs). To "
                    "add/remove tasks or edit constraints, select **Baseline** in the sidebar "
                    "and edit the baseline itself.")
            _render_scenario_panel(session, baseline)
            _render_scenario_manager(session, baseline, allow_edit=True)
        # Execution-mode picks apply to whatever schedule runs (a run-config concern), so the
        # picker sits below both edit targets.
        _render_mode_picker(session, baseline)


if __name__ == "__main__":
    main()
