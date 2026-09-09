# PRISM GUI — Model & Interface Spec (Phase 1–2)

Interface-level definitions: the domain types, their fields, and the port contracts, expressed language-agnostically. No implementation. Companion to the architecture document.

## Framing decisions carried in
- **Thin typed view over an authoritative raw tree.** The raw parsed JSON tree is authoritative for serialization; typed objects are a synchronized view. **Refined thin-view rule:** type everything Phase 1–2 reads or edits, **plus every field required to construct and validate the PRISM runtime**. All remaining fields survive round-trip via `_raw`. (Phase 1 *runs* schedules, not just displays them — so any runtime-required field must be typed or explicitly passed through, never left implicitly in `_raw`.)
- **`_raw`/typed synchronization invariant.** The typed view is never committed directly. An edit produces domain patch operations → applied to a copy of `_raw` → typed view rehydrated from the patched raw tree → validated → only that reconstructed ReferencePlan is committed. Flow: `_raw → parse typed view → proposed patches → patched _raw → rehydrate typed view → validate → commit`. This keeps a single source of truth. (`_raw` is used here as the *conceptual* name for the authoritative raw tree; §2b gives it concrete types — the mutable `raw_working_tree` on a `PlanDraft` during editing, and the immutable `raw_snapshot` on a committed ReferencePlan.)
- **Dependencies are normalized at the plan level** (top-level edge list), not owned by Task. Serialization maps them back to the schema's per-task `successors` shape.
- **Time:** domain and DTOs use normalized float hour-offsets anchored to a timezone-aware project start; `_raw` keeps the schema's ISO timestamps; the serialization mapper converts between them for the domain/DTO view. (The PRISM adapter, by contrast, feeds PRISM the schema-shaped ISO snapshot *directly* — `OutageData.from_dict` is datetime-native — and converts datetimes→hour-offsets only on its *output* side; see `prism-gui-prism-adapter.md` §1.) Availability intervals are half-open `[start, end)`; sub-hour precision and rounding follow the **1 ms grid** defined in `prism-gui-canonicalization.md` §4.
- Domain types are pure — no PRISM, no Streamlit, no plotting/DataFrame objects.
- `RunResult` carries **neutral DTOs** only.
- All validation/diagnostic output is a **structured issue**, never a raw string.

### Notation
Types are described as field lists. `?` marks optional/nullable. `[X]` is a list of X. `{K: V}` is a map. "ref → Foo.id" means the field holds an identifier referring to a Foo. These are contracts, not a language binding.

---

## 1. Issue (structured)
Returned by validation, referential-integrity checks, disposition, and diagnostics. Defined first because everything else produces it.

**Issue**
- `code` — stable machine identifier (e.g. `REF_RESOURCE_MISSING`). Enables filtering, docs, LLM mapping.
- `severity` — one of: `error` | `warning` | `info`.
- `category` — one of: `schema` | `referential_integrity` | `feasibility` | `time_window` | `dose` | `system_state` | `execution` | `provenance` (extensible).
- `entity_type?` — what the issue is about: `task` | `dependency` | `resource` | `equipment` | `location` | `consumable` | `system` | `hold_point` | `run` | ... 
- `entity_id?` — id of the affected entity, for highlighting.
- `field_path?` — JSON Pointer-like path to the exact offending field, so the GUI can focus the specific invalid input, not just the entity.
- `message` — human-readable text (GUI-neutral, not a PRISM dump).
- `suggested_action?` — optional remediation hint.

Notes:
- `error` blocks (commit or feasibility); `warning` does not block but affects disposition; `info` is advisory.
- The same shape serves schema errors, referential-integrity failures, window violations, and later LLM-consumable diagnostics.

### Phase 1–2 core code catalogue
Enumerated now (codes only earn their keep if tests and UI can depend on them); extensible later.
- `SCHEMA_TYPE_ERROR`, `SCHEMA_RANGE_ERROR` — schema/type/range violations.
- `REF_MISSING` — reference to a nonexistent entity (resource/equipment/location/consumable/system/task).
- `DUP_ID` — duplicate identifier.
- `DUP_DEPENDENCY` — duplicate dependency edge.
- `DEP_CYCLE` — dependency cycle. Includes a self-referencing successor / a hold point blocking itself (a degenerate 1-cycle); the existing validator reports these the same way, so there is **no** separate `DEP_SELF_LOOP` code.
- `INVALID_MODE` — execution mode invalid or referring to a missing/replaced task.
- `INVALID_TIME_WINDOW` — malformed time window (`w_min`/`w_max`).
- `INVALID_AVAILABILITY_INTERVAL` — malformed or overlapping availability interval.
- `HOLD_POINT_MISUSE` — non-hold-point task carrying `hold_point_type`/`blocks_tasks` (from the validator seam; see §8b).
- `INSUFFICIENT_RESOURCE` — coarse resource-sufficiency shortfall (warning; from the validator seam, §8b).
- `MATERIALIZE_CONFLICT` — baseline+scenario combination invalid.
- `EMERGENT_ID_COLLISION` — an emergent task/dependency id clashes with a baseline id (see Open questions).
- `UNSCHEDULED_TASK` — task not placed in the schedule.
- `DEP_VIOLATION` — precedence/lag violated in the produced schedule.
- `EXECUTION_FAILURE` — run failed in the executor/adapter.
- `SNAPSHOT_MISSING` — a provenance hash does not resolve in the snapshot store (§8a).
- `PROV_HASH_MISMATCH` — scenario applied against a baseline whose hash differs from `base_plan_hash`.

