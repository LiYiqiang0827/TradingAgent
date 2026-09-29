"""Behavioral checks for the MA20 second-wave replay."""

from __future__ import annotations

import pandas as pd

from study_ma20_second_wave import execute, rising_run


def test_rising_run_stops_at_flat_or_falling_observation():
    values = pd.Series([1.0, 1.2, 1.3, 1.3, 1.4, 1.5])
    assert rising_run(values, 2) == 2
    assert rising_run(values, 5) == 2


def test_ma20_break_sells_no_earlier_than_next_day_and_waits_through_locked_open():
    stock = pd.DataFrame([
        ("20260803", 10.0, 10.1, 9.9, 10.0, 10.0),
        ("20260804", 10.0, 10.1, 9.6, 9.7, 10.0),
        ("20260805", 8.73, 8.73, 8.73, 8.73, 8.73),
        ("20260806", 8.5, 8.7, 8.3, 8.6, 8.5),
    ], columns=["trade_date", "open", "high", "low", "close", "raw_open"])
    stock["ma20"] = [9.8, 9.9, 9.8, 9.7]
    stock["vol"] = [100, 100, 100, 100]
    limits = {"20260804": (11.0, 9.0),
              "20260805": (10.67, 8.73),
              "20260806": (9.6, 7.86)}
    result = execute(stock, "20260803", limits)
    assert result["entry_date"] == "20260804"
    assert result["decision_date"] == "20260804"
    assert result["exit_date"] == "20260806"
    assert result["blocked_exit_days"] == 1
    assert result["exit_reason"] == "ma20_close_break"
