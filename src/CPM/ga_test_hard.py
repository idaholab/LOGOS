"""
ga_test_hard.py — Hard-instance integration test for the RCPSP Genetic Algorithm (ga.py)

Runs the GA on a selected set of difficult PSPLIB j120 benchmark instances and prints
a comparison table of:
  - Best duration from all named priority rules (serial + parallel SGS)
  - Best GA duration (activity list chromosome + serial SGS decoder)
  - Improvement over the best seeded solution

The GA uses the Activity List representation with configurable crossover
and mutation operators.  Decoding is performed by the Serial SGS.
Default operators: two-point crossover, consensus-reorder mutation.

Reference
---------
Kolisch, R. and Hartmann, S. (1999). Heuristic Algorithms for Solving the
Resource-Constrained Project Scheduling Problem. In J. Weglarz (ed.),
Project Scheduling: Recent Models, Algorithms and Applications, 147-178.

Usage (from the src/CPM directory):
    python ga_test_hard.py

Or from the repo root:
    python -m src.CPM.ga_test_hard
    python -m src.CPM.ga_test_hard --replacement-strategy elitist
"""

import argparse
import sys
import logging
import json
from pathlib import Path



# Ensure repo root is on the path before project imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import Pert  # noqa: E402
from src.CPM.ga import RCPSPGeneticAlgorithm, PRIORITY_RULES  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
SCHEMA = Path(__file__).parent / "outage_schema.json"
BEST_RESULTS_PATH = Path(__file__).parent / "benchmarks" / "best_results.json"

CASES = [
    ("j12051_6",  "benchmarks/PSPLIB_Json/j120/j12051_6.json"),
    ("j12031_10", "benchmarks/PSPLIB_Json/j120/j12031_10.json"),
    ("j12036_6",  "benchmarks/PSPLIB_Json/j120/j12036_6.json"),
    ("j12056_7",  "benchmarks/PSPLIB_Json/j120/j12056_7.json"),
    ("j12051_5",  "benchmarks/PSPLIB_Json/j120/j12051_5.json"),
    ("j12056_1",  "benchmarks/PSPLIB_Json/j120/j12056_1.json"),
    ("j12026_10", "benchmarks/PSPLIB_Json/j120/j12026_10.json"),
    ("j12051_7",  "benchmarks/PSPLIB_Json/j120/j12051_7.json"),
    ("j12056_5",  "benchmarks/PSPLIB_Json/j120/j12056_5.json"),
    ("j12056_9",  "benchmarks/PSPLIB_Json/j120/j12056_9.json"),
]

# ================================================================================
# SUMMARY — GA (Activity List, two-point crossover, adjacent-swap mutation, Serial SGS)
# ================================================================================
#   Case              N    CPM (h)   Best Serial  Best Parallel    Best GA   Best Known    GA-BK    Δ (h)
#   --------------------------------------------------------------------------------------------------
#   j12051_6        122     106.00        262.00         256.00     230.00       214.00    16.00    26.00
#   j12031_10       122      92.00        285.00         266.00     254.00       225.00    29.00    12.00
#   j12036_6        122     103.00        271.00         263.00     251.00       224.00    27.00    12.00
#   j12056_7        122     119.00        336.00         320.00     303.00       282.00    21.00    17.00
#   j12051_5        122     104.00        280.00         266.00     259.00       229.00    30.00     7.00
#   j12056_1        122      97.00        279.00         273.00     259.00       236.00    23.00    14.00
#   j12026_10       122     124.00        224.00         219.00     200.00       183.00    17.00    19.00
#   j12051_7        122      93.00        254.00         247.00     235.00       211.00    24.00    12.00
#   j12056_5        122     119.00        338.00         315.00     309.00       279.00    30.00     6.00
#   j12056_9        122     103.00        341.00         325.00     314.00       287.00    27.00    11.00


