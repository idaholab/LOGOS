"""Update an RCPLIB results CSV to use LPP UB-lit best-known values.

The updater reads ``RCPLIB_Solution.xlsx`` through ``extract_rcplib_solution``,
extracts the requested sheet's solution values, replaces ``best_known`` in the
target CSV, and recomputes ``gap_to_best_known`` as:

    logos_best - best_known

By default this updates:

    results_LPP_UB_lit/rcplib_results.csv

using the LPP sheet's ``UB-lit`` values.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]

try:
    from extract_rcplib_solution import DEFAULT_WORKBOOK, extract_solution_columns
except ModuleNotFoundError as exc:  # pragma: no cover - used with ``python -m``.
    if exc.name != "extract_rcplib_solution":
        raise
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from src.CPM.benchmarks.extract_rcplib_solution import (
        DEFAULT_WORKBOOK,
        extract_solution_columns,
    )


DEFAULT_RESULTS_CSV = SCRIPT_DIR / "results_LPP_UB_lit" / "rcplib_results.csv"
DEFAULT_SHEET = "LPP"
DEFAULT_BEST_KEY = "UB-lit"
REQUIRED_COLUMNS = {"rcp_name", "best_known", "logos_best", "gap_to_best_known"}


@dataclass(frozen=True)
class UpdateSummary:
    rows: int
    changed_rows: int
    solution_values: int
    missing_solution_rows: tuple[str, ...]


def parse_decimal(value: Any, column: str, rcp_name: str) -> Decimal | None:
    """Convert a CSV/workbook value to Decimal, preserving blanks as None."""
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(
            f"Could not parse {column!r} value {value!r} for {rcp_name!r}."
        ) from exc


def format_decimal(value: Decimal | None) -> str:
    """Format whole-number decimals as ints and leave blanks for None."""
    if value is None:
        return ""
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def load_solution_values(
    workbook_path: Path,
    sheet: str,
    best_key: str,
) -> dict[str, Decimal]:
    """Return ``{rcp_name: best_key_value}`` from the selected workbook sheet."""
    solution_data = extract_solution_columns(
        workbook_path=workbook_path,
        sheets=[sheet],
        key_style="header",
    )

    solution_values: dict[str, Decimal] = {}
    missing_key = 0
    for rcp_name, entry in solution_data.items():
        if not isinstance(entry, dict) or best_key not in entry:
            missing_key += 1
            continue

        value = parse_decimal(entry[best_key], best_key, rcp_name)
        if value is not None:
            solution_values[rcp_name] = value

    if missing_key:
        raise ValueError(
            f"{missing_key} solution entries from sheet {sheet!r} do not have "
            f"the key {best_key!r}."
        )

    return solution_values


def read_csv(csv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV file and validate the columns needed for this update."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{csv_path} is empty or has no header row.")

        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"{csv_path} is missing required columns: {missing}")

        return reader.fieldnames, list(reader)


def write_csv(csv_path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    """Atomically rewrite the CSV with the original field order."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        newline="",
        encoding="utf-8",
        dir=csv_path.parent,
        delete=False,
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        tmp_path = Path(tmp.name)

    os.replace(tmp_path, csv_path)


def update_results_csv(
    csv_path: Path,
    workbook_path: Path,
    sheet: str = DEFAULT_SHEET,
    best_key: str = DEFAULT_BEST_KEY,
    dry_run: bool = False,
) -> UpdateSummary:
    """Update ``best_known`` and ``gap_to_best_known`` in ``csv_path``."""
    solution_values = load_solution_values(workbook_path, sheet, best_key)
    fieldnames, rows = read_csv(csv_path)

    changed_rows = 0
    missing_solution_rows: list[str] = []
    for row in rows:
        rcp_name = row["rcp_name"].strip()
        best_known = solution_values.get(rcp_name)
        if best_known is None:
            missing_solution_rows.append(rcp_name)
            continue

        logos_best = parse_decimal(row.get("logos_best"), "logos_best", rcp_name)
        gap_to_best_known = (
            logos_best - best_known if logos_best is not None else None
        )

        new_best_known = format_decimal(best_known)
        new_gap = format_decimal(gap_to_best_known)
        if (
            row.get("best_known") != new_best_known
            or row.get("gap_to_best_known") != new_gap
        ):
            changed_rows += 1

        row["best_known"] = new_best_known
        row["gap_to_best_known"] = new_gap

    if missing_solution_rows:
        preview = ", ".join(missing_solution_rows[:10])
        suffix = "..." if len(missing_solution_rows) > 10 else ""
        raise ValueError(
            f"{len(missing_solution_rows)} CSV row(s) had no {best_key!r} value "
            f"from sheet {sheet!r}: {preview}{suffix}"
        )

    if not dry_run:
        write_csv(csv_path, fieldnames, rows)

    return UpdateSummary(
        rows=len(rows),
        changed_rows=changed_rows,
        solution_values=len(solution_values),
        missing_solution_rows=tuple(missing_solution_rows),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replace rcplib_results.csv best_known values with RCPLIB "
            "Solution workbook UB-lit values and recompute gaps."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_RESULTS_CSV,
        help=f"Results CSV to update (default: {DEFAULT_RESULTS_CSV}).",
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=DEFAULT_WORKBOOK,
        help=f"RCPLIB solution workbook (default: {DEFAULT_WORKBOOK}).",
    )
    parser.add_argument(
        "--sheet",
        default=DEFAULT_SHEET,
        help=f"Workbook sheet to extract (default: {DEFAULT_SHEET}).",
    )
    parser.add_argument(
        "--best-key",
        default=DEFAULT_BEST_KEY,
        help=f"Extracted solution key to use (default: {DEFAULT_BEST_KEY}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report changes without rewriting the CSV.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = update_results_csv(
        csv_path=args.csv,
        workbook_path=args.workbook,
        sheet=args.sheet,
        best_key=args.best_key,
        dry_run=args.dry_run,
    )

    action = "Would update" if args.dry_run else "Updated"
    print(
        f"{action} {summary.changed_rows} of {summary.rows} row(s) in {args.csv} "
        f"using {summary.solution_values} {args.sheet} {args.best_key} value(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
