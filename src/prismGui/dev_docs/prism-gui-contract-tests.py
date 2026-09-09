"""
PRISM GUI — Contract test SPECIFICATION (Phase 1–2)
===================================================

This is a contract-test *specification*, not yet executable tests: the functions
below name and document the invariants, but bodies are stubbed (marked skip/xfail).
Each becomes a real assertion as behavior is implemented. Accurate label matters —
do not mistake a green run of this file for verified behavior.

Test execution strategy (do NOT leave everything permanently skipped):
  * Implement Phase 1 contract tests with REAL assertions immediately.
  * Mark future-phase tests with @pytest.mark.phase2 (deselected by default CI).
  * Use @pytest.mark.xfail(strict=True) only for short-lived known gaps — a strict
    xfail that unexpectedly passes FAILS, forcing removal of the marker.
  * Default CI fails if a REQUIRED Phase 1 contract is skipped (no silent gaps).
    "Required" is the DEFAULT: a test is a required Phase-1 contract unless it is
    positively marked phase2 / adapter_integration / benchmark. Forgetting to
    categorize a new test therefore makes it required (fails loud), never silently
    ungated. The concrete, copy-paste-ready machinery (marker registration, the
    default CI selection, and the conftest.py gate that enforces this) is in the
    "CI ENFORCEMENT MACHINERY" block below — this is the Tier-3 item made real.
    NOTE: while the file is all stubs (every body skips), the gate is red by design
    — that is the honest "Phase 1 not implemented yet" signal. Wire this file into
    the *blocking* CI job as its contracts gain real assertions, not before.

Three separate test categories (keep in separate files/markers):
  1. Contract tests  — domain behavior & port semantics (this file). No PRISM/Streamlit.
  2. Adapter integration — real PRISM scheduling against small known input files.
  3. Benchmarks — measured runtime/scale with acceptance thresholds, e.g.
        - ~seconds for a single 500-task schedule (parallel SGS)
        - repeated priority-rule sweep latency
     (No thresholds are asserted yet; define them with the benchmark suite.)

Groups:
  A. Isolation & immutability      B. Editing contract
  C. Provenance & snapshots        D. Freshness / lineage
  E. Materialization & preparation F. Disposition policy
  G. Execution port conformance    H. Serialization round-trip
  I. Issue & classification        J. Input validation (structural)
  K. Validation port & adapter mapping (§8b: validate_outage_data -> Issues)

Fixtures at the bottom are the seams an implementer fills in (move to conftest.py).
Nothing here imports Streamlit or PRISM.
"""

from __future__ import annotations

import dataclasses
import pytest

# Import surface (from the skeleton module layout). Adjust paths at implementation.
# from domain.issues import Issue, Severity, IssueCategory, IssueCode, has_blocking
# from domain.plan import (
#     PlanContent, ReferencePlan, EffectivePlan, PlanDraft, Task, Dependency,
#     ResourcePool, ResourceType, ResourceAvailability,
#     PatchOp, PatchAction, open_draft, apply_patch, commit_draft, discard_draft,
# )
# from domain.scenario import Scenario, ResourceChange, DurationOverride
# from domain.run_config import RunConfig, SGSVariant, ModeSelection
# from domain.results import (
#     RunResult, RunResultStatus, ScheduleDTO, ScheduledActivityDTO, FloatClass,
#     Disposition, DispositionOverall, Tri, Provenance, Freshness, TF_ZERO_TOL,
#     classify_float,
# )
# from domain.materialize import materialize, validate_run_config, prepare_run
# from domain.disposition import compute_disposition, ScheduleSummary
# from domain.freshness import assess_freshness
# from domain import hashing
# from domain import serialization
# from ports.execution import ExecutionPort, RunRequest, RunStatus
# from ports.snapshot_store import SnapshotStorePort, SnapshotNotFoundError
# from ports.repository import RepositoryPort
# from ports.validation import ValidationPort
# The validator adapter is pure (stdlib + jsonschema; no PRISM, no Streamlit), so
# the mapping contract (group K) may import and drive it directly here:
# from infrastructure.validation_adapter import OutageValidatorAdapter

NOT_IMPL = "behavior not implemented yet"

