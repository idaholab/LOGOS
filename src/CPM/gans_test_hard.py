"""
gans_test_hard.py — Hard-instance integration test for the RCPSP GANS solver

Runs the hybrid GA + neighbourhood search algorithm on a selected set of
difficult PSPLIB j120 benchmark instances and prints:
  - Best GANS duration
  - Best-known PSPLIB duration
  - Gap between GANS and the best-known solution

Usage (from the src/CPM directory):
    python gans_test_hard.py

Or from the repo root:
    python -m src.CPM.gans_test_hard
"""

import sys
import logging
import json
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import Pert  # noqa: E402
from src.CPM.gans import RCPSPHybridGANS, PRIORITY_RULES  # noqa: E402

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


def load_best_known_results() -> dict[str, float]:
    """Load PSPLIB best-known results keyed by `<instance>.sm`."""
    with BEST_RESULTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_best_known_result(best_results: dict[str, float], json_file: str) -> float | None:
    """Resolve the benchmark JSON filename to the corresponding PSPLIB result key."""
    instance_key = f"{Path(json_file).stem}.sm"
    return best_results.get(instance_key)


def run_gans_case(
    case_name: str,
    json_file: str,
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
    """
    Run GANS on a single hard PSPLIB benchmark case.

    Returns
    -------
    dict with keys:
        case, n_activities, cpm_duration, best_serial_seed, best_parallel_seed,
        best_gans, best_known, gans_vs_best_known, improvement, gans_n_ns
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
    best_gans = gans_best['fitness']
    gans_summary = gans.get_convergence_summary(gans_log)
    gans_vs_best_known = (
        best_gans - best_known if best_known is not None else float('nan')
    )

    print()
    print(f"{'─' * 60}")
    print(f"  CPM duration (unconstrained)  : {cpm_duration:.2f} h")
    print(f"  Best serial SGS  (all rules)  : {best_serial:.2f} h")
    print(f"  Best parallel SGS (all rules) : {best_parallel:.2f} h")
    print(f"  Best GANS duration            : {best_gans:.2f} h")
    print(f"  Improvement over best rule    : {best_rule_overall - best_gans:.2f} h")
    if best_known is not None:
        print(f"  Best-known solution           : {best_known:.2f} h")
        print(f"  GANS - best-known            : {gans_vs_best_known:.2f} h")
    else:
        print("  Best-known solution           : n/a")
        print("  GANS - best-known            : n/a")
    print(f"  GANS initial best             : {gans_summary['initial_best']:.2f} h")
    print(f"  GANS improvement              : {gans_summary['improvement']:.2f} h")
    print(f"  GANS NS activations           : {gans_summary['n_ns_activations']}")
    print(f"{'─' * 60}")
    print(f"  GANS best list (first 10)     : {gans.get_best_activity_list(gans_best)[:10]}")
    print()

    return {
        'case': case_name,
        'n_activities': n_activities,
        'cpm_duration': cpm_duration,
        'best_serial_seed': best_serial,
        'best_parallel_seed': best_parallel,
        'best_gans': best_gans,
        'best_known': best_known,
        'gans_vs_best_known': gans_vs_best_known,
        'improvement': best_rule_overall - best_gans,
        'gans_n_ns': gans_summary['n_ns_activations'],
    }


def main() -> None:
    """Run GANS on all hard j120 benchmark cases and print a summary table."""
    best_results = load_best_known_results()
    results = []

    for case_name, json_file in CASES:
        best_known = get_best_known_result(best_results, json_file)
        result = run_gans_case(
            case_name=case_name,
            json_file=json_file,
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
    print(
        f"  {'Case':<12} {'N':>6} {'CPM (h)':>10} {'Best Serial':>13} "
        f"{'Best Parallel':>14} {'Best GANS':>12} {'Best Known':>12} "
        f"{'GANS-BK':>9} {'Δ (h)':>8} {'NS#':>5}"
    )
    print("  " + "-" * 116)
    for r in results:
        best_known_str = f"{r['best_known']:.2f}" if r['best_known'] is not None else "n/a"
        gans_gap_str = (
            f"{r['gans_vs_best_known']:.2f}"
            if r['best_known'] is not None
            else "n/a"
        )
        print(
            f"  {r['case']:<12} {r['n_activities']:>6} {r['cpm_duration']:>10.2f} "
            f"{r['best_serial_seed']:>13.2f} {r['best_parallel_seed']:>14.2f} "
            f"{r['best_gans']:>12.2f} {best_known_str:>12} {gans_gap_str:>9} "
            f"{r['improvement']:>8.2f} {r['gans_n_ns']:>5}"
        )
    print()


if __name__ == "__main__":
    main()
