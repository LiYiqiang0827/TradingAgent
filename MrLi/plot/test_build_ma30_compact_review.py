"""Causal candidate reselection must happen before episode de-duplication."""
import pandas as pd

from plot.build_ma30_compact_review import collapse_compact_matches


def test_later_compact_signal_survives_an_earlier_noncompact_match():
    days = pd.bdate_range("2026-05-01", periods=20).strftime("%Y%m%d").tolist()
    rows = pd.DataFrame([
        {"ts_code": "A", "trade_date": days[0], "template_observation": "ma30_coil_restart",
         "ma5_20_spread_pct": 8.},
        {"ts_code": "A", "trade_date": days[2], "template_observation": "ma30_coil_restart",
         "ma5_20_spread_pct": 7.},
        {"ts_code": "A", "trade_date": days[5], "template_observation": "ma30_coil_restart",
         "ma5_20_spread_pct": 3.},
    ])
    assert collapse_compact_matches(rows, days).trade_date.tolist() == [days[2]]
