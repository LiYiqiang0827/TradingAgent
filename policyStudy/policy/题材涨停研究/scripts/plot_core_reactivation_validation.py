"""Visual summary of frozen-entry causal and minute-execution research runs."""
from pathlib import Path
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "outputs/early_high_board_research"
COLORS = {"old": "#296b9f", "carry": "#d07927"}
NAMES = {"old": "Multi-day exit", "carry": "Carry D0 invalidation"}


def interval(frame, column):
    scored = frame[frame[column].notna()]
    dates = pd.to_datetime(scored.signal_time).dt.strftime("%Y%m%d")
    blocks = pd.DataFrame({"date": dates, "ret": scored[column]}).groupby("date").ret.agg(["sum", "count"]).to_numpy()
    rng = np.random.default_rng(20260929)
    sampled = blocks[rng.integers(0, len(blocks), (5000, len(blocks)))].sum(axis=1)
    return np.quantile(sampled[:, 0] / sampled[:, 1] * 100, [.025, .975])


def main():
    sources = [ROOT / f"outputs/core_reactivation_causal_{year}/trades.csv" for year in (2025, 2026)]
    sources.append(ROOT / "outputs/core_reactivation_minute_execution_2026/trades.csv")
    frames = [pd.read_csv(p) for p in sources]
    fig, axs = plt.subplots(2, 2, figsize=(14, 10), layout="constrained")
    fig.suptitle("Core-theme pullback / second-start: execution and failure audit", fontsize=18, weight="bold")
    ax = axs[0, 0]
    labels = ["2025 | 15m fills", "2026 Jan-Sep | 15m fills", "2026 Jun-Sep | 1m fills"]
    for m, mode in enumerate(("old", "carry")):
        for i, frame in enumerate(frames):
            col = f"{'minute_' if i == 2 else ''}{mode}_net"
            mean = frame[col].mean() * 100
            ci = interval(frame, col)
            y = i + (m - .5) * .23
            ax.errorbar(mean, y, xerr=[[mean-ci[0]], [ci[1]-mean]], fmt="o", capsize=4,
                        color=COLORS[mode], label=NAMES[mode] if i == 0 else None)
            ax.text(mean, y-.09, f"{mean:+.2f}%  n={frame[col].count()}", fontsize=9, ha="center")
    ax.set(yticks=range(3), yticklabels=labels, xlabel="Mean net return per filled trade (%)",
           title="A | Means and signal-date block bootstrap 95% intervals", ylim=(2.45, -.5))
    ax.axvline(0, color="black", lw=.8)
    ax.legend(loc="lower left", fontsize=9)

    late = frames[2]
    ax = axs[0, 1]
    for mode in ("old", "carry"):
        values = np.sort(late[f"minute_{mode}_net"].dropna().to_numpy() * 100)
        ax.plot(values, np.arange(1, len(values)+1)/len(values)*100, color=COLORS[mode], label=NAMES[mode])
    ax.axvline(0, color="black", lw=.8)
    ax.set(title="B | Late-period 1m execution: return distribution", xlabel="Net return per filled trade (%)", ylabel="Empirical cumulative share (%)")
    ax.legend(fontsize=9)

    ax = axs[1, 0]
    monthly = late.groupby(late.signal_time.str[:7])
    for j, mode in enumerate(("old", "carry")):
        values = monthly[f"minute_{mode}_net"].mean() * 100
        counts = monthly[f"minute_{mode}_net"].count()
        x = np.arange(len(values)) + (j-.5)*.32
        ax.bar(x, values, width=.3, color=COLORS[mode], label=NAMES[mode])
        for xx, v, n in zip(x, values, counts):
            ax.text(xx, v + (.12 if v >= 0 else -.15), f"{v:+.2f}%\nn={n}", ha="center", va="bottom" if v >= 0 else "top", fontsize=9)
    ax.set(xticks=range(len(values)), xticklabels=values.index, ylabel="Mean net return (%)",
           title="C | Monthly consistency: same rules and execution", ylim=(-3.8, 4.3))
    ax.axhline(0, color="black", lw=.8)
    ax.legend(fontsize=9)

    ax = axs[1, 1]
    ax.axis("off")
    audit = json.loads((sources[2].parent / "audit.json").read_text())
    lines = ["D | What these results do and do not establish", "",
             f"Late-period denominator: {len(late)} signals, {late.minute_old_net.count()} filled & closed,",
             f"{late.minute_old_status.eq('entry_at_up_limit').sum()} entry attempts rejected at upper limit.", "",
             "15m decisions -> at least 60-second reaction -> 1m open.",
             "T+1 enforced; blocked sells remain pending.",
             "Fixed round-trip costs approximately 0.31%.", "",
             f"Minute partitions: {audit['source']['mixed_partition_count']} / {audit['source']['dates']} dates have CSV fallback.",
             "Per-stock-day TDX provenance is not fully recoverable.",
             "OHLC checks do not establish queue fills or capacity.", "",
             "2026 has been explored repeatedly; no untouched holdout.",
             "Per-trade results are not portfolio compound returns.",
             "No stable positive expectancy established."]
    ax.text(0, 1, "\n".join(lines), va="top", fontsize=11, linespacing=1.5,
            bbox=dict(boxstyle="round,pad=.9", facecolor="#f1f4f7", edgecolor="none"))
    for a in axs.flat[:3]:
        a.grid(alpha=.15)
        a.spines[["top", "right"]].set_visible(False)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "core_reactivation_validation.jpg"
    fig.savefig(target, dpi=170, facecolor="white")
    plt.close(fig)
    print(target)


if __name__ == "__main__":
    main()