# =============================================================================
# CI ENFORCEMENT MACHINERY  (Tier-3 deliverable — copy the three blocks below
# into the real test tree when Phase-1 coding starts)
# =============================================================================
#
# --- 1. Register the markers (pyproject.toml, or pytest.ini with [pytest]) ---
#
#   [tool.pytest.ini_options]
#   markers = [
#     "phase2: deferred to Phase 2; deselected from the default Phase-1 CI job",
#     "adapter_integration: needs a real PRISM runtime; runs in the integration job, not the pure-contract job",
#     "benchmark: measured runtime/scale; thresholds asserted in the benchmark job",
#   ]
#   # A test carrying NONE of these markers is a REQUIRED Phase-1 contract.
#   # 'Required' is the default so that an uncategorized new test fails loud.
#
# --- 2. The default Phase-1 CI job selects the pure, non-deferred contracts ---
#
#   pytest tests/gui/contract -m "not phase2 and not adapter_integration and not benchmark"
#
#   Adapter-integration and benchmark tests run in their own jobs (they need PRISM /
#   measure timing); phase2 tests run once Phase 2 opens. The gate (block 3) then
#   fails THIS job if any required contract in the selection was merely skipped.
#
# --- 3. conftest.py — the "no silent gaps" gate ------------------------------
#
#   import pytest
#
#   _DEFERRED = {"phase2", "adapter_integration", "benchmark"}
#   _skipped_required: set[str] = set()   # real gaps -> fail the run
#   _xfailed_required: set[str] = set()   # sanctioned short-lived gaps -> report only
#
#   def _is_required(report) -> bool:
#       # report.keywords carries applied marker names as keys.
#       return _DEFERRED.isdisjoint(report.keywords)
#
#   def pytest_runtest_logreport(report):
#       if not report.skipped or not _is_required(report):
#           return
#       # A strict xfail that starts passing already FAILS the run (pytest built-in);
#       # one that stays xfailed is a sanctioned short-lived gap -> report, don't fail.
#       if getattr(report, "wasxfail", None) is not None:
#           _xfailed_required.add(report.nodeid)
#       else:
#           _skipped_required.add(report.nodeid)   # plain skip of a required contract
#
#   def pytest_terminal_summary(terminalreporter, exitstatus, config):
#       if _xfailed_required:
#           terminalreporter.write_sep("!", f"{len(_xfailed_required)} required contract(s) xfailed — close these")
#           for nid in sorted(_xfailed_required):
#               terminalreporter.write_line(f"  xfail (required): {nid}")
#       if _skipped_required:
#           terminalreporter.write_sep("!", f"{len(_skipped_required)} REQUIRED Phase-1 contract(s) SKIPPED — silent gap")
#           for nid in sorted(_skipped_required):
#               terminalreporter.write_line(f"  skipped (required): {nid}")
#
#   def pytest_sessionfinish(session, exitstatus):
#       if _skipped_required:
#           session.exitstatus = pytest.ExitCode.TESTS_FAILED   # turn the silent gap into a red run
#
# Marker convention recap (see block 1 for the registration):
#   phase2              — deferred to Phase 2; deselected from the default Phase-1 job
#   adapter_integration — needs a real PRISM runtime; separate job (not pure-contract)
#   benchmark           — measured runtime/scale; separate job, own thresholds
#   (no marker)         — a REQUIRED Phase-1 contract: must carry a real assertion, never skip
# =============================================================================


# =============================================================================
# A. Isolation & immutability
# =============================================================================

