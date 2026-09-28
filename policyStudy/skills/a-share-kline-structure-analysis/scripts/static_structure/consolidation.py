"""Detect rectangular consolidation/platform candidates from OHLCV bars."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .common import finite, number


def _structure_level(timeframe: str, bars: int) -> str:
    limits = {"daily": (25, 60), "weekly": (12, 26), "monthly": (8, 18)}
    minor_limit, intermediate_limit = limits.get(timeframe, limits["daily"])
    if bars <= minor_limit:
        return "minor"
    if bars <= intermediate_limit:
        return "intermediate"
    return "major"


def _candidate(frame: pd.DataFrame, start: int, end: int, timeframe: str) -> dict[str, Any] | None:
    sample = frame.iloc[start : end + 1]
    if len(sample) < 12:
        return None
    close = sample["close"].to_numpy(dtype=float)
    x = np.arange(len(sample), dtype=float)
    slope = float(np.polyfit(x, np.log(close), 1)[0])
    lower = float(sample["low"].quantile(0.15))
    upper = float(sample["high"].quantile(0.85))
    center = (lower + upper) / 2
    if center <= 0 or upper <= lower:
        return None
    width_pct = (upper / lower - 1) * 100
    atr = float(sample["atr14"].median()) if "atr14" in sample and sample["atr14"].notna().any() else center * 0.03
    width_atr = (upper - lower) / atr if atr > 0 else 99.0
    tolerance = max(atr * 0.30, center * 0.006)
    upper_touches = int((sample["high"] >= upper - tolerance).sum())
    lower_touches = int((sample["low"] <= lower + tolerance).sum())
    containment = float(((sample["close"] >= lower - tolerance) & (sample["close"] <= upper + tolerance)).mean())
    slope_total_pct = (np.exp(slope * max(1, len(sample) - 1)) - 1) * 100
    volume_slope = None
    if "vol" in sample and sample["vol"].notna().sum() >= 5:
        volumes = sample["vol"].replace(0, np.nan).dropna().to_numpy(dtype=float)
        if len(volumes) >= 5:
            volume_slope = float(np.polyfit(np.arange(len(volumes)), np.log(volumes), 1)[0])

    qualifies = (
        width_pct <= 18.0
        and width_atr <= 7.0
        and abs(slope_total_pct) <= max(8.0, width_pct * 0.75)
        and containment >= 0.72
        and upper_touches >= 2
        and lower_touches >= 2
    )
    if not qualifies:
        return None
    latest_close = float(frame.iloc[-1]["close"])
    if latest_close > upper + tolerance:
        status = "broken_up"
    elif latest_close < lower - tolerance:
        status = "broken_down"
    elif end < len(frame) - 1:
        status = "completed_inside"
    else:
        status = "active"
    score = (
        20 * containment
        + 5 * min(upper_touches, 4)
        + 5 * min(lower_touches, 4)
        + max(0.0, 20 - width_pct)
        + (8 if volume_slope is not None and volume_slope < 0 else 0)
    )
    return {
        "platform_id": f"{timeframe}:{sample.iloc[0]['trade_date']}:{sample.iloc[-1]['trade_date']}",
        "timeframe": timeframe,
        "structure_level": _structure_level(timeframe, len(sample)),
        "start_date": str(sample.iloc[0]["trade_date"]),
        "end_date": str(sample.iloc[-1]["trade_date"]),
        "start_index": int(start),
        "end_index": int(end),
        "bars": int(len(sample)),
        "lower": number(lower),
        "upper": number(upper),
        "width_pct": number(width_pct),
        "width_atr": number(width_atr),
        "slope_total_pct": number(slope_total_pct),
        "upper_touches": upper_touches,
        "lower_touches": lower_touches,
        "containment": number(containment),
        "volume_contracting": bool(volume_slope is not None and volume_slope < 0),
        "status": status,
        "evidence_score": number(min(100.0, score), 2),
        "score_kind": "uncalibrated_structure_evidence",
    }


def detect_platforms(frame: pd.DataFrame, *, timeframe: str, max_results: int = 6) -> list[dict[str, Any]]:
    if frame.empty or len(frame) < 15:
        return []
    windows = (15, 20, 30, 40, 60, 80) if timeframe == "daily" else (12, 18, 26, 40)
    end_candidates = sorted(set([len(frame) - 1, len(frame) - 3, len(frame) - 5, len(frame) - 10, len(frame) - 15]))
    candidates: list[dict[str, Any]] = []
    for end in end_candidates:
        if end < 0:
            continue
        for window in windows:
            start = end - window + 1
            if start < 0:
                continue
            item = _candidate(frame, start, end, timeframe)
            if item is not None:
                candidates.append(item)
    candidates.sort(key=lambda item: (-item["evidence_score"], -item["end_index"], -item["bars"]))
    selected: list[dict[str, Any]] = []
    for item in candidates:
        duplicate = any(
            abs(item["lower"] / existing["lower"] - 1) < 0.015
            and abs(item["upper"] / existing["upper"] - 1) < 0.015
            and abs(item["end_index"] - existing["end_index"]) <= 5
            for existing in selected
        )
        if not duplicate:
            selected.append(item)
        if len(selected) >= max_results:
            break
    return selected
