"""domain/disposition.py — the pure Ready / Ready-with-warnings / Blocked policy.

Disposition is the single badge the UI shows for "can I act on this schedule?" It is
PURE domain policy (model-spec §6): the application layer gathers a ``ScheduleSummary``
and the run's ``Issue``s and calls ``compute_disposition``; the *decision* lives here so
it is tested in one place and no adapter re-invents it.

Two halves:
  * six tri-state INDICATORS — each TRUE / FALSE / NOT_EVALUATED, because "we didn't
    check" (no schedule, no audit) is not the same as "checked and false";
  * one OVERALL verdict via explicit first-match precedence.

Issue-classification predicates (model-spec §6, adapter §6) are defined once here:
  * input error   — ERROR whose category is input-level (schema / ref-integrity / provenance)
  * hard error    — ERROR that is NOT input-level (feasibility / execution / window / dose / …)
  * audit issue   — an ``AUDIT_*`` code, or one of UNSCHEDULED_TASK / DEP_VIOLATION / INVALID_MODE
  * window issue  — category TIME_WINDOW
Pure: stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass

from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity, has_blocking
from prismGui.domain.results import (
    Disposition,
    DispositionIndicators,
    DispositionOverall,
    Tri,
)

# Input-level error categories: an ERROR here means the INPUT is invalid (blocks
# before any feasibility question). Everything else is a feasibility/runtime concern.
_INPUT_CATEGORIES = frozenset(
    {IssueCategory.SCHEMA, IssueCategory.REFERENTIAL_INTEGRITY, IssueCategory.PROVENANCE}
)

# Findings that come from the schedule audit / dependency check (adapter §6).
_AUDIT_CODES = frozenset(
    {IssueCode.UNSCHEDULED_TASK, IssueCode.DEP_VIOLATION, IssueCode.INVALID_MODE}
)


@dataclass
class ScheduleSummary:
    """Minimal facts compute_disposition needs, extracted from a produced schedule."""
    produced: bool          # a schedule was returned at all
    n_unscheduled: int      # tasks the SGS could not place
    audit_ran: bool         # validate_schedule() executed (vs. skipped)


# --- issue predicates --------------------------------------------------------

def _is_input_error(i: Issue) -> bool:
    return i.severity is Severity.ERROR and i.category in _INPUT_CATEGORIES


def _is_hard_error(i: Issue) -> bool:
    return i.severity is Severity.ERROR and i.category not in _INPUT_CATEGORIES


def _is_audit_issue(i: Issue) -> bool:
    return i.code_value.startswith("AUDIT_") or i.code in _AUDIT_CODES


def _is_window_issue(i: Issue) -> bool:
    return i.category is IssueCategory.TIME_WINDOW


def _any(issues: tuple[Issue, ...], pred) -> bool:
    return any(pred(i) for i in issues)


# --- the policy --------------------------------------------------------------

def compute_disposition(summary: ScheduleSummary, issues: tuple[Issue, ...]) -> Disposition:
    """Turn (schedule facts, issues) into indicators + one overall verdict. Pure and
    deterministic: same inputs -> same Disposition, no I/O."""
    input_valid = Tri.FALSE if _any(issues, _is_input_error) else Tri.TRUE

    if not summary.produced:
        schedule_complete = Tri.NOT_EVALUATED
        has_unscheduled = Tri.NOT_EVALUATED
        hard_feasible = Tri.NOT_EVALUATED
    else:
        schedule_complete = Tri.TRUE if summary.n_unscheduled == 0 else Tri.FALSE
        has_unscheduled = Tri.TRUE if summary.n_unscheduled > 0 else Tri.FALSE
        hard_feasible = Tri.FALSE if _any(issues, _is_hard_error) else Tri.TRUE

    if not summary.audit_ran:
        audit_passed = Tri.NOT_EVALUATED
        has_window_violations = Tri.NOT_EVALUATED
    else:
        # §6 truth table (line 338): audit_passed is FALSE only for an ERROR-severity
        # audit Issue. Soft audit findings (e.g. a `quality` warning) are audit issues
        # but WARNING severity, so they must NOT flip audit_passed — they feed
        # `ready_with_warnings` via `overall`, not `audit_passed == false`.
        audit_passed = (
            Tri.FALSE
            if _any(issues, lambda i: _is_audit_issue(i) and i.severity is Severity.ERROR)
            else Tri.TRUE
        )
        has_window_violations = Tri.TRUE if _any(issues, _is_window_issue) else Tri.FALSE

    indicators = DispositionIndicators(
        input_valid=input_valid,
        schedule_complete=schedule_complete,
        hard_feasible=hard_feasible,
        has_unscheduled_tasks=has_unscheduled,
        has_window_violations=has_window_violations,
        audit_passed=audit_passed,
    )

    # Overall — first match wins (model-spec §6). Incomplete schedule blocks: a
    # schedule that did not place every task cannot be acted on (this makes the
    # "n_unscheduled > 0 -> not READY" contract hold on its own, without relying on
    # an accompanying error issue).
    if input_valid is Tri.FALSE:
        overall = DispositionOverall.BLOCKED
    elif not summary.produced:
        overall = DispositionOverall.BLOCKED
    elif has_blocking(issues):
        overall = DispositionOverall.BLOCKED
    elif summary.n_unscheduled > 0:
        overall = DispositionOverall.BLOCKED
    elif any(i.severity is Severity.WARNING for i in issues):
        overall = DispositionOverall.READY_WITH_WARNINGS
    else:
        overall = DispositionOverall.READY

    return Disposition(overall=overall, indicators=indicators)
