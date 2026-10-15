from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_first_ma30_pullback_cohesion_ppt import (
    compute_event_state_metrics,
    compute_true_streak,
    summarize_extended_metrics,
    attach_event_metrics,
    create_distribution_chart,
)


def test_compute_true_streak_resets_on_false_or_missing() -> None:
    mask = pd.Series([False, True, True, pd.NA, True, False, True], dtype="boolean")

    assert compute_true_streak(mask).tolist() == [0, 1, 2, 0, 1, 0, 1]


def test_event_metrics_use_strict_rises_bull_order_and_relative_gap() -> None:
    frame = pd.DataFrame(
        {
            "ma30": [3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
            "ma60": [2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
            "ma120": [2.0, 2.5, 3.0, 3.5, 4.0, 4.1],
        }
    )

    result = compute_event_state_metrics(frame, position=5)

    assert result["ma30_streak_at_position"] == 5
    assert result["ma60_streak_at_position"] == 5
    assert result["ma120_streak_at_position"] == 5
    assert result["ma30_ma60_ma120_bull_streak_at_position"] == 6
    assert result["all_three_rising_bull_streak_at_position"] == 5
    assert result["ma30_vs_ma60_pct"] == pytest.approx(60.0)
    assert result["ma60_vs_ma120_pct"] == pytest.approx(100.0 * (5.0 / 4.1 - 1.0))


def test_bull_alignment_streak_restarts_after_order_break() -> None:
    frame = pd.DataFrame(
        {
            "ma30": [3.0, 4.0, 5.0, 3.8, 7.0, 8.0],
            "ma60": [2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
            "ma120": [2.0, 2.5, 3.0, 3.5, 4.0, 4.1],
        }
    )

    result = compute_event_state_metrics(frame, position=5)

    assert result["ma30_ma60_ma120_bull_streak_at_position"] == 2
    assert result["all_three_rising_bull_streak_at_position"] == 2


def test_attach_event_metrics_matches_event_date_in_full_stock_history() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["20260101", "20260102", "20260105"],
            "ma30": [3.0, 4.0, 5.0],
            "ma60": [2.0, 3.0, 4.0],
            "ma120": [1.0, 2.0, 3.0],
        }
    )
    events = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "pullback_date": ["20260105"],
            "scope": ["UNION_UNIQUE"],
        }
    )

    result = attach_event_metrics(events, {"000001.SZ": frame})

    assert result.iloc[0]["ma30_streak_at_position"] == 2
    assert result.iloc[0]["ma30_ma60_ma120_bull_streak_at_position"] == 3
    assert result.iloc[0]["ma30_vs_ma60_pct"] == pytest.approx(25.0)


def test_attach_event_metrics_rejects_mismatched_formal_streak() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["20260101", "20260102", "20260105"],
            "ma30": [3.0, 4.0, 5.0],
            "ma60": [2.0, 3.0, 4.0],
            "ma120": [1.0, 2.0, 3.0],
        }
    )
    events = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "pullback_date": ["20260105"],
            "scope": ["MA60"],
            "ma60_streak_at_position": [99],
        }
    )

    with pytest.raises(ValueError, match="streak mismatch"):
        attach_event_metrics(events, {"000001.SZ": frame})


def test_distribution_chart_is_created(tmp_path: Path) -> None:
    events = pd.DataFrame(
        {
            "ma30_streak_at_position": [1, 2, 3],
            "ma60_streak_at_position": [2, 3, 4],
            "ma120_streak_at_position": [3, 4, 5],
            "ma30_ma60_ma120_bull_streak_at_position": [0, 2, 5],
            "ma30_vs_ma60_pct": [-1.0, 2.0, 5.0],
        }
    )
    output = tmp_path / "distribution.png"

    create_distribution_chart(events, output)

    assert output.exists()
    assert output.stat().st_size > 0


def test_extended_summary_reports_zero_inclusive_distributions() -> None:
    events = pd.DataFrame(
        {
            "scope": ["UNION_UNIQUE"] * 4,
            "ma30_streak_at_position": [0, 2, 4, 6],
            "ma60_streak_at_position": [0, 10, 20, 30],
            "ma120_streak_at_position": [0, 5, 15, 25],
            "ma30_ma60_ma120_bull_streak_at_position": [0, 0, 3, 7],
            "all_three_rising_bull_streak_at_position": [0, 0, 2, 4],
            "ma30_vs_ma60_pct": [-1.0, 1.0, 3.0, 5.0],
        }
    )

    summary = summarize_extended_metrics(events)
    row = summary.iloc[0]

    assert row["event_count"] == 4
    assert row["ma30_active_count"] == 3
    assert row["ma30_streak_all_median"] == pytest.approx(3.0)
    assert row["bull_alignment_active_count"] == 2
    assert row["bull_alignment_active_median"] == pytest.approx(5.0)
    assert row["ma30_vs_ma60_pct_median"] == pytest.approx(2.0)
    assert row["ma30_vs_ma60_pct_p25"] == pytest.approx(0.5)
    assert row["ma30_vs_ma60_pct_p75"] == pytest.approx(3.5)
