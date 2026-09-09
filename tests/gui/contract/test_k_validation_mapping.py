"""Contract group K — Validation port & adapter mapping (§8b).

The ValidationPort seam wraps the existing PURE ``src/CPM/validate_outage_data.py``
(Draft7 schema + referential integrity, stdlib + jsonschema — no PRISM, no Streamlit).
These pin the PORT semantics (structured Issues; "valid" == "no ERROR-severity Issue")
and the adapter's message->code mapping + isolation of the module's CLI-isms. Because the
wrapped validator is pure, this whole group runs in the contract job, distinct from the
real-PRISM adapter-integration suite.

Deferred (NOT written; not part of the Step-4 gate):
``test_materialize_reuses_validation_port_on_effective_plan`` — materialize() is
deliberately port-free (a pure domain function that emits its own MATERIALIZE_CONFLICT /
PROV_HASH_MISMATCH); running the schema/referential validator on the *serialized*
effective plan is application-layer orchestration (Step 6: prepare_run), so this test
lands with the services wiring, not here. Writing it now would either be hollow (validate
a dict the test serialized itself, proving nothing about reuse) or force a port into the
pure domain. Left uncollected so the negative-inference gate does not require it yet.
"""

from __future__ import annotations

import copy

import pytest

from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity, has_blocking
from prismGui.infrastructure.validation_adapter import OutageValidatorAdapter


def _has(issues, code, severity, category, *, entity_id=None):
    """True iff some Issue matches (code, severity, category) — and entity_id if given.
    Presence, not exclusivity: some defects legitimately produce companion issues (a
    self-referencing successor is BOTH a self-reference and a 1-cycle)."""
    return any(
        i.code is code and i.severity is severity and i.category is category
        and (entity_id is None or i.entity_id == entity_id)
        for i in issues)


