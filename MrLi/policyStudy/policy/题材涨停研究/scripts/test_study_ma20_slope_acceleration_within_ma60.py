from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_ma20_slope_acceleration_within_ma60 import (
    _parse_args,
    aggregate_acceleration_results,
    compute_segment_speed_metrics,
    summarize_min_day_sensitivity,
    summarize_stock_acceleration,
    terminal_acceleration_tier,
)


def test_parse_args_freezes_primary_acceleration_thresholds() -> None:
    defaults = _parse_args([])
    assert defaults.min_segment_days == 5
    assert defaults.intra_segment_min_days == 10
    assert defaults.acceleration_threshold == pytest.approx(0.25)
    assert defaults.min_absolute_increase_bps == pytest.approx(2.0)
    changed = _parse_args(["--acceleration-threshold", "0.4"])
    assert changed.acceleration_threshold == pytest.approx(0.4)


def test_terminal_acceleration_tier_prioritizes_combined_signal() -> None:
    assert terminal_acceleration_tier(
        pd.Series(
            {
                "terminal_comparison_valid": True,
                "combined_terminal_acceleration_25": True,
                "terminal_acceleration_25": True,
                "final_is_fastest": True,
            }
        )
    ) == "A-跨段且段内加速"
    assert terminal_acceleration_tier(
        pd.Series(
            {
                "terminal_comparison_valid": True,
                "combined_terminal_acceleration_25": False,
                "terminal_acceleration_25": True,
                "final_is_fastest": True,
            }
        )
    ) == "B-跨段加速"
    assert terminal_acceleration_tier(
        pd.Series(
            {
                "terminal_comparison_valid": False,
                "combined_terminal_acceleration_25": False,
                "terminal_acceleration_25": False,
                "final_is_fastest": False,
            }
        )
    ) == "N-不可判定"


def _daily_from_speeds(speeds_bps: list[float], *, scale: float = 1.0) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=len(speeds_bps) + 1)
    ma20 = [100.0 * scale]
    for speed in speeds_bps:
        ma20.append(ma20[-1] * math.exp(speed / 10000.0))
    return pd.DataFrame(
        {
            "ts_code": ["A"] * len(dates),
            "trade_date": dates.strftime("%Y%m%d"),
            "ma20": ma20,
        }
    )


def test_segment_speed_uses_scale_invariant_daily_log_changes() -> None:
    speeds = [10.0, 20.0, 30.0, 40.0, 50.0]
    segment = pd.Series(
        {
            "ts_code": "A",
            "segment_index": 1,
            "clipped_start_date": "20260105",
            "clipped_end_date": "20260109",
            "days_in_anchor": 5,
        }
    )

    base = compute_segment_speed_metrics(_daily_from_speeds(speeds), segment)
    scaled = compute_segment_speed_metrics(_daily_from_speeds(speeds, scale=10.0), segment)

    assert base["mean_log_speed_bps_per_day"] == pytest.approx(30.0)
    assert base["median_log_speed_bps_per_day"] == pytest.approx(30.0)
    assert base["first_half_mean_speed_bps_per_day"] == pytest.approx(15.0)
    assert base["second_half_mean_speed_bps_per_day"] == pytest.approx(45.0)
    assert base["second_vs_first_half_pct"] == pytest.approx(200.0)
    assert base["daily_speed_trend_bps_per_day2"] == pytest.approx(10.0)
    assert base["mean_log_speed_bps_per_day"] == pytest.approx(
        scaled["mean_log_speed_bps_per_day"]
    )


def test_stock_summary_detects_monotone_and_terminal_acceleration() -> None:
    segments = pd.DataFrame(
        [
            {
                "segment_index": 1,
                "days_in_anchor": 6,
                "mean_log_speed_bps_per_day": 10.0,
                "first_half_mean_speed_bps_per_day": 9.0,
                "second_half_mean_speed_bps_per_day": 11.0,
                "second_vs_first_half_pct": 22.22,
                "daily_speed_trend_bps_per_day2": 0.5,
            },
            {
                "segment_index": 2,
                "days_in_anchor": 7,
                "mean_log_speed_bps_per_day": 15.0,
                "first_half_mean_speed_bps_per_day": 14.0,
                "second_half_mean_speed_bps_per_day": 16.0,
                "second_vs_first_half_pct": 14.29,
                "daily_speed_trend_bps_per_day2": 0.4,
            },
            {
                "segment_index": 3,
                "days_in_anchor": 12,
                "mean_log_speed_bps_per_day": 30.0,
                "first_half_mean_speed_bps_per_day": 10.0,
                "second_half_mean_speed_bps_per_day": 20.0,
                "second_vs_first_half_pct": 100.0,
                "daily_speed_trend_bps_per_day2": 1.0,
            },
        ]
    )

    result = summarize_stock_acceleration(
        segments,
        min_segment_days=5,
        intra_segment_min_days=10,
        acceleration_threshold=0.25,
        min_absolute_increase_bps=2.0,
    )

    assert result["eligible_segment_count"] == 3
    assert result["strictly_increasing_speed"] is True
    assert result["speed_order_spearman"] == pytest.approx(1.0)
    assert result["actual_final_segment_eligible"] is True
    assert result["final_is_fastest"] is True
    assert result["final_vs_previous_pct"] == pytest.approx(100.0)
    assert result["final_vs_prior_median_pct"] == pytest.approx(140.0)
    assert result["terminal_acceleration_25"] is True
    assert result["final_intra_segment_acceleration_25"] is True
    assert result["combined_terminal_acceleration_25"] is True


