"""Multi-scale trendline, rolling channel and acceleration detection."""

from __future__ import annotations

from itertools import combinations
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import theilslopes

from .common import finite, latest_atr_pct, number


def structure_level(timeframe: str, span_bars: int) -> str:
    """Map bar span to a comparable visual hierarchy inside each timeframe."""

    limits = {"daily": (45, 120), "weekly": (18, 52), "monthly": (12, 36)}
    minor_limit, intermediate_limit = limits.get(timeframe, limits["daily"])
    if span_bars <= minor_limit:
        return "minor"
    if span_bars <= intermediate_limit:
        return "intermediate"
    return "major"


def _dedupe_pivots(
    pivots: Iterable[dict[str, Any]],
    *,
    timeframe: str,
    kind: str,
    scales: set[str],
    cutoff_index: int = 0,
) -> list[dict[str, Any]]:
    selected = [
        item
        for item in pivots
        if item["timeframe"] == timeframe
        and item["kind"] == kind
        and item["scale"] in scales
        and int(item["index"]) >= cutoff_index
    ]
    by_date: dict[str, dict[str, Any]] = {}
    for item in selected:
        old = by_date.get(item["trade_date"])
        if old is None or float(item.get("prominence_atr") or 0) > float(old.get("prominence_atr") or 0):
            by_date[item["trade_date"]] = item
    return sorted(by_date.values(), key=lambda item: item["index"])


def build_connected_swing_paths(
    frame: pd.DataFrame,
    pivots: list[dict[str, Any]],
    *,
    timeframe: str = "daily",
) -> list[dict[str, Any]]:
    """Build continuous, alternating swing paths from confirmed pivots.

    These paths describe how price travelled from one confirmed turning point
    to the next.  They are deliberately different from an indefinitely
    extended support/resistance fit.  A final dashed segment to the latest
    close is exposed separately because that endpoint is not yet a confirmed
    pivot.
    """

    if frame.empty or timeframe != "daily":
        return []
    scale_by_level = {"major": "long", "intermediate": "medium", "minor": "short"}
    output: list[dict[str, Any]] = []
    for level, scale in scale_by_level.items():
        candidates = [
            item
            for item in pivots
            if item.get("timeframe") == timeframe and item.get("scale") == scale
        ]
        candidates.sort(key=lambda item: (int(item["index"]), item["kind"]))
        alternating: list[dict[str, Any]] = []
        for item in candidates:
            point = {
                "trade_date": str(item["trade_date"]),
                "confirmed_on": str(item["confirmed_on"]),
                "index": int(item["index"]),
                "price": number(float(item["price"])),
                "kind": str(item["kind"]),
            }
            if not alternating:
                alternating.append(point)
                continue
            previous = alternating[-1]
            if point["kind"] != previous["kind"]:
                alternating.append(point)
                continue
            # Consecutive same-side pivots do not form a new swing. Keep only
            # the more extreme endpoint so every confirmed segment connects a
            # high to a low or a low to a high.
            more_extreme = (
                point["price"] >= previous["price"]
                if point["kind"] == "high"
                else point["price"] <= previous["price"]
            )
            if more_extreme:
                alternating[-1] = point
        if len(alternating) < 2:
            continue
        last = alternating[-1]
        provisional = None
        latest_index = len(frame) - 1
        if int(last["index"]) < latest_index:
            provisional = {
                "trade_date": str(frame.iloc[-1]["trade_date"]),
                "index": latest_index,
                "price": number(float(frame.iloc[-1]["close"])),
                "kind": "current_close",
                "status": "provisional",
            }
        output.append(
            {
                "swing_path_id": f"{timeframe}:{level}:{scale}",
                "timeframe": timeframe,
                "structure_level": level,
                "pivot_scale": scale,
                "method": "connected_confirmed_pivots",
                "points": alternating,
                "provisional_endpoint": provisional,
                "confirmed_segments": len(alternating) - 1,
                "status": "confirmed_path_with_provisional_tail" if provisional else "confirmed_path",
            }
        )
    return output


