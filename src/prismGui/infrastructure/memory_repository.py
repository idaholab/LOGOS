"""infrastructure/memory_repository.py — Phase-1 in-memory RepositoryPort.

Dict-backed entity store keyed by id, for the life of a session/process. Entities are
already deeply-immutable domain objects (frozen dataclasses with tuple fields), so the
store holds them directly — no copy needed to prevent mutation. ``load_*`` raises
``KeyError`` for an unknown id, matching the port contract (a typed error, never a
silent ``None``). A persistent store replaces this behind the same port later.

Infrastructure, but engine-free: it imports only the domain entity types and the port,
never PRISM.
"""

from __future__ import annotations

from prismGui.domain.plan import ReferencePlan
from prismGui.domain.results import RunResult
from prismGui.domain.run_config import RunConfig
from prismGui.domain.scenario import Scenario


class InMemoryRepository:
    """RepositoryPort backed by process-memory dicts."""

    def __init__(self) -> None:
        self._baselines: dict[str, ReferencePlan] = {}
        self._scenarios: dict[str, Scenario] = {}
        self._run_configs: dict[str, RunConfig] = {}
        self._run_results: dict[str, RunResult] = {}

    def save_baseline(self, plan: ReferencePlan) -> None:
        self._baselines[plan.plan_id] = plan

    def load_baseline(self, plan_id: str) -> ReferencePlan:
        return self._baselines[plan_id]

    def save_scenario(self, scenario: Scenario) -> None:
        self._scenarios[scenario.scenario_id] = scenario

    def load_scenario(self, scenario_id: str) -> Scenario:
        return self._scenarios[scenario_id]

    def save_run_config(self, run_config: RunConfig) -> None:
        self._run_configs[run_config.run_config_id] = run_config

    def load_run_config(self, run_config_id: str) -> RunConfig:
        return self._run_configs[run_config_id]

    def save_run_result(self, result: RunResult) -> None:
        self._run_results[result.run_id] = result

    def load_run_result(self, run_id: str) -> RunResult:
        return self._run_results[run_id]
