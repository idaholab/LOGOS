"""Pure scenario-overlay authoring (Streamlit-free Scenario transforms)."""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Optional

from prismGui.domain.materialize import materialize
from prismGui.domain.plan import Dependency, EquipmentReq, ResourceReq, Task
from prismGui.domain.scenario import (
    DependencySuppression, EquipmentChange, LocationChange, ResourceChange, DurationOverride,
    Scenario, TaskSuppression,
)

# Every delta-collection field on a Scenario. ``_scenario_is_empty`` is authoritative over ALL of
# them (a scenario carrying only, say, an equipment change or an emergent task is NOT empty), so this
# list must gain any new what-if family — it mirrors view_data._OVERLAY_FIELDS. ``checkpoint_hour``
# is a reserved scalar, not a delta collection, so it is intentionally absent.
_DELTA_FIELDS = (
    "duration_overrides", "resource_changes", "equipment_changes", "location_changes",
    "hold_point_release_overrides", "emergent_tasks", "emergent_dependencies",
    "task_suppressions", "dependency_suppressions",
)


# =============================================================================
# scenario authoring (streamlit-free): build/extend a Scenario overlay
# =============================================================================
# A Scenario is an immutable OVERLAY delta bound to a baseline revision by
# ``base_plan_hash``. These helpers fold a single what-if (a task duration override or a
# resource-availability change) into a new frozen Scenario, so the render panel stays a
# thin ``st.*`` shell over pure, unit-testable transforms — the same discipline the editor
# builders follow. The domain (materialize) is the arbiter of validity; these only shape
# the delta. An EMPTY scenario (no overrides, no changes) is treated as "no scenario".

# The resource-change intent radio: a resource-availability edit is canonically ambiguous,
# so the user tags it. A what-if becomes a Scenario overlay; a baseline correction is
# redirected to the editor (a baseline edit), never authored here.
_SCN_INTENTS = ("What-if (scenario)", "Baseline correction")

def _is_whatif(intent: str) -> bool:
    return intent == _SCN_INTENTS[0]

def _scenario_is_empty(scenario: Optional[Scenario]) -> bool:
    """True when the scenario touches NOTHING across every delta family (``_DELTA_FIELDS``), so the
    run should use the plain baseline (materialize's scenario=None mirror path). Checking only a
    subset would let an equipment-only / emergent-only / suppression-only overlay read as empty and
    silently run the baseline."""
    if scenario is None:
        return True
    return not any(getattr(scenario, f, None) for f in _DELTA_FIELDS)

def _new_scenario_for(baseline) -> Scenario:
    """A fresh empty Scenario bound to the given baseline revision (by plan_hash), with a
    FIXED per-baseline id — the internal single-overlay rebuild target used by
    ``_scenario_base``. For a NEW named scenario the analyst keeps alongside others, use
    ``_mint_scenario`` (a unique id), never this."""
    return Scenario(
        scenario_id=f"scn-{baseline.plan_id}",
        base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash,
        name="session what-if",
    )

def _mint_scenario(baseline, existing_ids=(), name: Optional[str] = None,
                   derived_from: Optional[str] = None) -> Scenario:
    """A fresh empty Scenario bound to ``baseline`` with a UNIQUE id, so several scenarios
    coexist (unlike ``_new_scenario_for``'s fixed per-baseline id, which would collide). When
    no ``name`` is given an auto label is chosen from how many already exist ("Scenario A",
    "Scenario B", …). ``existing_ids`` guards the (vanishingly unlikely) id clash. ``derived_from``
    is a non-semantic provenance label (the scenario_id this was branched from; None == from the
    baseline) — see ``_clone_scenario``."""
    existing = tuple(existing_ids)
    scenario_id = f"scn-{uuid.uuid4().hex[:8]}"
    while scenario_id in existing:
        scenario_id = f"scn-{uuid.uuid4().hex[:8]}"
    if not name:
        n = len(existing)
        name = f"Scenario {chr(ord('A') + n)}" if n < 26 else f"Scenario {n + 1}"
    return Scenario(
        scenario_id=scenario_id,
        base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash,
        name=name,
        derived_from=derived_from,
    )

