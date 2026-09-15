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

Scenario delta application: duration overrides; period-based availability rewrites for
skills, equipment, and locations (a shared clip-and-split over the change's ``[from, to)``
window — ``to=None`` is open-ended — with ghost-id / duplicate-hour / out-of-range checks);
emergent tasks / dependencies (add, with referential checks); and task / dependency
suppressions (remove, with dangling-edge cleanup and referential checks). Hold-point release
overrides are still deferred; the thin slice runs a plain baseline (scenario=None). Pure: stdlib only.
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
from prismGui.domain.serialization import (
    build_effective_plan,
    hours_to_iso,
    iso_to_hours,
    load_plan_content,
    parse_project_start,
)

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


def _split_window(triples: list, from_hour: float, to_hour, apply_new) -> list:
    """Clip-and-split a period list at the ``[from_hour, to_hour)`` window boundaries.

    ``triples`` are ``(s, e, raw)`` hour-space periods (``raw`` = the raw period dict, dates
    NOT yet recomputed). For each period the window intersection gets ``apply_new(copy)`` —
    a mutator that overwrites its value key(s) — while the parts outside the window keep the
    original ``raw``. Up to three pieces per period: ``[s, from)`` old, ``[max(s,from),
    min(e,to))`` new, ``[to, e)`` old. Zero-length pieces are never emitted (strict ``<``).

    ``to_hour is None`` is the OPEN-ENDED case (everything at/after ``from`` gets the new
    value) — byte-identical to the pre-window single-boundary behavior, so existing skill
    scenarios materialize unchanged."""
    fh = q(from_hour)
    th = None if to_hour is None else q(to_hour)
    out: list = []
    for s, e, raw in triples:
        if e <= fh or (th is not None and s >= th):
            out.append((s, e, raw))
            continue
        if s < fh:                                   # left remainder (old)
            out.append((s, fh, raw))
        w_start = max(s, fh)
        w_end = e if th is None else min(e, th)
        if w_start < w_end:                          # in-window segment (new)
            out.append((w_start, w_end, apply_new(dict(raw))))
        if th is not None and e > th:                # right remainder (old)
            out.append((th, e, raw))
    return out


def _write_periods(triples: list, start) -> list:
    """Bridge ``(s, e, raw)`` hour-space periods back to raw DATE-based period dicts, recomputing
    ``start_date`` / ``end_date`` from the hour bounds and preserving every other key as-is."""
    written = []
    for s, e, raw in triples:
        p = dict(raw)
        p["start_date"] = hours_to_iso(s, start)
        p["end_date"] = hours_to_iso(e, start)
        written.append(p)
    return written


