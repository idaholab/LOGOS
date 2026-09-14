"""Pure editor builders: value coercers, option readers, and PatchOp builders."""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity
from prismGui.domain.plan import PatchAction, PatchOp, ResourceType


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

def _mode_options(raw_tree) -> list[dict]:
    """Per-task execution-mode rows for the run-time mode picker — only tasks that define MORE
    THAN ONE mode (a task with zero or one mode offers no choice, so it is omitted). Each row is
    ``{task_id, modes}`` where ``modes`` is ``[{mode_name, duration}, …]`` (``mode_name`` is the
    schema's ``mode_id``, matching ``ModeSelection``/``validate_run_config``). Empty for every
    shipping sample (none defines modes). Streamlit-free."""
    rows: list[dict] = []
    for t in (raw_tree.get("tasks") or []):
        modes = t.get("modes") or []
        if len(modes) > 1:
            rows.append({
                "task_id": t.get("task_id", ""),
                "modes": [{"mode_name": m.get("mode_id", ""), "duration": m.get("duration")}
                          for m in modes],
            })
    return rows

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

def _resource_dose_budget(raw_tree, resource_index: int) -> Optional[float]:
    """The current ``dose_budget_per_worker_mrem`` of pool #resource_index (None if absent or out of
    range) -- the pre-fill reader for the dose-budget widget. Dedicated reader, not a key on
    ``_resource_options``, whose exact shape a contract test pins."""
    resources = raw_tree.get("resources") or []
    if resource_index < 0 or resource_index >= len(resources):
        return None
    return resources[resource_index].get("dose_budget_per_worker_mrem")

def _resource_dose_budget_patch(resource_index: int, dose_budget: float) -> PatchOp:
    """ADD (set-or-create) a pool's optional ``dose_budget_per_worker_mrem``. ADD, not REPLACE: the
    field is optional and shipping pools omit it (the ``_resource_type_patch`` precedent). Only
    meaningful when the pool is ``consumable`` (schema note), so the widget gates on that; a negative
    value BLOCKS at commit (SCHEMA_RANGE_ERROR -- minimum 0)."""
    return PatchOp(action=PatchAction.ADD,
                   path=f"/resources/{resource_index}/dose_budget_per_worker_mrem",
                   value=float(dose_budget))

def _resource_dose_budget_clear_patch(resource_index: int) -> PatchOp:
    """REMOVE a pool's ``dose_budget_per_worker_mrem`` -- the only way to clear it (a set-to-null is
    impossible; ``apply_patch`` rejects a None value). Valid only when the key is present (the wrapper
    gates on the current budget)."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/resources/{resource_index}/dose_budget_per_worker_mrem")

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

def _equipment_zone(raw_tree, equip_index: int) -> Optional[str]:
    """The current ``zone_id`` (zone affinity) of equipment #equip_index (None if absent or out of
    range) -- the pre-fill reader for the zone-affinity selector. Dedicated reader, not a key on
    ``_equipment_options``, whose exact shape a contract test pins."""
    equipment = raw_tree.get("equipment") or []
    if equip_index < 0 or equip_index >= len(equipment):
        return None
    return equipment[equip_index].get("zone_id")

def _equipment_zone_patch(equip_index: int, zone_id: str) -> PatchOp:
    """ADD (set-or-create) ``/equipment/{i}/zone_id`` (a zone affinity, the ``_resource_type_patch``
    precedent: optional, shipping items omit it). The picker offers only declared ``location_id``s
    (equipment ``zone_id`` shares the location namespace task ``zone_ids`` reference), so a blank
    ``minLength 1`` value never arises through the UI."""
    return PatchOp(action=PatchAction.ADD, path=f"/equipment/{equip_index}/zone_id",
                   value=str(zone_id))

def _equipment_zone_clear_patch(equip_index: int) -> PatchOp:
    """REMOVE ``/equipment/{i}/zone_id`` -- the only way to clear the zone affinity (a set-to-null is
    impossible; ``apply_patch`` rejects a None value). Clearing makes the equipment usable from any
    zone. Valid only when the key is present (the wrapper gates on the current zone)."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/equipment/{equip_index}/zone_id")

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

def _array_add_op(list_pointer: str, present: bool, item) -> PatchOp:
    """ADD an item to a JSON-Pointer-addressed list that MAY NOT EXIST YET. When the list is
    already present (even if empty) append with the trailing ``-``; otherwise create it with a
    one-element list. This is the create-or-append the not-root-required ``consumables`` /
    ``plant_systems`` arrays (and their optional ``restocks`` / ``valid_states`` sub-arrays) need —
    every shipping sample omits them, so an "add first item" cannot assume ``{pointer}/-`` resolves.
    Callers pass ``present = isinstance(<container>.get(<key>), list)``."""
    if present:
        return PatchOp(action=PatchAction.ADD, path=f"{list_pointer}/-", value=item)
    return PatchOp(action=PatchAction.ADD, path=list_pointer, value=[item])

