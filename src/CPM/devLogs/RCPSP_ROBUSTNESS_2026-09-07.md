# RCPSP Engine Robustness — Notes & Prototype Plan

**Date:** 2026-09-07
**Author:** Claude Code (session with D. Mandelli)
**Scope:** `src/CPM/pert.py` (the RCPSP engine), `src/CPM/schedule_validator.py`
(the independent feasibility oracle), and the `tests/unit_tests/CPM/` suite.
**Branch:** `mandd/res_opt`
**Status:** ACTIVE — the property harness of §3 is built and running in CI
(hypothesis declared in `dependencies.xml`, commit `332bad0`). What began as a
brainstorm has driven **four** engine fixes: ES-gate quantization (§6),
`first`-strategy makespan inflation (§7), completion-gate quantization (§8), and
sub-minute duration collapse (§9). Remaining open items and next phases are
tracked in **§10**.

---

## 0. Motivation

The 2026-09-03 review round fixed **18 findings** in `pert.py`
(see `PERT_MANUAL_REVIEW_2026-09-03.md`). That volume is unsettling, but the
fixes are not 18 unrelated mistakes — they collapse into ~5 *root-cause
families*. That reframing matters: systemic patterns can be attacked at the
root (make a whole bug-class impossible, or auto-detected), whereas isolated
mistakes can only be whack-a-moled.

RCPSP is NP-hard and this engine models a lot (crews, equipment, locations,
dose budgets, equipment zones, shift calendars, skill substitution, hold
points, replan/clone). **100% correctness is not the goal.** The goal is to
convert every *silent wrong answer* into a *loud failure* — because for a tool
feeding uncertainty quantification into RAVEN, a plausible-but-infeasible
makespan quietly biases the entire distribution, which is far more dangerous
than a crash.

---

## 1. Bug taxonomy — the 5 root-cause families

| # | Family | Fixes from the review | Systemic root cause |
|---|---|---|---|
| **1** | Time-varying resource checked at a **single instant** | C2, C2b, B4, M-1 | Availability sampled at `startTime` (or one point) instead of across the activity's whole `[start, end)` interval |
| **2** | A constraint enforced in **one scheduler path** but not the others | SC1, SC2, SC3, PD1 | Serial / parallel / from-scratch / replan each re-implement feasibility; a constraint added to one drifts out of the others |
| **3** | Graph / longest-path **edge cases** | B1, B2, B3, B5, C1 | Multi-source graphs, zero-duration nodes, F-S lags, non-adjacent pairs — the degenerate/boundary corners |
| **4** | Derived state goes **stale after a mutation** | RP1, RP2, RP-l | `set_duration` / `clone` / `replan` mutate one field but forget to recompute dependent state (`endTime`, availability events, priority metrics) |
| **5** | **Float tolerance** | SC-m1 | Exact `>` / `==` on hour arithmetic |

Each family maps to a specific robustness technique below.

---

## 2. Robustness toolkit (ranked by ROI on *this* codebase)

### A. Turn the existing validator into an always-on oracle  — *cheapest, do first*

`schedule_validator.py` is already a genuine **independent oracle**: 15 checks
(crew, equipment, location, dose, zones, precedence+lags, time windows, hold
points, shift calendar, consumables, system-states, completeness, durations,
quality) that re-derive feasibility from the *actual* start/end times without
trusting the scheduler. `conftest.py` already wraps it as
`assert_valid_schedule(pert)`.

**Gap (updated 2026-09-07):** **20 of 35** test files now call it — up from the
~3 of ~37 when this note was written, so the Toolkit-A sweep is well underway. The
remaining ~15 that build a schedule still never ask the oracle whether it is
feasible; finishing the sweep is tracked in §10.

**Action:** make `assert_valid_schedule(pert)` the standard last line of every
test that runs the scheduler. Guards families **1, 2, 5** for free (any interval
over-commit, path-specific gap, or tolerance slip becomes a validator
violation).

### B. Property-based testing (Hypothesis)  — *highest coverage-per-effort → this is prototype #1*

Today there are **zero** Hypothesis tests; everything is hand-picked examples.
Yet the invariants are already written down in `test_invariants.py`:

- `makespan ≥ CPM_duration`
- unconstrained `makespan == CPM_duration`
- **monotonicity**: tighter resources ⇒ makespan can't shrink
- resource-feasibility (via the validator)

**Idea:** generate random-but-valid RCPSP instances and assert those same
invariants **plus `validate_schedule().is_feasible`** on every one. This is the
"induction" idea operationalized — properties true for *all* inputs, checked
over thousands of instances no human would enumerate. Hypothesis *shrinks* any
failure to a minimal counterexample, and it naturally produces the degenerate
graphs that fed family **3** (zero-duration nodes, single-source, weird lags).
See §3 for the concrete starter.

### C. Design-by-contract / assertion logic  — *"detection logic"*

`pert.py` has **0 `assert`s** today; all 53 `raise`s are input validation, none
are internal-consistency checks. Add contracts at the exact seams where bugs
clustered, guarded by a `self._validate` / `__debug__` flag so production stays
fast:

- **Family 1 →** one helper `resource_free_over(r, start, end)` used
  *everywhere*, plus a post-commit assertion that no committed activity exceeds
  any pool over its whole interval. Kills the "sampled at the wrong instant"
  class structurally.
- **Family 2 →** collapse the serial/parallel/replan feasibility checks into a
  **single shared gate** every dispatch path calls. Then a new constraint is
  added *once* and serial can't forget what parallel enforces. (This is the only
  invasive refactor here; deserves its own design note before code.)
- **Family 4 →** after any mutation, assert derived-state invariants
  (`endTime == startTime + effective_duration`; `availability_events` non-empty
  when pools exist; priority-metric block recomputed).

### D. Differential & metamorphic oracles

- **Exact oracle on tiny instances:** brute-force (or CP-SAT) the optimal
  makespan for ≤ ~8-activity instances; assert the scheduler's result is
  feasible and `makespan ≥ optimum`. Catches "plausible but wrong".
- **PSPLIB best-known solutions:** `psplib_regression.py` currently compares to
  its *own* frozen output, so a *systematically* wrong scheduler stays green.
  Importing published PSPLIB BKS values would give a real external oracle.
- **Metamorphic relations** (no oracle needed — a transformation that must not
  change the answer):
  - scale all durations ×k ⇒ CPM/makespan ×k;
  - add a slack unit of any resource ⇒ makespan can't increase;
  - relabel/permute activity IDs ⇒ identical makespan;
  - permute priorities ⇒ *unconstrained* CPM length invariant.

### E. Fuzz-and-freeze regression loop

When B or D finds a counterexample, freeze the shrunk instance into
`test_bugfix_regressions.py` (the pattern and file already exist). Over time the
regression corpus becomes a growing fingerprint of every real failure mode.

---

## 3. Prototype #1 — property-based harness: where to start

**Objective:** a single self-contained test module that generates random valid
RCPSP instances and asserts the engine's existing invariants + validator on all
of them. RAVEN-free, so it runs in the stand-alone CPM dev env.

### 3.1 Prerequisite  — RESOLVED (2026-09-07)

Hypothesis is a test-only dependency. **Resolution (commit `332bad0`):** it is
declared in RAVEN's [`dependencies.xml`](../../../dependencies.xml) alongside the
already-present `pytest`, so RAVEN's `establish_conda_env.sh` installs it into the
`raven_libraries` conda env that CI builds — the same path every other CPM test
dep (`deap`, `alns`, …) uses. The module still `pytest.importorskip`s Hypothesis so
the suite collects cleanly in a bare dev env without it — mirroring the
`ravenframework` guard in `test_raven_interface.py`.

The `requirements-dev.txt` / `pyproject.toml` `[test]`-extra options floated
originally were **not** taken: CI does not run `pip install -e ".[test]"`, it runs
the RAVEN test harness against `raven_libraries`, so the dep has to live where that
env is assembled. `setup.py` / `pyproject.toml` stay dependency-light on purpose.

For a bare stand-alone dev env (no RAVEN), install it directly:

```bash
pip install hypothesis
```

### 3.2 Construction API recap (verified against the current code)

```python
from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool

start = Activity("START", 0.0)            # Activity(name, duration_hours)
a     = Activity("A", 4.0)
end   = Activity("END", 0.0)

fwd = {start: [a], a: [end], end: []}     # {activity: [successors]}  — leaves map to []
p = Pert(graph=fwd)
p.crew_pool, p.equipment_pool, p.location_pool = ResourcePool(), EquipmentPool(), LocationPool()
p.startTime = datetime(2026, 1, 1)
p.generateInfo()

result = p.calculateScheduleWithResources(sgs='max_use_res_ranked')
result['scheduled_duration']   # resource-constrained makespan
result['cpm_duration']         # unconstrained CPM length
```

- Empty pools = **unlimited** capacity (so unconstrained makespan must equal CPM).
- Lags: `p.lag_dict = {(a, b): 2.0}` (see `make_lag_pert` / `_lag_pert`).
- Valid `sgs` names: `first`, `max_use_res_ranked`, `max_use_res_shuffled`,
  `md_knapsack`, `look_ahead` (raw match — anything else raises `ValueError`).
- Oracle: `from conftest import assert_valid_schedule` → `assert_valid_schedule(p)`.

### 3.3 Phased plan

- **Phase 0 — smoke.** Confirm the generator builds a valid `Pert` and the
  scheduler runs on ~50 generated instances (no property yet). Shakes out the
  generator itself.
- **Phase 1 — precedence-only invariants (no resources).** Empty pools; assert
  `assert_valid_schedule`, `makespan == CPM`, and the duration-scaling
  metamorphic relation. This alone exercises family **3** hard (zero-duration
  nodes, single/multi-source, isolated nodes) with zero new engine API.
