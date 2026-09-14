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

### Phase 4 — High-value analysis — 🟡 4.1 comparison view + 4.2 guided augmentation + 4.3 config sweep (priority rule / SGS / seed / **modes**) + chart layer (makespan bars + aligned Gantt) + chain-sets (within-run overlap) delivered; cross-revision row ⬜
| Capability | Status | Where / caveat |
|---|---|---|
| What-if cloning / compare scenarios on same baseline | ✅ *(comparison half)* | **Delivered 2026-09-14 as Phase 4.1 — the keystone run-comparison diff, GUI-only.** A **Compare runs** segment on the Results page (4th beside Plots / Activity DAG / Task inspector) picks **2..N** stored runs and shows their makespan / CPM lower bound / optimism gap / fitness / disposition / status / freshness **side by side**, each row labeled by resolving its `scenario_delta_hash` **forward** — *"match, don't decode"*: provenance hashes are one-way and omit human names, so we hash the live `session.list_scenarios()` into `{hash: name}` and look up (`None` → "Baseline", unmatched/edited-since → `scenario <8-char-hash>`), with each run's full provenance in a collapsed expander for audit. **First and only consumer of `session.list_run_results()`** (which had zero call sites before this). Two pure builders (`_scenario_hash_labels`, `_comparison_rows` in `view_data.py`, re-exported through `main.py`; render helper `_render_run_comparison` in `pages/results.py`) — freshness is computed in the render helper (which owns the `services` dependency) and passed in as a `{run_id: Freshness}` dict so the builders stay `services`/`st`-free; **no engine/adapter/DTO/schema/serialization change**. There is deliberately **no config-summary column**: only the *current* `RunConfig` object is retained (past runs keep only their `run_config_hash`), so a run made under a different config is surfaced honestly by the **freshness** column (`🟠 different config`), never a synthesized past config. Scope was **table only** — the overlaid makespan chart / aligned Gantt landed the same day (see the **Phase-4 chart layer** row). This closes the *comparison* half of the row and lays the reusable diff substrate for 4.2/4.3; the remaining Phase-4 rows (sweeps, augmentation) stay ⬜. |
| Priority-rule sweep | ✅ | **Delivered 2026-09-14 as Phase 4.3 — the second Phase-4 orchestration, GUI-only.** A sweep segment on the Results page (6th beside Plots / Activity DAG / Task inspector / Compare runs / Augment resources) lets the analyst select a set of priority rules (default: the current rule + a classic shortlist `lf/ls/ef/es/duration`, ordered by `PRIORITY_RULES`; **all 22** engine rules selectable) and **one button** re-runs the baseline once per rule, then shows a **ranked leaderboard** (shortest makespan first) of each rule's makespan / optimism gap / fitness / **Δ-vs-best** / freshness, with an `st.success` banner naming the winning rule and its hours saved vs the *current* rule. Where 4.2 varies the **scenario**, 4.3 varies the **RunConfig** (`replace(run_config, priority_rule=rule)`) — the direct generalization of 4.2's clone→rerun→delta loop from a single +N bump to an **N-way fan-out**. Every rule is **actually solved** (verified, not estimated) through the composition root's **one shared store+executor** so run ids increment and never collide (the 4.1/4.2 lesson). Each swept run is a plain **baseline** run (`scenario=None`); its rule is **not** recoverable from the stored `RunResult`/`Provenance` (only the opaque `run_config_hash` is kept — "match, don't decode" applies to configs too), so the sweep tracks `{run_id: rule}` at run time, pins it in session state (survives Streamlit reruns), and feeds a **rule-aware** builder. Swept runs are stored **additively** (`add_run_result` per rule, no `add_scenario`) so they also appear in **Compare runs** — the path for full provenance/cross-diff, keeping this segment focused on the leaderboard (a caption points there). One pure builder (`_sweep_rows` in `view_data.py`, re-exported through `main.py`; render helper `_render_run_sweep` in `pages/results.py`) — freshness is computed in the render helper (owns `services`) and passed in as a `{run_id: Freshness}` dict so the builder stays `services`/`st`-free. The one seam was **generalized** `run_scenario(scenario)` → `run_plan(run_config=None, scenario=None)` (run_config `None` ⇒ the live one; 4.2's two calls now pass `scenario=`); **no engine/adapter/DTO/schema/serialization change** (all 22 rules pre-validated by `validate_run_config`, so no new validation surface). **Generalized the same day** (see the next row): the segment was renamed **Sweep** and given an **axis picker** (priority rule / SGS variant / seed), so the priority-rule sweep is now the first axis of a unified config sweep — `_sweep_rows` gained a `label_key` keyword (default `"priority_rule"`, so this row is untouched behaviorally). The **mode sweep** (the fourth axis — combinatorial per-task modes) landed 2026-09-14 as the **Modes** radio option (see the **Guided mode-sweep** row); deferred still: an "adopt winner" write-back to the sidebar picker (the analyst changes the config themselves after reading the winner); the overlaid makespan bar chart landed the same day (see the **Phase-4 chart layer** row). |
| Chain-sets comparison | ✅ *(within-run half)* | **Delivered 2026-09-14 — the within-run overlap, GUI-only.** A **Chain sets** segment on the Results page (4th, grouped with the single-run views: Plots / Activity DAG / Task inspector / **Chain sets**, before the multi-run Compare / Augment / Sweep) quantifies, for **one** selected COMPLETED run, how its three criticality sets overlap: the **CPM logical critical path** (`ScheduleDTO.cpm_critical_path` — resources ignored), the **resource-constrained chain** (`ScheduleDTO.constrained_chain` — the red chain), and the **zero-float set** (activities with `float_class ∈ {CRITICAL, ZERO_FLOAT}`). The headline is the **CPM-vs-constrained partition** (the engine's own `print_chain_sets_summary` semantics: `both` / `only_cpm` / `only_constrained`): the **`only_constrained` set — on the constrained chain but NOT the CPM path — are the resource leverage points**, tasks critical because of resource contention rather than precedence logic (a classic CPM read misses them; on the bundled `example_10` this names `C106, C110`). Three size metrics, the leverage-point line + partition caption, and a per-task membership table (✓ CPM path / constrained / zero-float + float class + actual TF). **Schedule-only → NO freshness gate** (reads `result.schedule` alone, like the inspector's summary/slip — it fuses in nothing from the *plan's* edges, which is what forces the inspector's neighbor tables to gate on `Freshness.CURRENT`); COMPLETED-run gated exactly like the Task inspector. One **pure** builder — `_chain_sets(schedule)` in `view_data.py` (set arithmetic + `FloatClass` only, re-exported through `main.py`) returning `{cpm, constrained, zero_tf, n_*, only_cpm, only_constrained, both, rows}`; render helper `_render_chain_sets` in `pages/results.py`. Distinct from the DAG (which *draws* the two chains) — this *quantifies* membership + names the leverage points. Everything it needs already rides `ScheduleDTO`/`ScheduledActivityDTO` (`DiagnosticsDTO`'s docstring had reserved the "chain-sets" slot), so **no engine/adapter/DTO/schema/serialization/services/session-API change**. Closes the *within-run* half; deferred: the **cross-run / cross-revision** chain-sets diff (does the constrained chain shift between two runs? — needs the 4b baseline-versioning substrate), and the authoritative *named* binding resource per leverage point (Tier B). |
| Idle-time diagnostics + **verified** resource-augmentation (clone→add→rerun→delta) | ✅ *(augmentation half)* | **Delivered 2026-09-14 as Phase 4.2 — the first Phase-4 orchestration, GUI-only.** An **Augment resources** segment on the Results page (5th beside Plots / Activity DAG / Task inspector / Compare runs) ranks the selected run's resource pools bottleneck-first, lets the analyst pick a pool + increment **N**, and **one button** clones the baseline, reruns it with **+N** crew on that pool, and shows a **verified** before/after delta (makespan / optimism gap / fitness composite, with Δ and Δ%) — both sides *actually re-run*, not estimated. **"+N" = SET the pool to (baseline hour-0 count) + N held flat from hour 0** via `ResourceChange` REPLACE semantics (`_mint_scenario` + `_add_resource_change`); a **time-varying** pool is thereby flattened, surfaced with a caveat. The bump's arithmetic is recomputed against the **freshly-run baseline** (not the possibly-scenario selected run), so the delta is honest regardless of what run was selected. Both runs go through the composition root's **one shared store+executor** (a `run_scenario` seam threaded into the Results page — the page layer stays infrastructure-free; **generalized in 4.3** to `run_plan(run_config=None, scenario=None)`, these calls now pass `scenario=`) so run ids increment and never collide (the 4.1 smoke's lesson); results are stored **additively** (`add_run_result` ×2 + `add_scenario`, never `set_scenario`) and the before/after pair is pinned in session state so the delta survives Streamlit reruns. Two pure builders (`_augmentation_candidates`, `_augmentation_delta` in `view_data.py`, re-exported through `main.py`; render helper `_render_run_augmentation` in `pages/results.py` reusing the **4.1** `_comparison_rows` / `_provenance_rows` in a "Full comparison & provenance" expander) — **no engine/adapter/DTO/schema/serialization change**. The ranking is **aggregate/heuristic** (`demand > 0 and demand ≥ available` over the horizon — the `demand > 0` guard keeps an idle 0-of-0 pool from posing as the top bottleneck; captioned as advisory, NOT the authoritative named binding resource, deferred Tier B). Closes the *augmentation* half of the row; the *idle-time* diagnostics half (authoritative named binding resource) stays with Tier B. |
| Regulatory time-window pre-flight (tested, not global) | ⬜ | |
| Guided mode-sweep / trade-off exploration | ✅ | **Delivered 2026-09-14 as the fourth axis of Phase 4.3's unified Sweep — GUI-only.** A **Modes** option was added to the Sweep axis picker (`st.radio` — now Priority rule / SGS variant / Seed / **Modes**), reusing the same fan-out → leaderboard → makespan-bars pipeline as the three scalar axes. Unlike them, per-task mode selection is a **combinatorial** space, so the branch: reads the current schedule payload (`_current_schedule_payload`), enumerates the multi-mode tasks (`_mode_options` — one row per task with >1 mode), lets the analyst **multiselect which tasks to vary**, and runs the **cartesian product** of *those* tasks' modes, **holding every unselected multi-mode task at its current pick** so a swept run never silently reverts a task to its default mode. Each combination is a **full solve** (the run-config hash already folds `{task_id: mode_name}`, so every distinct combination is a distinct verified run — **zero new validation surface**; `validate_run_config` owns `INVALID_MODE`), so the product is **capped** (`_MODE_SWEEP_CAP = 24`) and the branch **refuses** above the cap (a warning names the count), never truncates. All the combinatorial + label + cap + base-merge logic lives in **one pure builder** — `_mode_sweep_variants(mode_options, selected_task_ids, base_selections, cap)` in `view_data.py` (stdlib `itertools.product` + the `ModeSelection` domain record only; re-exported through `main.py`), returning `{variants: [(label, tuple[ModeSelection])], total, capped, base_label}` where the label names only the swept tasks (e.g. `"C104=crash, C105=fast"`) and the tuple is the full merged selection. The render branch (`_render_run_sweep` in `pages/results.py`) stays thin — it builds `variants` via `replace(run_config, mode_selections=ms)` and hands off to the **unchanged** shared code (the `len(variants) < 2` guard, the run-button fan-out through the shared `run_plan` seam, the pinned `prism_sweep` slot with `label_key="modes"`, the axis-gated leaderboard via `_sweep_rows(..., label_key="modes")`, the winner banner comparing against `base_label`, the makespan bars). Honesty guards: on a plan with **no multi-mode tasks** (every bundled sample — none define modes) the branch shows an info pointing at the Plan editor and returns before touching the builder; base picks are filtered to tasks still in `mode_options` so a stale pick can neither ride into a run nor trip `INVALID_MODE`. **No engine/adapter/DTO/schema/serialization/services/session-API change; `_sweep_rows` untouched** (the `modes` label column + sort tiebreak fall straight out of its existing `label_key` parameter). Deferred: an "adopt winner" write-back to the sidebar mode picker; multi-axis (modes × rule/SGS/seed) sweeps; a bundled multi-mode demo sample (tests synthesize modes inline). |
| Compare different solver configs on same baseline+scenario | ✅ | **Delivered 2026-09-14 as the config-sweep cut of Phase 4.3 — GUI-only.** The 4.3 *Sweep priority rules* segment was **generalized** into a single **Sweep** segment with an **axis picker** (`st.radio` — Priority rule / SGS variant / Seed) rather than bolting on parallel segments: one render helper, one leaderboard, one generalized pure builder. Each axis builds a common `variants: list[(label_str, RunConfig)]` via `replace(run_config, <field>=…)` — **priority rule** (multiselect over all 22, as 4.3), **SGS variant** (multiselect over `[v.value for v in SGSVariant]`, default `max_use_res_ranked/max_use_res_shuffled/first` ∪ the current variant, converted back with `SGSVariant(v)` — mirrors the sidebar), **seed** (`number_input("Number of seeds")` sweeping `[base_seed + i for i in range(n)]`, labels `str(seed)`) — then the **one** button fans out through the shared `run_plan` seam exactly as 4.3, pinning `{run_id: label}` + `label_key` + `axis_label` in session state. Both `sgs` and `seed` feed `run_config_hash` (`hashing.py`), so each distinct value is a **distinct verified run** — the same property the priority-rule axis relies on; **zero new validation surface** (`validate_run_config` validates only `mode_selections` + `priority_rule ∈ PRIORITY_RULES`, never `sgs`/`seed`; `RunConfig` has no `__post_init__`, and the widget bounds — enum choice, `min_value` — are the only guard, matching existing behavior). The pure `_sweep_rows` gained a `label_key` keyword (default `"priority_rule"`) naming **both** the label column and the sort tiebreak, so labels stay strings and the 4.3 tests pass untouched; the leaderboard render is gated on `sweep["label_key"] == label_key` so switching axis never shows a stale cross-axis table (only one `prism_sweep` slot is pinned). **No engine/adapter/DTO/schema/serialization/services/session-API change; `main.py` unchanged** (the seam was already `run_plan(run_config=None, scenario=None)` and `_sweep_rows` re-exported — the added keyword needs no re-export edit). "Match, don't decode" holds per axis: the swept value is never recovered from the stored run's one-way hash. The **mode sweep** (the fourth axis — combinatorial per-task modes) landed 2026-09-14 as the **Modes** radio option (see the **Guided mode-sweep** row), added as one pure builder + one thin branch with no change to this axis's shared code. Deferred still: an explicit seed-*list* input (the N-consecutive-seeds input is the first cut), and an "adopt winner" sidebar write-back (the overlaid makespan bar chart + aligned Gantt landed the same day — see the **Phase-4 chart layer** row). |
| **Phase-4 chart layer** — the deferred visual half of 4.1 / 4.2 / 4.3 | ✅ | **Delivered 2026-09-14 — GUI-only, pure display polish, no new analysis.** Two pure list-of-dict builders in `view_data.py` (re-exported through `main.py`), each taking the same `labeled_results: list[(label, RunResult)]` primitive so **Compare runs / Augment resources / Sweep** feed them from the DTOs they already hold: **`_makespan_bar_rows`** — one stacked-bar row per run (`{label, status, cpm_lower_bound_hours, optimism_gap_hours, makespan_hours, is_best}`), the CPM-floor + optimism-gap segments summing to the makespan (read straight off `schedule`, so the split can never disagree with the total), the shortest makespan(s) flagged `is_best` (ties flag all); and **`_multi_gantt_rows`** — each run's `_gantt_rows` tagged with its `run_label` and concatenated over the shared hour-0 x-axis (every run's times are hour-offsets from project start, so no alignment transform), FAILED/no-schedule runs contributing zero rows. Two lazy-Plotly render helpers in `pages/results.py` (`_render_makespan_bars` — stacked horizontal `go.Bar` traces, `barmode="stack"`, shortest bar outlined green + a "◄ shortest" annotation; `_render_multi_gantt` — `make_subplots(rows=n, shared_xaxes=True)` faceted one subplot per run, bars colored by float class via `_FLOAT_CLASS_COLORS`, bottom-axis range slider — the `_render_plots` idiom, per-run) import Plotly **inside the body** so the modules still import with neither Streamlit nor Plotly present, and degrade to a caption (never an empty figure) when no run has a schedule. Wired: the **makespan bar chart** into all three views; the **aligned multi-run Gantt** into **Compare runs** + **Augment** only (each in a collapsed `st.expander`), **not** the Sweep (a 22-rule fan-out would be unreadable faceted — bars there echo the leaderboard order). **No engine/adapter/DTO/schema/serialization/services/session-API change.** |
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
builders fusing existing DTOs with the plan's precedence edges, no engine/adapter/DTO change. **Phase-4
has now begun the same way:** the **run-comparison view** (4.1, 2026-09-14) is the first *analytical*
capability — it diffs stored `RunResult`s side by side — and it too shipped GUI-only, by consuming the
`list_run_results()` substrate and the metrics already on the output DTOs rather than adding any engine
analysis. **Guided resource-augmentation** (4.2, 2026-09-14) then landed the first *orchestration* on that
substrate — one click clones the baseline, reruns it with +N crew on a chosen bottleneck pool, and diffs
the two real solves — reusing the existing clone (`_mint_scenario` / `_add_resource_change`), run
(`run_pipeline`), and 4.1 diff builders behind a single composition-root `run_scenario` seam, again with
**no engine/adapter/DTO change**. **Config sweep** (4.3, 2026-09-14) is the second orchestration: it
generalizes that seam to `run_plan(run_config=None, scenario=None)` and fans the loop out **N ways** —
re-running the baseline once per value of **one chosen RunConfig axis** (`replace(run_config, <field>=…)`)
and ranking the real solves in a leaderboard — where 4.2 varies the scenario, 4.3 varies the RunConfig. It
shipped in two same-day cuts: first the **priority-rule** axis, then a **generalization** into a single
**Sweep** segment with an **axis picker** (priority rule / SGS variant / seed) — one render helper, one
leaderboard, one `label_key`-parameterized `_sweep_rows`; again **no engine/adapter/DTO change** (both `sgs`
and `seed` already feed `run_config_hash`, so each value is a distinct verified run and there is zero new
validation surface). Do **not** read "the graph/plot exists" as "the phase is done" —
for the *remaining* Phase-4 rows the work is the diagnostics, not the rendering:

