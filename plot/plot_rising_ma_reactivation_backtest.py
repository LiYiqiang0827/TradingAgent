"""Plot the May-Aug rising-MA audit from de-duplicated episode records."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


KINDS = {"ma20_pullback_zone": "MA20 pullback zone",
         "ma30_coil_restart": "MA30 coil restart"}
COLORS = {"ma20_pullback_zone": "#4b78ae",
          "ma30_coil_restart": "#c85768"}


def plot(input_file: Path, output_file: Path) -> None:
    data = pd.read_csv(input_file, dtype={"trade_date": str})
    data["month"] = data.trade_date.str[:6]
    months = sorted(data.month.unique())
    fig = plt.figure(figsize=(13.5, 9), layout="constrained")
    grid = fig.add_gridspec(2, 2, height_ratios=[1, 1.15])
    count_ax, month_ax = fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1])
    dist_ax = fig.add_subplot(grid[1, :])
    x = np.arange(len(months))
    for position, (kind, label) in enumerate(KINDS.items()):
        subset = data[data.template_observation.eq(kind)]
        counts = subset.groupby("month").size().reindex(months, fill_value=0)
        count_ax.bar(x + (position - .5) * .38, counts, width=.38,
                     label=label, color=COLORS[kind], alpha=.85)
        means = subset.groupby("month").net_10d_pct.mean().reindex(months)
        medians = subset.groupby("month").net_10d_pct.median().reindex(months)
        month_ax.plot(x, means, marker="o", color=COLORS[kind], lw=2,
                      label=f"{label} mean")
        month_ax.plot(x, medians, marker="x", color=COLORS[kind], lw=1,
                      linestyle="--", label=f"{label} median")
    count_ax.set_xticks(x, months)
    count_ax.set_title("Distinct episodes (10-session gap)")
    count_ax.set_ylabel("Count")
    count_ax.legend(fontsize=8)
    month_ax.axhline(0, color="black", lw=.8)
    month_ax.set_xticks(x, months)
    month_ax.set_title("10-session next-open proxy net return by month")
    month_ax.set_ylabel("Percent")
    month_ax.legend(fontsize=7, ncol=2)
    distributions = [pd.to_numeric(data.loc[data.template_observation.eq(kind),
                                            "net_10d_pct"], errors="coerce").dropna()
                     for kind in KINDS]
    boxes = dist_ax.boxplot(distributions, labels=list(KINDS.values()),
                            patch_artist=True, showfliers=False)
    for box, color in zip(boxes["boxes"], COLORS.values()):
        box.set_facecolor(color)
        box.set_alpha(.55)
    dist_ax.axhline(0, color="black", lw=.8)
    dist_ax.set_title("10-session net return distribution; outliers hidden in boxplot")
    dist_ax.set_ylabel("Percent")
    dist_ax.grid(axis="y", alpha=.15)
    fig.suptitle("2026 May-Aug chart templates after first-wave board + sustained volume", fontsize=14)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_file, dpi=170)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(
        "outputs/rising_ma_reactivation/may_aug_2026_final/first_episode_matches.csv"))
    parser.add_argument("--output", type=Path, default=Path(
        "outputs/rising_ma_reactivation/may_aug_2026_final/return_distribution.jpg"))
    args = parser.parse_args()
    plot(args.input, args.output)


if __name__ == "__main__":
    main()
