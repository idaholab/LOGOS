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


@dataclass(frozen=True)
class EquipmentChange:
    equipment_id: str
    from_hour: Hours
    new_quantity: int        # 0 == OOS


@dataclass(frozen=True)
class DurationOverride:
    task_id: str
    duration_hours: Hours


@dataclass(frozen=True)
class HoldPointReleaseOverride:
    target_id: str           # task_id or hold_id
    release_hour: Hours


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
    checkpoint_hour: Optional[Hours] = None          # Phase 5; reserved
    duration_overrides: Optional[tuple[DurationOverride, ...]] = None
    resource_changes: Optional[tuple[ResourceChange, ...]] = None
    equipment_changes: Optional[tuple[EquipmentChange, ...]] = None
    hold_point_release_overrides: Optional[tuple[HoldPointReleaseOverride, ...]] = None
    emergent_tasks: Optional[tuple[Task, ...]] = None
    emergent_dependencies: Optional[tuple[Dependency, ...]] = None
