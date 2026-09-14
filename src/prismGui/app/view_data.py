"""Pure view-data builders: table/CSV rows, label maps, and graph-data shapers."""
from __future__ import annotations

import csv
import io
import math
from typing import Optional

from prismGui.domain.results import Freshness
from prismGui.domain.run_config import EvaluationWeights
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

def _sweep_rows(results, rule_by_run, freshness_by_run) -> list[dict]:
    """Leaderboard rows for a priority-rule sweep (Phase-4.3) — one dict per swept run, ranked
    shortest-makespan-first, ``{priority_rule, status, makespan_hours, optimism_gap_hours, fitness,
    delta_vs_best, freshness}``. Each swept run is a plain BASELINE run whose only difference is its
    ``priority_rule`` — but the rule is NOT recoverable from the stored ``RunResult`` (provenance keeps
    only the one-way ``run_config_hash``), so the caller tracks ``{run_id: rule}`` at sweep time and
    passes it as ``rule_by_run``; a run absent from that map labels ``""``. ``delta_vs_best`` is
    ``makespan_hours - best`` where ``best`` is the smallest makespan among runs that have one (``0`` for
    the winner, ``None`` when this run — or every run — has no makespan). ``fitness`` is the composite
    when a ``FitnessDTO`` is present else ``None``; ``freshness`` maps the supplied ``{run_id: Freshness}``
    through ``_FRESHNESS_LABEL`` (the SAME dict 4.1 uses; missing → ``""``). Sorted so completed/with-a-
    makespan runs lead in ascending makespan and any FAILED runs sink to the bottom (rule-name tiebreak).
    ``results == []`` → ``[]``. Pure — takes DTOs + the two maps, no ``st``/``services``."""
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
            "priority_rule": rule_by_run.get(r.run_id, ""),
            "status": r.status.value,
            "makespan_hours": makespan,
            "optimism_gap_hours": None if sched is None else sched.optimism_gap_hours,
            "fitness": fitness,
            "delta_vs_best": delta_vs_best,
            "freshness": _FRESHNESS_LABEL.get(freshness_by_run.get(r.run_id), ""),
        })
    rows.sort(key=lambda row: (row["makespan_hours"] is None,
                               row["makespan_hours"] if row["makespan_hours"] is not None else 0.0,
                               row["priority_rule"]))
    return rows

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
    and the resource-contention arcs. ``layer_by`` chooses the x axis: ``topo`` (longest-path
    depth, the base layout), ``es`` or ``ls`` (CPM time, project start = 0), each falling back to
    depth for a node with no CPM value. Two asymmetries are tolerated without raising: the DTO
    carries synthetic START/END the input topology omits (ignored — no node), and an un-timed task
    has no DTO (stays structural/grey, ``scheduled=False``). Contention arcs are filtered to pairs
    whose endpoints are both nodes (same guard as ``_activity_graph_data``). Pure — stdlib only."""
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
            if layer_by == "es" and a.es_hours is not None:
                node["x"] = float(a.es_hours)
            elif layer_by == "ls" and a.ls_hours is not None:
                node["x"] = float(a.ls_hours)
            # else: keep the base longest-path depth as x
        else:
            node.update(
                scheduled=False, color=_FLOAT_CLASS_COLORS[""], float_class=None,
                es_hours=None, ls_hours=None, cpm_slack_hours=None, cpm_critical=False,
                on_constrained_chain=False, tf_actual_hours=None,
                start_hour=None, end_hour=None, description=None,
            )
        nodes.append(node)

    contention = [(p, s) for p, s in schedule.contention_edges
                  if p in id_set and s in id_set]
    # CPM (logical) critical path as edge pairs, filtered to known nodes so synthetic CPM
    # START/END ids the input topology omits drop out cleanly (same guard as contention).
    cpm_path = [(p, s) for p, s in _cpm_path_edges(schedule.cpm_critical_path)
                if p in id_set and s in id_set]
    return {"nodes": nodes, "edges": base["edges"], "contention_edges": contention,
            "cpm_path_edges": cpm_path, "has_cycle": base["has_cycle"],
            "enriched": True, "layer_by": layer_by}

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

def _graph_layout(fig, height: int) -> None:
    """Common styling for a network-scatter figure: hidden axes, tight margins, no legend —
    so the nodes/edges read as a graph, not a chart. Mutates ``fig`` in place."""
    fig.update_layout(
        height=height, showlegend=False, margin=dict(l=10, r=10, t=10, b=10),
        hovermode="closest", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    axis = dict(showgrid=False, zeroline=False, showticklabels=False)
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
