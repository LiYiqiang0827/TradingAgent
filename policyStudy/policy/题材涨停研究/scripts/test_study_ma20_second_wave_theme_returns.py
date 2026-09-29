"""The theme trigger must be known at its close, before next-open execution."""

from __future__ import annotations

import pandas as pd

import study_ma20_second_wave_theme_returns as returns


def test_reexpansion_uses_each_day_as_of_and_requires_stock_above_ma20(monkeypatch):
    days = ["20260810", "20260811", "20260812"]
    stock = pd.DataFrame({"trade_date": days, "close": [10.0, 9.8, 10.1],
                          "ma20": [10.0, 10.0, 10.0]})
    case = pd.Series({"ts_code": "X", "theme_id": "T", "touch_date": days[0],
                      "touch_frozen_old_peer_codes": "A,B"})
    calls = []

    def fake_members(theme_id, *, as_of, historical):
        assert theme_id == "T" and historical
        calls.append(as_of)
        values = ({"20260810": ["A", "X"],
                   "20260811": ["A", "B", "C"],
                   "20260812": ["A", "B", "D"]})[as_of]
        return pd.DataFrame({"ts_code": values,
                             "trade_date": [as_of] * len(values)})

    monkeypatch.setattr(returns, "get_theme_members", fake_members)
    signal = returns._first_theme_reexpansion(case, stock, old_min=2)
    assert calls == days
    assert signal["signal_date"] == "20260812"
    assert signal["signal_old_peer_limit"] == 2
    assert signal["signal_theme_width"] == 3
