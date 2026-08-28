"""
gans_priority_rule_vs_random.py - Compare GANS initialization modes.

The GANS solver supports:

* ``initial_population_mode='priority_rules'``: rank deterministic
  priority-rule serial/parallel seeds, keep the best 20% of ``pop_size``, then
  fill the rest randomly.
* ``initial_population_mode='random'``: fill the full initial population with
  random precedence-feasible activity lists.

By default this module runs the same hard PSPLIB j120 cases used by
``gans_test_hard.py``.  Explicit JSON inputs can also be supplied.

Usage from the repo root:
    python -m src.CPM.gans_priority_rule_vs_random
    python -m src.CPM.gans_priority_rule_vs_random --cases j12051_6
    python -m src.CPM.gans_priority_rule_vs_random src/CPM/j1201_1.json

Usage from the src/CPM directory:
    python gans_priority_rule_vs_random.py --cases j12051_6
    python gans_priority_rule_vs_random.py j1201_1.json
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


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import Pert  # noqa: E402
from src.CPM.gans import RCPSPHybridGANS, PRIORITY_RULES  # noqa: E402


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
DEFAULT_OUTPUT_DIR = CPM_DIR / "results" / "gans_priority_rule_vs_random"
DEFAULT_INITIAL_POPULATION_MODES = ["priority_rules", "random"]
DEFAULT_RCPLIB_BEST_KEY = "LB-lit"

DEFAULT_HARD_CASES = [
    ("j12051_6", "benchmarks/PSPLIB_Json/j120/j12051_6.json"),
    ("j12031_10", "benchmarks/PSPLIB_Json/j120/j12031_10.json"),
    ("j12036_6", "benchmarks/PSPLIB_Json/j120/j12036_6.json"),
    ("j12056_7", "benchmarks/PSPLIB_Json/j120/j12056_7.json"),
    ("j12051_5", "benchmarks/PSPLIB_Json/j120/j12051_5.json"),
    ("j12056_1", "benchmarks/PSPLIB_Json/j120/j12056_1.json"),
    ("j12026_10", "benchmarks/PSPLIB_Json/j120/j12026_10.json"),
    ("j12051_7", "benchmarks/PSPLIB_Json/j120/j12051_7.json"),
    ("j12056_5", "benchmarks/PSPLIB_Json/j120/j12056_5.json"),
    ("j12056_9", "benchmarks/PSPLIB_Json/j120/j12056_9.json"),
]


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
    hard_case_names = [name for name, _ in DEFAULT_HARD_CASES]

    parser = argparse.ArgumentParser(
        description=(
            "Compare GANS priority-rule seeded initialization against pure "
            "random initialization."
        )
    )
    parser.add_argument(
        "json_inputs",
        nargs="*",
        help=(
            "Optional RCPSP JSON input paths. If omitted, selected hard j120 "
            "cases from gans_test_hard.py are used."
        ),
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        default=["all"],
        help=(
            "Hard-case names to run when no JSON inputs are supplied, or 'all'. "
            f"Choices: {hard_case_names}."
        ),
    )
    parser.add_argument(
        "--initial-population-modes",
        nargs="+",
        default=DEFAULT_INITIAL_POPULATION_MODES,
        help=(
            "Initialization modes to compare. Default: priority_rules random. "
            f"Choices: {DEFAULT_INITIAL_POPULATION_MODES}."
        ),
    )
    parser.add_argument("--pop-size", type=int, default=50)
    parser.add_argument("--lambda-max", type=int, default=5000)
    parser.add_argument("--ga-stall-limit", type=int, default=50)
    parser.add_argument("--ns-steps", type=int, default=200)
    parser.add_argument("--block-size", type=int, default=6)
    parser.add_argument("--resource-threshold", type=float, default=0.75)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--no-fast-large-instance",
        action="store_true",
        help=(
            "Disable GANS large-instance fast mode and use exact three-pass "
            "FBI even for large schedules."
        ),
    )
    parser.add_argument(
        "--large-instance-threshold",
        type=int,
        default=1000,
        help="Activity-count threshold for enabling fast large-instance mode.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable verbose GANS output for each run.",
    )

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
            "Path for per-run convergence CSV containing eval and best curve "
            "points. Defaults under --output-dir."
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

    try:
        args.initial_population_modes = _parse_name_list(
            args.initial_population_modes,
            DEFAULT_INITIAL_POPULATION_MODES,
            "initial population mode",
        )
        args.cases = _parse_name_list(args.cases, hard_case_names, "hard case")
        args.json_paths = [_resolve_json_path(path) for path in args.json_inputs]
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
    """Resolve a PSPLIB or RCPLIB benchmark reference value."""
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


def selected_cases(args: argparse.Namespace) -> list[tuple[str, Path]]:
    """Return case name and resolved JSON path pairs."""
    if args.json_paths:
        return [(path.stem, path) for path in args.json_paths]

    hard_case_map = dict(DEFAULT_HARD_CASES)
    return [
        (case_name, _resolve_json_path(hard_case_map[case_name]))
        for case_name in args.cases
    ]


def load_pert(json_path: Path) -> Any:
    pert = Pert.from_json_file(str(json_path), schema_path=str(SCHEMA))
    pert.generateInfo()
    return pert


def compute_priority_baseline(json_path: Path) -> dict[str, Any]:
    """Compute CPM and priority-rule baselines once for one input instance."""
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


def _best_series(log: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    """Return eval-count and best-so-far series from the GANS log."""
    evals: list[float] = []
    best: list[float] = []
    current = float("inf")
    for row in log:
        evals.append(float(row["n_evals"]))
        current = min(current, float(row["best"]))
        best.append(current)
    return evals, best


def run_case(
    case_name: str,
    json_path: Path,
    initial_population_mode: str,
    args: argparse.Namespace,
    baseline: dict[str, Any],
    best_known: float | None,
    best_known_source: str,
) -> dict[str, Any]:
    """Run one GANS initialization mode on a fresh Pert instance."""
    print(
        "Running GANS: "
        f"case={case_name}, initial_population_mode={initial_population_mode}, "
        f"pop={args.pop_size}, lambda_max={args.lambda_max}, seed={args.seed}"
    )
    pert = load_pert(json_path)
    gans = RCPSPHybridGANS(
        pert,
        pop_size=args.pop_size,
        lambda_max=args.lambda_max,
        ga_stall_limit=args.ga_stall_limit,
        ns_steps=args.ns_steps,
        block_size=args.block_size,
        resource_threshold=args.resource_threshold,
        seed=args.seed,
        verbose=not args.quiet,
        initial_population_mode=initial_population_mode,
        fast_large_instance=not args.no_fast_large_instance,
        large_instance_threshold=args.large_instance_threshold,
    )
    best, log = gans.run()
    summary = gans.get_convergence_summary(log)
    evals, best_curve = _best_series(log)
    best_gans = best["fitness"]

    return {
        "case": case_name,
        "json_path": str(json_path),
        "initial_population_mode": initial_population_mode,
        "label": f"{case_name}/{initial_population_mode}",
        "best_gans": best_gans,
        "logged_best": summary["best_duration"],
        "initial_best": summary["initial_best"],
        "improvement_gen0": summary["improvement"],
        "improvement_vs_seed": baseline["best_rule_overall"] - best_gans,
        "best_known": best_known,
        "best_known_source": best_known_source,
        "gap_to_best_known": (
            best_gans - best_known if best_known is not None else float("nan")
        ),
        "n_activities": baseline["n_activities"],
        "cpm_duration": baseline["cpm_duration"],
        "best_serial_seed": baseline["best_serial"],
        "best_parallel_seed": baseline["best_parallel"],
        "n_evals": summary["n_evals"],
        "n_ns_activations": summary["n_ns_activations"],
        "final_stall": summary["final_stall"],
        "pop_size": args.pop_size,
        "lambda_max": args.lambda_max,
        "ga_stall_limit": args.ga_stall_limit,
        "ns_steps": args.ns_steps,
        "block_size": args.block_size,
        "resource_threshold": args.resource_threshold,
        "seed": args.seed,
        "fast_large_instance": not args.no_fast_large_instance,
        "large_instance_threshold": args.large_instance_threshold,
        "evals": evals,
        "best_curve": best_curve,
    }


def build_delta_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build paired priority_rules-vs-random comparisons per case."""
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for result in results:
        by_case.setdefault(result["case"], {})[
            result["initial_population_mode"]
        ] = result

    rows: list[dict[str, Any]] = []
    for case_name in sorted(by_case):
        pair = by_case[case_name]
        priority = pair.get("priority_rules")
        random_result = pair.get("random")
        if priority is None or random_result is None:
            continue
        delta = random_result["best_gans"] - priority["best_gans"]
        if delta > 0:
            winner = "priority_rules"
        elif delta < 0:
            winner = "random"
        else:
            winner = "tie"
        rows.append(
            {
                "case": case_name,
                "priority_best_gans": priority["best_gans"],
                "random_best_gans": random_result["best_gans"],
                "delta_random_minus_priority": delta,
                "winner": winner,
                "priority_gap_to_best_known": priority["gap_to_best_known"],
                "random_gap_to_best_known": random_result["gap_to_best_known"],
                "priority_initial_best": priority["initial_best"],
                "random_initial_best": random_result["initial_best"],
                "priority_n_evals": priority["n_evals"],
                "random_n_evals": random_result["n_evals"],
                "priority_ns_activations": priority["n_ns_activations"],
                "random_ns_activations": random_result["n_ns_activations"],
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
    """Plot best-so-far convergence curves for all GANS runs."""
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
            result["evals"],
            result["best_curve"],
            linestyle=linestyle,
            linewidth=1.6,
            label=result["label"],
        )
        if result["evals"] and result["best_curve"]:
            ax.scatter(
                result["evals"][-1],
                result["best_curve"][-1],
                s=18,
                zorder=3,
            )

    ax.set_title(title)
    ax.set_xlabel("SGS evaluations")
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
        "case",
        "json_path",
        "initial_population_mode",
        "best_gans",
        "best_known",
        "best_known_source",
        "gap_to_best_known",
        "improvement_vs_seed",
        "initial_best",
        "logged_best",
        "improvement_gen0",
        "n_activities",
        "cpm_duration",
        "best_serial_seed",
        "best_parallel_seed",
        "n_evals",
        "n_ns_activations",
        "final_stall",
        "pop_size",
        "lambda_max",
        "ga_stall_limit",
        "ns_steps",
        "block_size",
        "resource_threshold",
        "seed",
        "fast_large_instance",
        "large_instance_threshold",
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
        "case",
        "priority_best_gans",
        "random_best_gans",
        "delta_random_minus_priority",
        "winner",
        "priority_gap_to_best_known",
        "random_gap_to_best_known",
        "priority_initial_best",
        "random_initial_best",
        "priority_n_evals",
        "random_n_evals",
        "priority_ns_activations",
        "random_ns_activations",
    ]
    with filename.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(delta_rows)


