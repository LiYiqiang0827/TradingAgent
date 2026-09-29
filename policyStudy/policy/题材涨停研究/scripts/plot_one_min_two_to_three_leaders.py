"""二进三题材龙头试验的样本漏斗与纸面收益图。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


GROUPS = [
    ("all_exact_two_board", "All 2-board"),
    ("height_leader", "Theme leader"),
    ("leader_prior_strong", "Leader + strong"),
    ("leader_prior20_sync2", "Leader + peer sync"),
    ("leader_prior_strong_sync2", "Leader + both"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="outputs/one_min_limit_up_pilot/two_to_three_leaders_202606_202608")
    args = parser.parse_args()
    root = Path(args.input)
    results = json.loads((root / "results.json").read_text(encoding="utf-8"))
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5), layout="constrained")
    x = np.arange(len(GROUPS))
    for period, label, color, offset in [
        ("describe_jun_jul", "Jun-Jul", "#64748b", -.18),
        ("check_aug_not_blind", "Aug", "#2563eb", .18),
    ]:
        part = results[period]
        mean = [part[key]["mean_net_pct"] for key, _ in GROUPS]
        n = [part[key]["scored"] for key, _ in GROUPS]
        bars = axes[0].bar(x + offset, mean, width=.34, color=color, label=label)
        for bar, count, value in zip(bars, n, mean):
            axes[0].annotate(f"n={count}",
                             (bar.get_x() + bar.get_width() / 2, value),
                             xytext=(0, 3 if value >= 0 else -13), textcoords="offset points",
                             ha="center", fontsize=8)
    axes[0].axhline(0, color="#111827", linewidth=.8)
    axes[0].set_xticks(x, [name for _, name in GROUPS], rotation=16)
    axes[0].set_ylabel("Paper net mean: D0 limit -> D1 09:32 (%)")
    axes[0].set_title("Strong theme and peer sync do not replicate")
    axes[0].grid(axis="y", alpha=.2)
    axes[0].legend()

    labels = ["Jun-Jul", "Aug"]
    leader = [results[p]["height_leader"] for p in ["describe_jun_jul", "check_aug_not_blind"]]
    axes[1].barh(np.arange(2) + .21, [r["candidates"] for r in leader], height=.2,
                 color="#94a3b8", label="D-1 leader candidates")
    axes[1].barh(np.arange(2), [r["observed_touches"] for r in leader], height=.2,
                 color="#2563eb", label="D0 third-board touches")
    axes[1].barh(np.arange(2) - .21, [r["next_minute_below_limit_buyable"] for r in leader],
                 height=.2, color="#ea580c", label="Below-limit next-open proxy")
    for i, row in enumerate(leader):
        for val, ypos in [(row["candidates"], i+.21), (row["observed_touches"], i),
                          (row["next_minute_below_limit_buyable"], i-.21)]:
            axes[1].text(val+2, ypos, str(val), va="center", fontsize=9)
    axes[1].set_yticks(np.arange(2), labels)
    axes[1].invert_yaxis()
    axes[1].set_xlim(0, max(r["candidates"] for r in leader) * 1.2)
    axes[1].set_xlabel("Stock-date observations")
    axes[1].set_title("The genuinely buyable proxy is much smaller")
    axes[1].legend(loc="lower right", fontsize=8)
    axes[1].grid(axis="x", alpha=.2)
    fig.suptitle("A-share 2-to-3 board attempt | KPL theme leaders, 2026 Jun-Aug")
    dest = root / "two_to_three_leaders.jpg"
    fig.savefig(dest, dpi=170, facecolor="white")
    plt.close(fig)
    print(dest)


if __name__ == "__main__":
    main()
