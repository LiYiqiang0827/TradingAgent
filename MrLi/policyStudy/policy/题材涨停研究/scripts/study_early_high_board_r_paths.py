"""Evaluate predeclared next-session risk/reward paths for 09:36 buyable candidates.

This is an exit diagnostic, not a new entry signal.  The candidate CSV was
created using only D-1 and D0 09:35 information; D+1 bars are outcomes only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_oneMin, get_stk_limit, get_tradecal


ROUND_TRIP_COST = .0031
STOP_PCT = .03
TARGETS = (.03, .06, .09)


def _simulate(bars: pd.DataFrame, entry: float, down_limit: float,
              take_pct: float) -> dict:
    """At each close set the exit decision; fill at the next tradable open."""
    base = {"net": None, "reason": "incomplete", "blocked": 0,
            "exit_time": None, "first_hit_time": None}
    if len(bars) != 240 or not np.isfinite(entry) or not np.isfinite(down_limit):
        return base
    decision = None
    reason = None
    for row in bars.itertuples():
        if row.close >= entry * (1 + take_pct):
            decision, reason = row.datetime, "take"
        elif row.close <= entry * (1 - STOP_PCT):
            decision, reason = row.datetime, "cut"
        elif row.datetime.strftime("%H:%M") >= "14:45":
            decision, reason = row.datetime, "time"
        if decision is not None:
            break
    blocked = 0
    for row in bars[bars.datetime > decision].itertuples():
        if row.vol <= 0 or row.open <= down_limit + .005:
            blocked += 1
            continue
        return {"net": float(row.open / entry - 1 - ROUND_TRIP_COST),
                "reason": reason, "blocked": blocked,
                "exit_time": str(row.datetime), "first_hit_time": str(decision)}
    return {**base, "reason": "blocked_or_no_next_bar", "blocked": blocked,
            "first_hit_time": str(decision)}


def run(source_path: Path, label_end: str) -> tuple[pd.DataFrame, dict]:
    source = pd.read_csv(source_path, dtype={"d0": str, "ts_code": str})
    source = source[source.buyable_936.eq(True)].copy()
    start = str(source.d0.min())
    calendar = sorted(get_tradecal(start_date=start, end_date=label_end,
                                   source="database_only").cal_date.astype(str))
    next_date = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}
    limits = get_stk_limit(start_date=start, end_date=label_end,
                           source="database_only")
    limits.trade_date = limits.trade_date.astype(str)
    down = {(r.trade_date, r.ts_code): float(r.down_limit)
            for r in limits.itertuples() if pd.notna(r.down_limit)}

    rows = []
    for d0, group in source.groupby("d0", sort=True):
        d1 = next_date.get(d0)
        parts = {}
        if d1:
            day = get_oneMin(ts_codes=sorted(group.ts_code.unique()), trade_date=d1)
            parts = {code: part.sort_values("datetime")
                     for code, part in day.groupby("ts_code")}
        for event in group.itertuples():
            bars = parts.get(event.ts_code, pd.DataFrame())
            entry = float(event.entry_price)
            lo = down.get((d1, event.ts_code), float("nan"))
            record = {"d0": d0, "d1": d1, "ts_code": event.ts_code,
                      "theme_name": event.theme_name,
                      "future_four_board": bool(event.future_four_board),
                      "entry_price": entry, "baseline_net": event.net,
                      "full_d1": len(bars) == 240}
            if len(bars) == 240:
                record.update({
                    "max_favorable_pct": 100 * (bars.high.max() / entry - 1),
                    "max_adverse_pct": 100 * (bars.low.min() / entry - 1),
                    "d1_open_pct": 100 * (bars.iloc[0].open / entry - 1),
                    "d1_close_pct": 100 * (bars.iloc[-1].close / entry - 1),
                })
            for take in TARGETS:
                tag = f"r{round(take / STOP_PCT)}"
                outcome = _simulate(bars, entry, lo, take)
                record.update({f"{tag}_{key}": value for key, value in outcome.items()})
            rows.append(record)
    frame = pd.DataFrame(rows)
    comparable = frame[frame.baseline_net.notna() & frame.r1_net.notna()]
    mismatch = int((comparable.baseline_net - comparable.r1_net).abs().gt(1e-10).sum())
    if mismatch:
        raise AssertionError(f"R1 baseline reproduction differs for {mismatch} rows")
    audit = {"source": str(source_path), "buyable": len(frame),
             "full_d1": int(frame.full_d1.sum()), "baseline_reproduced": len(comparable),
             "baseline_mismatch": mismatch, "stop_pct": 100 * STOP_PCT,
             "cost_pct": 100 * ROUND_TRIP_COST, "date_end": label_end,
             "rules": {"decision": "D+1 minute close crosses target or -3%, otherwise 14:45",
                       "fill": "next tradable minute open, never sell at/below down limit"},
             "targets": {}}
    for take in TARGETS:
        tag = f"r{round(take / STOP_PCT)}"
        scored = frame[frame[f"{tag}_net"].notna()]
        x = scored[f"{tag}_net"]
        audit["targets"][tag] = {
            "take_pct": 100 * take, "scored": len(scored),
            "unresolved": len(frame) - len(scored),
            "mean_net_pct": float(100 * x.mean()),
            "median_net_pct": float(100 * x.median()),
            "win_pct": float(100 * x.gt(0).mean()),
            "worst_pct": float(100 * x.min()),
            "average_win_pct": float(100 * x[x > 0].mean()),
            "average_loss_pct": float(100 * x[x <= 0].mean()),
            "take_count": int(scored[f"{tag}_reason"].eq("take").sum()),
            "cut_count": int(scored[f"{tag}_reason"].eq("cut").sum()),
            "time_count": int(scored[f"{tag}_reason"].eq("time").sum()),
        }
    full = frame[frame.full_d1]
    audit["potential_touch"] = {
        "d1_high_at_least_6pct": int(full.max_favorable_pct.ge(6).sum()),
        "d1_high_at_least_9pct": int(full.max_favorable_pct.ge(9).sum()),
        "d1_low_at_most_minus_3pct": int(full.max_adverse_pct.le(-3).sum()),
        "meaning": "Outcome-only upper bound; high/low touch is not a fill or prior knowledge",
    }
    return frame, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--label-end", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    frame, audit = run(Path(args.source), args.label_end)
    frame.to_csv(out / "r_paths.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
