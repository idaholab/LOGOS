"""Pure pipeline helpers: sample discovery, the validator, and the run pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from prismGui.application import services
from prismGui.domain.run_config import RunConfig
from prismGui.domain.scenario import Scenario
from prismGui.infrastructure.memory_repository import InMemoryRepository
from prismGui.infrastructure.memory_snapshot_store import InMemorySnapshotStore
from prismGui.infrastructure.validation_adapter import OutageValidatorAdapter


# app/main.py -> parents: [0]=app [1]=prismGui [2]=src [3]=repo root
REPO_ROOT = Path(__file__).resolve().parents[3]

SCHEMA_PATH = REPO_ROOT / "src" / "CPM" / "outage_schema.json"

EXAMPLES_DIR = REPO_ROOT / "doc" / "demos" / "rcpsp" / "examples"

# =============================================================================
# streamlit-free helpers (importable / testable without Streamlit)
# =============================================================================

def discover_samples() -> dict[str, str]:
    """Map each shipping sample project's display name to its absolute path (sorted).
    The canonical location the CPM suite reads — NOT the byte-identical tests/CPMmodel/
    copies."""
    if not EXAMPLES_DIR.is_dir():
        return {}
    return {p.stem: str(p) for p in sorted(EXAMPLES_DIR.glob("*.json"))}

@dataclass(frozen=True)
class PipelineResult:
    """Outcome of the app's load→prepare→run pipeline. ``ok`` is False if the load or the
    preparation blocked (``result`` is then None and ``issues`` explains why); otherwise
    ``result`` is the terminal ``RunResult``."""
    ok: bool
    stage: str                     # "load" | "prepare" | "run"
    issues: tuple
    result: Any = None
    reference_plan: Any = None

def run_pipeline(
    raw_plan: dict,
    plan_id: str,
    run_config: RunConfig,
    *,
    validator: OutageValidatorAdapter,
    store: InMemorySnapshotStore,
    executor,
    repository: Optional[InMemoryRepository] = None,
    scenario: Optional[Scenario] = None,
) -> PipelineResult:
    """The exact sequence the Run button triggers, factored out so it can be driven
    headlessly: load+validate → prepare_run → run. When a ``scenario`` is supplied it is
    materialized into the effective plan (baseline + delta) before the run; ``None`` runs
    the plain baseline. Blocking is reported per stage; a non-ok pipeline never fabricates
    a result."""
    load = services.load_and_validate(plan_id, raw_plan, validator)
    if not load.ok:
        return PipelineResult(ok=False, stage="load", issues=load.issues)

    prep = services.prepare_run(load.reference_plan, scenario, run_config, store, validator=validator)
    if not prep.ok:
        return PipelineResult(ok=False, stage="prepare", issues=prep.issues,
                              reference_plan=load.reference_plan)

    result = services.run(prep.run_request, executor, repository=repository)
    return PipelineResult(ok=True, stage="run", issues=result.issues, result=result,
                          reference_plan=load.reference_plan)

def build_validator() -> OutageValidatorAdapter:
    """The ValidationPort adapter over the shipping outage schema."""
    return OutageValidatorAdapter(str(SCHEMA_PATH))

def _make_executor(store: InMemorySnapshotStore):
    """Build the in-process PRISM executor (lazy import keeps the PRISM dependency inside
    the composition root's run path)."""
    from prismGui.infrastructure.prism_adapter import InProcessPrismExecutor
    return InProcessPrismExecutor(store)
