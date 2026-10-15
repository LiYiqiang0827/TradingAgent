#!/usr/bin/env python3
"""Generate a deterministic, future-isolated five-path analysis specification.

This is the reproducible baseline implementation of the skill.  It only reads a
packet already truncated at ``as_of``.  The result is deliberately called an
uncalibrated judgmental-weight specification; later data must be opened only by
the separate scorer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


VERSION = "default-spec-v1.0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata-json", default="{}")
    return parser.parse_args()


def frame_from(records: list[dict[str, Any]], date_column: str) -> pd.DataFrame:
    frame = pd.DataFrame(records).copy()
    if frame.empty:
        raise ValueError("Empty timeframe in packet")
    frame[date_column] = frame[date_column].astype(str).str.replace("-", "", regex=False)
    for column in ("open", "high", "low", "close", "vol", "amount"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["open", "high", "low", "close"]).sort_values(date_column).reset_index(drop=True)


def confirmed_pivots(frame: pd.DataFrame, order: int = 2) -> list[dict[str, Any]]:
    pivots: list[dict[str, Any]] = []
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    for index in range(order, len(frame) - order):
        if highs[index] >= np.nanmax(highs[index - order : index + order + 1]):
            pivots.append({"kind": "high", "price": float(highs[index]), "index": index})
        if lows[index] <= np.nanmin(lows[index - order : index + order + 1]):
            pivots.append({"kind": "low", "price": float(lows[index]), "index": index})
    return pivots


def trend_state(frame: pd.DataFrame, fast: int, slow: int) -> tuple[str, dict[str, float]]:
    close = frame["close"].astype(float)
    fast_ma = close.rolling(fast).mean()
    slow_ma = close.rolling(slow).mean()
    latest = float(close.iloc[-1])
    fast_now = float(fast_ma.iloc[-1]) if pd.notna(fast_ma.iloc[-1]) else latest
    slow_now = float(slow_ma.iloc[-1]) if pd.notna(slow_ma.iloc[-1]) else fast_now
    lookback = min(3, len(frame) - 1)
    fast_old = float(fast_ma.iloc[-1 - lookback]) if pd.notna(fast_ma.iloc[-1 - lookback]) else fast_now
    if latest > fast_now > slow_now and fast_now >= fast_old:
        state = "up"
    elif latest < fast_now < slow_now and fast_now <= fast_old:
        state = "down"
    else:
        state = "neutral"
    return state, {"close": latest, f"ma{fast}": fast_now, f"ma{slow}": slow_now}


def dow_structure(swings: list[dict[str, Any]]) -> str:
    highs = [float(item["price"]) for item in swings if item["kind"] in {"high", "swing_high"}]
    lows = [float(item["price"]) for item in swings if item["kind"] in {"low", "swing_low"}]
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            return "更高高点与更高低点候选，上升结构占优"
        if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            return "更低高点与更低低点候选，下降结构占优"
    return "高低点未同向确认，按区间或转折候选处理"


def level_candidates(
    packet: dict[str, Any], daily: pd.DataFrame, weekly: pd.DataFrame, monthly: pd.DataFrame
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in packet.get("confirmed_swings", []):
        candidates.append(
            {
                "price": float(item["price"]),
                "kind": "high" if item["kind"] == "swing_high" else "low",
                "timeframe": "daily",
                "source": f"日线确认摆动点 {item['trade_date']}",
            }
        )
    for timeframe, frame in (("weekly", weekly), ("monthly", monthly)):
        label = "周线" if timeframe == "weekly" else "月线"
        for item in confirmed_pivots(frame, 2)[-16:]:
            candidates.append(
                {
                    "price": float(item["price"]),
                    "kind": item["kind"],
                    "timeframe": timeframe,
                    "source": f"{label}确认摆动点",
                }
            )
    latest = packet["latest"]
    for window in (5, 10, 20, 60, 120, 250):
        value = latest.get(f"ma{window}")
        if value is not None and math.isfinite(float(value)):
            candidates.append(
                {
                    "price": float(value),
                    "kind": "ma",
                    "timeframe": "daily",
                    "source": f"日线MA{window}",
                }
            )
    return candidates


def select_levels(packet: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    latest = packet["latest"]
    close = float(latest["close"])
    high = float(latest["high"])
    atr = float(latest.get("atr14") or close * 0.03)
    resistance = sorted((item for item in candidates if item["kind"] == "high" and item["price"] >= close * 1.005), key=lambda x: x["price"])
    if resistance:
        upper_item = resistance[0]
        upper = float(upper_item["price"])
    else:
        upper = max(high, close * 1.003)
        upper_item = {"price": upper, "timeframe": "daily", "source": "分析日高点/新高确认"}

    below = sorted(
        (item for item in candidates if item["price"] <= close * 0.985 and item["price"] > 0),
        key=lambda x: x["price"],
        reverse=True,
    )
    if below:
        support_item = below[0]
        support = float(support_item["price"])
    else:
        support = max(0.01, close - max(1.5 * atr, close * 0.06))
        support_item = {"price": support, "timeframe": "daily", "source": "ATR回撤支撑"}

    deeper = [item for item in below[1:] if item["price"] <= support - max(0.5 * atr, close * 0.02)]
    if deeper:
        invalid_item = deeper[0]
        invalidation = float(invalid_item["price"])
    else:
        invalidation = max(0.01, support - max(1.5 * atr, close * 0.07))
        invalid_item = {"price": invalidation, "timeframe": "daily", "source": "支撑下方ATR失效位"}

    # Protect ordering even for unusual adjusted histories.
    support = min(support, upper - max(0.5 * atr, close * 0.02))
    invalidation = min(invalidation, support - max(0.5 * atr, close * 0.02))
    invalidation = max(0.01, invalidation)

    later_resistance = [item for item in resistance if item["price"] > upper + max(0.5 * atr, close * 0.02)]
    if later_resistance:
        target_item = later_resistance[0]
        target = float(target_item["price"])
    else:
        target = upper + max(2.0 * atr, close * 0.08)
        target_item = {"price": target, "timeframe": upper_item["timeframe"], "source": "ATR/8%测量目标"}

    return {
        "upper": round(upper, 4),
        "support": round(support, 4),
        "invalidation": round(invalidation, 4),
        "target": round(target, 4),
        "upper_item": upper_item,
        "support_item": support_item,
        "invalid_item": invalid_item,
        "target_item": target_item,
    }


def timeframes(item: dict[str, Any]) -> list[str]:
    timeframe = item.get("timeframe", "daily")
    if timeframe == "monthly":
        return ["daily", "weekly", "monthly"]
    if timeframe == "weekly":
        return ["daily", "weekly"]
    return ["daily"]


def choose_weights(states: dict[str, str], latest: dict[str, Any], levels: dict[str, Any]) -> tuple[str, list[int]]:
    up = sum(value == "up" for value in states.values())
    down = sum(value == "down" for value in states.values())
    if up == 3:
        regime, weights = "月周日多周期上行", [30, 25, 15, 10, 20]
    elif states["daily"] == "up" and down >= 1:
        regime, weights = "日线转强但高周期仍弱", [15, 20, 25, 25, 15]
    elif up >= 2:
        regime, weights = "多数周期偏强", [25, 25, 20, 15, 15]
    elif down >= 2:
        regime, weights = "多数周期偏弱", [10, 15, 25, 35, 15]
    else:
        regime, weights = "多周期混合或区间", [20, 20, 25, 20, 15]

    close = float(latest["close"])
    ma20 = float(latest.get("ma20") or close)
    overextension = close / ma20 - 1 if ma20 else 0.0
    if overextension > 0.15:
        weights[0] -= 5
        weights[3] += 5
    elif overextension < 0.04 and states["daily"] == "up":
        weights[0] += 5
        weights[4] -= 5
    if float(latest.get("close_location") or 0.5) >= 0.9 and float(latest.get("vol_ratio20") or 1.0) >= 1.8:
        weights[1] += 5
        weights[4] -= 5
    if (float(levels["upper"]) / close - 1) < 0.025 and levels["upper"] > close:
        weights[0] -= 5
        weights[2] += 5

    weights = [max(5, int(round(value / 5) * 5)) for value in weights]
    delta = 100 - sum(weights)
    # Adjust the largest bucket in five-point increments while keeping every path alive.
    while delta != 0:
        step = 5 if delta > 0 else -5
        order = sorted(range(5), key=lambda i: weights[i], reverse=delta < 0)
        for index in order:
            if weights[index] + step >= 5:
                weights[index] += step
                delta -= step
                break
    return regime, weights


def linear_path(start: float, end: float, count: int = 5) -> list[float]:
    return [round(float(value), 4) for value in np.linspace(start, end, count)]


def build_scenarios(close: float, levels: dict[str, Any], weights: list[int]) -> list[dict[str, Any]]:
    upper = float(levels["upper"])
    support = float(levels["support"])
    invalidation = float(levels["invalidation"])
    target = float(levels["target"])
    p1_start = max(close * 1.015, upper * 1.005)
    p1 = linear_path(p1_start, max(target, p1_start * 1.035))
    p2 = [
        round(max(support * 1.015, close - 0.45 * (close - support)), 4),
        round(support * 1.005, 4),
        round((support + upper) / 2, 4),
        round(upper * 1.005, 4),
        round((upper + target) / 2, 4),
    ]
    p3 = [
        round(close - 0.35 * (close - support), 4),
        round(support * 1.005, 4),
        round(support + 0.35 * (upper - support), 4),
        round(support + 0.55 * (upper - support), 4),
        round(support + 0.45 * (upper - support), 4),
    ]
    p4 = [
        round(close - 0.60 * (close - support), 4),
        round(support * 0.99, 4),
        round((support + invalidation) / 2, 4),
        round(invalidation * 0.99, 4),
        round(invalidation * 0.96, 4),
    ]
    ceiling = min(close, upper) * 0.998
    floor = max(support * 1.03, close * 0.965)
    if floor >= ceiling:
        floor = support + 0.25 * (upper - support)
    p5 = [round(value, 4) for value in (ceiling, (ceiling + floor) / 2, ceiling * 0.997, floor * 1.005, ceiling * 0.999)]

    common = {"probability_type": "judgmental_weight"}
    return [
        {"id": "P1", "name": "直接上行", "weight": weights[0], **common, "expected_period": "1-5交易日", "trigger": f"未先触及{support:.2f}，收盘突破{upper:.2f}", "invalidation": f"重新收于{support:.2f}下方", "closes": p1, "theory_transition": "SOS和更高高点候选增强；推动段延伸；均线继续上拐；向上离开类中枢。"},
        {"id": "P2", "name": "回踩后转强", "weight": weights[1], **common, "expected_period": "3-10交易日", "trigger": f"先测试{support:.2f}并守住{invalidation:.2f}，再收盘突破{upper:.2f}", "invalidation": f"收盘跌破{invalidation:.2f}", "closes": p2, "theory_transition": "LPS/测试供给候选；形成更高低点；短均线消化乖离；调整浪结束；候选三买。"},
        {"id": "P3", "name": "回踩后横盘", "weight": weights[2], **common, "expected_period": "5-15交易日", "trigger": f"触及{support:.2f}且守住{invalidation:.2f}，窗口内未突破{upper:.2f}", "invalidation": f"突破{upper:.2f}或跌破{invalidation:.2f}", "closes": p3, "theory_transition": "供需重新平衡；道氏方向待定；短均线走平；调整延长；类中枢扩展。"},
        {"id": "P4", "name": "直接转弱", "weight": weights[3], **common, "expected_period": "1-8交易日", "trigger": f"有效突破{upper:.2f}前收盘跌破{invalidation:.2f}", "invalidation": f"快速重新站回{support:.2f}", "closes": p4, "theory_transition": "原结构失效；道氏转弱；短均线拐头；主浪计数失效；向下离开区间。"},
        {"id": "P5", "name": "高位或原区间横盘", "weight": weights[4], **common, "expected_period": "5-12交易日", "trigger": f"未触及{support:.2f}且未突破{upper:.2f}或跌破{invalidation:.2f}", "invalidation": f"突破{upper:.2f}或触及{support:.2f}", "closes": p5, "theory_transition": "供需暂平衡；趋势未新增确认；均线逐步靠拢；平台调整；形成新的重叠区。"},
    ]


def forecast_states(top_path: str, states: dict[str, str]) -> dict[str, str]:
    if top_path == "P4":
        return {"t5": "below_invalidation", "t10": "below_invalidation", "t20": "below_invalidation"}
    if top_path in {"P3", "P5"}:
        return {"t5": "between_support_and_upper", "t10": "between_support_and_upper", "t20": "between_support_and_upper"}
    t20 = "above_upper" if states["monthly"] == states["weekly"] == "up" else "between_support_and_upper"
    return {"t5": "above_upper", "t10": "above_upper", "t20": t20}


def main() -> None:
    args = parse_args()
    raw = args.packet.read_bytes()
    packet = json.loads(raw.decode("utf-8"))
    metadata = json.loads(args.metadata_json)
    if packet.get("future_isolated") is not True or packet.get("available_end") != packet.get("as_of"):
        raise ValueError("Packet is not future isolated")
    daily = frame_from(packet["daily"], "trade_date")
    weekly = frame_from(packet["weekly_from_truncated_daily"], "period_end")
    monthly = frame_from(packet["monthly_from_truncated_daily"], "period_end")
    as_of = str(packet["as_of"])
    if daily.iloc[-1]["trade_date"] != as_of or weekly.iloc[-1]["period_end"] > as_of or monthly.iloc[-1]["period_end"] > as_of:
        raise ValueError("A timeframe extends beyond as_of")

    states: dict[str, str] = {}
    states["monthly"], monthly_ma = trend_state(monthly, 3, 6)
    states["weekly"], weekly_ma = trend_state(weekly, 5, 20)
    states["daily"], daily_ma = trend_state(daily, 20, 60)
    candidates = level_candidates(packet, daily, weekly, monthly)
    levels = select_levels(packet, candidates)
    regime, weights = choose_weights(states, packet["latest"], levels)
    close = float(packet["latest"]["close"])
    scenarios = build_scenarios(close, levels, weights)
    top = max(scenarios, key=lambda item: item["weight"])["id"]
    forecasts = forecast_states(top, states)
    dow = dow_structure(packet.get("confirmed_swings", []))
    relation = f"月线{states['monthly']}、周线{states['weekly']}、日线{states['daily']}；归类为{regime}。"
    vol_ratio = float(packet["latest"].get("vol_ratio20") or 0.0)
    dist20 = float(packet["latest"].get("dist_ma20_pct") or 0.0)

    tf_name = {"daily": "日线", "weekly": "周线", "monthly": "月线"}
    output = {
        "schema": "a_share_kline_analysis.v1",
        "generator": {"name": "a-share-kline-structure-analysis default spec", "version": VERSION, "deterministic": True},
        "input_packet_sha256": hashlib.sha256(raw).hexdigest(),
        "frozen_before_future": True,
        "sample_metadata": metadata,
        "probability_note": "未校准情景权重，按5个百分点给出；用于后续冻结预测评分，不等同于统计概率。",
        "future_data_used": False,
        "ts_code": packet["ts_code"],
        "stock_name": packet.get("stock_name"),
        "as_of": as_of,
        "regime": regime,
        "structure_summary": f"{relation} 当前收盘{close:.2f}，距MA20为{dist20:.1f}%，量比20日均量{vol_ratio:.2f}倍；U1/S1/D1固定为{levels['upper']:.2f}/{levels['support']:.2f}/{levels['invalidation']:.2f}。",
        "multi_timeframe": {
            "monthly": f"月线状态{states['monthly']}，收盘{monthly_ma['close']:.2f}，MA3/MA6为{monthly_ma['ma3']:.2f}/{monthly_ma['ma6']:.2f}。",
            "weekly": f"周线状态{states['weekly']}，收盘{weekly_ma['close']:.2f}，MA5/MA20为{weekly_ma['ma5']:.2f}/{weekly_ma['ma20']:.2f}。",
            "daily": f"日线状态{states['daily']}，收盘{daily_ma['close']:.2f}，MA20/MA60为{daily_ma['ma20']:.2f}/{daily_ma['ma60']:.2f}。",
            "relation": relation,
        },
        "observable_features": {
            "timeframe_states": states,
            "latest_pct_chg": packet["latest"].get("pct_chg_calc"),
            "close_location": packet["latest"].get("close_location"),
            "vol_ratio20": packet["latest"].get("vol_ratio20"),
            "dist_ma20_pct": packet["latest"].get("dist_ma20_pct"),
            "atr14": packet["latest"].get("atr14"),
        },
        "five_theory_static": {
            "wyckoff": f"涨停/强势事件后按SOS候选观察；{levels['support']:.2f}能否缩量守住决定测试是否有效，跌破{levels['invalidation']:.2f}否定原强势假设。",
            "dow": f"{dow}；收盘越过{levels['upper']:.2f}才新增向上确认。",
            "ma": f"多周期均线状态为月{states['monthly']}/周{states['weekly']}/日{states['daily']}；当前距MA20 {dist20:.1f}%，需要同时考虑趋势和乖离。",
            "elliott": f"主计数按{regime}的推动/反弹候选处理；备选为末端加速或延长调整，{levels['invalidation']:.2f}是统一失效参考。",
            "chan": f"以{levels['support']:.2f}-{levels['upper']:.2f}作为可观察重叠区；有效离开、返回区间或跌破失效位分别对应三类状态迁移。",
        },
        "levels": [
            {"kind": "resistance", "price": levels["upper"], "label": f"U1 {levels['upper_item']['source']}", "timeframes": timeframes(levels["upper_item"])},
            {"kind": "target", "price": levels["target"], "label": f"U2 {levels['target_item']['source']}", "timeframes": timeframes(levels["target_item"])},
            {"kind": "support", "price": levels["support"], "label": f"S1 {levels['support_item']['source']}", "timeframes": timeframes(levels["support_item"])},
            {"kind": "invalidation", "price": levels["invalidation"], "label": f"D1 {levels['invalid_item']['source']}", "timeframes": timeframes(levels["invalid_item"])},
        ],
        "scenarios": scenarios,
        "path_boundaries": {"upper": levels["upper"], "support": levels["support"], "invalidation": levels["invalidation"], "horizon": 20},
        "forecast_terminal_states": forecasts,
        "forecast_trajectory_signature": f"{top}->{forecasts['t5']}@T5->{forecasts['t10']}@T10->{forecasts['t20']}@T20",
        "base_close": round(close, 6),
        "level_sources": {key: {"timeframe": tf_name.get(value.get('timeframe'), value.get('timeframe')), "source": value.get("source")} for key, value in (("U1", levels["upper_item"]), ("U2", levels["target_item"]), ("S1", levels["support_item"]), ("D1", levels["invalid_item"]))},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ts_code": output["ts_code"], "as_of": as_of, "regime": regime, "top_path": top, "weights": weights}, ensure_ascii=False))


if __name__ == "__main__":
    main()
