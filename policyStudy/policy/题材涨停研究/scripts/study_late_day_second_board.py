"""首板后的次日14:30观察近涨停股票，14:31买入代理与次日退出检验。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from coreClient.data_provider import get_day, get_oneMin, get_stk_limit, get_tradecal
from study_early_high_board_one_min import _next_day_exit


def run(source_path: Path, start_date: str, end_date: str,
        label_end: str) -> tuple[pd.DataFrame, dict]:
    source = pd.read_csv(source_path, dtype={"dminus1": str, "d0": str, "ts_code": str})
    source = source[source.d0.between(start_date, end_date)].copy()
    codes = sorted(source.ts_code.unique())
    day = get_day(ts_codes=codes, start_date=source.dminus1.min(), end_date=end_date,
                  qfq=False, source="database_only")
    day.trade_date = day.trade_date.astype(str)
    close = {(r.trade_date, r.ts_code): float(r.close) for r in day.itertuples()}
    limits = get_stk_limit(start_date=start_date, end_date=label_end,
                           source="database_only")
    limits.trade_date = limits.trade_date.astype(str)
    bands = {(r.trade_date, r.ts_code): (float(r.up_limit), float(r.down_limit))
             for r in limits.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    cal = sorted(get_tradecal(start_date=start_date, end_date=label_end,
                              source="database_only").cal_date.astype(str))
    next_date = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}
    rows = []
    for d0, part in source.groupby("d0", sort=True):
        intraday = get_oneMin(ts_codes=sorted(part.ts_code.unique()), trade_date=d0)
        current = {code: x.sort_values("datetime").reset_index(drop=True)
                   for code, x in intraday.groupby("ts_code")}
        entries = []
        for e in part.itertuples():
            row = {"dminus1": e.dminus1, "d0": d0, "ts_code": e.ts_code,
                   "name": e.name, "theme_name": e.theme_name,
                   "prior_theme_heat": e.prior_theme_heat,
                   "prior_theme_width": e.prior_theme_width,
                   "future_four_board": bool(e.future_four_board),
                   "quality_ok": False, "buyable": False}
            x = current.get(e.ts_code)
            up, _ = bands.get((d0, e.ts_code), (None, None))
            previous = close.get((e.dminus1, e.ts_code))
            if x is None or len(x) != 240 or up is None or previous is None:
                rows.append(row)
                continue
            snapshot = x[x.datetime.dt.strftime("%H:%M").eq("14:30")]
            entry = x[x.datetime.dt.strftime("%H:%M").eq("14:31")]
            if len(snapshot) != 1 or len(entry) != 1:
                rows.append(row)
                continue
            b, ex = snapshot.iloc[0], entry.iloc[0]
            row.update({"quality_ok": True,
                        "snapshot_return_pct": 100 * (b.close / previous - 1),
                        "snapshot_to_limit_pct": 100 * (b.close / up - 1),
                        "snapshot_body_pct": 100 * (b.close / b.open - 1),
                        "d0_closed_limit": bool(x.iloc[-1].close >= up - .005)})
            near_limit = b.close >= up * .98 and b.close < up - .005
            buyable = bool(near_limit and ex.vol > 0 and ex.open < up - .005 and
                           ex.open > 0 and ex.open <= b.close * 1.03)
            row["buyable"] = buyable
            if buyable:
                row["entry_price"] = float(ex.open)
                entries.append(row)
            rows.append(row)
        d1 = next_date.get(d0)
        if not d1 or not entries:
            continue
        future = get_oneMin(ts_codes=[e["ts_code"] for e in entries], trade_date=d1)
        lookup = {code: x.sort_values("datetime") for code, x in future.groupby("ts_code")}
        for e in entries:
            _, lower = bands.get((d1, e["ts_code"]), (None, None))
            if lower is None:
                continue
            e.update(_next_day_exit(lookup.get(e["ts_code"], pd.DataFrame()),
                                    e["entry_price"], lower))
    frame = pd.DataFrame(rows)
    eligible = frame[frame.quality_ok]
    trades = eligible[eligible.buyable]
    scored = trades[trades.net.notna()]
    audit = {"period": f"{start_date}-{end_date}", "candidate": len(source),
             "quality_ok": len(eligible), "near_limit_and_buyable": len(trades),
             "future_four_board": int(trades.future_four_board.sum()),
             "d0_closed_limit": int(trades.d0_closed_limit.sum()),
             "scored": len(scored), "net_mean_pct": 100 * scored.net.mean(),
             "win_pct": 100 * scored.net.gt(0).mean(),
             "worst_pct": 100 * scored.net.min(),
             "unresolved": int(trades.net.isna().sum()),
             "rule": "D-1首板; D0 14:30价在涨停价98%—涨停价以下; 14:31开盘低于涨停价; D1一分钟±3%或14:45退出",
             "caveat": "盘中识别与成交均为分钟代理；已看过2026其他探索结果，此规则不是封存样本预注册策略"}
    return frame, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="outputs/fifteen_min_early_high_board_2026/events.csv")
    parser.add_argument("--start-date", default="20260601")
    parser.add_argument("--end-date", default="20260914")
    parser.add_argument("--label-end", default="20260924")
    parser.add_argument("--output", default="outputs/one_min_late_second_board_2026")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    frame, audit = run(Path(args.source), args.start_date, args.end_date,
                       args.label_end)
    frame.to_csv(out / "events.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
