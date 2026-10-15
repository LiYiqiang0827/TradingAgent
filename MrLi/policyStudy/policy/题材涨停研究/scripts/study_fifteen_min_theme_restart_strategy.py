"""全2026可见样本：题材涨停候选 + 15分钟二次启动 + T+1动态卖出。

这是一份研究回放，不会自动下单。候选只使用D-1 KPL/题材记录，盘中
信号只在15分钟K线收盘后计算，订单代理价是下一根K线开盘价。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from coreClient.data_provider import (get_day, get_fifteenMin, get_market_theme_review_series,
                                      get_stk_limit, get_theme_daily, get_tradecal)
from study_fifteen_min_two_board_pullback import _find_signals, _net, _outcome, _signal_info


FIRST = "20260106"
LAST = "20260915"


def candidates(graph_db: Path, calendar: list[str]) -> pd.DataFrame:
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
                 AND e.trade_date BETWEEN '20260105' AND '20260914'"""
        ).df()
    finally:
        con.close()
    facts = facts[(facts.tag == "涨停") &
                  ~facts.theme_name.isin(["ST板块", "ST摘帽", "次新股"]) &
                  facts.level1_name.ne("ST与次新") &
                  ~facts.name.str.contains("ST|退", case=False, na=False) &
                  ~facts.ts_code.str.endswith(".BJ")].copy()
    known_members = facts.groupby(["trade_date", "theme_id"]).ts_code.agg(
        lambda codes: "|".join(sorted(set(codes)))).rename("known_members")
    next_day = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}
    prior = facts[facts.status_raw.str.fullmatch(r"首板|[2-9][0-9]*连板", na=False)].copy()
    prior["d0"] = prior.trade_date.map(next_day)
    prior = prior[prior.d0.between(FIRST, LAST)].rename(columns={"trade_date": "dminus1"})
    height = facts.groupby(["trade_date", "theme_id"]).board_height.max().rename("theme_height")
    prior = prior.join(height, on=["dminus1", "theme_id"])
    prior = prior.join(known_members, on=["dminus1", "theme_id"])
    prior["board_stage"] = np.select([prior.board_height.eq(1), prior.board_height.eq(2)],
                                      ["first", "second"], default="third_plus")
    prior["order_float"] = prior.limit_order / prior.free_float.where(prior.free_float > 0)
    prior = prior.sort_values(["dminus1", "theme_id", "board_stage", "board_height",
                               "lu_time", "order_float", "ts_code"],
                              ascending=[True, True, True, False, True, False, True],
                              na_position="last")
    prior = prior[prior.groupby(["dminus1", "theme_id", "board_stage"]).cumcount().eq(0)].copy()
    daily = get_theme_daily(start_date="20260105", end_date="20260914")
    daily.trade_date = daily.trade_date.astype(str)
    daily = daily[(daily.level1_name != "ST与次新") &
                  ~daily.canonical_name.isin(["ST板块", "ST摘帽", "次新股"])].copy()
    daily["eligible"] = daily.limit_up_count.ge(3) & daily.heat_score.ge(50)
    daily = daily.sort_values(["trade_date", "eligible", "heat_score", "theme_id"],
                              ascending=[True, False, False, True])
    daily["rank"] = daily[daily.eligible].groupby("trade_date").cumcount().add(1).reindex(daily.index)
    daily["strong_theme"] = daily.eligible & daily["rank"].le(3)
    prior = prior.merge(daily[["trade_date", "theme_id", "strong_theme", "heat_score",
                               "limit_up_count", "rank"]]
                        .rename(columns={"trade_date": "dminus1"}),
                        on=["dminus1", "theme_id"], how="left", validate="many_to_one")
    prior["strong_theme"] = prior.strong_theme.eq(True)
    # 单票孤立涨停缺乏可观察的题材共振，先不作为题材策略候选。
    prior = prior[prior.limit_up_count.ge(2)].copy()
    market = get_market_theme_review_series(start_date="20260105", end_date="20260914",
                                            top_n=1, leader_count=0)
    market_day = {str(item["trade_date"]): item for item in market}
    prior["market_sentiment"] = prior.dminus1.map(
        lambda d: market_day.get(d, {}).get("theme_sentiment_score"))
    prior["market_structure"] = prior.dminus1.map(
        lambda d: market_day.get(d, {}).get("structure", {}).get("code"))
    # 理论上主归因每个股票日唯一；若源修订出现多条，保留前日热度更强的题材。
    duplicates = int(prior.duplicated(["d0", "ts_code"]).sum())
    prior = prior.sort_values(["d0", "ts_code", "heat_score"],
                              ascending=[True, True, False]).drop_duplicates(["d0", "ts_code"])
    day = get_day(ts_codes=prior.ts_code.unique().tolist(), start_date=FIRST,
                  end_date=LAST, qfq=False, source="database_only")
    day.trade_date = day.trade_date.astype(str)
    limits = get_stk_limit(start_date=FIRST, end_date=LAST, source="database_only")
    limits.trade_date = limits.trade_date.astype(str)
    band = prior[["d0", "ts_code"]].merge(
        day[["trade_date", "ts_code", "pre_close"]].rename(columns={"trade_date": "d0"}),
        on=["d0", "ts_code"], how="left", validate="one_to_one").merge(
        limits[["trade_date", "ts_code", "up_limit"]].rename(columns={"trade_date": "d0"}),
        on=["d0", "ts_code"], how="left", validate="one_to_one")
    prior = prior.loc[(band.up_limit / band.pre_close).gt(1.075).to_numpy()].copy()
    prior.attrs["duplicate_primary_links"] = duplicates
    return prior[["d0", "dminus1", "ts_code", "name", "theme_name", "theme_id",
                  "board_height", "board_stage", "theme_height", "limit_up_count",
                  "strong_theme", "heat_score", "rank", "market_sentiment", "market_structure",
                  "known_members"]]


