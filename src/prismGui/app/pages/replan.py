"""Replan page: the selected scenario's overlay panel + execution-mode picker."""
from __future__ import annotations

import json
from dataclasses import replace

from prismGui.app._streamlit import st
from prismGui.domain.run_config import ModeSelection
from prismGui.domain.scenario import Scenario
from prismGui.app.edit_model import (
    _as_float, _availability_options, _dependency_options, _equipment_availability_options,
    _equipment_options, _location_availability_options, _location_options, _mode_options,
    _resource_options, _task_options,
)
from prismGui.app.scenario_model import (
    _SCN_INTENTS, _add_dependency_suppression, _add_duration_override, _add_emergent_dependency,
    _add_emergent_task, _add_equipment_change, _add_location_change, _add_resource_change,
    _add_task_suppression, _current_schedule_payload, _is_whatif, _remove_dependency_suppression,
    _remove_duration_override, _remove_emergent_dependency, _remove_emergent_task,
    _remove_equipment_change, _remove_location_change, _remove_resource_change,
    _remove_task_suppression, _scenario_dependency_suppression_rows, _scenario_duration_rows,
    _scenario_emergent_dependency_rows, _scenario_emergent_task_rows, _scenario_equipment_rows,
    _scenario_is_empty, _scenario_location_rows, _scenario_resource_rows,
    _scenario_task_suppression_rows,
)

# Guided resource augmentation is a REPLAN action (auto-build a resource what-if + re-run). Its
# renderer physically lives in the results module beside the comparison helpers it shares with
# Compare runs / Sweep (_render_makespan_bars / _render_multi_gantt); we surface it here.
from prismGui.app.pages.results import _render_run_augmentation


# Per-family delta labels for the "Active scenario" summary caption. Grows as new what-if families
# are added (mirrors scenario_model._DELTA_FIELDS / view_data._OVERLAY_FIELDS).
_FAMILY_LABELS = (
    ("duration_overrides", "duration override"),
    ("resource_changes", "skill change"),
    ("equipment_changes", "equipment change"),
    ("location_changes", "location change"),
    ("emergent_tasks", "added task"),
    ("emergent_dependencies", "added dependency"),
    ("task_suppressions", "removed task"),
    ("dependency_suppressions", "removed dependency"),
)


def _scenario_summary(scenario) -> str:
    """One-line count of the scenario's staged deltas across every family (for the panel caption)."""
    parts = []
    for field, label in _FAMILY_LABELS:
        n = len(getattr(scenario, field, None) or ())
        if n:
            parts.append(f"{n} {label}{'s' if n != 1 else ''}")
    return "Active scenario: " + (", ".join(parts) if parts else "no deltas") + "."


def _window_inputs(key_prefix: str):
    """Shared 'initial hour' + optional 'final hour' inputs for a period-based what-if. Returns
    ``(from_hour, to_hour)`` where ``to_hour is None`` means an open-ended change (from the initial
    hour onward); checking the box reveals a bounded ``[from, to)`` window."""
    c1, c2 = st.columns([1, 1])
    from_hour = c1.number_input("From hour", min_value=0.0, value=0.0, step=1.0,
                                key=f"{key_prefix}_from")
    bounded = c2.checkbox("Set final hour (bounded window)", value=False, key=f"{key_prefix}_bounded",
                          help="Leave unchecked for an open-ended change — the new value holds from "
                               "the initial hour to the end of the horizon.")
    to_hour = None
    if bounded:
        to_hour = st.number_input("Until hour", min_value=float(from_hour), value=float(from_hour) + 1.0,
                                  step=1.0, key=f"{key_prefix}_to")
    return from_hour, to_hour


def _window_label(from_hour, to_hour) -> str:
    """Human window text: 'from hour X' (open-ended) or 'from hour X until Y' (bounded ``[from, to)``)."""
    return (f"from hour {from_hour:g}" if to_hour is None
            else f"from hour {from_hour:g} until {to_hour:g}")


