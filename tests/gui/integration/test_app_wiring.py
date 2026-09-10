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
from prismGui.domain.scenario import DurationOverride, ResourceChange, Scenario
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


def test_app_pipeline_runs_a_scenario_end_to_end(example_10_path):
    """run_pipeline with a scenario materializes baseline + delta and runs the EFFECTIVE plan
    through the real executor. Against a plain-baseline run of the same plan+config, the
    effective-plan hash differs and a scenario delta hash is stamped — proof the duration
    override + resource what-if are folded in, not silently dropped (the Increment-8 gap)."""
    raw = json.loads(Path(example_10_path).read_text())
    validator = app_main.build_validator()
    rc = RunConfig(run_config_id="rc-ex10", sgs=SGSVariant.MAX_USE_RES_RANKED,
                   priority_rule="lf", seed=42)

    # (a) a plain-baseline run for comparison
    store_base = InMemorySnapshotStore()
    base_out = app_main.run_pipeline(
        raw, "example_10", rc, validator=validator, store=store_base,
        executor=app_main._make_executor(store_base))
    assert base_out.ok and base_out.stage == "run"
    base_prov = base_out.result.provenance
    assert base_prov.scenario_delta_hash is None

    # (b) the SAME plan + config, now under a what-if: stretch P002 20h -> 30h and cut the
    # MECHANIC pool from 6 to 4 (== max single-task demand, so still schedulable) from hour 24.
    baseline = base_out.reference_plan
    scenario = Scenario(
        scenario_id="scn-e2e", base_plan_id=baseline.plan_id, base_plan_hash=baseline.plan_hash,
        name="e2e what-if",
        duration_overrides=(DurationOverride(task_id="P002", duration_hours=30.0),),
        resource_changes=(ResourceChange("MECHANIC", 24.0, 4),))
    store_scn = InMemorySnapshotStore()
    scn_out = app_main.run_pipeline(
        raw, "example_10", rc, validator=validator, store=store_scn,
        executor=app_main._make_executor(store_scn), scenario=scenario)

    assert scn_out.ok and scn_out.stage == "run"
    assert scn_out.result.status is RunResultStatus.COMPLETED
    scn_prov = scn_out.result.provenance
    # same baseline lineage, but the effective plan and stamped scenario delta both differ
    assert scn_prov.baseline_snapshot_hash == base_prov.baseline_snapshot_hash
    assert scn_prov.effective_plan_hash != base_prov.effective_plan_hash
    assert scn_prov.scenario_delta_hash is not None
    # the effective snapshot the run consumed carries the folded-in delta (P002 at 30h)
    import json as _json
    effective_payload = _json.loads(store_scn.get(scn_prov.effective_plan_hash))["payload"]
    p002 = next(t for t in effective_payload["tasks"] if t["task_id"] == "P002")
    assert p002["duration"] == 30.0
