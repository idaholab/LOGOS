# PRISM GUI — Phase 1–2 Architecture

Companion to the capability notes. Scoped to Phase 1 (minimum viable spine + provenance) and Phase 2 (scoped in-GUI editing). Layered shape and deliberate seams — not detailed class diagrams yet.

## Scope & settled assumptions
- **Framework:** Streamlit.
- **Backend integration:** in-process (an infrastructure adapter calls the PRISM package directly).
- **Single user, single session.** No concurrency handling.
- **No persistence in Phase 1–2.** Run results and scenarios live in session only; closing the app loses them. Types are designed as serializable neutral DTOs so persistence can land later without redesign.
- Provenance infrastructure is Phase 1, not deferred.

## Design goals
- The domain model is the spine; it depends on **nothing** outside itself — not Streamlit, not PRISM.
- Seams that later phases attach to (execution, persistence) are defined now as real contracts, even while the implementations behind them are simple and synchronous.
- Never let a what-if silently mutate the reference plan; never let one run contaminate another.
- Results carry enough provenance to be reproduced and to have their lineage checked; results are never silently wrong.

---

## Layering (ports & adapters)

The domain model sits at the center and depends on nothing above or beside it. The application layer depends on the domain and on **interfaces (ports)**. Infrastructure adapters implement those ports and are the only code that touches PRISM.

```
UI (Streamlit)
  → Application services
      → Domain model & policies          (pure Python; no Streamlit, no PRISM)
      → Ports (execution, repository,
                snapshot store, validation)  (interfaces only)
Infrastructure adapters  → implement Ports
                         → call existing PRISM package (in-process)
```

Key correction from the earlier draft: **the PRISM adapter is infrastructure, not domain.** Putting it in the domain would make the "independent, testable, portable" model depend on the existing PRISM implementation. The domain never imports PRISM; it defines the types and policies, and an adapter translates them into a PRISM runtime and translates PRISM output back into neutral result objects.

---

## Domain layer — pure model & policies

Contains only:
- The four objects: **ReferencePlan**, **Scenario**, **RunConfig**, **RunResult**.
- The **`Issue` type, the issue-code catalogue, and the validation *contract*** (what blocks vs. warns). Schema and referential-integrity *checking* is **not** reimplemented here — `src/CPM/validate_outage_data.py` already does it as a pure module, so the domain depends on a **validation port** and an adapter wraps that validator and maps its output to `Issue`s (see Ports, and model spec §8b). Reimplementing the rules in the domain would create two rule sets that silently drift.
- **Scenario-application** rule: `materialize(reference_plan, scenario) → effective_plan` — its schema/referential validation reuses the same validator on the serialized effective plan; only combination-specific findings (`MATERIALIZE_CONFLICT`, `PROV_HASH_MISMATCH`) are the domain's own.
- **Schedule-disposition** policy.

No Streamlit, no PRISM, no plotting objects, no live DataFrames.

### The four objects
- **ReferencePlan (baseline)** — the definition of the plan (tasks, dependencies, pools, calendars, constraints).
- **Scenario** — a delta of temporary overrides layered on a baseline (duration overrides, resource-availability changes when tagged as scenario, emergent tasks, hold-point release assumptions, equipment OOS). Holds no schedule.
- **RunConfig** — SGS variant, priority rule, **execution modes**, seed, (later) optimization settings. Execution modes live here and are applied by the adapter when constructing the runtime — never applied to a baseline working copy.
- **RunResult** — schedule, diagnostics, disposition, and provenance. Neutral DTOs only (records, lists, scalars, graph edge lists). **No live PRISM objects, plotting figures, or mutable DataFrames.** The UI builds charts and tables from these. Immutable once created.

