"""domain/results.py — neutral result DTOs, provenance, disposition, freshness.

Everything a RunResult carries is deeply immutable (frozen + tuple-backed) and
neutral: no live PRISM object, no DataFrame, no plotting handle. The UI builds all
visuals from these. Mirrors the skeleton and model-spec §6. Pure: stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from prismGui.domain.issues import Issue

Hours = float
Hash = str


# -----------------------------------------------------------------------------
# Float classification (the single shared rule; no adapter reproduces it)
# -----------------------------------------------------------------------------

class FloatClass(str, Enum):
    CRITICAL = "critical"              # red: on the resource-constrained chain
    ZERO_FLOAT = "zero_float"          # orange: |actual TF| <= TF_ZERO_TOL, off chain
    POSITIVE_FLOAT = "positive_float"  # blue: actual TF > TF_ZERO_TOL


# Actual TF may be slightly negative — an expected artifact of concurrent
# augmented-graph successors. classify_float() is THE rule; do not inline it.
TF_ZERO_TOL: Hours = 0.01


def classify_float(tf_actual_hours: Optional[Hours], on_constrained_chain: bool) -> FloatClass:
    """
    THE single source of the float-classification rule (model-spec §6, adapter §5):
        on_constrained_chain            -> CRITICAL
        else tf_actual <= TF_ZERO_TOL   -> ZERO_FLOAT   (includes negative & None-as-0)
        else                            -> POSITIVE_FLOAT
    """
    if on_constrained_chain:
        return FloatClass.CRITICAL
    tf = 0.0 if tf_actual_hours is None else tf_actual_hours
    if tf <= TF_ZERO_TOL:
        return FloatClass.ZERO_FLOAT
    return FloatClass.POSITIVE_FLOAT


# -----------------------------------------------------------------------------
# Schedule DTOs
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ActualResource:
    """Assignment after skill substitution (replaces dict[str, Any])."""
    skill_type: str
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


# -----------------------------------------------------------------------------
# Resource utilization (demand vs. time-varying capacity, per skill)
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class UtilizationInterval:
    """Half-open [start_hour, end_hour) over which both demand and capacity are constant."""
    start_hour: Hours
    end_hour: Hours
    demand: int          # crew of this skill active on [start_hour, end_hour)
    available: int       # capacity of this skill on that interval


@dataclass(frozen=True)
class SkillUtilizationSeries:
    skill_type: str
    intervals: tuple[UtilizationInterval, ...]


@dataclass(frozen=True)
class ResourceUtilizationDTO:
    """The demand-vs-capacity timeline, one series per resource pool. Neutral (tuple-
    backed scalars) so it rides the immutable RunResult and the isolation walker."""
    horizon_hours: Hours
    series: tuple[SkillUtilizationSeries, ...]


@dataclass(frozen=True)
class DiagnosticsDTO:
    """Phase 1 minimal; grows later (chain-sets, idle-time, buffers...)."""
    fitness: Optional[FitnessDTO] = None
    dependency_violations: tuple[Issue, ...] = ()
    resource_utilization: Optional[ResourceUtilizationDTO] = None


# -----------------------------------------------------------------------------
# Disposition (tri-state indicators + overall summary)
# -----------------------------------------------------------------------------

class Tri(str, Enum):
    """Tri-state: false is NOT the same as 'not evaluated'."""
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


# -----------------------------------------------------------------------------
# Provenance
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Provenance:
    """
    Content-addressed. Each result conceptually owns the full effective-plan
    snapshot; physically the bytes live once in the SnapshotStore, referenced here
    by hash. INVARIANT: every hash below resolves in the SnapshotStore. Versions
    split so app / PRISM / canonicalization can move independently (model-spec §6).
    """
    baseline_snapshot_hash: Hash
    effective_plan_hash: Hash
    run_config_hash: Hash
    schema_version: str
    canonicalization_version: str
    app_version: str
    prism_version: str
    run_id: str
    timestamp: datetime
    scenario_delta_hash: Optional[Hash] = None


# -----------------------------------------------------------------------------
# RunResult
# -----------------------------------------------------------------------------

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
    CURRENT = "current"                    # inputs match present state
    STALE = "stale"                        # displayed against since-changed inputs
    DIFFERENT_CONFIG = "different_config"  # not the currently selected config (not "stale")
