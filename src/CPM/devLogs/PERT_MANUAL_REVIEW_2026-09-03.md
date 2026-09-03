# `pert.py` — Manual Correctness Review (complete — discovery only, no fixes applied)

**Date:** 2026-09-03  ·  **Reviewer:** Claude Code
**Scope:** hand read-through of the `Pert` class (and its schedulers/validator)
hunting for logic bugs beyond what the (green, 849-pass) test suite covers.
This is a *living* log so the review is recoverable if interrupted.

**Status:** all method clusters reviewed (see coverage map). **No source code
was modified** — this pass is discovery/assessment only. All repro scripts live
under `/tmp` (`repro_timevarying.py`, `repro_earlybreak.py`, `repro_c1_window.py`,
plus agents' `repro_lp.py`, `repro_lag.py`, `repro_serial_dose.py`,
`repro_serial_zone.py`); they are ephemeral and should be preserved into the
repo before acting on fixes if they are wanted as regression seeds.

Baseline: `python -m pytest` → **849 passed, 5 skipped** (confirmed 2026-09-03;
unchanged — no source edits this pass).

---

## Findings summary (17 fixed · 1 refuted · 1 latent — 8 of the fixed are HIGH)

| ID | Severity | Where | One-liner | Status |
|---|---|---|---|---|
| **C2** | HIGH | `_build_capacity_snapshots` 3443 | sparse grid omits pool availability breakpoints → crew over-commit on mid-activity avail drop | ✅ FIXED (round 2) |
| **C2b** | HIGH | `schedule_validator` crew/equip/loc 318/359/406 | validator samples availability only at demand-start events → misses the same over-commit | ✅ FIXED (round 2) |
| **C3** | HIGH | `max_use_res_ranked` early-break 3868 + `_compute_univ_skill_min` 1502 | "universal skill" excludes 0-demand acts → early-break starves zero-crew candidates (delay, or never-scheduled) | ✅ FIXED (round 2) |
| **SC1** | HIGH | serial `_serial_check_feasibility` 5652 | serial path ignores dose budgets entirely AND is undetectable by validator | ✅ FIXED (round 2) |
| **SC2** | HIGH | serial equipment block 5705 | serial path ignores equipment zone-affinity | ✅ FIXED (round 2) |
| **PD1** | HIGH | parallel `_apply_tentative` / `_fits_with_tentative` 3708/3834 | same-timestep multi-select doesn't tentatively decrement dose → parallel over-commits the dose budget (infeasible) | ✅ FIXED (round 2) |
| **RP1** | HIGH | `_partial_reset` override branch 1806 | in-progress duration override doesn't update `endTime` → resource double-booking | ✅ FIXED (round 2) |
| **RP2** | HIGH | `clone_for_analysis` 2621 | clone empties `_availability_events`, never repopulated → time-varying clone deadlocks | ✅ FIXED (round 2) |
| **C1** | med | `_apply_time_windows` 1011 | window ES not propagated forward → wrong successor ES/slack + false "infeasible" warning (reported values only, schedule OK) | ✅ FIXED (round 2) |
| B1 | med | `_longest_path_in_augmented` 5249 | only first source's duration seeded → wrong constrained chain (multi-source, no START) | ✅ FIXED (round 2) |
| B2 | med | `_splice_buffer_activity` 4175 | buffer splice drops the FS lag on the rewritten edge | ✅ FIXED (round 2) |
| B3 | low | DP reconstruction 5253 | zero-duration prefix nodes dropped from chain | ✅ FIXED (round 2) |
| B4 | med | `_build_augmented_graph` 5126 | binding early-exit samples avail at `startTime` not overlap time | ✅ FIXED (round 2, confirmed) |
| B5 | low | augmented-graph location 5385 | only consecutive sorted pairs checked for `max_tasks==1` zones | ✅ FIXED (round 2) — all-pairs scan; latent (analytics-only, unreachable via correct scheduler) |
| SC3 | low | serial resource check 6004 | serial omits skill substitution → over-delays feasible acts | ✅ FIXED (round 2) — substitution in serial check + commit/consumption, mirrors parallel; no over-commit |
| SC-m1 | low | `check_dependency_violations` 5232 | no float tolerance vs validator's `_PREC_TOL` | ✅ FIXED (round 2, confirmed) |
| SC-m2 | low | `priority_calculation` 5777 | `'minrr'` sorted largest-first (possible inverted direction) | ❌ REFUTED (intended — rr-family aggregation) |
| RP-l | latent | `_generate_info_from` 1973 | rule-based priority metrics not recomputed on replan (not reachable via public `replan()`) | PLAUSIBLE |
| M-1 | ~~low~~ **med** | `_rank_by_value_top_k` 4789 | top-k cutoff `max_slots` estimated from `get_availability(s, startTime)`, not current time; with 8× overbook, truncates valid candidates when availability grows (same time-varying-at-startTime family as B4) — **behavioral makespan bug, not cosmetic** | ✅ FIXED (round 2, confirmed) |

Note the recurring **time-varying-availability** root defect: C2, C2b, B4 (and
RP2 in a related way) all fail to account for capacity that changes *during* an
interval. The correct primitives already exist — `get_availability_in_range` /
`get_capacity_in_range` (min over interval) and `get_periods_in_range`
(breakpoints) in `outage_data.py` — and are simply not called on the
feasibility path. See cross-cutting theme at the end.

---

## Method-cluster coverage map