def _apply_period_changes(working: JSONTree, changes: tuple, *, collection_key: str, id_key: str,
                          valid_ids: set, entity_label: str, id_of, mutator_for,
                          start) -> list[Issue]:
    """Generic clip-and-split for the period-based availability what-ifs (skills / equipment /
    locations). All three share the raw shape ``{start_date, end_date, <value>…, reason?}`` and
    the same referential/range guards; only the collection, the id field, and which value key(s)
    a change writes differ — supplied by the thin wrappers below.

    Semantics (per entity, changes applied cumulatively in ascending ``from_hour``): a change sets
    the value key(s) to the new value across ``[from_hour, to_hour)`` (``to_hour=None`` == from
    ``from_hour`` onward), splitting periods at the boundaries (``_split_window``). Only entities
    named by a change are rewritten — untouched entities stay byte-identical.

    Errors (all blocking): a ghost id -> MATERIALIZE_CONFLICT; two changes sharing an
    ``(id, from_hour)``, a ``from_hour`` outside ``[0, max end)``, or a ``to_hour <= from_hour``
    -> INVALID_AVAILABILITY_INTERVAL."""
    issues: list[Issue] = []
    if not changes:
        return issues

    by_id_raw = {item[id_key]: item for item in working.get(collection_key, [])}

    # bucket by entity; reject ghosts and duplicate (id, from_hour) up front
    by_entity: dict[str, list] = {}
    seen: set[tuple[str, float]] = set()
    for ch in changes:
        eid = id_of(ch)
        if eid not in valid_ids:
            issues.append(_err(
                IssueCode.MATERIALIZE_CONFLICT, IssueCategory.REFERENTIAL_INTEGRITY,
                f"{entity_label} change targets '{eid}' not present in the baseline {collection_key}",
                entity_type=entity_label, entity_id=eid,
            ))
            continue
        key = (eid, q(ch.from_hour))
        if key in seen:
            issues.append(_err(
                IssueCode.INVALID_AVAILABILITY_INTERVAL, IssueCategory.REFERENTIAL_INTEGRITY,
                f"two {entity_label} changes target '{eid}' at the same hour {ch.from_hour:g} "
                f"(a change must be at a distinct hour per {entity_label})",
                entity_type=entity_label, entity_id=eid,
            ))
            continue
        seen.add(key)
        by_entity.setdefault(eid, []).append(ch)

    for eid, entity_changes in by_entity.items():
        item = by_id_raw[eid]
        triples = [
            (iso_to_hours(p["start_date"], start), iso_to_hours(p["end_date"], start), p)
            for p in (item.get("availability_periods") or [])
        ]
        max_end = max((e for _, e, _ in triples), default=None)

        # range-check every change against the ORIGINAL envelope before mutating this entity
        bad = False
        for ch in entity_changes:
            fh = q(ch.from_hour)
            if max_end is None or fh < 0 or fh >= max_end:
                issues.append(_err(
                    IssueCode.INVALID_AVAILABILITY_INTERVAL, IssueCategory.REFERENTIAL_INTEGRITY,
                    f"{entity_label} change for '{eid}' at hour {ch.from_hour:g} is outside the "
                    f"availability [0, {max_end}) — it would change nothing",
                    entity_type=entity_label, entity_id=eid,
                ))
                bad = True
            elif ch.to_hour is not None and q(ch.to_hour) <= fh:
                issues.append(_err(
                    IssueCode.INVALID_AVAILABILITY_INTERVAL, IssueCategory.REFERENTIAL_INTEGRITY,
                    f"{entity_label} change for '{eid}' has final hour {ch.to_hour:g} at or before "
                    f"initial hour {ch.from_hour:g} (an empty window changes nothing)",
                    entity_type=entity_label, entity_id=eid,
                ))
                bad = True
        if bad:
            continue

        for ch in sorted(entity_changes, key=lambda c: q(c.from_hour)):
            triples = _split_window(triples, ch.from_hour, ch.to_hour, mutator_for(ch))

        item["availability_periods"] = _write_periods(triples, start)

    return issues


def _apply_resource_changes(working: JSONTree, changes: tuple, skill_types: set) -> list[Issue]:
    """Skill-pool availability what-ifs: set ``available_count`` across the change's window."""
    def mutator_for(ch):
        def mutate(raw):
            raw["available_count"] = int(ch.new_count)
            return raw
        return mutate

    return _apply_period_changes(
        working, changes, collection_key="resources", id_key="skill_type",
        valid_ids=skill_types, entity_label="resource", id_of=lambda c: c.skill_type,
        mutator_for=mutator_for, start=parse_project_start(working["outage"]["start_date"]))


def _apply_equipment_changes(working: JSONTree, changes: tuple, equipment_ids: set) -> list[Issue]:
    """Equipment availability what-ifs: set ``quantity_available`` across the change's window
    (``new_quantity == 0`` == out of service)."""
    def mutator_for(ch):
        def mutate(raw):
            raw["quantity_available"] = int(ch.new_quantity)
            return raw
        return mutate

    return _apply_period_changes(
        working, changes, collection_key="equipment", id_key="equipment_id",
        valid_ids=equipment_ids, entity_label="equipment", id_of=lambda c: c.equipment_id,
        mutator_for=mutator_for, start=parse_project_start(working["outage"]["start_date"]))


def _apply_location_changes(working: JSONTree, changes: tuple, location_ids: set) -> list[Issue]:
    """Location capacity what-ifs: set ``max_concurrent_tasks`` (and, when the change names one,
    ``max_concurrent_workers``) across the change's window. A ``None`` worker cap on the change
    leaves the baseline's worker cap untouched (which may itself be absent == no cap)."""
    def mutator_for(ch):
        def mutate(raw):
            raw["max_concurrent_tasks"] = int(ch.new_max_concurrent_tasks)
            if ch.new_max_concurrent_workers is not None:
                raw["max_concurrent_workers"] = int(ch.new_max_concurrent_workers)
            return raw
        return mutate

    return _apply_period_changes(
        working, changes, collection_key="locations", id_key="location_id",
        valid_ids=location_ids, entity_label="location", id_of=lambda c: c.location_id,
        mutator_for=mutator_for, start=parse_project_start(working["outage"]["start_date"]))


def _successor_id(succ) -> str:
    """The target task id of a raw ``successors`` entry, which is either a bare task-id
    string (lag 0) or the schema's object form ``{"task_id", "lag_hours"}`` (outage_schema.json
    ``successors.items`` oneOf). Mirrors ``serialization._normalize_edges``."""
    return succ["task_id"] if isinstance(succ, dict) else succ


