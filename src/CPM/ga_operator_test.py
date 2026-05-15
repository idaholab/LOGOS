"""
ga_operator_test.py - Compare GA crossover/mutation operator combinations.

Run this script with one RCPSP JSON input.  It executes the standard GA once for
each selected crossover/mutation pair, plots all convergence curves in one figure,
and prints a comparison table sorted by final GA duration.

Usage from the repo root:
    python -m src.CPM.ga_operator_test src/CPM/j301_1.json

Usage from the src/CPM directory:
    python ga_operator_test.py j301_1.json

Examples:
    python -m src.CPM.ga_operator_test src/CPM/j301_1.json --n-gen 100
    python -m src.CPM.ga_operator_test j301_1.json --crossovers two_point --mutations swap,adjacent_swap
    python -m src.CPM.ga_operator_test j301_1.json --max-evals 5000 --no-fb-improvement
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any


# Ensure repo root is on the path before project imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import Pert  # noqa: E402
from src.CPM.ga import RCPSPGeneticAlgorithm, PRIORITY_RULES  # noqa: E402


logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

CPM_DIR = Path(__file__).parent
SCHEMA = CPM_DIR / "outage_schema.json"
BEST_RESULTS_PATH = CPM_DIR / "benchmarks" / "best_results.json"
DEFAULT_OUTPUT_DIR = CPM_DIR / "results" / "ga_operator_test"


def _available_crossovers() -> list[str]:
    return list(RCPSPGeneticAlgorithm._CROSSOVER_METHODS)


def _available_mutations() -> list[str]:
    return list(RCPSPGeneticAlgorithm._MUTATION_METHODS)


def _parse_name_list(raw: str | list[str], choices: list[str], label: str) -> list[str]:
    """Parse comma-separated or whitespace-separated operator names."""
    raw_values = [raw] if isinstance(raw, str) else raw
    if raw_values == ["all"]:
        return choices
    names: list[str] = []
    for value in raw_values:
        for chunk in value.split(","):
            names.extend(part.strip() for part in chunk.split() if part.strip())
    unknown = [name for name in names if name not in choices]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"Unknown {label}: {unknown}. Choose from {choices} or 'all'."
        )
    deduped: list[str] = []
    for name in names:
        if name not in deduped:
            deduped.append(name)
    if not deduped:
        raise argparse.ArgumentTypeError(f"At least one {label} is required.")
    return deduped


def _resolve_json_path(path: str | Path) -> Path:
    """Resolve JSON input from cwd, absolute path, or src/CPM-relative path."""
    candidate = Path(path).expanduser()
    if candidate.exists():
        return candidate.resolve()
    cpm_candidate = CPM_DIR / candidate
    if cpm_candidate.exists():
        return cpm_candidate.resolve()
    raise FileNotFoundError(f"Could not find JSON input: {path}")


def _safe_float(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "nan"
    return f"{value:.2f}"


def parse_args() -> argparse.Namespace:
    crossovers = _available_crossovers()
    mutations = _available_mutations()

    parser = argparse.ArgumentParser(
        description=(
            "Run all selected GA crossover/mutation combinations for one RCPSP "
            "JSON input and compare final results."
        )
    )
    parser.add_argument(
        "json_input",
        help="RCPSP JSON input path. Relative paths are checked against cwd and src/CPM.",
    )
    parser.add_argument(
        "--crossovers",
        nargs="+",
        default=["all"],
        help=f"Selected crossovers, or 'all'. Choices: {crossovers}.",
    )
    parser.add_argument(
        "--mutations",
        nargs="+",
        default=["all"],
        help=f"Selected mutations, or 'all'. Choices: {mutations}.",
    )
    parser.add_argument("--pop-size", type=int, default=50)
    parser.add_argument("--n-gen", type=int, default=100)
    parser.add_argument("--cxpb", type=float, default=0.9)
    parser.add_argument("--mutpb", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-random", type=int, default=8)
    parser.add_argument("--hof-size", type=int, default=5)
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
        "--consensus-update-freq",
        type=int,
        help=(
            "Generation interval for refreshing the consensus library when "
            "using consensus_reorder mutation. Omit for auto; use 0 to disable."
        ),
    )
    parser.add_argument(
        "--consensus-elite-frac",
        type=float,
        default=0.2,
        help="Fraction of the current population used in consensus refreshes.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable verbose GA output for each combination.",
    )
    parser.add_argument(
        "--no-fb-improvement",
        action="store_true",
        help="Disable final Forward-Backward-Forward improvement.",
    )
    parser.add_argument("--fb-freq", type=int, default=0)

    stopping = parser.add_argument_group("stopping criteria")
    stopping.add_argument("--target-fitness", type=float)
    stopping.add_argument(
        "--target-best-known",
        action="store_true",
        help="Use best-known result for the JSON filename as target_fitness.",
    )
    stopping.add_argument("--max-evals", type=int)
    stopping.add_argument("--stall-generations", type=int)
    stopping.add_argument("--stall-tolerance", type=float, default=1e-9)
    stopping.add_argument("--fitness-std-tol", type=float)
    stopping.add_argument("--std-generations", type=int, default=1)
    stopping.add_argument("--max-unique-schedules", type=int)

    output = parser.add_argument_group("output")
    output.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for generated comparison CSV and plot.",
    )
    output.add_argument(
        "--plot-file",
        type=Path,
        help="Path for combined convergence plot. Defaults under --output-dir.",
    )
    output.add_argument(
        "--csv-file",
        type=Path,
        help="Path for comparison CSV. Defaults under --output-dir.",
    )
    output.add_argument(
        "--no-plot",
        action="store_true",
        help="Skip convergence plot generation.",
    )
    output.add_argument(
        "--show",
        action="store_true",
        help="Show the convergence plot interactively after saving.",
    )

    args = parser.parse_args()
    if args.target_best_known and args.target_fitness is not None:
        parser.error("--target-best-known and --target-fitness are mutually exclusive.")

    try:
        args.crossovers = _parse_name_list(args.crossovers, crossovers, "crossover")
        args.mutations = _parse_name_list(args.mutations, mutations, "mutation")
        args.json_path = _resolve_json_path(args.json_input)
    except (argparse.ArgumentTypeError, FileNotFoundError) as exc:
        parser.error(str(exc))
    return args


def load_best_known_results() -> dict[str, float]:
    if not BEST_RESULTS_PATH.exists():
        return {}
    with BEST_RESULTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_best_known_result(best_results: dict[str, float], json_path: Path) -> float | None:
    return best_results.get(f"{json_path.stem}.sm")


def load_pert(json_path: Path) -> Any:
    pert = Pert.from_json_file(str(json_path), schema_path=str(SCHEMA))
    pert.generateInfo()
    return pert


def compute_priority_baseline(json_path: Path) -> dict[str, Any]:
    """Compute CPM and priority-rule baselines once for the input instance."""
    pert = load_pert(json_path)
    cpm_duration = pert.getProjectDuration()
    n_activities = len(pert.infoDict)
    serial_durations: dict[str, float] = {}
    parallel_durations: dict[str, float] = {}

    for rule in PRIORITY_RULES:
        try:
            pert.priorities = None
            s_out = pert.calculateSerialScheduleWithResources(priority_rule=rule)
            serial_durations[rule] = s_out["scheduled_duration"] - 2

            pert.priorities = None
            p_out = pert.calculateScheduleWithResources(
                sgs="max_use_res_ranked",
                priority_rule=rule,
            )
            parallel_durations[rule] = p_out["scheduled_duration"] - 2
        except Exception as exc:  # noqa: BLE001
            logger.debug("Rule '%s' skipped in baseline: %s", rule, exc)

    best_serial = min(serial_durations.values()) if serial_durations else float("inf")
    best_parallel = min(parallel_durations.values()) if parallel_durations else float("inf")
    return {
        "n_activities": n_activities,
        "cpm_duration": cpm_duration,
        "best_serial": best_serial,
        "best_parallel": best_parallel,
        "best_rule_overall": min(best_serial, best_parallel),
    }


def _best_series(log: Any) -> tuple[list[float], list[float]]:
    """Return generation and best-so-far series from a DEAP logbook."""
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


def run_operator_case(
    json_path: Path,
    crossover: str,
    mutation: str,
    args: argparse.Namespace,
    target_fitness: float | None,
    best_rule_overall: float,
    best_known: float | None,
) -> dict[str, Any]:
    """Run one crossover/mutation combination on a fresh Pert instance."""
    print(f"Running GA: crossover={crossover}, mutation={mutation}")
    pert = load_pert(json_path)
    ga = RCPSPGeneticAlgorithm(
        pert,
        pop_size=args.pop_size,
        n_gen=args.n_gen,
        cxpb=args.cxpb,
        mutpb=args.mutpb,
        seed=args.seed,
        n_random=args.n_random,
        hof_size=args.hof_size,
        verbose=not args.quiet,
        crossover=crossover,
        mutation=mutation,
        fb_improvement=not args.no_fb_improvement,
        fb_freq=args.fb_freq,
        initial_population_mode=args.initial_population_mode,
        target_fitness=target_fitness,
        max_evals=args.max_evals,
        stall_generations=args.stall_generations,
        stall_tolerance=args.stall_tolerance,
        fitness_std_tol=args.fitness_std_tol,
        std_generations=args.std_generations,
        max_unique_schedules=args.max_unique_schedules,
        consensus_update_freq=args.consensus_update_freq,
        consensus_elite_frac=args.consensus_elite_frac,
    )
    hof, log = ga.run()
    best_result = ga.get_best_schedule(hof)
    best_ga = best_result["scheduled_duration"] - 2
    summary = ga.get_convergence_summary(log)
    gens, best_curve = _best_series(log)

    return {
        "crossover": crossover,
        "mutation": mutation,
        "label": f"{crossover}/{mutation}",
        "best_ga": best_ga,
        "logged_best": summary["best_duration"],
        "initial_best": summary["initial_best"],
        "improvement_gen0": summary["improvement"],
        "improvement_vs_seed": best_rule_overall - best_ga,
        "best_known": best_known,
        "gap_to_best_known": (
            best_ga - best_known if best_known is not None else float("nan")
        ),
        "final_avg": summary["final_avg"],
        "final_std": summary["final_std"],
        "n_gen_executed": summary["n_gen"],
        "n_evals": summary["n_evals"],
        "n_unique_schedules": summary["n_unique_schedules"],
        "stop_reason": summary["stop_reason"],
        "gens": gens,
        "best_curve": best_curve,
    }


def plot_combined_convergence(
    results: list[dict[str, Any]],
    filename: Path,
    title: str,
    show: bool = False,
) -> None:
    """Plot best-so-far convergence curves for all operator combinations."""
    import matplotlib

    matplotlib.use("Agg", force=not show)
    import matplotlib.pyplot as plt

    filename.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for result in results:
        ax.plot(
            result["gens"],
            result["best_curve"],
            linewidth=1.6,
            label=result["label"],
        )
        if result["gens"] and result["best_curve"]:
            ax.scatter(
                result["gens"][-1],
                result["best_curve"][-1],
                s=18,
                zorder=3,
            )

    ax.set_title(title)
    ax.set_xlabel("Generation")
    ax.set_ylabel("Best-so-far duration (h)")
    ax.grid(True, linestyle=":", linewidth=0.8, alpha=0.7)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(filename, dpi=150, bbox_inches="tight")
    if show:
        plt.show()
    plt.close(fig)


def write_csv(results: list[dict[str, Any]], filename: Path) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank",
        "crossover",
        "mutation",
        "best_ga",
        "gap_to_best_known",
        "improvement_vs_seed",
        "initial_best",
        "logged_best",
        "improvement_gen0",
        "final_avg",
        "final_std",
        "n_gen_executed",
        "n_evals",
        "n_unique_schedules",
        "stop_reason",
    ]
    with filename.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rank, result in enumerate(results, start=1):
            row = {field: result.get(field) for field in fields}
            row["rank"] = rank
            writer.writerow(row)


def print_comparison(
    results: list[dict[str, Any]],
    baseline: dict[str, Any],
    best_known: float | None,
) -> None:
    print()
    print("=" * 118)
    print("GA OPERATOR COMPARISON")
    print("=" * 118)
    print(f"Activities        : {baseline['n_activities']}")
    print(f"CPM duration      : {baseline['cpm_duration']:.2f} h")
    print(f"Best serial seed  : {baseline['best_serial']:.2f} h")
    print(f"Best parallel seed: {baseline['best_parallel']:.2f} h")
    print(f"Best known        : {_safe_float(best_known)} h")
    print("-" * 118)
    print(
        f"{'Rank':>4} {'Crossover':<14} {'Mutation':<18} "
        f"{'Best':>8} {'BK Gap':>8} {'Seed Gap':>9} "
        f"{'Gen':>5} {'Evals':>8} {'Avg/Std':>17} {'Stop':>20}"
    )
    print("-" * 118)
    for rank, r in enumerate(results, start=1):
        avg_std = f"{r['final_avg']:.2f}/{r['final_std']:.2f}"
        print(
            f"{rank:>4} {r['crossover']:<14} {r['mutation']:<18} "
            f"{r['best_ga']:>8.2f} {_safe_float(r['gap_to_best_known']):>8} "
            f"{r['improvement_vs_seed']:>9.2f} {r['n_gen_executed']:>5} "
            f"{r['n_evals']:>8} {avg_std:>17} {r['stop_reason']:>20}"
        )
    print("-" * 118)
    best = results[0]
    print(
        "Best combination: "
        f"{best['crossover']} crossover + {best['mutation']} mutation "
        f"with final duration {best['best_ga']:.2f} h"
    )


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    best_results = load_best_known_results()
    best_known = get_best_known_result(best_results, args.json_path)
    target_fitness = best_known if args.target_best_known else args.target_fitness
    baseline = compute_priority_baseline(args.json_path)

    print("=" * 78)
    print(f"Input: {args.json_path}")
    print(f"Crossovers: {', '.join(args.crossovers)}")
    print(f"Mutations : {', '.join(args.mutations)}")
    print(f"Runs      : {len(args.crossovers) * len(args.mutations)}")
    print("=" * 78)

    results: list[dict[str, Any]] = []
    for crossover in args.crossovers:
        for mutation in args.mutations:
            result = run_operator_case(
                args.json_path,
                crossover,
                mutation,
                args,
                target_fitness,
                baseline["best_rule_overall"],
                best_known,
            )
            results.append(result)

    results.sort(
        key=lambda r: (
            r["best_ga"],
            r["n_evals"],
            r["crossover"],
            r["mutation"],
        )
    )

    stem = args.json_path.stem
    plot_file = args.plot_file or (
        args.output_dir / f"{stem}_operator_convergence_seed{args.seed}.png"
    )
    csv_file = args.csv_file or (
        args.output_dir / f"{stem}_operator_comparison_seed{args.seed}.csv"
    )

    if not args.no_plot:
        plot_combined_convergence(
            results,
            plot_file,
            title=f"GA Operator Convergence - {stem}",
            show=args.show,
        )
        print(f"Combined convergence plot: {plot_file}")

    write_csv(results, csv_file)
    print(f"Comparison CSV: {csv_file}")

    print_comparison(results, baseline, best_known)
    if not args.no_fb_improvement:
        print()
        print(
            "Note: convergence curves use the GA logbook. Final FBF polishing can "
            "improve the table's final duration after the last logged generation."
        )


if __name__ == "__main__":
    main()