def _known_peer_breadth(stock_bars: dict[str, pd.DataFrame], known_members: str,
                        leader: str, signal_time: str | None, calendar: list[str]) -> dict:
    """只看D-1已知同题材涨停成员在信号时已收完的15分钟K线。"""
    empty = {"peer_observed": 0, "peer_positive_share": None,
             "peer_max_return_pct": None, "peer_sync": False}
    if signal_time is None or not isinstance(known_members, str):
        return empty
    stamp = pd.Timestamp(signal_time)
    day = stamp.strftime("%Y%m%d")
    if day not in calendar or calendar.index(day) < 1:
        return empty
    previous_day = int(calendar[calendar.index(day) - 1])
    changes = []
    for code in known_members.split("|"):
        if code == leader:
            continue
        bars = stock_bars.get(code)
        if bars is None:
            continue
        now = bars[bars.datetime == stamp]
        previous = bars[bars.trade_date == previous_day]
        if len(now) != 1 or len(previous) != 16:
            continue
        prior = float(previous.iloc[-1].close)
        if prior > 0:
            changes.append(float(now.iloc[0].close) / prior - 1)
    if not changes:
        return empty
    share = float(np.mean(np.array(changes) > 0))
    top = float(np.max(changes))
    return {"peer_observed": len(changes), "peer_positive_share": share,
            "peer_max_return_pct": round(100 * top, 3),
            "peer_sync": len(changes) >= 2 and share >= 2 / 3 and top >= .02}


