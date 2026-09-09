"""
PRISM GUI — Python skeletons (Phase 1–2)
=========================================

Generated from the Model & Interface Spec, revised for deep immutability,
explicit snapshot storage, optional scenarios, non-self-referential hashing,
and a distinct EffectivePlan. Contracts only: no implementation bodies.

Module layout (mirrors ports-&-adapters; split into real files when implementing):

    domain/
        issues.py            # Issue, Severity, codes
        plan.py              # PlanContent, ReferencePlan, EffectivePlan, PlanDraft, pools
        scenario.py          # Scenario
        run_config.py        # RunConfig
        results.py           # RunResult, DTOs, Provenance, Disposition, Freshness
        materialize.py       # materialize / validate_run_config / prepare_run
        disposition.py       # compute_disposition
        freshness.py         # assess_freshness (derived, not stored)
        hashing.py           # explicit hash payloads + canonicalization
    ports/
        execution.py         # ExecutionPort, RunRequest, RunStatus
        repository.py        # RepositoryPort (entities by id)
        snapshot_store.py    # SnapshotStorePort (content-addressed blobs)
        validation.py        # ValidationPort (schema + referential-integrity seam)
    application/
        accessors.py         # SessionAccessors
    infrastructure/
        prism_executor.py    # in-process ExecutionPort impl (adapter -> PRISM)
        memory_snapshot_store.py  # Phase 1 in-memory SnapshotStorePort
        validation_adapter.py     # ValidationPort impl over src/CPM/validate_outage_data.py

INVARIANTS (enforced by implementations, asserted by tests):
  * Domain imports neither Streamlit nor PRISM.
  * A fresh PRISM runtime is built per run; runtime objects are never stored on
    any domain object, never reused across runs.
  * Editing never mutates a committed ReferencePlan. Edits live on a PlanDraft
    (mutable raw tree + pending patches); commit = patch _raw -> rehydrate typed
    view -> validate -> produce a new immutable ReferencePlan.
  * RunResult holds neutral, deeply-immutable DTOs (no live PRISM/plotting/DataFrame).
  * Every hash in Provenance points to bytes that exist in the SnapshotStore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable


# Time convention: domain *_hours / start / end / w_min / w_max are float hour-offsets
# from a timezone-aware project start. The schema-shaped ISO dict is authoritative
# (it is what gets hashed, validated, and fed to OutageData.from_dict, which is
# datetime-native). ISO->hour-offset conversion is an OUTPUT-side concern: the PRISM
# adapter reads datetime results back into hour-offset DTOs, quantized to the 1 ms
# grid (canonicalization spec q()). See prism-gui-prism-adapter.md §1.
Hours = float
Hash = str                    # content hash from canonical serialization
JSONTree = dict[str, Any]     # authoritative raw parsed JSON
Canonical = str               # a canonical serialization string


# =============================================================================
# domain/issues.py
# =============================================================================

class Severity(str, Enum):
    ERROR = "error"      # blocks commit or feasibility
    WARNING = "warning"  # does not block; affects disposition
    INFO = "info"        # advisory


class IssueCategory(str, Enum):
    SCHEMA = "schema"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    FEASIBILITY = "feasibility"
    TIME_WINDOW = "time_window"
    DOSE = "dose"
    SYSTEM_STATE = "system_state"
    EXECUTION = "execution"
    PROVENANCE = "provenance"


class IssueCode(str, Enum):
    """Phase 1–2 core catalogue. Extensible; tests and UI may depend on these."""
    SCHEMA_TYPE_ERROR = "SCHEMA_TYPE_ERROR"
    SCHEMA_RANGE_ERROR = "SCHEMA_RANGE_ERROR"
    REF_MISSING = "REF_MISSING"
    DUP_ID = "DUP_ID"
    DUP_DEPENDENCY = "DUP_DEPENDENCY"
    # DEP_CYCLE also covers a self-referencing successor / a hold point blocking
    # itself (a degenerate 1-cycle). The existing validator reports these the same
    # way, so there is no separate DEP_SELF_LOOP code (see model spec §1 and §8b).
    DEP_CYCLE = "DEP_CYCLE"
    HOLD_POINT_MISUSE = "HOLD_POINT_MISUSE"   # non-hold-point task carries hold_point_type/blocks_tasks
    INVALID_MODE = "INVALID_MODE"
    INVALID_TIME_WINDOW = "INVALID_TIME_WINDOW"
    INVALID_AVAILABILITY_INTERVAL = "INVALID_AVAILABILITY_INTERVAL"
    INSUFFICIENT_RESOURCE = "INSUFFICIENT_RESOURCE"   # coarse resource-sufficiency shortfall (warning)
    EMERGENT_ID_COLLISION = "EMERGENT_ID_COLLISION"
    MATERIALIZE_CONFLICT = "MATERIALIZE_CONFLICT"
    UNSCHEDULED_TASK = "UNSCHEDULED_TASK"
    DEP_VIOLATION = "DEP_VIOLATION"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    PROV_HASH_MISMATCH = "PROV_HASH_MISMATCH"
    SNAPSHOT_MISSING = "SNAPSHOT_MISSING"


@dataclass(frozen=True)
class Issue:
    """Structured validation/diagnostic finding. Never a raw PRISM string."""
    code: IssueCode
    severity: Severity
    category: IssueCategory
    message: str
    entity_type: Optional[str] = None   # "task" | "dependency" | "resource" | ...
    entity_id: Optional[str] = None
    field_path: Optional[str] = None    # JSON Pointer-like path to the exact field
    suggested_action: Optional[str] = None


def has_blocking(issues: tuple[Issue, ...]) -> bool:
    """True if any issue is ERROR severity."""
    ...


# =============================================================================
# domain/plan.py  —  PlanContent + ReferencePlan / EffectivePlan / PlanDraft
# =============================================================================
# Immutability: PlanContent and both plan types are frozen AND hold tuples, so the
# committed baseline cannot be mutated in place. Editing happens only via PlanDraft.

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
    hold_point_type: Optional[str] = None      # generic; NRC/QA/Eng/Ops under outage profile
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
    """Runtime-affecting discriminator (schema: resources[].resource_type). Typed per
    the refined thin-view rule — the adapter builds a DIFFERENT PRISM constraint for
    each, so it cannot ride in _raw."""
    RENEWABLE = "renewable"      # capacity resets each availability period
    CONSUMABLE = "consumable"    # capacity draws down permanently over the outage (e.g. dose budget)


@dataclass(frozen=True)
class ResourcePool:
    """Crew-skill or consumable-budget pool.

    resource_type is runtime-affecting (renewable resets per period; consumable draws
    down permanently). dose_budget_per_worker_mrem carries dose ON the resource
    (matching outage_schema.json) — meaningful only when resource_type == CONSUMABLE;
    total pool budget = value x peak count across availability_periods. There is no
    standalone DoseBudget type and no top-level dose_budgets list (see model spec §2)."""
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


@dataclass(frozen=True)
class PlanMeta:
    outage_id: str
    start_date: datetime             # timezone-aware; serialization converts to/from ISO
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
    # No dose_budgets: dose is carried on a consumable-type ResourcePool
    # (resource_type == CONSUMABLE + dose_budget_per_worker_mrem), matching the schema.


@dataclass(frozen=True)
class ReferencePlan:
    """
    Committed, immutable, validated baseline.

    plan_hash is the revision identity a Scenario binds to and the lineage anchor.
    raw_snapshot is the canonical serialization of the authoritative raw tree
    (stored in the SnapshotStore); the typed `content` is the synchronized view.
    """
    plan_id: str
    plan_hash: Hash
    content: PlanContent
    raw_snapshot: Canonical


@dataclass(frozen=True)
class EffectivePlan:
    """
    Materialized scheduling input (baseline + scenario applied). DISTINCT from
    ReferencePlan so a scenario can't be bound to an effective hash, and a
    materialized plan can't be edited as though it were the baseline.
    """
    base_plan_id: str
    base_plan_hash: Hash
    effective_plan_hash: Hash
    content: PlanContent
    raw_snapshot: Canonical


@dataclass
class PlanDraft:
    """
    Mutable editing workspace — the ONLY mutable plan type. Holds the raw working
    tree and pending patches. Never scheduled or hashed directly; commit rehydrates
    a fresh immutable ReferencePlan (patch _raw -> rehydrate -> validate -> commit).
    """
    base_plan_id: str
    raw_working_tree: JSONTree
    pending_patches: list["PatchOp"] = field(default_factory=list)


# --- Editing operations (callable contract for the draft/commit lifecycle) ----

class PatchAction(str, Enum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


@dataclass(frozen=True)
class PatchOp:
    """JSON-Patch-like operation over the authoritative raw tree via a stable path."""
    action: PatchAction
    path: str                          # JSON Pointer-like path into the raw tree
    value: Optional[Any] = None        # required for add/replace; ignored for remove


@dataclass
class PatchOutcome:
    ok: bool
    issues: tuple["Issue", ...]
    draft: Optional[PlanDraft] = None  # draft with the patch appended, if accepted


@dataclass
class CommitOutcome:
    ok: bool
    issues: tuple["Issue", ...]
    plan: Optional[ReferencePlan] = None   # new immutable committed plan, if valid


def open_draft(plan: ReferencePlan) -> PlanDraft:
    """Start an editing session: copy the authoritative raw tree into a PlanDraft.
    Does not touch the committed plan."""
    ...


def apply_patch(draft: PlanDraft, patch: "PatchOp") -> PatchOutcome:
    """Validate and append a patch to the draft's pending list (lightweight,
    per-patch structural checks). Does not commit."""
    ...


def commit_draft(draft: PlanDraft, schema: JSONTree) -> CommitOutcome:
    """
    Apply pending patches to a copy of the raw tree, rehydrate the typed view,
    run full schema + referential-integrity validation, and — only if clean —
    produce a NEW immutable ReferencePlan with a recomputed plan_hash. The typed
    view is never committed directly.
    """
    ...


def discard_draft(draft: PlanDraft) -> None:
    """Cancel: drop the draft. The committed baseline is unaffected (no-op on it)."""
    ...


# =============================================================================
# domain/scenario.py
# =============================================================================

@dataclass(frozen=True)
class ResourceChange:
    skill_type: str
    from_hour: Hours
    new_count: int


@dataclass(frozen=True)
class EquipmentChange:
    equipment_id: str
    from_hour: Hours
    new_quantity: int        # 0 == OOS


@dataclass(frozen=True)
class DurationOverride:
    """Tuple-backed record (not a dict) so the frozen Scenario is deeply immutable
    and ordering is canonical."""
    task_id: str
    duration_hours: Hours


@dataclass(frozen=True)
class HoldPointReleaseOverride:
    target_id: str           # task_id or hold_id
    release_hour: Hours


@dataclass(frozen=True)
class Scenario:
    """
    Delta over a baseline. Holds no schedule. Binds to base_plan_hash (a baseline
    edit can leave base_plan_id unchanged); materialization checks the hash.

    NOTE ON None vs. empty: Scenario fields are a delta by nature. None here means
    "does not touch"; a future replan may need to distinguish that from "explicitly
    clears," so these stay Optional rather than defaulting to empty. All collections
    are tuple-backed records (no dicts) to keep the frozen type deeply immutable.

    Change ordering/collision (resource_changes / equipment_changes):
      * applied in ascending from_hour
      * multiple changes at same hour for same entity are invalid
      * a temporary outage is two changes (0 at start, restored at end)
    """
    scenario_id: str
    base_plan_id: str
    base_plan_hash: Hash
    name: Optional[str] = None
    checkpoint_hour: Optional[Hours] = None          # Phase 5; reserved
    duration_overrides: Optional[tuple[DurationOverride, ...]] = None
    resource_changes: Optional[tuple[ResourceChange, ...]] = None
    equipment_changes: Optional[tuple[EquipmentChange, ...]] = None
    hold_point_release_overrides: Optional[tuple[HoldPointReleaseOverride, ...]] = None
    emergent_tasks: Optional[tuple[Task, ...]] = None
    emergent_dependencies: Optional[tuple[Dependency, ...]] = None  # in/out edges + lags


# =============================================================================
# domain/run_config.py
# =============================================================================

class SGSVariant(str, Enum):
    MAX_USE_RES_RANKED = "max_use_res_ranked"    # default, production
    MAX_USE_RES_SHUFFLED = "max_use_res_shuffled"
    FIRST = "first"
    MD_KNAPSACK = "md_knapsack"
    LOOK_AHEAD = "look_ahead"


@dataclass(frozen=True)
class EvaluationWeights:
    """Post-hoc fitness weights. These change how completed schedules are COMPARED;
    they do NOT change the GA/ALNS search objective."""
    alpha: float = 1.0
    beta: float = 0.5
    gamma: float = 0.3
    delta: float = 2.0


@dataclass(frozen=True)
class ModeSelection:
    """Tuple-backed record (not a dict) so RunConfig is deeply immutable."""
    task_id: str
    mode_name: str


@dataclass(frozen=True)
class RunConfig:
    """How PRISM solves a (baseline + scenario). Execution-mode SELECTION lives here
    and is applied by the adapter to the fresh runtime — never to a working copy."""
    run_config_id: str
    sgs: SGSVariant = SGSVariant.MAX_USE_RES_RANKED
    priority_rule: str = "lf"                          # one of the 22-rule library keys
    mode_selections: tuple[ModeSelection, ...] = ()
    seed: int = 42
    scheduling_horizon_hours: Optional[Hours] = None   # renamed from max_time_hours (was ambiguous)
    evaluation_weights: Optional[EvaluationWeights] = None
    # optimization: reserved for GA/ALNS (Phase 6); absent in Phase 1–2. When added,
    # an optimizer wall-clock limit is a DISTINCT field from scheduling_horizon_hours.


# =============================================================================
# domain/results.py  —  neutral DTOs, provenance, disposition, freshness
# =============================================================================

class FloatClass(str, Enum):
    CRITICAL = "critical"              # red: on resource-constrained chain
    ZERO_FLOAT = "zero_float"         # orange: |actual TF| <= TF_ZERO_TOL and off chain
    POSITIVE_FLOAT = "positive_float" # blue: actual TF > TF_ZERO_TOL


# Negative-float classification rule (contract, so all adapters agree):
# actual TF may be slightly negative as an expected artifact of concurrent
# augmented-graph successors. Use classify_float() — do NOT reproduce inline.
TF_ZERO_TOL: Hours = 0.01


def classify_float(tf_actual_hours: Optional[Hours], on_constrained_chain: bool) -> FloatClass:
    """
    THE single source of the float-classification rule (no adapter reproduces it):
        on_constrained_chain            -> CRITICAL
        else tf_actual <= TF_ZERO_TOL   -> ZERO_FLOAT   (includes negative & None-as-0 per impl)
        else                            -> POSITIVE_FLOAT
    """
    ...


@dataclass(frozen=True)
class ActualResource:
    """Assignment after skill substitution (replaces dict[str, Any])."""
    skill_type: str          # the trade that actually performed the work
    crew_count: int


@dataclass(frozen=True)
class ScheduledActivityDTO:
    task_id: str
    start_hour: Hours
    end_hour: Hours
    duration: Hours
    delay_hours: Hours
    on_constrained_chain: bool
    float_class: FloatClass
    description: Optional[str] = None
    tf_actual_hours: Optional[Hours] = None      # may be negative (expected)
    actual_resources: tuple[ActualResource, ...] = ()
    wbs_group: Optional[str] = None


@dataclass(frozen=True)
class ScheduleDTO:
    makespan_hours: Hours
    cpm_lower_bound_hours: Hours
    optimism_gap_hours: Hours                     # makespan - cpm
    activities: tuple[ScheduledActivityDTO, ...]
    constrained_chain: tuple[str, ...]            # ordered task_ids
    cpm_critical_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class FitnessDTO:
    composite: float
    makespan_ratio: float
    delay_ratio: float
    criticality_ratio: float
    window_violation_ratio: float
    n_window_violations: int


@dataclass(frozen=True)
class DiagnosticsDTO:
    """Phase 1 minimal; grows later (chain-sets, idle-time, buffers...)."""
    fitness: Optional[FitnessDTO] = None
    dependency_violations: tuple[Issue, ...] = ()


class Tri(str, Enum):
    """Tri-state: false is not the same as 'not evaluated'."""
    TRUE = "true"
    FALSE = "false"
    NOT_EVALUATED = "not_evaluated"


class DispositionOverall(str, Enum):
    READY = "ready"
    READY_WITH_WARNINGS = "ready_with_warnings"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DispositionIndicators:
    input_valid: Tri
    schedule_complete: Tri
    hard_feasible: Tri
    has_unscheduled_tasks: Tri
    has_window_violations: Tri
    audit_passed: Tri


@dataclass(frozen=True)
class Disposition:
    overall: DispositionOverall
    indicators: DispositionIndicators


@dataclass(frozen=True)
class Provenance:
    """
    Content-addressed. Each result conceptually owns the full effective-plan
    snapshot; physically the bytes live once in the SnapshotStore, referenced
    here by hash. INVARIANT: every hash below resolves in the SnapshotStore.
    Versions split so app and PRISM can move independently.
    """
    baseline_snapshot_hash: Hash
    effective_plan_hash: Hash
    run_config_hash: Hash
    schema_version: str
    canonicalization_version: str    # distinct from schema_version
    app_version: str
    prism_version: str
    run_id: str
    timestamp: datetime
    scenario_delta_hash: Optional[Hash] = None


class RunResultStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RunResult:
    """Immutable, deeply so (tuples throughout). Neutral DTOs only."""
    run_id: str
    status: RunResultStatus
    provenance: Provenance
    issues: tuple[Issue, ...] = ()
    disposition: Optional[Disposition] = None
    schedule: Optional[ScheduleDTO] = None
    diagnostics: Optional[DiagnosticsDTO] = None


class Freshness(str, Enum):
    """Derived, never stored on RunResult (a cached flag can itself go stale)."""
    CURRENT = "current"          # inputs match present state
    STALE = "stale"              # displayed against inputs that have since changed
    DIFFERENT_CONFIG = "different_config"  # not the currently selected config (not "stale")


# =============================================================================
# domain/materialize.py
# =============================================================================

@dataclass
class MaterializeOutcome:
    ok: bool
    issues: tuple[Issue, ...]
    effective_plan: Optional[EffectivePlan] = None


@dataclass
class PrepareOutcome:
    ok: bool
    issues: tuple[Issue, ...]
    run_request: Optional["RunRequest"] = None


def materialize(
    reference_plan: ReferencePlan,
    scenario: Optional[Scenario],          # None == plain baseline run, no delta
) -> MaterializeOutcome:
    """
    Apply the scenario delta (if any) to the baseline and validate the COMBINATION.
    Does NOT take RunConfig. Catches emergent tasks referencing nonexistent
    entities (MATERIALIZE_CONFLICT) and base_plan_hash mismatch (PROV_HASH_MISMATCH).
    With scenario=None the effective plan mirrors the baseline content.
    """
    ...


def validate_run_config(effective_plan: EffectivePlan, run_config: RunConfig) -> tuple[Issue, ...]:
    """Validate config against the plan it will run on: mode selections reference
    existing tasks & valid modes (INVALID_MODE); SGS/rule keys valid."""
    ...


def prepare_run(
    reference_plan: ReferencePlan,
    scenario: Optional[Scenario],
    run_config: RunConfig,
    snapshot_store: "SnapshotStorePort",
) -> PrepareOutcome:
    """
    Orchestrate materialize() + validate_run_config(); ENSURE all referenced
    snapshots (baseline, scenario?, effective plan, run config) are persisted in
    snapshot_store; build a RunRequest only when no ERROR-severity issues remain.
    Guarantees every provenance hash in the eventual RunResult resolves to bytes.
    """
    ...


# =============================================================================
# domain/disposition.py
# =============================================================================

@dataclass
class ScheduleSummary:
    """Minimal facts compute_disposition needs, extracted from a produced schedule."""
    produced: bool
    n_unscheduled: int
    audit_ran: bool


def compute_disposition(summary: ScheduleSummary, issues: tuple[Issue, ...]) -> Disposition:
    """PURE domain policy with explicit, tested precedence turning indicators +
    issues into overall Ready / Ready-with-warnings / Blocked. Application layer
    orchestrates the call but does not decide the policy."""
    ...


# =============================================================================
# domain/freshness.py
# =============================================================================

def assess_freshness(
    result: RunResult,
    current_plan_hash: Hash,
    current_scenario_hash: Optional[Hash],
    current_run_config_hash: Optional[Hash] = None,
) -> Freshness:
    """
    Derive freshness by comparing the result's provenance hashes against the
    current lineage. STALE only when the displayed baseline/scenario has changed;
    a mere change of selected config is DIFFERENT_CONFIG, not STALE.
    """
    ...


# =============================================================================
# domain/hashing.py  —  explicit hash payloads over the AUTHORITATIVE snapshot
# =============================================================================
# CORRECTION: revision identity/provenance hashes are computed over the canonical
# RAW snapshot (the authoritative representation, incl. unmodeled fields), NOT the
# thin typed PlanContent. Two baselines that differ only in unmodeled fields must
# get different hashes, or provenance/stale-detection goes blind to those changes.
# A separate typed-content hash may exist for "did a GUI-relevant field change,"
# but it is not the revision identity.
# Prefer explicit serializers over a generic Any serializer so accidentally hashing
# a PRISM/UI object fails at the type boundary.

def hash_reference_plan(
    canonical_raw_snapshot: Canonical,
    schema_version: str,
    canonicalization_version: str,
) -> Hash:
    """Baseline / revision identity hash over the canonical AUTHORITATIVE raw
    snapshot + schema version + canonicalization version. This is the hash stored
    in SnapshotStore.put() and referenced in provenance."""
    ...


def hash_effective_plan(
    canonical_effective_snapshot: Canonical,
    base_plan_hash: Hash,
    scenario_hash: Optional[Hash],
    canonicalization_version: str,
) -> Hash:
    """Effective-plan hash over the complete canonical effective snapshot + lineage
    (base + scenario). Also the SnapshotStore key for the effective plan."""
    ...


def hash_scenario(scenario: Scenario, canonicalization_version: str) -> Hash:
    """Scenario hash over delta content + baseline binding (base_plan_hash).
    Excludes scenario_id and display name (non-semantic)."""
    ...


def hash_run_config(run_config: RunConfig, canonicalization_version: str) -> Hash:
    """Run-config hash over solver-affecting fields only. Excludes run_config_id."""
    ...


def hash_typed_content(content: PlanContent, schema_version: str, canonicalization_version: str) -> Hash:
    """OPTIONAL, separate from revision identity: a hash over the thin typed view,
    for detecting GUI-relevant changes. NOT used as plan_hash / provenance identity."""
    ...


# =============================================================================
# ports/execution.py
# =============================================================================

class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProvenanceInputs:
    """Explicit (was dict[str, Any]). Hashes needed to populate RunResult.provenance;
    bytes are already in the SnapshotStore by the time a request is submitted."""
    baseline_snapshot_hash: Hash
    effective_plan_hash: Hash
    run_config_hash: Hash
    schema_version: str
    canonicalization_version: str
    scenario_delta_hash: Optional[Hash] = None


@dataclass(frozen=True)
class RunRequest:
    """Serializable execution package produced by prepare_run(). Snapshots are
    referenced by hash (bytes live in the SnapshotStore); the adapter resolves the
    hashes to the schema-shaped ISO snapshots and feeds them to OutageData.from_dict.
    ISO->hour conversion happens on the adapter's output side (adapter spec §1)."""
    effective_plan_hash: Hash          # resolve via SnapshotStore
    run_config_hash: Hash              # resolve via SnapshotStore
    provenance_inputs: ProvenanceInputs
    request_id: Optional[str] = None


