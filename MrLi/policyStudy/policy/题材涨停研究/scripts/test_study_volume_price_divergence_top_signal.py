from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_volume_price_divergence_top_signal import (
    _parse_args,
    add_forward_top_outcomes,
    aggregate_within_anchor_effectiveness,
    apply_class_cooldown,
    build_within_anchor_effectiveness,
    detect_divergence_candidates,
    summarize_effectiveness,
)


def test_cli_defaults_use_verified_qfq_long_ma_inputs() -> None:
    args = _parse_args([])
    assert "2026_through_20260930_qfq" in str(args.daily_file)
    assert "2026_through_20260930_qfq" in str(args.ma60_file)
    assert args.output_dir.name == "2026_through_20260930_qfq"
    assert args.lookback_days == 60
    assert args.volume_window == 5
    assert args.volume_ratio_threshold == pytest.approx(0.8)
    assert args.forward_days == 20


def test_divergence_signal_is_a_new_high_with_lower_trailing_volume_than_prior_peak() -> None:
    daily = pd.DataFrame(
        {
            "trade_date": pd.date_range("2026-01-01", periods=10, freq="D").strftime(
                "%Y%m%d"
            ),
            "close": [10, 11, 12, 11.5, 13, 12.5, 13.5, 13.0, 14.0, 13.8],
            "vol": [100, 100, 100, 80, 40, 40, 30, 30, 120, 120],
        }
    )

    events = detect_divergence_candidates(
        daily,
        anchor_start_date="20260101",
        anchor_end_date="20260110",
        price_col="close",
        lookback_days=3,
        volume_window=2,
        volume_ratio_threshold=0.8,
    )

    first = events.loc[events["signal_date"].eq("20260105")].iloc[0]
    assert first["prior_peak_date"] == "20260103"
    assert first["current_volume_average"] == pytest.approx(60.0)
    assert first["prior_peak_volume_average"] == pytest.approx(100.0)
    assert first["volume_ratio_to_prior_peak"] == pytest.approx(0.6)
    assert bool(first["is_volume_price_divergence"]) is True

    high_volume_breakout = events.loc[events["signal_date"].eq("20260109")].iloc[0]
    assert high_volume_breakout["volume_ratio_to_prior_peak"] > 0.8
    assert bool(high_volume_breakout["is_volume_price_divergence"]) is False


def test_class_cooldown_deduplicates_each_signal_class_without_mixing_controls() -> None:
    events = pd.DataFrame(
        {
            "ts_code": ["A"] * 6,
            "anchor_period": [60] * 6,
            "anchor_start_date": ["20260101"] * 6,
            "is_volume_price_divergence": [True, False, True, False, True, False],
            "anchor_offset_days": [1, 2, 3, 5, 12, 15],
        }
    )

    result = apply_class_cooldown(events, cooldown_days=10)

    selected = result.loc[result["cooldown_selected"]]
    assert selected.loc[
        selected["is_volume_price_divergence"], "anchor_offset_days"
    ].tolist() == [1, 12]
    assert selected.loc[
        ~selected["is_volume_price_divergence"], "anchor_offset_days"
    ].tolist() == [2, 15]


def test_effectiveness_summary_keeps_signal_and_new_high_control_denominators_separate() -> None:
    events = pd.DataFrame(
        {
            "is_volume_price_divergence": [True, True, False, False],
            "forward_window_complete": [True, True, True, True],
            "forward_return_pct": [-12.0, 3.0, 5.0, 8.0],
            "forward_max_runup_pct": [2.0, 8.0, 10.0, 12.0],
            "forward_max_drawdown_pct": [-15.0, -5.0, -4.0, -3.0],
            "forward_drawdown_ge_10pct": [True, False, False, False],
            "top_like_forward_pattern": [True, False, False, False],
            "anchor_peak_observable": [True, True, True, True],
            "within_10d_before_anchor_peak": [True, False, False, False],
        }
    )

    result = summarize_effectiveness(events)

    divergence = result.loc[result["event_class"].eq("divergence")].iloc[0]
    control = result.loc[result["event_class"].eq("non_divergent_new_high")].iloc[0]
    assert divergence["event_count"] == 2
    assert divergence["drawdown_ge_10pct_rate"] == pytest.approx(50.0)
    assert divergence["near_anchor_peak_10d_rate"] == pytest.approx(50.0)
    assert control["drawdown_ge_10pct_rate"] == pytest.approx(0.0)
    assert control["near_anchor_peak_10d_rate"] == pytest.approx(0.0)


def test_within_anchor_comparison_prevents_stock_mix_from_driving_the_signal_result() -> None:
    events = pd.DataFrame(
        {
            "ts_code": ["A"] * 4 + ["B"] * 4,
            "anchor_period": [60] * 8,
            "anchor_start_date": ["20260101"] * 4 + ["20260201"] * 4,
            "is_volume_price_divergence": [True, True, False, False] * 2,
            "forward_window_complete": [True] * 8,
            "forward_drawdown_ge_10pct": [True, False, False, False, False, False, True, True],
            "forward_return_pct": [-10.0, 2.0, 4.0, 6.0, 5.0, 7.0, -8.0, -6.0],
        }
    )

    per_anchor = build_within_anchor_effectiveness(events)
    aggregate = aggregate_within_anchor_effectiveness(per_anchor)

    assert len(per_anchor) == 2
    assert per_anchor.loc[per_anchor["ts_code"].eq("A"), "drawdown_rate_difference_pct"].item() == pytest.approx(50.0)
    assert per_anchor.loc[per_anchor["ts_code"].eq("B"), "drawdown_rate_difference_pct"].item() == pytest.approx(-100.0)
    assert aggregate["paired_anchor_count"] == 2
    assert aggregate["divergence_rate_higher_count"] == 1
    assert aggregate["divergence_rate_lower_count"] == 1
    assert aggregate["median_drawdown_rate_difference_pct"] == pytest.approx(-25.0)


def test_forward_outcomes_measure_drawdown_and_distance_to_completed_anchor_peak() -> None:
    daily = pd.DataFrame(
        {
            "trade_date": pd.date_range("2026-01-01", periods=12, freq="D").strftime(
                "%Y%m%d"
            ),
            "close": [10, 11, 12, 13, 14, 15, 14, 13, 12, 11, 10, 9],
        }
    )
    events = pd.DataFrame(
        {
            "signal_date": ["20260105"],
            "signal_close": [14.0],
            "anchor_start_date": ["20260101"],
            "anchor_end_date": ["20260110"],
            "anchor_right_censored": [False],
        }
    )

    result = add_forward_top_outcomes(events, daily, forward_days=5)
    row = result.iloc[0]

    assert row["forward_window_complete"]
    assert row["forward_return_pct"] == pytest.approx(100 * (11 / 14 - 1))
    assert row["forward_max_runup_pct"] == pytest.approx(100 * (15 / 14 - 1))
    assert row["forward_max_drawdown_pct"] == pytest.approx(100 * (11 / 14 - 1))
    assert row["anchor_peak_date"] == "20260106"
    assert row["trading_days_to_anchor_peak"] == 1
    assert row["within_5d_before_anchor_peak"]
