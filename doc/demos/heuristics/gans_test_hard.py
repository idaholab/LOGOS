"""
gans_test_hard.py — Hard-instance integration test for the RCPSP GANS solver

Runs the hybrid GA + neighborhood search algorithm on a selected set of
difficult PSPLIB j120 benchmark instances and prints:
  - Best GANS duration
  - Best-known PSPLIB duration
  - Gap between GANS and the best-known solution

Usage (from the repository root):
    python doc/demos/heuristics/gans_test_hard.py \
        --data-dir /path/to/PSPLIB_Json
"""

import argparse
import json
import logging
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from src import Pert  # noqa: E402
from src.CPM.gans import RCPSPHybridGANS, PRIORITY_RULES  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCHEMA = (REPO_ROOT / "src" / "CPM" / "outage_schema.json").resolve()
BEST_RESULTS_PATH = (
    REPO_ROOT / "doc" / "demos" / "benchmarks" / "best_results.json"
).resolve()

# Paths are relative to the external PSPLIB_Json root supplied by --data-dir.
CASES = [
    ("j12051_6", Path("j120/j12051_6.json")),
    ("j12031_10", Path("j120/j12031_10.json")),
    ("j12036_6", Path("j120/j12036_6.json")),
    ("j12056_7", Path("j120/j12056_7.json")),
    ("j12051_5", Path("j120/j12051_5.json")),
    ("j12056_1", Path("j120/j12056_1.json")),
    ("j12026_10", Path("j120/j12026_10.json")),
    ("j12051_7", Path("j120/j12051_7.json")),
    ("j12056_5", Path("j120/j12056_5.json")),
    ("j12056_9", Path("j120/j12056_9.json")),
]

