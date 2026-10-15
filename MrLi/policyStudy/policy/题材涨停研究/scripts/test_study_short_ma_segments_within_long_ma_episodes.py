from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_short_ma_segments_within_long_ma_episodes import (
    _parse_args,
    aggregate_anchor_results,
    analyze_anchor_short_period,
    build_per_stock_wide,
    prepare_anchor_episodes,
    validate_pool_price_basis,
)


def test_parse_args_allows_explicit_qfq_nested_study() -> None:
    assert _parse_args([]).qfq is False
    assert _parse_args(["--qfq"]).qfq is True


def test_validate_pool_price_basis_requires_matching_qfq_contract() -> None:
    validate_pool_price_basis({"ranking_price_basis": "QFQ close (qfq=True)"}, True)
    validate_pool_price_basis(
        {"ranking_price_basis": "Unadjusted close (qfq=False)"}, False
    )
    with pytest.raises(ValueError, match="mismatch"):
        validate_pool_price_basis({"ranking_price_basis": "QFQ close (qfq=True)"}, False)


def _daily() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=7)
    return pd.DataFrame(
        {
            "ts_code": ["A"] * 7,
            "trade_date": dates.strftime("%Y%m%d"),
            "close": [10.0, 12.0, 11.0, 14.0, 16.0, 18.0, 20.0],
        }
    )


def test_prepare_anchor_episodes_materializes_both_selected_long_periods() -> None:
    union = pd.DataFrame(
        [
            {
                "observation_rank": 1,
                "ts_code": "A",
                "in_ma60_top100": True,
                "ma60_rank": 3,
                "ma60_start_date": 20260102.0,
                "ma60_end_date": 20260112.0,
                "ma60_length": 7,
                "ma60_target_days": 7,
                "ma60_left_censored": False,
                "ma60_carried_in": False,
                "ma60_right_censored": True,
                "in_ma120_top100": True,
                "ma120_rank": 8,
                "ma120_start_date": 20251201.0,
                "ma120_end_date": 20260112.0,
                "ma120_length": 30,
                "ma120_target_days": 7,
                "ma120_left_censored": False,
                "ma120_carried_in": True,
                "ma120_right_censored": True,
            }
        ]
    )
    identities = pd.DataFrame(
        [
            {
                "ts_code": "A",
                "stock_name": "甲",
                "primary_theme": "芯片",
                "broad_category": "半导体/芯片",
            }
        ]
    )

    result = prepare_anchor_episodes(union, identities)

    assert result["anchor_period"].tolist() == [60, 120]
    assert result["anchor_start_date"].tolist() == ["20260102", "20251201"]
    assert result["stock_name"].tolist() == ["甲", "甲"]


def test_analyze_anchor_counts_clipped_segments_and_first_gain_share() -> None:
    anchor = pd.Series(
        {
            "observation_rank": 1,
            "ts_code": "A",
            "stock_name": "甲",
            "primary_theme": "芯片",
            "broad_category": "半导体/芯片",
            "anchor_period": 60,
            "anchor_rank": 1,
            "anchor_start_date": "20260102",
            "anchor_end_date": "20260112",
            "anchor_length": 7,
            "anchor_target_days": 7,
            "anchor_left_censored": False,
            "anchor_carried_in": False,
            "anchor_right_censored": True,
        }
    )
    episodes = pd.DataFrame(
        [
            {
                "ts_code": "A",
                "period": 20,
                "episode_id": "carry",
                "start_date": "20251231",
                "end_date": "20260105",
                "length": 4,
                "left_censored": False,
                "right_censored": False,
            },
            {
                "ts_code": "A",
                "period": 20,
                "episode_id": "later",
                "start_date": "20260107",
                "end_date": "20260109",
                "length": 3,
                "left_censored": False,
                "right_censored": False,
            },
        ]
    )

    summary, details = analyze_anchor_short_period(anchor, _daily(), episodes, 20)

    assert summary["segment_count"] == 2
    assert summary["segment_count_ge5"] == 0
    assert summary["first_segment_carried_in"] is True
    assert summary["first_segment_start_offset_days"] == 0
    assert summary["first_segment_days"] == 2
    assert summary["anchor_total_return_pct"] == pytest.approx(100.0)
    assert summary["first_segment_return_pct"] == pytest.approx(20.0)
    assert summary["first_segment_gain_share_pct"] == pytest.approx(20.0)
    assert summary["first_end_cumulative_gain_share_pct"] == pytest.approx(20.0)
    assert [item["clipped_start_date"] for item in details] == ["20260102", "20260107"]


