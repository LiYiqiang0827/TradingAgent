"""Audit two clock-valid, daily-price entry proxies after the fourth board.

This does not assert live fills. A non-limit open is only a necessary condition;
daily OHLC cannot establish order priority or a stop fill. Costs are a flat
0.31% round trip. The D1 five-board status is outcome-only, never a signal.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_stk_limit


COST = .0031


def summary(frame: pd.DataFrame, field: str) -> dict:
    clean = frame[field].dropna().astype(float)
    return {
        "count": len(clean), "mean_pct": round(float(clean.mean()), 3) if len(clean) else None,
        "median_pct": round(float(clean.median()), 3) if len(clean) else None,
        "win_pct": round(float(100 * clean.gt(0).mean()), 2) if len(clean) else None,
        "worst_pct": round(float(clean.min()), 3) if len(clean) else None,
    }


def run(source: Path, end_date: str) -> tuple[pd.DataFrame, dict]:
    events = pd.read_csv(source, dtype={"ts_code": str, "d0": str, "d1": str})
    codes = events.ts_code.unique().tolist()
    qfq = get_day(ts_codes=codes, start_date="20260101", end_date=end_date,
                  qfq=True, source="database_only")
    raw = get_day(ts_codes=codes, start_date="20260101", end_date=end_date,
                  qfq=False, source="database_only")
    limits = get_stk_limit(ts_codes=codes, start_date="20260101", end_date=end_date,
                           source="database_only")
    calendar = get_day(ts_code="000001.SZ", start_date="20260101", end_date=end_date,
                       qfq=False, source="database_only")
    days = sorted(calendar.trade_date.astype(str).unique())
    indices = {date: i for i, date in enumerate(days)}
    for frame in (qfq, raw, limits):
        frame["trade_date"] = frame.trade_date.astype(str)
    q = qfq.set_index(["ts_code", "trade_date"])
    r = raw.set_index(["ts_code", "trade_date"])
    l = limits.set_index(["ts_code", "trade_date"])

    rows = []
    for event in events.itertuples():
        i = indices.get(event.d0)
        if i is None:
            continue
        future = days[i + 1:i + 11]
        rec = {"ts_code": event.ts_code, "d0": event.d0,
               "d1_direct_five_outcome": bool(event.d1_direct_five),
               "broken_d1_close_holds": bool(not event.d1_direct_five and event.d1_close_pct >= 0),
               "broken_d1_holds_with_two_theme_boards": bool(
                   not event.d1_direct_five and event.d1_close_pct >= 0
                   and event.theme_width_d1 >= 2),
               "d1_open_below_up_limit": None, "d1_open_to_d2_open_net_pct": np.nan,
               "d1_open_to_d5_close_net_pct": np.nan,
               "d2_open_below_up_limit": None, "d2_open_to_d5_close_net_pct": np.nan,
               "d2_open_to_d10_close_net_pct": np.nan,
               "d2_open_to_d10_stop8_net_pct": np.nan,
               "stop8_exit_reason": None, "stop8_locked_down_observed": False}
        code = event.ts_code
        try:
            if len(future) >= 5:
                d1, d2, d5 = future[0], future[1], future[4]
                open1 = float(q.loc[(code, d1)].open)
                rec["d1_open_below_up_limit"] = bool(
                    float(r.loc[(code, d1)].open) < float(l.loc[(code, d1)].up_limit) - .005)
                rec["d1_open_to_d2_open_net_pct"] = 100 * (
                    float(q.loc[(code, d2)].open) / open1 - 1 - COST)
                rec["d1_open_to_d5_close_net_pct"] = 100 * (
                    float(q.loc[(code, d5)].close) / open1 - 1 - COST)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
        if not event.d1_direct_five and len(future) >= 10:
            try:
                d2, d5, d10 = future[1], future[4], future[9]
                entry = float(q.loc[(code, d2)].open)
                rec["d2_open_below_up_limit"] = bool(
                    float(r.loc[(code, d2)].open) < float(l.loc[(code, d2)].up_limit) - .005)
                rec["d2_open_to_d5_close_net_pct"] = 100 * (
                    float(q.loc[(code, d5)].close) / entry - 1 - COST)
                rec["d2_open_to_d10_close_net_pct"] = 100 * (
                    float(q.loc[(code, d10)].close) / entry - 1 - COST)
                stop = entry * .92
                exit_price = float(q.loc[(code, d10)].close)
                reason = "d10_close"
                # T+1: a D2 purchase can first be sold on D3.
                for date in future[2:]:
                    bar = q.loc[(code, date)]
                    if float(bar.open) <= stop:
                        exit_price, reason = float(bar.open), "gap_below_stop"
                    elif float(bar.low) <= stop:
                        exit_price, reason = stop, "stop_touch_optimistic"
                    if reason != "d10_close":
                        if float(r.loc[(code, date)].high) <= float(l.loc[(code, date)].down_limit) + .005:
                            rec["stop8_locked_down_observed"] = True
                        break
                rec["d2_open_to_d10_stop8_net_pct"] = 100 * (exit_price / entry - 1 - COST)
                rec["stop8_exit_reason"] = reason
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                pass
        rows.append(rec)
    frame = pd.DataFrame(rows)
    # The branch is chosen only after D1 closes. It is an exit diagnostic for
    # an existing D1-open purchase, not a D1-open selection filter.
    hold_to_d5 = frame.d1_direct_five_outcome | frame.broken_d1_holds_with_two_theme_boards
    frame["d1_eod_branch_net_pct"] = np.where(
        hold_to_d5, frame.d1_open_to_d5_close_net_pct,
        frame.d1_open_to_d2_open_net_pct)
    d1 = frame[frame.d1_open_below_up_limit.eq(True)]
    broken = frame[frame.d2_open_below_up_limit.eq(True)]
    chosen = broken[broken.broken_d1_holds_with_two_theme_boards]
    audit = {
        "source_events": len(events), "day_last_date": days[-1], "cost_round_trip_pct": .31,
        "d1_open_all_four_boards": {"eligible_proxy": len(d1),
            "d2_open": summary(d1, "d1_open_to_d2_open_net_pct"),
            "d5_close": summary(d1, "d1_open_to_d5_close_net_pct"),
            "d1_eod_exit_branch": summary(d1, "d1_eod_branch_net_pct")},
        "d2_open_after_broken_board": {"eligible_proxy": len(broken),
            "d5_close": summary(broken, "d2_open_to_d5_close_net_pct"),
            "d10_close": summary(broken, "d2_open_to_d10_close_net_pct")},
        "d2_open_after_d1_holds_and_theme_width_two": {
            "signal_count_before_open_filter": int(frame.broken_d1_holds_with_two_theme_boards.sum()),
            "eligible_proxy": len(chosen),
            "d5_close": summary(chosen, "d2_open_to_d5_close_net_pct"),
            "d10_close": summary(chosen, "d2_open_to_d10_close_net_pct"),
            "d10_stop8_optimistic": summary(chosen, "d2_open_to_d10_stop8_net_pct"),
            "stop8_locked_down_observed": int(chosen.stop8_locked_down_observed.sum()),
        },
    }
    return frame, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="outputs/four_board_paths_2026/four_board_paths.csv")
    parser.add_argument("--end-date", default="20260929")
    parser.add_argument("--output-dir", default="outputs/four_board_paths_2026")
    args = parser.parse_args()
    frame, audit = run(Path(args.source), args.end_date)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "entry_proxies.csv", index=False, encoding="utf-8-sig")
    (output / "entry_proxy_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