@runtime_checkable
class ExecutionPort(Protocol):
    """Job-oriented contract. Phase 1 in-process impl may complete inside submit()
    (get_status then returns COMPLETED immediately), keeping UX synchronous. A later
    background executor implements the SAME contract with real queue/progress/cancel."""
    def submit(self, request: RunRequest) -> str: ...           # returns RunId
    def get_status(self, run_id: str) -> RunStatus: ...
    def get_result(self, run_id: str) -> RunResult: ...         # valid at terminal state
    def cancel(self, run_id: str) -> None: ...


# =============================================================================
# ports/snapshot_store.py  —  content-addressed blob storage (separate port)
# =============================================================================
# Deliberately separate from RepositoryPort: content-addressed immutable blobs
# (put-returns-hash, dedup) are a different contract from entity save/load-by-id.

class SnapshotNotFoundError(Exception):
    """Raised by SnapshotStorePort.get() when a hash is absent. A DOMAIN-NEUTRAL
    storage exception — the executor catches it and converts to a SNAPSHOT_MISSING
    Issue on a failed RunResult. Storage mechanics stay separate from diagnostics."""
    def __init__(self, snapshot_hash: Hash) -> None:
        super().__init__(f"snapshot not found: {snapshot_hash}")
        self.snapshot_hash = snapshot_hash


