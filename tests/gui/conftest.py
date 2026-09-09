"""conftest.py — PRISM GUI test island.

Two jobs:

1. The **"no silent gaps" gate** (CI ENFORCEMENT MACHINERY, block 3, from
   ``src/prismGui/dev_docs/prism-gui-contract-tests.py``). A test carrying none of
   the deferred markers (``phase2`` / ``adapter_integration`` / ``benchmark``) is a
   REQUIRED Phase-1 contract; a *plain skip* of such a test fails the run, so an
   uncategorized-and-unimplemented contract can never pass silently. A strict-xfail
   required contract is a sanctioned short-lived gap — reported, not failed.

2. Shared fixtures — sample-project paths (resolving to the canonical
   ``doc/demos/rcpsp/examples/`` the CPM suite already reads, NOT the byte-identical
   ``tests/CPMmodel/`` copies) and the in-memory domain/port objects the contract
   tests build on.

Mirrors ``tests/unit_tests/CPM/`` conventions; ``src/`` is on the path via
``pytest.ini`` so ``from prismGui... import ...`` and ``from CPM... import ...`` both
resolve. Nothing here imports Streamlit.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# =============================================================================
# 1. "No silent gaps" gate
# =============================================================================

_DEFERRED = {"phase2", "adapter_integration", "benchmark"}
_skipped_required: set[str] = set()   # real gaps -> fail the run
_xfailed_required: set[str] = set()   # sanctioned short-lived gaps -> report only


def _is_required(report) -> bool:
    # report.keywords carries applied marker names as keys.
    return _DEFERRED.isdisjoint(report.keywords)


def pytest_runtest_logreport(report):
    if not report.skipped or not _is_required(report):
        return
    # A strict xfail that starts passing already FAILS the run (pytest built-in);
    # one that stays xfailed is a sanctioned short-lived gap -> report, don't fail.
    if getattr(report, "wasxfail", None) is not None:
        _xfailed_required.add(report.nodeid)
    else:
        _skipped_required.add(report.nodeid)   # plain skip of a required contract


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if _xfailed_required:
        terminalreporter.write_sep(
            "!", f"{len(_xfailed_required)} required contract(s) xfailed — close these"
        )
        for nid in sorted(_xfailed_required):
            terminalreporter.write_line(f"  xfail (required): {nid}")
    if _skipped_required:
        terminalreporter.write_sep(
            "!", f"{len(_skipped_required)} REQUIRED Phase-1 contract(s) SKIPPED — silent gap"
        )
        for nid in sorted(_skipped_required):
            terminalreporter.write_line(f"  skipped (required): {nid}")


def pytest_sessionfinish(session, exitstatus):
    if _skipped_required:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED   # turn the silent gap red


# =============================================================================
# 2a. Sample-project paths
# =============================================================================
# tests/gui/conftest.py -> parents[2] is the repo root.

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = str(REPO_ROOT / "src" / "CPM" / "outage_schema.json")
EXAMPLES_DIR = REPO_ROOT / "doc" / "demos" / "rcpsp" / "examples"


@pytest.fixture(scope="session")
def schema_path() -> str:
    """Absolute path to the shipping outage schema (the real one the adapter uses)."""
    return SCHEMA_PATH


@pytest.fixture(scope="session")
def example_10_path() -> str:
    """Primary guided-load sample: 15 tasks, 2 resources -> real resource contention."""
    return str(EXAMPLES_DIR / "example_10.json")


@pytest.fixture(scope="session")
def test_case_1_path() -> str:
    """Secondary sample: 8 tasks, 1 resource; carries hold-point round-trip fields."""
    return str(EXAMPLES_DIR / "test_case_1.json")


# =============================================================================
# 2b. Domain / port fixtures  (added as the layers land, build-order bottom-up)
# =============================================================================
# Filled incrementally by the domain (Step 2), ports+infra (Step 3), and adapters
# (Steps 4-5). Kept in this single conftest so the contract tests stay declarative.
# Port-dependent fixtures (snapshot_store, executor, run_request, validator_adapter,
# baseline_invalid, ...) arrive with Steps 3-6, alongside the tests that need them.

import copy
from datetime import datetime, timezone

from prismGui.domain import serialization as ser
from prismGui.domain.disposition import ScheduleSummary, compute_disposition
from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity
from prismGui.domain.materialize import materialize
from prismGui.domain.results import (
    ActualResource,
    DiagnosticsDTO,
    Provenance,
    RunResult,
    RunResultStatus,
    ScheduleDTO,
    ScheduledActivityDTO,
    classify_float,
)
from prismGui.domain.run_config import ModeSelection, RunConfig, SGSVariant
from prismGui.domain.scenario import DurationOverride, Scenario
from prismGui.domain.versions import APP_VERSION, CANON_VERSION, SCHEMA_VERSION

# The thin-slice contract tests build on a tiny, schema-valid raw plan (2 tasks, one
# renewable pool) rather than example_10 — fast, and every field is under test control.


@pytest.fixture
def raw_plan() -> dict:
    """A minimal raw JSON tree matching the input schema (all five required root keys;
    an empty equipment/locations list is schema-valid). Deep-copied per test so
    mutating fixtures never leak across tests."""
    return {
        "outage": {
            "outage_id": "T",
            "start_date": "2025-01-01",
            "target_end_date": "2025-01-05",
            "working_hours_per_day": 24,
        },
        "tasks": [
            {"task_id": "A", "description": "task a", "duration": 4, "successors": ["B"],
             "location_id": None, "required_resources": [{"skill_type": "MECH", "crew_count": 1}],
             "required_equipment": [], "is_hold_point": False},
            {"task_id": "B", "description": "task b", "duration": 6, "successors": [],
             "location_id": None, "required_resources": [{"skill_type": "MECH", "crew_count": 2}],
             "required_equipment": [], "is_hold_point": False},
        ],
        "resources": [
            {"skill_type": "MECH", "availability_periods": [
                {"start_date": "2025-01-01T00:00:00", "end_date": "2025-01-05T00:00:00",
                 "available_count": 3, "reason": "base crew"}]},
        ],
        "equipment": [],
        "locations": [],
    }


@pytest.fixture
def raw_plan_with_extra_fields(raw_plan) -> dict:
    """A schema-valid plan carrying fields the thin typed view does not fully model
    (a task dose rate + WBS group). 'Unmodeled' here means schema-defined-but-not-typed:
    the root/task schema is additionalProperties:false, so truly arbitrary keys are not
    schema-valid, but plenty of real fields fall outside the thin PlanContent."""
    plan = copy.deepcopy(raw_plan)
    plan["tasks"][0]["dose_rate_mrem_per_hour"] = 12.5
    plan["tasks"][0]["wbs_group"] = "WBS-1"
    return plan


@pytest.fixture
def raw_plan_with_iso_dates(raw_plan) -> dict:
    """A raw JSON tree with ISO-8601 timestamps in availability periods (the minimal
    plan already carries them)."""
    return copy.deepcopy(raw_plan)


@pytest.fixture
def baseline(raw_plan):
    """A small valid committed ReferencePlan (2 tasks, one pool)."""
    return ser.build_reference_plan("baseline-1", raw_plan, schema_version=SCHEMA_VERSION)


@pytest.fixture
def scenario(baseline):
    """A valid Scenario bound to `baseline` — a duration override on task B."""
    return Scenario(
        scenario_id="scn-1",
        base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash,
        name="stretch B",
        duration_overrides=(DurationOverride(task_id="B", duration_hours=9.0),),
    )


@pytest.fixture
def scenario_with_stale_hash(baseline):
    """A Scenario whose base_plan_hash does not match `baseline`."""
    return Scenario(
        scenario_id="scn-stale",
        base_plan_id=baseline.plan_id,
        base_plan_hash="0" * 64,
        duration_overrides=(DurationOverride(task_id="A", duration_hours=5.0),),
    )


@pytest.fixture
def scenario_emergent_bad_ref(baseline):
    """A Scenario valid in isolation but whose emergent task references a resource that
    does not exist in the baseline (surfaces only when combined -> MATERIALIZE_CONFLICT)."""
    from prismGui.domain.plan import ResourceReq, Task
    emergent = Task(
        task_id="E1", duration=3.0, description="emergent",
        required_resources=(ResourceReq(skill_type="NONEXISTENT_SKILL", crew_count=1),),
    )
    return Scenario(
        scenario_id="scn-bad",
        base_plan_id=baseline.plan_id,
        base_plan_hash=baseline.plan_hash,
        emergent_tasks=(emergent,),
    )


@pytest.fixture
def run_config():
    """A default valid RunConfig (max_use_res_ranked, lf, seed 42)."""
    return RunConfig(run_config_id="rc-default", sgs=SGSVariant.MAX_USE_RES_RANKED,
                     priority_rule="lf", seed=42)


@pytest.fixture
def run_config_bad_mode():
    """A RunConfig selecting a mode for a task that is not present in the plan."""
    return RunConfig(run_config_id="rc-bad", mode_selections=(ModeSelection("GHOST", "crash"),))


@pytest.fixture
def effective_plan(baseline):
    """A materialized EffectivePlan from `baseline` + None scenario."""
    outcome = materialize(baseline, None)
    assert outcome.ok and outcome.effective_plan is not None
    return outcome.effective_plan


@pytest.fixture
def run_result(baseline, effective_plan, run_config):
    """A completed RunResult with populated provenance + schedule DTOs (built directly;
    no executor needed). Lineage hashes reference `baseline` so freshness fixtures line
    up with the current lineage by default."""
    from prismGui.domain.hashing import hash_run_config
    activities = (
        ScheduledActivityDTO(
            task_id="A", start_hour=0.0, end_hour=4.0, duration=4.0, delay_hours=0.0,
            on_constrained_chain=True, float_class=classify_float(0.0, True),
            description="task a", tf_actual_hours=0.0,
            actual_resources=(ActualResource(skill_type="MECH", crew_count=1),)),
        ScheduledActivityDTO(
            task_id="B", start_hour=4.0, end_hour=10.0, duration=6.0, delay_hours=0.0,
            on_constrained_chain=True, float_class=classify_float(0.0, True),
            description="task b", tf_actual_hours=0.0,
            actual_resources=(ActualResource(skill_type="MECH", crew_count=2),)),
    )
    schedule = ScheduleDTO(
        makespan_hours=10.0, cpm_lower_bound_hours=10.0, optimism_gap_hours=0.0,
        activities=activities, constrained_chain=("A", "B"), cpm_critical_path=("A", "B"))
    prov = Provenance(
        baseline_snapshot_hash=baseline.plan_hash,
        effective_plan_hash=effective_plan.effective_plan_hash,
        run_config_hash=hash_run_config(run_config),
        schema_version=SCHEMA_VERSION, canonicalization_version=CANON_VERSION,
        app_version=APP_VERSION, prism_version="test",
        run_id="run-1", timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        scenario_delta_hash=None)
    disposition = compute_disposition(
        ScheduleSummary(produced=True, n_unscheduled=0, audit_ran=True), ())
    return RunResult(
        run_id="run-1", status=RunResultStatus.COMPLETED, provenance=prov,
        issues=(), disposition=disposition, schedule=schedule,
        diagnostics=DiagnosticsDTO())


# =============================================================================
# 2c. Ports + in-memory infrastructure  (Step 3)
# =============================================================================
# The content-addressed store, a synchronous ExecutionPort test double (the FAKE the
# plan's group-G gate runs against — no PRISM), and hand-built RunRequests with their
# snapshots persisted. The real in-process PRISM executor arrives in Step 5 and must
# satisfy the SAME conformance suite via the `executor` fixture's parametrization.

from prismGui.domain.hashing import hash_run_config, run_config_snapshot
from prismGui.infrastructure.memory_snapshot_store import InMemorySnapshotStore
from prismGui.ports.execution import (
    ProvenanceInputs,
    RunRequest,
    RunStatus,
)
from prismGui.ports.snapshot_store import SnapshotNotFoundError


@pytest.fixture
def snapshot_store():
    """A fresh in-memory content-addressed SnapshotStorePort per test."""
    return InMemorySnapshotStore()


@pytest.fixture
def memory_repository():
    """A fresh in-memory RepositoryPort per test (entity save/load-by-id)."""
    from prismGui.infrastructure.memory_repository import InMemoryRepository
    return InMemoryRepository()


class FakeExecutor:
    """Synchronous in-process ExecutionPort test double (no PRISM).

    Completes inside ``submit()`` (the Phase-1 in-process contract): it resolves the
    effective-plan and run-config snapshots from the store, then either records a
    COMPLETED RunResult or — when a snapshot is missing — catches
    ``SnapshotNotFoundError`` and records a FAILED RunResult carrying a SNAPSHOT_MISSING
    issue (never a raw exception to the caller). It fabricates a trivial disposition and
    NO schedule: the port SEMANTICS, not the scheduling, are what group G verifies.

    Out-of-band ids raise ``KeyError`` per the port contract; ``cancel`` is a no-op on an
    already-terminal (immediate-complete) run, hence idempotent.
    """

    prism_version = "fake-0"

    def __init__(self, snapshot_store) -> None:
        self._store = snapshot_store
        self._status: dict[str, RunStatus] = {}
        self._results: dict[str, RunResult] = {}
        self._counter = 0

    def _provenance(self, run_id: str, pin: ProvenanceInputs) -> Provenance:
        return Provenance(
            baseline_snapshot_hash=pin.baseline_snapshot_hash,
            effective_plan_hash=pin.effective_plan_hash,
            run_config_hash=pin.run_config_hash,
            schema_version=pin.schema_version,
            canonicalization_version=pin.canonicalization_version,
            app_version=APP_VERSION,
            prism_version=self.prism_version,
            run_id=run_id,
            timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            scenario_delta_hash=pin.scenario_delta_hash,
        )

    def submit(self, request: RunRequest) -> str:
        self._counter += 1
        run_id = f"fake-run-{self._counter}"
        prov = self._provenance(run_id, request.provenance_inputs)
        try:
            self._store.get(request.effective_plan_hash)
            self._store.get(request.run_config_hash)
        except SnapshotNotFoundError as exc:
            issue = Issue(
                code=IssueCode.SNAPSHOT_MISSING, severity=Severity.ERROR,
                category=IssueCategory.PROVENANCE,
                message=f"snapshot not found: {exc.snapshot_hash}")
            self._results[run_id] = RunResult(
                run_id=run_id, status=RunResultStatus.FAILED, provenance=prov,
                issues=(issue,))
            self._status[run_id] = RunStatus.FAILED
            return run_id
        disposition = compute_disposition(
            ScheduleSummary(produced=True, n_unscheduled=0, audit_ran=True), ())
        self._results[run_id] = RunResult(
            run_id=run_id, status=RunResultStatus.COMPLETED, provenance=prov,
            issues=(), disposition=disposition)
        self._status[run_id] = RunStatus.COMPLETED
        return run_id

    def get_status(self, run_id: str) -> RunStatus:
        if run_id not in self._status:
            raise KeyError(run_id)
        return self._status[run_id]

    def get_result(self, run_id: str) -> RunResult:
        if run_id not in self._results:
            raise KeyError(run_id)
        return self._results[run_id]

    def cancel(self, run_id: str) -> None:
        if run_id not in self._status:
            raise KeyError(run_id)
        # Immediate-complete runs are already terminal; cancel is a defined no-op.


@pytest.fixture(params=[
    pytest.param("fake"),
    # The real in-process PRISM executor runs the SAME group-G suite, but only in the
    # adapter_integration job: the marker on this param propagates to every group-G test
    # when it is the active param, so `-m "not adapter_integration"` (the pure-contract
    # job) deselects it and never imports PRISM.
    pytest.param("in_process", marks=pytest.mark.adapter_integration),
])
def executor(request, snapshot_store):
    """ExecutionPort implementations, parametrized so the group-G conformance suite runs
    against each. Phase-1 pure-contract job: the FAKE only (no PRISM). The
    adapter_integration job adds the real in-process PRISM executor."""
    if request.param == "fake":
        return FakeExecutor(snapshot_store)
    if request.param == "in_process":
        from prismGui.infrastructure.prism_adapter import InProcessPrismExecutor
        return InProcessPrismExecutor(snapshot_store)
    raise ValueError(f"unknown executor param: {request.param}")


@pytest.fixture
def fake_executor(snapshot_store):
    """An UNparametrized FakeExecutor over `snapshot_store` — for pure-contract service
    tests that need a completed run without PRISM. (The parametrized `executor` fixture
    also offers the real in-process PRISM executor, which is deselected in the pure job;
    a service contract test wants just the fake, deterministically.)"""
    return FakeExecutor(snapshot_store)


@pytest.fixture
def run_request(baseline, effective_plan, run_config, snapshot_store):
    """A RunRequest whose referenced snapshots are all persisted in `snapshot_store`
    (baseline, effective plan, run config). Built by hand here; `prepare_run` produces
    the same shape in Step 6. The store keys equal the provenance hashes by construction
    (hash_bytes(raw_snapshot) == plan/effective hash; put(run_config_snapshot) ==
    hash_run_config)."""
    snapshot_store.put(baseline.raw_snapshot)
    effective_hash = snapshot_store.put(effective_plan.raw_snapshot)
    run_config_hash = snapshot_store.put(run_config_snapshot(run_config))
    provenance = ProvenanceInputs(
        baseline_snapshot_hash=baseline.plan_hash,
        effective_plan_hash=effective_hash,
        run_config_hash=run_config_hash,
        schema_version=SCHEMA_VERSION,
        canonicalization_version=CANON_VERSION,
        scenario_delta_hash=None,
    )
    return RunRequest(
        effective_plan_hash=effective_hash, run_config_hash=run_config_hash,
        provenance_inputs=provenance, request_id="req-1")


@pytest.fixture
def run_request_dangling_hash(run_config, snapshot_store):
    """A RunRequest whose effective-plan hash is ABSENT from the store (only the run
    config is persisted). Submitting it must yield a FAILED result with SNAPSHOT_MISSING,
    not a crash."""
    missing = "0" * 64
    run_config_hash = snapshot_store.put(run_config_snapshot(run_config))
    provenance = ProvenanceInputs(
        baseline_snapshot_hash=missing,
        effective_plan_hash=missing,
        run_config_hash=run_config_hash,
        schema_version=SCHEMA_VERSION,
        canonicalization_version=CANON_VERSION,
        scenario_delta_hash=None,
    )
    return RunRequest(
        effective_plan_hash=missing, run_config_hash=run_config_hash,
        provenance_inputs=provenance, request_id="req-missing")


# =============================================================================
# 2d. Validation adapter  (Step 4)
# =============================================================================
# The ValidationPort adapter wrapping src/CPM/validate_outage_data.py (pure — stdlib +
# jsonschema, no PRISM), plus small raw plans provoking exactly one defect each. Built
# with the REAL shipping schema (SCHEMA_PATH) so the module's undefined-DEFAULT_SCHEMA
# fallback is never reached.

from prismGui.infrastructure.validation_adapter import OutageValidatorAdapter


@pytest.fixture
def validator_adapter() -> OutageValidatorAdapter:
    """ValidationPort over the real outage schema, strict_resource_overlaps=False."""
    return OutageValidatorAdapter(SCHEMA_PATH)


@pytest.fixture
def validator_adapter_strict() -> OutageValidatorAdapter:
    """As `validator_adapter` but strict_resource_overlaps=True (resource availability
    overlaps become ERROR rather than WARNING)."""
    return OutageValidatorAdapter(SCHEMA_PATH, strict_resource_overlaps=True)


@pytest.fixture
def baseline_invalid(raw_plan) -> dict:
    """A raw plan that fails SCHEMA validation with a locatable field: task 'A' carries a
    string duration, so Draft7 reports a type error at /tasks/0/duration. Schema failure
    short-circuits referential checks, so this yields exactly one SCHEMA_TYPE_ERROR issue
    whose field_path points at the offending field."""
    plan = copy.deepcopy(raw_plan)
    plan["tasks"][0]["duration"] = "four"     # string where the schema requires a number
    return plan


@pytest.fixture
def raw_plan_overlapping_resource(raw_plan) -> dict:
    """A raw plan whose single resource pool has two OVERLAPPING availability periods.
    Resource overlaps honor strict_resource_overlaps: WARNING by default, ERROR under the
    strict adapter (equipment/location overlaps are always strict in the validator, so a
    resource overlap is the only case that demonstrates the flag). Otherwise clean —
    max available_count (3) still covers the peak demand (2), so no shortfall fires."""
    plan = copy.deepcopy(raw_plan)
    plan["resources"][0]["availability_periods"].append(
        {"start_date": "2025-01-03T00:00:00", "end_date": "2025-01-07T00:00:00",
         "available_count": 3, "reason": "overlaps the base period"})
    return plan