| Lines | Cluster | Owner | Status |
|---|---|---|---|
| 616–1157 | CPM core (generateInfo, slack, windows, WBS) | main | reviewed |
| 1159–1471 | Critical path, project duration, addActivity/insert_task | main | reviewed |
| 1502–1636 | ready-set/heap/priority-cache helpers | main | pending |
| 1636–2634 | Replan path (_partial_reset … replan, clone) | agent A | pending |
| 2634–2856 | shift/availability event machinery | main | pending |
| 2856–3420 | main schedule loop + candidate selection | main | pending |
| 3420–3929 | resource feasibility core (_fits/_apply_tentative, SGS) | main | pending |
| 3929–4433 | can_schedule, fitness, buffers | agent B | **reviewed** |
| 4433–5058 | idle explainers, ranking, ongoing, dep-violations | main + agent C | **reviewed** |
| 5058–5294 | augmented graph + resource-constrained chain | agent B | **reviewed** |
| 5294–6103 | serial scheduler + priority_calculation | agent C | pending |
| 6103–6497 | plotting (`plot_activity_dag` + helpers) | skim | **reviewed** (viz-only, no risk) |
| 6497–6971 | `_weight_function` + Gantt/utilization plots | main | **reviewed** |
| 6974–7119 | `MDKnapsackScheduler` (`md_knapsack` SGS) | main | **reviewed** |
| 7127–7248 | `LookAheadScheduler` (`look_ahead` SGS) | main | **reviewed** |

Also directly reviewed by main: 3420–3929 (resource feasibility core + SGS
early-break), the capacity-snapshot builder, and `_compute_univ_skill_min`.

**SGS-by-SGS exposure to C2/C3:**
- `max_use_res_ranked` — C2 (sparse grid) **and** C3 (early-break starves
  zero-crew). Both.
- `max_use_res_shuffled` — C2 (uses the same snapshots); C3 status depends on
  whether its branch shares the early-break (not separately traced — assume C2
  at least).
- `look_ahead` (`LookAheadScheduler`) — C2 (same `_build_capacity_snapshots` /
  `_fits_with_tentative` machinery) but **immune to C3** (no `_univ_skill_min`
  early-break; scans all scored candidates).
- `md_knapsack` (`MDKnapsackScheduler`) — the knapsack is a *tentative* selector
  (point-in-time `_get_capacities`, and under-models dose/zone/skill-
  substitution), but the `md_knapsack` branch re-validates every pick with
  `_fits_with_tentative`, so its under-modeling is compensated. Inherits C2 via
  that re-validation.
- `'first'` (serial, `calculateSerialScheduleWithResources`) — separate code
  path; carries SC1/SC2/SC3 instead (and does NOT use the C2 sparse grid — it
  has its own `_find_earliest_feasible_start_serial` boundary logic, which Agent
  C found covers capacity-change boundaries correctly).

**`_weight_function` (6497)** — correct urgency sigmoid; latent-only:
`math.exp(threshold − total_float)` overflows for total_float ≈ −700
(unreachable for realistic hour-based slack). Not logged as a finding.

---

## Candidate findings (unverified → verified)

### C1 — `_apply_time_windows`: tightened ES not propagated forward (CONFIRMED, reported-values only — ✅ FIXED 2026-09-03)
> **FIX (round 2):** `_apply_time_windows` now re-relaxes the network whenever a
> topological order is available (always, from the CPM driver): (1) a forward
> pass applies each `west` floor and propagates the raised EF to successors,
> (2) a backward pass is re-anchored to `min(new project end, wlf)` per
> activity, (3) `slack = LS − ES` with `window_infeasible = slack < 0`. Release
> dates can no longer be flagged infeasible; genuine deadlines still are. The
> old local-only tightening is preserved as `_apply_time_windows_local` for the
> `topo is None` path. Regressions: `TestWindowEarliestStartPropagates` in
> `test_bugfix_regressions.py` (mutation-verified, both re-relaxation halves);
> two bug-codifying known-answer/window tests corrected. See CHANGELOG
> "Correctness — time-window ES not propagated / backward pass not re-anchored".

`_apply_time_windows` (pert.py:1011) raises a windowed activity's ES/EF locally
(1065-1068) and propagates tightened **LF backward** to predecessors (1095-1105),
but **never re-runs a forward relaxation**, so the raised EF is not propagated
**forward** to successors. Two symptoms, one root cause:

1. **Successor ES/EF/slack too early.** A successor keeps its CPM ES computed
   from the predecessor's *original* EF. Reported project duration is
   understated.
2. **Spurious negative slack + false "window infeasibility" warning** on the
   release-dated activity itself. The backward pass is never re-anchored to the
   window-extended project end, so a plain release date (window `[west, ∞]`, no
   deadline) yields `slack = LS − ES` with the un-extended `LS` → negative,
   which trips `window_infeasible=True` and logs a `WARNING`.

**Repro** (`/tmp/repro_c1_window.py`): START→A(4h, `earliest=10`)→B(4h)→END.
Reported: A.ES=10 A.EF=14 **slack=−10 (false-infeasible warning)**; B.ES=4 B.EF=8
(should be 14/18); reported project = 14. **Actual schedule is correct**: A[10,14]
B[14,18], scheduled_duration=18 — the scheduler enforces precedence and windows
independently.

**Impact (MEDIUM, no infeasible schedule):** corrupts reported ES/EF/slack,
project duration, and criticality; a successor looks less critical than it is
(TF_based priority mis-ranks it); fitness makespan/criticality ratios are off;
the validator's `delayed_activity` warning fires spuriously (actual start >
understated CPM ES); and release-dated activities are mislabelled infeasible.
**Fix direction:** after tightening ES, run a forward relaxation
(`EF(v)=ES(v)+dur`, `ES(w)=max(ES(w), EF(v)+lag+lead)`) in topo order, then a
backward pass re-anchored to the new max-EF project end.

