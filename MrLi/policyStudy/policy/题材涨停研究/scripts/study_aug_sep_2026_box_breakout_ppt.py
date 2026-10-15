"""Scan 2026 Aug-Sep A-shares for post-impulse box breakouts."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from study_first_ma30_pullback_cohesion_ppt import (
    BLUE,
    CYAN,
    DARK_TEXT,
    FONT_NAME,
    GOLD,
    LIGHT,
    MID,
    MUTED,
    NAVY,
    ORANGE,
    RED,
    TEAL,
    WHITE,
    _add_card,
    _add_footer,
    _add_header,
    _add_text,
    _draw_candlesticks,
    _register_fonts,
    _set_background,
    _style_table,
    convert_ppt_to_pdf,
    create_contact_sheets,
    render_pdf,
)


ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUTPUT = ROOT / "outputs" / "aug_sep_2026_box_breakout" / "20260801_20260930_qfq"
EVENT_BIG_BULL_RETURN_PCT = 6.0
EVENT_BIG_BULL_BODY_PCT = 5.0
EVENT_BIG_BULL_CLOSE_LOCATION = 0.70
PREBOX_BIG_BULL_RETURN_PCT = 5.0
PREBOX_BIG_BULL_BODY_PCT = 3.0
PREBOX_BIG_BULL_CLOSE_LOCATION = 0.65
PREBOX_MAX_SIGNAL_GAP = 10
PREBOX_RULE_CN = (
    "箱体前30个实际交易日内：连续涨停至少2日，或至少1个涨停与1根非涨停大阳相距不超过10个实际交易日；"
    "前置大阳要求收涨≥5%、close/open实体≥3%、收盘位置≥0.65；该段涨幅≥12%，且至少一个入选信号日原始量比≥1.3"
)


def compute_rising_streak(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    result = np.zeros(len(numeric), dtype=int)
    for index in range(1, len(numeric)):
        current = numeric.iloc[index]
        previous = numeric.iloc[index - 1]
        if pd.notna(current) and pd.notna(previous) and current > previous:
            result[index] = result[index - 1] + 1
    return pd.Series(result, index=values.index, dtype=int)


def find_prebreakout_box(
    frame: pd.DataFrame,
    *,
    event_position: int,
    min_days: int = 20,
    max_days: int = 120,
    max_range_pct: float = 15.0,
) -> dict[str, Any] | None:
    end = int(event_position) - 1
    if min_days <= 0 or max_days < min_days or end - min_days + 1 < 0:
        return None
    shortest_start = end - min_days + 1
    search_start = max(0, end - max_days + 1)
    best: dict[str, Any] | None = None
    for start in range(shortest_start, search_start - 1, -1):
        window = frame.iloc[start : end + 1]
        high = float(pd.to_numeric(window["high"], errors="coerce").max())
        low = float(pd.to_numeric(window["low"], errors="coerce").min())
        if not np.isfinite(high) or not np.isfinite(low) or low <= 0:
            return None
        range_pct = 100.0 * (high - low) / low
        if range_pct > max_range_pct + 1e-12:
            break
        best = {
            "box_start_position": int(start),
            "box_end_position": int(end),
            "box_start_date": str(frame.iloc[start]["trade_date"]),
            "box_end_date": str(frame.iloc[end]["trade_date"]),
            "box_trading_days": int(end - start + 1),
            "box_high": high,
            "box_low": low,
            "box_range_pct": float(range_pct),
        }
    return best


def is_sixty_day_close_breakout(
    frame: pd.DataFrame,
    event_position: int,
    *,
    lookback: int = 60,
) -> bool:
    if event_position < lookback:
        return False
    prior = pd.to_numeric(
        frame.iloc[event_position - lookback : event_position]["high"],
        errors="coerce",
    )
    close = pd.to_numeric(pd.Series([frame.iloc[event_position]["close"]]), errors="coerce").iloc[0]
    return bool(pd.notna(close) and prior.notna().all() and close > float(prior.max()))


def is_big_bull(
    row: pd.Series,
    *,
    min_return_pct: float = EVENT_BIG_BULL_RETURN_PCT,
    min_body_pct: float = EVENT_BIG_BULL_BODY_PCT,
    min_close_location: float = EVENT_BIG_BULL_CLOSE_LOCATION,
) -> bool:
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    pre_close = float(row["pre_close"])
    if min(open_price, low) <= 0 or high < low:
        return False
    pct_chg = pd.to_numeric(pd.Series([row.get("pct_chg", np.nan)]), errors="coerce").iloc[0]
    if pd.notna(pct_chg):
        return_pct = float(pct_chg)
    elif pre_close > 0:
        return_pct = 100.0 * (close / pre_close - 1.0)
    else:
        return False
    body_pct = 100.0 * (close / open_price - 1.0)
    close_location = (close - low) / (high - low) if high > low else float(close >= high)
    return bool(
        return_pct >= min_return_pct
        and body_pct >= min_body_pct
        and close_location >= min_close_location
    )


def _max_true_run(flags: list[bool]) -> int:
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


def detect_prebox_impulse(
    frame: pd.DataFrame,
    *,
    box_start_position: int,
    lookback: int = 30,
    max_signal_gap: int = PREBOX_MAX_SIGNAL_GAP,
    min_big_bull_return_pct: float = PREBOX_BIG_BULL_RETURN_PCT,
    min_big_bull_body_pct: float = PREBOX_BIG_BULL_BODY_PCT,
    min_rise_pct: float = 12.0,
    min_signal_volume_ratio: float = 1.3,
) -> dict[str, Any]:
    start = max(0, int(box_start_position) - int(lookback))
    end = int(box_start_position) - 1
    result: dict[str, Any] = {
        "prebox_impulse_pass": False,
        "prebox_impulse_mode": "",
        "prebox_window_start_position": int(start),
        "prebox_window_end_position": int(end),
        "prebox_limit_up_count": 0,
        "prebox_big_bull_count": 0,
        "prebox_max_consecutive_limit_up": 0,
        "prebox_impulse_start_position": None,
        "prebox_impulse_end_position": None,
        "prebox_impulse_rise_pct": float("nan"),
        "prebox_max_signal_volume_ratio": float("nan"),
    }
    if end < start:
        return result
    window_positions = list(range(start, end + 1))
    limit_flags = [bool(frame.iloc[position].get("is_limit_up", False)) for position in window_positions]
    limit_positions = [position for position, flag in zip(window_positions, limit_flags) if flag]
    big_positions = [
        position
        for position in window_positions
        if not bool(frame.iloc[position].get("is_limit_up", False))
        and is_big_bull(
            frame.iloc[position],
            min_return_pct=min_big_bull_return_pct,
            min_body_pct=min_big_bull_body_pct,
            min_close_location=PREBOX_BIG_BULL_CLOSE_LOCATION,
        )
    ]
    max_run = _max_true_run(limit_flags)
    result["prebox_limit_up_count"] = int(len(limit_positions))
    result["prebox_big_bull_count"] = int(len(big_positions))
    result["prebox_max_consecutive_limit_up"] = int(max_run)

    signal_positions: list[int] = []
    if max_run >= 2:
        result["prebox_impulse_mode"] = "consecutive_limit_up"
        signal_positions = limit_positions
    else:
        pairs = [
            (abs(limit_position - big_position), limit_position, big_position)
            for limit_position in limit_positions
            for big_position in big_positions
            if abs(limit_position - big_position) <= max_signal_gap
        ]
        if pairs:
            _, limit_position, big_position = min(pairs)
            result["prebox_impulse_mode"] = "limit_up_plus_big_bull"
            signal_positions = sorted({limit_position, big_position})
    if not signal_positions:
        return result

    impulse_start = int(min(signal_positions))
    impulse_end = int(max(signal_positions))
    baseline_position = impulse_start - 1
    if baseline_position < 0:
        return result
    baseline_close = float(frame.iloc[baseline_position]["close"])
    peak_close = float(pd.to_numeric(frame.iloc[impulse_start : box_start_position]["close"], errors="coerce").max())
    rise_pct = 100.0 * (peak_close / baseline_close - 1.0) if baseline_close > 0 else float("nan")

    raw_volume = pd.to_numeric(frame["raw_vol"], errors="coerce")
    prior_average = raw_volume.shift(1).rolling(20, min_periods=5).mean()
    volume_ratios = [
        float(raw_volume.iloc[position] / prior_average.iloc[position])
        for position in signal_positions
        if pd.notna(prior_average.iloc[position]) and prior_average.iloc[position] > 0
    ]
    max_volume_ratio = max(volume_ratios) if volume_ratios else float("nan")
    result.update(
        {
            "prebox_impulse_start_position": impulse_start,
            "prebox_impulse_end_position": impulse_end,
            "prebox_impulse_rise_pct": float(rise_pct),
            "prebox_max_signal_volume_ratio": float(max_volume_ratio),
            "prebox_impulse_pass": bool(
                np.isfinite(rise_pct)
                and rise_pct >= min_rise_pct
                and np.isfinite(max_volume_ratio)
                and max_volume_ratio >= min_signal_volume_ratio
            ),
        }
    )
    return result


def classify_setup(
    *,
    event_volume_ratio: float,
    open_inside_box: bool,
    six_ma_spread_pct: float,
    bullish_order_count: int,
    ma60_rising_streak: int,
    ma120_rising_streak: int,
) -> str:
    strict = (
        event_volume_ratio >= 1.5
        and open_inside_box
        and six_ma_spread_pct <= 8.0
        and bullish_order_count == 5
        and ma60_rising_streak >= 20
        and ma120_rising_streak >= 10
    )
    if strict:
        return "A_严格优选"
    preferred = (
        event_volume_ratio >= 1.3
        and open_inside_box
        and six_ma_spread_pct <= 10.0
        and bullish_order_count >= 4
        and ma60_rising_streak >= 10
        and ma120_rising_streak >= 5
    )
    return "B_主观察" if preferred else "C_仅结构符合"


def compute_similarity_score(
    *,
    box_days: int,
    box_range_pct: float,
    event_volume_ratio: float,
    open_inside_box: bool,
    six_ma_spread_pct: float,
    bullish_order_count: int,
    ma60_rising_streak: int,
    ma120_rising_streak: int,
    impulse_mode: str,
    impulse_rise_pct: float,
) -> float:
    if six_ma_spread_pct <= 8.0:
        spread_points = 20.0
    elif six_ma_spread_pct <= 10.0:
        spread_points = 15.0
    elif six_ma_spread_pct <= 12.0:
        spread_points = 10.0
    elif six_ma_spread_pct <= 15.0:
        spread_points = 5.0
    else:
        spread_points = 0.0
    score = (
        min(max(float(box_days), 0.0), 40.0) / 40.0 * 8.0
        + max(0.0, 15.0 - min(float(box_range_pct), 15.0)) / 15.0 * 7.0
        + min(max(float(event_volume_ratio), 0.0), 3.0) / 3.0 * 15.0
        + (10.0 if open_inside_box else 0.0)
        + spread_points
        + min(max(int(bullish_order_count), 0), 5) * 3.0
        + min(max(int(ma60_rising_streak), 0), 20) / 20.0 * 8.0
        + min(max(int(ma120_rising_streak), 0), 10) / 10.0 * 7.0
        + (5.0 if impulse_mode == "consecutive_limit_up" else 3.0)
        + min(max(float(impulse_rise_pct), 0.0), 30.0) / 30.0 * 5.0
    )
    return float(min(max(score, 0.0), 100.0))


def select_unique_best_events(events: pd.DataFrame) -> pd.DataFrame:
    rank = {"A_严格优选": 1, "B_主观察": 2, "C_仅结构符合": 3}
    result = events.copy()
    result["quality_rank"] = result["quality_tier"].map(rank).fillna(99).astype(int)
    if "similarity_score" not in result:
        result["similarity_score"] = result["event_volume_ratio"]
    result = (
        result.sort_values(
            ["ts_code", "quality_rank", "similarity_score", "event_volume_ratio", "event_date"],
            ascending=[True, True, False, False, True],
            kind="mergesort",
        )
        .drop_duplicates("ts_code", keep="first")
        .sort_values(["quality_rank", "similarity_score", "event_date", "ts_code"], ascending=[True, False, True, True], kind="mergesort")
        .reset_index(drop=True)
    )
    result["display_order"] = np.arange(1, len(result) + 1)
    return result


def prepare_stock_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["trade_date"] = result["trade_date"].astype(str).str.replace("-", "", regex=False).str.split(".").str[0]
    result = result.sort_values("trade_date", kind="mergesort").drop_duplicates("trade_date", keep="last").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg"):
        if column not in result:
            continue
        result[column] = pd.to_numeric(result[column], errors="coerce")
    close = result["close"]
    for period in (5, 10, 20, 30, 60, 120):
        result[f"ma{period}"] = close.rolling(period, min_periods=period).mean()
    result["ma60_rising_streak"] = compute_rising_streak(result["ma60"])
    result["ma120_rising_streak"] = compute_rising_streak(result["ma120"])
    ma_columns = ["ma5", "ma10", "ma20", "ma30", "ma60", "ma120"]
    moving = result[ma_columns]
    result["six_ma_spread_pct"] = 100.0 * (moving.max(axis=1) - moving.min(axis=1)) / moving.mean(axis=1)
    comparisons = [
        result["ma5"].ge(result["ma10"]),
        result["ma10"].ge(result["ma20"]),
        result["ma20"].ge(result["ma30"]),
        result["ma30"].ge(result["ma60"]),
        result["ma60"].ge(result["ma120"]),
    ]
    result["bullish_order_count"] = sum(item.astype(int) for item in comparisons)
    result["full_bullish_alignment"] = result["bullish_order_count"].eq(5)
    result["prior_20d_avg_raw_vol"] = result["raw_vol"].shift(1).rolling(20, min_periods=20).mean()
    result["event_volume_ratio"] = result["raw_vol"] / result["prior_20d_avg_raw_vol"]
    return result


def scan_stock_events(
    frame: pd.DataFrame,
    *,
    ts_code: str,
    stock_name: str,
    event_start: str,
    event_end: str,
    box_min_days: int = 20,
    box_max_days: int = 120,
    box_max_range_pct: float = 15.0,
    new_high_lookback: int = 60,
    event_big_bull_return_pct: float = EVENT_BIG_BULL_RETURN_PCT,
    event_big_bull_body_pct: float = EVENT_BIG_BULL_BODY_PCT,
    prebox_lookback: int = 30,
    prebox_signal_gap: int = PREBOX_MAX_SIGNAL_GAP,
    prebox_big_bull_return_pct: float = PREBOX_BIG_BULL_RETURN_PCT,
    prebox_big_bull_body_pct: float = PREBOX_BIG_BULL_BODY_PCT,
    prebox_min_rise_pct: float = 12.0,
    prebox_min_signal_volume_ratio: float = 1.3,
) -> pd.DataFrame:
    featured = prepare_stock_features(frame)
    if "is_limit_up" not in featured:
        featured["is_limit_up"] = False
    featured["is_limit_up"] = featured["is_limit_up"].fillna(False).astype(bool)
    dates = featured["trade_date"]
    candidate_positions = np.flatnonzero(dates.ge(str(event_start)) & dates.le(str(event_end)))
    records: list[dict[str, Any]] = []
    for position in candidate_positions:
        row = featured.iloc[int(position)]
        limit_up = bool(row["is_limit_up"])
        big_bull = is_big_bull(
            row,
            min_return_pct=event_big_bull_return_pct,
            min_body_pct=event_big_bull_body_pct,
            min_close_location=EVENT_BIG_BULL_CLOSE_LOCATION,
        )
        if not (limit_up or big_bull):
            continue
        if not is_sixty_day_close_breakout(featured, int(position), lookback=new_high_lookback):
            continue
        box = find_prebreakout_box(
            featured,
            event_position=int(position),
            min_days=box_min_days,
            max_days=box_max_days,
            max_range_pct=box_max_range_pct,
        )
        if box is None or float(row["close"]) <= float(box["box_high"]):
            continue
        impulse = detect_prebox_impulse(
            featured,
            box_start_position=int(box["box_start_position"]),
            lookback=prebox_lookback,
            max_signal_gap=prebox_signal_gap,
            min_big_bull_return_pct=prebox_big_bull_return_pct,
            min_big_bull_body_pct=prebox_big_bull_body_pct,
            min_rise_pct=prebox_min_rise_pct,
            min_signal_volume_ratio=prebox_min_signal_volume_ratio,
        )
        if not bool(impulse["prebox_impulse_pass"]):
            continue
        prior_high = float(
            pd.to_numeric(
                featured.iloc[int(position) - new_high_lookback : int(position)]["high"],
                errors="coerce",
            ).max()
        )
        open_inside = bool(float(box["box_low"]) <= float(row["open"]) <= float(box["box_high"]))
        pct_chg = pd.to_numeric(pd.Series([row.get("pct_chg", np.nan)]), errors="coerce").iloc[0]
        return_pct = float(pct_chg) if pd.notna(pct_chg) else 100.0 * (float(row["close"]) / float(row["pre_close"]) - 1.0)
        body_pct = 100.0 * (float(row["close"]) / float(row["open"]) - 1.0)
        close_location = (
            (float(row["close"]) - float(row["low"])) / (float(row["high"]) - float(row["low"]))
            if float(row["high"]) > float(row["low"]) else 1.0
        )
        quality = classify_setup(
            event_volume_ratio=float(row["event_volume_ratio"]),
            open_inside_box=open_inside,
            six_ma_spread_pct=float(row["six_ma_spread_pct"]),
            bullish_order_count=int(row["bullish_order_count"]),
            ma60_rising_streak=int(row["ma60_rising_streak"]),
            ma120_rising_streak=int(row["ma120_rising_streak"]),
        )
        similarity_score = compute_similarity_score(
            box_days=int(box["box_trading_days"]),
            box_range_pct=float(box["box_range_pct"]),
            event_volume_ratio=float(row["event_volume_ratio"]),
            open_inside_box=open_inside,
            six_ma_spread_pct=float(row["six_ma_spread_pct"]),
            bullish_order_count=int(row["bullish_order_count"]),
            ma60_rising_streak=int(row["ma60_rising_streak"]),
            ma120_rising_streak=int(row["ma120_rising_streak"]),
            impulse_mode=str(impulse["prebox_impulse_mode"]),
            impulse_rise_pct=float(impulse["prebox_impulse_rise_pct"]),
        )
        record: dict[str, Any] = {
            "ts_code": str(ts_code),
            "stock_name": str(stock_name),
            "event_date": str(row["trade_date"]),
            "event_position": int(position),
            "signal_type": "涨停" if limit_up else "大阳线",
            "is_limit_up": limit_up,
            "is_big_bull": big_bull,
            "event_return_pct": float(return_pct),
            "event_body_pct": float(body_pct),
            "event_close_location": float(close_location),
            "event_volume_ratio": float(row["event_volume_ratio"]),
            "new_high_lookback_days": int(new_high_lookback),
            "prior_new_high_reference": prior_high,
            "new_high_margin_pct": 100.0 * (float(row["close"]) / prior_high - 1.0),
            "box_breakout_margin_pct": 100.0 * (float(row["close"]) / float(box["box_high"]) - 1.0),
            "event_open_inside_box": open_inside,
            "event_low_touched_box": bool(float(row["low"]) <= float(box["box_high"])),
            "six_ma_spread_pct": float(row["six_ma_spread_pct"]),
            "bullish_order_count": int(row["bullish_order_count"]),
            "full_bullish_alignment": bool(row["full_bullish_alignment"]),
            "ma60_rising_streak": int(row["ma60_rising_streak"]),
            "ma120_rising_streak": int(row["ma120_rising_streak"]),
            "quality_tier": quality,
            "similarity_score": similarity_score,
            "match_band": "高匹配" if similarity_score >= 65.0 else ("中匹配" if similarity_score >= 55.0 else "结构候选"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "raw_vol": float(row["raw_vol"]),
        }
        for period in (5, 10, 20, 30, 60, 120):
            record[f"ma{period}"] = float(row[f"ma{period}"])
        record.update(box)
        record.update(impulse)
        impulse_start = int(record["prebox_impulse_start_position"])
        impulse_end = int(record["prebox_impulse_end_position"])
        record["prebox_impulse_start_date"] = str(featured.iloc[impulse_start]["trade_date"])
        record["prebox_impulse_end_date"] = str(featured.iloc[impulse_end]["trade_date"])
        for optional in ("limit_status", "limit_theme", "limit_desc"):
            record[optional] = str(row.get(optional, "") or "")
        records.append(record)
    return pd.DataFrame(records)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--history-start", default="20240101")
    parser.add_argument("--event-start", default="20260801")
    parser.add_argument("--event-end", default="20260930")
    parser.add_argument("--data-end", default="20260930")
    parser.add_argument("--metrics-only", action="store_true")
    parser.add_argument("--reuse-main", action="store_true")
    parser.add_argument("--reuse-sensitivity", action="store_true")
    return parser.parse_args(argv)


def _compact_date(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).replace("-", "").split(".")[0]


def format_period_label(event_start: object, event_end: object) -> str:
    start = _compact_date(event_start)
    end = _compact_date(event_end)
    if len(start) != 8 or len(end) != 8:
        raise ValueError("event window dates must use YYYYMMDD")
    start_year, start_month = int(start[:4]), int(start[4:6])
    end_year, end_month = int(end[:4]), int(end[4:6])
    if start_year == end_year and start_month == end_month:
        return f"{start_year}年{start_month}月"
    if start_year == end_year:
        return f"{start_year}年{start_month}—{end_month}月"
    return f"{start_year}年{start_month}月—{end_year}年{end_month}月"


def rank_table_page_count(stock_count: int, page_size: int = 8) -> int:
    return max(1, (max(int(stock_count), 0) + page_size - 1) // page_size)


def _fmt_date(value: object) -> str:
    text = _compact_date(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 else text


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_or_fetch_inputs(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    daily_path = output / "daily_input_qfq_rawvol.csv.gz"
    basic_path = output / "stock_basic.csv"
    kpl_path = output / "kpl_limit_up.csv"
    if not (daily_path.exists() and basic_path.exists() and kpl_path.exists()):
        from coreClient.data_provider import get_basic, get_day, get_kpl_list

        data_end = getattr(args, "data_end", args.event_end)
        qfq = get_day(
            start_date=args.history_start,
            end_date=data_end,
            qfq=True,
            source="database_only",
        )
        raw = get_day(
            start_date=args.history_start,
            end_date=data_end,
            qfq=False,
            source="database_only",
        )
        for frame in (qfq, raw):
            frame["trade_date"] = frame["trade_date"].map(_compact_date)
            frame["ts_code"] = frame["ts_code"].astype(str)
        suffixes = (".SH", ".SZ", ".BJ")
        qfq = qfq.loc[qfq["ts_code"].str.endswith(suffixes)].copy()
        raw = raw.loc[raw["ts_code"].str.endswith(suffixes), ["ts_code", "trade_date", "vol", "amount"]].rename(
            columns={"vol": "raw_vol", "amount": "raw_amount"}
        )
        if qfq.duplicated(["ts_code", "trade_date"]).any() or raw.duplicated(["ts_code", "trade_date"]).any():
            raise ValueError("duplicate stock-date rows in data_provider output")
        daily = qfq.drop(columns=["vol", "amount"]).merge(
            raw,
            on=["ts_code", "trade_date"],
            validate="one_to_one",
        )
        basic = get_basic(list_status=None, source="database_only")
        kpl = get_kpl_list(
            start_date=args.history_start,
            end_date=data_end,
            tags="涨停",
            source="database_only",
        )
        daily.to_csv(daily_path, index=False, compression="gzip")
        basic.to_csv(basic_path, index=False, encoding="utf-8-sig")
        kpl.to_csv(kpl_path, index=False, encoding="utf-8-sig")
    daily = pd.read_csv(daily_path, dtype={"ts_code": str, "trade_date": str})
    basic = pd.read_csv(basic_path, dtype={"ts_code": str})
    kpl = pd.read_csv(kpl_path, dtype={"ts_code": str, "trade_date": str})
    daily["trade_date"] = daily["trade_date"].map(_compact_date)
    kpl["trade_date"] = kpl["trade_date"].map(_compact_date)
    return daily, basic, kpl


def scan_market(
    daily: pd.DataFrame,
    basic: pd.DataFrame,
    kpl: pd.DataFrame,
    args: argparse.Namespace,
    *,
    box_min_days: int = 20,
    box_max_range_pct: float = 15.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    kpl = (
        kpl.sort_values(["trade_date", "ts_code"], kind="mergesort")
        .drop_duplicates(["ts_code", "trade_date"], keep="last")
        .copy()
    )
    limit_dates = {
        str(code): set(group["trade_date"].astype(str))
        for code, group in kpl.groupby("ts_code", sort=False)
    }
    name_map = dict(zip(basic["ts_code"].astype(str), basic["name"].astype(str)))
    name_map.update(dict(zip(kpl["ts_code"].astype(str), kpl["name"].astype(str))))
    event_parts: list[pd.DataFrame] = []
    for code, group in daily.groupby("ts_code", sort=False):
        stock = group.copy()
        stock["is_limit_up"] = stock["trade_date"].isin(limit_dates.get(str(code), set()))
        events = scan_stock_events(
            stock,
            ts_code=str(code),
            stock_name=name_map.get(str(code), str(code)),
            event_start=args.event_start,
            event_end=args.event_end,
            box_min_days=box_min_days,
            box_max_range_pct=box_max_range_pct,
        )
        if not events.empty:
            event_parts.append(events)
    if not event_parts:
        raise ValueError("no structural box breakouts found")
    all_events = pd.concat(event_parts, ignore_index=True)
    for column in ("limit_status", "limit_theme", "limit_desc"):
        if column in all_events:
            all_events = all_events.drop(columns=column)
    event_meta = kpl[["ts_code", "trade_date", "status", "theme", "lu_desc"]].rename(
        columns={
            "trade_date": "event_date",
            "status": "limit_status",
            "theme": "limit_theme",
            "lu_desc": "limit_desc",
        }
    )
    all_events = all_events.merge(
        event_meta,
        on=["ts_code", "event_date"],
        how="left",
        validate="many_to_one",
    )
    basic_columns = [column for column in ("ts_code", "industry", "market", "list_date") if column in basic.columns]
    all_events = all_events.merge(
        basic[basic_columns].drop_duplicates("ts_code", keep="last"),
        on="ts_code",
        how="left",
        validate="many_to_one",
    )
    unique = select_unique_best_events(all_events)
    unique["match_band"] = np.select(
        [unique["similarity_score"].ge(65.0), unique["similarity_score"].ge(55.0)],
        ["高匹配", "中匹配"],
        default="结构候选",
    )
    return all_events, unique


def create_event_chart(frame: pd.DataFrame, event: pd.Series, output: Path) -> None:
    _register_fonts()
    featured = prepare_stock_features(frame)
    event_position = int(event["event_position"])
    impulse_start = int(event["prebox_impulse_start_position"])
    lower = max(0, impulse_start - 15)
    upper = min(len(featured) - 1, event_position + 20)
    view = featured.iloc[lower : upper + 1].copy().reset_index(drop=True)
    event_x = event_position - lower
    box_start_x = int(event["box_start_position"]) - lower
    box_end_x = int(event["box_end_position"]) - lower
    impulse_start_x = impulse_start - lower
    impulse_end_x = int(event["prebox_impulse_end_position"]) - lower

    fig = plt.figure(figsize=(12.5, 6.2), dpi=165, facecolor="white")
    grid = fig.add_gridspec(4, 1, height_ratios=[3.5, 0.05, 1.0, 0.05], hspace=0.04)
    ax = fig.add_subplot(grid[0, 0])
    vol_ax = fig.add_subplot(grid[2, 0], sharex=ax)
    _draw_candlesticks(ax, view)
    palette = {5: RED, 10: ORANGE, 20: GOLD, 30: TEAL, 60: BLUE, 120: NAVY}
    x = np.arange(len(view))
    for period in (5, 10, 20, 30, 60, 120):
        ax.plot(x, view[f"ma{period}"], linewidth=1.05 if period < 60 else 1.35, color=f"#{palette[period]}", label=f"MA{period}")
    ax.axvspan(impulse_start_x - 0.5, impulse_end_x + 0.5, color=f"#{ORANGE}", alpha=0.12, label="箱体前放量拉升")
    rectangle = Rectangle(
        (box_start_x - 0.5, float(event["box_low"])),
        box_end_x - box_start_x + 1,
        float(event["box_high"]) - float(event["box_low"]),
        facecolor=f"#{CYAN}", edgecolor=f"#{BLUE}", linewidth=1.2, alpha=0.11,
    )
    ax.add_patch(rectangle)
    ax.axvline(event_x, color=f"#{RED}", linewidth=1.5, linestyle="--")
    ax.scatter([event_x], [float(event["close"])], color=f"#{RED}", s=34, zorder=6)
    ax.axhline(float(event["prior_new_high_reference"]), color=f"#{MUTED}", linewidth=0.9, linestyle=":", label="此前60日最高价")
    ax.annotate(
        f"{event['signal_type']}突破\n{_fmt_date(event['event_date'])}",
        xy=(event_x, float(event["close"])), xytext=(10, 20), textcoords="offset points",
        fontsize=8.2, color=f"#{RED}", fontweight="bold",
        arrowprops={"arrowstyle": "->", "color": f"#{RED}", "lw": 0.8},
    )
    ax.set_ylabel("前复权价格", fontsize=8)
    ax.grid(axis="y", color="#E7EDF3", linewidth=0.6)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper left", ncol=5, frameon=False, fontsize=6.5)
    ax.tick_params(axis="x", labelbottom=False)

    colors = np.where(view["close"].ge(view["open"]), "#E85D75", "#2CA58D")
    vol_ax.bar(x, view["raw_vol"], color=colors, width=0.74, alpha=0.76)
    vol_ax.plot(x, view["raw_vol"].shift(1).rolling(20, min_periods=5).mean(), color=f"#{NAVY}", linewidth=0.9, label="此前20日均量")
    vol_ax.axvline(event_x, color=f"#{RED}", linewidth=1.2, linestyle="--")
    vol_ax.set_ylabel("原始成交量", fontsize=7)
    vol_ax.grid(axis="y", color="#E7EDF3", linewidth=0.5)
    vol_ax.spines[["top", "right"]].set_visible(False)
    step = max(1, len(view) // 9)
    ticks = sorted(set(list(range(0, len(view), step)) + [event_x, len(view) - 1]))
    vol_ax.set_xticks(ticks)
    vol_ax.set_xticklabels([_fmt_date(view.iloc[index]["trade_date"])[5:] for index in ticks], fontsize=6.5)
    fig.suptitle(
        f"{event['stock_name']}  {event['ts_code']}｜{event['match_band']}｜相似度 {float(event['similarity_score']):.1f}",
        x=0.01, y=0.995, ha="left", va="top", fontsize=11.5, color=f"#{DARK_TEXT}", fontweight="bold",
    )
    fig.text(
        0.99, 0.99,
        f"箱体 {int(event['box_trading_days'])}日 / {float(event['box_range_pct']):.1f}%｜六MA离差 {float(event['six_ma_spread_pct']):.1f}%｜放量 {float(event['event_volume_ratio']):.1f}×",
        ha="right", va="top", fontsize=7.7, color=f"#{MUTED}",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def create_summary_chart(events: pd.DataFrame, output: Path) -> None:
    _register_fonts()
    names = events["stock_name"].astype(str).tolist()
    x = np.arange(len(events))
    fig, axes = plt.subplots(2, 2, figsize=(12.2, 6.2), dpi=165, facecolor="white")
    axes[0, 0].bar(x, events["similarity_score"], color=f"#{BLUE}")
    axes[0, 0].axhline(65, color=f"#{RED}", linestyle="--", linewidth=1, label="高匹配65")
    axes[0, 0].set_title("综合相似度（仅排序）", fontsize=10, fontweight="bold")
    axes[0, 0].legend(frameon=False, fontsize=7)
    axes[0, 1].bar(x, events["six_ma_spread_pct"], color=f"#{GOLD}")
    axes[0, 1].axhline(10, color=f"#{RED}", linestyle="--", linewidth=1, label="10%")
    axes[0, 1].set_title("突破日六均线最大离差", fontsize=10, fontweight="bold")
    axes[0, 1].legend(frameon=False, fontsize=7)
    axes[1, 0].bar(x - 0.18, events["ma60_rising_streak"], width=0.36, color=f"#{TEAL}", label="MA60")
    axes[1, 0].bar(x + 0.18, events["ma120_rising_streak"], width=0.36, color=f"#{NAVY}", label="MA120")
    axes[1, 0].set_title("突破日长均线连续上涨", fontsize=10, fontweight="bold")
    axes[1, 0].legend(frameon=False, fontsize=7)
    axes[1, 1].bar(x - 0.18, events["box_trading_days"], width=0.36, color=f"#{BLUE}", label="箱体日数")
    axes[1, 1].bar(x + 0.18, events["box_range_pct"], width=0.36, color=f"#{ORANGE}", label="振幅%")
    axes[1, 1].set_title("箱体时长与振幅", fontsize=10, fontweight="bold")
    axes[1, 1].legend(frameon=False, fontsize=7)
    for ax in axes.ravel():
        ax.set_xticks(x)
        ax.set_xticklabels(names, rotation=42, ha="right", fontsize=6.1)
        ax.grid(axis="y", color="#E7EDF3", linewidth=0.5)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="y", labelsize=7)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)


def _add_study_footer(slide: Any, slide_no: int, data_end: str) -> None:
    _add_text(
        slide,
        f"前复权日线｜截至{_fmt_date(data_end)}｜相似结构观察，不构成买入信号",
        0.42, 7.18, 9.7, 0.18, size=7.5, color=MUTED,
    )
    _add_text(slide, str(slide_no), 12.35, 7.18, 0.5, 0.18, size=7.5, color=MUTED, align=PP_ALIGN.RIGHT)


def _add_rank_table_slide(
    prs: Presentation,
    page: pd.DataFrame,
    title: str,
    slide_no: int,
    data_end: str,
) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_background(slide, LIGHT)
    _add_header(slide, title, "硬条件全通过｜按相似度排序")
    columns = ["序", "代码", "名称", "突破日", "信号", "分数", "箱体", "六MA", "长均线"]
    table_shape = slide.shapes.add_table(len(page) + 1, len(columns), Inches(0.48), Inches(1.08), Inches(12.36), Inches(5.68))
    table = table_shape.table
    widths = [0.45, 1.15, 1.20, 1.18, 0.82, 0.70, 1.34, 1.10, 2.25]
    for index, width in enumerate(widths):
        table.columns[index].width = Inches(width)
    for index, value in enumerate(columns):
        table.cell(0, index).text = value
    for row_index, (_, event) in enumerate(page.iterrows(), start=1):
        values = [
            str(int(event["display_order"])),
            str(event["ts_code"]),
            str(event["stock_name"]),
            _fmt_date(event["event_date"]),
            str(event["signal_type"]),
            f"{float(event['similarity_score']):.1f}",
            f"{int(event['box_trading_days'])}日 / {float(event['box_range_pct']):.1f}%",
            f"{float(event['six_ma_spread_pct']):.1f}% / {int(event['bullish_order_count'])}项",
            f"MA60 {int(event['ma60_rising_streak'])}日｜MA120 {int(event['ma120_rising_streak'])}日",
        ]
        for column_index, value in enumerate(values):
            table.cell(row_index, column_index).text = value
    _style_table(table, header_size=8.5, body_size=7.4)
    _add_study_footer(slide, slide_no, data_end)


def create_presentation(
    events: pd.DataFrame,
    summary_chart: Path,
    chart_map: dict[str, Path],
    output: Path,
    *,
    event_start: str,
    event_end: str,
    data_end: str,
    universe_stocks: int,
) -> Path:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    period_label = format_period_label(event_start, event_end)

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_background(slide, NAVY)
    accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(0.18), Inches(7.5))
    accent.fill.solid(); accent.fill.fore_color.rgb = RGBColor.from_string(BLUE); accent.line.fill.background()
    _add_text(slide, f"{period_label}全市场", 1.02, 1.10, 9.2, 0.55, size=18, color=CYAN, bold=True)
    _add_text(slide, "箱体突破 · 60日新高", 1.02, 1.78, 10.7, 0.86, size=35, color=WHITE, bold=True)
    _add_text(slide, "前置放量拉升 → 20日以上窄箱体 → 涨停/放量大阳突破", 1.05, 2.95, 10.9, 0.54, size=17, color="DCEBFA")
    _add_text(slide, f"全A {universe_stocks:,}只｜主口径命中 {len(events)}只｜前复权日线｜截至{_fmt_date(data_end)}", 1.05, 4.08, 10.9, 0.38, size=12.5, color=WHITE)
    _add_text(slide, "均线排列、长均线上升、平台内启动与粘合作为排序偏好，不作为硬剔除条件", 1.05, 4.68, 10.9, 0.54, size=12, color=GOLD)
    _add_text(slide, "历史结构筛选，仅供研究，不构成投资建议", 1.05, 6.72, 6.5, 0.24, size=8.5, color="94A3B8")

    slide = prs.slides.add_slide(prs.slide_layouts[6]); _set_background(slide, LIGHT)
    _add_header(slide, "研究口径", "无未来数据｜实际交易日")
    method_text = (
        "硬条件\n"
        f"① 事件日在{_fmt_date(event_start)}至{_fmt_date(event_end)}；涨停来自数据库涨停榜，或收涨≥6%、close/open实体≥5%、收盘位于当日振幅上部30%的大阳线。\n"
        "② 收盘同时高于此前60个交易日最高价；这是比‘最高收盘’更严格的盘中高点基准。\n"
        "③ 突破前紧邻箱体≥20日、整体最高价/最低价振幅≤15%，事件收盘突破箱体上沿。\n"
        f"④ {PREBOX_RULE_CN}。"
    )
    preference_text = (
        "偏好条件（只排序）\n"
        "• 突破日开盘仍在箱体内；突破量比越高越好。\n"
        "• MA5/10/20/30/60/120最大离差越小越好。\n"
        "• 五组相邻均线多头关系满足项越多越好。\n"
        "• MA60、MA120连续上涨越久越好；持平或下降即把连续段清零。\n"
        "• 综合相似度用于排序，不改变硬条件命中资格。"
    )
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.55), Inches(1.05), Inches(7.55), Inches(5.75)); shape.fill.solid(); shape.fill.fore_color.rgb = RGBColor.from_string(WHITE); shape.line.color.rgb = RGBColor.from_string(MID)
    shape2 = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8.35), Inches(1.05), Inches(4.43), Inches(5.75)); shape2.fill.solid(); shape2.fill.fore_color.rgb = RGBColor.from_string(WHITE); shape2.line.color.rgb = RGBColor.from_string(MID)
    _add_text(slide, method_text, 0.82, 1.34, 7.02, 5.04, size=13.2, color=DARK_TEXT)
    _add_text(slide, preference_text, 8.62, 1.34, 3.90, 5.03, size=12.1, color=DARK_TEXT)
    _add_study_footer(slide, 2, data_end)

    limit_count = int(events["is_limit_up"].sum())
    inside_count = int(events["event_open_inside_box"].sum())
    cohesive_count = int(events["six_ma_spread_pct"].le(10).sum())
    ma60_count = int(events["ma60_rising_streak"].ge(10).sum())
    ma120_count = int(events["ma120_rising_streak"].ge(5).sum())
    strict_count = int((events["event_open_inside_box"] & events["six_ma_spread_pct"].le(10) & events["bullish_order_count"].ge(4) & events["ma60_rising_streak"].ge(10) & events["ma120_rising_streak"].ge(5) & events["event_volume_ratio"].ge(1.3)).sum())
    slide = prs.slides.add_slide(prs.slide_layouts[6]); _set_background(slide, LIGHT)
    _add_header(slide, "结果总览", f"{len(events)}只全部满足四项硬条件")
    cards = [
        (str(len(events)), "股票级结构事件", BLUE),
        (f"{limit_count} / {len(events) - limit_count}", "涨停 / 非涨停大阳", RED),
        (str(int(events["match_band"].eq("高匹配").sum())), "高匹配（分数≥65）", TEAL),
        (str(strict_count), "全部偏好同时满足", ORANGE),
    ]
    for index, (value, label, color) in enumerate(cards):
        _add_card(slide, 0.55 + index * 3.12, 1.10, 2.78, 1.35, value, label, accent=color)
    _add_card(slide, 0.72, 3.02, 2.25, 1.28, f"{inside_count}/{len(events)}", "开盘仍在箱体内", accent=BLUE)
    _add_card(slide, 3.12, 3.02, 2.25, 1.28, f"{cohesive_count}/{len(events)}", "六MA离差≤10%", accent=GOLD)
    _add_card(slide, 5.52, 3.02, 2.25, 1.28, f"{int(events['bullish_order_count'].ge(4).sum())}/{len(events)}", "多头关系≥4项", accent=TEAL)
    _add_card(slide, 7.92, 3.02, 2.25, 1.28, f"{ma60_count}/{len(events)}", "MA60上涨≥10日", accent=CYAN)
    _add_card(slide, 10.32, 3.02, 2.25, 1.28, f"{ma120_count}/{len(events)}", "MA120上涨≥5日", accent=ORANGE)
    _add_text(slide, f"结论：硬结构筛出{len(events)}只；全部均线与启动偏好同时满足者为{strict_count}只。主表仍保留全部硬结构事件，并用相似度排序，避免把偏好描述误当成必要条件。", 0.82, 5.05, 11.65, 1.02, size=14.2, color=NAVY, bold=True, align=PP_ALIGN.CENTER)
    _add_study_footer(slide, 3, data_end)

    table_pages = rank_table_page_count(len(events))
    for table_index in range(table_pages):
        start = table_index * 8
        end = min(start + 8, len(events))
        _add_rank_table_slide(
            prs,
            events.iloc[start:end],
            f"候选排序 {start + 1}—{end}",
            4 + table_index,
            data_end,
        )

    slide = prs.slides.add_slide(prs.slide_layouts[6]); _set_background(slide, LIGHT)
    _add_header(slide, "结构分布", "分数不等于收益预测")
    slide.shapes.add_picture(str(summary_chart), Inches(0.42), Inches(0.92), width=Inches(12.48))
    summary_slide_no = 4 + table_pages
    _add_study_footer(slide, summary_slide_no, data_end)

    slide = prs.slides.add_slide(prs.slide_layouts[6]); _set_background(slide, LIGHT)
    _add_header(slide, "个股页阅读方式", "下一页起按相似度递减")
    guide = (
        "橙色阴影：箱体前放量拉升区间\n\n"
        "蓝色矩形：突破前紧邻窄箱体，至少20个实际交易日，振幅≤15%\n\n"
        "红色虚线：正式突破日；标记为涨停或放量大阳，并创前60日高点新高\n\n"
        "灰色点线：突破日前60个交易日的最高价参考线\n\n"
        "成交量：使用不复权原始量；价格和MA5/10/20/30/60/120均为前复权\n\n"
        "高匹配/中匹配/结构候选：仅是规则化相似度分组，不是交易评级"
    )
    _add_text(slide, guide, 1.05, 1.20, 11.15, 4.95, size=16.0, color=DARK_TEXT)
    _add_text(slide, "相似度100分：箱体15 + 突破量15 + 平台内启动10 + 六MA粘合20 + 多头排列15 + MA60/MA120趋势15 + 前置拉升10。", 1.05, 6.24, 11.15, 0.44, size=10.5, color=BLUE, bold=True, align=PP_ALIGN.CENTER)
    guide_slide_no = summary_slide_no + 1
    _add_study_footer(slide, guide_slide_no, data_end)

    for offset, (_, event) in enumerate(events.iterrows(), start=guide_slide_no + 1):
        slide = prs.slides.add_slide(prs.slide_layouts[6]); _set_background(slide, LIGHT)
        _add_header(slide, f"{int(event['display_order']):02d}｜{event['stock_name']}  {event['ts_code']}", f"{event['match_band']}｜{event['signal_type']}｜{_fmt_date(event['event_date'])}")
        chart_path = chart_map[str(event["ts_code"])]
        slide.shapes.add_picture(str(chart_path), Inches(0.38), Inches(1.02), width=Inches(9.25))
        band_color = TEAL if event["match_band"] == "高匹配" else (BLUE if event["match_band"] == "中匹配" else ORANGE)
        _add_card(slide, 9.86, 1.02, 2.98, 1.10, f"{float(event['similarity_score']):.1f}", f"{event['match_band']}｜相似度", accent=band_color)
        metric_text = (
            f"突破：{float(event['event_return_pct']):.1f}%｜量比 {float(event['event_volume_ratio']):.2f}×\n"
            f"60日新高裕度：{float(event['new_high_margin_pct']):.1f}%\n"
            f"箱体：{int(event['box_trading_days'])}日｜振幅 {float(event['box_range_pct']):.1f}%\n"
            f"箱体前拉升：{float(event['prebox_impulse_rise_pct']):.1f}%｜涨停/大阳 {int(event['prebox_limit_up_count'])}/{int(event['prebox_big_bull_count'])}\n"
            f"平台内开盘：{'是' if bool(event['event_open_inside_box']) else '否'}\n"
            f"六MA离差：{float(event['six_ma_spread_pct']):.1f}%\n"
            f"多头关系：{int(event['bullish_order_count'])}/5\n"
            f"MA60连续上升：{int(event['ma60_rising_streak'])}日\n"
            f"MA120连续上升：{int(event['ma120_rising_streak'])}日"
        )
        _add_text(slide, metric_text, 9.95, 2.38, 2.82, 3.05, size=11.0, color=DARK_TEXT)
        flags = []
        if float(event["six_ma_spread_pct"]) <= 10: flags.append("均线较粘合")
        if int(event["bullish_order_count"]) >= 4: flags.append("多头排列较完整")
        if int(event["ma60_rising_streak"]) >= 10: flags.append("MA60趋势明确")
        if int(event["ma120_rising_streak"]) >= 5: flags.append("MA120趋势明确")
        if bool(event["event_open_inside_box"]): flags.append("平台内启动")
        if not flags: flags.append("仅硬结构达标")
        _add_text(slide, "观察点｜" + "、".join(flags), 9.95, 5.64, 2.82, 0.76, size=10.2, color=band_color, bold=True)
        _add_study_footer(slide, offset, data_end)

    output.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output)
    return output


def build_preference_sensitivity(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    tests = [
        ("六MA离差", "≤6%", events["six_ma_spread_pct"].le(6)),
        ("六MA离差", "≤8%", events["six_ma_spread_pct"].le(8)),
        ("六MA离差", "≤10%", events["six_ma_spread_pct"].le(10)),
        ("六MA离差", "≤12%", events["six_ma_spread_pct"].le(12)),
        ("突破量比", "≥1.3×", events["event_volume_ratio"].ge(1.3)),
        ("突破量比", "≥1.5×", events["event_volume_ratio"].ge(1.5)),
        ("多头关系", "≥4/5", events["bullish_order_count"].ge(4)),
        ("MA60上涨", "≥5日", events["ma60_rising_streak"].ge(5)),
        ("MA60上涨", "≥10日", events["ma60_rising_streak"].ge(10)),
        ("MA120上涨", "≥1日", events["ma120_rising_streak"].ge(1)),
        ("MA120上涨", "≥5日", events["ma120_rising_streak"].ge(5)),
        ("启动位置", "开盘在箱体内", events["event_open_inside_box"].astype(bool)),
    ]
    for dimension, threshold, mask in tests:
        rows.append({"dimension": dimension, "threshold": threshold, "retained_stocks": int(mask.sum()), "base_stocks": len(events), "retention_pct": float(100.0 * mask.mean())})
    return pd.DataFrame(rows)


def write_report(
    events: pd.DataFrame,
    sensitivity: pd.DataFrame,
    hard_sensitivity: pd.DataFrame,
    output: Path,
    *,
    event_start: str,
    event_end: str,
    data_end: str,
    daily_rows: int,
    universe_stocks: int,
    event_trading_days: int,
) -> None:
    top = events.head(10)
    period_label = format_period_label(event_start, event_end)
    strict_count = int((events["event_open_inside_box"] & events["six_ma_spread_pct"].le(10) & events["bullish_order_count"].ge(4) & events["ma60_rising_streak"].ge(10) & events["ma120_rising_streak"].ge(5) & events["event_volume_ratio"].ge(1.3)).sum())
    lines = [
        f"# {period_label}全市场箱体突破60日新高研究",
        "",
        "## 结果",
        f"- 全市场输入：{universe_stocks:,}只A股，{daily_rows:,}条日线；研究期{event_trading_days}个实际交易日。",
        f"- 主口径命中：{len(events)}只股票；其中涨停{int(events['is_limit_up'].sum())}只、非涨停大阳{int((~events['is_limit_up']).sum())}只。",
        f"- 相似度分组：高匹配{int(events['match_band'].eq('高匹配').sum())}只、中匹配{int(events['match_band'].eq('中匹配').sum())}只、结构候选{int(events['match_band'].eq('结构候选').sum())}只。",
        f"- 如果把全部均线和启动偏好同时硬编码，命中{strict_count}只；主表仍仅把它们用于排序，避免把偏好描述误当成事件身份。",
        "",
        "## 相似度前10",
        "",
        "| 排名 | 代码 | 名称 | 突破日 | 信号 | 分数 | 箱体 | 六MA离差 | MA60/MA120上涨 |",
        "|---:|---|---|---|---|---:|---|---:|---|",
    ]
    for _, row in top.iterrows():
        lines.append(
            f"| {int(row['display_order'])} | {row['ts_code']} | {row['stock_name']} | {_fmt_date(row['event_date'])} | {row['signal_type']} | {float(row['similarity_score']):.1f} | {int(row['box_trading_days'])}日/{float(row['box_range_pct']):.1f}% | {float(row['six_ma_spread_pct']):.1f}% | {int(row['ma60_rising_streak'])}/{int(row['ma120_rising_streak'])}日 |"
        )
    lines.extend([
        "",
        "## 主口径",
        "1. 日线来自 `coreClient.data_provider` 且使用 `source=\"database_only\"`；价格和均线前复权，成交量为原始量。",
        f"2. 突破日在{_fmt_date(event_start)}至{_fmt_date(event_end)}。涨停由数据库涨停榜确认；大阳为收涨≥6%、close/open实体≥5%、收盘位置≥0.70。",
        "3. 收盘高于此前60个实际交易日最高价。",
        "4. 突破前紧邻箱体至少20日、至多120日；整体高低振幅 `(最高价/最低价-1)` ≤15%，且事件收盘高于箱体上沿。",
        f"5. {PREBOX_RULE_CN}。",
        "6. 均线偏好不硬筛：六MA离差、五项多头次序、MA60/MA120严格连续上升日数、平台内开盘、事件量比进入排序。",
        "7. 股票级去重：先按A/B/C质量层级，再按相似度、事件量比降序，最后按事件日期、代码升序；本次事件表恰为一股一事件，去重未改变行数。",
        "",
        "## 关键限制",
        "- 这是历史形态筛选，不是收益预测，也未计算事件后的交易结果。",
        "- 涨停识别优先使用数据库涨停榜，因此已覆盖主板、创业板、科创板、北交所和ST的不同涨跌停制度；非涨停大阳另用统一量价规则。",
        "- 综合相似度的权重具有研究者主观性，只用于安排图册顺序，不改变硬条件资格。",
        f"- 本地行情截止{_fmt_date(data_end)}，报告描述为截至该日的2026 YTD研究，不代表完整2026年。",
        "",
        "## 箱体阈值单因素敏感性（每一行均从冻结日线重新识别）",
        "",
        hard_sensitivity.to_markdown(index=False),
        "",
        f"## 偏好阈值留存（在主口径{len(events)}只内部）",
        "",
        sensitivity.to_markdown(index=False),
    ])
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_outputs(pptx_path: Path, pdf_path: Path, events: pd.DataFrame, chart_paths: list[Path]) -> dict[str, Any]:
    prs = Presentation(pptx_path)
    slide_text = "\n".join(shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, "text"))
    missing_codes = [code for code in events["ts_code"].astype(str) if code not in slide_text]
    image_hashes = [_sha256(path) for path in chart_paths]
    picture_count = sum(1 for slide in prs.slides for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE)
    import fitz
    with fitz.open(pdf_path) as document:
        pdf_pages = len(document)
    expected_pages = 5 + rank_table_page_count(len(events)) + len(events)
    return {
        "pptx_pages": len(prs.slides),
        "pdf_pages": pdf_pages,
        "expected_pages": expected_pages,
        "missing_stock_codes": missing_codes,
        "event_chart_count": len(chart_paths),
        "unique_event_chart_hashes": len(set(image_hashes)),
        "pptx_picture_count": picture_count,
        "passed": len(prs.slides) == pdf_pages == expected_pages and not missing_codes and len(chart_paths) == len(set(image_hashes)) == len(events),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    daily, basic, kpl = load_or_fetch_inputs(args)
    all_path = output / "all_structural_events.csv"
    ranked_path = output / "ranked_unique_candidates.csv"
    if args.reuse_main and all_path.exists() and ranked_path.exists():
        all_events = pd.read_csv(all_path, dtype={"ts_code": str, "event_date": str})
        events = pd.read_csv(ranked_path, dtype={"ts_code": str, "event_date": str})
    else:
        all_events, events = scan_market(daily, basic, kpl, args)
    all_events.to_csv(output / "all_structural_events.csv", index=False, encoding="utf-8-sig")
    events.to_csv(output / "ranked_unique_candidates.csv", index=False, encoding="utf-8-sig")
    events.loc[events["match_band"].isin(["高匹配", "中匹配"])].to_csv(output / "higher_similarity_candidates.csv", index=False, encoding="utf-8-sig")

    sensitivity_path = output / "box_threshold_sensitivity.csv"
    if args.reuse_sensitivity and sensitivity_path.exists():
        hard_sensitivity = pd.read_csv(sensitivity_path)
        hard_rows = hard_sensitivity.to_dict("records")
    else:
        hard_rows = []
        hard_variants = [(12.0, 20), (15.0, 20), (18.0, 20), (15.0, 25), (15.0, 30)]
        for range_limit, min_days in hard_variants:
            if range_limit == 15.0 and min_days == 20:
                variant_events = events
                variant_all = all_events
            else:
                variant_all, variant_events = scan_market(daily, basic, kpl, args, box_min_days=min_days, box_max_range_pct=range_limit)
            hard_rows.append({
                "box_range_limit_pct": range_limit,
                "box_min_trading_days": min_days,
                "event_rows": len(variant_all),
                "unique_stocks": len(variant_events),
                "limit_up_stocks": int(variant_events["is_limit_up"].sum()),
                "big_bull_only_stocks": int((~variant_events["is_limit_up"]).sum()),
            })
        hard_sensitivity = pd.DataFrame(hard_rows)
        hard_sensitivity.to_csv(sensitivity_path, index=False, encoding="utf-8-sig")
    preference_sensitivity = build_preference_sensitivity(events)
    preference_sensitivity.to_csv(output / "preference_threshold_retention.csv", index=False, encoding="utf-8-sig")

    if args.metrics_only:
        print(json.dumps({"stocks": len(events), "hard_sensitivity": hard_rows}, ensure_ascii=False))
        return 0

    chart_dir = output / "charts"
    chart_dir.mkdir(exist_ok=True)
    selected_codes = set(events["ts_code"].astype(str))
    chart_frames = {str(code): group.copy() for code, group in daily.loc[daily["ts_code"].isin(selected_codes)].groupby("ts_code", sort=False)}
    chart_map: dict[str, Path] = {}
    for _, event in events.iterrows():
        code = str(event["ts_code"])
        chart_path = chart_dir / f"{int(event['display_order']):02d}_{code.replace('.', '_')}_{event['event_date']}.png"
        create_event_chart(chart_frames[code], event, chart_path)
        chart_map[code] = chart_path
    summary_chart = output / "summary_distribution.png"
    create_summary_chart(events, summary_chart)

    actual_data_end = str(daily["trade_date"].max())
    universe_stocks = int(daily["ts_code"].nunique())
    event_trading_days = int(daily.loc[daily["trade_date"].between(args.event_start, args.event_end), "trade_date"].nunique())
    file_period = format_period_label(args.event_start, args.event_end).replace("—", "至")
    pptx_path = output / f"{file_period}全市场箱体突破60日新高研究.pptx"
    create_presentation(
        events,
        summary_chart,
        chart_map,
        pptx_path,
        event_start=args.event_start,
        event_end=args.event_end,
        data_end=actual_data_end,
        universe_stocks=universe_stocks,
    )
    pdf_path = convert_ppt_to_pdf(pptx_path, output)
    rendered_dir = output / "rendered_slides"
    slide_images = render_pdf(pdf_path, rendered_dir)
    contact_sheets = create_contact_sheets(slide_images, output / "contact_sheets", per_sheet=12)
    write_report(
        events,
        preference_sensitivity,
        hard_sensitivity,
        output / "report.md",
        event_start=args.event_start,
        event_end=args.event_end,
        data_end=actual_data_end,
        daily_rows=len(daily),
        universe_stocks=universe_stocks,
        event_trading_days=event_trading_days,
    )
    verification = verify_outputs(pptx_path, pdf_path, events, list(chart_map.values()))
    (output / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": {"event_start": args.event_start, "event_end": args.event_end, "history_start": args.history_start, "data_end": actual_data_end, "description": f"2026 YTD through {_fmt_date(actual_data_end)}"},
        "data_governance": {"provider": "coreClient.data_provider", "source": "database_only", "price_adjustment": "qfq", "volume": "raw_unadjusted"},
        "universe": {"daily_rows": len(daily), "stocks": universe_stocks, "event_period_trading_days": event_trading_days},
        "results": {"event_rows": len(all_events), "unique_stocks": len(events), "high_match": int(events["match_band"].eq("高匹配").sum()), "medium_match": int(events["match_band"].eq("中匹配").sum()), "structural_only": int(events["match_band"].eq("结构候选").sum())},
        "definitions": {
            "new_high": "event close > max high of prior 60 actual trading rows",
            "box": "immediately preceding 20-120 actual trading rows, range=(max high/min low-1)<=15%, choose longest qualifying window",
            "event": "database limit-up OR close return>=6%, close/open body>=5%, close location>=0.70",
            "prebox_impulse": PREBOX_RULE_CN,
            "stock_deduplication": "quality tier ascending, similarity score descending, event volume ratio descending, event date ascending, ts_code ascending",
            "similarity_score": "ranking only; box15, volume15, open-inside10, six-MA cohesion20, bullish order15, MA60/MA120 trend15, impulse10",
            "sensitivity_scope": "full re-identification for five one-factor box threshold variants; preference retention is conditional within the main sample"
        },
        "bias_disclosure": ["No future rows are used to identify an event.", "Post-event rows through the data cutoff may appear only as chart context.", "Similarity weights are researcher-defined and are not return forecasts.", f"The local database ends on {_fmt_date(actual_data_end)}; this is not a full-calendar-year 2026 study."],
        "files": {},
        "verification": verification,
        "contact_sheets": [str(path) for path in contact_sheets],
    }
    for path in [
        pptx_path,
        pdf_path,
        output / "daily_input_qfq_rawvol.csv.gz",
        output / "stock_basic.csv",
        output / "kpl_limit_up.csv",
        output / "all_structural_events.csv",
        output / "ranked_unique_candidates.csv",
        output / "higher_similarity_candidates.csv",
        output / "box_threshold_sensitivity.csv",
        output / "preference_threshold_retention.csv",
        output / "report.md",
    ]:
        metadata["files"][path.name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "stocks": len(events), "pptx": str(pptx_path), "pdf": str(pdf_path), "verification": verification}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
