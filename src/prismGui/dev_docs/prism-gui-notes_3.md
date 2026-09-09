# PRISM GUI — Capability Notes

Working notes on what the GUI should do. Capabilities and core concepts first; architecture comes after.

## Context / assumptions
- The GUI is a front end for the existing RCPSP scheduling code (PRISM). General-purpose scheduling, outages being the primary but not the only use case.
- **Framework:** Streamlit.
- **Backend integration:** in-process (Streamlit imports the PRISM package directly) for now. Long-running work (GA/ALNS, RAVEN) will eventually want to run out-of-process — design a seam for it, build behind the seam later.
- **Users:** both personas need to be served, and a single user may be both — the *planner* (thinks in tasks, work packages, crews) and the *analyst* (comfortable with priority rules, metaheuristics, fitness scores). Handle via **progressive disclosure within one interface**, not two locked modes (see Core concepts).
- **Editing in the GUI:** in scope, scoped to schema elements, phased (see Build order). Governed by the editing contract below, not a naive "always valid" rule.
- **Out of scope for now:** cost figures ($/day), fleet / multi-project handling, and outage-**scope** selection (`mdkChoiceModel` in `src/CPM/MDKoutage.py` — deciding *which* candidate tasks to include given capacity). `md_knapsack` is surfaced as an SGS variant (sequencing) only.

---

## Core concepts / data model
The single most important thing to get right before architecture. These four objects must be kept distinct — mixing them (e.g. a duration change that is ambiguously a permanent edit or a temporary override) is what makes comparison, replanning, and provenance accidental instead of coherent.

- **Reference plan (baseline)** — tasks, dependencies, pools, calendars, constraints. The definition of the plan. ("Reference," not "approved" — formal approval/governance workflow is not in scope.)
- **Scenario** — temporary changes layered on the baseline: crew loss, duration override, emergent work. Never silently mutates the baseline.
- **Run configuration** — SGS, priority rule, modes, seed, optimization settings.
- **Run result** — schedule, diagnostics, validation status, and provenance.

A what-if analysis must not modify the reference plan. Comparisons operate over run results produced from a (baseline, scenario, run config) triple.

### Baseline vs. scenario vs. run config — the discriminating rule
Changes to the **definition** of the plan are baseline edits; changes introduced to **test or represent a particular operating condition** are scenario overrides; run configuration controls **how PRISM solves** that baseline-plus-scenario combination.