class TestValidationPortMapping:

    def test_validate_plan_returns_issue_tuple_not_strings(self, validator_adapter, baseline_invalid):
        """Port semantics: validate_plan returns a tuple of Issue objects, never the
        validator's raw (errors, warnings) strings."""
        issues = validator_adapter.validate_plan(baseline_invalid)
        assert isinstance(issues, tuple)
        assert issues                                   # baseline_invalid is not clean
        assert all(isinstance(i, Issue) for i in issues)
        assert not any(isinstance(i, str) for i in issues)

    def test_clean_plan_yields_no_error_severity_issues(self, validator_adapter, raw_plan):
        """'Valid' is expressed as 'no ERROR-severity Issue' — the validator's is_valid
        flag is not surfaced separately. The minimal plan is fully clean here."""
        issues = validator_adapter.validate_plan(raw_plan)
        assert has_blocking(issues) is False
        assert issues == ()                             # this plan provokes nothing at all

    def test_message_to_code_mapping_matches_spec_table(self, validator_adapter,
                                                         validator_adapter_strict, raw_plan):
        """THE mapping contract (§8b table): each single-defect plan yields an Issue with
        the specified (code, severity, category)."""

        def dup_task_id(p):
            p["tasks"].append({
                "task_id": "A", "description": "duplicate id", "duration": 2.0,
                "successors": [], "location_id": None,
                "required_resources": [{"skill_type": "MECH", "crew_count": 1}],
                "required_equipment": [], "is_hold_point": False})
            return p

        def schema_type(p):
            p["tasks"][0]["duration"] = "four"          # string where a number is required
            return p

        def schema_range(p):
            p["tasks"][0]["duration"] = -1              # exclusiveMinimum 0 -> below bound
            return p

        def dangling_successor(p):
            p["tasks"][0]["successors"] = ["ZZZ"]
            return p

        def undefined_skill(p):
            p["tasks"][0]["required_resources"] = [{"skill_type": "WELD", "crew_count": 1}]
            return p

        def self_successor(p):
            p["tasks"][0]["successors"] = ["A"]
            return p

        def circular(p):
            p["tasks"][0]["successors"] = ["B"]
            p["tasks"][1]["successors"] = ["A"]
            return p

        def bad_availability(p):
            p["resources"][0]["availability_periods"][0]["start_date"] = "2025-01-09T00:00:00"
            return p                                     # start now after end -> start>=end

        def hold_misuse(p):
            p["tasks"][0]["blocks_tasks"] = ["B"]        # blocks_tasks on a non-hold-point
            return p

        def shortfall(p):
            p["tasks"][1]["required_resources"] = [{"skill_type": "MECH", "crew_count": 5}]
            return p                                     # peak demand 5 > peak supply 3

        C, R, F, S = (IssueCategory.SCHEMA, IssueCategory.REFERENTIAL_INTEGRITY,
                      IssueCategory.FEASIBILITY, Severity)
        cases = [
            (schema_type,         IssueCode.SCHEMA_TYPE_ERROR,             S.ERROR,   C, None),
            (schema_range,        IssueCode.SCHEMA_RANGE_ERROR,            S.ERROR,   C, None),
            (dup_task_id,         IssueCode.DUP_ID,                        S.ERROR,   R, None),
            (dangling_successor,  IssueCode.REF_MISSING,                   S.ERROR,   R, "A"),
            (undefined_skill,     IssueCode.REF_MISSING,                   S.ERROR,   R, "A"),
            (self_successor,      IssueCode.DEP_CYCLE,                     S.ERROR,   R, "A"),
            (circular,            IssueCode.DEP_CYCLE,                     S.ERROR,   R, None),
            (bad_availability,    IssueCode.INVALID_AVAILABILITY_INTERVAL, S.ERROR,   R, "MECH"),
            (hold_misuse,         IssueCode.HOLD_POINT_MISUSE,             S.ERROR,   R, "A"),
            (shortfall,           IssueCode.INSUFFICIENT_RESOURCE,         S.WARNING, F, "MECH"),
        ]
        for mutate, code, severity, category, entity_id in cases:
            issues = validator_adapter.validate_plan(mutate(copy.deepcopy(raw_plan)))
            assert _has(issues, code, severity, category, entity_id=entity_id), (
                f"{mutate.__name__}: expected {code}/{severity}/{category} "
                f"(entity_id={entity_id}); got {[(i.code_value, i.severity.value) for i in issues]}")

    def test_schema_error_carries_field_path(self, validator_adapter, raw_plan):
        """Schema-branch issues populate field_path from the validator's JSON-Pointer-like
        path — the exact offending field, not merely the entity id."""
        plan = copy.deepcopy(raw_plan)
        plan["tasks"][0]["duration"] = "four"
        issues = validator_adapter.validate_plan(plan)
        schema_issues = [i for i in issues if i.category is IssueCategory.SCHEMA]
        assert schema_issues
        assert any(i.field_path == "/tasks/0/duration" for i in schema_issues)

    def test_strict_resource_overlaps_flag_changes_severity(
            self, validator_adapter, validator_adapter_strict, raw_plan_overlapping_resource):
        """A resource availability overlap is WARNING by default and ERROR under
        strict_resource_overlaps=True — the adapter forwards the flag and maps severity
        from the list the message lands in. (The flag governs RESOURCE overlaps only;
        equipment/location overlaps are unconditionally strict in the validator.)"""
        default = validator_adapter.validate_plan(raw_plan_overlapping_resource)
        strict = validator_adapter_strict.validate_plan(raw_plan_overlapping_resource)
        code = IssueCode.INVALID_AVAILABILITY_INTERVAL
        assert _has(default, code, Severity.WARNING, IssueCategory.REFERENTIAL_INTEGRITY)
        assert has_blocking(default) is False           # a warning does not block
        assert _has(strict, code, Severity.ERROR, IssueCategory.REFERENTIAL_INTEGRITY)
        assert has_blocking(strict) is True

    def test_adapter_never_exits_the_process(self):
        """Embedding caveat 1+2: a falsy or nonexistent schema_path raises a CATCHABLE
        error (ValueError / FileNotFoundError), never SystemExit (which would kill the
        Streamlit process) and never the module's undefined-DEFAULT_SCHEMA NameError.
        pytest.raises on an Exception subclass would not swallow SystemExit, so a match
        proves the process was not exited."""
        with pytest.raises(ValueError):
            OutageValidatorAdapter("")
        with pytest.raises(FileNotFoundError):
            OutageValidatorAdapter("/no/such/schema/path.json")

    def test_adapter_always_passes_a_real_schema_path(self, validator_adapter, raw_plan):
        """Embedding caveat 2: constructed with the real src/CPM/outage_schema.json, the
        not-found fallback (undefined DEFAULT_SCHEMA -> NameError) is never reached — a
        clean plan validates without error, proving the real schema loaded."""
        assert validator_adapter.validate_plan(raw_plan) == ()
