"""高板候选可交易性与早盘强度的实证图，读取回放结果生成静态JPG。"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


OUT = Path("outputs/early_high_board_research")


def _date_bootstrap(x: pd.DataFrame, column: str, seed: int = 20260929) -> tuple[float, float]:
    agg = x[x[column].notna()].groupby("d0")[column].agg(["sum", "count"]).to_numpy()
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(agg), size=(5000, len(agg)))
    draw = agg[picks].sum(axis=1)
    return tuple(100 * np.quantile(draw[:, 0] / draw[:, 1], [.025, .975]))


def _plot_r_paths() -> None:
    specs = [
        ("D0 09:36 entry", "early_high_board_r_paths_2025", "early_high_board_r_paths_2026", "r_paths.csv"),
        ("Next morning after confirmed 2nd board", "confirmed_two_board_next_morning_2025",
         "confirmed_two_board_next_morning_2026", "events.csv"),
    ]
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False})
    fig, axs = plt.subplots(2, 1, figsize=(11.8, 8), sharex=True, constrained_layout=True)
    colors = ["#41678d", "#d08745"]
    for ax, (title, first, second, filename) in zip(axs, specs):
        for offset, (label, folder, color) in enumerate(zip(["2025", "2026 Jun–Sep"],
                                                             [first, second], colors)):
            x = pd.read_csv(Path("outputs") / folder / filename, dtype={"d0": str})
            for i, tag in enumerate(["r1", "r2", "r3"]):
                values = x[f"{tag}_net"].dropna()
                mean = 100 * values.mean()
                lo, hi = _date_bootstrap(x, f"{tag}_net")
                xx = i + (-.18 if offset == 0 else .18)
                ax.bar(xx, mean, width=.32, color=color, label=label if i == 0 else None)
                ax.errorbar(xx, mean, yerr=[[mean - lo], [hi - mean]],
                            fmt="none", ecolor="#262d32", capsize=3, linewidth=1)
                ax.text(xx, min(mean, lo) - .18, f"n={len(values):,}",
                        ha="center", va="top", fontsize=8)
        ax.axhline(0, color="#27313c", linewidth=1)
        ax.set_ylabel("Mean net return (%)")
        ax.set_title(title)
        ax.legend(loc="upper right", frameon=False, ncol=2)
        ax.grid(axis="y", alpha=.2)
        ax.set_ylim(-2.9, .55)
    axs[-1].set_xticks([0, 1, 2], ["+3% take / -3% cut", "+6% take / -3% cut",
                                    "+9% take / -3% cut"])
    fig.suptitle("Wider profit targets and later confirmation did not create a positive edge\n"
                 "Bars show mean realized net return; whiskers resample whole trading days",
                 fontsize=13, fontweight="bold")
    fig.savefig(OUT / "risk_reward_and_confirmation.jpg", dpi=180)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    prior = pd.read_csv("outputs/one_min_early_high_board_2025/events.csv")
    current = pd.read_csv("outputs/one_min_early_high_board_2026/events.csv")
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False})
    fig, axs = plt.subplots(2, 1, figsize=(12, 8.5), constrained_layout=True)
    palette = {"sealed": "#b14545", "buyable": "#238c71", "other": "#a3aab2"}
    for i, (label, x) in enumerate([("2025 full year", prior),
                                    ("2026 Jun–Sep", current)]):
        winner = x[x.future_four_board & x.quality_ok]
        sealed = int(winner.sealed_935.sum())
        buyable = int(winner.buyable_936.sum())
        other = len(winner) - sealed - buyable
        left = 0
        for name, value in [("sealed at 09:35", sealed),
                            ("buyable at 09:36", buyable),
                            ("other", other)]:
            share = 100 * value / len(winner)
            axs[0].barh(i, share, left=left, color=palette[name.split()[0]],
                        label=name if i == 0 else None, height=.55)
            if share >= 6:
                axs[0].text(left + share / 2, i, f"{value} ({share:.0f}%)",
                            va="center", ha="center",
                            color="white" if name != "other" else "black", fontweight="bold")
            left += share
    axs[0].set_yticks([0, 1], ["2025 full year (n=322)", "2026 Jun–Sep (n=67)"])
    axs[0].set_xlim(0, 100)
    axs[0].set_ylim(1.6, -1.1)
    axs[0].set_xlabel("Share of stocks that later reached four consecutive limit-up days (%)")
    axs[0].set_title("The profitable-looking winners were often inaccessible by 09:35")
    axs[0].legend(loc="upper right", ncol=3, frameon=False)
    axs[0].grid(axis="x", alpha=.2)

    x = current[current.quality_ok & current.buyable_936 & current.net.notna()].copy()
    edges = [-25, -5, 0, 3, 5, 8, 25]
    labels = ["<-5%", "-5–0%", "0–3%", "3–5%", "5–8%", ">8%"]
    x["early_bin"] = pd.cut(x.close_935_pct, edges, labels=labels,
                            include_lowest=True, right=False)
    agg = x.groupby("early_bin", observed=True).agg(
        n=("ts_code", "size"), mean_net=("net", "mean"),
        future_four_rate=("future_four_board", "mean")).reindex(labels)
    xx = np.arange(len(labels))
    axs[1].bar(xx, 100 * agg.mean_net, color="#41678d", width=.62,
               label="Mean next-day net return")
    axs[1].axhline(0, color="black", linewidth=.8)
    axs[1].set_xticks(xx, labels)
    axs[1].set_ylabel("Mean net return (%)")
    axs[1].set_xlabel("D0 09:35 return from prior close, only 09:36-buyable stocks")
    axs[1].set_title("Early strength alone did not deliver positive expected returns")
    for i, row in enumerate(agg.itertuples()):
        if pd.notna(row.n):
            axs[1].text(i, 100 * row.mean_net + (.12 if row.mean_net >= 0 else -.12),
                        f"n={int(row.n)}", ha="center",
                        va="bottom" if row.mean_net >= 0 else "top", fontsize=8)
    ax2 = axs[1].twinx()
    ax2.plot(xx, 100 * agg.future_four_rate, color="#b14545", marker="o",
             linewidth=2, label="Four-board rate")
    ax2.set_ylabel("Later four-board rate (%)")
    ax2.spines["top"].set_visible(False)
    h1, l1 = axs[1].get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    axs[1].legend(h1 + h2, l1 + l2, loc="upper left", frameon=False)
    axs[1].grid(axis="y", alpha=.2)
    fig.suptitle("First-board candidates: point-in-time feasibility, not hindsight selection",
                 fontsize=14, fontweight="bold")
    fig.savefig(OUT / "early_high_board_funnel_and_returns.jpg", dpi=180)
    plt.close(fig)
    _plot_r_paths()


if __name__ == "__main__":
    main()
