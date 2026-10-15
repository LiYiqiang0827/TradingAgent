from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from study_rising_ma_reactivation import _spread, describe, plot_template, prepare_daily, theme_evidence


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


def test_compactness_uses_ma5_10_20_and_preserves_scale() -> None:
    row = pd.Series({"ma5": 10., "ma10": 10.3, "ma20": 10.7, "ma30": 15.})
    assert abs(_spread(row, (5, 10, 20)) - 7) < 1e-10
    assert _spread(row) > 7
    assert abs(_spread(row * 3, (5, 10, 20)) - 7) < 1e-10


def test_peak_marker_accepts_csv_integer_dates_and_uses_display_price(tmp_path):
    from unittest.mock import patch
    from matplotlib.collections import PathCollection
    import matplotlib.pyplot as plt
    dates = pd.bdate_range("2025-01-01", periods=180).strftime("%Y%m%d")
    frame = pd.DataFrame({"trade_date": dates, "open": 10., "close": 10.5,
                          "high": 11., "low": 9., "raw_vol": 1000.})
    for n in (5, 10, 20, 30, 60, 120):
        frame[f"ma{n}"] = 10.2
    result = {"ts_code": "A", "trade_date": dates[-1], "peak_date": int(dates[-10]),
              "peak_high": 1100., "template_observation": "ma30_coil_restart"}
    with patch("matplotlib.pyplot.close"):
        plot_template(frame, result, tmp_path / "test.jpg")
        fig = plt.gcf()
        points = [c for c in fig.axes[0].collections if isinstance(c, PathCollection)]
        assert len(points) == 1
        assert float(points[0].get_offsets()[0, 1]) == 11.
    plt.close(fig)


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
