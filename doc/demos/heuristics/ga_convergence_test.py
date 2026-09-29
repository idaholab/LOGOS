"""
ga_convergence_test.py — Compare GA convergence across replacement strategies.

For each PSPLIB benchmark case, the GA is run once per replacement strategy
(default: ``generational`` and ``diverse_elitist``) and the best-so-far
curves are overlaid on a single convergence plot. The plot also draws a
horizontal reference line at the PSPLIB best-known duration so each curve's
remaining gap is visible at a glance.

The GA uses the Activity List representation with configurable crossover
and mutation operators. Decoding is performed by the Serial SGS.
Default operators: two-point crossover, adjacent-swap mutation.

Reference
---------
Kolisch, R. and Hartmann, S. (1999). Heuristic Algorithms for Solving the
Resource-Constrained Project Scheduling Problem. In J. Weglarz (ed.),
Project Scheduling: Recent Models, Algorithms and Applications, 147-178.

Usage (from the repository root):
    python doc/demos/heuristics/ga_convergence_test.py

Examples:
    python doc/demos/heuristics/ga_convergence_test.py --case j12051_6
    python doc/demos/heuristics/ga_convergence_test.py \\
        --replacement-strategies generational \\
        --replacement-strategies diverse_elitist \\
        --replacement-strategies elitist
    python doc/demos/heuristics/ga_convergence_test.py --target-best-known
"""

import argparse
import logging
import json
import sys
from pathlib import Path


# Ensure repo root is on the path before project imports
REPO_ROOT = Path(__file__).resolve().parents[3]
CPM_DIR = REPO_ROOT / "src" / "CPM"
sys.path.insert(0, str(REPO_ROOT))