def _consumable_options(raw_tree) -> list[dict]:
    """Per-consumable selector rows ``{index, item_id, description, total_quantity, n_restocks}``."""
    return [
        {
            "index": i,
            "item_id": c.get("item_id", ""),
            "description": c.get("description", ""),
            "total_quantity": c.get("total_quantity"),
            "n_restocks": len(c.get("restocks") or []),
        }
        for i, c in enumerate(raw_tree.get("consumables") or [])
    ]

def _restock_options(raw_tree, cons_index: int) -> list[dict]:
    """Restock-delivery rows ``{index, delivery_hour, quantity}`` for one consumable (empty if the
    index is out of range). ``delivery_hour`` is hours from outage start, not a date."""
    consumables = raw_tree.get("consumables") or []
    if cons_index < 0 or cons_index >= len(consumables):
        return []
    restocks = consumables[cons_index].get("restocks") or []
    return [
        {"index": j, "delivery_hour": d.get("delivery_hour"), "quantity": d.get("quantity")}
        for j, d in enumerate(restocks)
    ]

def _add_consumable_patch(raw_tree, item_id: str, description: str,
                          total_quantity: float) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the ADD op for a schema-complete consumable with its required ``item_id`` /
    ``description`` / ``total_quantity`` (no restocks initially). ``consumables`` is not
    root-required and every sample omits it, so create-or-append via ``_array_add_op``. Returns
    ``(None, [issue])`` (DUP_ID) when the id is blank or already an item. A ``total_quantity <= 0``
    stages and is blocked by commit (SCHEMA_RANGE_ERROR — the schema's ``exclusiveMinimum: 0``)."""
    iid = (item_id or "").strip()
    if not iid:
        return None, [_dup_id("consumable", iid, "a new consumable needs a non-empty item_id")]
    existing = {c.get("item_id") for c in (raw_tree.get("consumables") or [])}
    if iid in existing:
        return None, [_dup_id("consumable", iid, f"a consumable '{iid}' already exists")]
    item = {"item_id": iid, "description": description, "total_quantity": float(total_quantity)}
    present = isinstance(raw_tree.get("consumables"), list)
    return _array_add_op("/consumables", present, item), []

def _remove_consumable_patch(cons_index: int) -> PatchOp:
    """REMOVE the consumable at ``cons_index``. Unlike a resource pool / equipment / location, a
    task's ``required_consumables`` is NOT referentially validated by the CPM validator, so this
    never blocks with REF_MISSING even if a task still names the removed item."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/consumables/{cons_index}")

def _consumable_total_patch(cons_index: int, total_quantity: float) -> PatchOp:
    """REPLACE the always-present ``total_quantity`` of one consumable. A value <= 0 is blocked by
    commit (SCHEMA_RANGE_ERROR)."""
    return PatchOp(action=PatchAction.REPLACE, path=f"/consumables/{cons_index}/total_quantity",
                   value=float(total_quantity))

def _add_restock_patch(raw_tree, cons_index: int, delivery_hour: float,
                       quantity: float) -> PatchOp:
    """ADD a ``{delivery_hour, quantity}`` restock delivery to the consumable at ``cons_index``.
    ``restocks`` is optional and often absent, so create-or-append via ``_array_add_op``. A
    ``quantity <= 0`` (or ``delivery_hour < 0``) is blocked by commit (SCHEMA_RANGE_ERROR)."""
    consumables = raw_tree.get("consumables") or []
    present = (0 <= cons_index < len(consumables)
               and isinstance(consumables[cons_index].get("restocks"), list))
    return _array_add_op(f"/consumables/{cons_index}/restocks", present,
                         {"delivery_hour": float(delivery_hour), "quantity": float(quantity)})

def _remove_restock_patch(cons_index: int, restock_index: int) -> PatchOp:
    """REMOVE one restock delivery (by index) from the consumable at ``cons_index``."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/consumables/{cons_index}/restocks/{restock_index}")

def _restock_edit_patch(cons_index: int, restock_index: int, delivery_hour: float,
                        quantity: float) -> list[PatchOp]:
    """Edit one restock delivery: REPLACE its required ``delivery_hour`` and ``quantity`` (both are
    restock-required, so present -> REPLACE). A ``quantity <= 0`` / ``delivery_hour < 0`` is blocked
    by commit (SCHEMA_RANGE_ERROR)."""
    base = f"/consumables/{cons_index}/restocks/{restock_index}"
    return [
        PatchOp(action=PatchAction.REPLACE, path=f"{base}/delivery_hour",
                value=float(delivery_hour)),
        PatchOp(action=PatchAction.REPLACE, path=f"{base}/quantity", value=float(quantity)),
    ]

def _system_options(raw_tree) -> list[dict]:
    """Per-plant-system selector rows ``{index, system_id, description, n_states}``."""
    return [
        {
            "index": i,
            "system_id": s.get("system_id", ""),
            "description": s.get("description", ""),
            "n_states": len(s.get("valid_states") or []),
        }
        for i, s in enumerate(raw_tree.get("plant_systems") or [])
    ]

