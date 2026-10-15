#!/usr/bin/env python3
"""Replay frozen analyses bar by bar and evaluate path recognition speed."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_dynamic_paths import PATH_IDS, replay  # pylint: disable=wrong-import-position


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, required=True)
    parser.add_argument("--packet-dir", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path)
    return parser.parse_args()


def first_true(values: list[bool]) -> int | None:
    for index, value in enumerate(values, start=1):
        if value:
            return index
    return None


def stable_true(values: list[bool], through_step: int) -> int | None:
    limited = values[:through_step]
    for index in range(len(limited)):
        if all(limited[index:]):
            return index + 1
    return None


def top_two(weights: dict[str, float]) -> list[str]:
    return sorted(PATH_IDS, key=lambda path: (-weights[path], PATH_IDS.index(path)))[:2]


def event_step(timeline: list[dict[str, Any]], event_date: str | None, fallback: int) -> int:
    if event_date:
        for item in timeline:
            if item["trade_date"] == event_date:
                return int(item["step"])
    return fallback


def score_sample(result: dict[str, Any], dynamic: dict[str, Any]) -> dict[str, Any]:
    realized = result["realized_path"]
    timeline = dynamic["timeline"]
    if realized in {"P1", "P2"}:
        trigger_date = result["events"]["upper_close_date"]
    elif realized == "P4":
        trigger_date = result["events"]["invalidation_close_date"]
    else:
        trigger_date = timeline[-1]["trade_date"]
    trigger = event_step(timeline, trigger_date, len(timeline))
    correct = [item["top_origin_path"] == realized for item in timeline]
    top2_correct = [realized in top_two(item["origin_path_evidence_weights"]) for item in timeline]
    first = first_true(correct)
    stable = stable_true(correct, trigger)
    switches = sum(timeline[index]["top_origin_path"] != timeline[index - 1]["top_origin_path"] for index in range(1, trigger))
    warning_dates: dict[str, str] = {}
    for item in timeline:
        phase = item["current_phase"]
        warning_dates.setdefault(phase, item["trade_date"])
    severe_warning_dates = [
        date
        for phase, date in warning_dates.items()
        if phase in {"breakout_failure_warning", "extension_reversal_warning", "structure_invalidated"}
    ]
    first_severe_warning = min(severe_warning_dates) if severe_warning_dates else None
    return {
        "ts_code": result["ts_code"],
        "stock_name": result.get("stock_name"),
        "as_of": result["as_of"],
        "realized_path": realized,
        "trigger_date": trigger_date,
        "trigger_step": trigger,
        "top_at_t1": timeline[0]["top_origin_path"],
        "top_at_t2": timeline[min(1, len(timeline) - 1)]["top_origin_path"],
        "top_at_t3": timeline[min(2, len(timeline) - 1)]["top_origin_path"],
        "top_at_t5": timeline[min(4, len(timeline) - 1)]["top_origin_path"],
        "correct_at_t1": correct[0],
        "correct_at_t2": correct[min(1, len(correct) - 1)],
        "correct_at_t3": correct[min(2, len(correct) - 1)],
        "correct_at_t5": correct[min(4, len(correct) - 1)],
        "correct_at_trigger": correct[trigger - 1],
        "top2_at_t1": top2_correct[0],
        "top2_at_trigger": top2_correct[trigger - 1],
        "correct_one_bar_before_trigger": None if trigger == 1 else correct[trigger - 2],
        "first_correct_step": first,
        "stable_correct_step_through_trigger": stable,
        "stable_lead_bars": None if stable is None else trigger - stable,
        "top_path_switches_before_trigger": switches,
        "realized_weight_t1": timeline[0]["origin_path_evidence_weights"][realized],
        "realized_weight_at_trigger": timeline[trigger - 1]["origin_path_evidence_weights"][realized],
        "phase_at_t5": timeline[min(4, len(timeline) - 1)]["current_phase"],
        "phase_at_t10": timeline[min(9, len(timeline) - 1)]["current_phase"],
        "phase_at_t20": timeline[min(19, len(timeline) - 1)]["current_phase"],
        "breakout_failure_actual_date": result["events"].get("breakout_failure_date"),
        "breakout_failure_warning_date": warning_dates.get("breakout_failure_warning") or warning_dates.get("extension_reversal_warning"),
        "first_severe_warning_date": first_severe_warning,
        "post_breakout_invalidation_actual_date": result["events"].get("post_breakout_invalidation_date"),
        "structure_invalidated_warning_date": warning_dates.get("structure_invalidated"),
        "dynamic_output": f"{result['ts_code'].replace('.', '_')}_{result['as_of']}.json",
    }


def mean_bool(rows: list[dict[str, Any]], key: str) -> float:
    values = [row[key] for row in rows if row.get(key) is not None]
    return sum(bool(value) for value in values) / len(values) if values else float("nan")


def mean_number(rows: list[dict[str, Any]], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return sum(values) / len(values) if values else float("nan")


def main() -> None:
    args = parse_args()
    score = json.loads(args.score.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for result in score["results"]:
        stem = f"{result['ts_code'].replace('.', '_')}_{result['as_of']}"
        packet = json.loads((args.packet_dir / f"{stem}.json").read_text(encoding="utf-8"))
        analysis = json.loads((args.analysis_dir / f"{stem}.json").read_text(encoding="utf-8"))
        observed = pd.DataFrame(result["future_bars"])
        dynamic = replay(packet, analysis, observed)
        (args.output_dir / f"{stem}.json").write_text(json.dumps(dynamic, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(score_sample(result, dynamic))

    failures = [row for row in rows if row["breakout_failure_actual_date"]]
    invalidations = [row for row in rows if row["post_breakout_invalidation_actual_date"]]
    aggregate = {
        "samples": len(rows),
        "path_coverage": 1.0,
        "origin_top1_t1_accuracy": mean_bool(rows, "correct_at_t1"),
        "origin_top1_t2_accuracy": mean_bool(rows, "correct_at_t2"),
        "origin_top1_t3_accuracy": mean_bool(rows, "correct_at_t3"),
        "origin_top1_t5_accuracy": mean_bool(rows, "correct_at_t5"),
        "origin_top1_at_trigger_accuracy": mean_bool(rows, "correct_at_trigger"),
        "origin_top2_t1_accuracy": mean_bool(rows, "top2_at_t1"),
        "origin_top2_at_trigger_accuracy": mean_bool(rows, "top2_at_trigger"),
        "one_bar_before_trigger_accuracy": mean_bool(rows, "correct_one_bar_before_trigger"),
        "stable_recognition_by_trigger_rate": sum(row["stable_correct_step_through_trigger"] is not None for row in rows) / len(rows),
        "mean_stable_lead_bars": mean_number(rows, "stable_lead_bars"),
        "mean_realized_weight_t1": mean_number(rows, "realized_weight_t1"),
        "mean_realized_weight_at_trigger": mean_number(rows, "realized_weight_at_trigger"),
        "mean_top_path_switches_before_trigger": mean_number(rows, "top_path_switches_before_trigger"),
        "breakout_failure_events": len(failures),
        "breakout_failure_same_day_warning_rate": mean_bool(
            [{"same": row["breakout_failure_actual_date"] == row["breakout_failure_warning_date"]} for row in failures], "same"
        ),
        "breakout_failure_timely_or_stronger_warning_rate": mean_bool(
            [{"timely": row["first_severe_warning_date"] is not None and row["first_severe_warning_date"] <= row["breakout_failure_actual_date"]} for row in failures], "timely"
        ),
        "post_breakout_invalidations": len(invalidations),
        "post_breakout_invalidation_same_day_warning_rate": mean_bool(
            [{"same": row["post_breakout_invalidation_actual_date"] == row["structure_invalidated_warning_date"]} for row in invalidations], "same"
        ),
        "post_breakout_invalidation_timely_warning_rate": mean_bool(
            [{"timely": row["structure_invalidated_warning_date"] is not None and row["structure_invalidated_warning_date"] <= row["post_breakout_invalidation_actual_date"]} for row in invalidations], "timely"
        ),
    }
    payload = {"schema": "a_share_kline_dynamic_replay_score.v1", "aggregate": aggregate, "results": rows}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.output_csv:
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(args.output_csv, index=False, encoding="utf-8-sig")
    print(json.dumps(aggregate, ensure_ascii=False))


if __name__ == "__main__":
    main()
