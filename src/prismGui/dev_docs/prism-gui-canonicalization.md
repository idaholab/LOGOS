# PRISM GUI — Canonical Serialization & Hashing Spec (Phase 1)

The precise, implementable rules for turning a plan / scenario / config into deterministic bytes and
a stable content hash. Resolves the "canonical-serialization spec" and "sub-hour precision" items
that both the model spec (§Open questions) and architecture (§Open architecture questions) left open.
Companion to the model spec (§6 Provenance) and the PRISM adapter spec (§1 time model).

## Why this must be nailed before Phase-1 code

Three Phase-1 deliverables are built directly on top of "the same plan always produces the same
bytes": the **content-addressed snapshot store** (hash = the key), **provenance** (hash = the
identity of each input), and **stale / lineage detection** (compare hashes). If the bytes aren't
deterministic, none of these are trustworthy — a re-load of the same file, or a no-op edit, would
mint a new identity and spuriously flag every result stale.

## Decisions locked (this doc's contract)

- **Scheme:** RFC 8785 JSON Canonicalization Scheme (JCS).
- **Digest:** SHA-256, lowercase hex. The hex digest is the snapshot-store key.
- **Time precision floor:** 1 ms (the engine's `_EVENT_EPSILON` / `_PREC_TOL`). Nothing the scheduler
  can distinguish is ever lost; anything finer is float noise and is quantized away.
- **What we hash:** the authoritative **schema-shaped dict** — not the normalized hour-offset form.

---

## 1. What gets canonicalized

Four kinds are hashed independently (they are the four provenance inputs, model-spec §6):

| `kind` | Source object | Shape canonicalized |
|---|---|---|
| `reference_plan` | ReferencePlan | schema-shaped dict (`outage_schema.json` structure, ISO timestamps) — i.e. `_raw` re-serialized deterministically |
| `effective_plan` | EffectivePlan (post-materialize) | same schema-shaped structure, overrides + emergent tasks folded in |
| `scenario` | Scenario | GUI-domain DTO dict (§6) |
| `run_config` | RunConfig | GUI-domain DTO dict (§6) |

**The schema-shaped dict is the one artifact for three jobs** (see adapter spec §1): it is what we
hash here, what the ValidationPort validates, and what the PRISM adapter feeds to
`OutageData.from_dict`. There is **no** separately-hashed "normalized hour-offset" form — the
hour-offset values the DTOs and adapter output use are a *deterministic derivation* of this hashed
form (via the 1 ms quantizer in §4), so reproducing the run input from the hash is exact.

`reference_plan` / `effective_plan` are canonicalized from the authoritative raw tree (`_raw`),
re-serialized through a **canonical renderer** (§5) — distinct from the Phase-2 lossless-export
renderer, which preserves original ordering. Same data, two renderers, different jobs; single source
of truth preserved.

---

## 2. The hash envelope

Do not hash the payload dict directly. Hash a small envelope so that (a) an algorithm change can
never silently collide with old hashes, and (b) a config can never hash-collide with a plan:

```
canonical_bytes = JCS({
    "canon_version":  "1",              # bump when THIS spec changes
    "schema_version": <plan meta schema_version, or null for scenario/run_config>,
    "kind":           "reference_plan" | "effective_plan" | "scenario" | "run_config",
    "payload":        <canonical payload dict, §5/§6>
})
digest = sha256(canonical_bytes).hexdigest()   # lowercase hex → store key
```

- `schema_version` participates by being **inside the hashed envelope** — a schema-version bump
  therefore changes the hash even if every field value is unchanged (correct: it is a different
  contract). For `scenario` / `run_config`, which are not outage-schema objects, it is `null`.
- `canon_version` is this document's version. Provenance records it (the skeletons' Provenance
  already carries a `canonicalization_version`), so a stored hash always says which algorithm made it.

---

## 3. Scheme: RFC 8785 (JCS)

JCS gives: UTF-8 output; object **keys sorted lexicographically by UTF-16 code unit**; JCS string
escaping; JSON numbers formatted per ECMAScript `Number.prototype.toString` (shortest round-trip);
**no insignificant whitespace**; **array order preserved** (§7).