def _clone_scenario(source: Scenario, baseline, existing_ids=(),
                    name: Optional[str] = None) -> Scenario:
    """Branch a NEW scenario off ``source``: a fresh scenario (unique id, bound to the CURRENT
    ``baseline``) that copies every one of ``source``'s delta families as its starting point and
    records ``derived_from=source.scenario_id`` for lineage. This is a SNAPSHOT, not a live link —
    later edits to ``source`` do not propagate. The clone stays a flat overlay on the baseline (no
    scenario-of-scenario chaining), so materialization and the identity hash are unaffected; only
    the delta content is carried over and the lineage label set. ``name`` defaults to
    "``<source name>`` (copy)"."""
    default_name = f"{source.name or source.scenario_id} (copy)"
    fresh = _mint_scenario(baseline, existing_ids=existing_ids, name=name or default_name,
                           derived_from=source.scenario_id)
    return replace(
        fresh,
        duration_overrides=source.duration_overrides,
        resource_changes=source.resource_changes,
        equipment_changes=source.equipment_changes,
        location_changes=source.location_changes,
        hold_point_release_overrides=source.hold_point_release_overrides,
        emergent_tasks=source.emergent_tasks,
        emergent_dependencies=source.emergent_dependencies,
        task_suppressions=source.task_suppressions,
        dependency_suppressions=source.dependency_suppressions,
    )

def _scenario_base(scenario: Optional[Scenario], baseline) -> Scenario:
    """The Scenario to extend: the session's when it targets THIS baseline, else a fresh
    one — a scenario bound to a superseded revision is rebuilt so its overrides never leak
    across a baseline change (the session's resolve_for_new_baseline normally clears such a
    scenario first; this is the belt-and-suspenders guard)."""
    if scenario is None or scenario.base_plan_hash != baseline.plan_hash:
        return _new_scenario_for(baseline)
    return scenario

def _scenario_duration_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Duration overrides as display/removal rows ``{index, task_id, duration_hours}``."""
    if scenario is None:
        return []
    return [{"index": i, "task_id": ov.task_id, "duration_hours": ov.duration_hours}
            for i, ov in enumerate(scenario.duration_overrides or ())]

def _scenario_resource_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Skill-pool changes as rows ``{index, skill_type, from_hour, to_hour, new_count}``."""
    if scenario is None:
        return []
    return [{"index": i, "skill_type": rc.skill_type, "from_hour": rc.from_hour,
             "to_hour": rc.to_hour, "new_count": rc.new_count}
            for i, rc in enumerate(scenario.resource_changes or ())]

def _scenario_equipment_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Equipment changes as rows ``{index, equipment_id, from_hour, to_hour, new_quantity}``."""
    if scenario is None:
        return []
    return [{"index": i, "equipment_id": ec.equipment_id, "from_hour": ec.from_hour,
             "to_hour": ec.to_hour, "new_quantity": ec.new_quantity}
            for i, ec in enumerate(scenario.equipment_changes or ())]

def _scenario_location_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Location changes as rows ``{index, location_id, from_hour, to_hour,
    new_max_concurrent_tasks, new_max_concurrent_workers}``."""
    if scenario is None:
        return []
    return [{"index": i, "location_id": lc.location_id, "from_hour": lc.from_hour,
             "to_hour": lc.to_hour, "new_max_concurrent_tasks": lc.new_max_concurrent_tasks,
             "new_max_concurrent_workers": lc.new_max_concurrent_workers}
            for i, lc in enumerate(scenario.location_changes or ())]

