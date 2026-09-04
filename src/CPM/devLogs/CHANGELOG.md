# CPM Module Changelog

## [Unreleased]

### Correctness — replan partial-CPM recomputes priority metrics (RP-l) (2026-09-03, round 2)

Finding RP-l (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`). `_generate_info_from`
— the partial CPM pass replanning runs after injecting activities — recomputed
ES/EF/LS/LF/slack but **omitted the priority-metric block** that `generateInfo`
runs (`calculate_total_successors/…_predecessors/…_rank_position_weight/
…_resource_demand/…_resource_requirement/calculate_gp_rules`). Because the
inject path also runs `resetInitialGraph() → resetInfo()`, which zeroes the
structural metrics (mts/mtp/grpw/grd/rr/…) and never creates the custom-heuristic
keys (`mehh_*`, `gphh_b`) at all, after a replan-injection the infoDict was left
with stale-zero structural metrics **and missing heuristic keys**. This was
catalogued as latent, but it is in fact reachable through the public API: the
sequence `calculateScheduleWithResources()` → `replan(new_activities=[…])` →
`calculateScheduleWithResources(priority_rule=<rule>)` **raises
`KeyError('mehh_8000_b')`** for any of the 13 rules whose `priority_calculation`
tie-breaker reads that key (the 5 basic `lf/ls/ef/es/duration` and the 8
structural `mts/mtp/grpw/grd/rr/avgrr/maxrr/minrr`); the remaining
`random/wcs/acs/irsm` and the default `TF_based`/`external` paths are unaffected.
Fix: `_generate_info_from` now runs the same six metric calls after its CPM /
time-window pass (metrics are timing-independent, so recomputing them over the
full post-injection graph is correct in the replan context). Repro:
`repros/repro_rpl_replan_metrics.py` (function-level defect + public two-call
crash). Regression: `test_bugfix_regressions.py::TestReplanRecomputesPriorityMetrics`
— two detectors, mutation-verified (dropping the grpw call reddens the
stale-metric test only; dropping `calculate_gp_rules` reddens the public-crash
test only). Full CPM suite: 898 passed.

### Correctness — augmented-graph location arcs cover all overlapping pairs (B5) (2026-09-03, round 2)

Finding B5 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`). `_build_augmented_graph`
adds a serialization arc between two tasks that overlap at a `max_tasks == 1`
zone (the resource-flow dependency the constrained critical chain must reflect),
but the per-zone scan visited only **consecutive** start-sorted pairs. When a
long activity spans a short one and then overlaps a third that the short one does
*not* reach — e.g. `A[0,10]`, `B[1,2]`, `C[3,13]` at the same single-task zone —
the arc `A→B` is added but `B→C` is not (they don't overlap), so the genuinely
binding `A↔C` pair gets **no arc, directly or transitively**. The
constrained-critical-chain / actual-total-float analytics (`_compute_actual_tf_proxy`,
the only consumer) then under-connect the graph. Fix: the location block now
scans **all** pairs per zone (with an early `break` once a later task starts at/
after the current task's end, since the list is start-sorted — so the common
well-separated case stays near-linear). Arc direction is unchanged
(earlier-start → later-start), so the augmented graph stays acyclic.

**Latent behind a correct scheduler.** The block only fires when two tasks
*overlap* at a `max_tasks == 1` zone, which a correct SGS never produces (probe:
three single-zone activities schedule back-to-back, `A[0,10] C[10,20] B[20,21]`,
zero overlaps). So the defect could surface only if a future scheduler
regression let such an overlap through — this fix is defense-in-depth for the
analytics, matching `_build_augmented_graph`'s own docstring ("Location ordering
when max_tasks == 1 AND two tasks overlap"). Repro:
`repros/repro_b5_locpairs.py` (function-level; sets activity times directly).
Regression: `test_bugfix_regressions.py::TestAugmentedGraphSerializesAllZonePairs`
— detector + control, mutation-verified (restricting the scan to the adjacent
neighbor reddens the non-adjacent-overlap test while the control holds). Full
CPM suite: 896 passed.

### Correctness — Serial SGS now applies skill substitution (SC3) (2026-09-03, round 2)

Finding SC3 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`) — previously confirmed
and deferred (see the SC-m1 entry below), now fixed. The Serial SGS feasibility
check counted only the exact declared skill, so a substitution-feasible activity
(primary skill short, but an `alternative_skill_types` skill available) was
over-delayed or dropped on the serial path even though the parallel path
schedules it. The reason it was deferred: a check-only fix would open an
over-commit hole — two overlapping activities could both draw the same
alternative-skill unit, because the serial commit recorded only `(act,start,end)`
with no substitution-resolved breakdown. The fix closes both halves, mirroring
the parallel path (`_fits_with_tentative` / `_apply_tentative`):

- **Check side.** `_serial_check_feasibility` now, for each required skill whose
  net availability is short, draws the shortfall from `alternative_skill_types`
  before declaring infeasibility (parity with the parallel per-hour check).
- **Commit + consumption side.** Two new helpers resolve and account the
  substitution. `_resolve_serial_consumption(activity, start, profile)` computes
  the `{skill: workers}` breakdown actually drawn (primary first, then
  alternatives), mirroring `_apply_tentative`; the commit path stores it on
  `activity._actual_resources_for_start`. `_serial_consumed_at(skill, h, profile)`
  — the point-in-time consumption used by every feasibility sum — now reads that
  resolved breakdown when present (falling back to declared requirements
  otherwise), so a borrowed alternative-skill unit is charged against later
  overlapping activities. The dose check likewise consumes the resolved
  breakdown. This makes the serial path both schedule the substitution-feasible
  activity *and* refuse to over-commit the shared skill.

`_serial_dose_worker_map` is superseded by `_resolve_serial_consumption` (it
reduces to the same map when no substitution occurs) and is marked deprecated;
it is no longer called by the scheduler. Repro: `repros/repro_sc3_serialsub.py`
now prints "NOT REPRODUCED (fixed)". Regressions:
`test_bugfix_regressions.py::TestSerialAppliesSkillSubstitution` — two tests,
mutation-verified in isolation (disabling the check fallback reddens the
"schedules substitution" test; making consumption ignore the resolved breakdown
reddens the "does not over-commit" test while the first stays green). Full CPM
suite: 894 passed.

### Correctness — `check_dependency_violations` precedence tolerance (SC-m1) (2026-09-03, round 2)

Finding SC-m1 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`). `Pert.check_dependency_violations`
compared `succ_start < pred_end + lag` **strictly**, with no time tolerance,
while the authoritative `schedule_validator._check_precedence` allows a 1-minute
grace (`_PREC_TOL = 60 s`). The two feasibility surfaces therefore disagreed at
the margin: a sub-minute gap — the kind that hour→`timedelta` float arithmetic
can produce — was reported **infeasible** by `check_dependency_violations()` yet
**feasible** by `validate_schedule()`. Confirmed with a repro (B started 30 s
before A's finish: `dep_feasible=False`, `val_feasible=True`). Fix:
`check_dependency_violations` now imports and applies the same `_PREC_TOL`
(`succ_start < required_start - _PREC_TOL`), so the two agree; genuine
violations (well beyond the grace) are still flagged. Single source of truth —
the constant is imported from `schedule_validator`, not duplicated. Repro:
`repros/repro_scm1_prectol.py`. Regression:
`test_bugfix_regressions.py::TestDependencyCheckPrecTolerance`. Full CPM suite:
892 passed.

Two sibling findings from the same cluster were **investigated and not fixed**:
- **SC-m2 (refuted, not a bug):** `priority_calculation`'s `'minrr'` rule sorts
  greatest-first (`reverse=True`) like its siblings `rr`/`avgrr`/`maxrr`. All
  four are aggregations of the same resource-requirement vector (min/avg/max/
  count over skills); `minrr` names the *aggregation*, not a "least-first" sort
  intent, and is consistent with the standard greatest-resource-demand family.
  No inverted direction — no change.
- **SC3 (confirmed, deferred at the time — since fixed):** the Serial SGS
  feasibility check performed no skill substitution (the parallel
  `_fits_with_tentative` draws a shortfall from `alternative_skill_types`), so a
  substitution-feasible activity was over-delayed or dropped on the serial path
  (repro: `repros/repro_sc3_serialsub.py` — serial drops an ELEC activity that
  borrows MECH; parallel schedules it). This made the serial path
  **over-conservative, never unsafe** (it could not over-commit). A correct fix
  needs substitution modeled in *both* the serial check and the serial
  commit/consumption (the commit recorded only `(act,start,end)`, no resolved
  breakdown); a check-only fix would open an over-commit hole. **Now fixed** —
  see the "Serial SGS now applies skill substitution (SC3)" entry above.

### Correctness — time-varying availability sampled at startTime (B4 + M-1) (2026-09-03, round 2)

Findings B4 and M-1 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`) — two members
of the same family as C2/C2b: a decision that depends on a *time-varying* pool
sampled availability at `self.startTime` instead of the time that actually
matters. Both were catalogued as *plausible*; both are now **confirmed with
behavioral repros** and fixed.

**B4 — `_build_augmented_graph` resource-binding skip gate.** The O(n²) pair
scan that adds resource-flow arcs (for the CCPM resource-constrained chain) is
guarded by a cheap skip gate: `2·max_demand ≥ avail` for some skill/equipment.
That gate read `get_availability(skill, startTime)`, but the actual per-pair
binding test reads `get_availability(skill, overlap_start)`. When a pool dips
*below* its startTime level during an overlap, the gate could close on the
higher startTime value and skip the whole scan — dropping a genuine binding arc
between a saturating overlapping pair, so the resource-constrained chain came
out too short. Repro: `avail 5` at startTime, `4` during the overlap; a pair
demanding `2+2=4` saturates the pool at overlap (binding) but `4 < 5` closes the
gate. Fix: the gate now samples the **minimum** availability over the horizon
actually spanned by the scheduled activities (`get_availability_in_range` /
`get_capacity_in_range`, which return the per-interval minimum), so it is
conservative and never wrongly closes. The hot per-pair test is unchanged.

**M-1 — `_rank_by_value_top_k` cutoff.** The ranked SGS estimates the number of
startable slots this step (`max_slots`) to size the `heapq.nlargest(k)` cutoff
(`k = max_slots·8`). It estimated `max_slots` from `get_availability(s,
startTime)`. When availability *grows* after startTime, `max_slots` is
under-counted and the top-k truncates candidates that are placeable **now**,
deferring them to a later event. Confirmed behavioral impact (not just a
reporting quirk): 20 activities gated by a predecessor become ready at h=10 when
`MECH` has risen 1→30; the startTime estimate (`max_slots=1`, `k=8`) launched
them in 3 waves (8/8/4) for a **25 h makespan vs. the optimal 15 h**. Fix:
`_rank_by_value_top_k` now takes the current `time_index` and samples
availability there; `_schedule_generation_scheme` passes it. Over-counting
`max_slots` is safe (the hard `_fits_with_tentative` check still bounds
placement); under-counting was the defect.

**Regression tests** — `test_bugfix_regressions.py` gained
`TestAugmentedGraphSkipGateHorizon` (B4: binding arc added when the pool dips
below startTime + a no-spurious-arc control) and
`TestTopKUsesCurrentTimeAvailability` (M-1: all 20 launch in one wave, makespan
15 h). Mutation-verified independently: reverting the skip gate to startTime
sampling reddens only the B4 arc test; reverting the cutoff to startTime reddens
only the M-1 makespan test (25 h returns). All green after.

**Repros** — `devLogs/repros/repro_b4_skipgate.py` (new) and
`repro_m1_topk.py` (new) both print "NOT REPRODUCED".

**Files changed**
- `pert.py` — `_build_augmented_graph` (horizon-minimum skip gate);
  `_rank_by_value_top_k` (new `time_index` parameter) + its
  `_schedule_generation_scheme` call site
- `tests/unit_tests/CPM/test_bugfix_regressions.py` (two new classes)
- `devLogs/repros/repro_b4_skipgate.py`, `repro_m1_topk.py` (new)

Result: plain `python -m pytest tests/unit_tests/CPM/` →
**890 passed, 5 skipped, 0 failed**.

### Correctness — time-window ES not propagated / backward pass not re-anchored (2026-09-03, round 2)

Finding C1 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`) — `_apply_time_windows`
folded regulatory time windows into the CPM values by tightening each activity
**locally** and never re-relaxing the network. Two silent consequences (the
*scheduled* times were correct; only the *reported* CPM values were wrong):

