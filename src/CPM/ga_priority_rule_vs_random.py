"""
ga_priority_rule_vs_random.py - Compare priority-rule vs random GA initialization.

The GA supports:

* ``initial_population_mode='priority_rules'``: rank deterministic
  priority-rule serial/parallel seeds, keep the best 20% of ``pop_size``, then
  fill the rest randomly.
* ``initial_population_mode='random'``: fill the full initial population with
  random precedence-feasible chromosomes.

Usage from the repo root:
    python -m src.CPM.ga_priority_rule_vs_random src/CPM/j1201_1.json

Usage from the src/CPM directory:
    python ga_priority_rule_vs_random.py j1201_1.json

Examples:
    python -m src.CPM.ga_priority_rule_vs_random src/CPM/j1201_1.json --n-gen 100
    python -m src.CPM.ga_priority_rule_vs_random j1201_1.json --mutations all
    python -m src.CPM.ga_priority_rule_vs_random j1201_1.json --replacement-strategies elitist
    python -m src.CPM.ga_priority_rule_vs_random j1201_1.json --no-fb-improvement
    python -m src.CPM.ga_priority_rule_vs_random LPP_Json/LPP_1.json --target-best-known
    python -m src.CPM.ga_priority_rule_vs_random RG300_Json/RG300_1.json --rcplib-best-key LB-lit
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
RCPLIB_SOLUTIONS_PATH = CPM_DIR / "benchmarks" / "rcplib_solution_results.json"
DEFAULT_OUTPUT_DIR = CPM_DIR / "results" / "ga_priority_rule_vs_random"
FIXED_CROSSOVER = "decuple"
DEFAULT_INITIAL_POPULATION_MODES = ["priority_rules", "random"]
DEFAULT_REPLACEMENT_STRATEGIES = ["generational"]
DEFAULT_RCPLIB_BEST_KEY = "LB-lit"


def _available_mutations() -> list[str]:
    return list(RCPSPGeneticAlgorithm._MUTATION_METHODS)


def _available_replacement_strategies() -> list[str]:
    preferred_order = ["generational", "elitist", "steady_state", "diverse_elitist"]
    available = RCPSPGeneticAlgorithm._REPLACEMENT_STRATEGIES
    return [strategy for strategy in preferred_order if strategy in available]


def _parse_name_list(raw: str | list[str], choices: list[str], label: str) -> list[str]:
    """Parse comma-separated or whitespace-separated names."""
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
    """Resolve JSON input from cwd, src/CPM, or src/CPM/benchmarks."""
    candidate = Path(path).expanduser()
    if candidate.exists():
        return candidate.resolve()
    for base in (CPM_DIR, CPM_DIR / "benchmarks"):
        base_candidate = base / candidate
        if base_candidate.exists():
            return base_candidate.resolve()
    raise FileNotFoundError(f"Could not find JSON input: {path}")


def _safe_float(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "nan"
    return f"{value:.2f}"


def parse_args() -> argparse.Namespace:
    mutations = _available_mutations()
    replacement_strategies = _available_replacement_strategies()
    initial_modes = DEFAULT_INITIAL_POPULATION_MODES

    parser = argparse.ArgumentParser(
        description=(
            "Compare priority-rule seeded initialization vs pure random fill "
            f"with fixed {FIXED_CROSSOVER!r} crossover."
        )
    )
    parser.add_argument(
        "json_input",
        help="RCPSP JSON input path. Relative paths are checked against cwd and src/CPM.",
    )
    parser.add_argument(
        "--mutations",
        nargs="+",
        default=["all"],
        help=f"Selected mutations, or 'all'. Choices: {mutations}.",
    )
    parser.add_argument(
        "--initial-population-modes",
        nargs="+",
        default=initial_modes,
        help=(
            "Initialization modes to compare. Default: priority_rules random. "
            f"Choices: {initial_modes}."
        ),
    )
    parser.add_argument(
        "--replacement-strategies",
        nargs="+",
        default=DEFAULT_REPLACEMENT_STRATEGIES,
        help=(
            "Selected population replacement strategies, or 'all'. "
            "Default: generational. "
            f"Choices: {replacement_strategies}."
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
        default=0.9,
        help="Population fraction replaced by offspring under steady_state.",
    )
    parser.add_argument("--pop-size", type=int, default=50)
    parser.add_argument("--n-gen", type=int, default=100)
    parser.add_argument("--cxpb", type=float, default=0.8)
    parser.add_argument("--mutpb", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-random", type=int, default=10)
    parser.add_argument("--hof-size", type=int, default=10)
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
        help="Disable verbose GA output for each run.",
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

    references = parser.add_argument_group("best-known references")
    references.add_argument(
        "--best-known-source",
        choices=["auto", "psplib", "rcplib", "none"],
        default="auto",
        help=(
            "Reference source for best-known durations. 'auto' uses "
            "best_results.json for PSPLIB .sm instances and "
            "rcplib_solution_results.json for .rcp instances such as LPP/RG300."
        ),
    )
    references.add_argument(
        "--rcplib-solutions",
        type=Path,
        default=RCPLIB_SOLUTIONS_PATH,
        help=(
            "Path to rcplib_solution_results.json for LPP/RG300 references "
            f"(default: {RCPLIB_SOLUTIONS_PATH})."
        ),
    )
    references.add_argument(
        "--rcplib-best-key",
        default=DEFAULT_RCPLIB_BEST_KEY,
        help=(
            "Second-level key in rcplib_solution_results.json to use as the "
            f"best-known value for .rcp instances (default: {DEFAULT_RCPLIB_BEST_KEY})."
        ),
    )

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
        help="Path for full comparison CSV. Defaults under --output-dir.",
    )
    output.add_argument(
        "--delta-csv-file",
        type=Path,
        help=(
            "Path for paired priority_rules-vs-random delta CSV. "
            "Defaults under --output-dir."
        ),
    )
    output.add_argument(
        "--curve-csv-file",
        type=Path,
        help=(
            "Path for per-generation convergence CSV containing result['gens'] "
            "and result['best_curve']. Defaults under --output-dir."
        ),
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
        args.mutations = _parse_name_list(args.mutations, mutations, "mutation")
        args.initial_population_modes = _parse_name_list(
            args.initial_population_modes,
            initial_modes,
            "initial population mode",
        )
        args.replacement_strategies = _parse_name_list(
            args.replacement_strategies,
            replacement_strategies,
            "replacement strategy",
        )
        args.json_path = _resolve_json_path(args.json_input)
    except (argparse.ArgumentTypeError, FileNotFoundError) as exc:
        parser.error(str(exc))
    return args


def load_best_known_results() -> dict[str, float]:
    if not BEST_RESULTS_PATH.exists():
        return {}
    with BEST_RESULTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_rcplib_solution_results(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_best_known_result(
    best_results: dict[str, float],
    rcplib_results: dict[str, dict[str, Any]],
    json_path: Path,
    source: str,
    rcplib_best_key: str,
) -> tuple[float | None, str]:
    """Resolve the benchmark reference value for PSPLIB or RCPLIB JSON input."""
    psplib_key = f"{json_path.stem}.sm"
    rcplib_key = f"{json_path.stem}.rcp"

    if source in {"auto", "rcplib"} and rcplib_key in rcplib_results:
        entry = rcplib_results[rcplib_key]
        value = _to_float(entry.get(rcplib_best_key))
        if value is not None:
            return value, f"rcplib:{rcplib_key}:{rcplib_best_key}"
        if source == "rcplib":
            return None, f"rcplib:{rcplib_key}:{rcplib_best_key}:missing"

    if source in {"auto", "psplib"} and psplib_key in best_results:
        value = _to_float(best_results[psplib_key])
        if value is not None:
            return value, f"psplib:{psplib_key}"
        if source == "psplib":
            return None, f"psplib:{psplib_key}:missing"

    if source == "none":
        return None, "none"

    if source == "auto":
        return None, "auto:not-found"
    return None, f"{source}:not-found"


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


def run_case(
    json_path: Path,
    replacement_strategy: str,
    mutation: str,
    initial_population_mode: str,
    args: argparse.Namespace,
    target_fitness: float | None,
    best_rule_overall: float,
    best_known: float | None,
    best_known_source: str,
) -> dict[str, Any]:
    """Run one mutation/initialization-mode combination on a fresh Pert instance."""
    print(
        "Running GA: "
        f"crossover={FIXED_CROSSOVER}, replacement={replacement_strategy}, "
        f"mutation={mutation}, "
        f"initial_population_mode={initial_population_mode}"
    )
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
        crossover=FIXED_CROSSOVER,
        mutation=mutation,
        replacement_strategy=replacement_strategy,
        elite_size=args.elite_size,
        replacement_fraction=args.replacement_fraction,
        fb_improvement=not args.no_fb_improvement,
        fb_freq=args.fb_freq,
        initial_population_mode=initial_population_mode,
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
        "crossover": FIXED_CROSSOVER,
        "replacement_strategy": replacement_strategy,
        "elite_size": args.elite_size,
        "replacement_fraction": args.replacement_fraction,
        "mutation": mutation,
        "initial_population_mode": initial_population_mode,
        "label": f"{replacement_strategy}/{initial_population_mode}/{mutation}",
        "best_ga": best_ga,
        "logged_best": summary["best_duration"],
        "initial_best": summary["initial_best"],
        "improvement_gen0": summary["improvement"],
        "improvement_vs_seed": best_rule_overall - best_ga,
        "best_known": best_known,
        "best_known_source": best_known_source,
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


def build_delta_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build paired priority_rules-vs-random comparisons."""
    by_pair: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for result in results:
        key = (result["replacement_strategy"], result["mutation"])
        by_pair.setdefault(key, {})[result["initial_population_mode"]] = result

    rows: list[dict[str, Any]] = []
    for replacement_strategy in _available_replacement_strategies():
        for mutation in _available_mutations():
            pair = by_pair.get((replacement_strategy, mutation), {})
            priority = pair.get("priority_rules")
            random_result = pair.get("random")
            if priority is None or random_result is None:
                continue
            delta = random_result["best_ga"] - priority["best_ga"]
            if delta > 0:
                winner = "priority_rules"
            elif delta < 0:
                winner = "random"
            else:
                winner = "tie"
            rows.append(
                {
                    "replacement_strategy": replacement_strategy,
                    "mutation": mutation,
                    "priority_best_ga": priority["best_ga"],
                    "random_best_ga": random_result["best_ga"],
                    "delta_random_minus_priority": delta,
                    "winner": winner,
                    "priority_gap_to_best_known": priority["gap_to_best_known"],
                    "random_gap_to_best_known": random_result["gap_to_best_known"],
                    "priority_initial_best": priority["initial_best"],
                    "random_initial_best": random_result["initial_best"],
                    "priority_n_evals": priority["n_evals"],
                    "random_n_evals": random_result["n_evals"],
                    "priority_stop_reason": priority["stop_reason"],
                    "random_stop_reason": random_result["stop_reason"],
                }
            )
    rows.sort(key=lambda row: abs(row["delta_random_minus_priority"]), reverse=True)
    return rows


