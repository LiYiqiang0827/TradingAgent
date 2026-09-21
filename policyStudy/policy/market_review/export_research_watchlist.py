from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_STAGES = {
    "restart_completed",
    "continuous_board",
    "initial_launch",
}


def board_status(card: dict[str, Any]) -> str:
    kind = str(card.get("board_kind") or "")
    days = card.get("board_days")
    count = card.get("board_count")
    try:
        days_int = int(float(days)) if days is not None else None
        count_int = int(float(count)) if count is not None else None
    except (TypeError, ValueError):
        days_int = count_int = None
    if kind == "n_day_m_board" and days_int and count_int:
        return f"{days_int}天{count_int}板"
    if kind == "continuous" and count_int:
        return "首板" if count_int == 1 else f"{count_int}连板"
    if kind == "pullback_watch":
        return "首板后回调观察"
    return str(card.get("status_raw") or "")


def export_rows(
    packet: dict[str, Any],
    stages: set[str] | None = None,
    include_pullbacks: bool = False,
) -> pd.DataFrame:
    selected = set(stages or DEFAULT_STAGES)
    if include_pullbacks:
        selected.add("pullback_in_progress")
    review_date = str(packet.get("metadata", {}).get("trade_date") or "")
    rows = []
    for card in packet.get("candidate_cards", []):
        stage = str(card.get("pattern_stage") or "")
        if stage not in selected:
            continue
        anchor_date = str(card.get("launch_date") or review_date)
        themes = card.get("themes") or []
        primary_theme = str(card.get("primary_theme") or (themes[0] if themes else ""))
        rows.append({
            "trade_date": anchor_date,
            "ts_code": str(card.get("ts_code") or ""),
            "name": str(card.get("name") or ""),
            "lu_time": str(card.get("first_limit_time") or ""),
            "lu_desc": primary_theme,
            "status": board_status(card),
            "review_date": review_date,
            "review_stage": stage,
            "pattern_shape": str(card.get("pattern_shape") or ""),
        })
    columns = [
        "trade_date", "ts_code", "name", "lu_time", "lu_desc", "status",
        "review_date", "review_stage", "pattern_shape",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return (
        pd.DataFrame(rows, columns=columns)
        .drop_duplicates(["trade_date", "ts_code", "review_stage"], keep="first")
        .sort_values(["trade_date", "review_stage", "ts_code"])
        .reset_index(drop=True)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export market-review candidates as a 题材涨停研究-compatible watchlist"
    )
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--stages",
        nargs="*",
        choices=[
            "pullback_in_progress", "restart_completed", "continuous_board",
            "initial_launch", "failed_touch",
        ],
        help="Candidate stages to export; defaults to completed restart, continuous board and initial launch",
    )
    parser.add_argument(
        "--include-pullbacks",
        action="store_true",
        help="Also export pending pullbacks, anchored on their original launch date",
    )
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    frame = export_rows(
        packet,
        stages=set(args.stages) if args.stages else None,
        include_pullbacks=args.include_pullbacks,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(json.dumps({
        "status": "PASS",
        "rows": int(len(frame)),
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
