#!/usr/bin/env python3
"""Plot future-isolated static K-line structure and hypothetical path charts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.patches import Rectangle
from matplotlib.ticker import MaxNLocator
import numpy as np
import pandas as pd


MA_WINDOWS = (5, 10, 20, 30, 60, 120, 250)
SCENARIO_MAS = (5, 10, 20, 60, 120)
MA_COLORS = {
    5: "#e6b800",
    10: "#2f95c7",
    20: "#9b59b6",
    30: "#ef7f3b",
    60: "#536dfe",
    120: "#7f6045",
    250: "#263238",
}
LEVEL_COLORS = {
    "support": "#00897b",
    "resistance": "#ef6c00",
    "target": "#8e24aa",
    "invalidation": "#c62828",
}
SCENARIO_COLORS = ("#d32f2f", "#1976d2", "#00897b", "#5d4037", "#7b1fa2", "#455a64")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--history-bars", type=int, default=80)
    parser.add_argument("--dpi", type=int, default=170)
    parser.add_argument(
        "--charts",
        choices=("all", "summary", "top"),
        default="all",
        help="all: summary plus every path; summary: three overview charts; top: summary plus highest-weight path",
    )
    args = parser.parse_args()
    if args.history_bars < 30:
        parser.error("--history-bars must be at least 30")
    return args


def configure_font() -> None:
    plt.rcParams["font.sans-serif"] = [
        "Arial Unicode MS",
        "PingFang SC",
        "Hiragino Sans GB",
        "Microsoft YaHei",
        "SimHei",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def safe_name(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", text).strip("_")


def load_inputs(packet_path: Path, analysis_path: Path) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    if packet.get("ts_code") != analysis.get("ts_code") or packet.get("as_of") != analysis.get("as_of"):
        raise ValueError("Packet and analysis must have identical ts_code and as_of")
    if packet.get("available_end") != packet.get("as_of"):
        raise ValueError("Packet contains an end date different from as_of")

    frame = pd.DataFrame(packet["daily"]).copy()
    frame["date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d")
    for column in ("open", "high", "low", "close", "vol"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close", "vol"]).sort_values("date").reset_index(drop=True)
    if frame.iloc[-1]["trade_date"] != packet["as_of"]:
        raise ValueError("Last daily bar does not equal as_of")
    for window in MA_WINDOWS:
        frame[f"ma{window}"] = frame["close"].rolling(window).mean()
    return packet, analysis, frame


def draw_candles(ax: Axes, frame: pd.DataFrame, start_x: int = 0, alpha: float = 1.0, hypothetical: bool = False) -> None:
    width = 0.62
    for offset, row in enumerate(frame.itertuples(index=False), start=start_x):
        up = row.close >= row.open
        color = "#d64541" if up else "#26a269"
        ax.vlines(offset, row.low, row.high, color=color, linewidth=1.0, alpha=alpha, zorder=2)
        lower = min(row.open, row.close)
        height = abs(row.close - row.open)
        if height == 0:
            height = max(abs(row.close) * 0.001, 0.01)
        rectangle = Rectangle(
            (offset - width / 2, lower),
            width,
            height,
            facecolor="none" if hypothetical else color,
            edgecolor=color,
            linewidth=1.25 if hypothetical else 0.7,
            alpha=alpha,
            hatch="//" if hypothetical else None,
            zorder=3,
        )
        ax.add_patch(rectangle)


def draw_levels(ax: Axes, levels: list[dict[str, Any]], xmin: float, xmax: float, timeframe: str = "daily") -> None:
    for level in levels:
        timeframes = level.get("timeframes", ["daily", "weekly", "monthly"])
        if timeframe not in timeframes:
            continue
        price = float(level["price"])
        kind = str(level.get("kind", "support"))
        color = LEVEL_COLORS.get(kind, "#616161")
        ax.hlines(price, xmin, xmax, colors=color, linestyles="--", linewidth=1.25, alpha=0.9)
        label = level.get("label", kind)
        ax.text(
            xmax,
            price,
            f" {label} {price:.2f}",
            va="center",
            ha="left",
            fontsize=8.5,
            color=color,
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.72, pad=1.2),
        )


def format_x_axis(ax: Axes, labels: list[str], future_start: int | None = None) -> None:
    count = len(labels)
    ticks = sorted(set(np.linspace(0, count - 1, min(9, count), dtype=int).tolist()))
    if future_start is not None:
        ticks.extend(range(future_start, count))
        ticks = sorted(set(ticks))
    ax.set_xticks(ticks)
    ax.set_xticklabels([labels[index] for index in ticks], rotation=35, ha="right", fontsize=8)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))


def plot_static(packet: dict[str, Any], analysis: dict[str, Any], frame: pd.DataFrame, output: Path, bars: int, dpi: int) -> None:
    visible = frame.tail(bars).reset_index(drop=True)
    fig, (price_ax, volume_ax) = plt.subplots(
        2,
        1,
        figsize=(16, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04},
    )
    x = np.arange(len(visible))
    draw_candles(price_ax, visible)
    for window in MA_WINDOWS:
        price_ax.plot(x, visible[f"ma{window}"], color=MA_COLORS[window], linewidth=1.05, label=f"MA{window}")
    draw_levels(price_ax, analysis.get("levels", []), -0.5, len(visible) - 0.5, "daily")
    price_ax.axvline(len(visible) - 1, color="#f57c00", linestyle=":", linewidth=1.4)
    price_ax.set_ylabel("前复权价格（元）")
    price_ax.grid(alpha=0.16)
    price_ax.legend(loc="upper left", ncol=7, fontsize=8)
    summary = analysis.get("structure_summary", "")
    if summary:
        price_ax.text(
            0.012,
            0.02,
            summary,
            transform=price_ax.transAxes,
            va="bottom",
            ha="left",
            fontsize=9.2,
            bbox=dict(boxstyle="round,pad=0.4", facecolor="white", edgecolor="#90a4ae", alpha=0.88),
        )

    colors = np.where(visible["close"] >= visible["open"], "#d64541", "#26a269")
    volume_ax.bar(x, visible["vol"], width=0.62, color=colors, alpha=0.78)
    volume_ax.set_ylabel("成交量")
    volume_ax.grid(alpha=0.13)
    labels = visible["date"].dt.strftime("%Y-%m-%d").tolist()
    format_x_axis(volume_ax, labels)
    volume_ax.set_xlabel("交易日期")

    name = packet.get("stock_name") or packet["ts_code"]
    fig.suptitle(f"{name} {packet['ts_code']} 静态结构图｜截止 {packet['as_of']}｜仅使用当日及以前数据", fontsize=15)
    fig.subplots_adjust(left=0.07, right=0.89, top=0.92, bottom=0.12)
    fig.savefig(output, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def synthesize_future(last_close: float, closes: list[float], atr: float) -> pd.DataFrame:
    rows: list[dict[str, float]] = []
    previous = last_close
    pad = max(atr * 0.12, last_close * 0.003)
    for close in closes:
        open_price = previous
        high = max(open_price, close) + pad
        low = max(0.01, min(open_price, close) - pad)
        rows.append({"open": open_price, "high": high, "low": low, "close": close, "vol": np.nan})
        previous = close
    return pd.DataFrame(rows)


def plot_scenario(
    packet: dict[str, Any],
    analysis: dict[str, Any],
    frame: pd.DataFrame,
    scenario: dict[str, Any],
    output: Path,
    bars: int,
    dpi: int,
) -> None:
    history = frame.tail(bars).copy().reset_index(drop=True)
    closes = [float(value) for value in scenario["closes"]]
    atr = float(packet["latest"].get("atr14") or history["close"].tail(14).std())
    future = synthesize_future(float(history.iloc[-1]["close"]), closes, atr)
    combined_close = pd.concat([history["close"], future["close"]], ignore_index=True)
    for window in SCENARIO_MAS:
        history_values = frame["close"].tolist()[:-len(history)] + combined_close.tolist()
        full_ma = pd.Series(history_values).rolling(window).mean()
        combined_length = len(combined_close)
        combined_ma = full_ma.tail(combined_length).reset_index(drop=True)
        history[f"scenario_ma{window}"] = combined_ma.iloc[: len(history)].to_numpy()
        future[f"scenario_ma{window}"] = combined_ma.iloc[len(history) :].to_numpy()

    fig, (price_ax, volume_ax) = plt.subplots(
        2,
        1,
        figsize=(16, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04},
    )
    future_start = len(history)
    total = len(history) + len(future)
    price_ax.axvspan(future_start - 0.5, total - 0.5, color="#e3f2fd", alpha=0.75, zorder=0)
    volume_ax.axvspan(future_start - 0.5, total - 0.5, color="#e3f2fd", alpha=0.75, zorder=0)
    draw_candles(price_ax, history)
    draw_candles(price_ax, future, start_x=future_start, alpha=0.9, hypothetical=True)

    all_x = np.arange(total)
    for window in SCENARIO_MAS:
        values = pd.concat([history[f"scenario_ma{window}"], future[f"scenario_ma{window}"]], ignore_index=True)
        price_ax.plot(all_x, values, color=MA_COLORS[window], linewidth=1.15, label=f"MA{window}")
    draw_levels(price_ax, analysis.get("levels", []), -0.5, total - 0.5, "daily")
    price_ax.axvline(future_start - 0.5, color="#1565c0", linestyle="--", linewidth=1.5)
    price_ax.text(
        future_start + max(len(future) - 1, 0) / 2,
        price_ax.get_ylim()[1],
        "情景假设区｜非真实行情",
        ha="center",
        va="top",
        fontsize=10,
        color="#0d47a1",
        bbox=dict(facecolor="white", edgecolor="#90caf9", alpha=0.85, pad=2),
    )
    price_ax.set_ylabel("前复权价格（元）")
    price_ax.grid(alpha=0.16)
    price_ax.legend(loc="upper left", ncol=5, fontsize=8)

    x_history = np.arange(len(history))
    colors = np.where(history["close"] >= history["open"], "#d64541", "#26a269")
    volume_ax.bar(x_history, history["vol"], width=0.62, color=colors, alpha=0.78)
    volume_ax.text(
        future_start + max(len(future) - 1, 0) / 2,
        max(float(history["vol"].max()) * 0.45, 1.0),
        "未来成交量未假设",
        ha="center",
        va="center",
        fontsize=9,
        color="#546e7a",
    )
    volume_ax.set_ylabel("成交量")
    volume_ax.grid(alpha=0.13)
    labels = history["date"].dt.strftime("%m-%d").tolist() + [f"T+{index}" for index in range(1, len(future) + 1)]
    format_x_axis(volume_ax, labels, future_start=future_start)
    volume_ax.set_xlabel("历史交易日 / 假设交易周期")

    weight = scenario.get("weight")
    probability_type = scenario.get("probability_type", "unspecified")
    meta = (
        f"权重 {weight}%（{probability_type}）｜预计周期 {scenario.get('expected_period', '未注明')}\n"
        f"触发：{scenario.get('trigger', '未注明')}\n"
        f"失效：{scenario.get('invalidation', '未注明')}"
    )
    price_ax.text(
        0.012,
        0.02,
        meta,
        transform=price_ax.transAxes,
        va="bottom",
        ha="left",
        fontsize=8.7,
        bbox=dict(boxstyle="round,pad=0.38", facecolor="white", edgecolor="#90a4ae", alpha=0.9),
    )
    name = packet.get("stock_name") or packet["ts_code"]
    fig.suptitle(
        f"{name} {packet['ts_code']}｜{scenario.get('id', '')} {scenario['name']}｜截止 {packet['as_of']} 的条件推演",
        fontsize=14.5,
    )
    fig.subplots_adjust(left=0.07, right=0.89, top=0.92, bottom=0.12)
    fig.savefig(output, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_overview(packet: dict[str, Any], analysis: dict[str, Any], frame: pd.DataFrame, output: Path, bars: int, dpi: int) -> None:
    history = frame.tail(bars).reset_index(drop=True)
    scenarios = analysis.get("scenarios", [])
    max_horizon = max(len(item["closes"]) for item in scenarios)
    future_start = len(history) - 1
    fig, ax = plt.subplots(figsize=(16, 8.5))
    x_history = np.arange(len(history))
    ax.plot(x_history, history["close"], color="#263238", linewidth=1.5, label="历史收盘")
    ax.axvspan(future_start, future_start + max_horizon, color="#e3f2fd", alpha=0.7)
    ax.axvline(future_start, color="#1565c0", linestyle="--", linewidth=1.4)
    last_close = float(history.iloc[-1]["close"])
    for index, scenario in enumerate(scenarios):
        closes = [last_close] + [float(value) for value in scenario["closes"]]
        x = np.arange(future_start, future_start + len(closes))
        label = f"{scenario.get('id', '')} {scenario['name']} {scenario.get('weight', '?')}%"
        ax.plot(x, closes, marker="o", markersize=3.5, linewidth=1.8, linestyle="--", color=SCENARIO_COLORS[index % len(SCENARIO_COLORS)], label=label)
    draw_levels(ax, analysis.get("levels", []), -0.5, future_start + max_horizon, "daily")
    ax.text(
        future_start + max_horizon / 2,
        ax.get_ylim()[1],
        "未来条件路径｜非真实行情",
        ha="center",
        va="top",
        fontsize=11,
        color="#0d47a1",
    )
    labels = history["date"].dt.strftime("%m-%d").tolist() + [f"T+{index}" for index in range(1, max_horizon + 1)]
    ticks = sorted(set(np.linspace(0, len(labels) - 1, min(12, len(labels)), dtype=int).tolist() + list(range(future_start, len(labels)))))
    ax.set_xticks(ticks)
    ax.set_xticklabels([labels[index] for index in ticks], rotation=35, ha="right", fontsize=8)
    ax.set_ylabel("前复权价格（元）")
    ax.set_xlabel("历史交易日 / 假设交易周期")
    ax.grid(alpha=0.17)
    ax.legend(loc="upper left", ncol=2, fontsize=9)
    name = packet.get("stock_name") or packet["ts_code"]
    ax.set_title(f"{name} {packet['ts_code']} 动态路径总览｜基于 {packet['as_of']} 及以前数据", fontsize=15)
    fig.subplots_adjust(left=0.07, right=0.89, top=0.92, bottom=0.13)
    fig.savefig(output, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def aggregated_frame(items: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(items).copy()
    frame["date"] = pd.to_datetime(frame["period_end"], format="%Y%m%d")
    for column in ("open", "high", "low", "close", "vol"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


def plot_multitimeframe(
    packet: dict[str, Any],
    analysis: dict[str, Any],
    daily: pd.DataFrame,
    output: Path,
    dpi: int,
) -> None:
    weekly = aggregated_frame(packet["weekly_from_truncated_daily"])
    monthly = aggregated_frame(packet["monthly_from_truncated_daily"])
    panels = [
        ("月线｜主要趋势与历史空间", monthly.tail(72).reset_index(drop=True), (3, 6, 12, 24), "monthly"),
        ("周线｜中级趋势与平台结构", weekly.tail(156).reset_index(drop=True), (5, 10, 20, 40), "weekly"),
        ("日线｜当前节奏与短期触发", daily.tail(120).reset_index(drop=True), (5, 20, 60, 120), "daily"),
    ]
    fig, axes = plt.subplots(3, 1, figsize=(17, 14), gridspec_kw={"hspace": 0.25})
    palette = ("#e6b800", "#2f95c7", "#9b59b6", "#536dfe")
    for ax, (title, frame, windows, timeframe) in zip(axes, panels):
        for window in windows:
            frame[f"ma{window}"] = frame["close"].rolling(window).mean()
        draw_candles(ax, frame)
        x = np.arange(len(frame))
        for color, window in zip(palette, windows):
            ax.plot(x, frame[f"ma{window}"], color=color, linewidth=1.05, label=f"MA{window}")
        draw_levels(ax, analysis.get("levels", []), -0.5, len(frame) - 0.5, timeframe)
        ax.axvline(len(frame) - 1, color="#f57c00", linestyle=":", linewidth=1.3)
        labels = frame["date"].dt.strftime("%Y-%m-%d").tolist()
        format_x_axis(ax, labels)
        ax.set_title(title, loc="left", fontsize=12.5)
        ax.set_ylabel("前复权价格（元）")
        ax.grid(alpha=0.16)
        ax.legend(loc="upper left", ncol=len(windows), fontsize=8)
    axes[-1].set_xlabel("周期结束日；最后一个周期可能尚未结束，但数据严格截止分析日")
    name = packet.get("stock_name") or packet["ts_code"]
    fig.suptitle(
        f"{name} {packet['ts_code']} 日/周/月联合静态结构｜截止 {packet['as_of']}｜无未来数据",
        fontsize=16,
    )
    fig.subplots_adjust(left=0.07, right=0.89, top=0.94, bottom=0.07)
    fig.savefig(output, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    configure_font()
    packet, analysis, frame = load_inputs(args.packet, args.analysis)
    scenarios = analysis.get("scenarios", [])
    if not scenarios:
        raise ValueError("Analysis must contain at least one scenario")
    total_weight = sum(float(item.get("weight", 0)) for item in scenarios)
    if abs(total_weight - 100.0) > 1e-6:
        raise ValueError(f"Scenario weights must sum to 100, got {total_weight}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{packet['ts_code'].replace('.', '_')}_{packet['as_of']}"
    static_output = args.output_dir / f"{prefix}_static.jpg"
    multitimeframe_output = args.output_dir / f"{prefix}_multitimeframe_static.jpg"
    overview_output = args.output_dir / f"{prefix}_scenario_overview.jpg"
    plot_static(packet, analysis, frame, static_output, args.history_bars, args.dpi)
    plot_multitimeframe(packet, analysis, frame, multitimeframe_output, args.dpi)
    plot_overview(packet, analysis, frame, overview_output, args.history_bars, args.dpi)
    outputs = [str(static_output), str(multitimeframe_output), str(overview_output)]
    scenario_outputs = scenarios
    if args.charts == "summary":
        scenario_outputs = []
    elif args.charts == "top":
        scenario_outputs = [max(scenarios, key=lambda item: float(item.get("weight", 0)))]
    for scenario in scenario_outputs:
        scenario_output = args.output_dir / f"{prefix}_{safe_name(str(scenario.get('id', scenario['name'])))}.jpg"
        plot_scenario(packet, analysis, frame, scenario, scenario_output, args.history_bars, args.dpi)
        outputs.append(str(scenario_output))
    print(json.dumps({"outputs": outputs, "count": len(outputs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
