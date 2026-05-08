"""
gga_test.py — Integration test for the RCPSP Graph Genetic Algorithm (gga.py)

Runs the GGA on each PSPLIB benchmark JSON (j30, j60, j90, j120) and prints
a comparison table of:
  - Best duration from all named priority rules (serial SGS baseline)
  - Best GGA duration (AON lag chromosome + serial SGS decoder)
  - Improvement over the best seeded solution and over the priority-rule baseline

The GGA uses an Activity-on-Node (AON) lag-based chromosome with a micro-GA
structure and frozen subgraph blocks (Liu et al., 2025).

Reference
---------
Liu, Y., Liu, X., and Huang, L. (2025). A graph-based genetic algorithm for
resource-constrained project scheduling problems. SSRN 5851447.

Usage (from the src/CPM directory):
    python gga_test.py

Or from the repo root:
    python -m src.CPM.gga_test
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import Pert  # noqa: E402
from src.CPM.gga import RCPSPGraphGeneticAlgorithm, PRIORITY_RULES  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
SCHEMA = Path(__file__).parent / "outage_schema.json"

CASES = [
    ("j30",  "j301_1.json"),
    ("j60",  "j601_1.json"),
    ("j90",  "j901_1.json"),
    ("j120", "j1201_1.json"),
]


def run_gga_case(
    case_name: str,
    json_file: str,
    ne: int = 50,
    n_gen: int = 500,
    restart_threshold: int = 50,
    rho: float = 0.2,
    seed: int = 42,
    verbose: bool = True,
) -> dict:
    """
    Run the GGA on a single PSPLIB benchmark case.

    Steps
    -----
    1. Load and initialise the Pert model.
    2. Record baseline durations for every named priority rule under serial SGS
       (same evaluations used to seed the GGA's initial population).
    3. Run the GGA (AON lag chromosome, micro-GA, serial SGS decoder).
    4. Print per-case results and return a summary dict.

    Parameters
    ----------
    case_name : str
        Human-readable label (e.g. 'j30').
    json_file : str
        Path to the input JSON relative to this file's directory.
    ne : int
        Elite pool size (micro-GA).
    n_gen : int
        Number of GA generations.
    restart_threshold : int
        Generations without improvement before population restart.
    rho : float
        Exclusion fraction for frozen-block node removal (Algorithm 2).
    seed : int
        RNG seed.
    verbose : bool
        Print per-generation stats and seeding table.

    Returns
    -------
    dict with keys:
        case, n_activities, cpm_duration,
        best_serial_seed, best_gga, improvement, n_restarts
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

    # ── Baseline: best duration from all named priority rules (serial SGS) ──
    serial_durations = {}
    for rule in PRIORITY_RULES:
        try:
            pert.priorities = None
            s_out = pert.calculateSerialScheduleWithResources(priority_rule=rule)
            serial_durations[rule] = s_out['scheduled_duration'] - 2
        except Exception as exc:  # noqa: BLE001
            logger.debug("Rule '%s' skipped in baseline: %s", rule, exc)

    best_serial = (
        min(serial_durations.values()) if serial_durations else float('inf')
    )

    if verbose:
        print("Priority rule baseline durations (serial SGS):")
        print(f"  {'Rule':<22} {'Serial (h)':>12}")
        print("  " + "-" * 36)
        for rule in PRIORITY_RULES:
            s = serial_durations.get(rule, float('nan'))
            print(f"  {rule:<22} {s:>12.2f}")
        print()

    # ── Run GGA ─────────────────────────────────────────────────────────────
    print(f"Running GGA (ne={ne}, n_gen={n_gen}, restart={restart_threshold}, rho={rho})...")
    gga = RCPSPGraphGeneticAlgorithm(
        pert,
        ne=ne,
        n_gen=n_gen,
        restart_threshold=restart_threshold,
        rho=rho,
        seed=seed,
        verbose=verbose,
    )
    winner, log = gga.run()

    best_gga = winner['fitness']
    summary = gga.get_convergence_summary(log)
    best_activity_list = gga.get_best_activity_list(winner)

    print()
    print(f"{'─' * 60}")
    print(f"  CPM duration (unconstrained)  : {cpm_duration:.2f} h")
    print(f"  Best serial SGS (all rules)   : {best_serial:.2f} h")
    print(f"  Best GGA duration             : {best_gga:.2f} h")
    print(f"  Improvement over best seed    : {summary['initial_best'] - best_gga:.2f} h")
    print(f"  Improvement over best rule    : {best_serial - best_gga:.2f} h")
    print(f"  Restarts                      : {summary['n_restarts']}")
    print(f"  Final stall count             : {summary['final_stall']}")
    print(f"{'─' * 60}")
    print(f"  Best activity list (first 10) : {best_activity_list[:10]}")
    print()

    return {
        'case': case_name,
        'n_activities': n_activities,
        'cpm_duration': cpm_duration,
        'best_serial_seed': best_serial,
        'best_gga': best_gga,
        'improvement': best_serial - best_gga,
        'n_restarts': summary['n_restarts'],
    }


def main() -> None:
    """Run the GGA on all benchmark cases and print a summary table."""
    results = []
    for case_name, json_file in CASES:
        result = run_gga_case(
            case_name=case_name,
            json_file=json_file,
            ne=50,
            n_gen=1000,
            restart_threshold=50,
            rho=0.2,
            seed=42,
            verbose=True,
        )
        results.append(result)

    print("\n" + "=" * 80)
    print("SUMMARY — GGA (AON lag chromosome, micro-GA, serial SGS decoder)")
    print("=" * 80)
    print(
        f"  {'Case':<8} {'N':>6} {'CPM (h)':>10} "
        f"{'Best Serial':>13} {'Best GGA':>10} {'Δ (h)':>8} {'Restarts':>10}"
    )
    print("  " + "-" * 70)
    for r in results:
        print(
            f"  {r['case']:<8} {r['n_activities']:>6} {r['cpm_duration']:>10.2f} "
            f"{r['best_serial_seed']:>13.2f} {r['best_gga']:>10.2f} "
            f"{r['improvement']:>8.2f} {r['n_restarts']:>10}"
        )
    print()


if __name__ == "__main__":
    main()
