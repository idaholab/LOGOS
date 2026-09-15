"""Pure view-data builders: table/CSV rows, label maps, and graph-data shapers."""
from __future__ import annotations

import csv
import io
import math
from itertools import product
from typing import Optional

from prismGui.domain.results import FloatClass, Freshness
from prismGui.domain.run_config import EvaluationWeights, ModeSelection
from prismGui.domain.scenario import Scenario
from prismGui.domain.hashing import hash_scenario
from prismGui.app.edit_model import _dependency_options, _task_options


def _evaluation_weights(
    alpha: float, beta: float, gamma: float, delta: float
) -> Optional[EvaluationWeights]:
    """Assemble the four sidebar fitness-weight inputs into an ``EvaluationWeights``, or ``None``
    when all four equal the engine/dataclass defaults (1.0/0.5/0.3/2.0). Returning ``None`` at the
    defaults keeps a GUI "defaults" run byte-identical to the no-weights path — same run_config_hash,
    same engine fitness — so this control is strictly additive and invisible until a weight is actually
    customized. (The weights are post-hoc COMPARISON weights: they re-score a completed schedule's
    composite fitness, they do NOT change the search.) Pure — a frozen domain dataclass, no ``st``."""
    weights = EvaluationWeights(alpha=alpha, beta=beta, gamma=gamma, delta=delta)
    return None if weights == EvaluationWeights() else weights


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

def _scenario_hash_labels(scenarios) -> dict[str, str]:
    """``{hash_scenario(s): s.name or s.scenario_id}`` for the live session scenarios — the
    forward-hash map used to resolve a run's ``provenance.scenario_delta_hash`` back to a human
    label. Resolution is FORWARD (the direction ``services.current_freshness`` already uses): a
    provenance hash cannot be decoded, because the canonical scenario payload deliberately omits the
    id/name, so we re-hash the live scenarios and match. Pure — hashing is stdlib SHA-256, no ``st``."""
    return {hash_scenario(s): (s.name or s.scenario_id) for s in scenarios}

def _comparison_rows(results, scenario_labels, freshness_by_run) -> list[dict]:
    """One streamlit-free row per run for the side-by-side comparison table, in the given order —
    ``{run_id, scenario, status, makespan_hours, cpm_lower_bound_hours, optimism_gap_hours, fitness,
    disposition, freshness}``. The ``scenario`` label resolves ``provenance.scenario_delta_hash``
    against ``scenario_labels`` (``None`` → ``"Baseline"``; unmatched → ``"scenario <8-char prefix>"``,
    i.e. that scenario was edited/removed since the run). Schedule metrics are ``None`` for a run with
    no schedule (FAILED/CANCELLED — the ``status`` column explains the blanks, per the "no data → None
    cell" convention); ``fitness`` is ``None`` unless a ``FitnessDTO`` rides ``diagnostics``. The
    ``freshness`` cell maps ``freshness_by_run[run_id]`` (a ``{run_id: Freshness}`` map the caller
    computes via ``services`` — kept out of this module so it stays ``st``/``services``-free) through
    ``_FRESHNESS_LABEL``, empty string when a run is absent from the map. Pure — no ``st``."""
    rows: list[dict] = []
    for r in results:
        h = r.provenance.scenario_delta_hash
        scenario = "Baseline" if h is None else scenario_labels.get(h, f"scenario {h[:8]}")
        sched = r.schedule
        fitness = None
        if r.diagnostics is not None and r.diagnostics.fitness is not None:
            fitness = r.diagnostics.fitness.composite
        rows.append({
            "run_id": r.run_id,
            "scenario": scenario,
            "status": r.status.value,
            "makespan_hours": None if sched is None else sched.makespan_hours,
            "cpm_lower_bound_hours": None if sched is None else sched.cpm_lower_bound_hours,
            "optimism_gap_hours": None if sched is None else sched.optimism_gap_hours,
            "fitness": fitness,
            "disposition": r.disposition.overall.value if r.disposition is not None else None,
            "freshness": _FRESHNESS_LABEL.get(freshness_by_run.get(r.run_id), ""),
        })
    return rows

def _augmentation_candidates(util) -> list[dict]:
    """One row per resource pool from a ``ResourceUtilizationDTO``, ranked bottleneck-first, to guide
    a resource-augmentation what-if — ``{skill_type, current_count, saturated_hours, peak_shortfall,
    time_varying}``. ``current_count`` is the pool's availability over the interval covering hour 0
    (the whole-outage bump's baseline); ``saturated_hours`` sums the interval lengths where
    the pool is tight **with real demand** (``demand > 0 and demand >= available`` — so an idle
    ``0``-of-``0`` pool is never mistaken for a bottleneck, the trap the example_10 smoke exposed) and
    ``peak_shortfall`` is the largest ``demand - available`` (0 when never over-subscribed) — an
    aggregate/heuristic pressure signal (kin to ``_saturated_skills``), NOT the
    authoritative per-task binding resource. ``time_varying`` flags a pool whose availability changes
    across intervals (a from-hour-0 bump flattens it to one count). Sorted by ``(saturated_hours,
    peak_shortfall, skill_type)`` so the tightest pool leads (the selectbox default). ``util is None``
    (or a pool with no intervals) contributes nothing → ``[]``. Pure — no ``st``."""
    if util is None:
        return []
    rows: list[dict] = []
    for series in util.series:
        intervals = series.intervals
        if not intervals:
            continue
        covering = next((iv for iv in intervals if iv.start_hour <= 0 < iv.end_hour), intervals[0])
        saturated_hours = sum(
            iv.end_hour - iv.start_hour for iv in intervals
            if iv.demand > 0 and iv.demand >= iv.available)
        peak_shortfall = max(0, max(iv.demand - iv.available for iv in intervals))
        rows.append({
            "skill_type": series.skill_type,
            "current_count": covering.available,
            "saturated_hours": saturated_hours,
            "peak_shortfall": peak_shortfall,
            "time_varying": len({iv.available for iv in intervals}) > 1,
        })
    rows.sort(key=lambda r: (-r["saturated_hours"], -r["peak_shortfall"], r["skill_type"]))
    return rows

