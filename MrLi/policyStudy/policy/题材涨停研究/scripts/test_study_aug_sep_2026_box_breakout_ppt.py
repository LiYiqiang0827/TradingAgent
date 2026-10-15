from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_aug_sep_2026_box_breakout_ppt import (
    classify_setup,
    compute_similarity_score,
    compute_rising_streak,
    detect_prebox_impulse,
    find_prebreakout_box,
    format_period_label,
    is_big_bull,
    is_sixty_day_close_breakout,
    rank_table_page_count,
    scan_stock_events,
    select_unique_best_events,
)


def _frame(count: int = 100) -> pd.DataFrame:
    close = np.full(count, 10.0)
    return pd.DataFrame(
        {
            "trade_date": [f"D{i:03d}" for i in range(count)],
            "open": close.copy(),
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close.copy(),
            "pre_close": close.copy(),
            "pct_chg": np.zeros(count),
            "raw_vol": np.full(count, 100.0),
            "is_limit_up": np.zeros(count, dtype=bool),
        }
    )


def test_period_label_and_dynamic_rank_table_pages() -> None:
    assert format_period_label("20260601", "20260731") == "2026年6—7月"
    assert format_period_label("20260801", "20260930") == "2026年8—9月"
    assert rank_table_page_count(16) == 2
    assert rank_table_page_count(17) == 3


def test_rising_streak_resets_on_flat_down_and_missing() -> None:
    values = pd.Series([1.0, 2.0, 3.0, 3.0, 4.0, np.nan, 5.0, 4.0, 5.0])
    assert compute_rising_streak(values).tolist() == [0, 1, 2, 0, 1, 0, 0, 0, 1]


def test_box_is_longest_immediate_prebreakout_suffix_within_range() -> None:
    frame = _frame(55)
    # Event is position 50. Days 20-49 form a 30-day, <=15% box.
    frame.loc[20:49, "high"] = np.linspace(10.0, 11.2, 30)
    frame.loc[20:49, "low"] = np.linspace(9.9, 10.4, 30)
    # Including day 19 would widen the box beyond 15%.
    frame.loc[19, ["high", "low"]] = [12.0, 9.0]

    box = find_prebreakout_box(
        frame,
        event_position=50,
        min_days=20,
        max_days=120,
        max_range_pct=15.0,
    )

    assert box is not None
    assert box["box_start_position"] == 20
    assert box["box_end_position"] == 49
    assert box["box_trading_days"] == 30
    assert box["box_range_pct"] == pytest.approx(100.0 * (11.2 - 9.9) / 9.9)


def test_box_fails_when_last_twenty_days_are_too_wide() -> None:
    frame = _frame(30)
    frame.loc[9:28, "high"] = 12.0
    frame.loc[9:28, "low"] = 9.0
    assert find_prebreakout_box(
        frame,
        event_position=29,
        min_days=20,
        max_days=120,
        max_range_pct=15.0,
    ) is None


def test_sixty_day_breakout_requires_close_above_prior_highs() -> None:
    frame = _frame(70)
    frame.loc[:68, "high"] = 11.0
    frame.loc[69, "close"] = 11.01
    assert is_sixty_day_close_breakout(frame, 69, lookback=60)
    frame.loc[69, "close"] = 11.0
    assert not is_sixty_day_close_breakout(frame, 69, lookback=60)


def test_big_bull_requires_return_body_and_strong_close_location() -> None:
    strong = pd.Series({"open": 10.0, "high": 10.8, "low": 9.9, "close": 10.7, "pre_close": 10.0})
    assert is_big_bull(strong, min_return_pct=6.0, min_body_pct=5.0, min_close_location=0.70)
    weak_body = strong.copy(); weak_body["open"] = 10.3
    assert not is_big_bull(weak_body, min_return_pct=6.0, min_body_pct=5.0, min_close_location=0.70)
    weak_close = strong.copy(); weak_close["close"] = 10.5
    assert not is_big_bull(weak_close, min_return_pct=4.0, min_body_pct=4.0, min_close_location=0.80)


def test_big_bull_uses_pct_chg_instead_of_unadjusted_pre_close_with_qfq_prices() -> None:
    row = pd.Series(
        {
            "open": 10.0,
            "high": 10.8,
            "low": 9.9,
            "close": 10.7,
            "pre_close": 20.0,
            "pct_chg": 7.0,
        }
    )
    assert is_big_bull(row, min_return_pct=6.0, min_body_pct=5.0, min_close_location=0.70)


