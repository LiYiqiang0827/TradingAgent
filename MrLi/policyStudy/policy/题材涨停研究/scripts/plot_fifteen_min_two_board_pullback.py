"""回调/二次启动15分钟试验的总体结果和一正一反路径示例。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from coreClient.data_provider import get_fifteenMin


PERIODS = [("jun_jul", "Jun-Jul"), ("aug_not_blind", "Aug"),
           ("sep_diagnostic", "Sep 1-15")]
SIGNALS = [("support", "Support hold"), ("restart", "Initial restart"),
           ("confirmed", "2-bar confirm*")]


def _summary(root: Path, results: dict) -> Path:
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(15, 5.5), layout="constrained")
    color = ["#94a3b8", "#2563eb", "#0d9488"]
    for j, (signal, label) in enumerate(SIGNALS):
        vals = [results[p]["all"][signal]["dplus1_mean_net_pct"] for p, _ in PERIODS]
        n = [results[p]["all"][signal]["dplus1_scored"] for p, _ in PERIODS]
        bars = ax.bar(np.arange(3) + (j-1)*.23, vals, width=.22, color=color[j], label=label)
        for bar, count, value in zip(bars, n, vals):
            ax.annotate(f"n={count}", (bar.get_x()+bar.get_width()/2, value),
                        xytext=(0, 3 if value >= 0 else -14), textcoords="offset points",
                        ha="center", fontsize=8)
    ax.axhline(0, color="#111827", linewidth=.8)
    ax.set_xticks(np.arange(3), [name for _, name in PERIODS])
    ax.set_ylabel("Next session close: net mean (%)")
    ax.set_title("No stable positive mean; 2-bar filter is exploratory")
    ax.grid(axis="y", alpha=.2)
    ax.legend(fontsize=8)
    ratio = np.zeros((3, 3))
    for j, (signal, _) in enumerate(SIGNALS):
        for i, (period, _) in enumerate(PERIODS):
            item = results[period]["all"][signal]
            up = item["two_session_up3_before_down3"]
            down = item["two_session_down3_before_up3"]
            n = item["buyable"]
            ratio[j, i] = 100*up/n if n else 0
            ax2.text(i, j, f"{ratio[j,i]:.0f}%\n{up} up / {down} down",
                     ha="center", va="center", fontsize=10,
                     color="white" if ratio[j, i] >= 50 else "#111827")
    ax2.imshow(ratio, vmin=0, vmax=70, cmap="Blues", aspect="auto")
    ax2.set_xticks(np.arange(3), [name for _, name in PERIODS])
    ax2.set_yticks(np.arange(3), [name for _, name in SIGNALS])
    ax2.set_title("+3% close before -3% close within two sessions")
    fig.suptitle("Two-board leaders: 15-minute support and restart signals | 2026")
    out = root / "fifteen_min_signal_results.jpg"
    fig.savefig(out, dpi=170, facecolor="white")
    plt.close(fig)
    return out


def _candles(ax, df: pd.DataFrame) -> None:
    for i, row in enumerate(df.itertuples()):
        color = "#dc2626" if row.close >= row.open else "#16a34a"
        ax.vlines(i, row.low, row.high, color=color, linewidth=.8)
        bottom = min(row.open, row.close)
        height = max(abs(row.close-row.open), .005)
        ax.add_patch(Rectangle((i-.32, bottom), .64, height,
                               facecolor=color, edgecolor=color, linewidth=.35))


def _path(ax, item: pd.Series, end_date: str) -> None:
    df = get_fifteenMin(ts_code=str(item.ts_code), start_date=str(item.dminus1), end_date=end_date)
    df = df.sort_values("datetime").reset_index(drop=True)
    _candles(ax, df)
    x = np.arange(len(df))
    ax.plot(x, df.close.rolling(5).mean(), color="#7c3aed", linewidth=1,
            alpha=.75, label="15m MA5")
    # 成交量压在图底部，方便核对突破K线是否真的放量。
    volume_ax = ax.twinx()
    volume_ax.bar(x, df.vol / 1_000_000, width=.58, color="#64748b", alpha=.16)
    volume_ax.set_ylim(0, max(1, float(df.vol.max() / 1_000_000) * 5))
    volume_ax.set_yticks([])
    volume_ax.set_zorder(0)
    ax.set_zorder(1)
    ax.patch.set_alpha(0)
    ax.axhline(item.second_close, color="#ea580c", linestyle="--", linewidth=1,
               label="D-1 second-board close")
    if pd.notna(item.restart_neckline):
        ax.axhline(item.restart_neckline, color="#2563eb", linestyle=":", linewidth=1,
                   label="breakout neckline")
    marks = [("support_signal_time", "support", "o", "#d97706"),
             ("restart_signal_time", "restart", "^", "#2563eb"),
             ("confirmed_signal_time", "confirmed*", "D", "#0d9488"),
             ("restart_entry_time", "next-bar entry", "x", "#111827")]
    for key, name, marker, color in marks:
        value = item.get(key)
        if pd.isna(value):
            continue
        point = df.index[df["datetime"].eq(pd.Timestamp(value))]
        if len(point):
            i = int(point[0])
            price = df.iloc[i].open if key == "restart_entry_time" else df.iloc[i].close
            ax.scatter([i], [price], s=70, marker=marker, c=color,
                       zorder=5, label=name)
    ticks = df.index[df.time_idx.eq(0)].to_list()
    ax.set_xticks(ticks, [str(df.iloc[i].trade_date) for i in ticks], rotation=25)
    ax.set_xlim(-1, len(df))
    ax.set_title(f"{item.ts_code} {item['name']} | D0 {item.d0} | net {100*item.restart_dplus1_net:+.1f}%")
    ax.set_ylabel("Unadjusted price (CNY)")
    ax.grid(alpha=.15)
    ax.legend(fontsize=7, loc="upper left", ncol=2)


def _examples(root: Path, events: pd.DataFrame) -> Path:
    # 一条延续、一条失败；案例是在结果出来后挑选，只作机制展示。
    selections = [("20260903", "600967.SH", "20260909"),
                  ("20260910", "002349.SZ", "20260914")]
    fig, axes = plt.subplots(2, 1, figsize=(15, 9), layout="constrained")
    for ax, (d0, code, end_date) in zip(axes, selections):
        item = events[(events.d0 == d0) & events.ts_code.eq(code)].iloc[0]
        _path(ax, item, end_date)
    fig.suptitle("15-minute continuation and failed restart; post-hoc illustration, not strategy proof")
    out = root / "fifteen_min_restart_examples.jpg"
    fig.savefig(out, dpi=160, facecolor="white")
    plt.close(fig)
    return out


def main() -> None:
    plt.rcParams["font.family"] = "Arial Unicode MS"
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="outputs/fifteen_min_two_board_pullback_202606_20260915")
    args = parser.parse_args()
    root = Path(args.input)
    results = json.loads((root / "results.json").read_text(encoding="utf-8"))
    events = pd.read_csv(root / "events.csv", dtype={"d0": str, "dminus1": str})
    print(_summary(root, results))
    print(_examples(root, events))


if __name__ == "__main__":
    main()
