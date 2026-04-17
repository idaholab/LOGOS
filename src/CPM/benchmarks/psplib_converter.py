"""Convert PSPLIB benchmark files (.sm) to LOGOS outage JSON format.

Usage
-----
Convert a specific benchmark set (j30 / j60 / j90 / j120):
    python psplib_converter.py --psplib-dir PSPLIB/ --sets j30
    python psplib_converter.py --psplib-dir PSPLIB/ --sets j30 j60 j90 j120

Convert all four sets at once (default when only --psplib-dir is given):
    python psplib_converter.py --psplib-dir PSPLIB/

Single file:
    python psplib_converter.py PSPLIB/j30/j301_1.sm

Directory (converts all *.sm files found recursively):
    python psplib_converter.py PSPLIB/j30/

Common options:
    --out-dir results/      write all JSON files under results/ (mirroring
                            the set subdirectory structure)
    --start-date 2026-01-01
    --working-hours 8
    --mode 0
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import psplib  # pip install psplib

# PSPLIB on-disk folder names for each benchmark set.
# j120 is stored in j120rcp/ in the standard PSPLIB distribution.
PSPLIB_SET_DIRS: dict[str, str] = {
    "j30": "j30",
    "j60": "j60",
    "j90": "j90",
    "j120": "j120",
}


def to_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def convert_psplib_sm_to_outage_json(
    sm_path: str | Path,
    out_path: str | Path,
    outage_id: str = "PSPLIB_BENCH",
    start_date: str = "2026-01-01",
    working_hours_per_day: int = 24,
    mode_index: int = 0,
) -> None:
    """Convert a single PSPLIB .sm file to LOGOS outage JSON.

    Parameters
    ----------
    sm_path:
        Path to the PSPLIB .sm instance file.
    out_path:
        Destination .json file path.
    outage_id:
        Identifier written into the ``outage.outage_id`` field.
    start_date:
        ISO date string (YYYY-MM-DD) for the project start.
    working_hours_per_day:
        Used to convert PSPLIB time units to calendar hours.
        1 time unit = (24 / working_hours_per_day) hours.
    mode_index:
        Which activity mode to export (0 = first/only mode for
        single-mode RCPSP instances).
    """
    inst = psplib.parse(str(sm_path), instance_format="psplib")

    n_jobs = inst.num_activities
    n_res = inst.num_resources

    durations: list[int] = []
    demands: list[list[int]] = []
    successors: list[list[int]] = []

    for i, act in enumerate(inst.activities):
        if mode_index >= len(act.modes):
            raise ValueError(
                f"Activity {i + 1} has only {len(act.modes)} modes; "
                f"cannot use mode_index={mode_index}."
            )
        m = act.modes[mode_index]
        durations.append(int(m.duration))
        demands.append([int(x) for x in m.demands])
        successors.append(list(act.successors))

    capacities = [int(r.capacity) for r in inst.resources]

    horizon_units = sum(durations)
    start_dt = datetime.fromisoformat(start_date)
    hours_per_unit = 24.0 / float(working_hours_per_day)
    end_dt = start_dt + timedelta(hours=horizon_units * hours_per_unit)

    # Dummy source/sink nodes have duration 0; give them a minimal duration
    # so the scheduler does not stall.
    if durations[0] == 0:
        durations[0] = 1
    if durations[-1] == 0:
        durations[-1] = 1

    tasks = []
    for i in range(n_jobs):
        task_id = f"J{i + 1}"
        succ_ids = [f"J{j + 1}" for j in successors[i]]

        req_resources = []
        for r in range(n_res):
            d = demands[i][r]
            if d > 0:
                req_resources.append({"skill_type": f"R{r + 1}", "crew_count": d})

        tasks.append(
            {
                "task_id": task_id,
                "description": f"Activity {i + 1}",
                "duration": float(durations[i]),
                "successors": succ_ids,
                "location_id": None,
                "required_resources": req_resources,
                "required_equipment": [],
                "is_hold_point": False,
            }
        )

    resources = []
    for r in range(n_res):
        resources.append(
            {
                "skill_type": f"R{r + 1}",
                "availability_periods": [
                    {
                        "start_date": to_iso(start_dt),
                        "end_date": to_iso(end_dt),
                        "available_count": capacities[r],
                        "reason": "PSPLIB constant capacity",
                    }
                ],
            }
        )

    data = {
        "outage": {
            "outage_id": outage_id,
            "start_date": start_dt.date().isoformat(),
            "target_end_date": None,
            "working_hours_per_day": working_hours_per_day,
        },
        "tasks": tasks,
        "resources": resources,
        "equipment": [],
        "locations": [],
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _targets_from_sets(psplib_dir: Path, sets: list[str]) -> list[tuple[Path, str]]:
    """Return (sm_path, set_name) pairs for the requested benchmark sets.

    The set_name is used to mirror subdirectory structure under --out-dir.
    """
    targets: list[tuple[Path, str]] = []
    for set_name in sets:
        subdir_name = PSPLIB_SET_DIRS[set_name]
        subdir = psplib_dir / subdir_name
        if not subdir.is_dir():
            print(
                f"  WARNING: expected directory not found, skipping: {subdir}",
                file=sys.stderr,
            )
            continue
        sm_files = sorted(subdir.glob("*.sm"))
        if not sm_files:
            print(f"  WARNING: no .sm files found in {subdir}", file=sys.stderr)
            continue
        for f in sm_files:
            targets.append((f, set_name))
    return targets


def _targets_from_path(input_path: Path) -> list[tuple[Path, str]]:
    """Return (sm_path, '') pairs from a file or directory argument."""
    if input_path.is_file():
        if input_path.suffix.lower() != ".sm":
            raise SystemExit(f"Expected a .sm file, got: {input_path}")
        return [(input_path, "")]
    if input_path.is_dir():
        files = sorted(input_path.rglob("*.sm"))
        if not files:
            raise SystemExit(f"No .sm files found under: {input_path}")
        return [(f, "") for f in files]
    raise SystemExit(f"Path does not exist: {input_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert PSPLIB .sm benchmark files to LOGOS outage JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # --- input source (mutually exclusive styles) ---
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="Path to a single .sm file or a directory containing .sm files.",
    )
    input_group.add_argument(
        "--psplib-dir",
        type=Path,
        metavar="DIR",
        help=(
            "Root of the PSPLIB distribution (the folder that contains j30/, j60/, "
            "j90/, j120rcp/ subdirectories). Use with --sets to select specific sets."
        ),
    )

    parser.add_argument(
        "--sets",
        nargs="+",
        choices=list(PSPLIB_SET_DIRS),
        default=list(PSPLIB_SET_DIRS),
        metavar="SET",
        help=(
            "Which benchmark sets to convert when using --psplib-dir. "
            "Choices: j30 j60 j90 j120. Defaults to all four sets."
        ),
    )

    # --- output / conversion options ---
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Root output directory. JSON files are written under "
            "<out-dir>/<set>/<stem>.json. "
            "Defaults to the same directory as each input file."
        ),
    )
    parser.add_argument(
        "--start-date",
        default="2026-01-01",
        help="Project start date in YYYY-MM-DD format (default: 2026-01-01).",
    )
    parser.add_argument(
        "--working-hours",
        type=int,
        default=24,
        metavar="HOURS",
        help="Working hours per day used for calendar conversion (default: 24).",
    )
    parser.add_argument(
        "--mode",
        type=int,
        default=0,
        metavar="INDEX",
        help="Activity mode index to export (default: 0, correct for single-mode RCPSP).",
    )

    args = parser.parse_args()

    # Build the list of (sm_path, set_name) targets
    if args.psplib_dir is not None:
        if not args.psplib_dir.is_dir():
            raise SystemExit(f"--psplib-dir does not exist: {args.psplib_dir}")
        targets = _targets_from_sets(args.psplib_dir, args.sets)
        print(
            f"Converting sets {args.sets} from {args.psplib_dir} "
            f"({len(targets)} files) ..."
        )
    else:
        targets = _targets_from_path(args.input)

    converted = 0
    errors = 0
    for sm_path, set_name in targets:
        if args.out_dir is not None:
            # Mirror set subdirectory under out_dir
            sub = Path(set_name) if set_name else Path()
            out_path = args.out_dir / sub / (sm_path.stem + ".json")
        else:
            out_path = sm_path.parent / (sm_path.stem + ".json")

        outage_id = sm_path.stem.upper()
        try:
            convert_psplib_sm_to_outage_json(
                sm_path=sm_path,
                out_path=out_path,
                outage_id=outage_id,
                start_date=args.start_date,
                working_hours_per_day=args.working_hours,
                mode_index=args.mode,
            )
            print(f"  converted: {sm_path.name} -> {out_path}")
            converted += 1
        except Exception as exc:
            print(f"  ERROR: {sm_path}: {exc}", file=sys.stderr)
            errors += 1

    print(f"\n{converted} file(s) converted, {errors} error(s).")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
