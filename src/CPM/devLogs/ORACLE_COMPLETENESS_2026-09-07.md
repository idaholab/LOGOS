# Oracle Completeness — Is `assert_valid_schedule` Actually Complete?

**Date:** 2026-09-07
**Author:** Claude Code (session with D. Mandelli)
**Scope:** `src/CPM/schedule_validator.py` (the independent feasibility oracle,
exposed to tests as `assert_valid_schedule` via `conftest.py`), its own tests in
`tests/unit_tests/CPM/test_schedule_validator.py`, and the property harness in
`tests/unit_tests/CPM/test_property_based.py`.
**Branch:** `mandd/res_opt`
**Status:** ASSESSMENT + BACKLOG — **COMPLETE**. §6.1, §6.2, §6.3 are now **DONE**
(breadth trio landed on `mandd/res_opt`; see the status notes in each item and §8).
§6.4 is **DONE — Gap 1 fully closed** (touch-points 1–6: availability + windows +
crew-substitution legality + equipment-zone; see §8b). §6.6 is **DONE** — the
constraint-type audit found multimode/execution-mode the one genuine gap and
closed it with `_check_mode_consistency` (type `mode`; the 15th hard check);
safety-function and interaction constraints reduce to existing checks. §6.2 is
**fully DONE** — the feasible side of consumables and system-states is fuzzed
too, closing the last soundness-side gap. §6.5 (`_DUR_TOL`) is now **DONE** —
tightened 60 s → 1 ms after proving the duration delta is identically 0 on the
normal path (the engine derives `endTime` from `startTime`+`duration`, and the
oracle re-derives the identical expression; see §6.5), frozen by a 56.25 s
regression test that is red under the old 60 s. **The oracle-completeness backlog
is now fully closed — all of §6.1–§6.6 are DONE.** Originally the honest answer to
"have we double-checked the checker is complete?" plus a prioritized plan to raise
that confidence, all additive test work.
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

- **Broad** — 15 hard feasibility checks (+ 1 warnings pass), covering
  completeness, durations, precedence (incl. lags), time-windows, hold-points,
  crew, crew-substitution legality, execution-mode consistency, equipment,
  equipment-zone affinity, location, consumables, shift-calendar, dose budgets,
  and system-states.
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

