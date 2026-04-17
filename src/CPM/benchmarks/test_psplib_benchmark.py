"""Run LOGOS CPM scheduler against all converted PSPLIB JSON instances and
collect results for comparison against known benchmark values.

Usage
-----
# All four sets, 4 parallel workers, save to results.csv
python test_psplib_benchmark.py --psplib-json-dir PSPLIB_Json/ --workers 4

# Just j30, single worker
python test_psplib_benchmark.py --psplib-json-dir PSPLIB_Json/ --sets j30

# Limit to 10 files per set (quick sanity check)
python test_psplib_benchmark.py --psplib-json-dir PSPLIB_Json/ --max-files 10

# Custom output path
python test_psplib_benchmark.py --psplib-json-dir PSPLIB_Json/ --out my_results.csv
"""

import argparse
import json
import logging
import multiprocessing as mp
import sys
import traceback
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Benchmark rule mapping
#
# LOGOS priority-rule name → PSPLIB benchmark column name (before appending
# _serial_forward / _parallel_forward).  Rules not in the benchmark are still
# run by LOGOS but will have no benchmark counterpart to compare against.
# ---------------------------------------------------------------------------
RULE_TO_BENCHMARK: dict[str, str] = {
    "es":           "EST",
    "ef":           "EFT",
    "ls":           "LST",
    "lf":           "LFT",
    "duration":     "SPT",
    "random":       "RAND",
    "mts":          "MTS",
    "grpw":         "GRPW",
    "grd":          "GRD",
    "irsm":         "IRSM",
    "wcs":          "WCS",
    "acs":          "ACS",
    "mehh_8000_b":  "mehh_8000_b",
    "mehh_3375_b":  "mehh_3375_b",
    "mehh_1000_b":  "mehh_1000_b",
    "mehh_125_b":   "mehh_125_b",
    "gphh_b":       "gphh_b",
}

PRIORITY_RULES = [
    "es", "ef", "ls", "lf", "duration", "random",
    "mts", "mtp", "grpw", "grd", "rr", "avgrr",
    "maxrr", "minrr", "irsm", "wcs", "acs",
    "mehh_8000_b", "mehh_3375_b", "mehh_1000_b", "mehh_125_b", "gphh_b",
]

# SGS_METHODS = ["first", "max_use_res_ranked", "max_use_res_shuffled",
#                "md_knapsack", "look_ahead"]
SGS_METHODS = ["max_use_res_ranked"]
# Dummy source + sink each contribute 1 unit to scheduled_duration; subtract
# both to get the true project makespan matching PSPLIB benchmark values.
DUMMY_OFFSET = 2


# ---------------------------------------------------------------------------
# Per-instance worker
# ---------------------------------------------------------------------------

def _run_instance(args: tuple) -> dict:
    """Process a single JSON instance.  Designed to run in a worker process.

    Returns a flat dict of results, or a dict with 'error' key on failure.
    """
    json_path, schema_path, set_name = args

    # Lazy import so each worker process initialises cleanly.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from src.CPM.pert import Pert  # noqa: PLC0415

    logging.disable(logging.CRITICAL)  # suppress per-instance noise in workers

    stem = Path(json_path).stem          # e.g. "j3010_1"
    sm_name = stem + ".sm"               # e.g. "j3010_1.sm"

    row: dict = {"set": set_name, "instance": stem, "sm_name": sm_name}

    try:
        # --- Serial generation scheme ---
        for rule in PRIORITY_RULES:
            pert = Pert.from_json_file(json_path, schema_path=schema_path)
            out = pert.calculateSerialScheduleWithResources(priority_rule=rule)
            row[f"sgs_{rule}"] = out["scheduled_duration"] - DUMMY_OFFSET

        # --- Parallel generation scheme + priority rules ---
        for sgs in SGS_METHODS:
            for rule in PRIORITY_RULES:
                pert = Pert.from_json_file(json_path, schema_path=schema_path)
                out = pert.calculateScheduleWithResources(
                    sgs=sgs, priority_rule=rule
                )
                row[f"pgs_{sgs}_{rule}"] = out["scheduled_duration"] - DUMMY_OFFSET

    except Exception:
        row["error"] = traceback.format_exc()

    return row


