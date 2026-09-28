#!/usr/bin/env python3
"""Build a future-isolated OHLCV packet for five-framework K-line analysis."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


MA_WINDOWS = (5, 10, 20, 30, 60, 120, 250)
VOL_WINDOWS = (5, 20, 60)
POSITION_WINDOWS = (20, 60, 120, 250)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--ts-code", required=True, help="Tushare code, for example 000565.SZ")
    parser.add_argument("--as-of", required=True, help="Last visible trade date, YYYYMMDD")
    parser.add_argument("--bars", type=int, default=750, help="Number of daily bars in the packet")
    parser.add_argument("--context-bars", type=int, default=1250, help="Daily bars used to build weekly/monthly context")
    parser.add_argument("--swing-order", type=int, default=3, help="Bars required on each side of a pivot")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.bars < 260:
        parser.error("--bars must be at least 260 so MA250 is meaningful")
    if args.context_bars < args.bars:
        parser.error("--context-bars must be greater than or equal to --bars")
    if args.swing_order < 1:
        parser.error("--swing-order must be positive")
    return args


def scalar(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if math.isnan(float(value)) or math.isinf(float(value)) else round(float(value), 6)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y%m%d")
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def records(frame: pd.DataFrame, columns: list[str] | None = None) -> list[dict[str, Any]]:
    selected = frame if columns is None else frame[columns]
    return [{key: scalar(value) for key, value in row.items()} for row in selected.to_dict("records")]


def load_data(project_root: Path, ts_code: str, as_of: str, context_bars: int) -> tuple[pd.DataFrame, str | None]:
    sys.path.insert(0, str(project_root))
    from coreClient.data_provider import get_basic, get_day  # pylint: disable=import-error,import-outside-toplevel

    as_of_ts = pd.to_datetime(as_of, format="%Y%m%d")
    start = (as_of_ts - pd.Timedelta(days=max(1900, context_bars * 3))).strftime("%Y%m%d")

    qfq = get_day(
        ts_code=ts_code,
        start_date=start,
        end_date=as_of,
        qfq=True,
        source="database",
    )
    raw = get_day(
        ts_code=ts_code,
        start_date=start,
        end_date=as_of,
        qfq=False,
        source="database",
    )
    if qfq is None or qfq.empty:
        raise RuntimeError(f"No qfq daily data for {ts_code} through {as_of}")
    if raw is None or raw.empty:
        raise RuntimeError(f"No raw daily data for {ts_code} through {as_of}")

    qfq = qfq.copy()
    raw = raw.copy()
    qfq["trade_date"] = qfq["trade_date"].astype(str).str.replace("-", "", regex=False)
    raw["trade_date"] = raw["trade_date"].astype(str).str.replace("-", "", regex=False)
    qfq = qfq[qfq["trade_date"] <= as_of].sort_values("trade_date")
    raw = raw[raw["trade_date"] <= as_of].sort_values("trade_date")

    price_columns = ["trade_date", "open", "high", "low", "close"]
    optional_price = [column for column in ("pre_close", "pct_chg") if column in qfq.columns]
    volume_columns = [column for column in ("trade_date", "vol", "amount") if column in raw.columns]
    frame = qfq[price_columns + optional_price].merge(raw[volume_columns], on="trade_date", how="inner")
    frame = frame.drop_duplicates("trade_date", keep="last").sort_values("trade_date").reset_index(drop=True)
    frame = frame.tail(context_bars).reset_index(drop=True)

    if frame.empty or frame.iloc[-1]["trade_date"] != as_of:
        latest = None if frame.empty else frame.iloc[-1]["trade_date"]
        raise RuntimeError(f"Latest available bar is {latest}, not requested as-of {as_of}")

    name = None
    try:
        basic = get_basic(source="database")
        if basic is not None and not basic.empty and {"ts_code", "name"}.issubset(basic.columns):
            matched = basic[basic["ts_code"] == ts_code]
            if not matched.empty:
                name = str(matched.iloc[0]["name"])
    except Exception:  # Static metadata is optional; OHLCV is not.
        name = None
    return frame, name


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    numeric = ["open", "high", "low", "close", "vol", "amount", "pre_close", "pct_chg"]
    for column in numeric:
        if column in data.columns:
            data[column] = pd.to_numeric(data[column], errors="coerce")

    prior_close = data["close"].shift(1)
    data["pct_chg_calc"] = data["close"].pct_change() * 100
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prior_close).abs(),
            (data["low"] - prior_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr14"] = true_range.rolling(14).mean()
    data["range_pct"] = (data["high"] - data["low"]) / prior_close * 100
    span = data["high"] - data["low"]
    data["close_location"] = np.where(span > 0, (data["close"] - data["low"]) / span, 0.5)

    for window in MA_WINDOWS:
        column = f"ma{window}"
        data[column] = data["close"].rolling(window).mean()
        data[f"{column}_slope5_pct"] = (data[column] / data[column].shift(5) - 1) * 100
        data[f"dist_{column}_pct"] = (data["close"] / data[column] - 1) * 100

    for window in VOL_WINDOWS:
        column = f"vma{window}"
        data[column] = data["vol"].rolling(window).mean()
        data[f"vol_ratio{window}"] = data["vol"] / data[column]

    for window in POSITION_WINDOWS:
        low_column = f"low_{window}"
        high_column = f"high_{window}"
        data[low_column] = data["low"].rolling(window).min()
        data[high_column] = data["high"].rolling(window).max()
        width = data[high_column] - data[low_column]
        data[f"position_{window}"] = np.where(width > 0, (data["close"] - data[low_column]) / width, 0.5)
    return data


def confirmed_swings(data: pd.DataFrame, order: int) -> list[dict[str, Any]]:
    swings: list[dict[str, Any]] = []
    highs = data["high"].to_numpy()
    lows = data["low"].to_numpy()
    # The loop stops order bars before the end, so every emitted pivot was confirmable by as_of.
    for index in range(order, len(data) - order):
        high_window = highs[index - order : index + order + 1]
        low_window = lows[index - order : index + order + 1]
        if np.isfinite(highs[index]) and highs[index] == np.nanmax(high_window):
            swings.append(
                {
                    "trade_date": data.iloc[index]["trade_date"],
                    "kind": "swing_high",
                    "price": scalar(highs[index]),
                    "confirmed_on": data.iloc[index + order]["trade_date"],
                }
            )
        if np.isfinite(lows[index]) and lows[index] == np.nanmin(low_window):
            swings.append(
                {
                    "trade_date": data.iloc[index]["trade_date"],
                    "kind": "swing_low",
                    "price": scalar(lows[index]),
                    "confirmed_on": data.iloc[index + order]["trade_date"],
                }
            )
    return swings[-16:]


def candidate_events(data: pd.DataFrame) -> list[dict[str, Any]]:
    recent = data.tail(80).copy()
    recent["median_range20"] = data["range_pct"].rolling(20).median().tail(80).to_numpy()
    events: list[dict[str, Any]] = []
    for _, row in recent.iterrows():
        labels: list[str] = []
        if pd.notna(row.get("vol_ratio20")) and row["vol_ratio20"] >= 1.8:
            labels.append("high_volume")
        if pd.notna(row.get("median_range20")) and row["median_range20"] > 0 and row["range_pct"] >= 1.8 * row["median_range20"]:
            labels.append("wide_range")
        if pd.notna(row["pct_chg_calc"]) and row["pct_chg_calc"] >= 9.5:
            labels.append("limit_up_candidate")
        if pd.notna(row["pct_chg_calc"]) and row["pct_chg_calc"] <= -9.5:
            labels.append("limit_down_candidate")
        if labels:
            events.append(
                {
                    "trade_date": row["trade_date"],
                    "labels": labels,
                    "pct_chg": scalar(row["pct_chg_calc"]),
                    "range_pct": scalar(row["range_pct"]),
                    "vol_ratio20": scalar(row.get("vol_ratio20")),
                    "close_location": scalar(row["close_location"]),
                }
            )
    return events


def aggregate_bars(data: pd.DataFrame, frequency: str, count: int) -> list[dict[str, Any]]:
    indexed = data.copy()
    indexed.index = pd.to_datetime(indexed["trade_date"], format="%Y%m%d")
    grouped = indexed.resample(frequency).agg(
        actual_end=("trade_date", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        vol=("vol", "sum"),
        amount=("amount", "sum"),
    )
    grouped = grouped.dropna(subset=["open", "high", "low", "close"]).tail(count).reset_index(drop=True)
    grouped = grouped.rename(columns={"actual_end": "period_end"})
    return records(grouped)


def build_packet(args: argparse.Namespace) -> dict[str, Any]:
    frame, stock_name = load_data(args.project_root.resolve(), args.ts_code, args.as_of, args.context_bars)
    data = add_features(frame)
    daily_output = data.tail(args.bars).reset_index(drop=True)
    output_columns = [
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "vol",
        "amount",
        "pct_chg_calc",
        "range_pct",
        "close_location",
        "atr14",
    ]
    for window in MA_WINDOWS:
        output_columns.extend([f"ma{window}", f"ma{window}_slope5_pct", f"dist_ma{window}_pct"])
    for window in VOL_WINDOWS:
        output_columns.extend([f"vma{window}", f"vol_ratio{window}"])
    for window in POSITION_WINDOWS:
        output_columns.extend([f"low_{window}", f"high_{window}", f"position_{window}"])

    warnings: list[str] = []
    if len(data) < args.context_bars:
        warnings.append(f"Only {len(data)} context bars available; requested {args.context_bars}.")
    if data["trade_date"].duplicated().any():
        warnings.append("Duplicate trade dates remain after normalization.")
    if data[["open", "high", "low", "close", "vol"]].isna().any().any():
        warnings.append("Core OHLCV contains missing values.")

    latest = records(data.tail(1), output_columns)[0]
    packet = {
        "schema": "a_share_kline_packet.v1",
        "ts_code": args.ts_code,
        "stock_name": stock_name,
        "as_of": args.as_of,
        "future_isolated": True,
        "source": "offlineDataManager database via coreClient.data_provider",
        "price_basis": "qfq",
        "volume_basis": "raw_actual",
        "available_start": daily_output.iloc[0]["trade_date"],
        "available_end": data.iloc[-1]["trade_date"],
        "context_start": data.iloc[0]["trade_date"],
        "context_bar_count": len(data),
        "daily_bar_count": len(daily_output),
        "swing_order": args.swing_order,
        "warnings": warnings,
        "latest": latest,
        "confirmed_swings": confirmed_swings(daily_output, args.swing_order),
        "candidate_events": candidate_events(data),
        "weekly_from_truncated_daily": aggregate_bars(data, "W-FRI", 260),
        "monthly_from_truncated_daily": aggregate_bars(data, "ME", 72),
        "daily": records(daily_output, output_columns),
    }
    return packet


def main() -> None:
    args = parse_args()
    packet = build_packet(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "ts_code": packet["ts_code"],
                "as_of": packet["as_of"],
                "bars": packet["daily_bar_count"],
                "warnings": packet["warnings"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