def _location_period_caption(p) -> str:
    """One current-location period as text; the worker cap is shown only when the baseline sets one."""
    base = f"[{p['start_date']} … {p['end_date']}) × {p['max_concurrent_tasks']} tasks"
    mw = p.get("max_concurrent_workers")
    return base if mw is None else base + f", {mw} workers"


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

def _render_duration_tab(session, baseline, scenario, raw_tree) -> Scenario:
    """Task duration override tab. Returns the (possibly updated) scenario so the caller re-reads
    the current overlay after an add/remove within the same rerun."""
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
    return scenario


def _render_skills_tab(session, baseline, scenario, raw_tree) -> Scenario:
    """Skill-pool availability what-if tab (intent-tagged: a roster correction is redirected to
    the Plan editor, never authored here). A bounded ``[from, until)`` window is optional."""
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
        from_hour, to_hour = _window_inputs("prism_scn_res")
        new_count = st.number_input("New available count", min_value=0, value=0, step=1,
                                    key="prism_scn_res_count")
        if _is_whatif(intent):
            if st.button("Add resource what-if", key="prism_scn_res_add"):
                _apply_scenario(
                    session,
                    _add_resource_change(scenario, baseline, chosen_r["skill_type"],
                                         from_hour, new_count, to_hour=to_hour),
                    success_msg=(f"Scenario: {chosen_r['skill_type']} → {int(new_count)} "
                                 f"{_window_label(from_hour, to_hour)}."))
                scenario = session.get_scenario()
        else:
            st.info(
                "A roster **correction** is a baseline change, not a what-if. Make it in the "
                "**Resources & availability** tab of the plan editor above (open a draft → "
                "change the pool's available count → commit). That keeps the correction in "
                "your baseline, where scheduling and provenance expect it.")

    for row in _scenario_resource_rows(scenario):
        c1, c2 = st.columns([4, 1])
        c1.caption(f"• {row['skill_type']} → {row['new_count']} "
                   f"{_window_label(row['from_hour'], row['to_hour'])}")
        if c2.button("Remove", key=f"prism_scn_res_rm_{row['index']}"):
            _apply_scenario(
                session, _remove_resource_change(scenario, baseline, row["index"]),
                success_msg=f"Removed resource change for {row['skill_type']}.")
            scenario = session.get_scenario()
    return scenario


def _render_equipment_tab(session, baseline, scenario, raw_tree) -> Scenario:
    """Equipment availability what-if tab (e.g. a crane out of service for a window). A bounded
    ``[from, until)`` window is optional; ``new quantity = 0`` == out of service."""
    st.caption("A what-if change to equipment availability. To permanently correct equipment "
               "availability, edit the baseline on the **Plan** page instead.")
    equip = _equipment_options(raw_tree)
    if not equip:
        st.caption("No equipment in this plan.")
    else:
        e_labels = [f"{e['equipment_id']} ({e['n_periods']} period(s))" for e in equip]
        e_pick = st.selectbox("Equipment", range(len(equip)),
                              format_func=lambda k: e_labels[k], key="prism_scn_eqp_pick")
        chosen_e = equip[e_pick]
        existing = _equipment_availability_options(raw_tree, chosen_e["index"])
        if existing:
            st.caption("Current availability: " + "; ".join(
                f"[{p['start_date']} … {p['end_date']}) × {p['quantity_available']}"
                for p in existing))
        from_hour, to_hour = _window_inputs("prism_scn_eqp")
        new_qty = st.number_input("New quantity available", min_value=0, value=0, step=1,
                                  key="prism_scn_eqp_qty")
        if st.button("Add equipment what-if", key="prism_scn_eqp_add"):
            _apply_scenario(
                session,
                _add_equipment_change(scenario, baseline, chosen_e["equipment_id"],
                                      from_hour, new_qty, to_hour=to_hour),
                success_msg=(f"Scenario: {chosen_e['equipment_id']} → {int(new_qty)} "
                             f"{_window_label(from_hour, to_hour)}."))
            scenario = session.get_scenario()

    for row in _scenario_equipment_rows(scenario):
        c1, c2 = st.columns([4, 1])
        c1.caption(f"• {row['equipment_id']} → {row['new_quantity']} "
                   f"{_window_label(row['from_hour'], row['to_hour'])}")
        if c2.button("Remove", key=f"prism_scn_eqp_rm_{row['index']}"):
            _apply_scenario(
                session, _remove_equipment_change(scenario, baseline, row["index"]),
                success_msg=f"Removed equipment change for {row['equipment_id']}.")
            scenario = session.get_scenario()
    return scenario


