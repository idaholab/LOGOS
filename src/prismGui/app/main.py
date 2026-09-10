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
import sys
from dataclasses import dataclass
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
from prismGui.domain.results import DispositionOverall, Freshness, RunResultStatus
from prismGui.domain.run_config import PRIORITY_RULES, RunConfig, SGSVariant
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
) -> PipelineResult:
    """The exact sequence the Run button triggers, factored out so it can be driven
    headlessly: load+validate → prepare_run → run. Blocking is reported per stage; a
    non-ok pipeline never fabricates a result."""
    load = services.load_and_validate(plan_id, raw_plan, validator)
    if not load.ok:
        return PipelineResult(ok=False, stage="load", issues=load.issues)

    prep = services.prepare_run(load.reference_plan, None, run_config, store, validator=validator)
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
# Streamlit-backed session state (the app's SessionState implementation)
# =============================================================================

class StreamlitSessionState:
    """``services.SessionState`` over ``st.session_state`` — the ONLY code that knows the
    session-state keys. Constructed inside ``main()`` (touches ``st``)."""

    _BASELINE = "prism_baseline"
    _DRAFT = "prism_draft"
    _SCENARIO = "prism_scenario"
    _RUN_CONFIG = "prism_run_config"
    _RESULTS = "prism_results"
    _SELECTED = "prism_selected_id"
    _SOURCE_KEY = "prism_source_key"      # signature of the last file-reloaded source

    def __init__(self) -> None:
        st.session_state.setdefault(self._BASELINE, None)
        st.session_state.setdefault(self._DRAFT, None)
        st.session_state.setdefault(self._SCENARIO, None)
        st.session_state.setdefault(self._RUN_CONFIG, None)
        st.session_state.setdefault(self._RESULTS, {})
        st.session_state.setdefault(self._SELECTED, None)
        st.session_state.setdefault(self._SOURCE_KEY, None)

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

    def get_scenario(self):
        return st.session_state[self._SCENARIO]

    def set_scenario(self, scenario) -> None:
        st.session_state[self._SCENARIO] = scenario

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


def _render_gantt(schedule) -> None:
    import altair as alt  # lazy: Streamlit ships Altair, but keep it off module import

    rows = _gantt_rows(schedule)
    st.markdown("**Gantt**")
    if not rows:
        st.caption("No scheduled activities to chart.")
        return
    chart = (
        alt.Chart(alt.Data(values=rows))
        .mark_bar(stroke="#111", strokeWidth=0)
        .encode(
            x=alt.X("start:Q", title="hours since project start"),
            x2="end:Q",
            y=alt.Y("task:N", sort=[r["task"] for r in rows], title=None),
            color=alt.Color("float_class:N", title="float class"),
            opacity=alt.condition("datum.on_chain", alt.value(1.0), alt.value(0.55)),
            tooltip=["task:N", "start:Q", "end:Q", "duration:Q", "delay:Q",
                     "float_class:N", "on_chain:N", "description:N"],
        )
    )
    st.altair_chart(chart, use_container_width=True)


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


def _render_resource_util(util) -> None:
    st.markdown("**Resource utilization**")
    rows = _resource_util_rows(util)
    if not rows:
        st.caption("No resource-utilization data for this run.")
        return
    import altair as alt  # lazy: same discipline as _render_gantt

    base = alt.Chart(alt.Data(values=rows)).encode(
        x=alt.X("start_hour:Q", title="hours since project start"))
    demand = base.mark_area(interpolate="step-after", opacity=0.5, color="#3498db").encode(
        y=alt.Y("demand:Q", title="crew"))
    available = base.mark_line(
        interpolate="step-after", strokeDash=[4, 3], color="#e67e22").encode(
        y=alt.Y("available:Q", title="crew"))
    chart = (
        alt.layer(demand, available)
        .facet(row=alt.Row("skill:N", title="skill"))
        .resolve_scale(y="independent")
    )
    st.altair_chart(chart, use_container_width=True)


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


