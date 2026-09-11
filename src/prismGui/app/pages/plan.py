"""Plan page: read-only data viewer + the structured editor forms."""
from __future__ import annotations

import json
from datetime import date

from prismGui.app._streamlit import st
from prismGui.application import services
from prismGui.domain.plan import PatchAction, PatchOp, ResourceType, apply_patch, discard_draft, open_draft
from prismGui.app.components import _render_issues
from prismGui.app.edit_model import _HOLD_POINT_TYPES, _NO_VALUE, _add_availability_period_patch, _add_consumable_patch, _add_dependency_patch, _add_equipment_availability_patch, _add_equipment_patch, _add_location_availability_patch, _add_location_patch, _add_resource_pool_patch, _add_restock_patch, _add_system_patch, _add_system_state_patch, _add_task_alt_skill_patch, _add_task_consumable_patch, _add_task_equipment_patch, _add_task_mode_equipment_patch, _add_task_mode_patch, _add_task_mode_resource_patch, _add_task_patch, _add_task_resource_patch, _add_task_system_state_patch, _add_task_time_window_patch, _as_date, _as_float, _as_str, _availability_options, _availability_window_patch, _available_count_patch, _consumable_options, _consumable_total_patch, _dependency_options, _description_patch, _duration_patch, _equipment_availability_options, _equipment_options, _equipment_quantity_patch, _equipment_window_patch, _iso_date_value, _location_availability_options, _location_capacity_patch, _location_options, _location_window_patch, _patch_rows, _remove_availability_period_patch, _remove_consumable_patch, _remove_dependency_patch, _remove_equipment_availability_patch, _remove_equipment_patch, _remove_location_availability_patch, _remove_location_patch, _remove_resource_pool_patch, _remove_restock_patch, _remove_system_patch, _remove_system_state_patch, _remove_task_alt_skill_patch, _remove_task_consumable_patch, _remove_task_equipment_patch, _remove_task_mode_equipment_patch, _remove_task_mode_patch, _remove_task_mode_resource_patch, _remove_task_patch, _remove_task_resource_patch, _remove_task_system_state_patch, _remove_task_time_window_patch, _resource_options, _resource_type_patch, _restock_edit_patch, _restock_options, _system_options, _system_state_options, _task_consumable_reqs, _task_equipment_reqs, _task_hold_point, _task_hold_point_clear_patch, _task_hold_point_set_patch, _task_ids, _task_location, _task_location_clear_patch, _task_location_patch, _task_mode_dose_clear_patch, _task_mode_dose_patch, _task_mode_duration_patch, _task_mode_mob_clear_patch, _task_mode_mob_patch, _task_modes, _task_options, _task_resource_crew_patch, _task_resource_reqs, _task_resource_skill_patch, _task_system_state_reqs, _task_time_window_edit_patch, _task_time_windows
from prismGui.app.scenario_model import _current_schedule_payload, _schedule_label
from prismGui.app.view_data import _data_viewer_rows


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

# --- workflow pages -------------------------------------------------------------------------
# One render wrapper per navigation page (Plan / Results / Replan / Scenarios). Each takes the
# prologue locals as ordinary arguments and delegates to the existing single-purpose renderers;
# main()'s zero-arg st.Page closures are the only callers. Keeping them as plain module-level
# functions (not test-referenced) is the seam Phase 2 will cut along when lifting pages into
# their own modules under app/.

def _render_plan_page(session, baseline, validator) -> None:
    """Plan page: the baseline plan itself. A light segmented switch chooses the read-only data
    *Overview* or the full structural *Edit plan* editor (tasks, constraints, resources, …).
    Editing here always targets the baseline; scenario what-ifs live on the Replan page."""
    view = st.segmented_control(
        "View", ["Overview", "Edit plan"], default="Overview", key="prism_plan_view")
    if view == "Edit plan":
        _render_editor(session, validator)
    else:
        _render_data_viewer(session, baseline)