@runtime_checkable
class SnapshotStorePort(Protocol):
    def put(self, canonical_snapshot: Canonical) -> Hash:
        """Store bytes; return their content hash. Idempotent (dedup by hash).
        The returned hash MUST equal the provenance hash computed for the same bytes."""
        ...

    def get(self, snapshot_hash: Hash) -> Canonical:
        """Retrieve bytes by hash. Raises SnapshotNotFoundError if absent."""
        ...

    def contains(self, snapshot_hash: Hash) -> bool: ...


# =============================================================================
# ports/repository.py  (entities by id; unimplemented in Phase 1–2)
# =============================================================================

@runtime_checkable
class RepositoryPort(Protocol):
    """Persistence seam for entities addressed by id. Named now so DTOs stay
    honestly serializable; no impl in Phase 1–2. (Snapshot BLOBS are the separate
    SnapshotStorePort.)"""
    def save_baseline(self, plan: ReferencePlan) -> None: ...
    def load_baseline(self, plan_id: str) -> ReferencePlan: ...
    def save_scenario(self, scenario: Scenario) -> None: ...
    def load_scenario(self, scenario_id: str) -> Scenario: ...
    def save_run_config(self, run_config: RunConfig) -> None: ...
    def load_run_config(self, run_config_id: str) -> RunConfig: ...
    def save_run_result(self, result: RunResult) -> None: ...
    def load_run_result(self, run_id: str) -> RunResult: ...


