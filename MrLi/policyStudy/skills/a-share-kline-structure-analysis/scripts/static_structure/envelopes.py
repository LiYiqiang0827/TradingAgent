"""Daily multi-scale outer envelopes and envelope-derived trend segments."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .common import finite, number


ENVELOPE_SCALES = {
    "major": {"lookback": 750, "confirm_bars": 25, "min_span": 25},
    "intermediate": {"lookback": 360, "confirm_bars": 12, "min_span": 12},
    "minor": {"lookback": 180, "confirm_bars": 5, "min_span": 5},
}


def _cross(first: tuple[int, float], second: tuple[int, float], third: tuple[int, float]) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])


def _outer_chain(points: list[tuple[int, float]], *, side: str) -> list[tuple[int, float]]:
    """Return a time-ordered upper or lower monotone outer hull."""

    chain: list[tuple[int, float]] = []
    for point in points:
        if side == "upper":
            while len(chain) >= 2 and _cross(chain[-2], chain[-1], point) >= 0:
                chain.pop()
        else:
            while len(chain) >= 2 and _cross(chain[-2], chain[-1], point) <= 0:
                chain.pop()
        chain.append(point)
    return chain


def _cluster_contacts(indexes: list[int], residuals: list[float], *, cluster_gap: int = 2) -> list[tuple[int, float]]:
    if not indexes:
        return []
    clusters: list[list[tuple[int, float]]] = [[(indexes[0], residuals[0])]]
    for index, residual in zip(indexes[1:], residuals[1:]):
        if index - clusters[-1][-1][0] <= cluster_gap:
            clusters[-1].append((index, residual))
        else:
            clusters.append([(index, residual)])
    return [min(cluster, key=lambda item: item[1]) for cluster in clusters]


def _segment_record(
    frame: pd.DataFrame,
    *,
    side: str,
    level: str,
    start_index: int,
    end_index: int,
    confirm_bars: int,
    min_span: int,
    tolerance_log: float,
) -> dict[str, Any] | None:
    span = end_index - start_index
    if span < min_span:
        return None
    start_price = float(frame.iloc[start_index]["high" if side == "upper" else "low"])
    end_price = float(frame.iloc[end_index]["high" if side == "upper" else "low"])
    slope = (np.log(end_price) - np.log(start_price)) / span
    intercept = np.log(start_price) - slope * start_index
    contact_indexes: list[int] = []
    residuals: list[float] = []
    for index in range(start_index, end_index + 1):
        line = slope * index + intercept
        observed = np.log(float(frame.iloc[index]["high" if side == "upper" else "low"]))
        residual = max(0.0, line - observed) if side == "upper" else max(0.0, observed - line)
        if residual <= tolerance_log:
            contact_indexes.append(index)
            residuals.append(residual)
    contacts = _cluster_contacts(contact_indexes, residuals)
    if len(contacts) < 2:
        return None
    confirmed_endpoint = end_index + confirm_bars < len(frame)
    if not confirmed_endpoint:
        quality_status = "provisional"
    elif len(contacts) >= 3:
        quality_status = "confirmed"
    else:
        quality_status = "candidate"

    breakout_side = "up" if side == "upper" else "down"
    consecutive = 0
    breakout_index = None
    # A historical base segment is not allowed to project indefinitely. Match
    # the common charting convention that the extension may be at most twice
    # the base span unless price breaks it first.
    extension_limit = min(len(frame) - 1, end_index + 2 * span)
    extension_end = extension_limit
    for index in range(end_index + 1, extension_limit + 1):
        line_price = float(np.exp(slope * index + intercept))
        close = float(frame.iloc[index]["close"])
        outside = close > line_price * np.exp(tolerance_log) if side == "upper" else close < line_price / np.exp(tolerance_log)
        consecutive = consecutive + 1 if outside else 0
        if consecutive >= 3:
            breakout_index = index
            extension_end = index
            break
    if breakout_index is not None:
        lifecycle = f"broken_{breakout_side}"
    elif quality_status == "provisional":
        lifecycle = "provisional"
    elif extension_limit < len(frame) - 1:
        lifecycle = "expired"
    else:
        lifecycle = "active"
    mean_error = float(np.mean([value for _, value in contacts])) if contacts else tolerance_log
    recency = max(0.0, 1 - (len(frame) - 1 - end_index) / max(1, len(frame)))
    score = (
        min(len(contacts), 6) * 10
        + min(span, 250) / 250 * 20
        + max(0.0, 1 - mean_error / max(tolerance_log, 1e-9)) * 20
        + recency * 15
        + (10 if quality_status == "confirmed" else 0)
        - (12 if breakout_index is not None else 0)
        - (10 if lifecycle == "expired" else 0)
    )
    return {
        "segment_id": f"daily:{level}:{side}:{frame.iloc[start_index]['trade_date']}:{frame.iloc[end_index]['trade_date']}",
        "timeframe": "daily",
        "structure_level": level,
        "side": side,
        "kind": "resistance" if side == "upper" else "support",
        "start_index": start_index,
        "end_index": end_index,
        "start_date": str(frame.iloc[start_index]["trade_date"]),
        "end_date": str(frame.iloc[end_index]["trade_date"]),
        "start_price": number(start_price),
        "end_price": number(end_price),
        "span_bars": span,
        "slope_log_per_bar": number(slope, 9),
        "slope_pct_per_bar": number((np.exp(slope) - 1) * 100),
        "intercept_log": number(intercept, 9),
        "touch_count": len(contacts),
        "touch_dates": [str(frame.iloc[index]["trade_date"]) for index, _ in contacts],
        "mean_touch_error_pct": number(mean_error * 100),
        "touch_tolerance_log": number(tolerance_log, 9),
        "quality_status": quality_status,
        "status": lifecycle,
        "confirmation_date": None if not confirmed_endpoint else str(frame.iloc[end_index + confirm_bars]["trade_date"]),
        "breakout_date": None if breakout_index is None else str(frame.iloc[breakout_index]["trade_date"]),
        "extension_end_index": extension_end,
        "extension_end_date": str(frame.iloc[extension_end]["trade_date"]),
        "evidence_score": number(max(0.0, min(100.0, score)), 2),
        "score_kind": "uncalibrated_structure_evidence",
    }


def _point_record(frame: pd.DataFrame, index: int, side: str, confirm_bars: int) -> dict[str, Any]:
    confirmation_index = index + confirm_bars
    return {
        "index": index,
        "trade_date": str(frame.iloc[index]["trade_date"]),
        "price": number(float(frame.iloc[index]["high" if side == "upper" else "low"])),
        "source": "high" if side == "upper" else "low",
        "status": "confirmed" if confirmation_index < len(frame) else "provisional",
        "confirmed_on": str(frame.iloc[confirmation_index]["trade_date"]) if confirmation_index < len(frame) else None,
    }


def detect_multiscale_envelopes(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Build connected outer high/low chains at daily major/intermediate/minor scales."""

    if frame.empty or len(frame) < 30:
        return []
    output: list[dict[str, Any]] = []
    for level, config in ENVELOPE_SCALES.items():
        lookback = min(int(config["lookback"]), len(frame))
        offset = len(frame) - lookback
        sample = frame.iloc[offset:].reset_index(drop=True)
        upper_local = _outer_chain([(i, float(np.log(row.high))) for i, row in enumerate(sample.itertuples())], side="upper")
        lower_local = _outer_chain([(i, float(np.log(row.low))) for i, row in enumerate(sample.itertuples())], side="lower")
        upper_indexes = [offset + index for index, _ in upper_local]
        lower_indexes = [offset + index for index, _ in lower_local]
        atr_pct = np.asarray(frame.iloc[offset:]["atr14"] / frame.iloc[offset:]["close"], dtype=float)
        median_atr_pct = float(np.nanmedian(atr_pct))
        if not finite(median_atr_pct) or median_atr_pct <= 0:
            median_atr_pct = 0.025
        tolerance_log = max(0.004, min(0.025, median_atr_pct * 0.50))
        segments: list[dict[str, Any]] = []
        for side, indexes in (("upper", upper_indexes), ("lower", lower_indexes)):
            for start_index, end_index in zip(indexes, indexes[1:]):
                item = _segment_record(
                    frame,
                    side=side,
                    level=level,
                    start_index=start_index,
                    end_index=end_index,
                    confirm_bars=int(config["confirm_bars"]),
                    min_span=int(config["min_span"]),
                    tolerance_log=tolerance_log,
                )
                if item is not None:
                    segments.append(item)
        output.append(
            {
                "envelope_id": f"daily:{level}:{lookback}",
                "timeframe": "daily",
                "structure_level": level,
                "lookback_bars": lookback,
                "window_start": str(frame.iloc[offset]["trade_date"]),
                "window_end": str(frame.iloc[-1]["trade_date"]),
                "method": "time_price_monotone_outer_hull",
                "input_basis": "every daily high and low",
                "confirm_bars": int(config["confirm_bars"]),
                "touch_tolerance_log": number(tolerance_log, 9),
                "upper_points": [_point_record(frame, index, "upper", int(config["confirm_bars"])) for index in upper_indexes],
                "lower_points": [_point_record(frame, index, "lower", int(config["confirm_bars"])) for index in lower_indexes],
                "segments": sorted(segments, key=lambda item: (item["start_index"], item["side"])),
            }
        )
    return output


