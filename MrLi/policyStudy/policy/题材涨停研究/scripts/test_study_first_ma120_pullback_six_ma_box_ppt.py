from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_first_ma120_pullback_six_ma_box_ppt import (
    add_ma120_pullback_features,
    box_short_ma_position_metrics,
    build_sensitivity,
    find_narrow_box,
    minimum_six_ma_spread,
    select_first_qualifying_touch,
    select_first_touch,
    select_unique_first_touches,
    write_report,
)


def _base_feature_frame(rows: int = 25) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=rows).strftime("%Y%m%d")
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": [100.0] * rows,
            "high": [102.0] * rows,
            "low": [99.0] * rows,
            "close": [100.0] * rows,
            "ma5": [101.0] * rows,
            "ma10": [100.8] * rows,
            "ma20": [100.6] * rows,
            "ma30": [100.4] * rows,
            "ma60": [100.2] * rows,
            "ma120": [100.0] * rows,
            "ma120_rising_streak": list(range(1, rows + 1)),
        }
    )


def test_ma120_touch_uses_prior_extension_and_current_price_only() -> None:
    frame = _base_feature_frame()
    frame.loc[23, "close"] = 109.0
    frame.loc[24, ["low", "close"]] = [101.5, 98.0]

    result = add_ma120_pullback_features(
        frame,
        extension_lookback=20,
        prior_extension_pct=8.0,
        touch_upper_pct=2.0,
        close_floor_pct=-3.0,
        cohesion_threshold_pct=6.0,
        max_short_below_ma120_pct=-2.0,
        min_short_above_count=4,
    )

    assert result.iloc[-1]["prior_max_close_vs_ma120_pct"] == pytest.approx(9.0)
    assert bool(result.iloc[-1]["ma120_pullback_candidate"])
    assert bool(result.iloc[-1]["six_ma_cohesion_position_pass"])


def test_first_touch_is_fixed_before_cohesion_filter() -> None:
    frame = _base_feature_frame(rows=4)
    frame["ma120_pullback_candidate"] = [False, True, True, True]
    frame["six_ma_cohesion_position_pass"] = [False, False, True, True]

    position = select_first_touch(
        frame,
        anchor_start_date=frame.iloc[0]["trade_date"],
        anchor_end_date=frame.iloc[-1]["trade_date"],
    )

    assert position == 1
    assert not bool(frame.iloc[position]["six_ma_cohesion_position_pass"])


def test_first_qualifying_touch_skips_an_earlier_uncohesive_touch() -> None:
    frame = _base_feature_frame(rows=4)
    frame["ma120_pullback_candidate"] = [False, True, True, True]
    frame["six_ma_cohesion_position_pass"] = [False, False, True, True]

    position = select_first_qualifying_touch(
        frame,
        anchor_start_date=frame.iloc[0]["trade_date"],
        anchor_end_date=frame.iloc[-1]["trade_date"],
    )

    assert position == 2


def test_six_ma_position_requires_four_above_and_none_below_two_percent() -> None:
    frame = _base_feature_frame(rows=2)
    frame.loc[1, ["ma5", "ma10", "ma20", "ma30", "ma60"]] = [101.0, 100.8, 100.6, 100.4, 97.9]
    result = add_ma120_pullback_features(
        frame,
        extension_lookback=1,
        prior_extension_pct=0.0,
        touch_upper_pct=2.0,
        close_floor_pct=-3.0,
        cohesion_threshold_pct=6.0,
        max_short_below_ma120_pct=-2.0,
        min_short_above_count=4,
    )

    assert result.iloc[1]["short_ma_above_ma120_count"] == 4
    assert result.iloc[1]["min_short_ma_vs_ma120_pct"] == pytest.approx(-2.1)
    assert not bool(result.iloc[1]["six_ma_cohesion_position_pass"])


def test_narrow_box_maximizes_contiguous_days_containing_touch() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": pd.bdate_range("2026-01-01", periods=9).strftime("%Y%m%d"),
            "high": [120, 105, 104, 103, 104, 105, 104, 106, 120],
            "low": [100, 100, 99, 100, 99, 100, 100, 101, 100],
            "close": [110, 102, 101, 101, 102, 103, 102, 103, 110],
        }
    )

    box = find_narrow_box(
        frame,
        touch_position=4,
        anchor_start_position=0,
        anchor_end_position=8,
        lookback=4,
        lookforward=4,
        max_range_pct=8.0,
        min_days=5,
    )

    assert box["box_start_position"] == 1
    assert box["box_end_position"] == 7
    assert box["box_trading_days"] == 7
    assert box["box_range_pct"] == pytest.approx(100.0 * (106.0 / 99.0 - 1.0))
    assert box["narrow_box_pass"]


def test_minimum_six_ma_spread_is_selected_inside_box() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["20260101", "20260102", "20260105"],
            "six_ma_spread_pct": [4.0, 1.2, 2.0],
        }
    )

    result = minimum_six_ma_spread(frame, start_position=0, end_position=2)

    assert result["minimum_six_ma_spread_pct"] == pytest.approx(1.2)
    assert result["minimum_six_ma_spread_date"] == "20260102"


