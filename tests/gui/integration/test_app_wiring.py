"""Integration — the app's composition root over the REAL executor (Step 7 backstop).

The Step-7 gate is a manual ``streamlit run`` smoke, which no headless env can perform
(Streamlit is not installed in the contract env, and AppTest with it). This test covers
everything about that smoke that is NOT visual rendering: it drives ``app/main.run_pipeline``
— the exact load → prepare → run wiring the Run button triggers — through the real
in-process PRISM executor on example_10, asserting the engine-verified metrics and
disposition come back COMPLETED. Builds a real ``Pert``, so it is ``adapter_integration``.

What remains for the human smoke is only that the widgets render and the tables/badges
display — the numbers themselves are pinned here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismGui.app import main as app_main
from prismGui.domain.results import DispositionOverall, RunResultStatus, Tri
from prismGui.domain.run_config import RunConfig, SGSVariant
from prismGui.infrastructure.memory_repository import InMemoryRepository
from prismGui.infrastructure.memory_snapshot_store import InMemorySnapshotStore

pytestmark = pytest.mark.adapter_integration


def test_app_pipeline_runs_example_10_end_to_end(example_10_path):
    """run_pipeline wires the concrete adapters exactly as main() does and reproduces the
    engine-probe-verified example_10 schedule."""
    raw = json.loads(Path(example_10_path).read_text())

    validator = app_main.build_validator()
    store = InMemorySnapshotStore()
    executor = app_main._make_executor(store)
    repo = InMemoryRepository()

    rc = RunConfig(run_config_id="rc-ex10", sgs=SGSVariant.MAX_USE_RES_RANKED,
                   priority_rule="lf", seed=42)
    outcome = app_main.run_pipeline(
        raw, "example_10", rc,
        validator=validator, store=store, executor=executor, repository=repo)

    assert outcome.ok and outcome.stage == "run"
    result = outcome.result
    assert result.status is RunResultStatus.COMPLETED
    assert result.schedule.makespan_hours == 85.0
    assert result.schedule.cpm_lower_bound_hours == 71.0
    assert result.schedule.optimism_gap_hours == 14.0
    assert result.disposition.overall is DispositionOverall.READY_WITH_WARNINGS
    assert result.disposition.indicators.audit_passed is Tri.TRUE

    # persisted through the wired repository, retrievable by identity
    assert repo.load_run_result(result.run_id) is result
