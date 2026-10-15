from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import study_jun_jul_2026_box_breakout_ppt as study
from study_jun_jul_2026_box_breakout_ppt import (
    assign_sensitivity_band,
    expected_deck_pages,
)


def test_assign_sensitivity_band_keeps_strict_and_relaxed_ranges_distinct() -> None:
    assert assign_sensitivity_band(14.9) == "严格主口径"
    assert assign_sensitivity_band(17.5) == "18%边缘候选"
    assert assign_sensitivity_band(18.1) == "20%宽松候选"


def test_zero_main_sensitivity_deck_has_six_fixed_pages_plus_stock_pages() -> None:
    assert expected_deck_pages(4) == 10


def test_prepare_tables_derives_counts_from_frozen_full_rescans(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(study, "OUTPUT", tmp_path)
    edge20 = pd.DataFrame(
        {
            "ts_code": ["A", "B", "C"],
            "event_date": ["20260601", "20260602", "20260603"],
            "similarity_score": [60.0, 70.0, 50.0],
            "box_range_pct": [17.0, 17.8, 19.4],
        }
    )
    edge18 = edge20.iloc[:2].copy()
    edge18.to_csv(tmp_path / "sensitivity_unique_box18.csv", index=False)
    edge20.to_csv(tmp_path / "sensitivity_unique_box20.csv", index=False)
    pd.DataFrame(
        {
            "stage": ["active", "signal", "new_high", "box15", "full15"],
            "unique_stocks": [5, 4, 3, 1, 0],
            "event_rows": [5, 4, 3, 1, 0],
        }
    ).to_csv(tmp_path / "strict_main_funnel_raw.csv", index=False)

    events, funnel, sensitivity = study._prepare_tables()

    assert len(events) == 3
    assert int(funnel.loc[funnel["stage_key"].eq("active"), "unique_stocks"].iloc[0]) == 5
    counts = sensitivity.set_index(["box_range_limit_pct", "box_min_trading_days"])["unique_stocks"]
    assert int(counts.loc[(18.0, 20)]) == 2
    assert int(counts.loc[(20.0, 20)]) == 3
