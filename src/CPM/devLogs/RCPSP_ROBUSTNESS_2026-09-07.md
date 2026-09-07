# RCPSP Engine Robustness — Notes & Prototype Plan

**Date:** 2026-09-07
**Author:** Claude Code (session with D. Mandelli)
**Scope:** `src/CPM/pert.py` (the RCPSP engine), `src/CPM/schedule_validator.py`
(the independent feasibility oracle), and the `tests/unit_tests/CPM/` suite.
**Branch:** `mandd/res_opt`
**Status:** brainstorm + a concrete starting point for prototype #1
(property-based testing). No engine code changed by this note.

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

**Gap:** only ~3 of ~37 test files call it. Most tests that build a schedule
never ask the oracle whether that schedule is feasible.

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

### 3.1 Prerequisite

Hypothesis is **not installed** in the dev env yet:

```bash
pip install hypothesis
```

Dependency declaration: `setup.py` has `dependencies = []` and the CPM suite is
governed by `tests/unit_tests/CPM/pytest.ini`. Decide where the test-only dep
lives — options: a `requirements-dev.txt`, or a `[project.optional-dependencies]`
`test` extra in `pyproject.toml`. (Recommendation: a `test` extra, installed in
CI via `pip install -e ".[test]"`.) The module below should `pytest.importorskip`
Hypothesis so the suite still collects cleanly where it isn't installed —
mirroring the `ravenframework` guard in `test_raven_interface.py`.

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

