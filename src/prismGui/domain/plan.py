"""domain/plan.py — PlanContent + ReferencePlan / EffectivePlan / PlanDraft.

PlanContent and both committed plan types are frozen AND hold tuples, so a baseline
cannot be mutated in place. Editing happens only via a PlanDraft (§2b) — the sole
mutable plan type. Time in the typed view is float hour-offsets from a tz-aware
project start; the authoritative ISO form lives in ``raw_snapshot``. Mirrors the
skeleton and model-spec §2/§2b. Pure: stdlib + domain.issues only.

The pure editing operations live here: ``open_draft`` seeds a draft from a committed
plan's raw payload, ``apply_patch`` stages a single structural edit (lightweight — no
schema/referential validation), and ``discard_draft`` clears the working state. The
FULL commit (``commit_draft``) is orchestrated in the application layer, not here: it
must run the ValidationPort, which the domain cannot import without a cycle
(ports/validation.py imports domain.issues). See application/services.commit_draft.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity

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
# Editing lifecycle (§2b) — the mutable draft, its patch ops, and the pure
# open/apply/discard operations. The full commit is in application/services.
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


# -----------------------------------------------------------------------------
# JSON-Pointer patch engine (module-private, RFC-6901-style)
# -----------------------------------------------------------------------------

class _PointerError(Exception):
    """A JSON Pointer does not resolve, or an op is mechanically invalid against
    the working tree. Caught by apply_patch and turned into an INVALID_PATCH issue —
    never propagated to callers."""


def _split_pointer(path: str) -> list[str]:
    """RFC-6901 pointer -> reference tokens. Leading '/' required (a non-empty path
    that does not start with '/' is malformed); '~1' un-escapes to '/', '~0' to '~'
    (in that order). The empty string is the whole-document pointer -> no tokens."""
    if path == "":
        return []
    if not path.startswith("/"):
        raise _PointerError(f"pointer must start with '/': {path!r}")
    return [tok.replace("~1", "/").replace("~0", "~") for tok in path[1:].split("/")]


def _descend(node: Any, token: str) -> Any:
    """One step down: dict by key, list by integer index. Raises _PointerError on a
    missing key, a non-integer / out-of-range list index, or a scalar dead-end."""
    if isinstance(node, dict):
        if token not in node:
            raise _PointerError(f"key {token!r} not found")
        return node[token]
    if isinstance(node, list):
        idx = _list_index(node, token, allow_end=False)
        return node[idx]
    raise _PointerError(f"cannot descend into scalar with token {token!r}")


def _resolve(tree: JSONTree, tokens: list[str]) -> Any:
    """Follow every token from the root; raise _PointerError on the first miss."""
    node: Any = tree
    for tok in tokens:
        node = _descend(node, tok)
    return node


def _list_index(seq: list, token: str, *, allow_end: bool) -> int:
    """Parse a list index token. With allow_end, '-' and len(seq) are the append
    position; otherwise the index must address an existing element [0, len)."""
    if allow_end and token == "-":
        return len(seq)
    try:
        idx = int(token)
    except (TypeError, ValueError):
        raise _PointerError(f"invalid list index {token!r}")
    upper = len(seq) if allow_end else len(seq) - 1
    if idx < 0 or idx > upper:
        raise _PointerError(f"list index {idx} out of range")
    return idx


def _apply(tree: JSONTree, action: "PatchAction", path: str, value: Any) -> None:
    """Mutate ``tree`` in place per one PatchOp. Raises _PointerError on any
    structural problem (bad path, index out of range, remove/replace of a missing
    target) so the caller can reject the op without having touched the tree — the
    parent is resolved first, and only the final assignment mutates."""
    tokens = _split_pointer(path)
    if not tokens:
        raise _PointerError("empty path cannot be patched")
    *parent_tokens, last = tokens
    parent = _resolve(tree, parent_tokens)

    if isinstance(parent, dict):
        if action is PatchAction.ADD or action is PatchAction.REPLACE:
            if action is PatchAction.REPLACE and last not in parent:
                raise _PointerError(f"replace target {last!r} does not exist")
            parent[last] = value
        elif action is PatchAction.REMOVE:
            if last not in parent:
                raise _PointerError(f"remove target {last!r} does not exist")
            del parent[last]
    elif isinstance(parent, list):
        if action is PatchAction.ADD:
            parent.insert(_list_index(parent, last, allow_end=True), value)
        elif action is PatchAction.REPLACE:
            parent[_list_index(parent, last, allow_end=False)] = value
        elif action is PatchAction.REMOVE:
            del parent[_list_index(parent, last, allow_end=False)]
    else:
        raise _PointerError(f"cannot patch scalar parent at {path!r}")


# -----------------------------------------------------------------------------
# Pure editing operations
# -----------------------------------------------------------------------------

def open_draft(plan: ReferencePlan) -> PlanDraft:
    """Seed a mutable draft from a committed plan's authoritative raw payload. The
    working tree is a deep copy of the envelope's ``payload`` (the schema-shaped tree),
    so edits never reach back into the immutable baseline; the audit log starts empty."""
    payload = json.loads(plan.raw_snapshot)["payload"]
    return PlanDraft(
        base_plan_id=plan.plan_id,
        raw_working_tree=copy.deepcopy(payload),
        pending_patches=[],
    )


def apply_patch(draft: PlanDraft, patch: "PatchOp") -> PatchOutcome:
    """Stage ONE structural edit. Lightweight by contract: it resolves the pointer and
    checks the op is mechanically valid, then mutates ``raw_working_tree`` in place and
    appends to ``pending_patches`` — it does NOT run schema or referential validation
    (that is commit's job). A structurally-broken op (path does not resolve, or add/
    replace with no value) returns ok=False with an INVALID_PATCH issue and mutates
    nothing.

    Documented limitation: a ``None`` value on add/replace is read as "no value
    provided", so a literal JSON ``null`` cannot be set through this thin editor."""
    if patch.action in (PatchAction.ADD, PatchAction.REPLACE) and patch.value is None:
        return PatchOutcome(
            ok=False,
            issues=(_invalid_patch(patch, "add/replace requires a value"),),
            draft=draft,
        )
    # Trial-apply on a copy first, so a mid-descent failure never leaves the working
    # tree half-mutated; only commit the mutation to the live tree once it succeeds.
    trial = copy.deepcopy(draft.raw_working_tree)
    try:
        _apply(trial, patch.action, patch.path, patch.value)
    except _PointerError as exc:
        return PatchOutcome(
            ok=False,
            issues=(_invalid_patch(patch, str(exc)),),
            draft=draft,
        )
    draft.raw_working_tree = trial
    draft.pending_patches.append(patch)
    return PatchOutcome(ok=True, issues=(), draft=draft)


def discard_draft(draft: PlanDraft) -> None:
    """Abandon the draft: clear its working tree and audit log. The committed baseline
    is never touched (the draft only ever held a copy), so nothing else is affected."""
    draft.raw_working_tree = {}
    draft.pending_patches = []
    return None


def _invalid_patch(patch: "PatchOp", detail: str) -> Issue:
    """Build the ERROR/SCHEMA issue apply_patch emits for a structurally bad op."""
    return Issue(
        code=IssueCode.INVALID_PATCH,
        severity=Severity.ERROR,
        category=IssueCategory.SCHEMA,
        message=f"invalid {patch.action.value} patch: {detail}",
        field_path=patch.path,
    )
