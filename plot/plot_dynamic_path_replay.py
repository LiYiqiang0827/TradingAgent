#!/usr/bin/env python3
"""Plot bar-by-bar path evidence weights and current evolution phases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd


PATHS = ("P1", "P2", "P3", "P4", "P5")
COLORS = {"P1": "#d32f2f", "P2": "#1976d2", "P3": "#00897b", "P4": "#5d4037", "P5": "#7b1fa2"}
PHASE_LABELS = {
    "pretrigger_wait": "等待触发",
    "support_test": "支撑测试",
    "breakout_hold": "突破保持",
    "breakout_retest": "突破回踩",
    "breakout_failure_warning": "突破失败预警",
    "extension_reversal_warning": "延伸反转预警",
    "structure_invalidated": "结构失效",
}
POSTURE_COLORS = {
    "observe_no_position": "#9e9e9e",
    "observe_upside_unconfirmed": "#fbc02d",
    "upside_confirmed": "#2e7d32",
    "reduce_risk": "#ef6c00",
    "exit_risk": "#c62828",
}
POSTURE_LABELS = {
    "observe_no_position": "等待/不新增风险",
    "observe_upside_unconfirmed": "回踩观察/尚未确认",
    "upside_confirmed": "上升确认",
    "reduce_risk": "降低风险",
    "exit_risk": "退出上升假设",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dynamic", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=170)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    plt.rcParams["font.sans-serif"] = ["Arial Unicode MS", "PingFang SC", "Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    data = json.loads(args.dynamic.read_text(encoding="utf-8"))
    timeline = data["timeline"]
    dates = [item["trade_date"] for item in timeline]
    x = np.arange(len(timeline))
    closes = np.array([item["bar"]["close"] for item in timeline], dtype=float)
    origin = {path: np.array([item["origin_path_evidence_weights"][path] for item in timeline]) for path in PATHS}
    active = {path: np.array([item["active_path_evidence_weights"][path] for item in timeline]) for path in PATHS}
    bounds = data["boundaries"]

    fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True, gridspec_kw={"height_ratios": [2.2, 1.5, 1.5], "hspace": 0.08})
    price_ax, origin_ax, active_ax = axes
    price_ax.plot(x, closes, color="#263238", marker="o", linewidth=2, label="新增日线收盘")
    posture_states = [item.get("long_only_risk_posture", {}).get("state", "observe_no_position") for item in timeline]
    price_ax.scatter(x, closes, c=[POSTURE_COLORS[state] for state in posture_states], s=48, zorder=4)
    for key, color, label in (("upper", "#ef6c00", "U1"), ("support", "#00897b", "S1"), ("invalidation", "#c62828", "D1")):
        price_ax.axhline(float(bounds[key]), color=color, linestyle="--", linewidth=1.3, label=f"{label} {bounds[key]:.2f}")
    if bounds.get("target") is not None:
        price_ax.axhline(float(bounds["target"]), color="#8e24aa", linestyle=":", linewidth=1.1, label=f"U2 {bounds['target']:.2f}")
    price_ax.set_ylabel("前复权价格")
    price_ax.set_title(f"{data.get('stock_name')} {data['ts_code']}｜逐日路径证据更新｜初始分析日 {data['as_of']}", fontsize=15)
    price_ax.grid(alpha=0.18)
    level_legend = price_ax.legend(loc="upper left", ncol=5, fontsize=8.5)
    present_states = list(dict.fromkeys(posture_states))
    posture_handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=POSTURE_COLORS[state], markeredgecolor="none", markersize=7, label=POSTURE_LABELS[state])
        for state in present_states
    ]
    posture_legend = price_ax.legend(handles=posture_handles, loc="lower left", ncol=min(3, len(posture_handles)), fontsize=8.2, title="做多风险状态")
    price_ax.add_artist(level_legend)

    for path in PATHS:
        origin_ax.plot(x, origin[path], color=COLORS[path], linewidth=1.8, marker="o", markersize=3, label=f"{path}起始路径")
    origin_ax.set_ylim(0, 103)
    origin_ax.set_ylabel("证据权重（%）")
    origin_ax.set_title("第一触发路径：如何开始", loc="left", fontsize=11.5)
    origin_ax.grid(alpha=0.18)
    origin_ax.legend(loc="upper left", ncol=5, fontsize=8)

    active_ax.stackplot(x, *[active[path] for path in PATHS], labels=PATHS, colors=[COLORS[path] for path in PATHS], alpha=0.78)
    active_ax.set_ylim(0, 100)
    active_ax.set_ylabel("当前路径权重（%）")
    active_ax.set_title("当前演化路径：允许突破后迁移到回踩、失败或失效", loc="left", fontsize=11.5)
    active_ax.grid(alpha=0.16)
    phase_last = None
    for index, item in enumerate(timeline):
        phase = item["current_phase"]
        if phase != phase_last:
            active_ax.axvline(index, color="#455a64", linestyle=":", linewidth=0.9)
            active_ax.text(index, 101, PHASE_LABELS.get(phase, phase), rotation=35, ha="left", va="bottom", fontsize=8, color="#37474f")
            phase_last = phase
    ticks = np.arange(len(dates))
    active_ax.set_xticks(ticks)
    active_ax.set_xticklabels([f"T+{i+1}\n{date[4:6]}-{date[6:8]}" for i, date in enumerate(dates)], fontsize=7.5)
    active_ax.set_xlabel("每加入一根新日K后立即更新；全部权重为规则证据权重")
    fig.subplots_adjust(left=0.07, right=0.98, top=0.94, bottom=0.09)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=args.dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(json.dumps({"output": str(args.output), "bars": len(timeline)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