def plot_combined_convergence(
    results: list[dict[str, Any]],
    filename: Path,
    title: str,
    show: bool = False,
) -> None:
    """Plot best-so-far convergence curves for all runs."""
    import matplotlib

    matplotlib.use("Agg", force=not show)
    import matplotlib.pyplot as plt

    filename.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for result in results:
        linestyle = (
            "-"
            if result["initial_population_mode"] == "priority_rules"
            else "--"
        )
        ax.plot(
            result["gens"],
            result["best_curve"],
            linestyle=linestyle,
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
        "replacement_strategy",
        "elite_size",
        "replacement_fraction",
        "mutation",
        "initial_population_mode",
        "best_ga",
        "best_known",
        "best_known_source",
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


def write_delta_csv(delta_rows: list[dict[str, Any]], filename: Path) -> None:
    filename.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "replacement_strategy",
        "mutation",
        "priority_best_ga",
        "random_best_ga",
        "delta_random_minus_priority",
        "winner",
        "priority_gap_to_best_known",
        "random_gap_to_best_known",
        "priority_initial_best",
        "random_initial_best",
        "priority_n_evals",
        "random_n_evals",
        "priority_stop_reason",
        "random_stop_reason",
    ]
    with filename.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(delta_rows)


def write_curve_csv(results: list[dict[str, Any]], filename: Path) -> None:
    """Write one row per run/generation from result['gens'] and result['best_curve']."""
    filename.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "run_rank",
        "crossover",
        "replacement_strategy",
        "elite_size",
        "replacement_fraction",
        "mutation",
        "initial_population_mode",
        "label",
        "point_index",
        "gen",
        "best_curve",
        "best_ga",
        "best_known",
        "gap_to_best_known",
        "stop_reason",
    ]
    with filename.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for run_rank, result in enumerate(results, start=1):
            gens = result.get("gens") or []
            best_curve = result.get("best_curve") or []
            for point_index, (gen, best_value) in enumerate(
                zip(gens, best_curve),
                start=1,
            ):
                writer.writerow(
                    {
                        "run_rank": run_rank,
                        "crossover": result.get("crossover"),
                        "replacement_strategy": result.get("replacement_strategy"),
                        "elite_size": result.get("elite_size"),
                        "replacement_fraction": result.get("replacement_fraction"),
                        "mutation": result.get("mutation"),
                        "initial_population_mode": result.get(
                            "initial_population_mode"
                        ),
                        "label": result.get("label"),
                        "point_index": point_index,
                        "gen": gen,
                        "best_curve": best_value,
                        "best_ga": result.get("best_ga"),
                        "best_known": result.get("best_known"),
                        "gap_to_best_known": result.get("gap_to_best_known"),
                        "stop_reason": result.get("stop_reason"),
                    }
                )


