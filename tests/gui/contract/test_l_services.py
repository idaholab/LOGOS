"""Contract group L — Application services (pure orchestration slice).

The application layer composes the thin-slice loop over the domain + ports. These tests
run in the PURE-contract job: they use the FakeExecutor and the (pure) ValidationPort
adapter, never PRISM. The full example_10 loop through the REAL executor is the
adapter_integration test ``tests/gui/integration/test_services_end_to_end.py``.

Covered here:
  * ``load_and_validate`` — a clean plan yields a ReferencePlan + no blocking issue;
    a schema-invalid plan blocks (no ReferencePlan, an ERROR issue surfaced).
  * ``prepare_run`` — persists every referenced snapshot and builds a submittable
    RunRequest on the happy path; a bad run-config blocks and persists NOTHING.
  * ``test_materialize_reuses_validation_port_on_effective_plan`` — the reuse group K
    defers here: prepare_run runs the SAME ValidationPort on the EFFECTIVE plan (with
    the scenario delta applied), not the untouched baseline.
  * ``run`` — submits to the ExecutionPort, collects the terminal result, persists it.
  * ``current_freshness`` — CURRENT / STALE / DIFFERENT_CONFIG against the live lineage.
  * ``InMemorySessionState`` — the accessor round-trips baseline / scenario / config /
    results / selection; "nothing selected" stays distinct from "the first result".
"""

from __future__ import annotations

import copy

from prismGui.application import services
from prismGui.application.services import InMemorySessionState
from prismGui.domain.hashing import hash_run_config
from prismGui.domain.issues import IssueCode, Severity
from prismGui.domain.materialize import materialize
from prismGui.domain.results import Freshness, RunResultStatus
from prismGui.domain.run_config import RunConfig
from prismGui.domain import serialization as ser
from prismGui.domain.versions import SCHEMA_VERSION


class _RecordingValidator:
    """A ValidationPort spy: records every plan dict it is asked to validate and returns
    no issues. Proves WHICH tree prepare_run hands the port, not what the port decides."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def validate_plan(self, plan_data):
        self.calls.append(plan_data)
        return ()


class TestLoadAndValidate:

    def test_clean_plan_loads_into_a_reference_plan(self, raw_plan, validator_adapter):
        """A schema-valid plan validates without an ERROR and yields a committed
        ReferencePlan whose hash matches its canonical snapshot."""
        outcome = services.load_and_validate("p1", raw_plan, validator_adapter)
        assert outcome.ok
        assert not any(i.severity is Severity.ERROR for i in outcome.issues)
        assert outcome.reference_plan is not None
        assert outcome.reference_plan.plan_id == "p1"

    def test_schema_invalid_plan_blocks_with_no_reference_plan(self, baseline_invalid,
                                                               validator_adapter):
        """A schema-invalid plan blocks the load: no ReferencePlan is built (the typed
        view is never assembled from a rejected tree) and an ERROR issue is surfaced."""
        outcome = services.load_and_validate("bad", baseline_invalid, validator_adapter)
        assert not outcome.ok
        assert outcome.reference_plan is None
        assert any(i.severity is Severity.ERROR for i in outcome.issues)


class TestPrepareRun:

    def test_prepare_persists_snapshots_and_builds_request(self, baseline, run_config,
                                                            snapshot_store, validator_adapter):
        """The happy path (scenario=None) persists baseline / effective / run-config
        snapshots and returns a submittable RunRequest whose hashes are the store keys."""
        outcome = services.prepare_run(baseline, None, run_config, snapshot_store,
                                       validator=validator_adapter)
        assert outcome.ok
        req = outcome.run_request
        assert req is not None

        # every referenced snapshot resolves in the store (the prepare_run invariant)
        assert snapshot_store.contains(req.effective_plan_hash)
        assert snapshot_store.contains(req.run_config_hash)
        assert snapshot_store.contains(req.provenance_inputs.baseline_snapshot_hash)

        # store keys equal the by-construction hashes
        assert req.provenance_inputs.baseline_snapshot_hash == baseline.plan_hash
        assert req.effective_plan_hash == outcome.effective_plan.effective_plan_hash
        assert req.run_config_hash == hash_run_config(run_config)
        assert req.provenance_inputs.scenario_delta_hash is None
        assert req.provenance_inputs.schema_version == SCHEMA_VERSION

    def test_bad_run_config_blocks_and_persists_nothing(self, baseline, run_config_bad_mode,
                                                        snapshot_store, validator_adapter):
        """A run-config referencing a task absent from the plan blocks: no RunRequest, an
        INVALID_MODE error, and NOTHING is written to the store (persistence happens only
        after the block check)."""
        effective_hash = materialize(baseline, None).effective_plan.effective_plan_hash
        outcome = services.prepare_run(baseline, None, run_config_bad_mode, snapshot_store,
                                       validator=validator_adapter)
        assert not outcome.ok
        assert outcome.run_request is None
        assert any(i.code is IssueCode.INVALID_MODE and i.severity is Severity.ERROR
                   for i in outcome.issues)
        assert not snapshot_store.contains(effective_hash)
        assert not snapshot_store.contains(hash_run_config(run_config_bad_mode))

    def test_materialize_reuses_validation_port_on_effective_plan(self, baseline, scenario,
                                                                  run_config, snapshot_store):
        """prepare_run runs the SAME ValidationPort on the EFFECTIVE plan — the tree with
        the scenario delta applied — not the untouched baseline. The spy sees exactly one
        call, carrying task B's overridden duration (9.0, up from the baseline's 6.0)."""
        spy = _RecordingValidator()
        outcome = services.prepare_run(baseline, scenario, run_config, snapshot_store,
                                       validator=spy)
        assert outcome.ok
        assert len(spy.calls) == 1                      # validated the effective plan once
        durations = {t["task_id"]: t["duration"] for t in spy.calls[0]["tasks"]}
        assert durations["B"] == 9.0                    # scenario stretched B 6h -> 9h
        assert outcome.run_request.provenance_inputs.scenario_delta_hash is not None


class TestRun:

    def test_run_submits_collects_and_persists(self, baseline, run_config, snapshot_store,
                                               validator_adapter, fake_executor, memory_repository):
        """run() submits the prepared request, collects the terminal RunResult, and (given a
        repository) persists it; the result's provenance echoes the request hashes."""
        prep = services.prepare_run(baseline, None, run_config, snapshot_store,
                                    validator=validator_adapter)
        result = services.run(prep.run_request, fake_executor, repository=memory_repository)
        assert result.status is RunResultStatus.COMPLETED
        assert result.provenance.effective_plan_hash == prep.run_request.effective_plan_hash
        assert result.provenance.run_config_hash == prep.run_request.run_config_hash
        assert memory_repository.load_run_result(result.run_id) is result