def _augmentation_delta(before, after) -> list[dict]:
    """Before/after rows for a resource-augmentation what-if — one dict per headline metric
    ``{metric, before, after, delta, pct}`` with ``delta = after - before`` and ``pct = delta /
    before`` (``None`` when ``before`` is missing or zero). Reads ``makespan_hours`` and
    ``optimism_gap_hours`` off each run's ``schedule`` and the composite off ``diagnostics.fitness``;
    a missing schedule/fitness (a FAILED side, a bare ``DiagnosticsDTO``) yields ``None`` cells rather
    than raising (the "no data → None cell" convention). ``cpm_lower_bound_hours`` is omitted — it is
    logical and unchanged by resources. Pure — takes two ``RunResult`` DTOs, no ``st``/``services``."""
    def _sched(run, attr):
        s = run.schedule
        return None if s is None else getattr(s, attr)

    def _fitness(run):
        d = run.diagnostics
        return d.fitness.composite if (d is not None and d.fitness is not None) else None

    specs = (
        ("makespan_hours", _sched(before, "makespan_hours"), _sched(after, "makespan_hours")),
        ("optimism_gap_hours", _sched(before, "optimism_gap_hours"), _sched(after, "optimism_gap_hours")),
        ("fitness_composite", _fitness(before), _fitness(after)),
    )
    rows: list[dict] = []
    for metric, b, a in specs:
        numeric = isinstance(b, (int, float)) and isinstance(a, (int, float))
        delta = (a - b) if numeric else None
        pct = (delta / b) if (numeric and b) else None
        rows.append({"metric": metric, "before": b, "after": a, "delta": delta, "pct": pct})
    return rows

def _sweep_rows(results, label_by_run, freshness_by_run, label_key="priority_rule") -> list[dict]:
    """Leaderboard rows for a config sweep (Phase-4.3) — one dict per swept run, ranked
    shortest-makespan-first, ``{<label_key>, status, makespan_hours, optimism_gap_hours, fitness,
    delta_vs_best, freshness}``. The sweep varies ONE ``RunConfig`` axis (priority rule / SGS variant /
    seed) across otherwise-identical BASELINE runs; the swept value is NOT recoverable from the stored
    ``RunResult`` (provenance keeps only the one-way ``run_config_hash``), so the caller tracks
    ``{run_id: label}`` at sweep time and passes it as ``label_by_run``, naming the label column via
    ``label_key`` (``"priority_rule"`` / ``"sgs"`` / ``"seed"``; default preserves the priority-rule
    shape); a run absent from that map labels ``""``. Labels are always strings (seed → ``str(seed)``) so
    the tiebreak stays type-homogeneous. ``delta_vs_best`` is ``makespan_hours - best`` where ``best`` is
    the smallest makespan among runs that have one (``0`` for the winner, ``None`` when this run — or every
    run — has no makespan). ``fitness`` is the composite when a ``FitnessDTO`` is present else ``None``;
    ``freshness`` maps the supplied ``{run_id: Freshness}`` through ``_FRESHNESS_LABEL`` (the SAME dict 4.1
    uses; missing → ``""``). Sorted so completed/with-a-makespan runs lead in ascending makespan and any
    FAILED runs sink to the bottom (label tiebreak). ``results == []`` → ``[]``. Pure — takes DTOs + the
    two maps, no ``st``/``services``."""
    makespans = [r.schedule.makespan_hours for r in results
                 if r.schedule is not None and r.schedule.makespan_hours is not None]
    best = min(makespans) if makespans else None
    rows: list[dict] = []
    for r in results:
        sched = r.schedule
        makespan = None if sched is None else sched.makespan_hours
        fitness = (r.diagnostics.fitness.composite
                   if (r.diagnostics is not None and r.diagnostics.fitness is not None) else None)
        delta_vs_best = (makespan - best) if (makespan is not None and best is not None) else None
        rows.append({
            label_key: label_by_run.get(r.run_id, ""),
            "status": r.status.value,
            "makespan_hours": makespan,
            "optimism_gap_hours": None if sched is None else sched.optimism_gap_hours,
            "fitness": fitness,
            "delta_vs_best": delta_vs_best,
            "freshness": _FRESHNESS_LABEL.get(freshness_by_run.get(r.run_id), ""),
        })
    rows.sort(key=lambda row: (row["makespan_hours"] is None,
                               row["makespan_hours"] if row["makespan_hours"] is not None else 0.0,
                               row[label_key]))
    return rows

