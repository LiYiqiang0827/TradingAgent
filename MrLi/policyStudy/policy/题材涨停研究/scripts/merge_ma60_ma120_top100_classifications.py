"""Merge reused 58-stock reviews with new MA60/MA120 classification batches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from build_ma60_top1pct_kline_ppt import ALLOWED_BROAD_CATEGORIES


AUDIT_CORRECTIONS: dict[str, dict[str, Any]] = {
    "301273.SZ": {
        "primary_theme": "基础建设",
        "broad_category": "地产/基建",
        "secondary_themes": ["建筑节能", "稀土永磁"],
        "confidence": "medium",
        "rationale": "独立复核：KPL中基础建设与建筑节能各1次，limit performance仅主归基础建设1次；稀土永磁长期概念来自控股子公司永磁电机，行业为机械基件，单次题材证据不支持高置信度。",
    },
    "301115.SZ": {
        "primary_theme": "机器人概念",
        "broad_category": "机器人/高端装备",
        "secondary_themes": ["地产链", "房屋养老金概念"],
        "confidence": "medium",
        "rationale": "独立复核：机器人概念仅见1次KPL及同一涨停记录，长期概念与建筑工程行业主要支持房建、市政、水利检测；保留交易主归，但因缺少独立业务证据降为中置信度。",
    },
    "301086.SZ": {
        "primary_theme": "消费电子散热材料",
        "broad_category": "其他科技电子",
        "secondary_themes": ["液冷服务器", "AI手机"],
        "confidence": "medium",
        "rationale": "独立复核：长期概念描述直接支持消费电子导热、散热、电磁屏蔽及液冷板等电子硬件部件，行业为元器件；未见高速铜缆互联业务的直接证据，故删除该次题材。",
    },
    "688378.SH": {
        "primary_theme": "OLED/面板材料与蒸发设备",
        "broad_category": "其他科技电子",
        "secondary_themes": ["半导体材料", "玻璃基板", "MR/硅基OLED"],
        "confidence": "medium",
        "rationale": "独立复核：KPL有芯片2次，但长期证据集中于OLED发光与封装材料、PSPI、硅基OLED及面板蒸发设备，相关材料导入对象为面板厂；未形成IC封装业务的直接证据。",
    },
    "603608.SH": {
        "primary_theme": "服装家纺",
        "broad_category": "消费",
        "secondary_themes": ["机器人/滚珠丝杠（控制权变更预期）", "实控人变更", "并购重组"],
        "confidence": "medium",
        "rationale": "独立复核：KPL服装家纺7次且行业为服饰；机器人与滚珠丝杠记录来自受让方实控人另持企业形成的转型预期，未证明上市公司已开展相关业务，故降为事件型次题材。",
    },
    "603682.SH": {
        "primary_theme": "物业服务/城市更新",
        "broad_category": "地产/基建",
        "secondary_themes": ["首发经济", "新型城镇化", "小红书概念"],
        "confidence": "high",
        "rationale": "独立复核：KPL物业服务7次、limit performance地产链6次，长期概念及房产服务行业均明确支持老旧及低效商用物业改造、招商与运营；首发经济8次保留为次题材。",
    },
    "603663.SH": {
        "primary_theme": "锆材料（氧化锆/金属锆）",
        "broad_category": "有色金属/稀土/资源",
        "secondary_themes": ["芯片", "固态电池材料", "稀土永磁"],
        "confidence": "medium",
        "rationale": "独立复核：长期概念直接支持锆基液态金属、含锆固态电解质和海绵锆，limit performance也未以稀土永磁为主；稀土永磁虽有9次KPL，但缺少业务描述补强，故改主归锆材料。",
    },
}


def classification_records_from_payload(payload: Any) -> list[dict[str, Any]]:
    """Normalize either a plain list or a wrapped classifications payload."""
    records = payload.get("classifications") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("classification payload must be a list or contain classifications")
    return list(records)


def apply_audit_corrections(
    records: Sequence[dict[str, Any]],
    corrections: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply independently reviewed fields without altering stock identity/rank."""
    codes = {str(item["ts_code"]) for item in records}
    unknown = sorted(set(corrections) - codes)
    if unknown:
        raise ValueError(f"audit corrections reference unknown codes: {unknown}")
    output: list[dict[str, Any]] = []
    for item in records:
        record = dict(item)
        correction = corrections.get(str(item["ts_code"]))
        if correction:
            record.update(correction)
        category = str(record.get("broad_category", ""))
        if category not in ALLOWED_BROAD_CATEGORIES:
            raise ValueError(f"invalid corrected broad category: {category}")
        confidence = str(record.get("confidence", ""))
        if confidence not in {"high", "medium", "low"}:
            raise ValueError(f"invalid corrected confidence: {confidence}")
        record["secondary_themes"] = list(record.get("secondary_themes", []))[:3]
        if not str(record.get("rationale", "")).strip():
            raise ValueError(f"empty corrected rationale for {record['ts_code']}")
        output.append(record)
    return sorted(output, key=lambda item: int(item["observation_rank"]))


