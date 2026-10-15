from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_ma20_first_pullback_cohesion_entry import (
    _parse_args,
    add_deduction_price_features,
    add_entry_candidate_features,
    build_coverage_summary,
    build_unique_stock_tier_events,
    compute_forward_outcomes,
    compute_rising_streak,
    deduction_price_features_at_position,
    rising_episode_status_at_position,
    select_first_signal_after_segment,
    select_tier_signal_positions,
    summarize_future_joint_ma_rise,
)


def test_deduction_prices_for_next_10_days_use_only_prices_known_at_signal() -> None:
    close = np.arange(1.0, 61.0)
    close[21:31] = np.arange(30.0, 20.0, -1.0)  # MA20 deductions t-19..t-10
    close[11:21] = np.arange(20.0, 10.0, -1.0)  # MA30 deductions t-29..t-20
    frame = pd.DataFrame({"close": close})

    result = deduction_price_features_at_position(
        frame,
        signal_position=40,
        entry_price=50.0,
        horizon=10,
    )

    assert result["ma20_deduction_prices"] == pytest.approx(list(np.arange(30.0, 20.0, -1.0)))
    assert result["ma30_deduction_prices"] == pytest.approx(list(np.arange(20.0, 10.0, -1.0)))
    assert result["ma20_deduction_all_below_entry"] is True
    assert result["ma30_deduction_all_below_entry"] is True
    assert result["both_deduction_all_below_entry"] is True
    assert result["ma20_deduction_end_below_start"] is True
    assert result["ma30_deduction_end_below_start"] is True
    assert result["both_deduction_declining"] is True


def test_vectorized_deduction_features_match_position_formula() -> None:
    frame = pd.DataFrame({"close": np.linspace(10.0, 80.0, 80)})
    featured = add_deduction_price_features(frame, horizon=10)
    expected = deduction_price_features_at_position(
        frame,
        signal_position=50,
        entry_price=float(frame.loc[50, "close"]),
        horizon=10,
    )

    assert featured.loc[50, "ma20_deduction_max"] == pytest.approx(
        max(expected["ma20_deduction_prices"])
    )
    assert featured.loc[50, "ma30_deduction_max"] == pytest.approx(
        max(expected["ma30_deduction_prices"])
    )
    assert bool(featured.loc[50, "both_deduction_all_below_entry"])


def test_entry_candidate_uses_previous_day_cohesion_and_prior_20_day_volume() -> None:
    rows = 22
    frame = pd.DataFrame(
        {
            "open": [10.0] * rows,
            "high": [10.2] * rows,
            "low": [9.8] * rows,
            "close": [10.0] * 21 + [10.4],
            "vol": [100.0] * 21 + [200.0],
            "ma20": [10.0] * rows,
            "ma30": [10.1] * rows,
            "ma60": [9.9] * rows,
        }
    )

    result = add_entry_candidate_features(
        frame,
        cohesion_threshold_pct=3.0,
        bullish_body_threshold_pct=3.0,
        volume_ratio_threshold=1.5,
        volume_lookback=20,
    )

    row = result.iloc[-1]
    assert row["previous_ma_cohesion_spread_pct"] == pytest.approx(2.0, abs=0.02)
    assert row["bullish_body_pct"] == pytest.approx(4.0)
    assert row["volume_ratio_to_prior_mean"] == pytest.approx(2.0)
    assert bool(row["previous_day_ma_cohesive"])
    assert bool(row["large_volume_bullish_bar"])
    assert bool(row["cohesion_large_volume_bullish_bar"])


def test_rising_streak_resets_on_flat_or_falling_values() -> None:
    series = pd.Series([np.nan, 1.0, 2.0, 3.0, 3.0, 4.0, 3.5, 3.6])

    result = compute_rising_streak(series)

    assert result.tolist() == [0, 0, 1, 2, 0, 1, 0, 1]


def test_forward_outcomes_enter_at_next_open_without_signal_day_lookahead() -> None:
    frame = pd.DataFrame(
        {
            "open": [8.0, 9.0, 10.0, 11.0, 12.0, 13.0],
            "high": [8.5, 9.5, 10.5, 12.0, 14.0, 15.0],
            "low": [7.5, 8.5, 9.5, 10.0, 9.0, 12.0],
            "close": [8.2, 9.2, 10.2, 11.5, 13.0, 14.0],
            "trade_date": [f"2026010{i + 1}" for i in range(6)],
        }
    )

    result = compute_forward_outcomes(frame, signal_position=2, horizons=(3,))

    assert result["next_open_date"] == "20260104"
    assert result["next_open_price"] == pytest.approx(11.0)
    assert result["forward_3d_complete"] is True
    assert result["forward_3d_return_pct"] == pytest.approx(100.0 * (14.0 / 11.0 - 1.0))
    assert result["forward_3d_max_runup_pct"] == pytest.approx(100.0 * (15.0 / 11.0 - 1.0))
    assert result["forward_3d_max_drawdown_pct"] == pytest.approx(100.0 * (9.0 / 11.0 - 1.0))