def _add_duration_override(scenario: Optional[Scenario], baseline, task_id: str,
                           duration_hours: float) -> Scenario:
    """New Scenario with a duration override for ``task_id`` (last-write-wins per task: an
    existing override for the same task is replaced, never duplicated)."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(ov for ov in (base.duration_overrides or ()) if ov.task_id != task_id)
    return replace(base, duration_overrides=kept + (
        DurationOverride(task_id=task_id, duration_hours=float(duration_hours)),))

def _remove_duration_override(scenario: Optional[Scenario], baseline, task_id: str) -> Scenario:
    """New Scenario with the override for ``task_id`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(ov for ov in (base.duration_overrides or ()) if ov.task_id != task_id)
    return replace(base, duration_overrides=kept or None)

def _add_resource_change(scenario: Optional[Scenario], baseline, skill_type: str,
                         from_hour: float, new_count: int, to_hour=None) -> Scenario:
    """New Scenario with a skill-pool change (last-write-wins per (skill_type, from_hour), so
    the UI can never author the duplicate-hour case materialize would reject). ``to_hour=None``
    is an open-ended change (from ``from_hour`` onward); a value bounds it to ``[from, to)``."""
    base = _scenario_base(scenario, baseline)
    fh = float(from_hour)
    th = None if to_hour is None else float(to_hour)
    kept = tuple(rc for rc in (base.resource_changes or ())
                 if not (rc.skill_type == skill_type and rc.from_hour == fh))
    return replace(base, resource_changes=kept + (
        ResourceChange(skill_type=skill_type, from_hour=fh, new_count=int(new_count), to_hour=th),))

def _remove_resource_change(scenario: Optional[Scenario], baseline, index: int) -> Scenario:
    """New Scenario with the skill-pool change at ``index`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    changes = list(base.resource_changes or ())
    if 0 <= index < len(changes):
        del changes[index]
    return replace(base, resource_changes=tuple(changes) or None)

def _add_equipment_change(scenario: Optional[Scenario], baseline, equipment_id: str,
                          from_hour: float, new_quantity: int, to_hour=None) -> Scenario:
    """New Scenario with an equipment change (last-write-wins per (equipment_id, from_hour)).
    ``new_quantity == 0`` == out of service; ``to_hour`` bounds the window as for skills."""
    base = _scenario_base(scenario, baseline)
    fh = float(from_hour)
    th = None if to_hour is None else float(to_hour)
    kept = tuple(ec for ec in (base.equipment_changes or ())
                 if not (ec.equipment_id == equipment_id and ec.from_hour == fh))
    return replace(base, equipment_changes=kept + (
        EquipmentChange(equipment_id=equipment_id, from_hour=fh, new_quantity=int(new_quantity),
                        to_hour=th),))

