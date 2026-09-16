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
from dataclasses import replace

from prismGui.application import services
from prismGui.application.services import InMemorySessionState
from prismGui.domain.hashing import hash_run_config, hash_scenario
from prismGui.domain.issues import IssueCode, Severity
from prismGui.domain.materialize import materialize
from prismGui.domain.plan import open_draft
from prismGui.domain.results import Freshness, RunResultStatus
from prismGui.domain.run_config import RunConfig
from prismGui.domain.scenario import DurationOverride, ResourceChange, Scenario, TaskSuppression
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

    def test_resource_change_yields_a_different_effective_hash(self, baseline, run_config,
                                                               snapshot_store, validator_adapter):
        """A resource-availability what-if rewrites the baseline periods, so the effective
        plan (and its hash) differs from the plain-baseline effective plan — the delta is
        actually folded in, not silently dropped."""
        plain_hash = materialize(baseline, None).effective_plan.effective_plan_hash
        scenario = Scenario(
            scenario_id="scn-res", base_plan_id=baseline.plan_id,
            base_plan_hash=baseline.plan_hash,
            resource_changes=(ResourceChange("MECH", 48.0, 1),))
        outcome = services.prepare_run(baseline, scenario, run_config, snapshot_store,
                                       validator=validator_adapter)
        assert outcome.ok
        assert outcome.effective_plan.effective_plan_hash != plain_hash
        assert outcome.run_request.provenance_inputs.scenario_delta_hash is not None

    def test_over_cut_warns_but_does_not_block(self, baseline, run_config, snapshot_store,
                                               validator_adapter):
        """Cutting MECH to a single crew (below task B's demand of 2) is a coarse shortfall:
        the validator raises INSUFFICIENT_RESOURCE as a WARNING, which does NOT trip the
        ERROR-only prepare gate. prepare.ok stays True and the warning rides along."""
        scenario = Scenario(
            scenario_id="scn-cut", base_plan_id=baseline.plan_id,
            base_plan_hash=baseline.plan_hash,
            resource_changes=(ResourceChange("MECH", 0.0, 1),))   # whole pool -> 1
        outcome = services.prepare_run(baseline, scenario, run_config, snapshot_store,
                                       validator=validator_adapter)
        assert outcome.ok                                          # WARNING, not a block
        assert outcome.run_request is not None
        shortfalls = [i for i in outcome.issues if i.code is IssueCode.INSUFFICIENT_RESOURCE]
        assert shortfalls and all(i.severity is Severity.WARNING for i in shortfalls)

    def test_scenario_base_hash_mismatch_hard_blocks(self, baseline, scenario_with_stale_hash,
                                                     run_config, snapshot_store, validator_adapter):
        """A scenario built against a superseded revision (base_plan_hash != baseline) is a
        hard PROV_HASH_MISMATCH block at prepare_run — no RunRequest, nothing persisted."""
        outcome = services.prepare_run(baseline, scenario_with_stale_hash, run_config,
                                       snapshot_store, validator=validator_adapter)
        assert not outcome.ok
        assert outcome.run_request is None
        assert any(i.code is IssueCode.PROV_HASH_MISMATCH and i.severity is Severity.ERROR
                   for i in outcome.issues)
        assert not snapshot_store.contains(baseline.plan_hash)


