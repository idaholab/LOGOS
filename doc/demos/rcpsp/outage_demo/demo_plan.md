# Demo Notebook Plan: PERT Class Capabilities
## Target: `demos/npp_outage_demo.ipynb`

**Purpose:** A self-contained, narrative-driven Jupyter notebook that demonstrates the full
PERT class feature set using a realistic nuclear power plant (NPP) refueling outage scenario.
Audience is dual: (1) show-and-tell for outage coordinators / report readers (Section 2 of the
LWRS report), (2) quick-start reference for analytics staff (Section 9).

---

## 1. Test Case Scenario: "Unit 2 Spring Refueling Outage"

**Outage ID:** `UNIT2_RFO_SPRING`
**Start date:** `2025-04-07 06:00` (Monday morning, shift start)
**Target end:** `2025-04-24` (~17 days / ~408 hours)
**Shift calendar:** `working_hours_per_day: 24` (scheduler runs continuous clock).
  Two-shift structure (day/night) is encoded via **resource availability periods** with
  count changes at h=0, 12, 24, … — e.g. MECHANIC drops from 8 (day shift) to 5 (night
  shift) every 12 hours. This triggers shift-boundary events in the event queue automatically.

### Why this scenario?
A 17-day, ~35-activity outage sits squarely in the range where:
- Resource contention is real and visible (optimism gap ≥ 15%)
- The Gantt is readable (not too large to show in a report)
- Every constraint type can be exercised without manufacturing contrived situations

---

## 2. Activity Network (38 tasks + START/END)

### Precedence structure (simplified work-package view)

```
START
  └─ T01: Reactor shutdown & cooldown                    [24h, no resources, lag to T02]
       └─ T02: RCS pressure boundary confirmation        [4h, MECHANIC×2, QA hold point]
            ├─ T03: Reactor vessel head removal          [16h, MECHANIC×4, POLAR_CRANE×1, REACTOR_CAVITY]
            │    └─ T04: Upper internals removal         [12h, MECHANIC×3, POLAR_CRANE×1, REACTOR_CAVITY]
            │         └─ T05: Fuel offload               [48h, RCT×4, FUEL_MACH×1, REACTOR_CAVITY]
            │              └─ T06: In-core inspection    [24h, RCT×2, EC_RIG×1, REACTOR_CAVITY, dose-bearing]
            │                   └─ T07: Fuel reload      [36h, RCT×4, FUEL_MACH×1, REACTOR_CAVITY]
            │                        └─ T08: Upper internals reinstall  [12h, MECHANIC×3, POLAR_CRANE×1, REACTOR_CAVITY]
            │                             └─ T09: Vessel head reinstall [16h, MECHANIC×4, POLAR_CRANE×1, REACTOR_CAVITY]
            │                                  └─ T10: RCS fill & vent  [8h, MECHANIC×2]
            │                                       └─ T11: RCS leak check [6h, MECHANIC×2, QA hold point]
            │                                            └─ END
            ├─ T12: ECCS Train A isolation & maintenance [20h, MECHANIC×3, system_state: ECCS_A=OOS, SG_BAY]
            │    └─ T13: Train A valve testing           [8h, MECHANIC×2, system_state: ECCS_A=OOS, SG_BAY]
            │         └─ T14: Train A return to service  [4h, MECHANIC×2, system_state: ECCS_A=OPERATIONAL]
            │              └─ T15: Train A post-maint op test [6h, ELECTRICIAN×2, time_window: 0–240h]
            │                   └─ END (via T11)
            ├─ T16: ECCS Train B isolation & maintenance [20h, MECHANIC×3, system_state: ECCS_B=OOS, SG_BAY]
            │    (T16 blocked while T12 active – mutual exclusion enforced by system state locks)
            │    └─ T17: Train B valve testing           [8h, MECHANIC×2, system_state: ECCS_B=OOS, SG_BAY]
            │         └─ T18: Train B return to service  [4h, MECHANIC×2, system_state: ECCS_B=OPERATIONAL]
            │              └─ T19: Train B post-maint op test [6h, ELECTRICIAN×2, time_window: 0–240h]
            ├─ T20: SG-1 tube inspection (normal mode)   [24h, RCT×2, EC_RIG×1, SG_BAY, dose-bearing]
            │    └─ T21: SG-1 tube plugging              [12h, MECHANIC×3, SG_BAY]
            │         └─ END (via T11)
            ├─ T22: SG-2 tube inspection (multi-mode)    [24h/16h, RCT×2/3, EC_RIG×1/2, SG_BAY, dose-bearing]
            │    └─ T23: SG-2 tube plugging              [12h, MECHANIC×3, SG_BAY]
            ├─ T24: RCP-1 seal replacement               [16h, MECHANIC×4, CONTAINMENT, AC_SUIT×6/task]
            │    └─ T25: RCP-1 post-maint functional test [4h, ELECTRICIAN×2, CONTAINMENT]
            ├─ T26: RCP-2 seal replacement               [16h, MECHANIC×4, CONTAINMENT, AC_SUIT×6/task]
            │    └─ T27: RCP-2 post-maint functional test [4h, ELECTRICIAN×2, CONTAINMENT]
            ├─ T28: Containment isolation valve test      [8h, MECHANIC×2, ELECTRICIAN×1,
            │        time_window: 72–200h (Tech Spec surveillance)]
            ├─ T29: Pressurizer heater replacement       [20h, ELECTRICIAN×3, WELDER×2, CONTAINMENT]
            │    └─ T30: Pressurizer leak test            [6h, MECHANIC×2, NRC hold point]
            ├─ T31: Turbine inspection                   [24h, MECHANIC×4, TURBINE_HALL]
            │    └─ T32: Turbine bearing replacement     [12h, MECHANIC×3, WELDER×2, TURBINE_HALL]
            │         └─ T33: Turbine reassembly         [8h, MECHANIC×4, TURBINE_HALL]
            │              └─ END (via T11)
            ├─ T34: Electrical bus maintenance           [12h, ELECTRICIAN×4, ELEC_ROOM,
            │         mobilization_lead_hours: 12 (specialist crew mobilization)]
            │    └─ T35: Electrical bus testing          [4h, ELECTRICIAN×2, ELEC_ROOM,
            │              time_window: 0–300h]
            ├─ T36: SFP cooling train 1 maintenance      [12h, MECHANIC×2,
            │         system_state: SFP_COOLING=TRAIN_1_OOS]
            │    └─ T38: SFP cooling return to normal    [4h, MECHANIC×1,
            │              system_state: SFP_COOLING=NORMAL]  ← T38 also waits for T37
            └─ T37: SFP cooling train 2 maintenance      [12h, MECHANIC×2,
                      system_state: SFP_COOLING=TRAIN_2_OOS]
                 └─ T38 (same as above — T38 succeeds both T36 and T37)
```

