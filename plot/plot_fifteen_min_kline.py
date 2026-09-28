#!/usr/bin/env python3
"""从15分钟DuckDB绘制A股15分钟K线、成交量和移动均线。"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path
import sys
from typing import Sequence

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAVE_DIR = PROJECT_ROOT / "plot" / "tmp_savepic"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from coreClient.data_provider import get_fifteenMin  # noqa: E402
from plot.plot_daily_kline import (  # noqa: E402
    MA_COLORS,
    _configure_chinese_font,
    _draw_candles,
    lookup_stock_name,
    normalize_ts_code,
    parse_date,
)


DEFAULT_MA_WINDOWS = (5, 10, 20, 30, 60, 120, 250)
BARS_PER_DAY = 16
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_LOOKAHEAD_DAYS = 7


@dataclass(frozen=True)
class MinutePlotWindow:
    selected_start: pd.Timestamp
    selected_end: pd.Timestamp
    display_start: pd.Timestamp
    display_end: pd.Timestamp
    fetch_start: pd.Timestamp
    marked_dates: tuple[pd.Timestamp, ...]
    selection_mode: str


def _flatten_trade_dates(values: Sequence[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in str(value).split(",") if part.strip())
    return result


def resolve_window(
    *,
    start_date: str | None,
    end_date: str | None,
    trade_date: str | None,
    trade_dates: Sequence[str] | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    ma_warmup_bars: int = 250,
    to_latest: bool = False,
    today: date | pd.Timestamp | None = None,
) -> MinutePlotWindow:
    """计算标记区间、展示区间以及均线预热读取区间。"""

    if lookback_days < 0 or lookahead_days < 0 or ma_warmup_bars < 0:
        raise ValueError("前后扩展天数和均线预热根数都不能为负数")
    range_mode = start_date is not None or end_date is not None
    multi_values = _flatten_trade_dates(trade_dates)
    active_modes = int(range_mode) + int(trade_date is not None) + int(bool(multi_values))
    if active_modes > 1:
        raise ValueError(
            "--trade-date、--trade-dates 和 --start-date/--end-date 三种模式互斥"
        )
    if range_mode and not (start_date and end_date):
        raise ValueError("--start-date 和 --end-date 必须同时提供")
    if active_modes == 0:
        raise ValueError(
            "必须传 --trade-date、--trade-dates，或同时传 --start-date 和 --end-date"
        )

    if trade_date is not None:
        selected_start = selected_end = parse_date(trade_date)
        marked_dates = (selected_start,)
        selection_mode = "single"
    elif multi_values:
        marked_dates = tuple(sorted(set(parse_date(value) for value in multi_values)))
        selected_start, selected_end = marked_dates[0], marked_dates[-1]
        selection_mode = "multi"
    else:
        selected_start = parse_date(start_date or "")
        selected_end = parse_date(end_date or "")
        if selected_start > selected_end:
            raise ValueError("开始日期不能晚于结束日期")
        marked_dates = ()
        selection_mode = "range"

    display_start = selected_start - pd.Timedelta(days=lookback_days)
    natural_end = selected_end + pd.Timedelta(days=lookahead_days)
    if to_latest:
        display_end = pd.Timestamp(today if today is not None else date.today()).normalize()
        if selected_end > display_end:
            raise ValueError("选择日期不能晚于 --to-latest 对应的当前日期")
    else:
        display_end = natural_end

    # 用交易日折算为自然日并留一周余量，确保图首MA已有足够历史根数。
    warmup_days = math.ceil(ma_warmup_bars / BARS_PER_DAY * 7 / 5) + 7
    fetch_start = display_start - pd.Timedelta(days=warmup_days)
    return MinutePlotWindow(
        selected_start=selected_start,
        selected_end=selected_end,
        display_start=display_start,
        display_end=display_end,
        fetch_start=fetch_start,
        marked_dates=marked_dates,
        selection_mode=selection_mode,
    )


def _latest_adjustment_factor(ts_code: str) -> float | None:
    factors = get_fifteenMin(
        ts_code,
        columns=["trade_date", "adj_factor"],
    )
    if factors.empty:
        return None
    values = pd.to_numeric(factors["adj_factor"], errors="coerce").dropna()
    if values.empty or values.iloc[-1] == 0:
        return None
    return float(values.iloc[-1])


def load_fifteen_min_data(
    ts_code: str,
    window: MinutePlotWindow,
    *,
    qfq: bool = True,
    ma_windows: Sequence[int] = DEFAULT_MA_WINDOWS,
) -> pd.DataFrame:
    """从统一data_provider读取15分钟OHLCV并计算均线。"""

    query = {
        "start_date": window.fetch_start.strftime("%Y%m%d"),
        "end_date": window.display_end.strftime("%Y%m%d"),
    }
    frame = get_fifteenMin(ts_code, **query)
    if frame.empty:
        raise ValueError(
            f"15分钟DuckDB在 {query['start_date']} 至 {query['end_date']} "
            f"没有 {ts_code} 的数据"
        )
    df = frame.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df["date"] = df["datetime"].dt.normalize()
    for column in ["open", "high", "low", "close", "vol", "amount", "adj_factor"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = (
        df.dropna(subset=["datetime", "open", "high", "low", "close", "vol"])
        .drop_duplicates(subset=["datetime"], keep="last")
        .sort_values("datetime")
        .reset_index(drop=True)
    )
    if df.empty:
        raise ValueError(f"{ts_code} 没有可绘制的有效15分钟OHLCV数据")

    if qfq:
        latest_factor = _latest_adjustment_factor(ts_code)
        if latest_factor is not None:
            factor = df["adj_factor"].fillna(latest_factor) / latest_factor
            for column in ("open", "high", "low", "close"):
                df[column] = df[column] * factor

    for bars in ma_windows:
        if bars <= 0:
            raise ValueError("MA周期必须为正整数")
        df[f"ma{bars}"] = df["close"].rolling(bars, min_periods=bars).mean()

    visible = df[
        (df["date"] >= window.display_start) & (df["date"] <= window.display_end)
    ].copy()
    if visible.empty:
        raise ValueError(
            f"展示窗口内没有 {ts_code} 的15分钟数据；当前读取范围为"
            f" {df['date'].min():%Y-%m-%d} 至 {df['date'].max():%Y-%m-%d}"
        )
    return visible.reset_index(drop=True)


def _date_bar_bounds(df: pd.DataFrame, target: pd.Timestamp) -> tuple[int, int] | None:
    indices = np.flatnonzero(df["date"].to_numpy(dtype="datetime64[ns]") == np.datetime64(target))
    if not len(indices):
        return None
    return int(indices[0]), int(indices[-1])


def _mark_selection(ax: Axes, df: pd.DataFrame, window: MinutePlotWindow) -> None:
    if window.marked_dates:
        for marker in window.marked_dates:
            bounds = _date_bar_bounds(df, marker)
            if bounds is None:
                raise ValueError(
                    f"指定日期 {marker.date()} 没有15分钟交易数据；请确认日期和离线库"
                )
            left, right = bounds
            ax.axvspan(left - 0.5, right + 0.5, color="#ffd54f", alpha=0.22, zorder=0)
            ax.axvline(left - 0.5, color="#d35400", linewidth=0.9, linestyle="--")
            ax.axvline(right + 0.5, color="#d35400", linewidth=0.9, linestyle="--")
        return

    dates = df["date"].to_numpy(dtype="datetime64[ns]")
    selected = (dates >= np.datetime64(window.selected_start)) & (
        dates <= np.datetime64(window.selected_end)
    )
    indices = np.flatnonzero(selected)
    if not len(indices):
        raise ValueError("所选日期段内没有15分钟交易数据")
    left, right = int(indices[0]), int(indices[-1])
    ax.axvspan(left - 0.5, right + 0.5, color="#ffd54f", alpha=0.13, zorder=0)
    ax.axvline(left - 0.5, color="#d35400", linewidth=0.9, linestyle="--")
    ax.axvline(right + 0.5, color="#d35400", linewidth=0.9, linestyle="--")


def _draw_session_guides(ax: Axes, df: pd.DataFrame) -> None:
    dates = df["date"]
    day_starts = np.flatnonzero(dates.ne(dates.shift()).to_numpy())
    for position in day_starts[1:]:
        ax.axvline(position - 0.5, color="#8c8c8c", linewidth=0.7, alpha=0.65)
    afternoon = np.flatnonzero(
        ((df["datetime"].dt.hour == 13) & (df["datetime"].dt.minute == 15)).to_numpy()
    )
    for position in afternoon:
        ax.axvline(position - 0.5, color="#bdbdbd", linewidth=0.55, linestyle=":", alpha=0.7)


def _apply_datetime_ticks(ax: Axes, df: pd.DataFrame) -> None:
    # 横轴只在每个交易日的第一根K线处标注，避免标签落到任意盘中时点。
    # 默认一个月窗口通常包含20多个交易日，此时隔一个交易日显示一次；
    # 更长窗口继续增大步长，把标签控制在大约16个以内。
    day_starts = np.flatnonzero(df["date"].ne(df["date"].shift()).to_numpy())
    if not len(day_starts):
        return
    if len(day_starts) <= 16:
        stride = 1
    elif len(day_starts) <= 32:
        stride = 2
    else:
        stride = math.ceil(len(day_starts) / 16)
    positions = day_starts[::stride]
    labels = [
        pd.Timestamp(df["datetime"].iloc[pos]).strftime("%m-%d\n%H:%M")
        for pos in positions
    ]
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=0, ha="center")


def plot_fifteen_min_kline(
    df: pd.DataFrame,
    *,
    ts_code: str,
    stock_name: str | None,
    window: MinutePlotWindow,
    qfq: bool,
    ma_windows: Sequence[int] = DEFAULT_MA_WINDOWS,
    save_path: Path | None = None,
    show: bool = True,
    dpi: int = 160,
) -> Path | None:
    if dpi <= 0:
        raise ValueError("--dpi 必须大于0")
    if not show and save_path is None:
        raise ValueError("show=False 时必须提供 save_path")
    if save_path is not None and save_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("保存文件必须使用 .jpg 或 .jpeg 扩展名")

    _configure_chinese_font()
    fig, (price_ax, volume_ax) = plt.subplots(
        2, 1, figsize=(18, 10), sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04},
    )
    fig.patch.set_facecolor("#fafafa")
    for axis in (price_ax, volume_ax):
        axis.set_facecolor("#fafafa")
        axis.grid(True, color="#d9d9d9", linewidth=0.5, alpha=0.55)
        _mark_selection(axis, df, window)
        _draw_session_guides(axis, df)

    colors = _draw_candles(price_ax, df)
    x = np.arange(len(df))
    for bars in ma_windows:
        price_ax.plot(
            x, df[f"ma{bars}"], color=MA_COLORS.get(bars),
            linewidth=1.0 if bars < 120 else 1.25, label=f"MA{bars}", zorder=4,
        )
    volume_ax.bar(x, df["vol"] / 10_000.0, width=0.64, color=colors, alpha=0.76)
    volume_ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    volume_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))

    if len(window.marked_dates) == 1:
        selected_text = f"标记交易日 {window.marked_dates[0]:%Y-%m-%d}"
    elif len(window.marked_dates) > 1:
        selected_text = (
            f"标记{len(window.marked_dates)}个交易日 "
            f"({window.selected_start:%Y-%m-%d}至{window.selected_end:%Y-%m-%d})"
        )
    else:
        selected_text = (
            f"标记区间 {window.selected_start:%Y-%m-%d}至{window.selected_end:%Y-%m-%d}"
        )
    title_name = f"{stock_name} " if stock_name else ""
    adjustment = "前复权价格" if qfq else "不复权价格"
    price_ax.set_title(
        f"{title_name}{ts_code}  15分钟K线  |  {adjustment}  |  {selected_text}\n"
        f"实际展示 {df['datetime'].iloc[0]:%Y-%m-%d %H:%M} 至 "
        f"{df['datetime'].iloc[-1]:%Y-%m-%d %H:%M}，共{len(df)}根",
        fontsize=15, pad=12,
    )
    price_ax.set_ylabel("价格（元）")
    volume_ax.set_ylabel("成交量（万股）")
    volume_ax.set_xlabel("交易日期 / 时间（非交易时段已压缩）")
    price_ax.legend(loc="upper left", ncol=7, fontsize=8, framealpha=0.8)
    price_ax.set_xlim(-1, len(df))
    _apply_datetime_ticks(volume_ax, df)
    price_ax.tick_params(axis="both", labelsize=9)
    volume_ax.tick_params(axis="both", labelsize=8)

    saved: Path | None = None
    if save_path is not None:
        saved = save_path.expanduser().resolve()
        saved.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            saved, dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor(),
            pil_kwargs={"quality": 95},
        )
    if show:
        plt.show()
    plt.close(fig)
    return saved


def build_save_path(ts_code: str, window: MinutePlotWindow, save_dir: Path) -> Path:
    start = window.selected_start.strftime("%Y%m%d")
    end = window.selected_end.strftime("%Y%m%d")
    suffix = "_multidays" if window.selection_mode == "multi" else ""
    return save_dir.expanduser() / f"kline_15min_{ts_code}_{start}_{end}{suffix}.jpg"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从15分钟DuckDB绘制K线、实际成交量和MA均线。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("ts_code", help="证券代码，如600519、000001.SZ、920014.BJ")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--trade-date", "--tradedate", help="指定并标记单个交易日")
    mode.add_argument(
        "--trade-dates", "--tradedates", nargs="+",
        help="指定并标记多个交易日，可用空格或逗号分隔",
    )
    mode.add_argument("--start-date", help="标记区间起点，需与--end-date同用")
    parser.add_argument("--end-date", help="标记区间终点")
    parser.add_argument(
        "--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
        help="选择起点前展示自然日数",
    )
    parser.add_argument(
        "--lookahead-days", type=int, default=DEFAULT_LOOKAHEAD_DAYS,
        help="选择终点后展示自然日数",
    )
    parser.add_argument("--ma-warmup-bars", type=int, default=250, help="图外均线预热根数")
    parser.add_argument("--to-latest", action="store_true", help="展示终点延伸到当前日期")
    parser.add_argument("--no-qfq", action="store_true", help="使用不复权价格")
    parser.add_argument("--save-pic", "--savepic", "--save", action="store_true")
    parser.add_argument("--save-dir", type=Path, default=DEFAULT_SAVE_DIR)
    parser.add_argument("--dpi", type=int, default=160)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ts_code = normalize_ts_code(args.ts_code)
        window = resolve_window(
            start_date=args.start_date, end_date=args.end_date,
            trade_date=args.trade_date, trade_dates=args.trade_dates,
            lookback_days=args.lookback_days, lookahead_days=args.lookahead_days,
            ma_warmup_bars=args.ma_warmup_bars, to_latest=args.to_latest,
        )
        df = load_fifteen_min_data(ts_code, window, qfq=not args.no_qfq)
        target = build_save_path(ts_code, window, args.save_dir) if args.save_pic else None
        saved = plot_fifteen_min_kline(
            df, ts_code=ts_code, stock_name=lookup_stock_name(ts_code),
            window=window, qfq=not args.no_qfq, save_path=target,
            show=not args.save_pic, dpi=args.dpi,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if saved is not None:
        print(f"已保存: {saved}")
    else:
        print("图形已显示，本次未保存图片")
    print(
        f"实际数据: {df['datetime'].iloc[0]:%Y-%m-%d %H:%M} 至 "
        f"{df['datetime'].iloc[-1]:%Y-%m-%d %H:%M}，共{len(df)}根15分钟K线"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
