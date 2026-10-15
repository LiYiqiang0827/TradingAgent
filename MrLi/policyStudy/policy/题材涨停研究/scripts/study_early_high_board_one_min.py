"""统一TDX来源的2026年6—9月：首板后次日09:35识别与09:36可成交性诊断。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_oneMin, get_stk_limit, get_tradecal


def _net(entry: float, exit_price: float) -> float:
    return exit_price / entry - 1 - .0031


def _next_day_exit(bars: pd.DataFrame, entry: float, lower: float) -> dict:
    empty = {"net": None, "exit_time": None, "exit_reason": None,
             "blocked_bars": 0, "unresolved": True}
    if len(bars) != 240:
        return empty
    decision = None
    reason = None
    for row in bars.itertuples():
        if row.close >= entry * 1.03:
            decision, reason = row.datetime, "take_3pct"
        elif row.close <= entry * .97:
            decision, reason = row.datetime, "cut_3pct"
        elif row.datetime.strftime("%H:%M") >= "14:45":
            decision, reason = row.datetime, "next_day_time"
        if decision is not None:
            break
    blocked = 0
    for row in bars[bars.datetime > decision].itertuples():
        if row.vol <= 0 or row.open <= lower + .005:
            blocked += 1
            continue
        return {"net": _net(entry, float(row.open)), "exit_time": str(row.datetime),
                "exit_reason": reason, "blocked_bars": blocked, "unresolved": False}
    return {**empty, "blocked_bars": blocked}


def _first_tradable_open(bars: pd.DataFrame, entry: float, lower: float) -> dict:
    if len(bars) != 240:
        return {"invalidation_net": None, "invalidation_exit_time": None,
                "invalidation_blocked_bars": 0, "invalidation_unresolved": True}
    blocked = 0
    for row in bars.itertuples():
        if row.vol <= 0 or row.open <= lower + .005:
            blocked += 1
            continue
        return {"invalidation_net": _net(entry, float(row.open)),
                "invalidation_exit_time": str(row.datetime),
                "invalidation_blocked_bars": blocked,
                "invalidation_unresolved": False}
    return {"invalidation_net": None, "invalidation_exit_time": None,
            "invalidation_blocked_bars": blocked, "invalidation_unresolved": True}


def run(source_path: Path, start_date: str, end_date: str,
        label_end: str) -> tuple[pd.DataFrame, dict]:
    source = pd.read_csv(source_path, dtype={"dminus1": str, "d0": str, "ts_code": str})
    # 一分钟样本单独审计质量，不能用将来的15分钟完整性预筛候选。
    source = source[source.d0.between(start_date, end_date)].copy()
    codes = sorted(source.ts_code.unique())
    day = get_day(ts_codes=codes, start_date=source.dminus1.min(), end_date=end_date,
                  qfq=False, source="database_only")
    day.trade_date = day.trade_date.astype(str)
    prev_close = {(r.trade_date, r.ts_code): float(r.close) for r in day.itertuples()}
    limits = get_stk_limit(start_date=start_date, end_date=label_end,
                           source="database_only")
    limits.trade_date = limits.trade_date.astype(str)
    limit_map = {(r.trade_date, r.ts_code): (float(r.up_limit), float(r.down_limit))
                 for r in limits.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    calendar = sorted(get_tradecal(start_date=start_date, end_date=label_end,
                                   source="database_only").cal_date.astype(str))
    next_date = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}
    rows = []
    for d0, part in source.groupby("d0", sort=True):
        wanted = sorted(part.ts_code.unique())
        intraday = get_oneMin(ts_codes=wanted, trade_date=d0)
        intraday.trade_date = intraday.trade_date.astype(int)
        current = {code: x.sort_values("datetime") for code, x in intraday.groupby("ts_code")}
        buys = []
        for e in part.itertuples():
            x = current.get(e.ts_code)
            base = {"dminus1": e.dminus1, "d0": d0, "ts_code": e.ts_code,
                    "name": e.name, "theme_name": e.theme_name,
                    "prior_theme_heat": e.prior_theme_heat,
                    "prior_theme_width": e.prior_theme_width,
                    "future_four_board": bool(e.future_four_board),
                    "quality_ok": False, "buyable_936": False}
            if x is None or len(x) != 240:
                rows.append(base)
                continue
            first = x.iloc[:5]
            entry_bar = x.iloc[5]
            prior = prev_close.get((e.dminus1, e.ts_code))
            up, _ = limit_map.get((d0, e.ts_code), (None, None))
            if (prior is None or up is None or
                    [t.strftime("%H:%M") for t in x.iloc[:6].datetime] !=
                    ["09:31", "09:32", "09:33", "09:34", "09:35", "09:36"]):
                rows.append(base)
                continue
            base.update({"quality_ok": True,
                         "close_935_pct": 100 * (first.iloc[-1].close / prior - 1),
                         "open_gap_pct": 100 * (first.iloc[0].open / prior - 1),
                         "first5_body_pct": 100 * (first.iloc[-1].close / first.iloc[0].open - 1),
                         "last3_gain_pct": 100 * (first.iloc[-1].close / first.iloc[1].close - 1),
                         "close_location": (first.iloc[-1].close - first.low.min()) /
                         max(first.high.max() - first.low.min(), .001),
                         "sealed_935": bool(first.iloc[-1].close >= up - .005),
                         "d0_closed_limit": bool(x.iloc[-1].close >= up - .005)})
            buyable = bool(entry_bar.vol > 0 and entry_bar.open < up - .005 and
                           entry_bar.open > 0 and entry_bar.open <= first.iloc[-1].close * 1.03)
            base["buyable_936"] = buyable
            if buyable:
                base["entry_price"] = float(entry_bar.open)
                buys.append(base)
            rows.append(base)
        d1 = next_date.get(d0)
        if not d1 or not buys:
            continue
        next_bars = get_oneMin(ts_codes=[b["ts_code"] for b in buys], trade_date=d1)
        next_parts = {code: x.sort_values("datetime") for code, x in next_bars.groupby("ts_code")}
        for b in buys:
            _, down = limit_map.get((d1, b["ts_code"]), (None, None))
            if down is None:
                continue
            result = _next_day_exit(next_parts.get(b["ts_code"], pd.DataFrame()),
                                    b["entry_price"], down)
            b.update(result)
            if b["d0_closed_limit"]:
                b.update({"invalidation_net": result["net"],
                          "invalidation_exit_time": result["exit_time"],
                          "invalidation_blocked_bars": result["blocked_bars"],
                          "invalidation_unresolved": result["unresolved"],
                          "invalidation_reason": "held_after_second_board"})
            else:
                b.update(_first_tradable_open(next_parts.get(b["ts_code"], pd.DataFrame()),
                                              b["entry_price"], down))
                b["invalidation_reason"] = "failed_second_board_next_open"
    frame = pd.DataFrame(rows)
    good = frame[frame.quality_ok]
    buy = good[good.buyable_936]
    scored = buy[buy.net.notna()]
    invalidation_scored = buy[buy.invalidation_net.notna()]
    audit = {"period": f"{start_date}-{end_date}",
             "source": "oneMin archive, 240 bars/day; verify catalog per date",
             "candidate": len(source), "quality_ok": len(good),
             "four_board": int(good.future_four_board.sum()),
             "sealed_at_935_four_board": int(good.loc[good.future_four_board, "sealed_935"].sum()),
             "buyable_936": len(buy),
             "four_board_buyable_936": int(buy.future_four_board.sum()),
             "scored": len(scored), "mean_net_pct": 100 * scored.net.mean(),
             "win_pct": 100 * scored.net.gt(0).mean(),
             "worst_pct": 100 * scored.net.min(),
             "exit_unresolved": int((buy.unresolved.eq(True) | buy.unresolved.isna()).sum()),
             "invalidation_scored": len(invalidation_scored),
             "invalidation_mean_net_pct": 100 * invalidation_scored.invalidation_net.mean(),
             "invalidation_win_pct": 100 * invalidation_scored.invalidation_net.gt(0).mean(),
             "invalidation_worst_pct": 100 * invalidation_scored.invalidation_net.min(),
             "invalidation_unresolved": int((buy.invalidation_unresolved.eq(True) |
                                             buy.invalidation_unresolved.isna()).sum()),
             "caveat": "09:36开盘与次日一分钟成交仍是代理，排队和滑点不可验证"}
    return frame, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="outputs/fifteen_min_early_high_board_2026/events.csv")
    parser.add_argument("--start-date", default="20260601")
    parser.add_argument("--end-date", default="20260914")
    parser.add_argument("--label-end", default="20260924")
    parser.add_argument("--output", default="outputs/one_min_early_high_board_2026")
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