def test_future_joint_ma_rise_reports_max_consecutive_run() -> None:
    frame = pd.DataFrame(
        {
            "ma20_rising": [False, False, True, True, True, True, True, False, True, True, True, True],
            "ma30_rising": [False, False, True, True, True, True, True, False, True, True, False, True],
        }
    )

    result = summarize_future_joint_ma_rise(frame, signal_position=1, horizon=10)

    assert result["future_10d_complete"] is True
    assert result["future_10d_joint_ma_rise_days"] == 8
    assert result["future_10d_joint_ma_rise_max_run"] == 5
    assert result["future_10d_joint_ma_rise_run_ge_5"] is True
    assert result["future_10d_joint_ma_rise_all_days"] is False


def test_first_signal_is_strictly_after_first_segment_and_inside_anchor() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["20260101", "20260102", "20260105", "20260106", "20260107"],
            "main_entry_signal": [False, True, False, True, True],
        }
    )

    selected = select_first_signal_after_segment(
        frame,
        first_segment_end_date="20260102",
        anchor_end_date="20260107",
        signal_column="main_entry_signal",
    )

    assert selected == 3


def test_rising_episode_status_reports_observable_and_eventual_lengths() -> None:
    rising = pd.Series([False, True, True, True, False, True, True])

    completed = rising_episode_status_at_position(rising, 2)
    ongoing = rising_episode_status_at_position(rising, 6)

    assert completed == {
        "streak_at_position": 2,
        "observed_episode_total": 3,
        "remaining_observed_days": 1,
        "episode_right_censored": False,
    }
    assert ongoing == {
        "streak_at_position": 2,
        "observed_episode_total": 2,
        "remaining_observed_days": 0,
        "episode_right_censored": True,
    }


def test_deduction_filter_is_applied_to_first_cohesion_bar_not_a_later_bar() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["20260101", "20260102", "20260105", "20260106"],
            "large_volume_bullish_bar": [False, False, True, True],
            "cohesion_large_volume_bullish_bar": [False, False, True, True],
            "both_deduction_all_below_entry": [False, False, False, True],
            "both_deduction_declining": [False, False, False, True],
        }
    )

    result = select_tier_signal_positions(
        frame,
        first_segment_end_date="20260102",
        anchor_end_date="20260106",
    )

    assert result["big_bar"] == 2
    assert result["cohesion"] == 2
    assert result["main"] is None
    assert result["strict"] is None


def test_unique_stock_main_cannot_skip_earlier_failed_cohesion_anchor() -> None:
    events = pd.DataFrame(
        [
            {"ts_code": "A", "tier": "cohesion", "signal_date": "20260105", "anchor_period": 60, "both_deduction_all_below_entry": False, "both_deduction_declining": False},
            {"ts_code": "A", "tier": "main", "signal_date": "20260106", "anchor_period": 120, "both_deduction_all_below_entry": True, "both_deduction_declining": False},
            {"ts_code": "B", "tier": "cohesion", "signal_date": "20260107", "anchor_period": 60, "both_deduction_all_below_entry": True, "both_deduction_declining": True},
            {"ts_code": "B", "tier": "main", "signal_date": "20260107", "anchor_period": 60, "both_deduction_all_below_entry": True, "both_deduction_declining": True},
            {"ts_code": "B", "tier": "strict", "signal_date": "20260107", "anchor_period": 60, "both_deduction_all_below_entry": True, "both_deduction_declining": True},
        ]
    )

    result = build_unique_stock_tier_events(events)

    main_codes = set(result.loc[result["tier"].eq("main"), "ts_code"])
    strict_codes = set(result.loc[result["tier"].eq("strict"), "ts_code"])
    assert main_codes == {"B"}
    assert strict_codes == {"B"}


def test_coverage_summary_counts_eligible_anchor_denominator() -> None:
    contexts = pd.DataFrame(
        {
            "ts_code": ["A", "B", "C"],
            "anchor_period": [60, 60, 60],
            "search_eligible": [True, True, False],
        }
    )
    events = pd.DataFrame(
        {
            "ts_code": ["A", "A", "B"],
            "anchor_period": [60, 60, 60],
            "tier": ["big_bar", "main", "big_bar"],
        }
    )

    result = build_coverage_summary(
        contexts,
        events,
        tiers=("big_bar", "main"),
    ).set_index("tier")

    assert result.loc["big_bar", "eligible_count"] == 2
    assert result.loc["big_bar", "signal_count"] == 2
    assert result.loc["big_bar", "coverage_pct"] == pytest.approx(100.0)
    assert result.loc["main", "signal_count"] == 1
    assert result.loc["main", "coverage_pct"] == pytest.approx(50.0)


def test_cli_defaults_are_bound_to_formal_qfq_inputs() -> None:
    args = _parse_args([])

    assert args.daily_file.name == "daily_input_qfq.csv.gz"
    assert "2026_through_20260930_qfq" in str(args.output_dir)
    assert args.cohesion_threshold_pct == 3.0
    assert args.bullish_body_threshold_pct == 3.0
    assert args.volume_ratio_threshold == 1.5
    assert args.deduction_horizon == 10
