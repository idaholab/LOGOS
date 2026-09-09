"""domain/materialize.py — baseline + scenario -> EffectivePlan; run-config validity.

Two PURE functions the application layer composes (model-spec §5):

  * ``materialize(reference_plan, scenario)`` applies the scenario delta (if any) to the
    baseline's authoritative raw tree and returns an ``EffectivePlan`` — or, when the
    combination is inconsistent, ``ok=False`` with the responsible ``Issue``s. It does
    NOT take a RunConfig: mode validity is a separate concern (below). With
    ``scenario=None`` the effective plan mirrors the baseline exactly (thin-slice path).

  * ``validate_run_config(effective_plan, run_config)`` checks the config against the
    plan it will run on: every mode selection names a real, multi-mode task with that
    mode (INVALID_MODE), and the priority rule is one the engine knows.

``prepare_run`` (persisting input snapshots + stamping provenance into a RunRequest) is
deliberately NOT here — it needs the SnapshotStore port, so it lives in the application
layer (Step 6). Keeping materialize port-free keeps the domain pure.

Scenario delta application is Phase-1-minimal: duration overrides, emergent tasks and
dependencies (with referential checks). Resource/equipment availability *rewrites* and
their chronological-collision checks arrive with the validation adapter (group J);
the thin slice runs a plain baseline (scenario=None). Pure: stdlib only.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Optional

from prismGui.domain.hashing import effective_plan_snapshot, hash_bytes, q
from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity
from prismGui.domain.plan import EffectivePlan, ReferencePlan
from prismGui.domain.run_config import PRIORITY_RULES, RunConfig
from prismGui.domain.scenario import Scenario
from prismGui.domain.serialization import build_effective_plan, load_plan_content

JSONTree = dict


@dataclass
class MaterializeOutcome:
    ok: bool
    issues: tuple[Issue, ...]
    effective_plan: Optional[EffectivePlan] = None


def _envelope(reference_plan: ReferencePlan) -> tuple[dict, str]:
    """Recover (payload, schema_version) from the baseline's canonical raw snapshot."""
    env = json.loads(reference_plan.raw_snapshot)
    return env["payload"], env["schema_version"]


def _err(code: IssueCode, category: IssueCategory, message: str, **kw) -> Issue:
    return Issue(code=code, severity=Severity.ERROR, category=category, message=message, **kw)