def _remove_equipment_change(scenario: Optional[Scenario], baseline, index: int) -> Scenario:
    """New Scenario with the equipment change at ``index`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    changes = list(base.equipment_changes or ())
    if 0 <= index < len(changes):
        del changes[index]
    return replace(base, equipment_changes=tuple(changes) or None)

def _add_location_change(scenario: Optional[Scenario], baseline, location_id: str,
                         from_hour: float, new_max_concurrent_tasks: int, to_hour=None,
                         new_max_concurrent_workers=None) -> Scenario:
    """New Scenario with a location capacity change (last-write-wins per (location_id, from_hour)).
    ``new_max_concurrent_workers=None`` leaves the baseline worker cap untouched; ``to_hour`` bounds
    the window as for skills."""
    base = _scenario_base(scenario, baseline)
    fh = float(from_hour)
    th = None if to_hour is None else float(to_hour)
    mw = None if new_max_concurrent_workers is None else int(new_max_concurrent_workers)
    kept = tuple(lc for lc in (base.location_changes or ())
                 if not (lc.location_id == location_id and lc.from_hour == fh))
    return replace(base, location_changes=kept + (
        LocationChange(location_id=location_id, from_hour=fh,
                       new_max_concurrent_tasks=int(new_max_concurrent_tasks), to_hour=th,
                       new_max_concurrent_workers=mw),))

def _remove_location_change(scenario: Optional[Scenario], baseline, index: int) -> Scenario:
    """New Scenario with the location change at ``index`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    changes = list(base.location_changes or ())
    if 0 <= index < len(changes):
        del changes[index]
    return replace(base, location_changes=tuple(changes) or None)

# --- activity what-ifs: emergent (added) tasks / dependencies ------------------------------

def _scenario_emergent_task_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Emergent (added) tasks as rows ``{index, task_id, description, duration, location_id,
    required_resources: [{skill_type, crew_count}], required_equipment: [{equipment_id,
    quantity_needed}]}``."""
    if scenario is None:
        return []
    rows = []
    for i, t in enumerate(scenario.emergent_tasks or ()):
        rows.append({
            "index": i, "task_id": t.task_id, "description": t.description,
            "duration": t.duration, "location_id": t.location_id,
            "required_resources": [{"skill_type": r.skill_type, "crew_count": r.crew_count}
                                   for r in t.required_resources],
            "required_equipment": [{"equipment_id": e.equipment_id, "quantity_needed": e.quantity_needed}
                                   for e in t.required_equipment],
        })
    return rows

def _add_emergent_task(scenario: Optional[Scenario], baseline, task_id: str, duration: float,
                       description: Optional[str] = None, location_id: Optional[str] = None,
                       required_resources=(), required_equipment=()) -> Scenario:
    """New Scenario with an emergent (added) task (last-write-wins per ``task_id``). ``hold_point``
    is forced to None so materialize writes ``is_hold_point:False`` and NO ``hold_point_type`` key
    (the schema's vacuous conditional). ``required_resources`` is an iterable of
    ``(skill_type, crew_count)`` pairs; ``required_equipment`` of ``(equipment_id, quantity_needed)``
    pairs. Referential validity (ghost skill/equipment/location, id collision) is materialize's job."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(t for t in (base.emergent_tasks or ()) if t.task_id != task_id)
    task = Task(
        task_id=task_id,
        duration=float(duration),
        description=description,
        location_id=location_id,
        required_resources=tuple(ResourceReq(skill_type=s, crew_count=int(c))
                                 for s, c in required_resources),
        required_equipment=tuple(EquipmentReq(equipment_id=e, quantity_needed=int(q))
                                 for e, q in required_equipment),
        hold_point=None,
    )
    return replace(base, emergent_tasks=kept + (task,))

def _remove_emergent_task(scenario: Optional[Scenario], baseline, task_id: str) -> Scenario:
    """New Scenario with the emergent task ``task_id`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(t for t in (base.emergent_tasks or ()) if t.task_id != task_id)
    return replace(base, emergent_tasks=kept or None)

def _scenario_emergent_dependency_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Emergent (added) dependencies as rows ``{index, predecessor_id, successor_id, lag_hours}``."""
    if scenario is None:
        return []
    return [{"index": i, "predecessor_id": d.predecessor_id, "successor_id": d.successor_id,
             "lag_hours": d.lag_hours}
            for i, d in enumerate(scenario.emergent_dependencies or ())]

def _add_emergent_dependency(scenario: Optional[Scenario], baseline, predecessor_id: str,
                             successor_id: str, lag_hours: float = 0.0) -> Scenario:
    """New Scenario with an emergent (added) dependency edge (last-write-wins per
    ``(predecessor_id, successor_id)``). Endpoint existence is materialize's job — the panel picks
    endpoints from the MATERIALIZED task list so a just-added emergent task is selectable."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(d for d in (base.emergent_dependencies or ())
                 if not (d.predecessor_id == predecessor_id and d.successor_id == successor_id))
    return replace(base, emergent_dependencies=kept + (
        Dependency(predecessor_id=predecessor_id, successor_id=successor_id,
                   lag_hours=float(lag_hours)),))