T36 and T37 both succeed START and are not explicitly ordered relative to each other.
The SystemStatePool serializes them automatically because they require *different* states
(`TRAIN_1_OOS` vs `TRAIN_2_OOS`) on the same `SFP_COOLING` system. T38 requires state
`NORMAL`, which is released only when both T36 and T37 are complete.

**Key design choices:**
- Polar crane is the single most-contested equipment (used by T03, T04, T07, T08, T09 sequentially)
- EC_RIG (eddy-current rig) is shared between T06 (in-core), T20 (SG-1), T22 (SG-2)
- Mechanic pool contention is high: T12+T16 cannot run together (system state), T24+T26+T31 compete simultaneously
- REACTOR_CAVITY and SG_BAY have strict occupancy limits (confined space / radiological zone)
- T01 carries a `lag_hours: 24` to T02 (reactor cooldown)

---

## 3. Resource Configuration

### Skill pools

| Skill type       | h 0–96   | h 96–264 | h 264–408 | Notes                                  |
|------------------|----------|----------|-----------|----------------------------------------|
| MECHANIC         | 8        | 12       | 8         | Contractor reinforcement arrives day 4 |
| ELECTRICIAN      | 5        | 5        | 5         | Constant                               |
| WELDER           | 3        | 3        | 3         | Constant                               |
| RCT              | 6        | 6        | 6         | Constant (radiological control techs)  |

- MECHANIC is `resource_type: renewable`
- RCT is `resource_type: consumable` with `dose_budget_per_worker_mrem: 500` (ALARA limit)
  → triggers dose budget enforcement; after ~300 mRem accumulated, scheduler blocks further dose tasks

### Equipment pools

