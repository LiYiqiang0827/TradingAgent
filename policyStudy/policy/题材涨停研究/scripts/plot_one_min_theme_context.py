"""可视化题材条件的月份反转和按交易日聚集风险。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


GROUPS = [
    ("all_sealed", "all sealed"),
    ("d0_strong_theme", "D0 strong theme"),
    ("d1_same_theme_sync_0945", "D1 peer sync"),
    ("d0_strong_and_d1_sync", "strong + sync"),
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="outputs/one_min_limit_up_pilot/theme_context_202606_202608")
    args = p.parse_args()
    root = Path(args.input)
    data = json.loads((root / "results.json").read_text(encoding="utf-8"))
    events = pd.read_csv(root / "events_with_theme.csv", dtype={"trade_date": str})
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5), layout="constrained")
    ax = axes[0]
    xs = np.arange(len(GROUPS))
    for offset, (period, label, color) in enumerate([
        ("discover_jun_jul", "Jun-Jul", "#64748b"),
        ("validate_aug", "Aug", "#2563eb"),
    ]):
        vals = [data[period]["d1"][name]["mean_net_pct"] for name, _ in GROUPS]
        ns = [data[period]["d1"][name]["exits"] for name, _ in GROUPS]
        pos = xs + (offset - .5) * .32
        bars = ax.bar(pos, vals, width=.29, color=color, label=label, alpha=.9)
        for bar, n, val in zip(bars, ns, vals):
            ax.annotate(f"n={n}", (bar.get_x() + bar.get_width() / 2, val),
                        textcoords="offset points", xytext=(0, 3 if val >= 0 else -13),
                        ha="center", fontsize=8)
    ax.axhline(0, color="#111827", lw=.8)
    ax.set_xticks(xs, [label for _, label in GROUPS], rotation=15)
    ax.set_ylabel("D1 09:46 buy -> D2 09:32 sell, net mean (%)")
    ax.set_title("Theme filters reverse across months")
    ax.grid(axis="y", alpha=.2)
    ax.legend()
    ax = axes[1]
    chosen = events[events.final_sealed & events.d1_theme_strong_sync &
                    events.d1_0946_to_d2_0932_net.notna()].copy()
    by_date = chosen.groupby("trade_date").d1_0946_to_d2_0932_net.agg(["mean", "count"]).reset_index()
    by_date["date"] = pd.to_datetime(by_date.trade_date, format="%Y%m%d")
    colors = np.where(by_date.date < pd.Timestamp("2026-08-01"), "#64748b", "#2563eb")
    ax.scatter(by_date.date, by_date["mean"] * 100, s=25 + by_date["count"] * 3,
               c=colors, alpha=.75, edgecolors="white", linewidth=.5)
    ax.axhline(0, color="#111827", lw=.8)
    ax.axvline(pd.Timestamp("2026-07-30"), color="#dc2626", lw=1, ls="--", label="source regime change")
    ax.axvspan(pd.Timestamp("2026-08-04"), pd.Timestamp("2026-08-07"), color="#fbbf24", alpha=.18,
               label="Aug 4-6 cluster")
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax.tick_params(axis="x", rotation=30)
    ax.set_ylabel("Daily net mean of strong + sync (%)")
    ax.set_title("Each dot is one D0 trading day; size = event count")
    ax.grid(alpha=.2)
    ax.legend(fontsize=8)
    fig.suptitle("1-minute limit-up study with theme context | 2026 Jun-Aug")
    out = root / "theme_conditioned_returns.jpg"
    fig.savefig(out, dpi=170, facecolor="white")
    plt.close(fig)
    print(out)


if __name__ == "__main__":
    main()