def print_full_comparison(
    results: list[dict[str, Any]],
    baseline: dict[str, Any],
    best_known: float | None,
    best_known_source: str,
) -> None:
    print()
    print("=" * 146)
    print("GA INITIAL POPULATION COMPARISON")
    print("=" * 146)
    print(f"Activities        : {baseline['n_activities']}")
    print(f"CPM duration      : {baseline['cpm_duration']:.2f} h")
    print(f"Best serial seed  : {baseline['best_serial']:.2f} h")
    print(f"Best parallel seed: {baseline['best_parallel']:.2f} h")
    print(f"Best known        : {_safe_float(best_known)} h ({best_known_source})")
    print(f"Crossover         : {FIXED_CROSSOVER}")
    print("-" * 146)
    print(
        f"{'Rank':>4} {'Replacement':<16} {'Init Mode':<10} {'Mutation':<18} "
        f"{'Best':>8} {'BK Gap':>8} {'Seed Gap':>9} "
        f"{'Initial':>8} {'Gen':>5} {'Evals':>8} {'Avg/Std':>17} {'Stop':>20}"
    )
    print("-" * 146)
    for rank, r in enumerate(results, start=1):
        avg_std = f"{r['final_avg']:.2f}/{r['final_std']:.2f}"
        print(
            f"{rank:>4} {r['replacement_strategy']:<16} "
            f"{r['initial_population_mode']:<10} {r['mutation']:<18} "
            f"{r['best_ga']:>8.2f} {_safe_float(r['gap_to_best_known']):>8} "
            f"{r['improvement_vs_seed']:>9.2f} {r['initial_best']:>8.2f} "
            f"{r['n_gen_executed']:>5} {r['n_evals']:>8} "
            f"{avg_std:>17} {r['stop_reason']:>20}"
        )
    print("-" * 146)
    best = results[0]
    print(
        "Best run: "
        f"{best['replacement_strategy']} replacement + "
        f"{best['initial_population_mode']} initialization + "
        f"{best['mutation']} mutation with final duration "
        f"{best['best_ga']:.2f} h"
    )


