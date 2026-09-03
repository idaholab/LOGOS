# Branch Assessment — `mandd/res_opt`

**Date:** 2026-09-03
**Reviewer:** Claude Code (initial status assessment)
**Base:** `devel`  ·  **Head:** `mandd/res_opt`  ·  **128 commits, ~190k insertions**

---

## TL;DR

The branch contains a large, genuinely substantial body of new CPM / RCPSP
scheduling work — a rewritten core engine (`pert.py`), supporting modules
(`activity.py`, `outage_data.py`, `schedule_validator.py`, `ga.py`,
`rcpsp_alns.py`), and ~840 unit tests. The engineering is real and the core
scheduling logic appears healthy.

**However, the branch is currently in a broken state for anyone who checks it
out and runs the tests.** The dev logs claim "747 tests pass, 0 violations"
(true as of 2026-04-16), but the branch has since **regressed** — chiefly
because recent file-reorganization commits moved test data out from under the
tests without updating the paths. The good news: the breakage is dominated by
**one mechanical root cause**, so recovery is mostly path-fixing, not
re-engineering.

### What the test run actually shows

Standard invocation (`python -m pytest` from `tests/unit_tests/CPM/`) **cannot
even collect** — it dies immediately (see Blocker #1).

After working around the collection blocker and excluding the 3 non-collecting
files:

```
740 passed, 55 failed, 3 skipped, 44 errors   (842 collected)
+ 3 test files that fail to collect at all
```

Of the 55 failures + 44 errors, **~97 are the same root cause** (relocated data
files). Only **2 are genuine logic-test failures**.

---

## ⚠️ Open items for reviewer (2026-09-03)

The plumbing findings (B1, H2, M4, M5, M6, H3, L10) are **resolved** — the suite
now reports **839 passed, 5 skipped, 0 failed, 0 errors** under a plain
`python -m pytest`. Two substantive items were surfaced during that work and
deliberately **left for a human's judgment** (not silently "fixed"):

1. **PSPLIB golden-value drift — `psplib_regression.py` (was `test_psplib.py`).**
   Once its stale data path was fixed and it could run again, it reports
   **176 passed / 30 failed**. All 30 failures are `scheduled_duration`
   mismatches against the notebook-recorded golden values — in *both* directions,
   across many priority rules, a few large (e.g. `j120 Parallel SGS [first]:
   332 != 209`). **Feasibility is intact** (the `check_dependency_violations`
   section has zero failures), so these are *stale golden baselines and/or
   heuristic-behavior drift*, **not** constraint violations. They were previously
   invisible because the file never collected. **Golden values were NOT edited.**
   → *Decision needed:* re-baseline the golden durations (if the engine changes
   since the notebook were intentional) vs. investigate specific rules as
   regressions. Full FAIL list reproducible via `python psplib_regression.py`.

2. **Two standalone regression scripts, intentionally kept out of CI.**
   Neither is a pytest file; both are homegrown `checkAnswer`-style scripts that
   `sys.exit()`. `run_cpm_pytests.py` runs *only* the pytest files, so these are
   not gated:
   - `legacy_cpm_regression.py` (was `CPM.py`) — **broken as-is** (uses
     `pd.date_range` / `np.ones` at ~line 165 but never imports pandas/numpy), so
     it currently provides zero coverage. It is nonetheless the **only** artifact
     exercising 3 scheduler strategies with no pytest equivalent —
     `MD-Knapsack`, `max_use_res_act`, `first_with_res`. → *Decision needed:*
     port these 3 strategies into pytest (fix the imports + validate gold values)
     vs. retire the file.
   - `psplib_regression.py` — see item 1; red on the 30 drifts.

### Coverage-guided regression hardening (2026-09-03) — RESOLVED

A green suite is not proof the earlier `pert.py` bug fixes are still in place.
A `pytest-cov` audit of the six documented `pert.py` fixes (CHANGELOG →
"Bug Fixes — `pert.py`") showed **three fix lines were dark** — no test
executed them — so a re-introduction of any of those bugs would *not* have
turned the suite red:

| # | Fix | Fix line(s) | Status before |
|---|---|---|---|
| 1 | Lag honoured in `check_dependency_violations` | 4952–4963 | dark |
| 2 | `_effective_duration` returns *remaining* on replan | 3431–3432 | dark |
| 4 | `_build_augmented_graph` None-pool guard | 5088–5089 | partial |
| 5 | Cycle detection in `_longest_path_in_augmented` | 5229–5239 | dark |
| 3 | `_window_violations` per-run isolation | — | already covered |
| 6 | `_apply_tentative` `eq_rem` KeyError | 3613 / 3759 | already covered |

Added **`test_bugfix_regressions.py`** (10 tests) driving each of the four
uncovered/partial fix lines directly. Each test was **mutation-verified**:
reverting the corresponding fix in `pert.py` turns exactly its test(s) red
(bug 2 → 2 tests, bugs 1/4/5 → 1 each); the control tests stay green. Suite is
now **849 passed, 5 skipped, 0 failed**, and all four fix sites are executed
(the only remaining dark lines there are the defensive `None`-guard `continue`
branches at 4945/4950 and the empty loc-loop body when no pools are attached).

---

## Findings by severity

### 🔴 Blocker

**B1. Legacy `tests/unit_tests/CPM/CPM.py` breaks *all* pytest collection.**

> **Status: RESOLVED (2026-09-03).** The legacy runner was **renamed**
> `CPM.py` → `legacy_cpm_regression.py` (via `git mv`, history preserved) so it
> no longer shadows the `src/CPM` package, and `pytest.ini`'s `pythonpath` was
> corrected (`../..` → `../../../src`) so `from CPM.x` resolves to the source
> package under the project's own config. Rename (not delete) was chosen because
> the legacy file is the *only* artifact exercising 3 scheduler strategies with
> no pytest coverage — `MD-Knapsack`, `max_use_res_act`, `first_with_res` — worth
> porting into pytest later (nothing imports it; only the RAVEN `tests` file did,
> fixed under M6). The suite now collects and runs under a plain
> `python -m pytest` (no workaround, no `PYTHONPATH` override): **839 passed,
> 3 skipped**, with only the 3 H3 files erroring on collection (missing
> `deap`/`alns`, module-level data load). NB: the file is broken as-is (uses
> `pd`/`np` without importing them) so it currently provides zero coverage —
> porting it means fixing those imports and validating its gold values.

Two stacked problems:
1. It is named `CPM.py` inside a directory named `CPM/` that pytest puts on
   `sys.path`, so it **shadows the real `src/CPM` package**. `conftest.py`'s
   `from CPM.activity import Activity` resolves to this file instead of the
   package.
2. The file itself is broken — it uses `pd.date_range(...)` and `np.ones(...)`
   at [CPM.py:165](../../../tests/unit_tests/CPM/CPM.py#L165) but never imports
   `pandas`/`numpy`.

Result: `conftest.py` import fails → **the entire suite refuses to collect**.
Every developer running the documented command hits a wall on line one.
(This file is the pre-pytest legacy regression runner; its header still says
"To run it: `python CPM.py`".)

### 🟠 High

**H2. File reorganization orphaned the test data → ~97 test breakages.**

> **Status: RESOLVED (2026-09-03).** Canonical paths are now centralized in
> `conftest.py` (`SCHEMA_PATH` → `src/CPM/outage_schema.json`, `EXAMPLES_DIR` →
> `doc/demos/rcpsp/examples/`) and all 16 affected test files point at them.
> With the B1 shadow file temporarily moved aside, the suite now reports
> **837 passed, 2 failed, 3 skipped** (the 2 failures are M4 below; the 44
> errors and 53 of the 55 failures are gone).

Commit `ef2493b "moved example files"` (plus the demo-folder reorg) relocated
the data the tests depend on:
- `example_10.json`, `test_case_1.json` → now in `doc/demos/rcpsp/examples/`
- `outage_schema.json` → lives at `src/CPM/outage_schema.json`

But the tests still look for them under `tests/unit_tests/`. Worse,
`conftest.py`'s `DATA_DIR` is itself wrong: at
[conftest.py:91](../../../tests/unit_tests/CPM/conftest.py#L91) it computes
`Path(__file__).parent.parent` = `tests/unit_tests/`, while the inline comment
claims it is `.../src/CPM/`. So fixtures resolve to paths like
`tests/unit_tests/example_10.json` — which do not exist.

This produces **44 collection errors + ~33 of the 55 failures**, all
`FileNotFoundError`. None of these are logic bugs.

**H3. Three test files do not collect at all.**

> **Status: RESOLVED (2026-09-03).**
> - `test_ga.py` / `test_rcpsp_alns.py` — standardized on the `from CPM.x` import
>   root and guarded with `pytest.importorskip("deap")` / `importorskip("alns")`
>   at module top. With the deps absent they now report a clean, informative
>   **skip** instead of a collection error. (Also fixed the 10 `patch('src.CPM.ga…')`
>   mock targets → `patch('CPM.ga…')` so they match the actual module path.)
> - `test_psplib.py` — turned out **not to be a pytest file at all**: no `test_*`
>   functions, ~900 lines executing at *module scope*, ending in
>   `sys.exit(results["fail"])`. It is a standalone regression script (same species
>   as the legacy `CPM.py` / B1), only caught by the `test_*.py` glob. So "move the
>   data load into a fixture" was not applicable. Treated consistently with B1:
>   **renamed** `test_psplib.py` → `psplib_regression.py` (out of pytest's
>   collection glob) and fixed its stale data path (`PSPLIB_DIR` → the relocated
>   `doc/demos/rcpsp/examples/`). It now runs standalone.
>   ⚠️ **New finding (needs your call):** once runnable, it reports **176 passed /
>   30 failed** — all `scheduled_duration` mismatches vs. the notebook-recorded
>   golden values (both directions, across many priority rules; a few large, e.g.
>   `j120 Parallel SGS [first]: 332 != 209`). Feasibility is intact — the
>   `check_dependency_violations` section has **zero** failures — so these are
>   **stale golden baselines and/or heuristic-behavior drift**, not constraint
>   violations. These were previously invisible (the file never collected). I did
>   **not** touch the golden values; re-baselining vs. investigating is a judgment
>   call for you.
> - `conftest.py` de-stubbed (see M5): the `sys.modules` fabrication that swallowed
>   these ImportErrors is gone, so a genuinely missing dep now surfaces honestly.
>
> Net: plain `python -m pytest` reports **839 passed, 5 skipped, 0 errors**.

- `test_ga.py` — imports `from src.CPM.ga import RCPSPGeneticAlgorithm`
  (a *different* import root than every other test, which use `from CPM...`).
  The class exists ([ga.py:116](../ga.py#L116)); the failure is (a) the
  `src.CPM.*` root colliding with `conftest.py`'s module stubbing, and
  (b) `ga.py` requires **`deap`**, which is not installed in this environment.
- `test_rcpsp_alns.py` — `rcpsp_alns.py` requires **`alns`**
  ([rcpsp_alns.py:99](../rcpsp_alns.py#L99)), not installed.
- `test_psplib.py` — performs a **module-level** `Pert.from_json_file('.../j301_1.json')`
  at import time (line ~107), so a missing/moved data file becomes a hard
  collection error instead of a skip. The `j301_1.json` was also moved to
  `doc/demos/rcpsp/examples/`.

Note: `deap` and `alns` *are* declared in `dependencies.xml` (RAVEN-style), so
this is an **environment mismatch** (the active `conda`/`anaconda` env lacks
them), not a missing declaration. But module-level `import` of an optional
heavy dependency will keep breaking collection regardless.

### 🟡 Medium

**M4. Two genuine logic-test failures in `test_buffer.py` (CCPM buffers).**

> **Status: RESOLVED (2026-09-03).** Diagnosis: **stale test expectations, not a
> code bug.** `insert_project_buffer()` deliberately splices the buffer as
> `terminal → PB → END` — stepping back off the zero-duration `END` sentinel —
> with a documented rationale ([pert.py:4259-4265](../pert.py#L4259-L4265)):
> placing PB *after* END makes a dangling sink that forces `generateInfo()` to
> use the old `project_duration` as the backward-pass ceiling, producing negative
> slack. This matches the test module's own stated contract
> ([test_buffer.py:15](../../../tests/unit_tests/CPM/test_buffer.py#L15),
> "terminal→PB→successors"). The two tests failed only because they read
> `constrained_chain_list[-1]`, which is the END sentinel (the chain is a full
> START→…→END longest path), not the terminal work activity. Fixed by indexing
> the terminal work activity (`[-2]`) and correcting the second test's premise;
> `test_pb_is_new_terminal` → `test_pb_feeds_end_milestone`, now asserting both
> splice edges (`terminal→PB` **and** `PB→END`). Suite (excluding the 3
> H3-deferred files) now reports **839 passed, 3 skipped, 0 failed**.

- `test_pb_predecessor_is_chain_terminal`
  ([test_buffer.py:275](../../../tests/unit_tests/CPM/test_buffer.py#L275))
- `test_pb_is_new_terminal` (renamed → `test_pb_feeds_end_milestone`)
  ([test_buffer.py:287](../../../tests/unit_tests/CPM/test_buffer.py#L287))

`insert_project_buffer()` attaches the project buffer after the last **real**
activity (`B`), but the tests expected it after the zero-duration `END` sentinel
that `constrained_chain_list[-1]` returns:
`assert Activity('END', 0.0h) in [Activity('B', 6.0h)]` failed.
Localized to the buffer feature; the other ~13 buffer tests pass.

**M5. Inconsistent import conventions + fragile `conftest.py` stubbing.**

> **Status: RESOLVED (2026-09-03).** All collected tests now use the single
> `from CPM.x` import root (`test_ga.py` / `test_rcpsp_alns.py` converted off
> `src.CPM.*`; verified no `src.CPM` import remains in any `test_*.py` or
> `conftest.py`). `conftest.py` was **de-stubbed**: the `sys.modules`
> fabrication block (`_ensure('src.CPM.ga')` et al.), the vestigial `LOGOS`
> stub, and the `REPO_ROOT` `sys.path.insert` were all removed — the CPM package
> imports neither `LOGOS` nor `src` internally, so they were dead weight that
> only served to swallow real ImportErrors. `REPO_ROOT` is retained purely to
> anchor the `SCHEMA_PATH` / `EXAMPLES_DIR` data paths. The two standalone
> regression scripts keep the `src.CPM` root (correct for `python <script>.py`
> with the repo root on `sys.path`, matching `legacy_cpm_regression.py`).

Most tests use `from CPM.x`; `test_ga.py` uses `from src.CPM.x`. `conftest.py`
`_ensure('src.CPM.ga')` ([conftest.py:36-39](../../../tests/unit_tests/CPM/conftest.py#L36-L39))
creates **empty stub modules** on `ImportError`, which masks the real cause
with a misleading `cannot import name ... (unknown location)`. Standardize on a
single import root and stop swallowing import errors.

**M6. The pytest suite is not wired into the project's real test runner.**

> **Status: RESOLVED (2026-09-03).** No repo precedent exists for registering a
> pytest suite via RAVEN (siblings use `LogosRun` with XML+CSV; only this dir
> used `RavenPython`, which runs `python <input>` and gates on exit code 0). So
> the malformed 4-entry `tests` file (duplicate `[./test_ga]` mislabel, the
> now-renamed `CPM.py`, and files that either self-skip or are red) was replaced
> with a **single** entry pointing at a new shim, `run_cpm_pytests.py`, which
> execs `python -m pytest` over this directory. That wires the full ~840-test
> suite into the RAVEN CI path; optional-dep files self-skip to exit 0 so a dev
> env without `deap`/`alns` doesn't break CI. Verified: `python run_cpm_pytests.py`
> → exit 0, **839 passed, 5 skipped**. The two standalone regression scripts are
> intentionally *not* registered — `legacy_cpm_regression.py` is broken (B1) and
> `psplib_regression.py` is currently red (30 golden drifts, H3); wiring either
> in would gate CI on a known-failing check. They remain manual dev harnesses
> pending repair.

`tests/unit_tests/CPM/tests` (the RAVEN `RavenPython` registration) lists only
the **4 broken files** — `CPM.py`, `test_psplib.py`, `test_ga.py`,
`test_rcpsp_alns.py` — and none of the ~840 passing pytest tests. It also has a
malformed **duplicate `[./test_ga]`** label (the second one actually points to
`test_rcpsp_alns.py`). So in the RAVEN CI path, only the broken files run.

### 🟢 Low / hygiene

**L7. `pyproject.toml` is `uv init` boilerplate and self-contradictory.**
`description = "Add your description here"`, `dependencies = []`, and
`requires-python = ">=3.12"` — but the env is **Python 3.11.11** and
`mandd_pert_edits_summary.md` claims "Python 3.10 compatibility" (a three-way
contradiction). It also sets `[tool.pytest.ini_options] addopts =
"--import-mode=importlib"`, which competes with the separate
`tests/unit_tests/CPM/pytest.ini`. Pick one source of truth
(pyproject vs `dependencies.xml` vs `pytest.ini`).

**L8. `pert.py` is a 7,248-line / 322 KB monolith.** One 394-line plotting
method (`plot_activity_dag`), several 150–230-line methods, and two auxiliary
schedulers (`MDKnapsackScheduler`, `LookAheadScheduler`) all live in one file.
Not a bug, but a maintainability risk — plotting helpers and the aux schedulers
are natural extraction candidates.

**L9. Uncommitted clutter in the working tree.** `mandd_pert_edits_summary.md`,
`delete_trailing_whitespace.sh`, stray `schedule.json` / `test_schedule.json` /
`benchmarkOutageSchedule.json` under `doc/demos/CPM/`, a notebook with a space
in its name (`CPM_testing_from file.ipynb`), and `tests/test_BaseCPMmodel_res_11.xml`.
Decide keep / `.gitignore` / remove before this branch is reviewed.

**L10. Dev-log drift.** `CHANGELOG.md` / `CORRECTNESS_REVIEW.md` assert a green
suite as of 2026-04-16. That is no longer true post-reorg. The docs should be
updated (or, better, the suite fixed so they become true again).

> **Status: RESOLVED (2026-09-03).** The suite was fixed (B1/H2/M4/M5/M6/H3
> above), so the green claim is true again — now at a higher count. Rather than
> rewrite the dated 2026-04-16 entries (preserved as historical record), both
> logs got a dated update banner reflecting current state and pointing here:
> `CORRECTNESS_REVIEW.md` (top banner) and `CHANGELOG.md` (a "Test-harness
> recovery (2026-09-03)" entry under `[Unreleased]`). Current status recorded in
> both: **839 passed, 5 skipped, 0 failed, 0 errors, 0 validator violations**.

---

## Recommended recovery order (mostly mechanical)

1. **Unblock collection (B1):** delete `tests/unit_tests/CPM/CPM.py`, or
   port it to pytest and rename it so it no longer shadows the package.
   *This one change lets the suite collect again.*
2. **Fix data-file resolution (H2):** correct `conftest.py` `DATA_DIR` and the
   per-test paths, or copy a canonical fixture set into
   `tests/unit_tests/CPM/data/`. *Recovers ~97 tests.*
3. **Handle optional deps (H3):** install `deap` + `alns` in the dev env (they
   are in `dependencies.xml`), guard `test_ga` / `test_rcpsp_alns` with
   `pytest.importorskip`, and move `test_psplib.py`'s data load out of module
   scope into a fixture.
4. **Fix the 2 buffer failures (M4):** reconcile `insert_project_buffer` with
   the `END`-sentinel definition of the chain terminal.
5. **Wire the pytest suite into the runner (M6)** and clean up the duplicate
   `[./test_ga]` entry.
6. **Reconcile config + clean the tree (L7, L9);** refresh the dev logs (L10).

### To reproduce this assessment

```bash
cd tests/unit_tests/CPM
mv CPM.py CPM.py.bak          # step around Blocker B1 (restore afterward)
PYTHONPATH=$(git rev-parse --show-toplevel)/src \
  python -m pytest -q -o addopts="" \
  --ignore=test_ga.py --ignore=test_psplib.py --ignore=test_rcpsp_alns.py
mv CPM.py.bak CPM.py
# → 740 passed, 55 failed, 3 skipped, 44 errors
```

---

## Bottom line

This is not throwaway work — the core scheduler and its validation strategy are
solid, and the vast majority of tests pass once the harness can find its data.
The branch's problems are overwhelmingly **plumbing**: a shadowing legacy file,
a file move that wasn't propagated to the tests, optional deps not installed,
and config drift. Estimate the bulk of the red can be cleared by fixes #1–#3
above; only #4 (2 buffer tests) is a genuine logic question. It is **not
merge-ready today**, but it is close to recoverable.