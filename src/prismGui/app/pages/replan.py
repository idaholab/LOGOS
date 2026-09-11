"""Replan page: the selected scenario's overlay panel + execution-mode picker."""
from __future__ import annotations

import json
from dataclasses import replace

from prismGui.app._streamlit import st
from prismGui.domain.run_config import ModeSelection
from prismGui.domain.scenario import Scenario
from prismGui.app.edit_model import _as_float, _availability_options, _mode_options, _resource_options, _task_options
from prismGui.app.scenario_model import _SCN_INTENTS, _add_duration_override, _add_resource_change, _current_schedule_payload, _is_whatif, _remove_duration_override, _remove_resource_change, _scenario_duration_rows, _scenario_is_empty, _scenario_resource_rows


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

def _render_replan(session, baseline) -> None:
    """Replan page: author the SELECTED scenario's overlay (task-duration overrides + resource
    what-ifs) and its execution modes, then re-run from the sidebar. Empty-state when the
    baseline is the current schedule — a scenario must be picked or created first."""
    if session.get_current_scenario_id() is None:
        st.info("Select a scenario in the sidebar, or create one on the **Scenarios** page, to "
                "replan. (The baseline itself is edited on the **Plan** page.)")
        return
    _render_scenario_panel(session, baseline)
    _render_mode_picker(session, baseline)