def print_delta_comparison(delta_rows: list[dict[str, Any]]) -> None:
    if not delta_rows:
        return

    print()
    print("=" * 112)
    print("PRIORITY_RULES VS RANDOM BY REPLACEMENT STRATEGY AND MUTATION")
    print("=" * 112)
    print(
        f"{'Replacement':<16} {'Mutation':<18} {'Priority':>10} {'Random':>8} "
        f"{'Random-Priority':>16} {'Winner':>14} "
        f"{'Priority Init':>13} {'Random Init':>12}"
    )
    print("-" * 112)
    for row in delta_rows:
        print(
            f"{row['replacement_strategy']:<16} "
            f"{row['mutation']:<18} "
            f"{row['priority_best_ga']:>10.2f} "
            f"{row['random_best_ga']:>8.2f} "
            f"{row['delta_random_minus_priority']:>16.2f} "
            f"{row['winner']:>14} "
            f"{row['priority_initial_best']:>13.2f} "
            f"{row['random_initial_best']:>12.2f}"
        )
    print("-" * 112)
    print(
        "Positive Random-Priority means priority_rules initialization produced "
        "a shorter schedule."
    )


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    best_results = load_best_known_results()
    rcplib_results = load_rcplib_solution_results(args.rcplib_solutions)
    best_known, best_known_source = get_best_known_result(
        best_results,
        rcplib_results,
        args.json_path,
        args.best_known_source,
        args.rcplib_best_key,
    )
    target_fitness = best_known if args.target_best_known else args.target_fitness
    baseline = compute_priority_baseline(args.json_path)

    print("=" * 84)
    print(f"Input                    : {args.json_path}")
    print(f"Crossover                : {FIXED_CROSSOVER}")
    print(f"Replacement strategies   : {', '.join(args.replacement_strategies)}")
    print(f"Mutations                : {', '.join(args.mutations)}")
    print(f"Initial population modes : {', '.join(args.initial_population_modes)}")
    print(f"Best-known source        : {best_known_source}")
    print(f"Best-known value         : {_safe_float(best_known)}")
    print(
        "Runs                     : "
        f"{len(args.replacement_strategies) * len(args.mutations) * len(args.initial_population_modes)}"
    )
    print("=" * 84)

    results: list[dict[str, Any]] = []
    for replacement_strategy in args.replacement_strategies:
        for mutation in args.mutations:
            for mode in args.initial_population_modes:
                result = run_case(
                    args.json_path,
                    replacement_strategy,
                    mutation,
                    mode,
                    args,
                    target_fitness,
                    baseline["best_rule_overall"],
                    best_known,
                    best_known_source,
                )
                results.append(result)

    results.sort(
        key=lambda r: (
            r["best_ga"],
            r["n_evals"],
            r["replacement_strategy"],
            r["mutation"],
            r["initial_population_mode"],
        )
    )
    delta_rows = build_delta_rows(results)

    stem = args.json_path.stem
    replacement_suffix = ""
    if args.replacement_strategies != DEFAULT_REPLACEMENT_STRATEGIES:
        if len(args.replacement_strategies) == 1:
            replacement_suffix = f"_{args.replacement_strategies[0]}"
        else:
            replacement_suffix = "_replacement_multi"
    plot_file = args.plot_file or (
        args.output_dir / f"{stem}_priority_vs_random{replacement_suffix}_convergence_seed{args.seed}.png"
    )
    csv_file = args.csv_file or (
        args.output_dir / f"{stem}_priority_vs_random{replacement_suffix}_comparison_seed{args.seed}.csv"
    )
    delta_csv_file = args.delta_csv_file or (
        args.output_dir / f"{stem}_priority_vs_random{replacement_suffix}_delta_seed{args.seed}.csv"
    )
    curve_csv_file = args.curve_csv_file or (
        args.output_dir / f"{stem}_priority_vs_random{replacement_suffix}_curves_seed{args.seed}.csv"
    )

    if not args.no_plot:
        plot_combined_convergence(
            results,
            plot_file,
            title=f"GA Initial Population Comparison - {stem}",
            show=args.show,
        )
        print(f"Combined convergence plot: {plot_file}")

    write_csv(results, csv_file)
    print(f"Comparison CSV: {csv_file}")
    write_delta_csv(delta_rows, delta_csv_file)
    print(f"Mixed-vs-random delta CSV: {delta_csv_file}")
    write_curve_csv(results, curve_csv_file)
    print(f"Convergence curves CSV: {curve_csv_file}")

    print_full_comparison(results, baseline, best_known, best_known_source)
    print_delta_comparison(delta_rows)
    if not args.no_fb_improvement:
        print()
        print(
            "Note: convergence curves use the GA logbook. Final FBF polishing can "
            "improve the table's final duration after the last logged generation."
        )


if __name__ == "__main__":
    main()