def _render_locations_tab(session, baseline, scenario, raw_tree) -> Scenario:
    """Location concurrency-cap what-if tab. Sets max concurrent tasks (always) and, optionally,
    max concurrent workers; leaving the worker box unchecked keeps the baseline worker cap."""
    st.caption("A what-if change to a location's concurrency caps. To permanently correct a "
               "location, edit the baseline on the **Plan** page instead.")
    locs = _location_options(raw_tree)
    if not locs:
        st.caption("No locations in this plan.")
    else:
        l_labels = [f"{lo['location_id']} ({lo['n_periods']} period(s))" for lo in locs]
        l_pick = st.selectbox("Location", range(len(locs)),
                              format_func=lambda k: l_labels[k], key="prism_scn_loc_pick")
        chosen_l = locs[l_pick]
        existing = _location_availability_options(raw_tree, chosen_l["index"])
        if existing:
            st.caption("Current availability: " + "; ".join(
                _location_period_caption(p) for p in existing))
        from_hour, to_hour = _window_inputs("prism_scn_loc")
        c1, c2 = st.columns([1, 1])
        new_tasks = c1.number_input("New max concurrent tasks", min_value=0, value=0, step=1,
                                    key="prism_scn_loc_tasks")
        set_workers = c2.checkbox("Also set max concurrent workers", value=False,
                                  key="prism_scn_loc_setw")
        new_workers = None
        if set_workers:
            new_workers = st.number_input("New max concurrent workers", min_value=0, value=0,
                                          step=1, key="prism_scn_loc_workers")
        if st.button("Add location what-if", key="prism_scn_loc_add"):
            _apply_scenario(
                session,
                _add_location_change(scenario, baseline, chosen_l["location_id"], from_hour,
                                     new_tasks, to_hour=to_hour,
                                     new_max_concurrent_workers=new_workers),
                success_msg=(f"Scenario: {chosen_l['location_id']} tasks → {int(new_tasks)}"
                             + ("" if new_workers is None else f", workers → {int(new_workers)}")
                             + f" {_window_label(from_hour, to_hour)}."))
            scenario = session.get_scenario()

    for row in _scenario_location_rows(scenario):
        c1, c2 = st.columns([4, 1])
        wtxt = ("" if row["new_max_concurrent_workers"] is None
                else f", workers → {row['new_max_concurrent_workers']}")
        c1.caption(f"• {row['location_id']} tasks → {row['new_max_concurrent_tasks']}{wtxt} "
                   f"{_window_label(row['from_hour'], row['to_hour'])}")
        if c2.button("Remove", key=f"prism_scn_loc_rm_{row['index']}"):
            _apply_scenario(
                session, _remove_location_change(scenario, baseline, row["index"]),
                success_msg=f"Removed location change for {row['location_id']}.")
            scenario = session.get_scenario()
    return scenario


