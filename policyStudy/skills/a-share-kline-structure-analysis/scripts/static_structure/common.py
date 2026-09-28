"""Shared helpers for static-structure detection."""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np
import pandas as pd


NUMERIC_COLUMNS = ("open", "high", "low", "close", "vol", "amount", "atr14", "pct_chg_calc")


def normalize_frame(records: list[dict[str, Any]], date_column: str = "trade_date") -> pd.DataFrame:
    frame = pd.DataFrame(records).copy()
    if frame.empty:
        return frame
    if date_column not in frame:
        if "period_end" in frame:
            frame[date_column] = frame["period_end"]
        else:
            raise ValueError(f"Missing date column: {date_column}")
    frame[date_column] = frame[date_column].astype(str).str.replace("-", "", regex=False)
    for column in NUMERIC_COLUMNS:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    required = [column for column in ("open", "high", "low", "close") if column in frame]
    frame = frame.dropna(subset=required).drop_duplicates(date_column, keep="last")
    frame = frame.sort_values(date_column).reset_index(drop=True)
    if "atr14" not in frame or frame["atr14"].isna().all():
        prior = frame["close"].shift(1)
        tr = pd.concat(
            [(frame["high"] - frame["low"]), (frame["high"] - prior).abs(), (frame["low"] - prior).abs()],
            axis=1,
        ).max(axis=1)
        frame["atr14"] = tr.rolling(14, min_periods=3).mean()
    return frame


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def number(value: Any, digits: int = 6) -> float | None:
    return round(float(value), digits) if finite(value) else None


def unique_strings(values: Iterable[Any]) -> list[str]:
    return sorted({str(value) for value in values if value is not None and str(value)})


def latest_atr_pct(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.03
    close = float(frame.iloc[-1]["close"])
    atr = frame.iloc[-1].get("atr14")
    if not finite(atr) or close <= 0:
        return 0.03
    return max(0.005, min(0.15, float(atr) / close))


def status_after_break(
    closes: pd.Series,
    *,
    upper: float,
    lower: float,
    start_index: int,
    tolerance: float = 0.0,
) -> tuple[str, str | None]:
    later = closes.iloc[start_index + 1 :]
    if later.empty:
        return "active", None
    above = later[later > upper + tolerance]
    below = later[later < lower - tolerance]
    if not above.empty and (below.empty or above.index[0] < below.index[0]):
        return "broken_up", str(above.index[0])
    if not below.empty:
        return "broken_down", str(below.index[0])
    return "active", None


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return number(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y%m%d")
    if value is pd.NA:
        return None
    return value
