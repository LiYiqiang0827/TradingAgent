"""Daily-bar approximations of time-at-price and volume-at-price."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .common import number


def daily_approx_profile(frame: pd.DataFrame, *, bins: int = 48, value_area: float = 0.70) -> dict[str, Any]:
    """Approximate a price profile by distributing each daily bar over crossed bins.

    This deliberately declares its low-frequency source. It must not be presented
    as a true transaction-level chip distribution.
    """

    if frame.empty or len(frame) < 10 or "vol" not in frame:
        return {"available": False, "source_granularity": "daily_approximation"}
    low, high = float(frame["low"].min()), float(frame["high"].max())
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return {"available": False, "source_granularity": "daily_approximation"}
    edges = np.linspace(low, high, max(12, int(bins)) + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    volume = np.zeros(len(centers), dtype=float)
    time = np.zeros(len(centers), dtype=float)

    for row in frame.itertuples(index=False):
        bar_low, bar_high = float(row.low), float(row.high)
        mask = (centers >= bar_low) & (centers <= bar_high)
        if not mask.any():
            mask[int(np.argmin(np.abs(centers - float(row.close))))] = True
        count = int(mask.sum())
        row_volume = float(getattr(row, "vol", 0.0) or 0.0)
        volume[mask] += row_volume / count
        time[mask] += 1.0 / count

    if volume.sum() <= 0:
        return {"available": False, "source_granularity": "daily_approximation"}
    poc_index = int(np.argmax(volume))
    target = float(volume.sum() * value_area)
    included = {poc_index}
    total = float(volume[poc_index])
    left, right = poc_index - 1, poc_index + 1
    while total < target and (left >= 0 or right < len(volume)):
        left_value = volume[left] if left >= 0 else -1
        right_value = volume[right] if right < len(volume) else -1
        selected = right if right_value > left_value else left
        if selected < 0 or selected >= len(volume):
            break
        included.add(selected)
        total += float(volume[selected])
        if selected == left:
            left -= 1
        else:
            right += 1

    hvn_indexes = np.argsort(volume)[-min(5, len(volume)) :][::-1]
    time_indexes = np.argsort(time)[-min(5, len(time)) :][::-1]
    return {
        "available": True,
        "source_granularity": "daily_approximation",
        "confidence": "low",
        "method": "uniform_volume_across_daily_range",
        "window_start": str(frame.iloc[0]["trade_date"]),
        "window_end": str(frame.iloc[-1]["trade_date"]),
        "bins": int(len(centers)),
        "poc": number(centers[poc_index]),
        "vah": number(edges[max(included) + 1]),
        "val": number(edges[min(included)]),
        "hvn": [number(centers[index]) for index in hvn_indexes],
        "time_dense": [number(centers[index]) for index in time_indexes],
    }
