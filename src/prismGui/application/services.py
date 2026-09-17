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
from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity
from prismGui.domain.materialize import materialize, validate_run_config
from prismGui.domain.plan import CommitOutcome, EffectivePlan, PlanDraft, ReferencePlan
from prismGui.domain.plan_of_record import PlanOfRecord
from prismGui.domain.replan import replan_preflight
from prismGui.domain.results import Freshness, ReplanStep, RunResult
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
# prepare_replan (Phase 5 CORE)
# =============================================================================

def _chain_error(message: str) -> Issue:
    """A blocking rolling-plan-of-record chain-ordering ERROR (category EXECUTION)."""
    return Issue(
        code=IssueCode.REPLAN_CHAIN_ORDER,
        severity=Severity.ERROR,
        category=IssueCategory.EXECUTION,
        message=message,
    )


def _validate_replan_chain(
    scenario: Scenario, plan_of_record: Optional[PlanOfRecord]
) -> list[Issue]:
    """ERROR issues if the rolling plan-of-record chain is invalid, else empty.

    A chain is well-formed only when every adopted step is checkpoint-bearing, the adopted
    as-of hours run forward (non-decreasing), and this replan's as-of hour is at or after the
    last adopted step's. The chain replays as ``initial → replan(step₁) → … → replan(stepₙ) →
    replan(candidate)`` on one Pert, and each ``replan(T)`` freezes the previous step's
    rescheduled prefix before T — so a backwards T would try to reschedule an already-frozen
    region and is meaningless. Empty chain (no PoR) → no constraint, returns []."""
    if plan_of_record is None or plan_of_record.is_empty():
        return []
    issues: list[Issue] = []
    prev: Optional[float] = None
    for step in plan_of_record.steps:
        t = step.checkpoint_hour
        if t is None:
            issues.append(_chain_error(
                f"Adopted plan-of-record step (run {step.adopted_run_id}) has no as-of hour; "
                f"a rolling chain step must be checkpoint-bearing."))
            continue
        if prev is not None and t < prev:
            issues.append(_chain_error(
                f"Plan-of-record chain runs backwards: a step at h={t:g} precedes the prior "
                f"step at h={prev:g}. A rolling chain can only move forward in time."))
        prev = t
    cand = scenario.checkpoint_hour
    if cand is None:
        issues.append(_chain_error(
            "A replan chained onto a plan of record must have an as-of hour."))
    elif prev is not None and cand < prev:
        issues.append(_chain_error(
            f"This replan's as-of hour (h={cand:g}) is before the last adopted plan-of-record "
            f"step (h={prev:g}); a chained replan must reschedule at or after the adopted hour."))
    return issues


