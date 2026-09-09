# PRISM GUI dev_docs — Assessment & Missing Capabilities

Review of the five design docs in `src/prismGui/dev_docs/` (capability notes, architecture,
model spec, skeletons, contract tests), cross-checked against the actual PRISM code
(`src/CPM/pert.py`), the schema (`src/CPM/outage_schema.json`), and the existing validator
(`src/CPM/validate_outage_data.py`).

---

## Verdict

Unusually strong architecture work — well above the level of most "GUI plan" docs. It is
**grounded in reality, not aspirational**: every `[Existing]` API the plan leans on was verified
to exist in `pert.py`, the "22-rule library" is *exactly* 22 keys in `_list_priority_names`, and
all five SGS variants are real. The claims check out.

### What is genuinely right (keep intact)

- **The fresh-runtime invariant is the single most important call in the whole design.** PRISM
  mutates activities / pools / queues / timing fields in place (verified). "Never store a runtime;
  build a fresh one per run from the effective-plan snapshot" is exactly what prevents silent
  cross-run contamination during rule sweeps and scenario comparisons. Making it a *tested*
  invariant (A→B→A form) is correct.
- **Ports & adapters with a pure domain**, plus the explicit correction that "the PRISM adapter is
  infrastructure, not domain." Right.
- **The baseline / scenario / run-config trichotomy**, and especially the insight that a
  resource-availability change is *canonically ambiguous* (roster correction vs. "what if we lose
  two welders") and the GUI must make the user tag intent. Sophisticated and correct.
- **Provenance in Phase 1**, content-addressed snapshot store, freshness *derived* not stored, and
  the STALE vs. DIFFERENT_CONFIG distinction. All subtle and all correct.
- **Patch-on-`_raw`-then-rehydrate** so there is a single source of truth. The right way to avoid a
  lossy round-trip and two drifting sources of truth.
- **Structured `Issue` objects** instead of raw PRISM strings, and **tri-state disposition**
  (`true | false | not_evaluated`, so "audit failed" ≠ "audit did not run"). Both correct.

---

## Missing / mismatched capabilities

Ranked by materiality. The first two are concrete schema mismatches, not style.

### 1. `resource_type` (renewable vs. consumable) and dose-budget shape do not match the schema — **highest priority**

The spec models `ResourcePool = { skill_type, availability_periods }` and invents a separate
top-level `dose_budgets: [{ skill_type, limit_mrem }]`. But the actual schema
(`outage_schema.json`) has:

- `resources[].resource_type` = enum `[renewable, consumable]` — **runtime-affecting** (renewable
  resets each period; consumable draws down permanently). By the spec's *own* "refined thin-view
  rule" (type everything needed to build/validate the runtime), this **must be typed** — and it is
  omitted from `ResourcePool`.
- Dose budget lives *on a resource* as `dose_budget_per_worker_mrem` (meaningful only when
  `resource_type = consumable`; total pool budget = value × peak available_count), **not** as a
  separate top-level list. The spec's `DoseBudget` will not round-trip to the schema.

This is the most important fix: the Phase-1 pool model cannot build the runtime correctly as
written. Suggested resolution: add `resource_type` to `ResourcePool` and fold the dose budget onto
the resource, matching the schema.

> **Resolved (2026-09-08) in the md docs.** `ResourcePool` now types `resource_type`
> (`renewable | consumable`, default `renewable`) and `dose_budget_per_worker_mrem?`; the standalone
> `DoseBudget` type and the top-level `dose_budgets` list are removed from the model spec.
> Architecture (Phase-2 editing) and capability notes (project view + Phase-2 build order) updated
> to match. **Also mirrored (2026-09-09) in the code skeletons:** `prism-gui-skeletons.py` now has a
> `ResourceType` enum, `ResourcePool.resource_type` (default `RENEWABLE`) + `dose_budget_per_worker_mrem?`,
> and the standalone `DoseBudget` dataclass and `PlanContent.dose_budgets` are gone;
> `prism-gui-contract-tests.py`'s import surface drops `DoseBudget` for `ResourceType`. **Done.**

### 2. `safety_functions` — decide Option A vs. Option B (the mutual-exclusion mechanism *is* already modeled)

> **Note on framing:** an earlier draft called this "entirely unmodeled," which overstated it. On
> verification the mutual-exclusion *mechanism* **is** modeled (Option A — see the decision box below);
> what is genuinely open is only Option B (K-of-N) and the flagging feature.

The schema has a top-level `safety_functions` array
`{ safety_function_id, train_ids, plant_system_id, max_trains_oos_simultaneously }`. The model spec
never types it. The schema marks it **"metadata only" — the scheduler does not read it directly**
(verified: `pert.py` never references `safety_functions`), and mutual exclusion is encoded indirectly via
`plant_systems` states — the schema's **Option A** — so it survives in `_raw` for round-trip.
**But**:

- The capability notes explicitly promise "prohibited-overlap flagging (both ECCS trains OOS)" as a
  feature.
- The schema itself documents a planned "Option B: first-class `SafetyFunctionPool` with
  `{ trains, max_trains_oos }`" for K-of-N (K>2) trains.

Neither the K-of-N semantics nor the flagging has a typed home in the model. For a nuclear-outage
tool this is arguably *the* domain constraint — it deserves an explicit decision even if that
decision is "stays in `_raw`, revisit in Phase 6."

> **Decided (2026-09-09): Option A for now.** `safety_functions` stays in `_raw` as metadata; mutual
> exclusion continues to rely on the already-modeled Option A encoding (`systems` / `PlantSystem.valid_states`
> with one state per train + per-task `required_states`, made mutually exclusive by PRISM's SystemStatePool).
> No new typed model is added. **Option B** (first-class `SafetyFunctionPool` for K-of-N,
> `max_trains_oos_simultaneously > 1`) is **deferred pending a more detailed investigation** — and the schema
> already marks it "not yet implemented" in PRISM, so there is nothing for the GUI to expose until the engine
> supports it. The "prohibited-overlap flagging" GUI feature remains a later `[New analysis]` item (capability
> notes), not a Phase 1–2 commitment. Recorded in the model spec (§2 `_raw` note). No skeleton change needed —
> `systems` / `required_states` are already typed.

### 3. The plan silently ignores the existing validator

The docs put schema + referential-integrity validation in the pure domain ("no PRISM"). But
`src/CPM/validate_outage_data.py` **already does exactly this** — Draft7 jsonschema + cycle /
availability-period / resource-sufficiency referential checks — and it is **already pure** (imports
only stdlib + `jsonschema`, no PRISM, no scheduling). The plan should wrap this behind the
validation seam and map its output to structured `Issue`s, **not reimplement it** (reimplementation
= drift from the canonical rules). It is never mentioned anywhere in the five docs — a notable
omission given the plan leans hard on "readable structured validation."

> **Resolved (2026-09-08) in the md docs.** A **`ValidationPort`** now names the seam (model spec §8b;
> architecture Ports + domain-layer + Phase-1 build list; capability 1 in the notes). The domain owns the
> `Issue` type, code catalogue, and validation *contract*; a Phase-1 adapter wraps
> `validate_outage_data.py` and maps `(is_valid, errors, warnings)` → `Issue`s (full message→code table in
> §8b). `materialize()` reuses the same validator on the serialized effective plan. Two catalogue additions
> (`HOLD_POINT_MISUSE`, `INSUFFICIENT_RESOURCE`) and three embedding caveats (no `sys.exit`; always pass the
> real schema path — the not-found fallback hits an undefined `DEFAULT_SCHEMA`; prefer a structured-emit
> refactor of the validator over string-parsing) are recorded. **Also mirrored (2026-09-09) in the code
> skeletons:** `prism-gui-skeletons.py` adds a `ValidationPort` Protocol (`validate_plan(plan_data) → [Issue]`),
> an `OutageValidatorAdapter` infrastructure class documenting the three caveats, and the two new codes
> (`HOLD_POINT_MISUSE`, `INSUFFICIENT_RESOURCE`); the drifted `DEP_SELF_LOOP` code was folded into `DEP_CYCLE`
> to match §8b. `prism-gui-contract-tests.py` adds group **K** (port semantics + the §8b message→code mapping
> + the CLI-ism-isolation caveats) and reconciles the self-loop test to assert `DEP_CYCLE`. **Still open
> (recommended, user-owned):** the small backward-compatible structured-emit refactor of
> `validate_outage_data.py` itself — a change to production code, deferred to the user.

### 4. Scope selection (`MDKoutage.py`) may be an unexposed capability — verify

`mdkChoiceModel` (in `src/CPM/MDKoutage.py`) is a multi-dimensional-knapsack model for *which
candidate tasks to include* given resource capacity — that is outage-**scope** selection, distinct
from sequencing. The plan only surfaces `md_knapsack` as an SGS variant. Worth confirming: is scope
selection a user-facing capability you want, or is the SGS the only intended surface? If the former,
it is absent from the capability list and build order.

> **Decided (2026-09-09): SGS-only, for now.** `md_knapsack` stays a `SGSVariant` and nothing more;
> `mdkChoiceModel` scope selection is **intentionally out of scope** (alongside cost and
> fleet/multi-project). No capability-list or build-order addition is needed, and the skeletons already
> match — `SGSVariant.MD_KNAPSACK` with no separate scope-selection surface. Revisitable later if scope
> selection becomes a wanted capability.

---

## Minor notes

- **Contract tests are 100% `pytest.skip` including the fixtures.** The header is honest about this
  ("do not mistake a green run for verified behavior") and states the intent (real Phase-1
  assertions; CI fails on skipped required contracts) — but that enforcement machinery (markers +
  CI gate) is *described*, not built. The "no silent gaps" guarantee depends on it existing.
- **In-memory snapshot store grows unbounded within a session** — a 500-task plan × a 22-rule sweep
  stores 22 effective-plan snapshots in RAM with no eviction. Fine for now; worth a note.
- The dependency shape is handled correctly: schema `successors` entries can be a bare string *or*
  `{ task_id, lag_hours }`, and the spec's normalized top-level edge list with `lag_hours`
  round-trips both. Good.

---

## Open questions for the author

1. ~~**Dose / `resource_type` mismatch (#1):** shall I draft the corrected `ResourcePool`?~~
   **Done** across the md docs (spec, architecture, notes) *and* the code skeletons
   (`prism-gui-skeletons.py` + `prism-gui-contract-tests.py`). Fully resolved.
2. ~~**Scope selection (#4):** is `mdkChoiceModel` meant to be a GUI capability, or intentionally out
   of scope like cost ($/day) and fleet/multi-project?~~ **Decided (2026-09-09):** out of scope for now —
   `md_knapsack` stays an SGS variant only. Fully resolved.

---

## Pre-implementation gap review (2026-09-09)

With the four findings resolved, a separate pass asked: *what is still missing/under-specified before
Phase-1 code?* Three tiers. **All three are now RESOLVED (2026-09-09)** — the design docs are
complete enough that an implementer following the prose alone can start Phase-1 code.

### Tier 1 — would block Phase 1 immediately. **RESOLVED (2026-09-09).**
1. **The PRISM adapter mapping was undocumented** — the single largest Phase-1 component. Now specified
   in **`prism-gui-prism-adapter.md`**: fresh-runtime construction via `Pert(outage_data=OutageData.from_dict(...))`,
   RunConfig wiring (modes/seed/sgs/rule/horizon), the datetime→hour output conversion, the full
   PRISM→DTO field mapping, the `validate_schedule()` audit→`Issue` table (16 `Violation.type`s), the
   dependency-check mapping, disposition sourcing, failure handling, and an exact API reference (all
   `file:line`-verified). A key correction fell out: the loader is **datetime-native and ingests the
   schema-shaped ISO dict directly**, so the adapter's *input* is the schema snapshot (ISO) and ISO→hour
   conversion is an *output*-side concern — this supersedes the old model-spec §7 claim (annotated).
2. **Canonicalization was unspecified** — yet hashing/snapshot-store/lineage are all Phase 1. Now
   specified in **`prism-gui-canonicalization.md`**. User decisions: **RFC 8785 (JCS)** scheme,
   **~1 ms** time-precision floor (matching the engine). Plus SHA-256, a `{canon_version,
   schema_version, kind, payload}` envelope, and **preserved array order** (conservative — the engine's
   tie-breaking may depend on task input order, so the hash must capture it). Resolves the
   canonical-serialization and sub-hour-precision open questions in the model spec + architecture.

### Tier 2 — doc drift: the `.py` skeletons are ahead of the prose specs. **RESOLVED (2026-09-09).**
The skeletons carried revisions the model-spec/architecture prose never absorbed; an implementer
following the prose alone would have built superseded shapes. Reconciled in one pass — the prose now
matches the intended skeleton forms:
- `Task.hold_point` — model-spec `is_hold_point` flag dropped; presence of the `hold_point` record (now `{ hold_point_type?, blocks_tasks }`) carries the status, misuse → `HOLD_POINT_MISUSE`. **Done** (model-spec §2).
- Scenario/RunConfig overrides — model-spec maps `{task_id: value}` → **tuple-backed record lists** (`[{ task_id, duration_hours }]`, `[{ target_id, release_hour }]`, `[{ task_id, mode_name }]`), with None = "does not touch" semantics. **Done** (model-spec §3, §4).
- RunConfig horizon — model-spec `max_time_hours?` → `scheduling_horizon_hours?`; the adapter maps it to PRISM's `max_time_hours=` arg (**PRISM's API param is unchanged** — only the domain field renamed). **Done** (model-spec §4; adapter spec §3 table note corrected).
- Provenance version — model-spec single `software_version` → **split** `canonicalization_version` + `app_version` + `prism_version`, with a "version split" rationale. **Done** (model-spec §6; architecture provenance list).
- Accessors — model-spec §9 `get_working_copy()`/`mark_lineage_dirty()` → **`PlanDraft`** (raw working tree + pending patches) via `get_draft`/`set_draft`/`clear_draft`; `mark_lineage_dirty` removed (freshness is **derived, never stored** — `assess_freshness(...) → current | stale | different_config`); added `get/set_selected_result_id`. **The one that mattered most** — the raw-tree-authoritative `PlanDraft` model is now the prose's model. **Done** (new model-spec §2b + §9; architecture editing-contract, state-model, baseline-vs-scenario).
- `AUDIT_*` code-prefix rule folded into the §1 catalogue (reuse-or-prefix convention documented). **Done** (model-spec §1; adapter spec §6 note updated).

The pass also reconciled the same drift class beyond the six enumerated items: expanded the §1 Issue
catalogue (`DUP_DEPENDENCY`, `INVALID_TIME_WINDOW`, `HOLD_POINT_MISUSE`, `INSUFFICIENT_RESOURCE`,
`EMERGENT_ID_COLLISION`, `SNAPSHOT_MISSING`; `DEP_CYCLE` subsumes self-loop); the `PlanContent` /
`raw_snapshot` immutability model (committed plan holds a canonical `raw_snapshot`, the mutable raw
tree lives only on `PlanDraft`); the separate **`SnapshotStorePort`** (new model-spec §8a; architecture
ports + build list); the **hash-reference `RunRequest`** (model-spec §7; architecture RunRequest);
`prepare_run` persisting snapshots (§5); and the **corrected time model** — the skeleton's own two
superseded comments (the `# conversion happens in the serialization mapper` header and the `RunRequest`
docstring) were fixed, along with the model-spec §7/§Open-questions and adapter-spec pointer notes: the
schema-shaped ISO dict is authoritative and datetime-native, ISO→hour conversion is output-side. The
`EMERGENT_ID_COLLISION` code now exists and leans **reject**, with the reject-vs-namespace design
choice explicitly left open (model-spec §Open questions).

### Tier 3 — content/scaffolding to exist before Phase-1 code. **RESOLVED (2026-09-09).**
- The **22 priority-rule descriptions** — authored in **`prism-gui-priority-rules.md`**, every
  description grounded in `pert.py`/`cpm_utils.py` (not invented): dispatch mechanism
  (`priority_calculation`, rank→`1/(1+i)`, `IOError` on unknown key — no silent default), the three
  sort conventions (ascending timing/evolved/slack, descending structure/resource, `random`), the 22
  rules grouped (Timing, Structure, Resource, Stochastic, Slack-based Kolisch 1996, Evolved/GP), and a
  caveats section (`minrr` near-degenerate; `mts`/`mtp`/`grpw` path-count approximations; evolved rules
  sort ascending; `rr` is breadth not quantity; normalization cosmetic; unknown = hard error). Default
  = `lf`; sweep-and-compare is the intended workflow. **Done** (new file; model-spec §4 + adapter §4
  now point to it). Corrected several would-be inventions along the way (`rr` = fraction of resource
  *types*; `irsm` = *improved*, not integrated; `wcs`/`acs` = worst-/average-case slack).
- **CI enforcement machinery** for the contract tests — built as copy-paste-ready blocks in
  **`prism-gui-contract-tests.py`** ("CI ENFORCEMENT MACHINERY"): marker registration
  (`phase2`, `adapter_integration`, `benchmark`), the default Phase-1 selection command, and a
  `conftest.py` gate on a **negative-inference** design — a test is a *required* Phase-1 contract
  *unless* positively marked deferred, so an uncategorized new test fails loud (no silent gaps). The
  gate (`pytest_runtest_logreport` / `pytest_terminal_summary` / `pytest_sessionfinish`) hard-fails the
  run on a plain skip of a required contract, and tolerates-but-reports strict-xfail required contracts
  as known gaps to close. **Done.**
- **Sample project** for the guided-load path — **decided** (notes_3.md Phase-1 build list):
  `doc/demos/rcpsp/examples/example_10.json` primary (15 tasks, 2 resources → real contention),
  `doc/demos/rcpsp/examples/test_case_1.json` secondary (carries the `is_hold_point` field shape, but 0
  of 8 tasks flagged — not an actual hold-point round-trip); both validate against `outage_schema.json`
  today, with coverage gaps documented to motivate a purpose-built Phase-2 sample. **Done.**
- `run_id` minting — **decided** (model-spec §7): the **executor mints `run_id` inside `submit()`**
  (identity of an execution *attempt*, not of the inputs; UUID4 hex; timestamp stamped at the same
  point). `RunRequest.request_id?` is a distinct caller-supplied correlation tag. **Done.**
- `compute_disposition` precedence — **written** (model-spec §6): an ordered, first-match-wins
  **precedence truth table** driven by Issue *severity* (single source of truth), plus an
  **indicator-derivation table** for the six tri-state indicators. **Done.**