def test_short_actual_final_segment_is_not_replaced_by_an_earlier_eligible_segment() -> None:
    segments = pd.DataFrame(
        [
            {
                "segment_index": 1,
                "days_in_anchor": 6,
                "mean_log_speed_bps_per_day": 10.0,
                "first_half_mean_speed_bps_per_day": np.nan,
                "second_half_mean_speed_bps_per_day": np.nan,
                "second_vs_first_half_pct": np.nan,
                "daily_speed_trend_bps_per_day2": 0.0,
            },
            {
                "segment_index": 2,
                "days_in_anchor": 6,
                "mean_log_speed_bps_per_day": 20.0,
                "first_half_mean_speed_bps_per_day": np.nan,
                "second_half_mean_speed_bps_per_day": np.nan,
                "second_vs_first_half_pct": np.nan,
                "daily_speed_trend_bps_per_day2": 0.0,
            },
            {
                "segment_index": 3,
                "days_in_anchor": 3,
                "mean_log_speed_bps_per_day": 50.0,
                "first_half_mean_speed_bps_per_day": np.nan,
                "second_half_mean_speed_bps_per_day": np.nan,
                "second_vs_first_half_pct": np.nan,
                "daily_speed_trend_bps_per_day2": 5.0,
            },
        ]
    )

    result = summarize_stock_acceleration(segments, min_segment_days=5)

    assert result["actual_final_segment_index"] == 3
    assert result["last_eligible_segment_index"] == 2
    assert result["actual_final_segment_eligible"] is False
    assert result["terminal_acceleration_25"] is False
    assert math.isnan(result["final_vs_previous_pct"])


def test_aggregate_reports_threshold_sensitivity_with_explicit_denominators() -> None:
    stocks = pd.DataFrame(
        [
            {
                "terminal_comparison_valid": True,
                "final_vs_previous_pct": 60.0,
                "final_vs_prior_median_pct": 60.0,
                "final_vs_previous_diff_bps": 5.0,
                "final_vs_prior_median_diff_bps": 5.0,
                "final_intra_comparison_valid": True,
                "final_second_vs_first_half_pct": 60.0,
                "final_second_vs_first_half_diff_bps": 5.0,
                "final_daily_speed_trend_bps_per_day2": 1.0,
                "strictly_increasing_speed": True,
                "speed_trend_valid": True,
                "final_is_fastest": True,
            },
            {
                "terminal_comparison_valid": True,
                "final_vs_previous_pct": 15.0,
                "final_vs_prior_median_pct": 20.0,
                "final_vs_previous_diff_bps": 3.0,
                "final_vs_prior_median_diff_bps": 3.0,
                "final_intra_comparison_valid": True,
                "final_second_vs_first_half_pct": 5.0,
                "final_second_vs_first_half_diff_bps": 1.0,
                "final_daily_speed_trend_bps_per_day2": -1.0,
                "strictly_increasing_speed": False,
                "speed_trend_valid": True,
                "final_is_fastest": False,
            },
            {
                "terminal_comparison_valid": False,
                "final_vs_previous_pct": np.nan,
                "final_vs_prior_median_pct": np.nan,
                "final_vs_previous_diff_bps": np.nan,
                "final_vs_prior_median_diff_bps": np.nan,
                "final_intra_comparison_valid": False,
                "final_second_vs_first_half_pct": np.nan,
                "final_second_vs_first_half_diff_bps": np.nan,
                "final_daily_speed_trend_bps_per_day2": np.nan,
                "strictly_increasing_speed": False,
                "speed_trend_valid": False,
                "final_is_fastest": False,
            },
        ]
    )

    result = aggregate_acceleration_results(
        stocks,
        thresholds=(0.10, 0.25, 0.50),
        min_absolute_increase_bps=2.0,
    )

    assert result["stocks"] == 3
    assert result["terminal_comparison_sample"] == 2
    assert result["speed_trend_sample"] == 2
    assert result["strictly_increasing_count"] == 1
    assert result["thresholds"]["10pct"]["terminal_acceleration_count"] == 2
    assert result["thresholds"]["25pct"]["terminal_acceleration_count"] == 1
    assert result["thresholds"]["50pct"]["combined_terminal_acceleration_count"] == 1


def test_min_day_sensitivity_recomputes_sequences() -> None:
    frame = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 3,
        "segment_index": [1, 2, 3],
        "days_in_anchor": [1, 5, 10],
        "mean_log_speed_bps_per_day": [10.0, 20.0, 40.0],
        "first_half_mean_speed_bps_per_day": [np.nan, np.nan, 20.0],
        "second_half_mean_speed_bps_per_day": [np.nan, np.nan, 40.0],
        "second_vs_first_half_pct": [np.nan, np.nan, 100.0],
        "daily_speed_trend_bps_per_day2": [0.0, 0.0, 1.0],
    })
    result = summarize_min_day_sensitivity(
        frame, min_day_values=(1, 5, 10), acceleration_threshold=0.25
    ).set_index("min_segment_days")

    assert result.loc[1, "strictly_increasing_count"] == 1
    assert result.loc[5, "speed_trend_sample"] == 0
    assert result.loc[5, "terminal_acceleration_count"] == 1
    assert result.loc[10, "terminal_comparison_sample"] == 0
