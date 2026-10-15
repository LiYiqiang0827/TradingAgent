from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_rising_ma_reactivation_202605_202608 import (
    _episodes, _first_wave_boards, _shape_without_sustained_volume, _wave_volume)


def test_first_wave_board_must_be_after_trough_and_before_peak_at_official_cap() -> None:
    dates = pd.bdate_range("2026-01-01", periods=40).strftime("%Y%m%d")
    stock = pd.DataFrame({"trade_date": dates, "low": [9.0] * 40,
                          "raw_close": [10.0] * 40, "raw_vol": [1000] * 40,
                          "pre_close": [9.0] * 40})
    stock.loc[10, "low"] = 5.0
    limits = {date: (10.0, 8.0) for date in dates}
    start, boards = _first_wave_boards(stock, dates[30],
                                      {dates[5], dates[20], dates[35]}, limits)
    assert start == dates[10]
    assert boards == [dates[20]]
    limits[dates[20]] = (11.0, 8.0)
    assert _first_wave_boards(stock, dates[30], {dates[20]}, limits)[1] == []


def test_episode_collapse_is_by_stock_and_subtype() -> None:
    dates = pd.bdate_range("2026-01-01", periods=25).strftime("%Y%m%d").tolist()
    frame = pd.DataFrame([
        {"ts_code": "A", "trade_date": dates[0], "template_observation": "early"},
        {"ts_code": "A", "trade_date": dates[4], "template_observation": "early"},
        {"ts_code": "A", "trade_date": dates[11], "template_observation": "early"},
        {"ts_code": "A", "trade_date": dates[4], "template_observation": "coil"},
        {"ts_code": "B", "trade_date": dates[4], "template_observation": "early"},
    ])
    got = _episodes(frame, dates, gap=10)
    assert len(got) == 4
    assert got[got.ts_code.eq("A") & got.template_observation.eq("early")].trade_date.tolist() == [dates[0], dates[11]]


def test_wave_and_pullback_volume_share_pre_wave_baseline() -> None:
    dates = pd.bdate_range("2026-01-01", periods=50).strftime("%Y%m%d").tolist()
    frame = pd.DataFrame({"trade_date": dates,
                          "raw_vol": [100.0] * 20 + [300.0] * 11 + [200.0] * 19})
    result = _wave_volume(frame, dates[20], dates[30], dates[40])
    assert result["first_wave_volume_vs_pre"] == 3
    assert result["pullback_volume_vs_pre"] == 2


def test_volume_failure_is_retained_in_pre_volume_denominator() -> None:
    result = {"ma20_pullback_gates": {"stage_new_high": True,
                                      "first_wave_volume_sustained": False,
                                      "pullback_volume_sustained": False}}
    assert _shape_without_sustained_volume(result)
    result["ma20_pullback_gates"]["stage_new_high"] = False
    assert not _shape_without_sustained_volume(result)