---

## Confirmed findings

### C2 — Scheduler over-commits crews when availability drops *mid-activity* (CONFIRMED — ✅ FIXED 2026-09-03)
> **FIX (round 2):** `_build_capacity_snapshots` now seeds each grid cell
> `[h, next_grid_point)` with the **minimum** availability/capacity across it
> (`get_availability_in_range` / `get_capacity_in_range`) instead of the point
> value at `h`. The hot `_fits_with_tentative` / `_apply_tentative` point-checks
> are untouched — each now conservatively represents its whole cell. No-op on
> time-invariant pools. Regression: `TestTimeVaryingCrewNoOvercommit` in
> `test_bugfix_regressions.py`. See CHANGELOG "Correctness — time-varying
> availability (round 2)".

**Root cause:** `_build_capacity_snapshots` (pert.py:3443) builds the sparse
capacity grid from only `{time_index, candidate end times}` ∪ ongoing-activity
boundaries ∪ `extra_boundaries`. It does **not** add the base resource pool's
own availability breakpoints (the `start_date`/`end_date` edges of each
`ResourceAvailability` period). Feasibility (`_fits_with_tentative`,
pert.py:3566) then point-samples `res_rem[skill].get(h, 0)` only at grid
points, and `_apply_tentative` (pert.py:3694) computes the assignment at the
activity's start hour `h0` and applies it uniformly across the whole run. So an
activity is admitted using the availability that holds *at its start*, and a
later drop inside its run window is invisible.

The docstring's claim "Between consecutive grid points capacity is constant" is
therefore false for time-varying pools — capacity can step down between grid
points and the grid never records it.

**Repro** (`/tmp/repro_timevarying.py`): MECH pool = 4 for [0h,4h) then 2 for
[4h,∞). Activity A needs 4 MECH for 6h. Scheduler starts A at t=0 (avail=4) and
runs it [0,6) → consumed 4 > available 2 at hours 4–5. Hour-by-hour replay of
completed activities confirms the over-commit.

**Impact:** silent infeasible schedules (resource over-allocation) whenever a
pool's availability decreases during an activity that is already running.
Time-varying availability is Challenge-1 core functionality, so this is not an
exotic edge case.

### C2b — Validator `_check_crew_feasibility` shares the same blind spot (CONFIRMED — ✅ FIXED 2026-09-03)
> **FIX (round 2):** all three checks (`_check_crew_feasibility`,
> `_check_equipment_feasibility`, `_check_location_feasibility`) now walk
> consecutive event pairs `[t, next_event)` and compare demand against the
> **minimum** availability/capacity over that interval, dropping the
> start-event-only gate. Subsumes the old point check (identical on
> time-invariant pools). Regressions: `TestValidatorCatchesMidActivity{Crew,
> Equipment,Location}Drop` (each with a constant-pool control) in
> `test_bugfix_regressions.py`.

`schedule_validator.py:290` runs a sweep-line over start/end events and only
evaluates `avail = get_availability(skill, t)` at **start** events (`if sign ==
+1`, line ~318). It never samples at availability breakpoints, so a mid-activity
availability drop is invisible to the independent audit too — which is exactly
why the whole suite reports "0 crew violations" and never caught C2. In the C2
repro the validator returns `crew=0` violations despite the demonstrable 4>2
over-commit.

**Follow-up verified (2026-09-03):** equipment (`_check_equipment_feasibility`,
line 359 `if sign == +1`) and location (`_check_location_feasibility`, line 406
`if delta_tasks == +1`) checks share the identical start-event-only pattern.
All three read availability/capacity only at demand-*start* events; demand is
piecewise-constant and peaks right after a start (so demand peaks are caught),
but a mid-activity **availability decrease** with no start event at that instant
is never checked. Equipment and location pools both support time-varying
capacity (`get_availability_at` @750, `get_capacity_at` @897, both period-based),
so the blind spot is reachable on all three dimensions, not just crew.

### C3 — `max_use_res_ranked` early-break starves zero-crew / low-demand candidates (CONFIRMED — ✅ FIXED 2026-09-03)
> **FIX (round 2):** the `max_use_res_ranked` / `max_use_res_shuffled`
> early-exit is now a per-candidate `continue` instead of a blanket `break`. A
> candidate is skipped only when it *itself* requires a universal skill whose
> remaining capacity has dropped below the minimum such-requiring demand
> (provably infeasible); a candidate needing none of the exhausted universal
> skills (zero-crew milestones, other-skill work) is no longer gated and still
> gets its `_fits_with_tentative` check. The optimization is preserved; only its
> unsound blanket termination is removed. `_compute_univ_skill_min` is unchanged
> — it is now consumed correctly. Regression: `TestEarlyBreakDoesNotStarveZeroCrew`
> in `test_bugfix_regressions.py`. Isolated evidence flips in
> `devLogs/repros/repro_earlybreak.py` (M now starts at t=0, makespan 6h).

`_compute_univ_skill_min` (pert.py:1502) computes the per-skill minimum crew
demand across activities, but **skips zero-requirement activities** from the
intersection (the `continue` at ~1525). So a skill can be labelled "universal"
(present in `_univ_skill_min`) even though some candidates — milestones,
inspections, START/END sentinels — need none of it.

