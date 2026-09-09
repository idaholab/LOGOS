"""domain/plan.py — PlanContent + ReferencePlan / EffectivePlan / PlanDraft.

PlanContent and both committed plan types are frozen AND hold tuples, so a baseline
cannot be mutated in place. Editing happens only via a PlanDraft (§2b) — the sole
mutable plan type. Time in the typed view is float hour-offsets from a tz-aware
project start; the authoritative ISO form lives in ``raw_snapshot``. Mirrors the
skeleton and model-spec §2/§2b. Pure: stdlib only.

Editing operations (open_draft / apply_patch / commit_draft / discard_draft) are
DEFERRED to Phase 2 — the types are defined here so the Phase-2 contract tests
collect, but the operations raise NotImplementedError until Phase 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

Hours = float
Hash = str
JSONTree = dict[str, Any]
Canonical = str


# -----------------------------------------------------------------------------
# Task-level value objects
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class TimeWindow:
    w_min: Hours
    w_max: Hours


@dataclass(frozen=True)
class ResourceReq:
    skill_type: str          # ref -> ResourcePool.skill_type
    crew_count: int


@dataclass(frozen=True)
class EquipmentReq:
    equipment_id: str        # ref -> EquipmentItem
    quantity_needed: int


@dataclass(frozen=True)
class ConsumableDemand:
    material_id: str         # ref -> Consumable
    quantity: float


@dataclass(frozen=True)
class SystemStateReq:
    system_id: str           # ref -> PlantSystem
    state: str


@dataclass(frozen=True)
class HoldPoint:
    # No redundant is_hold_point flag: Task.hold_point is not None carries that.
    hold_point_type: Optional[str] = None
    blocks_tasks: tuple[str, ...] = ()          # refs -> Task


@dataclass(frozen=True)
class ExecutionMode:
    mode_name: str           # "normal" | "crash" | "reduced_crew" | ...
    duration: Hours
    crew: tuple[ResourceReq, ...] = ()
    equipment: tuple[EquipmentReq, ...] = ()
    dose_rate: Optional[float] = None
    mobilization_lead_hours: Optional[Hours] = None


@dataclass(frozen=True)
class Task:
    """Thin task view. Dependency edges are NOT here — see PlanContent.dependencies.
    Absent optional collections default to empty (absent == empty in meaning)."""
    task_id: str
    duration: Hours
    description: Optional[str] = None
    required_resources: tuple[ResourceReq, ...] = ()
    required_equipment: tuple[EquipmentReq, ...] = ()
    location_id: Optional[str] = None
    zone_ids: tuple[str, ...] = ()
    consumable_demands: tuple[ConsumableDemand, ...] = ()
    required_states: tuple[SystemStateReq, ...] = ()
    time_windows: tuple[TimeWindow, ...] = ()
    hold_point: Optional[HoldPoint] = None
    execution_modes: tuple[ExecutionMode, ...] = ()   # available; selection is in RunConfig
    dose_rate: Optional[float] = None                  # mRem/worker/hour
    mobilization_lead_hours: Optional[Hours] = None
    wbs_group: Optional[str] = None
    alternative_skill_types: tuple[str, ...] = ()      # ordered


@dataclass(frozen=True)
class Dependency:
    """Normalized top-level edge. Serialized back to per-task successors on export."""
    predecessor_id: str
    successor_id: str
    lag_hours: Hours = 0.0


# -----------------------------------------------------------------------------
# Resource / equipment / location / consumable / system pools
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceAvailability:
    """Half-open [start, end) hour-offsets."""
    start: Hours
    end: Hours
    count: int
    reason: Optional[str] = None


@dataclass(frozen=True)
class EquipmentAvailability:
    start: Hours
    end: Hours
    quantity: int
    reason: Optional[str] = None


@dataclass(frozen=True)
class LocationAvailability:
    start: Hours
    end: Hours
    max_concurrent_tasks: int
    max_concurrent_workers: int
    reason: Optional[str] = None


class ResourceType(str, Enum):
    """Runtime-affecting discriminator (schema resources[].resource_type). The
    adapter builds a DIFFERENT PRISM constraint for each, so it cannot ride in _raw."""
    RENEWABLE = "renewable"      # capacity resets each availability period
    CONSUMABLE = "consumable"    # capacity draws down permanently (e.g. dose budget)


@dataclass(frozen=True)
class ResourcePool:
    """Crew-skill or consumable-budget pool. resource_type is runtime-affecting;
    dose_budget_per_worker_mrem carries dose ON the resource (matching the schema),
    meaningful only when resource_type == CONSUMABLE."""
    skill_type: str
    resource_type: ResourceType = ResourceType.RENEWABLE
    availability_periods: tuple[ResourceAvailability, ...] = ()
    dose_budget_per_worker_mrem: Optional[float] = None


@dataclass(frozen=True)
class EquipmentItem:
    equipment_id: str
    description: Optional[str] = None
    availability_periods: tuple[EquipmentAvailability, ...] = ()
    zone_affinity: Optional[str] = None   # ref -> LocationZone


@dataclass(frozen=True)
class LocationZone:
    location_id: str
    description: Optional[str] = None
    is_confined_space: Optional[bool] = None
    availability_periods: tuple[LocationAvailability, ...] = ()


@dataclass(frozen=True)
class RestockDelivery:
    hour: Hours
    quantity: float


@dataclass(frozen=True)
class Consumable:
    material_id: str
    initial_stock: float
    restock_deliveries: tuple[RestockDelivery, ...] = ()


@dataclass(frozen=True)
class PlantSystem:
    system_id: str
    valid_states: tuple[str, ...] = ()


# -----------------------------------------------------------------------------
# Meta + shared content + committed plans
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class PlanMeta:
    outage_id: str
    start_date: datetime             # anchor for hour-offset conversion
    working_hours_per_day: float
    shift_start_hour: Optional[float] = None
    target_end_date: Optional[datetime] = None


@dataclass(frozen=True)
class PlanContent:
    """Shared, deeply-immutable scheduling content. Reused by ReferencePlan and
    EffectivePlan so both share structure without sharing identity."""
    meta: PlanMeta
    tasks: tuple[Task, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    resources: tuple[ResourcePool, ...] = ()
    equipment: tuple[EquipmentItem, ...] = ()
    locations: tuple[LocationZone, ...] = ()
    consumables: tuple[Consumable, ...] = ()
    systems: tuple[PlantSystem, ...] = ()


@dataclass(frozen=True)
class ReferencePlan:
    """Committed, immutable, validated baseline. plan_hash is the revision identity
    a Scenario binds to; raw_snapshot is the canonical serialization of the
    authoritative raw tree (stored in the SnapshotStore); content is the synced view."""
    plan_id: str
    plan_hash: Hash
    content: PlanContent
    raw_snapshot: Canonical


@dataclass(frozen=True)
class EffectivePlan:
    """Materialized scheduling input (baseline + scenario applied). DISTINCT from
    ReferencePlan so a scenario can't be bound to an effective hash, and a
    materialized plan can't be edited as though it were the baseline."""
    base_plan_id: str
    base_plan_hash: Hash
    effective_plan_hash: Hash
    content: PlanContent
    raw_snapshot: Canonical


