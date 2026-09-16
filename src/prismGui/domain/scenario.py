"""domain/scenario.py — a delta over a baseline (holds no schedule).

Every field is a *change*, not a full restatement. All override collections are
tuple-backed records (never maps) so the frozen Scenario is deeply immutable and
ordering is canonical. A field left ``None`` means "does not touch" — kept distinct
from an explicit empty change a future replan may need. Mirrors the skeleton and
model-spec §3. Pure: stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from prismGui.domain.plan import Dependency, Task

Hours = float
Hash = str


@dataclass(frozen=True)
class ResourceChange:
    skill_type: str
    from_hour: Hours
    new_count: int
    to_hour: Optional[Hours] = None   # None == open-ended (from_hour onward); else a bounded [from, to) window


@dataclass(frozen=True)
class EquipmentChange:
    equipment_id: str
    from_hour: Hours
    new_quantity: int                 # 0 == OOS
    to_hour: Optional[Hours] = None   # None == open-ended; else a bounded [from, to) window


@dataclass(frozen=True)
class LocationChange:
    location_id: str
    from_hour: Hours
    new_max_concurrent_tasks: int
    to_hour: Optional[Hours] = None                    # None == open-ended; else a bounded [from, to) window
    new_max_concurrent_workers: Optional[int] = None   # None == leave the worker cap untouched (may itself be "no cap")


@dataclass(frozen=True)
class DurationOverride:
    task_id: str
    duration_hours: Hours


@dataclass(frozen=True)
class HoldPointReleaseOverride:
    target_id: str           # task_id or hold_id
    release_hour: Hours


@dataclass(frozen=True)
class TaskSuppression:
    """Remove a baseline (or emergent) task from the effective plan. Materialization also
    strips the suppressed id from every remaining task's ``successors`` (dangling-edge cleanup)."""
    task_id: str


@dataclass(frozen=True)
class DependencySuppression:
    """Remove one precedence edge — drop ``successor_id`` from ``predecessor_id``'s successors —
    without removing either task. A non-existent edge is a MATERIALIZE_CONFLICT."""
    predecessor_id: str
    successor_id: str


@dataclass(frozen=True)
class Scenario:
    """
    Delta over a baseline. Binds to base_plan_hash (a baseline edit can leave
    base_plan_id unchanged); materialization checks the hash (PROV_HASH_MISMATCH).

    Change ordering / collision (resource_changes / equipment_changes):
      * applied in ascending from_hour
      * multiple changes at the same hour for the same entity are invalid
      * a temporary outage is two changes (0 at start, restored at end)
    """
    scenario_id: str
    base_plan_id: str
    base_plan_hash: Hash
    name: Optional[str] = None
    derived_from: Optional[str] = None               # scenario_id this was cloned from (None == branched from the
                                                     # baseline). A provenance/display label ONLY: excluded from the
                                                     # identity hash (like scenario_id / name — see hashing.scenario_payload)
                                                     # and ignored by materialization (a clone is a flat baseline overlay).
    checkpoint_hour: Optional[Hours] = None          # Phase 5; reserved
    duration_overrides: Optional[tuple[DurationOverride, ...]] = None
    resource_changes: Optional[tuple[ResourceChange, ...]] = None
    equipment_changes: Optional[tuple[EquipmentChange, ...]] = None
    location_changes: Optional[tuple[LocationChange, ...]] = None
    hold_point_release_overrides: Optional[tuple[HoldPointReleaseOverride, ...]] = None
    emergent_tasks: Optional[tuple[Task, ...]] = None
    emergent_dependencies: Optional[tuple[Dependency, ...]] = None
    task_suppressions: Optional[tuple[TaskSuppression, ...]] = None
    dependency_suppressions: Optional[tuple[DependencySuppression, ...]] = None
