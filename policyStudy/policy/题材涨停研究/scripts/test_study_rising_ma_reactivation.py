from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from study_rising_ma_reactivation import describe, prepare_daily, theme_evidence


def test_describe_ignores_future_rows() -> None:
    dates = pd.bdate_range("2025-01-01", periods=210).strftime("%Y%m%d")
    close = np.linspace(20, 32, len(dates))
    close[165:170] += np.array([1, 2, 4, 6, 3])
    qfq = pd.DataFrame({"trade_date": dates, "open": close - .2,
                         "high": close + .5, "low": close - .5,
                         "close": close, "vol": np.full(len(dates), 1000)})
    raw = qfq[["trade_date", "vol"]].copy()
    data = prepare_daily(qfq, raw)
    cutoff = dates[190]
    assert describe(data, cutoff) == describe(data.iloc[:191], cutoff)
    changed_future = data.copy()
    changed_future.loc[191:, ["open", "high", "low", "close", "raw_vol"]] = 1000000
    assert describe(changed_future, cutoff) == describe(data.iloc[:191], cutoff)


def test_volume_uses_unadjusted_series() -> None:
    dates = pd.bdate_range("2025-01-01", periods=5).strftime("%Y%m%d")
    qfq = pd.DataFrame({"trade_date": dates, "open": [1] * 5,
                         "high": [2] * 5, "low": [.5] * 5,
                         "close": [1] * 5, "vol": [100] * 5})
    raw = qfq[["trade_date", "vol"]].copy()
    raw.loc[4, "vol"] = 300
    out = prepare_daily(qfq, raw)
    assert out.iloc[4].raw_vol == 300
    assert out.iloc[4].vol == 100


def test_theme_evidence_counts_unique_stock_and_excludes_st() -> None:
    kpl = pd.DataFrame([
        {"trade_date": "20260914", "ts_code": "A", "name": "甲", "theme": "创新药,医药"},
        {"trade_date": "20260914", "ts_code": "A", "name": "甲", "theme": "创新药,医药"},
        {"trade_date": "20260914", "ts_code": "B", "name": "*ST乙", "theme": "创新药"},
        {"trade_date": "20260914", "ts_code": "C", "name": "丙", "theme": "非创新药"},
        {"trade_date": "20260915", "ts_code": "D", "name": "丁", "theme": "创新药"},
    ])
    result = theme_evidence(kpl, "20260914", "创新药")
    assert result["theme_limitups_today"] == 1
    assert result["theme_limitups_recent_max"] == 1
    assert result["theme_last_limitup_date"] == "20260914"