The `max_use_res_ranked` early-break (pert.py:3868-3872) then does:
```python
if _univ_min and any(res_rem.get(s, {}).get(time_index, 0) < d
                     for s, d in _univ_min.items()):
    break   # abandon ALL remaining candidates this step
```
When that "universal" skill is momentarily exhausted (by an already-selected
ongoing activity, or a genuine availability shortfall), the loop `break`s and
**every** not-yet-scanned candidate is skipped this step — including zero-crew
activities that could legally start right now.

**Repro** (`/tmp/repro_earlybreak.py`, constant pool, isolated from C2): MECH=2
constant; `A`(6h, needs 2 MECH) ranked above `M`(4h, **0 crew**), both after
START. `A` is selected at t=0 and consumes MECH→0; the early-break then skips
`M`, delaying it from its correct start t=0 to t=6. Makespan **10h vs optimal
6h**. `_univ_skill_min = {'MECH': 2}`.

**Impact:** two severities. (1) *Quality/optimality* — zero-/low-demand
activities are needlessly delayed behind pool-exhausting ones (worse makespan;
can spuriously trip a time-window/deadline). (2) *Hard incompleteness* — if the
universal skill's availability never recovers to ≥ its universal min (e.g. a
permanent time-varying step-down), the break fires at *every* step where such a
candidate is eligible and it is **never scheduled** → incomplete schedule. This
is the mechanism behind the END-not-scheduled puzzle in the C2 repro.

**Note on scope:** only affects the `max_use_res_ranked` SGS (and any SGS using
this early-break). `'first'` (serial) and the knapsack paths do not use it. A
fix must exclude from `_univ_skill_min` any skill not required by *every*
candidate that could be ranked — or, more simply, drop the early-break's
"universal" assumption for zero-demand candidates (they never consume the
scarce skill, so exhaustion of it must not gate them).

---

## Findings from background agent B (buffers + augmented graph) — 2026-09-03

Agent B reviewed 3929–4433 and 5058–5294. Reproduced two bugs; three plausible.
Repro scripts under `/tmp` (`repro_lp.py`, `repro_lag.py`).

### B1 — `_longest_path_in_augmented` only seeds the first source's duration (CONFIRMED — ✅ FIXED 2026-09-03)
> **FIX (round 2):** the DP now seeds **every** in-degree-0 source with its own
> duration and every other node with `-inf` (the single `dist[start]` seed is
> gone). The source set is captured *before* Kahn's loop consumes `indeg` to
> all-zero. Each source contributes its full length, so the true longest path
> wins regardless of `topo[0]`. Fixed together with B3 in the same block. See
> CHANGELOG "CCPM critical-chain & buffer defects (round 2)". Regression:
> `test_bugfix_regressions.py::TestLongestPathMultiSourceDuration`; repro
> `repro_lp.py` flips to "NOT REPRODUCED (fixed)".

pert.py:5242-5255 (root cause ~5249). The DP inits `dist=0` for every node but
seeds only `dist[start]` (where `start = self.startActivity or topo[0]`) with
its own duration. Every *other* source node keeps `dist=0`, so a longest path
originating at a non-`topo[0]` source loses that source's entire duration.

**Failure:** multi-source graph with no unifying START (supported via
`_get_sources`). Sources `P(100h)→C` and `Q(1h)→C` with `Q` as `topo[0]`: DP
seeds `dist[Q]=1`, leaves `dist[P]=0`, returns chain `Q→C` (len 2) instead of
`P→C` (len 101). Feeds `_compute_resource_constrained_chain`, so
`insert_project_buffer`/`insert_feeding_buffers` then size/place buffers on the
**wrong chain**. Latent whenever a single unifying START exists.

### B2 — `_splice_buffer_activity` drops the finish-to-start lag on the spliced edge (CONFIRMED — ✅ FIXED 2026-09-03)
> **FIX (round 2):** the splice now transfers each rerouted `lag_dict[(pred,succ)]`
> onto the matching buffer edge — `pred → buffer` for a feeding splice
> (fan-in, `len(preds) >= len(succs)`), `buffer → succ` for a project splice
> (fan-out) — popping the orphaned `(pred,succ)` entry and keeping the larger
> lag on collision. Project EF is preserved across the splice. Regression:
> `test_bugfix_regressions.py::TestSpliceBufferPreservesLag`; repro
> `repro_lag.py` flips to "NOT REPRODUCED (fixed)".

pert.py:4155-4209 (edge rewrite ~4175-4191). Replacing `pred→succ` with
`pred→buffer→succ` neither deletes nor transfers `lag_dict[(pred,succ)]`, so a
non-zero FS lag on that edge vanishes from all CPM passes (`generateInfo` reads
`lag_dict.get((u,v))` at 675/700).

**Failure** (`/tmp/repro_lag.py`): `A→B` with `lag=5h`, splice a 0-duration
buffer → project EF drops **25→20** (should stay 25); new edges carry no lag,
orphan `(A,B)=5.0` left dangling. `get_buffer_status.cpm_start_hours` also wrong.

### B3 — DP strict-`>` with zero-init drops leading/zero-duration nodes from the reconstructed chain (CONFIRMED — ✅ FIXED 2026-09-03)
> **FIX (round 2):** fixed by the same B1 change — with sources seeded to their
> duration and all other nodes at `-inf`, any node's first relaxation always
> wins the strict `>` comparison and sets a parent, so leading zero-duration
> nodes (e.g. `START(0) → M(0)`) are retained in the reconstructed chain.
> Confirmed via new repro `repro_b3_zeroprefix.py` (RED on the old code:
> chain `['M','A','END']`; now `['START','M','A','END']`). Regression:
> `test_bugfix_regressions.py::TestLongestPathRetainsZeroDurationPrefix`.