def parse_args() -> argparse.Namespace:
    """Parse command-line options for hard-instance runs."""
    parser = argparse.ArgumentParser(
        description="Run RCPSP GA on the hard j120 benchmark cases.",
    )
    parser.add_argument(
        "--case",
        choices=[name for name, _ in CASES],
        action="append",
        help="Run only the selected hard case. May be supplied more than once.",
    )
    parser.add_argument("--pop-size", type=int, default=50)
    parser.add_argument("--n-gen", type=int, default=500)
    parser.add_argument("--cxpb", type=float, default=0.9)
    parser.add_argument("--mutpb", type=float, default=0.5)
    parser.add_argument("--n-random", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose per-generation output.",
    )
    parser.add_argument(
        "--crossover",
        default="two_point",
        choices=list(RCPSPGeneticAlgorithm._CROSSOVER_METHODS),
    )
    parser.add_argument(
        "--mutation",
        default="consensus_reorder",
        choices=["swap", "adjacent_swap", "insertion_window", "consensus_reorder"],
    )
    parser.add_argument(
        "--replacement-strategy",
        default="diverse_elitist",
        choices=["generational", "elitist", "steady_state", "diverse_elitist"],
        help="Population update method applied after each offspring generation.",
    )
    parser.add_argument(
        "--elite-size",
        type=int,
        default=1,
        help="Minimum parent elites preserved by steady_state replacement.",
    )
    parser.add_argument(
        "--replacement-fraction",
        type=float,
        default=0.5,
        help="Population fraction replaced by offspring under steady_state.",
    )
    parser.add_argument(
        "--initial-population-mode",
        default="priority_rules",
        choices=list(RCPSPGeneticAlgorithm._INITIAL_POPULATION_MODES),
    )
    parser.add_argument(
        "--no-fb-improvement",
        action="store_true",
        help="Disable final Forward-Backward-Forward improvement.",
    )
    parser.add_argument("--fb-freq", type=int, default=0)
    return parser.parse_args()


def load_best_known_results() -> dict[str, float]:
    """Load PSPLIB best-known results keyed by `<instance>.sm`."""
    with BEST_RESULTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_best_known_result(best_results: dict[str, float], json_file: str) -> float | None:
    """Resolve the benchmark JSON filename to the corresponding PSPLIB result key."""
    instance_key = f"{Path(json_file).stem}.sm"
    return best_results.get(instance_key)