def _system_state_options(raw_tree, sys_index: int) -> list[dict]:
    """Valid-state rows ``{index, state}`` for one plant system (empty if the index is out of
    range)."""
    systems = raw_tree.get("plant_systems") or []
    if sys_index < 0 or sys_index >= len(systems):
        return []
    states = systems[sys_index].get("valid_states") or []
    return [{"index": j, "state": s} for j, s in enumerate(states)]

def _add_system_patch(raw_tree, system_id: str,
                      description: str) -> tuple[Optional[PatchOp], list[Issue]]:
    """Build the ADD op for a schema-complete plant system with its required ``system_id`` /
    ``description`` (no valid_states initially). ``plant_systems`` is not root-required and every
    sample omits it, so create-or-append via ``_array_add_op``. Returns ``(None, [issue])`` (DUP_ID)
    when the id is blank or already a system."""
    sid = (system_id or "").strip()
    if not sid:
        return None, [_dup_id("system", sid, "a new plant system needs a non-empty system_id")]
    existing = {s.get("system_id") for s in (raw_tree.get("plant_systems") or [])}
    if sid in existing:
        return None, [_dup_id("system", sid, f"a plant system '{sid}' already exists")]
    item = {"system_id": sid, "description": description}
    present = isinstance(raw_tree.get("plant_systems"), list)
    return _array_add_op("/plant_systems", present, item), []

def _remove_system_patch(sys_index: int) -> PatchOp:
    """REMOVE the plant system at ``sys_index``. Like consumables, a task's
    ``required_system_states`` is NOT referentially validated, so this never blocks with
    REF_MISSING even if a task still names the removed system."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/plant_systems/{sys_index}")

def _add_system_state_patch(raw_tree, sys_index: int,
                            state: str) -> tuple[Optional[PatchOp], list[Issue]]:
    """ADD one ``valid_states`` string to the plant system at ``sys_index``. ``valid_states`` is
    optional and may be absent, so create-or-append via ``_array_add_op``. Rejects a blank or
    duplicate state up-front (DUP_ID-coded, matching the blank-id precedent) so it never reaches
    the schema's ``uniqueItems`` / ``minLength`` check."""
    sval = (state or "").strip()
    systems = raw_tree.get("plant_systems") or []
    if not sval:
        return None, [_dup_id("system", sval, "a valid state must be a non-empty string")]
    if 0 <= sys_index < len(systems):
        existing = set(systems[sys_index].get("valid_states") or [])
        if sval in existing:
            return None, [_dup_id("system", sval, f"state '{sval}' is already listed")]
    present = (0 <= sys_index < len(systems)
               and isinstance(systems[sys_index].get("valid_states"), list))
    return _array_add_op(f"/plant_systems/{sys_index}/valid_states", present, sval), []

def _remove_system_state_patch(sys_index: int, state_index: int) -> PatchOp:
    """REMOVE one valid-state (by index) from the plant system at ``sys_index``."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/plant_systems/{sys_index}/valid_states/{state_index}")

# -----------------------------------------------------------------------------
# Increment 6: task requirement wiring. Point a task at the entities the other
# tabs author -- its location_id, required_equipment, required_consumables,
# required_system_states, and required_resources beyond the one seeded at add.
# Same discipline: pure data -> PatchOp builders over the /tasks/{i}/... loader
# paths, fed through domain.apply_patch, blocked (never crashed) at commit on a
# bad edit. THE REF_MISSING ASYMMETRY (validate_outage_data.py): the CPM validator
# checks only three of the five references -- location_id,
# required_equipment[].equipment_id and required_resources[].skill_type must
# resolve to a defined pool (else REF_MISSING at commit); required_consumables
# [].item_id, required_system_states[].system_id and alternative_skill_types are
# NOT validated, so a task may name a ghost of those without blocking. Builders do
# NOT re-check refs up-front (the wrappers' pickers offer only existing entities);
# the commit validator is the single arbiter, so a bad ref -- reached by removing
# a wired entity, or via the raw editor -- is what exercises the REF_MISSING
# binding for tasks. location_id is nullable, but apply_patch rejects a None value,
# so CLEARING it is a REMOVE (present-only), not a set-to-null; SETTING it is an
# ADD (set-or-create). The optional sub-arrays (required_consumables /
# required_system_states / alternative_skill_types) start absent, so adds go
# through the Inc-5 _array_add_op create-or-append helper.
# -----------------------------------------------------------------------------

def _task_at(raw_tree, task_index: int) -> Optional[dict]:
    """The task dict at ``task_index`` in the raw ``tasks`` list, or None if out of range -- the
    shared bounds guard the per-task requirement readers use (read-only; never mutated here)."""
    tasks = raw_tree.get("tasks") or []
    if task_index < 0 or task_index >= len(tasks):
        return None
    return tasks[task_index]

def _task_location(raw_tree, task_index: int) -> Optional[str]:
    """The current ``location_id`` of task #task_index (None if absent/null or out of range)."""
    task = _task_at(raw_tree, task_index)
    return task.get("location_id") if task else None

def _task_equipment_reqs(raw_tree, task_index: int) -> list[dict]:
    """``required_equipment`` rows ``{index, equipment_id, quantity_needed}`` for one task
    (empty if out of range)."""
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_equipment") or []
    return [
        {"index": j, "equipment_id": e.get("equipment_id", ""),
         "quantity_needed": e.get("quantity_needed")}
        for j, e in enumerate(reqs)
    ]

