"""
Plot histograms for all dev_sgs_* columns and gap_to_best_known
from psplib_results.csv — overall and per benchmark set (J30/J60/J90/J120).
Covers SGS and PGS priority rule deviations from best known.
"""

import argparse
import math
import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

CSV_PATH = Path(__file__).parent / "psplib_results.csv"
OUTPUT_DIR = Path(__file__).parent / "results"

SETS = ["j30", "j60", "j90", "j120"]
SET_COLORS = {"j30": "steelblue", "j60": "seagreen", "j90": "darkorange", "j120": "mediumpurple"}

# Shared priority rule suffixes → display labels
_RULE_SUFFIXES = {
    "es":          "ES (Early Start)",
    "ef":          "EF (Early Finish)",
    "ls":          "LS (Late Start)",
    "lf":          "LF (Late Finish)",
    "duration":    "Duration",
    "random":      "Random",
    "mts":         "MTS",
    "mtp":         "MTP",
    "grpw":        "GRPW",
    "grd":         "GRD",
    "rr":          "RR",
    "avgrr":       "AvgRR",
    "maxrr":       "MaxRR",
    "minrr":       "MinRR",
    "irsm":        "IRSM",
    "wcs":         "WCS",
    "acs":         "ACS",
    "mehh_8000_b": "MEHH-8000",
    "mehh_3375_b": "MEHH-3375",
    "mehh_1000_b": "MEHH-1000",
    "mehh_125_b":  "MEHH-125",
    "gphh_b":      "GPHH",
}

