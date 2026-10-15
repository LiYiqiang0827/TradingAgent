from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_annual_ma_rising_streak_distribution import (
    DAILY_QFQ,
    _parse_args,
    add_ma_streak_columns,
    analyze_frame,
    distribution_tables,
    extract_episodes,
    price_basis_metadata,
    summarize_period,
)


def test_parse_args_allows_explicit_qfq_run() -> None:
    assert _parse_args([]).qfq is False
    assert _parse_args(["--qfq"]).qfq is True


def test_price_basis_metadata_distinguishes_qfq_from_unadjusted() -> None:
    assert price_basis_metadata(False) == {
        "data_access": "coreClient.data_provider.get_day(source='database_only', qfq=False)",
        "price_basis": "Unadjusted close (qfq=False)",
    }
    assert price_basis_metadata(True) == {
        "data_access": "coreClient.data_provider.get_day(source='database_only', qfq=True)",
        "price_basis": "QFQ close (qfq=True)",
    }


def test_daily_price_basis_is_unadjusted() -> None:
    assert DAILY_QFQ is False


def test_add_ma_streak_columns_resets_on_flat_and_falling_days() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 8,
            "trade_date": pd.date_range("2026-01-05", periods=8, freq="B").strftime("%Y%m%d"),
            "close": [1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 4.0, 5.0],
        }
    )

    result = add_ma_streak_columns(frame, periods=(2,))

    assert result["ma2_streak"].tolist() == [0, 0, 1, 2, 0, 0, 1, 2]


def test_extract_episodes_counts_runs_once_and_marks_window_boundaries() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 8,
            "trade_date": pd.date_range("2026-01-05", periods=8, freq="B").strftime("%Y%m%d"),
            "close": [1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 4.0, 5.0],
        }
    )
    featured = add_ma_streak_columns(frame, periods=(2,))

    episodes = extract_episodes(
        featured,
        period=2,
        target_start="20260108",
        target_end="20260114",
    )

    assert episodes["length"].tolist() == [2, 2]
    assert episodes["target_days"].tolist() == [1, 2]
    assert episodes["left_censored"].tolist() == [True, False]
    assert episodes["carried_in"].tolist() == [True, False]
    assert episodes["right_censored"].tolist() == [False, True]


def test_summarize_period_uses_stock_days_and_window_episode_lengths() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 8,
            "trade_date": pd.date_range("2026-01-05", periods=8, freq="B").strftime("%Y%m%d"),
            "close": [1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 4.0, 5.0],
        }
    )
    featured = add_ma_streak_columns(frame, periods=(2,))
    episodes = extract_episodes(
        featured,
        period=2,
        target_start="20260108",
        target_end="20260114",
    )

    summary = summarize_period(
        featured,
        episodes,
        period=2,
        target_start="20260108",
        target_end="20260114",
    )

    assert summary["valid_stock_days"] == 5
    assert summary["rising_stock_days"] == 3
    assert summary["rising_share_pct"] == 60.0
    assert summary["episodes"] == 2
    assert summary["completed_episodes"] == 1
    assert summary["right_censored_episodes"] == 1
    assert summary["left_censored_episodes"] == 1
    assert summary["carried_in_episodes"] == 1
    assert summary["window_days_median"] == 1.5
    assert summary["window_days_max"] == 2


def test_distribution_tables_preserve_counts_and_fill_empty_buckets() -> None:
    exact, buckets = distribution_tables(
        pd.Series([1, 1, 3, 6, 21, 181]),
        period=20,
        population="episodes_all",
        metric="window_days",
    )

    assert exact[["duration", "count"]].values.tolist() == [
        [1, 2], [3, 1], [6, 1], [21, 1], [181, 1]
    ]
    assert exact["count"].sum() == 6
    counts = buckets.set_index("bucket")["count"].to_dict()
    assert counts["1-2"] == 2
    assert counts["3-5"] == 1
    assert counts["6-10"] == 1
    assert counts["11-20"] == 0
    assert counts["21-40"] == 1
    assert counts["181+"] == 1
    assert round(float(buckets["pct"].sum()), 8) == 100.0


def test_analyze_frame_builds_episode_and_stock_day_outputs() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 8,
            "trade_date": pd.date_range("2026-01-05", periods=8, freq="B").strftime("%Y%m%d"),
            "close": [1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 4.0, 5.0],
        }
    )

    result = analyze_frame(
        frame,
        periods=(2,),
        target_start="20260108",
        target_end="20260114",
    )

    assert result["summary"].iloc[0]["episodes"] == 2
    assert len(result["episodes"]) == 2
    buckets = result["bucket_distribution"]
    episode_total = buckets[
        buckets["population"].eq("episodes_all")
        & buckets["metric"].eq("window_days")
    ]["count"].sum()
    stock_day_total = buckets[
        buckets["population"].eq("stock_days")
        & buckets["metric"].eq("current_streak")
    ]["count"].sum()
    assert episode_total == 2
    assert stock_day_total == 3