### Baseline vs. scenario (enforced in the model)
- Baseline edits change the *definition* → open a **PlanDraft** and apply validated patch operations to its raw working tree; committing rehydrates + validates and mints a **new immutable ReferencePlan** (model spec §2b). The committed ReferencePlan is never mutated in place.
- Scenario overrides represent a *condition to test* → recorded in a Scenario, never touching the baseline.
- Resource-availability change is the canonical ambiguous case: the model accepts it either way, but the caller must tag intent. The domain does not guess.

### Scenario application & combined validation
- `materialize(reference_plan, scenario) → effective_plan` is an explicit domain operation.
- The **effective plan** must then pass schema, referential-integrity, and run-config validation. A baseline and a scenario can each be valid alone yet invalid combined — e.g. an emergent task referencing a nonexistent resource, or an execution mode referring to a task the scenario replaced. This validation has no home unless materialize is explicit.

### Structured issues (not PRISM strings)
Validation and diagnostics return a structured issue object, defined early:
```
code, severity, category, entity_type, entity_id, message, suggested_action
```
Supports readable validation panels, filtering, affected-entity highlighting, and later LLM interpretation. GUI-facing code never parses raw PRISM strings. Schema and referential issues are produced by **mapping `validate_outage_data.py`'s output** to this shape — not by a reimplemented checker (the durable form is a small structured-emit refactor of that validator so the mapping is not string-parsing; see model spec §8b).

### Schedule disposition
- The domain computes independent indicators: input validity, completeness, hard-constraint feasibility, unscheduled tasks, window/policy violations, audit result.
- The application rolls them into one **disposition** (Ready / Ready with warnings / Blocked). Not a state machine — the indicators co-occur; disposition is a summary over them.

---

## Ports (interfaces the application depends on)

### Execution port — job-oriented from day one
A synchronous `execute() → RunResult` cannot later grow progress, cancellation, or survival across Streamlit reruns without changing UI control flow. So the contract is job-shaped now, even though Phase 1 completes jobs immediately:

```
submit(RunRequest) → RunId
get_status(RunId)  → queued | running | completed | failed | cancelled
get_result(RunId)  → RunResult
cancel(RunId)
```

- **RunRequest** — a serializable package that **references its inputs by content hash** (`effective_plan_hash`, `run_config_hash`, plus `provenance_inputs`), not by embedding snapshot bytes. The executor resolves the hashes to bytes through the **snapshot store port** (below). This keeps the request small and makes the snapshot store the single home for canonical bytes. (Model spec §7.)
- **Phase 1 in-process executor** implements this contract but completes synchronously inside `submit`, so `get_status` returns `completed` immediately and the UX stays synchronous.
- **Later background executor** implements the *same* contract with real queuing/progress/cancel. Application workflow does not change — only the executor implementation does.

### Repository port (defined, not implemented in Phase 1–2)
Interface for storing/retrieving baselines, scenarios, run configs, and run results. No implementation now (no persistence), but naming the port keeps the seam real and the RunResult DTOs honest about being serializable.

### Snapshot store port (content-addressed blobs — Phase 1)
Deliberately separate from the repository port: `put(canonical) → hash` (idempotent, dedup), `get(hash) → canonical` (raises `SnapshotNotFoundError` → `SNAPSHOT_MISSING` Issue), `contains(hash)`. This is where canonical snapshots live and what a `RunRequest` resolves its hashes through; **every hash in a Provenance must resolve here.** Phase 1 backs it with an in-memory dict; a persistent store replaces it when persistence lands. (Model spec §8a.)

### Validation port — wraps the existing validator (Phase 1)
`validate_plan(plan_data) → [Issue]`. The Phase-1 adapter behind it is the **only** code that imports
`src/CPM/validate_outage_data.py`; it runs that validator (Draft7 schema + referential integrity, already
pure) and maps `(is_valid, errors, warnings)` to structured `Issue`s. The domain and application depend on the
port, never on the concrete module — so the validator can be refactored or replaced without touching them, and
the domain still imports nothing outside itself. Embedding caveats the adapter must absorb (the module was
written as a CLI): it calls `sys.exit()` on a missing `jsonschema` / falsy schema path, and its not-found
fallback references an undefined `DEFAULT_SCHEMA` — so the adapter always passes the real
`src/CPM/outage_schema.json` and treats `jsonschema` as a hard dependency. (Contract and message→code mapping
in model spec §8b.)

