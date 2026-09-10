"""application/services.py — use-case orchestration over the domain + ports.

The application layer is the ONLY place the pieces are composed into the thin-slice
loop **load → validate → prepare → run → view**. It depends on the domain (pure logic)
and the port Protocols (``ValidationPort``, ``SnapshotStorePort``, ``ExecutionPort``,
``RepositoryPort``) — never on a concrete adapter, on PRISM, or on Streamlit. The app
shell (``app/main.py``) wires concrete adapters into these functions and renders the
result; a headless caller (the Step-6 end-to-end test) wires the same functions to the
in-memory infra and the real executor.

Four responsibilities (plan Step 6):

  * ``load_and_validate`` — run the ValidationPort on a raw plan dict; build a
    ``ReferencePlan`` only when no ERROR-severity issue is present (the typed view is
    built from a structurally-valid tree, never from one the schema rejected).
  * ``prepare_run`` — ``materialize`` (baseline + scenario) → re-validate the EFFECTIVE
    plan through the SAME ValidationPort (model-spec: one rule set, no drift; this is the
    reuse group K defers here) → ``validate_run_config`` → persist every referenced
    snapshot into the SnapshotStore → assemble a ``RunRequest``. A run is prepared only
    when nothing blocks, so every provenance hash in the eventual ``RunResult`` resolves.
  * ``run`` — submit the request to the ExecutionPort and collect the terminal
    ``RunResult`` (Phase 1 executors complete synchronously inside ``submit``); optionally
    persist it via the RepositoryPort.
  * freshness + session state — ``current_freshness`` derives freshness of a shown result
    against the present lineage; ``SessionState`` / ``InMemorySessionState`` broker the
    baseline / scenario / run-config / results / selection the UI reads (the Streamlit-
    backed accessor in ``app/`` wraps this same Protocol so the UI never touches
    ``st.session_state`` keys directly).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from prismGui.domain import serialization as ser
from prismGui.domain.freshness import assess_freshness, explain_freshness
from prismGui.domain.hashing import hash_run_config, hash_scenario, run_config_snapshot, scenario_snapshot
from prismGui.domain.issues import Issue, Severity
from prismGui.domain.materialize import materialize, validate_run_config
from prismGui.domain.plan import CommitOutcome, EffectivePlan, PlanDraft, ReferencePlan
from prismGui.domain.results import Freshness, RunResult
from prismGui.domain.run_config import RunConfig
from prismGui.domain.scenario import Scenario
from prismGui.domain.versions import CANON_VERSION, SCHEMA_VERSION
from prismGui.ports.execution import ExecutionPort, ProvenanceInputs, RunRequest
from prismGui.ports.repository import RepositoryPort
from prismGui.ports.snapshot_store import SnapshotStorePort
from prismGui.ports.validation import ValidationPort

JSONTree = dict


def _has_error(issues) -> bool:
    """A run is blocked iff some issue is ERROR-severity (warnings never block)."""
    return any(i.severity is Severity.ERROR for i in issues)


# =============================================================================
# load + validate
# =============================================================================

@dataclass(frozen=True)
class LoadOutcome:
    """Result of loading a raw plan: the validation issues (errors and/or warnings),
    and the committed ``ReferencePlan`` when nothing blocked. ``ok`` is False iff an
    ERROR-severity issue is present, in which case ``reference_plan`` is None."""
    ok: bool
    issues: tuple[Issue, ...]
    reference_plan: Optional[ReferencePlan] = None


def load_and_validate(
    plan_id: str,
    raw_plan: JSONTree,
    validator: ValidationPort,
    *,
    schema_version: str = SCHEMA_VERSION,
) -> LoadOutcome:
    """Validate a raw schema-shaped plan dict and, if it carries no ERROR-severity issue,
    build the committed ``ReferencePlan`` (typed view + authoritative canonical snapshot).
    Warnings are returned alongside a valid plan — they inform the UI, they do not block."""
    issues = tuple(validator.validate_plan(raw_plan))
    if _has_error(issues):
        return LoadOutcome(ok=False, issues=issues, reference_plan=None)
    plan = ser.build_reference_plan(plan_id, raw_plan, schema_version=schema_version)
    return LoadOutcome(ok=True, issues=issues, reference_plan=plan)


# =============================================================================
# commit_draft — the FULL commit of the editing lifecycle (§2b)
# =============================================================================

def commit_draft(
    draft: PlanDraft,
    validator: ValidationPort,
    *,
    schema_version: str = SCHEMA_VERSION,
    new_plan_id: Optional[str] = None,
) -> CommitOutcome:
    """Commit a draft's patched raw working tree into a NEW immutable ``ReferencePlan``.

    This is the "full" half of the lightweight/full split: ``domain.apply_patch`` staged
    each edit with only a structural check, and this runs the complete schema + referential
    validation via the SAME ``ValidationPort`` the load/prepare paths use (the domain cannot
    import that port without a cycle, so commit is orchestrated here — the ``prepare_run``
    precedent). Only when nothing blocks does ``build_reference_plan`` rehydrate the typed
    view from the raw tree and mint a new plan with a recomputed ``plan_hash``; the typed view
    is never committed directly. ``plan_id`` defaults to the draft's base id — the same logical
    plan, a new revision whose identity is the fresh hash.

    Blocking (any ERROR issue) returns ``ok=False`` with the issues and ``plan=None``; the
    draft is left untouched so the caller can fix a patch and retry."""
    issues = tuple(validator.validate_plan(draft.raw_working_tree))
    if _has_error(issues):
        return CommitOutcome(ok=False, issues=issues, plan=None)
    plan = ser.build_reference_plan(
        new_plan_id or draft.base_plan_id,
        draft.raw_working_tree,
        schema_version=schema_version,
    )
    return CommitOutcome(ok=True, issues=issues, plan=plan)


# =============================================================================
# prepare_run
# =============================================================================

@dataclass(frozen=True)
class PrepareOutcome:
    """Result of preparing a run. ``ok`` is False iff materialization, effective-plan
    validation, or run-config validation produced an ERROR-severity issue. On success
    ``run_request`` is ready to submit and every snapshot it references is persisted;
    ``effective_plan`` is carried for the caller (display / freshness) either way it
    could be materialized."""
    ok: bool
    issues: tuple[Issue, ...]
    run_request: Optional[RunRequest] = None
    effective_plan: Optional[EffectivePlan] = None


def prepare_run(
    reference_plan: ReferencePlan,
    scenario: Optional[Scenario],
    run_config: RunConfig,
    snapshot_store: SnapshotStorePort,
    *,
    validator: ValidationPort,
) -> PrepareOutcome:
    """Orchestrate ``materialize`` + effective-plan re-validation + ``validate_run_config``,
    then persist every referenced snapshot and assemble a ``RunRequest``.

    The effective plan is re-validated through the SAME ``ValidationPort`` the load used
    (on the authoritative effective payload, not the lossy typed view) so the schema /
    referential rules are never re-implemented in the domain — a scenario that introduces
    an invalid combination is caught here, before a run is prepared.

    Snapshots are persisted only when nothing blocks, guaranteeing the invariant that
    every provenance hash in the resulting ``RunResult`` resolves to bytes in the store
    (``store.put(x)`` returns ``hash_bytes(x)``, so each store key equals its provenance
    hash by construction)."""
    outcome = materialize(reference_plan, scenario)
    if not outcome.ok or outcome.effective_plan is None:
        return PrepareOutcome(ok=False, issues=outcome.issues, run_request=None,
                              effective_plan=outcome.effective_plan)
    effective = outcome.effective_plan

    issues: list[Issue] = list(outcome.issues)
    # Re-validate the EFFECTIVE plan's authoritative payload (the full schema-shaped tree,
    # incl. unmodeled fields) through the same port — this is the reuse group K defers here.
    effective_payload = json.loads(effective.raw_snapshot)["payload"]
    issues.extend(validator.validate_plan(effective_payload))
    issues.extend(validate_run_config(effective, run_config))

    if _has_error(issues):
        return PrepareOutcome(ok=False, issues=tuple(issues), run_request=None,
                              effective_plan=effective)

    # Persist all referenced snapshots. put() is idempotent and returns the content hash,
    # which equals the provenance hash for the same bytes.
    baseline_hash = snapshot_store.put(reference_plan.raw_snapshot)
    effective_hash = snapshot_store.put(effective.raw_snapshot)
    run_config_hash = snapshot_store.put(run_config_snapshot(run_config))
    scenario_hash = None
    if scenario is not None:
        scenario_hash = snapshot_store.put(scenario_snapshot(scenario))

    schema_version = json.loads(effective.raw_snapshot)["schema_version"]
    provenance = ProvenanceInputs(
        baseline_snapshot_hash=baseline_hash,
        effective_plan_hash=effective_hash,
        run_config_hash=run_config_hash,
        schema_version=schema_version,
        canonicalization_version=CANON_VERSION,
        scenario_delta_hash=scenario_hash,
    )
    request = RunRequest(
        effective_plan_hash=effective_hash,
        run_config_hash=run_config_hash,
        provenance_inputs=provenance,
        request_id=run_config.run_config_id,
    )
    return PrepareOutcome(ok=True, issues=tuple(issues), run_request=request,
                          effective_plan=effective)


# =============================================================================
# run
# =============================================================================

def run(
    run_request: RunRequest,
    executor: ExecutionPort,
    *,
    repository: Optional[RepositoryPort] = None,
) -> RunResult:
    """Submit the request and collect the terminal ``RunResult``. Phase-1 executors reach
    a terminal state inside ``submit`` (synchronous in-process), so the result is available
    immediately; the job-shaped port is unchanged, so a future async executor satisfies the
    same call. A missing snapshot never raises here — the executor returns a FAILED result
    bearing a ``SNAPSHOT_MISSING`` issue. Persists the result if a repository is given."""
    run_id = executor.submit(run_request)
    result = executor.get_result(run_id)
    if repository is not None:
        repository.save_run_result(result)
    return result


# =============================================================================
# freshness
# =============================================================================

def current_freshness(
    result: RunResult,
    *,
    baseline: ReferencePlan,
    scenario: Optional[Scenario] = None,
    run_config: Optional[RunConfig] = None,
) -> Freshness:
    """Derive a shown result's freshness against the CURRENT lineage objects (a convenience
    over ``assess_freshness``, which takes bare hashes): STALE if the baseline or scenario
    changed since the run, DIFFERENT_CONFIG if only the selected run config differs, else
    CURRENT. Passing ``run_config=None`` means "config not being compared"."""
    return assess_freshness(
        result,
        current_plan_hash=baseline.plan_hash,
        current_scenario_hash=None if scenario is None else hash_scenario(scenario),
        current_run_config_hash=None if run_config is None else hash_run_config(run_config),
    )


def current_freshness_detail(
    result: RunResult,
    *,
    baseline: ReferencePlan,
    scenario: Optional[Scenario] = None,
    run_config: Optional[RunConfig] = None,
) -> tuple[Freshness, tuple[str, ...]]:
    """``current_freshness`` paired with the ordered reason codes behind it (see
    ``explain_freshness``) — the (freshness, why) pair the provenance/freshness panel
    renders. Consistent by construction: the two derive from the SAME lineage hashes, so
    the reasons are empty iff the freshness is CURRENT."""
    current_scenario_hash = None if scenario is None else hash_scenario(scenario)
    current_run_config_hash = None if run_config is None else hash_run_config(run_config)
    freshness = assess_freshness(
        result,
        current_plan_hash=baseline.plan_hash,
        current_scenario_hash=current_scenario_hash,
        current_run_config_hash=current_run_config_hash,
    )
    reasons = explain_freshness(
        result,
        current_plan_hash=baseline.plan_hash,
        current_scenario_hash=current_scenario_hash,
        current_run_config_hash=current_run_config_hash,
    )
    return freshness, reasons


# =============================================================================
# session state
# =============================================================================

@runtime_checkable
class SessionState(Protocol):
    """The session-state broker the UI reads through — the ONLY seam that knows how the
    baseline / scenario / run-config / results / selection / draft are stored. The Streamlit
    shell implements this over ``st.session_state``; ``InMemorySessionState`` implements it
    over plain dicts for headless use and tests."""

    def get_baseline(self) -> Optional[ReferencePlan]: ...
    def set_baseline(self, plan: ReferencePlan) -> None: ...

    def get_draft(self) -> Optional[PlanDraft]: ...
    def set_draft(self, draft: PlanDraft) -> None: ...
    def clear_draft(self) -> None: ...

    def get_scenario(self) -> Optional[Scenario]: ...
    def set_scenario(self, scenario: Optional[Scenario]) -> None: ...

    def get_run_config(self) -> Optional[RunConfig]: ...
    def set_run_config(self, run_config: Optional[RunConfig]) -> None: ...

    def list_run_results(self) -> tuple[RunResult, ...]: ...
    def add_run_result(self, result: RunResult) -> None: ...
    def get_run_result(self, run_id: str) -> Optional[RunResult]: ...

    def get_selected_result_id(self) -> Optional[str]: ...
    def set_selected_result_id(self, run_id: Optional[str]) -> None: ...


class InMemorySessionState:
    """Pure in-memory ``SessionState`` — the reference implementation and the test double.
    Results are kept insertion-ordered (``dict`` order); selection is an explicit id, never
    inferred, so "nothing selected" (None) stays distinct from "the first result"."""

    def __init__(self) -> None:
        self._baseline: Optional[ReferencePlan] = None
        self._draft: Optional[PlanDraft] = None
        self._scenario: Optional[Scenario] = None
        self._run_config: Optional[RunConfig] = None
        self._results: dict[str, RunResult] = {}
        self._selected_id: Optional[str] = None

    def get_baseline(self) -> Optional[ReferencePlan]:
        return self._baseline

    def set_baseline(self, plan: ReferencePlan) -> None:
        self._baseline = plan

    def get_draft(self) -> Optional[PlanDraft]:
        return self._draft

    def set_draft(self, draft: PlanDraft) -> None:
        self._draft = draft

    def clear_draft(self) -> None:
        self._draft = None

    def get_scenario(self) -> Optional[Scenario]:
        return self._scenario

    def set_scenario(self, scenario: Optional[Scenario]) -> None:
        self._scenario = scenario

    def get_run_config(self) -> Optional[RunConfig]:
        return self._run_config

    def set_run_config(self, run_config: Optional[RunConfig]) -> None:
        self._run_config = run_config

    def list_run_results(self) -> tuple[RunResult, ...]:
        return tuple(self._results.values())

    def add_run_result(self, result: RunResult) -> None:
        self._results[result.run_id] = result

    def get_run_result(self, run_id: str) -> Optional[RunResult]:
        return self._results.get(run_id)

    def get_selected_result_id(self) -> Optional[str]:
        return self._selected_id

    def set_selected_result_id(self, run_id: Optional[str]) -> None:
        self._selected_id = run_id


# =============================================================================
# session lifecycle — resolving a scenario / draft against a newly-loaded baseline
# =============================================================================

@dataclass(frozen=True)
class BaselineResolution:
    """What survived a baseline switch: whether the prior scenario / draft were kept
    (still bound to the new revision) or cleared (bound to a different one)."""
    scenario_kept: bool
    draft_kept: bool


def resolve_for_new_baseline(
    session: SessionState, new_baseline: ReferencePlan
) -> BaselineResolution:
    """Point the session at ``new_baseline`` and resolve a now-incompatible scenario / draft.

    A scenario or draft is bound (by ``base_plan_hash``) to the revision it was built against.
    When the baseline changes, anything bound to a DIFFERENT revision is cleared — a delta or
    an edit built against another revision is never silently carried onto the new baseline
    (that would apply a mismatched change). Anything already bound to the new revision is kept.
    Returns which of the two survived. This is the pure core of the app shell's source-key
    guard, so the "loading a new baseline resolves an incompatible scenario/draft" contract is
    testable without Streamlit."""
    session.set_baseline(new_baseline)

    scenario = session.get_scenario()
    scenario_kept = scenario is not None and scenario.base_plan_hash == new_baseline.plan_hash
    if scenario is not None and not scenario_kept:
        session.set_scenario(None)

    draft = session.get_draft()
    draft_kept = draft is not None and draft.base_plan_hash == new_baseline.plan_hash
    if draft is not None and not draft_kept:
        session.clear_draft()

    return BaselineResolution(scenario_kept=scenario_kept, draft_kept=draft_kept)
