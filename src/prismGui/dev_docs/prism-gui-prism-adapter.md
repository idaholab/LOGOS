# PRISM Execution Adapter — Contract & Mapping Spec (Phase 1)

The concrete contract for the one infrastructure component that builds a PRISM runtime, runs a
schedule, and translates the result into neutral DTOs. Companion to the architecture doc (Ports →
Execution port) and the model spec (§6 RunResult, §7 Execution port). This is the doc an
implementer follows to write `infrastructure/prism_adapter.py`.

**Placement.** This adapter is the **only** code in the system that imports the PRISM package
(`src/CPM/pert.py`, `src/CPM/outage_data.py`, `src/CPM/activity.py`, `src/CPM/schedule_validator.py`).
It implements the `ExecutionPort` (model spec §7). The domain and application layers never import
PRISM; they hand this adapter a serialized effective-plan snapshot + a RunConfig and receive a
`RunResult` of neutral DTOs.

All `file:line` references below were verified against the current tree (`pert.py` is 8375 lines).

---

## 0. What the adapter does, end to end

```
submit(RunRequest):
  1. build a FRESH Pert runtime from the effective-plan snapshot           (§2, §3)
  2. apply RunConfig to that runtime (modes, seed, sgs, rule, horizon)     (§3, §4)
  3. run the schedule                                                       (§4)
  4. read PRISM output back into neutral DTOs                               (§5)
  5. run the schedule audit + dependency check → Issues                    (§6, §7)
  6. compute disposition indicators                                        (§8)
  7. assemble RunResult (+ provenance from the request)                    → get_result()
  on ANY PRISM exception → RunResult(status=failed, EXECUTION_FAILURE)     (§9)
```

Phase 1 completes all of this synchronously inside `submit`, so `get_status` returns `completed`
immediately (architecture: execution port is job-shaped but synchronous in Phase 1).

---

## 1. Time model — the reconciliation that shapes everything