Applying the rule to the ambiguous cases:
- **Duration override** → scenario.
- **Shift-calendar change** → baseline (part of the plan's definition).
- **Hold-point release assumption** → scenario (an assumption being tested).
- **Execution-mode selection** → run configuration. Reasonable, but this is an explicit choice, not incidental — modes could arguably be baseline; we keep them in run config.
- **Resource-availability change before scheduling** → *canonical ambiguous case*. The same action (change a headcount) is a baseline edit when it corrects the roster, or a scenario override when it asks "what if we lose two welders." Intent decides. Design cue: the GUI likely needs to let the user tag which, or ask.

### Object lifecycle (behaviors to pin down; persistence architecture deferred)
Not building persistence yet, but these behaviors will materially shape it:
- Can a scenario be named and saved?
- Can a run configuration be reused across runs?
- Are run results retained across Streamlit sessions?
- Can a user promote a scenario into a new baseline revision?
- Does editing a baseline invalidate results, or preserve them as historical results tied to the old snapshot?

Guiding rule: **stale results stay viewable and reproducible** — they are tied to their input snapshot, not deleted. Staleness is a flag, not a disappearance.

### Editing contract
Replaces the naive "JSON valid throughout" requirement (which would make form editing awkward — a task is legitimately incomplete mid-edit).
- The loaded baseline stays valid and unchanged until the user **applies** an edit.
- Edits are validated **transactionally** before being committed.
- Exported JSON must be schema-valid and round-trippable.
- Referential integrity must be specified: e.g. deleting a resource that tasks still reference is blocked (or requires explicit cascade). Same for locations, equipment, pools.

### Schedule status (a summary disposition, not a state machine)
"Schedule generated" ≠ "fully compliant." Time-window violations are penalized but do not block scheduling; unscheduled activities can exist. These are **independent dimensions**, not mutually exclusive states — a schedule can be complete *and* have window violations *and* pass audit at once.

Display one overall **disposition** — **Ready / Ready with warnings / Blocked** — backed by separate indicators:
- input validity
- schedule completeness
- hard-constraint feasibility
- unscheduled tasks present
- time-window / policy violations present
- audit / validation result

### Provenance (adopt in Phase 1)
Every run records: immutable input snapshot, run identifier, selected rule/SGS/seed, software version. Displayed results must be detectably **stale** after an input change. The rich provenance UI can come later; the plumbing is Phase 1 because it is painful to retrofit.

### Domain profile (principle now, mechanism later)
PRISM is general-purpose. Outage-specific capabilities (system isolation, dose budget, NRC/QA hold points) should surface under a nuclear-outage **profile**, while the core app uses generic constraint terminology. Adopt the *principle* now — don't bake "NRC" into core navigation. Build the profile *mechanism* later, when the outage-specific views actually get implemented (Phase 6); Phase 1 can simply use generic terms.

### Progressive disclosure (both personas, one interface)
Not two locked modes — the same user can move between depths, and complexity is revealed progressively.
- **Planner-facing defaults:** completion time, schedule status, optimism gap, critical chain, resource bottlenecks, comparison, replanning. Coarse actions like "Fast schedule" vs. "Search for a better schedule."
- **Analyst controls (a click away):** SGS choice, rule selection, objective weights, seeds, GA/ALNS parameters, network diagnostics.
- The underlying algorithm stays hidden from the planner but always visible in the run record.

### Organizing workflow
The user's journey (distinct from build order, which is construction sequence):

**Prepare → Schedule → Understand → Compare → Replan**

with optimization, stochastic analysis, and LLM interpretation as advanced layers *around* this central spine.

---

## Backend-readiness legend
Tag capabilities so a one-line wrapper and a research project don't look equivalent.
- **[Existing]** — directly exposed by the current API
- **[Adapter]** — backend exists, needs GUI-friendly structured output
- **[New UI]** — existing data and methods, but a new interactive workflow / editor / state management
- **[New analysis]** — requires additional deterministic logic
- **[New data model]** — requires schema or persistence changes
- **[New infrastructure]** — persistence, background jobs, cancellation, or execution management

---

## Capabilities

### 1. Input & data management
- **[Existing / Adapter]** Load a project from JSON; validate against schema with readable errors (missing fields, malformed dependencies, inconsistent resource declarations) — not raw stack traces. The validation itself already exists as a pure module (`src/CPM/validate_outage_data.py`: Draft7 schema + referential integrity — duplicate ids, dangling refs, cycles, availability overlaps, coarse resource sufficiency); the [Adapter] work is wrapping it behind a validation port and mapping its messages to structured issues, **not** rewriting the checks.
- **[Adapter]** View the loaded project: tasks, dependencies, and the resource pools — **resources** (each tagged `renewable` or `consumable`; a consumable resource carries a per-worker dose budget, `dose_budget_per_worker_mrem`), equipment, locations, consumable materials, and system states — including time-varying availability periods. (Dose is not a distinct pool type: it is an attribute of a consumable-type resource, per `outage_schema.json`.)
- **[New UI]** Create/edit/delete schema elements (tasks, dependencies with lags, resource/equipment/location/consumable assignments, pool definitions and availability periods) — the schema already contains these; this is a new editor + state-management capability, under the editing contract, phased per build order.
- **[Adapter]** Save/export back to valid JSON. The report documents loading, not a complete round-trip serializer — this must be built to **preserve all fields the GUI does not edit** (lossless round-trip), not just re-emit the parts it understands.

### 2. Configuration before scheduling
- **[Existing]** Override task durations (scenario-level, not a baseline edit) without re-editing the file.
- **[Existing]** Select execution modes per task (normal / crash / reduced crew) for multi-mode tasks.
- **[Existing]** Choose SGS variant and priority rule (22-rule library) with plain-language descriptions. (Analyst control under progressive disclosure.)
- **[Existing]** Set shift calendar and working hours.

### 3. Running & viewing a schedule
- **[Existing]** Trigger scheduling; show makespan, CPM lower bound, and the gap between them prominently.
- **[Existing]** CPM-only baseline view (resource-unaware): logical critical path (`getCriticalPath()`). Underpins the optimism gap.
- **[Existing]** Gantt chart with red/orange/blue criticality coloring.
- **[Adapter]** Resource utilization charts (allocated vs. available per pool).
- **[Existing]** Activity network / DAG view (`plot_activity_dag()`) with user-controllable options: contention edges, chain-highlight mode (CPM / constrained / both), layout (early start / late start / topological depth), rendering backend.
- **[Adapter]** Schedule table (start, end, delay, actual float, chain membership) with filter/sort and CSV export. Surface *actual* resource assignment after skill substitution, and WBS group membership, per task.
- **[Existing]** Fitness score with components broken out. Weights (α, β, γ, δ) configurable — but under an advanced "schedule evaluation" section, with an explicit note: *these weights change how completed schedules are compared; they do not currently change the GA/ALNS search objective.*
- **[Existing]** Validation report in human-readable form (`validate_schedule()` — full audit). Drives the multi-state schedule status above.
- **[Existing]** Dependency-violation check (`check_dependency_violations()`) as a separate structured feasibility check.
- **[Adapter]** System-state / plant-isolation timeline view: which systems are locked in which state and when (data model, scheduling behavior, and the visualization concept per Figure 15 already exist). Prohibited-overlap flagging across systems (e.g. both ECCS trains OOS) is the only part that may need **[New analysis / New data model]**. Outage-profile capability. Maps to Section 2.5 system-state conflict.
- **[Adapter / New analysis]** Hold-point / approval tracking view: modeled release times are [Adapter]; a genuinely **live** approval status (pending/granted, owner) is [New data model]. Shows what tasks are blocked behind each. Maps to Section 2.5 early-hold-release.

### 4. Analysis & comparison
- **[Existing]** Priority-rule sweep: run several rules, compare makespans side by side. (High value — large spread between best and worst rule.)
- **[Existing]** Chain-sets comparison (`print_chain_sets_summary()`): CPM path vs. constrained chain — overlap, CPM-only, constrained-chain-only. Answers "am I watching the wrong tasks?"
- **[Existing]** CCPM buffer insertion and buffer-consumption status.
- **[Adapter]** Idle-time diagnostics on the critical chain (where the gap is, which resource is short, at which hours).
- **[New analysis]** Targeted resource-augmentation recommendation. **Must not** infer hours saved from the idle diagnostic alone (the report's crash-mode case shows added resources can make things *worse*). Instead: clone scenario → add resource → rerun PRISM → report the **verified** delta.
- **[New analysis]** Regulatory time-window pre-flight check: report whether windows are satisfied **by the schedules tested** — not a claim of global satisfiability (PRISM does not do exhaustive feasibility analysis). Flag safe / tight / missed. Maps to Section 6.7 / 4.3.2.
- **[New analysis]** Guided mode-sweep / trade-off exploration: "test this mode change, show makespan delta against the resource-constrained chain." Guards against accelerating a non-critical task.
- **[Existing]** What-if cloning: compare scenarios without touching the live plan.
- **[Adapter]** Network diagnostics (`debug_connectivity_and_es()`, `debug_candidates_and_capacity()`): flag unreachable tasks that will never schedule; trace deadlocks. Distinct from schema validation; analyst control.
- **[New analysis]** Skill / qualification pre-flight check: is qualified crew sufficient for peak demand by skill? New deterministic logic, but no new data model if "qualification" means the existing skill pools; a new model is needed only for individual credentials / qualification attributes. Overlaps with utilization charts but framed as pre-flight. Outage root cause 2.
- **[Adapter / New analysis]** Vendor / contractor availability view: contractor availability timeline (from existing time-varying availability) is [Adapter]; flagging tasks whose mobilization lead is at risk is [New analysis]. Outage root cause 7 / Section 4.3.1.

### 4b. Comparison (distinct comparison types)
Comparison is not one capability — the types answer different questions and carry different warnings.
- **[New UI]** Compare scenarios on the **same baseline** (e.g. two disruption responses). Cleanest case.
- **[New UI]** Compare runs with **different solver configurations** on the same baseline+scenario (e.g. rule A vs. rule B). The sweep is a specialized form of this.
- **[New UI / New data model]** Compare **different baseline revisions** (e.g. Monday's plan vs. today's). Needs an explicit warning that the definition itself changed, so differences are not purely schedule effects. Requires baseline versioning.

### 5. Mid-execution replanning
- **[Existing]** Set a checkpoint hour; update resource counts; inject emergent tasks; mark equipment OOS; override durations for overruns.
- **[Existing]** Update hold-point release time (e.g. released early); `replan()` re-runs CPM and pulls forward downstream work. Maps to Section 2.5 early-hold-release.
- **[New analysis]** Original-vs-replanned comparison. Impact must be reported as **isolated counterfactual effects** with any interaction/residual identified — disruptions interact, so "3 h from emergent + 39 h from crew loss" is generally **not additive**.
- **[Adapter]** Buffer-consumption early-warning monitoring (temporal: burn tracked over time as a leading indicator).

### 6. Optimization (heavier compute)
- **[Existing]** Launch GA / ALNS runs (minutes, not seconds). Note: optimizes serial-SGS makespan, *not* the four-component fitness score.
- **[New infrastructure]** Progress indication and cancellation. This is job management, not an adapter — background execution that survives Streamlit reruns. Ties directly to the out-of-process seam.

### 7. Risk analysis (RAVEN / Monte Carlo)
- **[Existing / Adapter]** Stochastic activity durations across many runs → distribution of completion times rather than a single estimate. Backend implemented via RAVEN. Analyst-oriented; deferrable.

### 8. LLM-assisted interpretation & troubleshooting
Sits **on top of** deterministic outputs to explain them — never in the scheduling path, and always keeping clear whether a number came from the solver or the LLM. Reinforces the provenance/defensibility principle: correct, defensible schedules for a regulated environment.
- **[New analysis]** Explain outputs in plain language — validation violations, idle-time deficits, chain-sets comparison, and non-obvious artifacts like negative actual-float (why it's expected, not a bug).
- **[New analysis]** Diagnose infeasibility / deadlocks by reading the network-diagnostic outputs (e.g. "T30–T33 never schedule — unreachable from START").
- **[New analysis]** Guided troubleshooting: walk the user through "my replan added 60 hours, why?" using the verified impact decomposition.
- **[New analysis]** Guardrailed fix suggestions derived from the tool's own diagnostics — always a proposal the user explicitly applies, never an automatic edit. A plausible-but-wrong suggestion is worse than none. *(later sub-item; the three interpretation tasks come first)*

---

## Build order

Guiding principle: get to a complete **load → schedule → view** loop as fast as possible; everything else hangs off it. The organizing workflow (Prepare → Schedule → Understand → Compare → Replan) is the user's journey; the phases below are construction sequence.

### Phase 1 — Minimum viable spine (+ provenance infrastructure)
The core loop, demoable on its own — it does the job CPM tools can't. Provenance plumbing is included here, not deferred, because it is expensive to retrofit.
- Load JSON + schema validation (readable errors).
- Run a schedule: choose SGS + priority rule.
- Makespan vs. CPM lower bound (optimism gap).
- Gantt + schedule table (filter/sort) + CSV export.
- Schedule status as a **disposition summary** (Ready / Ready with warnings / Blocked) backed by independent indicators — not a single green light.
- **Provenance:** immutable input snapshot, run ID, recorded rule/SGS/seed/software version; stale-results detection after input change.
- Sample project / guided load path. **Decided (2026-09-09):** ship
  [`doc/demos/rcpsp/examples/example_10.json`](../../../doc/demos/rcpsp/examples/example_10.json) as the
  **primary** guided-load sample and
  [`doc/demos/rcpsp/examples/test_case_1.json`](../../../doc/demos/rcpsp/examples/test_case_1.json) as a
  smaller secondary. (These are the canonical copies the CPM suite already reads; the byte-identical
  `tests/CPMmodel/` copies are test scaffolding, not the shipping location — the GUI conftest resolves
  samples to `doc/demos/rcpsp/examples/`.) Both validate against `outage_schema.json` today. `example_10`
  (15 tasks, 2 resources, 1 equipment, 1 location, plain string successors, no hold points) is the cleaner
  "first run" — 2 resources actually exercise resource contention, which is the point of RCPSP; `test_case_1`
  (8 tasks, 1 resource) additionally carries the schema's `is_hold_point` *field* on its tasks, but **0 of
  its 8 tasks are actually flagged** (`is_hold_point` is False throughout and no `hold_point` object is
  present) — so it exercises the hold-point field *shape* on load, not an actual hold-point round-trip.
  **Coverage gaps to note
  (motivate a richer purpose-built sample when the matching editors land):** neither file exercises
  `resource_type` (both leave it null → defaults to `renewable`), `systems` / `safety_functions`,
  `consumables`, multi-mode tasks, or object-form `successors` with `lag_hours`. Both also still carry the
  schema's `is_hold_point` flag in their raw JSON — harmless (it rides in `_raw`; the typed view derives
  hold-point status from the `hold_point` object per model-spec §2), but a sign these files predate the
  typed-view field shape. A Phase-2 editor sample should cover a consumable/dose pool and a multi-mode task.

### Phase 2 — In-GUI editing (scoped)
Moved up so users aren't hand-editing JSON early; the Phase 1 spine gives an edit → reschedule → see-the-effect loop. Full editing across all pool types + calendars + windows + hold points + system states is its own application — start small:
- Tasks and durations.
- Dependencies and lags.
- Basic resources and availability — the default `renewable` `resource_type` first.
- Advanced constraint pools (equipment zone-affinity, consumable materials, system state, and consumable-type resources carrying a per-worker dose budget) in later increments.
- All under the editing contract (transactional commit, referential integrity, valid export).

### Phase 3 — Make the output trustworthy and readable
Turns "here's a schedule" into "here's why it looks like this."
- Resource utilization charts.
- DAG view with options.
- Per-task drill-down / inspector ("why isn't this task starting sooner?").
- Fitness score with components + configurable weights (under advanced "schedule evaluation," with the non-optimization caveat).
- CPM-only baseline view.
- Dependency-violation check as a distinct output.

### Phase 4 — High-value analysis
Where the tool gives advice, not just answers.
- Priority-rule sweep.
- Chain-sets comparison.
- Idle-time diagnostics + **verified** resource-augmentation recommendation (clone → add → rerun → report delta).
- Regulatory time-window pre-flight (tested, not global).
- Guided mode-sweep / trade-off exploration.
- What-if cloning.

### Phase 5 — Replanning
The full mid-outage loop; meaningful once the base schedule is solid.
- Checkpoint; resource/equipment updates; emergent tasks; equipment OOS; duration overrides; hold-point release updates.
- Original-vs-replanned comparison with isolated counterfactual effects + interaction/residual identified.
- Buffer-consumption early-warning monitoring.

### Phase 6 — Heavier, newer, and domain-specific scope
- GA / ALNS optimization (long-running; progress + cancel; out-of-process seam).
- RAVEN / Monte Carlo risk analysis.
- CCPM buffer insertion + status.
- **Domain-profile mechanism** + outage-specific views: system-state / isolation timeline, hold-point / approval tracking, skill pre-flight, vendor availability.
- Network diagnostics (connectivity, deadlock tracing).
- Rich provenance UI (infrastructure already in Phase 1).
- Baseline versioning + compare different baseline revisions (capability 4b) *(new data model)*.

### Phase 7 — LLM-assisted interpretation & troubleshooting
Interpretation layer on verified outputs (see capability 8). Interpretation tasks first; guardrailed fix suggestions last.

---

## Open questions / to decide later
- Architecture / app structure within Streamlit (state across reruns, the out-of-process seam) — next step, scoped to Phase 1–2.
- Exact ordering of which schema elements editing supports first within Phase 2.
- When the domain-profile mechanism is worth building vs. staying with generic terminology.