def _remove_emergent_dependency(scenario: Optional[Scenario], baseline, index: int) -> Scenario:
    """New Scenario with the emergent dependency at ``index`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    deps = list(base.emergent_dependencies or ())
    if 0 <= index < len(deps):
        del deps[index]
    return replace(base, emergent_dependencies=tuple(deps) or None)

# --- activity what-ifs: task / dependency suppressions (removal) ---------------------------

def _scenario_task_suppression_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Task suppressions (removals) as rows ``{index, task_id}``."""
    if scenario is None:
        return []
    return [{"index": i, "task_id": s.task_id}
            for i, s in enumerate(scenario.task_suppressions or ())]

def _add_task_suppression(scenario: Optional[Scenario], baseline, task_id: str) -> Scenario:
    """New Scenario suppressing (removing) ``task_id`` (idempotent — never listed twice).
    Referential validity (the task must exist) is materialize's job."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(s for s in (base.task_suppressions or ()) if s.task_id != task_id)
    return replace(base, task_suppressions=kept + (TaskSuppression(task_id=task_id),))

def _remove_task_suppression(scenario: Optional[Scenario], baseline, task_id: str) -> Scenario:
    """New Scenario with the suppression of ``task_id`` lifted (empties to None)."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(s for s in (base.task_suppressions or ()) if s.task_id != task_id)
    return replace(base, task_suppressions=kept or None)

def _scenario_dependency_suppression_rows(scenario: Optional[Scenario]) -> list[dict]:
    """Dependency suppressions (edge removals) as rows ``{index, predecessor_id, successor_id}``."""
    if scenario is None:
        return []
    return [{"index": i, "predecessor_id": s.predecessor_id, "successor_id": s.successor_id}
            for i, s in enumerate(scenario.dependency_suppressions or ())]

def _add_dependency_suppression(scenario: Optional[Scenario], baseline, predecessor_id: str,
                                successor_id: str) -> Scenario:
    """New Scenario suppressing (removing) the edge ``predecessor_id -> successor_id`` (idempotent).
    Edge existence is materialize's job (a non-existent edge is a MATERIALIZE_CONFLICT)."""
    base = _scenario_base(scenario, baseline)
    kept = tuple(s for s in (base.dependency_suppressions or ())
                 if not (s.predecessor_id == predecessor_id and s.successor_id == successor_id))
    return replace(base, dependency_suppressions=kept + (
        DependencySuppression(predecessor_id=predecessor_id, successor_id=successor_id),))

def _remove_dependency_suppression(scenario: Optional[Scenario], baseline, index: int) -> Scenario:
    """New Scenario with the dependency suppression at ``index`` dropped (empties to None)."""
    base = _scenario_base(scenario, baseline)
    sups = list(base.dependency_suppressions or ())
    if 0 <= index < len(sups):
        del sups[index]
    return replace(base, dependency_suppressions=tuple(sups) or None)

def _current_schedule_payload(baseline, scenario) -> tuple[dict, Optional[str]]:
    """The effective payload for the current schedule (streamlit-free): the plain baseline when
    ``scenario`` is None, else the baseline with the scenario overlay MATERIALIZED in (duration
    overrides, resource what-ifs, emergent tasks). Returns ``(payload, warning)`` — ``warning``
    is set when the scenario cannot be materialized (a stale binding or a conflict), in which
    case the plain baseline payload is returned so the caller still renders something."""
    if scenario is None:
        return json.loads(baseline.raw_snapshot)["payload"], None
    outcome = materialize(baseline, scenario)
    if not outcome.ok or outcome.effective_plan is None:
        return (json.loads(baseline.raw_snapshot)["payload"],
                "This scenario could not be applied to the baseline; showing the baseline.")
    return json.loads(outcome.effective_plan.raw_snapshot)["payload"], None

def _schedule_label(session, baseline) -> str:
    """Human label for the current schedule — 'Baseline' or the scenario's name/id."""
    scenario = session.get_scenario()
    if scenario is None:
        return "Baseline"
    return f"Scenario “{scenario.name or scenario.scenario_id}”"