def _task_consumable_reqs(raw_tree, task_index: int) -> list[dict]:
    """``required_consumables`` rows ``{index, item_id, quantity_needed}`` for one task
    (empty if out of range)."""
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_consumables") or []
    return [
        {"index": j, "item_id": c.get("item_id", ""), "quantity_needed": c.get("quantity_needed")}
        for j, c in enumerate(reqs)
    ]

def _task_system_state_reqs(raw_tree, task_index: int) -> list[dict]:
    """``required_system_states`` rows ``{index, system_id, required_state}`` for one task
    (empty if out of range)."""
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_system_states") or []
    return [
        {"index": j, "system_id": s.get("system_id", ""),
         "required_state": s.get("required_state", "")}
        for j, s in enumerate(reqs)
    ]

def _task_resource_reqs(raw_tree, task_index: int) -> list[dict]:
    """``required_resources`` rows ``{index, skill_type, crew_count, alternatives}`` for one task
    (empty if out of range). ``alternatives`` is the entry's ``alternative_skill_types`` list."""
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_resources") or []
    return [
        {"index": j, "skill_type": r.get("skill_type", ""), "crew_count": r.get("crew_count"),
         "alternatives": list(r.get("alternative_skill_types") or [])}
        for j, r in enumerate(reqs)
    ]

# --- location_id (a nullable single value, not a list) ---

def _task_location_patch(task_index: int, location_id: str) -> PatchOp:
    """ADD (set-or-create) ``/tasks/{i}/location_id`` to a zone id. ADD, not REPLACE: the field is
    optional and a task may not carry it yet. REF_MISSING-bound -- if the zone is not a defined
    location, commit BLOCKS (REF_MISSING); the picker offers only existing zones, so a bad ref
    arises only by removing the zone later (or via the raw editor)."""
    return PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/location_id",
                   value=str(location_id))

def _task_location_clear_patch(task_index: int) -> PatchOp:
    """REMOVE ``/tasks/{i}/location_id`` -- the ONLY way to clear it, since ``location_id`` is
    nullable but ``apply_patch`` rejects a None value (a set-to-null is impossible). Valid only when
    the key is present (the wrapper gates on a current location)."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/tasks/{task_index}/location_id")

# --- zone_ids (a multi-zone list; SET is a whole-list ADD, CLEAR is REMOVE) ---

def _task_zones(raw_tree, task_index: int) -> list[str]:
    """The current ``zone_ids`` of task #task_index (``[]`` if absent or out of range) -- the
    multiselect pre-fill reader. Multi-zone occupancy (Option C): each entry references a declared
    ``location_id``; falls back to ``[location_id]`` when omitted."""
    return list((_task_at(raw_tree, task_index) or {}).get("zone_ids") or [])

def _task_zones_patch(task_index: int, zone_ids) -> PatchOp:
    """ADD (set-or-create) the WHOLE ``/tasks/{i}/zone_ids`` list at once (a multiselect yields the
    full set). ADD, not REPLACE: the field is optional and tasks omit it. Each id must reference a
    declared ``location_id`` (the picker offers only those), else commit BLOCKS (REF_MISSING); an
    empty list clears via ``_task_zones_clear_patch`` instead (absent == empty)."""
    return PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/zone_ids",
                   value=[str(z) for z in zone_ids])

def _task_zones_clear_patch(task_index: int) -> PatchOp:
    """REMOVE ``/tasks/{i}/zone_ids`` -- clearing multi-zone occupancy (the task falls back to
    ``[location_id]``). Valid only when the key is present (the wrapper gates on current zones)."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/tasks/{task_index}/zone_ids")

# --- dose_rate_mrem_per_hour (the task-level default; a mode may override it) ---

def _task_dose(raw_tree, task_index: int) -> Optional[float]:
    """The current task-level ``dose_rate_mrem_per_hour`` of task #task_index (None if absent or out
    of range) -- the pre-fill reader for the task dose widget. A per-mode override lives separately
    at ``/tasks/{i}/modes/{m}/dose_rate_mrem_per_hour`` (``_task_mode_dose_patch``)."""
    task = _task_at(raw_tree, task_index)
    return task.get("dose_rate_mrem_per_hour") if task else None

def _task_dose_patch(task_index: int, dose_rate: float) -> PatchOp:
    """ADD (set-or-create) the task-level ``dose_rate_mrem_per_hour`` (each worker's per-hour dose on
    this task). ADD, not REPLACE: optional, tasks omit it. Same builder as the per-mode override
    minus the ``/modes/{m}`` segment; a negative value BLOCKS at commit (SCHEMA_RANGE_ERROR --
    minimum 0)."""
    return PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/dose_rate_mrem_per_hour",
                   value=float(dose_rate))

