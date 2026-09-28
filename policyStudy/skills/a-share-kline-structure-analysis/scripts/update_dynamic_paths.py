#!/usr/bin/env python3
"""Update K-line path evidence one visible daily bar at a time.

The output contains evidence weights rather than calibrated probabilities.  It
separates the first-trigger path from the current evolution state so a P1/P2
breakout can later become a retest, failure, extension reversal, or invalidation.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PATH_IDS = ("P1", "P2", "P3", "P4", "P5")
STATE_IDS = (
    "pretrigger_wait",
    "support_test",
    "breakout_hold",
    "breakout_retest",
    "breakout_failure_warning",
    "extension_reversal_warning",
    "structure_invalidated",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--through-date", required=True, help="Last newly visible date, YYYYMMDD")
    parser.add_argument("--max-bars", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["trade_date"] = data["trade_date"].astype(str).str.replace("-", "", regex=False)
    for column in ("open", "high", "low", "close", "vol", "amount", "pct_chg"):
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    if "vol" not in data:
        data["vol"] = np.nan
    return data.sort_values("trade_date").reset_index(drop=True)


def load_observations(project_root: Path, ts_code: str, as_of: str, through_date: str, max_bars: int) -> pd.DataFrame:
    if through_date <= as_of:
        raise ValueError("through-date must be later than as_of")
    sys.path.insert(0, str(project_root.resolve()))
    from coreClient.data_provider import get_day  # pylint: disable=import-error,import-outside-toplevel

    frame = get_day(
        ts_code=ts_code,
        start_date=as_of,
        end_date=through_date,
        qfq=True,
        source="database",
    )
    if frame is None or frame.empty:
        raise RuntimeError(f"No observations for {ts_code} after {as_of}")
    data = normalize_frame(frame)
    data = data[(data["trade_date"] > as_of) & (data["trade_date"] <= through_date)].head(max_bars)
    if data.empty:
        raise RuntimeError(f"No newly visible bars through {through_date}")
    return data.reset_index(drop=True)


def softmax_weights(scores: dict[str, float], temperature: float = 1.35) -> dict[str, float]:
    values = np.array([scores[path] / temperature for path in PATH_IDS], dtype=float)
    values -= values.max()
    probabilities = np.exp(values)
    probabilities /= probabilities.sum()
    rounded = {path: round(float(probabilities[index] * 100), 2) for index, path in enumerate(PATH_IDS)}
    # Keep the displayed result at exactly 100 after rounding.
    rounded[max(rounded, key=rounded.get)] = round(rounded[max(rounded, key=rounded.get)] + 100 - sum(rounded.values()), 2)
    return rounded


def first_index(mask: pd.Series) -> int | None:
    locations = np.flatnonzero(mask.fillna(False).to_numpy())
    return int(locations[0]) if len(locations) else None


def event_context(observed: pd.DataFrame, upper: float, support: float, invalidation: float) -> dict[str, Any]:
    support_index = first_index(observed["low"] <= support)
    upper_index = first_index(observed["close"] > upper)
    invalid_index = first_index(observed["close"] < invalidation)
    origin_path: str | None = None
    trigger_index: int | None = None
    if upper_index is not None and (invalid_index is None or upper_index < invalid_index):
        trigger_index = upper_index
        origin_path = "P2" if support_index is not None and support_index <= upper_index else "P1"
    elif invalid_index is not None:
        trigger_index = invalid_index
        origin_path = "P4"
    return {
        "support_index": support_index,
        "upper_index": upper_index,
        "invalid_index": invalid_index,
        "origin_path": origin_path,
        "trigger_index": trigger_index,
        "support_touch_date": None if support_index is None else str(observed.iloc[support_index]["trade_date"]),
        "upper_close_date": None if upper_index is None else str(observed.iloc[upper_index]["trade_date"]),
        "invalidation_close_date": None if invalid_index is None else str(observed.iloc[invalid_index]["trade_date"]),
    }


def observed_features(packet: dict[str, Any], prefix: pd.DataFrame) -> dict[str, float]:
    latest = prefix.iloc[-1]
    prior_daily = pd.DataFrame(packet["daily"])[["trade_date", "close", "vol"]].copy()
    prior_daily["close"] = pd.to_numeric(prior_daily["close"], errors="coerce")
    prior_daily["vol"] = pd.to_numeric(prior_daily["vol"], errors="coerce")
    combined = pd.concat([prior_daily, prefix[["trade_date", "close", "vol"]]], ignore_index=True)
    previous_close = float(combined.iloc[-2]["close"])
    pct_change = (float(latest["close"]) / previous_close - 1) * 100
    span = float(latest["high"] - latest["low"])
    close_location = 0.5 if span <= 0 else (float(latest["close"]) - float(latest["low"])) / span
    historical_vol = combined["vol"].iloc[-21:-1].dropna()
    vol_ratio20 = float(latest["vol"]) / float(historical_vol.mean()) if len(historical_vol) and pd.notna(latest["vol"]) else 1.0
    recent = combined["close"].tail(min(4, len(combined))).pct_change().dropna().abs() * 100
    mean_abs_change3 = float(recent.tail(3).mean()) if len(recent) else 0.0
    return {
        "pct_change": round(pct_change, 6),
        "close_location": round(close_location, 6),
        "vol_ratio20": round(vol_ratio20, 6),
        "mean_abs_change3": round(mean_abs_change3, 6),
    }


def origin_evidence_weights(
    prior_weights: dict[str, float],
    prefix: pd.DataFrame,
    bounds: dict[str, float],
    features: dict[str, float],
) -> tuple[dict[str, float], dict[str, Any], list[str], list[str]]:
    upper, support, invalidation = bounds["upper"], bounds["support"], bounds["invalidation"]
    context = event_context(prefix, upper, support, invalidation)
    scores = {path: math.log(max(prior_weights[path] / 100, 0.01)) for path in PATH_IDS}
    positive: list[str] = []
    negative: list[str] = []
    latest = prefix.iloc[-1]

    if context["origin_path"]:
        origin = context["origin_path"]
        scores[origin] += 6.0
        for path in PATH_IDS:
            if path != origin:
                scores[path] -= 3.0
        if origin == "P4":
            positive.append(f"收盘{latest['close']:.2f}已跌破D1 {invalidation:.2f}，P4第一触发确认")
        else:
            touch_text = "此前已触及S1" if origin == "P2" else "此前未触及S1"
            positive.append(f"收盘已突破U1 {upper:.2f}且{touch_text}，{origin}第一触发确认")
    elif context["support_index"] is not None:
        scores["P2"] += 2.2
        scores["P3"] += 2.0
        scores["P1"] -= 1.8
        scores["P5"] -= 1.3
        positive.append(f"已触及S1 {support:.2f}但尚未触发U1/D1，P2与P3保留")
        if latest["close"] >= support and features["pct_change"] > 0 and features["close_location"] >= 0.6:
            scores["P2"] += 1.1
            positive.append("支撑测试后收涨且收盘位于日内高位，增加P2证据")
        if features["mean_abs_change3"] <= 2.0 and features["vol_ratio20"] <= 1.0:
            scores["P3"] += 0.9
            positive.append("近三日振幅与成交趋缓，增加P3横盘证据")
        if latest["close"] < support:
            scores["P4"] += 1.0
            scores["P2"] -= 0.5
            negative.append("收盘位于S1下方，P2转强假设减弱，P4风险增加")
    else:
        distance = upper / float(latest["close"]) - 1
        if distance <= 0.03 and features["pct_change"] > 0:
            scores["P1"] += 1.3
            positive.append("价格接近U1且收涨，增加P1直接突破证据")
        if features["pct_change"] >= 2.0 and features["close_location"] >= 0.7 and features["vol_ratio20"] >= 1.2:
            scores["P1"] += 1.0
            positive.append("放量强收盘，增加P1动量证据")
        if features["mean_abs_change3"] <= 2.0:
            scores["P5"] += 1.0
            positive.append("未触及关键位且波动收缩，增加P5等待证据")
        if latest["high"] > upper and latest["close"] <= upper:
            scores["P1"] -= 0.8
            scores["P5"] += 0.8
            scores["P4"] += 0.4
            negative.append("盘中越过U1但收盘未站稳，出现突破失败探针")

    if features["pct_change"] <= -4 and features["close_location"] <= 0.3 and features["vol_ratio20"] >= 1.2:
        scores["P4"] += 1.2
        negative.append("放量长阴且收于日内低位，P4风险显著增加")
    return softmax_weights(scores), context, positive, negative


def phase_and_active_weights(
    origin_weights: dict[str, float],
    context: dict[str, Any],
    prefix: pd.DataFrame,
    bounds: dict[str, float],
) -> tuple[str, dict[str, float], list[str]]:
    upper, support, invalidation = bounds["upper"], bounds["support"], bounds["invalidation"]
    target = bounds.get("target")
    close = float(prefix.iloc[-1]["close"])
    high_since = float(prefix["high"].max())
    origin = context["origin_path"]
    notes: list[str] = []

    if close < invalidation:
        phase = "structure_invalidated"
        active = {"P1": 2.5, "P2": 2.5, "P3": 5.0, "P4": 87.5, "P5": 2.5}
        notes.append(f"收盘跌破D1 {invalidation:.2f}，原上行/横盘结构失效")
    elif context["upper_index"] is not None:
        if close > upper:
            phase = "breakout_hold"
            lead = origin if origin in {"P1", "P2"} else "P1"
            active = {path: 5.0 for path in PATH_IDS}
            active[lead] = 75.0
            active["P5"] = 10.0
            notes.append(f"突破后收盘仍保持在U1 {upper:.2f}上方")
        elif close >= support:
            phase = "breakout_retest"
            active = {"P1": 15.0, "P2": 25.0, "P3": 35.0, "P4": 10.0, "P5": 15.0}
            notes.append(f"已突破但收盘回到U1下方，S1 {support:.2f}仍有效")
        else:
            reached_extension = target is not None and high_since >= float(target)
            phase = "extension_reversal_warning" if reached_extension else "breakout_failure_warning"
            active = {"P1": 5.0, "P2": 10.0, "P3": 30.0, "P4": 50.0, "P5": 5.0}
            notes.append("突破后跌破S1，进入延伸后反转预警" if reached_extension else "突破后跌破S1，进入突破失败预警")
    elif context["support_index"] is not None:
        phase = "support_test"
        active = origin_weights.copy()
        notes.append(f"已测试S1 {support:.2f}，等待突破U1或跌破D1")
    else:
        phase = "pretrigger_wait"
        active = origin_weights.copy()
        notes.append("尚未触及S1、收盘突破U1或跌破D1")
    return phase, active, notes


def next_confirmation(phase: str, bounds: dict[str, float]) -> list[str]:
    upper, support, invalidation = bounds["upper"], bounds["support"], bounds["invalidation"]
    if phase == "pretrigger_wait":
        return [f"收盘突破U1 {upper:.2f}确认P1", f"触及S1 {support:.2f}转入P2/P3观察", f"收盘跌破D1 {invalidation:.2f}确认P4"]
    if phase == "support_test":
        return [f"守住D1 {invalidation:.2f}后收盘突破U1 {upper:.2f}确认P2", f"持续位于S1—U1确认P3", f"收盘跌破D1确认P4"]
    if phase == "breakout_hold":
        return [f"继续收于U1 {upper:.2f}上方确认保持", f"回踩U1/S1观察承接", f"跌破S1 {support:.2f}触发突破失败预警"]
    if phase == "breakout_retest":
        return [f"重新站上U1 {upper:.2f}恢复突破", f"守住S1 {support:.2f}维持回踩", f"跌破S1升级失败预警"]
    if phase in {"breakout_failure_warning", "extension_reversal_warning"}:
        return [f"重新站回U1 {upper:.2f}解除预警", f"守住D1 {invalidation:.2f}转入弱势整理", f"跌破D1确认结构失效"]
    return [f"重新站回S1 {support:.2f}才有修复证据", f"未收回前维持结构失效判断"]


def risk_posture(
    phase: str,
    active_weights: dict[str, float],
    bounds: dict[str, float],
) -> dict[str, Any]:
    """Map path evidence to an explicit long-only risk posture.

    The posture deliberately avoids position percentages.  It answers whether
    upside has been confirmed and which frozen price invalidates that premise.
    """
    upside_weight = round(active_weights["P1"] + active_weights["P2"], 2)
    non_upside_weight = round(100 - upside_weight, 2)
    if phase in {"structure_invalidated", "breakout_failure_warning", "extension_reversal_warning"}:
        state = "exit_risk"
        action = "上升预期已经失败或结构失效；退出上升假设并等待重新建构"
        must_hold = None
    elif phase == "breakout_retest":
        state = "reduce_risk"
        action = "突破未保持；降低风险暴露，等待重新站上U1或守住S1"
        must_hold = float(bounds["support"])
    elif phase == "breakout_hold":
        state = "upside_confirmed"
        action = "P1/P2上升路径已经确认；继续检查U1保持和S1承接"
        must_hold = float(bounds["support"])
    elif phase == "support_test":
        state = "observe_upside_unconfirmed"
        action = "只保留观察资格；尚未突破U1，不把回踩自动视为转强"
        must_hold = float(bounds["invalidation"])
    else:
        state = "observe_no_position"
        action = "上升路径尚未确认；等待U1突破或S1测试结果"
        must_hold = float(bounds["invalidation"])
    return {
        "state": state,
        "upside_evidence_weight": upside_weight,
        "non_upside_evidence_weight": non_upside_weight,
        "action_logic": action,
        "must_hold_level": must_hold,
        "hard_invalidation_level": float(bounds["invalidation"]),
    }


def replay(packet: dict[str, Any], analysis: dict[str, Any], observed: pd.DataFrame) -> dict[str, Any]:
    if packet["ts_code"] != analysis["ts_code"] or packet["as_of"] != analysis["as_of"]:
        raise ValueError("Packet and analysis mismatch")
    if packet.get("available_end") != packet.get("as_of") or analysis.get("future_data_used") is not False:
        raise ValueError("Frozen inputs failed future-isolation gate")
    observed = normalize_frame(observed)
    if (observed["trade_date"] <= packet["as_of"]).any():
        raise ValueError("Observed bars must all be later than as_of")
    prior = {item["id"]: float(item["weight"]) for item in analysis["scenarios"]}
    path_bounds = analysis["path_boundaries"]
    bounds = {
        "upper": float(path_bounds["upper"]),
        "support": float(path_bounds["support"]),
        "invalidation": float(path_bounds["invalidation"]),
        "target": next((float(item["price"]) for item in analysis.get("levels", []) if item.get("kind") == "target"), None),
    }
    timeline: list[dict[str, Any]] = []
    for index in range(len(observed)):
        prefix = observed.iloc[: index + 1].copy()
        features = observed_features(packet, prefix)
        weights, context, positive, negative = origin_evidence_weights(prior, prefix, bounds, features)
        phase, active_weights, phase_notes = phase_and_active_weights(weights, context, prefix, bounds)
        posture = risk_posture(phase, active_weights, bounds)
        top_origin = max(weights, key=weights.get)
        top_active = max(active_weights, key=active_weights.get)
        timeline.append(
            {
                "step": index + 1,
                "trade_date": str(prefix.iloc[-1]["trade_date"]),
                "bar": {key: round(float(prefix.iloc[-1][key]), 6) for key in ("open", "high", "low", "close", "vol") if pd.notna(prefix.iloc[-1][key])},
                "features": features,
                "origin_path_evidence_weights": weights,
                "top_origin_path": top_origin,
                "origin_triggered_path": context["origin_path"],
                "origin_trigger_date": None if context["trigger_index"] is None else str(prefix.iloc[context["trigger_index"]]["trade_date"]),
                "current_phase": phase,
                "active_path_evidence_weights": active_weights,
                "top_active_path": top_active,
                "long_only_risk_posture": posture,
                "supporting_evidence": positive + phase_notes,
                "contrary_evidence": negative,
                "next_confirmation": next_confirmation(phase, bounds),
                "event_dates": {key: context[key] for key in ("support_touch_date", "upper_close_date", "invalidation_close_date")},
            }
        )
    return {
        "schema": "a_share_kline_dynamic_path_update.v1",
        "ts_code": packet["ts_code"],
        "stock_name": packet.get("stock_name"),
        "as_of": packet["as_of"],
        "visible_end": str(observed.iloc[-1]["trade_date"]),
        "bars_processed": len(observed),
        "weight_type": "rule_based_evidence_weight_not_calibrated_probability",
        "boundaries": bounds,
        "initial_weights": prior,
        "timeline": timeline,
        "latest": timeline[-1],
    }


def main() -> None:
    args = parse_args()
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    observations = load_observations(args.project_root, packet["ts_code"], packet["as_of"], args.through_date, args.max_bars)
    result = replay(packet, analysis, observations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "ts_code": result["ts_code"], "visible_end": result["visible_end"], "latest_phase": result["latest"]["current_phase"], "top_active_path": result["latest"]["top_active_path"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