1. **Where the `hypothesis` test-dep is declared** (`requirements-dev.txt` vs a
   `pyproject.toml` `[project.optional-dependencies].test` extra) and how CI
   installs it.
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
gates at [`pert.py:3625`](../pert.py#L3625) / [`pert.py:3753`](../pert.py#L3753)
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

## Second testing outcome
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=16.7h | strategy=md_knapsack | max_time=167.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=16.7h | actual=16.7h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=16.2h | strategy=md_knapsack | max_time=162.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=16.2h | actual=16.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=16.0h | strategy=md_knapsack | max_time=159.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=16.0h | actual=16.0h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=15.9h | strategy=md_knapsack | max_time=158.7h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=15.9h | actual=15.9h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=15.9h | strategy=md_knapsack | max_time=159.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=15.9h | actual=15.9h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=16.0h | strategy=md_knapsack | max_time=159.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=16.0h | actual=16.0h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=16.0h | strategy=md_knapsack | max_time=159.8h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=16.0h | actual=16.0h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=16.0h | strategy=md_knapsack | max_time=159.7h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=16.0h | actual=16.0h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=16.0h | strategy=md_knapsack | max_time=159.7h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=16.0h | actual=16.0h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=13.7h | strategy=md_knapsack | max_time=137.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=13.7h | actual=13.7h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=13.7h | strategy=md_knapsack | max_time=137.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=13.7h | actual=13.7h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=14.9h | strategy=md_knapsack | max_time=148.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.9h | actual=14.9h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=15.2h | strategy=md_knapsack | max_time=152.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=15.2h | actual=28.2h | delay=0.0h | completed=6/6 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=3/6 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=4.2h | delay=0.0h | completed=6/6 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.1h | strategy=md_knapsack | max_time=31.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=4.1h | strategy=md_knapsack | max_time=41.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=4.1h | actual=4.1h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.6h | strategy=md_knapsack | max_time=36.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.6h | actual=3.6h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.4h | strategy=md_knapsack | max_time=33.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.4h | actual=3.4h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=31.8h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.1h | strategy=md_knapsack | max_time=31.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=4.1h | strategy=md_knapsack | max_time=41.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=4.1h | actual=4.1h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.6h | strategy=md_knapsack | max_time=36.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.6h | actual=3.6h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.4h | strategy=md_knapsack | max_time=33.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.4h | actual=3.4h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=31.8h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=4 | CPM=1.1h | strategy=md_knapsack | max_time=11.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.1h | actual=1.1h | delay=0.0h | completed=4/4 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=4 | CPM=0.0h | strategy=md_knapsack | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=4/4 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.1h | strategy=md_knapsack | max_time=11.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.1h | actual=1.1h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=0.0h | strategy=md_knapsack | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=3/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=4/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.1h | strategy=md_knapsack | max_time=11.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.1h | actual=1.1h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.0h | strategy=md_knapsack | max_time=30.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.0h | actual=3.0h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=5.0h | strategy=md_knapsack | max_time=50.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=5.0h | actual=5.0h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=4.0h | strategy=md_knapsack | max_time=40.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=4.0h | actual=4.0h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.5h | strategy=md_knapsack | max_time=35.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.1h | strategy=md_knapsack | max_time=31.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=31.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=1.1h | strategy=md_knapsack | max_time=11.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.1h | actual=1.1h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=3 | CPM=1.1h | strategy=md_knapsack | max_time=11.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.1h | actual=1.1h | delay=0.0h | completed=3/3 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=3 | CPM=1.0h | strategy=md_knapsack | max_time=10.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.0h | actual=1.0h | delay=0.0h | completed=3/3 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=4/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.1h | strategy=md_knapsack | max_time=11.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.1h | actual=1.1h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.1h | strategy=md_knapsack | max_time=11.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.1h | actual=1.1h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.0h | strategy=md_knapsack | max_time=20.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.0h | actual=2.0h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=4.0h | strategy=md_knapsack | max_time=40.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=4.0h | actual=4.0h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=3.0h | strategy=md_knapsack | max_time=30.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.0h | actual=3.0h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.5h | strategy=md_knapsack | max_time=25.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.5h | actual=2.5h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.1h | strategy=md_knapsack | max_time=21.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=21.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=3.1h | strategy=md_knapsack | max_time=31.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.6h | strategy=md_knapsack | max_time=26.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.6h | actual=2.6h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.4h | strategy=md_knapsack | max_time=23.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.4h | actual=2.4h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=21.8h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=3.1h | strategy=md_knapsack | max_time=31.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.6h | strategy=md_knapsack | max_time=26.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.6h | actual=2.6h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.4h | strategy=md_knapsack | max_time=23.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.4h | actual=2.4h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=21.8h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=4 | CPM=1.1h | strategy=md_knapsack | max_time=11.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.1h | actual=1.1h | delay=0.0h | completed=4/4 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=4 | CPM=2.1h | strategy=md_knapsack | max_time=21.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.1h | actual=2.1h | delay=0.0h | completed=4/4 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=4 | CPM=1.1h | strategy=md_knapsack | max_time=11.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.1h | actual=1.1h | delay=0.0h | completed=4/4 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=md_knapsack | max_time=32.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=4.2h | delay=0.0h | completed=6/6 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.2h | strategy=md_knapsack | max_time=22.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.2h | actual=2.2h | delay=0.0h | completed=3/5 | iterations=4
__________________________________________________________________ test_unconstrained_makespan_equals_cpm[look_ahead] ___________________________________________________________________
test_property_based.py:131: in test_unconstrained_makespan_equals_cpm
    @settings(max_examples=200, deadline=None,
                   ^^^^^^
test_property_based.py:137: in test_unconstrained_makespan_equals_cpm
    assert_valid_schedule(p)
conftest.py:50: in assert_valid_schedule
    pytest.fail(
E   Failed: Scheduler output failed validation.
E   
E   Schedule Validation Report
E   ==================================================
E   Status     : INFEASIBLE
E   Violations : 1
E   Warnings   : 0
E   
E   --- Violations ---
E     [ERROR] [completeness ] schedule: 2 of 6 activities not scheduled (first 5: ['A3'])  [excess=2.00]
E   ==================================================
E   Failing test case: test_unconstrained_makespan_equals_cpm(
E       sgs='look_ahead',
E       inst=(4, [0.0, 0.0, 1.7447551588106158, 1.504299842107538], [(2, 3)]),
E   )
E   Explanation:
E       These lines were always and only run by failing test cases:
E           /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/pert.py:3341
E           /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/pert.py:5206
E           /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/schedule_validator.py:215
E           /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/schedule_validator.py:301
E           /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/schedule_validator.py:306
--------------------------------------------------------------------------------- Captured stderr call ----------------------------------------------------------------------------------
INFO:CPM.pert:Starting event-driven RCPSP | activities=3 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=3/3 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=8/8 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=3 | CPM=16.0h | strategy=look_ahead | max_time=160.0h
INFO:CPM.pert:Scheduling complete | CPM=16.0h | actual=16.0h | delay=0.0h | completed=3/3 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.9h | strategy=look_ahead | max_time=219.0h
INFO:CPM.pert:Scheduling complete | CPM=21.9h | actual=21.9h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=13.7h | strategy=look_ahead | max_time=136.6h
INFO:CPM.pert:Scheduling complete | CPM=13.7h | actual=13.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO:CPM.pert:Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=3 | CPM=6.5h | strategy=look_ahead | max_time=65.0h
INFO:CPM.pert:Scheduling complete | CPM=6.5h | actual=6.5h | delay=0.0h | completed=3/3 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=6.5h | strategy=look_ahead | max_time=65.0h
INFO:CPM.pert:Scheduling complete | CPM=6.5h | actual=6.5h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=3 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=3/3 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=7
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO:CPM.pert:Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=20.5h | strategy=look_ahead | max_time=205.0h
INFO:CPM.pert:Scheduling complete | CPM=20.5h | actual=20.5h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.5h | strategy=look_ahead | max_time=215.0h
INFO:CPM.pert:Scheduling complete | CPM=21.5h | actual=21.5h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.0h | strategy=look_ahead | max_time=210.0h
INFO:CPM.pert:Scheduling complete | CPM=21.0h | actual=21.0h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.3h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.3h | actual=21.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.1h | strategy=look_ahead | max_time=211.3h
INFO:CPM.pert:Scheduling complete | CPM=21.1h | actual=21.1h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=211.9h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.2h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.4h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.3h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.3h | actual=21.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO:CPM.pert:Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.5h
INFO:CPM.pert:Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=6/8 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=7.5h | strategy=look_ahead | max_time=75.0h
INFO:CPM.pert:Scheduling complete | CPM=7.5h | actual=7.5h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.5h | strategy=look_ahead | max_time=85.0h
INFO:CPM.pert:Scheduling complete | CPM=8.5h | actual=8.5h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.0h | strategy=look_ahead | max_time=80.0h
INFO:CPM.pert:Scheduling complete | CPM=8.0h | actual=8.0h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.5h
INFO:CPM.pert:Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.1h | strategy=look_ahead | max_time=81.3h
INFO:CPM.pert:Scheduling complete | CPM=8.1h | actual=8.1h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=81.9h
INFO:CPM.pert:Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.2h
INFO:CPM.pert:Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.4h
INFO:CPM.pert:Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.5h
INFO:CPM.pert:Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.5h
INFO:CPM.pert:Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.5h
INFO:CPM.pert:Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=7.7h | strategy=look_ahead | max_time=77.4h
INFO:CPM.pert:Scheduling complete | CPM=7.7h | actual=7.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.7h | strategy=look_ahead | max_time=87.4h
INFO:CPM.pert:Scheduling complete | CPM=8.7h | actual=8.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.4h
INFO:CPM.pert:Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.5h | strategy=look_ahead | max_time=84.9h
INFO:CPM.pert:Scheduling complete | CPM=8.5h | actual=8.5h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.4h | strategy=look_ahead | max_time=83.7h
INFO:CPM.pert:Scheduling complete | CPM=8.4h | actual=8.4h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=83.1h
INFO:CPM.pert:Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.8h
INFO:CPM.pert:Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.6h
INFO:CPM.pert:Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.5h
INFO:CPM.pert:Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.5h
INFO:CPM.pert:Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.5h
INFO:CPM.pert:Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/8 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=2.7h | strategy=look_ahead | max_time=27.4h
INFO:CPM.pert:Scheduling complete | CPM=2.7h | actual=2.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.7h | strategy=look_ahead | max_time=37.4h
INFO:CPM.pert:Scheduling complete | CPM=3.7h | actual=3.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.5h | strategy=look_ahead | max_time=34.9h
INFO:CPM.pert:Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.4h | strategy=look_ahead | max_time=33.7h
INFO:CPM.pert:Scheduling complete | CPM=3.4h | actual=3.4h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=33.1h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.8h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.6h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=4 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=4/4 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO:CPM.pert:Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=5/5 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=5/5 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=6/6 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=7 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=7/7 | iterations=6
INFO:CPM.pert:Starting event-driven RCPSP | activities=7 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=7/7 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=7 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=7/7 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=2.5h | strategy=look_ahead | max_time=25.0h
INFO:CPM.pert:Scheduling complete | CPM=2.5h | actual=2.5h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.5h | strategy=look_ahead | max_time=35.0h
INFO:CPM.pert:Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.0h | strategy=look_ahead | max_time=30.0h
INFO:CPM.pert:Scheduling complete | CPM=3.0h | actual=3.0h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.1h | strategy=look_ahead | max_time=31.3h
INFO:CPM.pert:Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=31.9h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.2h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=8 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=8/8 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=7 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO:CPM.pert:Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=7/7 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=7 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=5/7 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=6/6 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=4/6 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=4 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=4/4 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=5/5 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=2.7h | strategy=look_ahead | max_time=27.4h
INFO:CPM.pert:Scheduling complete | CPM=2.7h | actual=2.7h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.7h | strategy=look_ahead | max_time=37.4h
INFO:CPM.pert:Scheduling complete | CPM=3.7h | actual=3.7h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.5h | strategy=look_ahead | max_time=34.9h
INFO:CPM.pert:Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.4h | strategy=look_ahead | max_time=33.7h
INFO:CPM.pert:Scheduling complete | CPM=3.4h | actual=3.4h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=33.1h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.8h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.6h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=2.5h | strategy=look_ahead | max_time=25.0h
INFO:CPM.pert:Scheduling complete | CPM=2.5h | actual=2.5h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.5h | strategy=look_ahead | max_time=35.0h
INFO:CPM.pert:Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.0h | strategy=look_ahead | max_time=30.0h
INFO:CPM.pert:Scheduling complete | CPM=3.0h | actual=3.0h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.1h | strategy=look_ahead | max_time=31.3h
INFO:CPM.pert:Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=31.9h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.2h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=2.5h | strategy=look_ahead | max_time=25.0h
INFO:CPM.pert:Scheduling complete | CPM=2.5h | actual=2.5h | delay=0.0h | completed=5/5 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO:CPM.pert:Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=5/5 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO:CPM.pert:Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=5/5 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=6/6 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO:CPM.pert:Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=6/6 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO:CPM.pert:Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=4/6 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=3 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO:CPM.pert:Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=3/3 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=3 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=3/3 | iterations=3
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=2.7h | strategy=look_ahead | max_time=27.4h
INFO:CPM.pert:Scheduling complete | CPM=2.7h | actual=2.7h | delay=0.0h | completed=5/5 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=2.7h | strategy=look_ahead | max_time=27.4h
INFO:CPM.pert:Scheduling complete | CPM=2.7h | actual=2.7h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.7h | strategy=look_ahead | max_time=37.4h
INFO:CPM.pert:Scheduling complete | CPM=3.7h | actual=3.7h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.5h | strategy=look_ahead | max_time=34.9h
INFO:CPM.pert:Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.4h | strategy=look_ahead | max_time=33.7h
INFO:CPM.pert:Scheduling complete | CPM=3.4h | actual=3.4h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=33.1h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.8h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.6h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=2.5h | strategy=look_ahead | max_time=25.0h
INFO:CPM.pert:Scheduling complete | CPM=2.5h | actual=2.5h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.5h | strategy=look_ahead | max_time=35.0h
INFO:CPM.pert:Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.0h | strategy=look_ahead | max_time=30.0h
INFO:CPM.pert:Scheduling complete | CPM=3.0h | actual=3.0h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.1h | strategy=look_ahead | max_time=31.3h
INFO:CPM.pert:Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=31.9h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.2h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO:CPM.pert:Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=6/6 | iterations=4
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO:CPM.pert:Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=6/6 | iterations=5
INFO:CPM.pert:Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO:CPM.pert:Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=4/6 | iterations=4
----------------------------------------------------------------------------------- Captured log call -----------------------------------------------------------------------------------
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=3 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=3/3 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=8/8 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=3 | CPM=16.0h | strategy=look_ahead | max_time=160.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=16.0h | actual=16.0h | delay=0.0h | completed=3/3 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.9h | strategy=look_ahead | max_time=219.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.9h | actual=21.9h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=13.7h | strategy=look_ahead | max_time=136.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=13.7h | actual=13.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=28.4h | strategy=look_ahead | max_time=284.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=28.4h | actual=28.4h | delay=0.0h | completed=6/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=3 | CPM=6.5h | strategy=look_ahead | max_time=65.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=6.5h | actual=6.5h | delay=0.0h | completed=3/3 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=6.5h | strategy=look_ahead | max_time=65.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=6.5h | actual=6.5h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=3 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=3/3 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=7
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=14.7h | strategy=look_ahead | max_time=147.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=14.7h | actual=14.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=6/8 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=20.5h | strategy=look_ahead | max_time=205.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=20.5h | actual=20.5h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.5h | strategy=look_ahead | max_time=215.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.5h | actual=21.5h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.0h | strategy=look_ahead | max_time=210.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.0h | actual=21.0h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.3h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.3h | actual=21.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.1h | strategy=look_ahead | max_time=211.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.1h | actual=21.1h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=211.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.3h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.3h | actual=21.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=21.2h | strategy=look_ahead | max_time=212.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=21.2h | actual=21.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=6/8 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=7.5h | strategy=look_ahead | max_time=75.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=7.5h | actual=7.5h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.5h | strategy=look_ahead | max_time=85.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.5h | actual=8.5h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.0h | strategy=look_ahead | max_time=80.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.0h | actual=8.0h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.1h | strategy=look_ahead | max_time=81.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.1h | actual=8.1h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=81.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=7.7h | strategy=look_ahead | max_time=77.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=7.7h | actual=7.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.7h | strategy=look_ahead | max_time=87.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.7h | actual=8.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.5h | strategy=look_ahead | max_time=84.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.5h | actual=8.5h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.4h | strategy=look_ahead | max_time=83.7h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.4h | actual=8.4h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=83.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.8h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.2h | strategy=look_ahead | max_time=82.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.2h | actual=8.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=8.3h | strategy=look_ahead | max_time=82.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=8.3h | actual=8.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/8 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=2.7h | strategy=look_ahead | max_time=27.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.7h | actual=2.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.7h | strategy=look_ahead | max_time=37.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.7h | actual=3.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.5h | strategy=look_ahead | max_time=34.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.4h | strategy=look_ahead | max_time=33.7h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.4h | actual=3.4h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=33.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.8h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=4 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=4/4 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=5/5 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=6/6 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=7 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=7/7 | iterations=6
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=7 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=7/7 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=7 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=7/7 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=2.5h | strategy=look_ahead | max_time=25.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.5h | actual=2.5h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.5h | strategy=look_ahead | max_time=35.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.0h | strategy=look_ahead | max_time=30.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.0h | actual=3.0h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.1h | strategy=look_ahead | max_time=31.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=31.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=8 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=8/8 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=7 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=7/7 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=7 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=5/7 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=6/6 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=4/6 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=4 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=4/4 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.7h | strategy=look_ahead | max_time=27.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.7h | actual=2.7h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.7h | strategy=look_ahead | max_time=37.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.7h | actual=3.7h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.5h | strategy=look_ahead | max_time=34.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.4h | strategy=look_ahead | max_time=33.7h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.4h | actual=3.4h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=33.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.8h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.5h | strategy=look_ahead | max_time=25.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.5h | actual=2.5h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.5h | strategy=look_ahead | max_time=35.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.0h | strategy=look_ahead | max_time=30.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.0h | actual=3.0h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.1h | strategy=look_ahead | max_time=31.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=31.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.5h | strategy=look_ahead | max_time=25.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.5h | actual=2.5h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=6/6 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=0.0h | strategy=look_ahead | max_time=0.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=0.0h | actual=0.0h | delay=0.0h | completed=6/6 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=4/6 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=3 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=3/3 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=3 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=3/3 | iterations=3
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=2.7h | strategy=look_ahead | max_time=27.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.7h | actual=2.7h | delay=0.0h | completed=5/5 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.7h | strategy=look_ahead | max_time=27.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.7h | actual=2.7h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.7h | strategy=look_ahead | max_time=37.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.7h | actual=3.7h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.5h | strategy=look_ahead | max_time=34.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.4h | strategy=look_ahead | max_time=33.7h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.4h | actual=3.4h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=33.1h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.8h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.6h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=2.5h | strategy=look_ahead | max_time=25.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=2.5h | actual=2.5h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.5h | strategy=look_ahead | max_time=35.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.5h | actual=3.5h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.0h | strategy=look_ahead | max_time=30.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.0h | actual=3.0h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.1h | strategy=look_ahead | max_time=31.3h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.1h | actual=3.1h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=31.9h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.2h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.3h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.3h | actual=3.3h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=5 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=5/5 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=1.5h | strategy=look_ahead | max_time=15.0h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.5h | actual=1.5h | delay=0.0h | completed=6/6 | iterations=4
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=1.7h | strategy=look_ahead | max_time=17.4h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=1.7h | actual=1.7h | delay=0.0h | completed=6/6 | iterations=5
INFO     CPM.pert:pert.py:3296 Starting event-driven RCPSP | activities=6 | CPM=3.2h | strategy=look_ahead | max_time=32.5h
INFO     CPM.pert:pert.py:3463 Scheduling complete | CPM=3.2h | actual=3.2h | delay=0.0h | completed=4/6 | iterations=4
================================================================================ short test summary info ================================================================================
FAILED test_property_based.py::test_unconstrained_makespan_equals_cpm[first] - BaseExceptionGroup: Hypothesis found 2 distinct failures. (2 sub-exceptions)
FAILED test_property_based.py::test_unconstrained_makespan_equals_cpm[max_use_res_ranked] - BaseExceptionGroup: Hypothesis found 2 distinct failures. (2 sub-exceptions)
FAILED test_property_based.py::test_unconstrained_makespan_equals_cpm[max_use_res_shuffled] - BaseExceptionGroup: Hypothesis found 2 distinct failures. (2 sub-exceptions)
FAILED test_property_based.py::test_unconstrained_makespan_equals_cpm[md_knapsack] - BaseExceptionGroup: Hypothesis found 2 distinct failures. (2 sub-exceptions)
FAILED test_property_based.py::test_unconstrained_makespan_equals_cpm[look_ahead] - Failed: Scheduler output failed validation.

Schedule Validation Report
==================================================
Status     : INFEASIBLE
Violations : 1
Warnings   : 0

--- Violations ---
  [ERROR] [completeness ] schedule: 2 of 6 activities not scheduled (first 5: ['A3'])  [excess=2.00]
==================================================
Failing test case: test_unconstrained_makespan_equals_cpm(
    sgs='look_ahead',
    inst=(4, [0.0, 0.0, 1.7447551588106158, 1.504299842107538], [(2, 3)]),
)
Explanation:
    These lines were always and only run by failing test cases:
        /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/pert.py:3341
        /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/pert.py:5206
        /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/schedule_validator.py:215
        /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/schedule_validator.py:301
        /Users/mandd/projects/LOGOS/tests/unit_tests/CPM/../../../src/CPM/schedule_validator.py:306
============================================================================== 5 failed, 2 passed in 5.75s ==============================================================================
