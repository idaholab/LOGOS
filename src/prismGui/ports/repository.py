"""ports/repository.py — entity persistence seam (addressed by id).

Named now so the DTOs stay honestly serializable and the application layer can depend
on a persistence seam rather than a concrete store. This is the entity-by-id contract
(baseline / scenario / run_config / run_result); the immutable content-addressed BLOBS
are the separate ``SnapshotStorePort``.

Phase 1 ships only the in-memory implementation (``infrastructure.memory_repository``);
a persistent store slots in behind this same Protocol later. Pure seam: Protocol only,
depends on domain types for the annotations, imports neither PRISM nor Streamlit.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from prismGui.domain.plan import ReferencePlan
from prismGui.domain.results import RunResult
from prismGui.domain.run_config import RunConfig
from prismGui.domain.scenario import Scenario


@runtime_checkable
class RepositoryPort(Protocol):
    """Persistence seam for entities addressed by id. ``load_*`` raises ``KeyError``
    for an unknown id (a typed error, never a silent ``None``)."""

    def save_baseline(self, plan: ReferencePlan) -> None: ...
    def load_baseline(self, plan_id: str) -> ReferencePlan: ...

    def save_scenario(self, scenario: Scenario) -> None: ...
    def load_scenario(self, scenario_id: str) -> Scenario: ...

    def save_run_config(self, run_config: RunConfig) -> None: ...
    def load_run_config(self, run_config_id: str) -> RunConfig: ...

    def save_run_result(self, result: RunResult) -> None: ...
    def load_run_result(self, run_id: str) -> RunResult: ...
