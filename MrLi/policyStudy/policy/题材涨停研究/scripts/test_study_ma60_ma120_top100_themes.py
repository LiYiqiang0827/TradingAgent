from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_ma60_ma120_top100_themes import (
    augment_evidence_with_membership,
    build_evidence_input,
    build_observation_union,
    select_top_n_with_ties,
)


def test_select_top_n_with_ties_uses_best_stock_episode_and_keeps_cutoff_ties() -> None:
    episodes = pd.DataFrame(
        [
            {"period": 60, "ts_code": "A", "target_days": 10, "length": 11, "end_date": "20260110"},
            {"period": 60, "ts_code": "A", "target_days": 5, "length": 30, "end_date": "20260111"},
            {"period": 60, "ts_code": "B", "target_days": 9, "length": 9, "end_date": "20260109"},
            {"period": 60, "ts_code": "C", "target_days": 8, "length": 8, "end_date": "20260108"},
            {"period": 60, "ts_code": "D", "target_days": 8, "length": 12, "end_date": "20260108"},
            {"period": 60, "ts_code": "E", "target_days": 7, "length": 7, "end_date": "20260107"},
        ]
    )

    selected, metadata = select_top_n_with_ties(episodes, period=60, top_n=3)

    assert selected["ts_code"].tolist() == ["A", "B", "D", "C"]
    assert selected["target_days"].tolist() == [10, 9, 8, 8]
    assert metadata == {
        "requested_top_n": 3,
        "cutoff_target_days": 8,
        "cutoff_tie_count": 2,
        "selected_with_ties": 4,
    }


def test_build_observation_union_marks_membership_and_preserves_period_ranks() -> None:
    ma60 = pd.DataFrame(
        [
            {"rank": 1, "ts_code": "A", "period": 60, "start_date": "20260101", "end_date": "20260110", "length": 10, "target_days": 10, "left_censored": False, "carried_in": False, "right_censored": False},
            {"rank": 2, "ts_code": "B", "period": 60, "start_date": "20260102", "end_date": "20260109", "length": 8, "target_days": 8, "left_censored": False, "carried_in": False, "right_censored": False},
        ]
    )
    ma120 = pd.DataFrame(
        [
            {"rank": 1, "ts_code": "B", "period": 120, "start_date": "20251201", "end_date": "20260109", "length": 28, "target_days": 8, "left_censored": False, "carried_in": True, "right_censored": False},
            {"rank": 2, "ts_code": "C", "period": 120, "start_date": "20251101", "end_date": "20260108", "length": 48, "target_days": 7, "left_censored": False, "carried_in": True, "right_censored": False},
        ]
    )

    union = build_observation_union(ma60, ma120)

    assert union["ts_code"].tolist() == ["A", "B", "C"]
    assert union.set_index("ts_code")["membership"].to_dict() == {
        "A": "MA60",
        "B": "MA60+MA120",
        "C": "MA120",
    }
    indexed = union.set_index("ts_code")
    assert indexed.loc["A", "ma60_rank"] == 1
    assert pd.isna(indexed.loc["A", "ma120_rank"])
    assert indexed.loc["B", "ma60_rank"] == 2
    assert indexed.loc["B", "ma120_rank"] == 1
    assert indexed.loc["C", "observation_rank"] == 3


def test_evidence_input_and_membership_augmentation_use_union_fields() -> None:
    ma60 = pd.DataFrame(
        [{"rank": 1, "ts_code": "A", "period": 60, "start_date": "20260101", "end_date": "20260110", "length": 10, "target_days": 10, "left_censored": False, "carried_in": False, "right_censored": False}]
    )
    ma120 = pd.DataFrame(
        [{"rank": 1, "ts_code": "B", "period": 120, "start_date": "20251201", "end_date": "20260930", "length": 220, "target_days": 181, "left_censored": False, "carried_in": True, "right_censored": True}]
    )
    union = build_observation_union(ma60, ma120)

    evidence_input = build_evidence_input(union)
    indexed = evidence_input.set_index("ts_code")
    assert indexed.loc["A", "period"] == 60
    assert indexed.loc["A", "target_days"] == 10
    assert indexed.loc["B", "period"] == 120
    assert indexed.loc["B", "carried_in"]

    evidence = [
        {"rank": 1, "ts_code": "A", "stock_name": "甲", "industry": "行业甲"},
        {"rank": 2, "ts_code": "B", "stock_name": "乙", "industry": "行业乙"},
    ]
    augmented = augment_evidence_with_membership(evidence, union)
    by_code = {item["ts_code"]: item for item in augmented}
    assert by_code["A"]["membership"] == "MA60"
    assert by_code["A"]["ma60_rank"] == 1
    assert by_code["A"]["ma120_rank"] is None
    assert by_code["B"]["membership"] == "MA120"
    assert by_code["B"]["ma120_target_days"] == 181
