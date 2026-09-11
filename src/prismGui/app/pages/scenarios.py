"""Scenarios page: the scenario library manager + baseline relation graph."""
from __future__ import annotations

from dataclasses import replace

from prismGui.app._streamlit import st
from prismGui.app.scenario_model import _mint_scenario
from prismGui.app.sidebar import _select_schedule_next_run
from prismGui.app.view_data import _graph_layout, _relation_graph_data


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

def _render_scenarios(session, baseline) -> None:
    """Scenarios page: the scenario library (create / rename / delete) and the baseline →
    scenarios relation graph. Side-by-side comparison of scenario runs is future Phase-4 work."""
    _render_scenario_manager(session, baseline, allow_edit=True)
    st.divider()
    st.subheader("Baseline → scenarios")
    scenarios = session.list_scenarios()
    st.caption(f"{len(scenarios)} scenario(s) hang off this baseline as overlays; the current "
               "schedule is highlighted in red.")
    _render_relation_graph(session, baseline)