- **Phase 2 — resource-constrained invariants.** Add one renewable crew skill
  with a sampled capacity and per-activity demand; assert `makespan ≥ CPM`,
  `assert_valid_schedule`, and **monotonicity** (extra capacity can't increase
  makespan). *First step here:* read how `test_invariants.py::_make_crew_pool`
  and `test_replan_resources.py` attach per-activity crew demand — do **not**
  guess that attribute API; wire the generator to whatever those tests use.
- **Phase 3 — fuzz-and-freeze.** Any counterexample Hypothesis shrinks gets
  copied into `test_bugfix_regressions.py` as a permanent case.

### 3.4 Starter skeleton (Phase 0 + Phase 1)

Proposed file: `tests/unit_tests/CPM/test_property_based.py`

```python
"""Property-based (Hypothesis) tests for the RCPSP engine.

Generates random *valid* activity-on-node DAGs and asserts the invariants
already codified in test_invariants.py, over inputs no hand-written fixture
would cover.  Phase 1: precedence only (unlimited resources).
"""
import math
from datetime import datetime

import pytest

hypothesis = pytest.importorskip("hypothesis")     # skip cleanly if not installed
from hypothesis import given, settings, strategies as st, HealthCheck

from CPM.activity import Activity
from CPM.pert import Pert
from CPM.outage_data import ResourcePool, EquipmentPool, LocationPool
from conftest import assert_valid_schedule

TOL = 1e-6
SGS = "max_use_res_ranked"


@st.composite
def rcpsp_dag(draw, max_activities=8, max_duration=20.0):
    """A random acyclic instance: n activities, durations, forward edges.

    Acyclic by construction — an edge i->j is only ever proposed for i < j,
    so the topological order is the index order and no cycle can form.
    """
    n = draw(st.integers(min_value=1, max_value=max_activities))
    durations = draw(st.lists(
        st.floats(min_value=0.0, max_value=max_duration,
                  allow_nan=False, allow_infinity=False),
        min_size=n, max_size=n))
    edges = set()
    for j in range(n):
        for i in range(j):
            if draw(st.booleans()):
                edges.add((i, j))
    return n, durations, sorted(edges)


def build_pert(n, durations, edges):
    """Wrap a generated instance in START/END and build a schedulable Pert."""
    acts = [Activity(f"A{i}", float(durations[i])) for i in range(n)]
    start, end = Activity("START", 0.0), Activity("END", 0.0)

    succ = {a: [] for a in acts}
    for i, j in edges:
        succ[acts[i]].append(acts[j])

    has_in  = {j for _, j in edges}
    has_out = {i for i, _ in edges}
    sources = [acts[i] for i in range(n) if i not in has_in]   # -> START
    sinks   = [acts[i] for i in range(n) if i not in has_out]  # -> END

    fwd = {start: list(sources)}
    fwd.update(succ)
    for s in sinks:
        fwd[s].append(end)
    fwd[end] = []

    p = Pert(graph=fwd)
    p.crew_pool, p.equipment_pool, p.location_pool = (
        ResourcePool(), EquipmentPool(), LocationPool())     # empty = unlimited
    p.startTime = datetime(2026, 1, 1)
    p.generateInfo()
    return p


# Phase 0 — the generator + scheduler don't blow up
@settings(max_examples=50, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(rcpsp_dag())
def test_generator_smoke(inst):
    p = build_pert(*inst)
    r = p.calculateScheduleWithResources(sgs=SGS)
    assert r["scheduled_duration"] >= 0.0


# Phase 1a — unlimited resources => makespan == CPM, and the schedule is feasible
@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(rcpsp_dag())
def test_unconstrained_makespan_equals_cpm(inst):
    p = build_pert(*inst)
    r = p.calculateScheduleWithResources(sgs=SGS)
    assert_valid_schedule(p)
    assert abs(r["scheduled_duration"] - r["cpm_duration"]) < TOL, (
        f"makespan {r['scheduled_duration']:.4f} != CPM {r['cpm_duration']:.4f}")


# Phase 1b — metamorphic: scaling every duration by k scales CPM by k
@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(rcpsp_dag(), st.floats(min_value=1.1, max_value=5.0,
                              allow_nan=False, allow_infinity=False))
def test_duration_scaling_scales_cpm(inst, k):
    n, durations, edges = inst
    base = build_pert(n, durations, edges).calculateScheduleWithResources(sgs=SGS)
    scaled = build_pert(n, [d * k for d in durations], edges) \
        .calculateScheduleWithResources(sgs=SGS)
    assert math.isclose(scaled["cpm_duration"], base["cpm_duration"] * k,
                        rel_tol=1e-6, abs_tol=1e-6)
```

### 3.5 How to run

```bash
pip install hypothesis
cd tests/unit_tests/CPM
python -m pytest test_property_based.py -v
# to reproduce a shrunk failure Hypothesis reports, paste its @example(...) line
# above the test, or run with --hypothesis-seed=<seed>
```

### 3.6 What "done" looks like for prototype #1

- The module runs green in the CPM dev env and is *skipped* (not errored) where
  Hypothesis isn't installed.
- At least the three properties above pass at `max_examples ≥ 200`.
- If any property *fails*, that's the prototype succeeding: shrink → freeze into
  `test_bugfix_regressions.py` → fix → keep the property.

---

## 4. Open decisions

1. ~~**Where the `hypothesis` test-dep is declared** and how CI installs it.~~
   **RESOLVED (2026-09-07, commit `332bad0`):** declared in RAVEN's
   `dependencies.xml`; installed into the `raven_libraries` conda env by
   `establish_conda_env.sh`. See §3.1.
2. **Should the validator become a runtime post-condition** (behind a flag) in
   `calculateScheduleWithResources`, not only a test-time oracle? (Toolkit item
   A vs C.)
3. **Scope of the shared feasibility gate refactor** (toolkit item C, family 2)
   — worth a dedicated design note before touching `pert.py`.
4. Whether to invest in an **exact/CP-SAT oracle** and **PSPLIB BKS import**
   (toolkit item D) now or after the property harness has paid off.

---

## 5. Suggested order of execution

1. **Prototype #1** (this note, §3) — property harness, Phases 0–1. Low risk,
   no engine change, immediate fuzzing.
2. **Toolkit A** — sweep `assert_valid_schedule` into every scheduling test.
3. **Phase 2** of the harness — resource monotonicity once the crew-demand API
   is confirmed.
4. **Toolkit C** — contracts + the shared feasibility gate (own design note).

---

## 6. Prototype #1 — first results (2026-09-07)

**The harness paid off on its first run: it found a real, previously-unknown
engine bug.**

### 6.1 The bug — ES-gate microsecond quantization (bug family #5, + #2)

`test_unconstrained_makespan_equals_cpm` failed, and Hypothesis shrank it to
**two distinct failures** (see the captured run under "Initial testing outcome"
below) — both on the canonical `max_use_res_ranked` strategy, both the *same*
root cause:

- **Mask A — deadlock / incompleteness.** `inst=(2, [1.38888045372999,
  1.38888045372999], [(0,1)])` (and, in my own dense search, the 4-activity
  diamond
  `[18.957307212181263, 7.896469928463469, 0.9657284725362469, 16.425485839826166]`,
  edges `A0→A1, A0→A3, A1→A3, A2→A3`). Under **unlimited** resources the
  scheduler halted at `completed 5/6` with a "possible deadlock" warning — the
  terminal `END` was stranded, tripping the validator's completeness check.
- **Mask B — inflated makespan.** `inst=(3, [1.38888045372999,
  1.38888045372999, 1.0], [(0,1),(1,2)])` — a 3-chain — returned
  `makespan 4.7778 != CPM 3.7778`. Same µs straddle, but here the stranded
  successor was instead revived at the *next* event step and started late, so
  its own duration (`1.0 h`) padded the makespan rather than deadlocking.

One root cause, two symptoms: whether a µs-deferred successor deadlocks (no
future event) or merely finishes late (a later event revives it) depends only on
the instance's event structure.

