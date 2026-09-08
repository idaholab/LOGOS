# Oracle Completeness — Is `assert_valid_schedule` Actually Complete?

**Date:** 2026-09-07
**Author:** Claude Code (session with D. Mandelli)
**Scope:** `src/CPM/schedule_validator.py` (the independent feasibility oracle,
exposed to tests as `assert_valid_schedule` via `conftest.py`), its own tests in
`tests/unit_tests/CPM/test_schedule_validator.py`, and the property harness in
`tests/unit_tests/CPM/test_property_based.py`.
**Branch:** `mandd/res_opt`
**Status:** ASSESSMENT + BACKLOG. §6.1, §6.2, §6.3 are now **DONE** (breadth
trio landed on `mandd/res_opt`; see the status notes in each item and §8);
§6.4–6.6 remain open for later PRs. Originally the honest answer to "have we
double-checked the checker is complete?" plus a prioritized plan to raise that
confidence, all additive test work.
**Companion:** extends the fuzz-and-freeze program documented in
`RCPSP_ROBUSTNESS_2026-09-07.md` (§10 backlog). Treat these as a §11.

---

## 0. The question

The whole fuzz-and-freeze program rests on one assumption: that
`assert_valid_schedule` (→ `schedule_validator.validate_schedule`) correctly
flags **every** infeasible schedule the engine can emit. If the oracle is too
lax, the property tests pass on schedules that are actually broken, and the
"silent wrong answer" class the project exists to eliminate slips through the
one net meant to catch it.

So: **have we double-checked that this checker is complete?**

## 1. Short answer — no.

We have established something weaker and worth naming precisely. The oracle is:

- **Broad** — 13 hard feasibility checks (+ 1 warnings pass), covering
  completeness, durations, precedence (incl. lags), time-windows, hold-points,
  crew, equipment, equipment-zone affinity, location, consumables,
  shift-calendar, dose budgets, and system-states.
- **Per-type sensitivity-tested** — for **12 of the 13** checks,
  `test_schedule_validator.py` does genuine fault injection: build a valid
  schedule, corrupt it (tamper an `endTime`, force a crew overlap, move a task
  to the wrong zone, shrink inventory post-schedule, inject a window/lag), and
  assert the correct violation fires — plus a matching happy-path negative test.
  This proves the checks are not no-ops. It is a real strength.

But "each check catches its one hand-crafted corruption" is **not** the same as
"the oracle catches every infeasible schedule the engine can emit." Three gaps
stand between those two claims.

## 2. Gap 1 — the oracle is NOT fully independent of the engine *(deepest issue)*

The oracle's *logic* (sweep-line over events, replay of the schedule) is
independent. Its *data model* is not. The resource checks call the engine's own
primitives:

- `crew_pool.get_availability_in_range()`
- `equipment_pool.get_availability_in_range()`
- `location_pool.get_capacity_in_range()`
- `pert._resolve_windows(act)`
- `_crew_demand()` prefers the engine-populated `_actual_resources` over the
  declared `required_resources`.

Consequence: a bug **inside** one of those shared primitives is invisible to the
oracle, because the oracle asks the engine the same question and gets the same
wrong answer. Concretely, any of these would pass validation:

- an off-by-one in a **time-varying availability profile** (shared by both),
- a **window-resolution** error in `_resolve_windows` (shared by both),
- the engine **forgetting to record a substitution** in `_actual_resources` —
  the crew check then validates against a too-low demand and passes.

This is the classic correlated blind spot: two "independent" checkers that share
a subroutine cannot catch a bug in that subroutine. Everything fuzz-and-freeze
has found so far lives *outside* these primitives; nothing has probed *inside*
them.

## 3. Gap 2 — the fuzzer exercises only ~4 of the 13 checks

The property generator (`test_property_based.py`) builds precedence + lags + a
single renewable crew skill. So under randomized exploration only four checks
are actually stressed with thousands of instances:

- completeness, durations, precedence (+ lags), crew.