**Schedule-audit codes (`AUDIT_*`).** The PRISM adapter maps the engine's schedule audit
(`validate_schedule()` → 16 `Violation.type` values) to Issues by a mechanical convention: **reuse an
existing code** where the meaning is identical to another producer (`completeness → UNSCHEDULED_TASK`,
`precedence → DEP_VIOLATION`, `mode → INVALID_MODE`), **otherwise `code = "AUDIT_" + type.upper()`**
(e.g. `AUDIT_CREW`, `AUDIT_DOSE`, `AUDIT_TIME_WINDOW`, `AUDIT_SYSTEM_STATE`). The prefix rule — rather
than 13 enumerated names — keeps one source of truth with the validator's own `type` strings; the full
16-row table is in `prism-gui-prism-adapter.md` §6. Phase-1 disposition needs only "any error-severity
audit Issue → `audit_passed = false`"; the fine-grained codes exist for UI filtering and the later LLM
layer.

**Source of schema & referential codes (do not reimplement).** `SCHEMA_*`, `REF_MISSING`, `DUP_ID`,
`DEP_CYCLE`, and `INVALID_AVAILABILITY_INTERVAL` are **not** produced by a new checker written in the
domain. They are produced by **mapping the output of the existing `src/CPM/validate_outage_data.py`**
behind the validation seam (see §8b). That module is already a pure Draft7-schema + referential-integrity
validator; the plan wraps it and translates its messages into `Issue`s, so there is one source of truth
for the validation rules. Two codes have **no** producer in the existing validator and are the domain's
own: `MATERIALIZE_CONFLICT` and `PROV_HASH_MISMATCH` (both are properties of the baseline+scenario
*combination*, which the validator does not know about). Two validator outputs earn their own codes
(now enumerated above and mapped in §8b): the coarse **resource-sufficiency** warning →
`INSUFFICIENT_RESOURCE`, and the **hold-point misuse** error (non-hold-point task carrying
`hold_point_type`/`blocks_tasks`) → `HOLD_POINT_MISUSE`.

---

## 2. ReferencePlan (baseline) — thin typed view

The definition of the plan. Typed fields cover what Phase 1–2 needs to display, run, and (Phase 2) edit. Everything else remains in the raw tree.

A committed **ReferencePlan is immutable** (see §2b for the editing lifecycle). In the skeletons the typed content — `meta`, `tasks`, `dependencies`, `resources`, `equipment`, `locations`, `consumables`, `systems` — is grouped into a shared **`PlanContent`** value held as `content`, so ReferencePlan and EffectivePlan (§5) share structure without sharing identity; the fields are listed individually below for readability.

**ReferencePlan**
- `plan_id` — identifier for this plan.
- `plan_hash` — content hash of the canonical serialization; the revision identity a Scenario binds to (see §3) and the anchor for lineage/stale detection.
- `meta` — outage/project block: id, start date (timezone-aware anchor for hour-offset conversion), working hours per day, shift start hour, target end date.
- `tasks` — [Task]
- `dependencies` — [Dependency] (normalized top-level edge list; Task does not own successors)
- `resources` — [ResourcePool]
- `equipment` — [EquipmentItem]
- `locations` — [LocationZone]
- `consumables` — [Consumable]
- `systems` — [PlantSystem]
- `raw_snapshot` — the authoritative raw representation, **canonically serialized** and content-addressed in the snapshot store (§8a); the source of truth for round-trip, not shown to the user. Because a committed ReferencePlan is immutable, this is a canonical snapshot, **not** a live dict — the mutable raw tree exists only during editing, on a `PlanDraft` (§2b). (Earlier drafts named this field `_raw` and described it as a live parsed tree on the plan.)

(No top-level `dose_budgets`: the schema carries dose **on the resource** — see `ResourcePool.dose_budget_per_worker_mrem` below.)

