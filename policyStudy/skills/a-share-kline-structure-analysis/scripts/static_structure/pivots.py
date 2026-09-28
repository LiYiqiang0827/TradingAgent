"""Multi-scale confirmed swing detection using SciPy peak primitives."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, peak_prominences

from .common import finite, number


DEFAULT_SCALES = {
    "daily": (("short", 3, 0.45), ("medium", 7, 0.75), ("long", 15, 1.10)),
    "weekly": (("medium", 2, 0.55), ("long", 4, 0.85)),
    "monthly": (("long", 2, 0.65),),
}


def _local_confirmation(values: np.ndarray, index: int, window: int, kind: str) -> bool:
    if index < window or index + window >= len(values):
        return False
    sample = values[index - window : index + window + 1]
    if kind == "high":
        return values[index] >= np.nanmax(sample)
    return values[index] <= np.nanmin(sample)


def detect_pivots(
    frame: pd.DataFrame,
    *,
    timeframe: str,
    scales: tuple[tuple[str, int, float], ...] | None = None,
) -> list[dict[str, Any]]:
    """Return pivots that were confirmable by the final bar in ``frame``.

    ``window`` right-side bars are required for confirmation. Peak prominence is
    evaluated in a bounded local window so appending distant future bars cannot
    silently change an already-frozen calculation.
    """

    if frame.empty or len(frame) < 7:
        return []
    selected_scales = scales or DEFAULT_SCALES.get(timeframe, DEFAULT_SCALES["daily"])
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    atr = frame.get("atr14", pd.Series(np.nan, index=frame.index)).to_numpy(dtype=float)
    output: list[dict[str, Any]] = []

    for scale, window, min_atr_prominence in selected_scales:
        for kind, signal in (("high", highs), ("low", -lows)):
            indexes, _ = find_peaks(signal, distance=max(1, window))
            if len(indexes) == 0:
                continue
            prominences = peak_prominences(signal, indexes, wlen=2 * window + 1)[0]
            for index, prominence in zip(indexes, prominences):
                if not _local_confirmation(highs if kind == "high" else lows, int(index), window, kind):
                    continue
                atr_value = atr[index]
                if not finite(atr_value) or atr_value <= 0:
                    atr_value = np.nanmedian((highs - lows)[max(0, index - 14) : index + 1])
                prominence_atr = float(prominence / atr_value) if finite(atr_value) and atr_value > 0 else 0.0
                if prominence_atr < min_atr_prominence:
                    continue
                price = highs[index] if kind == "high" else lows[index]
                output.append(
                    {
                        "pivot_id": f"{timeframe}:{scale}:{kind}:{frame.iloc[index]['trade_date']}",
                        "timeframe": timeframe,
                        "scale": scale,
                        "kind": kind,
                        "index": int(index),
                        "trade_date": str(frame.iloc[index]["trade_date"]),
                        "confirmed_on": str(frame.iloc[index + window]["trade_date"]),
                        "price": number(price),
                        "prominence": number(prominence),
                        "prominence_atr": number(prominence_atr),
                        "confirmation_bars": int(window),
                    }
                )

    # A pivot can occur at several scales. Keep that evidence but remove exact duplicates.
    deduped = {item["pivot_id"]: item for item in output}
    return sorted(deduped.values(), key=lambda item: (item["trade_date"], item["timeframe"], item["scale"], item["kind"]))


def provisional_extremes(frame: pd.DataFrame, *, timeframe: str, window: int = 5) -> list[dict[str, Any]]:
    """Expose recent unconfirmed extremes without presenting them as pivots."""

    if frame.empty:
        return []
    recent = frame.tail(min(window, len(frame)))
    result: list[dict[str, Any]] = []
    for kind, column, selector in (("high", "high", "idxmax"), ("low", "low", "idxmin")):
        index = int(getattr(recent[column], selector)())
        result.append(
            {
                "timeframe": timeframe,
                "kind": kind,
                "status": "provisional",
                "trade_date": str(frame.loc[index, "trade_date"]),
                "price": number(frame.loc[index, column]),
            }
        )
    return result
