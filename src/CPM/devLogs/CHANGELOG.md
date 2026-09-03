# CPM Module Changelog

## [Unreleased]

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

Result: plain `python -m pytest` → **839 passed, 5 skipped, 0 failed, 0 errors**.

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
