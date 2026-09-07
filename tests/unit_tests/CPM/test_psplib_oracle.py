"""
External-oracle regression against PSPLIB best-known solutions (dev log §10.6).

Distinct from psplib_regression.py, which compares the engine to its *own*
frozen golden makespans (self-consistency).  This module compares against an
*independent* external reference: the best-known-solution (BKS / optimal)
makespans published for the standard PSPLIB j30/j60/j90/j120 instances, stored
in doc/demos/benchmarks/best_results.json.

The oracle rests on one inequality that no correct RCPSP heuristic can violate:

    a resource-feasible makespan can never be shorter than the optimum.

So for every schedule-generation strategy,

    scheduled_duration - 2.0  >=  BKS

(the -2.0 removes the 1 h START + 1 h END dummy contribution, matching
psplib_regression.py).  A makespan *below* BKS could only mean the schedule is
resource-infeasible (or the makespan is mis-computed) — exactly the silent
"too-short = wrong" failure this suite exists to catch.  As a second, direct
check, validate_schedule must report the produced schedule feasible.

These instances ship pre-converted in doc/demos/rcpsp/examples/.  The module
skips cleanly if either the instance JSON or the BKS table is absent.
"""
import json

import pytest

from conftest import assert_valid_schedule, EXAMPLES_DIR, SCHEMA_PATH, REPO_ROOT
from CPM.pert import Pert

TOL = 1e-6

# 1 h START + 1 h END dummy activities inflate the engine makespan relative to
# the dummy-free PSPLIB convention (see psplib_regression.py:223).
DUMMY_OFFSET = 2.0

ALL_SGS = [
    "first",
    "max_use_res_ranked",
    "max_use_res_shuffled",
    "md_knapsack",
    "look_ahead",
]

# Instances with a BKS entry in best_results.json (keyed "<instance>.sm").
INSTANCES = ["j301_1", "j601_1", "j901_1", "j1201_1"]

_BKS_PATH = REPO_ROOT / "doc" / "demos" / "benchmarks" / "best_results.json"


def _load_bks():
    if not _BKS_PATH.is_file():
        return {}
    with open(_BKS_PATH) as fh:
        return json.load(fh)


_BKS = _load_bks()


def _oracle_cases():
    """(instance, bks) pairs whose JSON and BKS entry both exist."""
    cases = []
    for inst in INSTANCES:
        json_path = EXAMPLES_DIR / f"{inst}.json"
        bks = _BKS.get(f"{inst}.sm")
        if json_path.is_file() and bks is not None:
            cases.append((inst, float(bks)))
    return cases


ORACLE_CASES = _oracle_cases()

pytestmark = pytest.mark.skipif(
    not ORACLE_CASES,
    reason="PSPLIB instance JSON and/or best_results.json not available",
)


@pytest.mark.parametrize("inst,bks", ORACLE_CASES,
                         ids=[c[0] for c in ORACLE_CASES])
@pytest.mark.parametrize("sgs", ALL_SGS)
def test_makespan_not_below_bks(inst, bks, sgs):
    """No strategy may report a makespan below the best-known optimum, and the
    schedule it produces must validate as feasible."""
    p = Pert.from_json_file(str(EXAMPLES_DIR / f"{inst}.json"),
                            schema_path=SCHEMA_PATH)
    r = p.calculateScheduleWithResources(sgs=sgs)
    makespan = r["scheduled_duration"] - DUMMY_OFFSET

    assert_valid_schedule(p, f"{inst} / {sgs}")
    assert r["n_completed"] == r["n_activities"], (
        f"{inst} [{sgs}]: only {r['n_completed']}/{r['n_activities']} scheduled")
    assert makespan >= bks - TOL, (
        f"{inst} [{sgs}]: makespan {makespan:.4f} is below the best-known "
        f"optimum {bks:.1f} — schedule is resource-infeasible or makespan is "
        f"mis-computed")
