# pert.py correctness-review repros

Standalone reproduction scripts for the bugs catalogued in
[`../PERT_MANUAL_REVIEW_2026-09-03.md`](../PERT_MANUAL_REVIEW_2026-09-03.md).
Each script is self-contained, prints a `=== VERDICT ===`, and computes the
`src/` path relative to its own location — run from anywhere:

```bash
python src/CPM/devLogs/repros/repro_timevarying.py
```

These are **evidence / regression seeds**, not unit tests. They are the raw
material for the proper `pytest` regressions to be written during the fix phase;
once a bug is fixed, its repro's verdict should flip (or the behaviour it prints
should become correct).

| Script | Finding | Severity | Demonstrates |
|---|---|---|---|
| `repro_timevarying.py` | **C2 + C2b** (✅ fixed) | HIGH | mid-activity availability drop → crew over-commit the sparse grid admits and the validator can't see. Now "NOT REPRODUCED"; the once-"entangled C3" line is a precedence block (A genuinely infeasible), not starvation |
| `repro_earlybreak.py` | **C3** (✅ fixed) | HIGH | `max_use_res_ranked` early-break delayed a zero-crew activity (makespan 10h vs optimal 6h), isolated on a constant pool. Now "NOT REPRODUCED" (M starts at t=0, makespan 6h) |
| `repro_serial_dose.py` | **SC1** (✅ fixed) | HIGH | serial once placed 1200 mRem against a 500 mRem budget with the validator blind to it. Now serial places 1 of 3 (400 ≤ 500), tracker populated, B/C skipped. Parallel now also places 1 (was A+B=800 — see PD1). |
| `repro_pd1_parallel_dose.py` | **PD1** (✅ fixed) | HIGH | parallel SGS over-committed dose within one time-step: `_apply_tentative` never charged the tracker, so every same-step candidate's dose check read the untouched budget. 3×400 mRem ready at t=0, crew admits 3, budget admits 1 → placed all 3 (1200 vs 600 mRem, infeasible). Now a transient per-time-step dose overlay blocks it; each strategy places exactly 1. "NOT REPRODUCED (fixed)" |
| `repro_serial_zone.py` | **SC2** (✅ fixed) | HIGH | serial once placed an activity using zone-locked equipment from the wrong zone. Now serial refuses A (0 `equipment_zone` violations); matches parallel |
| `repro_lp.py` | **B1** (✅ fixed) | med | `_longest_path_in_augmented` returned the wrong chain (multi-source, no unifying START): short source Q as topo[0] gave `Q→C` (len 2) not `P→C` (len 101). Now seeds every source with its duration → `P→C`. "NOT REPRODUCED (fixed)" |
| `repro_lag.py` | **B2** (✅ fixed) | med | `_splice_buffer_activity` dropped the finish-to-start lag (project EF 25→20, orphan `(A,B)` left dangling). Now the 5 h lag moves onto `A→BUF` and EF stays 25. "NOT REPRODUCED (fixed)" |
| `repro_b3_zeroprefix.py` | **B3** (✅ fixed) | low | longest-path DP dropped a leading zero-duration node: `START(0)→M(0)→A(5)→END` reconstructed as `['M','A','END']`. Same B1 fix (sources seeded to duration, others −inf) retains the prefix → `['START','M','A','END']`. "NOT REPRODUCED (fixed)" |
| `repro_c1_window.py` | **C1** (✅ fixed) | med | windowed activity's ES not propagated forward → wrong successor ES/slack + false infeasibility warning (reported values only; schedule was correct). Now `_apply_time_windows` re-relaxes forward (propagating the raised EF) and re-anchors the backward pass to the extended project end → A.ES=10 EF=14, B.ES=14 EF=18, no false warning. "NOT REPRODUCED" |
| `repro_rp1_endtime.py` | **RP1** (✅ fixed) | HIGH | replan duration-override on an in-progress activity once left `endTime` stale (`B(10)`/`D(6)` sharing a 1-unit WELDER, `replan(t=2, {'B':20})` → 6h double-booking). Now `endTime` tracks the overridden duration; D waits for B's true release; "NOT REPRODUCED (fixed)" |
| `repro_rp2_clone_events.py` | **RP2** (✅ fixed) | HIGH | `clone_for_analysis` once emptied `_availability_events` → a time-varying clone deadlocked (WELDER 0 for [0,10h] then 2; clone 1/3 vs original 3/3). Now the clone recomputes them and completes 3/3; "NOT REPRODUCED (fixed)" |
| `repro_b4_skipgate.py` | **B4** (✅ fixed) | med | augmented-graph `2*max_demand ≥ avail` skip gate sampled the pool at `startTime`, so a saturating overlap whose window dips below the startTime level was waved through → binding resource-flow arc dropped → chain under-estimated. Now the gate samples `get_availability_in_range` (horizon min) → arc retained. "NOT REPRODUCED (fixed)" |
| `repro_m1_topk.py` | **M-1** (✅ fixed) | med | `_rank_by_value_top_k` estimated `k = max_slots·8` from availability at `startTime`; when the pool grows later, `k` truncates candidates placeable now and SGS (one call per event, no same-time re-entry) defers them → 20 siblings launched in 3 waves (makespan 25h vs optimal 15h). Now `max_slots` is sampled at the event's `time_index` → all 20 launch at once. "NOT REPRODUCED (fixed)" |
| `repro_scm1_prectol.py` | **SC-m1** (✅ fixed) | low | `check_dependency_violations` compared precedence strictly while the validator allows a 60s `_PREC_TOL` grace → a successor 30s early was "infeasible" here but "feasible" there. Now shares `_PREC_TOL`; both surfaces agree. "NOT REPRODUCED (fixed)" |
| `repro_sc3_serialsub.py` | **SC3** (✅ fixed) | low | serial feasibility once counted only the exact declared skill (no substitution), so an ELEC activity that can borrow MECH was scheduled by the parallel path but **dropped** by the serial path. Now the serial check draws shortfalls from `alternative_skill_types` and the commit records the resolved breakdown (`_resolve_serial_consumption` → `_actual_resources_for_start`, read back by `_serial_consumed_at`), so it schedules the activity without over-committing the shared skill. "NOT REPRODUCED (fixed)". |
| `repro_b5_locpairs.py` | **B5** (✅ fixed) | low | `_build_augmented_graph`'s location block scanned only consecutive start-sorted pairs, so a non-adjacent overlapping pair at a `max_tasks==1` zone (`A[0,10]` overlaps `C[3,13]`, with short `B[1,2]` between) got no serialization arc directly or transitively. Now scans all pairs per zone (early-break, DAG-preserving). **Function-level repro** (sets times directly): latent behind a correct scheduler — the block only fires on an overlap a correct SGS never produces, so it affects only the constrained-chain/TF analytics. "NOT REPRODUCED (fixed)". |
