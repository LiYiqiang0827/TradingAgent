"""Evaluate a fixed intraday loss stop after a high-board close, without filters.

This is a descriptive price-path proxy, not an executable order simulation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from coreClient.data_provider import get_day


HORIZONS = (2, 3, 4, 5)


def score(events: pd.DataFrame, last_day: str, stop_pct: float) -> pd.DataFrame:
    events = events.copy()
    events["threshold_or_first_date"] = events.threshold_or_first_date.astype(str)
    first_date = events.threshold_or_first_date.min()
    benchmark = get_day(
        ts_code="000001.SZ", start_date=first_date, end_date=last_day,
        qfq=False, source="database_only",
    )
    dates = sorted(benchmark.trade_date.astype(str).unique().tolist())
    if not dates:
        raise RuntimeError("No offline trading-day grid")
    date_index = {date: index for index, date in enumerate(dates)}
    day = get_day(
        ts_codes=events.ts_code.unique().tolist(), start_date=first_date,
        end_date=last_day, qfq=True, source="database_only",
    )
    day["trade_date"] = day.trade_date.astype(str)
    price = day.set_index(["ts_code", "trade_date"])[["open", "low", "close"]].to_dict("index")
    rows = []
    max_horizon = max(HORIZONS)
    for event in events.itertuples(index=False):
        anchor_date = event.threshold_or_first_date
        anchor_idx = date_index.get(anchor_date)
        anchor = price.get((event.ts_code, anchor_date))
        entry = float(anchor["close"]) if anchor is not None else None
        stop_price = entry * (1 - stop_pct / 100) if entry is not None else None
        stopped_at = None
        stop_fill = None
        fill_kind = None
        path_missing = anchor_idx is None or entry is None
        for horizon in range(1, max_horizon + 1):
            target_idx = anchor_idx + horizon if anchor_idx is not None else len(dates)
            target_date = dates[target_idx] if target_idx < len(dates) else ""
            bar = price.get((event.ts_code, target_date)) if target_date else None
            if target_date and stopped_at is None and not path_missing:
                if bar is None or any(pd.isna(bar[k]) for k in ("open", "low", "close")):
                    path_missing = True
                elif float(bar["open"]) <= stop_price:
                    stopped_at = horizon
                    stop_fill = float(bar["open"])
                    fill_kind = "gap_open"
                elif float(bar["low"]) <= stop_price:
                    stopped_at = horizon
                    stop_fill = stop_price
                    fill_kind = "intraday_touch"
            if horizon not in HORIZONS:
                continue
            if not target_date:
                status = "right_censored"
            elif path_missing:
                status = "missing_path_quote"
            elif stopped_at is not None:
                status = "observed"
            elif bar is None:
                status = "missing_path_quote"
            else:
                status = "observed"
            exit_price = stop_fill if stopped_at is not None else (
                float(bar["close"]) if bar is not None and status == "observed" else None
            )
            rows.append({
                "ts_code": event.ts_code,
                "name": event.name,
                "run_number": event.run_number,
                "anchor_date": anchor_date,
                "target_date": target_date,
                "horizon_trading_days": horizon,
                "status": status,
                "entry_close_qfq": entry,
                "stop_level_qfq": stop_price,
                "stop_triggered_by_horizon": bool(stopped_at is not None and status == "observed"),
                "stop_day_after_anchor": stopped_at if status == "observed" else None,
                "fill_kind": fill_kind if status == "observed" else None,
                "exit_price_qfq": exit_price if status == "observed" else None,
                "return_pct": round((exit_price / entry - 1) * 100, 6)
                if status == "observed" else None,
            })
    return pd.DataFrame(rows)


def summarize(samples: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for horizon, group in samples.groupby("horizon_trading_days", sort=True):
        observed = group[group.status == "observed"]
        returns = observed.return_pct.astype(float)
        rows.append({
            "horizon_trading_days": int(horizon),
            "event_count": int(len(group)),
            "observed_count": int(len(observed)),
            "right_censored_count": int((group.status == "right_censored").sum()),
            "missing_path_quote_count": int((group.status == "missing_path_quote").sum()),
            "stop_triggered_count": int(observed.stop_triggered_by_horizon.sum()),
            "gap_open_stop_count": int((observed.fill_kind == "gap_open").sum()),
            "mean_return_pct": round(float(returns.mean()), 4) if len(returns) else None,
            "median_return_pct": round(float(returns.median()), 4) if len(returns) else None,
            "positive_rate_pct": round(float((returns > 0).mean() * 100), 2)
            if len(returns) else None,
            "worst_return_pct": round(float(returns.min()), 4) if len(returns) else None,
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board-threshold", type=int, default=4)
    parser.add_argument("--stop-pct", type=float, default=10.0)
    parser.add_argument("--input-dir", type=Path)
    args = parser.parse_args()
    if not 0 < args.stop_pct < 100:
        parser.error("--stop-pct must be between 0 and 100")
    directory = args.input_dir or Path(
        f"outputs/{args.board_threshold}_board_followthrough_2026"
    )
    audit = json.loads((directory / "audit.json").read_text(encoding="utf-8"))
    if audit["board_threshold"] != args.board_threshold:
        raise ValueError("Input board threshold disagrees with --board-threshold")
    events = pd.read_csv(directory / "events.csv", dtype={"threshold_or_first_date": str})
    events = events[events.first_height == args.board_threshold]
    samples = score(events, audit["day_last_date"], args.stop_pct)
    summary = summarize(samples)
    suffix = f"stop_{args.stop_pct:g}pct"
    samples.to_csv(directory / f"{suffix}_samples.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(directory / f"{suffix}_summary.csv", index=False, encoding="utf-8-sig")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
