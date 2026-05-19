"""Plot RCPLIB benchmark results from test_rcplib_benchmark.py.

The input CSV is expected to contain the columns written by
``test_rcplib_benchmark.py``:

* ``set``, ``instance``, ``rcp_name``
* ``sgs_*`` priority-rule schedule lengths
* ``pgs_max_use_res_ranked_*`` priority-rule schedule lengths
* ``logos_best`` and ``gap_to_best_known``

Unlike the PSPLIB plotter, this script does not use ``dev_sgs_*`` benchmark
comparison columns. It reloads RCPLIB solution values from
``rcplib_solution_results.json`` by ``rcp_name`` and uses the selected key as
``best_known`` before computing gaps. The default key is ``UB-lit``.

Usage
-----
python plot_rcplib_results.py --csv rg300_results.csv --output-dir rg300_plots/
python plot_rcplib_results.py --csv lpp_results.csv --output-dir lpp_plots/
python plot_rcplib_results.py --csv lpp_results.csv --best-key UB-lit
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from pathlib import Path
from typing import Any


CSV_PATH = Path(__file__).parent / "rcplib_results.csv"
OUTPUT_DIR = Path(__file__).parent / "results_rcplib"
SOLUTION_PATH = Path(__file__).parent / "rcplib_solution_results.json"
BEST_KNOWN_KEY = "UB-lit"

SETS = ["RG300", "LPP"]
SET_COLORS = {"RG300": "steelblue", "LPP": "seagreen"}

_RULE_SUFFIXES = {
    "es": "ES (Early Start)",
    "ef": "EF (Early Finish)",
    "ls": "LS (Late Start)",
    "lf": "LF (Late Finish)",
    "duration": "Duration",
    "random": "Random",
    "mts": "MTS",
    "mtp": "MTP",
    "grpw": "GRPW",
    "grd": "GRD",
    "rr": "RR",
    "avgrr": "AvgRR",
    "maxrr": "MaxRR",
    "minrr": "MinRR",
    "irsm": "IRSM",
    "wcs": "WCS",
    "acs": "ACS",
    "mehh_8000_b": "MEHH-8000",
    "mehh_3375_b": "MEHH-3375",
    "mehh_1000_b": "MEHH-1000",
    "mehh_125_b": "MEHH-125",
    "gphh_b": "GPHH",
}

SGS_RULE_LABELS = {f"sgs_{key}": label for key, label in _RULE_SUFFIXES.items()}
PGS_RULE_LABELS = {
    f"pgs_max_use_res_ranked_{key}": label
    for key, label in _RULE_SUFFIXES.items()
}

BASE_COLUMNS = {"set", "instance", "rcp_name", "error"}


def import_pyplot():
    """Import matplotlib lazily so CLI help works without plot dependencies."""
    try:
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError as exc:
        raise SystemExit(
            "matplotlib is required to create plots. Install it in the "
            "environment used to run this script."
        ) from exc
    return plt


def parse_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_number(value: float | None) -> int | float | None:
    if value is None:
        return None
    if value.is_integer():
        return int(value)
    return round(value, 6)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def std(values: list[float]) -> float | None:
    if len(values) < 2:
        return 0.0 if values else None
    avg = sum(values) / len(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def load_results(csv_path: Path) -> list[dict[str, Any]]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, Any]] = []
        for raw_row in reader:
            row: dict[str, Any] = {}
            for key, value in raw_row.items():
                clean_key = key.strip() if key is not None else key
                if clean_key in BASE_COLUMNS:
                    row[clean_key] = value
                else:
                    row[clean_key] = parse_number(value)
            rows.append(row)
    return rows


def apply_best_known_from_solutions(
    rows: list[dict[str, Any]],
    solution_path: Path,
    best_key: str = BEST_KNOWN_KEY,
) -> None:
    """Overwrite CSV best_known values with RCPLIB solution values by rcp_name."""
    if not solution_path.exists():
        print(f"WARNING: {solution_path} not found; using best_known from CSV.")
        return

    with solution_path.open(encoding="utf-8") as f:
        solution_data = json.load(f)

    missing = 0
    for row in rows:
        rcp_name = row.get("rcp_name")
        entry = solution_data.get(rcp_name) if rcp_name else None
        if not isinstance(entry, dict) or best_key not in entry:
            missing += 1
            row["best_known"] = None
            continue
        row["best_known"] = parse_number(entry[best_key])

    if missing:
        print(
            f"WARNING: {missing} row(s) had no {best_key!r} value in "
            f"{solution_path}."
        )


def available_sets(rows: list[dict[str, Any]]) -> list[str]:
    present = {str(row.get("set")) for row in rows if row.get("set")}
    ordered = [set_name for set_name in SETS if set_name in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def get_numeric(rows: list[dict[str, Any]], col: str) -> list[float]:
    values = [parse_number(row.get(col)) for row in rows]
    return [value for value in values if value is not None]


def rule_columns(rows: list[dict[str, Any]], labels: dict[str, str]) -> list[str]:
    present = set().union(*(row.keys() for row in rows)) if rows else set()
    return [
        col
        for col in labels
        if col in present and any(parse_number(row.get(col)) is not None for row in rows)
    ]


def derive_gap_columns(rows: list[dict[str, Any]]) -> None:
    sgs_cols = rule_columns(rows, SGS_RULE_LABELS)
    pgs_cols = rule_columns(rows, PGS_RULE_LABELS)

    for row in rows:
        best_known = parse_number(row.get("best_known"))
        logos_best = parse_number(row.get("logos_best"))
        if logos_best is None:
            all_rule_values = [
                parse_number(row.get(col)) for col in [*sgs_cols, *pgs_cols]
            ]
            all_rule_values = [value for value in all_rule_values if value is not None]
            logos_best = min(all_rule_values) if all_rule_values else None

        row["logos_best"] = format_number(logos_best)
        if best_known is not None and logos_best is not None:
            row["gap_to_best_known"] = format_number(logos_best - best_known)
            row["relative_gap_to_best_known"] = format_number(
                (logos_best - best_known) / best_known * 100
            )
        else:
            row["gap_to_best_known"] = None
            row["relative_gap_to_best_known"] = None

        sgs_values = [parse_number(row.get(col)) for col in sgs_cols]
        sgs_values = [value for value in sgs_values if value is not None]
        row["sgs_best"] = format_number(min(sgs_values) if sgs_values else None)
        sgs_best = parse_number(row["sgs_best"])
        if best_known is not None and sgs_best is not None:
            row["sgs_best_abs_gap"] = format_number(sgs_best - best_known)
            row["sgs_best_rel_gap"] = format_number(
                (sgs_best - best_known) / best_known * 100
            )
        else:
            row["sgs_best_abs_gap"] = None
            row["sgs_best_rel_gap"] = None

        pgs_values = [parse_number(row.get(col)) for col in pgs_cols]
        pgs_values = [value for value in pgs_values if value is not None]
        row["pgs_best"] = format_number(min(pgs_values) if pgs_values else None)
        pgs_best = parse_number(row["pgs_best"])
        if best_known is not None and pgs_best is not None:
            row["pgs_best_abs_gap"] = format_number(pgs_best - best_known)
            row["pgs_best_rel_gap"] = format_number(
                (pgs_best - best_known) / best_known * 100
            )
        else:
            row["pgs_best_abs_gap"] = None
            row["pgs_best_rel_gap"] = None


def _hist_ax(ax, data: list[float], color: str, xlabel: str, title: str) -> None:
    if not data:
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
        ax.set_title(title, fontsize=10)
        return

    avg = mean(data)
    sigma = std(data)
    ax.hist(data, bins=40, color=color, edgecolor="white", linewidth=0.4)
    ax.axvline(
        avg,
        color="red",
        linestyle="--",
        linewidth=1.2,
        label=f"mean={avg:.2f}, std={sigma:.2f}",
    )
    ax.axvline(0, color="gray", linestyle=":", linewidth=1.0)
    ax.set_title(title, fontsize=10)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel("count", fontsize=8)
    ax.legend(fontsize=7)


def plot_gap_overall(
    rows: list[dict[str, Any]],
    output_path: Path,
    best_key: str,
) -> None:
    plt = import_pyplot()
    abs_data = get_numeric(rows, "gap_to_best_known")
    rel_data = get_numeric(rows, "relative_gap_to_best_known")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    _hist_ax(
        ax1,
        abs_data,
        "darkorange",
        "absolute gap (duration units)",
        f"Absolute Gap to {best_key}",
    )
    _hist_ax(ax2, rel_data, "tomato", "relative gap (%)", f"Relative Gap to {best_key}")
    fig.suptitle(f"RCPLIB Gap to {best_key}", fontsize=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot -> {output_path}")
    plt.close(fig)


def plot_gap_by_set(
    rows: list[dict[str, Any]],
    output_path: Path,
    best_key: str,
) -> None:
    plt = import_pyplot()
    sets = available_sets(rows)
    if not sets:
        return

    fig, axes = plt.subplots(2, len(sets), figsize=(len(sets) * 4, 8), squeeze=False)
    row_specs = [
        ("gap_to_best_known", "absolute gap (duration units)"),
        ("relative_gap_to_best_known", "relative gap (%)"),
    ]
    for row_idx, (col, xlabel) in enumerate(row_specs):
        for col_idx, set_name in enumerate(sets):
            ax = axes[row_idx][col_idx]
            subset = [row for row in rows if row.get("set") == set_name]
            color = SET_COLORS.get(set_name, "steelblue")
            title = set_name if row_idx == 0 else ""
            _hist_ax(ax, get_numeric(subset, col), color, xlabel, title)
            ax.tick_params(labelsize=7)

    axes[0][0].set_ylabel("count - absolute gap", fontsize=8)
    axes[1][0].set_ylabel("count - relative gap", fontsize=8)
    fig.suptitle(f"RCPLIB Gap to {best_key} by Benchmark Set", fontsize=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot -> {output_path}")
    plt.close(fig)


def plot_priority_rule_deviations(
    rows: list[dict[str, Any]],
    output_path: Path,
    title: str,
    rule_labels: dict[str, str],
    best_key: str,
    relative: bool = False,
) -> None:
    plt = import_pyplot()
    cols = rule_columns(rows, rule_labels)
    if not cols:
        print(f"Skipping {title}: no matching columns.")
        return

    ncols = 4
    nrows = math.ceil(len(cols) / ncols)
    color = "tomato" if relative else "steelblue"
    xlabel = (
        f"(rule - {best_key}) / {best_key} * 100 (%)"
        if relative
        else f"rule - {best_key} (duration units)"
    )

    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 4, nrows * 3))
    axes_flat = axes.flatten() if hasattr(axes, "flatten") else [axes]

    for idx, col in enumerate(cols):
        ax = axes_flat[idx]
        data: list[float] = []
        for row in rows:
            value = parse_number(row.get(col))
            best_known = parse_number(row.get("best_known"))
            if value is None or best_known is None:
                continue
            abs_gap = value - best_known
            data.append(abs_gap / best_known * 100 if relative else abs_gap)

        _hist_ax(ax, data, color, xlabel, rule_labels[col])
        ax.tick_params(labelsize=6)

    for idx in range(len(cols), len(axes_flat)):
        axes_flat[idx].set_visible(False)

    fig.suptitle(title, fontsize=12, y=1.01)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot -> {output_path}")
    plt.close(fig)


def plot_best_gap_overall(
    rows: list[dict[str, Any]],
    output_path: Path,
    abs_col: str,
    rel_col: str,
    abs_color: str,
    rel_color: str,
    suptitle: str,
    best_key: str,
) -> None:
    plt = import_pyplot()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    _hist_ax(
        ax1,
        get_numeric(rows, abs_col),
        abs_color,
        f"best rule - {best_key} (duration units)",
        "Absolute Gap",
    )
    _hist_ax(
        ax2,
        get_numeric(rows, rel_col),
        rel_color,
        "relative gap (%)",
        "Relative Gap (%)",
    )
    fig.suptitle(suptitle, fontsize=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot -> {output_path}")
    plt.close(fig)


def plot_best_gap_by_set(
    rows: list[dict[str, Any]],
    output_path: Path,
    abs_col: str,
    rel_col: str,
    suptitle: str,
    best_key: str,
) -> None:
    plt = import_pyplot()
    sets = available_sets(rows)
    if not sets:
        return

    fig, axes = plt.subplots(2, len(sets), figsize=(len(sets) * 4, 8), squeeze=False)
    row_specs = [
        (abs_col, f"best rule - {best_key} (duration units)"),
        (rel_col, "relative gap (%)"),
    ]

    for row_idx, (col, xlabel) in enumerate(row_specs):
        for col_idx, set_name in enumerate(sets):
            ax = axes[row_idx][col_idx]
            subset = [row for row in rows if row.get("set") == set_name]
            color = SET_COLORS.get(set_name, "steelblue")
            title = set_name if row_idx == 0 else ""
            _hist_ax(ax, get_numeric(subset, col), color, xlabel, title)
            ax.tick_params(labelsize=7)

    axes[0][0].set_ylabel("count - absolute gap", fontsize=8)
    axes[1][0].set_ylabel("count - relative gap", fontsize=8)
    fig.suptitle(suptitle, fontsize=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot -> {output_path}")
    plt.close(fig)


def _rule_display_name(rule: str) -> str:
    if rule == "overall_best":
        return "Overall Best"
    if rule == "sgs_best":
        return "SGS Best"
    if rule == "pgs_best":
        return "PGS Best"
    if rule.startswith("sgs_"):
        suffix = rule[len("sgs_") :]
        return f"SGS {_RULE_SUFFIXES.get(suffix, suffix)}"
    if rule.startswith("pgs_"):
        suffix = rule[len("pgs_") :]
        return f"PGS {_RULE_SUFFIXES.get(suffix, suffix)}"
    return rule


def build_gap_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    sgs_cols = rule_columns(rows, SGS_RULE_LABELS)
    pgs_cols = rule_columns(rows, PGS_RULE_LABELS)

    for row in rows:
        best_known = parse_number(row.get("best_known"))
        out_row = {
            "set": row.get("set"),
            "instance": row.get("instance"),
            "rcp_name": row.get("rcp_name"),
            "best_known": format_number(best_known),
        }

        for col in sgs_cols:
            suffix = col[len("sgs_") :]
            value = parse_number(row.get(col))
            abs_gap = value - best_known if value is not None and best_known else None
            out_row[f"sgs_{suffix}_abs_gap"] = format_number(abs_gap)
            out_row[f"sgs_{suffix}_rel_gap"] = (
                format_number(abs_gap / best_known * 100)
                if abs_gap is not None and best_known
                else None
            )

        for col in pgs_cols:
            suffix = col[len("pgs_max_use_res_ranked_") :]
            value = parse_number(row.get(col))
            abs_gap = value - best_known if value is not None and best_known else None
            out_row[f"pgs_{suffix}_abs_gap"] = format_number(abs_gap)
            out_row[f"pgs_{suffix}_rel_gap"] = (
                format_number(abs_gap / best_known * 100)
                if abs_gap is not None and best_known
                else None
            )

        out_row["sgs_best_abs_gap"] = row.get("sgs_best_abs_gap")
        out_row["sgs_best_rel_gap"] = row.get("sgs_best_rel_gap")
        out_row["pgs_best_abs_gap"] = row.get("pgs_best_abs_gap")
        out_row["pgs_best_rel_gap"] = row.get("pgs_best_rel_gap")
        out_row["overall_best_abs_gap"] = row.get("gap_to_best_known")
        out_row["overall_best_rel_gap"] = row.get("relative_gap_to_best_known")
        out.append(out_row)

    return out


def export_gap_csv(gap_rows: list[dict[str, Any]], output_path: Path) -> dict[str, dict]:
    gap_cols = [
        col
        for col in sorted(set().union(*(row.keys() for row in gap_rows)))
        if col not in {"set", "instance", "rcp_name", "best_known"}
    ]
    rule_names = [col[: -len("_abs_gap")] for col in gap_cols if col.endswith("_abs_gap")]
    set_specs = [(set_name, set_name) for set_name in available_sets(gap_rows)]
    set_specs.append((None, "Overall"))

    result: dict[str, dict[str, float | None]] = {}
    for rule in rule_names:
        abs_col = f"{rule}_abs_gap"
        rel_col = f"{rule}_rel_gap"
        summary: dict[str, float | None] = {}
        for set_key, set_label in set_specs:
            if set_key is None:
                subset = gap_rows
            else:
                subset = [row for row in gap_rows if row.get("set") == set_key]

            abs_data = get_numeric(subset, abs_col)
            rel_data = get_numeric(subset, rel_col)
            summary[f"{set_label}_abs_mean"] = format_number(mean(abs_data))
            summary[f"{set_label}_abs_std"] = format_number(std(abs_data))
            summary[f"{set_label}_rel_mean"] = format_number(mean(rel_data))
            summary[f"{set_label}_rel_std"] = format_number(std(rel_data))
        result[rule] = summary

    fieldnames = ["rule"]
    if result:
        fieldnames.extend(next(iter(result.values())).keys())

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rule, summary in result.items():
            writer.writerow({"rule": rule, **summary})

    print(f"Saved CSV  -> {output_path} ({len(result)} rows)")
    return result


def plot_overall_gap_bars(
    gap_result: dict[str, dict],
    output_path: Path,
    best_key: str,
) -> None:
    plt = import_pyplot()
    specs = [
        (
            "Overall_abs_mean",
            "Overall_abs_std",
            "steelblue",
            "Absolute gap (duration units)",
            f"Overall Absolute Gap to {best_key}",
        ),
        (
            "Overall_rel_mean",
            "Overall_rel_std",
            "tomato",
            "Relative gap (%)",
            f"Overall Relative Gap to {best_key} (%)",
        ),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(18, 12))
    for ax, (mean_col, std_col, color, xlabel, title) in zip(axes, specs, strict=True):
        data = [
            (
                rule,
                parse_number(summary.get(mean_col)),
                parse_number(summary.get(std_col)),
            )
            for rule, summary in gap_result.items()
        ]
        data = [
            (rule, avg, sigma if sigma is not None else 0.0)
            for rule, avg, sigma in data
            if avg is not None
        ]
        data.sort(key=lambda item: item[1])
        labels = [_rule_display_name(rule) for rule, _, _ in data]
        averages = [avg for _, avg, _ in data]
        errors = [sigma for _, _, sigma in data]

        ax.barh(
            labels,
            averages,
            xerr=errors,
            color=color,
            edgecolor="white",
            linewidth=0.4,
            capsize=3,
            error_kw={"elinewidth": 0.8, "capthick": 0.8},
        )
        ax.axvline(0, color="gray", linestyle=":", linewidth=1.0)
        ax.set_xlabel(xlabel, fontsize=9)
        ax.set_title(title, fontsize=11)
        ax.tick_params(axis="y", labelsize=7)
        ax.tick_params(axis="x", labelsize=8)

    fig.suptitle(f"RCPLIB Priority Rule Gap to {best_key}", fontsize=13)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot -> {output_path}")
    plt.close(fig)


def print_summary(rows: list[dict[str, Any]]) -> None:
    columns = [
        "gap_to_best_known",
        "relative_gap_to_best_known",
        "sgs_best_abs_gap",
        "pgs_best_abs_gap",
    ]
    print("\n--- Overall Summary ---")
    print(f"{'column':<30} {'count':>8} {'mean':>10} {'std':>10} {'min':>10} {'max':>10}")
    for col in columns:
        data = get_numeric(rows, col)
        if not data:
            continue
        print(
            f"{col:<30} {len(data):>8} {mean(data):>10.3f} "
            f"{std(data):>10.3f} {min(data):>10.3f} {max(data):>10.3f}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot RCPLIB benchmark results.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Directory for output plots and CSV (default: {OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=CSV_PATH,
        help=f"Path to rcplib_results.csv (default: {CSV_PATH}).",
    )
    parser.add_argument(
        "--solutions",
        type=Path,
        default=SOLUTION_PATH,
        help=(
            "Path to rcplib_solution_results.json used to load best-known values "
            f"(default: {SOLUTION_PATH})."
        ),
    )
    parser.add_argument(
        "--best-key",
        default=BEST_KNOWN_KEY,
        metavar="KEY",
        help=(
            "Second-level solution key to use as best known, such as LB-lit or "
            f"UB-lit (default: {BEST_KNOWN_KEY})."
        ),
    )
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="Remove the output directory before writing plots.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.clean_output and args.output_dir.exists():
        shutil.rmtree(args.output_dir)
        print(f"Removed '{args.output_dir}'")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = load_results(args.csv)
    apply_best_known_from_solutions(rows, args.solutions, args.best_key)
    derive_gap_columns(rows)
    print(f"Loaded {len(rows)} rows from {args.csv}")
    print(f"Using best-known key: {args.best_key}")
    print_summary(rows)

    # -- logos_best gap -------------------------------------------------------
    plot_gap_overall(rows, args.output_dir / "rcplib_gap_overall.png", args.best_key)
    plot_gap_by_set(rows, args.output_dir / "rcplib_gap_by_set.png", args.best_key)

    # -- SGS per-rule deviations from selected best-known key -----------------
    for relative, suffix, kind in [
        (False, "", "Absolute"),
        (True, "_rel", "Relative"),
    ]:
        plot_priority_rule_deviations(
            rows,
            args.output_dir / f"rcplib_sgs_rule_dev{suffix}.png",
            f"RCPLIB - SGS: Priority Rule {kind} Deviation from {args.best_key}",
            SGS_RULE_LABELS,
            args.best_key,
            relative=relative,
        )
        for set_name in available_sets(rows):
            subset = [row for row in rows if row.get("set") == set_name]
            plot_priority_rule_deviations(
                subset,
                args.output_dir / f"rcplib_sgs_rule_dev{suffix}_{set_name}.png",
                f"RCPLIB ({set_name}) - SGS: Priority Rule {kind} Deviation from {args.best_key}",
                SGS_RULE_LABELS,
                args.best_key,
                relative=relative,
            )

    # -- SGS best-rule gap ----------------------------------------------------
    plot_best_gap_overall(
        rows,
        args.output_dir / "rcplib_sgs_best_gap.png",
        "sgs_best_abs_gap",
        "sgs_best_rel_gap",
        "steelblue",
        "cornflowerblue",
        f"Best SGS Priority Rule vs {args.best_key}",
        args.best_key,
    )
    plot_best_gap_by_set(
        rows,
        args.output_dir / "rcplib_sgs_best_gap_by_set.png",
        "sgs_best_abs_gap",
        "sgs_best_rel_gap",
        f"Best SGS Priority Rule vs {args.best_key} by Benchmark Set",
        args.best_key,
    )

    # -- PGS per-rule deviations from selected best-known key -----------------
    for relative, suffix, kind in [
        (False, "", "Absolute"),
        (True, "_rel", "Relative"),
    ]:
        plot_priority_rule_deviations(
            rows,
            args.output_dir / f"rcplib_pgs_rule_dev{suffix}.png",
            f"RCPLIB - PGS: Priority Rule {kind} Deviation from {args.best_key}",
            PGS_RULE_LABELS,
            args.best_key,
            relative=relative,
        )
        for set_name in available_sets(rows):
            subset = [row for row in rows if row.get("set") == set_name]
            plot_priority_rule_deviations(
                subset,
                args.output_dir / f"rcplib_pgs_rule_dev{suffix}_{set_name}.png",
                f"RCPLIB ({set_name}) - PGS: Priority Rule {kind} Deviation from {args.best_key}",
                PGS_RULE_LABELS,
                args.best_key,
                relative=relative,
            )

    # -- PGS best-rule gap ----------------------------------------------------
    plot_best_gap_overall(
        rows,
        args.output_dir / "rcplib_pgs_best_gap.png",
        "pgs_best_abs_gap",
        "pgs_best_rel_gap",
        "mediumseagreen",
        "limegreen",
        f"Best PGS Priority Rule vs {args.best_key}",
        args.best_key,
    )
    plot_best_gap_by_set(
        rows,
        args.output_dir / "rcplib_pgs_best_gap_by_set.png",
        "pgs_best_abs_gap",
        "pgs_best_rel_gap",
        f"Best PGS Priority Rule vs {args.best_key} by Benchmark Set",
        args.best_key,
    )

    # -- CSV export + summary bar charts -------------------------------------
    gap_rows = build_gap_rows(rows)
    gap_result = export_gap_csv(gap_rows, args.output_dir / "rcplib_gap_analysis.csv")
    plot_overall_gap_bars(
        gap_result,
        args.output_dir / "rcplib_gap_bars_overall.png",
        args.best_key,
    )


if __name__ == "__main__":
    main()
