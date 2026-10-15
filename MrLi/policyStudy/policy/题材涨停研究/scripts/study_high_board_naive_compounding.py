"""Mathematically compound observed high-board stop returns; not a portfolio replay."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_day


SOURCES = {
    4: Path("outputs/4_board_followthrough_2026"),
    5: Path("outputs/high_board_stop_comparison_2026/5_board"),
    6: Path("outputs/high_board_stop_comparison_2026/6_board"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial-capital", type=float, default=300000)
    parser.add_argument("--output", type=Path, default=Path(
        "outputs/high_board_stop_comparison_2026/naive_compounding.csv"
    ))
    args = parser.parse_args()
    if args.initial_capital <= 0:
        parser.error("--initial-capital must be positive")

    benchmark = get_day(
        ts_code="000001.SZ", start_date="20260101", end_date="20260928",
        qfq=False, source="database_only",
    )
    dates = sorted(benchmark.trade_date.astype(str).unique().tolist())
    date_index = {date: i for i, date in enumerate(dates)}
    rows = []
    for board, directory in SOURCES.items():
        event_count = int((pd.read_csv(directory / "events.csv").first_height == board).sum())
        samples = pd.read_csv(
            directory / "stop_10pct_samples.csv", dtype={"anchor_date": str, "target_date": str}
        )
        for horizon in (2, 3, 4, 5):
            group = samples[
                (samples.horizon_trading_days == horizon)
                & (samples.status == "observed")
            ].copy()
            group = group.sort_values(["anchor_date", "ts_code", "run_number"])
            returns = group.return_pct.astype(float).to_numpy() / 100
            factor = float(np.exp(np.log1p(returns).sum()))
            entry_idx = group.anchor_date.map(date_index).to_numpy(dtype=int)
            exit_idx = np.array([
                date_index[row.target_date] if pd.isna(row.stop_day_after_anchor)
                else date_index[row.anchor_date] + int(row.stop_day_after_anchor)
                for row in group.itertuples(index=False)
            ])
            max_overlap = max(
                int(((entry_idx <= day) & (exit_idx >= day)).sum())
                for day in range(len(dates))
            )
            rows.append({
                "board_threshold": board,
                "horizon_trading_days": horizon,
                "total_events": event_count,
                "observed_trades": len(group),
                "max_overlapping_trades_inclusive": max_overlap,
                "arithmetic_mean_return_pct": round(float(returns.mean()) * 100, 4),
                "geometric_mean_return_pct": round((factor ** (1 / len(group)) - 1) * 100, 4),
                "mathematical_compound_factor": round(factor, 6),
                "mathematical_final_capital": round(args.initial_capital * factor, 2),
                "initial_capital": args.initial_capital,
            })
    result = pd.DataFrame(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