def _dynamic_exit(bars: pd.DataFrame, signal: dict, baseline: dict,
                  neckline: float | None, trough: float | None,
                  calendar: list[str], limit_map: dict) -> dict:
    """次根开盘进场；T+1后按已收完K线触发，下一根开盘卖出。"""
    empty = {"dynamic_net": None, "dynamic_exit_time": None, "dynamic_exit_reason": None,
             "dynamic_blocked_bars": 0, "dynamic_mae": None, "dynamic_factor_changed": False,
             "dynamic_exit_missing": False}
    if not baseline["buyable"]:
        return empty
    entry_time = pd.Timestamp(baseline["entry_time"])
    entry = float(baseline["entry_open"])
    entry_date = entry_time.strftime("%Y%m%d")
    if entry_date not in calendar:
        return empty
    pos = calendar.index(entry_date)
    if pos + 3 >= len(calendar):
        return empty
    last_date = calendar[pos + 3]
    holding = bars[(bars.datetime >= entry_time) & (bars.trade_date <= int(last_date))].copy()
    if len(holding) == 0:
        return empty
    if holding.adj_factor.max() / holding.adj_factor.min() - 1 > .00001:
        return {**empty, "dynamic_factor_changed": True}
    # 时间退出日14:45收完后，在15:00这根的开盘执行；整根K线先于委托。
    high_close = entry
    decision_time = None
    reason = None
    for row in holding.itertuples():
        high_close = max(high_close, float(row.close))
        if str(row.trade_date) <= entry_date:
            continue  # A股股票T+1；当天的失效信号只能预警，不能平仓。
        if row.close <= entry * .97 or (neckline is not None and row.close <= neckline * .99):
            decision_time, reason = row.datetime, "stop_or_neckline"
        elif high_close >= entry * 1.05 and row.close <= high_close * .97:
            decision_time, reason = row.datetime, "trailing_after_5pct"
        elif str(row.trade_date) == last_date and row.time_idx >= 14:
            decision_time, reason = row.datetime, "day3_time_exit"
        if decision_time is not None:
            break
    if decision_time is None:
        return {**empty, "dynamic_exit_missing": True}
    after = bars[bars.datetime > decision_time]
    blocked = 0
    for row in after.itertuples():
        date = str(row.trade_date)
        # 亏损情形下也可继续到下一交易日，不能删除无法成交的样本。
        lower = limit_map.get((date, row.ts_code), (None, None))[1]
        if row.vol <= 0 or (lower is not None and row.open <= lower + .005):
            blocked += 1
            continue
        if row.adj_factor / holding.iloc[0].adj_factor - 1 > .00001:
            return {**empty, "dynamic_factor_changed": True, "dynamic_blocked_bars": blocked}
        h = bars[(bars.datetime >= entry_time) & (bars.datetime <= row.datetime)]
        return {"dynamic_net": _net(entry, float(row.open)),
                "dynamic_exit_time": str(row.datetime), "dynamic_exit_reason": reason,
                "dynamic_blocked_bars": blocked,
                "dynamic_mae": float(h.low.min() / entry - 1),
                "dynamic_factor_changed": False, "dynamic_exit_missing": False}
    return {**empty, "dynamic_blocked_bars": blocked, "dynamic_exit_missing": True}


def _next_day_rule_exit(bars: pd.DataFrame, baseline: dict,
                        calendar: list[str], limit_map: dict) -> dict:
    """只在买入后下一交易日：+3%止盈/-3%止损，否则14:45后退出。"""
    empty = {"next_rule_net": None, "next_rule_exit_time": None,
             "next_rule_reason": None, "next_rule_blocked_bars": 0,
             "next_rule_mae": None, "next_rule_factor_changed": False}
    if not baseline["buyable"]:
        return empty
    entry_time = pd.Timestamp(baseline["entry_time"])
    entry = float(baseline["entry_open"])
    entry_date = entry_time.strftime("%Y%m%d")
    pos = calendar.index(entry_date)
    if pos + 1 >= len(calendar):
        return empty
    next_date = calendar[pos + 1]
    next_bars = bars[bars.trade_date == int(next_date)]
    if len(next_bars) != 16:
        return empty
    decision = None
    reason = None
    for row in next_bars.itertuples():
        if row.close >= entry * 1.03:
            decision, reason = row.datetime, "take_3pct"
        elif row.close <= entry * .97:
            decision, reason = row.datetime, "cut_3pct"
        elif row.time_idx >= 14:
            decision, reason = row.datetime, "next_day_time"
        if decision is not None:
            break
    blocked = 0
    reference_factor = float(bars[bars.datetime == entry_time].iloc[0].adj_factor)
    for row in bars[bars.datetime > decision].itertuples():
        date = str(row.trade_date)
        lower = limit_map.get((date, row.ts_code), (None, None))[1]
        if row.vol <= 0 or (lower is not None and row.open <= lower + .005):
            blocked += 1
            continue
        if abs(row.adj_factor / reference_factor - 1) > .00001:
            return {**empty, "next_rule_factor_changed": True,
                    "next_rule_blocked_bars": blocked}
        holding = bars[(bars.datetime >= entry_time) & (bars.datetime <= row.datetime)]
        return {"next_rule_net": _net(entry, float(row.open)),
                "next_rule_exit_time": str(row.datetime), "next_rule_reason": reason,
                "next_rule_blocked_bars": blocked,
                "next_rule_mae": float(holding.low.min() / entry - 1),
                "next_rule_factor_changed": False}
    return {**empty, "next_rule_blocked_bars": blocked}


