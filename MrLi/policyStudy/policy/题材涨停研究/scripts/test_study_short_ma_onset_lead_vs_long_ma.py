from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_short_ma_onset_lead_vs_long_ma import (
    _parse_args,
    aggregate_onset_leads,
    aggregate_paired_onsets,
    compute_duration_sensitivity,
    select_first_eligible_segments,
    signed_trading_day_lead,
)


def test_cli_defaults_use_the_verified_qfq_nested_episode_outputs() -> None:
    args = _parse_args([])
    assert "2026_through_20260930_qfq" in str(args.summary_file)
    assert "2026_through_20260930_qfq" in str(args.segment_file)
    assert args.output_dir.name == "2026_through_20260930_qfq"


def test_select_first_eligible_segment_recomputes_the_first_episode_for_each_threshold() -> None:
    segments = pd.DataFrame(
        {
            "ts_code": ["A", "A", "B", "B"],
            "anchor_period": [60, 60, 120, 120],
            "short_period": [20, 20, 30, 30],
            "anchor_start_date": ["20260105"] * 2 + ["20260105"] * 2,
            "segment_index": [1, 2, 1, 2],
            "episode_length": [2, 8, 3, 4],
            "episode_start_date": ["20260103", "20260108", "20260104", "20260110"],
        }
    )

    all_first = select_first_eligible_segments(segments, min_episode_days=1)
    five_day_first = select_first_eligible_segments(segments, min_episode_days=5)

    assert all_first.loc[all_first["ts_code"].eq("A"), "segment_index"].item() == 1
    assert five_day_first["ts_code"].tolist() == ["A"]
    assert five_day_first["segment_index"].item() == 2


def test_select_first_eligible_segment_accepts_production_duration_column() -> None:
    segments = pd.DataFrame(
        {
            "ts_code": ["A"],
            "anchor_period": [60],
            "short_period": [20],
            "anchor_start_date": ["20260105"],
            "segment_index": [1],
            "episode_full_length": [8],
        }
    )

    selected = select_first_eligible_segments(segments, min_episode_days=5)

    assert len(selected) == 1


def test_duration_sensitivity_reselects_first_episode_and_reports_missing_anchors() -> None:
    summary = pd.DataFrame(
        {
            "ts_code": ["A", "B"],
            "anchor_period": [60, 60],
            "short_period": [20, 20],
            "anchor_start_date": ["20260105", "20260105"],
            "anchor_left_censored": [False, False],
        }
    )
    segments = pd.DataFrame(
        {
            "ts_code": ["A", "A", "B"],
            "anchor_period": [60, 60, 60],
            "short_period": [20, 20, 20],
            "anchor_start_date": ["20260105"] * 3,
            "segment_index": [1, 2, 1],
            "episode_length": [2, 8, 3],
            "episode_start_date": ["20260103", "20260108", "20260104"],
            "episode_left_censored": [False, False, False],
        }
    )
    dates = pd.Series(
        pd.date_range("2026-01-01", periods=12, freq="D").strftime("%Y%m%d")
    )

    result = compute_duration_sensitivity(
        summary, segments, {"A": dates, "B": dates}, thresholds=(1, 5)
    )

    one_day = result.loc[result["min_episode_days"].eq(1)].iloc[0]
    five_day = result.loc[result["min_episode_days"].eq(5)].iloc[0]
    assert one_day["eligible_anchors"] == 2
    assert one_day["earlier_count"] == 2
    assert five_day["eligible_anchors"] == 1
    assert five_day["primary_sample"] == 1
    assert five_day["later_count"] == 1


def test_signed_trading_day_lead_is_positive_when_short_ma_starts_first() -> None:
    dates = pd.Series(
        pd.date_range("2026-01-01", periods=10, freq="D").strftime("%Y%m%d")
    )

    assert signed_trading_day_lead(dates, "20260103", "20260106") == 3
    assert signed_trading_day_lead(dates, "20260106", "20260106") == 0
    assert signed_trading_day_lead(dates, "20260108", "20260106") == -2

    with pytest.raises(ValueError, match="not found"):
        signed_trading_day_lead(dates, "20251231", "20260106")


def test_aggregate_uses_non_left_censored_sample_and_explicit_categories() -> None:
    detail = pd.DataFrame(
        {
            "anchor_left_censored": [False, False, False, True, False],
            "short_episode_left_censored": [False, False, False, False, True],
            "lead_trading_days": [8, 0, -3, 20, 15],
        }
    )

    result = aggregate_onset_leads(detail)

    assert result["rows"] == 5
    assert result["primary_sample"] == 3
    assert result["earlier_count"] == 1
    assert result["same_day_count"] == 1
    assert result["later_count"] == 1
    assert result["earlier_pct"] == pytest.approx(100 / 3)
    assert result["at_or_before_count"] == 2
    assert result["earlier_lead_median_days"] == pytest.approx(8.0)


def test_paired_summary_distinguishes_both_early_and_which_short_ma_started_first() -> None:
    paired = pd.DataFrame(
        {
            "anchor_left_censored": [False, False, False, True],
            "ma20_short_left_censored": [False, False, False, False],
            "ma30_short_left_censored": [False, False, False, False],
            "ma20_lead_trading_days": [10, 0, -2, 20],
            "ma30_lead_trading_days": [5, 3, -4, 10],
        }
    )

    result = aggregate_paired_onsets(paired)

    assert result["primary_sample"] == 3
    assert result["both_earlier_count"] == 1
    assert result["either_earlier_count"] == 2
    assert result["both_at_or_before_count"] == 2
    assert result["ma20_started_earlier_count"] == 2
    assert result["ma30_started_earlier_count"] == 1
    assert result["same_start_count"] == 0