| Equipment ID   | Description            | qty h 0–48 | qty h 48–408 | Notes                    |
|----------------|------------------------|------------|--------------|--------------------------|
| POLAR_CRANE    | Reactor building crane | 1          | 1            | Shared by 5 tasks        |
| EC_RIG         | Eddy-current test rig  | 1          | 2            | Second rig arrives day 2 |
| FUEL_MACH      | Fuel handling machine  | 1          | 1            | Exclusive to fuel tasks  |

### Consumable pool

| Item ID   | Initial qty | Restock at h 96 | qty per task | Description           |
|-----------|-------------|-----------------|--------------|----------------------|
| AC_SUIT   | 30          | +20             | 6            | Anti-contamination suits (T24, T26) |

### Locations

| Location ID      | Description              | max_tasks | max_workers | Confined? | Notes                        |
|------------------|--------------------------|-----------|-------------|-----------|------------------------------|
| REACTOR_CAVITY   | Reactor cavity/pool      | 1         | 6           | Yes       | One task at a time; radiological |
| SG_BAY           | Steam generator bay      | 2         | 10          | No        | Two SGs can work concurrently |
| CONTAINMENT      | Containment building     | 3         | 15          | No        | Restricted period h 0–24     |
| TURBINE_HALL     | Turbine hall             | 2         | 12          | No        | —                            |
| ELEC_ROOM        | Electrical switchgear room| 1        | 4           | No        | One task at a time           |

CONTAINMENT: `max_concurrent_tasks=0` for h 0–24 (initial containment isolation period), then 3.

---

## 4. Advanced Constraints

### Hold points
| Task | Hold point type | Who approves | Notes                        |
|------|----------------|--------------|------------------------------|
| T02  | QA             | Quality Assurance | Before breaking pressure boundary |
| T30  | NRC            | Resident Inspector | Before pressurizer startup   |
| T11  | QA             | Quality Assurance | Final RCS leak check sign-off |

### System state locks — two separate mechanisms demonstrated

**Mechanism A — precedence constraint (ECCS Train A/B mutual exclusion)**
T12 (Train A OOS) and T16 (Train B OOS) cannot overlap because losing both trains
simultaneously eliminates the safety function. This is enforced via an explicit
`successors` link: T14 (Train A return to service) is a mandatory predecessor of T16
(Train B isolation), so the trains are serviced sequentially. System state locks on
ECCS_A and ECCS_B are still declared (valid_states: OOS / OPERATIONAL) so that
T14/T15 (requiring OPERATIONAL) are correctly blocked until T12/T13 complete — but
the A↔B serialization is a precedence edge, not a state conflict.

| System ID | Valid states           | Tasks holding OOS | Tasks holding OPERATIONAL | Runtime effect                              |
|-----------|------------------------|-------------------|---------------------------|---------------------------------------------|
| ECCS_A    | [OOS, OPERATIONAL]     | T12, T13          | T14, T15                  | T12+T13 share OOS lock; T14/T15 wait until ref_count=0 |
| ECCS_B    | [OOS, OPERATIONAL]     | T16, T17          | T18, T19                  | T16+T17 share OOS lock; T18/T19 wait until ref_count=0 |

**Mechanism B — conflicting states on one system (new system group)**
Add a **Spent Fuel Pool (SFP) cooling system** with two redundant cooling trains.
Declare one abstract system `SFP_COOLING` with `valid_states: ['TRAIN_1_OOS', 'TRAIN_2_OOS']`.
Because the SystemStatePool treats any two *different* states on the same system as
mutually exclusive, declaring:
- T36: SFP cooling train 1 maintenance (requires `SFP_COOLING` state = `TRAIN_1_OOS`)
- T37: SFP cooling train 2 maintenance (requires `SFP_COOLING` state = `TRAIN_2_OOS`)
automatically prevents T36 and T37 from running concurrently — no explicit precedence
edge needed. This is the clean demonstration of the state-conflict mechanism.

New activities added to the network:
| Task | Description                        | Duration | Resources       | System state required         |
|------|------------------------------------|----------|-----------------|-------------------------------|
| T36  | SFP cooling train 1 maintenance    | 12h      | MECHANIC×2      | SFP_COOLING = TRAIN_1_OOS     |
| T37  | SFP cooling train 2 maintenance    | 12h      | MECHANIC×2      | SFP_COOLING = TRAIN_2_OOS     |
| T38  | SFP cooling return to normal       | 4h       | MECHANIC×1      | SFP_COOLING = NORMAL          |