def _fit_candidates(
    frame: pd.DataFrame,
    pivots: list[dict[str, Any]],
    *,
    kind: str,
    timeframe: str,
    max_results: int,
    scales: set[str],
    min_touches: int,
    point_limit: int,
    lookback_bars: int | None,
    method_scope: str,
) -> list[dict[str, Any]]:
    cutoff = max(0, len(frame) - lookback_bars) if lookback_bars else 0
    points = _dedupe_pivots(pivots, timeframe=timeframe, kind=kind, scales=scales, cutoff_index=cutoff)[-point_limit:]
    if len(points) < min_touches:
        return []
    tolerance_log = max(0.006, min(0.025, latest_atr_pct(frame) * 0.45))
    results: list[dict[str, Any]] = []
    min_gap = 3 if method_scope == "local" else 5
    for first, second in combinations(points, 2):
        i, j = int(first["index"]), int(second["index"])
        if j - i < min_gap:
            continue
        y1, y2 = np.log(float(first["price"])), np.log(float(second["price"]))
        slope = (y2 - y1) / (j - i)
        slope_pct = (np.exp(slope) - 1) * 100
        if method_scope == "local" and abs(slope_pct) > 4.0:
            continue
        intercept = y1 - slope * i
        touch_points: list[dict[str, Any]] = []
        residuals: list[float] = []
        for point in points:
            index = int(point["index"])
            if index < i:
                continue
            residual = abs(np.log(float(point["price"])) - (slope * index + intercept))
            if residual <= tolerance_log:
                touch_points.append(point)
                residuals.append(residual)
        if len(touch_points) < min_touches:
            continue
        violations = 0
        violation_date = None
        for index in range(j + 1, len(frame)):
            line_price = float(np.exp(slope * index + intercept))
            close = float(frame.iloc[index]["close"])
            violated = close < line_price * (1 - tolerance_log) if kind == "low" else close > line_price * (1 + tolerance_log)
            if violated:
                violations += 1
                violation_date = violation_date or str(frame.iloc[index]["trade_date"])
        current_value = float(np.exp(slope * (len(frame) - 1) + intercept))
        latest_close = float(frame.iloc[-1]["close"])
        if current_value < latest_close * 0.60 or current_value > latest_close * 1.60:
            continue
        if violations == 0:
            status = "active"
            break_direction = None
        elif kind == "low":
            status = "broken_down"
            break_direction = "down"
        else:
            status = "broken_up"
            break_direction = "up"
        span = int(touch_points[-1]["index"] - touch_points[0]["index"])
        mean_residual = float(np.mean(residuals)) if residuals else tolerance_log
        bars_since_anchor = len(frame) - 1 - j
        recency_bonus = max(0.0, 18.0 - bars_since_anchor * 0.55) if method_scope == "local" else 0.0
        violation_penalty = 8 if method_scope == "local" else 12
        score = (
            15 * min(len(touch_points), 5)
            + min(span, 100) * 0.25
            + max(0, 25 * (1 - mean_residual / tolerance_log))
            + recency_bonus
            - violation_penalty * violations
        )
        results.append(
            {
                "trendline_id": f"{method_scope}:{kind}:{first['trade_date']}:{second['trade_date']}",
                "kind": "support" if kind == "low" else "resistance",
                "timeframe": timeframe,
                "structure_level": structure_level(timeframe, max(span, j - i)),
                "method_scope": method_scope,
                "method": "confirmed_pivot_pair",
                "anchor_dates": [first["trade_date"], second["trade_date"]],
                "anchor_indices": [i, j],
                "touch_dates": [item["trade_date"] for item in touch_points],
                "touch_count": len(touch_points),
                "span_bars": span,
                "slope_log_per_bar": number(slope, 9),
                "slope_pct_per_bar": number(slope_pct),
                "intercept_log": number(intercept, 9),
                "current_value": number(current_value),
                "next_value": number(float(np.exp(slope * len(frame) + intercept))),
                "mean_error_pct": number(mean_residual * 100),
                "violations": violations,
                "first_violation_date": violation_date,
                "break_direction": break_direction,
                "status": status,
                "evidence_score": number(max(0, min(100, score)), 2),
                "score_kind": "uncalibrated_structure_evidence",
            }
        )
    results.sort(
        key=lambda item: (
            item["status"] != "active",
            -item["evidence_score"],
            -item["anchor_indices"][1] if method_scope == "local" else -item["span_bars"],
        )
    )
    deduped: list[dict[str, Any]] = []
    for item in results:
        duplicate = any(
            abs(item["current_value"] / old["current_value"] - 1) < tolerance_log
            and abs(item["slope_pct_per_bar"] - old["slope_pct_per_bar"]) < 0.08
            for old in deduped
        )
        if not duplicate:
            deduped.append(item)
        if len(deduped) >= max_results:
            break
    return deduped