def test_report_discloses_every_hard_filter(tmp_path: Path) -> None:
    event = {
        "stock_name": "样本",
        "ts_code": "000001.SZ",
        "touch_date": "20260105",
        "ma120_rising_streak_at_touch": 3,
        "box_trading_days": 5,
        "box_range_pct": 9.0,
        "six_ma_spread_at_touch_pct": 5.0,
        "minimum_six_ma_spread_pct": 4.0,
        "box_short_ma_observations_above_ma120_ratio": 0.9,
        "box_short_ma_observations_within_floor_ratio": 1.0,
    }
    result = {
        "all_first": pd.DataFrame([event]),
        "anchor_events": pd.DataFrame([event]),
        "unique_events": pd.DataFrame([event]),
        "unique_box_events": pd.DataFrame([event]),
        "summary": pd.DataFrame(
            [
                {
                    "ma120_streak_median": 3.0,
                    "ma120_streak_p25": 3.0,
                    "ma120_streak_p75": 3.0,
                    "box_days_median": 5.0,
                    "box_days_p25": 5.0,
                    "box_days_p75": 5.0,
                    "minimum_six_ma_spread_median": 4.0,
                    "minimum_six_ma_spread_min": 4.0,
                }
            ]
        ),
    }
    output = tmp_path / "report.md"

    write_report(result, pd.DataFrame(), output)

    text = output.read_text(encoding="utf-8")
    for required in (
        "排除左截断",
        "完整历史前复权close",
        "前20个交易日",
        "8%",
        "最低价",
        "2%",
        "收盘价",
        "-3%",
        "MA120连续上涨",
        "持平、下降或缺失均清零",
        "anchor_start_date",
        "最大—最小离差",
        "6%",
        "至少4条",
        "最多低于MA120 2%",
        "不允许跳到后续",
        "事件前最多10日",
        "事件后最多30日",
        "最高价/最低价",
        "10%",
        "至少5个交易日",
    ):
        assert required in text


def test_sensitivity_covers_pullback_and_position_thresholds() -> None:
    args = argparse.Namespace(
        min_short_above_count=4,
        max_short_below_ma120_pct=-2.0,
        box_lookback=10,
        box_lookforward=30,
        box_min_days=5,
        prior_extension_pct=8.0,
        touch_upper_pct=2.0,
        close_floor_pct=-3.0,
    )
    first = pd.DataFrame(
        [
            {
                "six_ma_spread_at_touch_pct": 5.0,
                "short_ma_above_ma120_count": 4,
                "min_short_ma_vs_ma120_pct": -1.0,
                "narrow_box_pass": True,
            }
        ]
    )
    result = {
        "unique_first": first,
        "unique_events": pd.DataFrame(),
        "features_by_code": {},
    }

    def scan_fn(variant: argparse.Namespace) -> dict[str, pd.DataFrame]:
        return {
            "unique_events": pd.DataFrame([{"x": variant.prior_extension_pct}]),
            "unique_box_events": pd.DataFrame([{"x": variant.touch_upper_pct}]),
        }

    sensitivity = build_sensitivity(result, args, scan_fn=scan_fn)

    types = set(sensitivity["sensitivity_type"])
    assert {
        "prior_extension_threshold",
        "touch_upper_threshold",
        "close_floor_threshold",
        "position_floor_threshold",
        "min_short_above_count",
    }.issubset(types)
    assert "narrow_box_count" in sensitivity.columns
    assert "box_range_threshold_pct" in sensitivity.columns
    assert "narrow_box_count_at_10pct" not in sensitivity.columns


def test_box_short_ma_position_metrics_measure_mainly_above_and_slightly_below() -> None:
    frame = pd.DataFrame(
        {
            "ma5": [101.0, 101.0],
            "ma10": [101.0, 101.0],
            "ma20": [101.0, 101.0],
            "ma30": [101.0, 101.0],
            "ma60": [101.0, 98.5],
            "ma120": [100.0, 100.0],
        }
    )

    metrics = box_short_ma_position_metrics(frame, start_position=0, end_position=1, floor_pct=-2.0)

    assert metrics["box_short_ma_observations_above_ma120_ratio"] == pytest.approx(0.9)
    assert metrics["box_short_ma_observations_within_floor_ratio"] == pytest.approx(1.0)
    assert metrics["box_days_all_short_ma_within_floor_ratio"] == pytest.approx(1.0)
    assert metrics["box_min_short_ma_vs_ma120_pct"] == pytest.approx(-1.5)


def test_unique_first_touch_uses_anchor_start_as_final_same_day_tie_breaker() -> None:
    events = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "touch_date": "20260110", "anchor_period": 60, "anchor_start_date": "20260105", "marker": "later"},
            {"ts_code": "000001.SZ", "touch_date": "20260110", "anchor_period": 60, "anchor_start_date": "20260101", "marker": "earlier"},
            {"ts_code": "000002.SZ", "touch_date": "20260111", "anchor_period": 120, "anchor_start_date": "20260102", "marker": "other"},
        ]
    )

    selected = select_unique_first_touches(events)

    assert selected.loc[selected["ts_code"].eq("000001.SZ"), "marker"].item() == "earlier"