def test_prebox_impulse_accepts_two_consecutive_limit_ups_with_volume_and_rise() -> None:
    frame = _frame(80)
    frame.loc[38, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg", "is_limit_up"]] = [10.0, 11.0, 9.95, 11.0, 10.0, 180.0, 10.0, True]
    frame.loc[39, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg", "is_limit_up"]] = [11.0, 12.1, 10.9, 12.1, 11.0, 200.0, 10.0, True]
    frame.loc[40:49, "close"] = 12.1

    impulse = detect_prebox_impulse(
        frame,
        box_start_position=50,
        lookback=30,
        max_signal_gap=10,
        min_big_bull_return_pct=5.0,
        min_big_bull_body_pct=3.0,
        min_rise_pct=12.0,
        min_signal_volume_ratio=1.3,
    )

    assert impulse["prebox_impulse_pass"]
    assert impulse["prebox_impulse_mode"] == "consecutive_limit_up"
    assert impulse["prebox_limit_up_count"] == 2
    assert impulse["prebox_max_consecutive_limit_up"] == 2
    assert impulse["prebox_impulse_rise_pct"] >= 20.0
    assert impulse["prebox_max_signal_volume_ratio"] >= 1.8


def test_prebox_impulse_accepts_limit_up_plus_nearby_big_bull() -> None:
    frame = _frame(80)
    frame.loc[35, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg", "is_limit_up"]] = [10.0, 11.0, 9.95, 11.0, 10.0, 180.0, 10.0, True]
    frame.loc[41, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg"]] = [11.1, 11.9, 11.0, 11.8, 11.0, 170.0, 7.27]
    frame.loc[42:49, "close"] = 11.8

    impulse = detect_prebox_impulse(
        frame,
        box_start_position=50,
        lookback=30,
        max_signal_gap=10,
        min_big_bull_return_pct=5.0,
        min_big_bull_body_pct=3.0,
        min_rise_pct=12.0,
        min_signal_volume_ratio=1.3,
    )

    assert impulse["prebox_impulse_pass"]
    assert impulse["prebox_impulse_mode"] == "limit_up_plus_big_bull"
    assert impulse["prebox_big_bull_count"] >= 1


def test_prebox_impulse_uses_a_looser_big_bull_threshold_than_the_breakout_event() -> None:
    frame = _frame(80)
    frame.loc[35, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg", "is_limit_up"]] = [10.0, 11.0, 9.95, 11.0, 10.0, 180.0, 10.0, True]
    # 5.09% close return and 4.14% close/open body: valid only under the
    # frozen 5%/3% pre-box rule, not the 6%/5% breakout-event rule.
    frame.loc[41, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg"]] = [11.1, 11.65, 11.0, 11.56, 11.0, 170.0, 5.09]
    frame.loc[42:49, "close"] = 11.56

    impulse = detect_prebox_impulse(frame, box_start_position=50)

    assert impulse["prebox_impulse_pass"]
    assert impulse["prebox_impulse_mode"] == "limit_up_plus_big_bull"


def test_prebox_impulse_rejects_two_big_bulls_without_a_limit_up() -> None:
    frame = _frame(80)
    frame.loc[35, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg"]] = [10.0, 10.7, 9.9, 10.6, 10.0, 180.0, 6.0]
    frame.loc[41, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg"]] = [10.7, 11.4, 10.6, 11.3, 10.6, 170.0, 6.6]

    impulse = detect_prebox_impulse(frame, box_start_position=50)

    assert impulse["prebox_big_bull_count"] == 2
    assert not impulse["prebox_impulse_pass"]
    assert impulse["prebox_impulse_mode"] == ""


def test_prebox_impulse_rejects_two_nonconsecutive_limit_ups_without_a_big_bull() -> None:
    frame = _frame(80)
    frame.loc[35, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg", "is_limit_up"]] = [10.0, 11.0, 9.95, 11.0, 10.0, 180.0, 10.0, True]
    frame.loc[41, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg", "is_limit_up"]] = [11.0, 12.1, 10.9, 12.1, 11.0, 190.0, 10.0, True]

    impulse = detect_prebox_impulse(frame, box_start_position=50)

    assert impulse["prebox_limit_up_count"] == 2
    assert impulse["prebox_max_consecutive_limit_up"] == 1
    assert not impulse["prebox_impulse_pass"]
    assert impulse["prebox_impulse_mode"] == ""


def test_prebox_impulse_rejects_unexpanded_volume() -> None:
    frame = _frame(80)
    frame.loc[38, ["open", "high", "low", "close", "pre_close", "pct_chg", "is_limit_up"]] = [10.0, 11.0, 9.95, 11.0, 10.0, 10.0, True]
    frame.loc[39, ["open", "high", "low", "close", "pre_close", "pct_chg", "is_limit_up"]] = [11.0, 12.1, 10.9, 12.1, 11.0, 10.0, True]
    frame.loc[40:49, "close"] = 12.1

    impulse = detect_prebox_impulse(
        frame,
        box_start_position=50,
        lookback=30,
        max_signal_gap=10,
        min_big_bull_return_pct=5.0,
        min_big_bull_body_pct=3.0,
        min_rise_pct=12.0,
        min_signal_volume_ratio=1.3,
    )

    assert not impulse["prebox_impulse_pass"]
    assert impulse["prebox_impulse_mode"] == "consecutive_limit_up"


def test_quality_tiers_separate_strict_preferred_and_structural_setups() -> None:
    assert classify_setup(
        event_volume_ratio=1.6,
        open_inside_box=True,
        six_ma_spread_pct=7.5,
        bullish_order_count=5,
        ma60_rising_streak=25,
        ma120_rising_streak=12,
    ) == "A_严格优选"
    assert classify_setup(
        event_volume_ratio=1.35,
        open_inside_box=True,
        six_ma_spread_pct=9.5,
        bullish_order_count=4,
        ma60_rising_streak=10,
        ma120_rising_streak=5,
    ) == "B_主观察"
    assert classify_setup(
        event_volume_ratio=1.2,
        open_inside_box=False,
        six_ma_spread_pct=11.0,
        bullish_order_count=3,
        ma60_rising_streak=4,
        ma120_rising_streak=0,
    ) == "C_仅结构符合"


def test_similarity_score_rewards_cohesion_volume_alignment_and_long_trend() -> None:
    strong = compute_similarity_score(
        box_days=40,
        box_range_pct=10.0,
        event_volume_ratio=3.0,
        open_inside_box=True,
        six_ma_spread_pct=7.0,
        bullish_order_count=5,
        ma60_rising_streak=25,
        ma120_rising_streak=15,
        impulse_mode="consecutive_limit_up",
        impulse_rise_pct=30.0,
    )
    weak = compute_similarity_score(
        box_days=20,
        box_range_pct=15.0,
        event_volume_ratio=1.0,
        open_inside_box=False,
        six_ma_spread_pct=18.0,
        bullish_order_count=2,
        ma60_rising_streak=0,
        ma120_rising_streak=0,
        impulse_mode="limit_up_plus_big_bull",
        impulse_rise_pct=12.0,
    )
    assert 0.0 <= weak < strong <= 100.0


def test_unique_stock_selection_prefers_higher_tier_before_earlier_date() -> None:
    events = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
            "event_date": ["20260805", "20260910", "20260820"],
            "quality_tier": ["C_仅结构符合", "A_严格优选", "B_主观察"],
            "event_volume_ratio": [1.1, 2.0, 1.5],
        }
    )

    selected = select_unique_best_events(events)

    assert selected["ts_code"].tolist() == ["000001.SZ", "000002.SZ"]
    assert selected.iloc[0]["event_date"] == "20260910"
    assert selected.iloc[0]["quality_tier"] == "A_严格优选"


def test_scan_stock_events_finds_limit_up_breakout_after_impulse_and_box() -> None:
    frame = _frame(180)
    frame["trade_date"] = pd.date_range("2026-01-01", periods=180, freq="B").strftime("%Y%m%d")
    # Two-volume-expanded limit ups before the platform.
    frame.loc[100, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg", "is_limit_up"]] = [10.0, 11.0, 9.95, 11.0, 10.0, 200.0, 10.0, True]
    frame.loc[101, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg", "is_limit_up"]] = [11.0, 12.1, 10.9, 12.1, 11.0, 220.0, 10.0, True]
    frame.loc[102:118, ["open", "high", "low", "close", "pre_close"]] = [12.0, 12.3, 11.7, 12.0, 12.0]
    # A wide day forces the immediately preceding box to begin at position 120.
    frame.loc[119, ["open", "high", "low", "close", "pre_close"]] = [12.0, 14.0, 10.0, 12.0, 12.0]
    frame.loc[120:144, ["open", "high", "low", "close", "pre_close"]] = [12.0, 12.4, 11.5, 12.0, 12.0]
    frame.loc[145, ["open", "high", "low", "close", "pre_close", "raw_vol", "pct_chg", "is_limit_up"]] = [12.1, 14.5, 12.0, 14.5, 100.0, 300.0, 20.83, True]

    events = scan_stock_events(
        frame,
        ts_code="000001.SZ",
        stock_name="样本股",
        event_start=frame.loc[145, "trade_date"],
        event_end=frame.loc[145, "trade_date"],
    )

    assert len(events) == 1
    event = events.iloc[0]
    assert event["event_date"] == frame.loc[145, "trade_date"]
    assert event["box_start_position"] == 120
    assert event["box_trading_days"] == 25
    assert event["prebox_impulse_mode"] == "consecutive_limit_up"
    assert bool(event["is_limit_up"])
    assert event["new_high_lookback_days"] == 60
    assert event["event_return_pct"] == pytest.approx(20.83)