pert.py:5253 (`if cand > dist[v]`). A cumulative-length-0 edge (e.g.
`START(0)→M(0)`) never sets `parent[·]`, so zero-duration prefixes/milestones
get `parent=None` and are dropped in reconstruction. Chain *length* unaffected;
`constrained_chain_list` may omit leading/zero-duration nodes (e.g. START),
perturbing any consumer keying off exact chain membership. Low impact for
sizing (real work has >0 duration).

### B4 — `_build_augmented_graph` binding early-exit samples availability at `startTime`, not overlap time (CONFIRMED — ✅ FIXED round 2)
> **FIX (round 2):** the cheap `2*max_demand >= avail` skip gate now samples the
> **horizon minimum** — `get_availability_in_range(skill, _h_start, _h_end)` over
> the scheduled span (`_h_start` = earliest scheduled start, `_h_end` = latest
> scheduled end), instead of the point value at `self.startTime`. The gate can no
> longer wave through a pair whose overlap window dips below the startTime level,
> so no binding resource-flow arc is dropped and the constrained chain is no
> longer under-estimated. Confirmed via a direct unit-level `_build_augmented_graph`
> call (a *saturating* overlap where `combined_demand == avail_at_overlap`, which a
> real leveled scheduler does produce). See CHANGELOG "time-varying availability
> sampled at startTime (B4 + M-1)". Repro: `repros/repro_b4_skipgate.py`.
> Regression: `test_bugfix_regressions.py::TestAugmentedGraphSkipGateHorizon`.

pert.py:5126-5152 (`_t0 = self.startTime`, used at 5130/5143). The
`2*max_demand >= avail` skip gate reads `get_availability(skill, _t0)` but the
actual binding test uses `get_availability(skill, overlap_start)`. If a pool is
*lower* at some overlap window than at startTime, a binding pair is skipped → a
resource-flow arc dropped → too-short constrained chain. Same time-varying root
family as C2/C2b.

### B5 — location binding checks only consecutive sorted pairs (CONFIRMED — ✅ FIXED round 2, latent)
> **FIX (round 2):** the location block in `_build_augmented_graph` now scans
> **all** pairs per zone (early `break` once a later task starts at/after the
> current task's end — the list is start-sorted, so well-separated activities
> stay near-linear). Arc direction is unchanged (earlier-start → later-start),
> so the DAG stays acyclic. Confirmed at the function level via
> `repros/repro_b5_locpairs.py` (`A[0,10] B[1,2] C[3,13]` at one `max_tasks==1`
> zone: `A→B` added, `B↔C` don't overlap, and the binding `A↔C` pair got no arc
> directly or transitively). **Latent:** the block only fires on an *overlap* at
> a `max_tasks==1` zone, which a correct SGS never produces (probe: the three
> activities schedule back-to-back `A[0,10] C[10,20] B[20,21]`, zero overlaps) —
> so this affects only the constrained-critical-chain / total-float analytics
> (`_compute_actual_tf_proxy`, the sole consumer) and only if a future scheduler
> regression let an overlap through. Defense-in-depth matching the method's own
> docstring. Regression:
> `test_bugfix_regressions.py::TestAugmentedGraphSerializesAllZonePairs`
> (detector + control, mutation-verified). See CHANGELOG "augmented-graph
> location arcs cover all overlapping pairs (B5)".