# -----------------------------------------------------------------------------
# Editing lifecycle (Phase 2) — types importable now; operations deferred
# -----------------------------------------------------------------------------

@dataclass
class PlanDraft:
    """Mutable editing workspace — the ONLY mutable plan type (§2b)."""
    base_plan_id: str
    raw_working_tree: JSONTree
    pending_patches: list["PatchOp"] = field(default_factory=list)


class PatchAction(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


@dataclass(frozen=True)
class PatchOp:
    """JSON-Patch-like operation over the authoritative raw tree via a stable path."""
    action: PatchAction
    path: str
    value: Optional[Any] = None        # required for add/replace; ignored for remove


@dataclass
class PatchOutcome:
    ok: bool
    issues: tuple["Issue", ...]        # noqa: F821 — forward ref, avoids import cycle
    draft: Optional[PlanDraft] = None


@dataclass
class CommitOutcome:
    ok: bool
    issues: tuple["Issue", ...]        # noqa: F821
    plan: Optional[ReferencePlan] = None


_PHASE2 = "editing (PlanDraft / commit) is deferred to Phase 2"


def open_draft(plan: ReferencePlan) -> PlanDraft:
    raise NotImplementedError(_PHASE2)


def apply_patch(draft: PlanDraft, patch: "PatchOp") -> PatchOutcome:
    raise NotImplementedError(_PHASE2)


def commit_draft(draft: PlanDraft, schema: JSONTree) -> CommitOutcome:
    raise NotImplementedError(_PHASE2)


def discard_draft(draft: PlanDraft) -> None:
    raise NotImplementedError(_PHASE2)
