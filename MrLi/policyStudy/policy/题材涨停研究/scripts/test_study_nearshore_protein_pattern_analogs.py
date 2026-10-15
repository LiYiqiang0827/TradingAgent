from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_nearshore_protein_pattern_analogs import (
    event_path_vector,
    evaluate_touch_candidate,
    locate_recent_wave,
    prepare_features,
    scan_structural_events,
    score_event_similarity,
    same_window_similarity,
)


def test_prepare_features_computes_strict_ma30_streak_and_short_spread() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=35).strftime("%Y%m%d"),
            "open": np.arange(35, dtype=float) + 99.0,
            "high": np.arange(35, dtype=float) + 101.0,
            "low": np.arange(35, dtype=float) + 98.0,
            "close": np.arange(35, dtype=float) + 100.0,
            "vol": np.arange(35, dtype=float) + 1000.0,
        }
    )

    result = prepare_features(frame)

    assert result.iloc[-1]["ma30_rising_streak"] == 5
    expected_spread = (
        result.iloc[-1][["ma5", "ma10", "ma30"]].max()
        / result.iloc[-1][["ma5", "ma10", "ma30"]].min()
        - 1.0
    ) * 100.0
    assert result.iloc[-1]["ma5_ma10_ma30_spread_pct"] == pytest.approx(expected_spread)


def _synthetic_touch_frame() -> pd.DataFrame:
    rows = 50
    close = np.full(rows, 100.0)
    close[:16] = np.linspace(100.0, 90.0, 16)
    close[16:28] = np.linspace(95.0, 160.0, 12)
    close[28:40] = np.linspace(155.0, 112.0, 12)
    close[40:] = np.linspace(120.0, 145.0, 10)
    ma30 = np.linspace(85.0, 115.0, rows)
    ma5 = ma30 * 1.03
    ma10 = ma30 * 1.02
    ma5[27] = ma30[27] * 1.30
    ma10[27] = ma30[27] * 1.18
    return pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=rows).strftime("%Y%m%d"),
            "open": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "vol": np.linspace(1000.0, 1500.0, rows),
            "ma5": ma5,
            "ma10": ma10,
            "ma30": ma30,
            "ma60": ma30 * 0.95,
            "ma120": ma30 * 0.90,
            "ma30_rising_streak": np.arange(rows),
            "ma60_rising_streak": np.arange(rows),
            "ma120_rising_streak": np.arange(rows),
            "ma5_ma10_ma30_spread_pct": (np.maximum.reduce([ma5, ma10, ma30]) / np.minimum.reduce([ma5, ma10, ma30]) - 1) * 100,
            "low_vs_ma30_pct": (close * 0.98 / ma30 - 1) * 100,
            "close_vs_ma30_pct": (close / ma30 - 1) * 100,
        }
    )


def test_touch_candidate_captures_wave_pullback_and_convergence() -> None:
    frame = _synthetic_touch_frame()
    frame.loc[39, "low"] = frame.loc[39, "ma30"] * 0.99
    frame.loc[39, "close"] = frame.loc[39, "ma30"] * 1.01
    frame.loc[39, "close_vs_ma30_pct"] = 1.0
    frame.loc[39, "low_vs_ma30_pct"] = -1.0

    event = evaluate_touch_candidate(frame, touch_position=39)

    assert event is not None
    assert event["wave_start_position"] == 15
    assert event["peak_position"] == 27
    assert event["correction_days"] == 12
    assert event["first_wave_gain_pct"] == pytest.approx((160.0 / 90.0 - 1) * 100)
    assert event["pullback_drawdown_pct"] < -20.0


def test_touch_candidate_rejects_nonconverged_short_mas() -> None:
    frame = _synthetic_touch_frame()
    frame.loc[39, "low"] = frame.loc[39, "ma30"] * 0.99
    frame.loc[39, "close"] = frame.loc[39, "ma30"] * 1.01
    frame.loc[39, "close_vs_ma30_pct"] = 1.0
    frame.loc[39, "low_vs_ma30_pct"] = -1.0
    frame.loc[39, "ma5_ma10_ma30_spread_pct"] = 12.0

    assert evaluate_touch_candidate(frame, touch_position=39) is None