(No typed `safety_functions` either — **Option A, by decision (2026-09-09)**. The schema marks its
`safety_functions` array *metadata only* ("the scheduler does not read it directly"; `pert.py` never
references it), so it survives untouched in `_raw`. The mutual-exclusion constraint it documents ("never
two trains OOS") is already enforced — and already modeled above — via the **Option A** encoding: abstract
`systems` / `PlantSystem.valid_states` (one state per train) plus per-task `required_states`, made mutually
exclusive by PRISM's SystemStatePool. A first-class **Option B** `SafetyFunctionPool` with K-of-N
(`max_trains_oos_simultaneously > 1`) semantics is deferred: the schema itself marks it *"not yet
implemented"* in PRISM, so there is nothing for the GUI to expose until the engine does — revisit after a
more detailed investigation.)

### Task (thin)
- `task_id`
- `description?`
- `duration` — hours (float)
- `required_resources` — [{ skill_type ref → ResourcePool.skill_type, crew_count }]
- `required_equipment` — [{ equipment_id ref → EquipmentItem, quantity_needed }]
- `location_id?` ref → LocationZone
- `zone_ids?` — [ref → LocationZone] (multi-zone tasks)
- `consumable_demands?` — [{ material_id ref → Consumable, quantity }]
- `required_states?` — [{ system_id ref → PlantSystem, state }]
- `time_windows?` — [{ w_min, w_max }] (one or more allowed intervals; hour-offsets)
- `hold_point?` — { hold_point_type?, blocks_tasks: [ref → Task] }. **Presence carries hold-point status**: a task is a hold point iff this object is present, so there is no redundant `is_hold_point` flag (the typed view tests `hold_point is not None`). The schema-level misuse — a non-hold-point task carrying `hold_point_type`/`blocks_tasks` — is caught by the validator as `HOLD_POINT_MISUSE` (§8b).
- `execution_modes?` — [ExecutionMode] (available modes; *selection* is in RunConfig)
- `dose_rate?` — mRem/worker/hour
- `mobilization_lead_hours?`
- `wbs_group?`
- `alternative_skill_types?` — [ref → ResourcePool.skill_type], ordered

Note: dependency edges are NOT on Task (see plan-level `dependencies`). Schema fields not needed by Phase 1–2 UI *and* not required to build/validate the runtime are not typed; they ride in `_raw`.

### Dependency (edge)
- `predecessor_id` ref → Task
- `successor_id` ref → Task
- `lag_hours` — default 0

Ordering: preserved only if the input schema assigns edge order meaning; otherwise a deterministic sort (by predecessor, successor, lag) is sufficient. Serialization maps the edge list back to per-task `successors`.

### ExecutionMode
- `mode_name` — e.g. `normal` | `crash` | `reduced_crew`
- `duration`
- `crew` — [{ skill_type, crew_count }]
- `equipment?` — [{ equipment_id, quantity_needed }]
- `dose_rate?`
- `mobilization_lead_hours?`

### ResourcePool (crew skill or consumable budget)
- `skill_type` — identifier (e.g. `MECHANIC`)
- `resource_type` — `renewable` | `consumable`. Default `renewable`. **Runtime-affecting**: a renewable pool's capacity resets each availability period; a consumable pool's capacity is drawn down permanently over the outage (e.g. a radiation-dose budget). Typed per the refined thin-view rule — the adapter builds a different PRISM constraint for each, so this discriminator cannot ride in `_raw`.
- `dose_budget_per_worker_mrem?` — per-worker outage dose budget in mRem. Meaningful **only** when `resource_type = consumable`; ignored otherwise. Total pool budget = this value × peak `count` across `availability_periods`. This is how the schema carries dose (a field on the resource), not a separate top-level list.
- `availability_periods` — [{ start, end, count, reason? }] (half-open `[start, end)`, hour-offsets)

(Dose is modeled as a consumable-type resource carrying `dose_budget_per_worker_mrem`, matching `outage_schema.json`. There is no standalone `DoseBudget` type and no top-level `dose_budgets` list — an earlier draft had both, which would not have round-tripped to the schema and dropped the runtime-affecting `resource_type` discriminator.)

### EquipmentItem
- `equipment_id`
- `description?`
- `availability_periods` — [{ start, end, quantity, reason? }] (half-open, hour-offsets)
- `zone_affinity?` ref → LocationZone

### LocationZone
- `location_id`
- `description?`
- `is_confined_space?`
- `availability_periods` — [{ start, end, max_concurrent_tasks, max_concurrent_workers, reason? }] (half-open, hour-offsets)

### Consumable
- `material_id`
- `initial_stock`
- `restock_deliveries?` — [{ hour, quantity }]

### PlantSystem
- `system_id`
- `valid_states` — [state]

---

## 2b. Editing lifecycle (PlanDraft → commit)

A committed ReferencePlan is immutable; editing never mutates it in place. The **only mutable plan type is a `PlanDraft`**, which holds the working raw tree plus a list of pending patches. Commit rehydrates a *new* immutable ReferencePlan; cancel discards the draft. This gives the `_raw`/typed synchronization invariant (framing, above) concrete types, and **replaces the earlier "mutate a working copy of the ReferencePlan" model.**

**PlanDraft** (mutable — the sole exception to plan immutability)
- `base_plan_id` — the baseline this draft edits.
- `raw_working_tree` — a mutable copy of the authoritative raw tree.
- `pending_patches` — [PatchOp], applied on commit.

**PatchOp** — a JSON-Patch-like operation over the raw tree through a stable path.
- `action` — `add | replace | remove`
- `path` — JSON Pointer-like path into the raw tree
- `value?` — required for add/replace; ignored for remove

**Operations**
- `open_draft(reference_plan) → PlanDraft` — copy the authoritative raw tree into a draft; does not touch the committed plan.
- `apply_patch(draft, patch) → PatchOutcome` — a lightweight per-patch structural check; appends to the pending list; does not commit.
- `commit_draft(draft, schema) → CommitOutcome` — apply the pending patches to a copy of the raw tree, rehydrate the typed view, run full schema + referential-integrity validation (§8b), and — only if clean — produce a **new** immutable ReferencePlan with a recomputed `plan_hash`. The typed view is never committed directly.
- `discard_draft(draft)` — cancel; the committed baseline is unaffected.

`PatchOutcome { ok, issues: [Issue], draft? }` and `CommitOutcome { ok, issues: [Issue], plan? }` carry the structured issues and, on success, the appended draft / the new committed plan.

**Freshness is derived, not stored.** A baseline (or scenario) edit does not push a "dirty" flag onto existing results — a cached flag can itself go stale. The application compares a displayed result's provenance hashes against the current lineage on demand (`assess_freshness`, §9).

---

## 3. Scenario — delta over a baseline

Temporary overrides representing a condition to test. Holds no schedule. Every field is a *change*, not a full re-statement.

**Scenario**
- `scenario_id`
- `base_plan_id` ref → ReferencePlan.plan_id
- `base_plan_hash` — the baseline revision this delta was built against. Materialization rejects (or explicitly warns, `PROV_HASH_MISMATCH`) on mismatch, since `plan_id` can be unchanged after a baseline edit. Same identity supports stale-result detection.
- `name?`
- `checkpoint_hour?` — for replanning contexts (Phase 5; field reserved)
- `duration_overrides?` — [{ task_id, duration_hours }]
- `resource_changes?` — [{ skill_type, from_hour, new_count }]
- `equipment_changes?` — [{ equipment_id, from_hour, new_quantity }]
- `hold_point_release_overrides?` — [{ target_id (task_id or hold_id), release_hour }]
- `emergent_tasks?` — [Task] (new tasks injected)
- `emergent_dependencies?` — [Dependency] (edges to/from emergent tasks — incoming and outgoing, with lags; replaces predecessor-only wiring)

**Change ordering & collision semantics** (resource_changes / equipment_changes):
- Applied in ascending `from_hour`.
- Multiple changes at the same hour for the same entity are invalid (`INVALID_AVAILABILITY_INTERVAL`) — or, if ever allowed, last-write-wins must be explicit.
- A temporary outage is two changes: quantity 0 at outage start, restored quantity at outage end.

Notes:
- **All override collections are tuple-backed record lists, not maps.** `duration_overrides`, `hold_point_release_overrides`, and RunConfig's `mode_selections` were map-shaped (`{task_id: value}`) in an earlier draft; records keep the frozen Scenario deeply immutable and give canonical ordering (the canonicalization spec §6 sorts them for hashing). A field left `None` means "does not touch" — kept distinct from an explicit empty change a future replan may need.
- Phase 2 exercises a subset (duration overrides, resource changes). Emergent tasks / replanning fields are reserved so the type is stable, implemented in Phase 5.
- A scenario alone is not validated for feasibility — only the *materialized effective plan* is (see §5).

---

## 4. RunConfig — how PRISM solves it

**RunConfig**
- `run_config_id`
- `sgs` — one of the SGS variants (`max_use_res_ranked` default, `first`, `max_use_res_shuffled`, `md_knapsack`, `look_ahead`).
- `priority_rule` — one of the 22-rule library keys. Plain-language descriptions, sort direction, and per-rule caveats (for the picker's tooltips and for the implementer) are in **`prism-gui-priority-rules.md`**. An unrecognized key is a hard error in the engine (no silent default), so the GUI only ever offers the enumerated 22.
- `mode_selections?` — [{ task_id, mode_name }] (a tuple-backed record list, not a map; execution-mode choice lives here, applied by the adapter to the fresh runtime — never to a draft).
- `seed`
- `scheduling_horizon_hours?` — schedule-length cap, passed to PRISM's `max_time_hours=` run argument. `None` → engine default. **Renamed from `max_time_hours`** (which was ambiguous with the future optimizer wall-clock limit).
- `optimization?` — reserved for GA/ALNS settings (Phase 6); absent in Phase 1–2. When it lands, the optimizer's wall-clock limit is a **distinct** field from `scheduling_horizon_hours`.
- `evaluation_weights?` — { alpha, beta, gamma, delta } for the post-hoc fitness score. **Does not change the GA/ALNS objective** (documented caveat). Optional; defaults applied if absent.

---

## 5. Materialization & run preparation

Three separate operations. Materialization does **not** receive RunConfig (it can't validate mode selections against config it doesn't have); run-config validation is its own step; `prepare_run()` orchestrates both and builds the RunRequest only when nothing blocks.

### `materialize(reference_plan, scenario) → MaterializeOutcome`
Applies the scenario delta to the baseline and checks the combination.

**MaterializeOutcome**
- `effective_plan?` — an EffectivePlan (same shape as ReferencePlan's typed view, overrides applied, emergent tasks + dependencies folded in). Present only if structurally materializable.
- `issues` — [Issue] (schema + referential-integrity of the *combination*: e.g. emergent task referencing a nonexistent resource, `MATERIALIZE_CONFLICT`; also `PROV_HASH_MISMATCH` on base-hash mismatch).
- `ok` — true iff no `error`-severity issues.

The schema + referential-integrity portion here is **the same validation** the baseline goes through at
load — so materialize does not hand-roll a second checker. It serializes the EffectivePlan back to the
schema-shaped dict and runs it through the validation seam (§8b), then adds only the genuinely
combination-specific findings the validator cannot see (`MATERIALIZE_CONFLICT`, `PROV_HASH_MISMATCH`).
Baseline-alone, scenario-materialized, and load-time validation therefore share one rule set.

### `validate_run_config(effective_plan, run_config) → [Issue]`
Checks the configuration against the plan it will run on:
- Execution-mode selections reference existing tasks (including emergent ones) and valid modes (`INVALID_MODE`).
- SGS / priority-rule keys are recognized and mutually valid.
- Seed / limits well-formed.

### `prepare_run(reference_plan, scenario, run_config, snapshot_store) → PrepareOutcome`
Orchestrates the two above and packages the request. It also **persists every referenced snapshot** — baseline, scenario (if any), effective plan, run config — into the snapshot store (§8a), so every provenance hash in the eventual RunResult is guaranteed to resolve to stored bytes. Builds the RunRequest only when no blocking errors remain.

**PrepareOutcome**
- `run_request?` — a RunRequest (see §7), built only if no blocking errors.
- `issues` — [Issue] (union of materialization + run-config issues).
- `ok` — true iff no `error`-severity issues.

Notes:
- Baseline and scenario may each be valid alone yet invalid combined — materialization catches that; config-vs-plan mismatches are caught by run-config validation (e.g. a mode selected for a task the scenario replaced).
- The EffectivePlan snapshot produced here is what the adapter builds a fresh PRISM runtime from.

---

## 6. RunResult — neutral DTOs + provenance

Immutable once created. Contains no live PRISM/plotting/DataFrame objects. The UI builds all visuals from these DTOs.

**RunResult**
- `run_id`
- `status` — mirrors execution port terminal states: `completed` | `failed` | `cancelled`
- `provenance` — Provenance (see below)
- `disposition?` — Disposition (present if a schedule was produced)
- `schedule?` — ScheduleDTO
- `diagnostics?` — DiagnosticsDTO
- `issues` — [Issue] (feasibility, window, dose, system-state findings)

### Provenance
- `baseline_snapshot_hash` — hash of the canonical serialized ReferencePlan (content-addressed; the snapshot bytes are stored once, keyed by hash).
- `scenario_delta_hash?` — hash of the canonical serialized Scenario.
- `effective_plan_hash` — hash of the canonical serialized EffectivePlan.
- `run_config_hash` — hash of the canonical serialized RunConfig.
- `schema_version`
- `canonicalization_version` — which canonicalization algorithm produced the hashes; distinct from `schema_version` (see `prism-gui-canonicalization.md` §2, `canon_version`).
- `app_version` — the GUI/application version.
- `prism_version` — the PRISM engine version.
- `run_id`
- `timestamp`

(**Version split:** an earlier draft carried a single `software_version`. The skeletons split it into `app_version` + `prism_version` + `canonicalization_version`, so the GUI, the engine, and the hashing scheme can move independently and provenance records exactly which of each produced a result.)

Storage model: each result **conceptually owns the full effective-plan snapshot** (reproducibility must not depend on `materialize()`'s historical behavior — reconstructing from baseline+delta would break if that logic ever changes). Physically, canonical snapshots are **content-addressed and deduplicated**: stored once by hash, referenced by hash from provenance. A self-contained provenance bundle (all referenced snapshots inlined) can be exported when needed.

### Disposition
- `overall` — `ready` | `ready_with_warnings` | `blocked`
- `indicators` — independent tri-state flags (`true | false | not_evaluated`): input_valid, schedule_complete, hard_feasible, has_unscheduled_tasks, has_window_violations, audit_passed
- Computed by a pure domain function `compute_disposition(schedule_summary, issues) → Disposition` with explicit, tested precedence rules. The application layer *orchestrates* the call; it does not decide what "ready with warnings" means.
- Tri-state matters: `audit_passed = false` is not the same as "audit did not run" (`not_evaluated`). `overall` is a summary over indicators, not a state machine.

**`overall` precedence (ordered; first matching row wins).** `overall` is driven by *issue severity* and whether a schedule was produced — **not** by re-deriving from the display indicators (single source of truth: an Issue's `severity`). `ScheduleSummary` supplies `{ produced, n_unscheduled, audit_ran }`.

| # | Condition | `overall` |
|---|---|---|
| 1 | `input_valid == false` — a structural / schema / referential **error** Issue exists (validation runs before scheduling) | **blocked** |
| 2 | `produced == false` — the engine returned no schedule (infeasible construction, adapter failure) | **blocked** |
| 3 | any surviving Issue with `severity == error` (hard-constraint infeasibility, `UNSCHEDULED_TASK`, hard window/time-window violations, an **error**-severity audit finding → `audit_passed == false`) | **blocked** |
| 4 | schedule produced, no error Issues, but ≥ 1 Issue with `severity == warning` (`INSUFFICIENT_RESOURCE`, soft window slips, non-blocking audit findings) | **ready_with_warnings** |
| 5 | schedule produced, no error and no warning Issues | **ready** |

**Indicator derivation** (each flag independently; `not_evaluated` is a real third value, never a stand-in for `false`):

| indicator | `true` | `false` | `not_evaluated` |
|---|---|---|---|
| `input_valid` | no structural/schema/referential error | such an error present | — (validation always runs) |
| `schedule_complete` | `produced` and `n_unscheduled == 0` | `produced` and `n_unscheduled > 0` | not `produced` |
| `hard_feasible` | `produced`, no hard-constraint error Issue | a hard-constraint error Issue present | not `produced` |
| `has_unscheduled_tasks` | `n_unscheduled > 0` | `n_unscheduled == 0` | not `produced` |
| `has_window_violations` | a window-violation Issue present | none | not `produced` |
| `audit_passed` | `audit_ran` and no error-severity audit Issue | `audit_ran` and an error-severity audit Issue | **`audit_ran == false`** |

The indicators exist for the UI (filtering, affected-entity highlighting) and are the audit trail behind `overall`; because `overall` reads issue severity directly (not the indicators), a UI that recolors an indicator can never silently flip Ready↔Blocked.

### ScheduleDTO
- `makespan_hours`
- `cpm_lower_bound_hours`
- `optimism_gap_hours` (derived; makespan − cpm)
- `activities` — [ScheduledActivityDTO]
- `constrained_chain` — [task_id] (resource-constrained critical chain, ordered)
- `cpm_critical_path?` — [task_id]

**ScheduledActivityDTO**
- `task_id`
- `description?`
- `start_hour`, `end_hour`
- `duration`
- `delay_hours` — waited-for-resource gap
- `tf_actual_hours?` — actual (resource-aware) float; may be negative (expected artifact)
- `on_constrained_chain` — bool
- `float_class` — `critical` | `zero_float` | `positive_float` (red/orange/blue). Assigned by a single shared rule `classify_float(tf_actual_hours, on_constrained_chain)` (skeletons; tolerance `TF_ZERO_TOL`) so every adapter classifies identically; derivation in `prism-gui-prism-adapter.md` §5.
- `actual_resources?` — assignment after skill substitution
- `wbs_group?`

### DiagnosticsDTO (Phase 1 minimal; grows later)
- `fitness?` — { composite, makespan_ratio, delay_ratio, criticality_ratio, window_violation_ratio, n_window_violations }
- `dependency_violations?` — [Issue] (from the targeted precedence check)
- (chain-sets, idle-time, augmentation, buffers etc. added in later phases as their own DTOs)

---

## 7. Execution port (job-oriented)

Interface only. Phase 1 implementation completes synchronously inside `submit`; a later background executor implements the same contract.

**RunRequest** (serializable, produced by `prepare_run()`)
- `effective_plan_hash` — the EffectivePlan snapshot, **referenced by hash**; the bytes live in the snapshot store (§8a), where `prepare_run()` has already persisted them.
- `run_config_hash` — the RunConfig snapshot, referenced by hash.
- `provenance_inputs` — the hashes needed to populate `RunResult.provenance` (baseline, effective plan, run config, `scenario_delta_hash?`, `schema_version`, `canonicalization_version`); every referenced blob is in the snapshot store by submit time.
- `request_id?`

(**Reference by hash, not by value:** an earlier draft embedded the serialized snapshot *bytes* in the RunRequest. The skeletons reference snapshots by hash and resolve them through the `SnapshotStorePort` (§8a) — which is what makes content-addressed dedup and the "every provenance hash resolves to stored bytes" invariant enforceable.)

**ExecutionPort**
- `submit(RunRequest) → RunId`
- `get_status(RunId) → RunStatus` where RunStatus ∈ `queued | running | completed | failed | cancelled`
- `get_result(RunId) → RunResult` (valid once status is a terminal state)
- `cancel(RunId) → void`

Notes:
- The adapter behind this port is the **only** code that builds a PRISM runtime, and it builds a **fresh** one per request from the effective-plan snapshot it resolves via the snapshot store (fresh-runtime invariant). Full contract + PRISM API mapping: `prism-gui-prism-adapter.md`.
- **`run_id` is minted by the executor inside `submit()`** — not by the domain, not by `prepare_run()`. Rationale: `run_id` is the identity of an *execution attempt*, not of the inputs. The content hashes (§6) already identify the inputs; submitting the *same* `RunRequest` twice is two attempts and must yield two `run_id`s and two `RunResult`s (e.g. to time a re-run, or after a transient failure). It is opaque and collision-free without coordination — recommend a UUID4 hex — so a future persistent/background executor mints ids the same way without a shared counter. The matching `timestamp` (§6) is stamped at the same point. `RunRequest.request_id?` is a *caller-supplied* correlation tag (optional, for the UI to match a submission to its returned id) and is distinct from the executor-minted `run_id`.
- Execution-mode selections in RunConfig are applied here, when constructing the runtime (`Pert.set_modes`, before scheduling).
- **Time model (corrected).** The effective-plan snapshot is the **schema-shaped dict with ISO timestamps** — the same artifact that is hashed (canonicalization spec) and validated (§8b). The verified PRISM loader `OutageData.from_dict` is **datetime-native**, so the adapter feeds it that ISO snapshot *directly*; the ISO→hour-offset conversion happens on the adapter's **output** side when reading PRISM's `datetime` results into DTOs (quantized to the 1 ms grid). This corrects an earlier draft (here and in the §1 framing) that said the snapshot carried already-normalized hour-offsets and the adapter did no ISO conversion — that was backwards. Detail: `prism-gui-prism-adapter.md` §1.

---

## 8. Repository port (defined, unimplemented in Phase 1–2)

Names the persistence seam so DTOs stay honest about serializability. No implementation now.

**RepositoryPort**
- `save_baseline(ReferencePlan) → void`
- `load_baseline(id) → ReferencePlan`
- `save_scenario(Scenario) → void`
- `load_scenario(id) → Scenario`
- `save_run_config(RunConfig) → void`
- `load_run_config(id) → RunConfig`
- `save_run_result(RunResult) → void`
- `load_run_result(id) → RunResult`
- `list_*` accessors as needed

---

## 8a. Snapshot store port (content-addressed blobs)

Deliberately **separate from the RepositoryPort**: content-addressed immutable blobs (put-returns-hash, dedup) are a different contract from entity save/load-by-id. This is where the canonical snapshots (§6, canonicalization spec) live, and what `RunRequest` (§7) resolves snapshots through.

**SnapshotStorePort**
- `put(canonical_snapshot) → hash` — store bytes, return their content hash; idempotent (dedup by hash). The returned hash **must equal** the provenance hash computed for the same bytes.
- `get(hash) → canonical_snapshot` — retrieve bytes by hash; raises a domain-neutral `SnapshotNotFoundError` if absent (the executor converts it to a `SNAPSHOT_MISSING` Issue on a failed RunResult, keeping storage mechanics separate from diagnostics).
- `contains(hash) → bool`

Phase 1 backs this with an in-memory dict (`InMemorySnapshotStore`); a persistent content-addressed store replaces it when persistence lands. **Invariant:** every hash in a `Provenance` resolves in this store.

---

## 8b. Validation seam — wrap the existing validator, don't reimplement

Schema and referential-integrity validation is **not** re-written in the domain. `src/CPM/validate_outage_data.py`
already implements it as a **pure** module (stdlib + `jsonschema` only — no PRISM, no scheduling, no Streamlit):
Draft7 schema validation (collecting *all* errors) plus duplicate-ID, successor/location/equipment/skill
references, self-reference, hold-point logic, cycle detection over `successors` + `blocks_tasks`,
availability-period ordering/overlap, and a coarse resource-sufficiency check. Reimplementing any of this in
the domain would create two rule sets that silently drift. So the plan **depends on a port and wraps the
existing validator behind it.**

**ValidationPort**
- `validate_plan(plan_data) → [Issue]` — validate a schema-shaped plan dict (a loaded baseline, or a
  serialized EffectivePlan from `materialize()`), returning structured issues.

**Adapter behavior** (the only code that imports `validate_outage_data`):
- Calls `OutageDataValidator(schema_path).validate(plan_data, strict_resource_overlaps=…)`, whose signature is
  `(is_valid: bool, errors: [str], warnings: [str])`.
- Maps `errors → Issue(severity=error)` and `warnings → Issue(severity=warning)`, assigning `code` / `category` /
  `entity_type` / `entity_id` / `field_path` (see the mapping below). `is_valid` is redundant with "no
  error-severity issues" and is not surfaced separately.

**Message → code mapping** (severity in parentheses):

| Validator output | `code` | `category` |
|---|---|---|
| Draft7 schema error | `SCHEMA_TYPE_ERROR` / `SCHEMA_RANGE_ERROR` (error) | `schema` |
| Duplicate task/skill/equipment/location id | `DUP_ID` (error) | `referential_integrity` |
| Non-existent successor/location/equipment/skill/blocked-task ref | `REF_MISSING` (error) | `referential_integrity` |
| Self-referencing successor / hold-point blocking itself | `DEP_CYCLE` (error) — degenerate 1-cycle | `referential_integrity` |
| Circular dependency | `DEP_CYCLE` (error) | `referential_integrity` |
| `start_date >= end_date`, overlap, bad date-time | `INVALID_AVAILABILITY_INTERVAL` (error/warning per `strict`) | `referential_integrity` |
| Non-hold-point task with `hold_point_type`/`blocks_tasks` | **new code** `HOLD_POINT_MISUSE` (error) | `referential_integrity` |
| Coarse resource-sufficiency shortfall | **new code** `INSUFFICIENT_RESOURCE` (warning) | `feasibility` |

The two **new codes** are the only catalogue additions the seam requires; everything else lands on codes
already enumerated in §1.

**Embedding caveats** (the validator was written as a CLI, so the adapter must isolate the GUI from three things):
1. **No process exits.** The module calls `sys.exit(1)` on a missing `jsonschema` import (module level) and on a
   falsy `schema_path` (`__init__`). A library must never kill the Streamlit process — the adapter guarantees a
   real `schema_path` and treats `jsonschema` as a hard dependency (already available in the environment).
2. **Always pass the real schema path.** The not-found fallback returns an **undefined** `DEFAULT_SCHEMA`
   (referenced at `validate_outage_data.py:76`, never assigned), so a bad path raises `NameError`, not a clean
   error. The adapter resolves and passes `src/CPM/outage_schema.json` explicitly.
3. **Strings, not records — pick the durable form.** Today the validator returns human-readable strings, so the
   mapping above must parse them. The message formats are stable and enumerable, so this works; but the durable
   fix (recommended as a Phase-1 task) is a small **backward-compatible refactor** of `validate_outage_data.py`
   to also append a structured record (`{code, severity, entity_type, entity_id, field_path, message}`) beside
   each string — the CLI keeps formatting the strings, the GUI consumes the records, and there is no string-parsing
   and no second rule set. The schema branch already builds a JSON-Pointer-like `path`, which populates
   `Issue.field_path` directly.

Layering note: `validate_outage_data.py` is pure, but the domain still depends on the **port**, not the concrete
module — the adapter that imports it is infrastructure, exactly as the PRISM adapter is. This keeps the domain
importing nothing outside itself and lets the validator be swapped or refactored without touching domain code.

---

## 9. Accessor layer (application ↔ session state)

The only code that knows Streamlit session-state keys. UI calls these, never `st.session_state` directly.

**SessionAccessors** (conceptual)
- `get_baseline() / set_baseline(ReferencePlan)`
- `get_draft() / set_draft(PlanDraft) / clear_draft()` — the in-progress edit is a **PlanDraft** (mutable raw tree + pending patches; §2b), **not** a mutable ReferencePlan.
- `get_scenario() / set_scenario(Scenario?)`
- `list_run_results() / add_run_result(RunResult) / get_run_result(run_id)`
- `get_selected_result_id() / set_selected_result_id(run_id?)` — which result the UI is displaying; freshness is assessed against it.

**No `mark_lineage_dirty`** — freshness is **derived, never stored** (a cached dirty flag can itself go stale). The application computes it on demand:
`assess_freshness(result, current_plan_hash, current_scenario_hash, current_run_config_hash?) → current | stale | different_config`, comparing the result's provenance hashes against the current lineage. **`stale`** means the displayed baseline/scenario has since changed; a mere change of *selected config* is **`different_config`**, not stale. Nothing is deleted — changed-lineage results stay viewable and reproducible. (Replaces the earlier `mark_lineage_dirty` / `get_working_copy` accessors.)

---

## Open questions (model-level)
- ~~**Canonical-serialization spec:** exact numeric normalization rules, how `schema_version` participates in the hash, key-ordering rules.~~ **Resolved (2026-09-09):** see `prism-gui-canonicalization.md` — RFC 8785 (JCS) + SHA-256, a `{canon_version, schema_version, kind, payload}` envelope, 1 ms numeric normalization, preserved array order.
- ~~**Sub-hour precision & rounding** at the ISO ↔ hour-offset mapping boundary.~~ **Resolved (2026-09-09):** 1 ms grid (matching the engine's `_EVENT_EPSILON` / `_PREC_TOL`); ISO normalized to UTC-ms. See canonicalization spec §4 and adapter spec §1/§5.
- **Emergent-task id collisions:** how an emergent task id that clashes with a baseline task id is handled (reject vs. namespace). *Partially settled:* the `EMERGENT_ID_COLLISION` code (§1) now exists and materialize detects the clash — the current lean is **reject** (the simpler, safe default). Left open is only whether a later phase should instead **namespace** emergent ids (e.g. an `emergent:` prefix) to allow intentional shadowing; not decided here.
- **Provenance bundle format:** the on-disk/exchange shape of a self-contained bundle (deferred until persistence/export lands, but noted).

## Resolved (previously open)
- Dependencies → normalized top-level edge list; serialized back to per-task `successors`.
- Effective-plan snapshot → full snapshot, content-addressed dedup (not reconstruct-from-delta).
- materialize vs. run-config validation → split into `materialize` / `validate_run_config` / `prepare_run`.
- Scenario → baseline binding via `base_plan_hash`, not `plan_id` alone.
- `_raw`/typed sync → patch-on-raw-then-rehydrate invariant; typed view never committed directly.
- Dose budget → carried **on the resource** (`resource_type = consumable` + `dose_budget_per_worker_mrem`), matching `outage_schema.json`; no separate top-level `dose_budgets` list and no standalone `DoseBudget` type. `ResourcePool` types both `resource_type` (runtime-affecting: renewable resets per period, consumable draws down) and the per-worker budget, per the refined thin-view rule.
- Time → hour-offsets in domain/DTOs, ISO in the schema-shaped dict (authoritative — hashed, validated, and fed to PRISM's datetime-native loader); ISO→hour conversion is an **output-side** concern (adapter reads `datetime` results into hour-offset DTOs, quantized to the 1 ms grid), half-open intervals. Corrected 2026-09-09: the earlier "conversion in the serialization mapper, adapter does no ISO conversion" claim was backwards — see adapter spec §1 and canonicalization spec §4.
- Disposition → pure domain `compute_disposition()`, tri-state indicators.
- Issue codes → Phase 1–2 core catalogue enumerated; `field_path?` added.
- Schema & referential validation → **reuse `src/CPM/validate_outage_data.py`** behind a `ValidationPort` (§8b), mapping its `(is_valid, errors, warnings)` to `Issue`s; do **not** reimplement the rules in the domain. `materialize()` runs the same validator on the serialized effective plan. Two new codes (`HOLD_POINT_MISUSE`, `INSUFFICIENT_RESOURCE`) added; three embedding caveats (no `sys.exit`, always pass the real schema path, prefer a structured-emit refactor over string-parsing) recorded.
- Emergent wiring → `emergent_dependencies: [Dependency]` (in/out edges + lags).
- Resource/equipment change semantics → ascending `from_hour`, same-hour invalid, outage = two changes.
