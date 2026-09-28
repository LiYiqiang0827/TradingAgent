#!/usr/bin/env python3
"""绘制单题材生命周期总览和核心股演化图。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import textwrap

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from coreClient.data_provider import get_theme_cycle_data  # noqa: E402
from plot.theme_cycle_common import (  # noqa: E402
    DEFAULT_THEME_CHART_DIR,
    LIFECYCLE_COLORS,
    configure_style,
    contiguous_runs,
    safe_filename,
    save_figure,
    trade_date_ticks,
)


def _catalysts(analyses: pd.DataFrame, labels: list[str]) -> list[dict]:
    if analyses is None or analyses.empty:
        return []
    valid_dates = set(labels)
    result: list[dict] = []
    summaries = analyses[analyses["analysis_type"] == "episode_summary"]
    for row in summaries.itertuples(index=False):
        analysis = getattr(row, "analysis", {}) or {}
        for item in analysis.get("catalysts", []):
            date = str(item.get("date") or item.get("event_time") or "")[:10].replace("-", "")
            if date not in valid_dates:
                continue
            if item.get("causal_status") == "background_only" or item.get("role") == "late_explanation":
                continue
            result.append({
                "date": date,
                "summary": str(item.get("summary") or "催化事件"),
                "role": str(item.get("role") or "catalyst"),
                "review_status": getattr(row, "review_status", "needs_review"),
            })
    deduped = []
    seen = set()
    for item in sorted(result, key=lambda value: (value["date"], value["summary"])):
        key = (item["date"], item["summary"][:60])
        if key not in seen:
            deduped.append(item)
            seen.add(key)
    return deduped[:10]


def _shade_episodes(axes, episodes: pd.DataFrame, date_to_x: dict[str, int]) -> None:
    if episodes is None or episodes.empty:
        return
    for index, row in enumerate(episodes.itertuples(index=False)):
        start = date_to_x.get(str(row.start_date))
        last = date_to_x.get(str(row.last_active_date))
        if start is None and last is None:
            continue
        start = 0 if start is None else start
        last = max(date_to_x.values()) if last is None else last
        color = "#deebf7" if index % 2 == 0 else "#f7f7f7"
        for ax in axes:
            ax.axvspan(start - 0.45, last + 0.45, color=color, alpha=0.34, zorder=0)
        peak = date_to_x.get(str(row.peak_date))
        if peak is not None:
            axes[0].axvline(peak, color="#9e9ac8", lw=0.9, ls="--", alpha=0.75)


def plot_theme_cycle(data: dict, output: Path, dpi: int) -> Path:
    daily = data["daily"].copy()
    labels = daily["trade_date"].astype(str).tolist()
    x = np.arange(len(daily))
    date_to_x = {date: index for index, date in enumerate(labels)}
    review_map = {str(item["trade_date"]): item for item in data.get("market_reviews", [])}
    market_sentiment = [float(review_map.get(date, {}).get("theme_sentiment_score") or 0) for date in labels]
    fig, axes = plt.subplots(
        5, 1, figsize=(18, 15), sharex=True,
        gridspec_kw={"height_ratios": [3.0, 2.2, 2.0, 2.0, 0.65], "hspace": 0.08},
    )
    _shade_episodes(axes[:-1], data.get("episodes"), date_to_x)

    ax = axes[0]
    ax.plot(x, daily["heat_score"], color="#d73027", lw=2.0, label="题材绝对热度")
    ax.plot(x, 100 * daily["heat_percentile_120"], color="#fdae61", lw=1.4,
            label="题材自身120日热度百分位")
    ax.plot(x, market_sentiment, color="#4d4d4d", lw=1.2, ls="--", alpha=0.8,
            label="全市场题材情绪")
    ax.set_ylim(0, 105)
    ax.set_ylabel("热度 / 百分位")
    ax.legend(loc="upper left", ncol=3, frameon=False)
    mode = "历史可见截面" if data.get("mode") == "as_of" else "完整研究视图"
    profile = data["profile"]
    ax.set_title(
        f"{profile['canonical_name']}（{profile.get('level1_name', '待归类')}）题材生命周期  "
        f"{labels[0]}—{labels[-1]}  [{mode}]",
        fontsize=15, pad=12,
    )
    catalysts = _catalysts(data.get("analyses"), labels)
    for offset, item in enumerate(catalysts):
        xpos = date_to_x[item["date"]]
        ypos = min(98, float(daily.iloc[xpos]["heat_score"]) + 10 + 8 * (offset % 3))
        ax.scatter([xpos], [ypos], marker="*", s=105, color="#7b3294", zorder=6)
        short = textwrap.shorten(item["summary"], width=24, placeholder="…")
        suffix = "†" if item["review_status"] != "confirmed" else ""
        ax.annotate(short + suffix, (xpos, ypos), xytext=(3, 7), textcoords="offset points",
                    rotation=35, ha="left", va="bottom", fontsize=7, color="#542788")

    ax = axes[1]
    ax.bar(x, daily["limit_up_count"], color="#d73027", alpha=0.78, width=0.76, label="涨停数")
    ax.bar(x, -daily["break_count"], color="#1a9850", alpha=0.64, width=0.76, label="炸板数")
    ax.axhline(0, color="#555", lw=0.8)
    seal_ax = ax.twinx()
    seal = 100 * pd.to_numeric(daily["seal_rate"], errors="coerce")
    seal_ax.plot(x, seal, color="#542788", lw=1.4, marker="o", ms=2.5, label="封板率")
    seal_ax.set_ylim(0, 105)
    ax.set_ylabel("涨停 / 炸板")
    seal_ax.set_ylabel("封板率 %")
    ax.legend(loc="upper left", ncol=2, frameon=False)
    seal_ax.legend(loc="upper right", frameon=False)

    ax = axes[2]
    ax.step(x, daily["max_board_height"], where="mid", color="#b2182b", lw=2.0,
            label="最高板")
    ax.plot(x, daily["ladder_points"], color="#2166ac", lw=1.4, marker=".",
            label="梯队积分")
    ax.bar(x, daily["multi_board_count"], color="#fdd49e", alpha=0.45, width=0.72,
           label="连板股数")
    ax.set_ylim(bottom=0)
    ax.set_ylabel("高度 / 梯队")
    ax.legend(loc="upper left", ncol=3, frameon=False)

    ax = axes[3]
    rank = pd.to_numeric(daily["market_rank"], errors="coerce")
    ax.plot(x, rank, color="#2c7fb8", lw=1.6, marker="o", ms=2.8, label="全市场题材排名")
    max_rank = max(10, int(rank.max()) + 1) if rank.notna().any() else 10
    ax.set_ylim(max_rank, 0.5)
    ax.set_ylabel("题材排名（1为最强）")
    share_ax = ax.twinx()
    share_ax.fill_between(x, 0, 100 * daily["limit_up_share"], color="#f03b20", alpha=0.20,
                          label="全市场涨停份额")
    share_ax.plot(x, 100 * daily["limit_up_share"], color="#f03b20", lw=1.1)
    share_ax.set_ylim(bottom=0)
    share_ax.set_ylabel("涨停份额 %")
    ax.legend(loc="upper left", frameon=False)
    share_ax.legend(loc="upper right", frameon=False)

    ax = axes[4]
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    states = daily["lifecycle_state"].astype(str).tolist()
    for start, end, state in contiguous_runs(states):
        ax.axvspan(start - 0.5, end + 0.5, color=LIFECYCLE_COLORS.get(state, "#cccccc"), alpha=0.96)
        if end - start >= 3:
            ax.text((start + end) / 2, 0.5, state, ha="center", va="center", fontsize=8,
                    color="#222", fontweight="bold")
    ax.set_ylabel("阶段", rotation=0, labelpad=22)
    trade_date_ticks(ax, labels)
    ax.set_xlabel("交易日")
    legend_states = [state for state in LIFECYCLE_COLORS if state in set(states)]
    ax.legend(handles=[Patch(facecolor=LIFECYCLE_COLORS[state], label=state) for state in legend_states],
              loc="upper center", bbox_to_anchor=(0.5, -0.9), ncol=min(9, len(legend_states)),
              frameon=False, fontsize=8)
    note = "† 催化尚待人工复核。" if any(item["review_status"] != "confirmed" for item in catalysts) else ""
    if data.get("mode") == "as_of":
        note += " 历史可见模式未使用截止日之后生成的模型归因或最终周期边界。"
    else:
        note += " 完整研究视图包含事后确认的周期边界。"
    fig.text(0.01, 0.006, note.strip(), fontsize=8, color="#666")
    return save_figure(fig, output, dpi)


def plot_core_evolution(data: dict, output: Path, dpi: int, max_stocks: int) -> Path:
    events = data["events"].copy()
    if events.empty:
        raise ValueError("指定题材区间没有涨停或炸板事件")
    labels = data["daily"]["trade_date"].astype(str).tolist()
    date_to_x = {date: index for index, date in enumerate(labels)}
    events["board_height"] = pd.to_numeric(events["board_height"], errors="coerce").fillna(1)
    ranking = events.groupby(["ts_code", "name"], as_index=False).agg(
        limit_days=("tag", lambda values: int((values == "涨停").sum())),
        max_height=("board_height", "max"),
        event_days=("trade_date", "nunique"),
    ).sort_values(["limit_days", "max_height", "event_days", "ts_code"], ascending=[False, False, False, True])
    selected = ranking.head(max_stocks)
    codes = selected["ts_code"].tolist()[::-1]
    y_map = {code: index for index, code in enumerate(codes)}
    names = dict(zip(selected["ts_code"], selected["name"]))
    events = events[events["ts_code"].isin(codes)].copy()
    fig, ax = plt.subplots(figsize=(18, max(8, 0.48 * len(codes) + 3)))
    for row in events.itertuples(index=False):
        xpos, ypos = date_to_x.get(str(row.trade_date)), y_map.get(str(row.ts_code))
        if xpos is None or ypos is None:
            continue
        size = 26 + 30 * max(1, float(row.board_height))
        if row.tag == "涨停":
            ax.scatter(xpos, ypos, s=size, color="#d73027", alpha=0.78, edgecolor="white",
                       linewidth=0.5, zorder=3)
            if row.board_height >= 2:
                ax.text(xpos, ypos, str(int(row.board_height)), ha="center", va="center",
                        fontsize=7, color="white", fontweight="bold", zorder=4)
        else:
            ax.scatter(xpos, ypos, s=size * 0.72, marker="x", color="#1a9850", lw=1.5, zorder=3)
    leaders = data.get("leaders")
    if leaders is not None and not leaders.empty:
        edge_colors = {1: "#ffd700", 2: "#ff8c00", 3: "#b0b0b0"}
        for row in leaders.itertuples(index=False):
            if row.ts_code not in y_map or str(row.trade_date) not in date_to_x:
                continue
            ax.scatter(date_to_x[str(row.trade_date)], y_map[row.ts_code], s=150,
                       facecolors="none", edgecolors=edge_colors.get(int(row.rank), "#333"),
                       linewidth=2.0, zorder=5)
    catalysts = _catalysts(data.get("analyses"), labels)
    for item in catalysts:
        xpos = date_to_x[item["date"]]
        ax.axvline(xpos, color="#7b3294", lw=0.9, ls="--", alpha=0.6, zorder=1)
    ax.set_yticks(range(len(codes)))
    ax.set_yticklabels([f"{names.get(code, code)}  {code}" for code in codes], fontsize=9)
    trade_date_ticks(ax, labels)
    ax.set_xlabel("交易日")
    ax.set_ylabel("核心股候选")
    profile = data["profile"]
    ax.set_title(
        f"{profile['canonical_name']}核心股演化：涨停、炸板、板高度与当日龙一至龙三\n"
        "红圆=涨停，绿色×=炸板，圆内数字=板高度，金/橙/银外圈=龙一/龙二/龙三",
        fontsize=14, pad=12,
    )
    ax.set_xlim(-0.7, len(labels) - 0.3)
    ax.grid(axis="x", alpha=0.16)
    ax.grid(axis="y", alpha=0.22)
    fig.text(0.01, 0.006, "紫色虚线为已入库周期摘要中的催化日期；因证据状态不同，仍需结合题材文档复核。", fontsize=8, color="#666")
    return save_figure(fig, output, dpi)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="绘制单题材生命周期和核心股演化")
    parser.add_argument("theme", help="题材名称或稳定theme_id")
    parser.add_argument("--theme-id", action="store_true", help="把theme参数解释为theme_id")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--as-of", help="历史可见截止日")
    parser.add_argument("--max-stocks", type=int, default=20)
    parser.add_argument("--save-dir", type=Path, default=DEFAULT_THEME_CHART_DIR / "themes")
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args(argv)
    configure_style()
    kwargs = {"theme_id": args.theme} if args.theme_id else {"name": args.theme}
    data = get_theme_cycle_data(
        **kwargs, start_date=args.start_date, end_date=args.end_date, as_of=args.as_of,
    )
    if not data:
        raise SystemExit(f"题材不存在或没有可用数据: {args.theme}")
    profile = data["profile"]
    safe = safe_filename(profile["canonical_name"])
    suffix = f"{data['start_date']}_{data['end_date']}" + ("_asof" if args.as_of else "")
    cycle_path = plot_theme_cycle(
        data, args.save_dir / safe / f"theme_cycle_{safe}_{suffix}.jpg", args.dpi,
    )
    core_path = plot_core_evolution(
        data, args.save_dir / safe / f"theme_core_evolution_{safe}_{suffix}.jpg",
        args.dpi, args.max_stocks,
    )
    print(cycle_path)
    print(core_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