**Implementation rule:** use a vetted JCS library, or a small conformant implementation with **RFC
8785's own published test vectors as unit tests**. Do **not** approximate JCS with
`json.dumps(sort_keys=True, separators=(",", ":"))` — that diverges from JCS on number formatting and
on non-ASCII key ordering/escaping, so its bytes are not JCS-conformant and lose the cross-tool
reproducibility that motivated choosing JCS. The point of a published scheme is that a provenance
bundle re-hashed by any conformant tool yields the same digest — an approximation forfeits that.

---

## 4. Number & time handling (the part that actually bites)

JCS formats numbers *as given* — it does not remove float noise. So a **pre-canonicalization
normalization pass** runs over the payload before JCS:

**Timestamps (ISO strings in the schema-shaped dict):** normalize every timestamp to a single
canonical form — **UTC, millisecond precision**, `YYYY-MM-DDTHH:MM:SS.sssZ`. This collapses
`12:00:00`, `12:00:00.000`, and any different-offset expression of the same instant to identical
bytes. (The project-local timezone stays semantically available in the typed view's anchor; it is not
needed for the hash, where only the instant matters.)

**Derived time values (hour-offsets), where they appear** — e.g. scenario `from_hour`,
`checkpoint_hour`, `release_hour`, and any hour-offset a DTO serializes: quantize to the 1 ms grid:
```
q(hours) = round(hours * 3_600_000) / 3_600_000     # 1 ms grid; 0.015625 h → 56250 ms exactly
```
This preserves sub-minute durations already present in test data (0.015625 h = 56.25 s) exactly, and
kills conversion noise below 1 ms. The **same `q`** is used by the adapter's datetime→hour output
conversion, so DTO hours and any hashed hour-offset agree to the bit.

**Durations / lags authored in hours (schema):** these are user-authored, not derived, so they carry
no conversion noise — pass them to JCS unchanged (JCS's shortest-round-trip formatting is
deterministic). If a value ever arrives with float noise from an edit, apply `q` defensively.

**Counts / quantities / crew_count:** integers — exact, never quantized.

**User-entered real magnitudes** (`dose_budget_per_worker_mrem`, `dose_rate`, evaluation weights
α/β/γ/δ): stored as authored; JCS shortest-round-trip formatting is deterministic for them. No
domain-specific quantization (they are not time and have no engine-resolution analogue).

---

## 5. Canonical renderer for schema-shaped kinds (`reference_plan`, `effective_plan`)

The renderer walks `_raw` (authoritative) and produces the payload dict:
1. Normalize all timestamps per §4.
2. Apply `q` to any derived hour-offset fields (none in a pure baseline; present in an effective plan
   that folded in hour-based overrides — those are converted back to the schema's ISO form during
   materialization, so by the time they are here they are already ISO and handled by step 1).
3. Leave object-key ordering to JCS; **leave array order as-is** (§7).
4. Emit through JCS (§3).

Unknown / passthrough fields in `_raw` are included verbatim (after timestamp normalization) — the
hash covers the whole plan, not just the typed subset, so two plans that differ only in a field the
GUI doesn't model still hash differently. This is required: reproducibility must not depend on the
GUI understanding every field.

---

## 6. Canonical DTO shapes for non-schema kinds

`scenario` and `run_config` are GUI-domain objects with no `outage_schema.json` counterpart, so this
spec defines their canonical payload explicitly (stable field set + the §4 number rules):

**`run_config` payload:** `{sgs, priority_rule, seed, scheduling_horizon_hours|null,
mode_selections|{} (sorted by task_id), evaluation_weights|null}`. `run_config_id` is **excluded** —
identity is by content, not by a mutable id (two configs with the same knobs must share a hash).

**`scenario` payload:** `{base_plan_hash, checkpoint_hour|null (q-quantized),
duration_overrides|{} (sorted by task_id, q), resource_changes|[] (sorted by (skill_type, from_hour)),
equipment_changes|[] (sorted by (equipment_id, from_hour)), hold_point_release_overrides|{} (sorted),
emergent_tasks|[] (each schema-task-shaped), emergent_dependencies|[] (sorted)}`. `scenario_id` and
`name` are **excluded** from the hash (metadata, not identity); `base_plan_hash` **is** included (a
delta against a different baseline is a different thing).