def _makespan_bar_rows(labeled_results) -> list[dict]:
    """Streamlit-free rows for the overlaid makespan bar chart shared by the three Phase-4
    orchestration views (Compare runs / Augment resources / Sweep) — one dict per labeled run, in the
    given order, ``{label, status, cpm_lower_bound_hours, optimism_gap_hours, makespan_hours,
    is_best}``. ``labeled_results`` is a list of ``(label, RunResult)`` pairs; the caller supplies the
    label (a run/scenario name, a swept value) since it is not recoverable from the run itself. The
    three schedule metrics are read straight off ``r.schedule`` so the bar's CPM-floor + optimism-gap
    segments always sum to its makespan (``cpm_lower_bound_hours + optimism_gap_hours ==
    makespan_hours``); a run with no schedule (FAILED/CANCELLED) yields ``None`` for all three (the
    render helper drops it from the bars). ``is_best`` is ``True`` for every run whose makespan equals
    the smallest makespan among runs that have one (ties → multiple ``True``; all ``False`` when no run
    completed). Pure — takes DTOs, no ``st``/Plotly."""
    makespans = [r.schedule.makespan_hours for _label, r in labeled_results
                 if r.schedule is not None and r.schedule.makespan_hours is not None]
    best = min(makespans) if makespans else None
    rows: list[dict] = []
    for label, r in labeled_results:
        sched = r.schedule
        makespan = None if sched is None else sched.makespan_hours
        rows.append({
            "label": label,
            "status": r.status.value,
            "cpm_lower_bound_hours": None if sched is None else sched.cpm_lower_bound_hours,
            "optimism_gap_hours": None if sched is None else sched.optimism_gap_hours,
            "makespan_hours": makespan,
            "is_best": makespan is not None and best is not None and makespan == best,
        })
    return rows

def _multi_gantt_rows(labeled_results) -> list[dict]:
    """Streamlit-free rows for the aligned multi-run Gantt (Compare runs / Augment resources) — the
    per-activity ``_gantt_rows`` of each run's schedule, each tagged with the caller-supplied
    ``run_label`` and concatenated in the given run order: ``{run_label, task, start, end, duration,
    delay, float_class, on_chain, description}``. ``labeled_results`` is a list of ``(label,
    RunResult)`` pairs. Because every run's time fields are hour-offsets from project start (0), the
    rows share one numeric x-axis with no alignment transform — the render helper facets one subplot
    per run over that shared axis. A run with no schedule (FAILED/CANCELLED) contributes zero rows (the
    render helper names it instead); within a run the rows stay start-sorted (inherited from
    ``_gantt_rows``). Pure — delegates to ``_gantt_rows``, no ``st``/Plotly."""
    rows: list[dict] = []
    for label, r in labeled_results:
        if r.schedule is None:
            continue
        for g in _gantt_rows(r.schedule):
            rows.append({"run_label": label, **g})
    return rows

def _mode_sweep_variants(mode_options, selected_task_ids, base_selections=(), cap=None) -> dict:
    """Streamlit-free enumeration for the Phase-4.3 **mode** sweep axis: the cartesian product of each
    SELECTED multi-mode task's modes, as ready-to-run ``RunConfig.mode_selections`` inputs.

    ``mode_options`` is the ``_mode_options(payload)`` shape — one row per task with >1 mode,
    ``{"task_id", "modes":[{"mode_name","duration"}, ...]}``. ``selected_task_ids`` names which of those
    tasks to VARY (order preserved; an id absent from ``mode_options`` is skipped defensively).
    ``base_selections`` is the current picks (a ``tuple[ModeSelection, ...]``): every UNSELECTED
    multi-mode task keeps its base pick while the swept tasks are overridden per combination, so a
    variant's selection is the FULL merged set — a swept run never silently reverts an unselected task
    to its default mode. Base picks are filtered to tasks still in ``mode_options`` so a stale pick can
    neither ride into a run nor trip ``INVALID_MODE``.

    Returns ``{"variants", "total", "capped", "base_label"}``:
      - ``variants``: ``list[(label, tuple[ModeSelection, ...])]`` — the label names only the swept
        tasks (e.g. ``"C104=crash, C105=fast"``) so it stays short/comparable; the tuple is the full
        merged selection. Empty when capped.
      - ``total``: the product size BEFORE the cap (an empty selection → the empty product → ``1``).
      - ``capped``: ``True`` when ``cap is not None and total > cap`` — the caller then refuses to run
        (``variants`` is empty), never truncates.
      - ``base_label``: the label of the combination equal to the current picks (each swept task at its
        base pick, or its FIRST mode when unset) — always one of the variant labels, so the leaderboard's
        winner banner can compare against it.

    The run-config hash folds selections as ``{task_id: mode_name}``, so each distinct combination is a
    distinct verified run with no new validation surface. Pure — ``itertools`` + the ``ModeSelection``
    domain record only, no ``st``."""
    modes_by_task = {row["task_id"]: [m["mode_name"] for m in row["modes"]] for row in mode_options}
    swept = [t for t in selected_task_ids if t in modes_by_task]
    base = {ms.task_id: ms.mode_name for ms in base_selections if ms.task_id in modes_by_task}

    total = 1
    for t in swept:
        total *= len(modes_by_task[t])
    if cap is not None and total > cap:
        return {"variants": [], "total": total, "capped": True, "base_label": ""}

    def _label(pairs) -> str:
        return ", ".join(f"{t}={m}" for t, m in pairs)

    variants: list = []
    for combo in product(*[modes_by_task[t] for t in swept]):
        pairs = list(zip(swept, combo))
        merged = dict(base)
        merged.update(dict(pairs))
        selection = tuple(ModeSelection(task_id=t, mode_name=m) for t, m in merged.items())
        variants.append((_label(pairs), selection))

    base_label = _label([(t, base.get(t, modes_by_task[t][0])) for t in swept])
    return {"variants": variants, "total": total, "capped": False, "base_label": base_label}

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
    "duration_overrides", "resource_changes", "equipment_changes", "location_changes",
    "hold_point_release_overrides", "emergent_tasks", "emergent_dependencies",
    "task_suppressions", "dependency_suppressions",
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