Both T36 and T37 succeed START; T38 succeeds both T36 and T37. The scheduler will
serialize T36 and T37 automatically due to state conflict, then allow T38 once both
complete and the NORMAL state is uncontested.

### Time windows (Technical Spec surveillance)
| Task | Window type    | Earliest (h) | Latest finish (h) | Notes                           |
|------|---------------|-------------|-------------------|---------------------------------|
| T15  | Single window | 0           | 240               | Train A op test before day 10   |
| T19  | Single window | 0           | 240               | Train B op test before day 10   |
| T28  | Single window | 72          | 200               | Containment isolation valve test |
| T35  | Single window | 0           | 300               | Electrical bus test before day 12.5 |
| T06  | Multi-window  | [48–168, 240–320] | —           | In-core inspection: 2 allowed windows |

### Finish-to-start lags
| Edge          | Lag (h) | Reason                                    |
|---------------|---------|-------------------------------------------|
| T01 → T02     | 24      | Reactor cooldown: RCS must cool 24h before work |
| T09 → T10     | 6       | Head seal cure time before fill/vent       |
| T30 → T11     | 4       | Pressurizer pressure stabilization        |

### Multi-mode tasks
| Task | Mode         | Duration (h) | MECHANIC | RCT | EC_RIG | When to use              |
|------|-------------|-------------|---------|-----|--------|--------------------------|
| T22  | normal      | 24          | —       | 2   | 1      | Default; ample schedule float |
| T22  | crash       | 16          | —       | 3   | 2      | Critical path; EC_RIG qty triggers constraint |

### Mobilization lead time
- T34: `mobilization_lead_hours: 12` (specialist electrical crew must be contracted in advance)

### Skill substitution
- T24, T26 (RCP seal): primary MECHANIC; alternative WELDER (if mechanic pool exhausted)
- This will be visible in the schedule when the demo exercises tight mechanic contention

### WBS grouping
- T12+T13 share `wbs_group: "ECCS_A_MAINT"` → when T12 goes critical, T13 is elevated too
- T20+T21 share `wbs_group: "SG1_WORK"`
- T22+T23 share `wbs_group: "SG2_WORK"`

---

## 5. Notebook Structure

### Section 0 — Imports and setup
```python
from src import Pert, plot_gantt_chart, plot_resource_utilization,
               plot_location_utilization, plot_equipment_utilization
import json, logging, pandas as pd
from IPython.display import display, HTML
```

### Section 1 — Load and inspect the schedule
- `Pert.from_json_file('npp_outage.json', 'outage_schema.json')`
- Print network summary: `pert.print_summary()`
- Network connectivity check: `pert.debug_connectivity_and_es()`
- Candidate capacity scan (first 48h): `pert.debug_candidates_and_capacity(hours_ahead=48)`

### Section 2 — CPM baseline (no resources)
- `pert.generateInfo()`
- Print CPM duration: `pert.getProjectDuration()`
- Show critical path: `pert.getCriticalPath()`
- **Key message:** CPM says X hours — resource-constrained reality will be longer

### Section 3 — Resource-constrained scheduling (Parallel SGS)
- Schedule with `sgs='max_use_res_ranked'`, `priority_rule='lf'` (default)
- Print results dict: duration, n_completed, delay_hours
- Print chain sets summary: `pert.print_chain_sets_summary()`
- **Demonstrate optimism gap:** CPM duration vs PERT makespan, makespan ratio
- Compute fitness: `pert.compute_fitness()` — show all 4 components

### Section 4 — Visualizations
- Gantt chart with augmented edges: `plot_gantt_chart(pert, show_delays=True)`
- DAG (plotly, highlight='both'): `pert.plot_activity_dag(...)`
- Resource utilization per skill type (loop over all skills)
- Location utilization (reactor cavity + SG bay + containment)
- Equipment utilization (polar crane + EC rig)
- **Key message:** Red = truly critical; orange = one disruption away; blue = has float

### Section 5 — Schedule DataFrame
- `df = pert.get_schedule_dataframe()`
- Show columns: activity_id, start_time, end_time, duration, delay, on_resource_constrained_chain, tf_actual_hours
- Highlight: activities with delay > 0 (resource-driven wait)
- Highlight: CPM-only vs constrained-chain-only activities

