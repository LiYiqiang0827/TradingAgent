"""Plot comparable post-board cumulative-return histograms from study output."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


HORIZONS = (2, 3, 5)
COLORS = ("#3975A8", "#2B9875", "#D6853B")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-threshold", type=int, default=4)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    directory = Path(f"outputs/{args.board_threshold}_board_followthrough_2026")
    input_path = args.input or directory / "horizon_samples.csv"
    output_path = args.output or directory / "return_distribution_2_3_5.png"
    source = pd.read_csv(input_path)
    selected = source.loc[
        (source.anchor_kind == "first_threshold_plus")
        & (source.first_height == args.board_threshold)
        & (source.status == "observed")
        & source.horizon_trading_days.isin(HORIZONS)
    ].copy()
    if selected.empty or set(selected.horizon_trading_days) != set(HORIZONS):
        raise RuntimeError("Missing observed samples for one or more horizons")

    # Common five-percentage-point bins and identical plot axes make the three
    # horizons visually comparable. Extend bounds if later data exceeds 2026.
    smallest = float(selected.return_pct.min())
    largest = float(selected.return_pct.max())
    left = min(-40, 5 * np.floor(smallest / 5))
    right = max(65, 5 * np.ceil(largest / 5))
    edges = np.arange(left, right + 5.001, 5, dtype=float)
    all_counts = [
        np.histogram(
            selected.loc[selected.horizon_trading_days == h, "return_pct"],
            bins=edges,
        )[0]
        for h in HORIZONS
    ]
    ymax = max(int(max(c)) for c in all_counts) * 1.18

    plt.rcParams.update({
        "font.family": "Arial Unicode MS",
        "axes.unicode_minus": False,
        "font.size": 11,
    })
    fig, axes = plt.subplots(3, 1, figsize=(13.2, 10.4), sharex=True, sharey=True)
    bin_rows = []
    for ax, horizon, color, counts in zip(axes, HORIZONS, COLORS, all_counts):
        values = selected.loc[
            selected.horizon_trading_days == horizon, "return_pct"
        ].astype(float)
        ax.hist(values, bins=edges, color=color, alpha=0.86, edgecolor="white", linewidth=1)
        ax.axvline(0, color="#252A2E", linewidth=1.5, label="零收益")
        ax.axvline(values.mean(), color="#A22E3D", linewidth=2, linestyle="--", label="平均值")
        ax.axvline(values.median(), color="#724D98", linewidth=2, linestyle=":", label="中位数")
        ax.set_title(
            f"4连板后第{horizon}个交易日  ·  有效样本 {len(values)}  ·  "
            f"平均 {values.mean():+.2f}%  ·  中位数 {values.median():+.2f}%",
            loc="left", pad=9, fontsize=13, fontweight="bold",
        )
        ax.set_ylabel("行情轮次数")
        ax.set_ylim(0, ymax)
        ax.grid(axis="y", alpha=0.18)
        ax.spines[["top", "right"]].set_visible(False)
        for low, high, count in zip(edges[:-1], edges[1:], counts):
            bin_rows.append({
                "horizon_trading_days": horizon,
                "bin_low_pct": low,
                "bin_high_pct": high,
                "count": int(count),
                "share_pct": round(100 * int(count) / len(values), 4),
            })
    axes[0].legend(loc="upper right", ncol=3, frameon=False)
    axes[-1].set_xlim(left, right)
    axes[-1].set_xticks(np.arange(left, right + 1, 10))
    axes[-1].set_xlabel("相对4连板当天收盘的累计涨跌幅（%）；每格宽5个百分点")
    fig.suptitle("2026年A股4连板后的涨跌幅分布", fontsize=18, fontweight="bold", y=0.995)
    fig.text(
        0.5, 0.015,
        "开盘啦截至2026-09-28；排除ST、次新、北交所；同轮只计一次；无报价/未走满窗口不填补。",
        ha="center", color="#60686D", fontsize=10,
    )
    fig.tight_layout(rect=(0.02, 0.035, 1, 0.975), h_pad=1.4)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=180, facecolor="white")
    plt.close(fig)
    pd.DataFrame(bin_rows).to_csv(
        output_path.with_name("return_distribution_bins_2_3_5.csv"),
        index=False, encoding="utf-8-sig",
    )
    print(output_path)


if __name__ == "__main__":
    main()
