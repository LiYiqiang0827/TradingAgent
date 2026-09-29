"""Draw all fixed MA20 second-wave trades with price, volume and theme breadth.

The plots are retrospective case reviews: bars after the MA20 touch are shown
to evaluate the trade, never to create its signal.
"""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_theme_members


DEFAULT_AUDIT = Path("outputs/ma20_second_wave_202606_202608/theme_cohort_audit.csv")
DEFAULT_STOCK_AUDIT = Path("outputs/ma20_second_wave_202606_202608/candidate_audit.csv")
DEFAULT_OUTPUT = Path("outputs/ma20_second_wave_202606_202608/all_26_trade_charts")
CHINESE_FONT = Path("/System/Library/Fonts/PingFang.ttc")
UP, DOWN = "#c7323e", "#219b61"
MARKS = (
    ("首板", "first_date", "#626c79", ":"),
    ("末板", "peak_date", "#a647ac", ":"),
    ("回踩", "touch_date", "#287cc2", "--"),
    ("买入", "early_entry_date", "#ef8c13", "--"),
    ("卖出", "early_exit_date", "#343945", "--"),
)


def _read_audits(theme_path: Path, stock_path: Path) -> pd.DataFrame:
    date_cols = ["first_date", "peak_date", "touch_date", "outcome_end",
                 "outcome_self_relimit_date"]
    theme = pd.read_csv(theme_path, dtype={name: str for name in date_cols})
    theme = theme[theme.status.eq("ok")].copy()
    stock = pd.read_csv(stock_path, dtype={name: str for name in
                        ("first_date", "peak_date", "touch_date",
                         "early_entry_date", "early_exit_date")})
    stock = stock[stock.early_trade_status.eq("closed")]
    keep = ["ts_code", "first_date", "peak_date", "touch_date", "peak_height",
            "early_entry_date", "early_exit_date", "early_entry_price_qfq",
            "early_exit_price_qfq", "early_net_return_pct", "early_exit_reason"]
    cases = theme.merge(stock[keep], on=["ts_code", "first_date", "peak_date",
                                         "touch_date"], how="inner",
                        suffixes=("", "_stock"), validate="one_to_one")
    if len(cases) != len(theme) or cases.ts_code.duplicated().any():
        raise ValueError(f"Expected one trade for every theme case: {len(cases)}/{len(theme)}")
    if not cases[["early_entry_date", "early_exit_date"]].notna().all().all():
        raise ValueError("An included trade has no buy or sell date")
    check = 100 * (cases.early_exit_price_qfq / cases.early_entry_price_qfq - 1 - .004)
    if (check - cases.early_net_return_pct).abs().max() > 1e-8:
        raise ValueError("Stored trade return does not match prices and 0.4% cost")
    return cases.sort_values(["touch_date", "ts_code"]).reset_index(drop=True)


def _stock_frames(cases: pd.DataFrame, data_end: str) -> dict[str, pd.DataFrame]:
    codes = cases.ts_code.tolist()
    qfq = get_day(ts_codes=codes, start_date="20260301", end_date=data_end,
                  qfq=True, source="database_only")
    raw = get_day(ts_codes=codes, start_date="20260301", end_date=data_end,
                  qfq=False, source="database_only")
    raw = raw[["ts_code", "trade_date", "vol"]].rename(columns={"vol": "raw_vol"})
    data = qfq.drop(columns="vol").merge(raw, on=["ts_code", "trade_date"],
                                         validate="one_to_one")
    frames = {}
    for code, part in data.groupby("ts_code"):
        part = part.sort_values("trade_date").reset_index(drop=True)
        part.trade_date = part.trade_date.astype(str)
        for col in ("open", "high", "low", "close", "raw_vol"):
            part[col] = pd.to_numeric(part[col], errors="coerce")
        for window in (5, 10, 20, 30):
            part[f"ma{window}"] = part.close.rolling(window).mean()
        part["vol_ma20"] = part.raw_vol.rolling(20).mean()
        frames[code] = part
    missing = set(codes) - set(frames)
    if missing:
        raise ValueError(f"No daily data for {sorted(missing)}")
    return frames