- **Successor ES not propagated.** When a window raised an activity's ES/EF,
  the raised EF never reached its successors — they kept an ES computed from the
  predecessor's *pre-window* finish (too early). Reported ES/EF/slack downstream
  were wrong and the project duration was understated.
- **Backward pass not re-anchored.** The project end extends to cover a
  window-delayed activity, but the backward pass was never re-run against the
  extended end. A plain release date (window `[west, ∞]`, no deadline) therefore
  produced `slack = LS − ES` with a stale, un-extended `LS` → spurious negative
  slack and a **false `window_infeasible` warning** on a perfectly feasible
  release date. The old code also left the activity with `LF < EF` (impossible).

Fix: when a topological order is available (always, from the CPM driver),
`_apply_time_windows` now runs a full re-relaxation — (1) a forward pass that
applies each `west` floor and propagates the raised EF to successors, (2) a
backward pass re-anchored to `min(new project end, wlf)` per activity, then
(3) `slack = LS − ES` with `window_infeasible = slack < 0`. A release date can
no longer be infeasible; a genuine deadline (finite `wlf` too tight for the
duration or a downstream deadline) still flags correctly. The former local-only
tightening is preserved as `_apply_time_windows_local` for the `topo is None`
path (never taken by the driver; kept so a direct call neither crashes nor
changes historical output).

