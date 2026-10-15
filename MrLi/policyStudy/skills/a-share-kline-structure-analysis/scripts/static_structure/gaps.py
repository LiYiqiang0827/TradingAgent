"""Detect and track daily/weekly/monthly price gaps."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .common import finite, number


def detect_gaps(frame: pd.DataFrame, *, timeframe: str, min_atr_fraction: float = 0.15) -> list[dict[str, Any]]:
    if frame.empty or len(frame) < 2:
        return []
    gaps: list[dict[str, Any]] = []
    for index in range(1, len(frame)):
        previous = frame.iloc[index - 1]
        current = frame.iloc[index]
        atr = current.get("atr14")
        minimum = float(atr) * min_atr_fraction if finite(atr) else float(previous["close"]) * 0.003
        direction: str | None = None
        lower = upper = 0.0
        if float(current["low"]) - float(previous["high"]) >= minimum:
            direction = "up"
            lower, upper = float(previous["high"]), float(current["low"])
        elif float(previous["low"]) - float(current["high"]) >= minimum:
            direction = "down"
            lower, upper = float(current["high"]), float(previous["low"])
        if direction is None:
            continue

        later = frame.iloc[index + 1 :]
        status = "unfilled"
        fill_date = None
        partial_date = None
        if direction == "up" and not later.empty:
            partial = later[later["low"] < upper]
            filled = later[later["low"] <= lower]
        elif direction == "down" and not later.empty:
            partial = later[later["high"] > lower]
            filled = later[later["high"] >= upper]
        else:
            partial = filled = pd.DataFrame()
        if not partial.empty:
            status = "partial"
            partial_date = str(partial.iloc[0]["trade_date"])
        if not filled.empty:
            status = "filled"
            fill_date = str(filled.iloc[0]["trade_date"])

        gaps.append(
            {
                "gap_id": f"{timeframe}:{direction}:{current['trade_date']}",
                "timeframe": timeframe,
                "direction": direction,
                "trade_date": str(current["trade_date"]),
                "lower": number(lower),
                "upper": number(upper),
                "size_pct": number((upper / lower - 1) * 100 if lower > 0 else 0),
                "status": status,
                "partial_fill_date": partial_date,
                "fill_date": fill_date,
            }
        )
    return gaps
