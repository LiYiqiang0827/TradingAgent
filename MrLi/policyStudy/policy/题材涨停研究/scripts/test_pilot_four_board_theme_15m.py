"""Clock and denominator checks for the 30-event four-board pilot."""

from types import SimpleNamespace

import pandas as pd
import pytest

from pilot_four_board_theme_15m import d0_ladder, execute_minutes, first_break_context, minute_schedule


def _market(first_open_next_day=9.5):
    days = ["20260105", "20260106"]
    slots = minute_schedule(days)
    one = pd.DataFrame({
        "datetime": slots,
        "trade_date": [int(s.strftime("%Y%m%d")) for s in slots],
        "open": [first_open_next_day if i == 240 else 10.0 for i in range(len(slots))],
        "high": 10.1, "low": 9.8, "close": 10.0, "vol": 100,
        "adj_factor": 1.0,
    })
    times = ["09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30",
             "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00"]
    fifteen = pd.DataFrame({
        "datetime": [pd.Timestamp(f"{d} {t}") for d in days for t in times],
        "trade_date": [int(d) for d in days for _ in times],
        "open": 10.0, "high": 10.1, "low": 9.8, "close": 10.0,
        "vol": 100, "adj_factor": 1.0,
    })
    fifteen.loc[fifteen.datetime.eq(pd.Timestamp("20260105 10:00")), "close"] = 9.8
    limits = {("000001.SZ", d): (11.0, 9.0) for d in days}
    signal = {"signal_time": "2026-01-05 09:45:00", "signal_close": 10.0,
              "signal_low": 9.9, "signal_factor": 1.0}
    return one, fifteen, signal, days, limits


def test_entry_waits_one_minute_and_entry_day_failure_sells_next_open():
    one, fifteen, signal, days, limits = _market()
    result = execute_minutes(one, fifteen, signal, days, "000001.SZ", limits)
    assert result["status"] == "closed"
    assert result["entry_time"] == "2026-01-05 09:46:00"
    assert result["entry_day_invalid"] is True
    assert result["exit_time"] == "2026-01-06 09:30:00"


def test_locked_down_open_defers_exit_to_first_unlocked_minute():
    one, fifteen, signal, days, limits = _market(first_open_next_day=9.0)
    one.loc[one.datetime.eq(pd.Timestamp("20260106 09:32")), "open"] = 9.5
    result = execute_minutes(one, fifteen, signal, days, "000001.SZ", limits)
    assert result["status"] == "closed"
    assert result["blocked_exit_bars"] == 1
    assert result["exit_time"] == "2026-01-06 09:31:00"


def test_ladder_counts_primary_limit_peers_and_auxiliary_failed_limit():
    event = SimpleNamespace(d0="20260105", ts_code="000001.SZ", theme_id="T")
    events = pd.DataFrame([
        ("20260105", "000001.SZ", "自身", "涨停", 4, "T"),
        ("20260105", "000002.SZ", "同伴", "涨停", 2, "T"),
        ("20260105", "000003.SZ", "同伴乙", "涨停", 1, "T"),
        ("20260105", "000004.SZ", "未封住", "炸板", None, "T"),
    ], columns=["trade_date", "ts_code", "name", "tag", "board_height", "theme_id"])
    peers, facts = d0_ladder(events, event)
    assert len(peers) == 2
    assert facts["d0_theme_peer_second_board"] == 1
    assert facts["d0_theme_peer_failed_limit"] == 1


def test_first_break_state_uses_break_close_and_theme_width_only_after_close():
    event = SimpleNamespace(ts_code="000001.SZ", d0="20260105", theme_id="T")
    daily = {("000001.SZ", "20260105"): {"close": 10.0},
             ("000001.SZ", "20260106"): {"close": 9.5}}
    result = first_break_context(event, "20260106", daily, {("T", "20260106"): 1})
    assert result["first_break_close_vs_d0_pct"] == pytest.approx(-5.0)
    assert result["first_break_risk_state"] == "lost_support_and_theme"
    assert first_break_context(event, None, daily, {})["first_break_risk_state"] == "unobserved"