class TestIsolationAndImmutability:

    def test_running_a_scenario_does_not_change_the_reference_plan(self, baseline, scenario, executor, snapshot_store):
        """Invariant: executing a (baseline, scenario) run leaves the committed
        ReferencePlan byte-identical (same plan_hash, same content, same raw_snapshot)."""
        pytest.skip(NOT_IMPL)

    def test_two_runs_do_not_influence_each_other(self, baseline, executor, snapshot_store):
        """Fresh-runtime invariant, A–B–A form: run config A, then a DIFFERENT config
        B, then A again — the two A results must be equivalent (same makespan, ordering,
        chain). Does NOT assert A and B are equal; only that B leaves no residue that
        changes A. This is the guard for priority-rule sweeps / scenario comparison."""
        pytest.skip(NOT_IMPL)

    def test_no_runtime_object_stored_on_domain_types(self, run_result):
        """No PRISM runtime / live object leaks into RunResult. All fields are
        neutral DTOs, recursively (dataclass fields resolve to DTOs/scalars/tuples)."""
        pytest.skip(NOT_IMPL)

    def test_reference_plan_is_deeply_immutable(self, baseline):
        """Committed ReferencePlan cannot be mutated: attribute set raises, and its
        collections are tuples (no append/setitem). Same for nested PlanContent."""
        pytest.skip(NOT_IMPL)

    def test_run_result_is_deeply_immutable(self, run_result):
        """RunResult and its DTOs are frozen and tuple-backed; no in-place edit of
        activities / issues / actual_resources."""
        pytest.skip(NOT_IMPL)

    def test_frozen_objects_have_no_mutable_dict_fields(self, scenario, run_config):
        """Regression guard: Scenario and RunConfig expose NO mutable dict/list
        fields (duration_overrides / hold_point_release_overrides / mode_selections
        are tuple-backed records). Attempting item assignment must fail."""
        pytest.skip(NOT_IMPL)

    def test_domain_imports_neither_streamlit_nor_prism(self):
        """Static invariant: importing any domain module pulls in neither streamlit
        nor the prism package. (Implement via import-graph inspection.)"""
        pytest.skip(NOT_IMPL)


# =============================================================================
# B. Editing contract (draft / commit / referential integrity / raw sync)
# =============================================================================
# Editing is Phase 2 — marked so default Phase 1 CI deselects it.

@pytest.mark.phase2
class TestEditingContract:

    def test_cancelled_edit_does_not_change_committed_baseline(self, baseline):
        """open_draft -> apply_patch(s) -> discard_draft: committed baseline unchanged
        (same object identity / same plan_hash)."""
        pytest.skip(NOT_IMPL)

    def test_invalid_edit_cannot_commit(self, baseline):
        """commit_draft on a draft whose patched raw tree fails schema or referential
        integrity returns ok=False with ERROR issues and no new plan."""
        pytest.skip(NOT_IMPL)

    def test_valid_edit_commits_to_new_immutable_plan_with_new_hash(self, baseline):
        """A valid commit yields a NEW ReferencePlan (distinct object) with a
        recomputed plan_hash; the prior baseline object is untouched."""
        pytest.skip(NOT_IMPL)

    def test_delete_referenced_resource_is_blocked(self, baseline):
        """A remove patch deleting a resource still referenced by a task's
        required_resources yields REF_MISSING and blocks commit (or requires cascade)."""
        pytest.skip(NOT_IMPL)

    def test_commit_goes_through_raw_then_rehydrate(self, baseline):
        """Sync invariant: commit applies patches to the raw tree and rehydrates the
        typed view from it; content is never an independently mutated typed object.
        Verified by round-tripping the committed plan through its raw snapshot."""
        pytest.skip(NOT_IMPL)

    def test_dependency_cycle_rejected_on_commit(self, baseline):
        """An add patch introducing a cycle yields DEP_CYCLE and blocks commit."""
        pytest.skip(NOT_IMPL)

    def test_apply_patch_is_lightweight_commit_is_full(self, baseline):
        """apply_patch does per-patch structural checks only; full schema +
        referential-integrity validation happens at commit_draft, not per patch."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# C. Provenance & snapshots
# =============================================================================

class TestProvenanceAndSnapshots:

    def test_stored_run_reproduces_from_snapshot_and_seed(self, baseline, run_config, executor, snapshot_store):
        """Reproducibility: re-running from a result's effective-plan snapshot + the
        same seed yields the same schedule (makespan, ordering, chain)."""
        pytest.skip(NOT_IMPL)

    def test_every_provenance_hash_resolves_in_snapshot_store(self, run_result, snapshot_store):
        """Invariant: baseline / effective_plan / run_config (and scenario if present)
        hashes in Provenance all resolve to bytes in the SnapshotStore."""
        pytest.skip(NOT_IMPL)

    def test_prepare_run_persists_snapshots_before_returning(self, baseline, run_config, snapshot_store):
        """prepare_run() stores all referenced snapshots; on success, snapshot_store
        .contains() is True for every hash in the produced RunRequest."""
        pytest.skip(NOT_IMPL)

    def test_baseline_hash_excludes_self_and_id(self, baseline):
        """hash_reference_plan is not self-referential: changing plan_id or the stored
        plan_hash does not change the recomputed content hash."""
        pytest.skip(NOT_IMPL)

    def test_run_config_hash_excludes_config_id(self, run_config):
        """Two RunConfigs differing only in run_config_id hash equal; differing in a
        solver-affecting field (sgs / priority_rule / seed / modes) hash different."""
        pytest.skip(NOT_IMPL)

    def test_scenario_hash_excludes_nonsemantic_fields(self, scenario):
        """Scenario hash ignores scenario_id and display name; depends on delta
        content and base_plan_hash."""
        pytest.skip(NOT_IMPL)

    def test_canonicalization_is_deterministic(self, baseline):
        """Canonical serialization is stable across key ordering / numeric formatting
        of equivalent inputs -> identical bytes -> identical hash."""
        pytest.skip(NOT_IMPL)

    def test_baseline_hash_covers_unmodeled_raw_fields(self, raw_plan_with_extra_fields):
        """Provenance-critical: changing ONLY an unmodeled/untyped raw field changes
        the baseline hash. (Guards that thin typing does not blind revision identity.)"""
        pytest.skip(NOT_IMPL)

    def test_stored_bytes_hash_to_the_put_key(self, snapshot_store):
        """SnapshotStore.put(x) returns h; the provenance hash computed for x equals h.
        Storage key and provenance hash cannot diverge."""
        pytest.skip(NOT_IMPL)

    def test_substituted_snapshot_bytes_are_detected(self, snapshot_store):
        """If stored bytes for a hash are corrupted/substituted, integrity re-check
        (recompute hash of get(h)) detects the mismatch."""
        pytest.skip(NOT_IMPL)

    def test_snapshot_store_is_content_addressed_and_idempotent(self, snapshot_store):
        """put(x) returns a hash; put(x) again returns the same hash and stores once
        (dedup). get(hash) returns the original bytes. get(missing) raises
        SnapshotNotFoundError."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# D. Freshness / lineage (derived, not stored)
