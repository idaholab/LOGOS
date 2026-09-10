"""domain/serialization.py — schema dict (ISO) <-> typed domain view (hour-offset).

Two directions, one anchor:
  * ``load_plan_content`` turns a schema-shaped JSON dict (ISO timestamps, per-task
    ``successors``) into the thin typed ``PlanContent`` the GUI reads — times become
    float hour-offsets from the project start; per-task successors become normalized
    top-level ``Dependency`` edges.
  * ``serialize_plan_content`` is the inverse, for export / effective-plan
    re-validation — hour-offsets back to ISO, edges back to per-task successors.

The typed view is a convenience for display and materialization; the AUTHORITATIVE
representation is always the canonical raw snapshot (``hashing``), which is what the
PRISM adapter and the provenance hashes are built from. So this mapping is
lossless-*semantic*, not byte-exact (contract group H): unknown/unmodeled fields are
preserved on the raw tree, not necessarily in the typed view.

Project-start timezone (documented decision — supersedes model-spec's "reject naive"):
  The canonical samples and ``outage_schema.json`` use tz-NAIVE dates
  (``"2025-09-01"``). Rejecting naive starts would reject the shipping input, so the
  loader is TOLERANT: a naive or date-only start is interpreted as UTC. Only the
  instant matters for hour-offsets and hashing, so this is deterministic. See the
  contract note on ``test_timezone_naive_start_date_rejected``. Pure: stdlib only.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from prismGui.domain.hashing import (
    effective_plan_snapshot,
    hash_bytes,
    q,
    reference_plan_snapshot,
)
from prismGui.domain.plan import (
    Consumable,
    ConsumableDemand,
    Dependency,
    EffectivePlan,
    EquipmentAvailability,
    EquipmentItem,
    EquipmentReq,
    ExecutionMode,
    HoldPoint,
    LocationAvailability,
    LocationZone,
    PlanContent,
    PlanMeta,
    PlantSystem,
    ReferencePlan,
    ResourceAvailability,
    ResourcePool,
    ResourceReq,
    ResourceType,
    RestockDelivery,
    SystemStateReq,
    Task,
    TimeWindow,
)

JSONTree = dict[str, Any]
Hours = float


# =============================================================================
# Time anchoring (ISO <-> hour-offset)
# =============================================================================

def parse_project_start(raw_start: str) -> datetime:
    """Interpret the schema ``outage.start_date`` as a tz-aware UTC instant. Accepts a
    date-only string (midnight UTC) or an ISO datetime; a naive datetime is read as
    UTC (documented tolerance, see module docstring)."""
    s = raw_start.strip()
    if "T" not in s and " " not in s:
        d = date.fromisoformat(s)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s.replace(" ", "T"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _parse_instant(raw: str) -> datetime:
    """Parse any schema timestamp to a tz-aware UTC instant (naive -> UTC)."""
    s = raw.strip()
    if "T" not in s and " " not in s:
        d = date.fromisoformat(s)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    dt = datetime.fromisoformat(s.replace(" ", "T"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso_to_hours(iso_str: str, start: datetime) -> Hours:
    """Hour-offset (quantized to the 1 ms grid) of an ISO instant from the project
    start. Half-open [start, end) semantics are preserved by mapping both ends."""
    return q((_parse_instant(iso_str) - start).total_seconds() / 3600.0)


def hours_to_iso(hours: Hours, start: datetime) -> str:
    """Inverse of ``iso_to_hours``: an hour-offset back to a UTC ISO-8601 string
    (seconds resolution; matches the schema's naive-datetime shape without a tz
    suffix, so exported plans round-trip through the same loader)."""
    inst = start.timestamp() + hours * 3600.0
    dt = datetime.fromtimestamp(inst, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# =============================================================================
# Small field helpers
# =============================================================================

def _as_tuple(seq: Optional[list]) -> tuple:
    return tuple(seq) if seq else ()


def _resource_reqs(raw: Optional[list]) -> tuple[ResourceReq, ...]:
    return tuple(
        ResourceReq(skill_type=r["skill_type"], crew_count=int(r.get("crew_count", 1)))
        for r in (raw or [])
    )


def _equipment_reqs(raw: Optional[list]) -> tuple[EquipmentReq, ...]:
    return tuple(
        EquipmentReq(equipment_id=e["equipment_id"], quantity_needed=int(e.get("quantity_needed", 1)))
        for e in (raw or [])
    )


def _execution_modes(raw: Optional[list]) -> tuple[ExecutionMode, ...]:
    return tuple(
        ExecutionMode(
            mode_name=m["mode_id"],
            duration=float(m["duration"]),
            crew=_resource_reqs(m.get("required_resources")),
            equipment=_equipment_reqs(m.get("required_equipment")),
            dose_rate=m.get("dose_rate_mrem_per_hour"),
            mobilization_lead_hours=m.get("mobilization_lead_hours"),
        )
        for m in (raw or [])
    )


def _hold_point(task: JSONTree) -> Optional[HoldPoint]:
    if not task.get("is_hold_point"):
        return None
    return HoldPoint(
        hold_point_type=task.get("hold_point_type"),
        blocks_tasks=_as_tuple(task.get("blocks_tasks")),
    )


# =============================================================================
# schema dict -> typed PlanContent
# =============================================================================

def _load_task(t: JSONTree) -> Task:
    windows = tuple(
        TimeWindow(w_min=float(w["earliest"]), w_max=float(w["latest"]))
        for w in (t.get("time_windows") or [])
    )
    return Task(
        task_id=t["task_id"],
        duration=float(t["duration"]),
        description=t.get("description"),
        required_resources=_resource_reqs(t.get("required_resources")),
        required_equipment=_equipment_reqs(t.get("required_equipment")),
        location_id=t.get("location_id"),
        zone_ids=_as_tuple(t.get("zone_ids")),
        consumable_demands=tuple(
            ConsumableDemand(material_id=c["item_id"], quantity=float(c["quantity_needed"]))
            for c in (t.get("required_consumables") or [])
        ),
        required_states=tuple(
            SystemStateReq(system_id=s["system_id"], state=s["required_state"])
            for s in (t.get("required_system_states") or [])
        ),
        time_windows=windows,
        hold_point=_hold_point(t),
        execution_modes=_execution_modes(t.get("modes")),
        dose_rate=t.get("dose_rate_mrem_per_hour"),
        mobilization_lead_hours=t.get("mobilization_lead_hours"),
        wbs_group=t.get("wbs_group"),
        alternative_skill_types=tuple(
            alt
            for r in (t.get("required_resources") or [])
            for alt in (r.get("alternative_skill_types") or [])
        ),
    )


def _dependencies_from_tasks(tasks: list[JSONTree]) -> tuple[Dependency, ...]:
    """Per-task ``successors`` -> normalized top-level edges (predecessor -> successor),
    de-duplicated while preserving first-seen order (contract group H / J). A successor
    entry is either a bare task-id string or the schema's object form
    ``{"task_id", "lag_hours"}`` (outage_schema.json ``successors.items`` oneOf); the
    object form carries a finish-to-start lag, a plain string means lag 0."""
    edges: list[Dependency] = []
    seen: set[tuple[str, str]] = set()
    for t in tasks:
        pred = t["task_id"]
        for succ in (t.get("successors") or []):
            if isinstance(succ, dict):
                succ_id = succ["task_id"]
                lag = float(succ.get("lag_hours", 0.0))
            else:
                succ_id = succ
                lag = 0.0
            key = (pred, succ_id)
            if key not in seen:
                seen.add(key)
                edges.append(Dependency(predecessor_id=pred, successor_id=succ_id, lag_hours=lag))
    return tuple(edges)


def _load_resources(raw: Optional[list], start: datetime) -> tuple[ResourcePool, ...]:
    pools = []
    for r in (raw or []):
        rtype = r.get("resource_type", "renewable")
        pools.append(ResourcePool(
            skill_type=r["skill_type"],
            resource_type=ResourceType(rtype) if rtype in (e.value for e in ResourceType) else ResourceType.RENEWABLE,
            availability_periods=tuple(
                ResourceAvailability(
                    start=iso_to_hours(p["start_date"], start),
                    end=iso_to_hours(p["end_date"], start),
                    count=int(p["available_count"]),
                    reason=p.get("reason"),
                )
                for p in (r.get("availability_periods") or [])
            ),
            dose_budget_per_worker_mrem=r.get("dose_budget_per_worker_mrem"),
        ))
    return tuple(pools)


def _load_equipment(raw: Optional[list], start: datetime) -> tuple[EquipmentItem, ...]:
    return tuple(
        EquipmentItem(
            equipment_id=e["equipment_id"],
            description=e.get("description"),
            availability_periods=tuple(
                EquipmentAvailability(
                    start=iso_to_hours(p["start_date"], start),
                    end=iso_to_hours(p["end_date"], start),
                    quantity=int(p["quantity_available"]),
                    reason=p.get("reason"),
                )
                for p in (e.get("availability_periods") or [])
            ),
            zone_affinity=e.get("zone_id"),
        )
        for e in (raw or [])
    )


def _load_locations(raw: Optional[list], start: datetime) -> tuple[LocationZone, ...]:
    return tuple(
        LocationZone(
            location_id=loc["location_id"],
            description=loc.get("description"),
            is_confined_space=loc.get("is_confined_space"),
            availability_periods=tuple(
                LocationAvailability(
                    start=iso_to_hours(p["start_date"], start),
                    end=iso_to_hours(p["end_date"], start),
                    max_concurrent_tasks=int(p["max_concurrent_tasks"]),
                    max_concurrent_workers=int(p["max_concurrent_workers"]),
                    reason=p.get("reason"),
                )
                for p in (loc.get("availability_periods") or [])
            ),
        )
        for loc in (raw or [])
    )


def _load_consumables(raw: Optional[list]) -> tuple[Consumable, ...]:
    return tuple(
        Consumable(
            material_id=c["item_id"],
            initial_stock=float(c.get("total_quantity", 0.0)),
            restock_deliveries=tuple(
                RestockDelivery(hour=q(float(d["delivery_hour"])), quantity=float(d["quantity"]))
                for d in (c.get("restocks") or [])
            ),
        )
        for c in (raw or [])
    )


def _load_systems(raw: Optional[list]) -> tuple[PlantSystem, ...]:
    return tuple(
        PlantSystem(system_id=s["system_id"], valid_states=_as_tuple(s.get("valid_states")))
        for s in (raw or [])
    )


def load_plan_content(schema_dict: JSONTree) -> PlanContent:
    """Parse a schema-shaped plan dict into the thin, immutable typed view."""
    outage = schema_dict["outage"]
    start = parse_project_start(outage["start_date"])
    target = outage.get("target_end_date")
    meta = PlanMeta(
        outage_id=outage["outage_id"],
        start_date=start,
        working_hours_per_day=float(outage.get("working_hours_per_day", 24)),
        shift_start_hour=outage.get("shift_start_hour"),
        target_end_date=(parse_project_start(target) if target else None),
    )
    tasks_raw = schema_dict.get("tasks") or []
    return PlanContent(
        meta=meta,
        tasks=tuple(_load_task(t) for t in tasks_raw),
        dependencies=_dependencies_from_tasks(tasks_raw),
        resources=_load_resources(schema_dict.get("resources"), start),
        equipment=_load_equipment(schema_dict.get("equipment"), start),
        locations=_load_locations(schema_dict.get("locations"), start),
        consumables=_load_consumables(schema_dict.get("consumables")),
        systems=_load_systems(schema_dict.get("systems") or schema_dict.get("plant_systems")),
    )


# =============================================================================
# typed PlanContent -> schema dict  (export / effective-plan re-validation)
# =============================================================================

def _dump_resource_reqs(reqs: tuple[ResourceReq, ...]) -> list:
    return [{"skill_type": r.skill_type, "crew_count": r.crew_count} for r in reqs]


def _dump_equipment_reqs(reqs: tuple[EquipmentReq, ...]) -> list:
    return [{"equipment_id": e.equipment_id, "quantity_needed": e.quantity_needed} for e in reqs]


def _successors_by_task(deps: tuple[Dependency, ...]) -> dict[str, list]:
    """Normalized edges -> the schema's per-task ``successors`` lists. A lag-0 edge emits a
    bare task-id string (so lag-free plans round-trip to the same shape the samples use); an
    edge with a non-zero lag emits the object form ``{"task_id", "lag_hours"}``."""
    out: dict[str, list] = {}
    for d in deps:
        succ = (d.successor_id if d.lag_hours == 0
                else {"task_id": d.successor_id, "lag_hours": d.lag_hours})
        out.setdefault(d.predecessor_id, []).append(succ)
    return out


def serialize_plan_content(content: PlanContent) -> JSONTree:
    """Inverse of ``load_plan_content``: a schema-shaped dict (per-task successors,
    ISO timestamps). Only modeled fields are emitted — unmodeled raw fields live on
    the authoritative snapshot, not here (lossless-semantic, not byte-exact)."""
    start = content.meta.start_date
    succ = _successors_by_task(content.dependencies)

    def dump_task(t: Task) -> JSONTree:
        d: JSONTree = {
            "task_id": t.task_id,
            "description": t.description,
            "duration": t.duration,
            "successors": succ.get(t.task_id, []),
            "location_id": t.location_id,
            "required_resources": _dump_resource_reqs(t.required_resources),
            "required_equipment": _dump_equipment_reqs(t.required_equipment),
            "is_hold_point": t.hold_point is not None,
        }
        if t.hold_point is not None:
            if t.hold_point.hold_point_type is not None:
                d["hold_point_type"] = t.hold_point.hold_point_type
            if t.hold_point.blocks_tasks:
                d["blocks_tasks"] = list(t.hold_point.blocks_tasks)
        if t.wbs_group is not None:
            d["wbs_group"] = t.wbs_group
        return d

    out: JSONTree = {
        "outage": {
            "outage_id": content.meta.outage_id,
            "start_date": start.strftime("%Y-%m-%d"),
            "working_hours_per_day": content.meta.working_hours_per_day,
        },
        "tasks": [dump_task(t) for t in content.tasks],
    }
    if content.meta.target_end_date is not None:
        out["outage"]["target_end_date"] = content.meta.target_end_date.strftime("%Y-%m-%d")
    # resources / equipment / locations are root-required by the schema even when
    # empty, so the export round-trips back through the input validator; the blocks
    # below overwrite these with the populated form when the collection is non-empty.
    out["resources"] = []
    out["equipment"] = []
    out["locations"] = []
    if content.resources:
        out["resources"] = [
            {
                "skill_type": r.skill_type,
                "resource_type": r.resource_type.value,
                "availability_periods": [
                    {
                        "start_date": hours_to_iso(p.start, start),
                        "end_date": hours_to_iso(p.end, start),
                        "available_count": p.count,
                        "reason": p.reason,
                    }
                    for p in r.availability_periods
                ],
            }
            for r in content.resources
        ]
    if content.equipment:
        out["equipment"] = [
            {
                "equipment_id": e.equipment_id,
                "description": e.description,
                "availability_periods": [
                    {
                        "start_date": hours_to_iso(p.start, start),
                        "end_date": hours_to_iso(p.end, start),
                        "quantity_available": p.quantity,
                        "reason": p.reason,
                    }
                    for p in e.availability_periods
                ],
            }
            for e in content.equipment
        ]
    if content.locations:
        out["locations"] = [
            {
                "location_id": loc.location_id,
                "description": loc.description,
                "is_confined_space": loc.is_confined_space,
                "availability_periods": [
                    {
                        "start_date": hours_to_iso(p.start, start),
                        "end_date": hours_to_iso(p.end, start),
                        "max_concurrent_tasks": p.max_concurrent_tasks,
                        "max_concurrent_workers": p.max_concurrent_workers,
                        "reason": p.reason,
                    }
                    for p in loc.availability_periods
                ],
            }
            for loc in content.locations
        ]
    return out


# =============================================================================
# committed-plan builders (content + authoritative snapshot + identity hash)
# =============================================================================

def build_reference_plan(plan_id: str, schema_dict: JSONTree, *, schema_version: str) -> ReferencePlan:
    """Build a committed ReferencePlan from a validated raw schema dict. ``raw_snapshot``
    is the canonical enveloped snapshot of the WHOLE raw tree (so unmodeled fields are
    covered, contract group C); ``plan_hash`` is its SHA-256 — identical to the key the
    SnapshotStore returns when this snapshot is put()."""
    snapshot = reference_plan_snapshot(schema_dict, schema_version=schema_version)
    return ReferencePlan(
        plan_id=plan_id,
        plan_hash=hash_bytes(snapshot),
        content=load_plan_content(schema_dict),
        raw_snapshot=snapshot,
    )


def build_effective_plan(
    base_plan_id: str,
    base_plan_hash: str,
    effective_schema_dict: JSONTree,
    *,
    schema_version: str,
) -> EffectivePlan:
    """Build an EffectivePlan from the materialized (baseline+scenario) raw dict. Its
    hash is over the complete effective snapshot; the ``effective_plan`` envelope kind
    keeps it distinct from a ``reference_plan`` with identical payload bytes."""
    snapshot = effective_plan_snapshot(effective_schema_dict, schema_version=schema_version)
    return EffectivePlan(
        base_plan_id=base_plan_id,
        base_plan_hash=base_plan_hash,
        effective_plan_hash=hash_bytes(snapshot),
        content=load_plan_content(effective_schema_dict),
        raw_snapshot=snapshot,
    )
