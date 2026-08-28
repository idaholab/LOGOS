"""
gans_test.py — Combined benchmark for GA, ALNS, and GANS on PSPLIB instances

Runs three RCPSP metaheuristics on each benchmark JSON (j30, j60, j90, j120):
  1. Genetic Algorithm (``ga.py``)
  2. Adaptive Large Neighbourhood Search (``rcpsp_alns.py``)
  3. Hybrid GA + Neighbourhood Search (``gans.py``)

Prints a per-case comparison report and a unified summary table.

This script subsumes ``alns_test.py`` — all four benchmark cases and the
same ALNS hyper-parameters are reproduced here.

References
----------
Kolisch, R. and Hartmann, S. (1999). Heuristic Algorithms for Solving the
Resource-Constrained Project Scheduling Problem.

Wouda, N.A., and L. Lan (2023). ALNS: a Python implementation.
Journal of Open Source Software, 8(81): 5028.

Goncharov, E.N. (2025). A hybrid heuristic algorithm for the
resource-constrained project scheduling problem. arXiv:2502.18330v2.

Usage (from the src/CPM directory):
    python gans_test.py

Or from the repo root:
    python -m src.CPM.gans_test
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import Pert  # noqa: E402
from src.CPM.ga import RCPSPGeneticAlgorithm  # noqa: E402
from src.CPM.rcpsp_alns import RCPSPAdaptiveLNS, SEED_PRIORITY_RULES  # noqa: E402
from src.CPM.gans import RCPSPHybridGANS, PRIORITY_RULES  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCHEMA = Path(__file__).parent / "outage_schema.json"

CASES = [
    ("j30",  "j301_1.json"),
    ("j60",  "j601_1.json"),
    ("j90",  "j901_1.json"),
    ("j120", "j1201_1.json"),
]

# Combined rule list for serial-SGS baseline
_ALL_RULES = list(dict.fromkeys(PRIORITY_RULES + SEED_PRIORITY_RULES))


def _compute_serial_baseline(pert) -> dict:
    """Run serial SGS for every priority rule; return {rule: duration} dict."""
    durations = {}
    for rule in _ALL_RULES:
        try:
            pert.priorities = None
            out = pert.calculateSerialScheduleWithResources(priority_rule=rule)
            durations[rule] = out['scheduled_duration'] - 2
        except Exception as exc:  # noqa: BLE001
            logger.debug("Rule '%s' skipped: %s", rule, exc)
    return durations


def run_case(
    case_name: str,
    json_file: str,
    # GA params
    ga_pop_size: int = 30,
    ga_n_gen: int = 500,
    ga_cxpb: float = 0.8,
    ga_mutpb: float = 0.1,
    ga_crossover: str = 'two_point',
    ga_mutation: str = 'adjacent_swap',
    # ALNS params
    alns_n_iter: int = 2000,
    alns_destroy_fraction: float = 0.25,
    alns_theta: float = 0.8,
    alns_seg_length: int = 100,
    alns_accept: str = 'hill_climbing',
    # GANS params
    gans_pop_size: int = 60,
    gans_lambda_max: int = 5000,
    gans_ga_stall_limit: int = 50,
    gans_ns_steps: int = 200,
    gans_block_size: int = 6,
    gans_resource_threshold: float = 0.75,
    # Shared
    seed: int = 42,
    verbose: bool = True,
) -> dict:
    """
    Load one PSPLIB benchmark instance and run GA, ALNS, and GANS on it.

    Returns a summary dict with per-algorithm best durations.
    """
    json_path = Path(__file__).parent / json_file

    print("=" * 70)
    print(f"Case: {case_name}  ({json_file})")
    print("=" * 70)

    pert = Pert.from_json_file(str(json_path), schema_path=str(SCHEMA))
    pert.generateInfo()

    cpm_duration = pert.getProjectDuration()
    n_activities = len(pert.infoDict)

    print(f"Activities      : {n_activities}")
    print(f"CPM duration    : {cpm_duration:.2f} h  (unconstrained)")
    print()

    # ── Priority-rule baseline ──────────────────────────────────────────────
    serial_durations = _compute_serial_baseline(pert)
    best_rule = min(serial_durations.values(), default=float('inf'))

    if verbose:
        print("Priority rule baseline durations (serial SGS):")
        print(f"  {'Rule':<22} {'Serial (h)':>12}")
        print("  " + "-" * 36)
        for rule in _ALL_RULES:
            s = serial_durations.get(rule, float('nan'))
            print(f"  {rule:<22} {s:>12.2f}")
        print()

    # ── GA ──────────────────────────────────────────────────────────────────
    print(f"Running GA (pop={ga_pop_size}, n_gen={ga_n_gen}, "
          f"cx={ga_crossover}, mut={ga_mutation})...")
    ga = RCPSPGeneticAlgorithm(
        pert,
        pop_size=ga_pop_size,
        n_gen=ga_n_gen,
        cxpb=ga_cxpb,
        mutpb=ga_mutpb,
        seed=seed,
        verbose=False,
        crossover=ga_crossover,
        mutation=ga_mutation,
    )
    ga_hof, _ = ga.run()
    best_ga = ga.get_best_schedule(ga_hof)['scheduled_duration'] - 2
    print(f"  GA best: {best_ga:.2f} h")
    print()

    # ── ALNS ────────────────────────────────────────────────────────────────
    print(f"Running ALNS (n_iter={alns_n_iter}, destroy={alns_destroy_fraction}, "
          f"accept={alns_accept})...")
    alns = RCPSPAdaptiveLNS(
        pert,
        n_iter=alns_n_iter,
        destroy_fraction=alns_destroy_fraction,
        theta=alns_theta,
        seg_length=alns_seg_length,
        seed=seed,
        accept=alns_accept,
        verbose=False,
    )
    alns_best_state, alns_log = alns.run()
    best_alns = alns_log['best_duration']
    print(f"  ALNS best: {best_alns:.2f} h")
    print()

    # ── GANS ────────────────────────────────────────────────────────────────
    print(f"Running GANS (pop={gans_pop_size}, λ_max={gans_lambda_max}, "
          f"stall={gans_ga_stall_limit}, ns_steps={gans_ns_steps})...")
    gans = RCPSPHybridGANS(
        pert,
        pop_size=gans_pop_size,
        lambda_max=gans_lambda_max,
        ga_stall_limit=gans_ga_stall_limit,
        ns_steps=gans_ns_steps,
        block_size=gans_block_size,
        resource_threshold=gans_resource_threshold,
        seed=seed,
        verbose=verbose,
    )
    gans_best, gans_log = gans.run()
    best_gans = gans_best['fitness']
    gans_summary = gans.get_convergence_summary(gans_log)

    print()
    print(f"{'─' * 60}")
    print(f"  CPM (unconstrained)           : {cpm_duration:.2f} h")
    print(f"  Best priority-rule (serial)   : {best_rule:.2f} h")
    print(f"  GA best                       : {best_ga:.2f} h  "
          f"(Δ {best_rule - best_ga:+.2f})")
    print(f"  ALNS best                     : {best_alns:.2f} h  "
          f"(Δ {best_rule - best_alns:+.2f})")
    print(f"  GANS best                     : {best_gans:.2f} h  "
          f"(Δ {best_rule - best_gans:+.2f})")
    print(f"  GANS initial best             : {gans_summary['initial_best']:.2f} h")
    print(f"  GANS improvement              : {gans_summary['improvement']:.2f} h")
    print(f"  GANS NS activations           : {gans_summary['n_ns_activations']}")
    print(f"{'─' * 60}")
    print(f"  GANS best list (first 10)     : "
          f"{gans.get_best_activity_list(gans_best)[:10]}")
    print()

    return {
        'case': case_name,
        'n_activities': n_activities,
        'cpm_duration': cpm_duration,
        'best_rule': best_rule,
        'best_ga': best_ga,
        'best_alns': best_alns,
        'best_gans': best_gans,
        'gans_n_ns': gans_summary['n_ns_activations'],
    }


def main() -> None:
    """Run all benchmark cases and print a unified comparison table."""
    results = []
    for case_name, json_file in CASES:
        r = run_case(
            case_name=case_name,
            json_file=json_file,
            ga_pop_size=50,
            ga_n_gen=100,
            alns_n_iter=2000,
            gans_pop_size=60,
            gans_lambda_max=5000,
            gans_ga_stall_limit=50,
            gans_ns_steps=200,
            seed=42,
            verbose=True,
        )
        results.append(r)

    print("\n" + "=" * 90)
    print("SUMMARY — GA / ALNS / GANS  (PSPLIB j30–j120, serial SGS decoder)")
    print("=" * 90)
    print(
        f"  {'Case':<8} {'N':>6} {'CPM (h)':>9} "
        f"{'BestRule':>9} {'GA':>8} {'ALNS':>8} {'GANS':>8} "
        f"{'Δ GA':>7} {'Δ ALNS':>7} {'Δ GANS':>7} {'NS#':>5}"
    )
    print("  " + "-" * 82)
    for r in results:
        print(
            f"  {r['case']:<8} {r['n_activities']:>6} {r['cpm_duration']:>9.2f} "
            f"{r['best_rule']:>9.2f} {r['best_ga']:>8.2f} {r['best_alns']:>8.2f} "
            f"{r['best_gans']:>8.2f} "
            f"{r['best_rule'] - r['best_ga']:>7.2f} "
            f"{r['best_rule'] - r['best_alns']:>7.2f} "
            f"{r['best_rule'] - r['best_gans']:>7.2f} "
            f"{r['gans_n_ns']:>5}"
        )
    print()


if __name__ == "__main__":
    main()
