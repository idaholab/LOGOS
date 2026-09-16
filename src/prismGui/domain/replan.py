"""domain/replan.py — project a Scenario overlay onto ``pert.replan()`` arguments.

Phase 5 (CORE) replan loop. PRISM's engine can reschedule the *remainder* of an
outage from an as-of hour T (``pert.replan()``, CPM/pert.py) — freezing everything
already underway/complete at T and re-solving the rest under revised conditions.
This module is the PURE seam between the GUI's ``Scenario`` deltas and that engine
call: it neither imports Streamlit nor PRISM, so it is exercised headlessly.

Two functions, two inputs, on purpose:

* ``build_replan_inputs(scenario_payload)`` takes the CANONICAL scenario *payload
  dict* (``hashing.scenario_payload`` — exactly what the SnapshotStore persists and
  the adapter resolves back), and projects it onto ``replan()``'s kwargs. The adapter
  is the only caller.
* ``replan_preflight(scenario, reference_plan)`` takes the typed ``Scenario`` and its
  baseline and returns WARNING-only ``Issue``s naming what a replan will silently
  drop (families ``replan()`` cannot apply; emergent edges it cannot wire). Never
  blocks — the replan runs the supported subset. Rendered on the Replan page and
  ridden on the RunResult so the drops are visible.

``replan()`` supports: duration overrides, resource availability changes, equipment
availability changes, and emergent tasks (with their out-edges + existing→new wiring).
It does NOT support: location changes, hold-point release overrides, task suppressions,
dependency suppressions, or an emergent dependency whose BOTH endpoints already exist
(``_inject_activities`` can only wire edges touching a new task). See the plan's
"Risks & gotchas".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity


# The Scenario delta families ``replan()`` cannot honor; a checkpoint-bearing scenario
# carrying any of them runs the supported subset and warns. (field name -> label used
# in the warning message.) Everything else — duration overrides, resource changes,
# equipment changes, emergent tasks — IS supported.
_UNSUPPORTED_FAMILY_LABELS: dict[str, str] = {
    "location_changes": "location change",
    "hold_point_release_overrides": "hold-point release override",
    "task_suppressions": "task suppression",
    "dependency_suppressions": "dependency suppression",
}


@dataclass(frozen=True)
class ReplanInputs:
    """The projection of a scenario overlay onto ``pert.replan()``'s keyword arguments.

    Every collection is empty-when-absent (never ``None``) so the adapter can test
    truthiness (``ri.resource_updates or None``) before passing it on. ``new_task_specs``
    entries are ``Activity.from_json``-ready task dicts: the minimal emergent-task shape
    plus ``is_hold_point: False`` and a ``successors`` list carrying the new task's
    OUT-edges (to existing or other new tasks). ``predecessor_wiring`` maps a new task id
    to the existing tasks that precede it (``_inject_activities`` forces those edges to
    lag 0)."""
    checkpoint_hour: float
    resource_updates: tuple[dict, ...] = ()
    equipment_updates: tuple[dict, ...] = ()
    duration_overrides: dict = field(default_factory=dict)
    new_task_specs: tuple[dict, ...] = ()
    predecessor_wiring: dict = field(default_factory=dict)


def build_replan_inputs(scenario_payload: dict) -> ReplanInputs:
    """Project the canonical ``scenario_payload`` dict onto ``pert.replan()`` kwargs.

    Pure dict-to-dict transform (no Scenario/ReferencePlan needed): the emergent-task
    ids in the payload are the only "is this a new task?" oracle needed to classify
    emergent dependencies. ``resource_changes``/``equipment_changes`` rename
    ``to_hour → until_hour`` (replan's key); ``duration_overrides`` pass through. Each
    emergent task becomes a ``from_json``-ready spec; each emergent dependency is routed
    by endpoint:

    * predecessor is a NEW task → the edge is an OUT-edge on that task's ``successors``
      (covers new→existing and new→new);
    * predecessor pre-exists, successor is NEW → ``predecessor_wiring[succ].append(pred)``
      (the engine wires it lag-0);
    * both endpoints pre-exist → **dropped** (unwireable; ``replan_preflight`` warns)."""
    checkpoint = scenario_payload.get("checkpoint_hour")
    checkpoint_hour = 0.0 if checkpoint is None else float(checkpoint)

    resource_updates = tuple(
        {"skill_type": c["skill_type"], "from_hour": c["from_hour"],
         "new_count": c["new_count"], "until_hour": c.get("to_hour")}
        for c in scenario_payload.get("resource_changes", ())
    )
    equipment_updates = tuple(
        {"equipment_id": c["equipment_id"], "from_hour": c["from_hour"],
         "new_quantity": c["new_quantity"], "until_hour": c.get("to_hour")}
        for c in scenario_payload.get("equipment_changes", ())
    )
    duration_overrides = dict(scenario_payload.get("duration_overrides", {}) or {})

    emergent_tasks = scenario_payload.get("emergent_tasks", ()) or ()
    emergent_deps = scenario_payload.get("emergent_dependencies", ()) or ()
    new_ids = {t["task_id"] for t in emergent_tasks}

    # OUT-edges keyed by the NEW predecessor task id → list of successor {task_id, lag}.
    out_edges: dict[str, list[dict]] = {}
    predecessor_wiring: dict[str, list[str]] = {}
    for d in emergent_deps:
        pred, succ = d["predecessor_id"], d["successor_id"]
        if pred in new_ids:
            out_edges.setdefault(pred, []).append(
                {"task_id": succ, "lag_hours": d.get("lag_hours", 0.0)})
        elif succ in new_ids:
            predecessor_wiring.setdefault(succ, []).append(pred)
        # else: both pre-exist -> unwireable, dropped (preflight warns).

    new_task_specs = tuple(
        {**t, "is_hold_point": False, "successors": out_edges.get(t["task_id"], [])}
        for t in emergent_tasks
    )

    return ReplanInputs(
        checkpoint_hour=checkpoint_hour,
        resource_updates=resource_updates,
        equipment_updates=equipment_updates,
        duration_overrides=duration_overrides,
        new_task_specs=new_task_specs,
        predecessor_wiring=predecessor_wiring,
    )


def _warn(message: str, entity_id: Optional[str] = None) -> Issue:
    return Issue(
        code=IssueCode.REPLAN_UNSUPPORTED,
        severity=Severity.WARNING,
        category=IssueCategory.EXECUTION,
        message=message,
        entity_id=entity_id,
    )


def replan_preflight(scenario, reference_plan) -> tuple[Issue, ...]:
    """WARNING-only issues naming what a replan of ``scenario`` will silently drop.

    Never returns ERRORs and never blocks — the replan runs the supported subset. An
    empty tuple means nothing is dropped. Warns for: any set-but-unsupported delta
    family (``location_changes`` / ``hold_point_release_overrides`` / ``task_suppressions``
    / ``dependency_suppressions``); any emergent dependency whose BOTH endpoints already
    exist in the baseline (``_inject_activities`` cannot wire an existing→existing edge);
    and any existing→new emergent dependency carrying a NON-ZERO lag (the wiring forces
    lag 0). Endpoint classification uses the baseline task ids + the scenario's emergent
    task ids."""
    issues: list[Issue] = []

    for field_name, label in _UNSUPPORTED_FAMILY_LABELS.items():
        fam = getattr(scenario, field_name, None)
        if fam:
            issues.append(_warn(
                f"Replan does not support {label}s — {len(fam)} {label}(s) will be "
                f"ignored (the replan reschedules from the as-of hour applying only the "
                f"supported deltas). Use the sidebar 'Run schedule' for a from-hour-0 run "
                f"that honors this family."))

    existing_ids = {t.task_id for t in reference_plan.content.tasks}
    new_ids = {t.task_id for t in (scenario.emergent_tasks or ())}
    for d in (scenario.emergent_dependencies or ()):
        pred_new = d.predecessor_id in new_ids
        succ_new = d.successor_id in new_ids
        edge = f"{d.predecessor_id} → {d.successor_id}"
        if not pred_new and not succ_new:
            issues.append(_warn(
                f"Replan cannot wire the emergent dependency {edge}: both endpoints "
                f"already exist in the baseline and the engine can only attach edges that "
                f"touch a new task. This edge will be dropped.",
                entity_id=edge))
        elif not pred_new and succ_new and d.lag_hours:
            issues.append(_warn(
                f"Replan wires the emergent dependency {edge} with lag 0; its "
                f"{d.lag_hours:g} h lag will be dropped.",
                entity_id=edge))

    return tuple(issues)