def write_curve_csv(results: list[dict[str, Any]], filename: Path) -> None:
    """Write one row per run/log point from result['evals'] and best curve."""
    filename.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "run_rank",
        "case",
        "initial_population_mode",
        "label",
        "point_index",
        "n_evals",
        "best_curve",
        "best_gans",
        "best_known",
        "gap_to_best_known",
        "seed",
    ]
    with filename.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for run_rank, result in enumerate(results, start=1):
            evals = result.get("evals") or []
            best_curve = result.get("best_curve") or []
            for point_index, (n_evals, best_value) in enumerate(
                zip(evals, best_curve),
                start=1,
            ):
                writer.writerow(
                    {
                        "run_rank": run_rank,
                        "case": result.get("case"),
                        "initial_population_mode": result.get(
                            "initial_population_mode"
                        ),
                        "label": result.get("label"),
                        "point_index": point_index,
                        "n_evals": n_evals,
                        "best_curve": best_value,
                        "best_gans": result.get("best_gans"),
                        "best_known": result.get("best_known"),
                        "gap_to_best_known": result.get("gap_to_best_known"),
                        "seed": result.get("seed"),
                    }
                )


def print_full_comparison(results: list[dict[str, Any]]) -> None:
    print()
    print("=" * 132)
    print("GANS INITIAL POPULATION COMPARISON")
    print("=" * 132)
    print(
        f"{'Rank':>4} {'Case':<12} {'Init Mode':<14} {'Best':>8} "
        f"{'BK Gap':>8} {'Seed Gap':>9} {'Initial':>8} "
        f"{'Evals':>8} {'NS#':>5} {'Serial':>8} {'Parallel':>9}"
    )
    print("-" * 132)
    for rank, result in enumerate(results, start=1):
        print(
            f"{rank:>4} {result['case']:<12} "
            f"{result['initial_population_mode']:<14} "
            f"{result['best_gans']:>8.2f} "
            f"{_safe_float(result['gap_to_best_known']):>8} "
            f"{result['improvement_vs_seed']:>9.2f} "
            f"{result['initial_best']:>8.2f} "
            f"{result['n_evals']:>8} "
            f"{result['n_ns_activations']:>5} "
            f"{result['best_serial_seed']:>8.2f} "
            f"{result['best_parallel_seed']:>9.2f}"
        )
    print("-" * 132)
    best = results[0]
    print(
        "Best run: "
        f"{best['case']} with {best['initial_population_mode']} initialization "
        f"at {best['best_gans']:.2f} h"
    )