Rule of thumb: **exclude human-assigned ids and names; include everything that changes what gets
scheduled.** For these two kinds, ordering maps/lists deterministically (as annotated) so a
cosmetic reordering of, say, `resource_changes` does not change the hash — safe here because these are
GUI-authored collections with no engine-order semantics (unlike the plan's task array, §7).

---

## 7. Array ordering — preserve (resolved)

**Decision: the canonical renderer preserves array order for the schema-shaped kinds** (JCS default).
It does not semantically sort the plan's `tasks`, `resources`, etc.

Rationale: the engine's candidate iteration and priority-rule tie-breaking may depend on task **input
order** (the parallel scheduler iterates candidate lists whose order derives from the input; several
priority rules break ties by iteration order, and this could not be proven order-insensitive from the
current code). If input order can change the produced schedule, then order is **semantic** and the
hash must capture it — otherwise two plans that schedule differently would share an identity and a
reproduced run could diverge from its record. Preserving order makes the hash faithful to exactly what
was (or would be) scheduled.

Cost accepted: a cosmetic reordering of the task array (e.g. via the Phase-2 editor) produces a new
hash and flags dependent results stale, even if the schedule is unchanged. This is the safe direction
to err. A future optimization — canonically sorting arrays once the engine is *proven* insensitive to
input order — can be added under a new `canon_version` without breaking stored hashes.

(The GUI-authored collections in `scenario`/`run_config` (§6) are sorted deterministically instead,
because they have no engine-order semantics — the distinction is "does PRISM's output depend on this
order." For the plan arrays, we cannot rule that out; for the scenario/config collections, they never
reach PRISM as ordered sequences.)

---

## 8. Hashing API (domain-owned, pure)

Pure functions in the domain (no PRISM, no Streamlit, no I/O):
```
canonical_bytes(kind, payload, *, schema_version=None) -> bytes     # envelope + JCS
content_hash(kind, payload, *, schema_version=None)    -> str       # sha256 hex
hash_reference_plan(plan)  -> str
hash_effective_plan(plan)  -> str
hash_scenario(scenario)    -> str
hash_run_config(config)    -> str
```
The snapshot store keys canonical bytes by their hash; `Provenance` (model-spec §6) stores the four
hashes; lineage/stale detection compares them. `PROV_HASH_MISMATCH` (model-spec §3) fires when a
Scenario's stored `base_plan_hash` ≠ `hash_reference_plan(current_baseline)`.

---

## 9. Conformance & tests

- **RFC 8785 test vectors** as unit tests on the JCS layer (proves conformance, catches a bad/swapped
  implementation).
- **Idempotence:** `hash(plan) == hash(reload(export(plan)))` — a round-trip through export + reload
  does not change identity (this is the load→hash stability the snapshot store depends on).
- **Noise immunity:** two plans differing only by sub-1 ms timestamp jitter, or by `12:00:00` vs
  `12:00:00.000`, or by float representation of the same duration, hash **equal**.
- **Order sensitivity (intended):** two plans differing only by task-array order hash **unequal**
  (§7) — asserted so the decision is a tested contract, not an accident.
- **Kind isolation:** a `run_config` and a `reference_plan` that happen to share payload bytes hash
  **unequal** (the envelope `kind` differs).
- **Id/name exclusion:** two run configs differing only in `run_config_id`, or two scenarios differing
  only in `name`, hash **equal** (§6).

---

## 10. Resolves (previously open)

- Model spec §Open questions — "Canonical-serialization spec (numeric normalization, schema_version
  participation, key ordering)": **JCS + §2 envelope + §4 numbers + §7 order.**
- Model spec §Open questions — "Sub-hour precision & rounding at the ISO↔hour-offset boundary": **1 ms
  grid via `q` (§4); ISO normalized to UTC ms.**
- Architecture §Open architecture questions — "Exact canonical-serialization spec": this document.
- The storage-vs-recompute question (architecture) is settled elsewhere (model-spec §6: full snapshot,
  content-addressed dedup); this doc supplies the hash those snapshots are keyed by.