def test_first_segment_standalone_and_cumulative_shares_are_both_reported() -> None:
    anchor = pd.Series(
        {
            "observation_rank": 1,
            "ts_code": "A",
            "stock_name": "甲",
            "primary_theme": "芯片",
            "broad_category": "半导体/芯片",
            "anchor_period": 120,
            "anchor_rank": 1,
            "anchor_start_date": "20260102",
            "anchor_end_date": "20260112",
            "anchor_length": 7,
            "anchor_target_days": 7,
            "anchor_left_censored": False,
            "anchor_carried_in": False,
            "anchor_right_censored": True,
        }
    )
    episodes = pd.DataFrame(
        [
            {
                "ts_code": "A",
                "period": 30,
                "episode_id": "first",
                "start_date": "20260106",
                "end_date": "20260108",
                "length": 3,
                "left_censored": False,
                "right_censored": False,
            }
        ]
    )

    summary, _ = analyze_anchor_short_period(anchor, _daily(), episodes, 30)

    assert summary["first_segment_start_offset_days"] == 2
    assert summary["first_segment_return_pct"] == pytest.approx(100.0 * (16.0 / 11.0 - 1.0))
    assert summary["first_segment_gain_share_pct"] == pytest.approx(50.0)
    assert summary["first_end_cumulative_gain_share_pct"] == pytest.approx(60.0)


def test_nonpositive_anchor_gain_has_no_gain_share() -> None:
    daily = _daily()
    daily.loc[daily.index[-1], "close"] = 9.0
    anchor = pd.Series(
        {
            "observation_rank": 1,
            "ts_code": "A",
            "stock_name": "甲",
            "primary_theme": "芯片",
            "broad_category": "半导体/芯片",
            "anchor_period": 60,
            "anchor_rank": 1,
            "anchor_start_date": "20260102",
            "anchor_end_date": "20260112",
            "anchor_length": 7,
            "anchor_target_days": 7,
            "anchor_left_censored": False,
            "anchor_carried_in": False,
            "anchor_right_censored": False,
        }
    )
    episodes = pd.DataFrame(
        [
            {
                "ts_code": "A",
                "period": 20,
                "episode_id": "first",
                "start_date": "20260102",
                "end_date": "20260105",
                "length": 2,
                "left_censored": False,
                "right_censored": False,
            }
        ]
    )

    summary, _ = analyze_anchor_short_period(anchor, daily, episodes, 20)

    assert summary["anchor_total_gain_positive"] is False
    assert math.isnan(summary["first_segment_gain_share_pct"])
    assert math.isnan(summary["first_end_cumulative_gain_share_pct"])


def test_left_censored_anchor_marks_gain_share_invalid() -> None:
    anchor = pd.Series(
        {
            "observation_rank": 1,
            "ts_code": "A",
            "stock_name": "甲",
            "primary_theme": "芯片",
            "broad_category": "半导体/芯片",
            "anchor_period": 120,
            "anchor_rank": 1,
            "anchor_start_date": "20260102",
            "anchor_end_date": "20260112",
            "anchor_length": 7,
            "anchor_target_days": 7,
            "anchor_left_censored": True,
            "anchor_carried_in": True,
            "anchor_right_censored": False,
        }
    )
    episodes = pd.DataFrame(
        [
            {
                "ts_code": "A",
                "period": 20,
                "episode_id": "first",
                "start_date": "20260102",
                "end_date": "20260105",
                "length": 2,
                "left_censored": True,
                "right_censored": False,
            }
        ]
    )

    summary, _ = analyze_anchor_short_period(anchor, _daily(), episodes, 20)

    assert summary["anchor_total_gain_positive"] is True
    assert summary["gain_share_valid"] is False


