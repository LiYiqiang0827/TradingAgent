from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from merge_ma60_ma120_top100_classifications import (
    apply_audit_corrections,
    classification_records_from_payload,
    merge_classification_records,
)


def test_classification_records_from_payload_accepts_wrapped_and_plain_lists() -> None:
    records = [{"ts_code": "A"}]
    assert classification_records_from_payload(records) == records
    assert classification_records_from_payload({"classifications": records}) == records


def test_merge_classification_records_reuses_existing_and_adds_review_batches() -> None:
    evidence = [
        {"observation_rank": 1, "ts_code": "A", "stock_name": "甲"},
        {"observation_rank": 2, "ts_code": "B", "stock_name": "乙"},
    ]
    existing = [
        {
            "ts_code": "A",
            "stock_name": "甲",
            "primary_theme": "芯片",
            "broad_category": "半导体/芯片",
            "secondary_themes": ["先进封装"],
            "confidence": "high",
            "evidence_note": "旧版逐票复核",
        }
    ]
    batches = [
        [
            {
                "observation_rank": 2,
                "ts_code": "B",
                "stock_name": "乙",
                "primary_theme": "银行",
                "broad_category": "金融",
                "secondary_themes": ["高股息"],
                "confidence": "medium",
                "rationale": "本地银行行业证据",
            }
        ]
    ]

    result = merge_classification_records(evidence, existing, batches)

    assert [item["ts_code"] for item in result] == ["A", "B"]
    assert result[0]["observation_rank"] == 1
    assert result[0]["rationale"] == "旧版逐票复核"
    assert result[1]["broad_category"] == "金融"


def test_merge_classification_records_rejects_duplicate_and_missing_codes() -> None:
    evidence = [
        {"observation_rank": 1, "ts_code": "A", "stock_name": "甲"},
        {"observation_rank": 2, "ts_code": "B", "stock_name": "乙"},
    ]
    duplicate = {
        "ts_code": "A", "stock_name": "甲", "primary_theme": "芯片",
        "broad_category": "半导体/芯片", "secondary_themes": [],
        "confidence": "high", "rationale": "证据",
    }
    with pytest.raises(ValueError, match="duplicate"):
        merge_classification_records(evidence, [duplicate], [[duplicate]])
    with pytest.raises(ValueError, match="coverage"):
        merge_classification_records(evidence, [duplicate], [])


def test_apply_audit_corrections_changes_only_review_fields() -> None:
    records = [
        {
            "observation_rank": 1,
            "ts_code": "A",
            "stock_name": "甲",
            "primary_theme": "旧题材",
            "broad_category": "其他科技电子",
            "secondary_themes": ["旧次题材"],
            "confidence": "high",
            "rationale": "初审",
        }
    ]
    corrections = {
        "A": {
            "primary_theme": "新题材",
            "broad_category": "消费",
            "secondary_themes": ["新次题材"],
            "confidence": "medium",
            "rationale": "独立复核后修订",
        }
    }

    result = apply_audit_corrections(records, corrections)

    assert result[0]["observation_rank"] == 1
    assert result[0]["stock_name"] == "甲"
    assert result[0]["primary_theme"] == "新题材"
    assert result[0]["broad_category"] == "消费"
    assert records[0]["primary_theme"] == "旧题材"
