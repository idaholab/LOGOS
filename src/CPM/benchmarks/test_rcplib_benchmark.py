"""Run LOGOS CPM scheduler against converted RG300/LPP JSON instances.

This script is based on ``test_psplib_benchmark.py`` but is targeted at the
flat ``RG300_Json`` and ``LPP_Json`` directories produced from ``.rcp`` files.
It uses ``rcplib_solution_results.json`` as the benchmark source and compares
LOGOS results against the ``UB-lit`` value for each ``.rcp`` instance.

Usage
-----
# Test RG300 JSON files
python test_rcplib_benchmark.py --rcplib-json-dir RG300_Json/ --set RG300

# Test LPP JSON files with 4 workers
python test_rcplib_benchmark.py --rcplib-json-dir LPP_Json/ --set LPP --workers 4

# Quick sanity check
python test_rcplib_benchmark.py --rcplib-json-dir RG300_Json/ --max-files 5

# Custom output path
python test_rcplib_benchmark.py --rcplib-json-dir LPP_Json/ --out lpp_results.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import multiprocessing as mp
import re
import sys
import traceback
from pathlib import Path
from typing import Any


PRIORITY_RULES = [
    "es",
    "ef",
    "ls",
    "lf",
    "duration",
    "random",
    "mts",
    "mtp",
    "grpw",
    "grd",
    "rr",
    "avgrr",
    "maxrr",
    "minrr",
    # "irsm",
    # "wcs",
    # "acs",
    "mehh_8000_b",
    "mehh_3375_b",
    "mehh_1000_b",
    "mehh_125_b",
    "gphh_b",
]

SGS_METHODS = ["max_use_res_ranked"]

# Converted .rcp files keep the dummy source and sink as positive 1-unit tasks
# for schema validity. Subtract both to compare against RCPLIB makespans.
DUMMY_OFFSET = 2

# Tie-breaker options evaluated per rule; the best (lowest) makespan is kept.
TIE_BREAKER_OPTIONS: list[str | None] = [None, "mehh_8000_b"]


def natural_sort_key(path: Path) -> list[int | str]:
    """Sort paths with embedded numbers in numeric order."""
    parts = re.split(r"(\d+)", path.stem)
    return [int(part) if part.isdigit() else part for part in parts]


def natural_instance_key(value: str) -> list[int | str]:
    """Sort instance names with embedded numbers in numeric order."""
    parts = re.split(r"(\d+)", value)
    return [int(part) if part.isdigit() else part for part in parts]


def infer_set_name(json_dir: Path) -> str:
    """Infer RG300/LPP from a directory such as RG300_Json or LPP_Json."""
    name = json_dir.name
    if name.endswith("_Json"):
        name = name[: -len("_Json")]
    return name


def _run_instance_with_pert(
    json_path: str | Path,
    schema_path: str | Path,
    set_name: str,
    pert_cls: Any,
) -> dict[str, Any]:
    """Run all configured LOGOS scheduling variants for one JSON instance."""
    path = Path(json_path)
    stem = path.stem
    rcp_name = stem + ".rcp"
    row: dict[str, Any] = {
        "set": set_name,
        "instance": stem,
        "rcp_name": rcp_name,
    }

    try:
        for rule in PRIORITY_RULES:
            best = None
            for tie_breaker in TIE_BREAKER_OPTIONS:
                pert = pert_cls.from_json_file(json_path, schema_path=schema_path)
                out = pert.calculateSerialScheduleWithResources(
                    priority_rule=rule,
                    tie_breaker=tie_breaker,
                )
                val = out["scheduled_duration"] - DUMMY_OFFSET
                if best is None or val < best:
                    best = val
            row[f"sgs_{rule}"] = best

        for sgs in SGS_METHODS:
            for rule in PRIORITY_RULES:
                best = None
                for tie_breaker in TIE_BREAKER_OPTIONS:
                    pert = pert_cls.from_json_file(json_path, schema_path=schema_path)
                    out = pert.calculateScheduleWithResources(
                        sgs=sgs,
                        priority_rule=rule,
                        tie_breaker=tie_breaker,
                    )
                    val = out["scheduled_duration"] - DUMMY_OFFSET
                    if best is None or val < best:
                        best = val
                row[f"pgs_{sgs}_{rule}"] = best

    except Exception:
        row["error"] = traceback.format_exc()

    return row


def _run_instance(args: tuple[str, str, str]) -> dict[str, Any]:
    """Worker-process entry point for one JSON instance."""
    json_path, schema_path, set_name = args

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from src.CPM.pert import Pert  # noqa: PLC0415

    logging.disable(logging.CRITICAL)
    return _run_instance_with_pert(json_path, schema_path, set_name, Pert)


def load_best_known(
    solution_path: Path,
    best_key: str,
) -> dict[str, int | float | None]:
    """Load best-known makespans by .rcp filename from RCPLIB solution JSON."""
    if not solution_path.exists():
        print(
            f"WARNING: {solution_path} not found; best-known comparison skipped.",
            file=sys.stderr,
        )
        return {}

    with solution_path.open(encoding="utf-8") as f:
        solution_data = json.load(f)

    best_known: dict[str, int | float | None] = {}
    missing_best_key = 0
    for rcp_name, entry in solution_data.items():
        if not isinstance(entry, dict):
            continue
        if best_key not in entry:
            missing_best_key += 1
            continue
        best_known[rcp_name] = entry[best_key]

    if missing_best_key:
        print(
            f"WARNING: {missing_best_key} solution entries have no {best_key!r}.",
            file=sys.stderr,
        )
    return best_known


def as_number(value: Any) -> float | None:
    """Convert a value to float for comparisons, returning None on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_number(value: float | None) -> int | float | None:
    """Use ints for whole-number JSON/CSV values."""
    if value is None:
        return None
    if value.is_integer():
        return int(value)
    return value


