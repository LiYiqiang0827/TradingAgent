from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_first_ma30_pullback_cohesion_timing import (
    _parse_args,
    add_pullback_features,
    build_event_record,
    compute_rising_streak,
    rising_episode_status_at_position,
    run_sensitivity,
    select_first_pullback,
    summarize_streaks,
)


def test_pullback_candidate_requires_prior_extension_touch_and_short_ma_cohesion() -> None:
    rows = 13
    frame = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=rows).strftime("%Y%m%d"),
            "close": [100.0] * 11 + [106.0, 99.0],
            "low": [99.0] * 12 + [98.5],
            "ma5": [100.0] * rows,
            "ma10": [100.5] * rows,
            "ma30": [100.0] * rows,
        }
    )

    result = add_pullback_features(
        frame,
        cohesion_threshold_pct=3.0,
        touch_upper_pct=1.0,
        close_floor_pct=-2.0,
        prior_extension_pct=5.0,
        extension_lookback=10,
    )

    row = result.iloc[-1]
    assert row["ma5_ma10_ma30_spread_pct"] == pytest.approx(0.5, abs=0.01)
    assert row["prior_max_close_vs_ma30_pct"] == pytest.approx(6.0)
    assert row["low_vs_ma30_pct"] == pytest.approx(-1.5)
    assert row["close_vs_ma30_pct"] == pytest.approx(-1.0)
    assert bool(row["ma30_pullback_cohesion_candidate"])


def test_strict_first_pullback_cannot_skip_noncohesive_touch() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=13).strftime("%Y%m%d"),
            "close": [100.0] * 9 + [106.0, 100.0, 100.0, 100.0],
            "low": [99.0] * 13,
            "ma5": [100.0] * 10 + [110.0, 100.0, 100.0],
            "ma10": [100.0] * 13,
            "ma30": [100.0] * 13,
        }
    )
    result = add_pullback_features(
        frame,
        cohesion_threshold_pct=3.0,
        touch_upper_pct=1.0,
        close_floor_pct=-2.0,
        prior_extension_pct=5.0,
        extension_lookback=10,
    )

    first_touch = select_first_pullback(
        result,
        anchor_start_date=result.iloc[10]["trade_date"],
        anchor_end_date=result.iloc[-1]["trade_date"],
        signal_column="ma30_pullback_candidate",
    )
    first_qualifying = select_first_pullback(
        result,
        anchor_start_date=result.iloc[10]["trade_date"],
        anchor_end_date=result.iloc[-1]["trade_date"],
    )

    assert first_touch == 10
    assert not bool(result.iloc[first_touch]["ma30_pullback_cohesion_candidate"])
    assert first_qualifying == 11


def test_sensitivity_applies_cohesion_to_first_touch_without_skipping() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=13).strftime("%Y%m%d"),
            "close": [100.0] * 9 + [106.0, 100.0, 100.0, 100.0],
            "low": [99.0] * 13,
            "ma5": [100.0] * 10 + [110.0, 100.0, 100.0],
            "ma10": [100.0] * 13,
            "ma30": [100.0] * 13,
            "ma60_rising_streak": list(range(13)),
            "ma120_rising_streak": list(range(13)),
        }
    )
    features = add_pullback_features(
        frame,
        cohesion_threshold_pct=3.0,
        touch_upper_pct=1.0,
        close_floor_pct=-2.0,
        prior_extension_pct=5.0,
        extension_lookback=10,
    )
    anchors = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "anchor_period": [60],
            "start_date": [features.iloc[10]["trade_date"]],
            "end_date": [features.iloc[-1]["trade_date"]],
            "search_eligible": [True],
        }
    )

    result = run_sensitivity(
        anchors=anchors,
        features_by_code={"000001.SZ": features},
        cohesion_thresholds=(3.0,),
        touch_thresholds=(1.0,),
        extension_thresholds=(5.0,),
        close_floor_pct=-2.0,
    )

    assert result.query("anchor_period == 60").iloc[0]["event_count"] == 0


def test_current_day_cannot_create_its_own_prior_extension() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=12).strftime("%Y%m%d"),
            "close": [100.0] * 11 + [106.0],
            "low": [100.0] * 12,
            "ma5": [100.0] * 12,
            "ma10": [100.0] * 12,
            "ma30": [100.0] * 12,
        }
    )

    result = add_pullback_features(
        frame,
        cohesion_threshold_pct=3.0,
        touch_upper_pct=1.0,
        close_floor_pct=-2.0,
        prior_extension_pct=5.0,
        extension_lookback=10,
    )

    assert not bool(result.iloc[-1]["ma30_pullback_cohesion_candidate"])