def _render_emergent_task_section(session, baseline, scenario, raw_tree) -> Scenario:
    """Author an emergent (added) task. Limited to the fields materialize writes; no hold point is
    authored here (materialize forces ``is_hold_point:False`` — the schema's vacuous conditional).
    An id colliding with a baseline or already-added task is rejected up front (materialize would
    also reject it → EMERGENT_ID_COLLISION). Referential validity (ghost skill/equipment/location)
    is checked by materialize at Run time."""
    st.markdown("**Add a task**")
    existing_ids = {t["task_id"] for t in _task_options(raw_tree)}
    existing_ids |= {r["task_id"] for r in _scenario_emergent_task_rows(scenario)}

    new_id = st.text_input("New task id", key="prism_scn_act_task_id").strip()
    desc = st.text_input("Description (optional)", key="prism_scn_act_task_desc").strip()
    dur = st.number_input("Duration (hours)", min_value=0.0, value=1.0, step=1.0,
                          key="prism_scn_act_task_dur")

    loc_opts = _location_options(raw_tree)
    loc_labels = ["(no location)"] + [lo["location_id"] for lo in loc_opts]
    loc_pick = st.selectbox("Location", range(len(loc_labels)),
                            format_func=lambda k: loc_labels[k], key="prism_scn_act_task_loc")
    location_id = None if loc_pick == 0 else loc_opts[loc_pick - 1]["location_id"]

    skill_opts = [p["skill_type"] for p in _resource_options(raw_tree)]
    chosen_skills = st.multiselect("Required skills", skill_opts, key="prism_scn_act_task_skills")
    req_resources = [
        (sk, int(st.number_input(f"Crew for {sk}", min_value=1, value=1, step=1,
                                 key=f"prism_scn_act_task_crew_{sk}")))
        for sk in chosen_skills
    ]

    equip_opts = [e["equipment_id"] for e in _equipment_options(raw_tree)]
    chosen_equip = st.multiselect("Required equipment", equip_opts, key="prism_scn_act_task_equip")
    req_equipment = [
        (eq, int(st.number_input(f"Quantity for {eq}", min_value=1, value=1, step=1,
                                 key=f"prism_scn_act_task_eqty_{eq}")))
        for eq in chosen_equip
    ]

    collision = bool(new_id) and new_id in existing_ids
    if collision:
        st.warning(f"Task id “{new_id}” already exists — choose a new id.")
    if st.button("Add task", key="prism_scn_act_task_add", disabled=(not new_id or collision)):
        _apply_scenario(
            session,
            _add_emergent_task(scenario, baseline, new_id, dur, description=desc or None,
                               location_id=location_id, required_resources=req_resources,
                               required_equipment=req_equipment),
            success_msg=f"Scenario: added task {new_id} ({dur:g}h).")
        scenario = session.get_scenario()

    for row in _scenario_emergent_task_rows(scenario):
        c1, c2 = st.columns([4, 1])
        rr = ", ".join(f"{r['skill_type']}×{r['crew_count']}"
                       for r in row["required_resources"]) or "no skills"
        c1.caption(f"• {row['task_id']} ({row['duration']:g}h) — {rr}")
        if c2.button("Remove", key=f"prism_scn_act_task_rm_{row['task_id']}"):
            _apply_scenario(
                session, _remove_emergent_task(scenario, baseline, row["task_id"]),
                success_msg=f"Removed added task {row['task_id']}.")
            scenario = session.get_scenario()
    return scenario


def _render_emergent_dependency_section(session, baseline, scenario) -> Scenario:
    """Author an emergent (added) dependency edge. Endpoints are picked from the MATERIALIZED task
    list, so a task just added in THIS scenario is selectable as an endpoint (that is how an
    emergent task gets its links — materialize hardcodes the new task's own ``successors:[]``)."""
    st.markdown("**Add a dependency**")
    payload, _warning = _current_schedule_payload(baseline, scenario)
    ids = [t["task_id"] for t in _task_options(payload)]
    if len(ids) < 2:
        st.caption("Need at least two tasks to add a dependency.")
    else:
        c1, c2, c3 = st.columns([2, 2, 1])
        pred = c1.selectbox("Predecessor", ids, key="prism_scn_act_dep_pred")
        succ = c2.selectbox("Successor", ids, key="prism_scn_act_dep_succ")
        lag = c3.number_input("Lag (h)", min_value=0.0, value=0.0, step=1.0,
                              key="prism_scn_act_dep_lag")
        if st.button("Add dependency", key="prism_scn_act_dep_add", disabled=(pred == succ)):
            _apply_scenario(
                session, _add_emergent_dependency(scenario, baseline, pred, succ, lag_hours=lag),
                success_msg=f"Scenario: added dependency {pred} → {succ}.")
            scenario = session.get_scenario()
        if pred == succ:
            st.caption("Predecessor and successor must differ.")

    for row in _scenario_emergent_dependency_rows(scenario):
        c1, c2 = st.columns([4, 1])
        lag_txt = "" if not row["lag_hours"] else f" (lag {row['lag_hours']:g}h)"
        c1.caption(f"• {row['predecessor_id']} → {row['successor_id']}{lag_txt}")
        if c2.button("Remove", key=f"prism_scn_act_dep_rm_{row['index']}"):
            _apply_scenario(
                session, _remove_emergent_dependency(scenario, baseline, row["index"]),
                success_msg=(f"Removed added dependency {row['predecessor_id']} → "
                             f"{row['successor_id']}."))
            scenario = session.get_scenario()
    return scenario


