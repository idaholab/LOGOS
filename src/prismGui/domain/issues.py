"""domain/issues.py — the structured finding every producer emits.

Defined first because validation, referential-integrity checks, materialization,
disposition, and the scheduling audit all return `Issue`s — never raw strings.
Mirrors the skeleton (dev_docs/prism-gui-skeletons.py) and model-spec §1 exactly.
Pure: stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    ERROR = "error"      # blocks commit or feasibility
    WARNING = "warning"  # does not block; affects disposition
    INFO = "info"        # advisory


class IssueCategory(str, Enum):
    SCHEMA = "schema"
    REFERENTIAL_INTEGRITY = "referential_integrity"
    FEASIBILITY = "feasibility"
    TIME_WINDOW = "time_window"
    DOSE = "dose"
    SYSTEM_STATE = "system_state"
    EXECUTION = "execution"
    PROVENANCE = "provenance"


class IssueCode(str, Enum):
    """Phase 1–2 core catalogue (model-spec §1). Extensible; tests and UI depend
    on these. Schedule-audit findings that have no identical twin here use a
    dynamically-built ``AUDIT_<TYPE>`` code (adapter §6) rather than an enum member,
    so this catalogue stays the *core* set without 13 audit rows."""
    SCHEMA_TYPE_ERROR = "SCHEMA_TYPE_ERROR"
    SCHEMA_RANGE_ERROR = "SCHEMA_RANGE_ERROR"
    REF_MISSING = "REF_MISSING"
    DUP_ID = "DUP_ID"
    DUP_DEPENDENCY = "DUP_DEPENDENCY"
    # DEP_CYCLE also covers a self-referencing successor / a hold point blocking
    # itself (a degenerate 1-cycle) — the validator reports these the same way, so
    # there is no separate DEP_SELF_LOOP code (model-spec §1, §8b).
    DEP_CYCLE = "DEP_CYCLE"
    HOLD_POINT_MISUSE = "HOLD_POINT_MISUSE"   # non-hold-point task carries hold fields
    INVALID_MODE = "INVALID_MODE"
    INVALID_TIME_WINDOW = "INVALID_TIME_WINDOW"
    INVALID_AVAILABILITY_INTERVAL = "INVALID_AVAILABILITY_INTERVAL"
    INSUFFICIENT_RESOURCE = "INSUFFICIENT_RESOURCE"   # coarse shortfall (warning)
    EMERGENT_ID_COLLISION = "EMERGENT_ID_COLLISION"
    MATERIALIZE_CONFLICT = "MATERIALIZE_CONFLICT"
    UNSCHEDULED_TASK = "UNSCHEDULED_TASK"
    DEP_VIOLATION = "DEP_VIOLATION"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    PROV_HASH_MISMATCH = "PROV_HASH_MISMATCH"
    SNAPSHOT_MISSING = "SNAPSHOT_MISSING"


@dataclass(frozen=True)
class Issue:
    """Structured validation/diagnostic finding. Never a raw PRISM string.

    ``code`` is either an ``IssueCode`` member or a plain ``str`` for the
    dynamically-built ``AUDIT_<TYPE>`` audit codes (adapter §6). Both are stored
    as-is; comparisons use ``code_value`` when a string form is wanted.
    """
    code: "IssueCode | str"
    severity: Severity
    category: IssueCategory
    message: str
    entity_type: Optional[str] = None   # "task" | "dependency" | "resource" | ...
    entity_id: Optional[str] = None
    field_path: Optional[str] = None    # JSON Pointer-like path to the exact field
    suggested_action: Optional[str] = None

    @property
    def code_value(self) -> str:
        """The code as a plain string, whether it is an IssueCode or a raw AUDIT_* str."""
        return self.code.value if isinstance(self.code, IssueCode) else str(self.code)


def has_blocking(issues: tuple[Issue, ...]) -> bool:
    """True iff any issue is ERROR severity (model-spec §1: error blocks)."""
    return any(i.severity is Severity.ERROR for i in issues)