# =============================================================================
# ports/validation.py  —  schema + referential-integrity validation seam
# =============================================================================
# The domain and application depend on THIS port, never on the concrete
# src/CPM/validate_outage_data.py. An infrastructure adapter (below) wraps that
# existing pure validator and maps its (is_valid, errors, warnings) output to
# structured Issues. Reimplementing the rules in the domain would create two rule
# sets that silently drift (see model spec §8b). materialize() reuses this same
# port on the serialized effective plan.

@runtime_checkable
class ValidationPort(Protocol):
    def validate_plan(self, plan_data: JSONTree) -> tuple[Issue, ...]:
        """Validate a schema-shaped plan dict (a loaded baseline, or a serialized
        EffectivePlan from materialize()) and return structured Issues. "No
        ERROR-severity issue" == valid; is_valid is not surfaced separately. Never
        returns raw validator strings."""
        ...


# =============================================================================
# application/accessors.py  —  the ONLY code that knows session-state keys
# =============================================================================

class SessionAccessors(Protocol):
    """Thin broker over Streamlit session state. UI never touches st.session_state
    directly. Note: no mark_lineage_dirty — freshness is DERIVED via
    assess_freshness against a selected result id, not a stored flag."""
    def get_baseline(self) -> Optional[ReferencePlan]: ...
    def set_baseline(self, plan: ReferencePlan) -> None: ...

    def get_draft(self) -> Optional[PlanDraft]: ...
    def set_draft(self, draft: PlanDraft) -> None: ...
    def clear_draft(self) -> None: ...

    def get_scenario(self) -> Optional[Scenario]: ...
    def set_scenario(self, scenario: Optional[Scenario]) -> None: ...

    def list_run_results(self) -> tuple[RunResult, ...]: ...
    def add_run_result(self, result: RunResult) -> None: ...
    def get_run_result(self, run_id: str) -> Optional[RunResult]: ...

    def get_selected_result_id(self) -> Optional[str]: ...
    def set_selected_result_id(self, run_id: Optional[str]) -> None: ...