def _task_dose_clear_patch(task_index: int) -> PatchOp:
    """REMOVE the task-level ``dose_rate_mrem_per_hour`` -- the only way to clear it (a set-to-null is
    impossible). Valid only when the key is present (the wrapper gates on the current dose)."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/tasks/{task_index}/dose_rate_mrem_per_hour")

# --- required_equipment (REF_MISSING-bound) ---

def _add_task_equipment_patch(raw_tree, task_index: int, equipment_id: str,
                              quantity_needed: int) -> PatchOp:
    """Create-or-append a ``{equipment_id, quantity_needed}`` requirement to task #task_index's
    ``required_equipment`` (always present -- seeded ``[]`` by _add_task_patch -- but guarded via
    _array_add_op for a raw-edited draft). REF_MISSING-bound: a ghost equipment_id BLOCKS at commit."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("required_equipment"), list)
    return _array_add_op(f"/tasks/{task_index}/required_equipment", present,
                         {"equipment_id": str(equipment_id),
                          "quantity_needed": int(quantity_needed)})

def _remove_task_equipment_patch(task_index: int, req_index: int) -> PatchOp:
    """REMOVE one ``required_equipment`` entry (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/required_equipment/{req_index}")

# --- required_consumables (NOT referentially validated -- the asymmetry) ---

def _add_task_consumable_patch(raw_tree, task_index: int, item_id: str,
                               quantity_needed: float) -> PatchOp:
    """Create-or-append an ``{item_id, quantity_needed}`` requirement to task #task_index's
    ``required_consumables`` (optional; starts absent). NOT referentially validated -- a ghost
    item_id COMMITS cleanly (unlike equipment). A ``quantity_needed <= 0`` (schema exclusiveMinimum
    0) BLOCKS at commit (SCHEMA_RANGE_ERROR)."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("required_consumables"), list)
    return _array_add_op(f"/tasks/{task_index}/required_consumables", present,
                         {"item_id": str(item_id), "quantity_needed": float(quantity_needed)})