# ==========================================================================================
# SUMMARY — GANS HARD CASES (PSPLIB j120, best-known comparison)
# ==========================================================================================
#   Case              N    CPM (h)   Best Serial  Best Parallel    Best GANS   Best Known   GANS-BK    Δ (h)   NS#
#   --------------------------------------------------------------------------------------------------------------------
#   j12051_6        122     106.00        262.00         256.00       230.00       214.00     16.00    26.00     4
#   j12031_10       122      92.00        285.00         266.00       250.00       225.00     25.00    16.00     4
#   j12036_6        122     103.00        271.00         263.00       253.00       224.00     29.00    10.00     4
#   j12056_7        122     119.00        336.00         320.00       314.00       282.00     32.00     6.00     4
#   j12051_5        122     104.00        280.00         266.00       254.00       229.00     25.00    12.00     4
#   j12056_1        122      97.00        279.00         273.00       263.00       236.00     27.00    10.00     4
#   j12026_10       122     124.00        224.00         219.00       187.00       183.00      4.00    32.00     4
#   j12051_7        122      93.00        254.00         247.00       218.00       211.00      7.00    29.00     4
#   j12056_5        122     119.00        338.00         315.00       305.00       279.00     26.00    10.00     4
#   j12056_9        122     103.00        341.00         325.00       321.00       287.00     34.00     4.00     4


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line options for the external hard-instance data set."""
    parser = argparse.ArgumentParser(
        description="Run GANS on selected hard PSPLIB j120 instances."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="path to the external PSPLIB_Json root (the directory containing j120/)",
    )
    return parser.parse_args(argv)


def preflight_inputs(data_dir: Path) -> list[tuple[str, Path]]:
    """Resolve all inputs and report every missing file in one actionable error."""
    data_root = data_dir.expanduser().resolve()
    case_paths = [(name, (data_root / relative_path).resolve()) for name, relative_path in CASES]
    expected = [
        ("canonical outage schema", SCHEMA),
        ("canonical best-known results", BEST_RESULTS_PATH),
        *((f"benchmark case {name}", path) for name, path in case_paths),
    ]
    missing = [(label, path) for label, path in expected if not path.is_file()]
    if missing:
        missing_lines = "\n".join(f"  - {label}: {path}" for label, path in missing)
        raise FileNotFoundError(
            "Hard-instance benchmark inputs are incomplete.\n"
            f"Missing required files:\n{missing_lines}\n"
            "Pass --data-dir pointing to the external PSPLIB_Json root; it must "
            "contain the j120/ directory and the listed JSON files."
        )
    return case_paths


def load_best_known_results(best_results_path: Path = BEST_RESULTS_PATH) -> dict[str, float]:
    """Load PSPLIB best-known results keyed by ``<instance>.sm``."""
    with best_results_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_best_known_result(best_results: dict[str, float], json_path: Path) -> float | None:
    """Resolve the benchmark JSON path to the corresponding PSPLIB result key."""
    instance_key = f"{json_path.stem}.sm"
    return best_results.get(instance_key)


def run_gans_case(
    case_name: str,
    json_path: Path,
    best_known: float | None,
    pop_size: int = 60,
    lambda_max: int = 5000,
    ga_stall_limit: int = 50,
    ns_steps: int = 200,
    block_size: int = 6,
    resource_threshold: float = 0.75,
    seed: int = 42,
    verbose: bool = True,
) -> dict:
    """Run GANS on one resolved PSPLIB benchmark JSON path."""
    json_path = json_path.resolve()

    print("=" * 70)
    print(f"Case: {case_name}  ({json_path})")
    print("=" * 70)

    pert = Pert.from_json_file(str(json_path), schema_path=str(SCHEMA))
    pert.generateInfo()

    cpm_duration = pert.getProjectDuration()
    n_activities = len(pert.infoDict)

    print(f"Activities      : {n_activities}")
    print(f"CPM duration    : {cpm_duration:.2f} h  (unconstrained)")
    print()

    serial_durations = {}
    parallel_durations = {}

    for rule in PRIORITY_RULES:
        try:
            pert.priorities = None
            s_out = pert.calculateSerialScheduleWithResources(priority_rule=rule)
            serial_durations[rule] = s_out["scheduled_duration"] - 2

            pert.priorities = None
            p_out = pert.calculateScheduleWithResources(
                sgs="max_use_res_ranked", priority_rule=rule
            )
            parallel_durations[rule] = p_out["scheduled_duration"] - 2
        except Exception as exc:  # noqa: BLE001
            logger.debug("Rule '%s' skipped in baseline: %s", rule, exc)

    best_serial = min(serial_durations.values()) if serial_durations else float("inf")
    best_parallel = min(parallel_durations.values()) if parallel_durations else float("inf")
    best_rule_overall = min(best_serial, best_parallel)

    print(
        f"Running GANS (pop={pop_size}, λ_max={lambda_max}, "
        f"stall={ga_stall_limit}, ns_steps={ns_steps})..."
    )
    gans = RCPSPHybridGANS(
        pert,
        pop_size=pop_size,
        lambda_max=lambda_max,
        ga_stall_limit=ga_stall_limit,
        ns_steps=ns_steps,
        block_size=block_size,
        resource_threshold=resource_threshold,
        seed=seed,
        verbose=verbose,
    )
    gans_best, gans_log = gans.run()
    best_gans = gans_best["fitness"]
    gans_summary = gans.get_convergence_summary(gans_log)
    gans_vs_best_known = (
        best_gans - best_known if best_known is not None else float("nan")
    )

    # Report the effective controls. Explicit values are preserved; omitted
    # controls would be selected by the instance classification.
    effective_ga_stall_limit = gans.ga_stall_limit
    effective_ns_steps = gans.ns_steps

    print()
    print(f"{'─' * 60}")
    print(f"  CPM duration (unconstrained)  : {cpm_duration:.2f} h")
    print(f"  Best serial SGS  (all rules)  : {best_serial:.2f} h")
    print(f"  Best parallel SGS (all rules) : {best_parallel:.2f} h")
    print(f"  Best GANS duration            : {best_gans:.2f} h")
    print(f"  Improvement over best rule    : {best_rule_overall - best_gans:.2f} h")
    if best_known is not None:
        print(f"  Best-known solution           : {best_known:.2f} h")
        print(f"  GANS - best-known             : {gans_vs_best_known:.2f} h")
    else:
        print("  Best-known solution           : n/a")
        print("  GANS - best-known             : n/a")
    print(f"  Effective GA stall limit      : {effective_ga_stall_limit}")
    print(f"  Effective NS steps            : {effective_ns_steps}")
    print(f"  GANS initial best             : {gans_summary['initial_best']:.2f} h")
    print(f"  GANS improvement              : {gans_summary['improvement']:.2f} h")
    print(f"  GANS NS activations           : {gans_summary['n_ns_activations']}")
    print(f"{'─' * 60}")
    print(f"  GANS best list (first 10)     : {gans.get_best_activity_list(gans_best)[:10]}")
    print()

    return {
        "case": case_name,
        "n_activities": n_activities,
        "cpm_duration": cpm_duration,
        "best_serial_seed": best_serial,
        "best_parallel_seed": best_parallel,
        "best_gans": best_gans,
        "best_known": best_known,
        "gans_vs_best_known": gans_vs_best_known,
        "improvement": best_rule_overall - best_gans,
        "gans_n_ns": gans_summary["n_ns_activations"],
        "effective_ga_stall_limit": effective_ga_stall_limit,
        "effective_ns_steps": effective_ns_steps,
    }


def main() -> None:
    """Run GANS on all hard j120 benchmark cases and print a summary table."""
    args = parse_args()
    try:
        case_paths = preflight_inputs(args.data_dir)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc

    best_results = load_best_known_results(BEST_RESULTS_PATH)
    results = []

    for case_name, json_path in case_paths:
        best_known = get_best_known_result(best_results, json_path)
        result = run_gans_case(
            case_name=case_name,
            json_path=json_path,
            best_known=best_known,
            pop_size=60,
            lambda_max=5000,
            ga_stall_limit=50,
            ns_steps=200,
            block_size=6,
            resource_threshold=0.75,
            seed=42,
            verbose=True,
        )
        results.append(result)

    print("\n" + "=" * 90)
    print("SUMMARY — GANS HARD CASES (PSPLIB j120, best-known comparison)")
    print("=" * 90)
    print("Effective stall/NS controls are shown for each case.")
    print(
        f"  {'Case':<12} {'N':>6} {'CPM (h)':>10} {'Best Serial':>13} "
        f"{'Best Parallel':>14} {'Best GANS':>12} {'Best Known':>12} "
        f"{'GANS-BK':>9} {'Δ (h)':>8} {'NS#':>5} {'Stall':>7} {'NS Steps':>9}"
    )
    print("  " + "-" * 136)
    for r in results:
        best_known_str = f"{r['best_known']:.2f}" if r["best_known"] is not None else "n/a"
        gans_gap_str = (
            f"{r['gans_vs_best_known']:.2f}"
            if r["best_known"] is not None
            else "n/a"
        )
        print(
            f"  {r['case']:<12} {r['n_activities']:>6} {r['cpm_duration']:>10.2f} "
            f"{r['best_serial_seed']:>13.2f} {r['best_parallel_seed']:>14.2f} "
            f"{r['best_gans']:>12.2f} {best_known_str:>12} {gans_gap_str:>9} "
            f"{r['improvement']:>8.2f} {r['gans_n_ns']:>5} "
            f"{r['effective_ga_stall_limit']:>7} {r['effective_ns_steps']:>9}"
        )
    print()


if __name__ == "__main__":
    main()
