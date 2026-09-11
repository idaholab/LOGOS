"""Pure view-data builders: table/CSV rows, label maps, and graph-data shapers."""
from __future__ import annotations

import csv
import io
import math
from typing import Optional

from prismGui.domain.results import Freshness
from prismGui.domain.scenario import Scenario
from prismGui.app.edit_model import _dependency_options, _task_options


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
    return {"nodes": nodes, "edges": base["edges"], "contention_edges": contention,
            "has_cycle": base["has_cycle"], "enriched": True, "layer_by": layer_by}

def _graph_layout(fig, height: int) -> None:
    """Common styling for a network-scatter figure: hidden axes, tight margins, no legend —
    so the nodes/edges read as a graph, not a chart. Mutates ``fig`` in place."""
    fig.update_layout(
        height=height, showlegend=False, margin=dict(l=10, r=10, t=10, b=10),
        hovermode="closest", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    axis = dict(showgrid=False, zeroline=False, showticklabels=False)
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