# ---------------------------------------------------------------------------
# Benchmark comparison helpers
# ---------------------------------------------------------------------------

def _attach_benchmark_columns(
    df: pd.DataFrame,
    benchmark_pr: dict,
    best_known: dict,
) -> pd.DataFrame:
    """Add columns from priority_rules_results.json and best_results.json."""

    # Best-known optimal makespan
    df["best_known"] = df["sm_name"].map(best_known)

    # Benchmark serial-forward results for mapped rules
    for logos_rule, bench_col in RULE_TO_BENCHMARK.items():
        col_key = f"{bench_col}_serial_forward"
        logos_col = f"sgs_{logos_rule}"
        bench_target_col = f"bench_sgs_{logos_rule}"

        def _lookup(row, ck=col_key):
            entry = benchmark_pr.get(row["set"], {}).get(row["sm_name"], {})
            return entry.get(ck)

        df[bench_target_col] = df.apply(_lookup, axis=1)

        # Deviation from benchmark serial result (LOGOS - benchmark)
        if logos_col in df.columns:
            df[f"dev_sgs_{logos_rule}"] = df[logos_col] - df[bench_target_col]

    # Deviation of best LOGOS result from best-known
    logos_cols = [c for c in df.columns
                  if c.startswith("sgs_") or c.startswith("pgs_")]
    numeric = df[logos_cols].apply(pd.to_numeric, errors="coerce")
    df["logos_best"] = numeric.min(axis=1)
    df["gap_to_best_known"] = df["logos_best"] - df["best_known"]

    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run LOGOS CPM scheduler on PSPLIB JSON instances and collect results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--psplib-json-dir",
        type=Path,
        required=True,
        metavar="DIR",
        help="Root directory with converted JSON files (contains j30/, j60/, j90/, j120/ subdirs).",
    )
    parser.add_argument(
        "--sets",
        nargs="+",
        choices=["j30", "j60", "j90", "j120"],
        default=["j30", "j60", "j90", "j120"],
        metavar="SET",
        help="Which benchmark sets to test (default: all four).",
    )
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Directory containing priority_rules_results.json and best_results.json. "
            "Defaults to the same directory as this script."
        ),
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=None,
        metavar="FILE",
        help="Path to outage_schema.json. Auto-detected from src/CPM/ if omitted.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("psplib_results.csv"),
        metavar="FILE",
        help="Output CSV file path (default: psplib_results.csv).",
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
        help="Limit to N files per set (useful for quick tests).",
    )
    args = parser.parse_args()

    # --- Resolve paths ---
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parents[2]

    benchmark_dir = args.benchmark_dir or script_dir
    schema_path = args.schema
    if schema_path is None:
        schema_path = repo_root / "src" / "CPM" / "outage_schema.json"
    if not schema_path.exists():
        raise SystemExit(f"Schema file not found: {schema_path}")

    # --- Load benchmark reference data ---
    pr_file = benchmark_dir / "priority_rules_results.json"
    best_file = benchmark_dir / "best_results.json"

    benchmark_pr: dict = {}
    if pr_file.exists():
        with open(pr_file, encoding="utf-8") as f:
            benchmark_pr = json.load(f)
    else:
        print(f"WARNING: {pr_file} not found — benchmark comparison skipped.")

    best_known: dict = {}
    if best_file.exists():
        with open(best_file, encoding="utf-8") as f:
            best_known = json.load(f)
    else:
        print(f"WARNING: {best_file} not found — best-known comparison skipped.")

    # --- Collect input files ---
    tasks: list[tuple[Path, str, str]] = []  # (json_path, schema_path, set_name)
    for set_name in args.sets:
        set_dir = args.psplib_json_dir / set_name
        if not set_dir.is_dir():
            print(f"WARNING: directory not found, skipping: {set_dir}", file=sys.stderr)
            continue
        files = sorted(set_dir.glob("*.json"))
        if args.max_files:
            files = files[: args.max_files]
        for f in files:
            tasks.append((str(f), str(schema_path), set_name))

    if not tasks:
        raise SystemExit("No JSON files found. Check --psplib-json-dir and --sets.")

    print(f"Running {len(tasks)} instances across {len(args.sets)} sets "
          f"with {args.workers} worker(s) ...")
    print(f"Output will be saved to: {args.out}")

    # --- Execute ---
    worker_args = [(json_path, schema, sname) for json_path, schema, sname in tasks]

    results: list[dict] = []
    errors = 0

    if args.workers > 1:
        with mp.Pool(processes=args.workers) as pool:
            for i, row in enumerate(pool.imap_unordered(_run_instance, worker_args), 1):
                if "error" in row:
                    print(f"  ERROR [{row.get('instance','?')}]: {row['error'][:200]}",
                          file=sys.stderr)
                    errors += 1
                else:
                    results.append(row)
                if i % 50 == 0 or i == len(tasks):
                    print(f"  ... {i}/{len(tasks)} done ({errors} errors)")
    else:
        # Single-process: import Pert here directly for efficiency
        sys.path.insert(0, str(repo_root))
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
        from src.CPM.pert import Pert  # noqa: PLC0415

        for i, (json_path, schema, set_name) in enumerate(tasks, 1):
            stem = Path(json_path).stem
            sm_name = stem + ".sm"
            row: dict = {"set": set_name, "instance": stem, "sm_name": sm_name}
            try:
                for rule in PRIORITY_RULES:
                    pert = Pert.from_json_file(json_path, schema_path=schema)
                    out = pert.calculateSerialScheduleWithResources(priority_rule=rule)
                    row[f"sgs_{rule}"] = out["scheduled_duration"] - DUMMY_OFFSET

                for sgs in SGS_METHODS:
                    for rule in PRIORITY_RULES:
                        pert = Pert.from_json_file(json_path, schema_path=schema)
                        out = pert.calculateScheduleWithResources(
                            sgs=sgs, priority_rule=rule
                        )
                        row[f"pgs_{sgs}_{rule}"] = out["scheduled_duration"] - DUMMY_OFFSET

                results.append(row)
            except Exception:
                row["error"] = traceback.format_exc()
                print(f"  ERROR [{stem}]: {traceback.format_exc()[:200]}", file=sys.stderr)
                errors += 1
                results.append(row)

            if i % 50 == 0 or i == len(tasks):
                print(f"  ... {i}/{len(tasks)} done ({errors} errors)")

    # --- Build DataFrame ---
    df = pd.DataFrame(results)

    # Sort by set, then instance name
    if "set" in df.columns and "instance" in df.columns:
        df = df.sort_values(["set", "instance"]).reset_index(drop=True)

    # --- Attach benchmark columns ---
    if benchmark_pr or best_known:
        df = _attach_benchmark_columns(df, benchmark_pr, best_known)

    # --- Save ---
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"\nSaved {len(df)} rows to {args.out}")

    # --- Summary ---
    if "gap_to_best_known" in df.columns:
        summary = (
            df.dropna(subset=["gap_to_best_known"])
            .groupby("set")["gap_to_best_known"]
            .agg(["mean", "min", "max", "count"])
        )
        print("\nGap to best-known makespan (LOGOS best - optimal):")
        print(summary.to_string())

    if errors:
        print(f"\n{errors} instance(s) had errors (see above). "
              "Error details are in the 'error' column of the CSV.")
        sys.exit(1)


if __name__ == "__main__":
    main()