pert.py:5385 (was ~5096). For a `max_tasks==1` zone with 3+ activities, only
adjacent (start-time-sorted) pairs were examined; a non-adjacent overlapping
pair got no serialization arc and wasn't recovered transitively. Weakly
reachable (a `max_tasks==1` zone shouldn't have overlaps in a feasible schedule)
— hence latent, fixed as analytics defense-in-depth.

Agent B also checked `_size_buffer` (SSQ `f·√Σd²` matches docstring; empty/zero
guarded), `get_buffer_status`, `_compute_actual_tf_proxy`, and
`insert_project_buffer` idempotency — no additional defects.

### M-1 — `_rank_by_value_top_k` top-k cutoff sampled at `startTime`, not current time (CONFIRMED — ✅ FIXED round 2; upgraded low→med)
> **FIX (round 2):** `_rank_by_value_top_k(candidates, time_index=None)` now
> takes the current scheduling time and estimates `max_slots` from
> `get_availability(s, time_index)` (falling back to `self.startTime` only when
> no time is passed). The `_schedule_generation_scheme` caller passes the event's
> `time_index`, so `k = max_slots * OVERBOOK` reflects capacity *now*, not at
> project start. See CHANGELOG "time-varying availability sampled at startTime
> (B4 + M-1)". Repro: `repros/repro_m1_topk.py`. Regression:
> `test_bugfix_regressions.py::TestTopKUsesCurrentTimeAvailability`.

pert.py:5036 (was 4789). `k = max_slots * OVERBOOK` (OVERBOOK=8) with `max_slots`
derived from `get_availability(s, self.startTime)`. **Catalogued "low" but
confirmed as a genuine behavioral makespan bug, not cosmetic.** The main
scheduling loop pops one event, calls SGS *once* at that `time_index`, starts the
selected activities, and advances to the next event — it does **not** re-enter SGS
at the same `time_index`. So when availability *grows* after startTime, the
under-counted `k` truncates candidates that are placeable now via
`heapq.nlargest(k)`, and the deferred ones must wait for a later event. Repro
(`repro_m1_topk.py`): 20 sibling activities all ready at t=10h with a pool that
rises 1→30 at 10h launched in 3 waves of 8/8/4 (makespan 25h) instead of one wave
of 20 (optimal 15h).

---

## Findings from background agent C (serial scheduler + dep checks) — 2026-09-03

Agent C reviewed 4433–5058 and 5294–6103. **Key structural finding:** the serial
feasibility path (`_serial_check_feasibility`, pert.py:5652) checks a *strict
subset* of what the parallel path (`_fits_with_tentative`, 3566) enforces — two
whole constraint dimensions are silently dropped. The serial path
(`calculateSerialScheduleWithResources`) has **essentially no unit-test
coverage** (only `psplib_regression.py` touches it; the dose/zone tests exercise
only the parallel `calculateScheduleWithResources`), which is why SC1/SC2 pass
the green suite.

### SC1 — Serial scheduler ignores dose budgets entirely (CONFIRMED — ✅ FIXED 2026-09-03)
> **FIX (round 2):** `_serial_check_feasibility` now calls the shared
> `_dose_fits(activity, _serial_dose_worker_map(activity))` helper, and the
> `calculateSerialScheduleWithResources` commit path calls `_dose_consume`, so
> the tracker reflects accrued dose (the validator can now see it).
> `_find_earliest_feasible_start_serial` returns `None` (rather than force-
> committing `max(candidates)`) when no candidate is feasible — dose is time-
> invariant, so no later start helps — and the callers skip the activity. See
> CHANGELOG "Serial SGS dropped dose + equipment-zone constraints (round 2)".
> Regression: `test_bugfix_regressions.py::TestSerialEnforcesDoseBudget`.

`_serial_check_feasibility` (5652-5787) has no dose block, and
`calculateSerialScheduleWithResources` commit path (6031-6048) never calls
`tracker.consume`. The parallel path checks `dose_trackers[...].fits(...)`
(3647-3669) and consumes on commit (1788/1835/3137). So cumulative
radiation-dose budgets are **unenforced** on the serial path.

**Worse — undetectable:** because serial never populates
`tracker.consumed_mrem`, it stays 0, so `validate_schedule`'s
`_check_dose_budgets` (which reads tracker state, not the replayed schedule)
reports `is_feasible=True` for a dose-blown schedule.

**Repro** (`/tmp/repro_serial_dose.py`): 3 independent dose activities, each 400
mRem, budget 500 mRem. Parallel places [A,B] = 800 mRem (blocks C by crew — but
800 > 500 is itself an over-commit, see **PD1** below); serial places
[A,B,C] = 1200 mRem against a 500 budget, and `validate_schedule.is_feasible =
True` with 0 dose violations.

### SC2 — Serial scheduler ignores equipment zone-affinity (CONFIRMED — ✅ FIXED 2026-09-03)
> **FIX (round 2):** `_serial_check_feasibility` now calls the shared
> `_equipment_zone_conflict(activity)` helper (also used by the refactored
> parallel `_fits_with_tentative`), rejecting an activity that would consume
> zone-locked equipment from a different zone. Zone affinity is time-invariant,
> so the same `_find_earliest_feasible_start_serial` → `None` → skip path
> applies. Regression: `test_bugfix_regressions.py::TestSerialEnforcesEquipmentZone`.

`_serial_check_feasibility` equipment block (5705-5716) checks count only;
`get_zone_id` never appears in 5652-5787. The parallel path enforces zone lock
via `equipment_pool.get_zone_id(...)` (3616-3626). So the serial path can place
an activity that consumes zone-locked equipment while sitting in a different
zone.

**Repro** (`/tmp/repro_serial_zone.py`): `EQ1` locked to `ZONE_1`; activity `A`
needs `EQ1` but declares `zone_ids=['ZONE_2']`. Parallel refuses A (correct);
serial places A, and `validate_schedule` then flags it infeasible
(`equipment_zone` violation = 1). (Here the validator *does* catch it — because
zone affinity is a static per-activity property, unlike the time-varying dose
tracker state in SC1.)

### PD1 — Parallel scheduler over-commits the dose budget within one time-step (CONFIRMED, HIGH — ✅ FIXED 2026-09-03)
> **FIX (round 2):** a transient per-time-step `dose_rem` overlay is threaded
> through `_fits_with_tentative` / `_apply_tentative` (like `res_rem`).
> `_apply_tentative` adds each selected activity's resolved dose to the overlay
> (same `rate × workers × eff` formula and substitution-resolved breakdown it
> already computes for crew), and `_dose_fits` gained an optional
> `extra_consumed` argument so the check is `committed + tentative + this ≤
> budget`. The overlay is transient — dose is still committed to the tracker
> only at start in `_update_activity_sets` — so analysis-only callers
> (`_compute_earliest_feasible`, `_can_schedule_activity`, serial) that pass no
> overlay never leak tentative dose into the real budget. All parallel selection
> loops (`max_use_res_ranked`/`max_use_res_shuffled`, `md_knapsack`,
> `LookAheadScheduler.select_activities`) build a fresh `dose_rem` per step. See
> CHANGELOG "parallel SGS over-commits dose within one time-step (round 2)".
> Regression: `test_bugfix_regressions.py::TestParallelEnforcesDoseBudget`.

The same-timestep analog of SC1, surfaced by the SC1 repro. The parallel
selection loop tentatively decrements the shared capacity snapshot
(`res_rem`/`eq_rem`/`loc_*`) as each candidate is picked so that later
candidates in the *same* step see the reduced capacity — but dose was never
tentatively decremented. `_apply_tentative` (pre-fix, 3808-3906) mutated crew,
equipment, location, consumables, and system-state, yet left `dose_trackers`
untouched; dose was charged only at commit (`_update_activity_sets`, 3144-3160).
So within one time-step every candidate's dose check in `_fits_with_tentative`
read the same pre-step `consumed_mrem`, and N dose activities that each fit
individually were all selected — committing N × dose against a one-activity
budget. Unlike SC1's *reporting* blind spot, this produces a genuinely
**infeasible** schedule.

**Repro** (`devLogs/repros/repro_pd1_parallel_dose.py`): 3 activities × 400 mRem
ready at t=0; crew admits all 3 (6 MECHANIC, 2 each); budget 600 mRem admits 1.
Pre-fix the loop placed all 3 (1200 mRem, ~2× over). Post-fix each strategy
places exactly 1 (400 ≤ 600).

### SC3 — Serial resource check omits skill substitution (CONFIRMED — ✅ FIXED round 2)
> **FIX (round 2):** the serial path now applies skill substitution in *both*
> halves, mirroring the parallel `_fits_with_tentative`/`_apply_tentative`.
> **Check:** `_serial_check_feasibility` draws a short skill's shortfall from
> `alternative_skill_types` before declaring infeasibility. **Commit +
> consumption:** two new helpers — `_resolve_serial_consumption(activity, start,
> profile)` computes the actual `{skill: workers}` breakdown drawn (primary then
> alternatives) and the commit stores it on `activity._actual_resources_for_start`;
> `_serial_consumed_at(skill, h, profile)` reads that resolved breakdown when
> present (declared requirements otherwise), so a borrowed alternative-skill unit
> is charged against later overlapping activities and cannot be double-drawn. The
> dose check consumes the resolved breakdown too. Result: the serial path both
> schedules the substitution-feasible activity *and* refuses to over-commit the
> shared skill. `_serial_dose_worker_map` is superseded (marked deprecated).
> Repro `repros/repro_sc3_serialsub.py` now prints "NOT REPRODUCED (fixed)".
> Regressions: `test_bugfix_regressions.py::TestSerialAppliesSkillSubstitution`
> (2 tests, mutation-verified in isolation — one per guard). Full CPM suite: 894
> passed. See CHANGELOG "Serial SGS now applies skill substitution (SC3)".
>
> **VERDICT (round 2, original):** CONFIRMED via repro (`repros/repro_sc3_serialsub.py`):
> an activity needing 1 ELEC with `alternative_skill_types=['MECH']`, pool
> ELEC=0/MECH=1, is scheduled by the parallel path (borrows MECH) but **dropped
> entirely by the serial path** (counts ELEC only). **Deferred, not fixed:** the
> defect is opposite-direction — the serial path is *over-conservative* (it can
> only delay/drop a feasible activity, never accept an infeasible one), so it is
> a schedule-optimality gap, not a safety violation, unlike every HIGH/med
> finding fixed so far. A *correct* fix is also more than a minor: `_serial_check_feasibility`
> (6004) counts only the exact declared skill, and the serial commit path
> (6383) records just `(act, start, end)` with **no substitution-resolved
> breakdown** — so adding substitution to the check alone would open an
> over-commit hole when two overlapping activities borrow the same alternative
> skill. A faithful fix needs substitution modeled in both the check and the
> commit/consumption (as the parallel path does at 3203/3635). Left for a
> dedicated pass.

pert.py:6004-6015 (serial) vs 3811-3823 (parallel falls back to
`req['alternative_skill_types']`). Opposite-direction error: serial can
*reject/delay a feasible* activity whose primary skill is short but which an
alternative skill (used by the parallel path) would satisfy. Produces a
suboptimal/over-delayed serial schedule, not an accepted-infeasible one.

### SC-m1 — `check_dependency_violations` has no float tolerance (CONFIRMED — ✅ FIXED round 2)
> **FIX (round 2):** `check_dependency_violations` now imports and applies the
> validator's `_PREC_TOL` (`succ_start < required_start - _PREC_TOL`), so the two
> precedence surfaces agree on a sub-minute gap; genuine violations beyond the
> 60 s grace are still flagged. The constant is imported from `schedule_validator`
> (single source of truth), not duplicated. Confirmed via
> `repros/repro_scm1_prectol.py` (B started 30 s early: `dep_feasible=False` vs
> `val_feasible=True`). Regression:
> `test_bugfix_regressions.py::TestDependencyCheckPrecTolerance`. See CHANGELOG
> "check_dependency_violations precedence tolerance (SC-m1)".

pert.py:5232: exact `succ_start < required_start`, whereas the validator's
`_check_precedence` uses `latest_start - _PREC_TOL` (216). At an exact
predecessor-end+lag boundary, float drift could over-report a violation.

### SC-m2 — `priority_calculation` `'minrr'` sort direction (❌ REFUTED — intended behavior)
> **VERDICT (round 2):** NOT A BUG. `'minrr'` is one of the `rr`/`avgrr`/`maxrr`/
> `minrr` family, all of which are aggregations of the **same** resource-
> requirement vector `rr_val` (pert.py:855-858: count / mean / max / min over the
> activity's skill demands). All four are sorted `reverse=True` (greatest-first)
> in `priority_calculation` (5775-5777) — the standard "greatest resource demand"
> heuristic. `minrr` names the min-*aggregation* of that vector, not a
> "schedule-least-first" sort intent; there is no separate least-resource rule it
> would be inverting. Direction is uniform with its siblings — no change.

pert.py:5775-5777: `'minrr'` is sorted `reverse=True` (largest-first) alongside
`maxrr`. Ambiguous heuristic semantics; yields a valid schedule regardless of
direction, not infeasibility. All other priority rules verified correct.

Agent C verified as **correct** (no bug): `_enforce_window_serial` (5611),
serial consumable check (5745-5764), serial system-state check (5766-5785),
`mobilization_lead_hours` parity between paths, and
`_find_earliest_feasible_start_serial` boundary coverage.

---

## Findings from background agent A (replan / partial-reset) — 2026-09-03

Agent A reviewed 1636–2634. Two confirmed HIGH bugs; one latent.

### RP1 — `_partial_reset` duration override on an in-progress activity leaves `endTime` stale (CONFIRMED, HIGH — ✅ FIXED 2026-09-03)
> **FIX (round 2):** the override branch now refreshes the frozen endTime,
> `act.endTime = max(current_abs, st + timedelta(hours=new_total))`
> (≡ `current_abs + remaining`; the clamp avoids placing endTime in the past on
> a shrink-below-elapsed override). Resource release, ongoing-list completion,
> and the event-queue seed now reflect the overridden duration. Regression:
> `test_bugfix_regressions.py::TestReplanDurationOverrideRefreshesEndTime`;
> repro `devLogs/repros/repro_rp1_endtime.py`. See CHANGELOG "replan: stale
> endTime + clone loses availability events (round 2)".

pert.py:1806-1819 (in-progress branch). A `duration_overrides` entry for an
in-progress activity updates `act.duration` and `act._remaining_duration` but
**never updates `act.endTime`** (set to `start + old_duration` during the
baseline run). Everything that drives actual completion reads `act.endTime`:
`_update_ongoing_list` (4826), the lag-event push (2278), the in-progress
completion seed in `_build_event_queue_from` (2126-2129). So the task is marked
done — and **releases its resources** — at the *old* end time, while CPM and
`_effective_duration` believe it runs longer. `duration_overrides` is a
first-class documented `replan()` feature.

**Failure (validated):** B(10) and D(6) both need the sole WELDER (cap 1);
baseline serializes B[0,10], D[10,16]. `replan(current_time_hours=2,
duration_overrides={'B':20})` → B's true span becomes [0,20] but stale
`endTime=10` frees the welder at 10h, so D runs [10,16] **concurrently** with
B's remaining work → 6h double-booking of a 1-unit resource → infeasible.

**Fix direction:** in the override branch also set
`act.endTime = st + timedelta(hours=new_total)` (≡ `current_abs + remaining`).

### RP2 — `clone_for_analysis` empties `_availability_events` and nothing repopulates it (CONFIRMED, HIGH — ✅ FIXED 2026-09-03)
> **FIX (round 2):** `clone_for_analysis` now calls
> `clone._precompute_availability_events()` after the pools are deep-copied
> (replacing the empty-frozenset hard-set). The method reads only the three
> pools, all set on the clone by that point. Regression:
> `test_bugfix_regressions.py::TestCloneRepopulatesAvailabilityEvents`; repro
> `devLogs/repros/repro_rp2_clone_events.py`.

pert.py:2621 hard-sets `clone._availability_events = frozenset()`.
`_precompute_availability_events()` is called only from `__init__` (178) and
from `replan()` guarded by `if resource_updates or equipment_updates`
(2484-2485). The clone bypasses `__init__` (`object.__new__`), and neither
`calculateScheduleWithResources` nor `generateInfo()` recomputes the set — so a
clone with **time-varying** resource/equipment/location pools loses all
availability-boundary wake-ups and can dead-lock into an incomplete schedule.
The line-2620 comment ("recomputed on demand if scheduling runs") is false.

**Failure (validated):** WELDER pool 0 for [0,10h] then 2. START→A(5, needs 1
WELDER)→END. Original completes 3/3 @15h. Clone (`clone_for_analysis();
generateInfo(); calculateScheduleWithResources(...)`) exhausts its heap at
t≈5h, logs "possible deadlock", completes only **1/3**. Manually calling
`clone._precompute_availability_events()` first restores 3/3 @15h. Same gap
hits `clone.replan(t)` with no resource/equipment updates — the documented
what-if replan use case.

**Fix direction:** call `self._precompute_availability_events()` inside
`clone_for_analysis` after pools are deep-copied (or unconditionally in
`replan()` / `calculateScheduleWithResources`).

### RP-latent — `_generate_info_from` doesn't recompute rule-based priority metrics (PLAUSIBLE, not reachable via public `replan()`)
pert.py:1973 recomputes ES/EF/LS/LF/slack but not `mts/mtp/grpw/grd/rr/…`,
which `resetInfo()` (called during `_inject_activities`) zeroes. A replan using
one of those priority rules would sort candidates on all-zero values. Public
`replan()` never forwards a `priority_rule` (`value_mode` is always
`TF_based`/`external`), so latent only.

Agent A verified as **correct** (no bug): state leakage across successive
`replan()` calls, consumable restock idempotency at the replan boundary,
in-progress `_remaining_duration` without override, dose/system-state re-commit
for frozen activities, `clone_for_analysis` deep-copy isolation, and
precedence+lag on injection/reactivation.

---

## Cross-cutting theme

C2, C2b, and B4 are the **same root defect in three places**: time-varying
resource availability is sampled at a single instant (activity start / pool
start) rather than across the interval where it matters. The scheduler
(`_build_capacity_snapshots`), the independent validator
(`_check_crew_feasibility`), and the augmented-graph builder
(`_build_augmented_graph`) each omit pool availability breakpoints from their
grid/sweep. Any real fix should add those breakpoints uniformly.