def _render_task_suppression_section(session, baseline, scenario) -> Scenario:
    """Suppress (remove) a task from the effective plan. The pick list is the MATERIALIZED task
    list, so an already-suppressed task is gone from it (no double-suppression) and an emergent
    task can also be suppressed. Materialization strips the removed id from every remaining task's
    successors, so no edge is left dangling."""
    st.markdown("**Remove a task**")
    payload, _warning = _current_schedule_payload(baseline, scenario)
    ids = [t["task_id"] for t in _task_options(payload)]
    if not ids:
        st.caption("No tasks to remove.")
    else:
        pick = st.selectbox("Task to remove", ids, key="prism_scn_act_sup_task")
        if st.button("Remove task", key="prism_scn_act_sup_task_add"):
            _apply_scenario(
                session, _add_task_suppression(scenario, baseline, pick),
                success_msg=f"Scenario: task {pick} removed.")
            scenario = session.get_scenario()

    for row in _scenario_task_suppression_rows(scenario):
        c1, c2 = st.columns([4, 1])
        c1.caption(f"• {row['task_id']} — removed")
        if c2.button("Undo", key=f"prism_scn_act_sup_task_rm_{row['task_id']}"):
            _apply_scenario(
                session, _remove_task_suppression(scenario, baseline, row["task_id"]),
                success_msg=f"Restored task {row['task_id']}.")
            scenario = session.get_scenario()
    return scenario


def _render_dependency_suppression_section(session, baseline, scenario) -> Scenario:
    """Suppress (remove) one precedence edge without removing either task. The pick list is the
    MATERIALIZED dependency list (bare-string or object successor forms both surface)."""
    st.markdown("**Remove a dependency**")
    payload, _warning = _current_schedule_payload(baseline, scenario)
    edges = _dependency_options(payload)
    if not edges:
        st.caption("No dependencies to remove.")
    else:
        labels = [f"{e['predecessor']} → {e['successor']}" for e in edges]
        pick = st.selectbox("Dependency to remove", range(len(edges)),
                            format_func=lambda k: labels[k], key="prism_scn_act_sup_dep")
        chosen = edges[pick]
        if st.button("Remove dependency", key="prism_scn_act_sup_dep_add"):
            _apply_scenario(
                session,
                _add_dependency_suppression(scenario, baseline, chosen["predecessor"],
                                            chosen["successor"]),
                success_msg=(f"Scenario: dependency {chosen['predecessor']} → "
                             f"{chosen['successor']} removed."))
            scenario = session.get_scenario()

    for row in _scenario_dependency_suppression_rows(scenario):
        c1, c2 = st.columns([4, 1])
        c1.caption(f"• {row['predecessor_id']} → {row['successor_id']} — removed")
        if c2.button("Undo", key=f"prism_scn_act_sup_dep_rm_{row['index']}"):
            _apply_scenario(
                session, _remove_dependency_suppression(scenario, baseline, row["index"]),
                success_msg=(f"Restored dependency {row['predecessor_id']} → "
                             f"{row['successor_id']}."))
            scenario = session.get_scenario()
    return scenario