---

## Application layer

### State model (across Streamlit reruns)
- Domain objects are held **directly in session state** (single-user, in-process).
- The UI **never** touches `st.session_state` raw. All access goes through a thin **accessor layer** — the one place session keys are known, so moving to an external store later is a one-module change.
- Session holds: committed ReferencePlan; the in-progress **PlanDraft** (during edit — a mutable raw tree + pending patches, not a mutable ReferencePlan; model spec §2b); current Scenario; RunResults produced this session (each with provenance); the selected result id.

### Editing contract (PlanDraft model)
- Editing operates on a **PlanDraft** — a mutable raw working tree plus a list of pending validated patch operations — **not** on the committed ReferencePlan (which is immutable) and **not** on a typed working copy. `open_draft → apply_patch* → commit_draft | discard_draft` (model spec §2b).
- On commit: patch the raw tree, rehydrate the typed view, validate (schema + referential integrity); only if it passes does `commit_draft` mint a **new immutable ReferencePlan** that replaces the committed baseline. Cancel = `discard_draft` (the committed baseline is untouched throughout).
- A committed baseline edit updates the **lineage** of existing results (see below) — it does not delete them. Freshness is *derived* from hash comparison at display time, never stored (model spec §9).

### The fresh-runtime invariant (explicit)
**Every run executes against a newly constructed PRISM runtime, built by the adapter from the materialized effective-plan snapshot.**
- PRISM mutates activities, pools, queues, assignments, and timing fields in place.
- A runtime object is therefore **never** stored inside ReferencePlan, Scenario, or RunResult, and never reused across runs.
- Without this, one run contaminates another — most damagingly during priority-rule sweeps and scenario comparisons, where it would be silent and wrong.

### Provenance, snapshots & lineage
Each RunResult retains:
- **canonical serialized reference-plan snapshot** (reproducible input)
- **scenario delta**
- **materialized effective-plan snapshot** (what was actually scheduled, after applying the scenario)
- **run configuration**
- **content hashes** for those inputs
- **schema version**, and the **version split** — `canonicalization_version` (which hashing algorithm minted the hashes), `app_version` (the GUI), and `prism_version` (the engine); a single "software version" cannot answer "can this hash still be trusted" (model spec §6)
- **run ID** and **timestamp**

Rules:
- Hashes are computed from **canonical serialization** — stable key ordering, normalized numeric representation, explicit schema version — or they are not stable enough to trust.
- **Snapshot and hash are both required, for different jobs:** the snapshot gives reproducibility; the hash gives cheap identity and lineage checks.
- **Lineage, not just "stale":** a result's inputs are matched by hash/revision against the current state. "Stale" is the user-facing word for the specific case where a result is *displayed against a baseline or scenario that has since changed*. Selecting a different priority rule does **not** make an old result stale — it simply is not the currently selected configuration. Track lineage precisely by hash; surface "stale" only when it means the displayed context moved.
- Results with changed lineage stay **viewable and reproducible**; nothing is deleted.

---

## Serialization

Two distinct needs, split by phase:

### Canonical snapshot serialization — Phase 1 (for provenance)
- Deterministic, canonical form (stable ordering, normalized numbers, schema version) used for snapshots and hashing.
- Required for provenance/reproducibility from the very first run.
- **Precise spec:** `prism-gui-canonicalization.md` — RFC 8785 (JCS) + SHA-256, a `{canon_version, schema_version, kind, payload}` envelope, 1 ms time normalization, preserved array order. The canonical renderer (hash-oriented) is distinct from the Phase-2 lossless-export renderer (order-preserving); same authoritative `_raw`, two renderers.