def _render_result(result, session, baseline, run_config) -> None:
    st.subheader("Result")
    if result.status is not RunResultStatus.COMPLETED:
        st.error(f"Run {result.status.value}.")
        _render_issues(result.issues, empty_msg="No diagnostics.")
        return

    s = result.schedule
    c1, c2, c3 = st.columns(3)
    c1.metric("Makespan (h)", f"{s.makespan_hours:g}")
    c2.metric("CPM lower bound (h)", f"{s.cpm_lower_bound_hours:g}")
    c3.metric("Optimism gap (h)", f"{s.optimism_gap_hours:g}")

    _render_disposition_panel(result.disposition)

    freshness, reasons = services.current_freshness_detail(
        result, baseline=baseline, run_config=run_config)
    _render_provenance_freshness(result, freshness, reasons)

    _render_gantt(s)

    util = result.diagnostics.resource_utilization if result.diagnostics is not None else None
    _render_resource_util(util)

    st.markdown("**Schedule**")
    _render_schedule_table(s)
    _render_schedule_export(s)

    if result.issues:
        st.markdown("**Audit findings**")
        _render_issues(result.issues)

    if result.diagnostics is not None and result.diagnostics.fitness is not None:
        f = result.diagnostics.fitness
        st.caption(
            f"fitness: composite={f.composite:g} · makespan_ratio={f.makespan_ratio:g} · "
            f"delay_ratio={f.delay_ratio:g} · criticality_ratio={f.criticality_ratio:g} · "
            f"window_violations={f.n_window_violations}"
        )


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
    structured forms (task/duration, dependencies/lags, resources/availability, equipment,
    locations) or the raw JSON-Pointer editor (Advanced), then commit (full schema + referential
    re-validation, minting a NEW immutable revision) or discard.

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

    tab_task, tab_dep, tab_res, tab_equip, tab_loc = st.tabs(
        ["Task & duration", "Dependencies & lags", "Resources & availability",
         "Equipment", "Locations"])
    with tab_task:
        _render_task_form(session, draft)
    with tab_dep:
        _render_dependency_form(session, draft)
    with tab_res:
        _render_resource_form(session, draft)
    with tab_equip:
        _render_equipment_form(session, draft)
    with tab_loc:
        _render_location_form(session, draft)

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


def _pick_run_config(plan_id: str) -> RunConfig:
    """Sidebar SGS + priority-rule + seed selectors -> a RunConfig. The rule list is the
    engine's own 22-key library, so an unknown key is impossible."""
    st.sidebar.header("Run configuration")
    sgs_value = st.sidebar.selectbox(
        "Schedule-generation scheme", [v.value for v in SGSVariant], index=0)
    rule = st.sidebar.selectbox(
        "Priority rule", list(PRIORITY_RULES),
        index=PRIORITY_RULES.index("lf"))
    seed = int(st.sidebar.number_input("Seed", min_value=0, value=42, step=1))
    return RunConfig(run_config_id=f"rc-{plan_id}", sgs=SGSVariant(sgs_value),
                     priority_rule=rule, seed=seed)


def main() -> None:
    if not _HAS_STREAMLIT:  # pragma: no cover - guarded entry
        raise SystemExit(
            "PRISM GUI requires Streamlit. Install it and launch the app:\n"
            "    pip install streamlit\n"
            "    streamlit run src/prismGui/app/main.py"
        )

    st.set_page_config(page_title="PRISM Scheduler", layout="wide")
    st.title("PRISM — Outage Schedule")
    st.caption("Load → validate → run one schedule → view: Gantt, resource utilization, "
               "disposition, provenance, and CSV export.")

    session = StreamlitSessionState()
    validator = build_validator()
    store = InMemorySnapshotStore()
    executor = _make_executor(store)
    repository = InMemoryRepository()

    raw, plan_id = _pick_source()
    if raw is None:
        st.info("Choose a sample project or upload a plan JSON to begin.")
        return

    # --- validation panel (of the freshly-selected source file) ---
    st.subheader("Validation")
    load = services.load_and_validate(plan_id, raw, validator)
    _render_issues(load.issues, empty_msg="Plan is valid — no issues.")
    if not load.ok:
        st.error("Plan has blocking errors; fix them before running.")
        return

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

    # --- edit plan (may commit a new revision into the session baseline) ---
    _render_editor(session, validator)
    baseline = session.get_baseline()

    run_config = _pick_run_config(plan_id)
    session.set_run_config(run_config)

    if st.button("Run schedule", type="primary"):
        # Run the CURRENT session baseline's payload — a committed edit is what runs.
        payload = json.loads(baseline.raw_snapshot)["payload"]
        with st.spinner("Scheduling…"):
            outcome = run_pipeline(
                payload, baseline.plan_id, run_config,
                validator=validator, store=store, executor=executor, repository=repository)
        if not outcome.ok:
            st.error(f"Cannot run — blocked at {outcome.stage}.")
            _render_issues(outcome.issues)
        else:
            session.add_run_result(outcome.result)
            session.set_selected_result_id(outcome.result.run_id)

    selected_id = session.get_selected_result_id()
    if selected_id:
        result = session.get_run_result(selected_id)
        if result is not None:
            _render_result(result, session, session.get_baseline(), session.get_run_config())


if __name__ == "__main__":
    main()
