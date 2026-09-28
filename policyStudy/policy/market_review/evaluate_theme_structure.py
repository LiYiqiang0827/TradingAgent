"""用冻结的历史截面让 Qwen/MiniMax 盲审每日题材结构标签。"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

import duckdb


REPO_ROOT = Path(__file__).resolve().parents[3]
OFFLINE_SCRIPTS = REPO_ROOT / "offlineDataManager" / "scripts"
THEME_RESEARCH = REPO_ROOT / "policyStudy" / "policy" / "题材涨停研究" / "theme_graph"
for path in (REPO_ROOT, OFFLINE_SCRIPTS, THEME_RESEARCH):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from config.settings import THEME_GRAPH_DB_PATH  # noqa: E402
from core.theme_daily_review import build_market_theme_reviews  # noqa: E402
from model_runner import call_hermes, call_qwen, extract_json  # noqa: E402


DEFAULT_DATES = [
    "20260113", "20260324", "20260720",
    "20260326", "20260519", "20260710", "20260916",
    "20260126", "20260305", "20260602", "20260924",
    "20260313", "20260904", "20260202", "20260917",
]
ALLOWED_CODES = {
    "clear_single_mainline", "multiple_mainlines", "mainline_replacement",
    "scattered_no_mainline", "cold_no_mainline",
}


def _load_reviews(database: Path) -> dict[str, dict[str, Any]]:
    conn = duckdb.connect(str(database), read_only=True)
    try:
        daily = conn.execute(
            """SELECT d.*,t.canonical_name,COALESCE(x.level1_name,'待归类') AS level1_name
               FROM fact_theme_daily d JOIN dim_theme t USING(theme_id)
               LEFT JOIN dim_theme_taxonomy x USING(theme_id)
               WHERE EXISTS (SELECT 1 FROM rel_limit_theme r
                             WHERE r.theme_id=d.theme_id AND r.attribution_role='primary')
               ORDER BY d.trade_date,d.heat_score DESC"""
        ).df()
        events = conn.execute(
            """SELECT e.trade_date,e.ts_code,e.name,e.tag,e.status_raw,e.board_height,e.lu_time,
                      e.limit_order,e.lu_limit_order,e.bid_amount,e.amount,e.free_float,
                      r.theme_id,r.attribution_role
               FROM fact_limit_event e JOIN rel_limit_theme r USING(event_id)
               WHERE e.tag='涨停' AND r.attribution_role='primary'
               ORDER BY e.trade_date,e.ts_code"""
        ).df()
    finally:
        conn.close()
    return build_market_theme_reviews(daily, events, top_n=10, leader_count=3)


def _theme_row(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": value["rank"],
        "theme": value["theme"],
        "heat": value["heat_score"],
        "limit_up": value["limit_up_count"],
        "break": value["break_count"],
        "height": value["max_board_height"],
        "seal_rate": value["seal_rate"],
        "persistence_5": value["persistence_5"],
        "phase": value["lifecycle_state"],
        "previous_rank": value["previous_rank"],
        "leaders": [
            {
                "name": row["name"], "height": row["board_height"],
                "limit_days_20": row["limit_days_20"], "roles": row["roles"],
            }
            for row in value.get("leaders", [])
        ],
    }


def build_blind_packet(reviews: dict[str, dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    ordered_dates = sorted(reviews)
    positions = {value: index for index, value in enumerate(ordered_dates)}
    samples = []
    for trade_date in dates:
        review = reviews.get(trade_date)
        if not review:
            raise ValueError(f"题材库没有日期 {trade_date}")
        position = positions[trade_date]
        previous = reviews[ordered_dates[position - 1]] if position else None
        samples.append({
            "trade_date": trade_date,
            "theme_sentiment_score": review["theme_sentiment_score"],
            "theme_sentiment_level": review["theme_sentiment_level"],
            "top3_heat_composite": review["top3_heat_composite"],
            "current_top_themes": [_theme_row(row) for row in review["hot_themes"][:8]],
            "previous_trade_date": previous["trade_date"] if previous else None,
            "previous_top_themes": [_theme_row(row) for row in previous["hot_themes"][:8]] if previous else [],
        })
    return {
        "task": "blind_daily_theme_structure_review",
        "point_in_time": True,
        "future_data_included": False,
        "scope": "KPL primary-attribution limit-up stocks; ST sector, ST removal and recent listings excluded",
        "label_definitions": {
            "clear_single_mainline": "一个题材在宽度、梯队、封板质量和持续性上明显领先",
            "multiple_mainlines": "至少两个题材都具备可持续宽度和梯队，而非两个短暂热点",
            "mainline_replacement": "旧主线显著走弱，新题材以足够宽度和梯队承接并升至领先",
            "scattered_no_mainline": "存在热点，但集中度、持续性或梯队不足以确认主线",
            "cold_no_mainline": "题材整体低迷，没有有效主线",
        },
        "samples": samples,
    }


def _prompt(packet: dict[str, Any]) -> str:
    return """你是A股短线题材结构审计员。请只使用输入中每个交易日及前一交易日的数据，逐日独立判断；不得使用未来行情、新闻常识或输入之外的信息。重点区分“多个活跃热点”和“多个可持续主线”，也不要因单只孤立高标就确认主线。输出严格JSON对象，不要Markdown：
{
  "items": [
    {
      "trade_date": "YYYYMMDD",
      "structure_code": "五个允许枚举之一",
      "main_themes": ["题材名"],
      "reason": "用宽度、梯队、封板、持续性和前日变化说明",
      "confidence": 0.0,
      "uncertainty": "仍需什么证据，没有则空字符串"
    }
  ]
}
每个输入日期必须且只能返回一次，confidence在0到1之间。输入：
""" + json.dumps(packet, ensure_ascii=False, indent=2)


def _validate(value: dict[str, Any], dates: list[str]) -> None:
    items = value.get("items")
    if not isinstance(items, list):
        raise ValueError("模型输出缺少 items 数组")
    returned = [str(item.get("trade_date")) for item in items]
    if sorted(returned) != sorted(dates) or len(returned) != len(set(returned)):
        raise ValueError(f"模型日期覆盖不完整: {returned}")
    for item in items:
        if item.get("structure_code") not in ALLOWED_CODES:
            raise ValueError(f"非法 structure_code: {item.get('structure_code')}")
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("confidence 必须在0到1之间")


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blind-audit deterministic daily theme structure labels")
    parser.add_argument("--database", type=Path, default=Path(THEME_GRAPH_DB_PATH))
    parser.add_argument("--dates", nargs="*", default=DEFAULT_DATES)
    parser.add_argument("--providers", choices=["qwen-vllm", "minimax-hermes", "both"], default="both")
    parser.add_argument("--chunk-size", type=int, default=8,
                        help="每次模型调用的日期数；长批次容易截断，默认8")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    dates = [str(value).replace("-", "") for value in args.dates]
    reviews = _load_reviews(args.database.expanduser())
    packet = build_blind_packet(reviews, dates)
    output_dir = args.output_dir or (
        REPO_ROOT / "outputs" / "theme_daily_review_validation" /
        datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write(output_dir / "blind_packet.json", packet)
    providers = ["qwen-vllm", "minimax-hermes"] if args.providers == "both" else [args.providers]
    judgments: dict[str, dict[str, Any]] = {}
    for provider in providers:
        items: list[dict[str, Any]] = []
        models: list[str] = []
        chunk_size = max(1, int(args.chunk_size))
        for part_number, offset in enumerate(range(0, len(dates), chunk_size), start=1):
            part_dates = dates[offset:offset + chunk_size]
            part_packet = build_blind_packet(reviews, part_dates)
            part_prompt = _prompt(part_packet)
            if provider == "qwen-vllm":
                raw, model = call_qwen(part_prompt, timeout=300, max_tokens=6000)
            else:
                raw, model = call_hermes(part_prompt, timeout=360)
            (output_dir / f"{provider}_part{part_number}_raw.txt").write_text(
                raw + "\n", encoding="utf-8"
            )
            parsed = extract_json(raw)
            _validate(parsed, part_dates)
            items.extend(parsed["items"])
            models.append(model)
        parsed = {"items": items}
        _validate(parsed, dates)
        judgments[provider] = {"model": ",".join(dict.fromkeys(models)), **parsed}
        _write(output_dir / f"{provider}.json", judgments[provider])

    comparison = []
    indexed = {
        provider: {item["trade_date"]: item for item in value["items"]}
        for provider, value in judgments.items()
    }
    for trade_date in dates:
        row = {
            "trade_date": trade_date,
            "program": reviews[trade_date]["structure"]["code"],
            "program_summary": reviews[trade_date]["structure"]["summary"],
        }
        for provider in providers:
            row[provider] = indexed[provider][trade_date]
        comparison.append(row)
    _write(output_dir / "comparison.json", comparison)
    print(json.dumps({"status": "PASS", "samples": len(dates), "providers": providers,
                      "output_dir": str(output_dir.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
