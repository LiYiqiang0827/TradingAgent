"""Show all six strict 2025 training matches; descriptive, never signal inputs."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from coreClient.data_provider import get_fifteenMin


ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "outputs/core_pullback_structure_2025"


def main():
    selected = pd.read_csv(OUT / "scored.csv")
    selected = selected[selected.selected_A_B_C.eq(True)].sort_values("signal_time")
    assert len(selected) == 6, "This layout documents the frozen six-case experiment"
    first = (pd.to_datetime(selected.signal_time).min()-pd.Timedelta(days=12)).strftime("%Y%m%d")
    last = pd.to_datetime(selected.old_exit_bar_end).max().strftime("%Y%m%d")
    bars = get_fifteenMin(ts_codes=selected.ts_code.tolist(), start_date=first, end_date=last)
    bars.datetime = pd.to_datetime(bars.datetime)
    fig, axs = plt.subplots(3, 2, figsize=(16, 14), layout="constrained")
    fig.suptitle("2025 | All six frozen A+B+C matches: known structure and subsequent path\n"
                 "15-minute raw OHLC; blue area is AFTER decision; execution remains a bar-open proxy", fontsize=16)
    for ax, (_, row) in zip(axs.flat, selected.iterrows()):
        signal = pd.Timestamp(row.signal_time)
        latest = max(pd.Timestamp(row.old_exit_bar_end), pd.Timestamp(row.carry_exit_bar_end))
        stock = bars[bars.ts_code.eq(row.ts_code)].sort_values("datetime")
        stock = pd.concat([stock[stock.datetime.le(signal)].tail(48), stock[stock.datetime.gt(signal) & stock.datetime.le(latest)]]).reset_index(drop=True)
        x = np.arange(len(stock))
        decision_index = stock.index[stock.datetime.eq(signal)][0]
        ax.axvspan(decision_index+.4, len(stock)-.4, color="#d7e8f4", alpha=.5)
        for i, bar in stock.iterrows():
            color = "#cb4947" if bar.close >= bar.open else "#318260"
            ax.vlines(i, bar.low, bar.high, color=color, lw=.65)
            ax.add_patch(Rectangle((i-.32, min(bar.open, bar.close)), .64,
                                   max(abs(bar.close-bar.open), .003), color=color, lw=.4))
        ax.axvline(decision_index, color="#31546f", ls="--", lw=1)
        ax.axhline(row.pressure, color="#a36b23", ls="--", lw=1, label="Prior 3-day high (reference)")
        ax.axhline(row.reference_stop, color="#6d7982", ls=":", lw=1, label="Confirmed low x 0.995 (reference)")
        ei = stock.index[stock.datetime.eq(pd.Timestamp(row.old_entry_bar_end))][0]
        ax.scatter(ei-.4, row.old_entry_open, color="#151515", marker="^", s=70, zorder=4)
        for mode, marker, color in (("old", "v", "#234cb7"), ("carry", "o", "#dc861d")):
            end = pd.Timestamp(row[f"{mode}_exit_bar_end"])
            ix = stock.index[stock.datetime.eq(end)][0]
            fill = float(stock.loc[ix, "open"])
            ax.scatter(ix-.4, fill, marker=marker, color=color, s=60 if mode=="old" else 30, zorder=5)
        ticks = stock.groupby(stock.datetime.dt.strftime("%m-%d"), sort=False).head(1).index.tolist()
        if len(ticks)>1 and ticks[1]-ticks[0]<8:
            ticks = ticks[1:]
        if len(ticks)>7: ticks = ticks[::2]
        ax.set(xticks=ticks, xticklabels=stock.loc[ticks, "datetime"].dt.strftime("%m-%d"), ylabel="Raw price (CNY)",
               title=f"{row.ts_code} | {signal:%Y-%m-%d %H:%M}\nold {row.old_net*100:+.2f}% / carry {row.carry_net*100:+.2f}% | reference space/risk {row.space_risk_ratio:.2f}")
        ax.tick_params(axis="x", labelrotation=25)
        ax.grid(alpha=.13)
        ax.spines[["top", "right"]].set_visible(False)
        if ax is axs[0, 0]: ax.legend(loc="upper right", fontsize=8)
    fig.supxlabel("Black triangle: entry | Blue triangle: old exit | Orange dot: carry exit | No sample omitted", fontsize=11)
    target = OUT / "all_six_structure_cases.jpg"
    fig.savefig(target, dpi=160, facecolor="white")
    print(target)


if __name__ == "__main__": main()
