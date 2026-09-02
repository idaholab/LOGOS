# CPM Module — Correctness Review Strategy

Goal: verify that the scheduler computes **the right answer** across all
analysis situations, not just that it runs without crashing.  This document
records the strategy, tracks progress, and serves as a living checklist to
refine as new edge cases are discovered.

---

## Pass 1 — Broad coverage, low effort

### 1a. Validator consistency across the full test suite

Run `validate_schedule()` on the output of every test that produces a
schedule and assert no violations.  If the scheduler output fails its own
validator that is a correctness bug, not a style issue.

**Status:** DONE (2026-04-16) — 747 tests pass, 0 violations

**Approach:** added shared helper `assert_valid_schedule(pert)` in
`conftest.py`; wired across all scheduling test files.  Two real correctness
bugs were uncovered and fixed in the process:

1. **Consumable concurrent-scheduling bug** — consumables were deducted only
   in `_update_activity_sets`, so two candidates in the same time step could
   both pass the feasibility check against the un-reduced pool.  Fixed by
   moving deduction to `_apply_tentative` (mirrors the system-state pattern).

2. **Validator equipment-zone false positive** — activities with no zone
   constraint (empty `act_zones`) were flagged as violating zone affinity.
   Fixed by skipping the zone check when `act_zones` is empty.

3. **Validator duration false positive on replanned activities** —
   `endTime` is stale for activities frozen mid-execution by `replan()`.
   Fixed by skipping activities where `_remaining_duration is not None`.

Files to touch:
- `unit_tests/test_scheduling.py`
- `unit_tests/test_shift_calendar.py`
- `unit_tests/test_time_windows.py`
- `unit_tests/test_zone_ids.py`
- `unit_tests/test_system_state_pool.py`
- `unit_tests/test_substitution.py`
- `unit_tests/test_multimode.py`
- `unit_tests/test_wbs_priority.py`
- `unit_tests/test_consumables.py` (if it schedules)
- `unit_tests/test_safety_function.py` (if it schedules)

### 1b. Determinism invariant — `reset()` + reschedule produces identical result

A second call to `calculateScheduleWithResources` on the same `Pert`
instance must produce bitwise-identical start/end times for every activity.
Any state that bleeds across runs is a latent bug.

**Status:** DONE (2026-04-16) — 13 new tests in `test_determinism.py`, all pass

**Fixtures covered:** serial chain, fork-join, FS lag, time window, consumable
pool, `example_10.json`, `test_case_1.json`, 6 priority rules on example_10.

---

## Pass 2 — Known-answer tests per constraint (targeted)

For each feature, at least one test where the **exact** expected output is
computed analytically and asserted precisely (not just "it scheduled").

Priority order — highest risk of silent wrong values first:

| # | Feature | Risk | Status |
|---|---|---|---|
| 1 | Lag + time window interaction | Lag pushes successor past latest finish | DONE (2026-04-16) |
| 2 | Replan correctness | Remaining duration anchoring; window baseline isolation | DONE (2026-04-16) |
| 3 | Critical chain after mode switch | `_effective_duration` vs full duration | skip — pending audit |
| 4 | Consumable restock cursor | Deduct-on-start timing with mid-outage restock | DONE (2026-04-16) |
| 5 | Multi-mode CPM | ES/LF/slack after `set_modes()` | DONE (2026-04-16) |
| 6 | Shift calendar + lag | Lag end in off-shift; successor waits for next shift open | DONE (2026-04-16) |
| 7 | System state + equipment zone | Same activity holds state lock + zone-locked equipment | skip — needs multi-zone fixture |
| 8 | Hold-point sequencing | Blocked tasks cannot start before hold point completes | skip |

Two correctness bugs found and fixed by Pass 2 tests:

- **Validator shift-calendar false positive** (`schedule_validator.py`): `_check_shift_calendar`
  computed hour-of-day from project-start offset; should use absolute clock time. Activities
  starting exactly at the shift-open hour were flagged as violations when the outage started
  mid-day.

- **Scheduler missed restock wake-up** (`pert.py`, `_build_event_queue` +
  `_build_event_queue_from`): consumable restock delivery times were not seeded in the event
  heap. Activities blocked on an empty consumable pool started at the next unrelated event
  instead of immediately when inventory arrived.

---

## Pass 3 — Invariant properties

Mathematical properties that must hold for **any** valid schedule.
Checked as parameterised tests over generated or representative inputs.

| Invariant | Description | Status |
|---|---|---|
| `makespan >= CPM_duration` | Scheduler never beats physics | DONE (2026-04-16) — 6 fixtures |
| Unconstrained → `makespan == CPM_duration` | No spurious waits when pools are infinite | DONE (2026-04-16) — 3 fixtures (window excluded: time constraints legitimately stretch makespan) |
| Tighter resource → `makespan_tight >= makespan_loose` | Monotonicity | DONE (2026-04-16) — exact + parametric + JSON fixture |
| `replan(t=0)` ≡ full schedule | Replan at hour 0 equals a fresh run | DONE (2026-04-16) — 5 fixtures incl. resource-constrained fork |

---

## Pass 4 — Constraint interaction tests

Combinations most likely to produce wrong answers silently.

**Status:** DONE (2026-04-16) — 18 tests in `test_interactions.py`, all pass

| Combination | What can go wrong | Status |
|---|---|---|
| Lag + time window | Lag pushes successor past latest finish window | DONE (2026-04-16) |
| Mode switch + consumables | Crash mode quantity not updated before feasibility check | DONE (2026-04-16) |
| Replan + window violations | Pre-replan violations contaminate current-run fitness | DONE (2026-04-16) |
| System state + equipment zone | Interplay between state lock and zone affinity check | DONE (2026-04-16) |
| Shift calendar + lag | Lag end lands in off-shift; next open slot computed correctly | DONE (2026-04-16) |

No new bugs found — all constraint interactions produce correct outputs.

---

## Pass 5 — Boundary / stress cases

**Status:** DONE (2026-04-16) — 24 tests in `test_boundary.py`, all pass

| Case | Expected behaviour | Status |
|---|---|---|
| Empty task list | Schedules instantly; makespan = 0 | DONE (2026-04-16) |
| Single task, no predecessors | Starts at h=0; ends at h=duration | DONE (2026-04-16) |
| Fully serial chain (all slack = 0) | Makespan = sum of durations | DONE (2026-04-16) |
| Fully parallel, no precedence | Makespan = max(duration) when resources allow | DONE (2026-04-16) |
| Tight resource — complete serialization | Makespan = sum of durations for bottleneck skill | DONE (2026-04-16) |
| Near-deadlock (circular resource dependency) | Deadlock detected; warning logged; partial schedule | DONE (2026-04-16) |

No new bugs found — all boundary conditions produce correct outputs.

---

## Notes / open questions

- Should `assert_valid_schedule` be a module-level helper in a shared
  `conftest.py` or a standalone utility imported by each test file?
- PSPLIB benchmark instances are available in `unit_tests/` (from earlier
  work); consider adding a known-makespan comparison for j30 instances once
  Pass 1–2 are complete.
- Randomised property tests (Pass 3) may benefit from `hypothesis` if it is
  available in the project environment; otherwise use a small fixed set of
  representative fixtures.