def _cleared_overlay(scenario: Scenario) -> Scenario:
    """The scenario with EVERY delta family emptied to None — kept as a named, empty overlay (a run
    then uses the plain baseline). Identity/lineage (id, name, derived_from, binding) is preserved;
    true deletion lives in the scenario manager."""
    return replace(scenario, **{f: None for f in _DELTA_FIELDS})

# --- human-readable delta formatting: the itemized change list (Replan draft) + the diff (Scenarios) ---

def _window_text(from_hour, to_hour) -> str:
    """A period-delta's window as text: open-ended 'from Xh' or bounded 'from Xh until Yh'."""
    return (f"from {from_hour:g}h" if to_hour is None
            else f"from {from_hour:g}h until {to_hour:g}h")

# (family_label, field_name, formatter) per delta family, in materialize apply order. One place both
# the staged-changes list and the scenario diff read, so their wording never drifts.
_SCENARIO_FAMILY_FORMATS = (
    ("duration", "duration_overrides",
     lambda d: f"{d.task_id} → {d.duration_hours:g}h"),
    ("skill", "resource_changes",
     lambda d: f"{d.skill_type} → {d.new_count} {_window_text(d.from_hour, d.to_hour)}"),
    ("equipment", "equipment_changes",
     lambda d: f"{d.equipment_id} → {d.new_quantity} {_window_text(d.from_hour, d.to_hour)}"),
    ("location", "location_changes",
     lambda d: (f"{d.location_id} → {d.new_max_concurrent_tasks} tasks"
                + ("" if d.new_max_concurrent_workers is None
                   else f", {d.new_max_concurrent_workers} workers")
                + f" {_window_text(d.from_hour, d.to_hour)}")),
    ("hold-point release", "hold_point_release_overrides",
     lambda d: f"{d.target_id} release @ {d.release_hour:g}h"),
    ("added task", "emergent_tasks",
     lambda d: f"{d.task_id} ({d.duration:g}h)"),
    ("added dependency", "emergent_dependencies",
     lambda d: f"{d.predecessor_id} → {d.successor_id} (lag {d.lag_hours:g}h)"),
    ("removed task", "task_suppressions",
     lambda d: d.task_id),
    ("removed dependency", "dependency_suppressions",
     lambda d: f"{d.predecessor_id} → {d.successor_id}"),
)

def _scenario_change_lines(scenario: Optional[Scenario]) -> list[tuple[str, str]]:
    """Every staged delta as ``(family_label, detail)`` pairs across all families, in apply order —
    the itemized "what will be committed" list the Replan editor shows, and the base the diff reads."""
    if scenario is None:
        return []
    lines: list[tuple[str, str]] = []
    for label, field, fmt in _SCENARIO_FAMILY_FORMATS:
        for d in (getattr(scenario, field, None) or ()):
            lines.append((label, fmt(d)))
    return lines

def _scenario_diff(a: Optional[Scenario], b: Optional[Scenario]) -> list[dict]:
    """Row-per-delta comparison of two scenarios' overlays: the UNION of both scenarios' staged
    deltas, each row ``{family, detail, in_a, in_b}``. A delta in both (identical) has both flags
    True; one that differs (e.g. same task, different duration) surfaces as two rows — present on one
    side each — the honest "what actually differs". Ordered by family (apply order) then detail.
    Pure — compares the INPUT overlays, NOT the output schedules (that is ``_comparison_rows``)."""
    la = set(_scenario_change_lines(a))
    lb = set(_scenario_change_lines(b))
    order = {label: i for i, (label, _field, _fmt) in enumerate(_SCENARIO_FAMILY_FORMATS)}
    return [{"family": fam, "detail": detail, "in_a": (fam, detail) in la, "in_b": (fam, detail) in lb}
            for fam, detail in sorted(la | lb, key=lambda kd: (order.get(kd[0], 99), kd[1]))]
