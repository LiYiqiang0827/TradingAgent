#!/usr/bin/env python3
"""Evaluate whether the dynamic path engine avoids or exits non-upside paths early."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


UPSIDE_PATHS = {"P1", "P2"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, required=True)
    parser.add_argument("--dynamic-score", type=Path, required=True)
    parser.add_argument("--dynamic-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path)
    return parser.parse_args()


def first_step(timeline: list[dict[str, Any]], states: set[str], start: int = 1) -> dict[str, Any] | None:
    for item in timeline:
        if item["step"] >= start and item["long_only_risk_posture"]["state"] in states:
            return item
    return None


def mean_bool(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [bool(row[key]) for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else None


def median_number(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return float(pd.Series(values).median()) if values else None


def main() -> None:
    args = parse_args()
    score = json.loads(args.score.read_text(encoding="utf-8"))
    dynamic_score = json.loads(args.dynamic_score.read_text(encoding="utf-8"))
    score_by_code = {item["ts_code"]: item for item in score["results"]}
    rows: list[dict[str, Any]] = []
    for summary in dynamic_score["results"]:
        result = score_by_code[summary["ts_code"]]
        dynamic = json.loads((args.dynamic_dir / summary["dynamic_output"]).read_text(encoding="utf-8"))
        timeline = dynamic["timeline"]
        realized = summary["realized_path"]
        is_upside = realized in UPSIDE_PATHS
        trigger_step = int(summary["trigger_step"])
        confirmation = first_step(timeline, {"upside_confirmed"})
        confirmation_step = None if confirmation is None else int(confirmation["step"])
        confirmation_date = None if confirmation is None else confirmation["trade_date"]
        false_upside_before_trigger = confirmation_step is not None and confirmation_step < trigger_step and not is_upside
        avoided_non_upside_entry = (not is_upside) and not false_upside_before_trigger

        post_start = confirmation_step or 1
        reduce_item = first_step(timeline, {"reduce_risk"}, start=post_start)
        exit_item = first_step(timeline, {"exit_risk"}, start=post_start)
        failure_date = result["events"].get("breakout_failure_date")
        invalid_date = result["events"].get("post_breakout_invalidation_date") or result["events"].get("invalidation_close_date")
        objective_adverse_dates = [date for date in (failure_date, invalid_date) if date]
        adverse_date = min(objective_adverse_dates) if objective_adverse_dates else None
        exit_date = None if exit_item is None else exit_item["trade_date"]
        adverse_after_upside_confirmation = bool(
            is_upside
            and confirmation_date is not None
            and adverse_date is not None
            and confirmation_date <= adverse_date
        )
        timely_exit = None if not adverse_after_upside_confirmation else exit_date is not None and exit_date <= adverse_date

        future = pd.DataFrame(result["future_bars"])
        for column in ("close", "low"):
            future[column] = pd.to_numeric(future[column], errors="coerce")
        potential_avoided_drawdown = None
        recovery_5d = None
        if exit_item is not None:
            exit_index = int(exit_item["step"]) - 1
            exit_close = float(future.iloc[exit_index]["close"])
            later = future.iloc[exit_index:]
            potential_avoided_drawdown = round((1 - float(later["low"].min()) / exit_close) * 100, 6)
            next_five = future.iloc[exit_index + 1 : exit_index + 6]
            upper = float(dynamic["boundaries"]["upper"])
            recovery_5d = bool((next_five["close"] > upper).any()) if not next_five.empty else False

        rows.append(
            {
                "ts_code": summary["ts_code"],
                "stock_name": summary.get("stock_name"),
                "as_of": summary["as_of"],
                "realized_path": realized,
                "is_upside_origin_path": is_upside,
                "trigger_step": trigger_step,
                "upside_confirmation_step": confirmation_step,
                "upside_confirmation_date": confirmation_date,
                "upside_confirmed_by_trigger": is_upside and confirmation_step is not None and confirmation_step <= trigger_step,
                "false_upside_before_non_upside_trigger": false_upside_before_trigger,
                "avoided_non_upside_entry_before_trigger": avoided_non_upside_entry,
                "first_reduce_step": None if reduce_item is None else reduce_item["step"],
                "first_exit_step": None if exit_item is None else exit_item["step"],
                "first_exit_date": exit_date,
                "objective_adverse_date": adverse_date,
                "adverse_after_upside_confirmation": adverse_after_upside_confirmation,
                "exit_warning_timely": timely_exit,
                "potential_additional_drawdown_after_exit_pct": potential_avoided_drawdown,
                "recovered_above_u1_within_5d_after_exit": recovery_5d,
                "risk_state_t1": timeline[0]["long_only_risk_posture"]["state"],
                "risk_state_t3": timeline[min(2, len(timeline) - 1)]["long_only_risk_posture"]["state"],
                "risk_state_t5": timeline[min(4, len(timeline) - 1)]["long_only_risk_posture"]["state"],
                "risk_state_t20": timeline[min(19, len(timeline) - 1)]["long_only_risk_posture"]["state"],
            }
        )

    upside = [row for row in rows if row["is_upside_origin_path"]]
    non_upside = [row for row in rows if not row["is_upside_origin_path"]]
    adverse = [row for row in rows if row["adverse_after_upside_confirmation"]]
    exited = [row for row in upside if row["first_exit_step"] is not None]
    aggregate = {
        "samples": len(rows),
        "upside_origin_samples": len(upside),
        "non_upside_origin_samples": len(non_upside),
        "upside_confirmation_by_trigger_rate": mean_bool(upside, "upside_confirmed_by_trigger"),
        "median_upside_confirmation_step": median_number(upside, "upside_confirmation_step"),
        "non_upside_entry_avoidance_rate": mean_bool(non_upside, "avoided_non_upside_entry_before_trigger"),
        "false_upside_before_non_upside_trigger_rate": mean_bool(non_upside, "false_upside_before_non_upside_trigger"),
        "adverse_events_after_upside_confirmation": len(adverse),
        "timely_exit_warning_rate_after_upside_confirmation": mean_bool(adverse, "exit_warning_timely"),
        "exit_signals_after_upside_confirmation": len(exited),
        "median_potential_additional_drawdown_after_exit_pct": median_number(exited, "potential_additional_drawdown_after_exit_pct"),
        "five_day_recovery_above_u1_after_exit_rate": mean_bool(exited, "recovered_above_u1_within_5d_after_exit"),
    }
    payload = {"schema": "a_share_long_only_risk_score.v1", "aggregate": aggregate, "results": rows}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.output_csv:
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(args.output_csv, index=False, encoding="utf-8-sig")
    print(json.dumps(aggregate, ensure_ascii=False))


if __name__ == "__main__":
    main()
