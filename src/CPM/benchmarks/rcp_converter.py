"""Convert Patterson/RG RCPSP benchmark files (.rcp) to LOGOS outage JSON.

The compact ``.rcp`` format used by the RG300/LPP benchmark files is an integer
token stream:

1. ``n_jobs n_resources``
2. ``n_resources`` renewable resource capacities
3. One activity record for each job ``1..n_jobs``:
   ``duration demand_R1 ... demand_Rm n_successors succ_1 ... succ_k``

Records may wrap across physical lines, so this converter parses the file as a
flat sequence of integers and uses ``n_successors`` to delimit each activity.

Usage
-----
Single file:
    python rcp_converter.py RG300/RG300_10.rcp

Directory, recursively converting all *.rcp files:
    python rcp_converter.py RG300/

Benchmark root with selected sets:
    python rcp_converter.py --rcp-dir . --sets RG300 LPP --out-dir RCP_Json/

Common options:
    --out-dir results/      write JSON files under results/, mirroring input
                            subdirectories for directory/set conversion
    --start-date 2026-01-01
    --working-hours 24
    --zero-duration 1       duration assigned to dummy zero-duration nodes
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


# Known benchmark set folders currently present in src/CPM/benchmarks.
DEFAULT_RCP_SETS = ["RG300", "LPP"]


@dataclass(frozen=True)
class RCPActivity:
    """One activity record from a compact .rcp file."""

    job_id: int
    duration: int
    demands: list[int]
    successors: list[int]


@dataclass(frozen=True)
class RCPInstance:
    """Parsed compact .rcp instance."""

    num_jobs: int
    num_resources: int
    capacities: list[int]
    activities: list[RCPActivity]


def to_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_rcp_file(rcp_path: str | Path) -> RCPInstance:
    """Parse a compact Patterson/RG ``.rcp`` file.

    The parser intentionally ignores physical line breaks after tokenization
    because long successor lists are wrapped in the benchmark files.
    """
    path = Path(rcp_path)
    try:
        tokens = [int(token) for token in path.read_text(encoding="utf-8").split()]
    except ValueError as exc:
        raise ValueError(f"{path} contains a non-integer token.") from exc

    if len(tokens) < 2:
        raise ValueError(f"{path} is too short to contain an RCP header.")

    num_jobs = tokens[0]
    num_resources = tokens[1]
    if num_jobs <= 0:
        raise ValueError(f"{path}: num_jobs must be positive, got {num_jobs}.")
    if num_resources <= 0:
        raise ValueError(
            f"{path}: num_resources must be positive, got {num_resources}."
        )

    pos = 2
    if len(tokens) < pos + num_resources:
        raise ValueError(f"{path}: missing resource capacity line.")

    capacities = tokens[pos : pos + num_resources]
    if any(capacity < 0 for capacity in capacities):
        raise ValueError(f"{path}: resource capacities must be non-negative.")
    pos += num_resources

    activities: list[RCPActivity] = []
    for job_id in range(1, num_jobs + 1):
        min_record_tokens = 1 + num_resources + 1
        if pos + min_record_tokens > len(tokens):
            raise ValueError(
                f"{path}: activity {job_id} is incomplete at token offset {pos}."
            )

        duration = tokens[pos]
        demands = tokens[pos + 1 : pos + 1 + num_resources]
        n_successors = tokens[pos + 1 + num_resources]
        pos += min_record_tokens

        if duration < 0:
            raise ValueError(f"{path}: activity {job_id} has negative duration.")
        if any(demand < 0 for demand in demands):
            raise ValueError(f"{path}: activity {job_id} has negative demand.")
        if n_successors < 0:
            raise ValueError(f"{path}: activity {job_id} has negative successor count.")
        if pos + n_successors > len(tokens):
            raise ValueError(
                f"{path}: activity {job_id} declares {n_successors} successors "
                "but the file ends first."
            )

        successors = tokens[pos : pos + n_successors]
        pos += n_successors
        for successor in successors:
            if successor < 1 or successor > num_jobs:
                raise ValueError(
                    f"{path}: activity {job_id} references invalid successor "
                    f"{successor}; valid range is 1..{num_jobs}."
                )

        activities.append(
            RCPActivity(
                job_id=job_id,
                duration=duration,
                demands=demands,
                successors=successors,
            )
        )

    if pos != len(tokens):
        raise ValueError(
            f"{path}: parsed {pos} integer tokens but file contains {len(tokens)}."
        )

    return RCPInstance(
        num_jobs=num_jobs,
        num_resources=num_resources,
        capacities=capacities,
        activities=activities,
    )


def convert_rcp_to_outage_dict(
    rcp_path: str | Path,
    outage_id: str | None = None,
    start_date: str = "2026-01-01",
    working_hours_per_day: int = 24,
    zero_duration: float = 1.0,
) -> dict:
    """Convert a compact ``.rcp`` file to a LOGOS outage JSON dictionary."""
    if not 1 <= working_hours_per_day <= 24:
        raise ValueError("working_hours_per_day must be in [1, 24].")
    if zero_duration <= 0:
        raise ValueError("zero_duration must be positive for LOGOS schema validity.")

    path = Path(rcp_path)
    instance = parse_rcp_file(path)
    start_dt = datetime.fromisoformat(start_date)

    normalized_durations = [
        float(activity.duration) if activity.duration > 0 else float(zero_duration)
        for activity in instance.activities
    ]

    # Keep the same calendar convention as the old PSPLIB converter:
    # one benchmark time unit spans 24 / working_hours_per_day clock hours.
    hours_per_unit = 24.0 / float(working_hours_per_day)
    horizon_units = sum(normalized_durations)
    end_dt = start_dt + timedelta(hours=horizon_units * hours_per_unit)

    tasks = []
    for activity, duration in zip(
        instance.activities,
        normalized_durations,
        strict=True,
    ):
        task_id = f"J{activity.job_id}"
        succ_ids = [f"J{successor}" for successor in activity.successors]

        req_resources = []
        for resource_idx, demand in enumerate(activity.demands, start=1):
            if demand > 0:
                req_resources.append(
                    {"skill_type": f"R{resource_idx}", "crew_count": int(demand)}
                )

        tasks.append(
            {
                "task_id": task_id,
                "description": f"Activity {activity.job_id}",
                "duration": duration,
                "successors": succ_ids,
                "location_id": None,
                "required_resources": req_resources,
                "required_equipment": [],
                "is_hold_point": False,
            }
        )

    resources = []
    for resource_idx, capacity in enumerate(instance.capacities, start=1):
        resources.append(
            {
                "skill_type": f"R{resource_idx}",
                "availability_periods": [
                    {
                        "start_date": to_iso(start_dt),
                        "end_date": to_iso(end_dt),
                        "available_count": int(capacity),
                        "reason": "RCP constant capacity",
                    }
                ],
            }
        )

    return {
        "outage": {
            "outage_id": outage_id or path.stem.upper(),
            "start_date": start_dt.date().isoformat(),
            "target_end_date": None,
            "working_hours_per_day": working_hours_per_day,
        },
        "tasks": tasks,
        "resources": resources,
        "equipment": [],
        "locations": [],
    }


def convert_rcp_to_outage_json(
    rcp_path: str | Path,
    out_path: str | Path,
    outage_id: str | None = None,
    start_date: str = "2026-01-01",
    working_hours_per_day: int = 24,
    zero_duration: float = 1.0,
) -> None:
    """Convert one compact ``.rcp`` file to a LOGOS outage JSON file."""
    data = convert_rcp_to_outage_dict(
        rcp_path=rcp_path,
        outage_id=outage_id,
        start_date=start_date,
        working_hours_per_day=working_hours_per_day,
        zero_duration=zero_duration,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _targets_from_sets(rcp_dir: Path, sets: list[str]) -> list[tuple[Path, Path]]:
    """Return ``(rcp_path, relative_subdir)`` pairs for named set folders."""
    targets: list[tuple[Path, Path]] = []
    for set_name in sets:
        subdir = rcp_dir / set_name
        if not subdir.is_dir():
            print(
                f"  WARNING: expected directory not found, skipping: {subdir}",
                file=sys.stderr,
            )
            continue
        rcp_files = sorted(subdir.glob("*.rcp"))
        if not rcp_files:
            print(f"  WARNING: no .rcp files found in {subdir}", file=sys.stderr)
            continue
        for path in rcp_files:
            targets.append((path, Path(set_name)))
    return targets


def _targets_from_path(input_path: Path) -> list[tuple[Path, Path]]:
    """Return ``(rcp_path, relative_subdir)`` pairs from a file or directory."""
    if input_path.is_file():
        if input_path.suffix.lower() != ".rcp":
            raise SystemExit(f"Expected a .rcp file, got: {input_path}")
        return [(input_path, Path())]

    if input_path.is_dir():
        files = sorted(input_path.rglob("*.rcp"))
        if not files:
            raise SystemExit(f"No .rcp files found under: {input_path}")
        return [(path, path.parent.relative_to(input_path)) for path in files]

    raise SystemExit(f"Path does not exist: {input_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert compact .rcp benchmark files to LOGOS outage JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="Path to a single .rcp file or a directory containing .rcp files.",
    )
    input_group.add_argument(
        "--rcp-dir",
        type=Path,
        metavar="DIR",
        help=(
            "Benchmark root directory containing set subdirectories such as "
            "RG300/ and LPP/. Use with --sets to select specific sets."
        ),
    )

    parser.add_argument(
        "--sets",
        nargs="+",
        default=DEFAULT_RCP_SETS,
        metavar="SET",
        help=(
            "Set folders to convert when using --rcp-dir. "
            f"Default: {' '.join(DEFAULT_RCP_SETS)}."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Root output directory. Directory and --rcp-dir conversions mirror "
            "input subdirectories under this root. Defaults to each input file's "
            "directory."
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
        "--zero-duration",
        type=float,
        default=1.0,
        metavar="HOURS",
        help=(
            "Positive duration assigned to zero-duration dummy activities "
            "because the LOGOS schema requires duration > 0 (default: 1)."
        ),
    )

    args = parser.parse_args()

    if args.rcp_dir is not None:
        if not args.rcp_dir.is_dir():
            raise SystemExit(f"--rcp-dir does not exist: {args.rcp_dir}")
        targets = _targets_from_sets(args.rcp_dir, args.sets)
        print(
            f"Converting sets {args.sets} from {args.rcp_dir} "
            f"({len(targets)} files) ..."
        )
    else:
        targets = _targets_from_path(args.input)

    converted = 0
    errors = 0
    for rcp_path, relative_subdir in targets:
        if args.out_dir is not None:
            out_path = args.out_dir / relative_subdir / (rcp_path.stem + ".json")
        else:
            out_path = rcp_path.parent / (rcp_path.stem + ".json")

        try:
            convert_rcp_to_outage_json(
                rcp_path=rcp_path,
                out_path=out_path,
                outage_id=rcp_path.stem.upper(),
                start_date=args.start_date,
                working_hours_per_day=args.working_hours,
                zero_duration=args.zero_duration,
            )
            print(f"  converted: {rcp_path.name} -> {out_path}")
            converted += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {rcp_path}: {exc}", file=sys.stderr)
            errors += 1

    print(f"\n{converted} file(s) converted, {errors} error(s).")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