def print_delta_comparison(delta_rows: list[dict[str, Any]]) -> None:
    if not delta_rows:
        return

    print()
    print("=" * 104)
    print("GANS PRIORITY_RULES VS RANDOM BY CASE")
    print("=" * 104)
    print(
        f"{'Case':<12} {'Priority':>10} {'Random':>8} "
        f"{'Random-Priority':>16} {'Winner':>14} "
        f"{'Priority Init':>13} {'Random Init':>12}"
    )
    print("-" * 104)
    for row in delta_rows:
        print(
            f"{row['case']:<12} "
            f"{row['priority_best_gans']:>10.2f} "
            f"{row['random_best_gans']:>8.2f} "
            f"{row['delta_random_minus_priority']:>16.2f} "
            f"{row['winner']:>14} "
            f"{row['priority_initial_best']:>13.2f} "
            f"{row['random_initial_best']:>12.2f}"
        )
    print("-" * 104)
    print(
        "Positive Random-Priority means priority_rules initialization produced "
        "a shorter schedule."
    )


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cases = selected_cases(args)
    best_results = load_best_known_results()
    rcplib_results = load_rcplib_solution_results(args.rcplib_solutions)

    print("=" * 84)
    print("GANS priority_rules vs random initialization")
    print(f"Cases                    : {', '.join(name for name, _ in cases)}")
    print(f"Initial population modes : {', '.join(args.initial_population_modes)}")
    print(f"pop_size                 : {args.pop_size}")
    print(f"lambda_max               : {args.lambda_max}")
    print(f"seed                     : {args.seed}")
    print(f"fast large-instance mode : {not args.no_fast_large_instance}")
    print(f"large-instance threshold : {args.large_instance_threshold}")
    print(
        "Runs                     : "
        f"{len(cases) * len(args.initial_population_modes)}"
    )
    print("=" * 84)

    results: list[dict[str, Any]] = []

    for case_name, json_path in cases:
        baseline = compute_priority_baseline(json_path)
        best_known, best_known_source = get_best_known_result(
            best_results,
            rcplib_results,
            json_path,
            args.best_known_source,
            args.rcplib_best_key,
        )

        print()
        print("-" * 84)
        print(f"Case: {case_name} ({json_path})")
        print(f"Activities        : {baseline['n_activities']}")
        print(f"CPM duration      : {baseline['cpm_duration']:.2f} h")
        print(f"Best serial seed  : {baseline['best_serial']:.2f} h")
        print(f"Best parallel seed: {baseline['best_parallel']:.2f} h")
        print(f"Best known        : {_safe_float(best_known)} h ({best_known_source})")
        print("-" * 84)

        for mode in args.initial_population_modes:
            result = run_case(
                case_name,
                json_path,
                mode,
                args,
                baseline,
                best_known,
                best_known_source,
            )
            results.append(result)

    results.sort(
        key=lambda row: (
            row["best_gans"],
            row["n_evals"],
            row["case"],
            row["initial_population_mode"],
        )
    )
    delta_rows = build_delta_rows(results)

    case_suffix = "hard_cases" if not args.json_paths else "_".join(
        path.stem for path in args.json_paths
    )
    if len(cases) == 1:
        case_suffix = cases[0][0]
    plot_file = args.plot_file or (
        args.output_dir / f"{case_suffix}_priority_vs_random_convergence_seed{args.seed}.png"
    )
    csv_file = args.csv_file or (
        args.output_dir / f"{case_suffix}_priority_vs_random_comparison_seed{args.seed}.csv"
    )
    delta_csv_file = args.delta_csv_file or (
        args.output_dir / f"{case_suffix}_priority_vs_random_delta_seed{args.seed}.csv"
    )
    curve_csv_file = args.curve_csv_file or (
        args.output_dir / f"{case_suffix}_priority_vs_random_curves_seed{args.seed}.csv"
    )

    if not args.no_plot:
        plot_combined_convergence(
            results,
            plot_file,
            title=f"GANS Initial Population Comparison - {case_suffix}",
            show=args.show,
        )
        print(f"Combined convergence plot: {plot_file}")

    write_csv(results, csv_file)
    print(f"Comparison CSV: {csv_file}")
    write_delta_csv(delta_rows, delta_csv_file)
    print(f"Priority-vs-random delta CSV: {delta_csv_file}")
    write_curve_csv(results, curve_csv_file)
    print(f"Convergence curves CSV: {curve_csv_file}")

    print_full_comparison(results)
    print_delta_comparison(delta_rows)


if __name__ == "__main__":
    main()
