"""Classic chart-pattern candidates derived from confirmed pivots."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .common import number


def _medium_sequence(pivots: list[dict[str, Any]], timeframe: str = "daily") -> list[dict[str, Any]]:
    candidates = [item for item in pivots if item["timeframe"] == timeframe and item["scale"] in {"medium", "long"}]
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        key = (item["trade_date"], item["kind"])
        old = by_key.get(key)
        if old is None or float(item.get("prominence_atr") or 0) > float(old.get("prominence_atr") or 0):
            by_key[key] = item
    ordered = sorted(by_key.values(), key=lambda item: item["index"])
    alternating: list[dict[str, Any]] = []
    for item in ordered:
        if not alternating or alternating[-1]["kind"] != item["kind"]:
            alternating.append(item)
        else:
            better = item["price"] > alternating[-1]["price"] if item["kind"] == "high" else item["price"] < alternating[-1]["price"]
            if better:
                alternating[-1] = item
    return alternating[-18:]


def _break_status(frame: pd.DataFrame, after_index: int, *, direction: str, level: float) -> tuple[str, str | None]:
    later = frame.iloc[after_index + 1 :]
    if later.empty:
        return "awaiting_breakout", None
    matched = later[later["close"] < level] if direction == "down" else later[later["close"] > level]
    if matched.empty:
        return "awaiting_breakout", None
    return "confirmed", str(matched.iloc[0]["trade_date"])


def detect_reversal_patterns(
    frame: pd.DataFrame,
    pivots: list[dict[str, Any]],
    *,
    timeframe: str = "daily",
) -> list[dict[str, Any]]:
    sequence = _medium_sequence(pivots, timeframe)
    patterns: list[dict[str, Any]] = []
    for offset in range(max(0, len(sequence) - 12), len(sequence) - 2):
        a, b, c = sequence[offset : offset + 3]
        if [a["kind"], b["kind"], c["kind"]] == ["high", "low", "high"]:
            similarity = abs(float(a["price"]) / float(c["price"]) - 1)
            depth = min(float(a["price"]), float(c["price"])) / float(b["price"]) - 1
            if similarity <= 0.035 and depth >= 0.035:
                status, break_date = _break_status(frame, int(c["index"]), direction="down", level=float(b["price"]))
                patterns.append(
                    {
                        "pattern_id": f"double_top:{a['trade_date']}:{c['trade_date']}",
                        "pattern_type": "double_top",
                        "timeframe": timeframe,
                        "status": status,
                        "pivot_dates": [a["trade_date"], b["trade_date"], c["trade_date"]],
                        "pivot_prices": [a["price"], b["price"], c["price"]],
                        "neckline": b["price"],
                        "breakout_date": break_date,
                        "invalidation": number(max(float(a["price"]), float(c["price"])) * 1.01),
                        "geometry_score": number(min(100, max(0, 100 - similarity * 1200 + min(depth * 250, 20))), 2),
                    }
                )
        if [a["kind"], b["kind"], c["kind"]] == ["low", "high", "low"]:
            similarity = abs(float(a["price"]) / float(c["price"]) - 1)
            height = float(b["price"]) / max(float(a["price"]), float(c["price"])) - 1
            if similarity <= 0.035 and height >= 0.035:
                status, break_date = _break_status(frame, int(c["index"]), direction="up", level=float(b["price"]))
                patterns.append(
                    {
                        "pattern_id": f"double_bottom:{a['trade_date']}:{c['trade_date']}",
                        "pattern_type": "double_bottom",
                        "timeframe": timeframe,
                        "status": status,
                        "pivot_dates": [a["trade_date"], b["trade_date"], c["trade_date"]],
                        "pivot_prices": [a["price"], b["price"], c["price"]],
                        "neckline": b["price"],
                        "breakout_date": break_date,
                        "invalidation": number(min(float(a["price"]), float(c["price"])) * 0.99),
                        "geometry_score": number(min(100, max(0, 100 - similarity * 1200 + min(height * 250, 20))), 2),
                    }
                )

    for offset in range(max(0, len(sequence) - 14), len(sequence) - 4):
        points = sequence[offset : offset + 5]
        kinds = [item["kind"] for item in points]
        if kinds == ["high", "low", "high", "low", "high"]:
            left, valley1, head, valley2, right = points
            shoulders_close = abs(float(left["price"]) / float(right["price"]) - 1) <= 0.06
            head_clear = float(head["price"]) >= max(float(left["price"]), float(right["price"])) * 1.025
            if shoulders_close and head_clear:
                neckline = (float(valley1["price"]) + float(valley2["price"])) / 2
                status, break_date = _break_status(frame, int(right["index"]), direction="down", level=neckline)
                patterns.append(
                    {
                        "pattern_id": f"head_shoulders:{left['trade_date']}:{right['trade_date']}",
                        "pattern_type": "head_and_shoulders",
                        "timeframe": timeframe,
                        "status": status,
                        "pivot_dates": [item["trade_date"] for item in points],
                        "pivot_prices": [item["price"] for item in points],
                        "neckline": number(neckline),
                        "breakout_date": break_date,
                        "invalidation": number(float(head["price"]) * 1.01),
                        "geometry_score": number(70 + min(20, (float(head["price"]) / max(float(left["price"]), float(right["price"])) - 1) * 200), 2),
                    }
                )
        elif kinds == ["low", "high", "low", "high", "low"]:
            left, peak1, head, peak2, right = points
            shoulders_close = abs(float(left["price"]) / float(right["price"]) - 1) <= 0.06
            head_clear = float(head["price"]) <= min(float(left["price"]), float(right["price"])) * 0.975
            if shoulders_close and head_clear:
                neckline = (float(peak1["price"]) + float(peak2["price"])) / 2
                status, break_date = _break_status(frame, int(right["index"]), direction="up", level=neckline)
                patterns.append(
                    {
                        "pattern_id": f"inverse_head_shoulders:{left['trade_date']}:{right['trade_date']}",
                        "pattern_type": "inverse_head_and_shoulders",
                        "timeframe": timeframe,
                        "status": status,
                        "pivot_dates": [item["trade_date"] for item in points],
                        "pivot_prices": [item["price"] for item in points],
                        "neckline": number(neckline),
                        "breakout_date": break_date,
                        "invalidation": number(float(head["price"]) * 0.99),
                        "geometry_score": number(75, 2),
                    }
                )
    deduped = {item["pattern_id"]: item for item in patterns}
    return sorted(deduped.values(), key=lambda item: item["pivot_dates"][-1])


def detect_triangle(frame: pd.DataFrame, pivots: list[dict[str, Any]], *, timeframe: str = "daily") -> list[dict[str, Any]]:
    highs = [item for item in _medium_sequence(pivots, timeframe) if item["kind"] == "high"][-4:]
    lows = [item for item in _medium_sequence(pivots, timeframe) if item["kind"] == "low"][-4:]
    if len(highs) < 2 or len(lows) < 2:
        return []
    hx = np.array([item["index"] for item in highs], dtype=float)
    lx = np.array([item["index"] for item in lows], dtype=float)
    hslope, hintercept = np.polyfit(hx, np.log([item["price"] for item in highs]), 1)
    lslope, lintercept = np.polyfit(lx, np.log([item["price"] for item in lows]), 1)
    if hslope >= lslope:
        return []
    current_index = len(frame) - 1
    upper = float(np.exp(hslope * current_index + hintercept))
    lower = float(np.exp(lslope * current_index + lintercept))
    if upper <= lower:
        return []
    close = float(frame.iloc[-1]["close"])
    if close > upper:
        status = "confirmed_up"
    elif close < lower:
        status = "confirmed_down"
    else:
        status = "awaiting_breakout"
    if hslope > 0 and lslope > 0:
        pattern_type = "rising_wedge"
    elif hslope < 0 and lslope < 0:
        pattern_type = "falling_wedge"
    elif abs(hslope) < 0.0007 and lslope > 0:
        pattern_type = "ascending_triangle"
    elif abs(lslope) < 0.0007 and hslope < 0:
        pattern_type = "descending_triangle"
    else:
        pattern_type = "symmetrical_triangle"
    return [
        {
            "pattern_id": f"triangle:{highs[0]['trade_date']}:{lows[0]['trade_date']}",
            "pattern_type": pattern_type,
            "timeframe": timeframe,
            "status": status,
            "pivot_dates": sorted([item["trade_date"] for item in highs + lows]),
            "upper_anchor_dates": [item["trade_date"] for item in highs],
            "upper_anchor_prices": [item["price"] for item in highs],
            "lower_anchor_dates": [item["trade_date"] for item in lows],
            "lower_anchor_prices": [item["price"] for item in lows],
            "upper_slope_log_per_bar": number(hslope, 9),
            "upper_intercept_log": number(hintercept, 9),
            "lower_slope_log_per_bar": number(lslope, 9),
            "lower_intercept_log": number(lintercept, 9),
            "upper_now": number(upper),
            "lower_now": number(lower),
            "upper_slope_pct_per_bar": number((np.exp(hslope) - 1) * 100),
            "lower_slope_pct_per_bar": number((np.exp(lslope) - 1) * 100),
            "geometry_score": number(min(95, 55 + 5 * (len(highs) + len(lows))), 2),
        }
    ]


def detect_flag(frame: pd.DataFrame, *, timeframe: str = "daily") -> list[dict[str, Any]]:
    """Detect a recent impulse followed by a compact counter-trend consolidation."""

    if len(frame) < 35:
        return []
    results: list[dict[str, Any]] = []
    latest = len(frame) - 1
    for flag_bars in (8, 10, 15, 20):
        flag_start = latest - flag_bars + 1
        if flag_start < 12:
            continue
        for pole_bars in (5, 8, 10, 12):
            pole_start = flag_start - pole_bars
            if pole_start < 0:
                continue
            start_close = float(frame.iloc[pole_start]["close"])
            pole_close = float(frame.iloc[flag_start - 1]["close"])
            impulse = pole_close / start_close - 1
            if abs(impulse) < 0.12:
                continue
            sample = frame.iloc[flag_start : latest + 1]
            x = np.arange(len(sample), dtype=float)
            slope = float(np.polyfit(x, np.log(sample["close"].to_numpy(dtype=float)), 1)[0])
            upper = float(sample["high"].quantile(0.90))
            lower = float(sample["low"].quantile(0.10))
            pole_size = abs(pole_close - start_close)
            retracement = (upper - lower) / pole_size if pole_size > 0 else 99.0
            countertrend = slope <= 0.001 if impulse > 0 else slope >= -0.001
            if retracement > 0.75 or not countertrend:
                continue
            current_close = float(frame.iloc[-1]["close"])
            if impulse > 0:
                status = "confirmed_up" if current_close > upper else "awaiting_breakout"
                pattern_type = "bull_flag"
                invalidation = lower
            else:
                status = "confirmed_down" if current_close < lower else "awaiting_breakout"
                pattern_type = "bear_flag"
                invalidation = upper
            score = min(95.0, 55 + abs(impulse) * 100 + max(0, 15 * (1 - retracement)))
            results.append(
                {
                    "pattern_id": f"{pattern_type}:{frame.iloc[pole_start]['trade_date']}:{frame.iloc[flag_start]['trade_date']}",
                    "pattern_type": pattern_type,
                    "timeframe": timeframe,
                    "status": status,
                    "pole_start": str(frame.iloc[pole_start]["trade_date"]),
                    "pole_end": str(frame.iloc[flag_start - 1]["trade_date"]),
                    "flag_start": str(frame.iloc[flag_start]["trade_date"]),
                    "flag_end": str(frame.iloc[-1]["trade_date"]),
                    "pole_return_pct": number(impulse * 100),
                    "lower": number(lower),
                    "upper": number(upper),
                    "invalidation": number(invalidation),
                    "geometry_score": number(score, 2),
                }
            )
    if not results:
        return []
    results.sort(key=lambda item: (-item["geometry_score"], item["flag_start"]))
    return [results[0]]


def detect_patterns(
    frame: pd.DataFrame,
    pivots: list[dict[str, Any]],
    platforms: list[dict[str, Any]],
    *,
    timeframe: str = "daily",
) -> list[dict[str, Any]]:
    patterns = (
        detect_reversal_patterns(frame, pivots, timeframe=timeframe)
        + detect_triangle(frame, pivots, timeframe=timeframe)
        + detect_flag(frame, timeframe=timeframe)
    )
    for item in platforms:
        if item["timeframe"] != timeframe:
            continue
        patterns.append(
            {
                "pattern_id": f"rectangle:{item['platform_id']}",
                "pattern_type": "rectangle",
                "timeframe": timeframe,
                "status": item["status"],
                "start_date": item["start_date"],
                "end_date": item["end_date"],
                "lower": item["lower"],
                "upper": item["upper"],
                "geometry_score": item["evidence_score"],
            }
        )
    return patterns
