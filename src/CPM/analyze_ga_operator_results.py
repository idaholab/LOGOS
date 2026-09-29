"""
analyze_ga_operator_results.py - Aggregate GA operator comparison CSVs.

The operator test writes one CSV per benchmark instance.  This script processes
those files together so crossover/mutation combinations can be compared across
all j120 runs.

Usage from the repo root:
    python src/CPM/analyze_ga_operator_results.py

Usage from the src/CPM directory:
    python analyze_ga_operator_results.py

Examples:
    python src/CPM/analyze_ga_operator_results.py --top 6
    python src/CPM/analyze_ga_operator_results.py --pattern "j120*.csv"
    python src/CPM/analyze_ga_operator_results.py --no-write
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any


CPM_DIR = Path(__file__).parent
DEFAULT_RESULTS_DIR = CPM_DIR / "results" / "ga_operator_test"
DEFAULT_PATTERN = "j120*_operator_comparison_seed*.csv"
FILENAME_RE = re.compile(
    r"(?P<instance>j120.*?)_operator_comparison_seed(?P<seed>\d+)\.csv$"
)


@dataclass(frozen=True)
class OperatorResult:
    source_file: str
    instance: str
    seed: int
    rank: int
    crossover: str
    mutation: str
    best_ga: float
    gap_to_best_known: float
    improvement_vs_seed: float
    initial_best: float
    logged_best: float
    improvement_gen0: float
    final_avg: float
    final_std: float
    n_gen_executed: int
    n_evals: int
    n_unique_schedules: int
    stop_reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate j120 GA operator comparison CSVs and rank "
            "crossover/mutation performance."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="Directory containing per-instance operator comparison CSV files.",
    )
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=(
            "Input glob pattern. The default avoids re-reading aggregate output "
            "files on later runs."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory for aggregate CSV outputs. Defaults to --input-dir.",
    )
    parser.add_argument(
        "--output-prefix",
        default="j120_operator",
        help="Prefix for generated aggregate CSV files.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=12,
        help="Number of crossover/mutation combinations to print.",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print summaries without writing aggregate CSV files.",
    )
    return parser.parse_args()


def _to_float(value: str | None) -> float:
    if value is None:
        return float("nan")
    stripped = value.strip()
    if not stripped:
        return float("nan")
    return float(stripped)


def _to_int(value: str | None) -> int:
    return int(round(_to_float(value)))


def _mean(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return mean(finite) if finite else float("nan")


def _median(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    return median(finite) if finite else float("nan")


def _fmt(value: float, digits: int = 2) -> str:
    if not math.isfinite(value):
        return "nan"
    return f"{value:.{digits}f}"


def _file_context(path: Path) -> tuple[str, int] | None:
    match = FILENAME_RE.match(path.name)
    if not match:
        return None
    return match.group("instance"), int(match.group("seed"))


def read_result_file(path: Path) -> list[OperatorResult]:
    context = _file_context(path)
    if context is None:
        return []

    instance, seed = context
    rows: list[OperatorResult] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                OperatorResult(
                    source_file=path.name,
                    instance=instance,
                    seed=seed,
                    rank=_to_int(row.get("rank")),
                    crossover=(row.get("crossover") or "").strip(),
                    mutation=(row.get("mutation") or "").strip(),
                    best_ga=_to_float(row.get("best_ga")),
                    gap_to_best_known=_to_float(row.get("gap_to_best_known")),
                    improvement_vs_seed=_to_float(row.get("improvement_vs_seed")),
                    initial_best=_to_float(row.get("initial_best")),
                    logged_best=_to_float(row.get("logged_best")),
                    improvement_gen0=_to_float(row.get("improvement_gen0")),
                    final_avg=_to_float(row.get("final_avg")),
                    final_std=_to_float(row.get("final_std")),
                    n_gen_executed=_to_int(row.get("n_gen_executed")),
                    n_evals=_to_int(row.get("n_evals")),
                    n_unique_schedules=_to_int(row.get("n_unique_schedules")),
                    stop_reason=(row.get("stop_reason") or "").strip(),
                )
            )
    return rows


def load_results(input_dir: Path, pattern: str) -> tuple[list[OperatorResult], list[Path]]:
    paths = sorted(input_dir.glob(pattern))
    rows: list[OperatorResult] = []
    skipped: list[Path] = []
    for path in paths:
        if _file_context(path) is None:
            skipped.append(path)
            continue
        rows.extend(read_result_file(path))
    return rows, skipped


def _group_key(row: OperatorResult, fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(getattr(row, field)) for field in fields)


def summarize(
    rows: list[OperatorResult],
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[OperatorResult]] = defaultdict(list)
    for row in rows:
        grouped[_group_key(row, fields)].append(row)

    summaries: list[dict[str, Any]] = []
    for key, group in grouped.items():
        wins = [row for row in group if row.rank == 1]
        stop_reasons = Counter(row.stop_reason for row in group)
        summary: dict[str, Any] = {
            field: value for field, value in zip(fields, key, strict=True)
        }
        summary.update(
            {
                "runs": len(group),
                "instances": len({row.instance for row in group}),
                "seeds": len({row.seed for row in group}),
                "wins": len(wins),
                "top3": sum(row.rank <= 3 for row in group),
                "win_rate": len(wins) / len(group),
                "top3_rate": sum(row.rank <= 3 for row in group) / len(group),
                "avg_rank": _mean([float(row.rank) for row in group]),
                "median_rank": _median([float(row.rank) for row in group]),
                "avg_best_ga": _mean([row.best_ga for row in group]),
                "avg_gap_to_best_known": _mean(
                    [row.gap_to_best_known for row in group]
                ),
                "avg_improvement_vs_seed": _mean(
                    [row.improvement_vs_seed for row in group]
                ),
                "avg_logged_best": _mean([row.logged_best for row in group]),
                "avg_final_avg": _mean([row.final_avg for row in group]),
                "avg_final_std": _mean([row.final_std for row in group]),
                "avg_n_gen_executed": _mean(
                    [float(row.n_gen_executed) for row in group]
                ),
                "avg_n_evals": _mean([float(row.n_evals) for row in group]),
                "winning_instances": ", ".join(
                    sorted({row.instance for row in wins})
                ),
                "stop_reasons": "; ".join(
                    f"{reason}:{count}"
                    for reason, count in sorted(stop_reasons.items())
                ),
            }
        )
        summaries.append(summary)

    summaries.sort(key=summary_sort_key)
    return summaries


def summary_sort_key(summary: dict[str, Any]) -> tuple[Any, ...]:
    return (
        summary["avg_rank"],
        summary["avg_gap_to_best_known"],
        summary["avg_best_ga"],
        -summary["wins"],
        summary.get("crossover", ""),
        summary.get("mutation", ""),
    )


def write_rows(rows: list[OperatorResult], path: Path) -> None:
    fields = list(OperatorResult.__dataclass_fields__)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: getattr(row, field) for field in fields})


def write_summary(summaries: list[dict[str, Any]], path: Path) -> None:
    if not summaries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(summaries[0])
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)


def write_matrix(
    combo_summaries: list[dict[str, Any]],
    metric: str,
    path: Path,
) -> None:
    crossovers = sorted({summary["crossover"] for summary in combo_summaries})
    mutations = sorted({summary["mutation"] for summary in combo_summaries})
    lookup = {
        (summary["crossover"], summary["mutation"]): summary
        for summary in combo_summaries
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["crossover", *mutations])
        for crossover in crossovers:
            row = [crossover]
            for mutation in mutations:
                value = lookup.get((crossover, mutation), {}).get(metric)
                row.append("" if value is None else _fmt(float(value), digits=4))
            writer.writerow(row)


def print_combo_summary(summaries: list[dict[str, Any]], top: int) -> None:
    print()
    print("CROSSOVER + MUTATION COMBINATION SUMMARY")
    print("-" * 104)
    print(
        f"{'#':>2} {'Crossover':<14} {'Mutation':<18} {'Runs':>4} "
        f"{'Wins':>4} {'Top3':>4} {'AvgRank':>8} {'AvgGap':>8} "
        f"{'AvgBest':>8} {'AvgSeed+':>9}"
    )
    print("-" * 104)
    for idx, summary in enumerate(summaries[:top], start=1):
        print(
            f"{idx:>2} {summary['crossover']:<14} {summary['mutation']:<18} "
            f"{summary['runs']:>4} {summary['wins']:>4} {summary['top3']:>4} "
            f"{_fmt(summary['avg_rank']):>8} "
            f"{_fmt(summary['avg_gap_to_best_known']):>8} "
            f"{_fmt(summary['avg_best_ga']):>8} "
            f"{_fmt(summary['avg_improvement_vs_seed']):>9}"
        )
    print("-" * 104)


def print_single_operator_summary(
    title: str,
    summaries: list[dict[str, Any]],
    field: str,
) -> None:
    print()
    print(title)
    print("-" * 82)
    print(
        f"{field.title():<18} {'Runs':>4} {'Wins':>4} {'Top3':>4} "
        f"{'AvgRank':>8} {'AvgGap':>8} {'AvgBest':>8} {'AvgSeed+':>9}"
    )
    print("-" * 82)
    for summary in summaries:
        print(
            f"{summary[field]:<18} {summary['runs']:>4} "
            f"{summary['wins']:>4} {summary['top3']:>4} "
            f"{_fmt(summary['avg_rank']):>8} "
            f"{_fmt(summary['avg_gap_to_best_known']):>8} "
            f"{_fmt(summary['avg_best_ga']):>8} "
            f"{_fmt(summary['avg_improvement_vs_seed']):>9}"
        )
    print("-" * 82)


def print_winners(rows: list[OperatorResult]) -> None:
    winners = sorted((row for row in rows if row.rank == 1), key=lambda r: r.instance)
    if not winners:
        return

    print()
    print("PER-INSTANCE WINNERS")
    print("-" * 86)
    print(
        f"{'Instance':<12} {'Seed':>4} {'Crossover':<14} {'Mutation':<18} "
        f"{'Best':>8} {'BK Gap':>8}"
    )
    print("-" * 86)
    for row in winners:
        print(
            f"{row.instance:<12} {row.seed:>4} {row.crossover:<14} "
            f"{row.mutation:<18} {_fmt(row.best_ga):>8} "
            f"{_fmt(row.gap_to_best_known):>8}"
        )
    print("-" * 86)


def write_outputs(
    rows: list[OperatorResult],
    combo_summaries: list[dict[str, Any]],
    crossover_summaries: list[dict[str, Any]],
    mutation_summaries: list[dict[str, Any]],
    output_dir: Path,
    prefix: str,
) -> list[Path]:
    paths = {
        "all_rows": output_dir / f"{prefix}_all_rows.csv",
        "combo_summary": output_dir / f"{prefix}_combo_summary.csv",
        "crossover_summary": output_dir / f"{prefix}_crossover_summary.csv",
        "mutation_summary": output_dir / f"{prefix}_mutation_summary.csv",
        "avg_rank_matrix": output_dir / f"{prefix}_avg_rank_matrix.csv",
        "avg_gap_matrix": output_dir / f"{prefix}_avg_gap_matrix.csv",
    }

    write_rows(rows, paths["all_rows"])
    write_summary(combo_summaries, paths["combo_summary"])
    write_summary(crossover_summaries, paths["crossover_summary"])
    write_summary(mutation_summaries, paths["mutation_summary"])
    write_matrix(combo_summaries, "avg_rank", paths["avg_rank_matrix"])
    write_matrix(combo_summaries, "avg_gap_to_best_known", paths["avg_gap_matrix"])
    return list(paths.values())


def main() -> None:
    args = parse_args()
    rows, skipped = load_results(args.input_dir, args.pattern)

    if not rows:
        raise SystemExit(
            f"No operator comparison rows found in {args.input_dir} "
            f"with pattern {args.pattern!r}."
        )

    combo_summaries = summarize(rows, ("crossover", "mutation"))
    crossover_summaries = summarize(rows, ("crossover",))
    mutation_summaries = summarize(rows, ("mutation",))

    instances = sorted({row.instance for row in rows})
    seeds = sorted({row.seed for row in rows})
    print("=" * 86)
    print("GA OPERATOR RESULT AGGREGATION")
    print("=" * 86)
    print(f"Input directory : {args.input_dir}")
    print(f"Input pattern   : {args.pattern}")
    print(f"Instances       : {len(instances)}")
    print(f"Seeds           : {', '.join(str(seed) for seed in seeds)}")
    print(f"Rows            : {len(rows)}")
    if skipped:
        print(f"Skipped files   : {len(skipped)} unmatched file name(s)")

    print_combo_summary(combo_summaries, args.top)
    print_single_operator_summary(
        "CROSSOVER SUMMARY", crossover_summaries, "crossover"
    )
    print_single_operator_summary("MUTATION SUMMARY", mutation_summaries, "mutation")
    print_winners(rows)

    if args.no_write:
        return

    output_dir = args.output_dir or args.input_dir
    paths = write_outputs(
        rows,
        combo_summaries,
        crossover_summaries,
        mutation_summaries,
        output_dir,
        args.output_prefix,
    )
    print()
    print("Wrote aggregate CSV files:")
    for path in paths:
        print(f"  {path}")


if __name__ == "__main__":
    main()
