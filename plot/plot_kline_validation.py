#!/usr/bin/env python3
"""Plot frozen scenario paths against realized future closes from SCORE.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


SCENARIO_COLORS = ("#d32f2f", "#1976d2", "#00897b", "#5d4037", "#7b1fa2")
LEVEL_COLORS = {"support": "#00897b", "resistance": "#ef6c00", "target": "#8e24aa", "invalidation": "#c62828"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--packet-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--history-bars", type=int, default=50)
    parser.add_argument("--dpi", type=int, default=170)
    return parser.parse_args()


def configure_font() -> None:
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def draw_one(result: dict[str, Any], analysis: dict[str, Any], packet: dict[str, Any], output: Path, bars: int, dpi: int) -> None:
    history = pd.DataFrame(packet["daily"]).tail(bars).reset_index(drop=True)
    history["date"] = pd.to_datetime(history["trade_date"], format="%Y%m%d")
    history["close"] = pd.to_numeric(history["close"])
    future = pd.DataFrame(result["future_bars"])
    future["close"] = pd.to_numeric(future["close"])
    last_x = len(history) - 1

    fig, ax = plt.subplots(figsize=(16, 8.5))
    ax.plot(np.arange(len(history)), history["close"], color="#37474f", linewidth=1.6, label="冻结前真实收盘")
    ax.axvline(last_x, color="#1565c0", linestyle="--", linewidth=1.4)
    ax.axvspan(last_x, last_x + len(future), color="#fff8e1", alpha=0.7)

    base_close = float(packet["latest"]["close"])
    for index, scenario in enumerate(analysis["scenarios"]):
        closes = [base_close] + [float(value) for value in scenario["closes"]]
        x = np.arange(last_x, last_x + len(closes))
        label = f"冻结{scenario['id']} {scenario['name']} {scenario['weight']}%"
        ax.plot(x, closes, color=SCENARIO_COLORS[index], linestyle="--", marker="o", markersize=3, linewidth=1.4, alpha=0.85, label=label)

    actual_x = np.arange(last_x, last_x + len(future) + 1)
    actual_close = [base_close] + future["close"].astype(float).tolist()
    ax.plot(actual_x, actual_close, color="#000000", linewidth=2.5, marker="o", markersize=3.5, label="实际未来收盘")

    xmax = last_x + len(future)
    for level in analysis.get("levels", []):
        if "daily" not in level.get("timeframes", ["daily"]):
            continue
        price = float(level["price"])
        color = LEVEL_COLORS.get(level.get("kind", "support"), "#616161")
        ax.hlines(price, -0.5, xmax, color=color, linestyle=":", linewidth=1.15, alpha=0.85)
        ax.text(xmax, price, f" {level['label']} {price:.2f}", color=color, va="center", fontsize=8.5)

    labels = history["date"].dt.strftime("%m-%d").tolist() + future["trade_date"].astype(str).str[4:6].str.cat(future["trade_date"].astype(str).str[6:8], sep="-").tolist()
    ticks = sorted(set(np.linspace(0, len(labels) - 1, min(14, len(labels)), dtype=int).tolist() + [last_x]))
    ax.set_xticks(ticks)
    ax.set_xticklabels([labels[index] for index in ticks], rotation=35, ha="right", fontsize=8)
    ax.grid(alpha=0.17)
    ax.set_xlabel("交易日期；竖虚线右侧为评分时才打开的真实未来")
    ax.set_ylabel("前复权价格（元）")
    ax.legend(loc="upper left", ncol=2, fontsize=8.5)
    ax.text(
        0.012,
        0.02,
        f"路径链：{result['trajectory_signature']}\n"
        f"T+5/T+10/T+20收益：{result['return_t5_pct']:.2f}% / {result['return_t10_pct']:.2f}% / {result['return_t20_pct']:.2f}%\n"
        f"第一触发Top-1：{'正确' if result['top1_correct'] else '错误'}；五日最接近路径：{result['nearest_five_day_scenario']}",
        transform=ax.transAxes,
        va="bottom",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", edgecolor="#90a4ae", alpha=0.9),
    )
    ax.set_title(f"{result['stock_name']} {result['ts_code']}｜冻结路径与真实未来对照｜as_of {result['as_of']}", fontsize=15)
    fig.subplots_adjust(left=0.07, right=0.88, top=0.92, bottom=0.13)
    fig.savefig(output, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    configure_font()
    score = json.loads(args.score.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    for result in score["results"]:
        stem = f"{result['ts_code'].replace('.', '_')}_{result['as_of']}"
        analysis = json.loads((args.analysis_dir / f"{stem}.json").read_text(encoding="utf-8"))
        packet = json.loads((args.packet_dir / f"{stem}.json").read_text(encoding="utf-8"))
        output = args.output_dir / f"{stem}_validation.jpg"
        draw_one(result, analysis, packet, output, args.history_bars, args.dpi)
        outputs.append(str(output))
    print(json.dumps({"count": len(outputs), "outputs": outputs}, ensure_ascii=False))


if __name__ == "__main__":
    main()