from src import Pert  # noqa: E402
from src.CPM.ga import RCPSPGeneticAlgorithm, PRIORITY_RULES  # noqa: E402
from doc.demos.heuristics._case_selection import (  # noqa: E402
    add_case_argument,
    select_cases,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
SCHEMA = (CPM_DIR / "outage_schema.json").resolve()
BEST_RESULTS_PATH = (
    REPO_ROOT / "doc" / "demos" / "benchmarks" / "best_results.json"
).resolve()
BENCHMARK_DIR = (REPO_ROOT / "doc" / "demos" / "benchmarks").resolve()
DEFAULT_PLOT_DIR = (
    Path(__file__).parent / "results" / "ga_convergence_high_mut_all_rules"
)


CASES = [
    ("j12051_6", "j12051_6.json"),
    ("j12031_10", "j12031_10.json"),
    ("j12036_6", "j12036_6.json"),
    ("j12056_7", "j12056_7.json"),
    ("j12051_5", "j12051_5.json"),
    ("j12056_1", "j12056_1.json"),
    ("j12026_10", "j12026_10.json"),
    ("j12051_7", "j12051_7.json"),
    ("j12056_5", "j12056_5.json"),
    ("j12056_9", "j12056_9.json"),
]

def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for integration runs."""
    parser = argparse.ArgumentParser(
        description="Run RCPSP GA benchmark cases with optional stopping criteria.",
    )
    add_case_argument(parser, CASES)
    parser.add_argument("--pop-size", type=int, default=50)
    parser.add_argument("--n-gen", type=int, default=150)
    parser.add_argument("--cxpb", type=float, default=0.1)
    parser.add_argument("--mutpb", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--initial-population-mode",
        choices=list(RCPSPGeneticAlgorithm._INITIAL_POPULATION_MODES),
        default="priority_rules",
        help=(
            "Initial GA population source: priority_rules uses best "
            "serial/parallel priority-rule seeds plus random fill; random uses "
            "pure random fill."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable verbose per-generation output.",
    )
    parser.add_argument(
        "--crossover",
        default="uniform_order",
        choices=list(RCPSPGeneticAlgorithm._CROSSOVER_METHODS),
    )
    parser.add_argument(
        "--mutation",
        default="insertion_window",
        choices=["swap", "adjacent_swap", "insertion_window"],
    )
    parser.add_argument(
        "--replacement-strategies",
        action="append",
        choices=["generational", "elitist", "steady_state", "diverse_elitist"],
        help=(
            "Replacement strategy to include in the convergence comparison. "
            "May be supplied more than once. "
            "Default: generational + diverse_elitist."
        ),
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
        "--no-fb-improvement",
        action="store_true",
        help="Disable final Forward-Backward-Forward improvement.",
    )
    parser.add_argument("--fb-freq", type=int, default=0)
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Do not save convergence plots.",
    )
    parser.add_argument(
        "--plot-dir",
        type=Path,
        default=DEFAULT_PLOT_DIR,
        help="Directory for convergence plots.",
    )

    stopping = parser.add_argument_group("stopping criteria")
    stopping.add_argument(
        "--target-fitness",
        type=float,
        help="Stop when GA best fitness/duration is <= this value.",
    )
    stopping.add_argument(
        "--target-best-known",
        action="store_true",
        help="Use each case's PSPLIB best-known result as target_fitness.",
    )
    stopping.add_argument(
        "--max-evals",
        type=int,
        help="Stop before a generation would exceed this many GA evaluations.",
    )
    stopping.add_argument(
        "--stall-generations",
        type=int,
        help="Stop after this many generations without best-fitness improvement.",
    )
    stopping.add_argument(
        "--stall-tolerance",
        type=float,
        default=1e-9,
        help="Minimum improvement needed to reset the stall counter.",
    )
    stopping.add_argument(
        "--fitness-std-tol",
        type=float,
        help="Stop when population fitness std stays at or below this value.",
    )
    stopping.add_argument(
        "--std-generations",
        type=int,
        default=1,
        help="Consecutive low-std generations needed for --fitness-std-tol.",
    )
    stopping.add_argument(
        "--max-unique-schedules",
        type=int,
        help="Stop after observing this many unique decoded schedules.",
    )

    args = parser.parse_args(args)
    if args.target_best_known and args.target_fitness is not None:
        parser.error("--target-best-known and --target-fitness are mutually exclusive.")
    return args


def load_best_known_results() -> dict[str, float]:
    """Load PSPLIB best-known results keyed by `<instance>.sm`."""
    with BEST_RESULTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_best_known_result(best_results: dict[str, float], json_file: str) -> float | None:
    """Resolve the benchmark JSON filename to the corresponding PSPLIB result key."""
    instance_key = f"{Path(json_file).stem}.sm"
    return best_results.get(instance_key)


def _best_series(log) -> tuple[list[float], list[float]]:
    """Return (gens, best-so-far) series from a DEAP logbook."""
    records = list(log)
    gens = [float(row["gen"]) for row in records]
    if records and "best" in records[0]:
        best = [float(row["best"]) for row in records]
    else:
        best = []
        current = float("inf")
        for row in records:
            current = min(current, float(row["min"]))
            best.append(current)
    return gens, best


def _avg_std_series(log) -> tuple[list[float], list[float]]:
    """Return (population avg, population std) series from a DEAP logbook.

    Returns empty lists if the log does not record avg/std stats.
    """
    records = list(log)
    if not records or "avg" not in records[0] or "std" not in records[0]:
        return [], []
    avg = [float(row["avg"]) for row in records]
    std = [float(row["std"]) for row in records]
    return avg, std


# Fixed color per replacement strategy so the same strategy always reads as
# the same line across cases.
STRATEGY_COLORS: dict[str, str] = {
    "generational": "#1f77b4",
    "elitist": "#ff7f0e",
    "steady_state": "#2ca02c",
    "diverse_elitist": "#d62728",
}


def plot_strategy_comparison(
    case_name: str,
    strategy_results: list[dict],
    best_known: float | None,
    out_path: Path,
) -> None:
    """Overlay best-so-far curves for each replacement strategy on one figure."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 6.5))
    for result in strategy_results:
        color = STRATEGY_COLORS.get(result["replacement_strategy"], None)
        gens = result["gens"]
        avg = result.get("avg_curve") or []
        std = result.get("std_curve") or []
        if avg and std and len(avg) == len(gens):
            lower = [a - s for a, s in zip(avg, std)]
            upper = [a + s for a, s in zip(avg, std)]
            ax.fill_between(
                gens,
                lower,
                upper,
                color=color,
                alpha=0.15,
                linewidth=0,
                label=f"{result['replacement_strategy']} pop avg ± 1σ",
            )
            ax.plot(
                gens,
                avg,
                color=color,
                linestyle=":",
                linewidth=1.2,
                alpha=0.8,
            )
        ax.plot(
            gens,
            result["best_curve"],
            color=color,
            linestyle="-",
            linewidth=1.8,
            label=(
                f"{result['replacement_strategy']} best-so-far "
                f"(final={result['best_ga']:.0f})"
            ),
        )

    if best_known is not None:
        ax.axhline(
            best_known,
            color="black",
            linestyle="--",
            linewidth=1.2,
            alpha=0.7,
            label=f"best-known ({best_known:.0f})",
        )

    ax.set_title(f"{case_name}: GA convergence — replacement strategy comparison")
    ax.set_xlabel("Generation")
    ax.set_ylabel("Best-so-far duration (h)")
    ax.grid(True, linestyle=":", linewidth=0.8, alpha=0.7)
    ax.legend(fontsize=9, loc="best")
    fig.tight_layout()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def run_ga_case(
    case_name: str,
    json_file: str,
    pop_size: int = 30,
    n_gen: int = 500,
    cxpb: float = 0.8,
    mutpb: float = 0.1,
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
    plot_convergence: bool = True,
    plot_dir: str | Path | None = None,
    target_fitness: float | None = None,
    max_evals: int | None = None,
    stall_generations: int | None = None,
    stall_tolerance: float = 1e-9,
    fitness_std_tol: float | None = None,
    std_generations: int = 1,
    max_unique_schedules: int | None = None,
) -> dict:
    """
    Run the GA on a single PSPLIB benchmark case.

    Steps
    -----
    1. Load and initialise the Pert model.
    2. Record baseline durations for every named priority rule under both
       serial and parallel SGS (these are the same evaluations used to seed
       the GA's initial population).
    3. Run the GA (Activity List representation, configurable crossover and
       mutation operators, serial SGS decoder).
    4. Print per-case results and return a summary dict.

    Parameters
    ----------
    case_name : str
        Human-readable label (e.g. 'j30').
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
    initial_population_mode : str
        Initial GA population source: ``'priority_rules'`` or ``'random'``.
    plot_convergence : bool
        Save a convergence plot for the GA logbook returned by ``run()``.
    plot_dir : str or Path, optional
        Directory for convergence plots.  Defaults to
        ``src/CPM/results/ga_convergence``.
    target_fitness : float, optional
        Stop when the GA best fitness/duration is at or below this value.
    max_evals : int, optional
        Stop before starting a generation that would exceed this evaluation
        budget.
    stall_generations : int, optional
        Stop after this many generations without best-fitness improvement.
    stall_tolerance : float
        Minimum best-fitness decrease required to reset the stall counter.
    fitness_std_tol : float, optional
        Stop when population fitness standard deviation stays at or below this
        threshold for ``std_generations`` generations.
    std_generations : int
        Consecutive low-variance generations required by ``fitness_std_tol``.
    max_unique_schedules : int, optional
        Stop after observing this many unique decoded schedules.

    Returns
    -------
    dict with keys:
        case, n_activities, cpm_duration,
        best_serial_seed, best_parallel_seed, best_ga, improvement
    """
    json_path = BENCHMARK_DIR / json_file

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
        target_fitness=target_fitness,
        max_evals=max_evals,
        stall_generations=stall_generations,
        stall_tolerance=stall_tolerance,
        fitness_std_tol=fitness_std_tol,
        std_generations=std_generations,
        max_unique_schedules=max_unique_schedules,
    )
    hof, log = ga.run()

    plot_path = None
    if plot_convergence:
        out_dir = Path(plot_dir) if plot_dir is not None else DEFAULT_PLOT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        plot_path = (
            out_dir
            / (
                f"{case_name}_ga_convergence_{crossover}_{mutation}_"
                f"{replacement_strategy}_seed{seed}.png"
            )
        )
        try:
            import matplotlib

            matplotlib.use('Agg', force=True)
            fig, _ = ga.plot_convergence(
                log,
                filename=str(plot_path),
                show=False,
                title=f"GA Convergence - {case_name}",
            )
            import matplotlib.pyplot as plt

            plt.close(fig)
            print(f"Convergence plot: {plot_path}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not save convergence plot for %s: %s",
                case_name,
                exc,
            )
            plot_path = None

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
    print(f"  Stop reason                   : {summary['stop_reason']}")
    print(f"  Generations executed          : {summary['n_gen']} / {n_gen}")
    print(f"  GA evaluations                : {summary['n_evals']}")
    if max_unique_schedules is not None:
        print(f"  Unique schedules              : {summary['n_unique_schedules']}")
    avg_std = f"{summary['final_avg']:.2f} / {summary['final_std']:.2f}"
    print(f"  Final avg / std               : {avg_std}")
    print(f"{'─' * 60}")
    first_ten = best_activity_list[:10]
    print(f"  Best activity list (first 10) : {first_ten}")
    print()

    gens, best_curve = _best_series(log)
    avg_curve, std_curve = _avg_std_series(log)

    return {
        'case': case_name,
        'n_activities': n_activities,
        'cpm_duration': cpm_duration,
        'best_serial_seed': best_serial,
        'best_parallel_seed': best_parallel,
        'best_ga': best_ga,
        'improvement': best_rule_overall - best_ga,
        'replacement_strategy': replacement_strategy,
        'convergence_plot': str(plot_path) if plot_path else None,
        'stop_reason': summary['stop_reason'],
        'n_gen_executed': summary['n_gen'],
        'n_evals': summary['n_evals'],
        'n_unique_schedules': summary['n_unique_schedules'],
        'gens': gens,
        'best_curve': best_curve,
        'avg_curve': avg_curve,
        'std_curve': std_curve,
    }