def detect_trendlines(
    frame: pd.DataFrame,
    pivots: list[dict[str, Any]],
    *,
    timeframe: str = "daily",
    max_per_side: int = 4,
) -> list[dict[str, Any]]:
    """Return structural and recent local pivot trendlines."""

    output: list[dict[str, Any]] = []
    for kind in ("low", "high"):
        output.extend(
            _fit_candidates(
                frame,
                pivots,
                kind=kind,
                timeframe=timeframe,
                max_results=max_per_side,
                scales={"medium", "long"},
                min_touches=3,
                point_limit=16,
                lookback_bars=None,
                method_scope="structural",
            )
        )
        if timeframe == "daily":
            output.extend(
                _fit_candidates(
                    frame,
                    pivots,
                    kind=kind,
                    timeframe=timeframe,
                    max_results=2,
                    scales={"short", "medium"},
                    min_touches=2,
                    point_limit=12,
                    lookback_bars=90,
                    method_scope="local",
                )
            )
    return output


def _channel_windows(timeframe: str, frame_size: int) -> tuple[int, ...]:
    defaults = {
        "daily": (20, 25, 30, 35, 40, 60, 90, 120, 180, 250),
        "weekly": (12, 18, 26, 40, 52, 80, 120),
        "monthly": (8, 12, 18, 24, 36, 48, 72),
    }
    return tuple(value for value in defaults.get(timeframe, defaults["daily"]) if value <= frame_size)


