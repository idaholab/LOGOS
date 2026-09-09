"""infrastructure/validation_adapter.py — ValidationPort over the existing validator.

Wraps the PURE ``src/CPM/validate_outage_data.py`` (Draft7 schema + referential
integrity, stdlib + ``jsonschema`` only — no PRISM, no scheduling, no Streamlit) behind
``ValidationPort``, mapping its ``(is_valid, errors, warnings)`` string output to
structured ``Issue``s per model-spec §8b. The domain depends on the *port*, never on this
module; this adapter is the only code that imports the validator — exactly as the PRISM
adapter is the only code that imports the engine.

Three CLI-isms of the wrapped module are isolated here (§8b embedding caveats):

1. **No process exits.** The module ``sys.exit(1)``s on a falsy ``schema_path``
   (``OutageDataValidator.__init__``) and on a missing ``jsonschema`` import (module
   level). A library must never kill the Streamlit process, so the adapter refuses a
   falsy path with a catchable ``ValueError`` *before* constructing the validator, and
   treats ``jsonschema`` as a hard dependency (present in the environment).
2. **Always a real schema path.** The module's not-found fallback returns an *undefined*
   ``DEFAULT_SCHEMA`` (``validate_outage_data.py:76`` — a ``NameError``), so the adapter
   verifies the path exists up front and raises a catchable ``FileNotFoundError`` if not,
   guaranteeing that branch is never reached.
3. **Strings, not records.** The module returns human-readable strings today, so this
   adapter parses them. The formats are stable and enumerable (see the rule table below);
   an unrecognised message is never dropped — it becomes an ``Issue`` with the sentinel
   ``VALIDATOR_UNMAPPED`` code so drift surfaces loudly rather than silently.

Two behaviours the wrapped validator's *code* fixes against its own docs, verified by
reading ``validate_outage_data.py`` directly:

* ``strict_resource_overlaps`` affects **resource** availability overlaps ONLY. Equipment
  and location overlaps are validated with ``strict=True`` unconditionally
  (``validate_outage_data.py:171-172``), so they are ALWAYS errors regardless of the flag.
  The severity of an overlap therefore follows the list the message lands in (errors vs
  warnings); this adapter maps severity from that list, not from the message text.
* Schema ``format`` (``date`` / ``date-time``) is **not** asserted — the module builds a
  bare ``Draft7Validator(schema)`` with no format checker — so a timezone-naive or
  malformed date is tolerated at the schema layer (consistent with the UTC-naive
  tolerance the canonicalization/adapter specs record).
"""

from __future__ import annotations

import re
from pathlib import Path

from CPM.validate_outage_data import OutageDataValidator

from prismGui.domain.issues import Issue, IssueCategory, IssueCode, Severity

# Sentinel for a validator message no rule below recognises: preserved as a structured
# Issue (never dropped) so a change in the wrapped module's message formats is visible.
UNMAPPED_CODE = "VALIDATOR_UNMAPPED"

# Duplicate-ID label -> entity_type, and availability-label -> entity_type.
_DUP_ENTITY = {"task": "task", "resource skill": "resource",
               "equipment": "equipment", "location": "location"}
_AVAIL_ENTITY = {"Resource": "resource", "Equipment": "equipment", "Location": "location"}

# Draft7 message fragments that denote a numeric- or length-BOUND violation (-> RANGE);
# everything else the schema branch can emit (type mismatch, required, enum, pattern,
# additionalProperties, oneOf) is a TYPE error.
_RANGE_MARKERS = ("minimum", "maximum", "too short", "too long",
                  "too few", "too many", "multiple of")

_REF = IssueCategory.REFERENTIAL_INTEGRITY


def _classify_schema(detail: str) -> IssueCode:
    """Draft7 error text -> SCHEMA_RANGE_ERROR (numeric/length bound) or SCHEMA_TYPE_ERROR."""
    low = detail.lower()
    return (IssueCode.SCHEMA_RANGE_ERROR
            if any(m in low for m in _RANGE_MARKERS)
            else IssueCode.SCHEMA_TYPE_ERROR)


