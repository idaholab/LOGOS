"""Integration — the whole thin-slice loop through the application services (Step 6 gate).

Where ``test_prism_adapter.py`` gates the adapter in isolation, this gates the SERVICE
composition: a raw ``example_10.json`` driven through the real
``load_and_validate → prepare_run → run`` path with the in-process PRISM executor, the
in-memory snapshot store / repository, and the in-memory session state — exactly the wiring
``app/main.py`` performs, minus Streamlit. Builds a real ``Pert``, so the module is
``adapter_integration`` (runs in the integration job, never the pure-contract job).

It pins the loop end to end: the run completes into neutral DTOs with the engine's verified
metrics/disposition; the result is persisted and retrievable through the repository and the
session's selection; and freshness reads CURRENT against the lineage it ran on, flipping to
STALE the moment the baseline changes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from prismGui.application import services
from prismGui.application.services import InMemorySessionState
from prismGui.domain import serialization as ser
from prismGui.domain.results import DispositionOverall, Freshness, RunResultStatus, Tri
from prismGui.domain.run_config import RunConfig, SGSVariant
from prismGui.domain.versions import SCHEMA_VERSION
from prismGui.infrastructure.memory_repository import InMemoryRepository
from prismGui.infrastructure.memory_snapshot_store import InMemorySnapshotStore
from prismGui.infrastructure.prism_adapter import InProcessPrismExecutor

pytestmark = pytest.mark.adapter_integration


def test_service_loop_runs_example_10_end_to_end(example_10_path, validator_adapter):
    """load → validate → prepare → run → view, wired exactly as the app wires it."""
    store = InMemorySnapshotStore()
    repo = InMemoryRepository()
    executor = InProcessPrismExecutor(store)
    session = InMemorySessionState()

    raw = json.loads(Path(example_10_path).read_text())

    # --- load + validate ---
    load = services.load_and_validate("ex10", raw, validator_adapter)
    assert load.ok and load.reference_plan is not None
    session.set_baseline(load.reference_plan)

    # --- prepare (persist snapshots + provenance) ---
    rc = RunConfig(run_config_id="rc", sgs=SGSVariant.MAX_USE_RES_RANKED,
                   priority_rule="lf", seed=42)
    session.set_run_config(rc)
    prep = services.prepare_run(load.reference_plan, None, rc, store, validator=validator_adapter)
    assert prep.ok and prep.run_request is not None
    assert store.contains(prep.run_request.effective_plan_hash)

    # --- run + collect ---
    result = services.run(prep.run_request, executor, repository=repo)
    session.add_run_result(result)
    session.set_selected_result_id(result.run_id)

    assert result.status is RunResultStatus.COMPLETED
    assert result.schedule.makespan_hours == 85.0             # engine-probe verified
    assert result.schedule.cpm_lower_bound_hours == 71.0
    assert result.schedule.optimism_gap_hours == 14.0
    assert result.disposition.overall is DispositionOverall.READY_WITH_WARNINGS
    assert result.disposition.indicators.audit_passed is Tri.TRUE

    # --- view: retrievable through the repo and the session's selection ---
    assert repo.load_run_result(result.run_id) is result
    selected = session.get_run_result(session.get_selected_result_id())
    assert selected is result

    # --- freshness: current against the lineage it ran on ---
    assert services.current_freshness(result, baseline=load.reference_plan,
                                      run_config=rc) is Freshness.CURRENT

    # A changed baseline makes the shown result STALE (derived, never stored).
    mutated = copy.deepcopy(raw)
    mutated["outage"]["outage_id"] = raw["outage"]["outage_id"] + "-v2"
    other_baseline = ser.build_reference_plan("ex10-v2", mutated, schema_version=SCHEMA_VERSION)
    assert services.current_freshness(result, baseline=other_baseline) is Freshness.STALE
