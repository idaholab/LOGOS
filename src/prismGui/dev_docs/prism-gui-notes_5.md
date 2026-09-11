# PRISM GUI — Phase ↔ Stage reconciliation & status

Single status map reconciling the two planning documents that were drifting apart:

- **`prism-gui-notes_3.md`** — *capabilities* organized into **Phases 1–7** (a construction
  sequence: what to build, in what order).
- **`prism-gui-notes_4.md`** — *layout/structure* organized into **Stages A–B** (a page-flow
  refactor: tabs, selectors, graphs).

These are **two different axes**, not two names for the same plan. The phases sequence *capability*;
the stages sequence *UI structure*. They intersect wherever a layout goal in notes_4 happens to be a
capability that notes_3 files under a phase — which is exactly what caused the confusion this doc
resolves. Nothing below changes either source doc; this is the reconciled view.

Only one item was ever formally reconciled between the two docs: the **baseline→scenarios relation
graph**, which notes_4 (lines 56–60) correctly moved *out* of notes_3 "Phase 6" (which presupposes
baseline versioning) and *into* Stage B, because it is a derivable star from each scenario's
`base_plan_hash` and needs no new persistence. That reclassification was sound. The rest of the
intersections below happened incidentally and had not been written down until now.

---

## Delivered so far (git history + current working tree)

| Unit | Where | State |
|---|---|---|
| **Phase 1** — MVP spine + provenance | commit `53236e9` | committed |
| **Phase 2, Increment 1** — in-GUI editing lifecycle | commit `d7353f1` | committed |
| **Stage A** — top-level tabs / aligned plots / results header / horizon knob / validation badge | working tree | **uncommitted** |
| **Stage B** — multi-scenario storage + selector + edit-target-follows-selection + relation graph + activity graph + mode picker | working tree | **uncommitted** (implemented & verified 2026-09-10) |

Stage A and Stage B are unstaged/uncommitted at the time of writing — the user performs all commits.

---

## Reconciliation table — every notes_3 phase item → current status

Legend: ✅ done · 🟡 partial (display only / missing options) · ⬜ not started · **(substrate)** =
the storage/navigation a capability needs exists, but the capability itself does not.

### Phase 1 — Minimum viable spine (+ provenance) — ✅ complete (committed)
| Capability | Status | Notes |
|---|---|---|
| Load JSON + schema validation (readable errors) | ✅ | via validation port/adapter |
| Run: choose SGS + priority rule | ✅ | sidebar `_pick_run_config` |
| Makespan vs CPM lower bound (optimism gap) | ✅ | results header, `main.py:730-732` |
| Gantt + schedule table + CSV export | ✅ | Stage A relocated these into **Plots** |
| Disposition summary (Ready / Warnings / Blocked) + 6 indicators | ✅ | `_disposition_rows`, `main.py:634` |
| Provenance: input snapshot, run id, rule/SGS/seed/version, staleness | ✅ | freshness shown in results header |
| Sample project guided load (`example_10` primary) | ✅ | |

### Phase 2 — In-GUI editing (scoped) — 🟡 Increment 1 committed, later increments pending
| Capability | Status | Notes |
|---|---|---|
| Tasks & durations | ✅ | `_render_editor` draft→commit lifecycle |
| Dependencies & lags | ✅ | |
| Basic resources & availability (renewable first) | ✅ | |
| Advanced pools: equipment zone-affinity, consumables, system state, dose-budget resources | ⬜ | notes_3 Phase 2 "later increments" |
| Editing contract (transactional commit, referential integrity, valid export) | ✅ | |

### Phase 3 — Make output trustworthy & readable — 🟡 partial (mostly *display*, little *diagnostics*)
| Capability | Status | Where / caveat |
|---|---|---|
| Resource utilization charts | ✅ | Stage A **Plots**, `make_subplots(shared_xaxes=True)` + range slider → aligned x-axes + horizontal scroll (this was a notes_4 layout goal that *is* a Phase-3 item) |
| DAG view **with options** | 🟡 | **Two complementary views — corrected 2026-09-10.** (a) Stage B **Graphs** ships an *input-side* activity/dependency DAG (pure-Python layered layout, Plotly pan/zoom/hover) — available before any run, no chain/contention coloring. (b) The options-rich DAG notes_3 meant **already exists in the engine**: `Pert.plot_activity_dag()` (`src/CPM/pert.py:7141`) with backend (`library`), chain-highlight (`highlight` = cpm/constrained/both), layout (`layer_by` = es/ls/topo), and contention arcs (`include_augmented_edges`). It is an **OUTPUT** view (reads post-schedule analytics: `getCriticalPathSymbolic`, `constrained_chain_list`, `actual_zero_tf_set`) and is **not yet surfaced in the GUI**. Remaining work is *wiring, not building*: call it via the adapter after a run, pass `library='plotly'` (pyvis is **not** installed in this env), take the returned Figure, place it on the output/Plots side. Demoed in `doc/demos/rcpsp/outage_demo/npp_outage_demo.ipynb` (Figure 17) and `.../examples/test_pert_res_full.ipynb`. |
| Per-task drill-down / inspector ("why isn't this starting sooner?") | ⬜ | activity-graph hover shows id / duration / depth only |
| Fitness score with components + **configurable weights** | 🟡 | composite + makespan_ratio shown read-only (`main.py:746-749`); **no** configurable α/β/γ/δ, no advanced "schedule evaluation" section, no non-optimization caveat |
| CPM-only baseline view (logical critical path) | ⬜ | only the CPM lower-bound *number* is shown, not the `getCriticalPath()` path view |
| Dependency-violation check as a distinct output | ⬜ | not surfaced (the disposition indicators from Phase 1 give a coarse feasibility flag, not the `check_dependency_violations()` structured output) |

