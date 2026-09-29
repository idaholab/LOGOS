"""Shared command-line case selection for heuristic benchmark scripts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence, TypeVar


T = TypeVar("T", str, Path)


def parse_case_name(value: str, cases: Sequence[tuple[str, T]]) -> str:
    """Return the configured case label matching a label or JSON filename."""
    aliases: dict[str, str] = {}
    for label, filename in cases:
        path = Path(filename)
        for alias in (label, path.name, path.stem):
            aliases[alias] = label

    candidate = Path(value).name
    if candidate in aliases:
        return aliases[candidate]

    available = ", ".join(label for label, _ in cases)
    raise argparse.ArgumentTypeError(
        f"unknown case {value!r}; available cases: {available}"
    )


def add_case_argument(
    parser: argparse.ArgumentParser,
    cases: Sequence[tuple[str, T]],
) -> None:
    """Add a repeatable ``--case`` option to a benchmark parser."""
    parser.add_argument(
        "--case",
        action="append",
        type=lambda value: parse_case_name(value, cases),
        help=(
            "Run only the selected case by label or JSON filename. "
            "May be supplied more than once; defaults to all cases."
        ),
    )


def select_cases(
    cases: Sequence[tuple[str, T]],
    selected: Sequence[str] | None,
) -> list[tuple[str, T]]:
    """Filter configured cases in declaration order."""
    if not selected:
        return list(cases)
    selected_names = set(selected)
    return [(name, filename) for name, filename in cases if name in selected_names]


def resolve_benchmark_case(value: str, benchmark_dir: Path) -> Path:
    """Resolve a case stem or JSON filename inside the benchmark directory."""
    filename = Path(value).name
    if Path(filename).suffix == "":
        filename += ".json"
    elif Path(filename).suffix.lower() != ".json":
        raise argparse.ArgumentTypeError("--case must name a JSON benchmark file")

    path = (benchmark_dir / filename).resolve()
    if path.is_file():
        return path

    available = ", ".join(sorted(candidate.stem for candidate in benchmark_dir.glob("*.json")))
    raise argparse.ArgumentTypeError(
        f"unknown benchmark case {value!r}; available cases: {available}"
    )