def run(graph_db: Path) -> tuple[pd.DataFrame, dict]:
    cal = get_tradecal(start_date="20260105", end_date="20260924", source="database_only")
    calendar = sorted(cal.cal_date.astype(str).tolist())
    candidate = candidates(graph_db, calendar)
    codes = sorted(candidate.ts_code.unique())
    limits = get_stk_limit(start_date="20260105", end_date="20260924", source="database_only")
    limits.trade_date = limits.trade_date.astype(str)
    limit_map = {(r.trade_date, r.ts_code): (float(r.up_limit), float(r.down_limit))
                 for r in limits.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    rows = []
    bad_bar_days = 0
    first_seen_day = None
    last_seen_day = None
    for month, monthly in candidate.groupby(candidate.d0.str.slice(0, 6), sort=True):
        first_i = calendar.index(monthly.d0.min())
        last_i = calendar.index(monthly.d0.max())
        batch_codes = sorted(set(monthly.ts_code) | {
            code for members in monthly.known_members for code in members.split("|")})
        bars = get_fifteenMin(ts_codes=batch_codes, start_date=calendar[first_i - 1],
                              end_date=calendar[min(last_i + 10, len(calendar) - 1)])
        bars.trade_date = bars.trade_date.astype(int)
        bars = bars.sort_values(["ts_code", "datetime"])
        stock_bars = {code: part.reset_index(drop=True) for code, part in bars.groupby("ts_code")}
        completeness = bars.groupby(["ts_code", "trade_date"]).size()
        bad_bar_days += int(completeness.ne(16).sum())
        first_seen_day = min(first_seen_day or str(bars.trade_date.min()), str(bars.trade_date.min()))
        last_seen_day = max(last_seen_day or str(bars.trade_date.max()), str(bars.trade_date.max()))
        for r in monthly.itertuples():
            stock = stock_bars.get(r.ts_code)
            i = calendar.index(r.d0)
            required = calendar[i - 1:i + 8]
            quality = stock is not None and len(required) == 9 and all(
                completeness.get((r.ts_code, int(d)), 0) == 16 for d in required)
            result = {"d0": r.d0, "dminus1": r.dminus1, "ts_code": r.ts_code,
                      "name": r.name, "theme_name": r.theme_name, "theme_id": r.theme_id,
                      "board_height": int(r.board_height), "board_stage": r.board_stage,
                      "theme_height": int(r.theme_height), "prior_theme_width": int(r.limit_up_count),
                      "known_members": r.known_members,
                      "strong_theme": bool(r.strong_theme), "prior_heat": r.heat_score,
                      "prior_rank": r.rank, "prior_market_sentiment": r.market_sentiment,
                      "prior_market_structure": r.market_structure, "quality_ok": quality}
            if not quality:
                rows.append(result)
                continue
            prior_close = float(stock[stock.trade_date == int(r.dminus1)].iloc[-1].close)
            support, restart, audit = _find_signals(stock, r.d0, prior_close, calendar, 4.0)
            confirmed = _signal_info(stock, None)
            if restart["time"] is not None:
                later = stock[stock.datetime > pd.Timestamp(restart["time"])].head(2)
                if len(later) == 2 and later.close.ge(audit["restart_neckline"]).all() and (
                        later.low.gt(audit["restart_trough"] * .99)).all():
                    confirmed = {"time": str(later.iloc[-1].datetime),
                                 "close": float(later.iloc[-1].close)}
            result.update({"prior_limit_close": prior_close,
                           "support_signal_time": support["time"],
                           "restart_neckline": audit["restart_neckline"],
                           "restart_trough": audit["restart_trough"]})
            for key, signal in (("restart", restart), ("confirmed", confirmed)):
                outcome = _outcome(stock, signal, limit_map, calendar)
                dynamic = _dynamic_exit(stock, signal, outcome, audit["restart_neckline"],
                                        audit["restart_trough"], calendar, limit_map)
                next_rule = _next_day_rule_exit(stock, outcome, calendar, limit_map)
                peer = _known_peer_breadth(stock_bars, r.known_members, r.ts_code,
                                           signal["time"], calendar)
                result[f"{key}_signal_time"] = signal["time"]
                result.update({f"{key}_{k}": v for k, v in
                               {**outcome, **dynamic, **next_rule, **peer}.items()})
            rows.append(result)
    frame = pd.DataFrame(rows)
    audit = {"candidate_count": len(candidate), "candidate_dates": int(candidate.d0.nunique()),
             "qualified_windows": int(frame.quality_ok.sum()),
             "missing_whole_stock_days": int((~frame.quality_ok).sum()),
             "bad_length_stock_days": bad_bar_days,
             "symbols": len(codes), "first_bar_day": first_seen_day,
             "last_bar_day": last_seen_day,
             "duplicate_primary_links": candidate.attrs.get("duplicate_primary_links", 0),
             "candidate_definition": "D-1 primary KPL exact 首板/N连板; one earliest-sealed stock per theme and board stage (highest for >=3); prior theme width>=2; excludes ST, BJ, new stocks, abnormal 5% band",
             "period_warning": "All 2026 months are now seen by rule designer; no untouched holdout remains."}
    return frame, audit


def _one(frame: pd.DataFrame, key: str, ret_col: str) -> dict:
    valid = frame[frame[f"{key}_buyable"].eq(True)]
    ret = valid[ret_col].dropna()
    daily = valid[valid[ret_col].notna()].groupby("d0")[ret_col].mean()
    gross_gain = ret[ret > 0].sum()
    gross_loss = -ret[ret < 0].sum()
    return {"candidates": len(frame), "signals": int(frame[f"{key}_signal_time"].notna().sum()),
            "buyable": len(valid), "scored": len(ret),
            "mean_net_pct": round(100 * ret.mean(), 3) if len(ret) else None,
            "median_net_pct": round(100 * ret.median(), 3) if len(ret) else None,
            "win_pct": round(100 * ret.gt(0).mean(), 2) if len(ret) else None,
            "profit_factor": round(gross_gain / gross_loss, 3) if gross_loss > 0 else None,
            "positive_setup_day_pct": round(100 * daily.gt(0).mean(), 2) if len(daily) else None,
            "setup_days": int(frame.loc[frame[f"{key}_signal_time"].notna(), "d0"].nunique()),
            "blocked_exits": int(valid[f"{key}_dynamic_blocked_bars"].gt(0).sum()) if ret_col.endswith("dynamic_net") else (
                int(valid[f"{key}_next_rule_blocked_bars"].gt(0).sum()) if ret_col.endswith("next_rule_net")
                else int(valid[f"{key}_dplus1_exit_blocked"].eq(True).sum())),
            "unscored": len(valid) - len(ret),
            "mae_below_minus3_pct": round(100 * valid.loc[valid[ret_col].notna(),
                                   f"{key}_{'dynamic_mae' if ret_col.endswith('dynamic_net') else ('next_rule_mae' if ret_col.endswith('next_rule_net') else 'dplus1_mae')}"].lt(-.03).mean(), 2) if len(ret) else None}


def summarize(frame: pd.DataFrame) -> dict:
    frame = frame[frame.quality_ok].copy()
    frame["month"] = frame.d0.str.slice(0, 6)
    groups = {"all": frame,
              "first": frame[frame.board_stage.eq("first")],
              "second": frame[frame.board_stage.eq("second")],
              "third_plus": frame[frame.board_stage.eq("third_plus")],
              "strong_theme": frame[frame.strong_theme],
              "other_theme": frame[~frame.strong_theme],
              "sentiment_ge_50": frame[frame.prior_market_sentiment.ge(50)],
              "mainline_structure": frame[frame.prior_market_structure.isin(
                  ["clear_single_mainline", "multiple_mainlines"])],
              "strong_theme_and_mainline": frame[frame.strong_theme &
                  frame.prior_market_structure.isin(
                      ["clear_single_mainline", "multiple_mainlines"])]}
    for signal in ("restart", "confirmed"):
        # 仅对应信号有意义；另一信号的分组在结果中只作交叉参考。
        groups[f"{signal}_peer_sync"] = frame[frame[f"{signal}_peer_sync"].eq(True)]
        groups[f"{signal}_peer_sync_strong"] = frame[
            frame[f"{signal}_peer_sync"].eq(True) & frame.strong_theme]
    for stage in ("first", "second", "third_plus"):
        groups[f"{stage}_strong"] = frame[frame.board_stage.eq(stage) & frame.strong_theme]
    for month, part in frame.groupby("month"):
        groups[month] = part
        groups[f"{month}_strong"] = part[part.strong_theme]
    return {name: {key: {exit_name: _one(part, key, col)
                         for exit_name, col in (("next_close", f"{key}_dplus1_net"),
                                                ("next_rule", f"{key}_next_rule_net"),
                                                ("third_close", f"{key}_dplus3_net"),
                                                ("dynamic", f"{key}_dynamic_net"))}
                   for key in ("restart", "confirmed")}
            for name, part in groups.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-db", default="offlineDataManager/data/db_theme_graph.duckdb")
    parser.add_argument("--output", default="outputs/fifteen_min_theme_restart_all_boards_2026")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    frame, audit = run(Path(args.graph_db))
    results = summarize(frame)
    frame.to_csv(out / "events.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audit": audit, "overall": results["all"],
                      "strong_theme": results["strong_theme"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