# =============================================================================
# infrastructure/prism_executor.py  —  in-process ExecutionPort (adapter -> PRISM)
# =============================================================================

class InProcessPrismExecutor:
    """
    Phase 1 ExecutionPort implementation. The ONLY code that builds a PRISM runtime.
    Resolves snapshots via the SnapshotStore, builds a FRESH runtime per request
    from the effective-plan snapshot (fresh-runtime invariant), applies RunConfig
    mode_selections when constructing it, translates PRISM output into neutral DTOs,
    and completes synchronously inside submit(). Depends on PRISM; domain must never
    import this module.
    """
    def __init__(self, snapshot_store: SnapshotStorePort) -> None: ...
    def submit(self, request: RunRequest) -> str: ...
    def get_status(self, run_id: str) -> RunStatus: ...
    def get_result(self, run_id: str) -> RunResult: ...
    def cancel(self, run_id: str) -> None: ...


# =============================================================================
# infrastructure/memory_snapshot_store.py  —  Phase 1 in-memory SnapshotStore
# =============================================================================

class InMemorySnapshotStore:
    """Phase 1 SnapshotStorePort backed by a dict in session/process memory.
    Replaced by a persistent content-addressed store when persistence lands."""
    def put(self, canonical_snapshot: Canonical) -> Hash: ...
    def get(self, snapshot_hash: Hash) -> Canonical: ...
    def contains(self, snapshot_hash: Hash) -> bool: ...


