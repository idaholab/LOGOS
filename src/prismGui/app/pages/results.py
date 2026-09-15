"""Results page: run header, plots, schedule table/export, activity graph."""
from __future__ import annotations

from dataclasses import replace

from prismGui.app._streamlit import st
from prismGui.application import services
from prismGui.domain.results import DispositionOverall, Freshness, RunResultStatus
from prismGui.domain.run_config import PRIORITY_RULES, SGSVariant
from prismGui.app.components import _render_issues
from prismGui.app.edit_model import _mode_options
from prismGui.app.scenario_model import _add_resource_change, _current_schedule_payload, _mint_scenario
from prismGui.app.view_data import _FLOAT_CLASS_COLORS, _FRESHNESS_LABEL, _FRESHNESS_REASON_LABEL, _activity_graph_data, _activity_graph_enriched, _augmentation_candidates, _augmentation_delta, _chain_sets, _comparison_rows, _cpm_path_label, _dag_hover, _disposition_rows, _gantt_rows, _graph_layout, _makespan_bar_rows, _mode_sweep_variants, _multi_gantt_rows, _provenance_rows, _resource_util_rows, _saturated_skills, _scenario_hash_labels, _schedule_csv, _step_series, _sweep_rows, _task_neighbors, _task_slip, _window_preflight


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
        # Per-band PIXEL budget → row-height fractions + a matching figure height, so every skill
        # row keeps a readable, fixed band. The old ``[0.5, 0.5/n, …]`` split divided a fixed height
        # N ways, collapsing the lower skill rows to a few px each on resource-rich plans (example_30
        # has 11 skills) — the bottom row then vanished under the range slider. The Gantt gets a
        # taller band; skill rows scroll within the page when there are many.
        gantt_px, skill_px = 300, 120
        px = [gantt_px, *([skill_px] * len(skills))] if skills else [gantt_px]
        row_heights = [h / sum(px) for h in px]
        gap = min(0.03, 0.3 / max(n_rows - 1, 1))     # keep inter-row gaps from eating the rows
        fig = make_subplots(
            rows=n_rows, cols=1, shared_xaxes=True, vertical_spacing=gap,
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

        # Float-class color legend (the Gantt bars themselves carry no legend — one swatch per class
        # actually present, in severity order, as zero-data marker traces so they add no geometry).
        for fc, label in (("critical", "Critical — on constrained chain"),
                          ("zero_float", "Zero float"), ("positive_float", "Positive float"),
                          ("", "Unclassified")):
            if any((r["float_class"] or "") == fc for r in g_rows):
                fig.add_trace(
                    go.Scatter(x=[None], y=[None], mode="markers", name=label,
                               marker=dict(size=11, symbol="square",
                                           color=_FLOAT_CLASS_COLORS.get(fc, "#7f8c8d")),
                               showlegend=True, hoverinfo="skip"),
                    row=1, col=1)

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

        # No range slider: pinned to the bottom x-axis, it swallowed the last (thin) skill row's
        # y-axis on resource-rich plans. Native drag-to-zoom on the shared x-axis covers long horizons.
        fig.update_xaxes(title_text="hours since project start", row=n_rows, col=1)
        fig.update_layout(
            height=int(sum(px) * 1.15) + 40, bargap=0.2,
            margin=dict(l=10, r=10, t=40, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Bar color = float class; faded bars are off the resource-constrained chain.")

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

def _render_schedule_export(schedule) -> None:
    st.download_button(
        "Download schedule (CSV)",
        data=_schedule_csv(schedule),
        file_name="prism_schedule.csv",
        mime="text/csv",
    )

def _render_disposition_badge(disposition) -> None:
    overall = disposition.overall
    if overall is DispositionOverall.READY:
        st.success("✅ READY")
    elif overall is DispositionOverall.READY_WITH_WARNINGS:
        st.warning("⚠️ READY — with warnings")
    else:
        st.error("⛔ BLOCKED")

def _render_dependency_violations(diagnostics) -> None:
    """The dependency-feasibility verdict as a DISTINCT output (not the generic audit list): the
    engine's ``check_dependency_violations()`` result rides ``DiagnosticsDTO.dependency_violations``.
    A clean run states so explicitly (that is the point of a distinct check); violations get a red
    headline + the focused issue list in an open expander. (These issues also appear, undifferentiated,
    in the header's full 'Audit findings' expander — this section is the feasibility-focused view.)"""
    if diagnostics is None:
        return
    violations = diagnostics.dependency_violations
    if not violations:
        st.success("✅ No dependency violations.")
        return
    st.error(f"⛔ {len(violations)} dependency violation(s)")
    with st.expander("Dependency violations", expanded=True):
        _render_issues(violations)

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

    # CPM (logical) critical path — the PATH behind the lower-bound number, distinct from the
    # resource-constrained chain the DAG/Gantt color red. Shown for any completed run.
    if s.cpm_critical_path:
        st.caption(f"Critical path (CPM): {_cpm_path_label(s.cpm_critical_path)}")

    # Dependency feasibility as its own verdict (distinct from the generic audit findings below).
    _render_dependency_violations(result.diagnostics)

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
        # Echo the custom weights that produced this composite — only when the selected run is
        # CURRENT (so run_config's weights ARE the run-time weights) and non-default (else no info;
        # a stale/different-config run is already flagged by the freshness badge above).
        w = run_config.evaluation_weights
        if w is not None and freshness is Freshness.CURRENT:
            st.caption(
                f"weights: makespan α={w.alpha:g} · delay β={w.beta:g} · "
                f"criticality γ={w.gamma:g} · window δ={w.delta:g}")

def _render_activity_graph(session, baseline, result=None) -> None:
    """The current schedule's activity/dependency DAG (Plotly network scatter): tasks as nodes,
    precedence links as edges, fed by the MATERIALIZED current schedule (a scenario's duration
    override / emergent task is reflected). When ``result`` is a COMPLETED run whose schedule
    matches the CURRENT lineage, the graph is ENRICHED — nodes colored by float class, a "Layer
    by" control lays them out (default **schedule**: x = actual start hour, y = float-class bands;
    or dependency depth / CPM ES / LS), and the run's contention arcs ride the tooltip/overlay;
    otherwise it degrades to the structural pre-run graph. Labels are dropped on large plans (kept
    hover-only) so the picture stays legible."""
    import plotly.graph_objects as go

    scenario = session.get_scenario()
    payload, warning = _current_schedule_payload(baseline, scenario)
    if warning:
        st.warning(warning)

    # Enrich only when a COMPLETED run's schedule matches the CURRENT lineage. run_config is
    # omitted from the compare (lineage only), so a config-only difference still enriches; only
    # a baseline/scenario change (STALE) suppresses. ``not warning`` covers the materialization
    # fallback, where the shown topology is the baseline rather than the run's scenario.
    enrich = (
        result is not None
        and result.status is RunResultStatus.COMPLETED
        and result.schedule is not None
        and not warning
        and services.current_freshness(
            result, baseline=baseline, scenario=scenario) is Freshness.CURRENT
    )

    layer_by = "topo"
    if enrich:
        # Default to the SCHEDULE layout: x = each task's ACTUAL start hour, y = its FLOAT CLASS as
        # horizontal bands (critical spine at 0, slack classes above, grey below). This spreads the
        # left-hand pile of equal-depth / equal-ES tasks along real start times and groups same-color
        # tasks into rows — the layout the review asked for. The three CPM/topology layouts stay on
        # offer: EARLIEST / LATEST start (CPM timeline — time monotonic along the critical path, so
        # the gold spine is straight) and DEPENDENCY DEPTH (structural; can double back when a
        # critical activity has no plan predecessors, e.g. example_30's T007 via a non-precedence
        # critical link — offered, never the default).
        layer_by = st.selectbox(
            "Layer by", ["schedule", "es", "topo", "ls"], key="prism_dag_layer_by",
            format_func=lambda m: {"schedule": "Schedule (start × float class)",
                                   "topo": "Dependency depth", "es": "Earliest start (CPM)",
                                   "ls": "Latest start (CPM)"}[m])
        data = _activity_graph_enriched(payload, result.schedule, layer_by=layer_by)
    else:
        data = _activity_graph_data(payload)
        st.caption("Structural (pre-run) graph — no completed run selected for the current "
                   "schedule (or the selected run is stale).")

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
    # Resource-contention overlay: the arcs the constrained schedule ADDED beyond plan precedence,
    # drawn dashed-red beneath the nodes (endpoints already filtered to known ids by the builder).
    cx: list = []
    cy: list = []
    for src, dst in data.get("contention_edges", []):
        cx += [by_id[src]["x"], by_id[dst]["x"], None]
        cy += [by_id[src]["y"], by_id[dst]["y"], None]
    if cx:
        fig.add_trace(go.Scatter(x=cx, y=cy, mode="lines", hoverinfo="skip",
                                 line=dict(color="#d62728", width=1, dash="dash")))
    # CPM (logical) critical-path overlay: the path behind the lower-bound number, drawn solid gold
    # beneath the nodes. Distinct from the red constrained-chain node coloring and the dashed-red
    # contention arcs. Endpoints already filtered to known ids by the enriched builder.
    px: list = []
    py: list = []
    for src, dst in data.get("cpm_path_edges", []):
        px += [by_id[src]["x"], by_id[dst]["x"], None]
        py += [by_id[src]["y"], by_id[dst]["y"], None]
    if px:
        fig.add_trace(go.Scatter(x=px, y=py, mode="lines", hoverinfo="skip",
                                 line=dict(color="#f1c40f", width=2.5)))
    show_labels = len(nodes) <= 60          # cap labels; hover always carries the detail
    enriched = data.get("enriched", False)
    marker_color = [n["color"] for n in nodes] if enriched else "#1f77b4"
    fig.add_trace(go.Scatter(
        x=[n["x"] for n in nodes], y=[n["y"] for n in nodes],
        mode="markers+text" if show_labels else "markers",
        text=[n["label"] for n in nodes] if show_labels else None,
        textposition="top center",
        marker=dict(size=18, color=marker_color, line=dict(color="#0d3b66", width=1)),
        hovertext=[_dag_hover(n) for n in nodes],
        hoverinfo="text"))
    # A wide DAG (many x-columns) can't fit legibly at once, so window it and add an overview /
    # pan bar (range-slider) beneath the plot to navigate portions. ``x_columns`` is post-remap:
    # even ordinal columns in schedule mode, real CPM-time values in es/ls — so the same count-based
    # windowing works for every mode. Small graphs (≤ WINDOW_COLS columns) show whole, no bar.
    WINDOW_COLS = 24
    cols = data.get("x_columns") or sorted({n["x"] for n in nodes})
    use_slider = enriched and len(cols) > WINDOW_COLS
    x_range = None
    if use_slider:
        lo, hi = cols[0], cols[WINDOW_COLS - 1]          # first WINDOW_COLS columns
        pad = max(0.5, (hi - lo) * 0.02)
        x_range = [lo - pad, hi + pad]
    # Height: keep the graph readable if the bands stack tall (light auto-scale off the y-extent),
    # and leave room for the overview bar when it is shown.
    ys = [n["y"] for n in nodes]
    y_span = (max(ys) - min(ys)) if ys else 1.0
    height = max(460, int(24 * y_span) + 80) + (120 if use_slider else 0)
    # Schedule mode alone puts a measured quantity (start hour) on x, so reveal + label that axis;
    # the CPM/topology modes stay a pure node-graph with both axes hidden.
    _graph_layout(
        fig, height=height,
        x_title="start hour (project time, evenly spaced)" if layer_by == "schedule" else None,
        x_ticks=data.get("x_ticks") if layer_by == "schedule" else None,
        x_range=x_range, rangeslider=use_slider)
    if use_slider:
        st.caption(f"Large DAG — showing the first {WINDOW_COLS} of {len(cols)} time columns. "
                   f"Drag the **overview bar** beneath the plot to pan / zoom to any portion.")
    if enriched and (cx or px):
        parts = ["Grey = plan precedence"]
        if cx:
            parts.append("dashed red = resource-contention arcs added beyond precedence")
        parts.append("node color = float class (red critical / orange zero / green positive)")
        if layer_by == "schedule":
            # x = actual start hour, y = float-class band. The gold CPM overlay can WEAVE between
            # bands here (unlike the es/ls spine): a task can be on the LOGICAL critical path yet
            # carry resource slack in the constrained schedule → a non-critical float class → a
            # different band. That is honest, and pointing to es/ls keeps the straight-spine reading.
            if px:
                parts.insert(1, "gold = CPM (logical) critical path")
            st.caption(" · ".join(parts)
                       + ". Position: **x = start hour** (distinct starts spaced *evenly*, not "
                         "proportionally — the tick labels carry the real hour), **y = float class** "
                         "in bands — critical on the centre row (y≈0), zero- / positive-float above, "
                         "unscheduled below; same-color tasks read as a row and co-starting tasks "
                         "fan within their band. (The gold path can cross bands here — a logically-"
                         "critical task may still have resource slack; the **Earliest / Latest "
                         "start** layouts straighten it into a spine and keep proportional time.)")
        else:
            if px:
                parts.insert(1, "gold = CPM (logical) critical path — pinned to a straight y=0 spine")
            st.caption(" · ".join(parts)
                       + ". Off-path tasks fan above / below the spine; constrained-chain tasks sit "
                         "nearest it. Use **Layer by** to switch the x-axis (schedule start / earliest "
                         "start / dependency depth / latest start).")
    if data["has_cycle"]:
        st.warning("The dependency graph contains a cycle — the layout is approximate.")
    st.plotly_chart(fig, use_container_width=True)

def _render_task_inspector(session, baseline, result) -> None:
    """Per-task drill-down — "why isn't this task starting sooner?" — built ENTIRELY from the
    selected run's output DTOs plus the current-schedule precedence edges (no engine call). A task
    selector, a summary (timing / CPM float / actual TF / crews), a slip decomposition (lateness vs
    CPM early start split into a resource-contention portion and a precedence/time-window/calendar
    portion), predecessor / successor slack-attribution tables with the finish-driving predecessor
    flagged, and a clearly-labeled AGGREGATE resource-pressure readout.

    Honesty boundaries (load-bearing): the summary and slip come from ``result.schedule`` alone and
    show regardless of freshness; the neighbor tables are gated on the run being CURRENT for the
    current schedule (so the plan's edges match the run). The resource-pressure line is aggregate
    demand≥capacity over the wait window, NOT attributed to this task, and the authoritative NAMED
    binding resource / delaying predecessor is a documented Tier-B follow-up (the engine computes it
    but does not surface it) — this panel never presents an aggregate reading, nor a contention
    overlap/coupling, as an authoritative per-task blocker."""
    schedule = result.schedule
    activities = schedule.activities
    if not activities:
        st.info("This run scheduled no activities to inspect.")
        return
    by_task = {a.task_id: a for a in activities}
    task_id = st.selectbox("Task", [a.task_id for a in activities], key="prism_inspector_task")
    activity = by_task[task_id]

    # --- summary: self-consistent from the run's own schedule; shown regardless of freshness -----
    if activity.description:
        st.caption(activity.description)
    c1, c2, c3 = st.columns(3)
    c1.metric("Start (h)", f"{activity.start_hour:g}")
    c2.metric("End (h)", f"{activity.end_hour:g}")
    c3.metric("Duration (h)", f"{activity.duration:g}")

    def _h(v):
        return "—" if v is None else f"{v:g}"

    st.caption(
        f"CPM: ES {_h(activity.es_hours)} · LS {_h(activity.ls_hours)} · "
        f"slack {_h(activity.cpm_slack_hours)} (h)  ·  actual TF {_h(activity.tf_actual_hours)} h  ·  "
        f"float **{activity.float_class.value if activity.float_class is not None else '—'}**"
        f"{'  ·  on constrained chain' if activity.on_constrained_chain else ''}")
    crews = ";".join(f"{r.skill_type}:{r.crew_count}" for r in activity.actual_resources)
    st.caption(f"Assigned crews: {crews or '—'}")

    # --- slip decomposition (lateness vs CPM early start) ----------------------------------------
    slip = _task_slip(activity)
    if slip["lateness_vs_es"] is None:
        st.caption("No CPM early start recorded for this task — slip decomposition unavailable.")
    elif slip["lateness_vs_es"] <= 0:
        st.success(f"Starts at its CPM-earliest ({activity.start_hour:g} h) — no slip.")
    else:
        st.markdown(
            f"**Slip:** started {activity.start_hour:g} h vs CPM-earliest {activity.es_hours:g} h "
            f"→ **{slip['lateness_vs_es']:g} h late** = {slip['contention_delay']:g} h waiting on "
            f"resources + {slip['other_gating']:g} h predecessor / time-window / calendar gating.")

    # --- predecessor / successor slack attribution (needs the plan's edges to match the run) -----
    scenario = session.get_scenario()
    payload, warning = _current_schedule_payload(baseline, scenario)
    current = (
        not warning
        and services.current_freshness(
            result, baseline=baseline, scenario=scenario) is Freshness.CURRENT
    )
    if not current:
        st.caption("Dependency attribution hidden — the selected run isn't current for the current "
                   "schedule (re-run, or revert edits, to align them).")
    else:
        neighbors = _task_neighbors(payload, schedule, task_id)
        preds, succs = neighbors["predecessors"], neighbors["successors"]
        st.markdown("**Predecessors**")
        if not preds:
            st.caption("None — this task has no predecessors in the current plan.")
        else:
            st.dataframe(
                [{"★": "★" if p["is_driver"] else "", "task": p["task_id"],
                  "lag (h)": p["lag_hours"], "end (h)": p["end_hour"],
                  "FS-ready (h)": p["fs_ready"], "CPM slack (h)": p["cpm_slack_hours"],
                  "actual TF (h)": p["tf_actual_hours"], "float": p["float_class"] or ""}
                 for p in preds],
                use_container_width=True, hide_index=True)
            drivers = [p["task_id"] for p in preds if p["is_driver"]]
            if drivers:
                st.caption("★ finish-driving predecessor(s) — latest finish-to-start ready time, "
                           "the finish that gated this task's eligibility: " + ", ".join(drivers) + ".")
        st.markdown("**Successors**")
        if not succs:
            st.caption("None — nothing in the current plan depends on this task.")
        else:
            st.dataframe(
                [{"task": s["task_id"], "lag (h)": s["lag_hours"], "start (h)": s["start_hour"],
                  "CPM slack (h)": s["cpm_slack_hours"], "actual TF (h)": s["tf_actual_hours"],
                  "float": s["float_class"] or ""}
                 for s in succs],
                use_container_width=True, hide_index=True)

    # --- aggregate resource pressure over the wait window (heuristic, NOT task-attributed) -------
    if activity.delay_hours and activity.delay_hours > 0:
        util = result.diagnostics.resource_utilization if result.diagnostics is not None else None
        lo = activity.start_hour - activity.delay_hours
        hot = _saturated_skills(util, lo, activity.start_hour)
        if hot:
            st.caption(
                f"Over its {activity.delay_hours:g} h resource wait ([{lo:g}, "
                f"{activity.start_hour:g}) h) these pools were at/over capacity: **{', '.join(hot)}** "
                "— aggregate demand ≥ capacity during the wait, NOT attributed to this task.")
        elif util is not None:
            st.caption(
                f"No pool was at capacity over its resource wait ([{lo:g}, {activity.start_hour:g}) "
                f"h) — the {activity.delay_hours:g} h wait isn't explained by aggregate saturation.")
        st.caption("The authoritative NAMED binding resource / delaying predecessor is a deferred "
                   "Tier-B item — the engine computes it but does not surface it yet.")

def _render_chain_sets(result) -> None:
    """Within-run overlap of the selected COMPLETED run's three criticality sets — the CPM logical
    critical path, the resource-constrained chain, and the zero-float set. Schedule-only, shown
    regardless of freshness (reads ``result.schedule`` alone, like the inspector's summary/slip):
    three size metrics, the CPM-vs-constrained partition with the resource-vs-logic leverage-point
    framing, and a per-task membership table with a roster FILTER (Any critical set / All activities /
    On the CPM path / On the resource chain) that narrows only the table — the metrics and partition
    caption stay whole-run facts. This QUANTIFIES membership — the Activity DAG already *draws* the two
    chains; here we name the tasks that are critical because of resource contention rather than
    precedence (on the constrained chain but off the CPM path — the true leverage points)."""
    schedule = result.schedule
    if not schedule.activities:
        st.info("This run scheduled no activities to analyze.")
        return
    cs = _chain_sets(schedule)
    c1, c2, c3 = st.columns(3)
    c1.metric("CPM path", cs["n_cpm"])
    c2.metric("Constrained chain", cs["n_constrained"])
    c3.metric("Zero-float set", cs["n_zero_tf"])
    if cs["only_constrained"]:
        st.markdown(f"**Resource leverage points ({len(cs['only_constrained'])}):** "
                    f"{', '.join(cs['only_constrained'])}")
        st.caption("On the resource-constrained chain but NOT the CPM logical path — critical because "
                   "of resource contention, not precedence. Adding crew or re-sequencing here moves the "
                   "makespan; a classic CPM read misses them.")
    else:
        st.caption("The constrained chain and CPM path coincide — no resource-only leverage points; "
                   "precedence logic drives the schedule.")
    st.caption(f"Both (precedence + resources): {len(cs['both'])} · only CPM (precedence-critical, "
               f"resources don't gate): {len(cs['only_cpm'])} · only constrained (resource-driven): "
               f"{len(cs['only_constrained'])}.")
    # Table filter: which roster to LIST. The metrics + partition caption above are whole-run facts
    # and stay fixed; this narrows only the per-task table. Every row keeps all three membership
    # columns, so the CPM-vs-resource overlap stays legible inside any filtered view.
    all_rows = cs["all_rows"]
    ANY, ALL, CPM, RES = ("Any critical set", "All activities", "On the CPM path",
                          "On the resource chain")
    choice = st.radio("Show", (ANY, ALL, CPM, RES), index=0, horizontal=True,
                      key="prism_chain_filter",
                      help="'Any critical set' = on the CPM path, the resource chain, OR zero-float "
                           "(the union — a task can be resource-tight without being on either chain).")
    if choice == ALL:
        shown = all_rows
    elif choice == CPM:
        shown = [r for r in all_rows if r["on_cpm"]]
    elif choice == RES:
        shown = [r for r in all_rows if r["on_constrained"]]
    else:  # ANY = CPM ∪ resource chain ∪ zero-float (the default; matches the prior union view)
        shown = [r for r in all_rows if r["on_cpm"] or r["on_constrained"] or r["zero_tf"]]
    st.caption(f"Showing {len(shown)} of {len(all_rows)} scheduled activities.")
    st.dataframe(
        [{"task": r["task_id"], "CPM path": "✓" if r["on_cpm"] else "",
          "constrained": "✓" if r["on_constrained"] else "", "zero-float": "✓" if r["zero_tf"] else "",
          "float": r["float_class"] or "", "actual TF (h)": r["tf_actual_hours"]}
         for r in shown],
        use_container_width=True, hide_index=True)

def _render_window_preflight(session, baseline, result) -> None:
    """Per-task regulatory time-window pre-flight for the selected COMPLETED run: each scheduled task's
    start/end checked against its AUTHORED windows (fit ANY one window + a 1 ms grace — the engine's
    rule), computed directly from the schedule so it is always available and structured per task (the
    DTOs otherwise carry only an aggregate violation count / free-text audit strings). FRESHNESS-GATED
    like the inspector's neighbor tables: it fuses the plan's windows with the run's scheduled times, so
    it is hidden unless the run is CURRENT for the current schedule (else new windows would be checked
    against old scheduled times)."""
    schedule = result.schedule
    if not schedule.activities:
        st.info("This run scheduled no activities to check.")
        return
    scenario = session.get_scenario()
    payload, warning = _current_schedule_payload(baseline, scenario)
    current = (
        not warning
        and services.current_freshness(result, baseline=baseline, scenario=scenario)
        is Freshness.CURRENT
    )
    if not current:
        st.caption("Time-window pre-flight hidden — the selected run isn't current for the current "
                   "schedule (re-run, or revert edits, to align the scheduled times with the plan's "
                   "windows).")
        return
    pf = _window_preflight(payload, schedule)
    if pf["n_windowed"] == 0:
        st.info("No task in this plan declares a regulatory time window — nothing to pre-flight.")
        return
    c1, c2 = st.columns(2)
    c1.metric("Windowed tasks", pf["n_windowed"])
    c2.metric("Window violations", pf["n_violations"])
    if pf["n_violations"] == 0:
        st.success(f"All {pf['n_windowed']} windowed task(s) fit an authored time window.")
    else:
        st.warning(f"**{pf['n_violations']} task(s) violate their time window(s):** "
                   f"{', '.join(pf['violations'])} — scheduled outside every authored window "
                   "(start before earliest, or finish after latest).")
    st.dataframe(
        [{"task": r["task_id"], "fits": "✓" if r["fits"] else "✗",
          "start (h)": r["start_hour"], "end (h)": r["end_hour"],
          "nearest window": f"[{r['best_earliest']:g}–{r['best_latest']:g}]",
          "start early (h)": r["start_short"] or "", "finish late (h)": r["end_over"] or "",
          "# windows": r["n_windows"], "all windows": r["windows"]}
         for r in pf["rows"]],
        use_container_width=True, hide_index=True)
    st.caption("A task is compliant if it fits ANY one of its authored windows (start ≥ earliest and "
               "finish ≤ latest), matching the engine's post-run audit with a 1 ms grace. Windows are "
               "hours from outage start (not dates). Computed directly from the schedule — available "
               "without running the full audit; the DTOs otherwise expose only an aggregate count.")

# --- Phase-4 chart helpers: the deferred visual half shared by the three orchestration views. Both
#     take the same ``labeled_results`` primitive — a list of ``(label, RunResult)`` — so Compare /
#     Augment / Sweep feed them from the DTOs they already hold. Plotly is imported lazily (the
#     _render_plots discipline) so the module still imports with neither Streamlit nor Plotly present;
#     the pure ``_makespan_bar_rows`` / ``_multi_gantt_rows`` builders do the streamlit-free shaping. ---
def _render_makespan_bars(labeled_results) -> None:
    """Overlaid makespan bar chart: one stacked horizontal bar per run — a CPM lower-bound floor plus
    the optimism-gap segment on top, so the total width is the makespan and the split shows how much is
    the irreducible critical-path floor vs resource-induced slack. The shortest bar(s) are outlined
    green and flagged. Runs that did not complete (no schedule) are named in a caption, not charted."""
    import plotly.graph_objects as go

    rows = _makespan_bar_rows(labeled_results)
    charted = [r for r in rows if r["makespan_hours"] is not None]
    if not charted:
        st.caption("No completed runs to chart.")
        return

    labels = [r["label"] for r in charted]
    line_colors = ["#2ecc71" if r["is_best"] else "#111" for r in charted]
    line_widths = [2.5 if r["is_best"] else 1.0 for r in charted]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=labels, x=[r["cpm_lower_bound_hours"] for r in charted], orientation="h", name="CPM floor",
        marker_color="#3498db", marker_line_color=line_colors, marker_line_width=line_widths,
        hovertemplate="%{y}<br>CPM lower bound %{x:g} h<extra></extra>"))
    fig.add_trace(go.Bar(
        y=labels, x=[r["optimism_gap_hours"] for r in charted], orientation="h", name="optimism gap",
        marker_color=_FLOAT_CLASS_COLORS["zero_float"], marker_line_color=line_colors,
        marker_line_width=line_widths,
        hovertemplate="%{y}<br>optimism gap %{x:g} h<extra></extra>"))
    for r in charted:  # makespan total at each bar's end; the shortest is flagged
        fig.add_annotation(
            x=r["makespan_hours"], y=r["label"], xanchor="left", showarrow=False,
            text=(f"  {r['makespan_hours']:g} h ◄ shortest" if r["is_best"]
                  else f"  {r['makespan_hours']:g} h"),
            font=dict(color="#2ecc71" if r["is_best"] else "#444"))
    fig.update_layout(
        barmode="stack", height=90 + 46 * len(charted), margin=dict(l=10, r=90, t=30, b=10),
        xaxis_title="hours since project start",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_yaxes(autorange="reversed")  # first run on top
    st.plotly_chart(fig, use_container_width=True)

    dropped = [r["label"] for r in rows if r["makespan_hours"] is None]
    if dropped:
        st.caption(f"Not charted (no schedule): {', '.join(dropped)}.")

def _render_multi_gantt(labeled_results) -> None:
    """Aligned multi-run Gantt: one faceted subplot per run over the shared "hours since project start"
    x-axis (every run's times are hour-offsets from 0, so no alignment transform is needed), bars
    colored by float class exactly as the single-run Plots Gantt and a range slider on the bottom axis.
    Runs with no scheduled activities (a FAILED run, or an empty schedule) are named in a caption, not
    faceted."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    rows = _multi_gantt_rows(labeled_results)
    labels = list(dict.fromkeys(r["run_label"] for r in rows))  # facet order = run order, deduped
    if not labels:
        st.caption("No scheduled activities to chart.")
        return

    n = len(labels)
    fig = make_subplots(rows=n, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=labels)
    for i, label in enumerate(labels, start=1):
        for r in (row for row in rows if row["run_label"] == label):
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
                row=i, col=1)
        fig.update_yaxes(autorange="reversed", row=i, col=1)  # first activity on top
    fig.update_xaxes(title_text="hours since project start", row=n, col=1)
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.06), row=n, col=1)
    fig.update_layout(height=120 + 160 * n, bargap=0.2, margin=dict(l=10, r=10, t=40, b=10))
    st.plotly_chart(fig, use_container_width=True)

    charted = set(labels)
    dropped = [lbl for lbl, _r in labeled_results if lbl not in charted]
    if dropped:
        st.caption(f"Not charted (no scheduled activities): {', '.join(dropped)}.")

def _render_run_comparison(session, baseline, run_config) -> None:
    """Side-by-side diff of 2..N stored runs (Phase-4 keystone) — the first consumer of
    ``session.list_run_results()``. A keyed multiselect picks the runs; the table shows makespan /
    CPM lower bound / optimism gap / fitness / disposition / status, each row labeled by resolving its
    provenance ``scenario_delta_hash`` back to a scenario name (or "Baseline"), plus a freshness column
    relative to the CURRENTLY selected baseline / scenario / run config. GUI-only: every metric is read
    off the stored ``RunResult`` DTOs — no engine call, no session-API change. Freshness is computed
    here (it needs ``services``) and handed to the pure ``_comparison_rows`` as a ``{run_id: Freshness}``
    map so the view-data layer stays ``st``/``services``-free."""
    runs = session.list_run_results()
    if len(runs) < 2:
        st.info("Run at least two schedules to compare them here (sidebar → Run).")
        return

    scenario_labels = _scenario_hash_labels(session.list_scenarios())

    def _label(run_id):
        r = session.get_run_result(run_id)
        h = r.provenance.scenario_delta_hash if r is not None else None
        scen = "Baseline" if h is None else scenario_labels.get(h, f"scenario {h[:8]}")
        return f"{run_id} · {scen}"

    ids = [r.run_id for r in runs]
    chosen = st.multiselect(
        "Runs to compare", ids, default=ids[-2:], format_func=_label, key="prism_compare_runs")
    if len(chosen) < 2:
        st.info("Select at least two runs to compare.")
        return

    selected = [r for r in (session.get_run_result(rid) for rid in chosen) if r is not None]
    scenario = session.get_scenario()
    freshness_by_run = {
        r.run_id: services.current_freshness_detail(
            r, baseline=baseline, scenario=scenario, run_config=run_config)[0]
        for r in selected
    }
    st.dataframe(
        _comparison_rows(selected, scenario_labels, freshness_by_run),
        use_container_width=True, hide_index=True)
    st.caption(
        "Freshness is relative to the currently selected baseline / scenario / run config. A scenario "
        "label of “scenario <hash>” means that scenario was edited or removed since the run.")
    for r in selected:
        with st.expander(f"Provenance — run `{r.run_id}`", expanded=False):
            st.dataframe(_provenance_rows(r.provenance), use_container_width=True, hide_index=True)

    # --- charts: overlaid makespan bars + an aligned multi-run Gantt over the compared runs ---
    labeled = [(_label(r.run_id), r) for r in selected]
    st.markdown("**Makespan**")
    _render_makespan_bars(labeled)
    with st.expander("Aligned Gantt", expanded=False):
        _render_multi_gantt(labeled)

# Rendered on the REPLAN page (imported by pages/replan.py), NOT Results: augmentation is a replan
# action — build a resource what-if from the run's bottlenecks and re-run. It physically stays here
# beside the comparison renderers it reuses (_render_makespan_bars / _render_multi_gantt, shared with
# Compare runs / Sweep) so relocating the tab did not drag those into a new module.
def _render_run_augmentation(session, baseline, result, run_config, run_plan) -> None:
    """Guided resource-augmentation what-if (Phase-4.2): pick the bottleneck pool + N crew, then one
    button clones the baseline, reruns with that pool SET to (current + N) crew from hour 0, and shows a
    VERIFIED before/after delta — both sides actually re-run. The bottleneck ranking and the shown crew
    count are ADVISORY, read off the selected run's aggregate resource utilization (the same heuristic as
    the Task inspector — NOT the authoritative named binding resource, a deferred Tier-B item); the
    bump's arithmetic is recomputed against the freshly-run baseline, so the delta is honest regardless
    of which run was selected. ``run_plan`` is the composition-root seam — a callable
    ``run_plan(run_config=None, scenario=None) -> PipelineResult`` running the current baseline through the
    shared store+executor (here called with only ``scenario=``, so it uses the live run config); ``None``
    means the page was built without wiring (defensive; never in the app)."""
    if run_plan is None:
        st.info("Augmentation is unavailable — the runner is not wired.")
        return
    if result is None or result.status is not RunResultStatus.COMPLETED:
        st.info("Run a schedule first (sidebar) to see resource bottlenecks to augment.")
        return
    util = result.diagnostics.resource_utilization if result.diagnostics is not None else None
    cands = _augmentation_candidates(util)
    if not cands:
        st.info("This run has no resource-utilization data to guide augmentation.")
        return

    by_skill = {c["skill_type"]: c for c in cands}
    pool = st.selectbox(
        "Resource pool", [c["skill_type"] for c in cands], key="prism_augment_pool",
        format_func=lambda s: (
            f"{s} — {by_skill[s]['current_count']} crew · saturated "
            f"{by_skill[s]['saturated_hours']:g} h · peak short {by_skill[s]['peak_shortfall']}"))
    n = int(st.number_input("Add crew (N)", min_value=1, value=1, step=1, key="prism_augment_n"))
    current = by_skill[pool]["current_count"]
    st.caption(f"Rerun with **{current + n}** {pool} crew (currently {current}).")
    if by_skill[pool]["time_varying"]:
        st.caption(f"⚠️ {pool} has time-varying availability — a from-hour-0 bump flattens it to "
                   f"{current + n} crew for the whole outage.")
    st.caption("Bottleneck ranking is aggregate demand ≥ capacity (heuristic), NOT the authoritative "
               "named binding resource (deferred Tier B); the counts above are from the selected run.")

    if st.button(f"Clone baseline & rerun with +{n} {pool} crew", type="primary"):
        before = run_plan(scenario=None)
        if not before.ok:
            st.error(f"Baseline run blocked at {before.stage}.")
            _render_issues(before.issues)
            return
        # Authoritative current count read off the freshly-run baseline (guidance value as fallback).
        base_util = (before.result.diagnostics.resource_utilization
                     if before.result.diagnostics is not None else None)
        base_counts = {c["skill_type"]: c["current_count"] for c in _augmentation_candidates(base_util)}
        base_current = base_counts.get(pool, current)
        scn = _add_resource_change(
            _mint_scenario(baseline, existing_ids=[s.scenario_id for s in session.list_scenarios()],
                           name=f"{pool} +{n}"),
            baseline, pool, 0.0, base_current + n)
        after = run_plan(scenario=scn)
        if not after.ok:
            st.error(f"Augmented run blocked at {after.stage}.")
            _render_issues(after.issues)
            return
        session.add_run_result(before.result)
        session.add_run_result(after.result)
        session.add_scenario(scn)      # additive — do NOT hijack the current-schedule pointer
        st.session_state["prism_augment_pair"] = (
            before.result.run_id, after.result.run_id, pool, n)

    # --- verified delta, rendered from the pinned pair so it survives the click's rerun ---
    pair = st.session_state.get("prism_augment_pair")
    if not pair:
        return
    before_id, after_id, done_pool, done_n = pair
    before = session.get_run_result(before_id)
    after = session.get_run_result(after_id)
    if before is None or after is None:
        return

    st.markdown(f"**Verified delta — {done_pool} +{done_n} crew**")
    delta_rows = _augmentation_delta(before, after)
    by_metric = {r["metric"]: r for r in delta_rows}
    m1, m2 = st.columns(2)
    for col, (metric, label) in zip(
            (m1, m2),
            (("makespan_hours", "Makespan (h)"), ("optimism_gap_hours", "Optimism gap (h)"))):
        row = by_metric.get(metric)
        if row is None or row["before"] is None or row["after"] is None:
            col.metric(label, "—")
        else:
            d = row["delta"]
            col.metric(label, f"{row['after']:g}",
                       delta=None if d is None else f"{d:g}", delta_color="inverse")  # shorter = good

    st.dataframe(delta_rows, use_container_width=True, hide_index=True)
    st.caption(f"Verified: both sides were actually re-run. The bump SET {done_pool} to its baseline "
               f"hour-0 count + {done_n} crew, held flat from hour 0.")

    # --- charts: baseline-vs-augmented makespan bars + an aligned before/after Gantt ---
    labeled = [("baseline", before), (f"{done_pool} +{done_n}", after)]
    st.markdown("**Makespan**")
    _render_makespan_bars(labeled)
    with st.expander("Aligned Gantt", expanded=False):
        _render_multi_gantt(labeled)

    with st.expander("Full comparison & provenance", expanded=False):
        scenario_labels = _scenario_hash_labels(session.list_scenarios())
        scenario = session.get_scenario()
        freshness_by_run = {
            r.run_id: services.current_freshness_detail(
                r, baseline=baseline, scenario=scenario, run_config=run_config)[0]
            for r in (before, after)}
        st.dataframe(_comparison_rows([before, after], scenario_labels, freshness_by_run),
                     use_container_width=True, hide_index=True)
        for r in (before, after):
            st.markdown(f"Provenance — run `{r.run_id}`")
            st.dataframe(_provenance_rows(r.provenance), use_container_width=True, hide_index=True)

# The classic/interpretable rules the priority-rule axis offers by default; the multiselect exposes all 22.
_SWEEP_DEFAULT_RULES = ("lf", "ls", "ef", "es", "duration")
# The SGS variants offered by default on the SGS axis; the multiselect exposes all five.
_SWEEP_DEFAULT_SGS = ("max_use_res_ranked", "max_use_res_shuffled", "first")
# The mode axis fans out the CARTESIAN PRODUCT of the selected tasks' modes; since each combination is a
# full solve, refuse (never truncate) above this many. A tuning detail, not a domain limit.
_MODE_SWEEP_CAP = 24

def _render_run_sweep(session, baseline, run_config, run_plan) -> None:
    """Config sweep (Phase-4.3): re-run the baseline once per value of ONE chosen ``RunConfig`` axis
    (priority rule / SGS variant / random seed / execution-mode combination) and rank the results
    shortest-makespan-first, so the
    analyst can see which config yields the tightest schedule. The second Phase-4 *orchestration* — where
    4.2 varies the scenario, this varies the RunConfig — it fans the clone→rerun→delta loop out N ways into
    a leaderboard. Every value is ACTUALLY solved (verified) through the composition-root ``run_plan`` seam
    and the shared store+executor, so run ids increment and never collide. Each swept run is a plain
    BASELINE run (``scenario=None``) whose only difference is the one varied field; that value is NOT
    recoverable from the stored run (provenance keeps only the one-way ``run_config_hash``), so we track
    ``{run_id: label}`` at sweep time, pin it in session state, and hand it to the pure ``_sweep_rows``
    builder (freshness/services stay here). Swept runs are stored additively, so they also appear in
    **Compare runs** — the path for full provenance/cross-diff, keeping this segment focused on the
    leaderboard. ``run_plan`` ``None`` means unwired (defensive)."""
    if run_plan is None:
        st.info("Sweep is unavailable — the runner is not wired.")
        return

    axis = st.radio("Sweep axis", ["Priority rule", "SGS variant", "Seed", "Modes"],
                    horizontal=True, key="prism_sweep_axis")

    # Each axis builds a common ``variants`` list of (label_str, RunConfig) plus the leaderboard column
    # name (``label_key``), a singular display noun (``axis_label``), and the current selection's label.
    if axis == "Priority rule":
        label_key, axis_label, base_label = "priority_rule", "rule", run_config.priority_rule
        default = [r for r in PRIORITY_RULES if r in set(_SWEEP_DEFAULT_RULES) | {base_label}]
        picked = st.multiselect(
            "Priority rules to sweep", list(PRIORITY_RULES), default=default, key="prism_sweep_rules")
        st.caption(f"Each value is a full solve; all {len(PRIORITY_RULES)} engine rules are selectable. "
                   f"The current rule is **{base_label}**.")
        variants = [(r, replace(run_config, priority_rule=r)) for r in picked]
    elif axis == "SGS variant":
        label_key, axis_label, base_label = "sgs", "SGS variant", run_config.sgs.value
        options = [v.value for v in SGSVariant]
        default = [v for v in options if v in set(_SWEEP_DEFAULT_SGS) | {base_label}]
        picked = st.multiselect(
            "SGS variants to sweep", options, default=default, key="prism_sweep_sgs")
        st.caption(f"Each value is a full solve; all {len(options)} schedule-generation schemes are "
                   f"selectable. The current variant is **{base_label}**.")
        variants = [(v, replace(run_config, sgs=SGSVariant(v))) for v in picked]
    elif axis == "Seed":
        label_key, axis_label, base_label = "seed", "seed", str(run_config.seed)
        n = int(st.number_input("Number of seeds to try", min_value=2, value=3, step=1,
                                key="prism_sweep_nseeds"))
        seeds = [run_config.seed + i for i in range(n)]
        st.caption(f"Each value is a full solve; sweeps seeds {seeds[0]}–{seeds[-1]} from the current "
                   f"seed **{run_config.seed}**.")
        variants = [(str(s), replace(run_config, seed=s)) for s in seeds]
    else:  # Modes — cartesian product of the SELECTED multi-mode tasks' modes (capped), holding every
           # unselected multi-mode task at its current pick. Combinatorial, so the enumeration + labelling
           # + cap + base-merge live in the pure ``_mode_sweep_variants`` builder; this branch stays thin.
        label_key, axis_label, base_label = "modes", "mode combination", ""
        payload, _warn = _current_schedule_payload(baseline, session.get_scenario())
        mode_opts = _mode_options(payload)
        if not mode_opts:
            st.info("No multi-mode tasks in this plan — add a second mode to a task in the Plan editor "
                    "(Execution modes) to sweep them.")
            return
        picked = st.multiselect("Multi-mode tasks to sweep", [r["task_id"] for r in mode_opts],
                                default=[r["task_id"] for r in mode_opts], key="prism_sweep_mode_tasks")
        if not picked:
            st.info("Select at least one multi-mode task to sweep.")
            return
        built = _mode_sweep_variants(mode_opts, picked, base_selections=run_config.mode_selections,
                                     cap=_MODE_SWEEP_CAP)
        st.caption(f"Each combination is a full solve; sweeps the product of the selected tasks' modes "
                   f"({built['total']} combination(s), cap {_MODE_SWEEP_CAP}). Unselected multi-mode "
                   f"tasks stay at their current pick.")
        if built["capped"]:
            st.warning(f"{built['total']} combinations exceeds the cap of {_MODE_SWEEP_CAP} — "
                       f"deselect some tasks.")
            return
        base_label = built["base_label"]
        variants = [(lbl, replace(run_config, mode_selections=ms)) for lbl, ms in built["variants"]]

    if len(variants) < 2:
        st.info(f"Pick at least two {axis_label}s to sweep.")
        return

    if st.button(f"Run sweep over {len(variants)} {axis_label}s", type="primary"):
        run_ids: list[str] = []
        label_by_run: dict[str, str] = {}
        blocked: list[tuple[str, str]] = []
        with st.spinner(f"Solving {len(variants)} {axis_label}s…"):
            for label, rc2 in variants:
                res = run_plan(run_config=rc2)
                if res.ok:
                    run_ids.append(res.result.run_id)
                    label_by_run[res.result.run_id] = label
                    session.add_run_result(res.result)   # additive; no scenario (baseline runs)
                else:
                    blocked.append((label, res.stage))
        st.session_state["prism_sweep"] = {
            "run_ids": run_ids, "label_by_run": label_by_run, "blocked": blocked,
            "base_label": base_label, "label_key": label_key, "axis_label": axis_label}

    # --- leaderboard, rendered from the pinned sweep so it survives the click's rerun. Only render it
    #     under the axis that produced it (one pinned slot), so no stale cross-axis table appears. ---
    sweep = st.session_state.get("prism_sweep")
    if not sweep or sweep["label_key"] != label_key:
        return
    results = [r for r in (session.get_run_result(i) for i in sweep["run_ids"]) if r is not None]
    if not results and not sweep["blocked"]:
        return
    scenario = session.get_scenario()
    freshness_by_run = {
        r.run_id: services.current_freshness_detail(
            r, baseline=baseline, scenario=scenario, run_config=run_config)[0]
        for r in results}
    key = sweep["label_key"]
    rows = _sweep_rows(results, sweep["label_by_run"], freshness_by_run, label_key=key)

    ranked = [row for row in rows if row["makespan_hours"] is not None]
    if ranked:
        winner = ranked[0]
        base = sweep["base_label"]
        noun = sweep["axis_label"]
        base_row = next((row for row in ranked if row[key] == base), None)
        if base_row is not None and base_row is not winner:
            st.success(
                f"Shortest makespan: **{winner[key]}** at {winner['makespan_hours']:g} h "
                f"— {base_row['makespan_hours'] - winner['makespan_hours']:g} h shorter than the current "
                f"{noun} '{base}' ({base_row['makespan_hours']:g} h).")
        else:
            st.success(
                f"Shortest makespan: **{winner[key]}** at {winner['makespan_hours']:g} h"
                + (" (the current selection)." if base_row is winner else "."))

    st.dataframe(rows, use_container_width=True, hide_index=True)
    if sweep["blocked"]:
        st.warning("Blocked: "
                   + ", ".join(f"{label} ({stage})" for label, stage in sweep["blocked"]))
    st.caption("Verified: each value was actually re-run on the baseline. Δ vs best is hours above the "
               "shortest makespan. The swept runs are stored — open or diff them with full provenance on "
               "**Compare runs**.")

    # --- chart: overlaid makespan bars echoing the leaderboard order (no per-run Gantt — a wide sweep
    #     would be unreadable faceted; the aligned Gantt lives on Compare runs / Augment). ---
    labeled = [(sweep["label_by_run"].get(r.run_id, ""), r) for r in results]
    st.markdown("**Makespan**")
    _render_makespan_bars(labeled)

def _render_results_page(session, baseline, result, run_config, run_plan=None) -> None:
    """Results page: the selected run's summary header (or a prompt to run), then a segmented
    switch between the Gantt / resource *Plots*, the run-aware *Activity DAG*, and a per-task
    *Task inspector*. The DAG degrades to the structural pre-run graph when no completed, current
    run is selected; the inspector needs a completed run."""
    if result is not None:
        _render_results_header(result, session, baseline, run_config)
    else:
        st.info("Run a schedule (sidebar) to see results for the current schedule.")
    view = st.segmented_control(
        "View",
        ["Plots", "Activity DAG", "Task inspector", "Chain sets", "Time windows", "Compare runs",
         "Sweep"],
        default="Plots", key="prism_results_view")
    if view == "Activity DAG":
        _render_activity_graph(session, baseline, result)
    elif view == "Task inspector":
        if result is not None and result.status is RunResultStatus.COMPLETED:
            _render_task_inspector(session, baseline, result)
        elif result is not None:
            st.info("The selected run did not complete — no schedule to inspect.")
        else:
            st.info("Run a schedule (sidebar) to inspect individual tasks.")
    elif view == "Chain sets":
        if result is not None and result.status is RunResultStatus.COMPLETED:
            _render_chain_sets(result)
        elif result is not None:
            st.info("The selected run did not complete — no schedule to analyze.")
        else:
            st.info("Run a schedule (sidebar) to see the chain sets.")
    elif view == "Time windows":
        if result is not None and result.status is RunResultStatus.COMPLETED:
            _render_window_preflight(session, baseline, result)
        elif result is not None:
            st.info("The selected run did not complete — no schedule to check.")
        else:
            st.info("Run a schedule (sidebar) to pre-flight the time windows.")
    elif view == "Compare runs":
        _render_run_comparison(session, baseline, run_config)
    elif view == "Sweep":
        _render_run_sweep(session, baseline, run_config, run_plan)
    elif result is not None and result.status is RunResultStatus.COMPLETED:
        _render_plots(result)
    elif result is not None:
        st.info("The selected run did not complete — see the failure above.")
    else:
        st.info("Run a schedule (sidebar) to see the Gantt and resource plots.")
