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

### Phase 2 — In-GUI editing (scoped) — ✅ all rows delivered (Increment 1 committed; advanced-pool editors in the working tree, not yet committed)
| Capability | Status | Notes |
|---|---|---|
| Tasks & durations | ✅ | `_render_editor` draft→commit lifecycle |
| Dependencies & lags | ✅ | |
| Basic resources & availability (renewable first) | ✅ | |
| Advanced pools: equipment zone-affinity, consumables, system state, dose-budget resources | ✅ | **The stale-row correction: two of the four already shipped** — consumables (`_render_consumable_form`) and system-states (`_render_system_form`). The remaining two shipped **2026-09-14 (GUI-only wire-up)**: **equipment zone-affinity** (`equipment[].zone_id` + its task-side `tasks[].zone_ids`) and **dose-budget** (`resources[].dose_budget_per_worker_mrem` + task-level `tasks[].dose_rate_mrem_per_hour`). All four fields were already schema-declared, loader-parsed, and on the domain dataclasses; the editors author them as `PatchOp`s onto the raw working tree (dedicated readers + widget blocks in `pages/plan.py`), which the commit path (`build_reference_plan(draft.raw_working_tree)`) and engine adapter read verbatim — **no engine/schema/domain/loader change**. Zone pickers offer only declared `location_id`s; the dose-budget widget gates on the pool's persisted `resource_type == consumable`. **Caveat:** the *typed export* path `serialize_plan_content` remains lossy for these four advanced fields (a **pre-existing** display/download gap, NOT on the run/commit path — the raw snapshot is authoritative), so the editors are fully functional for scheduling; documented, not fixed here. |
| Editing contract (transactional commit, referential integrity, valid export) | ✅ | |

