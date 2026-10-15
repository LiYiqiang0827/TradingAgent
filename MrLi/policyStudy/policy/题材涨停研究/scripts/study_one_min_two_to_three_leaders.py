"""二进三题材高度龙头的分钟级试验；所有筛选信息按信号时点截断。

本研究只读数据。分钟 OHLC 不能证明涨停价订单能排到，故涨停价收益一律称纸面收益。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_oneMin, get_stk_limit, get_theme_daily
from offlineDataManager.scripts.core.theme_daily_review import _leader_rows


EXCLUDED_THEMES = {"ST板块", "ST摘帽", "次新股"}
START, END = "20260601", "20260831"


def _facts(path: Path) -> pd.DataFrame:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(
            """SELECT e.trade_date, e.ts_code, e.name, e.tag, e.status_raw,
                      e.board_height, e.lu_time, e.limit_order, e.lu_limit_order,
                      e.bid_amount, e.amount, e.free_float,
                      r.theme_id, t.canonical_name AS theme_name,
                      COALESCE(x.level1_name, '') AS level1_name
               FROM fact_limit_event e
               JOIN rel_limit_theme r USING (event_id)
               JOIN dim_theme t USING (theme_id)
               LEFT JOIN dim_theme_taxonomy x USING (theme_id)
               WHERE r.attribution_role='primary'
                 AND e.trade_date BETWEEN '20260501' AND '20260831'"""
        ).df()
    finally:
        con.close()


def _day_bootstrap(frame: pd.DataFrame, flag: str, outcome: str,
                   seed: int = 20260929) -> dict:
    y = frame[frame[outcome].notna()].copy()
    a, b = y[y[flag]], y[~y[flag]]
    if len(a) < 3 or len(b) < 3:
        return {"yes": len(a), "no": len(b), "comparison": "too_few"}
    agg = y.groupby(["trade_date", flag])[outcome].agg(["sum", "count"]).unstack(flag, fill_value=0)
    values = np.column_stack([agg[("sum", True)], agg[("count", True)],
                              agg[("sum", False)], agg[("count", False)]]).astype(float)
    rng = np.random.default_rng(seed)
    draw = values[rng.integers(0, len(values), (4000, len(values)))].sum(axis=1)
    valid = (draw[:, 1] > 0) & (draw[:, 3] > 0)
    diffs = draw[valid, 0] / draw[valid, 1] - draw[valid, 2] / draw[valid, 3]
    return {"yes": len(a), "no": len(b),
            "difference_percentage_points": round(100 * (a[outcome].mean() - b[outcome].mean()), 3),
            "day_block_95_pp": [round(100 * x, 3) for x in np.quantile(diffs, [.025, .975])]}


def _metrics(frame: pd.DataFrame, outcome: str) -> dict:
    x = frame[frame[outcome].notna()]
    return {"candidates": len(frame), "observed_touches": int(frame.observed_d0_touch.sum()),
            "quality_accepted_touches": int(frame.quality_ok.eq(True).sum()),
            "third_board_sealed": int(frame.final_sealed.eq(True).sum()),
            "next_minute_limit_observed": int(frame.next_minute_limit_observed.eq(True).sum()),
            "next_minute_below_limit_buyable": int(frame.d0_after_touch_buyable.eq(True).sum()),
            "scheduled_exit_blocked": int(frame.d1_0932_exit_blocked.eq(True).sum()),
            "scored": len(x), "dates": int(x.trade_date.nunique()),
            "mean_net_pct": round(100 * x[outcome].mean(), 3) if len(x) else None,
            "median_net_pct": round(100 * x[outcome].median(), 3) if len(x) else None,
            "positive_pct": round(100 * x[outcome].gt(0).mean(), 2) if len(x) else None}


def build(events_path: Path, graph_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    events = pd.read_csv(events_path, dtype={"trade_date": str, "prev_date": str, "ts_code": str})
    assert events.quality_ok.all(), "Input must be quality-accepted base events"
    assert not events.duplicated(["trade_date", "ts_code"]).any()
    previous = events[["trade_date", "prev_date"]].drop_duplicates()
    assert not previous.trade_date.duplicated().any()
    facts = _facts(graph_path)
    facts = facts[(facts.tag == "涨停") &
                  ~facts.theme_name.isin(EXCLUDED_THEMES) &
                  (facts.level1_name != "ST与次新") &
                  ~facts.name.str.contains("ST|退", case=False, na=False) &
                  ~facts.ts_code.str.endswith(".BJ")].copy()
    facts = facts.drop_duplicates(["trade_date", "theme_id", "ts_code"])
    # "2连板"必须是连续二板；3天2板不是二进三。
    prior = facts[facts.status_raw.eq("2连板")].copy()
    prior = prior.merge(previous, left_on="trade_date", right_on="prev_date", how="inner",
                        suffixes=("_dminus1", ""), validate="many_to_one")
    prior = prior.rename(columns={"trade_date_dminus1": "dminus1"})
    assert prior.dminus1.lt(prior.trade_date).all()
    # S股等名称没有ST但仍按5%限价交易，也应在候选形成前剔除。
    limit = get_stk_limit(start_date=START, end_date=END, source="database_only")
    limit["trade_date"] = limit.trade_date.astype(str)
    daily_prices = get_day(ts_codes=prior.ts_code.unique().tolist(), start_date=START,
                           end_date=END, qfq=False, source="database_only")
    daily_prices["trade_date"] = daily_prices.trade_date.astype(str)
    band = prior[["trade_date", "ts_code"]].merge(
        daily_prices[["trade_date", "ts_code", "pre_close"]], on=["trade_date", "ts_code"],
        how="left", validate="one_to_one").merge(
            limit[["trade_date", "ts_code", "up_limit"]], on=["trade_date", "ts_code"],
            how="left", validate="one_to_one")
    valid_band = (band.up_limit / band.pre_close).gt(1.075)
    excluded_five_pct = int((~valid_band).sum())
    prior = prior.loc[valid_band.to_numpy()].copy()
    max_height = facts.groupby(["trade_date", "theme_id"]).board_height.max().rename("theme_max_height")
    prior = prior.join(max_height, on=["dminus1", "theme_id"])
    # 龙头候选：二板日没有同题材更高板；同为二板时先封者为第一候选。
    # 封板时间并不能单独证明真正龙头，因此报告只称机械定义的高度/先锋候选。
    prior["limit_order_float"] = prior.limit_order / prior.free_float.where(prior.free_float > 0)
    prior = prior.sort_values(["dminus1", "theme_id", "lu_time", "limit_order_float", "ts_code"],
                              ascending=[True, True, True, False, True], na_position="last")
    prior["theme_two_board_rank"] = prior.groupby(["dminus1", "theme_id"]).cumcount() + 1
    prior["height_leader"] = prior.theme_max_height.eq(2) & prior.theme_two_board_rank.eq(1)
    # 同时用项目每日题材复盘的既有龙头评分（人气/封板时点/封单/成交额）作定义敏感性复核。
    # 它与“高度相同先封板”不一致时，两种结果都报告，不事后挑表现较好的定义。
    dates_all = sorted(facts.trade_date.unique())
    date_indices = {day: i for i, day in enumerate(dates_all)}
    stock_indices = {code: sorted({date_indices[day] for day in part.trade_date})
                     for code, part in facts.groupby("ts_code")}
    wanted_groups = set(prior[["dminus1", "theme_id"]].itertuples(index=False, name=None))
    score_winners = {}
    for (day, theme), part in facts.groupby(["trade_date", "theme_id"]):
        if (day, theme) not in wanted_groups:
            continue
        top = _leader_rows(part, stock_indices, date_indices, 1)
        if top:
            score_winners[(day, theme)] = top[0]["ts_code"]
    prior["existing_score_leader"] = (
        prior.theme_max_height.eq(2) &
        prior.apply(lambda row: score_winners.get((row.dminus1, row.theme_id)) == row.ts_code, axis=1))

    theme_daily = get_theme_daily(start_date="20260501", end_date=END)
    theme_daily["trade_date"] = theme_daily.trade_date.astype(str)
    theme_daily = theme_daily[(theme_daily.level1_name != "ST与次新") &
                              ~theme_daily.canonical_name.isin(EXCLUDED_THEMES)].copy()
    theme_daily["eligible"] = theme_daily.limit_up_count.ge(3) & theme_daily.heat_score.ge(50)
    theme_daily = theme_daily.sort_values(["trade_date", "eligible", "heat_score", "theme_id"],
                                          ascending=[True, False, False, True])
    theme_daily["heat_rank"] = (theme_daily[theme_daily.eligible]
                                 .groupby("trade_date").cumcount().add(1).reindex(theme_daily.index))
    theme_daily["prior_strong_theme"] = theme_daily.eligible & theme_daily.heat_rank.le(3)
    prior = prior.merge(theme_daily[["trade_date", "theme_id", "heat_score", "limit_up_count",
                                    "heat_rank", "prior_strong_theme"]].rename(
                                        columns={"trade_date": "dminus1"}),
                        on=["dminus1", "theme_id"], how="left", validate="many_to_one")
    prior["prior_strong_theme"] = prior.prior_strong_theme.eq(True)

    # D0同题材同步只使用D0之前已归类的历史主归因股，不使用D0最终KPL标签。
    trading_dates = sorted(theme_daily.trade_date.unique())
    idx = {day: i for i, day in enumerate(trading_dates)}
    hist = facts[["trade_date", "theme_id", "ts_code"]].rename(
        columns={"trade_date": "hist_date", "ts_code": "peer_code"})
    hist["hist_idx"] = hist.hist_date.map(idx)
    prior["d0_idx"] = prior.trade_date.map(idx)
    assert prior.d0_idx.notna().all()
    historical_pool = prior[["trade_date", "dminus1", "ts_code", "theme_id", "d0_idx"]].merge(
        hist, on="theme_id", how="left")
    historical_pool = historical_pool[(historical_pool.hist_idx < historical_pool.d0_idx) &
                                      (historical_pool.hist_idx >= historical_pool.d0_idx - 20)]
    historical_pool = historical_pool.drop_duplicates(["trade_date", "ts_code", "theme_id", "peer_code"])
    # 仅用D0本身的分钟与涨停价计算同题材首触，不借用需要D1/D2质量合格才能进入的结果表。
    # 否则“某位同伴是否启动”的盘中判断会被未来数据可用性改变。
    limit_map = {(r.trade_date, r.ts_code): float(r.up_limit) for r in limit.itertuples()
                 if pd.notna(r.up_limit)}
    minute_touch_rows: list[tuple[str, str, float, bool]] = []
    candidate_codes = prior.groupby("trade_date").ts_code.apply(set).to_dict()
    historical_codes = historical_pool.groupby("trade_date").peer_code.apply(set).to_dict()
    for day, own_codes in candidate_codes.items():
        codes = sorted(own_codes | historical_codes.get(day, set()))
        bars = get_oneMin(ts_codes=codes, trade_date=day,
                          columns=["ts_code", "time_idx", "high", "vol"])
        for code, group in bars.groupby("ts_code", sort=False):
            up = limit_map.get((day, code))
            if up is None:
                continue
            group = group.sort_values("time_idx")
            eligible = group[group.vol.gt(0) & group.high.ge(up - .005)]
            if eligible.empty:
                continue
            first_idx = float(eligible.iloc[0].time_idx)
            next_bar = group[group.time_idx.eq(int(first_idx) + 1)]
            next_limit = bool(not next_bar.empty and next_bar.iloc[0].vol > 0 and
                              next_bar.iloc[0].high >= up - .005)
            minute_touch_rows.append((day, code, first_idx, next_limit))
    minute_touch = pd.DataFrame(minute_touch_rows,
                                columns=["trade_date", "peer_code", "peer_touch_idx",
                                         "next_minute_limit_observed"])
    assert not minute_touch.duplicated(["trade_date", "peer_code"]).any()
    historical_pool = historical_pool.merge(minute_touch[["trade_date", "peer_code", "peer_touch_idx"]],
                                            on=["trade_date", "peer_code"], how="left",
                                            validate="many_to_one")
    same_day = facts[["trade_date", "theme_id", "ts_code"]].rename(
        columns={"trade_date": "dminus1", "ts_code": "peer_code"})
    historical_pool["active_dminus1_member"] = historical_pool[["dminus1", "theme_id", "peer_code"]].apply(tuple, axis=1).isin(
        set(same_day[["dminus1", "theme_id", "peer_code"]].itertuples(index=False, name=None)))

    candidate = prior.merge(events, on=["trade_date", "ts_code"], how="left",
                            suffixes=("", "_event"), validate="many_to_one")
    candidate["quality_ok"] = candidate.quality_ok.eq(True)
    candidate["first_touch_idx"] = pd.to_numeric(candidate.first_touch_idx, errors="coerce")
    candidate = candidate.merge(minute_touch.rename(columns={"peer_code": "ts_code",
                                                       "peer_touch_idx": "minute_first_touch_idx"}),
                                on=["trade_date", "ts_code"], how="left", validate="many_to_one")
    candidate["observed_d0_touch"] = candidate.minute_first_touch_idx.notna()
    candidate["next_minute_limit_observed"] = candidate.next_minute_limit_observed.eq(True)
    accepted = candidate[candidate.quality_ok]
    assert accepted.minute_first_touch_idx.eq(accepted.first_touch_idx).all()
    leader_time = candidate[["trade_date", "ts_code", "theme_id", "minute_first_touch_idx"]].rename(
        columns={"minute_first_touch_idx": "leader_touch_idx"})
    historical_pool = historical_pool.merge(leader_time, on=["trade_date", "ts_code", "theme_id"],
                                            how="left", validate="many_to_one")
    assert historical_pool.hist_date.lt(historical_pool.trade_date).all()
    historical_pool["peer_started_30m"] = (
        historical_pool.peer_code.ne(historical_pool.ts_code) &
        historical_pool.peer_touch_idx.ge(historical_pool.leader_touch_idx - 30) &
        historical_pool.peer_touch_idx.le(historical_pool.leader_touch_idx))
    historical_pool["active_peer_started_30m"] = (historical_pool.peer_started_30m &
                                                     historical_pool.active_dminus1_member)
    peer = historical_pool.groupby(["trade_date", "ts_code", "theme_id"]).agg(
        prior20_peer30=("peer_started_30m", "sum"),
        dminus1_peer30=("active_peer_started_30m", "sum"),
        prior20_pool_size=("peer_code", "nunique"))
    candidate = candidate.join(peer, on=["trade_date", "ts_code", "theme_id"])
    for col in ["prior20_peer30", "dminus1_peer30", "prior20_pool_size"]:
        candidate[col] = candidate[col].fillna(0).astype(int)
    candidate["prior20_sync2"] = candidate.prior20_peer30.ge(2)
    candidate["dminus1_sync2"] = candidate.dminus1_peer30.ge(2)

    # 同步证据到首次触板分钟结束才产生；最早下一分钟才能尝试涨停价排队。
    candidate["paper_next_minute_net"] = candidate.d0_limit_paper_to_d1_0932_net.where(
        candidate.next_minute_limit_observed)

    first_winner = candidate[candidate.height_leader].set_index(["dminus1", "theme_id"]).ts_code
    score_winner = candidate[candidate.existing_score_leader].set_index(["dminus1", "theme_id"]).ts_code
    audit = {"candidate_stock_theme_days": len(candidate),
             "excluded_five_pct_d0_candidates": excluded_five_pct,
             "unique_candidate_stock_days": int(candidate[["trade_date", "ts_code"]].drop_duplicates().shape[0]),
             "leader_candidate_days": int(candidate.height_leader.sum()),
             "existing_score_leader_days": int(candidate.existing_score_leader.sum()),
             "leader_rule_disagreement_theme_days": int(first_winner.ne(
                 score_winner.reindex(first_winner.index)).sum()),
             "leader_touch_days": int((candidate.height_leader & candidate.quality_ok).sum()),
             "observed_leader_touches": int((candidate.height_leader & candidate.observed_d0_touch).sum()),
             "known_historical_peer_rows": len(historical_pool),
             "not_in_kpl_d0_touch": int((candidate.quality_ok & candidate.kpl_tag.eq("not_in_kpl")).sum()),
             "clock": {"dminus1_rank": "D-1 close; KPL first-publication time unarchived",
                       "d0_sync": "only prior 20-trading-day primary KPL members that first touched by leader's minute close",
                       "entry": "earliest D0 next minute; minute high at limit proves order could be submitted, not queue fill",
                       "exit": "D1 09:32, if not limit-down/zero-volume; T+1"}}
    return candidate, historical_pool, audit


def summarize(candidate: pd.DataFrame) -> dict:
    result = {}
    for period, p in [("describe_jun_jul", candidate[candidate.trade_date <= "20260731"]),
                      ("check_aug_not_blind", candidate[candidate.trade_date >= "20260801"])]:
        leader = p[p.height_leader].copy()
        touched = leader[leader.quality_ok].copy()
        result[period] = {
            "all_exact_two_board": _metrics(p, "d0_limit_paper_to_d1_0932_net"),
            "height_leader": _metrics(leader, "d0_limit_paper_to_d1_0932_net"),
            "existing_score_leader": _metrics(p[p.existing_score_leader], "d0_limit_paper_to_d1_0932_net"),
            "leader_prior_strong": _metrics(leader[leader.prior_strong_theme], "d0_limit_paper_to_d1_0932_net"),
            "leader_prior20_sync2": _metrics(leader[leader.prior20_sync2], "paper_next_minute_net"),
            "leader_dminus1_sync2": _metrics(leader[leader.dminus1_sync2], "paper_next_minute_net"),
            "leader_prior_strong_sync2": _metrics(
                leader[leader.prior_strong_theme & leader.prior20_sync2], "paper_next_minute_net"),
            "leader_below_limit_next_open": _metrics(leader, "d0_pullback_to_d1_0932_net"),
            "comparison_prior_strong_vs_other_leaders": _day_bootstrap(
                touched, "prior_strong_theme", "d0_limit_paper_to_d1_0932_net"),
            "comparison_prior20_sync2_vs_other_leaders_next_minute": _day_bootstrap(
                touched[touched.next_minute_limit_observed], "prior20_sync2", "paper_next_minute_net"),
            "comparison_leader_vs_other_two_board": _day_bootstrap(
                p[p.quality_ok], "height_leader", "d0_limit_paper_to_d1_0932_net"),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", default="outputs/one_min_limit_up_pilot/theme_context_202606_202608/events_with_theme.csv")
    parser.add_argument("--graph-db", default="offlineDataManager/data/db_theme_graph.duckdb")
    parser.add_argument("--output", default="outputs/one_min_limit_up_pilot/two_to_three_leaders_202606_202608")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    data, historical_pool, audit = build(Path(args.events), Path(args.graph_db))
    summary = summarize(data)
    data.to_csv(output / "candidate_events.csv", index=False)
    historical_pool.to_csv(output / "historical_peer_pool.csv", index=False)
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "results.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audit": audit, "results": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