SGS_RULE_LABELS = {f"sgs_{k}": v for k, v in _RULE_SUFFIXES.items()}
PGS_RULE_LABELS = {f"pgs_max_use_res_ranked_{k}": v for k, v in _RULE_SUFFIXES.items()}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_results(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df.columns = df.columns.str.strip()
    return df


def get_dev_columns(df: pd.DataFrame) -> list[str]:
    dev_cols = [c for c in df.columns if c.startswith("dev_sgs_") and df[c].notna().any()]
    skipped = [c for c in df.columns if c.startswith("dev_sgs_") and not df[c].notna().any()]
    if skipped:
        print(f"Skipping all-NaN columns: {skipped}")
    return dev_cols


def col_to_title(col: str) -> str:
    if col.startswith("dev_sgs_"):
        return col[len("dev_sgs_"):].upper()
    return "Gap to Best Known"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _hist_ax(ax, data, color, xlabel, title):
    ax.hist(data, bins=40, color=color, edgecolor="white", linewidth=0.4)
    ax.axvline(data.mean(), color="red", linestyle="--", linewidth=1.2,
               label=f"mean={data.mean():.2f}, std={data.std():.2f}")
    ax.axvline(0, color="gray", linestyle=":", linewidth=1.0)
    ax.set_title(title, fontsize=10)
    ax.set_xlabel(xlabel, fontsize=8)
    ax.set_ylabel("count", fontsize=8)
    ax.legend(fontsize=7)


# ---------------------------------------------------------------------------
# SGS deviation histograms (vs benchmark, not vs best_known)
# ---------------------------------------------------------------------------

def plot_histograms(df: pd.DataFrame, columns: list[str], output_path: Path, title: str) -> None:
    n = len(columns)
    ncols = 4
    nrows = math.ceil(n / ncols)

    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 4, nrows * 3))
    axes_flat = axes.flatten()

    for i, col in enumerate(columns):
        ax = axes_flat[i]
        data = df[col].dropna()
        ax.hist(data, bins=30, color="steelblue", edgecolor="white", linewidth=0.4)
        ax.axvline(data.mean(), color="red", linestyle="--", linewidth=1.2,
                   label=f"mean={data.mean():.2f}, std={data.std():.2f}")
        ax.axvline(0, color="gray", linestyle=":", linewidth=1.0)
        ax.set_title(col_to_title(col), fontsize=8, pad=4)
        ax.set_xlabel("deviation", fontsize=7)
        ax.set_ylabel("count", fontsize=7)
        ax.tick_params(labelsize=6)
        ax.legend(fontsize=6)

    for j in range(n, len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.suptitle(title, fontsize=12, y=1.01)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot → {output_path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# logos_best gap histograms
# ---------------------------------------------------------------------------

def plot_gap_overall(df: pd.DataFrame, output_path: Path) -> None:
    abs_data = df["gap_to_best_known"].dropna()
    rel_data = df["relative_gap_to_best_known"].dropna()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    _hist_ax(ax1, abs_data, "darkorange", "absolute gap (duration units)", "Absolute Gap to Best Known")
    _hist_ax(ax2, rel_data, "tomato",     "relative gap (%)",              "Relative Gap to Best Known")

    fig.suptitle("Gap to Best Known (All Sets)", fontsize=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot → {output_path}")
    plt.close(fig)


def plot_gap_by_set(df: pd.DataFrame, output_path: Path) -> None:
    sets = [s for s in SETS if s in df["set"].unique()]
    fig, axes = plt.subplots(2, len(sets), figsize=(len(sets) * 4, 8))

    row_specs = [
        ("gap_to_best_known",          "absolute gap (duration units)"),
        ("relative_gap_to_best_known", "relative gap (%)"),
    ]
    for row, (col, xlabel) in enumerate(row_specs):
        for c, set_name in enumerate(sets):
            ax = axes[row, c]
            data = df.loc[df["set"] == set_name, col].dropna()
            _hist_ax(ax, data, SET_COLORS[set_name], xlabel, set_name.upper() if row == 0 else "")
            ax.tick_params(labelsize=7)

    axes[0, 0].set_ylabel("count — absolute gap", fontsize=8)
    axes[1, 0].set_ylabel("count — relative gap", fontsize=8)
    fig.suptitle("Gap to Best Known by Benchmark Set", fontsize=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot → {output_path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Generic best-rule gap histograms (reused for SGS and PGS)
# ---------------------------------------------------------------------------

def plot_best_gap_overall(
    df: pd.DataFrame,
    output_path: Path,
    abs_col: str,
    rel_col: str,
    abs_color: str,
    rel_color: str,
    suptitle: str,
) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    _hist_ax(ax1, df[abs_col].dropna(), abs_color,
             "best rule − best known (duration units)", "Absolute Gap")
    _hist_ax(ax2, df[rel_col].dropna(), rel_color,
             "(best rule − best known) / best known × 100  (%)", "Relative Gap (%)")
    fig.suptitle(suptitle, fontsize=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot → {output_path}")
    plt.close(fig)


def plot_best_gap_by_set(
    df: pd.DataFrame,
    output_path: Path,
    abs_col: str,
    rel_col: str,
    suptitle: str,
) -> None:
    sets = [s for s in SETS if s in df["set"].unique()]
    fig, axes = plt.subplots(2, len(sets), figsize=(len(sets) * 4, 8))

    row_specs = [
        (abs_col, "best rule − best known (duration units)"),
        (rel_col, "relative gap (%)"),
    ]
    for row, (col, xlabel) in enumerate(row_specs):
        for c, set_name in enumerate(sets):
            ax = axes[row, c]
            data = df.loc[df["set"] == set_name, col].dropna()
            _hist_ax(ax, data, SET_COLORS[set_name], xlabel, set_name.upper() if row == 0 else "")
            ax.tick_params(labelsize=7)

    axes[0, 0].set_ylabel("count — absolute gap", fontsize=8)
    axes[1, 0].set_ylabel("count — relative gap", fontsize=8)
    fig.suptitle(suptitle, fontsize=12)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot → {output_path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Generic per-rule deviation histograms (reused for SGS and PGS)
# ---------------------------------------------------------------------------

def plot_priority_rule_deviations(
    df: pd.DataFrame,
    output_path: Path,
    title: str,
    rule_labels: dict,
    relative: bool = False,
) -> None:
    cols = [c for c in rule_labels if c in df.columns]
    n = len(cols)
    ncols = 4
    nrows = math.ceil(n / ncols)

    color = "tomato" if relative else "steelblue"
    xlabel = "(rule − best known) / best known × 100  (%)" if relative else "rule − best known (duration units)"

    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 4, nrows * 3))
    axes_flat = axes.flatten()

    for i, col in enumerate(cols):
        ax = axes_flat[i]
        abs_dev = df[col] - df["best_known"]
        dev = (abs_dev / df["best_known"] * 100 if relative else abs_dev).dropna()
        ax.hist(dev, bins=30, color=color, edgecolor="white", linewidth=0.4)
        ax.axvline(dev.mean(), color="red", linestyle="--", linewidth=1.2,
                   label=f"mean={dev.mean():.1f}, std={dev.std():.1f}")
        ax.axvline(0, color="gray", linestyle=":", linewidth=1.0)
        ax.set_title(rule_labels[col], fontsize=8, pad=4)
        ax.set_xlabel(xlabel, fontsize=7)
        ax.set_ylabel("count", fontsize=7)
        ax.tick_params(labelsize=6)
        ax.legend(fontsize=6)

    for j in range(n, len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.suptitle(title, fontsize=12, y=1.01)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot → {output_path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def _build_gap_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["set", "instance", "best_known"]].copy()

    for col, _ in SGS_RULE_LABELS.items():
        suffix = col[len("sgs_"):]
        abs_gap = df[col] - df["best_known"]
        out[f"sgs_{suffix}_abs_gap"] = abs_gap
        out[f"sgs_{suffix}_rel_gap"] = abs_gap / df["best_known"] * 100

    for col, _ in PGS_RULE_LABELS.items():
        suffix = col[len("pgs_max_use_res_ranked_"):]
        abs_gap = df[col] - df["best_known"]
        out[f"pgs_{suffix}_abs_gap"] = abs_gap
        out[f"pgs_{suffix}_rel_gap"] = abs_gap / df["best_known"] * 100

    out["sgs_best_abs_gap"] = df["sgs_best_abs_gap"]
    out["sgs_best_rel_gap"] = df["sgs_best_rel_gap"]
    out["pgs_best_abs_gap"] = df["pgs_best_abs_gap"]
    out["pgs_best_rel_gap"] = df["pgs_best_rel_gap"]
    out["overall_best_abs_gap"] = df["gap_to_best_known"]
    out["overall_best_rel_gap"] = df["relative_gap_to_best_known"]
    return out


def export_gap_csv(gap_df: pd.DataFrame, output_path: Path) -> None:
    """Export per-priority-rule gap statistics.

    Rows   : priority rules (sgs_*, pgs_*, sgs_best, pgs_best, overall_best)
    Columns: J30_abs_mean, J30_abs_std, J30_rel_mean, J30_rel_std, ...,
             Overall_abs_mean, Overall_abs_std, Overall_rel_mean, Overall_rel_std
    """
    gap_cols = [c for c in gap_df.columns if c not in ("set", "instance", "best_known")]

    # Identify unique rule names by stripping the _abs_gap suffix
    rule_names = [c[: -len("_abs_gap")] for c in gap_cols if c.endswith("_abs_gap")]

    set_specs = [(s, s.upper()) for s in SETS] + [(None, "Overall")]

    rows = {}
    for rule in rule_names:
        abs_col = f"{rule}_abs_gap"
        rel_col = f"{rule}_rel_gap"
        row = {}
        for set_key, set_label in set_specs:
            if set_key is None:
                abs_data = gap_df[abs_col].dropna()
                rel_data = gap_df[rel_col].dropna()
            else:
                mask = gap_df["set"] == set_key
                abs_data = gap_df.loc[mask, abs_col].dropna()
                rel_data = gap_df.loc[mask, rel_col].dropna()
            row[f"{set_label}_abs_mean"] = abs_data.mean()
            row[f"{set_label}_abs_std"]  = abs_data.std()
            row[f"{set_label}_rel_mean"] = rel_data.mean()
            row[f"{set_label}_rel_std"]  = rel_data.std()
        rows[rule] = row

    result = pd.DataFrame(rows).T.round(4)
    result.index.name = "rule"
    result.to_csv(output_path)
    print(f"Saved CSV  → {output_path}  ({len(result)} rows × {len(result.columns)} columns)")
    return result


# ---------------------------------------------------------------------------
# Gap summary bar charts
# ---------------------------------------------------------------------------

def _rule_display_name(rule: str) -> str:
    """Convert internal rule key to a readable label (e.g. sgs_es → SGS ES)."""
    if rule == "overall_best":
        return "Overall Best"
    if rule == "sgs_best":
        return "SGS Best"
    if rule == "pgs_best":
        return "PGS Best"
    if rule.startswith("sgs_"):
        suffix = rule[len("sgs_"):]
        return f"SGS {_RULE_SUFFIXES.get(suffix, suffix)}"
    if rule.startswith("pgs_"):
        suffix = rule[len("pgs_"):]
        return f"PGS {_RULE_SUFFIXES.get(suffix, suffix)}"
    return rule


def plot_overall_gap_bars(gap_result: pd.DataFrame, output_path: Path) -> None:
    """Two horizontal bar charts (abs and rel overall gap), sorted largest → smallest (top → bottom)."""
    specs = [
        ("Overall_abs_mean", "Overall_abs_std", "steelblue",
         "Absolute gap (duration units)", "Overall Absolute Gap to Best Known"),
        ("Overall_rel_mean", "Overall_rel_std", "tomato",
         "Relative gap (%)", "Overall Relative Gap to Best Known (%)"),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(18, 12))

    for ax, (mean_col, std_col, color, xlabel, title) in zip(axes, specs):
        data = gap_result[[mean_col, std_col]].dropna()
        # sort ascending so the largest value ends up at the top of the horizontal bars
        data = data.sort_values(mean_col, ascending=True)
        labels = [_rule_display_name(r) for r in data.index]

        ax.barh(
            labels, data[mean_col],
            xerr=data[std_col],
            color=color, edgecolor="white", linewidth=0.4,
            capsize=3, error_kw={"elinewidth": 0.8, "capthick": 0.8},
        )
        ax.axvline(0, color="gray", linestyle=":", linewidth=1.0)
        ax.set_xlabel(xlabel, fontsize=9)
        ax.set_title(title, fontsize=11)
        ax.tick_params(axis="y", labelsize=7)
        ax.tick_params(axis="x", labelsize=8)

    fig.suptitle("Priority Rule Gap to Best Known — All Instances", fontsize=13)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved plot → {output_path}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Plot PSPLIB benchmark results.")
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Directory for output plots and CSV (default: <script-dir>/results)",
    )
    parser.add_argument(
        "--csv", type=Path, default=None,
        help="Path to psplib_results.csv (default: <script-dir>/psplib_results.csv)",
    )
    args = parser.parse_args()

    output_dir = args.output_dir if args.output_dir is not None else OUTPUT_DIR
    if args.csv is not None:
        csv_path = args.csv
    elif args.output_dir is not None:
        csv_path = output_dir / CSV_PATH.name
    else:
        csv_path = CSV_PATH

    if output_dir.exists():
        answer = input(f"Output directory '{output_dir}' already exists. Remove it? [y/N] ").strip().lower()
        if answer == "y":
            shutil.rmtree(output_dir)
            print(f"Removed '{output_dir}'")
        else:
            print("Keeping existing directory — files may be overwritten.")
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_results(csv_path)
    print(f"Loaded {len(df)} rows, {len(df.columns)} columns")

    # Derived gap columns — logos_best
    df["relative_gap_to_best_known"] = (
        (df["logos_best"] - df["best_known"]) / df["best_known"] * 100
    )

    # Derived gap columns — SGS best
    sgs_cols = [c for c in SGS_RULE_LABELS if c in df.columns]
    df["sgs_best"] = df[sgs_cols].min(axis=1)
    df["sgs_best_abs_gap"] = df["sgs_best"] - df["best_known"]
    df["sgs_best_rel_gap"] = df["sgs_best_abs_gap"] / df["best_known"] * 100

    # Derived gap columns — PGS best
    pgs_cols = [c for c in PGS_RULE_LABELS if c in df.columns]
    df["pgs_best"] = df[pgs_cols].min(axis=1)
    df["pgs_best_abs_gap"] = df["pgs_best"] - df["best_known"]
    df["pgs_best_rel_gap"] = df["pgs_best_abs_gap"] / df["best_known"] * 100

    dev_cols = get_dev_columns(df)
    print("\n--- Overall Summary (dev_sgs) ---")
    print(df[dev_cols + ["gap_to_best_known"]].describe().T[
        ["count", "mean", "std", "min", "max"]].to_string())

    # -- SGS deviation vs benchmark ------------------------------------------
    plot_histograms(
        df, dev_cols,
        output_dir /"psplib_histograms.png",
        "PSPLIB Benchmark (All Sets): SGS Deviations",
    )
    for set_name in SETS:
        if set_name not in df["set"].unique():
            continue
        subset = df[df["set"] == set_name]
        subset_dev_cols = [c for c in dev_cols if subset[c].notna().any()]
        label = set_name.upper()
        print(f"\n--- {label} Summary ({len(subset)} instances) ---")
        print(subset[subset_dev_cols + ["gap_to_best_known"]].describe().T[
            ["count", "mean", "std", "min", "max"]].to_string())
        plot_histograms(
            subset, subset_dev_cols,
            output_dir /f"psplib_histograms_{set_name}.png",
            f"PSPLIB Benchmark ({label}): SGS Deviations",
        )

    # -- logos_best gap -------------------------------------------------------
    plot_gap_overall(df, output_dir /"psplib_gap_overall.png")
    plot_gap_by_set(df, output_dir /"psplib_gap_by_set.png")

    # -- SGS per-rule deviations from best known ------------------------------
    for relative, suffix, kind in [
        (False, "",     "Absolute"),
        (True,  "_rel", "Relative"),
    ]:
        plot_priority_rule_deviations(
            df,
            output_dir /f"psplib_sgs_rule_dev{suffix}.png",
            f"PSPLIB (All Sets) — SGS: Priority Rule {kind} Deviation from Best Known",
            SGS_RULE_LABELS, relative=relative,
        )
        for set_name in SETS:
            if set_name not in df["set"].unique():
                continue
            plot_priority_rule_deviations(
                df[df["set"] == set_name],
                output_dir /f"psplib_sgs_rule_dev{suffix}_{set_name}.png",
                f"PSPLIB ({set_name.upper()}) — SGS: Priority Rule {kind} Deviation from Best Known",
                SGS_RULE_LABELS, relative=relative,
            )

    # -- SGS best-rule gap ----------------------------------------------------
    plot_best_gap_overall(
        df, output_dir /"psplib_sgs_best_gap.png",
        "sgs_best_abs_gap", "sgs_best_rel_gap",
        "steelblue", "cornflowerblue",
        "Best SGS Priority Rule vs Best Known (All Sets)",
    )
    plot_best_gap_by_set(
        df, output_dir /"psplib_sgs_best_gap_by_set.png",
        "sgs_best_abs_gap", "sgs_best_rel_gap",
        "Best SGS Priority Rule vs Best Known by Benchmark Set",
    )

    # -- PGS per-rule deviations from best known ------------------------------
    for relative, suffix, kind in [
        (False, "",     "Absolute"),
        (True,  "_rel", "Relative"),
    ]:
        plot_priority_rule_deviations(
            df,
            output_dir /f"psplib_pgs_rule_dev{suffix}.png",
            f"PSPLIB (All Sets) — PGS: Priority Rule {kind} Deviation from Best Known",
            PGS_RULE_LABELS, relative=relative,
        )
        for set_name in SETS:
            if set_name not in df["set"].unique():
                continue
            plot_priority_rule_deviations(
                df[df["set"] == set_name],
                output_dir /f"psplib_pgs_rule_dev{suffix}_{set_name}.png",
                f"PSPLIB ({set_name.upper()}) — PGS: Priority Rule {kind} Deviation from Best Known",
                PGS_RULE_LABELS, relative=relative,
            )

    # -- PGS best-rule gap ----------------------------------------------------
    plot_best_gap_overall(
        df, output_dir /"psplib_pgs_best_gap.png",
        "pgs_best_abs_gap", "pgs_best_rel_gap",
        "mediumseagreen", "limegreen",
        "Best PGS Priority Rule vs Best Known (All Sets)",
    )
    plot_best_gap_by_set(
        df, output_dir /"psplib_pgs_best_gap_by_set.png",
        "pgs_best_abs_gap", "pgs_best_rel_gap",
        "Best PGS Priority Rule vs Best Known by Benchmark Set",
    )

    # -- CSV export + summary bar charts --------------------------------------
    gap_df = _build_gap_df(df)
    gap_result = export_gap_csv(gap_df, output_dir /"psplib_gap_analysis.csv")
    plot_overall_gap_bars(gap_result, output_dir /"psplib_gap_bars_overall.png")


if __name__ == "__main__":
    main()