**Regression tests** — `test_bugfix_regressions.py` gained
`TestWindowEarliestStartPropagates` (3 tests): raised-ES propagation to a
successor + extended project end; release-date-only feasibility (LF re-anchored,
slack 0, not flagged); genuine-deadline control (still flagged). Two known-answer
tests that had codified the bug were corrected: `test_window_tightens_es_when_
later_than_lag` (slack −2 → 0, `window_infeasible` True → False) and
`test_only_earliest_start_set` (LF 8 → 12, the re-anchored project end).
Mutation-verified: disabling the forward successor propagation reddens only the
propagation test (successor ES stays 4); using the stale LF instead of the
extended project end reddens only the release-date test (LF stays 7, the exact
`slack=-4.00 h` false-positive returns). All green after.

**Repros** — `devLogs/repros/repro_c1_window.py` now prints "NOT REPRODUCED".

**Files changed**
- `pert.py` — `_apply_time_windows` (forward ES propagation + re-anchored
  backward pass + `window_infeasible = slack < 0`); new
  `_apply_time_windows_local` (legacy topo-less path)
- `tests/unit_tests/CPM/test_bugfix_regressions.py` (new C1 class)
- `tests/unit_tests/CPM/test_known_answers.py`,
  `tests/unit_tests/CPM/test_time_windows.py` (two bug-codifying tests corrected)

Result: plain `python -m pytest tests/unit_tests/CPM/` →
**887 passed, 5 skipped, 0 failed**.

### Correctness — CCPM critical-chain & buffer defects (2026-09-03, round 2)

Findings B1, B2, B3 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`) — three
defects in the resource-constrained critical-chain computation and the buffer
splice. All three corrupt the CCPM chain / project finish silently (no crash,
plausible-looking output).

**B1 + B3 — `_longest_path_in_augmented` longest-path DP.** The DP seeded only
`self.startActivity` (or `topo[0]`) with its own duration and initialized every
other node's `dist` to `0.0`. Two consequences:
- **B1** — in a graph with *multiple* in-degree-0 sources and no unifying START
  milestone, every source other than the seeded one contributed `0` instead of
  its duration, so a longest path originating at a non-`topo[0]` source lost
  that source's entire length and the DP returned the wrong (shorter) chain.
- **B3** — with `dist` zero-initialized and a strict `>` relaxation, an edge
  that added no cumulative gain (e.g. `START(0) → M(0)`, where `dist[M]` is
  already `0`) never set `parent[M]`, so leading zero-duration nodes were
  dropped from the reconstructed chain.

Fix: seed **every** in-degree-0 source with its own duration and every other
node with `-inf`, removing the single `dist[start]` seed. The source set is
captured *before* Kahn's loop consumes `indeg` down to all-zero. Any node's
first relaxation now always wins the `>` comparison and sets a parent, fixing
both the multi-source duration loss (B1) and the zero-duration-prefix drop
(B3). A cyclic-input fallback seeds `topo[0]` when no true source exists, and
`-inf` (unreachable) nodes are skipped so the cycle-guard path still runs.

**B2 — `_splice_buffer_activity` dropped finish-to-start lag.** Inserting a
buffer rerouted each `pred → succ` edge to `pred → buffer → succ` but never
touched `lag_dict`. The `(pred, succ)` lag entry was orphaned — its edge gone,
so the forward/backward passes (which read lag by the exact `(pred, succ)` key)
skipped it and the successor started `lag` hours too early, collapsing the
project finish. Fix: move each rerouted lag onto the matching buffer edge —
`pred → buffer` for a feeding splice (fan-in, N preds ≥ 1 succ), `buffer → succ`
for a project splice (fan-out, 1 pred < M succs) — popping the orphaned entry
and keeping the larger lag on collision.

**Regression tests** — `test_bugfix_regressions.py` gained 5 tests:
`TestLongestPathMultiSourceDuration` (B1: true long source wins + single-source
control), `TestLongestPathRetainsZeroDurationPrefix` (B3), and
`TestSpliceBufferPreservesLag` (B2: feeding splice preserves project finish +
lag on `pred→buffer`; project splice attaches lag to `buffer→succ`).
Mutation-verified: reverting the DP seeding turns both B1 and B3 detectors red
(chain becomes `['Q','C']` / `['M','A','END']`) while the single-source control
stays green; disabling the lag transfer turns both B2 tests red (project finish
25→20, orphan entry retained). All 5 green after.

**Repros** — `devLogs/repros/repro_lp.py` (B1), `repro_lag.py` (B2),
`repro_b3_zeroprefix.py` (B3, new) all print "NOT REPRODUCED (fixed)".

**Files changed**
- `pert.py` — `_longest_path_in_augmented` (capture `sources` before Kahn;
  seed all sources / `-inf` elsewhere; skip unreachable; cyclic fallback);
  `_splice_buffer_activity` (lag transfer across the splice)
- `tests/unit_tests/CPM/test_bugfix_regressions.py`
- `devLogs/repros/repro_lp.py`, `repro_lag.py` (added `=== VERDICT ===`
  blocks), `repro_b3_zeroprefix.py` (new)

Result: plain `python -m pytest tests/unit_tests/CPM/` →
**884 passed, 5 skipped, 0 failed**.

### Correctness — parallel SGS over-commits dose within one time-step (2026-09-03, round 2)

Finding PD1 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`) — the same-time-step
analog of SC1, discovered while fixing it, and confirmed a genuine INFEASIBLE
schedule (not merely mis-reported). The parallel selection loop
(`_schedule_generation_scheme` for `max_use_res_ranked` / `max_use_res_shuffled`
/ `md_knapsack`, and `LookAheadScheduler.select_activities`) tentatively
decrements a shared capacity snapshot as each candidate is picked, so later
candidates in the *same* step see the reduced crew / equipment / location. Dose
was the exception: `_apply_tentative` never charged the tracker, so every
candidate's dose check in `_fits_with_tentative` read the same
(pre-time-step) `consumed_mrem`. With N dose activities all ready at once and
crew to spare, the loop selected them all — committing N × per-activity dose
against a budget that admits only one.