# =============================================================================
# infrastructure/validation_adapter.py  —  ValidationPort over validate_outage_data
# =============================================================================
# The ONLY code that imports src/CPM/validate_outage_data.py. Wraps the existing
# pure Draft7 + referential-integrity validator and maps its string output to
# structured Issues (full message->code table in model spec §8b). Infrastructure,
# exactly like the PRISM adapter: the domain depends on ValidationPort, never here.
#
# Isolates the GUI from the module's CLI-isms (it was written as a script):
#   1. No process exits. The module calls sys.exit(1) on a missing jsonschema
#      import and on a falsy schema_path; a library must never kill Streamlit, so
#      the adapter guarantees a real schema_path and treats jsonschema as a hard dep.
#   2. Always pass the real schema path. The not-found fallback references an
#      undefined DEFAULT_SCHEMA (NameError), so the adapter resolves and passes
#      src/CPM/outage_schema.json explicitly.
#   3. Strings, not records. Today the mapping parses stable message strings; the
#      durable fix is a backward-compatible structured-emit refactor of the
#      validator so CLI and GUI share one source and there is no string-parsing.

class OutageValidatorAdapter:
    """
    Phase 1 ValidationPort implementation. Calls
    OutageDataValidator(schema_path).validate(plan_data, strict_resource_overlaps=...)
    -> (is_valid, errors, warnings); maps errors -> Issue(ERROR) and
    warnings -> Issue(WARNING), assigning code / category / entity_type / entity_id /
    field_path per the §8b table. is_valid is dropped (redundant with "no
    ERROR-severity issue"). Depends on validate_outage_data; domain must never
    import this module.
    """
    def __init__(self, schema_path: str, *, strict_resource_overlaps: bool = False) -> None: ...
    def validate_plan(self, plan_data: JSONTree) -> tuple[Issue, ...]: ...