### Section 6 — Diagnosing idle time on the critical chain
- `pert.explain_idle_on_chain()` — compact view
- `pert.explain_idle_on_chain_detailed()` — per-hour breakdown
- **Show:** which resource is the bottleneck and during which hours

### Section 7 — Priority rule comparison
- Loop over 5-6 representative rules: `['lf', 'grpw', 'mts', 'rr', 'grd', 'mehh_8000_b']`
- Collect makespan for each
- Bar chart comparing results
- **Key message:** choice of priority rule affects makespan; GP optimizes this automatically

### Section 8 — SGS variant comparison
- Run all 5 variants: `first`, `max_use_res_ranked`, `max_use_res_shuffled`, `md_knapsack`, `look_ahead`
- Compare makespans in a DataFrame
- **Key message:** parallel SGS variants outperform first-feasible; knapsack helps under high contention

### Section 9 — Multi-mode RCPSP
- Run with T22 in 'normal' mode (default)
- Run with T22 in 'crash' mode via `pert.set_modes({'T22': 'crash'})`
- Compare makespans and fitness scores
- **Show:** how crash mode for one task can shorten the whole outage (if T22 is on critical chain)

### Section 10 — CCPM buffers
- `pert.insert_project_buffer(method='ssq')`
- `pert.insert_feeding_buffers(method='ssq')`
- `pert.get_buffer_status()` — show sizes
- **Key message:** structured schedule margin vs hidden padding in individual tasks

### Section 11 — Replanning (mid-outage disruption)
**Scenario:** At hour 72, three mechanics call in sick (pool drops from 12 to 9),
and an unexpected turbine vibration reading requires injecting a new 3-hour inspection
task (T_EMERG) that must precede T33 (turbine reassembly).

The confirmed `replan()` signature is:
```python
# Build emergent Activity object (Activity class, not a dict)
from src.CPM.activity import Activity
t_emerg = Activity('T_EMERG', duration=3.0)
t_emerg.description = 'Turbine vibration inspection (emergent)'
# required resources set separately via t_emerg.resources = [...]

result = pert.replan(
    current_time_hours=72.0,
    new_activities=[t_emerg],
    predecessor_wiring={'T_EMERG': ['T32']},   # T32 → T_EMERG → T33
    resource_updates=[
        {'skill_type': 'MECHANIC', 'from_hour': 72, 'new_count': 9},
    ],
    sgs='max_use_res_ranked',
)
```

Key parameters confirmed from code:
- `resource_updates`: list of `{'skill_type', 'from_hour', 'new_count', 'until_hour'(optional)}`
- `equipment_updates`: list of `{'equipment_id', 'from_hour', 'new_quantity', 'until_hour'(optional)}`
- `duration_overrides`: `{task_id: new_total_duration}` for in-progress tasks running over
- `predecessor_wiring`: `{new_task_id: [existing_pred_id, ...]}` for graph wiring

Demo steps:
- Clone first for what-if: `clone = pert.clone_for_analysis(); clone.replan(...)`
- Show before Gantt (from initial schedule)
- Run replan on the clone, show after Gantt
- Compare makespans: `result['scheduled_duration']` vs original
- `pert.check_dependency_violations()` — confirm feasibility of replanned schedule

### Section 12 — Validation
- `pert.validate_schedule()` — full constraint compliance check
- Show: any window violations? dose budget status? dependency violations?

### Section 13 — Export
- `pert.export_schedule_to_csv('npp_outage_schedule.csv')`
- Show final DataFrame

---

## 6. JSON Input File Plan: `demos/npp_outage.json`

The JSON will be purpose-built for this demo (not reused from test_case_*.json).
It will exercise **every** JSON schema field at least once:

| Schema field              | Used in                             |
|---------------------------|-------------------------------------|
| `working_hours_per_day`   | Outage header (12h shifts → 24 actual, but events at 12h) |
| `successors` (with lag)   | T01→T02 (lag=24h), T09→T10 (lag=6h), T30→T11 (lag=4h) |
| `is_hold_point`           | T02 (QA), T30 (NRC), T11 (QA)      |
| `hold_point_type`         | QA, NRC                             |
| `blocks_tasks`            | T02 blocks downstream until approved |
| `required_resources`      | All tasks (MECHANIC, ELECTRICIAN, RCT, WELDER) |
| `alternative_skill_types` | T24, T26 (MECHANIC → WELDER fallback) |
| `required_equipment`      | T03–T09 (POLAR_CRANE), T06/T20/T22 (EC_RIG), T05/T07 (FUEL_MACH) |
| `dose_rate_mrem_per_hour` | T06, T20, T22 (radiological tasks)  |
| `window_earliest_start_hours` / `window_latest_finish_hours` | T15, T19, T28, T35 |
| `time_windows` (multi-window) | T06                              |
| `required_system_states`  | T12–T19 (ECCS_A, ECCS_B); T36–T38 (SFP_COOLING conflicting states) |
| `required_consumables`    | T24, T26 (AC_SUIT)                  |
| `modes`                   | T22 (normal / crash)                |
| `wbs_group`               | T12+T13, T20+T21, T22+T23          |
| `mobilization_lead_hours` | T34                                 |
| `zone_ids` (multi-zone)   | T28 (occupies both CONTAINMENT + auxiliary zone) |
| `resources[].resource_type: consumable` + `dose_budget_per_worker_mrem` | RCT pool |
| `consumables[]` (top-level) | AC_SUIT with restock at h 96       |
| `plant_systems[]`         | ECCS_A, ECCS_B (ref_count-based OOS locking); SFP_COOLING (conflicting-state serialization) |
| `safety_functions[]`      | ECCS (metadata; maps TRAIN_A/B to ECCS_A/ECCS_B plant_system entries) |
| `locations[].availability_periods` with `max_concurrent_tasks=0` | CONTAINMENT h 0–24 |

---

## 7. Figures Generated by the Notebook (for the Report)

These outputs are the figures referenced as TODOs in the report sections:

| Figure ref (report)  | Generated by                                        | Section |
|---------------------|-----------------------------------------------------|---------|
| Figure 15 (Gantt)   | `plot_gantt_chart(pert, show_delays=True)`          | §2      |
| Figure 16 (Resource util) | `plot_resource_utilization(pert, 'MECHANIC')` | §2      |
| Figure 17 (DAG)     | `pert.plot_activity_dag(include_augmented_edges=True, highlight='both')` | §2 |
| Figure 8 (Replan Gantt before/after) | Section 11 replanning output    | §5      |
| Figure 9 (CCPM)     | Section 10 CCPM output                              | §5      |
| Figure 10 (CPM vs augmented) | Section 5/6 DAG with highlight='both'      | §5      |

---

## 8. Design Decisions (Resolved)

1. **Shift calendar:** `working_hours_per_day: 24`. Two-shift structure encoded via resource
   availability periods alternating every 12h (day crew vs night crew counts). This produces
   natural shift-boundary events in the scheduler's event queue without a reduced working-day
   constraint.

2. **ECCS mutual exclusion:** Dual approach to demonstrate both mechanisms:
   - ECCS Train A/B: enforced via **explicit precedence edge** (T14 precedes T16), plus
     individual `plant_systems` entries for ECCS_A and ECCS_B so that OPERATIONAL-state
     tasks (T14/T15, T18/T19) are correctly blocked by the system state pool.
   - SFP cooling trains (T36/T37/T38): enforced via **conflicting states on one system**
     (`SFP_COOLING` with valid_states `['TRAIN_1_OOS', 'TRAIN_2_OOS', 'NORMAL']`). The
     SystemStatePool automatically serializes T36 and T37 because they require different states.

3. **Multi-window time_windows field:** Confirmed from code. `time_windows` (array of
   `{earliest, latest}` objects) takes precedence over `window_earliest_start_hours` /
   `window_latest_finish_hours` when non-empty. Use `time_windows` for T06 (in-core
   inspection, two discrete windows); use the scalar fields for all other tasks.

4. **replan() API:** Confirmed from code. Exact signature:
   ```python
   replan(current_time_hours, new_activities=None, predecessor_wiring=None,
          resource_updates=None, equipment_updates=None,
          duration_overrides=None, sgs='max_use_res_ranked', max_time_hours=None)
   ```
   `new_activities` takes `Activity` objects (not dicts). `resource_updates` is a list of
   `{'skill_type', 'from_hour', 'new_count', 'until_hour'(optional)}`. `predecessor_wiring`
   wires new tasks into the graph as `{new_task_id: [existing_pred_id, ...]}`.

5. **Figure readability / activity count:** Deferred. Will not trim the activity network now;
   figure export parameters (width, height, font size) will be tuned after the notebook runs.