class TestFreshness:

    def _completed_result(self, baseline, run_config, snapshot_store, validator_adapter,
                          fake_executor):
        prep = services.prepare_run(baseline, None, run_config, snapshot_store,
                                    validator=validator_adapter)
        return services.run(prep.run_request, fake_executor)

    def test_current_when_lineage_matches(self, baseline, run_config, snapshot_store,
                                          validator_adapter, fake_executor):
        result = self._completed_result(baseline, run_config, snapshot_store,
                                        validator_adapter, fake_executor)
        assert services.current_freshness(result, baseline=baseline,
                                          run_config=run_config) is Freshness.CURRENT

    def test_stale_when_baseline_changed(self, baseline, raw_plan, run_config, snapshot_store,
                                         validator_adapter, fake_executor):
        result = self._completed_result(baseline, run_config, snapshot_store,
                                        validator_adapter, fake_executor)
        mutated = copy.deepcopy(raw_plan)
        mutated["outage"]["outage_id"] = "T-v2"          # different payload -> different hash
        other = ser.build_reference_plan("p2", mutated, schema_version=SCHEMA_VERSION)
        assert services.current_freshness(result, baseline=other) is Freshness.STALE

    def test_different_config_when_only_config_changed(self, baseline, run_config, snapshot_store,
                                                       validator_adapter, fake_executor):
        result = self._completed_result(baseline, run_config, snapshot_store,
                                        validator_adapter, fake_executor)
        other_config = RunConfig(run_config_id="rc-other", priority_rule="duration")
        assert services.current_freshness(result, baseline=baseline,
                                          run_config=other_config) is Freshness.DIFFERENT_CONFIG

    def test_freshness_detail_pairs_verdict_with_reasons(self, baseline, raw_plan, run_config,
                                                         snapshot_store, validator_adapter,
                                                         fake_executor):
        """``current_freshness_detail`` returns the (Freshness, reasons) pair the panel
        renders, consistent by construction with ``current_freshness``: CURRENT ⇒ no
        reasons; a baseline change ⇒ STALE + ``("baseline_changed",)``."""
        result = self._completed_result(baseline, run_config, snapshot_store,
                                        validator_adapter, fake_executor)

        # exact lineage + config -> CURRENT, empty reasons, verdict agrees with current_freshness
        freshness, reasons = services.current_freshness_detail(
            result, baseline=baseline, run_config=run_config)
        assert freshness is Freshness.CURRENT and reasons == ()
        assert freshness is services.current_freshness(
            result, baseline=baseline, run_config=run_config)

        # baseline edit -> STALE with the reason spelled out
        mutated = copy.deepcopy(raw_plan)
        mutated["outage"]["outage_id"] = "T-v2"
        other = ser.build_reference_plan("p2", mutated, schema_version=SCHEMA_VERSION)
        stale, stale_reasons = services.current_freshness_detail(result, baseline=other)
        assert stale is Freshness.STALE
        assert stale_reasons == ("baseline_changed",)


class TestSessionState:

    def test_accessors_round_trip(self, baseline, scenario, run_config, run_result):
        """The in-memory SessionState round-trips every entity, keeps results insertion-
        ordered, and defaults selection to None (distinct from 'the first result')."""
        s = InMemorySessionState()
        assert s.get_baseline() is None
        assert s.get_selected_result_id() is None

        s.set_baseline(baseline)
        s.set_scenario(scenario)
        s.set_run_config(run_config)
        assert s.get_baseline() is baseline
        assert s.get_scenario() is scenario
        assert s.get_run_config() is run_config

        s.add_run_result(run_result)
        assert s.list_run_results() == (run_result,)
        assert s.get_run_result(run_result.run_id) is run_result
        assert s.get_run_result("nope") is None

        s.set_selected_result_id(run_result.run_id)
        assert s.get_selected_result_id() == run_result.run_id
