"""
alns_test.py — Integration test for the RCPSP ALNS solver (rcpsp_alns.py)

Runs the Adaptive Large Neighbourhood Search on each PSPLIB benchmark JSON
(j30, j60, j90, j120) and prints a comparison table of:

  - CPM duration (unconstrained)
  - Best duration from all named priority rules (serial + parallel SGS)
  - Best ALNS duration (activity list + serial SGS decoder)
  - Improvement over the best seeded solution

The ALNS uses three destroy operators (most_mobile, segment, random) and two
repair operators (random_insert, greedy_insert) with SegmentedRouletteWheel
selection.  The acceptance criterion and other hyper-parameters can be
configured per-case via ``run_alns_case``.

Reference
---------
Wouda, N.A., and L. Lan (2023).  ALNS: a Python implementation.
  *Journal of Open Source Software*, 8(81): 5028.

Usage (from the src/CPM directory):
    python alns_test.py

Or from the repo root:
    python -m src.CPM.alns_test
"""

import sys
import logging
from pathlib import Path

# Ensure repo root is on the path before project imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import Pert  # noqa: E402
from src.CPM.rcpsp_alns import (  # noqa: E402
    RCPSPAdaptiveLNS,
    SEED_PRIORITY_RULES,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCHEMA = Path(__file__).parent / "outage_schema.json"

CASES = [
    ("j30",  "j301_1.json"),
    ("j60",  "j601_1.json"),
    ("j90",  "j901_1.json"),
    ("j120", "j1201_1.json"),
]

# Extra priority rules for the baseline (superset of SEED_PRIORITY_RULES)
ALL_PRIORITY_RULES = SEED_PRIORITY_RULES + [
    'random', 'irsm',
    'mehh_8000_b', 'mehh_3375_b', 'mehh_1000_b', 'mehh_125_b', 'gphh_b',
]


def run_alns_case(
    case_name: str,
    json_file: str,
    n_iter: int = 2000,
    destroy_fraction: float = 0.25,
    theta: float = 0.8,
    seg_length: int = 100,
    seed: int = 42,
    accept: str = 'hill_climbing',
    destroy_ops: list = None,
    repair_ops: list = None,
    verbose: bool = True,
) -> dict:
    """
    Run the ALNS on a single PSPLIB benchmark case.

    Steps
    -----
    1. Load and initialise the Pert model.
    2. Record baseline durations for every named priority rule under both
       serial and parallel SGS (these include the rules used to seed the
       ALNS initial solution).
    3. Run the ALNS (destroy/repair on Activity List representation, serial
       SGS decoder).
    4. Print per-case results and return a summary dict.

    Parameters
    ----------
    case_name : str
        Human-readable label (e.g. 'j30').
    json_file : str
        Path to the input JSON relative to this file's directory.
    n_iter : int
        Total ALNS iterations.
    destroy_fraction : float
        Fraction of non-dummy activities removed per destroy step.
    theta : float
        Segment-decay factor for SegmentedRouletteWheel.
    seg_length : int
        Iterations per segment for SegmentedRouletteWheel.
    seed : int
        RNG seed.
    accept : str
        Acceptance criterion: 'hill_climbing', 'record_to_record', or
        'simulated_annealing'.
    destroy_ops : list of str, optional
        Destroy operators to use; defaults to all three.
    repair_ops : list of str, optional
        Repair operators to use; defaults to both.
    verbose : bool
        Print per-iteration seeding table and operator usage stats.

    Returns
    -------
    dict with keys:
        case, n_activities, cpm_duration,
        best_serial_seed, best_parallel_seed, best_alns, improvement
    """
    json_path = Path(__file__).parent / json_file

    print("=" * 70)
    print(f"Case: {case_name}  ({json_file})")
    print("=" * 70)

    # ── Load and initialise Pert model ───────────────────────────────────────
    pert = Pert.from_json_file(str(json_path), schema_path=str(SCHEMA))
    pert.generateInfo()

    cpm_duration = pert.getProjectDuration()
    n_activities = len(pert.infoDict)

    print(f"Activities      : {n_activities}")
    print(f"CPM duration    : {cpm_duration:.2f} h  (unconstrained)")
    print()

    # ── Baseline: best duration from all named priority rules ─────────────────
    serial_durations = {}
    parallel_durations = {}

    for rule in ALL_PRIORITY_RULES:
        try:
            pert.priorities = None
            s_out = pert.calculateSerialScheduleWithResources(priority_rule=rule)
            serial_durations[rule] = s_out['scheduled_duration'] - 2

            pert.priorities = None
            p_out = pert.calculateScheduleWithResources(
                sgs='max_use_res_ranked', priority_rule=rule
            )
            parallel_durations[rule] = p_out['scheduled_duration'] - 2
        except Exception as exc:  # noqa: BLE001
            logger.debug("Rule '%s' skipped in baseline: %s", rule, exc)

    best_serial   = min(serial_durations.values(),   default=float('inf'))
    best_parallel = min(parallel_durations.values(), default=float('inf'))
    best_rule_overall = min(best_serial, best_parallel)

    if verbose:
        print("Priority rule baseline durations:")
        print(f"  {'Rule':<22} {'Serial (h)':>12} {'Parallel (h)':>14}")
        print("  " + "-" * 50)
        for rule in ALL_PRIORITY_RULES:
            s = serial_durations.get(rule, float('nan'))
            p = parallel_durations.get(rule, float('nan'))
            print(f"  {rule:<22} {s:>12.2f} {p:>14.2f}")
        print()

    # ── Run ALNS ─────────────────────────────────────────────────────────────
    alns = RCPSPAdaptiveLNS(
        pert,
        n_iter=n_iter,
        destroy_fraction=destroy_fraction,
        theta=theta,
        seg_length=seg_length,
        seed=seed,
        accept=accept,
        destroy_ops=destroy_ops,
        repair_ops=repair_ops,
        verbose=verbose,
    )
    best_state, log = alns.run()

    # ── Report ───────────────────────────────────────────────────────────────
    best_alns = log['best_duration']
    best_activity_list = alns.get_best_activity_list(best_state)

    print(f"{'─' * 60}")
    print(f"  CPM duration (unconstrained)  : {cpm_duration:.2f} h")
    print(f"  Best serial SGS  (all rules)  : {best_serial:.2f} h")
    print(f"  Best parallel SGS (all rules) : {best_parallel:.2f} h")
    print(f"  ALNS initial solution         : {log['initial_duration']:.2f} h")
    print(f"  Best ALNS duration            : {best_alns:.2f} h")
    print(f"  Improvement over best seed    : {best_rule_overall - best_alns:.2f} h")
    print(f"  ALNS internal improvement     : {log['improvement']:.2f} h")
    print(f"{'─' * 60}")
    print(f"  Best activity list (first 10) : {best_activity_list[:10]}")
    print()

    return {
        'case':               case_name,
        'n_activities':       n_activities,
        'cpm_duration':       cpm_duration,
        'best_serial_seed':   best_serial,
        'best_parallel_seed': best_parallel,
        'best_alns':          best_alns,
        'improvement':        best_rule_overall - best_alns,
    }


def main() -> None:
    """Run the ALNS on all benchmark cases and print a summary table."""
    results = []
    for case_name, json_file in CASES:
        result = run_alns_case(
            case_name=case_name,
            json_file=json_file,
            n_iter=2000,
            destroy_fraction=0.25,
            theta=0.8,
            seg_length=100,
            seed=42,
            accept='hill_climbing',
            verbose=True,
        )
        results.append(result)

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n" + "=" * 85)
    print(
        "SUMMARY — ALNS (Activity List, most_mobile + segment + random destroy, "
        "random + greedy repair, Serial SGS)"
    )
    print("=" * 85)
    print(
        f"  {'Case':<8} {'N':>6} {'CPM (h)':>10} "
        f"{'Best Serial':>13} {'Best Parallel':>14} {'Best ALNS':>10} {'Δ (h)':>8}"
    )
    print("  " + "-" * 72)
    for r in results:
        print(
            f"  {r['case']:<8} {r['n_activities']:>6} {r['cpm_duration']:>10.2f} "
            f"{r['best_serial_seed']:>13.2f} {r['best_parallel_seed']:>14.2f} "
            f"{r['best_alns']:>10.2f} {r['improvement']:>8.2f}"
        )
    print()


if __name__ == "__main__":
    main()
