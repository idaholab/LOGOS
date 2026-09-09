"""domain/hashing.py — canonical serialization (RFC 8785 / JCS) + content hashes.

The whole Phase-1 provenance story rests on "the same plan always produces the same
bytes": the content-addressed snapshot store (hash = key), provenance (hash =
input identity), and stale/lineage detection (compare hashes). This module is the
single source of those bytes. Pure: stdlib only (no PRISM, Streamlit, jsonschema).

Design (prism-gui-canonicalization.md):
  * Scheme: RFC 8785 JSON Canonicalization Scheme (JCS) — UTF-8, object keys sorted
    by UTF-16 code unit, ECMAScript-`Number.prototype.toString` number formatting,
    no insignificant whitespace, array order preserved.
  * Envelope: hash ``{canon_version, schema_version, kind, payload}`` (§2) so an
    algorithm change can't silently collide with old hashes and a run_config can't
    collide with a reference_plan.
  * Numbers/time (§4): timestamps normalized to UTC ms ``…Z``; derived hour-offsets
    quantized to the 1 ms grid ``q``; counts exact; user-authored magnitudes as-is.
  * Digest: SHA-256, lowercase hex — the snapshot-store key.

``snapshot()`` builds the canonical STRING the SnapshotStore stores; ``hash_*()`` is
``sha256`` of that string, so ``store.put(snapshot) == hash_*()`` by construction.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from prismGui.domain.run_config import RunConfig
from prismGui.domain.scenario import Scenario
from prismGui.domain.versions import CANON_VERSION

JSONTree = dict[str, Any]

# 1 ms grid (engine _EVENT_EPSILON / _PREC_TOL). 0.015625 h -> 56250 ms exactly.
_MS_PER_HOUR = 3_600_000


def q(hours: float) -> float:
    """Quantize an hour-offset to the 1 ms grid (canonicalization spec §4)."""
    return round(hours * _MS_PER_HOUR) / _MS_PER_HOUR


# =============================================================================
# RFC 8785 §3 — the JCS serializer
# =============================================================================

def _es_number(x: float) -> str:
    """Format a float exactly as ECMAScript ``Number.prototype.toString`` (and thus
    JCS) does. Python's ``repr`` already yields the shortest round-tripping digits;
    this reproduces ECMAScript's *placement* of the decimal point / exponent so the
    bytes match a conformant JCS implementation (RFC 8785 §7.1.12.1)."""
    if x != x or x in (float("inf"), float("-inf")):
        raise ValueError("NaN and Infinity are not permitted in canonical JSON")
    if x == 0:
        return "0"                      # collapses -0.0 to "0" (ECMAScript)
    sign = "-" if x < 0 else ""
    r = repr(abs(x))
    # Split repr into integer/fraction digits and a base-10 exponent.
    if "e" in r or "E" in r:
        mant, exp_s = re.split("[eE]", r)
        exp = int(exp_s)
    else:
        mant, exp = r, 0
    if "." in mant:
        int_part, frac_part = mant.split(".")
    else:
        int_part, frac_part = mant, ""
    digits = (int_part + frac_part) or "0"
    m = int(digits)                      # strips leading zeros
    p = exp - len(frac_part)             # value == m * 10**p
    if m == 0:
        return "0"
    while m % 10 == 0:                   # strip trailing zeros -> shortest s
        m //= 10
        p += 1
    s = str(m)
    k = len(s)
    n = p + k                            # value == s * 10**(n-k); 10**(n-1) <= |x| < 10**n
    if k <= n <= 21:
        return sign + s + "0" * (n - k)
    if 0 < n <= 21:
        return sign + s[:n] + "." + s[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + s
    # exponential
    mantissa = s[0] + ("." + s[1:] if k > 1 else "")
    e = n - 1
    return sign + mantissa + "e" + ("+" if e >= 0 else "-") + str(abs(e))


# JCS two-character escapes (RFC 8785 §3.2.2.2); other control chars -> \u00xx.
_ESCAPES = {
    '"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f",
    "\n": "\\n", "\r": "\\r", "\t": "\\t",
}


def _jcs_string(s: str) -> str:
    out = ['"']
    for ch in s:
        esc = _ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)              # non-ASCII emitted as raw UTF-8 (not \u)
    out.append('"')
    return "".join(out)


def _jcs(obj: Any) -> str:
    if obj is None:
        return "null"
    if isinstance(obj, bool):           # before int (bool is an int subclass)
        return "true" if obj else "false"
    if isinstance(obj, str):
        return _jcs_string(obj)
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        return _es_number(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_jcs(v) for v in obj) + "]"
    if isinstance(obj, dict):
        # Keys sorted by UTF-16 code unit == the UTF-16-BE byte ordering.
        items = sorted(obj.items(), key=lambda kv: str(kv[0]).encode("utf-16-be"))
        return "{" + ",".join(_jcs_string(str(k)) + ":" + _jcs(v) for k, v in items) + "}"
    raise TypeError(f"not canonicalizable: {type(obj).__name__}")


# =============================================================================
# §4 — pre-canonicalization normalization of schema-shaped payloads
# =============================================================================

_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:[Zz]|[+-]\d{2}:?\d{2})?)?$"
)


def _normalize_timestamp(s: str) -> Optional[str]:
    """Return the canonical ``YYYY-MM-DDTHH:MM:SS.sssZ`` form of an ISO date/datetime
    string, or None if ``s`` is not timestamp-shaped. Naive timestamps are read as
    UTC (only the instant matters for the hash); date-only becomes midnight UTC."""
    if not _TS_RE.match(s):
        return None
    try:
        if "T" not in s and " " not in s:
            d = date.fromisoformat(s)
            dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s.replace(" ", "T"))
            dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


def _normalize_schema_payload(obj: Any) -> Any:
    """Recursively normalize timestamps in a schema-shaped tree (§4/§5). Array order
    preserved (§7); object-key ordering is left to JCS. Unknown fields pass through
    verbatim after timestamp normalization, so the hash covers the whole plan."""
    if isinstance(obj, str):
        norm = _normalize_timestamp(obj)
        return norm if norm is not None else obj
    if isinstance(obj, dict):
        return {k: _normalize_schema_payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalize_schema_payload(v) for v in obj]
    return obj


# =============================================================================
# §8 — public hashing API (pure)
# =============================================================================

def canonical_bytes(kind: str, payload: Any, *, schema_version: Optional[str] = None) -> bytes:
    """Envelope + JCS -> the canonical bytes (§2)."""
    envelope = {
        "canon_version": CANON_VERSION,
        "schema_version": schema_version,
        "kind": kind,
        "payload": payload,
    }
    return _jcs(envelope).encode("utf-8")


def content_hash(kind: str, payload: Any, *, schema_version: Optional[str] = None) -> str:
    """SHA-256 lowercase hex of the canonical bytes."""
    return hashlib.sha256(canonical_bytes(kind, payload, schema_version=schema_version)).hexdigest()


def hash_bytes(canonical_snapshot: str) -> str:
    """Hash an already-canonical snapshot STRING — what the SnapshotStore stores and
    keys by. Kept separate so the store can recompute a hash without knowing kind."""
    return hashlib.sha256(canonical_snapshot.encode("utf-8")).hexdigest()


# --- schema-shaped kinds (reference_plan / effective_plan) -------------------

def reference_plan_snapshot(schema_dict: JSONTree, *, schema_version: str) -> str:
    payload = _normalize_schema_payload(schema_dict)
    return canonical_bytes("reference_plan", payload, schema_version=schema_version).decode("utf-8")


def effective_plan_snapshot(schema_dict: JSONTree, *, schema_version: str) -> str:
    payload = _normalize_schema_payload(schema_dict)
    return canonical_bytes("effective_plan", payload, schema_version=schema_version).decode("utf-8")


def hash_reference_plan(schema_dict: JSONTree, *, schema_version: str) -> str:
    return hash_bytes(reference_plan_snapshot(schema_dict, schema_version=schema_version))


def hash_effective_plan(schema_dict: JSONTree, *, schema_version: str) -> str:
    return hash_bytes(effective_plan_snapshot(schema_dict, schema_version=schema_version))


# --- GUI-domain kinds (scenario / run_config) --------------------------------
# Canonical payloads per §6: exclude human-assigned ids/names; include everything
# that changes what gets scheduled; sort GUI-authored collections deterministically
# (they have no engine-order semantics, unlike the plan's task array §7).

def run_config_payload(rc: RunConfig) -> dict:
    return {
        "sgs": rc.sgs.value,
        "priority_rule": rc.priority_rule,
        "seed": rc.seed,
        "scheduling_horizon_hours": (
            None if rc.scheduling_horizon_hours is None else q(rc.scheduling_horizon_hours)
        ),
        # map form -> JCS sorts keys, i.e. sorted by task_id
        "mode_selections": {ms.task_id: ms.mode_name for ms in rc.mode_selections},
        "evaluation_weights": (
            None if rc.evaluation_weights is None else {
                "alpha": rc.evaluation_weights.alpha,
                "beta": rc.evaluation_weights.beta,
                "gamma": rc.evaluation_weights.gamma,
                "delta": rc.evaluation_weights.delta,
            }
        ),
    }


def _emergent_task_payload(t) -> dict:
    """Minimal deterministic schema-task shape for an emergent Task. Emergent tasks
    are Phase-5 reserved and not exercised in Phase 1; this keeps the scenario hash
    total and stable if one is ever present."""
    return {
        "task_id": t.task_id,
        "duration": t.duration,
        "description": t.description,
        "location_id": t.location_id,
        "required_resources": [
            {"skill_type": r.skill_type, "crew_count": r.crew_count} for r in t.required_resources
        ],
        "required_equipment": [
            {"equipment_id": e.equipment_id, "quantity_needed": e.quantity_needed}
            for e in t.required_equipment
        ],
    }


def scenario_payload(sc: Scenario) -> dict:
    dur = sc.duration_overrides or ()
    res = sorted(sc.resource_changes or (), key=lambda c: (c.skill_type, c.from_hour))
    eqp = sorted(sc.equipment_changes or (), key=lambda c: (c.equipment_id, c.from_hour))
    hpr = sc.hold_point_release_overrides or ()
    emt = sc.emergent_tasks or ()
    emd = sorted(sc.emergent_dependencies or (),
                 key=lambda d: (d.predecessor_id, d.successor_id, d.lag_hours))
    return {
        "base_plan_hash": sc.base_plan_hash,
        "checkpoint_hour": None if sc.checkpoint_hour is None else q(sc.checkpoint_hour),
        "duration_overrides": {d.task_id: q(d.duration_hours) for d in dur},
        "resource_changes": [
            {"skill_type": c.skill_type, "from_hour": q(c.from_hour), "new_count": c.new_count}
            for c in res
        ],
        "equipment_changes": [
            {"equipment_id": c.equipment_id, "from_hour": q(c.from_hour), "new_quantity": c.new_quantity}
            for c in eqp
        ],
        "hold_point_release_overrides": {o.target_id: q(o.release_hour) for o in hpr},
        "emergent_tasks": [_emergent_task_payload(t) for t in emt],
        "emergent_dependencies": [
            {"predecessor_id": d.predecessor_id, "successor_id": d.successor_id, "lag_hours": q(d.lag_hours)}
            for d in emd
        ],
    }


def scenario_snapshot(sc: Scenario) -> str:
    return canonical_bytes("scenario", scenario_payload(sc)).decode("utf-8")


def run_config_snapshot(rc: RunConfig) -> str:
    return canonical_bytes("run_config", run_config_payload(rc)).decode("utf-8")


def hash_scenario(sc: Scenario) -> str:
    return hash_bytes(scenario_snapshot(sc))


def hash_run_config(rc: RunConfig) -> str:
    return hash_bytes(run_config_snapshot(rc))