def _dag_node_color(float_class) -> str:
    """DAG node fill for a float class — the SAME mapping the Gantt uses (``_FLOAT_CLASS_COLORS``,
    keyed by ``FloatClass.value``), so both views agree: red = constrained-chain critical, orange
    = zero-float, green = positive-float. This reproduces the engine ``_node_color`` RULE via the
    shared ``classify_float`` bucket (not a re-derived tolerance); unknown/None falls back to grey."""
    key = float_class.value if float_class is not None else ""
    return _FLOAT_CLASS_COLORS.get(key, _FLOAT_CLASS_COLORS[""])

def _dag_hover(n: dict) -> str:
    """Hover tooltip for one activity-DAG node. A scheduled (enriched) node gets the run overlay:
    CPM ES/LS/slack — labeled "(CPM)" so they read as project-start-axis values, never confused
    with the wall-clock start/end also shown — plus the float class + actual total float, and the
    CPM-critical / constrained flags. A structural node (pre-run, or an un-timed/unscheduled task
    with no DTO) falls back to the duration/depth line. Pure — string formatting only."""
    label = n.get("label", n["id"])
    if not n.get("scheduled"):
        return f"{label}<br>duration: {n.get('duration')}h<br>depth: {n.get('depth')}"

    def _h(v) -> str:
        return "—" if v is None else f"{v:g}h"

    lines = [f"<b>{label}</b>"]
    if n.get("description"):
        lines.append(str(n["description"]))
    lines.append(f"ES (CPM): {_h(n.get('es_hours'))} · LS (CPM): {_h(n.get('ls_hours'))} · "
                 f"slack: {_h(n.get('cpm_slack_hours'))}")
    lines.append(f"start: {_h(n.get('start_hour'))} · end: {_h(n.get('end_hour'))} (wall-clock)")
    lines.append(f"float: {n.get('float_class') or '—'} · "
                 f"TF actual: {_h(n.get('tf_actual_hours'))}")
    flags = [f for f, on in (("CPM-critical", n.get("cpm_critical")),
                             ("Constrained", n.get("on_constrained_chain"))) if on]
    if flags:
        lines.append(" · ".join(flags))
    return "<br>".join(lines)

def _cpm_path_edges(cpm_critical_path) -> list[tuple[str, str]]:
    """Consecutive (predecessor, successor) pairs along the ordered CPM critical path, so the DAG
    can highlight the path the same way it draws ``contention_edges``. A path of fewer than two
    nodes has no edges. Pure — no filtering to known ids (``_activity_graph_enriched`` guards that
    against synthetic CPM START/END nodes the input topology omits)."""
    path = tuple(cpm_critical_path)
    return [(path[i], path[i + 1]) for i in range(len(path) - 1)]

def _cpm_path_label(cpm_critical_path) -> str:
    """The ordered CPM (logical) critical path as a readable ``A → B → C`` string for the results
    header; an empty path yields ``""`` (the caller then shows nothing). Pure — string join only."""
    return " → ".join(cpm_critical_path)

