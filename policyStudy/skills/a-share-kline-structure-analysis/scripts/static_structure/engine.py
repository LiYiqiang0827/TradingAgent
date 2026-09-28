"""Orchestrate static structure detectors over a frozen K-line packet."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .candlesticks import detect_candlestick_patterns
from .common import json_ready, normalize_frame
from .consolidation import detect_platforms
from .envelopes import classify_envelope_geometry, detect_multiscale_envelopes, select_envelope_trendlines
from .gaps import detect_gaps
from .limit_up import detect_limit_up_anchors
from .patterns import detect_patterns
from .pivots import detect_pivots, provisional_extremes
from .price_profile import daily_approx_profile
from .trendlines import build_connected_swing_paths, detect_acceleration_legs, detect_trend_channels, detect_trendlines
from .zones import build_zones, candidates_from_pivots


def _timeframes(packet: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    daily = normalize_frame(packet.get("daily", []))
    weekly = normalize_frame(packet.get("weekly_from_truncated_daily", []))
    monthly = normalize_frame(packet.get("monthly_from_truncated_daily", []))
    return daily, weekly, monthly


def _candidate(price: float, source_type: str, source: str, timeframe: str = "daily", kind: str | None = None) -> dict[str, Any]:
    return {"price": float(price), "source_type": source_type, "source": source, "timeframe": timeframe, "kind": kind}


def analyze_packet(packet: dict[str, Any], *, official_limit_dates: set[str] | None = None) -> dict[str, Any]:
    if not packet.get("future_isolated"):
        raise ValueError("Packet must declare future_isolated=true")
    as_of = str(packet.get("as_of", "")).replace("-", "")
    daily, weekly, monthly = _timeframes(packet)
    if daily.empty or str(daily.iloc[-1]["trade_date"]) != as_of:
        raise ValueError("Daily packet is not aligned to as_of")
    for frame in (daily, weekly, monthly):
        if not frame.empty and str(frame.iloc[-1]["trade_date"]) > as_of:
            raise ValueError("A timeframe contains data after as_of")

    daily_pivots = detect_pivots(daily, timeframe="daily")
    weekly_pivots = detect_pivots(weekly, timeframe="weekly")
    monthly_pivots = detect_pivots(monthly, timeframe="monthly")
    pivots = daily_pivots + weekly_pivots + monthly_pivots
    provisional = (
        provisional_extremes(daily, timeframe="daily")
        + provisional_extremes(weekly, timeframe="weekly")
        + provisional_extremes(monthly, timeframe="monthly")
    )

    daily_gaps = detect_gaps(daily, timeframe="daily")
    weekly_gaps = detect_gaps(weekly, timeframe="weekly")
    monthly_gaps = detect_gaps(monthly, timeframe="monthly")
    gaps = daily_gaps + weekly_gaps + monthly_gaps
    daily_platforms = detect_platforms(daily, timeframe="daily")
    weekly_platforms = detect_platforms(weekly, timeframe="weekly")
    monthly_platforms = detect_platforms(monthly, timeframe="monthly")
    platforms = daily_platforms + weekly_platforms + monthly_platforms
    # Trend paths and channels use daily bars at three observation scales.
    # Weekly/monthly bars remain evidence for horizontal levels and patterns,
    # but no longer generate independently fitted diagonal lines.
    swing_paths = build_connected_swing_paths(daily, daily_pivots, timeframe="daily")
    price_envelopes = detect_multiscale_envelopes(daily)
    envelope_trendlines = select_envelope_trendlines(price_envelopes)
    envelope_geometry = classify_envelope_geometry(price_envelopes, envelope_trendlines)
    trendlines = detect_trendlines(daily, daily_pivots, timeframe="daily")
    trend_channels = detect_trend_channels(daily, timeframe="daily")
    # Acceleration needs consecutive completed bars. The final weekly/monthly
    # bars are truncated at as_of, so only daily bars are eligible here.
    acceleration_legs = detect_acceleration_legs(daily, trend_channels, timeframe="daily")
    profile = daily_approx_profile(daily.tail(min(250, len(daily))))
    limit_anchors = detect_limit_up_anchors(daily, official_dates=official_limit_dates)
    patterns = (
        detect_patterns(daily, daily_pivots, daily_platforms, timeframe="daily")
        + detect_patterns(weekly, weekly_pivots, weekly_platforms, timeframe="weekly")
        + detect_patterns(monthly, monthly_pivots, monthly_platforms, timeframe="monthly")
    )
    candle_patterns = (
        detect_candlestick_patterns(daily, timeframe="daily")
        + detect_candlestick_patterns(weekly, timeframe="weekly", lookback=60)
        + detect_candlestick_patterns(monthly, timeframe="monthly", lookback=36)
    )

    zone_candidates = candidates_from_pivots(pivots)
    latest = packet.get("latest", {})
    for window in (20, 60, 120, 250):
        value = latest.get(f"ma{window}")
        if value is not None:
            zone_candidates.append(_candidate(value, "moving_average", f"MA{window}"))
    for item in gaps:
        if item["status"] != "filled":
            zone_candidates.append(_candidate(item["lower"], "gap_edge", f"{item['timeframe']} {item['direction']} gap lower", item["timeframe"]))
            zone_candidates.append(_candidate(item["upper"], "gap_edge", f"{item['timeframe']} {item['direction']} gap upper", item["timeframe"]))
    for item in platforms:
        zone_candidates.append(_candidate(item["lower"], "platform_edge", f"{item['timeframe']} platform lower {item['start_date']}", item["timeframe"], "low"))
        zone_candidates.append(_candidate(item["upper"], "platform_edge", f"{item['timeframe']} platform upper {item['start_date']}", item["timeframe"], "high"))
    for item in limit_anchors:
        zone_candidates.append(_candidate(item["anchor_lower"], "limit_up_anchor", f"limit-up launch lower {item['trade_date']}"))
        zone_candidates.append(_candidate(item["anchor_upper"], "limit_up_anchor", f"limit-up launch upper {item['trade_date']}"))
    if profile.get("available"):
        for key in ("poc", "vah", "val"):
            zone_candidates.append(_candidate(profile[key], "volume_profile", f"daily approximate {key.upper()}"))
    zones = build_zones(daily, zone_candidates)

    warnings = list(packet.get("warnings", []))
    warnings.append("Volume-at-price is a daily-bar approximation until minute/tick history is available.")
    if official_limit_dates is None:
        warnings.append("Limit-up anchors use a daily percentage heuristic because official limit rows were not supplied.")
    result = {
        "schema": "a_share_static_structure.v1",
        "ts_code": packet.get("ts_code"),
        "stock_name": packet.get("stock_name"),
        "as_of": as_of,
        "future_isolated": True,
        "price_basis": packet.get("price_basis", "qfq"),
        "volume_basis": packet.get("volume_basis", "raw_actual"),
        "warnings": warnings,
        "timeframe_bar_counts": {"daily": len(daily), "weekly": len(weekly), "monthly": len(monthly)},
        "confirmed_pivots": pivots,
        "provisional_extremes": provisional,
        "horizontal_zones": zones,
        "gaps": gaps,
        "platforms": platforms,
        "swing_paths": swing_paths,
        "price_envelopes": price_envelopes,
        "envelope_trendlines": envelope_trendlines,
        "envelope_geometry": envelope_geometry,
        "trendlines": trendlines,
        "trend_channels": trend_channels,
        "acceleration_legs": acceleration_legs,
        "chart_patterns": patterns,
        "candlestick_patterns": candle_patterns,
        "limit_up_anchors": limit_anchors,
        "daily_approx_price_profile": profile,
        "detector_notes": {
            "zones": "Complete-linkage agglomerative clustering groups heterogeneous price evidence in log-price space without chain-merging distant levels.",
            "swing_paths": "Daily-only major/intermediate/minor paths connect alternating confirmed pivots; the final move to current close is explicitly provisional.",
            "price_envelopes": "Daily-only upper/lower monotone outer hulls use every candle high and low at 750/360/180-bar scales; recent endpoints remain provisional.",
            "envelope_trendlines": "Trendline segments are selected after envelope segmentation using ATR touch zones, touch clusters, endpoint confirmation and three-close break rules.",
            "trendlines": "Daily-only pivot-pair support/resistance evidence retained for machine-readable checks; split trend charts prioritize connected swing paths.",
            "trend_channels": "Daily-only robust Theil-Sen midline with parallel OHLC envelopes, evaluated separately at major, intermediate and minor horizons.",
            "acceleration_legs": "Provisional only: at least two consecutive closes outside a fitted channel with a materially steeper close slope.",
            "patterns": "Deterministic pivot geometry; forming/awaiting states are not presented as confirmed patterns.",
            "scores": "Evidence scores are uncalibrated ranking values, not probabilities.",
        },
    }
    return json_ready(result)
