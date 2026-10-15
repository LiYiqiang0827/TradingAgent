"""Plot reproducible daily candlestick audits for the MA20 second-wave pilot."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle
import pandas as pd

from coreClient.data_provider import get_day


DEFAULT_CODES = ("603155.SH", "002724.SZ", "002608.SZ", "002953.SZ")
CHINESE_FONT = Path("/System/Library/Fonts/PingFang.ttc")


def plot_case(row: pd.Series, output: Path) -> None:
    code = row.ts_code
    qfq = get_day(ts_code=code, start_date="20260301", end_date="20260928",
                  qfq=True, source="database_only").sort_values("trade_date")
    raw = get_day(ts_code=code, start_date="20260301", end_date="20260928",
                  qfq=False, source="database_only")[["trade_date", "vol"]]
    data = qfq.drop(columns="vol").merge(raw, on="trade_date", validate="one_to_one")
    data.trade_date = data.trade_date.astype(str)
    data = data.reset_index(drop=True)
    for period in (20, 30):
        data[f"ma{period}"] = data.close.rolling(period).mean()
    positions = {date: i for i, date in enumerate(data.trade_date)}
    left = max(0, positions[row.first_date] - 30)
    right = min(len(data), positions[row.touch_date] + 26)
    view = data.iloc[left:right].reset_index(drop=True)

    fig, (price, volume) = plt.subplots(2, 1, figsize=(15, 7), sharex=True,
                                      gridspec_kw={"height_ratios": [3, 1]})
    for i, bar in view.iterrows():
        color = "#c52a30" if bar.close >= bar.open else "#259e57"
        price.vlines(i, bar.low, bar.high, color=color, lw=.8)
        price.add_patch(Rectangle((i - .29, min(bar.open, bar.close)), .58,
                                  max(abs(bar.close - bar.open), .005),
                                  facecolor=color, edgecolor=color, alpha=.9))
        volume.bar(i, bar.vol, color=color, alpha=.45, width=.65)
    price.plot(view.index, view.ma20, color="#e49b17", lw=1.6, label="MA20")
    price.plot(view.index, view.ma30, color="#4062a0", lw=1.3, label="MA30")
    marks = (("first", row.first_date, "#555"),
             ("peak", row.peak_date, "#bd40bd"),
             ("touch", row.touch_date, "#1f77b4"),
             ("buy", row.early_entry_date, "#ff7f0e"),
             ("sell", row.early_exit_date, "#222"),
             ("re-rise", row.signal_date, "#c51b7d"))
    for label, date, color in marks:
        if pd.isna(date):
            continue
        points = view.index[view.trade_date.eq(date)]
        if len(points):
            x = int(points[0])
            price.axvline(x, color=color, ls="--", lw=.9, alpha=.7)
            price.text(x, price.get_ylim()[1], label, rotation=90, va="top",
                       ha="right", fontsize=7, color=color)
    ticks = list(range(0, len(view), max(1, len(view) // 9)))
    volume.set_xticks(ticks, [view.iloc[i].trade_date[4:] for i in ticks], rotation=45)
    title = f"{code} {row['name']} | MA20 touch {row.touch_date} | early net {row.early_net_return_pct:+.1f}%"
    font = FontProperties(fname=str(CHINESE_FONT)) if CHINESE_FONT.exists() else None
    price.set_title(title, fontproperties=font)
    price.set_ylabel("QFQ price")
    volume.set_ylabel("Raw volume")
    price.grid(alpha=.15)
    price.legend(loc="upper left")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path,
                        default=Path("outputs/ma20_second_wave_202606_202608/candidate_audit.csv"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/ma20_second_wave_202606_202608/case_charts"))
    parser.add_argument("--codes", nargs="+", default=DEFAULT_CODES)
    args = parser.parse_args()
    audit = pd.read_csv(args.audit, dtype={col: str for col in
                        ("first_date", "peak_date", "touch_date", "signal_date",
                         "early_entry_date", "early_exit_date")})
    for code in args.codes:
        matches = audit[(audit.ts_code == code) & audit.touch_date.notna()
                        & audit.early_entry_date.notna()]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one in-window case for {code}: {len(matches)}")
        output = args.output_dir / f"{code.replace('.', '_')}.jpg"
        plot_case(matches.iloc[0], output)
        print(output)


if __name__ == "__main__":
    main()