> **Update (Gap 1 progress).** The four availability/window primitives are now
> **decoupled** — the oracle recomputes them from raw declared data (§6.4 core,
> touch-points 1–4). The `_crew_demand`/`_actual_resources` trust — including the
> "forgetting to record a substitution" case above — is now **certified**: a new
> `_check_substitution_legality` independently verifies the committed breakdown is
> a legal, demand-conserving resolution of the *declared* requirements (§6.4
> touch-point 5, §8b). The last Gap-1 borrow — `get_zone_id` in the equipment-zone
> check (touch-point 6) — is now decoupled too: the check reads the declared
> `zone_id` off the equipment object directly. **Gap 1 is fully closed.**

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
- **Tolerance masking — NOW CLOSED (§6.5).** `_DUR_TOL` was `60 s` and silently
  passed any duration error under a minute, while precedence uses `_PREC_TOL = 1 ms`.
  For a microsecond-quantized engine, 60 s was a large hole — a real sub-minute
  duration bug (exactly the bug-family #9 class already found once, at 56.25 s)
  would pass. Now tightened to `1 ms` (see §6.5): the duration delta is identically
  0 on the normal path, so 1 ms is all the grace warranted, and bug-family #9's
  56.25 s discrepancy would now be caught (frozen by
  `test_schedule_validator.py::TestDuration::test_subminute_discrepancy_detected`).
- **Constraint *types* the engine models but the oracle may not check as
  feasibility:** ~~multimode (is the *selected mode* valid/consistent?)~~, and
  safety-function / interaction constraints beyond `system_state`. **— now
  audited (§6.6):** multimode is **checked** — `_check_mode_consistency` certifies
  the live profile faithfully realizes the committed `selected_mode_id` against
  the declared `modes`; safety-function reduces to `system_state` (the engine
  models no first-class safety-function pool); interactions beyond `system_state`
  are not enforced by the engine (only FS precedence + system-state exclusion).
  ~~substitution (was the substituted skill actually *legal* — in the
  allowed alternatives — or just recorded?)~~ **— now checked:**
  `_check_substitution_legality` verifies the committed breakdown is a legal,
  demand-conserving resolution of the declared requirements (§6.4 touch-point 5).

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

### 6.2 Extend the property generator to emit the other constraint types — **DONE**
Add equipment, location, time-windows, system-states, and consumables to the
generator so checks 5–13 are fuzzed *at all* (closes Gap 2). Reuse the pool
builders already in `test_invariants.py`.

> **Status — DONE for all five constraint types: equipment, location (tasks +
> workers), time-windows, consumables, and system-states.**
> `tests/unit_tests/CPM/test_property_based.py` Phase 3:
> `rcpsp_multi_constraint_instance` + `build_multi_constraint_pert` +
> `test_multi_constraint_schedule_is_valid` (over all 5 SGS). Every capacity is
> set to the exact upper bound on concurrent demand and every window wide open,
> so the instance is **feasible by construction** — this is the *no-false-positive*
> (soundness) side; the infeasible/sensitivity side is 6.1's job. The two
> awkward dimensions are now handled by a per-item / per-system invariant that
> holds regardless of DAG shape or SGS: **consumables** — initial stock = TOTAL
> demand across all activities, so deduct-on-start replay (which resets to `items`)
> can never go negative in any order; **system-states** — every state-touching
> activity requires the SAME state on the SAME system (a compatible shared lock),
> so any overlap is legal in any topology. No restocks and a single shared
> state keep the guarantee topology-independent; the richer variants (restock
> timing, forced-serial multi-state) are deferred (see the out-of-scope notes).

### 6.3 Add the dose fault-injection test *(quick win)* — **DONE**
Mirror the consumable pattern: build a schedule with a dose budget, exceed it
post-schedule, assert `dose` fires; add the happy-path negative. Closes the one
zero-coverage check (Gap 3).

> **Status — DONE.** `TestDoseBudgets` in `test_schedule_validator.py`:
> `test_over_budget_dose_detected` (build with a dose budget, populate
> `dose_trackers` **before** scheduling, then bump `consumed_mrem` above
> `total_budget_mrem` → `dose` fires) and `test_within_budget_no_violation`
> (happy-path negative). Gap 3's zero-coverage check is closed.

### 6.4 Decouple the resource check from shared engine primitives — **DONE (Gap 1 fully closed: touch-points 1–6)**
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
> **Update — touch-point 5 now DONE.** `_crew_demand` still reads the
> engine-committed `_actual_resources` (it is raw observable state, like
> `.periods` — the *split* is a scheduling decision the oracle cannot re-derive),
> but a new **`_check_substitution_legality`** independently *certifies* that
> breakdown is a legal, demand-conserving resolution of the *declared*
> `required_resources` — reading declared data only, never an engine primitive.
> It fires a new `substitution` violation when the recorded workers don't sum to
> the declared demand (**non-conservation** — the "forgot to record a
> substitution" bug), or admit no legal skill→requirement routing (**illegal
> substitution**). Legality is a bipartite **max-flow** saturation, *not* a
> per-skill membership test: allowed skill sets can overlap across requirements,
> so an all-"allowed" breakdown can still be unroutable (e.g. required
> `[{MECH,1},{ELEC,1}]` with recorded `{MECH:2}`). On correct engine output the
> resolution is legal + conserving by construction, so the check is silent —
> **behavior-preserving**, like the availability/window decoupling. Proof:
> `test_oracle_decoupling.py` §3 (differential blind-spot tests — the lie is
> invisible to `_crew_demand` yet caught by the new check — plus max-flow parity
> against a brute-force Hall-condition ground truth); `test_schedule_validator.py`
> `TestSubstitutionLegality`; and a `substitution` scenario in
> `test_oracle_mutation.py` (with the meta-guard extended to 14 checks).
>
> **Documented boundary (out of scope):** if the engine records a *clean, legal*
> breakdown while internally having run a *different, dirty* one, `_actual_resources`
> is the only observable state and the oracle treats it as truth; the named bug
> ("forgetting to record") is what conservation catches.
>
> **Update — touch-point 6 now DONE (Gap 1 fully closed).** The equipment-zone
> check no longer reads the zone through `equipment_pool.get_zone_id` — the same
> primitive the engine's own placement gate (`_equipment_zone_conflict`) consults,
> so one shared read could mis-place the activity *and* blind the oracle in
> lockstep. It now reads the declared `zone_id` off the equipment object directly
> (`equipment_pool.equipment.get(eq_id).zone_id`, missing id → `None` =
> unconstrained, matching the pool method's own contract) — a set-once,
> never-mutated field, read like `.periods`. Behavior-preserving (the primitive
> currently returns exactly this value); a differential lying-primitive test
> (`get_zone_id → None`) confirms the oracle still fires `equipment_zone`. With
> this, the oracle routes **no** availability / window / demand / zone judgement
> through an engine primitive. Proof: `test_oracle_decoupling.py` §4.

### 6.5 Reconsider `_DUR_TOL` — **DONE**
The mandate was "justify 60 s against the engine's actual time precision, or
tighten it." The investigation resolved it decisively: **tighten to 1 ms**, no
generous tolerance is warranted.

For every activity the oracle actually checks, the duration delta is
**identically 0.0** — not merely "~ms":

- The engine sets `endTime = startTime + timedelta(hours=duration)`
  (`activity.setActualStartTime`, the only normal-path writer), and
  `_check_durations` re-derives `expected` with the *identical* expression, so
  `actual − expected ≡ 0` bit-for-bit (naive datetimes, exact integer-µs arithmetic).
- Shift calendars and mobilization lead only gate *when* an activity may start;
  an activity runs contiguously through off-shift periods, so idle gaps fall
  *between* activities, never within one → 0 contribution. No calendar adjustment
  is needed.
- The hours→timedelta conversion is a single exact microsecond quantization that
  cancels on both sides; no float chaining over the horizon.
- No preemption/splitting exists.
- The one genuine in-activity divergence — an in-progress activity whose duration
  was clamped by a replan `duration_override` (`pert.py` ~2103-2126) — is already
  excluded by the `_remaining_duration` skip in `_check_durations`, **not** by this
  tolerance.

So 60 s was an unjustified pre-fix leftover (it was left at 60 s when `_PREC_TOL`
was tightened 60 s → 1 ms in commit `ed4c79de` for bug-family #9); worse, it sat
*above* bug-family #9's own 56.25 s discrepancy and would have masked exactly that
class of bug. **Change:** `_DUR_TOL = timedelta(milliseconds=1)` with an inline
rationale block (matching `_EVENT_EPSILON` / `_PREC_TOL`), plus a docstring note
that the difference is exactly 0 on the normal path. **Freeze:**
`test_schedule_validator.py::TestDuration::test_subminute_discrepancy_detected`
corrupts an endTime by −56.25 s (shrinking, so the fault stays isolated to
`duration`), asserts `'duration'` is flagged — green at 1 ms, verified red under
the old 60 s. Full CPM suite green cold (1050 passed, 3 skipped); thorough property
run green — 1 ms produces no false positives across shift-calendar, substitution,
multimode, and fuzzed schedules. This closes the oracle-completeness backlog.

### 6.6 Audit constraint-type coverage — **DONE**
Map every engine-modeled constraint (multimode, substitution legality,
safety-function, interactions) to a validator check, and either add the missing
check or document why it reduces to an existing one.

> **Status — DONE.** The audit mapped each engine-enforced constraint to an oracle
> check. Outcome:
>
> - **Substitution legality — checked** (§6.4 touch-point 5:
>   `_check_substitution_legality`).
> - **Multimode / execution-mode consistency — the one genuine gap; now checked.**
>   `Activity.set_mode` bakes a declared mode's profile into the activity's *live*
>   fields and stamps `selected_mode_id`; the scheduling loop reads only the live
>   fields and never re-reads `modes`. A buggy/partial mode application (a mixed
>   profile), a directly-stamped `selected_mode_id`, or a post-selection mutation
>   would therefore be scheduled and validated with no notion that modes exist.
>   `_check_mode_consistency` (type `mode`, the 15th hard check) independently
>   certifies, for every completed activity carrying a committed `selected_mode_id`,
>   that the live fields faithfully realize the *declared* mode entry in `act.modes`
>   (duration / `required_resources` / `required_equipment` always; the optional
>   dose / mobilization-lead / consumables / system-states only when the selected
>   mode declares them — mirroring `set_mode`'s write logic). Reads declared data
>   only, no engine primitive; silent (behavior-preserving) on correct `set_mode`
>   output and on all single-mode activities (`selected_mode_id is None`). Covered
>   by `TestModeConsistency` (direct faults) + a `mode` mutation scenario, taking
>   `ALL_CHECK_TYPES` / the mutation meta-guard to **15 hard checks**.
> - **Safety-function / LCO — reduces to `system_state`.** The engine models no
>   first-class safety-function pool (`SafetyFunctionPool` is not implemented; the
>   schema's `safety_functions` is metadata only); the working encoding is
>   trains-as-system-states, already certified by `_check_system_states`.
> - **Interactions beyond `system_state` — not a gap.** The engine enforces none
>   beyond FS precedence and system-state mutual exclusion, both already checked.

## 7. Bottom line

For the slice the property tests actually fuzz — precedence, lags, and
single-skill renewable crew — the oracle is well-exercised and trustworthy.
**Outside that slice, completeness is not established.** The oracle today is
"broad, per-type sanity-checked, but unfuzzed for 9 of 13 checks and
structurally blind to bugs in the engine primitives it borrows." Items 6.1–6.3
would move the needle most for the least risk.

**Update (breadth trio landed):** 6.1–6.3 are now done. Oracle *sensitivity* is
fuzzed across **all 13 hard checks** (6.1), and the feasible *soundness* side is
fuzzed for equipment, location, time-windows, consumables, and system-states on
top of the original crew slice (6.2); dose has fault-injection + happy-path
coverage (6.3).

**Update (Gap 1, core):** **6.4 is now done for its core — touch-points 1–4
(crew / equipment / location availability + time-windows).** The oracle no longer
asks the engine "how much is available?" or "what are the windows?"; it recomputes
both from the raw declared data, and differential tests prove it still catches an
infeasibility a *lying* primitive would hide. The structural blind spot is
therefore closed for availability and windows.

**Update (Gap 1, touch-point 5):** **the crew-demand/substitution blind spot is
now closed too.** The oracle no longer *only* trusts the engine-committed
`_actual_resources` as crew demand: `_check_substitution_legality` independently
certifies that breakdown is a legal, demand-conserving resolution of the declared
requirements (conservation + a bipartite max-flow legality test), firing a new
`substitution` violation otherwise. Behavior-preserving on correct output.

**Update (Gap 1, touch-point 6 — fully closed):** the equipment-zone check now
reads the declared `zone_id` off the equipment object directly instead of through
`equipment_pool.get_zone_id` (the primitive the engine's own placement gate also
uses). **Gap 1 is now fully closed:** the oracle routes no availability, window,
demand, or zone judgement through an engine primitive — it reads declared data
directly and independently certifies the one committed decision
(`_actual_resources`) it cannot re-derive. **Nothing open:** 6.5 (`_DUR_TOL`) is
now DONE — tightened 60 s → 1 ms (see §6.5) — and 6.2's feasible-side fuzz of
system-states and consumables is DONE, so the oracle-completeness backlog is fully
closed. See §8/§8b.

**Update (§6.6 constraint-type audit — DONE):** the audit closed the last
constraint-type gap. Multimode/execution-mode consistency is the same shape of
committed-decision blind spot as substitution: `set_mode` bakes a declared mode
into the live fields and stamps `selected_mode_id`, but the scheduler and every
other check then read only the live fields. `_check_mode_consistency` (type
`mode`, the 15th hard check) independently certifies the live profile faithfully
realizes the committed mode against the declared `modes`, reading no engine
primitive; behavior-preserving on correct `set_mode` output. Safety-function
reduces to `system_state` (no first-class engine pool) and interactions beyond
`system_state` are not engine-enforced — neither needs a new check. See §6.6.

## 8. What landed (breadth trio, `mandd/res_opt`)

- **6.1 — `tests/unit_tests/CPM/test_oracle_mutation.py` (new).** Fuzzed
  build→mutate→assert harness, 15 scenarios over all 13 hard checks + a
  coverage meta-guard. Registered `[./cpm_oracle_mutation]`.
- **6.2 — `tests/unit_tests/CPM/test_property_based.py` (Phase 3).**
  `rcpsp_multi_constraint_instance` / `build_multi_constraint_pert` /
  `test_multi_constraint_schedule_is_valid` — feasible-by-construction
  equipment + location + time-window + consumable + system-state fuzzing over all
  5 SGS (consumable stock = total demand; same-state shared lock).
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
change) and it closes Gap 1 for **availability + windows**.

### Touch-point 5 (crew-demand / substitution legality) — landed after the core

- **`src/CPM/schedule_validator.py`.** Three more pure helpers — `_allowed_skills`
  (primary ∪ declared alternatives), `_bipartite_saturates` (Edmonds–Karp max-flow
  saturation of the skill→requirement transportation graph), and
  `_substitution_is_feasible` (conservation + saturation) — plus the new check
  `_check_substitution_legality`, wired into `validate_schedule` right after the
  crew sweep, and a new `substitution` Violation type. The oracle now *certifies*
  the engine-committed `_actual_resources` breakdown against the *declared*
  requirements instead of trusting it verbatim. Reads declared data only;
  **behavior-preserving** on correct engine output (legal + conserving by
  construction → the check is silent).
- **`tests/unit_tests/CPM/test_oracle_decoupling.py` (§3, extended).** Differential
  blind-spot tests (a non-conserving under-count and an illegal-skill charge are
  each invisible to `_crew_demand` — proving the old crew-sweep blind spot — yet
  caught by `_check_substitution_legality`), an overlap-trap test showing max-flow
  is required over a membership check, and Hypothesis parity tests pinning the flow
  to a brute-force Hall-condition ground truth.
- **`tests/unit_tests/CPM/test_schedule_validator.py`.** `TestSubstitutionLegality`
  (legal-substitution happy-path negative + non-conserving and illegal-skill fault
  injections + a scheduler-output soundness test).
- **`tests/unit_tests/CPM/test_oracle_mutation.py`.** A `substitution` scenario and
  `'substitution'` added to `ALL_CHECK_TYPES`; the coverage meta-guard now enforces
  all **14** hard checks.

### Touch-point 6 (equipment-zone affinity) — landed; Gap 1 fully closed

- **`src/CPM/schedule_validator.py`.** `_check_equipment_zone_affinity` now reads
  the declared `zone_id` off the equipment object directly
  (`equipment_pool.equipment.get(eq_id).zone_id`, missing id → `None`) instead of
  calling `equipment_pool.get_zone_id` — the same primitive the engine's placement
  gate (`_equipment_zone_conflict`) consults. Behavior-preserving; the one-call
  decouple was the only production change.
- **`tests/unit_tests/CPM/test_oracle_decoupling.py` (§4, new test).** A single
  differential lying-primitive test: with the crane zone-locked and the activity
  moved to a foreign zone, `get_zone_id` is patched to lie `None` (unconstrained);
  the oracle still fires `equipment_zone` because it reads the declared field. The
  existing `equipment_zone` mutation scenario and `TestEquipmentZoneAffinity` cover
  the rest unchanged (behavior-preserving).

With touch-point 6, **Gap 1 is fully closed** — the oracle borrows no
availability / window / demand / zone answer from the engine.

### §6.6 constraint-type audit — landed; the last constraint-type gap closed

- **`src/CPM/schedule_validator.py`.** New `_check_mode_consistency` (type `mode`,
  the 15th hard check) + a pure `_selected_mode_mismatch(act)` helper. For every
  completed activity carrying a committed `selected_mode_id`, it certifies the live
  fields (`duration` / `required_resources` / `required_equipment` always; the
  optional dose / mobilization-lead / consumables / system-states only when the
  selected mode declares them, mirroring `set_mode`'s write logic) faithfully
  realize the *declared* mode entry in `act.modes`. Reads declared data only, no
  engine primitive; silent (behavior-preserving) on correct `set_mode` output and
  on all single-mode activities (`selected_mode_id is None`). Same committed-decision
  shape as `_check_substitution_legality`.
- **`tests/unit_tests/CPM/test_schedule_validator.py`.** `TestModeConsistency`
  (faithful-mode happy-path negative + divergent-live-resources and dangling-
  `selected_mode_id` fault injections + a single-mode-silent test).
- **`tests/unit_tests/CPM/test_oracle_mutation.py`.** A `mode` scenario (build a
  mode-bearing activity, commit it via `set_modes`, mutate by stamping a dangling
  `selected_mode_id`) and `'mode'` added to `ALL_CHECK_TYPES`; the coverage
  meta-guard now enforces all **15** hard checks.
- **Audit reductions (no new check needed).** Safety-function / LCO reduces to
  `system_state` (the engine models no first-class safety-function pool); constraint
  interactions beyond `system_state` are not engine-enforced (only FS precedence +
  system-state mutual exclusion, both already checked).

With §6.6, the constraint-type coverage audit is complete: every engine-enforced
constraint maps to an oracle check.

**Nothing open** — the oracle-completeness backlog is fully closed. **6.5**
(`_DUR_TOL`) is now DONE — tightened 60 s → 1 ms after proving the duration delta
is identically 0 on the normal path (see §6.5), frozen by a 56.25 s regression
test — and 6.2's feasible-side fuzzing of system-states + consumables is DONE
(Phase 3 fuzzes all five constraint types over all 5 SGS).
