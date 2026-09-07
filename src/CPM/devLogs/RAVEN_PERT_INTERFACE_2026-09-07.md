# RAVEN ↔ `Pert` Interface — Status & Notes

**Date:** 2026-09-07
**Author:** Claude Code (session with D. Mandelli)
**Scope:** `src/CPM/BaseCPMmodel.py` (the RAVEN `ExternalModel` plugin) and its
interaction with `src/CPM/pert.py`.
**Branch:** `mandd/res_opt`

---

## TL;DR

The intended purpose of the RAVEN interface is to **propagate uncertainty in
activity durations** through the schedule (RAVEN samples durations → `Pert`
computes the resource-constrained critical-path duration → RAVEN collects the
makespan distribution). Priority sampling is a secondary supported mode.

**As of this branch, that interface is broken:** `BaseCPMmodel.run()` crashes
before it ever reaches the scheduler for the normal duration-only case. The
break is mechanical (variable unpacking), not a design problem, and the fix is
small. It has gone unnoticed because **no unit test exercises `run()`** — the
suite only tests `Pert.set_durations()` directly.

This note records (1) exactly what is broken and why, (2) a proposed fix, and
(3) additional analyses RAVEN could drive through `Pert` once the interface
works. Optimization use cases are intentionally **out of scope for now** (see
end).

---

## 1. Current state of `run()`

`BaseCPMmodel._readMoreXML()` parses each `<map>` node as
`<map act='ACT_ID' attr='duration'|'priority'>RAVEN_VAR</map>` and records:

- `self.mapping[RAVEN_VAR] = (ACT_ID, attr)`  — the RAVEN-var → activity-ID table
- `self.duration_vars` / `self.priority_vars`  — lists of RAVEN variable names