**Root cause.** Candidate selection gates each ready activity on
`abs_es > time` (exact `datetime` comparison) at
[`pert.py:3611`](../pert.py#L3611) (`_collect_candidates_from_heap`, the
priority-cache fast path) and its twin at [`pert.py:3741`](../pert.py#L3741)
(`_select_candidate_activities`, the O(n) fallback). But:

- `abs_es` is derived from the **CPM early-start**, a full-precision float in
  hours (here `END.es = 43.2792629804709` h → `…45.346730`);
- `time` is an **actual finish accumulated through microsecond-quantized
  `timedelta`s** (A3's real end `…45.346729`).

These are two independent representations of the *same* logical instant and
they disagreed by **exactly 1 microsecond**. `abs_es > time` was therefore
true, `END` was pushed back, and — with no future event to revive it — stranded
forever. Precedence was already guaranteed by the `_pending_preds == 0` counter,
so the ES gate was a **redundant guard that µs quantization turned harmful**.
The duplication across the two selection paths is a textbook **family #2**
instance (fix one, the other still bites).

Why no existing test caught it: the trigger needs full-precision durations whose
CPM early-start and accumulated actual-finish straddle a microsecond boundary —
~1 in 30 000 random instances, and **0 of 64** integer/`dur=1.0` topologies.
Rounding any duration to ~4 decimals makes it vanish. Exactly the "silent corner
no human enumerates" that property-based testing exists to find.

### 6.2 The fix (applied, this branch)

Compare within the loop's own event resolution `_EVENT_EPSILON` (the loop
already treats times this close as simultaneous at
[`pert.py:3352`](../pert.py#L3352)), in **both** gates:

```python
if abs_es > time + self._EVENT_EPSILON:   # was: abs_es > time
```

- Verified: both of Hypothesis's shrunk instances now pass (the chain gives
  `makespan 3.7778 == cpm`, the 2-node instance completes 5/5); the witness
  completes 6/6 with `makespan == cpm`; a 30 000-trial dense full-precision
  search drops from `incomplete=1, makespan_mismatch≥1` to **`0` / `0`**; all 5
  SGS strategies complete.
- Regression frozen as `test_bugfix_regressions.py::TestESGateQuantization`
  (**3 tests**, covering *both* masks): `test_terminal_activity_not_stranded`
  and `test_independent_validator_agrees` freeze the diamond witness (mask A —
  deadlock); `test_makespan_not_inflated_on_chain` freezes the user's exact
  shrunk chain `[1.38888045372999, 1.38888045372999, 1.0]` (mask B — inflated
  makespan). All three **fail on the pre-fix engine** (`completed 5/6` /
  validator infeasible / `makespan 4.7778`) and pass after the fix (proven via
  `git stash` of `pert.py`).
- Full CPM suite: **906 passed, 4 skipped** (no failures; +3 regression tests
  over the pre-harness baseline of 903).

### 6.3 Generalized lesson (feeds Toolkit C)

The deeper root cause is systemic: the engine stores time as `datetime`
(microsecond resolution) while durations, CPM early/late starts, and lags are
floats in **hours**. *Any* comparison between a CPM-derived time and an
accumulated actual time can disagree at the ~µs level. The ES gate is merely
where it surfaced first. **Rule to enforce (contract / review checklist):** every
`datetime` comparison that pits a CPM-derived instant against an accumulated
actual instant must be tolerant (`> t + _EVENT_EPSILON`, not `> t`). The lag
gates at [`pert.py:3664`](../pert.py#L3664) / [`pert.py:3809`](../pert.py#L3809)
(`pred_end + lag > time`) are the same shape and are candidates for the same
treatment — not yet observed to fail, but on the same footing.

### 6.4 Separate observation — `first`-strategy makespan (NOT fixed)

**Not to be confused with mask B above.** The log's `makespan 4.7778` failure
was on `max_use_res_ranked` and is *fixed* by the ES-gate change. This §6.4
observation is a **distinct residual on a different strategy** that survives the
fix: on the diamond witness the `first` SGS returns `makespan = 44.245 h` vs
`cpm = 43.279 h` under **unlimited** resources — a `0.966 h` gap that is exactly
A2's duration. It is **pre-existing** (present before *and* after the ES-gate
fix) and *not* covered by prototype #1's asserted properties (which pin
`max_use_res_ranked`). Under truly unlimited resources every strategy should
start each activity at its earliest feasible time, so `first` yielding
`makespan > cpm` is either a documented non-optimality of that heuristic or a
second latent bug. **Root-caused and fixed in §7** (it was a genuine bug — the
serial SGS dispatched one activity per completion event); this observation was
deliberately kept out of scope for the ES-gate fix and handled separately.

## 7. `first`-strategy makespan inflation — root cause (2026-09-07)

**Verdict: genuine latent bug, not documented non-optimality.** A correct
serial SGS places each activity at its earliest precedence-and-resource-feasible
start; under **unlimited** resources that is always its CPM early-start, so every
strategy *must* return `makespan == cpm`. `first` violates this.

### 7.1 Trace of the diamond witness

Graph `START→{A0,A2}; A0→{A1,A3}; A1→A3; A2→A3; A3→END`. A2 is off the critical
path (`es = 0`) and feeds only A3. Per-activity **start** times:

| act | dur | es | `first` start | `max_use_res_ranked` start |
|-----|----:|---:|--------------:|---------------------------:|
| A0  | 18.96 | 0.00 | 0.00 | 0.00 |
| A1  | 7.90 | 18.96 | 18.96 | 18.96 |
| **A2** | **0.97** | **0.00** | **26.85** | **0.00** |
| A3  | 16.43 | 26.85 | 27.82 | 26.85 |
| END | 0.00 | 43.28 | 44.25 | 43.28 |

`first` starts A2 at **26.85 h** instead of its `es = 0` — 26.85 h late — and
because `A2→A3`, A3 is pushed from 26.85 to 27.82, inflating the makespan by
exactly A2's duration.

### 7.2 Root cause — decision epochs bonded to completion events

The `first` branch of `_schedule_generation_scheme`
([`pert.py:4431`](../pert.py#L4431)) is a **serial SGS**: it dispatches **exactly
one** activity per call (`return [act]`), highest-priority-feasible-first. The
event loop ([`pert.py:3386-3411`](../pert.py#L3386)) then pushes only the
*selected* activities' completion times onto the event heap — it **never pushes
the current instant back**. So when two activities are ready at the same time `T`
(A0 and A2 at `T=0`) and `first` dispatches only the winner (A0), the loser (A2)
is left in `candidates`, unselected, and **cannot be reconsidered until the next
completion event**. A2 keeps losing the priority contest (to A1 at 18.96) until
26.85, when it is finally the sole candidate — 26.85 h after it was first
eligible. The single-dispatch model is only correct if the loop re-polls at the
same instant; it does not, so decision epochs are artificially bonded to
completion events. This produces a **non-active schedule** (an activity could
start earlier without displacing anything) and, under unlimited resources,
`makespan > cpm`.

### 7.3 Scope / blast radius

- `first` is **user-selectable** via the `<sgs>` XML node
  ([`BaseCPMmodel.py:87`](../BaseCPMmodel.py#L87)), so a user choosing it hits
  inflated makespans — a reachable defect.
- It is **not** the default anywhere (`max_use_res_ranked` is, at
  [`pert.py:3235`](../pert.py#L3235) and both façade signatures), is **not** used
  by the GA, and **no test pins its behavior** — so a fix has zero test
  dependencies and cannot regress the default path.

### 7.4 The fix (applied, this branch — Option B)

**Greedy priority-fill within the timestep.** The `first` branch of
`_schedule_generation_scheme` ([`pert.py:4431`](../pert.py#L4431)) now selects
candidates one-at-a-time in strict `_rank_by_value` order, committing tentatively
(`_apply_tentative`) after each, and starts **every** one that is resource-
feasible at `time_index` — returning the batch instead of `[act]`. Under
unlimited resources all ready activities start now → `makespan == cpm`. This is
the standard, correct serial-SGS-within-a-timestep behaviour. It stays localized
to the `first` branch (the event loop remains strategy-agnostic), and the
distinction from `max_use_res_ranked` survives — strict full-order priority +
commit-after-each, vs. the top-K heap + batch scan — so the two can still differ
under contention.

Two alternatives were considered and rejected: **Option A** (keep single-dispatch
but re-push `time_index` to re-poll the same instant) would have added an
sgs-specific branch to the shared loop; **Option C** (document as intended
non-optimality and exclude `first` from the property) would have left a reachable
strategy returning provably suboptimal makespans.

### 7.5 Verification

- Diamond witness: all **5** strategies now return `makespan == cpm` (6/6
  complete, validator feasible). Pre-fix, `first` gave `44.245` (7 iterations, A2
  started at `26.85`); post-fix `43.279` (A2 starts at `0`).
- Overbooking guard: two independent activities sharing a single crew still
  **serialize** under `first` (makespan `8.0 = 5+3`, validator feasible, zero
  violations) — the greedy fill respects `_fits_with_tentative`.
- 30 000-trial random full-precision DAG sweep (n ≤ 6, unlimited resources) under
  `first`: `incomplete = 0`, `makespan_mismatch = 0`.
- Regression frozen as `test_bugfix_regressions.py::TestFirstStrategyMakespan`
  (2 tests: `test_first_makespan_equals_cpm_unlimited` — fails pre-fix at
  `44.245`, passes post-fix; `test_first_still_serializes_under_contention` —
  guards against the fix overbooking).
- Property harness `test_unconstrained_makespan_equals_cpm` is now
  **parametrized across all 5 strategies** (was `max_use_res_ranked` only — the
  blind spot that let this hide).
- Full CPM suite: **908 passed, 4 skipped** (no failures; +2 over the 906 after
  the ES-gate freeze).

## Third testing outcome
================================================================================ short test summary info ================================================================================
FAILED test_property_based.py::test_unconstrained_makespan_equals_cpm[first] - AssertionError: [first] makespan 1.0000 != CPM 1.0156
assert 0.015625 < 1e-06
 +  where 0.015625 = abs((1.0 - 1.015625))
Failing test case: test_unconstrained_makespan_equals_cpm(
    sgs='first',
    inst=(6, [0.0, 0.0, 0.0, 0.0, 0.015625, 1.0], [(4, 5)]),
)
FAILED test_property_based.py::test_unconstrained_makespan_equals_cpm[md_knapsack] - AssertionError: [md_knapsack] makespan 1.0000 != CPM 1.0156
assert 0.015625 < 1e-06
 +  where 0.015625 = abs((1.0 - 1.015625))
Failing test case: test_unconstrained_makespan_equals_cpm(
    sgs='md_knapsack',
    inst=(4, [0.015625, 0.0, 0.0, 1.0], [(0, 3)]),
)
Explanation:
    These lines were always and only run by failing test cases:
        /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/test_property_based.py:139
        /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/test_property_based.py:140
============================================================================== 2 failed, 5 passed in 1.61s ==============================================================================
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ git status
On branch mandd/res_opt
Your branch is ahead of 'origin/mandd/res_opt' by 1 commit.
  (use "git push" to publish your local commits)

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
        modified:   ../../../src/CPM/pert.py
        modified:   test_bugfix_regressions.py

Untracked files:
  (use "git add <file>..." to include in what will be committed)
        ../../../.claude/
        ../../../delete_trailing_whitespace.sh
        ../../../doc/demos/CPM/CPM_testing_from file.ipynb
        ../../CPMmodel/.ravenStatus
        ../../CPMmodel/Print_sim_PS.xml
        ../../CPMmodel/Print_sim_PS_map.xml
        .coverage

no changes added to commit (use "git add" and/or "git commit -a")
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ 
(LOGOS_libraries) mandd@INL431037:~/projects/LOGOS/tests/unit_tests/CPM$ python -m pytest test_property_based.py -v
================================================================================== test session starts ==================================================================================
platform darwin -- Python 3.14.2, pytest-9.1.1, pluggy-1.6.0 -- /opt/miniconda3/envs/LOGOS_libraries/bin/python
cachedir: .pytest_cache
hypothesis profile 'default'
rootdir: /Users/mandd/projects/LOGOS/tests/unit_tests/CPM
configfile: pytest.ini
plugins: hypothesis-6.167.1, anyio-4.13.0
collected 7 items                                                                                                                                                                       

test_property_based.py::test_generator_smoke PASSED                                                                                                                               [ 14%]
test_property_based.py::test_unconstrained_makespan_equals_cpm[first] FAILED                                                                                                      [ 28%]
test_property_based.py::test_unconstrained_makespan_equals_cpm[max_use_res_ranked] PASSED                                                                                         [ 42%]
test_property_based.py::test_unconstrained_makespan_equals_cpm[max_use_res_shuffled] PASSED                                                                                       [ 57%]
test_property_based.py::test_unconstrained_makespan_equals_cpm[md_knapsack] FAILED                                                                                                [ 71%]
test_property_based.py::test_unconstrained_makespan_equals_cpm[look_ahead] PASSED                                                                                                 [ 85%]
test_property_based.py::test_duration_scaling_scales_cpm PASSED                                                                                                                   [100%]

======================================================================================= FAILURES ========================================================================================
_____________________________________________________________________ test_unconstrained_makespan_equals_cpm[first] _____________________________________________________________________
test_property_based.py:131: in test_unconstrained_makespan_equals_cpm
    @settings(max_examples=200, deadline=None,
                   ^^^^^^
test_property_based.py:138: in test_unconstrained_makespan_equals_cpm
    assert abs(r["scheduled_duration"] - r["cpm_duration"]) < TOL, (
E   AssertionError: [first] makespan 1.0000 != CPM 1.0156
E   assert 0.015625 < 1e-06
E    +  where 0.015625 = abs((1.0 - 1.015625))
E   Failing test case: test_unconstrained_makespan_equals_cpm(
E       sgs='first',
E       inst=(6, [0.0, 0.0, 0.0, 0.0, 0.015625, 1.0], [(4, 5)]),
E   )
--------------------------------------------------------------------------------- Captured stderr call ----------------------------------------------------------------------------------
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=1.0h | strategy=first | max_time=10.2h
INFO:CPM.pert:Scheduling complete | CPM=1.0h | actual=1.0h | delay=0.0h | completed=8/8 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=1.0h | strategy=first | max_time=10.2h
INFO:CPM.pert:Scheduling complete | CPM=1.0h | actual=1.0h | delay=0.0h | completed=8/8 | iterations=4
----------------------------------------------------------------------------------- Captured log call -----------------------------------------------------------------------------------
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=1.0h | strategy=first | max_time=10.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.0h | actual=1.0h | delay=0.0h | completed=8/8 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=1.0h | strategy=first | max_time=10.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.0h | actual=1.0h | delay=0.0h | completed=8/8 | iterations=4
__________________________________________________________________ test_unconstrained_makespan_equals_cpm[md_knapsack] __________________________________________________________________
test_property_based.py:131: in test_unconstrained_makespan_equals_cpm
    @settings(max_examples=200, deadline=None,
                   ^^^^^^
test_property_based.py:138: in test_unconstrained_makespan_equals_cpm
    assert abs(r["scheduled_duration"] - r["cpm_duration"]) < TOL, (
E   AssertionError: [md_knapsack] makespan 1.0000 != CPM 1.0156
E   assert 0.015625 < 1e-06
E    +  where 0.015625 = abs((1.0 - 1.015625))
E   Failing test case: test_unconstrained_makespan_equals_cpm(
E       sgs='md_knapsack',
E       inst=(4, [0.015625, 0.0, 0.0, 1.0], [(0, 3)]),
E   )
--------------------------------------------------------------------------------- Captured stderr call ----------------------------------------------------------------------------------
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=1.0h | strategy=md_knapsack | max_time=10.2h
INFO:CPM.pert:Scheduling complete | CPM=1.0h | actual=1.0h | delay=0.0h | completed=6/6 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=1.0h | strategy=md_knapsack | max_time=10.2h
INFO:CPM.pert:Scheduling complete | CPM=1.0h | actual=1.0h | delay=0.0h | completed=6/6 | iterations=4
----------------------------------------------------------------------------------- Captured log call -----------------------------------------------------------------------------------
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=1.0h | strategy=md_knapsack | max_time=10.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.0h | actual=1.0h | delay=0.0h | completed=6/6 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=1.0h | strategy=md_knapsack | max_time=10.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.0h | actual=1.0h | delay=0.0h | completed=6/6 | iterations=4
================================================================================ short test summary info ================================================================================
FAILED test_property_based.py::test_unconstrained_makespan_equals_cpm[first] - AssertionError: [first] makespan 1.0000 != CPM 1.0156
assert 0.015625 < 1e-06
 +  where 0.015625 = abs((1.0 - 1.015625))
Failing test case: test_unconstrained_makespan_equals_cpm(
    sgs='first',
    inst=(6, [0.0, 0.0, 0.0, 0.0, 0.015625, 1.0], [(4, 5)]),
)
FAILED test_property_based.py::test_unconstrained_makespan_equals_cpm[md_knapsack] - AssertionError: [md_knapsack] makespan 1.0000 != CPM 1.0156
assert 0.015625 < 1e-06
 +  where 0.015625 = abs((1.0 - 1.015625))
Failing test case: test_unconstrained_makespan_equals_cpm(
    sgs='md_knapsack',
    inst=(4, [0.015625, 0.0, 0.0, 1.0], [(0, 3)]),
)
============================================================================== 2 failed, 5 passed in 0.95s ==============================================================================

## 8. Completion-gate microsecond quantization — root cause (2026-09-07)

The parametrized property run above (the §7 fix's own dividend — `first` now
reaches the harness, and the equality property now covers all 5 strategies)
surfaced a **third** bug. It is not a `first`-only defect: Hypothesis shrank
**all five** strategies to the *same* minimal counterexample —

```
inst = (4, [0.0, 0.0, 1.7447551588106158, 1.504299842107538], [(2, 3)])
```

i.e. four activities `A0(0.0) A1(0.0) A2(1.7448) A3(1.5043)` with the single
precedence edge `A2 → A3`; wrapped in START/END this is
`START → {A0, A1, A2}`, `A2 → A3`, `{A0, A1, A3} → END`. Under **unlimited**
resources it must trivially complete 6/6 — but every strategy halted at
`completed 4/6` with `END` (and A3) stranded (the `look_ahead` validator report
above: *"2 of 6 activities not scheduled"*). Strategy-independence was the tell:
the defect lives in the **event loop**, below the SGS layer the §7 fix touched.

### 8.1 Root cause — the completion-side twin of the ES-gate bug (§6, family #5)

Same microsecond-quantization mechanism as §6.1, on the *other* side of the loop:

- Durations / CPM early-starts are full-precision floats in **hours**; actual
  start/end are `datetime`s at **microsecond** resolution. Hours→`timedelta`
  quantizes to µs, and a chain of quantized `timedelta` adds can disagree with a
  single-quantized CPM float by ~1 µs.
- `_build_event_queue` (pert.py ~3175) **seeds** the absolute CPM early-start of
  every waiting activity, `startTime + timedelta(hours=es)`, as an event instant.
  The terminal `END`'s ES equals A3's early-finish.
- The main loop's **epsilon-merge** (pert.py ~3352) pops all events within
  `_EVENT_EPSILON` (1 min *at the time of this fix*; §9 later shrank it to 1 ms)
  of the current instant and treats them as one.
- `_update_ongoing_list` (pert.py ~5570) completed an ongoing activity only on an
  **exact** `time_index >= end_time`.

The collision, measured directly on the counterexample at the deciding iteration:

| quantity | value |
|---|---|
| A3 actual accumulated finish (`end_time`) | `…03:14:56.598004` |
| seeded `END` absolute ES (`time_index`)   | `…03:14:56.598003` |
| gap | **1.000 µs**, seed *earlier* than finish |

The epsilon-merge folds A3's true completion event into the earlier seeded
`END`-ES event, so the loop visits the instant **once**, at the seed. The exact
gate then evaluates `seed(…003) >= a3_end(…004)` → **False**, so A3 is *not*
completed. Nothing re-pushes the current instant (the loop only ever enqueues
selected activities' completion times, pert.py ~3408), the heap drains, and A3 —
and therefore `END` — are stranded forever: a spurious deadlock under unlimited
resources.

The zero-duration A0/A1 are load-bearing: they collapse START, A0, A1 to one
instant so the surviving A2→A3 chain's accumulated finish lands exactly one µs
off the seeded successor ES. This is why the earlier 30 000-trial sweep (§7.5)
missed it — `random.uniform(0, 20)` structurally never emits `0.0`, so it never
built the degenerate alignment. The bug needs an exact-zero duration *and*
full-precision non-zero durations in the same instance.

### 8.2 The fix (applied, this branch)

`_update_ongoing_list`, pert.py ~5583 — make the completion gate tolerant within
the loop's own declared event resolution, exactly mirroring the ES-gate fix:

```python
-        if time_index >= end_time:
+        if time_index >= end_time - self._EVENT_EPSILON:
             completed_now.append(act)
```

An activity whose true finish is within `_EVENT_EPSILON` of the merged event
instant is completed now, rather than being stranded by a sub-microsecond
shortfall. This is consistent with the ES gate (§6.2) and the systemic rule in
§6.3: *every comparison between an accumulated actual time and a CPM-derived
float instant must carry the loop's `_EVENT_EPSILON` tolerance.*

**Correctness of the early completion.** (The tolerance was 1 min when this
subsection was written; §9 later shrank it to 1 ms — the argument holds at either
scale, and *more* tightly at 1 ms.) The tolerance can only fire when an event
instant has *already been scheduled* within the tolerance of the true finish — in
practice the seeded successor ES or an epsilon-merged neighbour, i.e. essentially
*at* the finish (µs away). It cannot complete an activity whose finish is genuinely
in the future with no near event, because no such near `time_index` exists to
trigger it. The independent validator (which checks precedence with its own
`_PREC_TOL`, and resource feasibility over whole intervals) is the backstop: the
verification sweep below runs it on every instance and finds zero infeasibilities,
so the tolerant completion never manufactures a precedence or resource violation.

### 8.3 Verification

- **Exact counterexample** `inst=(4, [0.0, 0.0, 1.7447551588106158,
  1.504299842107538], [(2, 3)])`: pre-fix `completed 4/6` (`END`, A3 stranded) on
  **all 5** strategies; post-fix `completed 6/6`, `makespan == cpm`, validator
  feasible on all 5.
- **Zero-duration sweep** — 20 000 random DAGs (n ≤ 6, durations `0.0` w.p. 0.3
  else `uniform(0, 20)`, unlimited resources) across all 5 strategies:
  `incomplete = 0`, `makespan_mismatch = 0`. A **validator-checked** 5 000-instance
  variant reports `infeasible = 0` — the tolerant gate introduces no precedence /
  resource violations.
- **Regression frozen** as `test_bugfix_regressions.py::TestCompletionGate
  Quantization` (10 tests = 2 methods × 5 strategies): `test_terminal_activity_
  not_stranded` and `test_independent_validator_agrees`. All 10 **fail pre-fix**
  (verified by a temporary exact-gate revert) and **pass post-fix**.
- **Full CPM suite: 918 passed, 4 skipped** (+10 over the 908 after the §7 freeze;
  no failures, no regressions — the 1-min completion tolerance does not disturb
  the shift-calendar / time-window / resource-release tests that read `end_time`).

### 8.4 Status of the sibling gates (updated §6.3 ledger)

The µs-quantization family now has **three** confirmed-and-fixed members, all on
the same `_EVENT_EPSILON` footing:

| gate | location | status |
|---|---|---|
| ES gate (heap collect)        | pert.py ~3611 | fixed (§6.2) |
| ES gate (candidate select)    | pert.py ~3741 | fixed (§6.2) |
| **completion gate**           | pert.py ~5583 | **fixed (§8.2)** |
| lag gate (heap collect)       | pert.py ~3625 | same shape, not yet observed to fail — left on the §6.3 footing |
| lag gate (candidate select)   | pert.py ~3753 | same shape, not yet observed to fail — left on the §6.3 footing |

The two lag gates share the identical exact-comparison shape and would fall to the
same class of counterexample once a lag-bearing instance is added to the property
generator (the generator currently emits no lags). Flagged for the next harness
extension rather than speculatively edited.

## 9. Sub-minute duration collapse — root cause (makespan < CPM) (2026-09-07)

The re-run after the §8 completion-gate fix (raw log, "Third testing outcome"
above) turned the property green on 3 of 5 strategies but surfaced a **fourth**
bug on the other two. Hypothesis *reported* it only under `first` and
`md_knapsack`, but the two shrunk counterexamples —

```
inst = (6, [0.0, 0.0, 0.0, 0.0, 0.015625, 1.0], [(4, 5)])
inst = (4, [0.015625, 0.0, 0.0, 1.0],           [(0, 3)])
```

— reproduce identically on **all five** strategies (verified by direct replay;
see 9.3). The reporting asymmetry is a Hypothesis scheduling artefact, not a
strategy dependence: the defect is again in the event loop, below the SGS layer.
Both witnesses share one feature — an activity of duration **`0.015625 h`
(= 56.25 s)** feeding a successor. Pre-fix the run reported

```
makespan 1.0000 != CPM 1.0156    (assert 0.015625 < 1e-06)
```

i.e. the resource-constrained makespan came out **shorter than the unconstrained
CPM** — a strict impossibility under unlimited resources, and the mirror image of
the §6/§7 inflation bugs: here the schedule is too *short*.

### 9.1 Root cause — the tolerance was right in kind but wrong in magnitude

This is not a new mechanism. It is §6/§8's own fix, `_EVENT_EPSILON`, applied at
the wrong scale. That epsilon does double duty: it merges near-simultaneous events
*and* it is the grace on the ES gates (§6.2) and the completion gate (§8.2). Its
value was **`timedelta(minutes=1)`** — chosen only to be "small," never against a
real duration scale.

56.25 s is **less than** that 1-minute epsilon. So on the deciding iteration the
short activity's true finish (`start + 56.25 s`) sits *within* `_EVENT_EPSILON` of
its own start instant: the epsilon-merge folds the finish into the start, and the
tolerant completion gate (§8.2) reports the activity **already complete** at its
start. Its successor's ES gate — tolerant by the same epsilon — then releases the
successor at the *earlier* instant. The 56.25 s activity is effectively collapsed
to zero length; the successor chain slides forward by 56.25 s, and the makespan
lands 0.015625 h below CPM.

The independent validator did **not** catch it, because its precedence grace
`_PREC_TOL` was **`timedelta(seconds=60)`** — also larger than 56.25 s. A 56.25 s
successor overlap read as feasible. The two surfaces masked the same bug with the
same oversized constant: the oracle was as blunt as the engine.

Why earlier sweeps missed it: the §7.5 sweep drew durations from
`uniform(0, 20)`, which structurally never emits a value below a minute *next to*
an exact-zero neighbour; the §8 zero-duration sweep injected `0.0` but no
*sub-minute non-zero* durations. The bug needs a duration in the open interval
`(0, _EVENT_EPSILON)` — exactly the band both prior generators skipped.

The framing that ties §6–§9 together: **`cpm_duration` is computed at full float
precision, while `scheduled_duration` is walked on an event grid whose cell size
is `_EVENT_EPSILON`.** The two only agree when the grid cell is a true
*quantization* scale (µs-class rounding noise) and not a coarse *modelling* scale
that can swallow a real activity. A 1-minute cell is ~6 orders of magnitude too
coarse for the second condition.

### 9.2 The fix (applied, this branch)

Shrink the epsilon to an actual quantization tolerance, on **both** surfaces:

```python
# src/CPM/pert.py ~3251
-    _EVENT_EPSILON = timedelta(minutes=1)
+    _EVENT_EPSILON = timedelta(milliseconds=1)

# src/CPM/schedule_validator.py ~66
-_PREC_TOL   = timedelta(seconds=60)
+_PREC_TOL   = timedelta(milliseconds=1)   # quantization grace; matches Pert._EVENT_EPSILON
```

`_DUR_TOL` (the validator's *duration-consistency* grace, a separate concern) is
left at 60 s.

**Why 1 ms is the right cell.** The noise it must absorb is chain quantization:
each hours→`timedelta` conversion rounds to the µs, so an accumulated actual time
drifts from the single-quantized CPM float by ≈ `D × 0.5 µs` for a chain of depth
`D`. 1 ms = 1000 µs covers chains ~2000 deep — far beyond any realistic outage
network — so it never re-opens the §6/§8 deadlocks. It is also ~5 orders of
magnitude below the shortest plausible outage activity (minutes), so it can never
again collapse a real duration. 1 ms sits in the wide empty band between "µs
rounding noise" (what must be tolerated) and "a real activity" (what must not be),
which is exactly where a quantization tolerance belongs.

The §8.2 correctness argument for the tolerant completion gate holds *more*
strongly at 1 ms than at 1 min: the gate can still only fire when an event instant
is already scheduled within the tolerance of the true finish, and now "within the
tolerance" means within 1 ms, i.e. essentially *at* the finish.

### 9.3 Verification

- **Both frozen counterexamples**, replayed on **all 5** strategies: pre-fix
  `makespan 1.0000 < CPM 1.015625`; post-fix `makespan == CPM == 1.015625`,
  `completed 8/8` and `6/6` respectively, validator feasible on all 5.
- **Validator-checked sweep** — 8 000 instances (1 600 DAGs × 5 strategies, n ≤ 6,
  durations drawn to mix exact-`0.0`, sub-minute values `{56.25 s, 112.5 s, 28.1 s,
  30 s}`, and `uniform(0, 20) h`, unlimited resources): `makespan<cpm = 0`,
  `incomplete = 0`, `infeasible = 0` (the tolerant gates introduce no precedence /
  resource violation, and the tightened `_PREC_TOL` raises no false positive).
- **Regression frozen** as `test_bugfix_regressions.py::
  TestSubMinuteDurationNotCollapsed` — `test_makespan_not_below_cpm` and
  `test_short_activity_precedes_its_successor`, parametrized 5 strategies × 2
  witnesses = **20 tests**.
- **Oracle re-armed.** `TestDependencyCheckPrecTolerance` (SC-m1) updated to the
  new grace: `test_subtolerance_gap_tolerated_like_validator` (a 200 µs gap is
  still tolerated on both surfaces) and a new
  `test_subminute_but_supratolerance_gap_now_flagged` (a 30 s gap — previously
  swallowed by the 60 s tol — is **now flagged** by both engine and validator, and
  they agree).
- **Pre-fix revert** (temporary restore of both 1-min constants): **22 of the 23**
  `TestSubMinuteDurationNotCollapsed` + SC-m1 tests fail; **post-fix all pass**
  (the three quantization classes §8/§9/SC-m1 together: **33 passed**).
- **Full CPM suite: 939 passed, 4 skipped** (+21 over the 918 after the §8 freeze;
  no regressions — shrinking the epsilon to 1 ms does not disturb any
  shift-calendar / time-window / resource-release test).

### 9.4 Retroactive note on §6 and §8

§6.2 and §8.2 were written while `_EVENT_EPSILON` was 1 min, and their prose cites
that value; those gates are **unchanged in placement** but now inherit the 1 ms
value. Their correctness arguments were never magnitude-dependent (they only
require the tolerance to exceed µs-class chain noise, which 1 ms does), so both
remain valid — and, as §9.1 shows, are *only* valid at a quantization scale. The
§8.4 ledger stands; every gate in it now runs on the 1 ms footing:

| gate | location | epsilon now |
|---|---|---|
| ES gate (heap collect)     | pert.py ~3611 | 1 ms |
| ES gate (candidate select) | pert.py ~3741 | 1 ms |
| completion gate            | pert.py ~5583 | 1 ms |
| lag gates                  | pert.py ~3625 / ~3753 | 1 ms (still unexercised; see §8.4) |
| validator `_PREC_TOL`      | schedule_validator.py ~66 | 1 ms |

**Lesson (feeds Toolkit C).** A tolerance that reconciles two representations of
the same quantity must be sized to the *representation gap* (here µs rounding), not
picked as an arbitrary "small" constant. A grace larger than the smallest
meaningful value it guards will silently erase that value — and if the oracle
shares the same oversized grace, the erasure is invisible. Size quantization
tolerances to the quantization, and keep the checker's tolerance no looser than
the engine's.

---

## 10. Open items / next phases (backlog, 2026-09-07)

The four fixes above closed the bugs the harness *could reach* in its current
form. The items below are the known gaps — scattered as asides through §2–§9 and
consolidated here so they stop being invisible. Ordered by ROI.

### 10.1 Extend the generator to emit lags — exercise the lag gates  *(highest ROI)*

The two lag gates ([`pert.py:3664`](../pert.py#L3664) /
[`pert.py:3809`](../pert.py#L3809)) share the **exact** microsecond-quantization
shape of the three gates fixed in §6/§8 (`pred_end + lag > time`, an exact
comparison of a CPM-derived instant against an accumulated actual one). They are
untested purely because `rcpsp_dag()` emits **no lags** — verified: no `lag`
handling in `test_property_based.py`. This is a bug class we already know the
shape of, sitting one generator change away from being fuzzed.

- **Action:** add an optional `lag_dict` to `rcpsp_dag()` / `build_pert` (the API
  is `p.lag_dict = {(a, b): hours}`, see §3.2 and `make_lag_pert`), drawing small
  non-negative F-S lags on a subset of edges.
- **Expectation:** the equality property (`makespan == cpm` under unlimited
  resources, with CPM early-starts now lag-inflated) very likely shrinks a
  counterexample on the lag gates — the §6/§8 fix then transfers directly
  (`pred_end + lag > time + _EVENT_EPSILON`).
- Closes the last open row of the §8.4 / §9.4 ledger.

> **Status — ADDRESSED (2026-09-07, this pass).** `rcpsp_dag()` now draws a
> non-negative F-S lag on a random subset of edges and `build_pert` writes them
> into `lag_dict`; the equality property runs over all five SGS. Exactly as
> predicted, it shrank two counterexamples on the lag gates — makespan inflated
> by one activity slot, and (zero-duration successor) a hard deadlock. Both were
> the microsecond-quantization shape §6/§8 anticipated; the fix transferred
> verbatim (`pred_end + lag > time + _EVENT_EPSILON` at both gates). Full root
> cause in **§11.1**; frozen in `test_bugfix_regressions.py::TestLagReleaseQuantization`.
> This closes the last open row of the §8.4 / §9.4 ledger.

### 10.2 Property harness Phase 2 — resource-constrained invariants

The harness only proves the **unlimited-resource** case. Two of the four
invariants listed in §2B are therefore **never property-tested**:

- `makespan ≥ CPM` under finite resources, and
- **monotonicity** — adding capacity cannot increase makespan.

Phase 2 was deliberately deferred (see the module docstring, §3.3, §5.3) pending
the per-activity crew-demand API.

- **Prerequisite (do not guess):** read how `test_invariants.py` and
  `test_replan_resources.py` attach per-activity crew demand + pool capacity, and
  wire the generator to *that* API.
- **Then:** sample a crew skill with a capacity and per-activity demand; assert
  `makespan ≥ cpm − TOL`, `assert_valid_schedule`, and monotonicity (re-run with
  `capacity+1`, assert makespan does not increase). Metamorphic variants in §2D
  (extra slack unit, ID permutation) are cheap add-ons here.

> **Status — ADDRESSED (2026-09-07, this pass).** Added `rcpsp_crew_instance`
> (single renewable `CREW` skill, per-activity demand, capacity always ≥ the
> largest single demand so the instance is guaranteed feasible) and two
> properties over all five SGS: `test_resource_makespan_at_least_cpm`
> (`makespan ≥ cpm`, feasible, all scheduled) and `test_resource_capacity_monotonic`
> (`makespan(cap) ≥ makespan(cap+δ)`). The feasibility property immediately shrank
> a counterexample — but it was **not** a real over-allocation: the validator's
> resource sweeps lacked the `_PREC_TOL` grace the precedence check already has,
> so a microsecond boundary sliver on a plain A→B chain read as `demand=2 > 1`.
> Root cause in **§11.2**; fixed in the validator (not the engine) and frozen in
> `test_bugfix_regressions.py::TestCrewBoundarySliverNotFlagged`. Monotonicity held
> once the sliver false-positive was gone (no Graham-anomaly counterexample
> surfaced within the `ci` profile's search).
>
> **Update (see §13).** The `thorough` profile later *did* surface the anticipated
> anomaly: `test_resource_capacity_monotonic` failed on all five SGS. It was a
> test-property bug, not an engine bug — monotonicity is unsound for these
> heuristic schedulers — so the test was downgraded (and renamed
> `test_resource_capacity_variants_valid`), keeping only the sound invariants.

### 10.3 Finish the Toolkit-A oracle sweep

20 of 35 test files now call `assert_valid_schedule` (§2A, updated). The remaining
~15 that build a schedule but never validate it are silent holes — a
path-specific feasibility slip in those areas would not be caught.

- **Action:** make `assert_valid_schedule(pert)` the standard last line of every
  test that runs the scheduler; audit the 15 non-adopters first (some may not
  produce a full schedule and are legitimately exempt).

> **Status — ADDRESSED (2026-09-07, this pass).** Swept the five remaining
> schedule-producing files. `assert_valid_schedule(pert, "<context>")` now
> follows every complete-and-feasible schedule site; every intentionally
> partial / pre-scheduling / infeasible-by-design site is annotated with a
> `# no oracle check:` reason (a comment, not a silent omission) so the exempt
> set is auditable. Checks added: **test_replan.py** (17), **test_scale_performance.py** (7),
> **test_system_state_pool.py** (9), **test_rcpsp_alns.py** (2), **test_ga.py** (1).
> `test_schedule_validator.py` left untouched (it tests the oracle itself). Result:
> **110 passed, 2 skipped** (`test_ga.py` / `test_rcpsp_alns.py` skip cleanly —
> optional deps `deap` / `alns` absent, so their checks are placed but not
> exercised in this env). No oracle failure surfaced on any path that ran — the
> engine was already feasible everywhere the sweep newly checks; the value is the
> standing guard against a future path-specific slip.

### 10.4 CI operational profile for the property test  *(new — became live with `332bad0`)*

Now that Hypothesis runs in CI, its **non-determinism** is a live concern the
earlier sections never addressed: each CI run explores fresh random examples (the
`.hypothesis` DB is not committed), so a run *can* surface a brand-new
counterexample on an unrelated PR — desirable as a gate, but a source of
"flaky-looking" reds. Cost also scales with `max_activities` (currently in flux:
HEAD 8, an uncommitted local bump toward 30–90).

- **Decide:** whether CI should pin a deterministic profile
  (`hypothesis.settings(derandomize=True)` or a fixed `--hypothesis-seed`) for
  reproducible reds, while a nightly/manual job runs the randomized, higher
  `max_examples` exploration. Capture the chosen `max_activities` and
  `max_examples` for CI explicitly rather than leaving them at the generator
  default.
- **Related:** time a full `run_cpm_pytests.py` at the chosen `max_activities`
  before raising it, so CI wall-clock is a decision, not a surprise.

> **Status — ADDRESSED (2026-09-07, this pass).** `test_property_based.py`
> registers two Hypothesis profiles and loads one from `HYPOTHESIS_PROFILE`
> (default `ci`): `ci` = `max_examples=150, deadline=None, derandomize=True`
> (a CI red reproduces exactly from the recorded seed); `thorough` =
> `max_examples=2000` for local deep runs
> (`HYPOTHESIS_PROFILE=thorough pytest …`). Generator default pinned at
> `max_activities=30`. `run_cpm_pytests.py` passes no Hypothesis args, so CI gets
> `ci`.
>
> **CI timeout post-mortem (2026-09-07).** The first CI run of this suite
> *timed out* — `cpm_pytest_suite` was killed at the framework's ~320 s default.
> Root cause: two knobs had drifted well past the values this section intended —
> the `ci` profile was registered at `max_examples=600` and *both* generators
> defaulted to `max_activities=90`. The decisive trap is that **CI always runs
> cold**: the `.hypothesis` example database is not committed, so CI re-explores
> every example from scratch, whereas a local dev machine replays a warm DB and
> looks 6–8× faster — a warm local "~17 s" hid a cold cost north of the CI
> budget. Fix: `ci` → `max_examples=150`, `max_activities` → `30` (both now match
> the numbers above), and the RAVEN test registration
> ([`tests`](../../tests/unit_tests/CPM/tests)) gains `max_time = 900` as margin.
> Re-measured **cold** (`.hypothesis` wiped): the property module runs in ~13 s
> and the *whole* CPM suite (992 passed, 3 skipped) in **~15 s** — the per-property
> split is now flat (~0.6–1.0 s each), no single hog. Rule of thumb for anyone
> raising these knobs: **re-time a cold run** (`rm -rf tests/unit_tests/CPM/.hypothesis`
> first), not a warm one, before it reaches CI.
>
> **Update — per-file registrations + a heavy nightly deep run (2026-09-07,
> supersedes the single `cpm_pytest_suite` + `max_time = 900` above).** The one
> aggregate registration was replaced by **one `RavenPython` block per test
> file** in [`tests`](../../tests/unit_tests/CPM/tests). Motivation: the aggregate
> form reported only "the CPM suite failed", and on a *timeout* rook `kill()`s
> pytest before its end-of-run summary prints (rook `Tester.py:604-627`), so it
> could not even name the file that hung — precisely the failure mode we hit. Each
> block runs `python run_cpm_pytests.py <file>` (the shim now takes the target
> file as an argument; `input` may carry args because rook builds the command as
> `<python> <input>` and runs it through the shell). Per-file gives per-file
> red/green on the dashboard and a per-file `max_time`: every file clears the
> default 300 s comfortably (the heaviest, `cpm_property_based`, is ~15 s cold —
> a ~20× margin), so the aggregate's 900 s crutch is gone.
> [`test_tests_registration.py`](../../tests/unit_tests/CPM/test_tests_registration.py)
> guards the one downside of per-file — a newly-added `test_*.py` silently never
> running — by asserting every sibling test file has a block (and none is stale).
>
> Deep randomized exploration (the "high settings to catch bugs" the per-PR gate
> deliberately does *not* run) now lives in a separate **`heavy = True`**
> registration, `cpm_property_deep`: `heavy` gives it run-type `{"heavy"}` and
> drops `"normal"` (rook `Tester.py:417-421`), so it is **skipped on every per-PR
> run and executes only under `run_tests --heavy`** (nightly). It runs
> `run_cpm_pytests.py --thorough test_property_based.py` → the *randomized*
> `thorough` profile (2000 examples, fresh seed each run, `max_time = 3600`),
> hunting new counterexamples off the PR critical path; a red there freezes into
> `test_bugfix_regressions.py` like any other. This resolves the standing tension:
> the per-PR `ci` gate stays modest and deterministic (reproducible reds, no CI
> tax, no flaky reds on unrelated PRs), while the deep search runs where wall-clock
> and non-determinism are acceptable.

### 10.5 Toolkit C — the shared feasibility gate (bug-family #2)  *(largest, own design note)*

Family #2 (a constraint enforced in one scheduler path but not the others) is the
one root-cause family with **no structural fix yet**. Serial / parallel /
from-scratch / replan each re-implement feasibility; §4.3 / §5.4 flag collapsing
them into a single shared gate every dispatch path calls — the only invasive
refactor in this note. **Deserves its own design note before touching `pert.py`.**

> **Status — DESIGN NOTE WRITTEN, execution deferred (2026-09-07, this pass).**
> See **§12**. The parallel side is already unified (`_fits_with_tentative` /
> `_apply_tentative`); the only remaining duplication is the serial engine's
> `_serial_check_feasibility`. Because this is the one item that touches the
> production serial (GA/ALNS) path, it is *not* executed with the additive work
> of this pass — it lands in its own PR gated on the mandatory differential
> verification described in §12.

### 10.6 Toolkit D — a real external oracle

`psplib_regression.py` still compares to its *own* frozen output, so a
*systematically* wrong scheduler stays green (§2D / §4.4). Either import published
PSPLIB best-known-solution values, or brute-force / CP-SAT the optimum on
≤ ~8-activity instances, and assert `makespan ≥ optimum`. This is the only item
that would catch a "plausible but globally wrong" makespan; everything above
catches feasibility violations, not sub-optimality.

> **Status — ADDRESSED (2026-09-07, this pass).** New `test_psplib_oracle.py`
> loads the four PSPLIB instances with published best-known solutions
> (`j301_1`=43, `j601_1`=77, `j901_1`=73, `j1201_1`=105) from
> `doc/demos/rcpsp/examples/`, reads the BKS from
> `doc/demos/benchmarks/best_results.json`, runs all five SGS, and asserts the
> one inequality no correct heuristic can violate:
> `scheduled_duration − 2.0 ≥ BKS − TOL` (the −2.0 removes the START/END dummies,
> as in `psplib_regression.py`) plus `assert_valid_schedule`. This is a genuine
> *external* reference, distinct from the self-frozen goldens in
> `psplib_regression.py`; it skips cleanly if the instance JSON or BKS table is
> absent. Zero new dependencies.

### 10.7 Doc hygiene (carried)

- Open decision #2 (validator as a runtime post-condition behind a flag) and the
  §6.3 contract/assertion program (Toolkit C detection logic) remain unstarted.
- The embedded raw run logs are labelled "Initial" and "Third" testing outcome
  with no "Second" — harmless, but a reader will look for the missing one.

---

## 11. Bugs found this pass (2026-09-07) — root cause, fix, freeze

Two bugs surfaced the moment §10.1 (lags) and §10.2 (resource invariants)
extended the fuzzer's reach. Both are bug-family #5 (microsecond quantization),
the same family §6/§8/§9 fixed — the fuzzer simply reached two comparison sites
those passes had not yet exercised. Documented here in the §6–§9 style so the
family's footprint stays fully mapped.

### 11.1 Lag-release microsecond quantization (engine)

**Where.** The two lag-satisfaction gates the engine uses to decide whether a
successor may start: one in `_collect_candidates_from_heap`, one in
`_select_candidate_activities` ([`pert.py:3664`](../pert.py#L3664) /
[`pert.py:3809`](../pert.py#L3809)). Both read, verbatim:

```python
if pred_end is not None and pred_end + timedelta(hours=lag_h) > time:
    lag_unmet = True
    break
```

**Root cause.** Identical in shape to §6/§8. `pred_end` is an actual completion
instant — a `datetime` carrying an accumulated, microsecond-quantized timedelta.
`time` is the candidate event instant, likewise µs-quantized, but the lag target
`pred_end + timedelta(hours=lag_h)` is a *float-hours* offset added on top. When
the lag release is supposed to coincide *exactly* with an event instant, the two
sides differ by the ~1 µs quantization residue, and the exact `>` fires when it
should not. The successor is then held back to the next event tick.

Two distinct symptoms, both shrunk by the equality property (`makespan == cpm`
under unlimited resources) once the generator emitted lags:

- **Inflation (CE-1).** A non-zero-duration successor deferred by one event slot
  → makespan one activity-duration longer than CPM. Silent wrong answer.
- **Deadlock (CE-2).** A *zero-duration* successor whose lag release lands one µs
  after its own scheduled instant is never re-queued (a zero-length activity
  produces no later event to reopen the gate), so the schedule strands it —
  `n_completed < n_activities`. Hard failure.

**Fix.** Transfer the §6/§8 remedy verbatim — grant the gate the same
`_EVENT_EPSILON` (1 ms) grace the ES gate directly above it already uses, so a
lag whose release is within a millisecond of `time` counts as met:

```python
if (pred_end is not None
        and pred_end + timedelta(hours=lag_h) > time + self._EVENT_EPSILON):
    lag_unmet = True
    break
```

`_EVENT_EPSILON` (1 ms) dwarfs the µs residue but is far below any real activity
duration, so it erases only the quantization noise, never a genuine lag. Applied
at **both** gates (`replace_all`).

**Verification.** The equality property runs clean over all five SGS with lags
enabled (`ci` profile). Both counterexamples are frozen, parametrized over all
five SGS, in `test_bugfix_regressions.py::TestLagReleaseQuantization`
(`test_lagged_successor_not_deferred`, a6_dur = 1.0 → inflation;
`test_zero_duration_lagged_successor_not_stranded`, a6_dur = 0.0 → deadlock).

### 11.2 Resource-sweep boundary sliver false-positive (validator)

**Where.** The three resource-feasibility sweeps in the *oracle*, not the engine:
`_check_crew_feasibility`, `_check_equipment_feasibility`,
`_check_location_feasibility` in
[`schedule_validator.py`](../schedule_validator.py). Each walks a time-sorted
event list (ends before starts at equal instants) accumulating concurrent demand.

**Root cause.** This one is an *oracle* bug — the engine's schedule was correct;
the checker wrongly flagged it. §6.3's epsilon-tolerant completion gate
deliberately lets a predecessor complete up to `_EVENT_EPSILON` early to avoid
the deadlock class, so on a plain A→B chain over a shared capacity-1 crew, B can
legitimately be placed ~1 µs *before* A's recorded end instant. The sweep's
interval-advance guards (`if nxt <= t …` / `if nxt <= t or current_demand <= 0`)
had **no tolerance**, so that sub-microsecond overlap registered as
`concurrent_demand = 2 > capacity = 1` — a phantom over-allocation on a schedule
that is, in every operational sense, a clean hand-off.

The precedence check in the same file already carried a `_PREC_TOL`
(1 ms) grace for exactly this reason (§9); the resource sweeps had simply never
been reached by a chain-on-shared-crew instance before the §10.2 generator built
one.

**Fix — validator side, deliberately not the engine.** The counterexample is a
correct schedule, so the engine must not change: widening or removing the §6.3
completion-gate tolerance to make the instants coincide exactly would risk
re-opening the very deadlock class §6.3 exists to prevent. The right move is to
give the checker the same 1 ms grace its own precedence check already uses. All
three sweeps changed from an exact interval-advance to a `_PREC_TOL`-tolerant
one:

```python
# crew & equipment (shared guard shape):
if (nxt - t) <= _PREC_TOL or current_demand <= 0:
    ...
# location:
if (nxt - t) <= _PREC_TOL:
    ...
```

A sliver thinner than `_PREC_TOL` is collapsed and no longer counted as overlap;
anything wider is still a real concurrency and still flagged.

**Verification.** The §10.2 feasibility property runs clean over all five SGS.
Frozen in `test_bugfix_regressions.py::TestCrewBoundarySliverNotFlagged`:
`test_chain_on_shared_crew_is_feasible` (the A→B chain, all five SGS) plus a
**guard** test, `test_real_overallocation_still_flagged`, that forces two
activities to genuinely overlap on a capacity-1 crew and asserts
`_check_crew_feasibility` still returns exactly one violation — proving the grace
discards only sub-millisecond noise, not real over-allocation.

> **Cross-checker caution (restated from §9).** The engine and the oracle now
> share a 1 ms convention at three coupled sites (`_EVENT_EPSILON` on the
> completion + lag gates, `_PREC_TOL` on the precedence + resource-sweep checks).
> They are intentionally equal; if either constant is ever retuned, both sides
> must move together, or the oracle stops being an independent check of the
> engine at the boundary.

---

## 12. §10.5 design note — the shared feasibility gate (execution deferred)

§10.5 (bug-family #2: a constraint enforced on one dispatch path but not the
others) is the one root-cause family with **no structural fix**. It is
*intentionally not executed this pass* — it is the only change that touches the
production serial engine (GA/ALNS), i.e. the exact silent-wrong-answer surface
this whole effort exists to shrink. This section is the design note §10.5 called
for; execution lands in its own PR behind the differential verification below.

**Current state (already better than §4.3/§5.4 implied).** The *parallel* side is
already unified: from-scratch, `_from` seeding, replan, and all five SGS
(including look-ahead) funnel their placement test through a single pair —
`_fits_with_tentative` ([`pert.py:4152`](../pert.py#L4152)) and
`_apply_tentative` ([`pert.py:4288`](../pert.py#L4288)). The only genuine
remaining duplication is the *serial* engine's `_serial_check_feasibility`
([`pert.py:6576`](../pert.py#L6576), ~170 LOC), reached from the GA/ALNS serial
decode path.

**Obstacle (why this is not a mechanical merge).** The two gates read different
capacity representations. The parallel gate consumes live per-event snapshot
dicts (skill → remaining count at `time`); the serial gate walks profile lists it
maintains itself. A naive "call the parallel gate from the serial path" would
silently reinterpret one representation as the other — precisely the family-#2
drift we are trying to remove, now injected by the fix.

**Proposed approach.**
1. Extract a **representation-neutral feasibility core** taking an explicit
   `(demand, available)` view and returning the accept/reject decision + the
   resource delta, with *no* knowledge of how either caller stores capacity.
2. Adapt both callers to build that view: the parallel gate from its snapshot
   dicts (a near-identity wrapper over today's `_fits_with_tentative`), the serial
   gate from its profile lists.
3. Keep the epsilon conventions (§11.2) inside the core so both paths inherit one
   boundary rule.

**Mandatory verification before merge (non-negotiable).** A differential test:
run the current serial engine and the refactored one on thousands of fuzzed
instances (reuse the §10.2 generator, add serial-specific shapes) and assert
**identical** accept/reject decisions *and* identical makespans on every
instance — not merely "both feasible." Only once that differential is green
across a `thorough`-profile run does the refactor merge, and the differential
harness itself is frozen so the two paths can never again diverge unnoticed.

**Why deferred, explicitly.** Every other item this pass is additive (tests, a
generator knob, an external oracle, docs) and cannot change a production
makespan. §10.5 is the sole exception. Bundling an invasive engine refactor with
the additive work would make a regression here indistinguishable from the new
tests' own noise. It gets its own PR, its own differential gate, and its own
review.

---
## 13. Capacity-monotonicity property is unsound for heuristic SGS (2026-09-07) — property downgraded, no engine bug

The nightly `thorough` sweep (`[./cpm_property_deep]`, 2000 examples, fresh seed)
went **red**: `test_resource_capacity_monotonic` (§10.2) failed on **all five**
SGS, asserting `makespan(cap_low) >= makespan(cap_high) - TOL` ("more crew never
lengthens the schedule"). Triaged this session — **it is a test-property bug, not
an engine bug.** The property was removed; the test was downgraded and renamed.

### 13.1 Two independent root causes

**(a) Monotonicity is mathematically unsound for these schedulers.** All five SGS
are heuristic greedy list-schedulers, not optimal solvers (`MDKnapsackScheduler`
self-documents "greedy approximation… for exact solution, would need integer
programming solver", [pert.py:8113-8114](../pert.py#L8113-L8114)). Capacity feeds
directly into candidate-set sizing/truncation:
`max_slots = crew_pool.get_availability(...) // d`, then
`k_needed = max(1, max_slots) * 8` ([pert.py:3593-3607](../pert.py#L3593-L3607)),
and `heapq.nlargest(k, ...)` vs the full rank when `k < n`
([pert.py:5578-5589](../pert.py#L5578-L5589)). More crew ⇒ a *different* candidate
set is considered at an event ⇒ a different ordering ⇒ a possibly *longer*
makespan. This is a textbook **Graham anomaly**: deterministic and input-dependent.
Witnesses from the failing run: `[first] cap=2→2.5 < cap=3→3.0`,
`[look_ahead] cap=2→6.0 < cap=3→6.5`. The inequality holds only for the *optimal*
makespan, which none of these strategies computes. (The §10.2 status note already
anticipated this — "no Graham-anomaly counterexample surfaced within the `ci`
profile's search"; the deeper `thorough` search found one.)

**(b) `max_use_res_shuffled` is nondeterministic.** `_shuffle_candidates` calls
`random.shuffle` ([pert.py:5599](../pert.py#L5599)) with **no per-call seed**.
`Pert.__init__` seeds `random` once to the fixed constant `2506178`
([pert.py:140](../pert.py#L140)), and `calculateScheduleWithResources` never
reseeds — so the global RNG stream flows continuously across calls. The test
constructs `tight` and `loose`, then schedules them back-to-back, so `loose`
shuffles from wherever `tight` left the RNG. Witness: a shrunk counterexample with
`cap=1` vs `cap=1` → 2.0 vs 3.0 (identical input, cap delta zero, different
answer). This is why `shuffled` failures do not reproduce on replay: the RNG
history (test order, prior draws) differs. The other four strategies are pure
functions of the input on the default `TF_based` path (verified by a repeated-run
determinism probe: 8–12 identical runs each), so their failures are genuine
anomalies, not noise.

### 13.2 The engine is correct

Makespan monotonicity in capacity is simply **not a theorem** for priority-rule
heuristics; the engine's behavior is expected. So there is nothing to fix in
`pert.py` and **nothing to freeze in `test_bugfix_regressions.py`** — that step
freezes shrunk counterexamples after an *engine* fix, and there is none here. The
"freeze" is the corrected test plus this note.

### 13.3 The fix (test-only, this PR)

`tests/unit_tests/CPM/test_property_based.py`: `test_resource_capacity_monotonic`
→ **`test_resource_capacity_variants_valid`**. Drops the cross-capacity
comparison; asserts only the sound invariants — feasible, all activities
scheduled, `makespan >= cpm - TOL` — at **both** `cap_low` and `cap_high`. The
docstring records why monotonicity was removed. It still earns its keep: it
exercises `cap_high`, which `test_resource_makespan_at_least_cpm` (cap_low only)
does not. The Phase-2 header comment and module docstring were updated to stop
advertising monotonicity. Green under both `ci` and `thorough`.

The hand-written monotonicity tests in `test_invariants.py`
(`test_monotonicity`, `test_monotonicity_parametric`, `test_json_fixture_monotonicity`)
are **left as-is on purpose**: they run fixed, simple instances under the
deterministic default SGS (`max_use_res_ranked`) where the outcome is known and
monotonicity genuinely holds (e.g. 1 welder→7 h vs 2 welders→4 h). A fixed-instance
regression assertion is sound; only the *universal* fuzzed assertion was not.

### 13.4 Latent observations (recorded, not fixed here)

- Tie-break fragility: `md_knapsack` ([pert.py:8137](../pert.py#L8137)) and
  `look_ahead` ([pert.py:8283](../pert.py#L8283)) sort ties with no name
  tiebreaker, leaning on incoming candidate insertion order. Deterministic on the
  `TF_based` path (name-ordered heap), but fragile if a set-ordered candidate dict
  were ever fed in.
- The O(n) `_ready` fallback ([pert.py:3774](../pert.py#L3774)) iterates an
  identity-hashed `Activity` set (address-ordered, varies run-to-run); reached only
  on dynamic priority-rule modes, not the default path.
- Optional future engine improvement (**not** in scope): reseed per call, or add a
  `seed=` argument to `calculateScheduleWithResources`, so `max_use_res_shuffled`
  is reproducible in isolation rather than dependent on process-wide RNG history.