### Phase 4 — High-value analysis — 🟡 substrate only, no analysis delivered
| Capability | Status | Where / caveat |
|---|---|---|
| What-if cloning / compare scenarios on same baseline | **(substrate)** | Stage B's multi-scenario storage + selector + relation graph is the *storage & navigation* Phase-4 comparison needs. We can hold many scenarios and see how they relate to the baseline — but we **cannot yet diff their run results** (no side-by-side makespan comparison). |
| Priority-rule sweep | ⬜ | |
| Chain-sets comparison | ⬜ | |
| Idle-time diagnostics + **verified** resource-augmentation (clone→add→rerun→delta) | ⬜ | |
| Regulatory time-window pre-flight (tested, not global) | ⬜ | |
| Guided mode-sweep / trade-off exploration | ⬜ | (note: Stage B added a mode *picker* — the input control — not a mode *sweep* analysis) |
| Compare different solver configs on same baseline+scenario | ⬜ | |
| Compare different baseline **revisions** (4b) | ⬜ | needs baseline versioning (Phase 6) |

### Phase 5 — Replanning — ⬜ not started
| Capability | Status | Where / caveat |
|---|---|---|
| Checkpoint; resource/equipment updates; emergent tasks; equipment OOS; duration overrides; hold-point release updates | ⬜ | The scenario overlay model *has fields* (`emergent_tasks`, `emergent_dependencies`, `hold_point_release_overrides`, `equipment_changes`, `resource_changes`, `duration_overrides`) and the what-if panel edits some — but there is **no checkpoint/replan workflow** driving `replan()`. |
| Original-vs-replanned comparison (isolated counterfactuals + residual) | ⬜ | |
| Buffer-consumption early-warning monitoring | ⬜ | |

### Phases 6–7 — ⬜ not started
GA/ALNS optimization, RAVEN Monte Carlo, CCPM buffers, domain-profile mechanism + outage-specific
views, network diagnostics, rich provenance UI, baseline versioning, LLM interpretation. None
touched. (The relation graph is *not* the Phase-6 edit-history/lineage-across-revisions graph — it is
the cheaper baseline→scenarios star, see the reconciliation note at the top.)

---

## The one caveat to carry forward: *display* ≠ *analysis*

Stage A/B delivered the **readable-output shell** of several Phase-3/4 capabilities (utilization
charts, the activity DAG, a fitness readout, multi-scenario navigation) **without** their analytical
substance (DAG options, per-task "why", dependency-violation output, CPM-only path, and any actual
scenario/run comparison). Do **not** read "the graph/plot exists" as "the phase is done." Concretely,
when Phase 3/4 is picked up next, the remaining work is the diagnostics, not the rendering:

- **Phase 3 gaps:** per-task inspector, configurable fitness weights, CPM-only path view,
  dependency-violation output. (The options-rich DAG is **not** a gap to build — it exists as
  `Pert.plot_activity_dag()`; it needs output-side *wiring* only, plotly-only in this env.)
- **Phase 4 gaps:** everything analytical — the storage exists (Stage B), the comparison does not.
- **Phase 5:** untouched; overlay fields exist but no replan loop.

---

## Suggested next increment (not yet scoped/approved)

The cheapest high-value follow-on that builds directly on Stage B's substrate is **Phase-4
scenario/run comparison** (side-by-side makespan/disposition across the scenarios we can now store),
optionally paired with **surfacing the engine's `plot_activity_dag()`** on the output side (chain /
contention coloring — wiring, not building) and **dependency-violation output**, to close the
"display without diagnostics" gap. This is a suggestion only — no work is authorized past Stage B.