`run()` then tries to apply the sampled values
([`BaseCPMmodel.py:157-175`](../BaseCPMmodel.py#L157-L175)):

```python
inputDict_durations = dict(
    zip(self.duration_vars, itemgetter(*self.duration_vars)(inputDict))
)
self.pert.set_durations(inputDict_durations)          # in try/except KeyError
...
inputDict_priorities = dict(
    zip(self.priority_vars, itemgetter(*self.priority_vars)(inputDict))
)
self.pert.set_priorities(inputDict_priorities, 'replace')   # in try/except KeyError
```

There are **four** independent problems here.

### Bug 1 — `itemgetter(*[])` raises an uncaught `TypeError` (fatal, primary case)

When a model maps only durations (the main uncertainty-propagation use), there
are no priority maps, so `self.priority_vars == []` and
`itemgetter(*self.priority_vars)` becomes `itemgetter()`, which raises
`TypeError: itemgetter expected 1 argument, got 0`. The surrounding guard only
catches `KeyError`, so the `TypeError` propagates and `run()` dies. The
symmetric case (priority-only model) crashes on the durations block.

Empirical reproduction of the exact `run()` expressions
(`inputDict` values as RAVEN 1-element ndarrays):

| duration_vars | priority_vars | durations block | priorities block |
|---|---|---|---|
| `[C101, C102]` | `[]` | ok | **UNCAUGHT TypeError** (crash) |
| `[C101]` | `[]` | ok | **UNCAUGHT TypeError** (crash) |
| `[]` | `[C103]` | **UNCAUGHT TypeError** (crash) | ok |
| `[C101, C102]` | `[C103]` | ok | ok |

Only the fully-mixed case (at least one duration map **and** at least one
priority map) gets past this bug.

### Bug 2 — wrong dictionary keys (`self.mapping` is never used)

`run()` builds `{RAVEN_VAR: value}` and passes it to `set_durations`, which
matches by **activity ID** — [`pert.py:619`](../pert.py#L619) raises
`KeyError` if `task_id not in self.task_to_activity`. The
`RAVEN_VAR → ACT_ID` translation stored in `self.mapping` is populated at
[`BaseCPMmodel.py:96`](../BaseCPMmodel.py#L96) and **never read again**. So
unless every RAVEN variable is named identically to its activity ID, this
raises `KeyError` (→ re-raised as `IOError "... variable not found"`), and the
`<map>` node's whole purpose (decoupling RAVEN names from activity IDs) is
defeated. Note `tests/test_BaseCPMmodel_map.xml` uses distinct names
(`R_C101` for act `C101`), i.e. the case that fails.

> Open question for the main developer: is the intended convention
> "RAVEN variable name == activity ID" (making `self.mapping` dead code), or
> should `run()` translate through `self.mapping`? The proposed fix assumes the
> latter.

### Bug 3 — array vs scalar

With ≥2 mapped variables, `zip(...)` yields 1-element `ndarray`s as values, but
`set_durations` requires each value to be `isinstance(v, (int, float))` and
rejects `ndarray`s with `ValueError`. (A single variable yields `np.float64`,
which passes `isinstance(..., float)`; a single variable delivered as a plain
Python float crashes the `zip` instead.) Values must be coerced to scalar
floats before reaching `set_durations`/`set_priorities`.

### Bug 4 — no test coverage of `run()`

`tests/unit_tests/CPM/test_cpm.py` exercises `Pert.set_durations()` directly
with clean `{task_id: float}` inputs; it never instantiates `BaseCPMmodel` or
calls `run()`. The 903-test suite therefore stays green while the RAVEN entry
point is broken.

---

## 2. Proposed fix

Replace the `itemgetter`/`zip` unpacking with a single loop over `self.mapping`
that (a) keys by **activity ID**, (b) coerces each realization to a scalar
`float`, and (c) guards against empty maps:

```python
def run(self, container, inputDict):
    def _scalar(name):
        if name not in inputDict:
            raise IOError(f"CPM Model: mapped variable not found: {name}")
        return float(np.ravel(inputDict[name])[0])

    durations, priorities = {}, {}
    for raven_var, (act_id, attr) in self.mapping.items():
        (durations if attr == 'duration' else priorities)[act_id] = _scalar(raven_var)

    if durations:
        self.pert.set_durations(durations)
    if priorities:
        self.pert.set_priorities(priorities, 'replace')

    self.pert.calculateScheduleWithResources(self.sgs)
    container.__dict__[self.CPtime] = np.asarray(float(self.pert.getProjectDuration()))
```

This clears Bugs 1–3. To close Bug 4, a `run()`-level unit test builds a small
`Pert`, feeds a mock `inputDict` of 1-element ndarrays for a **duration-only**
model, and asserts `CPtime` is set to a finite float — i.e. the exact path that
previously crashed.

**Status: applied.**

- `run()` rewritten in [`BaseCPMmodel.py`](../BaseCPMmodel.py) as above; the
  dead `from operator import itemgetter` import was removed. The `<map>`-based
  `RAVEN_VAR → ACT_ID` translation is now used (Bug 2 resolved in favor of
  "translate through `self.mapping`"); the RAVEN variable name may differ from
  the activity ID).
- Verified end-to-end against the **real** `Pert` engine on
  `doc/demos/rcpsp/examples/test_case_1.json`: the duration-only path (2 vars,
  and 1 var) yields a finite makespan; a mapped variable absent from
  `inputDict` raises a clean `IOError`; the pre-fix body raised `ValueError` on
  the identical inputs.
- Bug 4 closed by `tests/unit_tests/CPM/test_raven_interface.py`, which drives
  the real `run()` (instantiated via `__new__` to bypass the RAVEN base
  `__init__`). It is guarded by `pytest.importorskip("ravenframework")`, so it
  **runs in a RAVEN-enabled environment** and is skipped in the stand-alone CPM
  dev env (where `ravenframework` is not importable).
- Regression: CPM suite `903 passed, 3 skipped` (was `903 passed, 2 skipped`;
  the extra skip is the new RAVEN-guarded module).

---

## 3. Additional uses of RAVEN for the `Pert` class

Beyond duration-uncertainty propagation, the class already exposes hooks for
several richer analyses. Ordered easy → more involved:

- **Return more than `CPtime`.** `run()` currently outputs only the project
  duration; the old `CPid` (critical-path) output was dropped.
  `calculateScheduleWithResources()` already returns `delay_hours`,
  `n_completed`, and `iterations`, and the class offers
  `getCriticalPathSymbolic()`, per-activity slack, and `get_buffer_status()`.
  Surfacing these as additional RAVEN outputs lets RAVEN build distributions of
  **resource-wait delay** and a **criticality index** per activity
  (P(activity on the critical path)), not just makespan — high value, low
  effort.

  > **This is more than a nice-to-have for priority/resource analyses — it is a
  > prerequisite.** `run()` reports `getProjectDuration()`, i.e. the
  > *unconstrained* CPM length, which is a function of durations and precedence
  > only and is **invariant to sampled priorities**. Verified on
  > `example_10.json`: three different priority vectors all give
  > `getProjectDuration() = 71.0 h`, while the resource-constrained makespan
  > `calculateScheduleWithResources()['scheduled_duration']` moves (85 / 85 /
  > 91 h). So any priority-sampling study or GA priority-optimization sees a
  > **flat objective** as currently wired. Making priority/resource decks
  > meaningful requires `run()` to output the resource-constrained
  > `scheduled_duration` (as `CPtime`, or as an additional output). This blocks
  > the priority decks below (`test_BaseCPMmodel_res.xml`,
  > `test_BaseCPMmodel_res_GA.xml`); duration decks are unaffected.

- **Sample resource availability, not just durations.** Crew counts, equipment
  readiness, and consumable/restock delivery times are uncertain too. A new
  `attr='resource'`-style map (or a small hook into the resource pools) would
  let RAVEN propagate **resource** uncertainty in addition to duration
  uncertainty.

- **Schedule-risk / deadline reliability.** Given an outage window `D`, RAVEN's
  statistics / limit-surface tooling can estimate **P(makespan > D)** and drive
  **CCPM buffer sizing** (`insert_project_buffer`) from the makespan
  distribution.

- **Sensitivity ranking.** RAVEN postprocessors (Sobol indices, correlation)
  over the sampled durations vs. makespan identify **which activities' duration
  uncertainty drives schedule risk** — directly useful for focusing outage
  planning effort.

- **Multi-mode (MMRCPSP) scenarios.** `set_modes()` accepts a
  `{task_id: mode_id}` assignment; RAVEN could sample discrete mode choices to
  study duration/resource trade-offs across execution modes.

The first two (richer outputs, resource sampling) are the natural next
increments once `run()` is fixed.

### Out of scope (for now)

**RAVEN-driven optimization** — using RAVEN's optimizers (e.g. GA, via the
existing `ga.py` / `rcpsp_alns.py`) to optimize activity priorities or mode
assignments toward minimum expected makespan or maximum on-time probability —
was discussed and is **intentionally deferred**. Recorded here only so the idea
isn't lost.

---

## 4. RAVEN input decks (`tests/test_BaseCPMmodel*.xml`)

The five system-test decks were **modernized** to the current interface
(`project_file` / `schema` / `<map act=... attr=...>`), replacing the previous
stale format (`<analysis>`, `<CPid>`, and a `graphModel.py` `<Files>` input that
no longer exists anywhere in the repo). All are repointed at real example JSONs
under `doc/demos/rcpsp/examples/`.

| Deck | Role | `project_file` | Maps |
|---|---|---|---|
| `test_BaseCPMmodel.xml` | minimal **duration** sampling | `test_case_1.json` | 6 × duration |
| `test_BaseCPMmodel_map.xml` | realistic **duration** sampling (unchanged) | `example_10.json` | 10 × duration |
| `test_BaseCPMmodel_res.xml` | **priority** sampling | `example_10.json` | 10 × priority |
| `test_BaseCPMmodel_res_11.xml` | **mixed** duration + priority | `example_10.json` | 5 + 5 |
| `test_BaseCPMmodel_res_GA.xml` | **GA priority optimization** | `example_10.json` | 10 × priority |

Also fixed a latent bug in the GA deck: its `<IOStep>` referenced an `optOut`
Print that was never defined; it now references the defined `Print_sim_PS` and
`opt_export` OutStreams.

**Verification done here (no RAVEN required):** each deck is well-formed XML;
every `<ExternalModel>` child node is accepted by the current
`_readMoreXML` grammar; every mapped `act` ID exists in its `project_file`; and
each deck's `{act: value}` mapping was driven through the **real** `Pert` engine
(mirroring the fixed `run()`), all producing a finite `end_time`
(`.xml`→34 h, the `example_10.json` decks→71 h).

**Not done here (needs the RAVEN env):**

1. Wiring the decks into the RAVEN harness — the two CPM entries in
   `tests/tests` are commented out, and `_res`/`_res_11`/`_res_GA` are
   unregistered; there is no `CPMmodel/` working directory, and each deck's
   `project_file` + `schema` (bare filenames) must be staged into that workdir.
   Gold CSVs must be generated in a RAVEN-enabled environment.
2. The **priority/GA decks are structurally valid but semantically inert**
   until `run()` returns `scheduled_duration` instead of (or in addition to)
   `getProjectDuration()` — see the blocking note under §3. Their descriptions
   carry this caveat inline.
