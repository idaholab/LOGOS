"""Contract group I — Issue & classification.

Two pure invariants (no port needed): has_blocking is true iff an ERROR is present, and
classify_float is the single float rule (constrained-chain -> CRITICAL; else <=
TF_ZERO_TOL incl. negative/None -> ZERO; else POSITIVE). Plus the two validator-driven
invariants — validation returns structured Issues (never raw strings), and a schema Issue
carries a field_path locating the offending field — which need the ValidationPort adapter
(Step 4) and so land here now that it exists.
"""

from __future__ import annotations

from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity, has_blocking
from prismGui.domain.results import FloatClass, TF_ZERO_TOL, classify_float


def _issue(severity, code=IssueCode.INSUFFICIENT_RESOURCE, category=IssueCategory.FEASIBILITY):
    return Issue(code=code, severity=severity, category=category, message="x")


class TestIssueContract:

    def test_has_blocking_true_iff_error_present(self):
        """has_blocking(issues) is True iff at least one Issue is ERROR severity."""
        assert has_blocking(()) is False
        warn = _issue(Severity.WARNING)
        info = _issue(Severity.INFO)
        assert has_blocking((warn, info)) is False
        err = _issue(Severity.ERROR, code=IssueCode.SCHEMA_TYPE_ERROR,
                     category=IssueCategory.SCHEMA)
        assert has_blocking((warn, err)) is True

    def test_classify_float_rule(self):
        """classify_float is THE rule: on-chain dominates -> CRITICAL; else actual TF
        at or below TF_ZERO_TOL (including negative and None-as-0) -> ZERO_FLOAT; else
        POSITIVE_FLOAT."""
        # on the constrained chain dominates regardless of the float value
        assert classify_float(100.0, True) is FloatClass.CRITICAL
        assert classify_float(-5.0, True) is FloatClass.CRITICAL
        assert classify_float(None, True) is FloatClass.CRITICAL
        # off chain: <= tolerance (incl. negative and None) -> zero
        assert classify_float(0.0, False) is FloatClass.ZERO_FLOAT
        assert classify_float(-0.5, False) is FloatClass.ZERO_FLOAT
        assert classify_float(None, False) is FloatClass.ZERO_FLOAT
        assert classify_float(TF_ZERO_TOL, False) is FloatClass.ZERO_FLOAT   # boundary inclusive
        # off chain, clearly positive float
        assert classify_float(TF_ZERO_TOL + 1.0, False) is FloatClass.POSITIVE_FLOAT

    def test_validation_returns_structured_issues_not_strings(self, validator_adapter,
                                                              baseline_invalid):
        """Validation output is Issue objects with code/severity/category, never the raw
        validator strings."""
        issues = validator_adapter.validate_plan(baseline_invalid)
        assert issues
        for i in issues:
            assert isinstance(i, Issue)
            assert not isinstance(i, str)
            assert isinstance(i.code, IssueCode)          # a mapped code, not a raw string
            assert isinstance(i.severity, Severity)
            assert isinstance(i.category, IssueCategory)

    def test_field_path_points_to_offending_field(self, validator_adapter, baseline_invalid):
        """Where applicable, an Issue carries a field_path locating the exact invalid field
        (the string-typed duration on task 0), not merely the entity id."""
        issues = validator_adapter.validate_plan(baseline_invalid)
        assert any(i.field_path == "/tasks/0/duration" for i in issues)