### Lossless round-trip export — Phase 2 (with editing)
- **Lossless means semantic preservation of unknown fields, not byte-for-byte whitespace/ordering.**
- Avoid two drifting sources of truth. **The raw parsed JSON tree is authoritative;** edits are applied to it as validated patch operations through stable paths. The typed model is regenerated/synchronized from the raw tree after each committed transaction.
- Export is schema-valid and round-trippable, preserving fields the GUI does not model.
- This belongs with Phase 2 because full round-trip is only exercised once editing exists; Phase 1 needs only canonical snapshot serialization.

---

## What Phase 1 vs. Phase 2 actually builds

**Phase 1 (spine + provenance):**
- Domain: ReferencePlan, RunConfig, RunResult (neutral DTOs); the `Issue` type + code catalogue + disposition policy; canonical snapshot serialization. (Validation *rules* are not here — see the validation adapter below.)
- Ports: execution port (job contract) with a synchronous in-process executor; **validation port with an adapter over `validate_outage_data.py`** (load-time schema + referential validation → `Issue`s); **snapshot store port with an in-memory content-addressed store** (put/get/contains; model spec §8a); repository port defined (unimplemented).
- Infrastructure: PRISM adapter that builds a **fresh runtime** from an effective-plan snapshot and returns neutral DTOs (full contract + PRISM API mapping in `prism-gui-prism-adapter.md`); validation adapter wrapping the existing pure validator (isolating the GUI from its `sys.exit`/`DEFAULT_SCHEMA` CLI-isms).
- Application: accessor layer over session state; snapshot + hashing + lineage/provenance.
- UI: load / choose SGS+rule / run / Gantt + table + CSV / disposition summary; sample project / guided load.

**Phase 2 (scoped editing):**
- Domain: Scenario type; `materialize()` + combined-validation; `PlanDraft` + `PatchOp` + commit-time validation (schema + referential integrity on the rehydrated view); baseline-vs-scenario tagging.
- Application: `open_draft`/`apply_patch`/`commit_draft`/`discard_draft` orchestration; baseline-edit → derived lineage/stale flagging.
- Infrastructure/serialization: lossless round-trip export (raw-tree-authoritative + validated patches).
- UI: editors for tasks & durations, dependencies & lags, basic resources & availability — the default `renewable` `resource_type` first. (Consumable-type pools carrying a per-worker dose budget, and other advanced pools, deferred.)

---

## Testing — highest-risk invariants
These are the invariants whose violation would be silent and dangerous; cover them early.
- Running a scenario does not change the reference plan.
- Two runs do not influence each other (fresh-runtime invariant).
- Cancelled edits do not change the committed baseline.
- Invalid edits cannot commit.
- Unknown JSON fields survive edit → export → reload.
- A stored run reproduces its schedule from its snapshot and seed.
- Baseline changes mark relevant displayed results as stale (lineage) without deleting them.
- The synchronous and (future) background executors satisfy the same execution contract.

---

## Deliberately deferred (seams left in place)
- Out-of-process execution / background jobs / cancel — execution port already job-shaped; implementation later.
- Persistence of scenarios, run configs, results — repository port defined; store not built; DTOs serializable.
- Baseline versioning / cross-revision comparison — not in Phase 1–2.
- Advanced constraint-pool editors, domain-profile mechanism, LLM layer — later phases.

## Open architecture questions
- Granularity of the accessor layer (one module vs. per-object accessors).
- ~~Exact canonical-serialization spec (numeric normalization rules, how schema version participates in the hash).~~ **Resolved (2026-09-09):** `prism-gui-canonicalization.md` (RFC 8785 JCS + SHA-256, envelope, 1 ms normalization).
- ~~Whether the materialized effective-plan snapshot is stored in full or reconstructed on demand.~~ **Resolved:** stored in full, content-addressed dedup (model spec §6); the canonicalization spec supplies the hash the snapshots are keyed by.
