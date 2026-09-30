#!/usr/bin/env python3
"""从 TradingAgent 离线数据库绘制 A 股日 K、成交量和移动均线。

支持三种观察区间：
1. ``--start-date`` + ``--end-date``：标记一个日期段；
2. ``--trade-date``：标记一个指定交易日。
3. ``--trade-dates``：标记同一股票的多个指定交易日。

所有模式都可以附加 ``--buy-date`` 和 ``--sell-date``，标记交易日期并突出
两日之间的持仓区间。日期标记不代表实际成交价格。

图形默认展示观察起点前 12 个月，以及观察终点后 1 个月的数据。使用
``--to-latest`` 时，展示终点改为当前日期。为了让 MA250 在展示窗口起点就有
完整数值，脚本会在展示窗口之前额外读取一段只用于均线计算的预热数据。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import sys
from typing import Sequence

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAVE_DIR = PROJECT_ROOT / "plot" / "tmp_savepic"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from coreClient.data_provider import get_basic, get_day  # noqa: E402


DEFAULT_MA_WINDOWS = (5, 10, 20, 30, 60, 120, 250)
MA_COLORS = {
    5: "#f4c430",
    10: "#4aa3df",
    20: "#b06ad8",
    30: "#ff8c42",
    60: "#6f7bf7",
    120: "#8e6c4a",
    250: "#2c3e50",
}


@dataclass(frozen=True)
class PlotWindow:
    """用户选择区间、图形展示区间和数据读取区间。"""

    selected_start: pd.Timestamp
    selected_end: pd.Timestamp
    display_start: pd.Timestamp
    display_end: pd.Timestamp
    fetch_start: pd.Timestamp
    trade_date_mode: bool
    marked_dates: tuple[pd.Timestamp, ...]
    selection_mode: str
    buy_date: pd.Timestamp | None = None
    sell_date: pd.Timestamp | None = None


def parse_date(value: str) -> pd.Timestamp:
    """解析 YYYY-MM-DD 或 YYYYMMDD，并去掉时间部分。"""

    text = str(value).strip()
    if not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"日期格式错误: {value!r}；请使用 YYYY-MM-DD 或 YYYYMMDD")
    try:
        return pd.Timestamp(text).normalize()
    except ValueError as exc:
        raise ValueError(f"无效日期: {value!r}") from exc


def normalize_ts_code(value: str) -> str:
    """把 6 位证券代码补成 Tushare 格式，也接受显式的 .SH/.SZ/.BJ。"""

    code = str(value).strip().upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        return code
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(
            f"证券代码格式错误: {value!r}；示例: 600519、600519.SH、000001.SZ"
        )

    first = code[0]
    if first in {"4", "8", "9"}:
        suffix = "BJ"
    elif first in {"5", "6", "7"}:
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{code}.{suffix}"


def resolve_window(
    *,
    start_date: str | None,
    end_date: str | None,
    trade_date: str | None,
    lookback_months: int,
    lookahead_months: int,
    ma_warmup_days: int,
    to_latest: bool,
    trade_dates: Sequence[str] | None = None,
    buy_date: str | None = None,
    sell_date: str | None = None,
    today: date | pd.Timestamp | None = None,
) -> PlotWindow:
    """根据命令行模式计算选择、展示和读取窗口。"""

    if lookback_months < 0 or lookahead_months < 0 or ma_warmup_days < 0:
        raise ValueError("前后扩展月份和均线预热天数都不能为负数")

    range_mode = start_date is not None or end_date is not None
    multi_values: list[str] = []
    if trade_dates is not None:
        for value in trade_dates:
            multi_values.extend(part.strip() for part in str(value).split(",") if part.strip())
        if not multi_values:
            raise ValueError("--trade-dates 至少需要一个日期")

    active_modes = int(range_mode) + int(trade_date is not None) + int(bool(multi_values))
    if active_modes > 1:
        raise ValueError(
            "--trade-date、--trade-dates 和 --start-date/--end-date 三种模式互斥，不能同时使用"
        )
    if range_mode and not (start_date and end_date):
        raise ValueError("--start-date 和 --end-date 必须同时提供")
    if active_modes == 0:
        raise ValueError(
            "必须传 --trade-date、--trade-dates，或同时传 --start-date 和 --end-date"
        )

    if (buy_date is None) != (sell_date is None):
        raise ValueError("--buy-date 和 --sell-date 必须同时提供")
    buy = parse_date(buy_date) if buy_date is not None else None
    sell = parse_date(sell_date) if sell_date is not None else None
    if buy is not None and sell is not None and buy > sell:
        raise ValueError("买入日期不能晚于卖出日期")

    if trade_date is not None:
        selected_start = selected_end = parse_date(trade_date)
        trade_date_mode = True
        marked_dates = (selected_start,)
        selection_mode = "single"
    elif multi_values:
        marked_dates = tuple(sorted(set(parse_date(value) for value in multi_values)))
        selected_start = marked_dates[0]
        selected_end = marked_dates[-1]
        trade_date_mode = True
        selection_mode = "multi"
    else:
        selected_start = parse_date(start_date or "")
        selected_end = parse_date(end_date or "")
        trade_date_mode = False
        marked_dates = ()
        selection_mode = "range"
        if selected_start > selected_end:
            raise ValueError("开始日期不能晚于结束日期")

    display_start = selected_start - pd.DateOffset(months=lookback_months)
    natural_end = selected_end + pd.DateOffset(months=lookahead_months)
    if to_latest:
        latest = pd.Timestamp(today if today is not None else date.today()).normalize()
        display_end = latest
        if selected_end > display_end:
            raise ValueError("选择日期不能晚于 --to-latest 对应的当前日期")
        if sell is not None and sell > display_end:
            raise ValueError("卖出日期不能晚于 --to-latest 对应的当前日期")
    else:
        display_end = natural_end

    # 交易标记即使在默认扩展窗口之外，也必须出现在最终图中。
    if buy is not None and sell is not None:
        display_start = min(display_start, buy)
        if not to_latest:
            display_end = max(display_end, sell)

    fetch_start = display_start - pd.Timedelta(days=ma_warmup_days)
    return PlotWindow(
        selected_start=selected_start,
        selected_end=selected_end,
        display_start=display_start,
        display_end=display_end,
        fetch_start=fetch_start,
        trade_date_mode=trade_date_mode,
        marked_dates=marked_dates,
        selection_mode=selection_mode,
        buy_date=buy,
        sell_date=sell,
    )


def load_daily_data(ts_code: str, window: PlotWindow, qfq: bool) -> pd.DataFrame:
    """只通过 data_provider 读取本地日线，并准备画图字段。"""

    query = dict(
        ts_code=ts_code,
        start_date=window.fetch_start.strftime("%Y-%m-%d"),
        end_date=window.display_end.strftime("%Y-%m-%d"),
        source="database_only",
    )
    price_df = get_day(qfq=qfq, **query)
    if price_df.empty:
        raise ValueError(
            f"离线数据库在 {query['start_date']} 至 {query['end_date']} "
            f"没有 {ts_code} 的日线数据"
        )

    # data_provider 的前复权口径会同步调整成交量。K 线图中的量柱应表示当天真实
    # 成交量，因此价格前复权时再读取一次原始量，并按交易日覆盖 vol/amount。
    if qfq:
        raw_df = get_day(qfq=False, **query)
        raw_volume = raw_df[["trade_date", "vol", "amount"]].rename(
            columns={"vol": "raw_vol", "amount": "raw_amount"}
        )
        price_df = price_df.merge(raw_volume, on="trade_date", how="left")
        price_df["vol"] = price_df["raw_vol"].fillna(price_df["vol"])
        price_df["amount"] = price_df["raw_amount"].fillna(price_df["amount"])
        price_df = price_df.drop(columns=["raw_vol", "raw_amount"])

    df = price_df.copy()
    df["date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
    numeric_columns = ["open", "high", "low", "close", "vol", "amount"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = (
        df.dropna(subset=["date", "open", "high", "low", "close", "vol"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    if df.empty:
        raise ValueError(f"{ts_code} 的离线日线没有可绘制的有效 OHLCV 数据")

    for days in DEFAULT_MA_WINDOWS:
        df[f"ma{days}"] = df["close"].rolling(days, min_periods=days).mean()

    visible = df[
        (df["date"] >= window.display_start) & (df["date"] <= window.display_end)
    ].copy()
    if visible.empty:
        actual_start = df["date"].min().date()
        actual_end = df["date"].max().date()
        raise ValueError(
            f"展示窗口内没有 {ts_code} 的数据；离线可用范围为 "
            f"{actual_start} 至 {actual_end}"
        )
    return visible.reset_index(drop=True)


def load_stock_name_map() -> dict[str, str]:
    """一次性读取离线股票名称映射；失败时返回空字典。"""

    try:
        basic = get_basic(list_status=None, source="database_only")
    except Exception:
        return {}
    if basic.empty or "ts_code" not in basic.columns or "name" not in basic.columns:
        return {}
    return {
        str(code): str(name)
        for code, name in zip(basic["ts_code"], basic["name"])
        if pd.notna(code) and pd.notna(name)
    }


def lookup_stock_name(ts_code: str) -> str | None:
    """从离线股票档案中查名称；查不到时不影响画图。"""

    return load_stock_name_map().get(ts_code)


def _configure_chinese_font() -> None:
    plt.rcParams["font.sans-serif"] = [
        "PingFang SC",
        "Hiragino Sans GB",
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def _mark_selection(ax: Axes, dates: pd.Series, window: PlotWindow) -> None:
    if window.trade_date_mode:
        date_values = dates.to_numpy(dtype="datetime64[ns]")
        for marker in window.marked_dates:
            exact = np.flatnonzero(date_values == np.datetime64(marker))
            if not len(exact):
                raise ValueError(
                    f"指定日期 {marker.date()} 没有交易数据；"
                    "请确认它是交易日且离线数据已更新"
                )
            center = float(exact[0])
            ax.axvspan(center - 0.5, center + 0.5, color="#ffd54f", alpha=0.24, zorder=0)
            ax.axvline(center, color="#d35400", linewidth=1.0, linestyle="--", alpha=0.9)
        return

    values = dates.to_numpy(dtype="datetime64[ns]")
    left_index = int(np.searchsorted(values, np.datetime64(window.selected_start), side="left"))
    right_index = int(np.searchsorted(values, np.datetime64(window.selected_end), side="right"))
    if left_index >= len(dates) or right_index <= 0 or right_index <= left_index:
        raise ValueError("所选日期段内没有交易数据")
    left = float(max(left_index, 0))
    right = float(min(right_index, len(dates)))
    ax.axvspan(left - 0.5, right - 0.5, color="#ffd54f", alpha=0.14, zorder=0)
    ax.axvline(left - 0.5, color="#d35400", linewidth=0.9, linestyle="--", alpha=0.8)
    ax.axvline(right - 0.5, color="#d35400", linewidth=0.9, linestyle="--", alpha=0.8)


def _trade_positions(dates: pd.Series, window: PlotWindow) -> tuple[int, int] | None:
    """定位买卖日的实际 K 线，避免把非交易日或缺失行情画到邻近日期。"""

    if window.buy_date is None or window.sell_date is None:
        return None
    values = dates.to_numpy(dtype="datetime64[ns]")
    positions: list[int] = []
    for label, marker in (("买入", window.buy_date), ("卖出", window.sell_date)):
        found = np.flatnonzero(values == np.datetime64(marker))
        if not len(found):
            raise ValueError(f"{label}日期 {marker.date()} 没有交易数据；请检查交易日和离线行情")
        positions.append(int(found[0]))
    return positions[0], positions[1]


def _draw_candles(ax: Axes, df: pd.DataFrame) -> list[str]:
    up_color = "#d62728"  # A 股习惯：红涨
    down_color = "#179c52"  # 绿跌
    colors: list[str] = []
    for x, row in df.iterrows():
        color = up_color if row["close"] >= row["open"] else down_color
        colors.append(color)
        ax.vlines(x, row["low"], row["high"], color=color, linewidth=0.8, zorder=2)
        body_low = min(row["open"], row["close"])
        body_height = abs(row["close"] - row["open"])
        if body_height == 0:
            ax.hlines(row["close"], x - 0.32, x + 0.32, color=color, linewidth=1.1, zorder=3)
        else:
            ax.add_patch(
                Rectangle(
                    (x - 0.32, body_low),
                    0.64,
                    body_height,
                    facecolor=color,
                    edgecolor=color,
                    linewidth=0.5,
                    zorder=3,
                )
            )
    return colors


def _apply_date_ticks(ax: Axes, dates: pd.Series) -> None:
    count = len(dates)
    tick_count = min(12, count)
    positions = np.unique(np.linspace(0, count - 1, tick_count, dtype=int))
    ax.set_xticks(positions)
    ax.set_xticklabels(
        [pd.Timestamp(dates.iloc[pos]).strftime("%Y-%m-%d") for pos in positions],
        rotation=35,
        ha="right",
    )


def plot_daily_kline(
    df: pd.DataFrame,
    *,
    ts_code: str,
    stock_name: str | None,
    window: PlotWindow,
    qfq: bool,
    save_path: Path | None = None,
    show: bool = True,
    dpi: int = 160,
) -> Path | None:
    """绘制日 K；可选择显示图形或保存为 JPG。"""

    if dpi <= 0:
        raise ValueError("--dpi 必须大于 0")
    if not show and save_path is None:
        raise ValueError("show=False 时必须提供 save_path")
    if save_path is not None and save_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("保存文件必须使用 .jpg 或 .jpeg 扩展名")
    trade_positions = _trade_positions(df["date"], window)

    _configure_chinese_font()
    fig, (price_ax, volume_ax) = plt.subplots(
        2,
        1,
        figsize=(16, 9),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04},
    )
    fig.patch.set_facecolor("#fafafa")
    for axis in (price_ax, volume_ax):
        axis.set_facecolor("#fafafa")
        axis.grid(True, axis="both", color="#d9d9d9", linewidth=0.5, alpha=0.55)
        _mark_selection(axis, df["date"], window)
        if trade_positions is not None:
            buy_x, sell_x = trade_positions
            axis.axvspan(
                buy_x - 0.5, sell_x + 0.5,
                color="#72b6e4", alpha=0.20, zorder=0.5,
            )
            axis.axvline(buy_x, color="#087f5b", linewidth=1.35, linestyle="--", zorder=5)
            axis.axvline(sell_x, color="#a52b51", linewidth=1.35, linestyle="--", zorder=5)

    colors = _draw_candles(price_ax, df)
    x = np.arange(len(df))
    for days in DEFAULT_MA_WINDOWS:
        column = f"ma{days}"
        price_ax.plot(
            x,
            df[column],
            color=MA_COLORS[days],
            linewidth=1.0 if days < 120 else 1.25,
            label=f"MA{days}",
            zorder=4,
        )

    if trade_positions is not None:
        buy_x, sell_x = trade_positions
        for marker_x, label, marker, color, offset, vertical_align in (
            (buy_x, "买入", "^", "#087f5b", 14, "bottom"),
            (sell_x, "卖出", "v", "#a52b51", -14, "top"),
        ):
            marker_price = float(df.iloc[marker_x]["open"])
            price_ax.scatter(
                [marker_x], [marker_price], marker=marker, s=135,
                color=color, edgecolor="white", linewidth=0.8, zorder=7,
            )
            price_ax.annotate(
                f"{label}日开盘 {marker_price:.2f}",
                xy=(marker_x, marker_price), xytext=(0, offset),
                textcoords="offset points", ha="center", va=vertical_align,
                fontsize=9, color=color, fontweight="bold", zorder=8,
                bbox={"facecolor": "#fafafa", "edgecolor": "none", "alpha": 0.8, "pad": 1},
            )

    volume_in_10k_lots = df["vol"] / 10_000.0
    volume_ax.bar(x, volume_in_10k_lots, width=0.64, color=colors, alpha=0.76)
    volume_ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
    volume_ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))

    actual_start = pd.Timestamp(df["date"].iloc[0]).strftime("%Y-%m-%d")
    actual_end = pd.Timestamp(df["date"].iloc[-1]).strftime("%Y-%m-%d")
    if len(window.marked_dates) == 1:
        selected_text = f"标记交易日 {window.marked_dates[0]:%Y-%m-%d}"
    elif len(window.marked_dates) > 1:
        selected_text = (
            f"标记 {len(window.marked_dates)} 个交易日 "
            f"({window.selected_start:%Y-%m-%d} 至 {window.selected_end:%Y-%m-%d})"
        )
    else:
        selected_text = (
            f"标记区间 {window.selected_start:%Y-%m-%d} 至 {window.selected_end:%Y-%m-%d}"
        )
    title_name = f"{stock_name} " if stock_name else ""
    adjustment = "前复权价格" if qfq else "不复权价格"
    title_lines = [f"{title_name}{ts_code}  日 K  |  {adjustment}  |  {selected_text}"]
    if window.buy_date is not None and window.sell_date is not None:
        title_lines.append(
            f"持仓 {window.buy_date:%Y-%m-%d} 至 "
            f"{window.sell_date:%Y-%m-%d}（仅标日期）"
        )
    title_lines.append(f"离线数据实际展示 {actual_start} 至 {actual_end}")
    price_ax.set_title(
        "\n".join(title_lines),
        fontsize=14,
        pad=12,
    )
    price_ax.set_ylabel("价格（元）")
    volume_ax.set_ylabel("成交量（万手）")
    volume_ax.set_xlabel("交易日期")
    price_ax.legend(loc="upper left", ncol=7, fontsize=8, framealpha=0.8)
    price_ax.set_xlim(-1, len(df))
    _apply_date_ticks(volume_ax, df["date"])
    price_ax.tick_params(axis="both", labelsize=9)
    volume_ax.tick_params(axis="both", labelsize=8)

    saved: Path | None = None
    if save_path is not None:
        saved = save_path.expanduser().resolve()
        saved.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            saved,
            dpi=dpi,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
            pil_kwargs={"quality": 95},
        )
    if show:
        plt.show()
    plt.close(fig)
    return saved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从 offlineDataManager 离线数据库绘制日 K、成交量和 MA 均线。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("ts_code", help="证券代码，如 600519、600519.SH、000001.SZ")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--trade-date", "--tradedate", help="指定并标记单个交易日")
    mode.add_argument(
        "--trade-dates",
        "--tradedates",
        nargs="+",
        help="指定并标记多个交易日，可用空格或逗号分隔",
    )
    mode.add_argument("--start-date", help="要标记的日期段起点，需与 --end-date 同用")
    parser.add_argument("--end-date", help="要标记的日期段终点")
    parser.add_argument("--buy-date", "--buydate", help="买入交易日；须与 --sell-date 同用")
    parser.add_argument("--sell-date", "--selldate", help="卖出交易日；须与 --buy-date 同用")
    parser.add_argument(
        "--lookback-months",
        type=int,
        default=12,
        help="展示窗口在选择起点前扩展的自然月数",
    )
    parser.add_argument(
        "--lookahead-months",
        type=int,
        default=1,
        help="未启用 --to-latest 时，在选择终点后扩展的自然月数",
    )
    parser.add_argument(
        "--ma-warmup-days",
        type=int,
        default=400,
        help="展示窗口前额外读取、只用于计算长均线的自然日数",
    )
    parser.add_argument(
        "--to-latest",
        action="store_true",
        help="把展示/查询终点延伸到今天，实际终点取决于离线库最新数据",
    )
    parser.add_argument(
        "--no-qfq",
        action="store_true",
        help="价格不做前复权；默认价格前复权、成交量保持实际原始值",
    )
    parser.add_argument(
        "--save-pic",
        "--savepic",
        "--save",
        action="store_true",
        help="保存 JPG 图片；默认关闭，关闭时直接显示图形",
    )
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=DEFAULT_SAVE_DIR,
        help="图片保存目录，仅在启用 --save-pic 时使用",
    )
    parser.add_argument("--dpi", type=int, default=160, help="输出图片 DPI")
    return parser


def build_save_path(ts_code: str, window: PlotWindow, save_dir: Path) -> Path:
    """按固定规则生成路径；多交易日模式使用最小和最大标记日期。"""

    start_text = window.selected_start.strftime("%Y%m%d")
    end_text = window.selected_end.strftime("%Y%m%d")
    suffix = "_multidays" if window.selection_mode == "multi" else ""
    if window.buy_date is not None and window.sell_date is not None:
        suffix += f"_buy{window.buy_date:%Y%m%d}_sell{window.sell_date:%Y%m%d}"
    filename = f"kline_day_{ts_code}_{start_text}_{end_text}{suffix}.jpg"
    return save_dir.expanduser() / filename


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ts_code = normalize_ts_code(args.ts_code)
        window = resolve_window(
            start_date=args.start_date,
            end_date=args.end_date,
            trade_date=args.trade_date,
            trade_dates=args.trade_dates,
            buy_date=args.buy_date,
            sell_date=args.sell_date,
            lookback_months=args.lookback_months,
            lookahead_months=args.lookahead_months,
            ma_warmup_days=args.ma_warmup_days,
            to_latest=args.to_latest,
        )
        df = load_daily_data(ts_code, window, qfq=not args.no_qfq)
        save_path = (
            build_save_path(ts_code, window, args.save_dir) if args.save_pic else None
        )
        saved = plot_daily_kline(
            df,
            ts_code=ts_code,
            stock_name=lookup_stock_name(ts_code),
            window=window,
            qfq=not args.no_qfq,
            save_path=save_path,
            show=not args.save_pic,
            dpi=args.dpi,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    if saved is not None:
        print(f"已保存: {saved}")
    else:
        print("图形已显示，本次未保存图片")
    print(
        f"实际数据: {df['date'].iloc[0]:%Y-%m-%d} 至 "
        f"{df['date'].iloc[-1]:%Y-%m-%d}，共 {len(df)} 个交易日"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
