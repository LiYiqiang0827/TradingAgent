"""Retrospective price and primary-theme breadth charts for MA20 cases."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_theme_members


DEFAULT_CODES = ("603155.SH", "000735.SZ", "002953.SZ", "002724.SZ")
CHINESE_FONT = Path("/System/Library/Fonts/PingFang.ttc")


def plot_case(row: pd.Series, output: Path) -> None:
    code = row.ts_code
    first, peak, touch, end = (row.first_date, row.peak_date,
                               row.touch_date, row.outcome_end)
    daily = get_day(ts_code=code, start_date="20260401", end_date=end,
                    qfq=True, source="database_only").sort_values("trade_date")
    daily.trade_date = daily.trade_date.astype(str)
    daily = daily.reset_index(drop=True)
    daily["ma20"] = daily.close.rolling(20).mean()
    pos = {date: i for i, date in enumerate(daily.trade_date)}
    start_i = max(0, pos[first] - 12)
    view = daily.iloc[start_i:].reset_index(drop=True)
    members = get_theme_members(row.theme_id, as_of=end, historical=True)
    members.trade_date = members.trade_date.astype(str)
    members = members[members.trade_date.isin(view.trade_date)]
    old = set(str(row.touch_frozen_old_peer_codes).split(",")) \
        if pd.notna(row.touch_frozen_old_peer_codes) else set()
    old -= {"", "nan", code}
    by_day = {day: set(part.ts_code) for day, part in members.groupby("trade_date")}
    old_counts = []
    new_counts = []
    anchor_counts = []
    for date in view.trade_date:
        codes = by_day.get(date, set())
        old_counts.append(len(codes & old))
        new_counts.append(len(codes - old - {code}))
        anchor_counts.append(int(code in codes))
    x = np.arange(len(view))
    fig, (price, breadth) = plt.subplots(2, 1, figsize=(14, 7), sharex=True,
                                        gridspec_kw={"height_ratios": [2, 1]})
    price.plot(x, view.close, color="#20252b", lw=1.6, label="QFQ close")
    price.plot(x, view.ma20, color="#e49b17", lw=1.4, label="MA20")
    breadth.bar(x, old_counts, color="#286bb4", alpha=.8,
                label="Frozen first-wave peers")
    breadth.bar(x, new_counts, bottom=old_counts, color="#91a7bd", alpha=.65,
                label="Other primary-theme limit-ups")
    breadth.bar(x, anchor_counts,
                bottom=np.array(old_counts) + np.array(new_counts),
                color="#c52a30", label="Anchor stock")
    marks = (("first", first, "#777"), ("peak", peak, "#9b59b6"),
             ("touch", touch, "#1f77b4"),
             ("stock re-limit", row.outcome_self_relimit_date, "#c52a30"))
    for label, date, color in marks:
        if not isinstance(date, str) or not date:
            continue
        found = view.index[view.trade_date.eq(date)]
        if len(found):
            at = int(found[0])
            price.axvline(at, color=color, ls="--", lw=.9, alpha=.8)
            breadth.axvline(at, color=color, ls="--", lw=.9, alpha=.5)
            price.text(at, price.get_ylim()[1], label, color=color,
                       rotation=90, ha="right", va="top", fontsize=8)
    font = FontProperties(fname=str(CHINESE_FONT)) if CHINESE_FONT.exists() else None
    price.set_title(f"{code} {row['name']} | first-wave theme: {row.peak_primary} | touch {touch}",
                    fontproperties=font)
    price.set_ylabel("QFQ price")
    breadth.set_ylabel("Limit-up stocks")
    ticks = list(range(0, len(view), max(1, len(view)//9)))
    breadth.set_xticks(ticks, [view.iloc[i].trade_date[4:] for i in ticks], rotation=45)
    price.legend(loc="upper left")
    breadth.legend(loc="upper left", fontsize=8)
    price.grid(alpha=.15)
    breadth.grid(axis="y", alpha=.15)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path,
                        default=Path("outputs/ma20_second_wave_202606_202608/theme_cohort_audit.csv"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/ma20_second_wave_202606_202608/theme_charts"))
    parser.add_argument("--codes", nargs="+", default=DEFAULT_CODES)
    args = parser.parse_args()
    audit = pd.read_csv(args.audit, dtype={col: str for col in
                        ("first_date", "peak_date", "touch_date", "outcome_end",
                         "outcome_self_relimit_date")})
    for code in args.codes:
        rows = audit[audit.ts_code == code]
        if len(rows) != 1:
            raise ValueError(f"Expected one case for {code}: {len(rows)}")
        path = args.output_dir / f"{code.replace('.', '_')}_theme.jpg"
        plot_case(rows.iloc[0], path)
        print(path)


if __name__ == "__main__":
    main()