def merge_classification_records(
    evidence: Sequence[dict[str, Any]],
    existing: Sequence[dict[str, Any]],
    batches: Sequence[Sequence[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Merge classifications, reject duplicates, and validate exact union coverage."""
    evidence_by_code = {str(item["ts_code"]): item for item in evidence}
    records = list(existing)
    for batch in batches:
        records.extend(batch)
    codes = [str(item["ts_code"]) for item in records]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        raise ValueError(f"duplicate classification codes: {duplicates}")
    actual = set(codes)
    expected = set(evidence_by_code)
    if actual != expected:
        raise ValueError(
            "classification coverage mismatch: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )

    output: list[dict[str, Any]] = []
    for item in records:
        code = str(item["ts_code"])
        source = evidence_by_code[code]
        source_name = str(source.get("stock_name", ""))
        record_name = str(item.get("stock_name", source_name))
        if source_name and record_name and source_name != record_name:
            raise ValueError(
                f"stock name mismatch for {code}: evidence={source_name}, review={record_name}"
            )
        category = str(item.get("broad_category", ""))
        if category not in ALLOWED_BROAD_CATEGORIES:
            raise ValueError(f"invalid broad category for {code}: {category}")
        confidence = str(item.get("confidence", ""))
        if confidence not in {"high", "medium", "low"}:
            raise ValueError(f"invalid confidence for {code}: {confidence}")
        primary = str(item.get("primary_theme", "")).strip()
        if not primary:
            raise ValueError(f"empty primary_theme for {code}")
        secondaries = list(item.get("secondary_themes", []))[:3]
        rationale = str(item.get("rationale", item.get("evidence_note", ""))).strip()
        if not rationale:
            raise ValueError(f"empty rationale for {code}")
        output.append(
            {
                "observation_rank": int(source["observation_rank"]),
                "ts_code": code,
                "stock_name": source_name or record_name,
                "primary_theme": primary,
                "broad_category": category,
                "secondary_themes": secondaries,
                "confidence": confidence,
                "rationale": rationale,
            }
        )
    return sorted(output, key=lambda item: int(item["observation_rank"]))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=Path("outputs/ma60_ma120_top100_themes/2026_through_20260930"),
    )
    parser.add_argument(
        "--existing",
        type=Path,
        default=Path("outputs/ma60_top1pct_ppt/2026_through_20260930/classifications.json"),
    )
    parser.add_argument("--batch-glob", default="classification_batches/output_batch_*.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_themes/2026_through_20260930/"
            "classifications_reviewed.json"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    evidence = json.loads(
        (args.analysis_dir / "theme_evidence_summary.json").read_text(encoding="utf-8")
    )
    existing_payload = json.loads(args.existing.read_text(encoding="utf-8"))
    existing_all = classification_records_from_payload(existing_payload)
    evidence_codes = {str(item["ts_code"]) for item in evidence}
    existing = [
        item for item in existing_all if str(item.get("ts_code")) in evidence_codes
    ]
    batch_paths = sorted(args.analysis_dir.glob(args.batch_glob))
    if not batch_paths:
        raise SystemExit(f"no classification batches matched {args.batch_glob}")
    batches: list[list[dict[str, Any]]] = []
    for path in batch_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        batches.append(classification_records_from_payload(payload))
    merged = merge_classification_records(evidence, existing, batches)
    active_corrections = {
        code: correction
        for code, correction in AUDIT_CORRECTIONS.items()
        if code in evidence_codes
    }
    merged = apply_audit_corrections(merged, active_corrections)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"classifications": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "existing": len(existing),
                "new": sum(len(batch) for batch in batches),
                "total": len(merged),
                "batches": len(batches),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
