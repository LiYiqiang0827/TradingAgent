"""画出事后挑选的同日弱转强分叉案例；图片仅用于解释，不能当验证样本。"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import numpy as np
import pandas as pd

from coreClient.data_provider import get_oneMin


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", default="outputs/one_min_limit_up_pilot/202606_202608/events.csv")
    p.add_argument("--trade-date", default="20260805")
    p.add_argument("--codes", nargs=2, default=["002636.SZ", "002990.SZ"])
    p.add_argument("--output", default="outputs/one_min_limit_up_pilot/202606_202608/weak_strong_divergence.jpg")
    args = p.parse_args()
    events = pd.read_csv(args.events, dtype={"trade_date": str, "d1": str, "d2": str, "ts_code": str})
    selected = events[(events.trade_date == args.trade_date) & events.ts_code.isin(args.codes)]
    if len(selected) != 2 or not selected.d1_weak_strong_0945.all():
        raise ValueError("案例必须是同一 D0 日期的两只合格弱转强股票")
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True, layout="constrained")
    cn_font = FontProperties(fname="/System/Library/Fonts/PingFang.ttc")
    for ax, row in zip(axes, selected.sort_values("ts_code").itertuples()):
        frames = []
        for day in (row.trade_date, row.d1, row.d2):
            frame = get_oneMin(ts_code=row.ts_code, trade_date=day,
                               columns=["time_idx", "close", "high", "low"])
            if len(frame) != 240:
                raise ValueError(f"分钟数量异常: {row.ts_code} {day} {len(frame)}")
            frames.append(frame.sort_values("time_idx"))
        base = frames[0].iloc[-1]["close"]
        for j, frame in enumerate(frames):
            x = np.arange(240) + 240 * j
            ax.plot(x, (frame["close"].to_numpy() / base - 1) * 100,
                    color=["#6b7280", "#2563eb", "#94a3b8"][j], lw=1.1)
        first = int(row.first_touch_idx)
        ax.scatter([first], [(frames[0].iloc[first]["close"] / base - 1) * 100],
                   color="#dc2626", zorder=5, s=35)
        ax.axvline(240 + 14, color="#f59e0b", lw=1, ls="--", label="D1 09:45 signal")
        ax.axvline(240 + 15, color="#16a34a", lw=1, ls="--", label="D1 09:46 buy proxy")
        ax.axvline(480 + 1, color="#7c3aed", lw=1, ls="--", label="D2 09:32 sell proxy")
        ax.axhline(0, color="#64748b", lw=.6)
        ax.grid(alpha=.18)
        ax.set_ylabel("vs D0 close (%)")
        ax.set_title(f"{row.name} {row.ts_code} | D1 09:31 {row.d1_gap_0931_pct:+.1f}% | "
                     f"D1 09:45 {row.d1_0945_return_pct:+.1f}% | "
                     f"D1 09:46 -> D2 09:32 net {row.d1_0946_to_d2_0932_net*100:+.1f}%",
                     fontproperties=cn_font)
    axes[0].legend(loc="upper left", fontsize=8, ncol=3)
    axes[-1].set_xticks([0, 120, 240, 360, 480, 600, 719],
                       ["D0 09:31", "D0 13:01", "D1 09:31", "D1 13:01",
                        "D2 09:31", "D2 13:01", "D2 15:00"], rotation=15)
    fig.suptitle("Same-day weak-to-strong signals can diverge (post-hoc examples)")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=170, facecolor="white")
    plt.close(fig)
    print(out)


if __name__ == "__main__":
    main()