### Phase 3 — Make output trustworthy & readable — ✅ complete (all rows delivered; the inspector's *named* binding-resource attribution is a documented Tier-B follow-up)
| Capability | Status | Where / caveat |
|---|---|---|
| Resource utilization charts | ✅ | Stage A **Plots**, `make_subplots(shared_xaxes=True)` + range slider → aligned x-axes + horizontal scroll (this was a notes_4 layout goal that *is* a Phase-3 item) |
| DAG view **with options** | ✅ | **Delivered 2026-09-11 as Tier 2 (Graphs tab).** The Stage B input-side DAG now enriches into the run-aware view notes_3 meant: nodes colored by float class (red constrained-chain / orange zero-float / green positive), a **Layer by** control (dependency depth / CPM ES / CPM LS), a dashed-red **resource-contention overlay** (the arcs the schedule adds beyond plan precedence), and a rich per-node tooltip (CPM ES/LS/slack labeled "(CPM)", wall-clock start/end, float class + actual TF, CPM-critical / Constrained flags). **Achieved by parity-by-wiring, NOT by calling engine plotting**: the engine's analytics reach the GUI as pure output-DTO fields (`ScheduledActivityDTO.es/ls/cpm_slack_hours`, `ScheduleDTO.contention_edges`) and the existing pure builder (`_activity_graph_enriched`) + lazy-Plotly render helper draw the graph the GUI already owns — so no engine drawing code enters the architecture and the pure path stays stdlib-only. Corrections baked in: the engine's `highlight` param is dead code and the "purple overlap" was fictional; the real `_node_color` rule (constrained-chain → red / actual-TF ≈ 0 → orange / else) is reproduced via the single `classify_float`/`FloatClass` source. Enrichment is gated on the selected run being COMPLETED and lineage-`CURRENT` for the shown schedule; otherwise the structural pre-run graph is drawn with a fallback caption. |
| Per-task drill-down / inspector ("why isn't this starting sooner?") | ✅ | **Delivered 2026-09-14 (Tier A — GUI-only wire-up).** A dedicated **Task inspector** segment on the Results page (third segment beside Plots / Activity DAG): a task selector, a **slip decomposition** (lateness vs CPM early start = the resource-contention portion `delay_hours` + a predecessor/time-window/**calendar** gating remainder, clamped at 0 for calendar rounding), **predecessor/successor slack-attribution** tables (each neighbor's lag, finish-or-start, CPM slack, actual TF, float class) with the **finish-driving predecessor** flagged (argmax `end_hour + lag`), and a clearly-labeled **aggregate** resource-pressure readout (which pools were at/over capacity during the wait — demand ≥ capacity, *not* task-attributed). Built by three **pure** builders (`_task_slip`, `_task_neighbors`, `_saturated_skills` in `view_data.py`, re-exported through `main.py`; render helper `_render_task_inspector` in `pages/results.py`) fusing existing output DTOs (`ScheduledActivityDTO.es/ls/cpm_slack/tf_actual/delay_hours`) with the plan's precedence edges (`_dependency_options`) — **no engine/adapter/DTO/serialization change**. Honesty guards: summary + slip come from `result.schedule` alone (shown regardless of freshness); the neighbor tables are gated on `Freshness.CURRENT` (so the plan's edges match the run); `contention_edges` are NEVER presented as causal blockers, and the aggregate pool readout is explicitly not per-task attribution. **Deferred (Tier B):** the authoritative *named* binding resource / *named* delaying predecessor — the engine computes both in `explain_idle_on_chain_detailed` (`pert.py:5275`) but only `logger.debug`s them; surfacing needs engine→adapter→DTO plumbing. |
| Fitness score with components + **configurable weights** | ✅ | **Delivered 2026-09-14 (UI wire-up).** The composite + component ratios were already shown read-only in the results header; the sidebar now carries a collapsed **Fitness weights (advanced)** expander (four `st.number_input`s, α/β/γ/δ, `min_value=0.0`) that populates `RunConfig.evaluation_weights` via the pure `_evaluation_weights` helper (`view_data.py`, re-exported through `main.py`). The whole substrate below the UI already existed — the field, its canonical hash (`hashing.py`), the `DIFFERENT_CONFIG` freshness it drives, and the adapter's `pert.compute_fitness(α,β,γ,δ)` consumption — so this was a **UI-only insertion**, no engine/adapter/domain/serialization change. Two honesty guards: `_evaluation_weights` returns `None` at the defaults (1.0/0.5/0.3/2.0) so a default run stays hash- and fitness-identical to the no-weights path (feature invisible until used), and the sidebar caption + header echo state these are **post-hoc comparison** weights — they re-score a completed schedule's composite, they do **not** change the GA/ALNS search (a weight change re-runs to the *same* schedule with a *different* composite). The results header echoes the active weights only when the selected run is `CURRENT` and the weights are non-default. |
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
**dependency-violation verdict** (a distinct feasibility output). A later 2026-09-14 increment closed
**configurable fitness weights** — a UI-only insertion over the already-wired `RunConfig.evaluation_weights`
(sidebar expander + `_evaluation_weights` helper + header echo; no engine/adapter/domain change). A later
2026-09-14 increment closed the **last** Phase-3 row the same way — the **per-task inspector** (Tier A:
slip decomposition + predecessor/successor slack attribution + aggregate resource pressure), three pure
builders fusing existing DTOs with the plan's precedence edges, no engine/adapter/DTO change. The
substance still missing is now **Phase-4** — any actual scenario/run comparison. Do **not** read "the
graph/plot exists" as "the phase is done" — for the remaining Phase-4 rows the work is the diagnostics,
not the rendering:

- **Phase 3 gaps (remaining):** none at Tier A — the per-task inspector shipped 2026-09-14 (slip
  decomposition, predecessor/successor slack attribution, aggregate resource pressure), joining the
  options-rich DAG (Tier 2), the CPM-only path view, the dependency-violation output, and configurable
  fitness weights. The one Phase-3 follow-up is **Tier B**: surfacing the engine's authoritative *named*
  binding resource / delaying predecessor (computed in `explain_idle_on_chain_detailed`, logged only —
  needs engine→adapter→DTO plumbing).
- **Phase 4 gaps:** everything analytical — the storage exists (Stage B), the comparison does not.
- **Phase 5:** untouched; overlay fields exist but no replan loop.

---

## Suggested next increment (not yet scoped/approved)

All Phase-3 rows are now **done** — the **per-task inspector** shipped 2026-09-14 (Tier A: slip
decomposition + predecessor/successor slack attribution + aggregate resource pressure), the last of the
row after the options-rich DAG, the CPM-only path view, the dependency-violation output, and
configurable fitness weights. Two candidate follow-ons remain, neither scoped/approved:

- **Phase-4 scenario/run comparison** — side-by-side makespan / disposition across the scenarios we can
  already store (Stage B substrate). Pure analysis, no substrate gap; the largest remaining GUI value,
  and still GUI-only.
- **Tier B for the inspector** — give `explain_idle_on_chain_detailed` (`pert.py:5275`) a structured
  return and thread the authoritative *named* binding resource / delaying predecessor through
  `prism_adapter.py` into new per-task `DiagnosticsDTO`/`ScheduledActivityDTO` fields, then show it in
  the existing inspector. This is the first **non-GUI-only** increment in a while (engine + adapter +
  DTOs), so it carries more risk than the Tier-A wire-ups.

This is a suggestion only — no work is authorized past the per-task-inspector increment.