The other **nine** — time-windows, hold-points, equipment, equipment-zone,
location, consumables, shift-calendar, dose, system-states — rest *entirely* on
the single hand-injected case each in `test_schedule_validator.py`. Those nine
could be subtly wrong in a way one example doesn't reveal, and no current test
would catch it. The nightly `thorough` run (2000 examples) does not help: it is
2000 examples of the same narrow shape.

## 4. Gap 3 — known coverage holes

- **Dose budget: zero tests.** Confirmed — no fault injection, no happy-path,
  nothing in `test_schedule_validator.py`. It is the one hard check with no test
  at all.
- **Tolerance masking:** `_DUR_TOL = 60 s` silently passes any duration error
  under a minute, while precedence uses `_PREC_TOL = 1 ms`. For a
  microsecond-quantized engine, 60 s is a large hole — a real sub-minute
  duration bug (exactly the bug-family #9 class already found once) would pass.
- **Constraint *types* the engine models but the oracle may not check as
  feasibility:** multimode (is the *selected mode* valid/consistent?),
  substitution (was the substituted skill actually *legal* — in the allowed
  alternatives — or just recorded?), and safety-function / interaction
  constraints beyond `system_state`. Some may reduce to checks that already
  exist; some may not. The mapping has not been audited.

## 5. What is genuinely solid (don't lose this)

- The lag check is **real, not a no-op**: the oracle reads `pred.successor_lags`
  (the *declared* input), and the engine independently mirrors that same source
  into `lag_dict` (`pert.py:267-269`, `pert.py:2240-2242`). The oracle validates
  against the declared input — the more independent of the two choices.
- Resource sweeps check the **minimum** availability over each `[t, next_event)`
  interval (finding C2b) and skip slivers `<= _PREC_TOL` (bug-family #5). That
  logic is independent and correct.
- The 12 fault-injection tests are the model to scale up — see §6.1.

## 6. Backlog — raising completeness confidence *(all additive; no engine risk)*

Same discipline as §10 of the robustness log: additive test/generator work that
cannot destabilize the production engine. A new red is a success. Priority order:

### 6.1 Oracle mutation testing under Hypothesis *(highest leverage)* — **DONE**
Generalize the 12 hand-injected fault tests into a fuzzed sensitivity test:
generate a feasible schedule, apply a random **feasibility-breaking** mutation
(shift a start into an overlap, over-book a pool, blow a window, exceed a dose
budget), and assert the oracle flags it with the expected violation type. This
directly *measures* the sensitivity question §0 asks, across many instances
instead of one per type. This is the single most valuable item.

> **Status — DONE.** `tests/unit_tests/CPM/test_oracle_mutation.py`. A Hypothesis
> test (`test_oracle_detects_mutation`) parametrized over a 15-scenario table
> covering **all 13 hard checks** (precedence and location twice — direct + lag,
> tasks + workers). Each scenario builds a feasible-by-construction schedule
> (fuzzing durations 1–20 h and crew 1–4), asserts the oracle accepts it
> **pre-mutation** (soundness), applies one guaranteed-infeasible mutation, then
> asserts the expected `Violation.type` fires (sensitivity). A meta-guard
> (`test_scenarios_cover_every_hard_check`) fails if a new validator check ever
> lacks a scenario. Registered as `[./cpm_oracle_mutation]`. No engine reds
> surfaced; two harness-construction issues found and fixed during bring-up
> (system_state must build with *compatible* states then flip; the dose-tracked
> skill is a *consumable* that depletes and whose budget the engine enforces at
> placement, so the dose scenario is single-activity with a low dose-rate).

### 6.2 Extend the property generator to emit the other constraint types — **DONE (partial)**
Add equipment, location, time-windows, system-states, and consumables to the
generator so checks 5–13 are fuzzed *at all* (closes Gap 2). Reuse the pool
builders already in `test_invariants.py`.

> **Status — DONE for equipment, location (tasks + workers), and time-windows.**
> `tests/unit_tests/CPM/test_property_based.py` Phase 3:
> `rcpsp_multi_constraint_instance` + `build_multi_constraint_pert` +
> `test_multi_constraint_schedule_is_valid` (over all 5 SGS). Every capacity is
> set to the exact upper bound on concurrent demand and every window wide open,
> so the instance is **feasible by construction** — this is the *no-false-positive*
> (soundness) side; the infeasible/sensitivity side is 6.1's job. **Still open:**
> system-states and consumables are awkward to fuzz feasibly by construction
> (consumables deplete; states need a compatible assignment) — both are covered
> on the sensitivity side by 6.1, but not yet on the fuzzed feasible side.

### 6.3 Add the dose fault-injection test *(quick win)* — **DONE**
Mirror the consumable pattern: build a schedule with a dose budget, exceed it
post-schedule, assert `dose` fires; add the happy-path negative. Closes the one
zero-coverage check (Gap 3).

> **Status — DONE.** `TestDoseBudgets` in `test_schedule_validator.py`:
> `test_over_budget_dose_detected` (build with a dose budget, populate
> `dose_trackers` **before** scheduling, then bump `consumed_mrem` above
> `total_budget_mrem` → `dose` fires) and `test_within_budget_no_violation`
> (happy-path negative). Gap 3's zero-coverage check is closed.

### 6.4 Decouple the resource check from shared engine primitives — **DONE (core: availability + windows)**
Recompute availability from the raw pool definition inside the oracle, rather
than calling `get_availability_in_range` / `_resolve_windows`, so a bug in a
shared primitive can no longer hide (closes Gap 1). Biggest effort; biggest
blind-spot removal. Watch that the independent parse doesn't just reintroduce
the same assumption.

> **Status — DONE for touch-points 1–4 (crew / equipment / location availability
> + time-windows).** `schedule_validator.py` now reduces the min-over-overlap
> availability and resolves windows itself, reading the raw declared data:
> three pure helpers — `_min_avail_over` (crew/equipment), `_min_location_cap_over`
> (tasks + the `None`-when-unconstrained workers rule), `_resolve_windows_indep` —
> each an exact mirror of its engine counterpart, wired into the four former call
> sites (`crew_pool` / `equipment_pool` / `location_pool` query methods and
> `pert._resolve_windows`). The oracle reads each availability object's
> `.periods` via `get_all_periods()` — a pure accessor the scheduler never
> mutates on any scheduled path (`update_from_hour`/`snapshot`/`restore` are
> replan-only, never called from `pert.py`) — so it still validates against the
> availability the engine used, but a buggy *reduction* can no longer hide an
> infeasibility. Pool container/enumeration accessors (`.resources`/`.equipment`/
> `.locations` dicts, `get_all_location_ids()`) are plain key-lookups, not the
> blind spot, so they stay.
>
> **Proof:** `tests/unit_tests/CPM/test_oracle_decoupling.py` (new,
> `[./cpm_oracle_decoupling]`). Five **differential blind-spot** tests build a
> genuinely-infeasible schedule, monkeypatch the engine primitive to *lie*
> (report `MASK` capacity / an empty window), assert the patched primitive really
> returns the mask, and assert the oracle **still** fires the expected
> `Violation.type` — which it can only do having stopped trusting the primitive.
> Hypothesis **parity** tests pin each helper to a brute-force, midpoint-sampled
> ground truth (independent of both engine and oracle), plus an equivalence check
> (`helper == engine primitive` on random valid pools) documenting that no
> behavior shipped on feasible data. The entire pre-existing oracle suite stays
> green **without edits** (behavior-preserving); full CPM suite green cold.
>
> **Deferred (later PRs), still Gap 1:** touch-point 5 — `_crew_demand`'s
> preference for the engine-populated `_actual_resources` (a bug there validates
> against a too-low demand; needs independent substitution-legality resolution,
> overlaps 6.6) — and touch-point 6 — `equipment_pool.get_zone_id` in the
> equipment-zone check. Both are *demand*/*zone* questions, not availability or
> windows, so they were out of this PR's approved scope.

### 6.5 Reconsider `_DUR_TOL`
Justify 60 s against the engine's actual time precision, or tighten it. If a
generous tolerance is genuinely needed for a real reason (e.g. float
accumulation over long horizons), document that reason inline.

### 6.6 Audit constraint-type coverage
Map every engine-modeled constraint (multimode, substitution legality,
safety-function, interactions) to a validator check, and either add the missing
check or document why it reduces to an existing one.

## 7. Bottom line

For the slice the property tests actually fuzz — precedence, lags, and
single-skill renewable crew — the oracle is well-exercised and trustworthy.
**Outside that slice, completeness is not established.** The oracle today is
"broad, per-type sanity-checked, but unfuzzed for 9 of 13 checks and
structurally blind to bugs in the engine primitives it borrows." Items 6.1–6.3
would move the needle most for the least risk.

**Update (breadth trio landed):** 6.1–6.3 are now done. Oracle *sensitivity* is
fuzzed across **all 13 hard checks** (6.1), and the feasible *soundness* side is
fuzzed for equipment/location/time-windows on top of the original crew slice
(6.2); dose has fault-injection + happy-path coverage (6.3).

**Update (Gap 1, core):** **6.4 is now done for its core — touch-points 1–4
(crew / equipment / location availability + time-windows).** The oracle no longer
asks the engine "how much is available?" or "what are the windows?"; it recomputes
both from the raw declared data, and differential tests prove it still catches an
infeasibility a *lying* primitive would hide. The structural blind spot is
therefore closed for availability and windows. What remains: the two demand/zone
touch-points of Gap 1 (5 = `_actual_resources`, 6 = `get_zone_id`), 6.5
(`_DUR_TOL`), 6.6 (constraint-type audit), and the two feasible-side fuzz gaps
6.2 left open (system-states, consumables). See §8.

## 8. What landed (breadth trio, `mandd/res_opt`)

- **6.1 — `tests/unit_tests/CPM/test_oracle_mutation.py` (new).** Fuzzed
  build→mutate→assert harness, 15 scenarios over all 13 hard checks + a
  coverage meta-guard. Registered `[./cpm_oracle_mutation]`.
- **6.2 — `tests/unit_tests/CPM/test_property_based.py` (Phase 3).**
  `rcpsp_multi_constraint_instance` / `build_multi_constraint_pert` /
  `test_multi_constraint_schedule_is_valid` — feasible-by-construction
  equipment + location + time-window fuzzing over all 5 SGS.
- **6.3 — `tests/unit_tests/CPM/test_schedule_validator.py`.** `TestDoseBudgets`
  (over-budget fault injection + happy-path negative).

All test-only, additive; **no engine or oracle behavior changed**, and no
fuzz-and-freeze reds surfaced during bring-up (only test-construction fixes).

## 8b. What landed (Gap-1 core decoupling, `mandd/res_opt`)

- **6.4 (core) — `src/CPM/schedule_validator.py`.** Three pure helpers
  (`_min_avail_over`, `_min_location_cap_over`, `_resolve_windows_indep`) that
  recompute crew/equipment/location availability and time-windows from the raw
  declared data, replacing the four former engine-primitive call sites. The only
  production change; **behavior-preserving on feasible data** (the whole existing
  oracle suite stays green with no edits).
- **6.4 (core) — `tests/unit_tests/CPM/test_oracle_decoupling.py` (new).**
  Differential blind-spot tests (lying-primitive → oracle still fires) + Hypothesis
  parity/equivalence tests. Registered `[./cpm_oracle_decoupling]`.

This is the first production change in the program (a decoupling, not a behavior
change) and it closes Gap 1 for **availability + windows**. Still open: the two
demand/zone touch-points of Gap 1 — **touch-point 5** (`_actual_resources` /
substitution demand, overlaps 6.6) and **touch-point 6** (`get_zone_id`) —
plus **6.5** (`_DUR_TOL=60s` review), **6.6** (constraint-type audit), and the
feasible-side fuzzing of system-states + consumables deferred from 6.2.