**PRISM is datetime-native.** Internally, activities carry `startTime` / `endTime` as Python
`datetime` objects ([activity.py:57-58](../../CPM/activity.py#L57)), anchored to the project start
`Pert.startTime` ([pert.py:88-133](../../CPM/pert.py#L88)); durations are floats in hours. Availability
periods are parsed from the schema's **ISO timestamps** by the loader.

This **supersedes an assumption in model spec §1/§7** ("the adapter receives already-normalized
hour-offsets and does no ISO conversion"). That is backwards. The correct design:

- **Adapter INPUT is the schema-shaped effective-plan dict, with ISO timestamps** — exactly what
  `OutageData.from_dict` ingests, and exactly what the canonicalization spec hashes. There is no
  separate "normalized hour-offset" artifact fed into PRISM.
- **The ISO → hour-offset conversion happens on the OUTPUT side**, inside this adapter, when reading
  PRISM's `datetime` results into the hour-offset DTOs (§5). It is quantized to the same 1 ms grid
  the canonicalization spec uses.
- The domain's typed-view hour-offsets (for display/edit) are produced by a **separate** load mapper
  from `_raw`; that mapper is a display concern and is *not* part of this adapter.

**Consequence — one artifact, three jobs.** The schema-shaped effective-plan dict is simultaneously
(a) the thing we hash for provenance, (b) the thing the ValidationPort validates, and (c) the thing
this adapter feeds to PRISM. No S-vs-N handoff, no dual serialization on the input side.

> Model-spec §7 now carries the corrected time-model wording (the old "adapter receives normalized
> hour-offsets / does no ISO conversion" claim is removed) and points here for the detail. Reconciled
> in the Tier-2 drift pass (2026-09-09).

---

## 2. Fresh-runtime construction (the invariant, made concrete)

PRISM mutates activities, pools, queues, and timing fields **in place** across a run
(§9 evidence below), so a runtime is never stored or reused. Every `submit` builds a new `Pert`.

**Do NOT use `Pert.from_json_file`** ([pert.py:191](../../CPM/pert.py#L191)) — it re-reads a file
from disk and re-runs `OutageDataValidator` (raising `ValueError` on failure). The GUI has already
validated through the ValidationPort and holds the plan in memory. Use the dict path:

```python
from CPM.pert import Pert
from CPM.outage_data import OutageData

outage = OutageData.from_dict(effective_plan_dict)     # outage_data.py:108
pert   = Pert(outage_data=outage, seed=run_config.seed) # pert.py:63
```

- `OutageData.from_dict(data)` ([outage_data.py:108](../../CPM/outage_data.py#L108)) builds every
  pool from the schema top-level keys and calls the `OutageData` constructor. It performs **no schema
  validation** — that is the ValidationPort's job, already done — so the adapter must treat a
  malformed dict as an execution failure (§9), not assume clean input.
- `Pert(outage_data=...)` ([pert.py:63](../../CPM/pert.py#L63)) auto-builds the activity graph via
  `_build_graph_from_outage_data()` ([pert.py:171](../../CPM/pert.py#L171)) — `Activity.from_json`
  per task, plus `forwardDict` / `lag_dict` / `task_to_activity`. Pools are copied off `outage_data`
  ([pert.py:88-133](../../CPM/pert.py#L88)): `crew_pool`, `equipment_pool`, `location_pool`,
  `consumable_pool`, `system_state_pool`, `startTime`, `working_hours_per_day`, `dose_trackers`.

**Pools built by `from_dict`** ([outage_data.py:139-143](../../CPM/outage_data.py#L139)):
`ResourcePool.from_json(data['resources'])`, `EquipmentPool.from_json(...)`,
`LocationPool.from_json(...)`, `ConsumablePool.from_json(data.get('consumables', []))`,
`SystemStatePool.from_json(data.get('plant_systems', []))`. The renewable/consumable discriminator
and the per-worker dose budget are read per skill by `ResourcePool.from_json`
([outage_data.py:1170-1172](../../CPM/outage_data.py#L1170)) into `ResourceAvailability`
([outage_data.py:609](../../CPM/outage_data.py#L609)) — confirming the model-spec §2 decision that
`resource_type` and `dose_budget_per_worker_mrem` are runtime-affecting and must be in the snapshot.

> **Phase-1 executor covers BOTH pool types.** A consumable/dose pool is runtime-affecting: the
> engine builds `DoseBudgetTracker`s ([outage_data.py:490](../../CPM/outage_data.py#L490)) and draws
> them down during scheduling. So a Phase-1 *run* against an outage plan already exercises consumable
> pools even though Phase-2 *editing* of them is deferred. The adapter needs no special casing — it
> passes the whole schema dict to `from_dict` — but the doc set should not imply Phase-1 execution is
> renewable-only. (Phase 2's editing scope is what's renewable-first, per the architecture.)

---

## 3. Applying RunConfig to the fresh runtime

Order matters: modes change durations/resources, which changes CPM, before scheduling.

| RunConfig field | Applied via | Notes |
|---|---|---|
| `seed` | `Pert(..., seed=...)` ctor ([pert.py:63](../../CPM/pert.py#L63)); or `reseed(v)` ([pert.py:353](../../CPM/pert.py#L353)) | Seed at construction. |
| `mode_selections?` {task_id: mode_name} | `pert.set_modes(mode_assignments)` ([pert.py:638](../../CPM/pert.py#L638)) | Calls `Activity.set_mode` per task, then re-runs CPM. Skip if empty. |
| `sgs` | arg to the run method (§4) | One of the 5 strings in §4. |
| `priority_rule` | arg to the run method (§4) | One of the 22 keys in §4. |
| `scheduling_horizon_hours` | `max_time_hours=` arg (§4) | Domain field renamed from `max_time_hours` for clarity; it maps to PRISM's run-method `max_time_hours=` param (unchanged). `None` → engine default (parallel: `cpm × 10`; serial: `cpm × 3`). |
| `evaluation_weights?` {alpha,beta,gamma,delta} | `compute_fitness(alpha,beta,gamma,delta)` (§5) | Post-hoc only; does **not** change the search objective. |

`priorities=` (external priority vector) stays `None` in Phase 1 — the GUI drives scheduling by the
named `priority_rule`, not an injected vector.

---

## 4. Running the schedule

**Parallel SGS (primary)** — `calculateScheduleWithResources`
([pert.py:3253](../../CPM/pert.py#L3253)):
```python
def calculateScheduleWithResources(self, sgs='max_use_res_ranked',
                                   max_time_hours=None, priority_rule='') -> dict
```
**Serial SGS** — `calculateSerialScheduleWithResources`
([pert.py:6876](../../CPM/pert.py#L6876)):
```python
def calculateSerialScheduleWithResources(self, priority_rule='lf',
                                         max_time_hours=None, _ordered=None) -> dict
```
Phase 1 uses the parallel entry point; the serial one is available if a future RunConfig flag selects
it. (Replan variants `calculateScheduleWithResources_from` [pert.py:2505](../../CPM/pert.py#L2505)
and `replan` [pert.py:2692](../../CPM/pert.py#L2692) are Phase-5.)

**SGS variant strings** (dispatched in `_schedule_generation_scheme`
[pert.py:4452](../../CPM/pert.py#L4452)); an unknown value raises `ValueError`
[pert.py:4653](../../CPM/pert.py#L4653)):
`first` · `max_use_res_ranked` (default) · `max_use_res_shuffled` · `md_knapsack` · `look_ahead`.
These match model-spec §4 exactly. `md_knapsack` / `look_ahead` delegate to `MDKnapsackScheduler`
([pert.py:8068](../../CPM/pert.py#L8068)) / `LookAheadScheduler`
([pert.py:8228](../../CPM/pert.py#L8228)) internally — no adapter involvement.

**Priority-rule keys** — the canonical list is `Pert._list_priority_names`
([pert.py:70-76](../../CPM/pert.py#L70)), **exactly 22**:
`lf, ls, ef, es, duration, random, mts, mtp, grpw, grd, rr, avgrr, maxrr, minrr,`
`mehh_8000_b, mehh_3375_b, mehh_1000_b, mehh_125_b, gphh_b, wcs, acs, irsm`.
`validate_run_config` (model-spec §5) checks the selected key against this list; the UI's
plain-language descriptions (sort direction + per-rule caveats, verified against the engine) live in
**`prism-gui-priority-rules.md`**. The dispatch is `priority_calculation`
([pert.py:6304](../../CPM/pert.py#L6304)); an unrecognized key raises `IOError` at
[pert.py:6415](../../CPM/pert.py#L6415) — no silent default.

**Return dict** (both methods; cached to `self._last_schedule_result`
[pert.py:3489](../../CPM/pert.py#L3489)): `scheduled_duration`, `cpm_duration`, `delay_hours`,
`n_activities`, `n_completed`, `iterations` (parallel) / `priority_rule` (serial), and
`window_violations` (parallel [pert.py:3471-3479](../../CPM/pert.py#L3471)).

---

## 5. Output mapping — PRISM → neutral DTOs

All time conversions use `hour = quantize_1ms((t - pert.startTime).total_seconds() / 3600)`, the
same 1 ms grid as the canonicalization spec (so DTO hours and hashed times share one quantizer).

### ScheduleDTO (model-spec §6)
| DTO field | Source |
|---|---|
| `makespan_hours` | `result['scheduled_duration']` |
| `cpm_lower_bound_hours` | `result['cpm_duration']` (= `getProjectDuration()` [pert.py:1537](../../CPM/pert.py#L1537)) |
| `optimism_gap_hours` | derived: `makespan − cpm` |
| `activities` | one ScheduledActivityDTO per node (below) |
| `constrained_chain` | `[a.name for a in pert.constrained_chain_list]` ([pert.py:6140](../../CPM/pert.py#L6140), ordered) |
| `cpm_critical_path?` | `[a.name for a in pert.getCriticalPath()]` ([pert.py:1405](../../CPM/pert.py#L1405)) |

`constrained_chain_list` / `constrained_chain_set` are **attributes**, not a getter; they are
populated by `_compute_resource_constrained_chain()` ([pert.py:6131](../../CPM/pert.py#L6131)),
auto-invoked at the end of each run ([pert.py:3465](../../CPM/pert.py#L3465)).

### ScheduledActivityDTO (per `Activity`; see field origins in §10)
| DTO field | Source |
|---|---|
| `task_id` | `activity.name` |
| `description?` | `activity.description` |
| `start_hour`, `end_hour` | from `activity.returnAbsTimes()` ([activity.py:700](../../CPM/activity.py#L700)) → datetime→hour |
| `duration` | `activity.duration` (mode-effective, after `set_modes`) |
| `delay_hours` | `activity.delay` ([activity.py:59](../../CPM/activity.py#L59)) |
| `tf_actual_hours?` | `pert.actual_tf.get(activity)` ([pert.py:5272](../../CPM/pert.py#L5272)); may be negative (expected artifact) |
| `on_constrained_chain` | `activity in pert.constrained_chain_set` |
| `float_class` | derived (below) |
| `actual_resources?` | `activity._actual_resources` ([activity.py:176](../../CPM/activity.py#L176)) — `{skill: workers}` post-substitution |
| `wbs_group?` | `activity.wbs_group` |

`float_class` derivation (red/orange/blue in the UI):
- `critical` — `activity.belongsToCP` is `True` (on the resource-constrained critical path).
- `zero_float` — not on CP but `|tf_actual| ≤ 1 ms-equivalent` (≈ 0 float).
- `positive_float` — `tf_actual > 0`.

`get_schedule_dataframe()` ([pert.py:5667](../../CPM/pert.py#L5667)) already assembles most of these
columns (`activity_id, description, start_time, end_time, duration, delay,
on_resource_constrained_chain, tf_actual_hours`). The adapter **may** use it internally, but must emit
plain DTO records — no DataFrame enters RunResult (architecture: neutral DTOs only).

### DiagnosticsDTO (Phase-1 minimal)
| DTO field | Source |
|---|---|
| `fitness?` | `compute_fitness(alpha,beta,gamma,delta)` ([pert.py:4745](../../CPM/pert.py#L4745)) → `{composite, makespan_ratio, delay_ratio, criticality_ratio, window_violation_ratio, n_window_violations}` — a **1:1** match with the model-spec fitness DTO. Raises `RuntimeError` if called before a run; the adapter always calls it post-run. |
| `dependency_violations?` | from `check_dependency_violations()` → Issues (§7) |

Defaults `alpha=1.0, beta=0.5, gamma=0.3, delta=2.0` ([pert.py:4745](../../CPM/pert.py#L4745));
composite = `α·makespan_ratio + β·delay_ratio + γ·criticality_ratio + δ·window_violation_ratio`
([pert.py:4844-4849](../../CPM/pert.py#L4844)).

---

## 6. Schedule audit → Issues

`validate_schedule()` ([pert.py:5782](../../CPM/pert.py#L5782), delegates to
`schedule_validator.validate_schedule(pert)` [schedule_validator.py:1552](../../CPM/schedule_validator.py#L1552))
returns a `ValidationResult` ([schedule_validator.py:125](../../CPM/schedule_validator.py#L125)):
`is_feasible: bool`, `violations: [Violation]`, `warnings: [Violation]`, `.summary()`.
Each `Violation` ([schedule_validator.py:95](../../CPM/schedule_validator.py#L95)) carries
`type, activity, detail, severity ('error'|'warning'), excess`.

**Mapping rule.** For every entry in `violations` **and** `warnings`:
- `Issue.severity = Violation.severity` — **passed through**, not hard-coded (this is why `time_window`
  and `dose` can be either error or warning depending on where they fire).
- `Issue.entity_id = Violation.activity`; `Issue.entity_type = "task"`.
- `Issue.message = Violation.detail`.
- `Issue.code` and `Issue.category` from the table below.

The 16 `Violation.type` values map by a stable convention: reuse an existing catalogue code where the
meaning is identical to another producer, otherwise `code = "AUDIT_" + type.upper()`. Deriving the
code mechanically from the validator's own `type` string keeps one source of truth and avoids
hand-maintaining 16 names.

| `Violation.type` | `Issue.code` | `Issue.category` |
|---|---|---|
| `completeness` | `UNSCHEDULED_TASK` *(reused)* | `feasibility` |
| `precedence` | `DEP_VIOLATION` *(reused)* | `feasibility` |
| `mode` | `INVALID_MODE` *(reused)* | `execution` |
| `duration` | `AUDIT_DURATION` | `execution` |
| `time_window` | `AUDIT_TIME_WINDOW` | `time_window` |
| `hold_point` | `AUDIT_HOLD_POINT` | `feasibility` |
| `crew` | `AUDIT_CREW` | `feasibility` |
| `substitution` | `AUDIT_SUBSTITUTION` | `feasibility` |
| `equipment` | `AUDIT_EQUIPMENT` | `feasibility` |
| `location` | `AUDIT_LOCATION` | `feasibility` |
| `consumable` | `AUDIT_CONSUMABLE` | `feasibility` |
| `equipment_zone` | `AUDIT_EQUIPMENT_ZONE` | `feasibility` |
| `shift_calendar` | `AUDIT_SHIFT_CALENDAR` | `feasibility` |
| `dose` | `AUDIT_DOSE` | `dose` |
| `system_state` | `AUDIT_SYSTEM_STATE` | `system_state` |
| `quality` | `AUDIT_QUALITY` | `feasibility` |

> **Catalogue note.** `completeness`/`precedence`/`mode` reuse codes already in model-spec §1. The
> `AUDIT_*` codes are generated by the reuse-or-prefix rule (reuse an existing code where the meaning
> is identical, else `code = "AUDIT_" + type.upper()`), now documented in the model-spec §1 catalogue
> under "Schedule-audit codes (`AUDIT_*`)" — reconciled in the Tier-2 pass (2026-09-09). Phase-1
> disposition needs only "any error-severity audit Issue → `audit_passed = false`"; the fine-grained
> codes exist for UI filtering and the later LLM-explanation layer.

`ValidationResult.warnings` map to `warning`-severity Issues the same way; they feed disposition
(`ready_with_warnings`) but never block.

---

## 7. Dependency-violation check → Issues

`check_dependency_violations()` ([pert.py:5710](../../CPM/pert.py#L5710)) returns a 2-tuple
`(violations: list[dict], is_feasible: bool)`; raises `ValueError` if no schedule was computed (the
adapter only calls it post-run). Each violation dict has
`predecessor, successor, pred_end_time, succ_start_time, overlap_hours, lag_hours`.

Map each dict → `Issue(code=DEP_VIOLATION, category=feasibility, severity=error,
entity_type="dependency", entity_id=successor, message=<formatted from the dict>)`. This unifies with
the audit's `precedence` producer on the same `DEP_VIOLATION` code. `is_feasible` folds into the
`hard_feasible` indicator (§8), not surfaced as its own Issue.

---

## 8. Disposition indicator sourcing

`compute_disposition` is the pure domain function (model-spec §6); the adapter's job is to source the
six tri-state indicators from PRISM reads and hand them over:

| Indicator | Source (`true` / `false` / `not_evaluated`) |
|---|---|
| `input_valid` | from the ValidationPort pre-run (not this adapter); `not_evaluated` if the run failed before validation |
| `schedule_complete` | `result['n_completed'] == result['n_activities']` |
| `hard_feasible` | `validate_schedule().is_feasible` **and** `check_dependency_violations()[1]` |
| `has_unscheduled_tasks` | `len(pert.wait) > 0` ([pert.py:143](../../CPM/pert.py#L143)) — equivalently `n_completed < n_activities` |
| `has_window_violations` | `result['window_violations'] > 0` (parallel) |
| `audit_passed` | `validate_schedule().is_feasible` (no error-severity violations) |

Call `validate_schedule()` **once** and reuse the `ValidationResult` for both `hard_feasible` and
`audit_passed` (and §6's Issues) — it is not cheap.

---

## 9. Failure handling — no PRISM exception escapes

The adapter is a hard boundary. Any exception from `OutageData.from_dict`, `Pert(...)`, `set_modes`,
the run method (including the `ValueError` on an unknown SGS), `compute_fitness`,
`validate_schedule`, or `check_dependency_violations` is caught and converted to:

```
RunResult(status="failed",
          issues=[Issue(code=EXECUTION_FAILURE, severity=error, category=execution,
                        message=<sanitized exception text>)],
          provenance=<from the request>, schedule=None, diagnostics=None, disposition=None)
```

- Never let a raw PRISM traceback reach the UI (architecture: GUI-facing code never parses PRISM
  strings). Log the full traceback; surface a neutral message.
- Because every run builds a fresh `Pert`, a failed run cannot corrupt a later one — there is no
  shared mutable runtime to clean up.
- The `RunResult` is still assembled with full provenance, so a failure is reproducible.

---

## 10. In-place mutation — why fresh-per-run is non-negotiable (evidence)

Running a schedule overwrites state on the live objects; PRISM does not return a new object:

- Each run calls `_reset_scheduling_state()` at the top
  ([pert.py:3305](../../CPM/pert.py#L3305)→[pert.py:1927](../../CPM/pert.py#L1927)), overwriting
  `actual_tf`, `actual_zero_tf_set`, `constrained_chain_list/set`
  ([pert.py:1990-1993](../../CPM/pert.py#L1990)) and resetting every activity via `Activity.reset()`
  ([activity.py:729](../../CPM/activity.py#L729)).
- `_update_activity_sets()` ([pert.py:3493](../../CPM/pert.py#L3493)) mutates activities in place at
  start: `setActualStartTime` overwrites `startTime`/`endTime`
  ([pert.py:3519](../../CPM/pert.py#L3519)), `delay` ([:3523](../../CPM/pert.py#L3523)),
  `status='in_progress'` ([:3527](../../CPM/pert.py#L3527)), `_actual_resources`
  ([:3535](../../CPM/pert.py#L3535)).
- Pools accumulate on shared instances: `DoseBudgetTracker.consume()`
  ([outage_data.py:552](../../CPM/outage_data.py#L552)), `SystemStatePool.acquire/release`
  ([outage_data.py:1981](../../CPM/outage_data.py#L1981)/[:2001](../../CPM/outage_data.py#L2001)),
  `ConsumablePool.consume`.

Building a fresh `Pert(outage_data=OutageData.from_dict(...))` per run is the clean-state guarantee —
tested via the A→B→A invariant (architecture: "two runs do not influence each other").

---

## 11. API reference (every PRISM symbol the adapter touches)

| Symbol | `file:line` | Use |
|---|---|---|
| `OutageData.from_dict(data)` | [outage_data.py:108](../../CPM/outage_data.py#L108) | build model from schema dict (no validation) |
| `Pert(outage_data=, seed=)` | [pert.py:63](../../CPM/pert.py#L63) | construct fresh runtime |
| `Pert.set_modes(dict)` | [pert.py:638](../../CPM/pert.py#L638) | apply RunConfig mode selections |
| `Pert.reseed(v)` | [pert.py:353](../../CPM/pert.py#L353) | re-seed if not set at ctor |
| `calculateScheduleWithResources(sgs, max_time_hours, priority_rule)` | [pert.py:3253](../../CPM/pert.py#L3253) | run (parallel) |
| `calculateSerialScheduleWithResources(priority_rule, max_time_hours)` | [pert.py:6876](../../CPM/pert.py#L6876) | run (serial) |
| `Pert._list_priority_names` | [pert.py:70](../../CPM/pert.py#L70) | the 22 rule keys |
| `getProjectDuration()` | [pert.py:1537](../../CPM/pert.py#L1537) | CPM lower bound (hours) |
| `getCriticalPath(return_all=False)` | [pert.py:1405](../../CPM/pert.py#L1405) | CPM critical path (List[Activity]) |
| `constrained_chain_list` / `constrained_chain_set` | [pert.py:6140](../../CPM/pert.py#L6140) | resource-constrained chain |
| `actual_tf` / `actual_zero_tf_set` | [pert.py:5272](../../CPM/pert.py#L5272) | actual float proxy |
| `compute_fitness(alpha,beta,gamma,delta)` | [pert.py:4745](../../CPM/pert.py#L4745) | fitness + components |
| `validate_schedule()` | [pert.py:5782](../../CPM/pert.py#L5782) | full audit → ValidationResult |
| `check_dependency_violations()` | [pert.py:5710](../../CPM/pert.py#L5710) | precedence check → (list, bool) |
| `get_schedule_dataframe()` | [pert.py:5667](../../CPM/pert.py#L5667) | (internal only) convenience table |
| `get_project_finish_actual()` | [pert.py:4721](../../CPM/pert.py#L4721) | absolute finish datetime |
| `self.completed` / `self.wait` | [pert.py:143-146](../../CPM/pert.py#L143) | scheduled / unscheduled sets |
| `Activity.returnAbsTimes()` | [activity.py:700](../../CPM/activity.py#L700) | (startTime, endTime) |
| `Activity` output fields `startTime, endTime, delay, belongsToCP, status, _actual_resources, selected_mode_id` | [activity.py:57-194](../../CPM/activity.py#L57) | per-task DTO sources |
| `ResourceAvailability(resource_type, dose_budget_per_worker_mrem)` | [outage_data.py:609](../../CPM/outage_data.py#L609) | confirms runtime-affecting pool fields |
| `SystemStatePool.fits/acquire/release` | [outage_data.py:1950](../../CPM/outage_data.py#L1950) | Option-A mutual exclusion mechanism |

---

## 12. Deferred / out of scope for the Phase-1 adapter

- **Replan** (`replan`, `calculateScheduleWithResources_from`) — Phase 5.
- **`plot_activity_dag()`** ([pert.py:7141](../../CPM/pert.py#L7141)) — the DAG is a Phase-3 UI
  concern; the adapter exposes the edge list / node attributes, the UI renders. Not Phase 1.
- **`print_chain_sets_summary()`** ([pert.py:5480](../../CPM/pert.py#L5480)) logs at DEBUG; the
  chain-sets **comparison DTO** is Phase 4. Phase 1 exposes only `constrained_chain` +
  `cpm_critical_path` on ScheduleDTO.
- **External priority vector** (`priorities=`) — not used in Phase 1.
- **Serial SGS** — wired but not surfaced until a RunConfig flag calls for it.