def test_first_pullback_is_selected_inside_anchor_inclusively() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["20260101", "20260102", "20260105", "20260106", "20260107"],
            "ma30_pullback_cohesion_candidate": [True, False, True, True, True],
        }
    )

    assert select_first_pullback(
        frame,
        anchor_start_date="20260102",
        anchor_end_date="20260106",
    ) == 2
    assert select_first_pullback(
        frame,
        anchor_start_date="20260106",
        anchor_end_date="20260106",
    ) == 3


def test_rising_streak_resets_on_flat_falling_or_missing() -> None:
    values = pd.Series([np.nan, 1.0, 2.0, 3.0, 3.0, 2.0, 2.1, np.nan, 3.0])

    assert compute_rising_streak(values).tolist() == [0, 0, 1, 2, 0, 0, 1, 0, 0]


def test_episode_status_reports_observed_total_and_remaining() -> None:
    rising = pd.Series([False, True, True, True, False, True, True])

    assert rising_episode_status_at_position(rising, 2) == {
        "streak_at_position": 2,
        "observed_episode_total": 3,
        "remaining_observed_days": 1,
        "episode_right_censored": False,
    }
    assert rising_episode_status_at_position(rising, 6)["episode_right_censored"]


def test_event_record_uses_full_history_streak_at_pullback() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["20260101", "20260102", "20260105", "20260106", "20260107", "20260108"],
            "close": [10.0] * 6,
            "low": [9.9] * 6,
            "ma5": [10.0] * 6,
            "ma10": [10.0] * 6,
            "ma30": [10.0] * 6,
            "ma60": [9.0, 9.1, 9.2, 9.3, 9.4, 9.5],
            "ma120": [8.0, 8.0, 8.1, 8.2, 8.3, 8.4],
            "ma60_rising": [False, True, True, True, True, True],
            "ma120_rising": [False, False, True, True, True, True],
            "ma5_ma10_ma30_spread_pct": [0.0] * 6,
            "prior_max_close_vs_ma30_pct": [6.0] * 6,
            "low_vs_ma30_pct": [-1.0] * 6,
            "close_vs_ma30_pct": [0.0] * 6,
        }
    )
    anchor = pd.Series(
        {
            "ts_code": "000001.SZ",
            "stock_name": "样本",
            "anchor_period": 60,
            "rank": 1,
            "start_date": "20260102",
            "end_date": "20260108",
            "length": 5,
            "right_censored": False,
        }
    )

    result = build_event_record(frame, signal_position=4, anchor=anchor)

    assert result["anchor_observed_days_at_pullback"] == 4
    assert result["ma60_streak_at_position"] == 4
    assert result["ma120_streak_at_position"] == 3
    assert result["ma60_observed_episode_total"] == 5
    assert result["ma120_observed_episode_total"] == 4


def test_streak_summary_reports_zero_inclusive_and_active_only_distributions() -> None:
    events = pd.DataFrame(
        {
            "scope": ["MA60"] * 4,
            "ma60_streak_at_position": [0, 1, 3, 5],
            "ma120_streak_at_position": [0, 2, 4, 6],
            "ma60_observed_episode_total": [0, 10, 10, 10],
            "ma120_observed_episode_total": [0, 12, 12, 12],
            "ma60_remaining_observed_days": [0, 9, 7, 5],
            "ma120_remaining_observed_days": [0, 10, 8, 6],
            "ma60_episode_right_censored": [False] * 4,
            "ma120_episode_right_censored": [False] * 4,
        }
    )

    row = summarize_streaks(events).query("ma_period == 60").iloc[0]

    assert row["streak_all_sample_median"] == pytest.approx(2.0)
    assert row["streak_median"] == pytest.approx(3.0)
    assert row["currently_rising_count"] == 3


def test_cli_defaults_use_formal_qfq_inputs() -> None:
    args = _parse_args([])

    assert args.daily_file.name == "daily_input_qfq.csv.gz"
    assert args.cohesion_threshold_pct == 3.0
    assert args.touch_upper_pct == 1.0
    assert args.close_floor_pct == -2.0
    assert args.prior_extension_pct == 5.0
    assert args.extension_lookback == 10
    assert "2026_through_20260930_qfq" in str(args.output_dir)