def detect_trend_channels(
    frame: pd.DataFrame,
    *,
    timeframe: str = "daily",
    max_results: int = 3,
) -> list[dict[str, Any]]:
    """Fit parallel rolling price channels using a robust Theil-Sen midline."""

    if frame.empty or len(frame) < 12:
        return []
    end_offset_limit = {"daily": 8, "weekly": 5, "monthly": 4}.get(timeframe, 6)
    candidates: list[dict[str, Any]] = []
    n = len(frame)
    for end_offset in range(end_offset_limit):
        end = n - end_offset
        for window in _channel_windows(timeframe, end):
            start = end - window
            sample = frame.iloc[start:end]
            if len(sample) < 8:
                continue
            x = np.arange(window, dtype=float)
            log_close = np.log(sample["close"].to_numpy(dtype=float))
            slope, intercept, _, _ = theilslopes(log_close, x)
            if not finite(slope) or not finite(intercept):
                continue
            mid = slope * x + intercept
            low_residual = np.log(sample["low"].to_numpy(dtype=float)) - mid
            high_residual = np.log(sample["high"].to_numpy(dtype=float)) - mid
            lower_offset = float(np.quantile(low_residual, 0.12))
            upper_offset = float(np.quantile(high_residual, 0.88))
            if upper_offset <= lower_offset:
                continue
            median_atr_pct = float(np.nanmedian(sample["atr14"] / sample["close"]))
            if not finite(median_atr_pct):
                median_atr_pct = latest_atr_pct(sample)
            tolerance_log = max(0.0045, min(0.018, median_atr_pct * 0.30))
            containment = float(
                np.mean(
                    (low_residual >= lower_offset - tolerance_log)
                    & (high_residual <= upper_offset + tolerance_log)
                )
            )
            lower_touches = int(np.sum(np.abs(low_residual - lower_offset) <= tolerance_log))
            upper_touches = int(np.sum(np.abs(high_residual - upper_offset) <= tolerance_log))
            total_sum = float(np.sum((log_close - np.mean(log_close)) ** 2))
            residual_sum = float(np.sum((log_close - mid) ** 2))
            r_squared = 1 - residual_sum / total_sum if total_sum > 0 else 0.0
            width_pct = (np.exp(upper_offset - lower_offset) - 1) * 100
            slope_pct = (np.exp(slope) - 1) * 100
            total_move_pct = (np.exp(slope * max(1, window - 1)) - 1) * 100
            if total_move_pct > 3.0:
                direction = "rising"
            elif total_move_pct < -3.0:
                direction = "falling"
            else:
                direction = "sideways"
            qualifies = (
                3.0 <= width_pct <= 28.0
                and containment >= 0.76
                and lower_touches >= 2
                and upper_touches >= 2
                and (r_squared >= 0.18 or direction == "sideways")
            )
            if not qualifies:
                continue

            breakout_status = "inside"
            breakout_date = None
            breakout_count = 0
            breakout_margin_pct = 0.0
            for index in range(end, n):
                local_x = index - start
                lower = float(np.exp(slope * local_x + intercept + lower_offset))
                upper = float(np.exp(slope * local_x + intercept + upper_offset))
                close = float(frame.iloc[index]["close"])
                if close > upper * (1 + tolerance_log):
                    side = "up"
                    margin = (close / upper - 1) * 100
                elif close < lower * (1 - tolerance_log):
                    side = "down"
                    margin = (close / lower - 1) * 100
                else:
                    side = None
                    margin = 0.0
                if side:
                    breakout_date = breakout_date or str(frame.iloc[index]["trade_date"])
                    breakout_status = f"broken_{side}"
                    breakout_count += 1
                    breakout_margin_pct = margin
                elif breakout_date:
                    breakout_status = "reentered"
                    breakout_count = 0
                    breakout_margin_pct = 0.0

            current_x = n - 1 - start
            lower_current = float(np.exp(slope * current_x + intercept + lower_offset))
            upper_current = float(np.exp(slope * current_x + intercept + upper_offset))
            mid_current = float(np.exp(slope * current_x + intercept))
            latest_close = float(frame.iloc[-1]["close"])
            if lower_current < latest_close * 0.45 or upper_current > latest_close * 1.80:
                continue
            level = structure_level(timeframe, window)
            score = (
                containment * 35
                + min(lower_touches, 4) * 6
                + min(upper_touches, 4) * 6
                + max(0.0, r_squared) * 15
                + max(0.0, 1 - abs(width_pct - 12) / 12) * 8
                + max(0, end_offset_limit - end_offset)
                + (8 if breakout_status in {"broken_up", "broken_down"} else 0)
            )
            candidates.append(
                {
                    "channel_id": f"{timeframe}:{start}:{end - 1}",
                    "timeframe": timeframe,
                    "structure_level": level,
                    "direction": direction,
                    "start_date": str(frame.iloc[start]["trade_date"]),
                    "fit_end_date": str(frame.iloc[end - 1]["trade_date"]),
                    "start_index": int(start),
                    "fit_end_index": int(end - 1),
                    "fit_bars": int(window),
                    "slope_log_per_bar": number(slope, 9),
                    "slope_pct_per_bar": number(slope_pct),
                    "intercept_log_local": number(intercept, 9),
                    "lower_offset_log": number(lower_offset, 9),
                    "upper_offset_log": number(upper_offset, 9),
                    "lower_current": number(lower_current),
                    "mid_current": number(mid_current),
                    "upper_current": number(upper_current),
                    "width_pct": number(width_pct),
                    "r_squared": number(r_squared),
                    "containment": number(containment),
                    "lower_touches": lower_touches,
                    "upper_touches": upper_touches,
                    "tolerance_log": number(tolerance_log, 9),
                    "status": breakout_status,
                    "breakout_date": breakout_date,
                    "breakout_bars": breakout_count,
                    "breakout_margin_pct": number(breakout_margin_pct),
                    "evidence_score": number(min(100.0, score), 2),
                    "score_kind": "uncalibrated_structure_evidence",
                }
            )

    selected: list[dict[str, Any]] = []
    for level in ("major", "intermediate", "minor"):
        same_level = [item for item in candidates if item["structure_level"] == level]
        if same_level:
            same_level.sort(
                key=lambda item: (
                    item["status"] not in {"broken_up", "broken_down"},
                    -int(item.get("breakout_bars", 0)),
                    -item["evidence_score"],
                    -item["fit_end_index"],
                )
            )
            selected.append(same_level[0])
        if len(selected) >= max_results:
            break
    return selected


