"""Contract group J — Input validation (structural): schema, references, graph, numerics.

Drives the ValidationPort adapter (group K's seam) with small plans that each provoke one
structural defect, asserting the §1/§8b code and — for the resource-shortfall case — that
a warning does NOT block. The wrapped validator is pure, so this runs in the contract job.

Deferred (NOT written — outside this thin slice / not caught by the wrapped validator, so
they cannot yet pass with real assertions; left uncollected so the negative-inference gate
does not require them):
  * ``test_duplicate_dependency_handled`` — duplicate-edge de-duplication is a
    serialization/domain concern, not a validator rule.
  * ``test_invalid_time_window_rejected`` — neither the schema (no earliest<=latest rule)
    nor the referential checks inspect ``time_windows`` ordering; a real check arrives with
    the window-handling increment.
  * ``test_scenario_resource_changes_same_hour_rejected`` /
    ``test_scenario_changes_applied_in_chronological_order`` — Scenario-delta application
    (materialize/editing), not the plan validator.
  * ``test_timezone_naive_start_date_rejected`` — the schema types ``start_date`` as
    ``date`` (YYYY-MM-DD) and the module builds a bare Draft7Validator with NO format
    checker, so naive/date-only project dates are tolerated (matching the UTC-naive
    tolerance the canonicalization/adapter specs record).
  * ``test_canonical_numeric_edge_cases_defined`` — canonical numeric normalization
    (NaN/inf/-0) is a hashing/domain invariant (group C / hashing), not input validation.

The two editing/session-lifecycle contracts formerly deferred here —
``test_emergent_task_id_collision_rejected`` and
``test_loading_new_baseline_resolves_incompatible_scenario_and_draft`` — landed with Phase 2,
Increment 2 (below): the first drives ``domain.materialize``'s emergent-id collision check,
the second the pure ``services.resolve_for_new_baseline``. Both run in this contract job
(pure domain/application, no PRISM), so the negative-inference gate now enforces them.
"""

from __future__ import annotations

import copy

from prismGui.application import services
from prismGui.application.services import InMemorySessionState
from prismGui.domain import serialization as ser
from prismGui.domain.issues import IssueCategory, IssueCode, Severity, has_blocking
from prismGui.domain.materialize import materialize
from prismGui.domain.plan import ResourceReq, Task, open_draft
from prismGui.domain.scenario import Scenario
from prismGui.domain.versions import SCHEMA_VERSION


def _codes(issues):
    return {i.code for i in issues}


