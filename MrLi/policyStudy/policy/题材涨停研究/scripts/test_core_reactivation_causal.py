"""Regression tests for time-causal signals and next-bar execution boundaries."""
from __future__ import annotations

import pandas as pd
import pytest

from study_core_reactivation_causal import TIMES, execute, find_setup


@pytest.fixture
def market():
    calendar = pd.bdate_range("2026-01-05", periods=12).strftime("%Y%m%d").tolist()
    rows = []
    for day_index, date in enumerate(calendar[:11]):
        for bar_index, time in enumerate(TIMES):
            price = 10.0 if day_index == 0 else 9.15
            rows.append({
                "ts_code": "000001.SZ", "trade_date": int(date),
                "datetime": pd.Timestamp(f"{date} {time}"), "time_idx": bar_index,
                "open": price, "high": price + 0.05, "low": price - 0.05,
                "close": price, "vol": 100, "adj_factor": 1.0,
            })
    bars = pd.DataFrame(rows)
    return calendar, bars


@pytest.fixture
def stopped_trade(market):
    calendar, source = market
    bars = source.iloc[:64].copy()
    bars.loc[:, ["open", "high", "low", "close"]] = 10.0
    # Stop is observable next day at 09:45; the following bar opens at 9.6.
    bars.loc[[16, 17], ["open", "high", "low", "close"]] = [9.6, 9.7, 9.5, 9.6]
    signal = {
        "ts_code": "000001.SZ", "signal_time": str(bars.iloc[0].datetime),
        "signal_close": 10.0, "neckline": 9.0, "signal_factor": 1.0,
    }
    limits = {(date, "000001.SZ"): (11.0, 8.0) for date in calendar}
    preclose = {(date, "000001.SZ"): 10.0 for date in calendar}
    return calendar, bars, signal, limits, preclose


def test_setup_is_invariant_when_all_bars_after_signal_are_removed(market):
    calendar, bars = market
    bars.loc[16, "low"] = 9.0
    bars.loc[21, ["open", "high", "low", "close", "vol"]] = [9.15, 9.4, 9.1, 9.35, 200]
    anchor = {"anchor_date": calendar[0], "ts_code": "000001.SZ", "theme_id": "T"}
    themes = {(date, "T"): {"heat": 80, "width": 5, "rank": 1} for date in calendar}
    full = find_setup(bars, anchor, themes, calendar, bars.iloc[-1].datetime)
    assert full["status"] == "signal"
    stamp = pd.Timestamp(full["signal_time"])
    prefix = bars[bars.datetime <= stamp].copy()
    assert len(prefix) < len(bars)
    assert find_setup(prefix, anchor, themes, calendar, stamp) == full


def test_factor_change_after_exit_cannot_remove_completed_trade(stopped_trade):
    calendar, bars, signal, limits, preclose = stopped_trade
    original = execute(bars, signal, calendar, limits, preclose, False)
    assert original["status"] == "closed"
    changed = bars.copy()
    changed.loc[changed.trade_date.eq(int(calendar[3])), "adj_factor"] = 2.0
    assert execute(changed, signal, calendar, limits, preclose, False) == original


def test_missing_afternoon_bar_cannot_remove_completed_morning_exit(stopped_trade):
    calendar, bars, signal, limits, preclose = stopped_trade
    original = execute(bars, signal, calendar, limits, preclose, False)
    assert original["status"] == "closed"
    missing_stamp = pd.Timestamp(f"{calendar[1]} 15:00")
    assert pd.Timestamp(original["exit_bar_end"]) < missing_stamp
    changed = bars[bars.datetime.ne(missing_stamp)].copy()
    assert execute(changed, signal, calendar, limits, preclose, False) == original


def test_exit_bar_post_fill_low_is_excluded_from_mae(stopped_trade):
    calendar, bars, signal, limits, preclose = stopped_trade
    original = execute(bars, signal, calendar, limits, preclose, False)
    assert original["status"] == "closed"
    changed = bars.copy()
    changed.loc[changed.datetime.eq(pd.Timestamp(original["exit_bar_end"])), "low"] = 1.0
    result = execute(changed, signal, calendar, limits, preclose, False)
    assert result["net"] == original["net"]
    assert result["mae"] == pytest.approx(-0.05)
    assert result["mae"] == original["mae"]


def test_overnight_factor_change_is_not_a_raw_price_pullback_entry(stopped_trade):
    calendar, bars, original_signal, _, _ = stopped_trade
    changed = bars.copy()
    after_anchor = changed.trade_date.gt(int(calendar[0]))
    changed.loc[after_anchor, ["open", "high", "low", "close"]] = 5.0
    changed.loc[after_anchor, "adj_factor"] = 2.0
    signal = {**original_signal, "signal_time": str(changed.iloc[15].datetime), "neckline": 9.9}
    limits = {(date, "000001.SZ"): ((11.0, 9.0) if date == calendar[0] else (5.5, 4.5))
              for date in calendar}
    preclose = {(date, "000001.SZ"): (10.0 if date == calendar[0] else 5.0)
                for date in calendar}
    result = execute(changed, signal, calendar, limits, preclose, True)
    assert result["status"] == "entry_factor_change"
    assert result["buyable"] is False
    assert result["entry_open"] is None
    assert result["net"] is None


def test_entry_day_invalidation_waits_for_next_day_first_open(stopped_trade):
    calendar, bars, signal, limits, preclose = stopped_trade
    changed = bars.copy()
    changed.loc[1, ["open", "high", "low", "close"]] = [10.0, 10.0, 9.5, 9.6]
    # Price recovers before close, but the predeclared invalidation stays pending.
    changed.loc[2:15, ["open", "high", "low", "close"]] = 10.0
    changed.loc[16, ["open", "high", "low", "close"]] = [9.4, 10.0, 9.3, 10.0]
    result = execute(changed, signal, calendar, limits, preclose, True)
    assert result["status"] == "closed"
    assert result["decision_time"] == str(changed.iloc[1].datetime)
    assert result["exit_execution_at"] == str(pd.Timestamp(f"{calendar[1]} 09:30"))
    assert result["exit_bar_end"] == str(changed.iloc[16].datetime)
    assert result["mae"] == pytest.approx(-0.06)


def test_anchor_is_not_available_before_its_close(market):
    calendar, bars = market
    anchor = {"anchor_date": calendar[0], "ts_code": "000001.SZ", "theme_id": "T"}
    result = find_setup(bars, anchor, {}, calendar, pd.Timestamp(f"{calendar[0]} 10:00"))
    assert result["status"] == "anchor_not_yet_known"
    assert result["signal_time"] is None