def detect_acceleration_legs(
    frame: pd.DataFrame,
    channels: list[dict[str, Any]],
    *,
    timeframe: str = "daily",
) -> list[dict[str, Any]]:
    """Expose recent channel exits as provisional acceleration legs."""

    if frame.empty:
        return []
    output: list[dict[str, Any]] = []
    for channel in channels:
        if channel["timeframe"] != timeframe or channel["status"] not in {"broken_up", "broken_down"}:
            continue
        if channel.get("structure_level") != "minor":
            continue
        if int(channel.get("breakout_bars", 0)) < 2 or not channel.get("breakout_date"):
            continue
        matches = frame.index[frame["trade_date"].astype(str) == str(channel["breakout_date"])].tolist()
        if not matches:
            continue
        breakout_index = int(matches[0])
        start_index = max(int(channel["fit_end_index"]), breakout_index - 1)
        segment = frame.iloc[start_index:]
        if len(segment) < 3:
            continue
        x = np.arange(len(segment), dtype=float)
        log_close = np.log(segment["close"].to_numpy(dtype=float))
        slope, intercept = np.polyfit(x, log_close, 1)
        slope_pct = (np.exp(slope) - 1) * 100
        channel_slope = abs(float(channel["slope_pct_per_bar"]))
        ratio = abs(slope_pct) / max(channel_slope, 0.05)
        direction = "up" if channel["status"] == "broken_up" else "down"
        if direction == "up" and slope_pct < max(1.5, channel_slope * 2.5):
            continue
        if direction == "down" and slope_pct > -max(1.5, channel_slope * 2.5):
            continue
        global_intercept = float(intercept - slope * start_index)
        output.append(
            {
                "acceleration_id": f"{timeframe}:{direction}:{segment.iloc[0]['trade_date']}:{segment.iloc[-1]['trade_date']}",
                "timeframe": timeframe,
                "structure_level": "minor",
                "direction": direction,
                "status": "provisional_acceleration",
                "start_date": str(segment.iloc[0]["trade_date"]),
                "end_date": str(segment.iloc[-1]["trade_date"]),
                "start_index": start_index,
                "end_index": len(frame) - 1,
                "bars": len(segment),
                "slope_log_per_bar": number(slope, 9),
                "slope_pct_per_bar": number(slope_pct),
                "intercept_log": number(global_intercept, 9),
                "channel_slope_pct_per_bar": number(channel_slope),
                "slope_multiple": number(ratio),
                "source_channel_id": channel["channel_id"],
                "basis": "at least two consecutive closes outside the fitted channel with a materially steeper close slope",
            }
        )
    return output
