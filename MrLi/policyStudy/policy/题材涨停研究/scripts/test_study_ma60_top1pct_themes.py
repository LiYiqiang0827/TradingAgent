from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_ma60_top1pct_themes import (
    aggregate_theme_counts,
    select_top_percent_stocks,
    split_theme_tokens,
)


def test_select_top_percent_stocks_uses_stock_max_and_includes_cutoff_ties() -> None:
    episodes = pd.DataFrame(
        [
            {"period": 60, "ts_code": "A", "target_days": 9, "length": 10, "end_date": "20260109"},
            {"period": 60, "ts_code": "A", "target_days": 5, "length": 20, "end_date": "20260110"},
            {"period": 60, "ts_code": "B", "target_days": 8, "length": 8, "end_date": "20260108"},
            {"period": 60, "ts_code": "C", "target_days": 7, "length": 7, "end_date": "20260107"},
            {"period": 60, "ts_code": "D", "target_days": 7, "length": 9, "end_date": "20260107"},
            {"period": 60, "ts_code": "E", "target_days": 6, "length": 6, "end_date": "20260106"},
            {"period": 60, "ts_code": "F", "target_days": 1, "length": 1, "end_date": "20260101"},
        ]
    )

    result, metadata = select_top_percent_stocks(
        episodes,
        period=60,
        valid_universe=6,
        top_pct=0.34,
    )

    assert metadata == {"nominal_top_n": 3, "cutoff_target_days": 7, "selected_with_ties": 4}
    assert result["ts_code"].tolist() == ["A", "B", "D", "C"]
    assert result["target_days"].tolist() == [9, 8, 7, 7]


def test_split_theme_tokens_handles_common_chinese_separators() -> None:
    assert split_theme_tokens("芯片、半导体设备,先进封装；AI硬件|通信") == [
        "芯片", "半导体设备", "先进封装", "AI硬件", "通信"
    ]
    assert split_theme_tokens(None) == []


def test_aggregate_theme_counts_splits_and_orders_by_frequency() -> None:
    rows = pd.DataFrame(
        {
            "ts_code": ["A", "A", "A", "B"],
            "theme": ["芯片、先进封装", "芯片", None, "机器人"],
        }
    )

    assert aggregate_theme_counts(rows, "A") == [
        {"theme": "芯片", "count": 2},
        {"theme": "先进封装", "count": 1},
    ]
