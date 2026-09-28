"""Render daily and multi-timeframe static-structure charts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

from .common import normalize_frame


TIMEFRAME_RECORDS = {
    "daily": "daily",
    "weekly": "weekly_from_truncated_daily",
    "monthly": "monthly_from_truncated_daily",
}
TIMEFRAME_BARS = {"daily": 260, "weekly": 180, "monthly": 72}
COMBINED_TIMEFRAME_BARS = {"daily": 180, "weekly": 180, "monthly": 72}
DAILY_TREND_WINDOWS = {"major": 750, "intermediate": 360, "minor": 180}
TIMEFRAME_MAS = {
    "daily": ((20, "#9467bd"), (60, "#1f77b4"), (120, "#8c564b"), (250, "#34495e")),
    "weekly": ((5, "#9467bd"), (20, "#1f77b4"), (60, "#8c564b")),
    "monthly": ((3, "#9467bd"), (12, "#1f77b4"), (24, "#8c564b")),
}


def _draw_candles(ax: plt.Axes, frame: pd.DataFrame, *, width: float = 0.62) -> None:
    for index, row in frame.iterrows():
        color = "#d62728" if row.close >= row.open else "#2ca02c"
        ax.vlines(index, row.low, row.high, color=color, linewidth=0.75, zorder=3)
        bottom = min(row.open, row.close)
        height = max(abs(row.close - row.open), max(row.close, 0.01) * 0.0005)
        ax.add_patch(Rectangle((index - width / 2, bottom), width, height, facecolor=color, edgecolor=color, linewidth=0.45, zorder=4))


def _zones_for_timeframe(analysis: dict[str, Any], frame: pd.DataFrame, timeframe: str) -> list[dict[str, Any]]:
    hierarchy = {"daily": {"daily", "weekly", "monthly"}, "weekly": {"weekly", "monthly"}, "monthly": {"monthly"}}
    visible_low = float(frame["low"].min()) * 0.88
    visible_high = float(frame["high"].max()) * 1.12
    candidates = [
        zone
        for zone in analysis.get("horizontal_zones", [])
        if visible_low <= float(zone["center"]) <= visible_high
        and set(zone.get("timeframes", [])) & hierarchy[timeframe]
    ]
    limit = 9 if timeframe == "daily" else (8 if timeframe == "weekly" else 6)
    return sorted(candidates, key=lambda item: item["evidence_score"], reverse=True)[:limit]


def _pattern_end(item: dict[str, Any]) -> str:
    dates = item.get("pivot_dates") or []
    return str(item.get("flag_end") or item.get("end_date") or (max(dates) if dates else ""))


def _status_suffix(status: str) -> str:
    if status in {"forming", "awaiting_breakout", "active"}:
        return "?"
    if status in {"broken_up", "breakout_up", "confirmed_up"}:
        return "↑"
    if status in {"broken_down", "breakout_down", "confirmed_down"}:
        return "↓"
    if status.startswith("confirmed"):
        return "✓"
    if status in {"failed", "invalidated"}:
        return "×"
    return ""


def _recent_patterns(analysis: dict[str, Any], frame: pd.DataFrame, timeframe: str, *, limit: int) -> list[dict[str, Any]]:
    cutoff_bars = {"daily": 180, "weekly": 100, "monthly": 48}[timeframe]
    cutoff = str(frame.iloc[max(0, len(frame) - cutoff_bars)]["trade_date"])
    candidates = [
        item
        for item in analysis.get("chart_patterns", [])
        if item.get("timeframe", "daily") == timeframe and _pattern_end(item) >= cutoff
    ]
    latest_by_type: dict[str, dict[str, Any]] = {}
    for item in sorted(candidates, key=_pattern_end):
        latest_by_type[str(item["pattern_type"])] = item
    return sorted(latest_by_type.values(), key=_pattern_end)[-limit:]


def _draw_pattern_geometry(
    ax: plt.Axes,
    frame: pd.DataFrame,
    analysis: dict[str, Any],
    timeframe: str,
    *,
    first_original_index: int,
) -> None:
    date_to_x = {str(date): index for index, date in enumerate(frame["trade_date"])}
    patterns = _recent_patterns(analysis, frame, timeframe, limit=5)
    colors = ("#6c3483", "#117864", "#b03a2e", "#7d6608", "#1f618d")
    for pattern_index, pattern in enumerate(patterns):
        color = colors[pattern_index % len(colors)]
        pattern_type = str(pattern["pattern_type"])
        suffix = _status_suffix(str(pattern.get("status", "")))
        pivot_dates = pattern.get("pivot_dates") or []
        pivot_prices = pattern.get("pivot_prices") or []
        plotted = False
        if pivot_dates and len(pivot_dates) == len(pivot_prices):
            points = [(date_to_x[date], float(price)) for date, price in zip(pivot_dates, pivot_prices) if date in date_to_x]
            if len(points) >= 2:
                xs, ys = zip(*points)
                ax.plot(xs, ys, color=color, linewidth=1.5, marker="o", markersize=3.0, zorder=8)
                if pattern.get("neckline") is not None:
                    ax.hlines(float(pattern["neckline"]), min(xs), len(frame) - 1, color=color, linestyle=":", linewidth=1.0)
                ax.text(xs[-1], ys[-1], f" {pattern_type}{suffix}", color=color, fontsize=6.8, va="bottom")
                plotted = True
        elif pattern_type in {"ascending_triangle", "descending_triangle", "symmetrical_triangle", "rising_wedge", "falling_wedge"}:
            upper_dates = pattern.get("upper_anchor_dates") or []
            lower_dates = pattern.get("lower_anchor_dates") or []
            visible_dates = [date for date in upper_dates + lower_dates if date in date_to_x]
            if visible_dates and pattern.get("upper_slope_log_per_bar") is not None:
                start = min(date_to_x[date] for date in visible_dates)
                x_values = np.arange(start, len(frame))
                global_x = x_values + first_original_index
                upper = np.exp(float(pattern["upper_slope_log_per_bar"]) * global_x + float(pattern["upper_intercept_log"]))
                lower = np.exp(float(pattern["lower_slope_log_per_bar"]) * global_x + float(pattern["lower_intercept_log"]))
                ax.plot(x_values, upper, color=color, linewidth=1.4)
                ax.plot(x_values, lower, color=color, linewidth=1.4)
                ax.fill_between(x_values, lower, upper, color=color, alpha=0.05)
                ax.text(x_values[-1], upper[-1], f" {pattern_type}{suffix}", color=color, fontsize=6.8, va="bottom")
                plotted = True
        elif pattern_type in {"bull_flag", "bear_flag"}:
            pole_start = date_to_x.get(str(pattern.get("pole_start")))
            pole_end = date_to_x.get(str(pattern.get("pole_end")))
            flag_start = date_to_x.get(str(pattern.get("flag_start")))
            flag_end = date_to_x.get(str(pattern.get("flag_end")))
            if None not in {pole_start, pole_end, flag_start, flag_end}:
                ax.plot(
                    [pole_start, pole_end],
                    [float(frame.iloc[pole_start]["close"]), float(frame.iloc[pole_end]["close"])],
                    color=color,
                    linewidth=2.0,
                    zorder=8,
                )
                ax.add_patch(
                    Rectangle(
                        (flag_start, float(pattern["lower"])),
                        max(1, flag_end - flag_start),
                        float(pattern["upper"]) - float(pattern["lower"]),
                        facecolor=color,
                        edgecolor=color,
                        alpha=0.08,
                        linewidth=1.2,
                    )
                )
                ax.text(flag_end, float(pattern["upper"]), f" {pattern_type}{suffix}", color=color, fontsize=6.8, va="bottom")
                plotted = True
        elif pattern_type == "rectangle":
            start = date_to_x.get(str(pattern.get("start_date")))
            end = date_to_x.get(str(pattern.get("end_date")))
            if start is not None and end is not None:
                ax.add_patch(
                    Rectangle(
                        (start, float(pattern["lower"])),
                        max(1, end - start),
                        float(pattern["upper"]) - float(pattern["lower"]),
                        facecolor=color,
                        edgecolor=color,
                        alpha=0.07,
                        linewidth=1.2,
                    )
                )
                ax.text(end, float(pattern["upper"]), f" {pattern_type}{suffix}", color=color, fontsize=6.8, va="bottom")
                plotted = True
        if not plotted:
            end_date = _pattern_end(pattern)
            x = date_to_x.get(end_date)
            if x is not None:
                ax.text(x, float(frame.iloc[x]["high"]) * 1.01, f"{pattern_type}{suffix}", color=color, fontsize=6.5, rotation=15)


def _draw_candlestick_pattern_markers(
    ax: plt.Axes,
    frame: pd.DataFrame,
    analysis: dict[str, Any],
    timeframe: str,
) -> None:
    date_to_x = {str(date): index for index, date in enumerate(frame["trade_date"])}
    lookback = {"daily": 80, "weekly": 40, "monthly": 24}[timeframe]
    cutoff = str(frame.iloc[max(0, len(frame) - lookback)]["trade_date"])
    priority = {
        "bullish_engulfing": 5,
        "bearish_engulfing": 5,
        "hammer_shape": 4,
        "shooting_star_shape": 4,
        "doji": 3,
        "long_bull_body": 2,
        "long_bear_body": 2,
    }
    codes = {
        "bullish_engulfing": "BE+",
        "bearish_engulfing": "BE-",
        "hammer_shape": "H?",
        "shooting_star_shape": "SS?",
        "doji": "D",
        "long_bull_body": "LB+",
        "long_bear_body": "LB-",
    }
    by_date: dict[str, dict[str, Any]] = {}
    for item in analysis.get("candlestick_patterns", []):
        if item.get("timeframe", "daily") != timeframe or str(item.get("trade_date")) < cutoff:
            continue
        date = str(item["trade_date"])
        old = by_date.get(date)
        if old is None or priority.get(str(item["pattern"]), 0) > priority.get(str(old["pattern"]), 0):
            by_date[date] = item
    selected = sorted(by_date.values(), key=lambda item: str(item["trade_date"]))[-12:]
    for item in selected:
        x = date_to_x.get(str(item["trade_date"]))
        if x is None:
            continue
        direction = str(item.get("direction"))
        if direction == "bullish":
            y, marker, color, va = float(frame.iloc[x]["low"]) * 0.992, "^", "#c0392b", "top"
        elif direction == "bearish":
            y, marker, color, va = float(frame.iloc[x]["high"]) * 1.008, "v", "#1e8449", "bottom"
        else:
            y, marker, color, va = float(frame.iloc[x]["high"]) * 1.008, "D", "#7d6608", "bottom"
        ax.scatter([x], [y], marker=marker, s=28, color=color, zorder=9)
        ax.text(x, y, codes.get(str(item["pattern"]), str(item["pattern"])), fontsize=5.8, color=color, ha="center", va=va)
    ax.text(
        0.005,
        0.015,
        "K-line: BE engulfing | H hammer-shape | SS shooting-star-shape | D doji | LB long body; ? requires context",
        transform=ax.transAxes,
        fontsize=6.2,
        color="#555555",
        ha="left",
        va="bottom",
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.72, "pad": 1.5},
    )


def _draw_connected_swing_path(
    ax: plt.Axes,
    frame: pd.DataFrame,
    analysis: dict[str, Any],
    *,
    structure_level: str,
    first_original_index: int,
    labels: bool,
) -> None:
    """Draw one continuous confirmed daily swing path plus a provisional tail."""

    path = next(
        (
            item
            for item in analysis.get("swing_paths", [])
            if item.get("timeframe") == "daily" and item.get("structure_level") == structure_level
        ),
        None,
    )
    if not path:
        return
    visible_points = [
        point for point in path.get("points", []) if int(point["index"]) >= first_original_index
    ]
    earlier = [point for point in path.get("points", []) if int(point["index"]) < first_original_index]
    if earlier:
        visible_points.insert(0, earlier[-1])
    if len(visible_points) >= 2:
        xs = [int(point["index"]) - first_original_index for point in visible_points]
        ys = [float(point["price"]) for point in visible_points]
        ax.plot(
            xs,
            ys,
            color="#154360",
            linewidth=1.8,
            marker="o",
            markersize=3.0,
            zorder=8,
            label="connected confirmed swings",
        )
        for x, point in zip(xs, visible_points):
            marker = "v" if point["kind"] == "high" else "^"
            color = "#8e44ad" if point["kind"] == "high" else "#117864"
            ax.scatter([x], [float(point["price"])], marker=marker, s=24, color=color, zorder=9)
    provisional = path.get("provisional_endpoint")
    if provisional and visible_points:
        start = visible_points[-1]
        x1 = int(start["index"]) - first_original_index
        x2 = int(provisional["index"]) - first_original_index
        if x2 >= 0 and x1 < len(frame):
            ax.plot(
                [x1, x2],
                [float(start["price"]), float(provisional["price"])],
                color="#8e44ad",
                linestyle="--",
                linewidth=1.8,
                marker="o",
                markersize=2.8,
                zorder=9,
                label="provisional current leg",
            )
    if labels:
        ax.text(
            0.992,
            0.985,
            f"daily {structure_level} path | {path['pivot_scale']} pivots | solid=confirmed, dashed=provisional",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=7.0,
            color="#154360",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.72, "pad": 1.5},
        )


def _draw_price_envelope(
    ax: plt.Axes,
    frame: pd.DataFrame,
    analysis: dict[str, Any],
    *,
    structure_level: str,
    first_original_index: int,
    labels: bool,
) -> None:
    """Draw the high/low outer envelope and its selected trend segments."""

    envelope = next(
        (
            item
            for item in analysis.get("price_envelopes", [])
            if item.get("timeframe") == "daily" and item.get("structure_level") == structure_level
        ),
        None,
    )
    if not envelope:
        return
    chains: dict[str, list[dict[str, Any]]] = {
        "upper": envelope.get("upper_points", []),
        "lower": envelope.get("lower_points", []),
    }
    colors = {"upper": "#c0392b", "lower": "#239b56"}
    for side, points in chains.items():
        label_used = False
        for first, second in zip(points, points[1:]):
            x1 = int(first["index"]) - first_original_index
            x2 = int(second["index"]) - first_original_index
            if x2 < 0 or x1 >= len(frame):
                continue
            provisional = "provisional" in {first.get("status"), second.get("status")}
            ax.plot(
                [x1, x2],
                [float(first["price"]), float(second["price"])],
                color=colors[side],
                linestyle="--" if provisional else "-",
                linewidth=0.95,
                alpha=0.42,
                zorder=5,
                label=f"{side} outer envelope" if not label_used else None,
            )
            label_used = True
    upper_points = chains["upper"]
    lower_points = chains["lower"]
    if len(upper_points) >= 2 and len(lower_points) >= 2:
        global_x = np.arange(first_original_index, first_original_index + len(frame))
        upper_x = np.asarray([int(point["index"]) for point in upper_points], dtype=float)
        lower_x = np.asarray([int(point["index"]) for point in lower_points], dtype=float)
        upper_y = np.log(np.asarray([float(point["price"]) for point in upper_points], dtype=float))
        lower_y = np.log(np.asarray([float(point["price"]) for point in lower_points], dtype=float))
        upper_interp = np.exp(np.interp(global_x, upper_x, upper_y))
        lower_interp = np.exp(np.interp(global_x, lower_x, lower_y))
        valid = upper_interp >= lower_interp
        ax.fill_between(np.arange(len(frame))[valid], lower_interp[valid], upper_interp[valid], color="#7f8c8d", alpha=0.035, zorder=0)

    selected = [
        item
        for item in analysis.get("envelope_trendlines", [])
        if item.get("timeframe") == "daily" and item.get("structure_level") == structure_level
    ]
    current_selected: list[dict[str, Any]] = []
    for side in ("upper", "lower"):
        matches = [item for item in selected if item["side"] == side]
        if not matches:
            continue
        matches.sort(
            key=lambda item: (
                item["status"] not in {"active", "provisional"},
                -int(item["end_index"]),
                -float(item["evidence_score"]),
            )
        )
        current_selected.append(matches[0])
    labeled_sides: set[str] = set()
    for item in current_selected:
        start = int(item["start_index"])
        end = int(item["extension_end_index"])
        visible_start = max(start, first_original_index)
        visible_end = min(end, first_original_index + len(frame) - 1)
        if visible_start >= visible_end:
            continue
        global_indexes = np.arange(visible_start, visible_end + 1)
        local_indexes = global_indexes - first_original_index
        values = np.exp(float(item["slope_log_per_bar"]) * global_indexes + float(item["intercept_log"]))
        visible_price_low = float(frame["low"].min()) * 0.90
        visible_price_high = float(frame["high"].max()) * 1.10
        visible_fraction = float(np.mean((values >= visible_price_low) & (values <= visible_price_high)))
        if visible_fraction < 0.35:
            continue
        side = str(item["side"])
        provisional = item.get("quality_status") == "provisional"
        inactive = str(item.get("status", "")).startswith("broken") or item.get("status") == "expired"
        ax.plot(
            local_indexes,
            values,
            color=colors[side],
            linestyle="--" if provisional else (":" if inactive else "-"),
            linewidth=2.15,
            alpha=0.88 if not inactive else 0.42,
            zorder=7,
            label=f"selected {item['kind']} trendline" if side not in labeled_sides else None,
        )
        labeled_sides.add(side)
        if labels and not inactive and visible_price_low <= float(values[-1]) <= visible_price_high:
            label_index = int(local_indexes[-1])
            ax.text(
                label_index,
                float(values[-1]),
                f"{item['quality_status']} {item['kind']} ({item['touch_count']} touches) ",
                fontsize=5.9,
                color=colors[side],
                va="bottom" if side == "upper" else "top",
                ha="right",
                clip_on=True,
            )
    geometry = next(
        (
            item
            for item in analysis.get("envelope_geometry", [])
            if item.get("timeframe") == "daily" and item.get("structure_level") == structure_level
        ),
        None,
    )
    if labels and geometry:
        ax.text(
            0.992,
            0.035,
            f"envelope geometry: {geometry['geometry']} ({geometry['status']})",
            transform=ax.transAxes,
            ha="right",
            va="bottom",
            fontsize=6.6,
            color="#616a6b",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.68, "pad": 1.3},
        )


def _draw_timeframe_panel(
    ax: plt.Axes,
    frame: pd.DataFrame,
    analysis: dict[str, Any],
    timeframe: str,
    *,
    labels: bool,
    draw_volume: bool = True,
    view: str = "combined",
    trend_level: str | None = None,
) -> None:
    if view not in {"combined", "trend", "levels", "patterns"}:
        raise ValueError(f"Unknown static chart view: {view}")
    _draw_candles(ax, frame, width=0.64 if timeframe == "daily" else 0.58)
    x_values = np.arange(len(frame))
    date_to_x = {str(date): index for index, date in enumerate(frame["trade_date"])}
    all_frame = normalize_frame(analysis.get("_render_records", {}).get(timeframe, []))
    first_original_index = max(0, len(all_frame) - len(frame)) if not all_frame.empty else 0
    if view in {"combined", "trend"}:
        for window, color in TIMEFRAME_MAS[timeframe]:
            ma_source = all_frame["close"] if not all_frame.empty else frame["close"]
            ma = ma_source.rolling(window).mean().tail(len(frame)).reset_index(drop=True)
            ax.plot(x_values, ma, color=color, linewidth=0.85, alpha=0.78, label=f"MA{window}")

    if view == "trend" and timeframe == "daily" and trend_level is not None:
        _draw_price_envelope(
            ax,
            frame,
            analysis,
            structure_level=trend_level,
            first_original_index=first_original_index,
            labels=labels,
        )

    palette = {"support": "#2ca02c", "resistance": "#d62728", "at_price": "#ff7f0e"}
    visible_zones = _zones_for_timeframe(analysis, frame, timeframe) if view in {"combined", "levels"} else []
    for zone in visible_zones:
        color = palette.get(zone["side"], "#7f8c8d")
        ax.axhspan(zone["lower"], zone["upper"], color=color, alpha=0.08, zorder=0)
        ax.axhline(zone["center"], color=color, alpha=0.35, linewidth=0.65)
        if labels:
            source_label = "/".join(value[0].upper() for value in zone.get("timeframes", []))
            ax.text(
                len(frame) - 0.5,
                zone["center"],
                f" {source_label} {zone['center']:.2f}",
                fontsize=7,
                color=color,
                va="center",
                ha="left",
                clip_on=False,
            )

    if view == "levels" and timeframe == "daily":
        profile = analysis.get("daily_approx_price_profile", {})
        if profile.get("available"):
            for key, color in (("poc", "#2c3e50"), ("vah", "#5d6d7e"), ("val", "#5d6d7e")):
                value = float(profile[key])
                ax.axhline(value, color=color, linestyle=":", linewidth=0.9, alpha=0.72)
                ax.text(len(frame) - 0.5, value, f" approx {key.upper()} {value:.2f}", fontsize=6.2, color=color, va="center", ha="left", clip_on=False)

    platform_candidates = [
        item
        for item in analysis.get("platforms", [])
        if item["timeframe"] == timeframe and view in {"combined", "trend"}
        and (trend_level is None or item.get("structure_level", "intermediate") == trend_level)
    ]
    selected_platforms: list[dict[str, Any]] = []
    platform_levels = (trend_level,) if trend_level else ("major", "intermediate", "minor")
    for level in platform_levels:
        candidates = [item for item in platform_candidates if item.get("structure_level", "intermediate") == level]
        if candidates:
            selected_platforms.append(sorted(candidates, key=lambda item: (-item["evidence_score"], -item["end_index"]))[0])
    for platform in selected_platforms:
        start, end = date_to_x.get(platform["start_date"]), date_to_x.get(platform["end_date"])
        if start is None or end is None:
            continue
        level = platform.get("structure_level", "intermediate")
        linewidth = {"major": 1.5, "intermediate": 1.2, "minor": 1.0}.get(level, 1.0)
        ax.add_patch(
            Rectangle(
                (start, platform["lower"]),
                max(1, end - start),
                platform["upper"] - platform["lower"],
                facecolor="#f1c40f",
                edgecolor="#b7950b",
                alpha=0.10,
                linewidth=linewidth,
            )
        )
        if labels:
            status_symbol = "↑" if platform["status"] == "broken_up" else ("↓" if platform["status"] == "broken_down" else "")
            ax.text(start, platform["upper"], f"{level} box{status_symbol}", fontsize=6.2, color="#8a6d00", va="bottom")

    channel_palette = {"rising": "#2471a3", "falling": "#ca6f1e", "sideways": "#707b7c"}
    level_alpha = {"major": 0.025, "intermediate": 0.045, "minor": 0.075}
    level_width = {"major": 0.8, "intermediate": 1.05, "minor": 1.45}
    channel_items = analysis.get("trend_channels", []) if view in {"combined", "trend"} else []
    for channel in channel_items:
        if channel["timeframe"] != timeframe or trend_level is not None:
            continue
        start_global = int(channel["start_index"])
        start_local = max(0, start_global - first_original_index)
        if start_local >= len(frame):
            continue
        global_x = x_values + first_original_index
        local_x = global_x - start_global
        slope = float(channel["slope_log_per_bar"])
        intercept = float(channel["intercept_log_local"])
        lower = np.exp(slope * local_x + intercept + float(channel["lower_offset_log"]))
        upper = np.exp(slope * local_x + intercept + float(channel["upper_offset_log"]))
        mask = x_values >= start_local
        level = channel["structure_level"]
        color = channel_palette.get(channel["direction"], "#707b7c")
        ax.fill_between(x_values[mask], lower[mask], upper[mask], color=color, alpha=level_alpha.get(level, 0.04), zorder=1)
        ax.plot(x_values[mask], lower[mask], color=color, linewidth=level_width.get(level, 1.0), alpha=0.82, zorder=2)
        ax.plot(x_values[mask], upper[mask], color=color, linewidth=level_width.get(level, 1.0), alpha=0.82, zorder=2)
        if labels:
            symbol = "↑" if channel["status"] == "broken_up" else ("↓" if channel["status"] == "broken_down" else "")
            label_index = min(len(frame) - 1, max(start_local, int(channel["fit_end_index"]) - first_original_index))
            ax.text(
                label_index,
                upper[label_index],
                f"{level} {channel['direction']} channel{symbol}",
                fontsize=6.3,
                color=color,
                va="bottom",
                rotation=8,
            )

    gap_items = analysis.get("gaps", []) if view in {"combined", "levels"} else []
    for gap in gap_items:
        if gap["timeframe"] == timeframe and gap["status"] != "filled":
            ax.axhspan(gap["lower"], gap["upper"], color="#3498db", alpha=0.09)
            if labels and view == "levels":
                x = date_to_x.get(str(gap.get("trade_date") or gap.get("start_date")))
                if x is not None:
                    ax.text(x, gap["upper"], f"gap {gap['direction']} {gap['status']}", fontsize=6.2, color="#2471a3", va="bottom")

    line_candidates: list[dict[str, Any]] = []
    for kind in (() if trend_level is not None else ("support", "resistance")):
        matches = [
            item
            for item in analysis.get("trendlines", [])
            if item["timeframe"] == timeframe and item["kind"] == kind and view in {"combined", "trend"}
        ]
        structural = [item for item in matches if item.get("method_scope", "structural") == "structural"]
        local = [item for item in matches if item.get("method_scope") == "local"]
        local.sort(
            key=lambda item: (
                item.get("structure_level") != "minor",
                -int(item["anchor_indices"][1]),
                -item["evidence_score"],
            )
        )
        line_candidates.extend(structural[:2])
        line_candidates.extend(local[:1])
    for line in line_candidates:
        start_local = max(0, int(line["anchor_indices"][0]) - first_original_index)
        slope, intercept = float(line["slope_log_per_bar"]), float(line["intercept_log"])
        global_x = x_values + first_original_index
        values = np.exp(slope * global_x + intercept)
        mask = x_values >= start_local
        color = "#27ae60" if line["kind"] == "support" else "#c0392b"
        is_local = line.get("method_scope") == "local"
        ax.plot(
            x_values[mask],
            values[mask],
            color=color,
            linestyle="-." if is_local else "--",
            linewidth=1.45 if is_local else 1.0,
            alpha=0.90 if is_local else 0.68,
        )
        if labels and is_local:
            label_index = max(0, min(len(frame) - 1, int(line["anchor_indices"][1]) - first_original_index))
            ax.text(
                label_index,
                values[label_index],
                f" local {line['kind']} {line['status']}",
                fontsize=6.1,
                color=color,
                va="bottom" if line["kind"] == "resistance" else "top",
                ha="left",
                rotation=8,
            )

    leg_items = analysis.get("acceleration_legs", []) if view in {"combined", "trend"} else []
    for leg in leg_items:
        if leg["timeframe"] != timeframe or (trend_level is not None and trend_level != "minor"):
            continue
        start = max(0, int(leg["start_index"]) - first_original_index)
        end = min(len(frame) - 1, int(leg["end_index"]) - first_original_index)
        if start >= end:
            continue
        segment_x = np.arange(start, end + 1)
        segment_close = frame.iloc[start : end + 1]["close"].to_numpy(dtype=float)
        color = "#8e44ad"
        ax.plot(segment_x, segment_close, color=color, linewidth=2.3, marker="o", markersize=2.5, zorder=8)
        if labels:
            ax.annotate(
                f"provisional acceleration {leg['direction']} ×{leg['slope_multiple']:.1f}",
                (end, segment_close[-1]),
                xytext=(-118, 12 if leg["direction"] == "up" else -18),
                textcoords="offset points",
                fontsize=6.7,
                color=color,
                arrowprops={"arrowstyle": "->", "color": color, "lw": 0.8},
            )

    if view == "trend" and timeframe == "daily" and trend_level is not None:
        _draw_connected_swing_path(
            ax,
            frame,
            analysis,
            structure_level=trend_level,
            first_original_index=first_original_index,
            labels=labels,
        )

    pivot_scale = {"daily": {"medium", "long"}, "weekly": {"medium", "long"}, "monthly": {"long"}}
    pivot_items = analysis.get("confirmed_pivots", []) if view in {"combined", "trend"} and trend_level is None else []
    for pivot in pivot_items:
        if pivot["timeframe"] != timeframe or pivot["scale"] not in pivot_scale[timeframe]:
            continue
        x = date_to_x.get(pivot["trade_date"])
        if x is None:
            continue
        marker, color = ("v", "#8e44ad") if pivot["kind"] == "high" else ("^", "#16a085")
        ax.scatter([x], [pivot["price"]], marker=marker, s=17, color=color, zorder=7)

    if view == "patterns":
        _draw_pattern_geometry(ax, frame, analysis, timeframe, first_original_index=first_original_index)
        _draw_candlestick_pattern_markers(ax, frame, analysis, timeframe)
    elif labels and view == "combined":
        recent_patterns = _recent_patterns(analysis, frame, timeframe, limit=3)
        for index, pattern in enumerate(recent_patterns):
            date = _pattern_end(pattern)
            x = date_to_x.get(date)
            if x is not None:
                y = float(frame.iloc[x]["high"]) * (1.015 + index * 0.012)
                suffix = _status_suffix(str(pattern.get("status", "")))
                ax.annotate(f"{pattern['pattern_type']}{suffix}", (x, y), fontsize=6.5, color="#6c3483", rotation=20)

    current_date = str(frame.iloc[-1]["trade_date"])
    current_candles = sorted(
        {
            item["pattern"]
            for item in analysis.get("candlestick_patterns", [])
            if item.get("timeframe", "daily") == timeframe and str(item.get("trade_date")) == current_date
        }
    )
    if labels and current_candles and view in {"combined", "patterns"}:
        ax.text(
            0.995,
            0.925 if timeframe in {"weekly", "monthly"} else 0.985,
            "current candle candidates: " + ", ".join(current_candles),
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=6.7,
            color="#555555",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.70, "pad": 1.5},
        )

    if timeframe == "daily" and view in {"combined", "levels"}:
        anchor_limit = 3 if view == "levels" else 5
        anchors = [item for item in analysis.get("limit_up_anchors", []) if item.get("status") != "failed"][-anchor_limit:]
        for anchor in anchors:
            x = date_to_x.get(anchor["trade_date"])
            if x is not None:
                ax.axvline(x, color="#e67e22", linewidth=0.7, alpha=0.35)
                if labels:
                    ax.text(x, anchor["anchor_lower"], "LU", color="#d35400", fontsize=6, ha="center", va="top")
                if view == "levels" and anchor.get("status") != "failed":
                    ax.add_patch(
                        Rectangle(
                            (x, float(anchor["anchor_lower"])),
                            max(1, len(frame) - 1 - x),
                            float(anchor["anchor_upper"]) - float(anchor["anchor_lower"]),
                            facecolor="#e67e22",
                            edgecolor="#d35400",
                            alpha=0.07,
                            linewidth=0.8,
                        )
                    )
                    ax.text(x, anchor["anchor_upper"], f"LU anchor {anchor['trade_date']}", fontsize=5.9, color="#d35400", va="bottom")

    if draw_volume and "vol" in frame:
        volume_ax = ax.twinx()
        colors = ["#d62728" if row.close >= row.open else "#2ca02c" for row in frame.itertuples(index=False)]
        volume_ax.bar(x_values, frame["vol"], width=0.65, color=colors, alpha=0.10, zorder=0)
        maximum = float(frame["vol"].max()) if frame["vol"].notna().any() else 1.0
        volume_ax.set_ylim(0, maximum * 5.0)
        volume_ax.set_yticks([])

    step = max(1, len(frame) // 9)
    ticks = list(range(0, len(frame), step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([frame.iloc[index]["trade_date"] for index in ticks], rotation=30, ha="right", fontsize=7)
    ax.set_ylim(float(frame["low"].min()) * 0.92, float(frame["high"].max()) * 1.08)
    ylabel = f"Daily {trend_level.title()} QFQ" if timeframe == "daily" and trend_level else f"{timeframe.title()} QFQ"
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.13)
    if view in {"combined", "trend"}:
        ax.legend(loc="upper left", ncol=4, fontsize=7)
    if timeframe in {"weekly", "monthly"}:
        unit = "week" if timeframe == "weekly" else "month"
        ax.text(
            0.995,
            0.985,
            f"last {unit} bar is truncated at {analysis.get('as_of')}",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=7,
            color="#7f8c8d",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.70, "pad": 1.5},
        )


def render_multitimeframe_structure(
    packet: dict[str, Any],
    analysis: dict[str, Any],
    output: Path,
) -> Path:
    frames = {
        timeframe: normalize_frame(packet.get(key, [])).tail(COMBINED_TIMEFRAME_BARS[timeframe]).reset_index(drop=True)
        for timeframe, key in TIMEFRAME_RECORDS.items()
    }
    if any(frame.empty for frame in frames.values()):
        raise ValueError("Daily, weekly and monthly frames are required")
    render_analysis = dict(analysis)
    render_analysis["_render_records"] = {
        timeframe: normalize_frame(packet.get(key, [])).to_dict("records") for timeframe, key in TIMEFRAME_RECORDS.items()
    }
    fig, axes = plt.subplots(3, 1, figsize=(17, 16), gridspec_kw={"hspace": 0.26})
    for ax, timeframe in zip(axes, ("monthly", "weekly", "daily")):
        _draw_timeframe_panel(ax, frames[timeframe], render_analysis, timeframe, labels=True)
    fig.suptitle(f"{analysis.get('ts_code')} multi-timeframe static structure through {analysis.get('as_of')}", fontsize=16)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return output


def render_split_analysis_views(
    packet: dict[str, Any],
    analysis: dict[str, Any],
    output_dir: Path,
) -> dict[str, Path]:
    """Render three uncluttered multi-timeframe views over the same evidence."""

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        timeframe: normalize_frame(packet.get(key, [])).tail(COMBINED_TIMEFRAME_BARS[timeframe]).reset_index(drop=True)
        for timeframe, key in TIMEFRAME_RECORDS.items()
    }
    if any(frame.empty for frame in frames.values()):
        raise ValueError("Daily, weekly and monthly frames are required")
    render_analysis = dict(analysis)
    render_analysis["_render_records"] = {
        timeframe: normalize_frame(packet.get(key, [])).to_dict("records") for timeframe, key in TIMEFRAME_RECORDS.items()
    }
    specifications = {
        "levels": ("02_support_resistance_gaps.jpg", "Support, resistance, gaps and limit-up anchors"),
        "patterns": ("03_patterns_candlesticks.jpg", "Chart patterns and key candlesticks"),
    }
    outputs: dict[str, Path] = {}
    daily_all = normalize_frame(packet.get("daily", []))
    fig, axes = plt.subplots(3, 1, figsize=(17, 16), gridspec_kw={"hspace": 0.26})
    for ax, level in zip(axes, ("major", "intermediate", "minor")):
        daily_frame = daily_all.tail(min(DAILY_TREND_WINDOWS[level], len(daily_all))).reset_index(drop=True)
        _draw_timeframe_panel(
            ax,
            daily_frame,
            render_analysis,
            "daily",
            labels=True,
            view="trend",
            trend_level=level,
        )
    fig.suptitle(
        f"{analysis.get('ts_code')} | Daily outer envelopes, connected swings and trendlines | through {analysis.get('as_of')}",
        fontsize=15,
    )
    trend_target = output_dir / "01_trend_channels_boxes.jpg"
    fig.savefig(trend_target, dpi=160, bbox_inches="tight")
    plt.close(fig)
    outputs["trend"] = trend_target
    for view, (filename, title) in specifications.items():
        fig, axes = plt.subplots(3, 1, figsize=(17, 16), gridspec_kw={"hspace": 0.26})
        for ax, timeframe in zip(axes, ("monthly", "weekly", "daily")):
            _draw_timeframe_panel(ax, frames[timeframe], render_analysis, timeframe, labels=True, view=view)
        fig.suptitle(f"{analysis.get('ts_code')} | {title} | through {analysis.get('as_of')}", fontsize=15)
        target = output_dir / filename
        fig.savefig(target, dpi=160, bbox_inches="tight")
        plt.close(fig)
        outputs[view] = target
    return outputs


def render_timeframe_details(
    packet: dict[str, Any],
    analysis: dict[str, Any],
    output_dir: Path,
) -> dict[str, Path]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}
    render_analysis = dict(analysis)
    render_analysis["_render_records"] = {
        timeframe: normalize_frame(packet.get(key, [])).to_dict("records") for timeframe, key in TIMEFRAME_RECORDS.items()
    }
    for timeframe, key in TIMEFRAME_RECORDS.items():
        frame = normalize_frame(packet.get(key, [])).tail(TIMEFRAME_BARS[timeframe]).reset_index(drop=True)
        fig, ax = plt.subplots(figsize=(17, 8))
        _draw_timeframe_panel(ax, frame, render_analysis, timeframe, labels=True)
        ax.set_title(f"{analysis.get('ts_code')} {timeframe} static structure through {analysis.get('as_of')}")
        target = output_dir / f"static_{analysis.get('ts_code')}_{analysis.get('as_of')}_{timeframe}.jpg"
        fig.savefig(target, dpi=160, bbox_inches="tight")
        plt.close(fig)
        outputs[timeframe] = target
    return outputs


def render_static_structure(
    packet: dict[str, Any],
    analysis: dict[str, Any],
    output: Path,
    *,
    bars: int = 260,
) -> Path:
    full_frame = normalize_frame(packet.get("daily", []))
    frame = full_frame.tail(bars).reset_index(drop=True)
    if frame.empty:
        raise ValueError("Cannot render an empty packet")
    fig, (price_ax, volume_ax) = plt.subplots(
        2,
        1,
        figsize=(16, 10),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04},
    )
    render_analysis = dict(analysis)
    render_analysis["_render_records"] = {"daily": full_frame.to_dict("records")}
    _draw_timeframe_panel(price_ax, frame, render_analysis, "daily", labels=True, draw_volume=False)
    colors = ["#d62728" if row.close >= row.open else "#2ca02c" for row in frame.itertuples(index=False)]
    x_values = np.arange(len(frame))
    volume_ax.bar(x_values, frame.get("vol", pd.Series(0, index=frame.index)), width=0.68, color=colors, alpha=0.65)
    tick_step = max(1, len(frame) // 12)
    ticks = list(range(0, len(frame), tick_step))
    volume_ax.set_xticks(ticks)
    volume_ax.set_xticklabels([frame.iloc[index]["trade_date"] for index in ticks], rotation=35, ha="right")
    price_ax.set_title(f"{analysis.get('ts_code')} static structure through {analysis.get('as_of')}")
    price_ax.tick_params(labelbottom=False)
    volume_ax.set_ylabel("Volume")
    volume_ax.grid(alpha=0.12)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return output