# =============================================================================

class TestFreshness:

    def test_baseline_change_marks_displayed_result_stale_not_deleted(self, baseline, run_result):
        """After a baseline edit, assess_freshness(result, new_hash, ...) == STALE,
        and the result remains present/viewable (nothing deleted)."""
        pytest.skip(NOT_IMPL)

    def test_selecting_different_rule_is_not_stale(self, run_result):
        """Changing only the selected run config yields DIFFERENT_CONFIG, NOT STALE
        (a result is not invalidated by a config selection change)."""
        pytest.skip(NOT_IMPL)

    def test_unchanged_lineage_is_current(self, run_result):
        """assess_freshness against the exact hashes the result was produced from
        returns CURRENT."""
        pytest.skip(NOT_IMPL)

    def test_freshness_is_derived_not_stored(self, run_result):
        """RunResult carries no freshness/stale field; freshness is only ever a
        function of provenance vs. current lineage."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# E. Materialization & preparation
# =============================================================================

class TestMaterialization:

    def test_materialize_with_none_scenario_mirrors_baseline(self, baseline):
        """materialize(baseline, None) -> ok, effective_plan whose content mirrors the
        baseline; effective_plan_hash derived with no scenario component."""
        pytest.skip(NOT_IMPL)

    def test_base_hash_mismatch_is_flagged(self, baseline, scenario_with_stale_hash):
        """A scenario whose base_plan_hash != baseline.plan_hash yields
        PROV_HASH_MISMATCH."""
        pytest.skip(NOT_IMPL)

    def test_valid_alone_invalid_combined_is_caught(self, baseline, scenario_emergent_bad_ref):
        """Baseline valid; scenario valid alone; combined invalid (emergent task
        references a nonexistent resource) -> MATERIALIZE_CONFLICT, not ok."""
        pytest.skip(NOT_IMPL)

    def test_materialize_does_not_receive_run_config(self):
        """Signature contract: materialize takes only (reference_plan, scenario). Mode
        validity is NOT its job (that's validate_run_config)."""
        pytest.skip(NOT_IMPL)

    def test_validate_run_config_catches_mode_for_missing_task(self, effective_plan, run_config_bad_mode):
        """A mode_selection for a task absent from the effective plan (e.g. replaced by
        the scenario) -> INVALID_MODE."""
        pytest.skip(NOT_IMPL)

    def test_prepare_run_blocks_on_any_error(self, baseline, run_config_bad_mode, snapshot_store):
        """prepare_run returns ok=False and no run_request when materialization OR
        run-config validation produces an ERROR; issues are the union of both."""
        pytest.skip(NOT_IMPL)

    def test_prepare_run_builds_request_when_clean(self, baseline, run_config, snapshot_store):
        """No blocking issues -> ok=True and a RunRequest referencing stored snapshots."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# F. Disposition policy (pure, tri-state, precedence)
# =============================================================================

class TestDisposition:

    def test_compute_disposition_is_pure(self):
        """Same (summary, issues) -> same Disposition, no side effects, no I/O."""
        pytest.skip(NOT_IMPL)

    def test_blocked_when_any_error_issue(self):
        """Any ERROR-severity issue -> overall BLOCKED regardless of other indicators."""
        pytest.skip(NOT_IMPL)

    def test_ready_with_warnings_for_window_violation_only(self):
        """A window violation (WARNING, non-blocking) with a complete schedule ->
        READY_WITH_WARNINGS, not BLOCKED and not READY."""
        pytest.skip(NOT_IMPL)

    def test_ready_when_clean(self):
        """Complete schedule, no unscheduled tasks, no warnings, audit passed -> READY."""
        pytest.skip(NOT_IMPL)

    def test_audit_not_run_is_tri_not_evaluated(self):
        """When the audit did not run, audit_passed indicator is NOT_EVALUATED,
        distinct from FALSE."""
        pytest.skip(NOT_IMPL)

    def test_unscheduled_tasks_reflected_in_indicators(self):
        """n_unscheduled > 0 -> has_unscheduled_tasks == TRUE and disposition is not READY."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# G. Execution port conformance (both impls satisfy the same contract)
# =============================================================================

class TestExecutionPortConformance:
    """Run the SAME suite against the Phase-1 in-process executor and (later) a
    background executor via the `executor` fixture parametrization."""

    def test_submit_returns_run_id(self, executor, run_request):
        """submit(request) returns a RunId (non-empty, resolvable via get_status)."""
        pytest.skip(NOT_IMPL)

    def test_status_reaches_terminal_state(self, executor, run_request):
        """After submit, status eventually reaches a terminal state
        (completed/failed/cancelled). In-process impl may be terminal immediately."""
        pytest.skip(NOT_IMPL)

    def test_result_available_at_terminal_state(self, executor, run_request):
        """get_result is valid once status is terminal, and returns a RunResult whose
        provenance matches the submitted request."""
        pytest.skip(NOT_IMPL)

    def test_cancel_is_accepted(self, executor, run_request):
        """cancel(run_id) is accepted; final status is cancelled (or already-terminal
        handled per contract). In-process impl: immediate-complete cannot be cancelled."""
        pytest.skip(NOT_IMPL)

    def test_missing_snapshot_surfaces_execution_failure(self, executor, run_request_dangling_hash):
        """A request whose snapshot hash is absent from the store: the port raises
        SnapshotNotFoundError; the executor catches it and returns a FAILED RunResult
        carrying a SNAPSHOT_MISSING issue — not a crash, not a raw exception to the UI."""
        pytest.skip(NOT_IMPL)

    def test_get_result_before_terminal_has_defined_behavior(self, executor, run_request):
        """get_result before a terminal status has defined behavior (raises a typed
        error or returns a sentinel per contract) — never undefined/garbage."""
        pytest.skip(NOT_IMPL)

    def test_unknown_run_id_has_defined_behavior(self, executor):
        """get_status / get_result / cancel on an unknown run_id behave per contract
        (typed error), not an unhandled exception."""
        pytest.skip(NOT_IMPL)

    def test_repeated_cancel_is_idempotent(self, executor, run_request):
        """Calling cancel twice (or on an already-terminal run) is defined and safe."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# H. Serialization round-trip (lossless-semantic)
# =============================================================================

class TestSerializationRoundTrip:

    def test_unknown_fields_survive_edit_export_reload(self, raw_plan_with_extra_fields):
        """Load -> edit a typed field -> export -> reload: fields the GUI does not
        model are semantically preserved (present, equal values)."""
        pytest.skip(NOT_IMPL)

    def test_export_is_schema_valid(self, baseline):
        """Exported JSON validates against the input schema."""
        pytest.skip(NOT_IMPL)

    def test_lossless_is_semantic_not_bytewise(self, raw_plan_with_extra_fields):
        """Round-trip preserves values/structure of unknown fields but need NOT
        preserve whitespace or original key ordering."""
        pytest.skip(NOT_IMPL)

    def test_iso_to_hour_offset_boundary(self, raw_plan_with_iso_dates):
        """Load converts ISO timestamps to hour-offsets in the typed view; export
        converts back; the adapter never sees ISO. Half-open [start,end) preserved."""
        pytest.skip(NOT_IMPL)

    def test_dependencies_roundtrip_to_per_task_successors(self, raw_plan):
        """Normalized top-level dependencies serialize back to the schema's per-task
        successors representation and reload to the same edge set."""
        pytest.skip(NOT_IMPL)

    @pytest.mark.phase2
    def test_change_only_unknown_field_keeps_export_valid_and_restales_binding(self, baseline, scenario):
        """KEY provenance test: edit ONLY an unknown/untyped raw field ->
          (1) export remains schema-valid,
          (2) baseline hash changes,
          (3) a scenario previously bound to the old base_plan_hash now assesses STALE.
        Proves thin typing does not weaken provenance."""
        pytest.skip(NOT_IMPL)

    def test_equivalent_iso_offsets_canonicalize_consistently(self, raw_plan_with_iso_dates):
        """Two ISO timestamps denoting the same instant with different offsets
        canonicalize to the same hour-offset -> same bytes -> same hash."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# J. Input validation (structural) — schema, references, graph, numerics
# =============================================================================

class TestInputValidation:

    def test_duplicate_task_or_resource_ids_rejected(self, raw_plan):
        """Duplicate task_id or resource skill_type -> DUP_ID."""
        pytest.skip(NOT_IMPL)

    def test_dependency_missing_endpoint_rejected(self, raw_plan):
        """A dependency referencing a nonexistent predecessor/successor -> REF_MISSING."""
        pytest.skip(NOT_IMPL)

    def test_dependency_self_loop_rejected(self, raw_plan):
        """A dependency with predecessor_id == successor_id -> DEP_CYCLE (a
        degenerate 1-cycle; there is no separate DEP_SELF_LOOP code — §8b)."""
        pytest.skip(NOT_IMPL)

    def test_duplicate_dependency_handled(self, raw_plan):
        """Duplicate identical edges are de-duplicated or flagged per contract."""
        pytest.skip(NOT_IMPL)

    def test_dependency_cycle_detected(self, raw_plan):
        """A cycle in the dependency graph -> DEP_CYCLE."""
        pytest.skip(NOT_IMPL)

    def test_negative_duration_rejected(self, raw_plan):
        """Negative task duration -> SCHEMA_RANGE_ERROR."""
        pytest.skip(NOT_IMPL)

    def test_negative_capacity_rejected(self, raw_plan):
        """Negative crew count / equipment quantity / zone capacity -> SCHEMA_RANGE_ERROR."""
        pytest.skip(NOT_IMPL)

    def test_invalid_time_window_rejected(self, raw_plan):
        """A time window with w_min >= w_max -> SCHEMA_RANGE_ERROR / INVALID interval."""
        pytest.skip(NOT_IMPL)

    def test_availability_start_ge_end_rejected(self, raw_plan):
        """An availability period with start >= end -> INVALID_AVAILABILITY_INTERVAL."""
        pytest.skip(NOT_IMPL)

    def test_non_hold_point_task_carrying_hold_fields_rejected(self, raw_plan):
        """A task that is NOT a hold point but carries hold_point_type / blocks_tasks
        -> HOLD_POINT_MISUSE (error). One of the two codes the validation seam adds."""
        pytest.skip(NOT_IMPL)

    def test_resource_shortfall_warns_not_blocks(self, raw_plan):
        """A coarse resource-sufficiency shortfall (peak demand exceeds peak supply for
        a skill) -> INSUFFICIENT_RESOURCE at WARNING severity: it affects disposition
        but does NOT block (feasibility category, not referential integrity)."""
        pytest.skip(NOT_IMPL)

    def test_scenario_resource_changes_same_hour_rejected(self, baseline):
        """Two resource_changes at the same from_hour for the same skill ->
        INVALID_AVAILABILITY_INTERVAL (no implicit last-write-wins)."""
        pytest.skip(NOT_IMPL)

    def test_scenario_changes_applied_in_chronological_order(self, baseline):
        """resource/equipment changes are applied in ascending from_hour, deterministically."""
        pytest.skip(NOT_IMPL)

    @pytest.mark.phase2
    def test_emergent_task_id_collision_rejected(self, baseline):
        """An emergent task whose id collides with an existing baseline task id is
        rejected (or namespaced per contract) — not silently overwriting."""
        pytest.skip(NOT_IMPL)

    def test_timezone_naive_start_date_rejected(self, raw_plan):
        """A timezone-naive project start date -> SCHEMA_TYPE_ERROR (PlanMeta requires
        tz-aware)."""
        pytest.skip(NOT_IMPL)

    def test_canonical_numeric_edge_cases_defined(self):
        """Canonical numeric serialization defines behavior for NaN, +/-infinity, and
        negative zero (reject or normalize) — never nondeterministic bytes."""
        pytest.skip(NOT_IMPL)

    @pytest.mark.phase2
    def test_loading_new_baseline_resolves_incompatible_scenario_and_draft(self, baseline):
        """Loading a new baseline either clears or explicitly retains an incompatible
        scenario/draft per contract — never silently applies a mismatched delta."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# I. Issue & classification contract
# =============================================================================

class TestIssueContract:

    def test_validation_returns_structured_issues_not_strings(self, baseline_invalid):
        """Validation output is Issue objects with code/severity/category, never raw
        PRISM strings."""
        pytest.skip(NOT_IMPL)

    def test_field_path_points_to_offending_field(self, baseline_invalid):
        """Where applicable, an Issue carries a field_path locating the exact invalid
        field, not merely the entity id."""
        pytest.skip(NOT_IMPL)

    def test_has_blocking_true_iff_error_present(self):
        """has_blocking(issues) is True iff at least one Issue has severity ERROR."""
        pytest.skip(NOT_IMPL)

    def test_classify_float_rule(self):
        """classify_float is the single source of the rule:
          on_constrained_chain -> CRITICAL;
          else tf_actual <= TF_ZERO_TOL (incl. negative) -> ZERO_FLOAT;
          else -> POSITIVE_FLOAT.
        No adapter reproduces this independently."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# K. Validation port & adapter mapping (§8b)
# =============================================================================
# The ValidationPort seam wraps the existing pure src/CPM/validate_outage_data.py.
# These pin the PORT SEMANTICS (structured Issues; "no error == valid") and the
# ADAPTER's message->code mapping + isolation of the module's CLI-isms. The wrapped
# validator is pure (stdlib + jsonschema), so these run WITHOUT PRISM or Streamlit
# — they are contract tests, distinct from the real-PRISM adapter-integration suite.

class TestValidationPortMapping:

    def test_validate_plan_returns_issue_tuple_not_strings(self, validator_adapter, baseline_invalid):
        """Port semantics: validate_plan(plan_data) returns a tuple of Issue objects
        (code/severity/category), never the validator's raw (errors, warnings) strings."""
        pytest.skip(NOT_IMPL)

    def test_clean_plan_yields_no_error_severity_issues(self, validator_adapter, raw_plan):
        """'Valid' is expressed as 'no ERROR-severity Issue'; the validator's is_valid
        flag is not surfaced separately (redundant with has_blocking over the result)."""
        pytest.skip(NOT_IMPL)

    def test_message_to_code_mapping_matches_spec_table(self, validator_adapter):
        """THE mapping contract (§8b table). For each known validator output the adapter
        assigns the specified (code, severity, category):
            schema type / range error          -> SCHEMA_TYPE_ERROR / SCHEMA_RANGE_ERROR (error, schema)
            duplicate task/skill/eqp/loc id     -> DUP_ID (error, referential_integrity)
            dangling successor/loc/eqp/skill/blocked ref -> REF_MISSING (error, referential_integrity)
            self-referencing successor / hold-point self-block -> DEP_CYCLE (error, referential_integrity)
            circular dependency                 -> DEP_CYCLE (error, referential_integrity)
            bad / overlapping availability       -> INVALID_AVAILABILITY_INTERVAL (error/warning per strict)
            non-hold-point carries hold fields   -> HOLD_POINT_MISUSE (error, referential_integrity)
            coarse resource shortfall            -> INSUFFICIENT_RESOURCE (warning, feasibility)
        Drive with small plans that each provoke exactly one defect; assert the code."""
        pytest.skip(NOT_IMPL)

    def test_schema_error_carries_field_path(self, validator_adapter):
        """Schema-branch issues populate Issue.field_path from the validator's
        JSON-Pointer-like path, not merely the entity id."""
        pytest.skip(NOT_IMPL)

    def test_strict_resource_overlaps_flag_changes_severity(self, validator_adapter_strict, raw_plan_overlapping_equipment):
        """An equipment/location availability overlap is WARNING by default and ERROR
        under strict_resource_overlaps=True — the adapter forwards the flag and maps
        severity accordingly."""
        pytest.skip(NOT_IMPL)

    def test_adapter_never_exits_the_process(self, validator_adapter):
        """Embedding caveat 1: the adapter isolates the GUI from the module's sys.exit()
        paths (missing jsonschema / falsy schema_path). Under a simulated failure it
        raises a catchable error, never SystemExit — Streamlit must not be killed."""
        pytest.skip(NOT_IMPL)

    def test_adapter_always_passes_a_real_schema_path(self):
        """Embedding caveat 2: the not-found fallback in validate_outage_data references
        an undefined DEFAULT_SCHEMA (NameError). The adapter is constructed with the
        real src/CPM/outage_schema.json so that branch is never reached."""
        pytest.skip(NOT_IMPL)

    def test_materialize_reuses_validation_port_on_effective_plan(self, baseline, scenario, validator_adapter):
        """materialize() runs the SAME validator on the serialized effective plan (not a
        reimplemented checker), so a defect emerging only from combining baseline+scenario
        surfaces through these same codes — plus the domain-own MATERIALIZE_CONFLICT /
        PROV_HASH_MISMATCH the validator cannot know about."""
        pytest.skip(NOT_IMPL)


# =============================================================================
# Fixtures — seams the implementer fills in
# =============================================================================
# Kept minimal and declarative. Real fixtures build small in-memory objects from
# the skeleton types; none touch Streamlit or PRISM. A conftest.py will host these
# at implementation time; stubbed here so the file is self-describing.

@pytest.fixture
def snapshot_store():
    """An InMemorySnapshotStore (SnapshotStorePort)."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def baseline():
    """A small valid committed ReferencePlan (few tasks, one or two pools)."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def baseline_invalid():
    """A raw plan that fails schema / referential integrity, for issue tests."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def scenario():
    """A valid Scenario bound to `baseline` (e.g. a duration override)."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def scenario_with_stale_hash():
    """A Scenario whose base_plan_hash does not match `baseline`."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def scenario_emergent_bad_ref():
    """A Scenario whose emergent task references a nonexistent resource."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def run_config():
    """A default valid RunConfig (max_use_res_ranked, lf, seed 42)."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def run_config_bad_mode():
    """A RunConfig selecting a mode for a task not present / not multi-mode."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def effective_plan():
    """A materialized EffectivePlan from `baseline` + None scenario."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def run_request():
    """A RunRequest produced by prepare_run with snapshots stored."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def run_request_dangling_hash():
    """A RunRequest referencing a snapshot hash absent from the store."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def run_result():
    """A completed RunResult with populated provenance + schedule DTOs."""
    pytest.skip(NOT_IMPL)


@pytest.fixture(params=["in_process"])  # add "background" when it exists
def executor(request, snapshot_store):
    """ExecutionPort implementations, parametrized so the conformance suite runs
    against each. Phase 1: in-process only."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def raw_plan():
    """A minimal raw JSON tree matching the input schema."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def raw_plan_with_extra_fields():
    """A raw JSON tree containing fields the GUI does not model."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def raw_plan_with_iso_dates():
    """A raw JSON tree with ISO-8601 timestamps in availability periods."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def validator_adapter():
    """An OutageValidatorAdapter (ValidationPort) constructed with the real
    src/CPM/outage_schema.json, strict_resource_overlaps=False. Pure — no PRISM."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def validator_adapter_strict():
    """As validator_adapter but strict_resource_overlaps=True (overlaps -> ERROR)."""
    pytest.skip(NOT_IMPL)


@pytest.fixture
def raw_plan_overlapping_equipment():
    """A raw plan whose equipment/location availability periods overlap (WARNING by
    default, ERROR under strict_resource_overlaps)."""
    pytest.skip(NOT_IMPL)