class _Rule:
    """One message->Issue-fields rule. ``build`` returns (code, category, entity_type,
    entity_id, field_path) or raises via a None match (handled by the caller)."""

    __slots__ = ("pattern", "code", "category", "entity_type", "id_group", "path_group")

    def __init__(self, pattern, code, category, *, entity_type=None,
                 id_group=None, path_group=None):
        self.pattern = re.compile(pattern)
        self.code = code
        self.category = category
        self.entity_type = entity_type
        self.id_group = id_group
        self.path_group = path_group


# Ordered rules; the FIRST whose pattern matches wins. Self-reference / self-block rules
# precede the generic reference rules so a "lists itself"/"blocks itself" message maps to
# DEP_CYCLE (a degenerate 1-cycle, §8b) rather than REF_MISSING.
_Q = r"'(?P<x>[^']*)'"          # a single-quoted capture named x (id_group="x")
_RULES: tuple[_Rule, ...] = (
    # --- schema branch (field_path from the validator's JSON-Pointer path) ------------
    _Rule(r"^Schema error at (?P<path>.+?): (?P<detail>.+)$",
          None, IssueCategory.SCHEMA, path_group="path"),
    # --- duplicate ids ----------------------------------------------------------------
    _Rule(r"^Duplicate (?P<label>task|resource skill|equipment|location) IDs found: ",
          IssueCode.DUP_ID, _REF),          # entity_type resolved from <label> in caller
    # --- self references / self-blocks (degenerate 1-cycle) BEFORE generic ref rules --
    _Rule(r"^Task '(?P<x>[^']*)' lists itself as a successor \(self-reference\)$",
          IssueCode.DEP_CYCLE, _REF, entity_type="task", id_group="x"),
    _Rule(r"^Task '(?P<x>[^']*)' blocks itself \(self-reference\)$",
          IssueCode.DEP_CYCLE, _REF, entity_type="task", id_group="x"),
    _Rule(r"^Hold task '(?P<x>[^']*)' cannot block itself$",
          IssueCode.DEP_CYCLE, _REF, entity_type="task", id_group="x"),
    # --- dangling references ----------------------------------------------------------
    _Rule(r"^Task '(?P<x>[^']*)' references non-existent successor '[^']*'$",
          IssueCode.REF_MISSING, _REF, entity_type="task", id_group="x"),
    _Rule(r"^Task '(?P<x>[^']*)' references non-existent blocked task '[^']*'$",
          IssueCode.REF_MISSING, _REF, entity_type="task", id_group="x"),
    _Rule(r"^Task '(?P<x>[^']*)' references non-existent location '[^']*'$",
          IssueCode.REF_MISSING, _REF, entity_type="task", id_group="x"),
    _Rule(r"^Task '(?P<x>[^']*)' references non-existent equipment '[^']*'$",
          IssueCode.REF_MISSING, _REF, entity_type="task", id_group="x"),
    _Rule(r"^Task '(?P<x>[^']*)' requires skill '[^']*' which is not defined$",
          IssueCode.REF_MISSING, _REF, entity_type="task", id_group="x"),
    _Rule(r"^Hold task '(?P<x>[^']*)' blocks non-existent task '[^']*'$",
          IssueCode.REF_MISSING, _REF, entity_type="task", id_group="x"),
    # --- hold-point misuse (error) and hold-point missing type (warning) --------------
    _Rule(r"^Task '(?P<x>[^']*)' is not a hold point but has "
          r"hold_point_type/blocks_tasks defined$",
          IssueCode.HOLD_POINT_MISUSE, _REF, entity_type="task", id_group="x"),
    _Rule(r"^Task '(?P<x>[^']*)' marked as hold point but "
          r"'hold_point_type' is missing/null$",
          IssueCode.HOLD_POINT_MISUSE, _REF, entity_type="task", id_group="x"),
    # --- circular dependency ----------------------------------------------------------
    _Rule(r"^Circular dependency detected involving '(?P<x>[^']*)'$",
          IssueCode.DEP_CYCLE, _REF, entity_type="task", id_group="x"),
    # --- availability periods (severity follows the errors/warnings list) -------------
    _Rule(r"^(?P<label>Resource|Equipment|Location) '(?P<x>[^']*)': "
          r"invalid date-time format in period \d+",
          IssueCode.INVALID_AVAILABILITY_INTERVAL, _REF, id_group="x"),
    _Rule(r"^(?P<label>Resource|Equipment|Location) '(?P<x>[^']*)': "
          r"start_date >= end_date in period \d+$",
          IssueCode.INVALID_AVAILABILITY_INTERVAL, _REF, id_group="x"),
    _Rule(r"^(?P<label>Resource|Equipment|Location) '(?P<x>[^']*)': "
          r"overlapping availability periods \d+ and \d+$",
          IssueCode.INVALID_AVAILABILITY_INTERVAL, _REF, id_group="x"),
    # --- coarse resource sufficiency (warning; feasibility) ---------------------------
    _Rule(r"^Skill '(?P<x>[^']*)': max single-task demand \(\d+\) exceeds ",
          IssueCode.INSUFFICIENT_RESOURCE, IssueCategory.FEASIBILITY,
          entity_type="resource", id_group="x"),
)