- **Phase 3 gaps (remaining):** none at Tier A — the per-task inspector shipped 2026-09-14 (slip
  decomposition, predecessor/successor slack attribution, aggregate resource pressure), joining the
  options-rich DAG (Tier 2), the CPM-only path view, the dependency-violation output, and configurable
  fitness weights. The one Phase-3 follow-up is **Tier B**: surfacing the engine's authoritative *named*
  binding resource / delaying predecessor (computed in `explain_idle_on_chain_detailed`, logged only —
  needs engine→adapter→DTO plumbing).
- **Phase 4 gaps:** the **comparison view (4.1)**, **guided resource-augmentation (4.2)**, and the
  **config sweep (4.3 — priority rule / SGS / seed / modes)** are delivered — the storage (Stage B) now has
  its diff, the first orchestration that generates a run set to diff, and the N-way fan-out that ranks a run
  set across **all four** RunConfig axes (the fourth, **modes**, cartesian over selected multi-mode tasks,
  landed 2026-09-14 as one pure builder `_mode_sweep_variants` + one thin `Sweep` branch — no change to the
  axis's shared code) — and their deferred **visual half** landed the same day as the **Phase-4
  chart layer** (an overlaid makespan bar chart across all three views + an aligned multi-run Gantt on
  Compare / Augment; GUI-only, pure display polish, no new analysis). The **chain-sets comparison** row's
  *within-run* half landed 2026-09-14 too (the **Chain sets** segment — one pure builder `_chain_sets` +
  one render helper — quantifying the CPM-path / constrained-chain / zero-float overlap for a single run
  and naming the resource-only leverage points; schedule-only, GUI-only). What remains is the *rest* of the
  orchestration: the **cross-run / cross-revision** chain-sets diff and the cross-revision (4b) comparison
  that need baseline versioning (Phase 6); plus the **idle-time** half of the augmentation row — the
  authoritative *named* binding resource — which is Tier B (engine→adapter→DTO plumbing), not GUI-only. The 4.1 diff is the
  reusable substrate every remaining comparison reduces to; 4.2 is the reusable clone→rerun→delta loop, and
  4.3 generalized its seam to `run_plan` and fanned it out over an axis picker — the pattern the mode sweep
  reused as its fourth axis.
- **Phase 5:** untouched; overlay fields exist but no replan loop.

---

## Suggested next increment (not yet scoped/approved)

All Phase-3 rows are **done**; Phase-4 has delivered **4.1, the run-comparison view** (2026-09-14,
GUI-only) — the Results-page **Compare runs** segment diffs 2..N stored runs side by side, resolving each
run's scenario forward from the live scenarios ("match, don't decode") — **4.2, guided resource-augmentation** (2026-09-14, GUI-only) — the **Augment resources** segment clones the baseline, reruns it
with +N crew on a chosen bottleneck pool, and shows the verified before/after delta — and **4.3, the
config sweep** (2026-09-14, GUI-only) — the **Sweep** segment with an **axis picker** (priority rule / SGS
variant / seed / **modes**) re-runs the baseline once per value of the chosen RunConfig axis and ranks the
real solves in a leaderboard. 4.1 is the **reusable diff substrate** the rest of the phase's comparisons
reduce to; 4.2 is the **reusable clone→rerun→delta loop**, and 4.3 generalized its seam to
`run_plan(run_config=None, scenario=None)` and fanned it out **N ways** over an axis picker — the pattern
the mode sweep reused as its fourth axis. Candidate follow-ons, none scoped/approved:

- **Chain sets (within-run overlap)** — ✅ **delivered 2026-09-14** (GUI-only): the **Chain sets** Results
  segment quantifies, for one selected COMPLETED run, how the CPM logical critical path, the resource-
  constrained chain, and the zero-float set overlap (the engine's `print_chain_sets_summary` partition:
  both / only-CPM / only-constrained), naming the **`only_constrained`** tasks as the resource leverage
  points (critical from contention, not precedence — invisible to a classic CPM read). One pure builder
  `_chain_sets` (`view_data.py`, re-exported) + one render helper `_render_chain_sets` (`pages/results.py`);
  schedule-only (no freshness gate), COMPLETED-run gated like the inspector. No engine/adapter/DTO/schema/
  serialization/services/session-API change. See the **Chain-sets comparison** row above. Deferred: the
  **cross-run / cross-revision** chain-sets diff (needs the 4b baseline-versioning substrate).
- **Mode sweep (the fourth 4.3 axis)** — ✅ **delivered 2026-09-14** (GUI-only): the **Modes** option on
  the Sweep axis picker fans the same `run_plan` loop out over the **cartesian product** of the analyst's
  selected multi-mode tasks' modes (capped at 24, refuse-not-truncate), holding unselected multi-mode tasks
  at their current pick. All combinatorial + label + cap + base-merge logic in one pure builder
  `_mode_sweep_variants` (`view_data.py`, re-exported); the branch feeds the same `_sweep_rows` leaderboard
  (`label_key="modes"`) + makespan bars. No engine/adapter/DTO/schema/serialization/services/session-API
  change; `_sweep_rows` untouched. `replace(run_config, mode_selections=…)`. See the **Guided mode-sweep**
  row above. Deferred: an "adopt winner" write-back to the sidebar mode picker; multi-axis (modes × rule)
  sweeps.
- **4.1/4.2/4.3 chart layer** — ✅ **delivered 2026-09-14** (GUI-only): the deferred visual half — an
  overlaid makespan bar chart across all three views (Compare / Augment / Sweep) + an aligned multi-run
  Gantt on Compare / Augment. Reused the existing selection + row builders; two pure builders
  (`_makespan_bar_rows` / `_multi_gantt_rows`) + two lazy-Plotly render helpers. Pure display polish, no new
  analysis. See the **Phase-4 chart layer** row above.
- **Multi-pool / layered augmentation** — extend 4.2 beyond a single-pool bump on the baseline: augment two
  pools at once, or layer +N over an *existing* scenario run (deferred in 4.2 because "match, don't decode"
  means a past run's scenario can't be reconstructed from its hash). GUI-only.
- **Tier B for the inspector / augmentation** — give `explain_idle_on_chain_detailed` (`pert.py:5275`) a
  structured return and thread the authoritative *named* binding resource / delaying predecessor through
  `prism_adapter.py` into new per-task `DiagnosticsDTO`/`ScheduledActivityDTO` fields, then show it in the
  inspector and use it to replace 4.2's **heuristic** bottleneck ranking with the true binding pool. This
  is the first **non-GUI-only** increment in a while (engine + adapter + DTOs), so it carries more risk
  than the Tier-A / 4.1 / 4.2 / 4.3 wire-ups.

This is a suggestion only — no work is authorized past the Phase-4 **chain sets** (within-run overlap,
delivered 2026-09-14; it followed the Phase-4 mode-sweep increment, itself after the chart-layer increment).
