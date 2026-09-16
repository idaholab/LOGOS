"""Scenarios page: the scenario library manager + baseline relation graph."""
from __future__ import annotations

from dataclasses import replace

from prismGui.app._streamlit import st
from prismGui.app.scenario_model import _clone_scenario, _mint_scenario, _scenario_diff
from prismGui.app.sidebar import _select_schedule_next_run
from prismGui.app.view_data import _graph_layout, _relation_graph_data


def _render_scenario_manager(session, baseline, *, allow_edit: bool) -> None:
    """Create / rename / delete scenarios (rendered inline — always visible, no expander). Creating
    a new scenario is always available; rename and delete act on the CURRENT scenario and are shown
    only when a scenario is selected (``allow_edit``). All three route their selection change through
    ``_select_schedule_next_run`` / a rerun so the sidebar selector stays in sync (see its
    widget-reconcile note)."""
    scenarios = session.list_scenarios()
    ids = [s.scenario_id for s in scenarios]
    st.caption(f"{len(scenarios)} scenario(s) on this baseline. "
               "Scenarios are thin overlays (durations + resource what-ifs) — structural "
               "edits belong to the Baseline. A new scenario can start empty off the baseline "
               "or branch from an existing one (copying its what-ifs).")

    # --- create -----------------------------------------------------------
    # "Branch from" lets a new scenario START as a copy of an existing one (clone + lineage
    # label) instead of empty off the baseline. The clone is a SNAPSHOT — later edits to the
    # source do not propagate.
    source = st.selectbox(
        "Branch from", [None, *scenarios],
        format_func=lambda s: "Baseline (empty)" if s is None else (s.name or s.scenario_id),
        key="prism_scn_branch_from",
        help="Baseline → a fresh empty overlay. A scenario → a copy of its what-ifs, tagged as "
             "derived from it.")
    new_name = st.text_input("New scenario name", key="prism_scn_new_name",
                             placeholder="(auto-named if left blank)")
    if st.button("Create scenario", key="prism_scn_create"):
        if source is None:
            scn = _mint_scenario(baseline, existing_ids=ids, name=new_name.strip() or None)
        else:
            scn = _clone_scenario(source, baseline, existing_ids=ids,
                                  name=new_name.strip() or None)
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

def _render_scenario_comparison(session) -> None:
    """List the ACTUAL overlay differences between two scenarios — the deltas each one stages that
    the other does not. Compares the INPUT overlays (what-ifs), NOT the run outputs (that is the
    Results page's Compare runs). A delta that differs only in value (same target, different amount)
    surfaces as two rows — present on one side each — the honest "what actually differs"."""
    scenarios = session.list_scenarios()
    if len(scenarios) < 2:
        st.caption("Add at least two scenarios to compare their overlays.")
        return
    names = {s.scenario_id: (s.name or s.scenario_id) for s in scenarios}
    by_id = {s.scenario_id: s for s in scenarios}

    c1, c2 = st.columns(2)
    a_id = c1.selectbox("Scenario A", list(by_id), format_func=lambda sid: names[sid],
                        key="prism_scn_diff_a")
    b_choices = [sid for sid in by_id if sid != a_id]
    b_id = c2.selectbox("Scenario B", b_choices, format_func=lambda sid: names[sid],
                        key="prism_scn_diff_b")

    rows = _scenario_diff(by_id[a_id], by_id[b_id])
    if not rows:
        st.info("Both scenarios are empty overlays (identical to the plain baseline).")
        return
    diffs = [r for r in rows if not (r["in_a"] and r["in_b"])]
    if not diffs:
        st.success(f"“{names[a_id]}” and “{names[b_id]}” stage identical overlays.")
        return
    # Distinct, prefixed column headers so two same-named scenarios never collide in the table.
    col_a, col_b = f"A · {names[a_id]}", f"B · {names[b_id]}"
    st.caption(f"{len(diffs)} difference(s). ✓ = staged in that scenario.")
    st.dataframe(
        [{"Family": r["family"], "Change": r["detail"],
          col_a: "✓" if r["in_a"] else "", col_b: "✓" if r["in_b"] else ""}
         for r in diffs],
        use_container_width=True, hide_index=True)

def _render_relation_graph(session, baseline) -> None:
    """The baseline → scenarios relation graph (Plotly network scatter): the baseline at the
    centre with each stored scenario on a ring around it. Each scenario's edge points to its
    lineage parent — the scenario it was branched from, else the baseline (see
    ``_relation_graph_data``). The baseline is a square, scenarios circles; the CURRENT schedule is
    highlighted. Hover shows each scenario's staged overlay count and its lineage parent."""
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
        line = f"Scenario “{n['label']}”<br>{n['overlay_count']} overlay change(s)"
        parent = n.get("derived_from")
        if parent is not None and parent in by_id:
            line += f"<br>derived from “{by_id[parent]['label']}”"
        return line

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

def _render_scenarios(session, baseline) -> None:
    """Scenarios page: the scenario library (create / branch / rename / delete, always visible), an
    overlay diff between two scenarios, and the baseline → scenarios relation graph. Side-by-side
    comparison of scenario RUN RESULTS lives on the Results page (Compare runs)."""
    st.subheader("Scenarios")
    _render_scenario_manager(session, baseline, allow_edit=True)
    st.divider()
    st.subheader("Compare overlays")
    _render_scenario_comparison(session)
    st.divider()
    st.subheader("Baseline → scenarios")
    scenarios = session.list_scenarios()
    st.caption(f"{len(scenarios)} scenario(s) hang off this baseline as overlays; the current "
               "schedule is highlighted in red.")
    _render_relation_graph(session, baseline)