def _render_activities_tab(session, baseline, scenario, raw_tree) -> Scenario:
    """Activity what-ifs: ADD emergent tasks & dependencies, and REMOVE (suppress) baseline/emergent
    tasks & dependencies. Add-endpoints and removal pick-lists read the MATERIALIZED current
    schedule so staged adds are linkable and staged removals drop out of the pickers. Returns the
    (possibly updated) scenario so the caller re-reads the overlay after an add/remove."""
    scenario = _render_emergent_task_section(session, baseline, scenario, raw_tree)
    st.divider()
    scenario = _render_emergent_dependency_section(session, baseline, scenario)
    st.divider()
    scenario = _render_task_suppression_section(session, baseline, scenario)
    st.divider()
    scenario = _render_dependency_suppression_section(session, baseline, scenario)
    return scenario


def _render_scenario_panel(session, baseline) -> None:
    """Author a what-if scenario over the CURRENT session baseline. Deltas span several families —
    task duration overrides; availability what-ifs for every time-dependent resource type
    (skills, equipment, locations), each with an optional bounded ``[from, until)`` window; and
    activity what-ifs (add emergent tasks & dependencies, remove tasks & dependencies). A
    *what-if* becomes a Scenario overlay (materialized into the effective plan at Run time and
    tracked by provenance freshness); a *baseline correction* is redirected to the Plan editor,
    never authored here (the domain never guesses intent). Families live on separate tabs
    (Streamlit forbids nested expanders). Empty scenario == plain baseline."""
    with st.expander("What-if scenario (optional)", expanded=False):
        scenario = session.get_scenario()
        raw_tree = json.loads(baseline.raw_snapshot)["payload"]

        if not _scenario_is_empty(scenario) and scenario.base_plan_hash == baseline.plan_hash:
            st.caption(_scenario_summary(scenario))
        else:
            st.caption("No scenario — the run uses the baseline as-is.")

        tab_dur, tab_skill, tab_equip, tab_loc, tab_act = st.tabs(
            ["Task duration", "Skills", "Equipment", "Locations", "Activities"])
        with tab_dur:
            scenario = _render_duration_tab(session, baseline, scenario, raw_tree)
        with tab_skill:
            scenario = _render_skills_tab(session, baseline, scenario, raw_tree)
        with tab_equip:
            scenario = _render_equipment_tab(session, baseline, scenario, raw_tree)
        with tab_loc:
            scenario = _render_locations_tab(session, baseline, scenario, raw_tree)
        with tab_act:
            scenario = _render_activities_tab(session, baseline, scenario, raw_tree)

        # --- reset overlay (keep the named scenario; empty its deltas) --------
        if not _scenario_is_empty(scenario):
            if st.button("Reset overlay", key="prism_scn_reset"):
                session.add_scenario(replace(
                    scenario, duration_overrides=None, resource_changes=None,
                    equipment_changes=None, location_changes=None,
                    hold_point_release_overrides=None,
                    emergent_tasks=None, emergent_dependencies=None,
                    task_suppressions=None, dependency_suppressions=None))
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

def _render_replan(session, baseline, result=None, run_config=None, run_plan=None) -> None:
    """Replan page: two ways to change the plan and re-run.

    (1) **Guided resource augmentation** — auto-build a resource what-if from the SELECTED run's
    bottleneck ranking and verify it by cloning the baseline and re-running both sides. It is
    baseline-relative (needs a completed run, NOT a selected scenario), so it renders ABOVE the
    scenario guard and is always available once a run exists.

    (2) **Manual scenario** — author the SELECTED scenario's overlay (task-duration overrides +
    resource what-ifs) and its execution modes. Requires a scenario picked in the sidebar (or
    created on **Scenarios**); empty-state otherwise. Re-run from the sidebar either way.

    ``result`` / ``run_config`` / ``run_plan`` default to None so a bare ``_render_replan(session,
    baseline)`` still renders the manual half (augmentation then shows its own unwired notice)."""
    st.subheader("Guided resource augmentation")
    _render_run_augmentation(session, baseline, result, run_config, run_plan)

    st.divider()
    st.subheader("Manual scenario")
    if session.get_current_scenario_id() is None:
        st.info("Select a scenario in the sidebar, or create one on the **Scenarios** page, to "
                "author an overlay manually. (The baseline itself is edited on the **Plan** page.)")
        return
    _render_scenario_panel(session, baseline)
    _render_mode_picker(session, baseline)
