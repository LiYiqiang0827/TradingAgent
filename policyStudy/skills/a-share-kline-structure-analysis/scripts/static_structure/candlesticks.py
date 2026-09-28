"""Transparent candlestick-shape candidates with no directional conclusion."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .common import number


def detect_candlestick_patterns(frame: pd.DataFrame, *, timeframe: str = "daily", lookback: int = 80) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    sample = frame.tail(lookback).copy()
    events: list[dict[str, Any]] = []
    for position, (index, row) in enumerate(sample.iterrows()):
        open_, high, low, close = map(float, (row.open, row.high, row.low, row.close))
        span = high - low
        if span <= 0:
            continue
        body = abs(close - open_)
        upper = high - max(open_, close)
        lower = min(open_, close) - low
        body_ratio = body / span
        labels: list[tuple[str, str]] = []
        if body_ratio <= 0.10:
            labels.append(("doji", "neutral"))
        if lower >= max(body * 2.0, span * 0.45) and upper <= max(body, span * 0.15):
            labels.append(("hammer_shape", "context_required"))
        if upper >= max(body * 2.0, span * 0.45) and lower <= max(body, span * 0.15):
            labels.append(("shooting_star_shape", "context_required"))
        if body_ratio >= 0.72:
            labels.append(("long_bull_body" if close > open_ else "long_bear_body", "bullish" if close > open_ else "bearish"))

        if position > 0:
            previous = sample.iloc[position - 1]
            previous_open, previous_close = float(previous.open), float(previous.close)
            if previous_close < previous_open and close > open_ and open_ <= previous_close and close >= previous_open:
                labels.append(("bullish_engulfing", "bullish"))
            if previous_close > previous_open and close < open_ and open_ >= previous_close and close <= previous_open:
                labels.append(("bearish_engulfing", "bearish"))

        for name, direction in labels:
            events.append(
                {
                    "trade_date": str(row.trade_date),
                    "timeframe": timeframe,
                    "pattern": name,
                    "direction": direction,
                    "body_ratio": number(body_ratio),
                    "close_location": number((close - low) / span),
                    "requires_structure_context": True,
                }
            )
    return events
