#!/usr/bin/env python3
"""绘制全市场题材情绪周期图和一级题材轮动图。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Patch
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from coreClient.data_provider import get_market_theme_review_series, get_theme_daily  # noqa: E402
from plot.theme_cycle_common import (  # noqa: E402
    DEFAULT_THEME_CHART_DIR,
    STRUCTURE_COLORS,
    STRUCTURE_LABELS,
    configure_style,
    contiguous_runs,
    save_figure,
    trade_date_ticks,
)


EXCLUDED_THEMES = {"ST板块", "ST摘帽", "次新股"}
EXCLUDED_LEVEL1 = {"ST与次新"}


def _review_frame(reviews: list[dict]) -> pd.DataFrame:
    rows = []
    for review in reviews:
        evidence = (review.get("structure") or {}).get("evidence") or {}
        breadth = review.get("market_breadth") or {}
        hot = review.get("hot_themes") or []
        rows.append({
            "trade_date": str(review["trade_date"]),
            "sentiment": float(review.get("theme_sentiment_score") or 0),
            "raw_heat": float(review.get("top3_heat_composite") or 0),
            "structure": (review.get("structure") or {}).get("code", "cold_no_mainline"),
            "structure_label": (review.get("structure") or {}).get("label", "未知"),
            "top_theme": hot[0]["theme"] if hot else "无",
            "limit_up": int(breadth.get("limit_up_count") or 0),
            "break_count": int(breadth.get("break_count") or 0),
            "seal_rate": float(breadth.get("seal_rate") or 0),
            "max_height": int(breadth.get("max_board_height") or 0),
            "top1_share": 100 * float(evidence.get("top_share") or 0),
            "top3_share": 100 * float(evidence.get("top3_share") or 0),
            "hhi": 100 * float(evidence.get("theme_width_hhi") or 0),
            "broad_count": int(evidence.get("broad_strong_theme_count") or 0),
        })
    return pd.DataFrame(rows)


def plot_market_sentiment_cycle(frame: pd.DataFrame, output: Path, dpi: int) -> Path:
    labels = frame["trade_date"].tolist()
    x = np.arange(len(frame))
    fig, axes = plt.subplots(
        4, 1, figsize=(18, 13), sharex=True,
        gridspec_kw={"height_ratios": [3.0, 2.4, 2.2, 0.65], "hspace": 0.08},
    )
    ax = axes[0]
    for low, high, color in [
        (0, 20, "#eef2f3"), (20, 40, "#d9edf7"), (40, 60, "#fff7bc"),
        (60, 80, "#fdd49e"), (80, 100, "#fcae91"),
    ]:
        ax.axhspan(low, high, color=color, alpha=0.48, zorder=0)
    ax.plot(x, frame["sentiment"], color="#2166ac", lw=1.4, label="题材情绪分")
    ax.plot(x, frame["sentiment"].rolling(3, min_periods=1).mean(), color="#08306b", lw=2.4,
            label="3日平滑")
    raw_ax = ax.twinx()
    raw_ax.plot(x, frame["raw_heat"], color="#e6550d", lw=1.1, alpha=0.65,
                label="前三题材绝对热度")
    raw_ax.set_ylabel("绝对热度")
    raw_ax.set_ylim(0, 105)
    ax.set_ylim(0, 100)
    ax.set_ylabel("情绪分 0–100")
    ax.legend(loc="upper left", ncol=2, frameon=False)
    raw_ax.legend(loc="upper right", frameon=False)
    ax.set_title(
        f"全市场题材情绪周期  {labels[0]}—{labels[-1]}\n"
        "蓝线为滚动历史百分位，橙线为绝对热度；ST、ST摘帽和次新股已排除",
        fontsize=15, pad=12,
    )

    ax = axes[1]
    ax.bar(x, frame["limit_up"], color="#d73027", alpha=0.74, width=0.78, label="涨停数")
    ax.bar(x, -frame["break_count"], color="#1a9850", alpha=0.62, width=0.78, label="炸板数")
    ax.axhline(0, color="#555", lw=0.8)
    ax.set_ylabel("涨停 / 炸板")
    quality_ax = ax.twinx()
    quality_ax.plot(x, 100 * frame["seal_rate"], color="#542788", lw=1.4, label="封板率")
    quality_ax.step(x, 10 * frame["max_height"], color="#222", lw=1.1, alpha=0.75,
                    where="mid", label="最高板×10")
    quality_ax.set_ylim(0, max(110, float((10 * frame["max_height"]).max()) + 10))
    quality_ax.set_ylabel("封板率% / 最高板×10")
    ax.legend(loc="upper left", ncol=2, frameon=False)
    quality_ax.legend(loc="upper right", ncol=2, frameon=False)

    ax = axes[2]
    ax.plot(x, frame["top1_share"], color="#b2182b", lw=1.5, label="Top1涨停份额")
    ax.plot(x, frame["top3_share"], color="#ef8a62", lw=1.5, label="Top3涨停份额")
    ax.plot(x, frame["hhi"], color="#4d4d4d", lw=1.1, ls="--", label="题材宽度HHI×100")
    count_ax = ax.twinx()
    count_ax.bar(x, frame["broad_count"], color="#67a9cf", alpha=0.22, width=0.8,
                 label="具备持续宽度的一级题材数")
    ax.set_ylim(bottom=0)
    count_ax.set_ylim(bottom=0)
    ax.set_ylabel("集中度 %")
    count_ax.set_ylabel("强一级题材数")
    ax.legend(loc="upper left", ncol=3, frameon=False)
    count_ax.legend(loc="upper right", frameon=False)

    ax = axes[3]
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    structures = frame["structure"].tolist()
    for start, end, code in contiguous_runs(structures):
        ax.axvspan(start - 0.5, end + 0.5, color=STRUCTURE_COLORS.get(code, "#cccccc"), alpha=0.95)
        if end - start >= 2:
            ax.text((start + end) / 2, 0.5, STRUCTURE_LABELS.get(code, code),
                    ha="center", va="center", fontsize=8, color="white", fontweight="bold")
    ax.set_ylabel("结构", rotation=0, labelpad=22)
    legend = [Patch(facecolor=color, label=STRUCTURE_LABELS[code])
              for code, color in STRUCTURE_COLORS.items()]
    ax.legend(handles=legend, loc="upper center", bbox_to_anchor=(0.5, -0.92),
              ncol=5, frameon=False, fontsize=8)
    trade_date_ticks(ax, labels)
    axes[-1].set_xlabel("交易日")
    fig.text(0.01, 0.006, "数据：KPL主归因涨停事实；所有曲线只使用各交易日及以前数据。", fontsize=8, color="#666")
    return save_figure(fig, output, dpi)


def _rotation_data(start_date: str, end_date: str, dates: list[str], top_families: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = get_theme_daily(start_date=start_date, end_date=end_date)
    daily = daily[
        (daily["limit_up_count"] > 0)
        & ~daily["canonical_name"].isin(EXCLUDED_THEMES)
        & ~daily["level1_name"].isin(EXCLUDED_LEVEL1)
    ].copy()
    grouped = daily.groupby(["trade_date", "level1_name"], as_index=False).agg(
        width=("limit_up_count", "sum"),
        peak_heat=("heat_score", "max"),
        peak_height=("max_board_height", "max"),
    )
    totals = grouped.groupby("level1_name")["width"].sum().sort_values(ascending=False)
    families = totals.head(top_families).index.tolist()
    width = grouped[grouped["level1_name"].isin(families)].pivot(
        index="level1_name", columns="trade_date", values="width"
    ).reindex(index=families, columns=dates).fillna(0)
    smoothed_width = width.T.rolling(5, min_periods=1).mean().T
    ranks = smoothed_width.rank(axis=0, method="min", ascending=False)
    return width, ranks


def plot_market_rotation(width: pd.DataFrame, ranks: pd.DataFrame, output: Path, dpi: int) -> Path:
    labels = list(width.columns)
    x = np.arange(len(labels))
    fig, axes = plt.subplots(2, 1, figsize=(18, 12), sharex=True,
                             gridspec_kw={"height_ratios": [2.2, 4.3], "hspace": 0.08})
    palette = plt.cm.tab20(np.linspace(0, 1, len(width.index)))
    ax = axes[0]
    display_families = list(width.index[: min(8, len(width.index))])
    for color, family in zip(palette, width.index):
        if family not in display_families:
            continue
        visible = ranks.loc[family].where(ranks.loc[family] <= min(8, len(width.index)))
        ax.plot(x, visible, lw=1.5, color=color, alpha=0.88, label=family)
    ax.invert_yaxis()
    ax.set_ylim(min(8.5, len(width.index) + 0.5), 0.5)
    ax.set_yticks(range(1, min(8, len(width.index)) + 1))
    ax.set_ylabel("5日滚动宽度排名")
    ax.set_title("全市场一级题材轮动：5日滚动排名与每日涨停宽度热力图", fontsize=15, pad=12)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.01), ncol=6, frameon=False, fontsize=8)

    ax = axes[1]
    cmap = LinearSegmentedColormap.from_list("theme_heat", ["#ffffff", "#fee8c8", "#fdbb84", "#e34a33", "#7f0000"])
    image = ax.imshow(width.values, aspect="auto", interpolation="nearest", cmap=cmap, vmin=0)
    ax.set_yticks(np.arange(len(width.index)))
    ax.set_yticklabels(width.index, fontsize=9)
    ax.set_ylabel("一级题材")
    trade_date_ticks(ax, labels)
    ax.set_xlabel("交易日")
    cbar = fig.colorbar(image, ax=ax, pad=0.01, fraction=0.025)
    cbar.set_label("主归因涨停宽度")
    fig.text(0.01, 0.006, "一级题材内各二级题材按主归因涨停数求和；同一股票只有一个主归因，不重复计算。", fontsize=8, color="#666")
    return save_figure(fig, output, dpi)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="绘制全市场题材情绪周期和题材轮动")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--as-of", help="历史可见截止日；存在时覆盖更晚的end-date")
    parser.add_argument("--top-families", type=int, default=12)
    parser.add_argument("--save-dir", type=Path, default=DEFAULT_THEME_CHART_DIR / "market")
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args(argv)
    configure_style()
    effective_end = args.as_of if args.as_of else args.end_date
    reviews = get_market_theme_review_series(
        start_date=args.start_date, end_date=effective_end, top_n=10, leader_count=3,
    )
    if not reviews:
        raise SystemExit("指定区间没有题材复盘数据")
    frame = _review_frame(reviews)
    start, end = frame.iloc[0]["trade_date"], frame.iloc[-1]["trade_date"]
    suffix = f"{start}_{end}" + ("_asof" if args.as_of else "")
    sentiment_path = plot_market_sentiment_cycle(
        frame, args.save_dir / f"market_theme_sentiment_{suffix}.jpg", args.dpi,
    )
    width, ranks = _rotation_data(start, end, frame["trade_date"].tolist(), args.top_families)
    rotation_path = plot_market_rotation(
        width, ranks, args.save_dir / f"market_theme_rotation_{suffix}.jpg", args.dpi,
    )
    print(sentiment_path)
    print(rotation_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