def _remove_task_consumable_patch(task_index: int, req_index: int) -> PatchOp:
    """REMOVE one ``required_consumables`` entry (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/required_consumables/{req_index}")

# --- required_system_states (NOT referentially validated -- the asymmetry) ---

def _add_task_system_state_patch(raw_tree, task_index: int, system_id: str,
                                 required_state: str) -> PatchOp:
    """Create-or-append a ``{system_id, required_state}`` requirement to task #task_index's
    ``required_system_states`` (optional; starts absent). NOT referentially validated -- a ghost
    system_id COMMITS cleanly. The wrapper picks required_state from the system's declared
    valid_states, so a blank required_state does not arise through the UI (a blank would otherwise
    hit the schema's minLength 1 at commit)."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("required_system_states"), list)
    return _array_add_op(f"/tasks/{task_index}/required_system_states", present,
                         {"system_id": str(system_id), "required_state": str(required_state)})

def _remove_task_system_state_patch(task_index: int, req_index: int) -> PatchOp:
    """REMOVE one ``required_system_states`` entry (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/required_system_states/{req_index}")

# --- required_resources (edit beyond the initial add) ---

def _add_task_resource_patch(raw_tree, task_index: int, skill_type: str,
                             crew_count: int) -> PatchOp:
    """Create-or-append a ``{skill_type, crew_count}`` resource requirement to task #task_index's
    ``required_resources`` (always present -- guarded via _array_add_op for a raw-edited draft).
    ``skill_type`` is REF_MISSING-bound: a ghost skill BLOCKS at commit."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("required_resources"), list)
    return _array_add_op(f"/tasks/{task_index}/required_resources", present,
                         {"skill_type": str(skill_type), "crew_count": int(crew_count)})

def _remove_task_resource_patch(task_index: int, req_index: int) -> PatchOp:
    """REMOVE one ``required_resources`` entry (by index) from task #task_index. A task with no
    resources is schema-valid (the array may be empty) but unschedulable -- a modeling choice, not
    a commit block."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/required_resources/{req_index}")

def _task_resource_crew_patch(task_index: int, req_index: int, crew_count: int) -> PatchOp:
    """REPLACE the always-present ``crew_count`` of one resource requirement (a value < 1 BLOCKS at
    commit -- schema minimum 1)."""
    return PatchOp(action=PatchAction.REPLACE,
                   path=f"/tasks/{task_index}/required_resources/{req_index}/crew_count",
                   value=int(crew_count))

def _task_resource_skill_patch(task_index: int, req_index: int, skill_type: str) -> PatchOp:
    """REPLACE the always-present ``skill_type`` of one resource requirement. REF_MISSING-bound: a
    ghost skill BLOCKS at commit."""
    return PatchOp(action=PatchAction.REPLACE,
                   path=f"/tasks/{task_index}/required_resources/{req_index}/skill_type",
                   value=str(skill_type))

def _add_task_alt_skill_patch(raw_tree, task_index: int, req_index: int,
                              skill_type: str) -> tuple[Optional[PatchOp], list[Issue]]:
    """Create-or-append one ``alternative_skill_types`` string to a resource requirement
    (``alternative_skill_types`` is optional and starts absent). Rejects a blank or duplicate value
    up-front (DUP_ID-coded, matching _add_system_state_patch) so it never reaches the schema's
    uniqueItems / minLength check. NOT referentially validated -- the validator checks only the
    primary skill_type, so a ghost alternative COMMITS cleanly."""
    alt = (skill_type or "").strip()
    if not alt:
        return None, [_dup_id("resource", alt, "an alternative skill must be a non-empty string")]
    reqs = (_task_at(raw_tree, task_index) or {}).get("required_resources") or []
    present = False
    if 0 <= req_index < len(reqs):
        existing = set(reqs[req_index].get("alternative_skill_types") or [])
        if alt in existing:
            return None, [_dup_id("resource", alt, f"alternative skill '{alt}' is already listed")]
        present = isinstance(reqs[req_index].get("alternative_skill_types"), list)
    return _array_add_op(
        f"/tasks/{task_index}/required_resources/{req_index}/alternative_skill_types",
        present, alt), []

def _remove_task_alt_skill_patch(task_index: int, req_index: int, alt_index: int) -> PatchOp:
    """REMOVE one ``alternative_skill_types`` string (by index) from a resource requirement."""
    return PatchOp(
        action=PatchAction.REMOVE,
        path=f"/tasks/{task_index}/required_resources/{req_index}"
             f"/alternative_skill_types/{alt_index}")

# -----------------------------------------------------------------------------
# Increment 7 -- per-task SCHEDULING attributes: hold points, time windows, and
# execution modes (incl. each mode's nested required_resources / required_equipment
# and its optional dose_rate / mobilization_lead overrides). These three schema
# sub-structures were previously reachable only through the raw JSON-Pointer editor.
#
# The hold-point PAIR is managed together so the GUI never emits either state the
# schema/runtime reject: is_hold_point==true + a null type -> a WARNING; is_hold_point
# ==false + a type present -> a HOLD_POINT_MISUSE ERROR. A single selectbox drives it
# -- a real type SETS the pair, "none" CLEARS it (removing the type and any blocks_tasks).
#
# The emitted keys are the JSON schema names, NOT the domain names the loader bridges
# to: execution modes live under `modes` (Task.execution_modes) and a mode's id is
# `mode_id` (ExecutionMode.mode_name). A mode item is additionalProperties:false with
# required [mode_id, duration, required_resources, required_equipment], so an added mode
# MUST carry all four -- the two nested arrays seeded []. Both loaders are required-indexed
# (`float(w["earliest"])`, `m["mode_id"]`/`float(m["duration"])`), so a partial/blank row
# would crash the commit rehydrate; the atomic add forms collect every required field at
# once (number_inputs default to valid numbers), so no partial row is ever staged.
#
# ASYMMETRY (as with Inc-6 consumables/system-states): the CPM validator's referential
# checks do NOT descend into modes[].required_resources / required_equipment, so a ghost
# skill_type / equipment_id inside a mode COMMITS cleanly -- the pickers (offering only
# existing pools) are the only guard. time_windows and hold-point fields reference no ids.
# -----------------------------------------------------------------------------

_HOLD_POINT_TYPES = ("NRC", "QA", "Engineering", "Operations")   # the schema enum (minus null)

def _task_hold_point(raw_tree, task_index: int) -> dict:
    """The hold-point pair of task #task_index as ``{is_hold_point, hold_point_type}``
    (``{False, None}`` when absent or out of range). ``is_hold_point`` is coerced to a bool so
    an absent key reads False (the vacuous-conditional escape every plain task relies on);
    ``hold_point_type`` is the raw enum value or None."""
    task = _task_at(raw_tree, task_index) or {}
    return {"is_hold_point": bool(task.get("is_hold_point")),
            "hold_point_type": task.get("hold_point_type")}

def _task_time_windows(raw_tree, task_index: int) -> list[dict]:
    """``time_windows`` rows ``{index, earliest, latest}`` for one task (empty if out of range).
    Both bounds are hours from outage start, NOT dates."""
    windows = (_task_at(raw_tree, task_index) or {}).get("time_windows") or []
    return [
        {"index": j, "earliest": w.get("earliest"), "latest": w.get("latest")}
        for j, w in enumerate(windows)
    ]

def _task_modes(raw_tree, task_index: int) -> list[dict]:
    """``modes`` rows for one task (empty if out of range), each ``{index, mode_id, duration,
    dose_rate, mobilization_lead_hours, resources, equipment}`` where ``resources`` is
    ``[{index, skill_type, crew_count}]`` and ``equipment`` is ``[{index, equipment_id,
    quantity_needed}]``. The nested lists are read HERE (before the per-mode add buttons in the
    wrapper), so a just-added per-mode resource/equipment appears only on the next rerun -- the
    same benign one-run lag as the Inc-6 alt-skills sub-editor."""
    modes = (_task_at(raw_tree, task_index) or {}).get("modes") or []
    return [
        {
            "index": j,
            "mode_id": m.get("mode_id", ""),
            "duration": m.get("duration"),
            "dose_rate": m.get("dose_rate_mrem_per_hour"),
            "mobilization_lead_hours": m.get("mobilization_lead_hours"),
            "resources": [
                {"index": k, "skill_type": r.get("skill_type", ""),
                 "crew_count": r.get("crew_count")}
                for k, r in enumerate(m.get("required_resources") or [])
            ],
            "equipment": [
                {"index": k, "equipment_id": e.get("equipment_id", ""),
                 "quantity_needed": e.get("quantity_needed")}
                for k, e in enumerate(m.get("required_equipment") or [])
            ],
        }
        for j, m in enumerate(modes)
    ]

# --- hold points (the paired conditional -- managed together, never a misuse state) ---

def _task_hold_point_set_patch(task_index: int, hold_point_type: str) -> list[PatchOp]:
    """Make task #task_index a hold point of ``hold_point_type``: ADD (set-or-create)
    ``is_hold_point`` True AND ``hold_point_type``. ADD (not REPLACE) so it works whether or not
    the keys preexist (a sample task may omit ``is_hold_point``). A type outside the schema enum
    BLOCKS at commit (SCHEMA_TYPE_ERROR); the selectbox offers only enum members, so that arises
    only via the raw editor."""
    return [
        PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/is_hold_point", value=True),
        PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/hold_point_type",
                value=str(hold_point_type)),
    ]

def _task_hold_point_clear_patch(raw_tree, task_index: int) -> list[PatchOp]:
    """Clear task #task_index's hold point: ADD (set-or-create) ``is_hold_point`` False and
    REMOVE ``hold_point_type`` / ``blocks_tasks`` when present. Leaving the flag False while a
    type lingered would be a HOLD_POINT_MISUSE error, so the type (and any blocked-task list) is
    always removed with it -- the GUI never leaves the false+type misuse state. The REMOVEs are
    guarded on key presence (a REMOVE of an absent key would fail)."""
    task = _task_at(raw_tree, task_index) or {}
    ops = [PatchOp(action=PatchAction.ADD, path=f"/tasks/{task_index}/is_hold_point", value=False)]
    if "hold_point_type" in task:
        ops.append(PatchOp(action=PatchAction.REMOVE,
                           path=f"/tasks/{task_index}/hold_point_type"))
    if "blocks_tasks" in task:
        ops.append(PatchOp(action=PatchAction.REMOVE, path=f"/tasks/{task_index}/blocks_tasks"))
    return ops

# --- time windows (hour-offset execution windows; NOT referentially validated) ---

def _add_task_time_window_patch(raw_tree, task_index: int, earliest: float,
                                latest: float) -> PatchOp:
    """Create-or-append an ``{earliest, latest}`` execution window (hours from outage start) to
    task #task_index's ``time_windows`` (optional; starts absent). Both bounds are schema
    ``minimum: 0``; a negative bound BLOCKS at commit (SCHEMA_RANGE_ERROR), though the
    ``number_input``'s ``min_value=0`` keeps it non-negative through the UI. ``earliest <= latest``
    is enforced by neither schema nor validator (the scheduler's concern)."""
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("time_windows"), list)
    return _array_add_op(f"/tasks/{task_index}/time_windows", present,
                         {"earliest": float(earliest), "latest": float(latest)})

def _remove_task_time_window_patch(task_index: int, win_index: int) -> PatchOp:
    """REMOVE one ``time_windows`` entry (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/time_windows/{win_index}")

def _task_time_window_edit_patch(task_index: int, win_index: int, earliest: float,
                                 latest: float) -> list[PatchOp]:
    """Edit one window: REPLACE its required ``earliest`` and ``latest`` (both are
    window-required, so present -> REPLACE). A negative bound BLOCKS at commit
    (SCHEMA_RANGE_ERROR)."""
    base = f"/tasks/{task_index}/time_windows/{win_index}"
    return [
        PatchOp(action=PatchAction.REPLACE, path=f"{base}/earliest", value=float(earliest)),
        PatchOp(action=PatchAction.REPLACE, path=f"{base}/latest", value=float(latest)),
    ]

# --- execution modes (mode CRUD + nested crew/equipment + optional overrides) ---

def _add_task_mode_patch(raw_tree, task_index: int, mode_id: str,
                         duration: float) -> tuple[Optional[PatchOp], list[Issue]]:
    """Create-or-append a schema-complete execution mode to task #task_index's ``modes``
    (optional; starts absent). The mode carries all four required keys -- ``mode_id``,
    ``duration``, and the two nested arrays seeded ``[]`` (per-mode crew/equipment are added by
    their own sub-editors). Rejects a blank or duplicate ``mode_id`` up-front (DUP_ID, the
    ``uniqueItems`` analogue) so it never reaches the schema. A ``duration <= 0`` (exclusiveMinimum
    0) stages and BLOCKS at commit (SCHEMA_RANGE_ERROR)."""
    mid = (mode_id or "").strip()
    if not mid:
        return None, [_dup_id("mode", mid, "a new mode needs a non-empty mode_id")]
    existing = {m.get("mode_id")
                for m in ((_task_at(raw_tree, task_index) or {}).get("modes") or [])}
    if mid in existing:
        return None, [_dup_id("mode", mid, f"a mode '{mid}' already exists on this task")]
    item = {"mode_id": mid, "duration": float(duration),
            "required_resources": [], "required_equipment": []}
    present = isinstance((_task_at(raw_tree, task_index) or {}).get("modes"), list)
    return _array_add_op(f"/tasks/{task_index}/modes", present, item), []

def _remove_task_mode_patch(task_index: int, mode_index: int) -> PatchOp:
    """REMOVE one execution mode (by index) from task #task_index."""
    return PatchOp(action=PatchAction.REMOVE, path=f"/tasks/{task_index}/modes/{mode_index}")

def _task_mode_duration_patch(task_index: int, mode_index: int, duration: float) -> PatchOp:
    """REPLACE the always-present ``duration`` of one mode. A value <= 0 BLOCKS at commit
    (SCHEMA_RANGE_ERROR -- exclusiveMinimum 0)."""
    return PatchOp(action=PatchAction.REPLACE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/duration", value=float(duration))

def _task_mode_dose_patch(task_index: int, mode_index: int, dose_rate: float) -> PatchOp:
    """ADD (set-or-create) a mode's optional ``dose_rate_mrem_per_hour`` override. A negative
    value BLOCKS at commit (SCHEMA_RANGE_ERROR -- minimum 0)."""
    return PatchOp(action=PatchAction.ADD,
                   path=f"/tasks/{task_index}/modes/{mode_index}/dose_rate_mrem_per_hour",
                   value=float(dose_rate))

def _task_mode_dose_clear_patch(task_index: int, mode_index: int) -> PatchOp:
    """REMOVE a mode's ``dose_rate_mrem_per_hour`` override (so it inherits the task-level
    value). Valid only when the key is present (the wrapper gates on the current override)."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/dose_rate_mrem_per_hour")

def _task_mode_mob_patch(task_index: int, mode_index: int, lead_hours: float) -> PatchOp:
    """ADD (set-or-create) a mode's optional ``mobilization_lead_hours`` override. A negative
    value BLOCKS at commit (SCHEMA_RANGE_ERROR -- minimum 0)."""
    return PatchOp(action=PatchAction.ADD,
                   path=f"/tasks/{task_index}/modes/{mode_index}/mobilization_lead_hours",
                   value=float(lead_hours))

def _task_mode_mob_clear_patch(task_index: int, mode_index: int) -> PatchOp:
    """REMOVE a mode's ``mobilization_lead_hours`` override (so it inherits the task-level
    value). Valid only when the key is present."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/mobilization_lead_hours")