def materialize(reference_plan: ReferencePlan, scenario: Optional[Scenario]) -> MaterializeOutcome:
    """Apply the scenario delta (if any) to the baseline and validate the COMBINATION."""
    payload, schema_version = _envelope(reference_plan)

    # --- plain baseline: effective plan mirrors the baseline exactly ----------
    if scenario is None:
        snapshot = effective_plan_snapshot(payload, schema_version=schema_version)
        effective = EffectivePlan(
            base_plan_id=reference_plan.plan_id,
            base_plan_hash=reference_plan.plan_hash,
            effective_plan_hash=hash_bytes(snapshot),
            content=reference_plan.content,      # exact mirror (same typed view)
            raw_snapshot=snapshot,
        )
        return MaterializeOutcome(ok=True, issues=(), effective_plan=effective)

    issues: list[Issue] = []

    # --- lineage binding: the scenario must target THIS baseline revision -----
    if scenario.base_plan_hash != reference_plan.plan_hash:
        issues.append(_err(
            IssueCode.PROV_HASH_MISMATCH, IssueCategory.PROVENANCE,
            "scenario.base_plan_hash does not match the baseline plan_hash "
            "(the scenario was built against a different revision)",
            entity_type="scenario", entity_id=scenario.scenario_id,
        ))

    working = copy.deepcopy(payload)
    tasks = working.setdefault("tasks", [])
    tasks_by_id = {t["task_id"]: t for t in tasks}
    skill_types = {r["skill_type"] for r in working.get("resources", [])}
    equipment_ids = {e["equipment_id"] for e in working.get("equipment", [])}
    location_ids = {loc["location_id"] for loc in working.get("locations", [])}

    # --- duration overrides ---------------------------------------------------
    for ov in (scenario.duration_overrides or ()):
        t = tasks_by_id.get(ov.task_id)
        if t is None:
            issues.append(_err(
                IssueCode.MATERIALIZE_CONFLICT, IssueCategory.REFERENTIAL_INTEGRITY,
                f"duration override targets task '{ov.task_id}' which is not in the plan",
                entity_type="task", entity_id=ov.task_id,
            ))
        else:
            t["duration"] = q(ov.duration_hours)

    # --- emergent tasks: valid alone, but must reference existing entities ----
    for et in (scenario.emergent_tasks or ()):
        for rr in et.required_resources:
            if rr.skill_type not in skill_types:
                issues.append(_err(
                    IssueCode.MATERIALIZE_CONFLICT, IssueCategory.REFERENTIAL_INTEGRITY,
                    f"emergent task '{et.task_id}' requires skill '{rr.skill_type}' "
                    "not present in the baseline resources",
                    entity_type="task", entity_id=et.task_id,
                ))
        for eq in et.required_equipment:
            if eq.equipment_id not in equipment_ids:
                issues.append(_err(
                    IssueCode.MATERIALIZE_CONFLICT, IssueCategory.REFERENTIAL_INTEGRITY,
                    f"emergent task '{et.task_id}' requires equipment '{eq.equipment_id}' "
                    "not present in the baseline equipment",
                    entity_type="task", entity_id=et.task_id,
                ))
        if et.location_id is not None and et.location_id not in location_ids:
            issues.append(_err(
                IssueCode.MATERIALIZE_CONFLICT, IssueCategory.REFERENTIAL_INTEGRITY,
                f"emergent task '{et.task_id}' references location '{et.location_id}' "
                "not present in the baseline locations",
                entity_type="task", entity_id=et.task_id,
            ))
        tasks.append({
            "task_id": et.task_id,
            "description": et.description,
            "duration": q(et.duration),
            "successors": [],
            "location_id": et.location_id,
            "required_resources": [
                {"skill_type": r.skill_type, "crew_count": r.crew_count} for r in et.required_resources
            ],
            "required_equipment": [
                {"equipment_id": e.equipment_id, "quantity_needed": e.quantity_needed}
                for e in et.required_equipment
            ],
            "is_hold_point": et.hold_point is not None,
        })
        tasks_by_id[et.task_id] = tasks[-1]

    # --- emergent dependencies: endpoints must exist --------------------------
    for dep in (scenario.emergent_dependencies or ()):
        for endpoint in (dep.predecessor_id, dep.successor_id):
            if endpoint not in tasks_by_id:
                issues.append(_err(
                    IssueCode.MATERIALIZE_CONFLICT, IssueCategory.REFERENTIAL_INTEGRITY,
                    f"emergent dependency references task '{endpoint}' not in the plan",
                    entity_type="dependency", entity_id=f"{dep.predecessor_id}->{dep.successor_id}",
                ))
        pred = tasks_by_id.get(dep.predecessor_id)
        if pred is not None and dep.successor_id not in pred.setdefault("successors", []):
            pred["successors"].append(dep.successor_id)

    if any(i.severity is Severity.ERROR for i in issues):
        return MaterializeOutcome(ok=False, issues=tuple(issues), effective_plan=None)

    snapshot = effective_plan_snapshot(working, schema_version=schema_version)
    effective = EffectivePlan(
        base_plan_id=reference_plan.plan_id,
        base_plan_hash=reference_plan.plan_hash,
        effective_plan_hash=hash_bytes(snapshot),
        content=load_plan_content(working),
        raw_snapshot=snapshot,
    )
    return MaterializeOutcome(ok=True, issues=tuple(issues), effective_plan=effective)


def validate_run_config(effective_plan: EffectivePlan, run_config: RunConfig) -> tuple[Issue, ...]:
    """Validate the config against the plan it will run on. Mode selections must name a
    real, multi-mode task carrying that mode (INVALID_MODE); the priority rule must be
    one the engine knows (an unknown key is a hard engine error, not a silent default)."""
    issues: list[Issue] = []
    tasks_by_id = {t.task_id: t for t in effective_plan.content.tasks}

    for ms in run_config.mode_selections:
        task = tasks_by_id.get(ms.task_id)
        if task is None:
            issues.append(_err(
                IssueCode.INVALID_MODE, IssueCategory.EXECUTION,
                f"mode selection targets task '{ms.task_id}' absent from the effective plan",
                entity_type="task", entity_id=ms.task_id,
            ))
            continue
        available = {m.mode_name for m in task.execution_modes}
        if not available:
            issues.append(_err(
                IssueCode.INVALID_MODE, IssueCategory.EXECUTION,
                f"task '{ms.task_id}' is single-mode; no mode may be selected for it",
                entity_type="task", entity_id=ms.task_id,
            ))
        elif ms.mode_name not in available:
            issues.append(_err(
                IssueCode.INVALID_MODE, IssueCategory.EXECUTION,
                f"task '{ms.task_id}' has no mode '{ms.mode_name}' (available: {sorted(available)})",
                entity_type="task", entity_id=ms.task_id,
            ))

    if run_config.priority_rule not in PRIORITY_RULES:
        issues.append(_err(
            IssueCode.EXECUTION_FAILURE, IssueCategory.EXECUTION,
            f"priority rule '{run_config.priority_rule}' is not one of the {len(PRIORITY_RULES)} "
            "engine rules",
            entity_type="run_config", entity_id=run_config.run_config_id,
        ))

    return tuple(issues)
