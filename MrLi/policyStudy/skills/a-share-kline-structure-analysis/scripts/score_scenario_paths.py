#!/usr/bin/env python3
"""Score frozen five-path K-line projections against later offline daily data."""

from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd


PATH_IDS = ("P1", "P2", "P3", "P4", "P5")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--analysis", action="append", required=True, help="JSON path or glob; repeatable")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path)
    return parser.parse_args()


def expand_inputs(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        matched = [Path(item) for item in glob.glob(pattern)]
        if not matched and Path(pattern).exists():
            matched = [Path(pattern)]
        files.extend(matched)
    unique = sorted(set(path.resolve() for path in files))
    if not unique:
        raise FileNotFoundError("No analysis files matched")
    return unique


def load_future(project_root: Path, ts_code: str, as_of: str, horizon: int) -> pd.DataFrame:
    sys.path.insert(0, str(project_root.resolve()))
    from coreClient.data_provider import get_day  # pylint: disable=import-error,import-outside-toplevel

    start = pd.to_datetime(as_of, format="%Y%m%d")
    end = start + pd.Timedelta(days=max(90, horizon * 3))
    frame = get_day(
        ts_code=ts_code,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        qfq=True,
        source="database",
    ).copy()
    frame["trade_date"] = frame["trade_date"].astype(str).str.replace("-", "", regex=False)
    frame = frame[frame["trade_date"] > as_of].sort_values("trade_date").head(horizon).reset_index(drop=True)
    for column in ("open", "high", "low", "close", "pre_close", "pct_chg"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if len(frame) < horizon:
        raise RuntimeError(f"{ts_code} only has {len(frame)} future bars; need {horizon}")
    return frame


def classify_path(frame: pd.DataFrame, upper: float, support: float, invalidation: float) -> tuple[str, dict[str, Any]]:
    support_touch_date: str | None = None
    first_trigger: str | None = None
    for row in frame.itertuples(index=False):
        if support_touch_date is None and row.low <= support:
            support_touch_date = row.trade_date
        if row.close < invalidation:
            first_trigger = "P4"
            break
        if row.close > upper:
            first_trigger = "P2" if support_touch_date is not None else "P1"
            break
    if first_trigger is None:
        first_trigger = "P3" if support_touch_date is not None else "P5"

    full_support_touch = next((row.trade_date for row in frame.itertuples(index=False) if row.low <= support), None)
    first_upper_date = next((row.trade_date for row in frame.itertuples(index=False) if row.close > upper), None)
    first_invalidation_date = next((row.trade_date for row in frame.itertuples(index=False) if row.close < invalidation), None)
    breakout_failure_date = None
    post_breakout_invalidation_date = None
    if first_upper_date is not None:
        after_breakout = frame[frame["trade_date"] > first_upper_date]
        failed = after_breakout[after_breakout["close"] < support]
        invalidated = after_breakout[after_breakout["close"] < invalidation]
        if not failed.empty:
            breakout_failure_date = failed.iloc[0]["trade_date"]
        if not invalidated.empty:
            post_breakout_invalidation_date = invalidated.iloc[0]["trade_date"]
    return first_trigger, {
        "support_touch_date": full_support_touch,
        "upper_close_date": first_upper_date,
        "invalidation_close_date": first_invalidation_date,
        "breakout_failure_date": breakout_failure_date,
        "post_breakout_invalidation_date": post_breakout_invalidation_date,
    }


def terminal_state(close: float, upper: float, support: float, invalidation: float) -> str:
    if close > upper:
        return "above_upper"
    if close >= support:
        return "between_support_and_upper"
    if close >= invalidation:
        return "between_invalidation_and_support"
    return "below_invalidation"


def future_return(frame: pd.DataFrame, base_close: float, index: int) -> float:
    return round((float(frame.iloc[index - 1]["close"]) / base_close - 1) * 100, 6)


def scenario_distance(actual: list[float], assumed: list[float], base_close: float) -> float:
    count = min(len(actual), len(assumed))
    return math.sqrt(sum(((actual[i] - assumed[i]) / base_close) ** 2 for i in range(count)) / count) * 100


def score_one(spec: dict[str, Any], future: pd.DataFrame) -> dict[str, Any]:
    bounds = spec["path_boundaries"]
    realized, events = classify_path(
        future,
        float(bounds["upper"]),
        float(bounds["support"]),
        float(bounds["invalidation"]),
    )
    probabilities = {item["id"]: float(item["weight"]) / 100 for item in spec["scenarios"]}
    top_path = max(probabilities, key=probabilities.get)
    brier = sum((probabilities.get(path, 0.0) - (1.0 if path == realized else 0.0)) ** 2 for path in PATH_IDS)
    log_loss = -math.log(max(probabilities.get(realized, 0.0), 1e-12))
    base_close = float(spec.get("base_close") or (future.iloc[0]["pre_close"] if "pre_close" in future.columns else 0.0))
    if base_close <= 0:
        # The frozen spec does not need to duplicate market data; infer the as-of close from first return relation.
        first = future.iloc[0]
        pct = float(first.get("pct_chg", 0.0)) / 100 if pd.notna(first.get("pct_chg", None)) else None
        base_close = float(first["close"]) / (1 + pct) if pct is not None and pct > -1 else float(first["open"])

    actual_closes = future["close"].astype(float).tolist()
    distances = {
        item["id"]: round(scenario_distance(actual_closes, [float(x) for x in item["closes"]], base_close), 6)
        for item in spec["scenarios"]
    }
    nearest_path = min(distances, key=distances.get)
    states = {
        f"t{step}": terminal_state(
            float(future.iloc[step - 1]["close"]),
            float(bounds["upper"]),
            float(bounds["support"]),
            float(bounds["invalidation"]),
        )
        for step in (5, 10, 20)
    }
    trajectory_signature = f"{realized}->{states['t5']}@T5->{states['t10']}@T10->{states['t20']}@T20"
    forecast_states = spec.get("forecast_terminal_states", {})
    terminal_correct = {
        key: forecast_states.get(key) == value if key in forecast_states else None
        for key, value in states.items()
    }
    chain_correct = (
        top_path == realized
        and all(terminal_correct.get(key) is True for key in ("t5", "t10", "t20"))
    ) if forecast_states else None
    return {
        "ts_code": spec["ts_code"],
        "stock_name": spec.get("stock_name"),
        "as_of": spec["as_of"],
        "horizon": len(future),
        "future_start": future.iloc[0]["trade_date"],
        "future_end": future.iloc[-1]["trade_date"],
        "boundaries": bounds,
        "realized_path": realized,
        "top_predicted_path": top_path,
        "top1_correct": top_path == realized,
        "realized_probability": probabilities.get(realized, 0.0),
        "brier_multiclass": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "events": events,
        "terminal_states": states,
        "forecast_terminal_states": forecast_states,
        "terminal_state_correct": terminal_correct,
        "trajectory_signature": trajectory_signature,
        "forecast_trajectory_signature": spec.get("forecast_trajectory_signature"),
        "full_trajectory_chain_correct": chain_correct,
        "return_t5_pct": future_return(future, base_close, 5),
        "return_t10_pct": future_return(future, base_close, 10),
        "return_t20_pct": future_return(future, base_close, 20),
        "max_high_pct": round((float(future["high"].max()) / base_close - 1) * 100, 6),
        "min_low_pct": round((float(future["low"].min()) / base_close - 1) * 100, 6),
        "nearest_five_day_scenario": nearest_path,
        "five_day_shape_top1_correct": top_path == nearest_path,
        "five_day_path_rmse_pct": distances,
        "future_bars": future[[column for column in ("trade_date", "open", "high", "low", "close", "vol", "amount", "pct_chg") if column in future.columns]].to_dict("records"),
    }


def main() -> None:
    args = parse_args()
    results: list[dict[str, Any]] = []
    for path in expand_inputs(args.analysis):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if spec.get("future_data_used") is not False:
            raise ValueError(f"Frozen analysis flag missing or invalid: {path}")
        horizon = int(spec["path_boundaries"].get("horizon", 20))
        future = load_future(args.project_root, spec["ts_code"], spec["as_of"], horizon)
        results.append(score_one(spec, future))

    aggregate = {
        "samples": len(results),
        "top1_accuracy": sum(item["top1_correct"] for item in results) / len(results),
        "five_day_shape_top1_accuracy": sum(item["five_day_shape_top1_correct"] for item in results) / len(results),
        "mean_brier_multiclass": sum(item["brier_multiclass"] for item in results) / len(results),
        "mean_log_loss": sum(item["log_loss"] for item in results) / len(results),
    }
    for key in ("t5", "t10", "t20"):
        available = [item["terminal_state_correct"][key] for item in results if item["terminal_state_correct"].get(key) is not None]
        if available:
            aggregate[f"terminal_state_{key}_accuracy"] = sum(available) / len(available)
    chain = [item["full_trajectory_chain_correct"] for item in results if item["full_trajectory_chain_correct"] is not None]
    if chain:
        aggregate["full_trajectory_chain_accuracy"] = sum(chain) / len(chain)
    payload = {"schema": "a_share_kline_scenario_score.v1", "aggregate": aggregate, "results": results}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.output_csv:
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        flat = []
        for item in results:
            row = {key: value for key, value in item.items() if key not in {"events", "terminal_states", "forecast_terminal_states", "terminal_state_correct", "five_day_path_rmse_pct", "future_bars", "boundaries"}}
            for key, value in item["terminal_states"].items():
                row[f"actual_state_{key}"] = value
            for key, value in item["forecast_terminal_states"].items():
                row[f"forecast_state_{key}"] = value
            for key, value in item["terminal_state_correct"].items():
                row[f"state_{key}_correct"] = value
            flat.append(row)
        pd.DataFrame(flat).to_csv(args.output_csv, index=False, encoding="utf-8-sig")
    print(json.dumps(aggregate, ensure_ascii=False))


if __name__ == "__main__":
    main()