def run_ga_case(
    case_name: str,
    json_file: str,
    pop_size: int = 30,
    n_gen: int = 500,
    cxpb: float = 0.8,
    mutpb: float = 0.1,
    n_random=8,
    seed: int = 42,
    verbose: bool = True,
    crossover: str = 'two_point',
    mutation: str = 'adjacent_swap',
    replacement_strategy: str = 'diverse_elitist',
    elite_size: int = 1,
    replacement_fraction: float = 0.5,
    fb_improvement: bool = True,
    fb_freq: int = 0,
    initial_population_mode: str = 'priority_rules',
) -> dict:
    """
    Run the GA on a single PSPLIB benchmark case.

    Parameters
    ----------
    case_name : str
        Human-readable label (e.g. 'j12051_6').
    json_file : str
        Path to the input JSON relative to this file's directory.
    pop_size : int
        GA population size.
    n_gen : int
        Number of GA generations.
    cxpb : float
        Crossover probability.
    mutpb : float
        Per-individual mutation probability.
    seed : int
        RNG seed.
    verbose : bool
        Print per-generation stats and seeding table.
    crossover : str
        Crossover operator name passed to ``RCPSPGeneticAlgorithm``.
        One of ``'one_point'``, ``'two_point'``, ``'uniform_order'``,
        ``'decuple'``.
    mutation : str
        Mutation operator name passed to ``RCPSPGeneticAlgorithm``.
        One of ``'swap'``, ``'adjacent_swap'``, ``'insertion_window'``.
    replacement_strategy : str
        Population update strategy passed to ``RCPSPGeneticAlgorithm``.
        One of ``'generational'``, ``'elitist'``, ``'steady_state'``,
        ``'diverse_elitist'``.
    elite_size : int
        Minimum parent elites preserved by ``steady_state`` replacement.
    replacement_fraction : float
        Population fraction replaced by offspring under ``steady_state``.
    fb_improvement : bool
        Apply Forward-Backward-Forward local improvement as a final
        polishing step on the full population.  Default ``True``.
    fb_freq : int
        Also apply FBF every ``fb_freq`` generations during evolution
        (``0`` = only at the end).

    Returns
    -------
    dict with keys:
        case, n_activities, cpm_duration,
        best_serial_seed, best_parallel_seed, best_ga, improvement
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

    for rule in PRIORITY_RULES:
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

    best_serial = (
        min(serial_durations.values()) if serial_durations else float('inf')
    )
    best_parallel = (
        min(parallel_durations.values()) if parallel_durations else float('inf')
    )
    best_rule_overall = min(best_serial, best_parallel)

    if verbose:
        print("Priority rule baseline durations:")
        print(f"  {'Rule':<22} {'Serial (h)':>12} {'Parallel (h)':>14}")
        print("  " + "-" * 50)
        for rule in PRIORITY_RULES:
            s = serial_durations.get(rule, float('nan'))
            p = parallel_durations.get(rule, float('nan'))
            print(f"  {rule:<22} {s:>12.2f} {p:>14.2f}")
        print()

    # ── Run GA ───────────────────────────────────────────────────────────────
    print(
        "Running GA "
        f"(crossover={crossover!r}, mutation={mutation!r}, "
        f"replacement={replacement_strategy!r}, Serial SGS)..."
    )
    ga = RCPSPGeneticAlgorithm(
        pert,
        pop_size=pop_size,
        n_gen=n_gen,
        cxpb=cxpb,
        mutpb=mutpb,
        n_random=n_random,
        seed=seed,
        verbose=verbose,
        crossover=crossover,
        mutation=mutation,
        replacement_strategy=replacement_strategy,
        elite_size=elite_size,
        replacement_fraction=replacement_fraction,
        fb_improvement=fb_improvement,
        fb_freq=fb_freq,
        initial_population_mode=initial_population_mode,
    )
    hof, log = ga.run()

    # ── Best schedule from GA ─────────────────────────────────────────────────
    best_result = ga.get_best_schedule(hof)
    best_ga = best_result['scheduled_duration'] - 2

    summary = ga.get_convergence_summary(log)
    best_activity_list = ga.get_best_activity_list(hof)

    print()
    print(f"{'─' * 60}")
    print(f"  CPM duration (unconstrained)  : {cpm_duration:.2f} h")
    print(f"  Best serial SGS  (all rules)  : {best_serial:.2f} h")
    print(f"  Best parallel SGS (all rules) : {best_parallel:.2f} h")
    print(f"  Best GA duration              : {best_ga:.2f} h")
    print(f"  Improvement over best seed    : {best_rule_overall - best_ga:.2f} h")
    print(f"  GA improvement (gen0 → final) : {summary['improvement']:.2f} h")
    avg_std = f"{summary['final_avg']:.2f} / {summary['final_std']:.2f}"
    print(f"  Final avg / std               : {avg_std}")
    print(f"{'─' * 60}")
    first_ten = best_activity_list[:10]
    print(f"  Best activity list (first 10) : {first_ten}")
    print()

    return {
        'case': case_name,
        'n_activities': n_activities,
        'cpm_duration': cpm_duration,
        'best_serial_seed': best_serial,
        'best_parallel_seed': best_parallel,
        'best_ga': best_ga,
        'improvement': best_rule_overall - best_ga,
        'replacement_strategy': replacement_strategy,
    }


def main() -> None:
    """Run the GA on all hard j120 benchmark cases and print a summary table."""
    args = parse_args()
    best_results = load_best_known_results()
    results = []
    selected_cases = (
        [(name, path) for name, path in CASES if name in set(args.case)]
        if args.case
        else CASES
    )
    for case_name, json_file in selected_cases:
        result = run_ga_case(
            case_name=case_name,
            json_file=json_file,
            pop_size=args.pop_size,
            n_gen=args.n_gen,
            cxpb=args.cxpb,
            mutpb=args.mutpb,
            n_random=args.n_random,
            seed=args.seed,
            verbose=args.verbose,
            crossover=args.crossover,
            mutation=args.mutation,
            replacement_strategy=args.replacement_strategy,
            elite_size=args.elite_size,
            replacement_fraction=args.replacement_fraction,
            fb_improvement=not args.no_fb_improvement,
            fb_freq=args.fb_freq,
            initial_population_mode=args.initial_population_mode,
        )
        best_known = get_best_known_result(best_results, json_file)
        result['best_known'] = best_known
        result['ga_vs_best_known'] = (
            result['best_ga'] - best_known if best_known is not None else float('nan')
        )
        results.append(result)

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print(
        "SUMMARY — GA "
        f"(Activity List, {args.crossover} crossover, {args.mutation} mutation, "
        f"{args.replacement_strategy} replacement, Serial SGS)"
    )
    print("=" * 80)
    print(
        f"  {'Case':<12} {'N':>6} {'CPM (h)':>10} "
        f"{'Best Serial':>13} {'Best Parallel':>14} {'Best GA':>10} "
        f"{'Best Known':>12} {'GA-BK':>8} {'Δ (h)':>8}"
    )
    print("  " + "-" * 98)
    for r in results:
        print(
            f"  {r['case']:<12} {r['n_activities']:>6} {r['cpm_duration']:>10.2f} "
            f"{r['best_serial_seed']:>13.2f} {r['best_parallel_seed']:>14.2f} "
            f"{r['best_ga']:>10.2f} {r['best_known']:>12.2f} "
            f"{r['ga_vs_best_known']:>8.2f} {r['improvement']:>8.2f}"
        )
    print()


if __name__ == "__main__":
    main()