class TestInputValidation:

    def test_duplicate_task_or_resource_ids_rejected(self, validator_adapter, raw_plan):
        """A duplicated task_id -> DUP_ID (error, referential_integrity)."""
        plan = copy.deepcopy(raw_plan)
        plan["tasks"].append({
            "task_id": "A", "description": "duplicate id", "duration": 2.0,
            "successors": [], "location_id": None,
            "required_resources": [{"skill_type": "MECH", "crew_count": 1}],
            "required_equipment": [], "is_hold_point": False})
        issues = validator_adapter.validate_plan(plan)
        assert IssueCode.DUP_ID in _codes(issues)
        assert has_blocking(issues) is True

    def test_dependency_missing_endpoint_rejected(self, validator_adapter, raw_plan):
        """A successor referencing a nonexistent task -> REF_MISSING."""
        plan = copy.deepcopy(raw_plan)
        plan["tasks"][0]["successors"] = ["ZZZ"]
        issues = validator_adapter.validate_plan(plan)
        assert any(i.code is IssueCode.REF_MISSING and i.entity_id == "A" for i in issues)

    def test_dependency_self_loop_rejected(self, validator_adapter, raw_plan):
        """A task listing itself as a successor -> DEP_CYCLE (a degenerate 1-cycle; there
        is no separate DEP_SELF_LOOP code — §8b)."""
        plan = copy.deepcopy(raw_plan)
        plan["tasks"][0]["successors"] = ["A"]
        issues = validator_adapter.validate_plan(plan)
        assert IssueCode.DEP_CYCLE in _codes(issues)
        assert IssueCode.REF_MISSING not in _codes(issues)   # 'A' exists; not a dangling ref

    def test_dependency_cycle_detected(self, validator_adapter, raw_plan):
        """A cycle across the dependency graph (A->B->A) -> DEP_CYCLE."""
        plan = copy.deepcopy(raw_plan)
        plan["tasks"][0]["successors"] = ["B"]
        plan["tasks"][1]["successors"] = ["A"]
        issues = validator_adapter.validate_plan(plan)
        assert IssueCode.DEP_CYCLE in _codes(issues)

    def test_negative_duration_rejected(self, validator_adapter, raw_plan):
        """A non-positive task duration -> SCHEMA_RANGE_ERROR (duration exclusiveMinimum 0)."""
        plan = copy.deepcopy(raw_plan)
        plan["tasks"][0]["duration"] = -1
        issues = validator_adapter.validate_plan(plan)
        assert IssueCode.SCHEMA_RANGE_ERROR in _codes(issues)
        assert all(i.category is IssueCategory.SCHEMA for i in issues
                   if i.code is IssueCode.SCHEMA_RANGE_ERROR)

    def test_negative_capacity_rejected(self, validator_adapter, raw_plan):
        """A below-minimum crew_count -> SCHEMA_RANGE_ERROR (crew_count integer minimum 1)."""
        plan = copy.deepcopy(raw_plan)
        plan["tasks"][0]["required_resources"][0]["crew_count"] = 0
        issues = validator_adapter.validate_plan(plan)
        assert IssueCode.SCHEMA_RANGE_ERROR in _codes(issues)

    def test_availability_start_ge_end_rejected(self, validator_adapter, raw_plan):
        """An availability period with start >= end -> INVALID_AVAILABILITY_INTERVAL."""
        plan = copy.deepcopy(raw_plan)
        plan["resources"][0]["availability_periods"][0]["start_date"] = "2025-01-09T00:00:00"
        issues = validator_adapter.validate_plan(plan)
        assert any(i.code is IssueCode.INVALID_AVAILABILITY_INTERVAL and i.entity_id == "MECH"
                   for i in issues)

    def test_non_hold_point_task_carrying_hold_fields_rejected(self, validator_adapter, raw_plan):
        """A non-hold-point task carrying blocks_tasks -> HOLD_POINT_MISUSE (error). One of
        the two codes the validation seam adds (§8b)."""
        plan = copy.deepcopy(raw_plan)
        plan["tasks"][0]["blocks_tasks"] = ["B"]         # is_hold_point stays False
        issues = validator_adapter.validate_plan(plan)
        assert any(i.code is IssueCode.HOLD_POINT_MISUSE and i.severity is Severity.ERROR
                   for i in issues)

    def test_resource_shortfall_warns_not_blocks(self, validator_adapter, raw_plan):
        """A coarse resource-sufficiency shortfall -> INSUFFICIENT_RESOURCE at WARNING
        severity in the feasibility category: it affects disposition but does NOT block."""
        plan = copy.deepcopy(raw_plan)
        plan["tasks"][1]["required_resources"] = [{"skill_type": "MECH", "crew_count": 5}]
        issues = validator_adapter.validate_plan(plan)
        shortfalls = [i for i in issues if i.code is IssueCode.INSUFFICIENT_RESOURCE]
        assert shortfalls
        assert all(i.severity is Severity.WARNING and i.category is IssueCategory.FEASIBILITY
                   for i in shortfalls)
        assert has_blocking(issues) is False             # a warning alone never blocks

    # --- Phase 2, Increment 2: editing / session-lifecycle contracts (now real) ---

    def test_emergent_task_id_collision_rejected(self, baseline):
        """An emergent task reusing an existing baseline task id is rejected by materialize
        (EMERGENT_ID_COLLISION, referential_integrity) — never appended or overwritten. The
        emergent task is valid in every other respect (MECH exists in the baseline), so the
        id collision is the sole error, and the combination fails closed
        (``ok is False``, ``effective_plan is None``)."""
        scenario = Scenario(
            scenario_id="scn-collision",
            base_plan_id=baseline.plan_id,
            base_plan_hash=baseline.plan_hash,
            name="reuse id A",
            emergent_tasks=(
                Task(task_id="A", duration=3.0, description="collides with baseline A",
                     required_resources=(ResourceReq(skill_type="MECH", crew_count=1),)),
            ),
        )
        outcome = materialize(baseline, scenario)
        assert outcome.ok is False
        assert outcome.effective_plan is None
        assert IssueCode.EMERGENT_ID_COLLISION in _codes(outcome.issues)

    def test_loading_new_baseline_resolves_incompatible_scenario_and_draft(self, baseline, raw_plan):
        """Loading a NEW baseline resolves anything bound to a prior revision: a scenario or
        draft built against a different plan_hash is cleared (never carried onto the new
        baseline as a mismatched delta), while one bound to the new revision is kept. Drives
        the pure ``services.resolve_for_new_baseline`` the app-shell source guard calls."""
        session = InMemorySessionState()
        session.set_baseline(baseline)
        # A scenario + a draft, both bound to the ORIGINAL baseline revision.
        session.set_scenario(Scenario(
            scenario_id="scn-old", base_plan_id=baseline.plan_id,
            base_plan_hash=baseline.plan_hash, name="bound to old"))
        session.set_draft(open_draft(baseline))

        # A different revision of the same logical plan (edit a duration -> new plan_hash).
        mutated = copy.deepcopy(raw_plan)
        mutated["tasks"][0]["duration"] = 99
        new_baseline = ser.build_reference_plan(
            "baseline-1", mutated, schema_version=SCHEMA_VERSION)
        assert new_baseline.plan_hash != baseline.plan_hash

        resolution = services.resolve_for_new_baseline(session, new_baseline)
        assert session.get_baseline() is new_baseline
        assert resolution.scenario_kept is False and resolution.draft_kept is False
        assert session.get_scenario() is None          # incompatible scenario cleared
        assert session.get_draft() is None             # incompatible draft cleared

        # Now bind a scenario + draft to the NEW revision and resolve against it again:
        # both are compatible, so both are RETAINED (clear the incompatible, keep the rest).
        kept_scenario = Scenario(
            scenario_id="scn-new", base_plan_id=new_baseline.plan_id,
            base_plan_hash=new_baseline.plan_hash, name="bound to new")
        kept_draft = open_draft(new_baseline)
        session.set_scenario(kept_scenario)
        session.set_draft(kept_draft)

        resolution = services.resolve_for_new_baseline(session, new_baseline)
        assert resolution.scenario_kept is True and resolution.draft_kept is True
        assert session.get_scenario() is kept_scenario
        assert session.get_draft() is kept_draft
