#!/usr/bin/env python3
"""Project simple moving averages under explicit hypothetical closing-price paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_WINDOWS = (5, 10, 20, 60, 120)
DEFAULT_PAIRS = ((5, 10), (5, 20), (10, 20), (20, 60), (60, 120))


def parse_scenario(value: str) -> tuple[str, list[float]]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("Scenario must be NAME:PRICE1,PRICE2,...")
    name, raw_prices = value.split(":", 1)
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError("Scenario name cannot be empty")
    try:
        prices = [float(item.strip()) for item in raw_prices.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid price in scenario {name}") from exc
    if not prices or any(price <= 0 for price in prices):
        raise argparse.ArgumentTypeError("Scenario prices must be positive")
    return name, prices


def parse_windows(value: str) -> tuple[int, ...]:
    try:
        windows = tuple(sorted({int(item.strip()) for item in value.split(",") if item.strip()}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Windows must be comma-separated integers") from exc
    if not windows or any(window < 2 for window in windows):
        raise argparse.ArgumentTypeError("Every MA window must be at least 2")
    return windows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True, help="JSON made by build_kline_packet.py")
    parser.add_argument("--scenario", action="append", type=parse_scenario, required=True)
    parser.add_argument("--windows", type=parse_windows, default=DEFAULT_WINDOWS)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def rounded(value: float) -> float:
    return round(float(value), 6)


def direction(previous: float, current: float, tolerance: float = 1e-10) -> str:
    change = current - previous
    if change > tolerance:
        return "rising"
    if change < -tolerance:
        return "falling"
    return "flat"


def cross_label(previous_diff: float, current_diff: float) -> str | None:
    if previous_diff <= 0 < current_diff:
        return "golden_cross_candidate"
    if previous_diff >= 0 > current_diff:
        return "death_cross_candidate"
    return None


def project_path(history: list[float], assumed: list[float], windows: tuple[int, ...]) -> dict[str, Any]:
    if len(history) < max(windows):
        raise ValueError(f"History has {len(history)} closes; need at least {max(windows)}")

    series = history.copy()
    current_mas = {window: mean(series[-window:]) for window in windows}
    initial = {f"ma{window}": rounded(value) for window, value in current_mas.items()}
    steps: list[dict[str, Any]] = []
    all_crosses: list[dict[str, Any]] = []

    for step_number, future_close in enumerate(assumed, start=1):
        previous_mas = current_mas.copy()
        ma_detail: dict[str, Any] = {}

        for window in windows:
            outgoing_close = series[-window]
            new_ma = previous_mas[window] + (future_close - outgoing_close) / window
            direct_ma = mean((series + [future_close])[-window:])
            if abs(new_ma - direct_ma) > 1e-8:
                raise AssertionError("Deduction formula and direct recomputation disagree")
            current_mas[window] = new_ma
            ma_detail[f"ma{window}"] = {
                "value": rounded(new_ma),
                "previous": rounded(previous_mas[window]),
                "change": rounded(new_ma - previous_mas[window]),
                "direction": direction(previous_mas[window], new_ma),
                "deduction_price": rounded(outgoing_close),
                "flat_ma_close": rounded(outgoing_close),
                "assumed_close_minus_deduction": rounded(future_close - outgoing_close),
            }

        step_crosses: list[dict[str, Any]] = []
        available_pairs = [pair for pair in DEFAULT_PAIRS if pair[0] in windows and pair[1] in windows]
        for fast, slow in available_pairs:
            label = cross_label(
                previous_mas[fast] - previous_mas[slow],
                current_mas[fast] - current_mas[slow],
            )
            if label:
                event = {
                    "step": f"T+{step_number}",
                    "pair": f"MA{fast}/MA{slow}",
                    "event": label,
                    "fast": rounded(current_mas[fast]),
                    "slow": rounded(current_mas[slow]),
                }
                step_crosses.append(event)
                all_crosses.append(event)

        steps.append(
            {
                "step": f"T+{step_number}",
                "assumed_close": rounded(future_close),
                "moving_averages": ma_detail,
                "crosses": step_crosses,
            }
        )
        series.append(future_close)

    return {"initial_moving_averages": initial, "steps": steps, "crosses": all_crosses}


def main() -> None:
    args = parse_args()
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    history = [float(row["close"]) for row in packet.get("daily", []) if row.get("close") is not None]
    if packet.get("available_end") != packet.get("as_of"):
        raise ValueError("Packet is not strictly aligned to its as_of date")

    projections: list[dict[str, Any]] = []
    for name, prices in args.scenario:
        projection = project_path(history, prices, args.windows)
        projections.append({"name": name, "assumed_closes": prices, **projection})

    result = {
        "schema": "a_share_ma_scenario_projection.v1",
        "ts_code": packet.get("ts_code"),
        "as_of": packet.get("as_of"),
        "price_basis": packet.get("price_basis"),
        "formula": "MA_N(t+1)=MA_N(t)+(C(t+1)-C(t-N+1))/N",
        "windows": list(args.windows),
        "note": "Every result is conditional on the supplied hypothetical closes; cross events are candidates, not forecasts.",
        "scenarios": projections,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "ts_code": result["ts_code"],
                "as_of": result["as_of"],
                "scenarios": len(projections),
                "max_horizon": max(len(item["assumed_closes"]) for item in projections),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