class OutageValidatorAdapter:
    """ValidationPort wrapping ``OutageDataValidator``.

    Construct once with the real schema path; ``validate_plan`` is reusable across many
    plans (the wrapped validator resets its accumulators each call).
    """

    def __init__(self, schema_path: str, *, strict_resource_overlaps: bool = False) -> None:
        if not schema_path:                                   # caveat 1: no sys.exit
            raise ValueError("schema_path is required (the validator would sys.exit on a "
                             "falsy path)")
        if not Path(schema_path).exists():                    # caveat 2: no NameError fallback
            raise FileNotFoundError(
                f"schema file not found: {schema_path!r} (the validator's not-found "
                "fallback references an undefined DEFAULT_SCHEMA)")
        self._validator = OutageDataValidator(schema_path)
        self._strict = strict_resource_overlaps

    def validate_plan(self, plan_data: dict) -> tuple[Issue, ...]:
        """Validate a schema-shaped plan dict, returning structured Issues.

        Errors -> ERROR severity, warnings -> WARNING severity; ``is_valid`` is not
        surfaced separately (it is redundant with 'no ERROR-severity Issue')."""
        _is_valid, errors, warnings = self._validator.validate(
            plan_data, strict_resource_overlaps=self._strict)
        issues = [self._map(msg, Severity.ERROR) for msg in errors]
        issues.extend(self._map(msg, Severity.WARNING) for msg in warnings)
        return tuple(issues)

    # ------------------------------ message -> Issue ------------------------------
    def _map(self, message: str, severity: Severity) -> Issue:
        for rule in _RULES:
            m = rule.pattern.match(message)
            if m is None:
                continue
            if rule.category is IssueCategory.SCHEMA:
                # schema branch: code from the detail text, field_path from the pointer
                return Issue(
                    code=_classify_schema(m.group("detail")), severity=severity,
                    category=IssueCategory.SCHEMA, message=message,
                    field_path=m.group(rule.path_group))
            entity_type = rule.entity_type or _AVAIL_ENTITY.get(m.groupdict().get("label"))
            if entity_type is None and "label" in m.groupdict():   # duplicate-id rule
                entity_type = _DUP_ENTITY.get(m.group("label"))
            entity_id = m.group(rule.id_group) if rule.id_group else None
            return Issue(
                code=rule.code, severity=severity, category=rule.category,
                message=message, entity_type=entity_type, entity_id=entity_id)
        # Unrecognised: preserve it structurally rather than drop it (drift alarm).
        return Issue(code=UNMAPPED_CODE, severity=severity,
                     category=IssueCategory.REFERENTIAL_INTEGRITY, message=message)
