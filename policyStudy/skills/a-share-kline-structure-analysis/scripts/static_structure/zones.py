"""Cluster heterogeneous price evidence into horizontal support/resistance zones."""

from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import AgglomerativeClustering

from .common import latest_atr_pct, number, unique_strings


TIMEFRAME_WEIGHT = {"daily": 1.0, "weekly": 1.8, "monthly": 2.6}
SOURCE_WEIGHT = {
    "pivot": 1.0,
    "moving_average": 0.55,
    "gap_edge": 1.15,
    "platform_edge": 1.35,
    "limit_up_anchor": 1.30,
    "volume_profile": 1.15,
}


def _touch_stats(frame: pd.DataFrame, lower: float, upper: float) -> dict[str, int]:
    intersects = (frame["high"] >= lower) & (frame["low"] <= upper)
    indexes = list(frame.index[intersects])
    # Consecutive bars in the same visit count as one test.
    visits = 0
    last = -2
    for index in indexes:
        if index > last + 1:
            visits += 1
        last = int(index)
    closes_above = int((frame["close"] > upper).sum())
    closes_below = int((frame["close"] < lower).sum())
    return {"touch_bars": len(indexes), "touch_visits": visits, "closes_above": closes_above, "closes_below": closes_below}


def build_zones(frame: pd.DataFrame, candidates: list[dict[str, Any]], *, max_zones: int = 18) -> list[dict[str, Any]]:
    valid = [item for item in candidates if item.get("price") is not None and float(item["price"]) > 0]
    if not valid or frame.empty:
        return []
    atr_pct = latest_atr_pct(frame)
    merge_pct = max(0.006, min(0.025, atr_pct * 0.45))
    values = np.log(np.array([[float(item["price"])] for item in valid], dtype=float))
    # Complete linkage prevents the chaining problem where many individually-near
    # prices form one excessively wide zone.
    if len(valid) == 1:
        labels = np.array([0], dtype=int)
    else:
        labels = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=np.log1p(merge_pct),
            linkage="complete",
            metric="euclidean",
        ).fit_predict(values)
    close = float(frame.iloc[-1]["close"])
    zones: list[dict[str, Any]] = []

    for label in sorted(set(int(value) for value in labels)):
        items = [item for item, item_label in zip(valid, labels) if int(item_label) == label]
        prices = np.array([float(item["price"]) for item in items], dtype=float)
        weights = np.array(
            [TIMEFRAME_WEIGHT.get(item.get("timeframe", "daily"), 1.0) * SOURCE_WEIGHT.get(item.get("source_type", "pivot"), 1.0) for item in items]
        )
        center = float(np.average(prices, weights=weights))
        half_width = max(center * merge_pct * 0.35, float(frame.iloc[-1].get("atr14", center * 0.03) or center * 0.03) * 0.15)
        lower = min(float(prices.min()), center - half_width)
        upper = max(float(prices.max()), center + half_width)
        stats = _touch_stats(frame, lower, upper)
        source_types = unique_strings(item.get("source_type") for item in items)
        timeframes = unique_strings(item.get("timeframe", "daily") for item in items)
        pivot_kinds = Counter(str(item.get("kind")) for item in items if item.get("kind"))
        if close > upper:
            side = "support"
        elif close < lower:
            side = "resistance"
        else:
            side = "at_price"
        confluence = len(timeframes) + len(source_types)
        raw_score = (
            8 * min(stats["touch_visits"], 5)
            + 7 * min(confluence, 5)
            + 4 * min(len(items), 6)
            + 7 * ("monthly" in timeframes)
            + 4 * ("weekly" in timeframes)
            + 6 * any(source in source_types for source in ("platform_edge", "limit_up_anchor", "volume_profile"))
        )
        zones.append(
            {
                "zone_id": f"zone:{label}",
                "lower": number(lower),
                "center": number(center),
                "upper": number(upper),
                "side": side,
                "distance_pct": number((center / close - 1) * 100),
                "timeframes": timeframes,
                "source_types": source_types,
                "source_count": len(items),
                "sources": [str(item.get("source", item.get("source_type", "unknown"))) for item in items],
                "pivot_kinds": dict(pivot_kinds),
                **stats,
                "evidence_score": round(min(100.0, raw_score), 2),
                "score_kind": "uncalibrated_structure_evidence",
            }
        )

    zones.sort(key=lambda item: (-item["evidence_score"], abs(item["distance_pct"])))
    selected = zones[:max_zones]
    selected.sort(key=lambda item: item["center"])
    return selected


def candidates_from_pivots(pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # A date can be detected at several scales. It remains one historical test,
    # so retain the most prominent representation instead of inflating confluence.
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in pivots:
        key = (item["timeframe"], item["trade_date"], item["kind"])
        old = deduped.get(key)
        if old is None or float(item.get("prominence_atr") or 0) > float(old.get("prominence_atr") or 0):
            deduped[key] = item
    return [
        {
            "price": item["price"],
            "kind": item["kind"],
            "timeframe": item["timeframe"],
            "source_type": "pivot",
            "source": f"{item['timeframe']} {item['scale']} {item['kind']} {item['trade_date']}",
        }
        for item in deduped.values()
    ]