def select_envelope_trendlines(envelopes: list[dict[str, Any]], *, max_per_side: int = 2) -> list[dict[str, Any]]:
    """Select the strongest non-duplicate envelope segments as drawable trendlines."""

    selected: list[dict[str, Any]] = []
    for envelope in envelopes:
        for side in ("upper", "lower"):
            candidates = [item for item in envelope.get("segments", []) if item["side"] == side]
            candidates.sort(
                key=lambda item: (
                    item["status"] not in {"active", "provisional"},
                    item["quality_status"] == "provisional",
                    -item["evidence_score"],
                    -item["end_index"],
                )
            )
            accepted: list[dict[str, Any]] = []
            for item in candidates:
                duplicate = any(
                    abs(float(item["slope_pct_per_bar"]) - float(old["slope_pct_per_bar"])) < 0.04
                    and abs(int(item["end_index"]) - int(old["end_index"])) < 20
                    for old in accepted
                )
                if not duplicate:
                    accepted.append(item)
                if len(accepted) >= max_per_side:
                    break
            selected.extend(accepted)
    return selected


def classify_envelope_geometry(envelopes: list[dict[str, Any]], trendlines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair recent upper/lower lines and classify parallel, converging or expanding geometry."""

    output: list[dict[str, Any]] = []
    for envelope in envelopes:
        level = str(envelope["structure_level"])
        upper = [item for item in trendlines if item["structure_level"] == level and item["side"] == "upper"]
        lower = [item for item in trendlines if item["structure_level"] == level and item["side"] == "lower"]
        if not upper or not lower:
            continue
        top = max(upper, key=lambda item: (item["end_index"], item["evidence_score"]))
        bottom = max(lower, key=lambda item: (item["end_index"], item["evidence_score"]))
        upper_slope = float(top["slope_log_per_bar"])
        lower_slope = float(bottom["slope_log_per_bar"])
        slope_gap = lower_slope - upper_slope
        parallel_tolerance = max(0.00045, 0.30 * max(abs(upper_slope), abs(lower_slope), 0.0002))
        if abs(slope_gap) <= parallel_tolerance:
            geometry = "parallel_channel"
        elif slope_gap > 0:
            geometry = "converging"
        else:
            geometry = "expanding"
        start_index = max(int(top["start_index"]), int(bottom["start_index"]))
        end_index = max(int(top["extension_end_index"]), int(bottom["extension_end_index"]))
        output.append(
            {
                "geometry_id": f"daily:{level}:{geometry}:{envelope['window_end']}",
                "timeframe": "daily",
                "structure_level": level,
                "geometry": geometry,
                "upper_segment_id": top["segment_id"],
                "lower_segment_id": bottom["segment_id"],
                "start_index": start_index,
                "end_index": end_index,
                "start_date": top["start_date"] if int(top["start_index"]) >= int(bottom["start_index"]) else bottom["start_date"],
                "end_date": envelope["window_end"],
                "upper_slope_log_per_bar": number(upper_slope, 9),
                "lower_slope_log_per_bar": number(lower_slope, 9),
                "slope_gap_log_per_bar": number(slope_gap, 9),
                "status": "provisional" if "provisional" in {top["quality_status"], bottom["quality_status"]} else "active",
            }
        )
    return output