Validated: three 400 mRem activities ready at t=0, crew for all three
(6 MECHANIC, 2 each), budget 600 mRem (100/worker × 6) → the loop placed all
three for 1200 mRem, ~2× over budget.

**Fix — transient per-time-step dose overlay.** A `dose_rem` dict `{skill: mRem}`
is threaded through `_fits_with_tentative` / `_apply_tentative` exactly like
`res_rem`: `_apply_tentative` adds each selected activity's resolved dose
(`rate × workers × eff`, the identical formula `tracker.consume` uses, keyed by
the *substitution-resolved* skill breakdown already computed there), and
`_dose_fits` gained an optional `extra_consumed` argument so the budget check
becomes `committed + tentative + this ≤ budget`. Crucially the overlay is
**transient** — dose is still committed to the tracker only at start (in
`_update_activity_sets`), never in `_apply_tentative` — so analysis-only callers
that pass no overlay (`_compute_earliest_feasible`, `_can_schedule_activity`,
the serial path) never leak tentative dose into the real budget. Each parallel
selection loop builds a fresh `dose_rem` per time-step; the serial `'first'`
branch is single-select and needs none.

**Regression tests** — `test_bugfix_regressions.py` gained 5 tests
(`TestParallelEnforcesDoseBudget`: `test_only_budget_many_dose_activities_scheduled`
and `test_never_over_budget`, each parametrized over `max_use_res_ranked` /
`max_use_res_shuffled`, plus a `test_within_budget_activities_still_scheduled`
guard). Mutation-verified: the 4 defect-detectors are red before the fix (both
strategies place 3 / consume 1200 vs 600 mRem) and red again when the overlay
increment alone is neutralized; the within-budget guard (two 200 mRem
activities that *should* both start at t=0) stays green throughout; all 5 green
after.

**Repro** — `devLogs/repros/repro_pd1_parallel_dose.py` (prints "NOT REPRODUCED
(fixed)"); `repro_serial_dose.py` now also shows the parallel path placing 1.

**Files changed**
- `pert.py` — `_dose_fits` (optional `extra_consumed`); `_fits_with_tentative`
  and `_apply_tentative` (new `dose_rem` param + overlay increment); the
  `max_use_res_ranked`/`max_use_res_shuffled`, `md_knapsack`, and
  `LookAheadScheduler.select_activities` selection loops (fresh per-step
  `dose_rem`, threaded to both calls)
- `tests/unit_tests/CPM/test_bugfix_regressions.py`
- `devLogs/repros/repro_pd1_parallel_dose.py`, `repro_serial_dose.py` (note)

Result: plain `python -m pytest tests/unit_tests/CPM/` →
**879 passed, 5 skipped, 0 failed**.

### Correctness — time-varying availability (2026-09-03, round 2)

A manual correctness review of `pert.py`
(`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`) found a root defect repeated
across the scheduler and the independent validator: **resource availability
was sampled at a single instant** (the activity/demand *start*), so an
availability DROP landing inside an already-running activity was invisible.
The scheduler over-committed; the validator failed to catch it. Findings
C2 (scheduler) and C2b (validator). This round fixes that cluster only.

**`_build_capacity_snapshots` — min-over-cell seeding (C2)**  
The sparse capacity grid seeded each grid point `h` with the *point* value
`get_availability(skill, h)`, and its docstring claimed capacity was constant
between consecutive grid points — false whenever a base-pool breakpoint fell
strictly inside a cell. Each cell `[h, next_grid_point)` is now seeded with
the **minimum** availability across it, via the existing
`get_availability_in_range` / `get_capacity_in_range` primitives. The hot
`_fits_with_tentative` / `_apply_tentative` point-checks and their
substitution logic are untouched — a point-check now conservatively
represents the whole cell. For time-invariant pools min-over-cell equals the
point value, so the common path is unchanged (no behaviour change on existing
green tests).

**`schedule_validator.py` — interval demand check on all three dimensions (C2b)**  
`_check_crew_feasibility`, `_check_equipment_feasibility`, and
`_check_location_feasibility` compared piecewise-constant demand against
availability only at each `+1` (start) event. Each now walks consecutive
event pairs `[t, next_event)` and checks demand against the **minimum**
availability/capacity over that interval (`get_availability_in_range` /
`get_capacity_in_range`), dropping the start-event-only gate. This subsumes
the old point check (identical on time-invariant pools) and catches a drop
that lands mid-demand.

**Test update — `test_resource_equipment_and_new_activity`**  
This combined-replan test reduced MECH from h=2 while two frozen in-progress
activities (2 MECH each) ran until h=4 — a genuine physical over-commit on
[2,4) that the pre-fix validator missed. The MECH reduction now takes effect
from h=4 (after those activities complete), so the test still exercises the
combined resource+equipment+new-activity replan machinery without asserting a
physically impossible schedule as valid.

**Regression tests** — `test_bugfix_regressions.py` gained 8 tests
(`TestTimeVaryingCrewNoOvercommit`, `TestValidatorCatchesMidActivity{Crew,
Equipment,Location}Drop`), each mutation-verified: 5 target tests red before
the fix, 3 constant-pool controls green throughout, all 8 green after.

**Files changed**
- `pert.py` — `_build_capacity_snapshots` (min-over-cell seeding + docstring)
- `schedule_validator.py` — `_check_crew_feasibility`,
  `_check_equipment_feasibility`, `_check_location_feasibility`
- `tests/unit_tests/CPM/test_bugfix_regressions.py`,
  `test_replan_resources.py`

Result: plain `python -m pytest tests/unit_tests/CPM/` →
**857 passed, 5 skipped, 0 failed**.

### Correctness — SGS early-break starvation (2026-09-03, round 2)

Finding C3 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`). Second defect fixed in
round 2, unrelated to the time-varying cluster above.

**`_schedule_generation_scheme` — per-candidate skip, not blanket break (C3)**  
`_compute_univ_skill_min` derives "universal" no-alternative skills from the
skill-requiring activities only (zero-requirement activities are excluded from
the intersection). The `max_use_res_ranked` / `max_use_res_shuffled`
early-exit then `break`ed the entire candidate scan the instant such a skill's
remaining capacity fell below its minimum crew demand — abandoning **every**
remaining candidate that step, including zero-crew milestones/inspections,
START/END sinks, and activities needing only *other* skills. Those never
consume the scarce skill, so they were needlessly delayed (worse makespan,
spurious deadline misses) and — if the skill never recovered — could be
starved forever (incomplete schedule).

The `break` is now a per-candidate `continue`: a candidate is skipped only
when it *itself* requires a universal skill whose remaining capacity has
dropped below the minimum any such-requiring activity needs (so it provably
cannot fit). A candidate that requires none of the exhausted universal skills
is no longer gated and proceeds to the normal `_fits_with_tentative` check.
The optimization (skipping a full feasibility scan for provably-infeasible
candidates) is preserved; only its unsound blanket-termination is removed.
`_compute_univ_skill_min` is unchanged — it is now consumed correctly.

**Regression tests** — `test_bugfix_regressions.py`
`TestEarlyBreakDoesNotStarveZeroCrew` (4 tests): a zero-crew activity ranked
below a MECH-draining one starts at t=0 (not delayed to h=6); makespan is the
optimal 6h; plus a precondition that MECH is genuinely the universal skill and
an over-commit guard that two activities sharing all crew still cannot overlap.
Mutation-verified: the two starvation tests are red before the fix.

**Files changed**
- `pert.py` — `_schedule_generation_scheme`
  (`max_use_res_ranked` / `max_use_res_shuffled` early-exit)
- `tests/unit_tests/CPM/test_bugfix_regressions.py`

Result: plain `python -m pytest tests/unit_tests/CPM/` →
**861 passed, 5 skipped, 0 failed**.

### Correctness — Serial SGS dropped dose + equipment-zone constraints (2026-09-03, round 2)

Findings SC1 + SC2 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`). The
independent Serial SGS path (`calculateSerialScheduleWithResources` /
`_serial_check_feasibility`) hand-rolled a *weaker* feasibility check than the
parallel path, silently omitting two constraints the parallel path enforces:

- **SC1 — cumulative dose budget.** The serial feasibility check had no dose
  block **and** the serial commit never called `tracker.consume`. So dose
  budgets were both unenforced (any number of dose activities placed) and
  undetectable — the validator reads tracker state, which stayed at zero. A
  1200 mRem schedule sailed through a 500 mRem budget reporting "feasible".
- **SC2 — equipment zone affinity.** The serial equipment check was
  count-only; it never called `get_zone_id`, so an activity in one zone could
  consume equipment locked to another.

**Fix — route serial through shared checks.** Four helpers were factored out
so both paths enforce the *same* contract instead of a duplicated weaker one:
`_equipment_zone_conflict`, `_serial_dose_worker_map`, `_dose_fits`,
`_dose_consume`. The parallel `_fits_with_tentative` zone and dose blocks were
refactored to call `_equipment_zone_conflict` / `_dose_fits` (behaviour-
preserving — the existing parallel dose/zone tests stay green). The serial
path now:
- checks `_equipment_zone_conflict` and `_dose_fits` in
  `_serial_check_feasibility` (both time-invariant, so checked once);
- calls `_dose_consume` on commit, so the tracker reflects accrued dose;
- returns `None` from `_find_earliest_feasible_start_serial` when no candidate
  is feasible (dose/zone can't be satisfied at any later time), instead of the
  old fallback that force-returned `max(candidates)` and committed an
  infeasible placement. `_enforce_window_serial` and the commit loop skip the
  activity (with a logged warning) on `None`.

**Regression tests** — `test_bugfix_regressions.py` gained 7 tests
(`TestSerialEnforcesDoseBudget` ×4, `TestSerialEnforcesEquipmentZone` ×3).
Mutation-verified: the 4 defect-detecting tests are red before the fix (serial
places all 3 dose activities / tracker stays 0 / A placed in the wrong zone),
the 3 within-budget/matching-zone guards stay green throughout, all 7 green
after.

**Files changed**
- `pert.py` — shared helpers (`_equipment_zone_conflict`,
  `_serial_dose_worker_map`, `_dose_fits`, `_dose_consume`);
  `_fits_with_tentative` (parallel, refactored to shared helpers);
  `_serial_check_feasibility`, `_find_earliest_feasible_start_serial`
  (fallback → `None`), `_enforce_window_serial`,
  `calculateSerialScheduleWithResources` (skip-on-None + dose consume)
- `tests/unit_tests/CPM/test_bugfix_regressions.py`

Note: the **parallel** path also over-commits dose in the same-timestep
multi-selection case (`_apply_tentative` did not tentatively decrement dose),
placing A+B for 800 mRem against a 500 mRem budget. Recorded as finding PD1 and
fixed in the round-2 entry above ("parallel SGS over-commits dose within one
time-step").

Result: plain `python -m pytest tests/unit_tests/CPM/` →
**868 passed, 5 skipped, 0 failed**.

### Correctness — replan: stale endTime + clone loses availability events (2026-09-03, round 2)

Findings RP1 + RP2 (`devLogs/PERT_MANUAL_REVIEW_2026-09-03.md`) — the last two
HIGH findings, both on the replan path.

**RP1 — `_partial_reset` duration override leaves `endTime` stale.**
A `duration_overrides` entry for an *in-progress* activity updated
`act.duration` and `act._remaining_duration` but never refreshed `act.endTime`,
which the baseline run set to `start + OLD duration`. Every consumer of actual
completion / resource release reads `endTime` (`_update_ongoing_list`, the
in-progress completion seed in `_build_event_queue_from`, the lag-event push),
so the activity freed its resources at the *old* end time. Validated: B(10) and
D(6) share the sole WELDER; `replan(t=2, duration_overrides={'B': 20})` extends
B to [0,20] but the stale `endTime=10` freed the welder at h=10, letting D run
[10,16] concurrently — a 6h double-booking of a 1-unit resource. (The validator
could not see it either — it also reads the stale `endTime`.)
Fix: in the override branch, set
`act.endTime = max(current_abs, st + timedelta(hours=new_total))`
(≡ `current_abs + remaining`; the clamp keeps a shrink-below-elapsed override
from placing `endTime` in the past).

**RP2 — `clone_for_analysis` empties `_availability_events`, nothing refills it.**
The clone hard-set `_availability_events = frozenset()`.
`_precompute_availability_events` runs only from `__init__` (which the clone
bypasses via `object.__new__`) and from `replan()` when pools change — so a
clone with **time-varying** pools lost every capacity-change wake-up and
dead-locked into an incomplete schedule. Validated: WELDER 0 for [0,10h] then 2;
the original completes 3/3, the clone only 1/3 (heap exhausts at the project
start with no h=10 wake-up). Fix: call `clone._precompute_availability_events()`
after the pools are deep-copied (the method reads only those three pools).

**Regression tests** — `test_bugfix_regressions.py` gained 6 tests
(`TestReplanDurationOverrideRefreshesEndTime` ×3,
`TestCloneRepopulatesAvailabilityEvents` ×3). Mutation-verified: 4 defect-
detectors red before the fixes (endTime inconsistent with duration / D
double-booked / clone events empty / clone completes 1/3), 2 preconditions
(baseline serializes; baseline completes 3/3) green throughout, all 6 green
after. Note: the RP1 double-booking assertion compares D's start against B's
*physical* end (`start + duration`), not `endTime` — comparing against the
stale `endTime` would pass spuriously.

**Repros** — `devLogs/repros/repro_rp1_endtime.py`,
`repro_rp2_clone_events.py` (both now print "NOT REPRODUCED (fixed)").

**Files changed**
- `pert.py` — `_partial_reset` (endTime refresh in override branch),
  `clone_for_analysis` (recompute availability events)
- `tests/unit_tests/CPM/test_bugfix_regressions.py`
- `devLogs/repros/repro_rp1_endtime.py`, `repro_rp2_clone_events.py`

Result: plain `python -m pytest tests/unit_tests/CPM/` →
**874 passed, 5 skipped, 0 failed**.

### Test-harness recovery (2026-09-03)

Recovered the unit-test suite, which had regressed after file-reorganization
commits moved test data and a legacy runner shadowed the `CPM` package. Full
diagnosis in `devLogs/BRANCH_ASSESSMENT_2026-09-03.md`.

- **Collection unblocked** — renamed the legacy pre-pytest runner
  `tests/unit_tests/CPM/CPM.py` → `legacy_cpm_regression.py` (it shadowed
  `src/CPM`), and fixed `pytest.ini` `pythonpath` (`../..` → `../../../src`).
- **Data paths recentralized** — `conftest.py` now exposes `SCHEMA_PATH`
  (`src/CPM/outage_schema.json`) and `EXAMPLES_DIR` (`doc/demos/rcpsp/examples/`);
  all affected test files point at them.
- **Import root standardized** — a single `from CPM.x` root across all collected
  tests; `conftest.py` de-stubbed (removed the `sys.modules` fabrication that
  was masking real `ImportError`s).
- **Optional deps guarded** — `test_ga.py` (`deap`) and `test_rcpsp_alns.py`
  (`alns`) self-skip via `pytest.importorskip` instead of erroring collection.
- **PSPLIB regression** — `test_psplib.py` (a standalone script, not pytest)
  renamed → `psplib_regression.py`, data path fixed. ⚠️ Now runnable, it shows
  176 pass / 30 fail on `scheduled_duration` vs. recorded golden values
  (feasibility intact); re-baseline-vs-investigate is an open reviewer item.
- **RAVEN wiring** — replaced the malformed `tests` registration with a single
  entry → `run_cpm_pytests.py` shim, exercising the full pytest suite in CI.
- **Two buffer tests fixed** — stale expectations read the `END` sentinel
  (`constrained_chain_list[-1]`) instead of the terminal work activity (`[-2]`).
- **Bug-fix regression hardening** — a `pytest-cov` audit found three of the six
  documented `pert.py` fixes (lag in `check_dependency_violations`,
  `_effective_duration` remaining-vs-full, cycle detection in
  `_longest_path_in_augmented`) sat on lines *no* test executed, plus the
  `_build_augmented_graph` None-pool guard only partially. New
  `test_bugfix_regressions.py` (10 tests) pins each fix line; every test is
  mutation-verified (reverting the fix turns exactly its test red).

Result: plain `python -m pytest` → **849 passed, 5 skipped, 0 failed, 0 errors**.

### Challenge 15 — Multi-Mode RCPSP (MMRCPSP)

Each activity can now carry an optional list of named execution modes. A mode
bundles an alternative duration, resource requirements, and optional overrides
for dose rate, mobilization lead time, consumables, and system-state
requirements. Selecting a mode on an activity is the primitive operation for
evolutionary / genetic-programming optimizers that search over mode assignments.

**API**

```python
# Activity level
activity.modes               # list of mode dicts loaded from JSON
activity.selected_mode_id    # None, or the currently active mode_id
activity.set_mode('crash')   # apply named mode (duration + resources + optional fields)
activity.get_available_modes()  # ['normal', 'crash']

# Pert level — batch apply a mode-assignment vector, then recompute CPM
pert.set_modes({'T001': 'crash', 'T002': 'normal'})
```

**JSON schema** (`outage_schema.json`)

The optional `"modes"` array is accepted on any task object:

```json
{
  "task_id": "T001",
  "modes": [
    {
      "mode_id": "normal",
      "duration": 8,
      "required_resources": [{"skill_type": "MECH", "crew_count": 2}],
      "required_equipment": []
    },
    {
      "mode_id": "crash",
      "duration": 4,
      "required_resources": [{"skill_type": "MECH", "crew_count": 4}],
      "required_equipment": [],
      "mobilization_lead_hours": 1
    }
  ]
}
```

`required_consumables` and `required_system_states` are also supported as
per-mode optional overrides.

**Files changed**
- `activity.py` — `modes`, `selected_mode_id` fields; `set_mode()`, `get_available_modes()`
- `pert.py` — `Pert.set_modes()`
- `outage_schema.json` — `modes` array definition
- `unit_tests/test_multimode.py` — 53 tests (Activity, from_json round-trip, Pert.set_modes, CPM, scheduler)

---

### Performance — O(n²) → O(n) RCPSP Scheduling

Seven targeted fixes eliminate all O(n²) sources in the event-driven
scheduling loop. All topologies (serial, pipeline, fan) across both
unconstrained and tight-pool resource scenarios now scale as O(n¹·⁰).

**Before / After (n = 1 500, fan/tight)**

| | Before | After |
|---|---|---|
| `n = 1 500` timing | 423 ms | 40 ms |
| Empirical exponent | O(n¹·⁹) | O(n¹·⁰) |

**Fix A — `self.wait` list → set**  
`wait.remove(act)` was O(n); converted to `set` so `discard` is O(1).

**Fix B — `_ready` set + `_pending_preds` counter (Kahn's algorithm)**  
Replaced `all(pred in _completed_set for pred in backwardDict[act])` — O(k)
per activity per step — with a per-activity integer counter decremented when
each predecessor completes. Promotion to `_ready` is O(1).

**Fix C — bulk `self.ongoing` filter**  
`self.ongoing = [a for a in self.ongoing if a not in completed_set_now]` in
one pass instead of n × `list.remove`.

**Fix D — `_completed_set` mirror**  
Added `_completed_set: set` alongside `self.completed: list` for O(1)
membership tests throughout.

**Fix E — `_build_augmented_graph` sweep-line**  
Max-demand precheck skips the O(n²) pair scan when no skill is bottlenecked;
sorted-by-start sweep-line with early-break reduces tight-pool case to
O(n log n).

**Fix F — `schedule_log` O(n²) → O(1)**  
`[a.name for a in self.completed]` at every iteration (sum O(n²)) replaced
with `len(self.completed)`.

**Fix G — `queue.pop(0)` → `deque.popleft()`**  
Four Kahn topological-sort sites converted from O(n) list shift to O(1) deque.

**Fix H — f-string debug guard**  
`logger.debug(f"completed={[a.name for a in self.completed]}")` eagerly built
O(n) strings at every step even when DEBUG was disabled. Guarded with
`if logger.isEnabledFor(logging.DEBUG):`.

**Fix I — Lazy delay accumulation**  
Replaced per-step `act.addDelay(elapsed)` for all O(n) postponed candidates
with a single computation at activity start:
`act.delay = (start_time − act._candidate_since).total_seconds() / 3600`.
`Activity._candidate_since` records the first time the activity enters the
candidate set; `Activity.reset()` clears it.

**Fix IIa — No infoDict copy**  
`candidates[act] = self.infoDict[act].copy()` — O(6) dict clone per activity
per step — replaced with a direct reference. The `value` key is written back
into `infoDict[act]` in-place (safe for single-threaded scheduling).

**Fix IIb — `heapq.nlargest` with slot estimation**  
`_rank_by_value_top_k`: estimates the maximum startable activities K from
`_univ_skill_min` and uses `heapq.nlargest(K×8, …)` — O(n + K log K) — instead
of a full O(n log n) sort when K << n.

**Fix III — Priority heap for `_select_candidate_activities`**  
The dominant O(n²/k) bottleneck for fan/tight topologies: iterating all n
ready activities each of the n/k scheduling steps.

- `_priority_cache` — static float per activity, precomputed once per run from
  `TF_based` / `external` priority mode. Activity name used as tie-breaker
  for deterministic ordering.
- `_ready_heap` — max-heap (stored as min-heap with negated priorities) of
  `(−priority, name, act)` maintained alongside `_ready`. Pushed when an
  activity is promoted to ready; lazily discarded on pop when the activity
  has already started (no longer in `_ready`).
- `_collect_candidates_from_heap` — extracts top-K candidates in O(K log n)
  instead of scanning all n. Collected candidates are re-pushed so non-selected
  activities remain available next step.

Falls back to full O(n) scan for dynamic priority rules (lf, ls, mts, …).

**Files changed**
- `activity.py` — `_candidate_since` field; `reset()` clears it
- `pert.py` — all fixes above; new helpers `_compute_priority_cache`,
  `_build_ready_heap`, `_collect_candidates_from_heap`, `_rank_by_value_top_k`
- `unit_tests/test_scale_performance.py` — regression tests for O(n) scaling

---

### Schedule Validator

New module `schedule_validator.py` that performs post-schedule feasibility
auditing on a completed `Pert` run. The validator re-examines the schedule
as an independent pass — it does not trust that the scheduler enforced every
constraint — and returns a structured `ValidationResult` listing every
violation and quality warning found.

**Violation types** (13)

| Type | What is checked |
|---|---|
| `completeness` | Every activity reached `completed` status |
| `duration` | `endTime − startTime` matches `activity.duration` within 1 s |
| `precedence` | Every successor starts no earlier than `pred.endTime + lag_hours` |
| `time_window` | Each activity's start/end falls within its declared time windows |
| `crew` | No skill pool is over-committed at any point in the schedule |
| `equipment` | No equipment type is over-committed at any point |
| `location` | No location exceeds its `max_concurrent_tasks` limit |
| `location_workers` | No location exceeds its `max_concurrent_workers` limit |
| `system_state` | No two concurrent activities require conflicting states for the same plant system |
| `hold_point` | No activity blocked by a hold point is scheduled before that hold point completes |
| `consumable` | Inventory never goes negative when activities are replayed in start-time order |
| `equipment_zone` | Every activity using zone-locked equipment is located in that equipment's zone |
| `shift_calendar` | No activity starts or ends outside the declared shift window |

**Quality warnings**

- `delayed_activity` — activity's actual start is later than its CPM early start,
  indicating it waited for resources or constraints.

**API**

```python
from CPM.schedule_validator import validate_schedule

result = validate_schedule(pert)

result.is_valid          # True when no violations found
result.violations        # list of Violation(type, activity, message, ...)
result.warnings          # list of Warning(type, activity, message, ...)
result.summary()         # formatted multi-section text report
```

**Files changed**
- `schedule_validator.py` — new module (752 lines)
- `unit_tests/test_schedule_validator.py` — 33 tests covering all 13 violation
  types plus quality warnings; each violation test uses the inject-post-schedule
  pattern (schedule with valid data, then mutate to introduce the violation)

---

### Bug Fixes — `pert.py`

Six logic errors and crash paths found during code review and corrected.

**Lag support in `check_dependency_violations`**  
The public method silently ignored `lag_dict` — a successor starting
immediately after its predecessor finish was accepted even when a mandatory
lag applied. The check now computes
`required_start = pred_end + timedelta(hours=lag_h)` and reports
`'lag_hours'` in the violation dict.

**`_longest_path_in_augmented` used full duration during replan**  
`infoDict[v]['duration']` always returned the activity's original planned
duration. After a replan, in-progress activities have `_remaining_duration`
set. Replaced with `self._effective_duration(v)` so the critical chain is
anchored to how much work is actually left, not how much was originally
planned.

**`_window_violations` per-run isolation**  
`_window_violations` accumulated across successive `replan()` calls, so
`compute_fitness()` counted window violations from prior runs against the
current schedule. Added `_window_violations_baseline: int` (set in
`_partial_reset` before each replan) so the result snapshot slices
`self._window_violations[baseline:]` — current run only. Pre-replan history
is preserved for audit but invisible to fitness scoring.

**`_build_augmented_graph` None-pool guards**  
`location_pool.get_all_location_ids()`, `crew_pool.get_all_skills()`, and
`equipment_pool.get_all_equipment_ids()` were called unconditionally. A
`Pert` instance with no location, crew, or equipment pool (valid for
unit-test fixtures) raised `AttributeError`. All three are now guarded with
`if pool` checks.

**Cycle detection in `_longest_path_in_augmented`**  
A resource-flow arc that closes a cycle (can occur with pathological input)
left `len(topo) != len(augmented)` after Kahn's sort — the longest-path DP
silently produced zero for stuck nodes. Stuck nodes are now detected, logged
as a warning, and appended to the topo order so the DP completes on all
nodes.

**`_apply_tentative` KeyError on `eq_rem`**  
`eq_rem[eq_id][h]` raised `KeyError` when hour `h` had no prior entry for
that equipment — `eq_rem` is a `defaultdict(dict)`, so the outer key is safe
but the inner dict starts empty. Replaced with `eq_rem[eq_id].get(h, 0)`,
matching the pattern already used in `_fits_with_tentative`.

**Files changed**
- `pert.py` — six targeted fixes in `check_dependency_violations`,
  `_longest_path_in_augmented`, `_partial_reset`,
  `calculateScheduleWithResources_from`, `_build_augmented_graph`,
  `_apply_tentative`

---

### Data Layer Robustness — `activity.py` / `outage_data.py`

Nine defensive hardening changes that turn silent crashes or wrong-value
paths into early, descriptive errors.

**`outage_data.py` — `start_date` ISO format**  
`outage_config['start_date'] + 'T00:00:00'` crashed with `ValueError` when
the JSON already supplied a full ISO datetime (`"2025-03-15T08:00:00"`).
Both `start_date` and `target_end_date` now check for an existing `T` before
appending the time suffix.

**`activity.py` — successor dict missing `task_id`**  
`entry['task_id']` raised `KeyError` for a malformed successor dict such as
`{"lag_hours": 2.0}`. Now uses `.get('task_id')` with a descriptive
`ValueError` naming the task and the entry index.

**`activity.py` — `duration` not coerced to `float`**  
`task_dict['duration']` was stored as-is. A JSON string `"12"` survived
construction only to crash `timedelta(hours=self.duration)` at schedule time.
Explicit `float()` coercion added, consistent with every other numeric field
in `from_json`.

**`activity.py` — `time_windows` missing keys**  
The one-liner list comprehension `float(w['earliest'])` / `float(w['latest'])`
raised `KeyError` for an incomplete window entry. Replaced with an explicit
loop that validates both keys and raises `ValueError` with the task ID and
entry index.

**`activity.py` — `set_mode()` / `get_available_modes()` key guards**  
`m['mode_id']` in the `next()` search and fallback list, and `mode['duration']`
after the match, raised `KeyError` on malformed mode dicts. All three accesses
converted to `.get()` with explicit `ValueError` messages. `get_available_modes()`
silently filters out any mode entry missing `mode_id` rather than crashing.

**`outage_data.py` — `validate_data_consistency()` crashes on malformed input**  
The three resource-type loops used direct key access (`res_req['skill_type']`,
`eq_req['equipment_id']`, `req['item_id']`), so a malformed entry crashed the
validator before it could report any errors. All loops converted to `.get()`
with `errors.append(...)` on missing keys. Task ID lookups also guarded with
`.get('task_id', '<unknown>')`.

**`outage_data.py` — missing successor reference validation**  
`validate_data_consistency()` did not cross-check declared successor IDs
against the task list. The scheduler silently skips unknown successors,
leaving broken dependency chains undetected. A new check appends an error
for every successor ID that does not exist in the task set.

**`outage_data.py` — missing numeric bounds checks**  
`validate_data_consistency()` now rejects: missing `duration`, non-positive
`duration` (zero-duration tasks corrupt CPM calculations), non-numeric
`duration`, and negative `crew_count` values.

**`outage_data.py` — pool capacity values not coerced to `int`**  
`ResourcePool`, `EquipmentPool`, and `LocationPool` `from_json` methods
stored `available_count`, `quantity_available`, and `max_concurrent_tasks`
directly from the JSON value. A string `"20"` survived pool construction
and caused `TypeError` when the scheduler performed integer arithmetic.
All three now wrap with `int()`; `max_concurrent_workers` (optional,
nullable) wraps with `int(...) if value is not None else None`.

**Files changed**
- `activity.py` — `from_json` (successors, duration, time_windows);
  `set_mode()`, `get_available_modes()`
- `outage_data.py` — `__init__` (date parsing); `validate_data_consistency()`
  (key guards, successor refs, bounds checks);
  `ResourcePool.from_json`, `EquipmentPool.from_json`, `LocationPool.from_json`
  (int coercions)
- `unit_tests/test_safety_function.py` — three minimal task fixtures updated
  to include `duration` now that the validator enforces its presence

---

## Earlier Challenges

| # | Feature | Summary |
|---|---|---|
| 1 | Time-varying resources | Resource availability changes over outage duration |
| 2 | Equipment & location constraints | Multi-dimensional capacity (equipment + location concurrent-task limits) |
| 3 | Exact CPM reset between runs | `_reset_scheduling_state()` for clean RAVEN Monte-Carlo iterations |
| 4 | Real-time replanning | `replan()` / `_partial_reset()` for mid-outage schedule updates |
| 5 | Shift calendar | Partial-day work windows; off-shift periods blocked from starting activities |
| 6 | Lag constraints | Mandatory wait between predecessor finish and successor start |
| 7 | Time-window constraints | `window_earliest_start_hours` / `window_latest_finish_hours` per activity |
| 8 | Multi-window constraints | Multiple disjoint time windows per activity |
| 9 | WBS-group priority | Aggregate slack for WBS packages used in scheduling priority |
| 10 | Hold-point logic | Hard stop points requiring explicit release before successors proceed |
| 11 | Consumable materials | Inventory-tracked consumables deducted when activity starts |
| 12 | Radiation dose budgets | Per-skill dose tracking with irrevocable commitment at activity start |
| 13 | System-state locking | Shared system states (valve position, breaker state) with incompatibility checks |
| 14 | Fitness function | Composite schedule quality metric (makespan ratio, delay ratio, criticality) |
| 15 | **Multi-mode RCPSP** | **Named execution modes per activity; `set_mode()` / `set_modes()` API** |