class TestPrepareReplan:
    """The checkpoint-driven replan preparation (``prepare_replan``). Unlike ``prepare_run``
    the scenario deltas are NOT baked into the effective plan — the persisted effective plan
    is the BASELINE MIRROR (``materialize(plan, None)``), and the deltas ride the scenario
    snapshot for the adapter's ``replan()`` call. No PRISM here: these pin the prepared
    RunRequest's shape and the persistence/blocking contract."""

    def test_checkpoint_run_persists_mirror_and_carries_the_as_of_hour(
            self, baseline, scenario, run_config, snapshot_store, validator_adapter):
        """A checkpoint + duration-override scenario prepares ok; the persisted effective
        plan is the BASELINE MIRROR (not the materialized-overlay plan — the guard against
        double-applying the delta), the scenario rides as ``scenario_delta_hash``, and the
        as-of hour flows onto ``checkpoint_hour``. Every referenced snapshot resolves."""
        replan_scenario = replace(scenario, checkpoint_hour=8.0)
        outcome = services.prepare_replan(baseline, replan_scenario, run_config,
                                          snapshot_store, validator=validator_adapter)
        assert outcome.ok
        req = outcome.run_request
        assert req is not None

        # effective_plan_hash is the baseline mirror — NOT the materialized-overlay hash.
        mirror_hash = materialize(baseline, None).effective_plan.effective_plan_hash
        overlay_hash = materialize(baseline, replan_scenario).effective_plan.effective_plan_hash
        assert req.effective_plan_hash == mirror_hash
        assert req.effective_plan_hash != overlay_hash        # delta is NOT baked in

        pin = req.provenance_inputs
        assert pin.scenario_delta_hash == hash_scenario(replan_scenario)
        assert pin.checkpoint_hour == replan_scenario.checkpoint_hour == 8.0

        # every referenced snapshot resolves in the store (incl. the scenario delta)
        assert snapshot_store.contains(req.effective_plan_hash)
        assert snapshot_store.contains(req.run_config_hash)
        assert snapshot_store.contains(pin.baseline_snapshot_hash)
        assert snapshot_store.contains(pin.scenario_delta_hash)

    def test_unsupported_family_warns_but_does_not_block(
            self, baseline, run_config, snapshot_store, validator_adapter):
        """A checkpoint scenario ALSO carrying an unsupported family (a task suppression the
        engine's replan cannot honor) still prepares ok — warn-and-run — with a
        REPLAN_UNSUPPORTED WARNING riding the issues and NO ERROR blocking it."""
        replan_scenario = Scenario(
            scenario_id="scn-sup", base_plan_id=baseline.plan_id,
            base_plan_hash=baseline.plan_hash, checkpoint_hour=8.0,
            task_suppressions=(TaskSuppression(task_id="A"),))
        outcome = services.prepare_replan(baseline, replan_scenario, run_config,
                                          snapshot_store, validator=validator_adapter)
        assert outcome.ok
        assert outcome.run_request is not None
        warnings = [i for i in outcome.issues if i.code is IssueCode.REPLAN_UNSUPPORTED]
        assert warnings and all(i.severity is Severity.WARNING for i in warnings)
        assert not any(i.severity is Severity.ERROR for i in outcome.issues)

    def test_broken_supported_delta_blocks_and_persists_nothing(
            self, baseline, run_config, snapshot_store, validator_adapter):
        """A referentially-broken SUPPORTED delta (a duration override on a ghost task) trips
        the materialize referential-ERROR gate: no RunRequest, a MATERIALIZE_CONFLICT error,
        and NOTHING is written to the store (persistence happens only past the gate)."""
        replan_scenario = Scenario(
            scenario_id="scn-ghost", base_plan_id=baseline.plan_id,
            base_plan_hash=baseline.plan_hash, checkpoint_hour=8.0,
            duration_overrides=(DurationOverride(task_id="GHOST", duration_hours=5.0),))
        outcome = services.prepare_replan(baseline, replan_scenario, run_config,
                                          snapshot_store, validator=validator_adapter)
        assert not outcome.ok
        assert outcome.run_request is None
        assert any(i.code is IssueCode.MATERIALIZE_CONFLICT and i.severity is Severity.ERROR
                   for i in outcome.issues)
        mirror_hash = materialize(baseline, None).effective_plan.effective_plan_hash
        assert not snapshot_store.contains(mirror_hash)
        assert not snapshot_store.contains(hash_scenario(replan_scenario))
        assert not snapshot_store.contains(baseline.plan_hash)


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

        # draft accessors: starts empty, round-trips the set draft, clears back to None
        assert s.get_draft() is None
        draft = open_draft(baseline)
        s.set_draft(draft)
        assert s.get_draft() is draft
        s.clear_draft()
        assert s.get_draft() is None

        s.add_run_result(run_result)
        assert s.list_run_results() == (run_result,)
        assert s.get_run_result(run_result.run_id) is run_result
        assert s.get_run_result("nope") is None

        s.set_selected_result_id(run_result.run_id)
        assert s.get_selected_result_id() == run_result.run_id

    def test_scenario_collection_and_current_pointer(self, baseline):
        """The session holds a keyed scenario collection plus a current-schedule pointer
        (None == the baseline). ``get_scenario`` / ``set_scenario`` are shims over the pointer:
        set stores-and-selects, ``set_scenario(None)`` detaches to the baseline WITHOUT
        deleting the stored scenario, and a dangling pointer reads as None."""
        s = InMemorySessionState()
        assert s.list_scenarios() == ()
        assert s.get_current_scenario_id() is None
        assert s.get_scenario() is None                      # None pointer == baseline

        a = Scenario(scenario_id="scn-a", base_plan_id=baseline.plan_id,
                     base_plan_hash=baseline.plan_hash, name="A")
        b = Scenario(scenario_id="scn-b", base_plan_id=baseline.plan_id,
                     base_plan_hash=baseline.plan_hash, name="B")

        # add keeps insertion order and does NOT move the pointer
        s.add_scenario(a)
        s.add_scenario(b)
        assert s.list_scenarios() == (a, b)
        assert s.get_current_scenario_id() is None and s.get_scenario() is None

        # set_scenario is the shim: stores (add-or-update) AND selects
        s.set_scenario(a)
        assert s.get_current_scenario_id() == "scn-a" and s.get_scenario() is a

        # set_scenario(None) detaches to the baseline but keeps both stored scenarios
        s.set_scenario(None)
        assert s.get_current_scenario_id() is None and s.get_scenario() is None
        assert s.list_scenarios() == (a, b)

        # explicit pointer selection + add-or-update by id (same id replaces in place)
        s.set_current_scenario_id("scn-b")
        assert s.get_scenario() is b
        b2 = Scenario(scenario_id="scn-b", base_plan_id=baseline.plan_id,
                      base_plan_hash=baseline.plan_hash, name="B renamed")
        s.add_scenario(b2)
        assert s.list_scenarios() == (a, b2) and s.get_scenario() is b2   # id-keyed, order kept

        # remove is idempotent; a dangling pointer reads as None (not an error)
        s.remove_scenario("scn-b")
        assert s.list_scenarios() == (a,)
        assert s.get_current_scenario_id() == "scn-b" and s.get_scenario() is None
        s.remove_scenario("scn-b")                            # gone already — no raise
