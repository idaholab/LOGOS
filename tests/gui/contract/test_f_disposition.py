"""Contract group F — Disposition policy (pure, tri-state, precedence).

All six are pure: build a ScheduleSummary + Issue tuple and assert compute_disposition's
indicators and overall verdict. Covers the precedence rows (any ERROR -> BLOCKED; a
window WARNING with a complete schedule -> READY_WITH_WARNINGS; clean -> READY;
unscheduled -> BLOCKED) and the tri-state distinction (audit not run -> NOT_EVALUATED,
not FALSE).
"""

from __future__ import annotations

from prismGui.domain.disposition import ScheduleSummary, compute_disposition
from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity
from prismGui.domain.results import DispositionOverall, Tri


def _issue(code, severity, category, message="x"):
    return Issue(code=code, severity=severity, category=category, message=message)


class TestDisposition:

    def test_compute_disposition_is_pure(self):
        """Same (summary, issues) -> equal Disposition, with no mutation of the inputs."""
        summary = ScheduleSummary(produced=True, n_unscheduled=0, audit_ran=True)
        first = compute_disposition(summary, ())
        second = compute_disposition(summary, ())
        assert first == second
        assert summary == ScheduleSummary(produced=True, n_unscheduled=0, audit_ran=True)

    def test_blocked_when_any_error_issue(self):
        """Any ERROR-severity issue -> overall BLOCKED regardless of the indicators."""
        err = _issue(IssueCode.INSUFFICIENT_RESOURCE, Severity.ERROR, IssueCategory.FEASIBILITY)
        disp = compute_disposition(
            ScheduleSummary(produced=True, n_unscheduled=0, audit_ran=True), (err,))
        assert disp.overall is DispositionOverall.BLOCKED
        assert disp.indicators.hard_feasible is Tri.FALSE

    def test_ready_with_warnings_for_window_violation_only(self):
        """A window violation (WARNING, non-blocking) on a complete schedule ->
        READY_WITH_WARNINGS, and the window indicator is TRUE."""
        warn = _issue(IssueCode.INVALID_TIME_WINDOW, Severity.WARNING, IssueCategory.TIME_WINDOW)
        disp = compute_disposition(
            ScheduleSummary(produced=True, n_unscheduled=0, audit_ran=True), (warn,))
        assert disp.overall is DispositionOverall.READY_WITH_WARNINGS
        assert disp.indicators.has_window_violations is Tri.TRUE

    def test_ready_when_clean(self):
        """Complete schedule, nothing unscheduled, no warnings, audit passed -> READY."""
        disp = compute_disposition(
            ScheduleSummary(produced=True, n_unscheduled=0, audit_ran=True), ())
        assert disp.overall is DispositionOverall.READY
        assert disp.indicators.input_valid is Tri.TRUE
        assert disp.indicators.schedule_complete is Tri.TRUE
        assert disp.indicators.audit_passed is Tri.TRUE

    def test_audit_not_run_is_tri_not_evaluated(self):
        """When the audit did not run, audit_passed / has_window_violations are
        NOT_EVALUATED, distinct from FALSE."""
        disp = compute_disposition(
            ScheduleSummary(produced=True, n_unscheduled=0, audit_ran=False), ())
        assert disp.indicators.audit_passed is Tri.NOT_EVALUATED
        assert disp.indicators.has_window_violations is Tri.NOT_EVALUATED

    def test_unscheduled_tasks_reflected_in_indicators(self):
        """n_unscheduled > 0 -> has_unscheduled_tasks TRUE, schedule_complete FALSE, and
        the schedule cannot be READY (an incomplete schedule blocks)."""
        disp = compute_disposition(
            ScheduleSummary(produced=True, n_unscheduled=2, audit_ran=True), ())
        assert disp.indicators.has_unscheduled_tasks is Tri.TRUE
        assert disp.indicators.schedule_complete is Tri.FALSE
        assert disp.overall is DispositionOverall.BLOCKED