def _view_for_case(case: pd.Series, frame: pd.DataFrame) -> pd.DataFrame:
    pos = {date: i for i, date in enumerate(frame.trade_date)}
    required = (case.first_date, case.peak_date, case.touch_date,
                case.early_entry_date, case.early_exit_date)
    missing = [date for date in required if date not in pos]
    if missing:
        raise ValueError(f"{case.ts_code}: required daily bars missing: {missing}")
    left = max(0, pos[case.first_date] - 25)
    right_end = max(pos[case.early_exit_date], pos.get(case.outcome_end, 0))
    right = min(len(frame), right_end + 6)
    return frame.iloc[left:right].reset_index(drop=True)


def _breadth(case: pd.Series, dates: pd.Series) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    members = get_theme_members(case.theme_id, as_of=str(dates.iloc[-1]), historical=True)
    members.trade_date = members.trade_date.astype(str)
    members = members[members.trade_date.isin(dates)]
    by_day = {date: set(part.ts_code) for date, part in members.groupby("trade_date")}
    old = set(str(case.touch_frozen_old_peer_codes).split(",")) \
        if pd.notna(case.touch_frozen_old_peer_codes) else set()
    old -= {"", "nan", case.ts_code}
    old_count, other_count, anchor_count = [], [], []
    for date in dates:
        codes = by_day.get(date, set())
        old_count.append(len(codes & old))
        other_count.append(len(codes - old - {case.ts_code}))
        anchor_count.append(int(case.ts_code in codes))
    return np.array(old_count), np.array(other_count), np.array(anchor_count)


def draw_case(case: pd.Series, frame: pd.DataFrame, output: Path) -> dict:
    view = _view_for_case(case, frame)
    old, other, anchor = _breadth(case, view.trade_date)
    position = {date: i for i, date in enumerate(view.trade_date)}
    x = np.arange(len(view))
    fig, (price, volume, breadth) = plt.subplots(
        3, 1, figsize=(16, 9.5), sharex=True,
        gridspec_kw={"height_ratios": [3.2, 1.0, 1.15], "hspace": .08})
    candle_colors = []
    for i, day in view.iterrows():
        color = UP if day.close >= day.open else DOWN
        candle_colors.append(color)
        price.vlines(i, day.low, day.high, color=color, lw=.85)
        price.add_patch(Rectangle(
            (i - .3, min(day.open, day.close)), .6,
            max(abs(day.close - day.open), .005),
            facecolor=color, edgecolor=color, lw=.4))
    for window, color, width in ((5, "#9668a7", .9), (10, "#5c8cb7", 1.0),
                                 (20, "#d89112", 1.65), (30, "#526674", 1.25)):
        price.plot(x, view[f"ma{window}"], color=color, lw=width,
                   label=f"MA{window}")
    volume.bar(x, view.raw_vol, color=candle_colors, alpha=.48, width=.7)
    volume.plot(x, view.vol_ma20, color="#68717c", lw=1.15, label="20日均量")
    breadth.bar(x, old, color="#2d72b3", width=.76, label="首轮冻结成员")
    breadth.bar(x, other, bottom=old, color="#a8b8c8", width=.76,
                label="非冻结成员")
    breadth.bar(x, anchor, bottom=old + other, color=UP, width=.76,
                label="本股")

    start = position[case.early_entry_date]
    end = position[case.early_exit_date]
    for axis in (price, volume, breadth):
        axis.axvspan(start - .5, end + .5,
                     color="#c5dfc9" if case.early_net_return_pct > 0 else "#edcbd0",
                     alpha=.18, zorder=0)
    for label, key, color, style in MARKS:
        at = position.get(getattr(case, key))
        if at is None:
            continue
        for axis in (price, volume, breadth):
            axis.axvline(at, color=color, ls=style, lw=.9, alpha=.77)
    price.scatter([start], [case.early_entry_price_qfq], marker="^", s=110,
                  color="#ef8c13", edgecolor="white", linewidth=.6, zorder=6)
    price.scatter([end], [case.early_exit_price_qfq], marker="v", s=110,
                  color="#343945", edgecolor="white", linewidth=.6, zorder=6)
    relimit = case.outcome_self_relimit_date
    if isinstance(relimit, str) and relimit in position:
        at = position[relimit]
        price.scatter([at], [view.iloc[at].high], marker="*", s=125,
                      color="#bd4085", edgecolor="white", linewidth=.5, zorder=6)

    font = FontProperties(fname=str(CHINESE_FONT)) if CHINESE_FONT.exists() else None
    headline = (f"{case['name']} {case.ts_code}  ·  {case.peak_primary}  ·  "
                f"{int(case.peak_height)}板后MA20回踩  ·  "
                f"净收益 {case.early_net_return_pct:+.2f}%")
    fig.suptitle(headline, fontsize=15, fontproperties=font, y=.98)
    dates_line = "  |  ".join(f"{label} {str(getattr(case, key))[4:]}"
                             for label, key, _, _ in MARKS)
    details = (f"{dates_line}     首轮题材峰值宽度 {int(case.touch_wave_theme_peak_width)}"
               f"  ·  回踩日宽度 {int(case.touch_theme_width)}"
               f"（老成员 {int(case.touch_old_peer_limit)}）")
    fig.text(.5, .943, details, ha="center", va="center", fontsize=9.3,
             fontproperties=font, color="#424b56")

    volume.legend(loc="upper left", fontsize=8, prop=font)
    breadth.legend(loc="upper left", ncol=3, fontsize=8, prop=font)
    price.set_ylabel("前复权价格", fontproperties=font)
    volume.set_ylabel("原始成交量", fontproperties=font)
    breadth.set_ylabel("题材涨停数", fontproperties=font)
    for axis in (price, volume, breadth):
        axis.grid(axis="y", alpha=.16)
        axis.set_xlim(-.7, len(view)-.3)
    volume.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
    step = max(1, len(view) // 12)
    ticks = sorted(set(list(range(0, len(view), step)) + [len(view)-1]))
    breadth.set_xticks(ticks, [view.iloc[i].trade_date[4:] for i in ticks],
                       rotation=40, ha="right")
    legend = [Line2D([0], [0], color=color, ls=style, lw=1.2, label=label)
              for label, _, color, style in MARKS]
    price.legend(handles=price.get_legend_handles_labels()[0] + legend,
                 loc="upper left", ncol=5, fontsize=7.7, prop=font)
    fig.text(.5, .013, "事后回放图：回踩日之后的价格和题材涨停只用于验证，不用于形成当时的信号。",
             ha="center", fontsize=8, color="#7b8490", fontproperties=font)
    fig.subplots_adjust(left=.07, right=.985, top=.91, bottom=.105)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=145, facecolor="white")
    plt.close(fig)
    return {"chart": output.name, "plot_first_date": view.iloc[0].trade_date,
            "plot_last_date": view.iloc[-1].trade_date,
            "chart_theme_width_at_touch": int(old[position[case.touch_date]]
                                              + other[position[case.touch_date]]
                                              + anchor[position[case.touch_date]])}