def _add_task_mode_resource_patch(raw_tree, task_index: int, mode_index: int, skill_type: str,
                                  crew_count: int) -> PatchOp:
    """Create-or-append a ``{skill_type, crew_count}`` requirement to mode #mode_index's
    ``required_resources`` (always present -- seeded ``[]`` by _add_task_mode_patch -- but guarded
    via _array_add_op for a raw-edited draft). NOT referentially validated: the CPM validator
    checks only task-level required_resources, so a ghost skill_type inside a mode COMMITS cleanly
    (the asymmetry). A ``crew_count < 1`` BLOCKS at commit (schema minimum 1)."""
    modes = (_task_at(raw_tree, task_index) or {}).get("modes") or []
    present = (0 <= mode_index < len(modes)
               and isinstance(modes[mode_index].get("required_resources"), list))
    return _array_add_op(f"/tasks/{task_index}/modes/{mode_index}/required_resources", present,
                         {"skill_type": str(skill_type), "crew_count": int(crew_count)})

def _remove_task_mode_resource_patch(task_index: int, mode_index: int,
                                     req_index: int) -> PatchOp:
    """REMOVE one ``required_resources`` entry (by index) from mode #mode_index of task
    #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/required_resources/{req_index}")

def _add_task_mode_equipment_patch(raw_tree, task_index: int, mode_index: int, equipment_id: str,
                                   quantity_needed: int) -> PatchOp:
    """Create-or-append an ``{equipment_id, quantity_needed}`` requirement to mode #mode_index's
    ``required_equipment`` (seeded ``[]``; guarded via _array_add_op). NOT referentially validated
    -- a ghost equipment_id inside a mode COMMITS cleanly (the asymmetry). A ``quantity_needed < 1``
    BLOCKS at commit (schema minimum 1)."""
    modes = (_task_at(raw_tree, task_index) or {}).get("modes") or []
    present = (0 <= mode_index < len(modes)
               and isinstance(modes[mode_index].get("required_equipment"), list))
    return _array_add_op(f"/tasks/{task_index}/modes/{mode_index}/required_equipment", present,
                         {"equipment_id": str(equipment_id),
                          "quantity_needed": int(quantity_needed)})

def _remove_task_mode_equipment_patch(task_index: int, mode_index: int,
                                      req_index: int) -> PatchOp:
    """REMOVE one ``required_equipment`` entry (by index) from mode #mode_index of task
    #task_index."""
    return PatchOp(action=PatchAction.REMOVE,
                   path=f"/tasks/{task_index}/modes/{mode_index}/required_equipment/{req_index}")

_NO_VALUE = object()   # sentinel: a value box that failed to parse (distinct from JSON null)
