"""Clock, first-attempt, T+1, and post-fill invariance regression tests."""
import pandas as pd
import pytest

from study_core_reactivation_minute_execution import TIMES, execute_minutes, minute_schedule


@pytest.fixture
def fixture():
    calendar = pd.bdate_range("2026-06-01", periods=5).strftime("%Y%m%d").tolist()
    one = pd.DataFrame([dict(datetime=t, ts_code="000001.SZ", trade_date=int(t.strftime("%Y%m%d")),
                             open=10.0, high=10.0, low=10.0, close=10.0, vol=100, adj_factor=1.0)
                        for t in minute_schedule(calendar)])
    fifteen = one[one.datetime.dt.strftime("%H:%M").isin(TIMES)].copy()
    signal = dict(ts_code="000001.SZ", signal_time=f"{calendar[0]} 09:45", signal_close=10.0,
                  signal_factor=1.0, neckline=9.0)
    limits = {(d, "000001.SZ"): (11.0, 9.0) for d in calendar}
    preclose = {(d, "000001.SZ"): 10.0 for d in calendar}
    return one, fifteen, signal, calendar, limits, preclose


def test_entry_and_exit_each_require_a_full_minute_reaction(fixture):
    one, fifteen, signal, calendar, limits, preclose = fixture
    decision = pd.Timestamp(f"{calendar[1]} 09:45")
    fifteen.loc[fifteen.datetime.eq(decision), "close"] = 9.6
    result = execute_minutes(one, fifteen, signal, calendar, limits, preclose, False)
    assert result["status"] == "closed"
    assert pd.Timestamp(result["entry_execution_at"]) == pd.Timestamp(signal["signal_time"]) + pd.Timedelta(minutes=1)
    assert pd.Timestamp(result["entry_bar_end"]) == pd.Timestamp(signal["signal_time"]) + pd.Timedelta(minutes=2)
    assert pd.Timestamp(result["exit_execution_at"]) == decision + pd.Timedelta(minutes=1)


@pytest.mark.parametrize("kind, expected", [("missing", "entry_minute_missing"),
                                            ("limit", "entry_at_up_limit")])
def test_first_scheduled_entry_is_not_replaced_by_later_better_bar(fixture, kind, expected):
    one, fifteen, signal, calendar, limits, preclose = fixture
    first = pd.Timestamp(signal["signal_time"]) + pd.Timedelta(minutes=2)
    if kind == "missing":
        one = one[one.datetime.ne(first)]
    else:
        one.loc[one.datetime.eq(first), "open"] = 11.0
    result = execute_minutes(one, fifteen, signal, calendar, limits, preclose, False)
    assert result["status"] == expected
    assert result["buyable"] is False


@pytest.mark.parametrize("signal_time, entry_clock, day_offset", [("11:30", "13:00", 0),
                                                                  ("15:00", "09:30", 1)])
def test_lunch_and_overnight_move_to_next_session(fixture, signal_time, entry_clock, day_offset):
    one, fifteen, signal, calendar, limits, preclose = fixture
    signal["signal_time"] = f"{calendar[0]} {signal_time}"
    result = execute_minutes(one, fifteen, signal, calendar, limits, preclose, False)
    assert pd.Timestamp(result["entry_execution_at"]) == pd.Timestamp(f"{calendar[day_offset]} {entry_clock}")


def test_carry_waits_for_tplus1_and_blocked_sell_minutes(fixture):
    one, fifteen, signal, calendar, limits, preclose = fixture
    fail = pd.Timestamp(f"{calendar[0]} 10:00")
    fifteen.loc[fifteen.datetime.eq(fail), "close"] = 9.6
    first_sell = pd.Timestamp(f"{calendar[1]} 09:31")
    one.loc[one.datetime.isin([first_sell, first_sell + pd.Timedelta(minutes=1)]), "open"] = 9.0
    result = execute_minutes(one, fifteen, signal, calendar, limits, preclose, True)
    assert result["status"] == "closed"
    assert result["blocked_minutes"] == 2
    assert pd.Timestamp(result["exit_execution_at"]) == pd.Timestamp(f"{calendar[1]} 09:32")
    assert pd.Timestamp(result["decision_time"]) == fail


def test_future_removal_and_selling_minute_low_do_not_change_result(fixture):
    one, fifteen, signal, calendar, limits, preclose = fixture
    fifteen.loc[fifteen.datetime.eq(pd.Timestamp(f"{calendar[1]} 09:45")), "close"] = 9.6
    original = execute_minutes(one, fifteen, signal, calendar, limits, preclose, False)
    sold = pd.Timestamp(original["exit_bar_end"])
    changed = one[one.datetime.le(sold)].copy()
    changed.loc[changed.datetime.eq(sold), "low"] = 1.0
    result = execute_minutes(changed, fifteen[fifteen.datetime.le(sold)], signal,
                             calendar, limits, preclose, False)
    assert result == original


def test_reached_gap_is_unresolved_and_overnight_factor_change_rejects_entry(fixture):
    one, fifteen, signal, calendar, limits, preclose = fixture
    missing = pd.Timestamp(f"{calendar[0]} 11:01")
    gap = execute_minutes(one[one.datetime.ne(missing)], fifteen, signal, calendar, limits, preclose, False)
    assert gap["status"] == "holding_minute_missing"
    assert gap["buyable"] is True and gap["net"] is None
    signal["signal_time"] = f"{calendar[0]} 15:00"
    one.loc[one.trade_date.gt(int(calendar[0])), "adj_factor"] = 2.0
    factor = execute_minutes(one, fifteen, signal, calendar, limits, preclose, False)
    assert factor["status"] == "entry_factor_missing_or_changed"
    assert factor["buyable"] is False
