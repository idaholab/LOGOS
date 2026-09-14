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
| **Stage A** — top-level tabs / aligned plots / results header / horizon knob / validation badge | commit `cf99139` | committed |
| **Stage B** — multi-scenario storage + selector + edit-target-follows-selection + relation graph + activity graph + mode picker | commit `cf99139` | committed (implemented & verified 2026-09-10) |
| **Phase 3 / Tier 2** — run-aware activity DAG: float-class node color, CPM ES/LS/topo layout control, resource-contention overlay, rich per-node tooltip | working tree | **committed 2026-09-11** (implemented & verified) |
| **Restructure Phase 1** — workflow multipage nav (`st.navigation` + 4 pages: Plan/Results/Replan/Scenarios) | commit `392fe14` | committed (layout-only; 232 tests green) |
| **Restructure Phase 2** — code split: `app/main.py` (~3,892 L) → cohesive `app` submodules (`_streamlit`, `pipeline`, `edit_model`, `scenario_model`, `view_data`, `session`, `sidebar`, `components`, `pages/{plan,results,replan,scenarios}`) + thin 246-L composition-root `main.py` that re-exports the pure surface | working tree | **implemented & verified 2026-09-11** (verbatim lift-and-shift: 187/187 nodes AST-identical, orphan `_render_disposition_panel` dropped; 232 tests green; **not yet committed**) |

Stage A and Stage B were committed in `cf99139`; Tier 2 is committed on top (this change). The user
performs all commits — nothing is committed without fresh explicit authorization.

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
| DAG view **with options** | ✅ | **Delivered 2026-09-11 as Tier 2 (Graphs tab).** The Stage B input-side DAG now enriches into the run-aware view notes_3 meant: nodes colored by float class (red constrained-chain / orange zero-float / green positive), a **Layer by** control (dependency depth / CPM ES / CPM LS), a dashed-red **resource-contention overlay** (the arcs the schedule adds beyond plan precedence), and a rich per-node tooltip (CPM ES/LS/slack labeled "(CPM)", wall-clock start/end, float class + actual TF, CPM-critical / Constrained flags). **Achieved by parity-by-wiring, NOT by calling engine plotting**: the engine's analytics reach the GUI as pure output-DTO fields (`ScheduledActivityDTO.es/ls/cpm_slack_hours`, `ScheduleDTO.contention_edges`) and the existing pure builder (`_activity_graph_enriched`) + lazy-Plotly render helper draw the graph the GUI already owns — so no engine drawing code enters the architecture and the pure path stays stdlib-only. Corrections baked in: the engine's `highlight` param is dead code and the "purple overlap" was fictional; the real `_node_color` rule (constrained-chain → red / actual-TF ≈ 0 → orange / else) is reproduced via the single `classify_float`/`FloatClass` source. Enrichment is gated on the selected run being COMPLETED and lineage-`CURRENT` for the shown schedule; otherwise the structural pre-run graph is drawn with a fallback caption. |
| Per-task drill-down / inspector ("why isn't this starting sooner?") | 🟡 | **partial (2026-09-11):** the enriched DAG tooltip now answers much of "why" per node — CPM ES/LS/slack, actual TF, float class, and CPM-critical / Constrained flags. A dedicated inspector panel (predecessor/successor slack attribution, binding resource) is still ⬜. |
| Fitness score with components + **configurable weights** | 🟡 | composite + makespan_ratio shown read-only (`main.py:746-749`); **no** configurable α/β/γ/δ, no advanced "schedule evaluation" section, no non-optimization caveat |
| CPM-only baseline view (logical critical path) | ✅ | **Delivered 2026-09-14 (display wire-up).** The `getCriticalPath()` path now rides `ScheduleDTO.cpm_critical_path` and is surfaced two ways for a COMPLETED run: an ordered `A → B → C` readout under the CPM lower-bound metric in the results header (`_cpm_path_label`), **and** a solid-gold edge trace along the path in the enriched Activity DAG (`_cpm_path_edges` → `cpm_path_edges` in `_activity_graph_enriched`, drawn beneath the nodes with an extended legend). This is the *logical* critical path (resources ignored) — **distinct** from the resource-constrained chain the DAG/Gantt already color red. Pure builders (stdlib-only, in `view_data.py`, re-exported through `main.py`); no engine/adapter/DTO change. |
| Dependency-violation check as a distinct output | ✅ | **Delivered 2026-09-14 (display wire-up).** `pert.check_dependency_violations()` already rode `DiagnosticsDTO.dependency_violations`; the results header now surfaces it as its **own verdict** (`_render_dependency_violations`) — a clean run states `✅ No dependency violations.` explicitly, violations get a `⛔ n dependency violation(s)` headline + a focused issue list in an open expander. (These same issues also remain, undifferentiated, in the full "Audit findings" expander because they're concatenated into `result.issues`; the new section is the feasibility-focused view.) No engine/adapter/DTO change. |

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
substance. Tier 2 (2026-09-11) has since closed the **DAG-options** gap — the activity DAG is now
run-aware (float-class color, CPM ES/LS/topo layout, resource-contention overlay, CPM-timing tooltip),
delivered by wiring the engine's analytics through output DTOs rather than rendering an engine figure.
A 2026-09-14 increment closed two more Phase-3 rows the **same way** — by surfacing DTOs the adapter
already populated: the **CPM-only (logical) critical path** (readout + gold DAG trace) and the
**dependency-violation verdict** (a distinct feasibility output). The substance still missing: the
full per-task "why" inspector and configurable fitness weights, plus any actual scenario/run
comparison. Do **not** read "the graph/plot exists" as "the phase is done" — for the remaining rows
the work is the diagnostics, not the rendering:

- **Phase 3 gaps (remaining):** configurable fitness weights, and the full per-task inspector (the
  Tier 2 DAG tooltip now covers per-node "why" — CPM slack, TF, flags — but not predecessor/successor
  attribution). The options-rich DAG (Tier 2), the CPM-only path view and the dependency-violation
  output (2026-09-14) are **done**.
- **Phase 4 gaps:** everything analytical — the storage exists (Stage B), the comparison does not.
- **Phase 5:** untouched; overlay fields exist but no replan loop.

---

## Suggested next increment (not yet scoped/approved)

The two cheapest display-only Phase-3 wins — **dependency-violation output** and the **CPM-only path
view** — are now **done** (2026-09-14), leaving two Phase-3 rows that need real diagnostics rather than
a DTO wire-up: **configurable fitness weights** (moderate — the adapter already consumes
`evaluation_weights` at run time via `_build_fitness(pert, weights)`, so this is a run-config control
+ re-run, not display-only) and the **full per-task inspector** (largest — predecessor/successor slack
attribution + binding-resource identification, substrate only partial). The other high-value follow-on
is **Phase-4 scenario/run comparison** (side-by-side makespan/disposition across the scenarios we can
now store) — pure analysis, no substrate gap. This is a suggestion only — no work is authorized past
the 2026-09-14 increment.