def _apply_task_suppressions(working: JSONTree, suppressions: tuple) -> list[Issue]:
    """Remove suppressed tasks from the effective plan. Each suppressed id is deleted from
    ``working["tasks"]`` AND stripped from every remaining task's ``successors`` (so no edge
    dangles to a removed task). An unknown task id -> MATERIALIZE_CONFLICT (blocking)."""
    issues: list[Issue] = []
    if not suppressions:
        return issues

    tasks = working.get("tasks", [])
    present = {t["task_id"] for t in tasks}
    to_remove: set[str] = set()
    for sup in suppressions:
        if sup.task_id not in present:
            issues.append(_err(
                IssueCode.MATERIALIZE_CONFLICT, IssueCategory.REFERENTIAL_INTEGRITY,
                f"task suppression targets task '{sup.task_id}' which is not in the plan",
                entity_type="task", entity_id=sup.task_id,
            ))
            continue
        to_remove.add(sup.task_id)

    if to_remove:
        working["tasks"] = [t for t in tasks if t["task_id"] not in to_remove]
        for t in working["tasks"]:
            succ = t.get("successors")
            if succ:
                # a successor entry may be a bare id string or a {"task_id", "lag_hours"} object
                t["successors"] = [s for s in succ if _successor_id(s) not in to_remove]
    return issues


def _apply_dependency_suppressions(working: JSONTree, suppressions: tuple) -> list[Issue]:
    """Remove one precedence edge each: drop ``successor_id`` from ``predecessor_id``'s
    ``successors``, leaving both tasks in place. An unknown predecessor, or an edge that is
    not currently present (successor not among the predecessor's successors), is a blocking
    MATERIALIZE_CONFLICT — a no-op suppression must not silently pass."""
    issues: list[Issue] = []
    if not suppressions:
        return issues

    tasks_by_id = {t["task_id"]: t for t in working.get("tasks", [])}
    for sup in suppressions:
        pred = tasks_by_id.get(sup.predecessor_id)
        if pred is None:
            issues.append(_err(
                IssueCode.MATERIALIZE_CONFLICT, IssueCategory.REFERENTIAL_INTEGRITY,
                f"dependency suppression references predecessor task '{sup.predecessor_id}' "
                "not in the plan",
                entity_type="dependency", entity_id=f"{sup.predecessor_id}->{sup.successor_id}",
            ))
            continue
        succ = pred.get("successors") or []
        # a successor entry may be a bare id string or a {"task_id", "lag_hours"} object
        if sup.successor_id not in {_successor_id(s) for s in succ}:
            issues.append(_err(
                IssueCode.MATERIALIZE_CONFLICT, IssueCategory.REFERENTIAL_INTEGRITY,
                f"dependency suppression targets edge '{sup.predecessor_id}->{sup.successor_id}' "
                "which is not present in the plan",
                entity_type="dependency", entity_id=f"{sup.predecessor_id}->{sup.successor_id}",
            ))
            continue
        pred["successors"] = [s for s in succ if _successor_id(s) != sup.successor_id]
    return issues


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
        if et.task_id in tasks_by_id:
            # A new task may not reuse an existing task id: overwriting the baseline task
            # (or appending a silent duplicate) would corrupt the schedule. Reject and skip.
            issues.append(_err(
                IssueCode.EMERGENT_ID_COLLISION, IssueCategory.REFERENTIAL_INTEGRITY,
                f"emergent task '{et.task_id}' collides with an existing baseline task id "
                "(an emergent task id must be new; the baseline task is never overwritten)",
                entity_type="task", entity_id=et.task_id,
            ))
            continue
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
        if pred is not None and dep.successor_id not in {
            _successor_id(s) for s in pred.setdefault("successors", [])
        }:
            pred["successors"].append(dep.successor_id)

    # --- activity removal (after adds, before availability): suppress tasks then edges. Task
    #     suppression strips dangling successor entries automatically, so a dependency
    #     suppression on an edge a removed task owned reads as "edge not present" (blocking).
    issues.extend(_apply_task_suppressions(working, scenario.task_suppressions or ()))
    issues.extend(_apply_dependency_suppressions(working, scenario.dependency_suppressions or ()))

    # --- availability what-ifs: rewrite the baseline periods (clip-and-split) -
    issues.extend(_apply_resource_changes(working, scenario.resource_changes or (), skill_types))
    issues.extend(_apply_equipment_changes(working, scenario.equipment_changes or (), equipment_ids))
    issues.extend(_apply_location_changes(working, scenario.location_changes or (), location_ids))

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
