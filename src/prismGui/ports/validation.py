"""ports/validation.py — schema + referential-integrity validation seam.

The domain and application depend on THIS port, never on the concrete
``src/CPM/validate_outage_data.py``. An infrastructure adapter (Step 4) wraps that
existing pure validator and maps its ``(is_valid, errors, warnings)`` output to
structured ``Issue``s (§8b table). Reimplementing the rules in the domain would create
two rule sets that silently drift, so ``materialize`` reuses this same port on the
serialized effective plan.

Pure seam: Protocol only, depends on domain ``Issue`` for the annotation; imports
neither PRISM nor Streamlit (the wrapped validator is stdlib + jsonschema).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from prismGui.domain.issues import Issue

JSONTree = dict[str, Any]


@runtime_checkable
class ValidationPort(Protocol):

    def validate_plan(self, plan_data: JSONTree) -> tuple[Issue, ...]:
        """Validate a schema-shaped plan dict (a loaded baseline, or a serialized
        EffectivePlan from ``materialize()``) and return structured Issues. "No
        ERROR-severity issue" == valid; ``is_valid`` is not surfaced separately. Never
        returns the validator's raw strings."""
        ...
