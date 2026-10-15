from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_volume_expansion_in_long_ma_episodes import (
    _parse_args,
    build_anchor_all_segments_summary,
    compare_interval_to_prior_volume,
    compute_ma20_segment_volume_metrics,
    summarize_anchor_all_segments,
    summarize_volume_ratios,
)


def test_cli_defaults_use_verified_qfq_inputs() -> None:
    args = _parse_args([])
    assert "2026_through_20260930_qfq" in str(args.daily_file)
    assert "2026_through_20260930_qfq" in str(args.ma60_file)
    assert "2026_through_20260930_qfq" in str(args.ma20_segments_file)
    assert args.output_dir.name == "2026_through_20260930_qfq"


def test_interval_volume_uses_a_complete_immediately_preceding_equal_length_window() -> None:
    daily = pd.DataFrame(
        {
            "trade_date": pd.date_range("2026-01-01", periods=10, freq="D").strftime(
                "%Y%m%d"
            ),
            "vol": [10, 20, 30, 40, 100, 100, 200, 200, 50, 60],
        }
    )

    result = compare_interval_to_prior_volume(
        daily, start_date="20260105", end_date="20260108", prior_days=None
    )

    assert result["interval_days"] == 4
    assert result["interval_mean_volume"] == pytest.approx(150.0)
    assert result["prior_window_days_required"] == 4
    assert result["prior_window_complete"] is True
    assert result["prior_start_date"] == "20260101"
    assert result["prior_end_date"] == "20260104"
    assert result["prior_mean_volume"] == pytest.approx(25.0)
    assert result["volume_ratio"] == pytest.approx(6.0)
    assert result["volume_increase_pct"] == pytest.approx(500.0)


def test_volume_ratio_summary_uses_explicit_comparable_denominator_and_thresholds() -> None:
    detail = pd.DataFrame(
        {
            "days": [1, 5, 5, 10, 10, 10],
            "ratio": [0.8, 1.0, 1.2, 1.5, 2.0, np.nan],
        }
    )

    result = summarize_volume_ratios(
        detail, ratio_col="ratio", days_col="days", min_days=5
    )

    assert result["rows"] == 6
    assert result["duration_eligible_rows"] == 5
    assert result["comparable_count"] == 4
    assert result["ratio_median"] == pytest.approx(1.35)
    assert result["amplified_gt_1_count"] == 3
    assert result["amplified_gt_1_pct"] == pytest.approx(75.0)
    assert result["amplified_ge_1_2_count"] == 3
    assert result["amplified_ge_1_5_count"] == 2
    assert result["amplified_ge_2_count"] == 1


def test_anchor_all_segments_summary_requires_every_comparable_segment_to_expand() -> None:
    detail = pd.DataFrame(
        {
            "ts_code": ["A", "A", "B", "B", "C"],
            "anchor_period": [60, 60, 60, 60, 60],
            "anchor_start_date": ["20260101"] * 5,
            "days_in_anchor": [5, 10, 5, 10, 10],
            "started_before_anchor": [False, False, False, False, False],
            "ratio": [1.1, 1.3, 1.1, 0.9, np.nan],
        }
    )

    per_anchor = build_anchor_all_segments_summary(
        detail,
        ratio_col="ratio",
        min_days=5,
        clean_only=False,
    )
    aggregate = summarize_anchor_all_segments(per_anchor)

    a = per_anchor.loc[per_anchor["ts_code"].eq("A")].iloc[0]
    b = per_anchor.loc[per_anchor["ts_code"].eq("B")].iloc[0]
    c = per_anchor.loc[per_anchor["ts_code"].eq("C")].iloc[0]
    assert bool(a["all_segments_amplified_gt_1"]) is True
    assert bool(a["all_segments_amplified_ge_1_2"]) is False
    assert bool(b["all_segments_amplified_gt_1"]) is False
    assert c["comparable_segments"] == 0
    assert aggregate["evaluable_anchor_count"] == 2
    assert aggregate["all_segments_amplified_gt_1_count"] == 1
    assert aggregate["all_segments_amplified_gt_1_pct"] == pytest.approx(50.0)


def test_anchor_all_segments_summary_can_filter_on_global_episode_length() -> None:
    detail = pd.DataFrame(
        {
            "ts_code": ["A", "A"],
            "anchor_period": [60, 60],
            "anchor_start_date": ["20260101", "20260101"],
            "days_in_anchor": [1, 1],
            "episode_full_length": [4, 8],
            "started_before_anchor": [False, False],
            "ratio": [0.8, 1.3],
        }
    )

    result = build_anchor_all_segments_summary(
        detail,
        ratio_col="ratio",
        days_col="episode_full_length",
        min_days=5,
        clean_only=False,
    )

    assert result.iloc[0]["candidate_segments"] == 1
    assert bool(result.iloc[0]["all_segments_amplified_gt_1"]) is True


def test_ma20_segment_metrics_flag_prior_window_contamination_for_carried_in_episode() -> None:
    daily = pd.DataFrame(
        {
            "trade_date": pd.date_range("2026-01-01", periods=15, freq="D").strftime(
                "%Y%m%d"
            ),
            "vol": [
                10, 10, 10, 10, 10,
                20, 20, 100, 100, 100,
                40, 40, 40, 40, 40,
            ],
        }
    )

    result = compute_ma20_segment_volume_metrics(
        daily,
        clipped_start_date="20260108",
        clipped_end_date="20260110",
        anchor_start_date="20260108",
        anchor_end_date="20260113",
        short_episode_start_date="20260106",
        short_episode_end_date="20260110",
        fixed_prior_days=4,
    )

    assert result["episode_days"] == 5
    assert result["episode_mean_volume"] == pytest.approx(68.0)
    assert result["episode_equal_prior_complete"] is True
    assert result["episode_equal_prior_volume_ratio"] == pytest.approx(6.8)
    assert result["segment_days"] == 3
    assert result["segment_mean_volume"] == pytest.approx(100.0)
    assert result["equal_prior_mean_volume"] == pytest.approx(50 / 3)
    assert result["equal_prior_volume_ratio"] == pytest.approx(6.0)
    assert result["equal_prior_overlap_short_episode_days"] == 2
    assert result["equal_prior_onset_clean"] is False
    assert result["anchor_mean_volume"] == pytest.approx(70.0)
    assert result["segment_to_anchor_volume_ratio"] == pytest.approx(10 / 7)


def test_interval_volume_marks_incomplete_prior_window_instead_of_shortening_it() -> None:
    daily = pd.DataFrame(
        {
            "trade_date": ["20260101", "20260102", "20260103"],
            "vol": [10, 20, 30],
        }
    )

    result = compare_interval_to_prior_volume(
        daily, start_date="20260102", end_date="20260103", prior_days=None
    )

    assert result["interval_days"] == 2
    assert result["prior_window_days_required"] == 2
    assert result["prior_window_days_available"] == 1
    assert result["prior_window_complete"] is False
    assert np.isnan(result["volume_ratio"])