def test_touch_candidate_can_relax_full_wave_ma30_rise_as_sensitivity() -> None:
    frame = _synthetic_touch_frame()
    frame.loc[39, "low"] = frame.loc[39, "ma30"] * 0.99
    frame.loc[39, "close"] = frame.loc[39, "ma30"] * 1.01
    frame.loc[39, "close_vs_ma30_pct"] = 1.0
    frame.loc[39, "low_vs_ma30_pct"] = -1.0
    frame.loc[15, "ma30"] = frame.loc[39, "ma30"] * 1.05

    assert evaluate_touch_candidate(frame, touch_position=39) is None
    assert evaluate_touch_candidate(
        frame,
        touch_position=39,
        require_ma30_above_wave_start=False,
    ) is not None


def test_scan_structural_events_deduplicates_nearby_touch_days() -> None:
    frame = _synthetic_touch_frame()
    for position in (39, 40):
        frame.loc[position, "low"] = frame.loc[position, "ma30"] * 0.99
        frame.loc[position, "close"] = frame.loc[position, "ma30"] * 1.01
        frame.loc[position, "close_vs_ma30_pct"] = 1.0
        frame.loc[position, "low_vs_ma30_pct"] = -1.0
    active = {
        str(frame.iloc[position]["trade_date"]): (60,)
        for position in range(len(frame))
    }

    events = scan_structural_events(
        frame,
        active_periods_by_date=active,
        ts_code="000001.SZ",
        stock_name="测试股",
        cooldown_days=10,
    )

    assert len(events) == 1
    assert events[0]["touch_position"] == 39
    assert events[0]["anchor_periods"] == "60"


def test_event_similarity_is_100_for_reference_and_lower_when_distorted() -> None:
    frame = _synthetic_touch_frame()
    frame.loc[39, "low"] = frame.loc[39, "ma30"] * 0.99
    frame.loc[39, "close"] = frame.loc[39, "ma30"] * 1.01
    frame.loc[39, "close_vs_ma30_pct"] = 1.0
    frame.loc[39, "low_vs_ma30_pct"] = -1.0
    event = evaluate_touch_candidate(frame, touch_position=39)
    assert event is not None
    path = event_path_vector(frame, event)
    altered = dict(event)
    altered["first_wave_gain_pct"] = event["first_wave_gain_pct"] / 2.0
    altered_path = path[::-1].copy()

    exact = score_event_similarity(event, path, event, path)
    distorted = score_event_similarity(event, path, altered, altered_path)

    assert exact == pytest.approx(100.0)
    assert distorted < exact


def test_same_window_similarity_rewards_identical_path() -> None:
    dates = pd.bdate_range("2026-08-03", periods=20).strftime("%Y%m%d")
    close = np.r_[np.linspace(100.0, 160.0, 10), np.linspace(155.0, 120.0, 10)]
    reference = pd.DataFrame({"trade_date": dates, "close": close})
    identical = reference.copy()
    reversed_path = pd.DataFrame({"trade_date": dates, "close": close[::-1]})

    exact = same_window_similarity(reference, identical, start_date=dates[0], end_date=dates[-1])
    reversed_score = same_window_similarity(reference, reversed_path, start_date=dates[0], end_date=dates[-1])

    assert exact["same_window_similarity_score"] == pytest.approx(100.0)
    assert reversed_score["same_window_similarity_score"] < exact["same_window_similarity_score"]
    assert exact["common_trading_days"] == 20


def test_locate_recent_wave_finds_prior_peak_and_pre_wave_low() -> None:
    close = np.full(45, 100.0)
    close[15] = 90.0
    close[16:28] = np.linspace(95.0, 160.0, 12)
    close[28:40] = np.linspace(155.0, 112.0, 12)
    frame = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=len(close)).strftime("%Y%m%d"),
            "close": close,
        }
    )

    result = locate_recent_wave(frame, touch_position=39)

    assert result == {"wave_start_position": 15, "peak_position": 27}
