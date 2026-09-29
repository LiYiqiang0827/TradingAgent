"""在冻结的 1 分钟触板事件上，检验历史可见题材强度和盘中同题材同步性。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from coreClient.data_provider import get_theme_daily


EXCLUDED = {"ST板块", "ST摘帽", "次新股"}


def _theme_facts(path: Path) -> pd.DataFrame:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        return conn.execute(
            """SELECT e.trade_date,e.ts_code,e.name,e.tag,r.theme_id,t.canonical_name,
                      COALESCE(x.level1_name,'待归类') AS level1_name
               FROM fact_limit_event e JOIN rel_limit_theme r USING(event_id)
               JOIN dim_theme t USING(theme_id)
               LEFT JOIN dim_theme_taxonomy x USING(theme_id)
               WHERE r.attribution_role='primary' AND e.trade_date BETWEEN '20260105' AND '20260831'"""
        ).df()
    finally:
        conn.close()


def _day_block_diff(frame: pd.DataFrame, outcome: str, flag: str, seed: int = 20260929) -> dict:
    y = frame[frame[outcome].notna()].copy()
    a, b = y[y[flag]], y[~y[flag]]
    if len(a) < 5 or len(b) < 5:
        return {"n_a": len(a), "n_b": len(b)}
    g = y.groupby(["trade_date", flag])[outcome].agg(["sum", "count"]).unstack(flag, fill_value=0)
    values = np.column_stack([g[("sum", True)], g[("count", True)],
                              g[("sum", False)], g[("count", False)]]).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(values), (4000, len(values)))
    totals = values[pick].sum(axis=1)
    valid = (totals[:, 1] > 0) & (totals[:, 3] > 0)
    boot = totals[valid, 0] / totals[valid, 1] - totals[valid, 2] / totals[valid, 3]
    return {"n_a": len(a), "n_b": len(b),
            "a_minus_not_a_mean_pp": round(100 * (a[outcome].mean() - b[outcome].mean()), 3),
            "day_bootstrap_95_pp": [round(100 * v, 3) for v in np.quantile(boot, [.025, .975])]}


def _group_result(frame: pd.DataFrame, mask: pd.Series, outcome: str,
                  buyable: str) -> dict:
    x = frame[mask].copy()
    traded = x[x[outcome].notna()]
    return {"signals": len(x), "buyable": int(x[buyable].sum()),
            "exit_blocked_after_buy": int((x[buyable] & x.d2_0932_exit_blocked).sum()) if outcome.startswith("d1_") else
                                      int((x.d0_after_touch_buyable & x.d1_0932_exit_blocked).sum()),
            "exits": len(traded),
            "mean_net_pct": round(100 * traded[outcome].mean(), 3) if len(traded) else None,
            "median_net_pct": round(100 * traded[outcome].median(), 3) if len(traded) else None,
            "net_win_pct": round(100 * (traded[outcome] > 0).mean(), 2) if len(traded) else None,
            "trading_days": traded.trade_date.nunique()}


def build(events_path: Path, graph_path: Path) -> tuple[pd.DataFrame, dict]:
    events = pd.read_csv(events_path, dtype={"trade_date": str, "d1": str, "d2": str, "ts_code": str})
    events = events[events.quality_ok].copy()
    facts = _theme_facts(graph_path)
    facts = facts[(facts.level1_name != "ST与次新") & ~facts.canonical_name.isin(EXCLUDED) &
                  ~facts.ts_code.str.endswith(".BJ")].copy()
    # 仅用户指定的已涨停主归因股进入题材集合，绝不扩展到概念普通成分。
    facts = facts[facts.tag == "涨停"].drop_duplicates(["trade_date", "ts_code", "theme_id"])
    daily = get_theme_daily(start_date="20260501", end_date="20260831")
    daily = daily[(daily.level1_name != "ST与次新") & ~daily.canonical_name.isin(EXCLUDED)].copy()
    daily["trade_date"] = daily.trade_date.astype(str)
    daily["strong_candidate"] = (daily.limit_up_count >= 3) & (daily.heat_score >= 50)
    daily = daily.sort_values(["trade_date", "strong_candidate", "heat_score", "theme_id"],
                              ascending=[True, False, False, True])
    daily["strong_rank"] = daily[daily.strong_candidate].groupby("trade_date").cumcount().add(1).reindex(daily.index)
    daily["strong_top3"] = daily.strong_candidate & daily.strong_rank.le(3)
    trading_days = sorted(daily.trade_date.unique())
    day_idx = {d: i for i, d in enumerate(trading_days)}
    prev_date = {d: trading_days[i - 1] if i else None for i, d in enumerate(trading_days)}
    events["prev_date"] = events.trade_date.map(prev_date)
    events["d0_idx"] = events.trade_date.map(day_idx)
    # D1 可使用 D0 收盘之后已知的开盘啦主题材；历史首发版本缺失，见审计说明。
    current = facts[facts.trade_date.between("20260601", "20260831")][
        ["trade_date", "ts_code", "theme_id", "canonical_name", "level1_name"]].rename(
        columns={"theme_id": "d0_theme_id", "canonical_name": "d0_theme", "level1_name": "d0_level1"})
    events = events.merge(current, on=["trade_date", "ts_code"], how="left", validate="one_to_one")
    d0_heat = daily[["trade_date", "theme_id", "limit_up_count", "heat_score", "strong_top3"]].rename(
        columns={"theme_id": "d0_theme_id", "limit_up_count": "d0_theme_width",
                 "heat_score": "d0_theme_heat", "strong_top3": "d0_theme_strong"})
    events = events.merge(d0_heat, on=["trade_date", "d0_theme_id"], how="left", validate="many_to_one")
    events["d0_theme_strong"] = events.d0_theme_strong.eq(True)
    # D0 盘中只能借用 D0 之前 20 个交易日已经出现过的主归因，不能用当天最终归因。
    hist = facts[["trade_date", "ts_code", "theme_id"]].rename(columns={"trade_date": "hist_date"})
    # 历史映射需覆盖 5 月之前的日期；另用有序事实日期构造统一会话索引。
    all_dates = sorted(set(facts.trade_date) | set(trading_days))
    all_idx = {d: i for i, d in enumerate(all_dates)}
    hist["hist_idx"] = hist.hist_date.map(all_idx)
    events["all_d0_idx"] = events.trade_date.map(all_idx)
    prior = events[["trade_date", "ts_code", "prev_date", "all_d0_idx", "first_touch_idx"]].merge(
        hist, on="ts_code", how="left")
    prior = prior[(prior.hist_idx < prior.all_d0_idx) & (prior.hist_idx >= prior.all_d0_idx - 20)].copy()
    prior = prior.drop_duplicates(["trade_date", "ts_code", "theme_id"])
    prev_heat = daily[["trade_date", "theme_id", "strong_top3"]].rename(
        columns={"trade_date": "prev_date", "strong_top3": "prev_theme_strong"})
    prior = prior.merge(prev_heat, on=["prev_date", "theme_id"], how="left")
    prior["prev_theme_strong"] = prior.prev_theme_strong.eq(True)
    prior_roll = prior.groupby(["trade_date", "ts_code"]).agg(
        prior_theme_count=("theme_id", "nunique"), prior_strong_theme=("prev_theme_strong", "max"))
    events = events.join(prior_roll, on=["trade_date", "ts_code"])
    events["prior_theme_count"] = events.prior_theme_count.fillna(0).astype(int)
    events["prior_strong_theme"] = events.prior_strong_theme.eq(True)
    # 同题材过去 30 个分钟索引内至少还有两只股票首次触板；同分钟数据到分钟收盘才可见。
    prior["peer30"] = 0
    for (_, _), group in prior.groupby(["trade_date", "theme_id"], sort=False):
        idx = group.first_touch_idx.to_numpy(dtype=int)
        counts = np.array([int(((idx >= t - 30) & (idx <= t)).sum() - 1) for t in idx])
        prior.loc[group.index, "peer30"] = counts
    peer = prior.groupby(["trade_date", "ts_code"]).peer30.max().rename("prior_theme_peer30")
    events = events.join(peer, on=["trade_date", "ts_code"])
    events["prior_theme_peer30"] = events.prior_theme_peer30.fillna(0).astype(int)
    events["d0_sync_prior"] = (events.prior_theme_peer30 >= 2)
    # D1 09:45：D0 已封板的同一主题材成员中，其他成员是否集体高于前收且从 09:31 上涨。
    sealed = events[events.final_sealed & events.d0_theme_id.notna()].copy()
    sealed["peer_morning_strong"] = ((sealed.d1_0945_return_pct >= .5) &
        ((1 + sealed.d1_0945_return_pct / 100) / (1 + sealed.d1_gap_0931_pct / 100) - 1 >= .005))
    full_counts = facts[facts.trade_date.between("20260601", "20260831")].groupby(
        ["trade_date", "theme_id"]).ts_code.nunique().rename("full_d0_theme_size")
    group = sealed.groupby(["trade_date", "d0_theme_id"]).agg(
        observed_theme_size=("ts_code", "nunique"), total_strong=("peer_morning_strong", "sum"))
    group = group.join(full_counts.rename_axis(index=["trade_date", "d0_theme_id"]), how="left")
    sealed = sealed.join(group, on=["trade_date", "d0_theme_id"])
    sealed["other_strong"] = sealed.total_strong.astype(int) - sealed.peer_morning_strong.astype(int)
    sealed["d1_theme_sync_0945"] = (
        sealed.full_d0_theme_size.ge(3) &
        (sealed.observed_theme_size / sealed.full_d0_theme_size >= .8) &
        sealed.other_strong.ge(2) &
        (sealed.other_strong / (sealed.full_d0_theme_size - 1) >= .5))
    events = events.merge(sealed[["trade_date", "ts_code", "full_d0_theme_size", "observed_theme_size",
                                   "other_strong", "d1_theme_sync_0945"]],
                           on=["trade_date", "ts_code"], how="left", validate="one_to_one")
    events["d1_theme_sync_0945"] = events.d1_theme_sync_0945.eq(True)
    events["d1_theme_strong_sync"] = events.d0_theme_strong & events.d1_theme_sync_0945
    events["d1_weak_theme_sync"] = events.d1_weak_strong_0945 & events.d1_theme_sync_0945
    audit = {"events_quality_ok": len(events), "d0_primary_mapped": int(events.d0_theme_id.notna().sum()),
             "prior_20d_theme_mapped": int((events.prior_theme_count > 0).sum()),
             "prior_strong_count": int(events.prior_strong_theme.sum()),
             "d0_prior_sync_count": int(events.d0_sync_prior.sum()),
             "d1_exact_theme_sync_count": int(events.d1_theme_sync_0945.sum()),
             "d0_primary_missing": int(events.d0_theme_id.isna().sum()),
             "rules": {"strong_theme": "top3 by heat among >=3 limit-ups and heat>=50 at prior completed close",
                       "historical_membership": "primary KPL theme in preceding 20 trading sessions",
                       "d0_sync": ">=2 other historical-theme stocks first touching limit in previous 30 minutes",
                       "d1_sync": "D0 sealed primary-theme group >=3; >=80% observed; >=2 other stocks and >=50% peers +0.5% vs D0 close and 09:31 open at D1 09:45"},
             "availability": "D0 KPL themes were captured later; D1 assumes D0 after-close labels were publishable before 09:45, without first-publication archive"}
    return events, audit


def summarize(events: pd.DataFrame) -> dict:
    out = {}
    for period, x in [("discover_jun_jul", events[events.trade_date <= "20260731"]),
                      ("validate_aug", events[events.trade_date >= "20260801"])]:
        d0 = x.copy()
        d0["prior_available"] = d0.prior_theme_count > 0
        d0["prior_strong_and_sync"] = d0.prior_strong_theme & d0.d0_sync_prior
        d1 = x[x.final_sealed].copy()
        d1["mapped"] = d1.d0_theme_id.notna()
        d1["weak_and_strong_sync"] = d1.d1_weak_strong_0945 & d1.d1_theme_strong_sync
        result = {"d0": {}, "d1": {}}
        for label, mask in {
            "all_touch": pd.Series(True, index=d0.index),
            "prior_theme_available": d0.prior_available,
            "prior_strong_theme": d0.prior_strong_theme,
            "prior_same_theme_30min_sync": d0.d0_sync_prior,
            "prior_strong_and_sync": d0.prior_strong_and_sync,
        }.items():
            result["d0"][label] = _group_result(d0, mask, "d0_pullback_to_d1_0932_net", "d0_after_touch_buyable")
        for label, mask in {
            "all_sealed": pd.Series(True, index=d1.index),
            "d0_primary_mapped": d1.mapped,
            "d0_strong_theme": d1.d0_theme_strong,
            "d1_same_theme_sync_0945": d1.d1_theme_sync_0945,
            "d0_strong_and_d1_sync": d1.d1_theme_strong_sync,
            "stock_weak_to_strong": d1.d1_weak_strong_0945,
            "stock_weak_and_theme_sync": d1.d1_weak_theme_sync,
            "stock_weak_and_strong_theme_sync": d1.weak_and_strong_sync,
        }.items():
            result["d1"][label] = _group_result(d1, mask, "d1_0946_to_d2_0932_net", "d1_0946_buyable")
        result["comparisons"] = {
            "d0_prior_strong_vs_other_prior_mapped": _day_block_diff(d0[d0.prior_available], "d0_pullback_to_d1_0932_net", "prior_strong_theme"),
            "d0_prior_sync_vs_other_prior_mapped": _day_block_diff(d0[d0.prior_available], "d0_pullback_to_d1_0932_net", "d0_sync_prior"),
            "d1_strong_vs_other_mapped": _day_block_diff(d1[d1.mapped], "d1_0946_to_d2_0932_net", "d0_theme_strong"),
            "d1_sync_vs_other_mapped": _day_block_diff(d1[d1.mapped], "d1_0946_to_d2_0932_net", "d1_theme_sync_0945"),
            "d1_strong_sync_vs_other_mapped": _day_block_diff(d1[d1.mapped], "d1_0946_to_d2_0932_net", "d1_theme_strong_sync"),
        }
        out[period] = result
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", default="outputs/one_min_limit_up_pilot/202606_202608/events.csv")
    p.add_argument("--graph-db", default="offlineDataManager/data/db_theme_graph.duckdb")
    p.add_argument("--output", default="outputs/one_min_limit_up_pilot/theme_context_202606_202608")
    args = p.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    events, audit = build(Path(args.events), Path(args.graph_db))
    results = summarize(events)
    events.to_csv(out / "events_with_theme.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audit": audit, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