def _activity_graph_enriched(raw_tree, schedule, layer_by: str = "topo") -> dict:
    """The activity DAG ENRICHED with a selected run's analytics: the same structural nodes/edges
    as ``_activity_graph_data`` (built from the current INPUT schedule), overlaid — keyed by
    task_id — with the run's chain-coloring, CPM ES/LS/slack, CPM-critical / constrained flags,
    and the resource-contention arcs. ``layer_by`` chooses the x axis: ``schedule`` (the default —
    actual start hour, y = float-class bands), ``topo`` (longest-path depth, the base layout), or
    ``es`` / ``ls`` (CPM time, project start = 0), each falling back to depth for a node with no
    value. ``schedule`` additionally remaps its distinct start hours to EVENLY-SPACED ORDINAL
    columns (so proportional-time clustering can't overlap nodes); each node then carries
    ``x_value`` (its true start hour) and the result carries ``x_ticks`` (``{vals, text}`` mapping
    ordinal columns back to hours, thinned for a wide axis). Every mode returns ``x_columns`` (the
    sorted distinct x values, post-remap) so the caller can window a wide DAG and arm a range-slider.
    Two asymmetries are tolerated without raising: the DTO carries synthetic START/END the input
    topology omits (ignored — no node), and an un-timed task has no DTO (stays structural/grey,
    ``scheduled=False``). Contention arcs are filtered to pairs whose endpoints are both nodes (same
    guard as ``_activity_graph_data``). Pure — stdlib only."""
    base = _activity_graph_data(raw_tree)
    by_task = {a.task_id: a for a in schedule.activities}
    cpm = set(schedule.cpm_critical_path)
    id_set = {n["id"] for n in base["nodes"]}

    nodes: list[dict] = []
    for n in base["nodes"]:
        node = dict(n)                       # copy structural fields (id/label/duration/depth/x/y)
        a = by_task.get(n["id"])
        if a is not None:
            node.update(
                scheduled=True,
                color=_dag_node_color(a.float_class),
                float_class=a.float_class.value,
                es_hours=a.es_hours, ls_hours=a.ls_hours, cpm_slack_hours=a.cpm_slack_hours,
                cpm_critical=n["id"] in cpm,
                on_constrained_chain=a.on_constrained_chain,
                tf_actual_hours=a.tf_actual_hours,
                start_hour=a.start_hour, end_hour=a.end_hour,
                description=a.description,
            )
            if layer_by == "schedule" and a.start_hour is not None:
                node["x"] = float(a.start_hour)          # actual scheduled start (wall-clock hours)
            elif layer_by == "es" and a.es_hours is not None:
                node["x"] = float(a.es_hours)
            elif layer_by == "ls" and a.ls_hours is not None:
                node["x"] = float(a.ls_hours)
            # else: keep the base longest-path depth as x
            #   (schedule also falls back to depth for a node with no start_hour —
            #    an un-scheduled task keeps its structural depth column.)
        else:
            node.update(
                scheduled=False, color=_FLOAT_CLASS_COLORS[""], float_class=None,
                es_hours=None, ls_hours=None, cpm_slack_hours=None, cpm_critical=False,
                on_constrained_chain=False, tf_actual_hours=None,
                start_hour=None, end_hour=None, description=None,
            )
        nodes.append(node)

    # Re-lane the y-axis. The base ``_activity_graph_data`` places y by document order within a
    # depth layer — arbitrary relative to the run — so nodes pile up and the gold CPM line jumps
    # lane-to-lane. Two enriched layouts fix that, chosen by ``layer_by``:
    #
    # *schedule* (the default) reads x as the ACTUAL START HOUR, which frees y to encode the run's
    # FLOAT CLASS — the node's colour — as horizontal BANDS: critical on the centre spine (y=0),
    # zero-float and positive-float stacked above it, grey (unscheduled / unclassified) below.
    # Within one (band, start-hour) group the highest-priority node anchors the band centre and any
    # co-starting peers fan outward (+1, -1, +2 …); band spacing is sized from the busiest group so
    # a fan never reaches into a neighbouring band. Net effect: real time on x, criticality on y —
    # the left-hand pile of equal-depth / equal-ES nodes the other layouts show spreads along the
    # true start times, and same-class tasks read as a row. The CPM path still lands on the y=0
    # spine because its activities start at strictly-increasing hours (zero-float, back-to-back),
    # so each holds its own start-hour column in the critical band and wins the centre.
    #
    # every OTHER mode (topo / es / ls) keeps the pure SPINE layout: within each x-COLUMN (a depth,
    # or an ES/LS value) the highest-priority node (CPM-critical, then constrained-chain, then the
    # rest) is pinned to the centre and the others fan outward. The CPM path advances through
    # strictly-increasing x there too, so it reads as a straight horizontal spine at y=0 and
    # constrained-chain nodes sit nearest it.
    #
    # Only y changes; x still encodes the chosen axis. Both layouts need the run's CPM/chain flags,
    # which exist only in this enriched path — the structural pre-run graph keeps its doc-order lanes.
    def _lane_priority(n: dict) -> int:
        if n.get("cpm_critical"):
            return 0
        if n.get("on_constrained_chain"):
            return 1
        return 2

    def _fan(k: int) -> float:                        # 0, +1, -1, +2, -2, … around a centre
        return 0.0 if k == 0 else (float((k + 1) // 2) if k % 2 else -float(k // 2))

    x_ticks = None                                   # schedule mode fills this (ordinal → true hour)
    if layer_by == "schedule":
        # --- non-uniform (ORDINAL) time axis --------------------------------------------------
        # x is the ACTUAL start hour, but PROPORTIONAL time crams distinct-yet-close starts within a
        # marker's width wherever they bunch up (measured: 100% of node overlaps are horizontal
        # time-clustering, worst on the big plans). Remap the distinct start hours to EVENLY-SPACED
        # column indices so no two columns can collapse onto each other; the true hour rides on
        # ``x_value`` (hover) and on the axis TICK LABELS the caller builds from ``x_ticks``. Order
        # is preserved — earlier starts stay left — so the timeline reading survives; only the
        # visual gap between columns becomes uniform instead of proportional. (The Earliest / Latest
        # start layouts keep proportional CPM time, for when true distances matter.)
        distinct = sorted({round(float(n["x"]), 6) for n in nodes})
        ordinal = {v: i for i, v in enumerate(distinct)}
        for node in nodes:
            node["x_value"] = round(float(node["x"]), 6)   # true start hour (or depth fallback)
            node["x"] = float(ordinal[node["x_value"]])     # even-spaced column index
        # Tick labels: map ordinal columns back to their true hour, thinned to ≤ ~18 labels so a
        # wide axis stays readable (Plotly rotates what remains). Caller applies these in schedule.
        stride = max(1, (len(distinct) + 17) // 18)
        x_ticks = {"vals": [float(i) for i in range(0, len(distinct), stride)],
                   "text": [f"{distinct[i]:g}" for i in range(0, len(distinct), stride)]}

        # Float class → band index (a multiple of ``spacing``). critical anchors the centre spine
        # (0); the scheduled-but-slack classes stack above; grey / unscheduled sits below.
        _BAND_INDEX = {"critical": 0, "zero_float": 1, "positive_float": 2}

        def _band(n: dict) -> int:
            return _BAND_INDEX.get(n.get("float_class") or "", -1)   # grey / None / unknown → -1

        # Group by (band, start-hour column) so co-starting same-class nodes fan within their band.
        groups: dict[tuple[int, float], list[dict]] = {}
        for node in nodes:
            groups.setdefault((_band(node), round(float(node["x"]), 6)), []).append(node)
        # Spacing wide enough that the busiest fan (max |offset| ≈ half its size) never reaches the
        # neighbouring band centre: size + 1 leaves ≥ 1 unit of clear air between adjacent bands.
        max_in_group = max((len(g) for g in groups.values()), default=1)
        spacing = float(max_in_group) + 1.0
        for (band, _x), group in groups.items():
            centre = band * spacing
            for k, node in enumerate(sorted(group, key=_lane_priority)):  # stable → doc order in tie
                node["y"] = centre + _fan(k)
    else:
        by_col: dict[float, list[dict]] = {}
        for node in nodes:
            by_col.setdefault(round(float(node["x"]), 6), []).append(node)
        for column in by_col.values():
            for k, node in enumerate(sorted(column, key=_lane_priority)):  # stable → doc order in tie
                node["y"] = _fan(k)

    contention = [(p, s) for p, s in schedule.contention_edges
                  if p in id_set and s in id_set]
    # CPM (logical) critical path as edge pairs, filtered to known nodes so synthetic CPM
    # START/END ids the input topology omits drop out cleanly (same guard as contention).
    cpm_path = [(p, s) for p, s in _cpm_path_edges(schedule.cpm_critical_path)
                if p in id_set and s in id_set]
    # Distinct x-columns (post-remap) — the caller windows a wide DAG and arms the range-slider off
    # this count, mode-agnostically (ordinal columns for schedule, real CPM-time values for es/ls).
    x_columns = sorted({round(float(n["x"]), 6) for n in nodes})
    return {"nodes": nodes, "edges": base["edges"], "contention_edges": contention,
            "cpm_path_edges": cpm_path, "has_cycle": base["has_cycle"],
            "enriched": True, "layer_by": layer_by, "x_ticks": x_ticks, "x_columns": x_columns}

# =============================================================================
# per-task inspector builders (streamlit-free): the "why isn't this task
# starting sooner?" decomposition, fusing a run's DTOs with the plan's edges
# =============================================================================
# All three are pure (stdlib + domain DTOs only, no ``st``) and derive the
# inspector entirely from data already on the output DTOs — no engine call. The
# authoritative NAMED binding resource / delaying predecessor is computed by the
# engine but not surfaced today; ``_saturated_skills`` is a clearly-labeled
# aggregate stand-in, not that per-task attribution (a documented Tier-B follow-up).

def _task_slip(activity) -> dict:
    """Decompose one scheduled task's lateness against its CPM early start into the two
    buckets the DTOs let us separate. Total lateness ``start_hour - es_hours`` splits into
    ``contention_delay`` (``delay_hours`` — the engine's eligible-but-waiting-on-resources
    portion, measured AFTER precedence/window/calendar gates cleared) and ``other_gating``
    (the remainder — precedence / time-window / shift-calendar gating), clamped at 0 so
    calendar-rounding never yields a spurious negative. ``lateness_vs_es`` / ``other_gating``
    are ``None`` when the task has no CPM early start (``es_hours is None``); ``contention_delay``
    is always the raw ``delay_hours``. Pure — arithmetic only."""
    delay = activity.delay_hours
    if activity.es_hours is None:
        return {"lateness_vs_es": None, "contention_delay": delay, "other_gating": None}
    lateness = activity.start_hour - activity.es_hours
    return {"lateness_vs_es": lateness, "contention_delay": delay,
            "other_gating": max(0.0, lateness - delay)}

def _task_neighbors(raw_tree, schedule, task_id: str) -> dict:
    """Predecessor/successor slack-attribution rows for one task, fusing the plan's precedence
    edges (``_dependency_options`` — both successor schema forms) with each neighbor's timing/
    float from the run's ``ScheduledActivityDTO``s (keyed by task_id). Returns
    ``{"predecessors": [...], "successors": [...]}``. A predecessor row carries its ``lag_hours``,
    ``end_hour``, the finish-to-start ready time ``fs_ready = end_hour + lag_hours``, CPM slack,
    actual TF, float class, and ``is_driver`` — the flag on the predecessor(s) whose ``fs_ready``
    is the maximum (the latest finish that gated this task's eligibility). A successor row carries
    its ``lag_hours``, ``start_hour``, CPM slack, actual TF, and float class. A neighbor with no
    matching DTO (un-timed) is still listed, with ``None`` timing and ``is_driver=False``; preds
    with an unknown finish never win the driver flag (``fs_ready is None``). Pure — no ``st``."""
    by_task = {a.task_id: a for a in schedule.activities}
    deps = _dependency_options(raw_tree)

    def _float(a):
        return a.float_class.value if (a is not None and a.float_class is not None) else None

    preds: list[dict] = []
    for d in deps:
        if d["successor"] != task_id:
            continue
        a = by_task.get(d["predecessor"])
        end = a.end_hour if a is not None else None
        preds.append({
            "task_id": d["predecessor"], "lag_hours": d["lag_hours"], "end_hour": end,
            "fs_ready": (end + d["lag_hours"]) if end is not None else None,
            "cpm_slack_hours": a.cpm_slack_hours if a is not None else None,
            "tf_actual_hours": a.tf_actual_hours if a is not None else None,
            "float_class": _float(a), "is_driver": False,
        })
    ready = [p["fs_ready"] for p in preds if p["fs_ready"] is not None]
    if ready:
        driving = max(ready)
        for p in preds:
            if p["fs_ready"] == driving:      # flag every predecessor tied at the latest finish
                p["is_driver"] = True

    succs: list[dict] = []
    for d in deps:
        if d["predecessor"] != task_id:
            continue
        a = by_task.get(d["successor"])
        succs.append({
            "task_id": d["successor"], "lag_hours": d["lag_hours"],
            "start_hour": a.start_hour if a is not None else None,
            "cpm_slack_hours": a.cpm_slack_hours if a is not None else None,
            "tf_actual_hours": a.tf_actual_hours if a is not None else None,
            "float_class": _float(a),
        })
    return {"predecessors": preds, "successors": succs}

def _saturated_skills(util, lo: float, hi: float) -> list[str]:
    """The sorted, unique skill pools whose demand met or exceeded capacity at any point over the
    half-open window ``[lo, hi)`` — a HEURISTIC, AGGREGATE resource-pressure readout (which pools
    were tight while a task waited), NOT the authoritative per-task binding resource. Reads a
    ``ResourceUtilizationDTO``; a skill counts when one of its intervals overlaps ``[lo, hi)`` with
    ``demand >= available``. Returns ``[]`` when ``util is None`` or nothing overlaps/saturates.
    Pure — no ``st``."""
    if util is None:
        return []
    saturated: set[str] = set()
    for series in util.series:
        for iv in series.intervals:
            if iv.start_hour < hi and iv.end_hour > lo and iv.demand >= iv.available:
                saturated.add(series.skill_type)
    return sorted(saturated)

def _chain_sets(schedule) -> dict:
    """Overlap of a single run's three criticality sets, computed from the schedule DTO alone.

    The three sets:
      - cpm         : ``ScheduleDTO.cpm_critical_path`` — the LOGICAL critical path (resources ignored)
      - constrained : ``ScheduleDTO.constrained_chain`` — the RESOURCE-constrained chain (the red chain)
      - zero_tf     : activities with ``float_class`` in ``{CRITICAL, ZERO_FLOAT}`` — no scheduling slack
                      (by construction constrained ⊆ zero_tf, since every chain task is CRITICAL)

    The headline partition is CPM vs constrained (the engine's ``print_chain_sets_summary`` semantics):
      - ``both``             : critical for BOTH reasons (precedence AND resources)
      - ``only_cpm``         : precedence-critical, but resources do not gate them
      - ``only_constrained`` : on the constrained chain but NOT the CPM path — critical because of
                               RESOURCE contention, not precedence logic. THE leverage points.

    Returns ``{"cpm", "constrained", "zero_tf": tuple[str], "n_cpm", "n_constrained", "n_zero_tf": int,
    "only_cpm", "only_constrained", "both": tuple[str] (sorted), "rows": [{task_id, on_cpm,
    on_constrained, zero_tf, float_class, tf_actual_hours}, ...] (over the union, sorted by task_id),
    "all_rows": same row shape over EVERY scheduled activity (sorted) — the superset the view's
    "All activities" filter draws on (tasks on no set carry three False flags)}``.
    ``cpm``/``constrained`` keep the DTO's order; the partitions and ``zero_tf`` are sorted for stable
    display. Pure — set arithmetic + ``FloatClass`` only, no ``st``."""
    cpm_set = set(schedule.cpm_critical_path)
    constrained_set = set(schedule.constrained_chain)
    zero_tf = {a.task_id for a in schedule.activities
               if a.float_class in (FloatClass.CRITICAL, FloatClass.ZERO_FLOAT)}
    by_task = {a.task_id: a for a in schedule.activities}
    def _row(tid: str) -> dict:
        a = by_task.get(tid)
        return {
            "task_id": tid,
            "on_cpm": tid in cpm_set,
            "on_constrained": tid in constrained_set,
            "zero_tf": tid in zero_tf,
            "float_class": a.float_class.value if (a is not None and a.float_class is not None) else None,
            "tf_actual_hours": a.tf_actual_hours if a is not None else None,
        }
    # ``rows`` covers only the criticality UNION (back-compat); ``all_rows`` is every scheduled
    # activity (sorted), so the view's "All activities" filter can surface tasks on NO set — the
    # positive-float, off-both-chains tasks that ``rows`` deliberately omits.
    rows = [_row(tid) for tid in sorted(cpm_set | constrained_set | zero_tf)]
    all_rows = [_row(a.task_id) for a in sorted(schedule.activities, key=lambda a: a.task_id)]
    return {
        "cpm": tuple(schedule.cpm_critical_path),
        "constrained": tuple(schedule.constrained_chain),
        "zero_tf": tuple(sorted(zero_tf)),
        "n_cpm": len(cpm_set),
        "n_constrained": len(constrained_set),
        "n_zero_tf": len(zero_tf),
        "only_cpm": tuple(sorted(cpm_set - constrained_set)),
        "only_constrained": tuple(sorted(constrained_set - cpm_set)),
        "both": tuple(sorted(cpm_set & constrained_set)),
        "rows": rows,
        "all_rows": all_rows,
    }

# Regulatory time-window grace -- mirrors schedule_validator._PREC_TOL (timedelta(milliseconds=1))
# so this pre-flight agrees with the engine's post-run _check_time_windows verdict.
_WINDOW_TOL_HOURS = 1e-3 / 3600.0    # 1 ms in hours ~= 2.7778e-7

def _window_preflight(raw_tree, schedule) -> dict:
    """Per-task regulatory time-window compliance for one run, fusing each task's AUTHORED windows
    (payload ``time_windows`` rows ``{earliest, latest}``, hours from outage start) with the run's
    scheduled ``start_hour``/``end_hour`` (``ScheduledActivityDTO``). Replicates the engine's rule
    (``schedule_validator._check_time_windows``): an activity is compliant iff it fits in AT LEAST ONE
    window (``start >= earliest - tol`` AND ``end <= latest + tol``), OR across the task's window list,
    with a ``_WINDOW_TOL_HOURS`` quantization grace -- so the verdict agrees with the post-run audit, but
    is computed directly from the schedule (always available, structured per task; the DTOs otherwise
    carry only an aggregate count). Only windowed tasks appear in ``rows`` (unconstrained tasks are
    excluded and not counted). For each windowed activity the CLOSEST window (min total miss) is reported
    with its ``start_short`` (hours the start is before ``earliest``) and ``end_over`` (hours the end is
    past ``latest``), each 0.0 when that edge is satisfied.

    Returns ``{"n_windowed": int, "n_violations": int, "violations": tuple[str] (sorted),
    "tol_hours": float, "rows": [{task_id, start_hour, end_hour, fits, n_windows, best_window,
    best_earliest, best_latest, start_short, end_over, windows}, ...]}`` -- rows are windowed tasks,
    violations first then by task_id. Reads only the multi-window list form (the GUI's domain); the
    engine's legacy scalar window fields are not represented in the GUI plan. Pure -- arithmetic only,
    no ``st``."""
    tol = _WINDOW_TOL_HOURS

    def _windows_for(task) -> list[tuple[float, float]]:
        wins: list[tuple[float, float]] = []
        for w in (task.get("time_windows") or []):
            try:
                lo, hi = float(w["earliest"]), float(w["latest"])
            except (KeyError, TypeError, ValueError):
                continue                      # skip a mid-draft/raw-editor window with bad bounds
            if math.isfinite(lo) and math.isfinite(hi):
                wins.append((lo, hi))
        return wins

    windows_by_task = {
        t.get("task_id"): _windows_for(t) for t in (raw_tree.get("tasks") or [])
    }

    rows: list[dict] = []
    for a in schedule.activities:
        wins = windows_by_task.get(a.task_id) or []
        if not wins:
            continue                          # unconstrained -- not pre-flighted, not counted
        # Per window: the RAW distance outside it (start_short/end_over, reported un-fudged like the
        # engine's audit detail) and whether it fits within the tolerance grace (the fit DECISION only).
        misses = []                           # (not fits_win, total_raw_miss, start_short, end_over, lo, hi)
        for lo, hi in wins:
            start_short = max(0.0, lo - a.start_hour)
            end_over = max(0.0, a.end_hour - hi)
            fits_win = a.start_hour >= lo - tol and a.end_hour <= hi + tol
            misses.append((not fits_win, start_short + end_over, start_short, end_over, lo, hi))
        best = min(misses, key=lambda m: (m[0], m[1]))   # a fitting window first, else the closest one
        fits = not best[0]
        rows.append({
            "task_id": a.task_id, "start_hour": a.start_hour, "end_hour": a.end_hour,
            "fits": fits, "n_windows": len(wins),
            "best_window": misses.index(best), "best_earliest": best[4], "best_latest": best[5],
            "start_short": best[2], "end_over": best[3],
            "windows": ", ".join(f"[{lo:g}h-{hi:g}h]" for lo, hi in wins),
        })

    rows.sort(key=lambda r: (r["fits"], r["task_id"]))     # violations first, then stable by id
    violations = tuple(sorted(r["task_id"] for r in rows if not r["fits"]))
    return {
        "n_windowed": len(rows),
        "n_violations": len(violations),
        "violations": violations,
        "tol_hours": tol,
        "rows": rows,
    }

def _graph_layout(fig, height: int, x_title: str | None = None, x_ticks: dict | None = None,
                  x_range=None, rangeslider: bool = False) -> None:
    """Common styling for a network-scatter figure: hidden axes, tight margins, no legend —
    so the nodes/edges read as a graph, not a chart. Mutates ``fig`` in place.

    ``x_title`` opts the x-axis back IN as a real measured scale (ticks + gridlines + this title):
    the *schedule* DAG layout puts (evenly-spaced) start hours on x, so that axis is meaningful and
    worth showing. ``x_ticks`` (``{"vals", "text"}``) labels that ordinal axis with the true hours.
    ``x_range`` sets the INITIAL visible x-window and ``rangeslider`` adds Plotly's overview/pan bar
    beneath the plot — together they let a long DAG be read in portions (drag the bar to pan/zoom)
    while the bar shows the whole extent for context; the caller sizes ``height`` to leave room for
    it. The y-axis stays hidden throughout — it encodes float-class bands / fan lanes, not a numeric
    quantity — so bottom margin grows only when an x-title is drawn."""
    fig.update_layout(
        height=height, showlegend=False,
        margin=dict(l=10, r=10, t=10, b=(40 if x_title else 10)),
        hovermode="closest", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    hidden = dict(showgrid=False, zeroline=False, showticklabels=False)
    if x_title:
        xcfg = dict(showgrid=True, gridcolor="rgba(0,0,0,0.06)", zeroline=False,
                    showticklabels=True, title_text=x_title)
        if x_ticks:
            xcfg.update(tickmode="array", tickvals=x_ticks["vals"], ticktext=x_ticks["text"])
        fig.update_xaxes(**xcfg)
    else:
        fig.update_xaxes(**hidden)
    if x_range is not None:
        fig.update_xaxes(range=list(x_range))            # initial window; slider spans the full data
    if rangeslider:
        fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.10,
                                          bgcolor="rgba(0,0,0,0.03)"))
    fig.update_yaxes(**hidden)