def _write_index(manifest: pd.DataFrame, output: Path) -> None:
    cards = []
    for case in manifest.itertuples(index=False):
        color = "gain" if case.early_net_return_pct > 0 else "loss"
        cards.append(
            f'<article class="card"><a href="{escape(case.chart)}">'
            f'<img loading="lazy" src="{escape(case.chart)}" '
            f'alt="{escape(case.name)} {escape(case.ts_code)} K线和题材宽度"></a>'
            f'<div class="caption"><strong>{escape(case.name)} {escape(case.ts_code)}</strong>'
            f'<span>{escape(case.peak_primary)} · 回踩 {escape(case.touch_date)}</span>'
            f'<b class="{color}">{case.early_net_return_pct:+.2f}%</b></div></article>')
    page = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>26笔MA20二波候选交易复盘</title><style>
body{font:15px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#f3f5f7;
color:#222;margin:0;padding:24px}h1{margin:0 0 8px}.hint{color:#637080;margin:0 0 22px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:18px}
.card{background:#fff;border:1px solid #dfe4e8;border-radius:10px;overflow:hidden;
box-shadow:0 2px 8px #d8dde355}.card img{width:100%;display:block}.caption{display:flex;
gap:8px;align-items:baseline;padding:10px 13px;flex-wrap:wrap}.caption span{color:#67717c}
.caption b{margin-left:auto}.gain{color:#c7323e}.loss{color:#219b61}
</style></head><body><h1>26笔MA20二波候选交易复盘</h1>
<p class="hint">按回踩日期排序。每张图含日K、均线、原始成交量、题材涨停宽度及模拟买卖点。点击查看原图；未来走势仅供事后核对。</p>
<main class="grid">""" + "\n".join(cards) + "</main></body></html>"
    (output / "index.html").write_text(page, encoding="utf-8")


def _write_overview(cases: pd.DataFrame, frames: dict[str, pd.DataFrame],
                    output: Path) -> None:
    """A small-multiple scan; detailed candlesticks remain in individual JPGs."""
    columns = 4
    rows = int(np.ceil(len(cases) / columns))
    fig, axes = plt.subplots(rows, columns, figsize=(18, rows * 3.4))
    axes = np.atleast_1d(axes).ravel()
    font = FontProperties(fname=str(CHINESE_FONT)) if CHINESE_FONT.exists() else None
    for axis, case in zip(axes, cases.itertuples(index=False)):
        view = _view_for_case(pd.Series(case._asdict()), frames[case.ts_code])
        x = np.arange(len(view))
        axis.plot(x, view.close, color="#26313b", lw=1.2)
        axis.plot(x, view.ma20, color="#d89112", lw=1.05)
        positions = {date: i for i, date in enumerate(view.trade_date)}
        touch = positions[case.touch_date]
        buy = positions[case.early_entry_date]
        sell = positions[case.early_exit_date]
        axis.axvline(touch, color="#287cc2", ls="--", lw=.8)
        axis.axvspan(buy-.5, sell+.5,
                     color="#c5dfc9" if case.early_net_return_pct > 0 else "#edcbd0",
                     alpha=.28)
        axis.scatter([buy], [case.early_entry_price_qfq], marker="^", s=28,
                     color="#ef8c13", zorder=4)
        axis.scatter([sell], [case.early_exit_price_qfq], marker="v", s=28,
                     color="#343945", zorder=4)
        axis.set_title(f"{case.name} {case.ts_code} · "
                       f"{case.early_net_return_pct:+.1f}%",
                       fontsize=10, fontproperties=font,
                       color=UP if case.early_net_return_pct > 0 else DOWN)
        ticks = [0, touch, len(view)-1]
        axis.set_xticks(ticks, [view.iloc[i].trade_date[4:] for i in ticks],
                        fontsize=7)
        axis.tick_params(axis="y", labelsize=7)
        axis.grid(alpha=.15)
    for axis in axes[len(cases):]:
        axis.axis("off")
    fig.suptitle("26笔MA20回踩交易总览 · 黑线股价 / 黄线MA20 / 蓝线回踩 / 橙三角买入 / 黑三角卖出",
                 fontproperties=font, fontsize=16, y=.995)
    fig.tight_layout(rect=(0, 0, 1, .984))
    fig.savefig(output / "overview_26.jpg", dpi=130, facecolor="white")
    plt.close(fig)


def run(theme_audit: Path, stock_audit: Path, output: Path,
        *, data_end: str = "20260928", codes: list[str] | None = None) -> dict:
    cases = _read_audits(theme_audit, stock_audit)
    if codes is not None:
        unknown = set(codes) - set(cases.ts_code)
        if unknown:
            raise ValueError(f"Unknown or incomplete case codes: {sorted(unknown)}")
        cases = cases[cases.ts_code.isin(codes)].copy()
    frames = _stock_frames(cases, data_end)
    records = []
    for case in cases.itertuples(index=False):
        item = pd.Series(case._asdict())
        path = output / f"{case.touch_date}_{case.ts_code.replace('.', '_')}.jpg"
        audit = draw_case(item, frames[case.ts_code], path)
        records.append({**{key: getattr(case, key) for key in
                           ("ts_code", "name", "peak_primary", "peak_height",
                            "first_date", "peak_date", "touch_date",
                            "early_entry_date", "early_exit_date",
                            "early_net_return_pct", "touch_wave_theme_peak_width",
                            "touch_theme_width", "touch_old_peer_limit")}, **audit})
        print(path)
    manifest = pd.DataFrame(records)
    output.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(output / "manifest.csv", index=False, encoding="utf-8-sig")
    _write_index(manifest, output)
    _write_overview(cases, frames, output)
    if (manifest.chart_theme_width_at_touch != manifest.touch_theme_width).any():
        raise AssertionError("Chart theme width and source audit disagree")
    return {"cases": len(cases), "charts": len(records),
            "theme_width_mismatches": int((manifest.chart_theme_width_at_touch
                                           != manifest.touch_theme_width).sum()),
            "output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme-audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--stock-audit", type=Path, default=DEFAULT_STOCK_AUDIT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--data-end", default="20260928")
    parser.add_argument("--codes", nargs="+", help="Only render these case codes")
    args = parser.parse_args()
    print(run(args.theme_audit, args.stock_audit, args.output_dir,
              data_end=args.data_end, codes=args.codes))


if __name__ == "__main__":
    main()