def attach_best_known_columns(
    rows: list[dict[str, Any]],
    best_known: dict[str, int | float | None],
) -> list[dict[str, Any]]:
    """Attach UB-lit values and LOGOS-vs-best-known gap columns."""
    for row in rows:
        best_known_value = as_number(best_known.get(row["rcp_name"]))
        logos_values = [
            as_number(value)
            for key, value in row.items()
            if key.startswith("sgs_") or key.startswith("pgs_")
        ]
        logos_values = [value for value in logos_values if value is not None]
        logos_best = min(logos_values) if logos_values else None

        row["best_known"] = normalize_number(best_known_value)
        row["logos_best"] = normalize_number(logos_best)
        if logos_best is not None and best_known_value is not None:
            row["gap_to_best_known"] = normalize_number(logos_best - best_known_value)
        else:
            row["gap_to_best_known"] = None

    return rows


def result_columns() -> list[str]:
    """Return output columns for every configured scheduling run."""
    columns = [f"sgs_{rule}" for rule in PRIORITY_RULES]
    for sgs in SGS_METHODS:
        columns.extend(f"pgs_{sgs}_{rule}" for rule in PRIORITY_RULES)
    return columns


def write_results_csv(rows: list[dict[str, Any]], out_csv: Path) -> None:
    """Write benchmark rows with a stable column order."""
    base_columns = ["set", "instance", "rcp_name"]
    tail_columns = ["best_known", "logos_best", "gap_to_best_known"]
    if any("error" in row for row in rows):
        tail_columns.append("error")

    preferred = base_columns + result_columns() + tail_columns
    extras = sorted({key for row in rows for key in row} - set(preferred))
    fieldnames = preferred + extras

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_gap_summary(rows: list[dict[str, Any]], best_key: str) -> None:
    """Print per-set gap statistics."""
    gaps_by_set: dict[str, list[float]] = {}
    for row in rows:
        gap = as_number(row.get("gap_to_best_known"))
        if gap is None:
            continue
        gaps_by_set.setdefault(str(row.get("set", "")), []).append(gap)

    if not gaps_by_set:
        return

    print(f"\nGap to best-known makespan (LOGOS best - {best_key}):")
    print(f"{'set':<8} {'mean':>10} {'min':>10} {'max':>10} {'count':>8}")
    for set_name in sorted(gaps_by_set):
        gaps = gaps_by_set[set_name]
        mean_gap = sum(gaps) / len(gaps)
        print(
            f"{set_name:<8} {mean_gap:>10.3f} "
            f"{min(gaps):>10.3f} {max(gaps):>10.3f} {len(gaps):>8}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run LOGOS CPM scheduler on converted RG300/LPP JSON instances "
            "and compare against RCPLIB UB-lit values."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--rcplib-json-dir",
        type=Path,
        required=True,
        metavar="DIR",
        help="Flat directory with converted JSON files, e.g. RG300_Json or LPP_Json.",
    )
    parser.add_argument(
        "--set",
        choices=["RG300", "LPP"],
        default=None,
        metavar="SET",
        help="Benchmark set name. Inferred from --rcplib-json-dir when omitted.",
    )
    parser.add_argument(
        "--solutions",
        type=Path,
        default=None,
        metavar="FILE",
        help=(
            "Path to rcplib_solution_results.json. Defaults to the file next "
            "to this script."
        ),
    )
    parser.add_argument(
        "--best-key",
        default="LB-lit",
        metavar="KEY",
        help="Second-level solution key to use as best known (default: LB-lit).",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        metavar="FILE",
        help="Path to outage_schema.json. Auto-detected from src/CPM/ if omitted.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Directory where outputs are saved. Overrides the directory part of --out.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("rcplib_results.csv"),
        metavar="FILE",
        help=(
            "Output CSV filename (default: rcplib_results.csv). When "
            "--output-dir is set, only the filename part is used."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Number of parallel worker processes (default: 1).",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        metavar="N",
        help="Limit to N files, useful for quick tests.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parents[2]

    json_dir = args.rcplib_json_dir
    if not json_dir.is_dir():
        raise SystemExit(f"JSON directory not found: {json_dir}")

    set_name = args.set or infer_set_name(json_dir)
    if set_name not in {"RG300", "LPP"}:
        raise SystemExit(
            "Could not infer benchmark set. Pass --set RG300 or --set LPP."
        )

    schema_path = args.schema or repo_root / "src" / "CPM" / "outage_schema.json"
    if not schema_path.exists():
        raise SystemExit(f"Schema file not found: {schema_path}")

    solution_path = args.solutions or script_dir / "rcplib_solution_results.json"

    if args.output_dir is not None:
        output_dir = args.output_dir
        out_csv = output_dir / args.out.name
    else:
        out_csv = args.out
        output_dir = out_csv.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(json_dir.glob("*.json"), key=natural_sort_key)
    if args.max_files is not None:
        files = files[: args.max_files]
    if not files:
        raise SystemExit(f"No JSON files found in {json_dir}")

    best_known = load_best_known(solution_path, args.best_key)

    print(
        f"Running {len(files)} {set_name} instance(s) with "
        f"{args.workers} worker(s) ..."
    )
    print(f"Output will be saved to: {out_csv}")

    worker_args = [(str(path), str(schema_path), set_name) for path in files]
    results: list[dict[str, Any]] = []
    errors = 0

    if args.workers > 1:
        with mp.Pool(processes=args.workers) as pool:
            for i, row in enumerate(pool.imap_unordered(_run_instance, worker_args), 1):
                if "error" in row:
                    print(
                        f"  ERROR [{row.get('instance', '?')}]: "
                        f"{row['error'][:200]}",
                        file=sys.stderr,
                    )
                    errors += 1
                results.append(row)
                if i % 50 == 0 or i == len(worker_args):
                    print(f"  ... {i}/{len(worker_args)} done ({errors} errors)")
    else:
        sys.path.insert(0, str(repo_root))
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
        from src.CPM.pert import Pert  # noqa: PLC0415

        for i, (json_path, schema, task_set_name) in enumerate(worker_args, 1):
            row = _run_instance_with_pert(json_path, schema, task_set_name, Pert)
            if "error" in row:
                print(
                    f"  ERROR [{row.get('instance', '?')}]: {row['error'][:200]}",
                    file=sys.stderr,
                )
                errors += 1
            results.append(row)
            if i % 50 == 0 or i == len(worker_args):
                print(f"  ... {i}/{len(worker_args)} done ({errors} errors)")

    results.sort(
        key=lambda row: (
            str(row.get("set", "")),
            natural_instance_key(str(row.get("instance", ""))),
        )
    )
    attach_best_known_columns(results, best_known)
    write_results_csv(results, out_csv)
    print(f"\nSaved {len(results)} rows to {out_csv}")

    missing_best = sum(row.get("best_known") is None for row in results)
    if missing_best:
        print(
            f"\nWARNING: {missing_best} row(s) had no {args.best_key!r} value "
            "in the solution JSON."
        )

    print_gap_summary(results, args.best_key)

    if errors:
        print(
            f"\n{errors} instance(s) had errors. Error details are in the "
            "'error' column of the CSV."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