def test_build_per_stock_wide_retains_membership_context() -> None:
    identities = pd.DataFrame(
        [
            {
                "observation_rank": 1,
                "ts_code": "A",
                "stock_name": "甲",
                "membership": "MA60+MA120",
                "in_ma60_top100": True,
                "in_ma120_top100": True,
                "primary_theme": "芯片",
                "broad_category": "半导体/芯片",
            }
        ]
    )
    summary = pd.DataFrame(
        [
            {
                "ts_code": "A",
                "anchor_period": 60,
                "short_period": 20,
                "anchor_rank": 1,
                "anchor_start_date": "20260102",
                "anchor_end_date": "20260112",
                "anchor_length": 7,
                "anchor_target_days": 7,
                "anchor_left_censored": False,
                "anchor_right_censored": False,
                "anchor_total_return_pct": 20.0,
                "anchor_total_gain_positive": True,
                "segment_count": 2,
                "segment_count_ge5": 1,
                "segment_count_ge10": 0,
                "short_rising_coverage_pct": 50.0,
                "first_segment_start_date": "20260102",
                "first_segment_end_date": "20260105",
                "first_segment_days": 2,
                "first_segment_carried_in": False,
                "first_segment_start_offset_days": 0,
                "first_segment_return_pct": 10.0,
                "first_segment_gain_share_pct": 40.0,
                "first_end_cumulative_return_pct": 10.0,
                "first_end_cumulative_gain_share_pct": 40.0,
                "gain_share_valid": True,
            }
        ]
    )

    result = build_per_stock_wide(identities, summary)

    assert result.loc[0, "membership"] == "MA60+MA120"
    assert bool(result.loc[0, "in_ma60_top100"]) is True
    assert result.loc[0, "ma60_ma20_segment_count"] == 2


def test_aggregate_anchor_results_summarizes_each_period_pair() -> None:
    rows = pd.DataFrame(
        [
            {
                "anchor_period": 60,
                "short_period": 20,
                "segment_count": 2,
                "segment_count_ge5": 1,
                "segment_count_ge10": 0,
                "short_rising_coverage_pct": 50.0,
                "anchor_total_return_pct": 20.0,
                "anchor_total_gain_positive": True,
                "anchor_left_censored": False,
                "anchor_right_censored": False,
                "gain_share_valid": True,
                "first_segment_days": 5,
                "first_segment_start_offset_days": 0,
                "first_segment_return_pct": 10.0,
                "first_segment_gain_share_pct": 40.0,
                "first_end_cumulative_gain_share_pct": 40.0,
            },
            {
                "anchor_period": 60,
                "short_period": 20,
                "segment_count": 4,
                "segment_count_ge5": 2,
                "segment_count_ge10": 1,
                "short_rising_coverage_pct": 70.0,
                "anchor_total_return_pct": 30.0,
                "anchor_total_gain_positive": True,
                "anchor_left_censored": False,
                "anchor_right_censored": False,
                "gain_share_valid": True,
                "first_segment_days": 10,
                "first_segment_start_offset_days": 3,
                "first_segment_return_pct": 15.0,
                "first_segment_gain_share_pct": 60.0,
                "first_end_cumulative_gain_share_pct": 75.0,
            },
            {
                "anchor_period": 60,
                "short_period": 20,
                "segment_count": 6,
                "segment_count_ge5": 3,
                "segment_count_ge10": 2,
                "short_rising_coverage_pct": 80.0,
                "anchor_total_return_pct": 40.0,
                "anchor_total_gain_positive": True,
                "anchor_left_censored": True,
                "anchor_right_censored": False,
                "gain_share_valid": True,
                "first_segment_days": 12,
                "first_segment_start_offset_days": 0,
                "first_segment_return_pct": 20.0,
                "first_segment_gain_share_pct": 1000.0,
                "first_end_cumulative_gain_share_pct": 1000.0,
            },
        ]
    )

    result = aggregate_anchor_results(rows)

    assert len(result) == 1
    assert result.iloc[0]["anchors"] == 3
    assert result.iloc[0]["gain_share_sample"] == 2
    assert result.iloc[0]["segment_count_median"] == 4
    assert result.iloc[0]["first_segment_gain_share_median_pct"] == 50
