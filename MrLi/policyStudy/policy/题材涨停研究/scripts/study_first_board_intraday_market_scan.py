"""全市场09:35近涨停扫描：在首板当日而非次日寻找早期可买窗口。

候选从当时已知的上限价和09:35一分钟价格形成；当天KPL首板与未来四板
只作事后标签。排除前一日已经涨停的股票，避免把二板当首板。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from coreClient.data_provider import (get_day, get_kpl_list, get_oneMin,
                                      get_stk_limit, get_tradecal)
from study_early_high_board_one_min import _next_day_exit


def run(start_date: str, end_date: str, label_end: str) -> tuple[pd.DataFrame, dict]:
    cal = sorted(get_tradecal(start_date=(pd.Timestamp(start_date) -
                                         pd.Timedelta(days=10)).strftime("%Y%m%d"),
                              end_date=label_end,
                              source="database_only").cal_date.astype(str))
    prior = {cal[i]: cal[i - 1] for i in range(1, len(cal))}
    next_date = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    plus3 = {cal[i]: cal[i + 3] for i in range(len(cal) - 3)}
    all_kpl = get_kpl_list(start_date=min(prior.values()), end_date=label_end,
                           tags=["涨停"], source="database_only")
    all_kpl.trade_date = all_kpl.trade_date.astype(str)
    day_board = {(r.trade_date, r.ts_code) for r in all_kpl.itertuples()}
    first_board = {(r.trade_date, r.ts_code) for r in all_kpl.itertuples()
                   if r.status == "首板"}
    fourth_board = {(r.trade_date, r.ts_code) for r in all_kpl.itertuples()
                    if r.status == "4连板"}
    limits = get_stk_limit(start_date=start_date, end_date=label_end,
                           source="database_only")
    limits.trade_date = limits.trade_date.astype(str)
    limit_by_day = {d: x.set_index("ts_code")[["up_limit", "down_limit"]]
                    for d, x in limits.groupby("trade_date")}
    rows = []
    for d0 in [d for d in cal if start_date <= d <= end_date]:
        dminus1 = prior[d0]
        prev = get_day(trade_date=dminus1, qfq=False, source="database_only")
        prev = prev[["ts_code", "close"]].rename(columns={"close": "preclose"})
        bars = get_oneMin(trade_date=d0,
                          columns=["ts_code", "name", "trade_date", "datetime",
                                   "time_idx", "open", "high", "low", "close", "vol"])
        snap = bars[bars.time_idx.eq(4)][["ts_code", "name", "open", "high", "low",
                                          "close", "vol"]].rename(
            columns={"open": "open_935", "high": "high_935", "low": "low_935",
                     "close": "close_935", "vol": "vol_935"})
        entry = bars[bars.time_idx.eq(5)][["ts_code", "open", "vol"]].rename(
            columns={"open": "entry_price", "vol": "entry_vol"})
        end = bars[bars.time_idx.eq(239)][["ts_code", "close"]].rename(
            columns={"close": "d0_close"})
        curr = snap.merge(entry, on="ts_code", validate="one_to_one").merge(
            end, on="ts_code", validate="one_to_one").merge(
            prev, on="ts_code", how="left", validate="one_to_one").join(
            limit_by_day[d0], on="ts_code", how="left")
        curr = curr[curr.ts_code.str.endswith((".SH", ".SZ")) &
                    ~curr.name.str.contains("ST|退", case=False, na=False) &
                    (curr.up_limit / curr.preclose).gt(1.075) &
                    curr.close_935.ge(curr.up_limit * .97) &
                    curr.close_935.lt(curr.up_limit - .005) &
                    curr.entry_price.lt(curr.up_limit - .005) &
                    curr.entry_price.le(curr.close_935 * 1.03) &
                    curr.entry_vol.gt(0)].copy()
        curr = curr[~curr.ts_code.map(lambda code: (dminus1, code) in day_board)].copy()
        if curr.empty:
            continue
        d1 = next_date.get(d0)
        lookup = {}
        if d1:
            future = get_oneMin(ts_codes=curr.ts_code.tolist(), trade_date=d1,
                                columns=["ts_code", "datetime", "time_idx", "open",
                                         "high", "low", "close", "vol"])
            lookup = {code: x.sort_values("datetime") for code, x in future.groupby("ts_code")}
        for r in curr.itertuples():
            item = {"d0": d0, "ts_code": r.ts_code, "name": r.name,
                    "preclose": float(r.preclose), "up_limit": float(r.up_limit),
                    "close_935_pct": 100 * (r.close_935 / r.preclose - 1),
                    "gap_935_pct": 100 * (r.open_935 / r.preclose - 1),
                    "entry_price": float(r.entry_price),
                    "d0_closed_limit": bool(r.d0_close >= r.up_limit - .005),
                    "d0_kpl_firstboard": (d0, r.ts_code) in first_board,
                    "future_four_board": (d0, r.ts_code) in first_board and
                    (plus3.get(d0), r.ts_code) in fourth_board}
            lower = limit_by_day[d1].at[r.ts_code, "down_limit"] if (
                d1 and d1 in limit_by_day and r.ts_code in limit_by_day[d1].index) else None
            if lower is not None and pd.notna(lower):
                item.update(_next_day_exit(lookup.get(r.ts_code, pd.DataFrame()),
                                           float(r.entry_price), float(lower)))
            rows.append(item)
    frame = pd.DataFrame(rows)
    scored = frame[frame.net.notna()]
    audit = {"period": f"{start_date}-{end_date}", "near_limit_threshold": 0.97,
             "all_market_screened": True, "preexisting_limit_up_excluded": True,
             "candidates": len(frame), "d0_firstboard": int(frame.d0_kpl_firstboard.sum()),
             "future_four_board": int(frame.future_four_board.sum()),
             "closed_limit": int(frame.d0_closed_limit.sum()),
             "scored": len(scored), "mean_net_pct": 100 * scored.net.mean(),
             "win_pct": 100 * scored.net.gt(0).mean(),
             "worst_pct": 100 * scored.net.min(),
             "unresolved": int(frame.net.isna().sum()),
             "lookahead_policy": "D0 09:35 price and preopen limit create screen; D0 KPL and D0 close are labels only",
             "caveat": "D0 09:36 and D1 one-minute opens are price/volume proxies; no order-book fill proof"}
    return frame, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="20260601")
    parser.add_argument("--end-date", default="20260917")
    parser.add_argument("--label-end", default="20260924")
    parser.add_argument("--output", default="outputs/first_board_intraday_market_scan_2026")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    frame, audit = run(args.start_date, args.end_date, args.label_end)
    frame.to_csv(out / "events.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