def main() -> None:
    """Run the GA on all benchmark cases and print a summary table."""
    args = parse_args()
    best_results = load_best_known_results()
    strategies = args.replacement_strategies or ["generational", "diverse_elitist"]
    selected_cases = select_cases(CASES, args.case)
    plot_dir = Path(args.plot_dir) if args.plot_dir is not None else DEFAULT_PLOT_DIR

    results: list[dict] = []
    for case_name, json_file in selected_cases:
        best_known = get_best_known_result(best_results, json_file)
        target_fitness = best_known if args.target_best_known else args.target_fitness
        per_strategy: list[dict] = []
        for strategy in strategies:
            result = run_ga_case(
                case_name=case_name,
                json_file=json_file,
                pop_size=args.pop_size,
                n_gen=args.n_gen,
                cxpb=args.cxpb,
                mutpb=args.mutpb,
                seed=args.seed,
                verbose=not args.quiet,
                crossover=args.crossover,
                mutation=args.mutation,
                replacement_strategy=strategy,
                elite_size=args.elite_size,
                replacement_fraction=args.replacement_fraction,
                fb_improvement=not args.no_fb_improvement,
                fb_freq=args.fb_freq,
                initial_population_mode=args.initial_population_mode,
                plot_convergence=False,
                plot_dir=plot_dir,
                target_fitness=target_fitness,
                max_evals=args.max_evals,
                stall_generations=args.stall_generations,
                stall_tolerance=args.stall_tolerance,
                fitness_std_tol=args.fitness_std_tol,
                std_generations=args.std_generations,
                max_unique_schedules=args.max_unique_schedules,
            )
            result['best_known'] = best_known
            result['ga_vs_best_known'] = (
                result['best_ga'] - best_known
                if best_known is not None
                else float('nan')
            )
            per_strategy.append(result)
            results.append(result)

        if not args.no_plot and per_strategy:
            out_path = (
                plot_dir
                / f"{case_name}_strategy_compare_seed{args.seed}.png"
            )
            plot_strategy_comparison(case_name, per_strategy, best_known, out_path)
            print(f"Strategy comparison plot: {out_path}")

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print(
        "SUMMARY — GA "
        f"(Activity List, {args.crossover} crossover, "
        f"{args.mutation} mutation, Serial SGS) — "
        f"strategies: {', '.join(strategies)}"
    )
    print("=" * 80)
    print(
        f"  {'Case':<10} {'Strategy':<18} {'N':>5} {'CPM':>8} "
        f"{'BestGA':>9} {'BestKnown':>10} {'GA-BK':>8} {'Δ seed':>8} "
        f"{'Gen':>5} {'Evals':>7} {'Stop':>20}"
    )
    print("  " + "-" * 120)
    for r in results:
        print(
            f"  {r['case']:<10} {r['replacement_strategy']:<18} "
            f"{r['n_activities']:>5} {r['cpm_duration']:>8.2f} "
            f"{r['best_ga']:>9.2f} {r['best_known']:>10.2f} "
            f"{r['ga_vs_best_known']:>8.2f} {r['improvement']:>8.2f} "
            f"{r['n_gen_executed']:>5} {r['n_evals']:>7} "
            f"{r['stop_reason']:>20}"
        )
    print()


if __name__ == "__main__":
    main()
