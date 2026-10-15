from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_ma20_acceleration_around_price_peaks import (
    _parse_args,
    aggregate_peak_events,
    analyze_peak_event,
    classify_large_acceleration,
    compute_background_acceleration,
    compute_ma20_speed_features,
)


def test_cli_defaults_freeze_the_peak_acceleration_definition() -> None:
    args = _parse_args([])
    assert args.speed_window == 10
    assert args.baseline_days == 40
    assert args.lag_half_window == 5
    assert args.relative_gain_threshold == pytest.approx(0.25)
    assert args.absolute_gain_threshold_bps == pytest.approx(5.0)
    assert args.percentile_threshold == pytest.approx(0.80)


def _ma20_from_speeds(speeds_bps: list[float], scale: float = 1.0) -> pd.DataFrame:
    values = [100.0 * scale]
    for speed in speeds_bps:
        values.append(values[-1] * np.exp(speed / 10000.0))
    return pd.DataFrame(
        {
            "trade_date": pd.date_range("2026-01-01", periods=len(values), freq="D").strftime("%Y%m%d"),
            "ma20": values,
        }
    )


def test_ma20_speed_is_scale_free_and_uses_trailing_log_changes() -> None:
    daily = _ma20_from_speeds([10, 20, 30, 40, 50])
    scaled = _ma20_from_speeds([10, 20, 30, 40, 50], scale=8.0)

    result = compute_ma20_speed_features(daily, speed_window=3)
    scaled_result = compute_ma20_speed_features(scaled, speed_window=3)

    assert result.loc[3, "ma20_daily_speed_bps"] == pytest.approx(30.0)
    assert result.loc[3, "ma20_trailing_speed_bps"] == pytest.approx(20.0)
    np.testing.assert_allclose(
        result["ma20_trailing_speed_bps"],
        scaled_result["ma20_trailing_speed_bps"],
        equal_nan=True,
    )


def test_price_peak_event_compares_non_overlapping_ma20_speed_windows() -> None:
    daily = _ma20_from_speeds([10.0] * 50 + [30.0] * 20)
    daily["high"] = 100.0
    daily["close"] = 90.0
    daily.loc[[60, 65], "high"] = 150.0
    featured = compute_ma20_speed_features(daily, speed_window=10)

    result = analyze_peak_event(
        featured,
        price_col="high",
        speed_window=10,
        baseline_days=40,
        lag_half_window=5,
        relative_gain_threshold=0.25,
        absolute_gain_threshold_bps=5.0,
        percentile_threshold=0.80,
    )

    assert result["price_peak_index"] == 60
    assert result["price_peak_count"] == 2
    assert result["ma20_speed_at_peak_bps"] == pytest.approx(30.0)
    assert result["prior_baseline_speed_bps"] == pytest.approx(10.0)
    assert result["speed_gain_vs_baseline_bps"] == pytest.approx(20.0)
    assert result["relative_speed_gain_pct"] == pytest.approx(200.0)
    assert result["speed_percentile_to_peak"] == pytest.approx(1.0)
    assert result["large_acceleration_at_peak"] is True
    assert result["lag_aware_speed_peak_distance_days"] == 0


def test_large_acceleration_classifier_handles_positive_and_nonpositive_baselines() -> None:
    events = pd.DataFrame(
        {
            "baseline_valid": [True, True, True, False],
            "prior_baseline_speed_bps": [10.0, 10.0, -2.0, 10.0],
            "ma20_speed_at_peak_bps": [20.0, 12.0, 10.0, 30.0],
            "speed_gain_vs_baseline_bps": [10.0, 2.0, 12.0, 20.0],
            "speed_percentile_to_peak": [0.90, 0.95, 0.85, 1.0],
        }
    )

    mask = classify_large_acceleration(
        events,
        speed_col="ma20_speed_at_peak_bps",
        gain_col="speed_gain_vs_baseline_bps",
        percentile_col="speed_percentile_to_peak",
        relative_gain_threshold=0.25,
        absolute_gain_threshold_bps=5.0,
        percentile_threshold=0.80,
    )

    assert mask.tolist() == [True, False, True, False]


def test_aggregate_peak_events_uses_explicit_valid_denominator() -> None:
    events = pd.DataFrame(
        {
            "baseline_valid": [True, True, False],
            "large_acceleration_at_peak": [True, False, False],
            "large_acceleration_with_lag": [True, True, False],
            "ma20_speed_at_peak_bps": [20.0, -1.0, 30.0],
            "speed_gain_vs_baseline_bps": [10.0, -5.0, 20.0],
            "relative_speed_gain_pct": [100.0, -50.0, 200.0],
            "speed_percentile_to_peak": [0.9, 0.2, 1.0],
            "lag_aware_speed_peak_distance_days": [-2, 3, 0],
            "price_peak_at_episode_end": [False, True, True],
            "price_peak_return_from_start_close_pct": [50.0, 20.0, 70.0],
        }
    )

    result = aggregate_peak_events(events, percentile_threshold=0.80)

    assert result["events"] == 3
    assert result["valid_events"] == 2
    assert result["large_acceleration_at_peak_count"] == 1
    assert result["large_acceleration_at_peak_pct"] == pytest.approx(50.0)
    assert result["large_acceleration_with_lag_count"] == 2
    assert result["peak_speed_top_threshold_count"] == 1
    assert result["lag_speed_before_peak_count"] == 1
    assert result["lag_speed_after_peak_count"] == 1


def test_background_acceleration_rate_uses_the_same_rolling_rule_on_ordinary_days() -> None:
    constant = compute_ma20_speed_features(
        _ma20_from_speeds([10.0] * 80).assign(high=100.0, close=90.0),
        speed_window=10,
    )
    stepped = compute_ma20_speed_features(
        _ma20_from_speeds([10.0] * 60 + [30.0] * 20).assign(high=100.0, close=90.0),
        speed_window=10,
    )

    constant_result = compute_background_acceleration(
        constant, speed_window=10, baseline_days=40
    )
    stepped_result = compute_background_acceleration(
        stepped, speed_window=10, baseline_days=40
    )

    assert constant_result["background_valid_days"] == 31
    assert constant_result["background_large_acceleration_days"] == 0
    assert stepped_result["background_valid_days"] == 31
    assert stepped_result["background_large_acceleration_days"] > 0
    assert 0 < stepped_result["background_large_acceleration_rate"] < 1