def prepare_replan(
    reference_plan: ReferencePlan,
    scenario: Scenario,
    run_config: RunConfig,
    snapshot_store: SnapshotStorePort,
    *,
    validator: ValidationPort,
    plan_of_record: Optional[PlanOfRecord] = None,
) -> PrepareOutcome:
    """Sibling to ``prepare_run`` for the checkpoint-driven replan path (``pert.replan()``).

    A replan runs the initial Pert on the **baseline mirror** (``materialize(plan, None)``)
    to satisfy the engine precondition, then passes the scenario's supported deltas to
    ``replan()`` as ARGUMENTS — it does NOT materialize them into the effective plan. This
    is deliberate: ``replan()``'s ``duration_overrides`` mutate *remaining* time as of T,
    whereas ``materialize`` rewrites the *pre-run* plan, so materializing AND passing the
    deltas would double-apply them. So the persisted ``effective_plan_hash`` is the
    baseline-mirror hash (the services test pins this), and the deltas ride the persisted
    ``scenario_snapshot`` which the adapter resolves + projects via ``build_replan_inputs``.

    Blocking is narrow: ``replan_preflight`` contributes WARNING-only issues (families /
    edges the replan drops) that NEVER block; ``materialize(plan, scenario)`` runs solely
    as a referential-ERROR gate (a ghost-task duration override, an emergent id collision,
    a ghost skill on an emergent task → ``MATERIALIZE_CONFLICT`` etc. block the replan), and
    its materialized effective plan is then DISCARDED. The mirror + run-config are validated
    through the same port as any run. Known minor limitation (acceptable for CORE): because
    materialize validates every family, a referentially-broken *unsupported* delta would
    also block even though the replan ignores it.

    Rolling plan-of-record (Phase 5): when ``plan_of_record`` carries adopted steps, this
    replan is scheduled ON TOP of that chain. The chain's ordering is gated first (an
    ERROR blocks — see ``_validate_replan_chain``); on success each adopted step's scenario
    snapshot is persisted and referenced (in T-order) from ``ProvenanceInputs.prior_steps``,
    so the adapter can replay ``initial → replan(step₁) → … → replan(candidate)`` on one Pert.
    An empty / absent chain leaves ``prior_steps`` empty → a plain hub-and-spoke replan,
    byte-identical to before."""
    issues: list[Issue] = list(replan_preflight(scenario, reference_plan))

    # Rolling plan-of-record chain-ordering gate (ERROR blocks; nothing persisted).
    chain_issues = _validate_replan_chain(scenario, plan_of_record)
    if _has_error(chain_issues):
        return PrepareOutcome(ok=False, issues=tuple(issues) + tuple(chain_issues),
                              run_request=None, effective_plan=None)

    # Referential-ERROR gate ONLY: materialize the overlay to catch ghost/collision errors,
    # then discard its effective plan (its baked-in deltas are wrong for a replan).
    gate = materialize(reference_plan, scenario)
    if _has_error(gate.issues):
        return PrepareOutcome(ok=False, issues=tuple(issues) + tuple(gate.issues),
                              run_request=None, effective_plan=gate.effective_plan)

    # The baseline mirror is the plan the adapter's INITIAL calculateScheduleWithResources
    # runs (to satisfy replan()'s "must have scheduled once" precondition).
    mirror_outcome = materialize(reference_plan, None)
    if not mirror_outcome.ok or mirror_outcome.effective_plan is None:
        return PrepareOutcome(ok=False, issues=tuple(issues) + tuple(mirror_outcome.issues),
                              run_request=None, effective_plan=mirror_outcome.effective_plan)
    effective = mirror_outcome.effective_plan

    effective_payload = json.loads(effective.raw_snapshot)["payload"]
    issues.extend(validator.validate_plan(effective_payload))
    issues.extend(validate_run_config(effective, run_config))
    if _has_error(issues):
        return PrepareOutcome(ok=False, issues=tuple(issues), run_request=None,
                              effective_plan=effective)

    # Persist baseline / mirror / run_config / scenario snapshots (the scenario snapshot IS
    # the canonical payload the adapter resolves + projects onto replan()'s kwargs).
    baseline_hash = snapshot_store.put(reference_plan.raw_snapshot)
    effective_hash = snapshot_store.put(effective.raw_snapshot)
    run_config_hash = snapshot_store.put(run_config_snapshot(run_config))
    scenario_hash = snapshot_store.put(scenario_snapshot(scenario))

    # Rolling plan-of-record: persist each adopted step's scenario snapshot (its bytes are what
    # the adapter resolves + projects onto replan()'s kwargs) and reference them in T-order.
    # Empty chain → prior_steps stays (). Steps are checkpoint-bearing (chain gate passed).
    prior_steps: tuple[ReplanStep, ...] = ()
    if plan_of_record is not None and not plan_of_record.is_empty():
        prior_steps = tuple(
            ReplanStep(
                scenario_delta_hash=snapshot_store.put(scenario_snapshot(step.scenario)),
                checkpoint_hour=float(step.checkpoint_hour),
            )
            for step in plan_of_record.steps
        )

    schema_version = json.loads(effective.raw_snapshot)["schema_version"]
    provenance = ProvenanceInputs(
        baseline_snapshot_hash=baseline_hash,
        effective_plan_hash=effective_hash,
        run_config_hash=run_config_hash,
        schema_version=schema_version,
        canonicalization_version=CANON_VERSION,
        scenario_delta_hash=scenario_hash,
        checkpoint_hour=scenario.checkpoint_hour,   # non-None → adapter takes the replan branch
        prior_steps=prior_steps,                    # T-ordered chain to replay before candidate
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

    # Scenarios: the session holds a keyed collection plus a "current schedule" pointer
    # (None == the baseline itself). get_scenario / set_scenario are convenience shims over
    # that pointer, kept so render-layer call sites and older tests read unchanged.
    def get_scenario(self) -> Optional[Scenario]: ...
    def set_scenario(self, scenario: Optional[Scenario]) -> None: ...

    def list_scenarios(self) -> tuple[Scenario, ...]: ...
    def add_scenario(self, scenario: Scenario) -> None: ...
    def remove_scenario(self, scenario_id: str) -> None: ...
    def get_current_scenario_id(self) -> Optional[str]: ...
    def set_current_scenario_id(self, scenario_id: Optional[str]) -> None: ...

    def get_run_config(self) -> Optional[RunConfig]: ...
    def set_run_config(self, run_config: Optional[RunConfig]) -> None: ...

    def list_run_results(self) -> tuple[RunResult, ...]: ...
    def add_run_result(self, result: RunResult) -> None: ...
    def get_run_result(self, run_id: str) -> Optional[RunResult]: ...

    def get_selected_result_id(self) -> Optional[str]: ...
    def set_selected_result_id(self, run_id: Optional[str]) -> None: ...

    # Rolling plan-of-record: the ordered chain of adopted replans the NEXT replan chains
    # from (None == no PoR; a plain hub-and-spoke replan off the baseline). Reset when the
    # baseline revision changes (resolve_for_new_baseline).
    def get_plan_of_record(self) -> Optional[PlanOfRecord]: ...
    def set_plan_of_record(self, plan_of_record: Optional[PlanOfRecord]) -> None: ...


class InMemorySessionState:
    """Pure in-memory ``SessionState`` — the reference implementation and the test double.
    Results are kept insertion-ordered (``dict`` order); selection is an explicit id, never
    inferred, so "nothing selected" (None) stays distinct from "the first result"."""

    def __init__(self) -> None:
        self._baseline: Optional[ReferencePlan] = None
        self._draft: Optional[PlanDraft] = None
        self._scenarios: dict[str, Scenario] = {}       # insertion-ordered, keyed by scenario_id
        self._current_schedule: Optional[str] = None    # scenario_id, or None == the baseline
        self._run_config: Optional[RunConfig] = None
        self._results: dict[str, RunResult] = {}
        self._selected_id: Optional[str] = None
        self._plan_of_record: Optional[PlanOfRecord] = None

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
        """The scenario the current-schedule pointer names, or None (== the baseline, or a
        pointer whose scenario is gone)."""
        if self._current_schedule is None:
            return None
        return self._scenarios.get(self._current_schedule)

    def set_scenario(self, scenario: Optional[Scenario]) -> None:
        """Shim over (collection + pointer): None detaches to the baseline (does NOT delete
        any stored scenario); a scenario is stored (add-or-update) and made current."""
        if scenario is None:
            self._current_schedule = None
            return
        self._scenarios[scenario.scenario_id] = scenario
        self._current_schedule = scenario.scenario_id

    def list_scenarios(self) -> tuple[Scenario, ...]:
        return tuple(self._scenarios.values())

    def add_scenario(self, scenario: Scenario) -> None:
        self._scenarios[scenario.scenario_id] = scenario

    def remove_scenario(self, scenario_id: str) -> None:
        self._scenarios.pop(scenario_id, None)

    def get_current_scenario_id(self) -> Optional[str]:
        return self._current_schedule

    def set_current_scenario_id(self, scenario_id: Optional[str]) -> None:
        self._current_schedule = scenario_id

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

    def get_plan_of_record(self) -> Optional[PlanOfRecord]:
        return self._plan_of_record

    def set_plan_of_record(self, plan_of_record: Optional[PlanOfRecord]) -> None:
        self._plan_of_record = plan_of_record


# =============================================================================
# session lifecycle — resolving a scenario / draft against a newly-loaded baseline
# =============================================================================

@dataclass(frozen=True)
class BaselineResolution:
    """What survived a baseline switch: how many stored scenarios were kept (still bound to
    the new revision) vs dropped (bound to a different one), whether the current-schedule
    pointer had to reset to the baseline (its scenario was dropped), whether the draft was
    kept, and whether the rolling plan of record was kept (still bound to the new revision)."""
    scenarios_kept: int
    scenarios_dropped: int
    current_reset: bool
    draft_kept: bool
    plan_of_record_kept: bool = True


def resolve_for_new_baseline(
    session: SessionState, new_baseline: ReferencePlan
) -> BaselineResolution:
    """Point the session at ``new_baseline`` and resolve now-incompatible scenarios / draft.

    A scenario or draft is bound (by ``base_plan_hash``) to the revision it was built against.
    When the baseline changes, anything bound to a DIFFERENT revision is dropped — a delta or
    an edit built against another revision is never silently carried onto the new baseline
    (that would apply a mismatched change). Anything already bound to the new revision is kept.
    If the current-schedule pointer named a dropped scenario, it resets to the baseline.
    Returns what survived. This is the pure core of the app shell's source-key guard, so the
    "loading a new baseline resolves incompatible scenarios/draft" contract is testable
    without Streamlit."""
    session.set_baseline(new_baseline)

    current_before = session.get_current_scenario_id()
    kept = dropped = 0
    for scenario in session.list_scenarios():   # tuple snapshot: safe to remove while iterating
        if scenario.base_plan_hash == new_baseline.plan_hash:
            kept += 1
        else:
            session.remove_scenario(scenario.scenario_id)
            dropped += 1

    # The pointer named a scenario that was dropped (get_scenario now returns None) -> reset it
    # to the baseline. A pointer to a surviving scenario, or one already on the baseline, stays.
    current_reset = current_before is not None and session.get_scenario() is None
    if current_reset:
        session.set_current_scenario_id(None)

    draft = session.get_draft()
    draft_kept = draft is not None and draft.base_plan_hash == new_baseline.plan_hash
    if draft is not None and not draft_kept:
        session.clear_draft()

    # The rolling plan of record is bound (by base_plan_hash) to the revision it was built
    # against; a chain of adopted replans is meaningless against a different revision. Drop it
    # when the revision changes; keep it (or a None PoR) otherwise.
    por = session.get_plan_of_record()
    plan_of_record_kept = por is None or por.base_plan_hash == new_baseline.plan_hash
    if por is not None and not plan_of_record_kept:
        session.set_plan_of_record(None)

    return BaselineResolution(scenarios_kept=kept, scenarios_dropped=dropped,
                              current_reset=current_reset, draft_kept=draft_kept,
                              plan_of_record_kept=plan_of_record_kept)
