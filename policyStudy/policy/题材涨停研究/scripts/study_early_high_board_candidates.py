"""高板回溯：首板后第一个15分钟能否提前识别未来四连板。

四连板只用于事后标签；D-1首板、题材背景和D0 09:45的OHLC/量价
产生选择特征，10:00开盘作成交代理，D+1后才卖出。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from coreClient.data_provider import (get_day, get_fifteenMin, get_kpl_list, get_stk_limit,
                                      get_tradecal)
from study_fifteen_min_theme_restart_strategy import _next_day_rule_exit
from study_fifteen_min_two_board_pullback import _outcome


def _candidate_pool(graph_db: Path, calendar: list[str], start_date: str,
                    candidate_end: str, label_end: str) -> tuple[pd.DataFrame, dict]:
    con = duckdb.connect(str(graph_db), read_only=True)
    try:
        facts = con.execute(
            """SELECT e.trade_date,e.ts_code,e.name,e.tag,e.status_raw,e.board_height,
                      e.lu_time,e.limit_order,e.free_float,r.theme_id,
                      t.canonical_name AS theme_name,
                      COALESCE(x.level1_name,'') AS level1_name
               FROM fact_limit_event e JOIN rel_limit_theme r USING(event_id)
               JOIN dim_theme t USING(theme_id)
               LEFT JOIN dim_theme_taxonomy x USING(theme_id)
               WHERE r.attribution_role='primary'
                 AND e.trade_date BETWEEN ? AND ?""", [start_date, candidate_end]).df()
        daily = con.execute(
            """SELECT d.trade_date,d.theme_id,d.limit_up_count,d.heat_score,
                      t.canonical_name,COALESCE(x.level1_name,'') AS level1_name
               FROM fact_theme_daily d JOIN dim_theme t USING(theme_id)
               LEFT JOIN dim_theme_taxonomy x USING(theme_id)
               WHERE d.trade_date BETWEEN ? AND ?""", [start_date, candidate_end]).df()
    finally:
        con.close()
    facts = facts[(facts.tag == "涨停") &
                  ~facts.theme_name.isin(["ST板块", "ST摘帽", "次新股"]) &
                  facts.level1_name.ne("ST与次新") &
                  ~facts.name.str.contains("ST|退", case=False, na=False) &
                  ~facts.ts_code.str.endswith(".BJ")].copy()
    members = facts.groupby(["trade_date", "theme_id"]).ts_code.agg(
        lambda values: "|".join(sorted(set(values)))).rename("prior_theme_members")
    first = facts[facts.status_raw.eq("首板")].copy()
    next_date = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}
    first["d0"] = first.trade_date.map(next_date)
    first = first[first.d0.notna()].rename(
        columns={"trade_date": "dminus1"})
    first = first.join(members, on=["dminus1", "theme_id"])
    daily.trade_date = daily.trade_date.astype(str)
    daily = daily[(daily.level1_name != "ST与次新") &
                  ~daily.canonical_name.isin(["ST板块", "ST摘帽", "次新股"])].copy()
    daily["strong"] = daily.limit_up_count.ge(3) & daily.heat_score.ge(50)
    daily = daily.sort_values(["trade_date", "strong", "heat_score", "theme_id"],
                              ascending=[True, False, False, True])
    daily["rank"] = daily[daily.strong].groupby("trade_date").cumcount().add(1).reindex(daily.index)
    daily["top3_strong"] = daily.strong & daily["rank"].le(3)
    first = first.merge(daily[["trade_date", "theme_id", "limit_up_count", "heat_score",
                               "rank", "top3_strong"]].rename(columns={"trade_date": "dminus1"}),
                        on=["dminus1", "theme_id"], how="left", validate="many_to_one")
    raw_count = len(first)
    # 保留同一股票日的唯一主归因；发生源异常时按已知题材热度稳定选择。
    first = first.sort_values(["d0", "ts_code", "heat_score"],
                              ascending=[True, True, False]).drop_duplicates(["d0", "ts_code"])
    day = get_day(ts_codes=first.ts_code.unique().tolist(), start_date=start_date,
                  end_date=calendar[-1], qfq=False, source="database_only")
    day.trade_date = day.trade_date.astype(str)
    limit = get_stk_limit(start_date=start_date, end_date=calendar[-1], source="database_only")
    limit.trade_date = limit.trade_date.astype(str)
    band = first[["d0", "ts_code"]].merge(
        day[["trade_date", "ts_code", "pre_close"]].rename(columns={"trade_date": "d0"}),
        on=["d0", "ts_code"], how="left", validate="one_to_one").merge(
        limit[["trade_date", "ts_code", "up_limit"]].rename(columns={"trade_date": "d0"}),
        on=["d0", "ts_code"], how="left", validate="one_to_one")
    first = first.loc[(band.up_limit / band.pre_close).gt(1.075).to_numpy()].copy()
    future = get_kpl_list(start_date=start_date, end_date=label_end, tags=["涨停"],
                          source="database_only")
    future.trade_date = future.trade_date.astype(str)
    fourth = set(future[future.status.eq("4连板")][["trade_date", "ts_code"]]
                 .itertuples(index=False, name=None))
    first["dplus2"] = first.d0.map(lambda d: calendar[calendar.index(d) + 2]
                                    if calendar.index(d) + 2 < len(calendar) else None)
    first["future_four_board"] = first.apply(
        lambda r: (r.dplus2, r.ts_code) in fourth, axis=1)
    audit = {"raw_first_board_primary_rows": raw_count,
             "unique_normal_price_band_candidates": len(first),
             "labeled_four_board": int(first.future_four_board.sum()),
             "label_rule": "D-1 首板, D0 二连板, D+1 三连板, D+2 四连板",
             "lookahead_policy": "future_four_board is label only, never candidate filter"}
    return first, audit


def _peer_snapshot(stock_bars: dict, member_codes: str, code: str,
                   dminus1: str, d0: str, stamp: pd.Timestamp) -> dict:
    changes = []
    for peer in member_codes.split("|"):
        if peer == code:
            continue
        bars = stock_bars.get(peer)
        if bars is None:
            continue
        prior = bars[bars.trade_date == int(dminus1)]
        now = bars[bars.datetime == stamp]
        if len(prior) != 16 or len(now) != 1 or prior.iloc[-1].close <= 0:
            continue
        changes.append(float(now.iloc[0].close / prior.iloc[-1].close - 1))
    if not changes:
        return {"peer_observed": 0, "peer_positive_share": None,
                "peer_at_least_2pct": 0}
    return {"peer_observed": len(changes),
            "peer_positive_share": float(np.mean(np.array(changes) > 0)),
            "peer_at_least_2pct": int(np.sum(np.array(changes) >= .02))}


def run(graph_db: Path, start_date: str, candidate_end: str,
        label_end: str) -> tuple[pd.DataFrame, dict]:
    cal = get_tradecal(start_date=start_date, end_date=label_end, source="database_only")
    calendar = sorted(cal.cal_date.astype(str))
    first, audit = _candidate_pool(graph_db, calendar, start_date, candidate_end, label_end)
    limits = get_stk_limit(start_date=start_date, end_date=label_end, source="database_only")
    limits.trade_date = limits.trade_date.astype(str)
    limit_map = {(r.trade_date, r.ts_code): (float(r.up_limit), float(r.down_limit))
                 for r in limits.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    rows = []
    for _, monthly in first.groupby(first.d0.str.slice(0, 6), sort=True):
        i0 = calendar.index(monthly.d0.min())
        i1 = calendar.index(monthly.d0.max())
        codes = sorted(set(monthly.ts_code) | {
            code for members in monthly.prior_theme_members for code in members.split("|")})
        bars = get_fifteenMin(ts_codes=codes, start_date=calendar[i0 - 1],
                              end_date=calendar[min(i1 + 4, len(calendar) - 1)])
        bars.trade_date = bars.trade_date.astype(int)
        stock_bars = {code: part.sort_values("datetime").reset_index(drop=True)
                      for code, part in bars.groupby("ts_code")}
        counts = bars.groupby(["ts_code", "trade_date"]).size()
        for r in monthly.itertuples():
            stock = stock_bars.get(r.ts_code)
            i = calendar.index(r.d0)
            dates = calendar[i - 1:i + 2]
            quality = stock is not None and len(dates) == 3 and all(
                counts.get((r.ts_code, int(d)), 0) == 16 for d in dates)
            item = {"dminus1": r.dminus1, "d0": r.d0, "ts_code": r.ts_code,
                    "name": r.name, "theme_name": r.theme_name, "theme_id": r.theme_id,
                    "prior_theme_width": r.limit_up_count, "prior_theme_heat": r.heat_score,
                    "prior_theme_rank": r.rank, "prior_top3_strong": bool(r.top3_strong),
                    "prior_theme_members": r.prior_theme_members,
                    "future_four_board": bool(r.future_four_board), "quality_ok": quality}
            if not quality:
                rows.append(item)
                continue
            prior = stock[stock.trade_date == int(r.dminus1)]
            current = stock[stock.trade_date == int(r.d0)]
            firstbar = current.iloc[0]
            secondbar = current.iloc[1]
            previous_close = float(prior.iloc[-1].close)
            upper = limit_map.get((r.d0, r.ts_code), (None, None))[0]
            signal = {"time": str(firstbar.datetime), "close": float(firstbar.close)}
            item.update({"first15_close_pct": 100 * (firstbar.close / previous_close - 1),
                         "first15_open_gap_pct": 100 * (firstbar.open / previous_close - 1),
                         "first15_body_pct": 100 * (firstbar.close / firstbar.open - 1),
                         "first15_volume_ratio": float(firstbar.vol / prior.iloc[0].vol)
                         if prior.iloc[0].vol > 0 else None,
                         "first15_sealed": bool(upper and firstbar.close >= upper - .005),
                         "next15_open_below_limit": bool(upper and secondbar.open < upper - .005)})
            item.update(_peer_snapshot(stock_bars, r.prior_theme_members, r.ts_code,
                                       r.dminus1, r.d0, firstbar.datetime))
            outcome = _outcome(stock, signal, limit_map, calendar)
            next_rule = _next_day_rule_exit(stock, outcome, calendar, limit_map)
            item.update(outcome)
            item.update(next_rule)
            rows.append(item)
    events = pd.DataFrame(rows)
    audit.update({"quality_ok": int(events.quality_ok.sum()),
                  "data_missing": int((~events.quality_ok).sum()),
                  "buyable_at_10": int(events.buyable.eq(True).sum()),
                  "winners_buyable_at_10": int(events.loc[events.future_four_board,
                                                       "buyable"].eq(True).sum())})
    return events, audit


def _summary(frame: pd.DataFrame) -> dict:
    scored = frame[frame.next_rule_net.notna()]
    ret = scored.next_rule_net
    return {"candidates": len(frame), "future_four_board": int(frame.future_four_board.sum()),
            "future_four_rate_pct": round(100 * frame.future_four_board.mean(), 3) if len(frame) else None,
            "buyable": int(frame.buyable.eq(True).sum()), "scored": len(ret),
            "mean_next_rule_net_pct": round(100 * ret.mean(), 3) if len(ret) else None,
            "win_pct": round(100 * ret.gt(0).mean(), 2) if len(ret) else None,
            "worst_pct": round(100 * ret.min(), 3) if len(ret) else None,
            "blocked_exit_count": int(frame.next_rule_blocked_bars.fillna(0).gt(0).sum())}


def summarize(events: pd.DataFrame) -> dict:
    e = events[events.quality_ok].copy()
    e["theme_active"] = e.prior_theme_width.ge(3) & e.prior_theme_heat.ge(50)
    e["first15_positive"] = e.first15_close_pct.between(3, 9.8) & e.first15_body_pct.gt(0)
    e["peer_sync"] = e.peer_observed.ge(2) & e.peer_positive_share.ge(2 / 3) & e.peer_at_least_2pct.ge(1)
    groups = {"all": e, "theme_active": e[e.theme_active],
              "first15_positive": e[e.first15_positive],
              "theme_and_first15": e[e.theme_active & e.first15_positive],
              "theme_first15_peer": e[e.theme_active & e.first15_positive & e.peer_sync]}
    return {key: _summary(part) for key, part in groups.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-db", default="offlineDataManager/data/db_theme_graph.duckdb")
    parser.add_argument("--output", default="outputs/fifteen_min_early_high_board_2026")
    parser.add_argument("--start-date", default="20260105")
    parser.add_argument("--candidate-end", default="20260914")
    parser.add_argument("--label-end", default="20260924")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    events, audit = run(Path(args.graph_db), args.start_date, args.candidate_end,
                        args.label_end)
    result = summarize(events)
    events.to_csv(out / "events.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audit": audit, "results": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
