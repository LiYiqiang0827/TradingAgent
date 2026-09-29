"""小样本精品方向：主线旧龙头大幅分歧后，题材修复与15分钟重启共振。

每条候选从历史当天的题材榜和龙头榜冻结；后续逐根15分钟K仅用已结束
的数据。该实验由万向德农案例启发，2026年结果均属于事后探索。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from coreClient.data_provider import (get_fifteenMin, get_market_theme_review_series,
                                      get_stk_limit, get_theme_daily, get_tradecal)
from study_fifteen_min_theme_restart_strategy import _dynamic_exit, _next_day_rule_exit
from study_fifteen_min_two_board_pullback import _outcome


def _isolated_store(database: Path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4] /
                           "offlineDataManager" / "scripts"))
    from core.theme_graph_store import ThemeGraphStore
    return ThemeGraphStore(database=database)


def _anchors(first: str, last: str, data_end: str, graph_db: Path | None
             ) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    calendar = sorted(get_tradecal(start_date=first, end_date=data_end,
                                   source="database_only").cal_date.astype(str).tolist())
    if graph_db:
        store = _isolated_store(graph_db)
        reviews = store.query_market_theme_review_series(start_date=first, end_date=last,
                                                          top_n=3, leader_count=3)
        daily = store.query_theme_daily(start_date=first, end_date=last)
    else:
        reviews = get_market_theme_review_series(start_date=first, end_date=last,
                                                  top_n=3, leader_count=3)
        daily = get_theme_daily(start_date=first, end_date=last)
    rows = []
    for review in reviews:
        for theme in review["hot_themes"]:
            if theme["rank"] > 3 or theme["heat_score"] < 50 or theme["limit_up_count"] < 3:
                continue
            for leader in theme["leaders"]:
                if leader["rank"] > 2 or leader["board_height"] < 3:
                    continue
                rows.append({"anchor_date": str(review["trade_date"]),
                             "theme_id": theme["theme_id"], "theme_name": theme["theme"],
                             "anchor_theme_rank": theme["rank"],
                             "anchor_heat": theme["heat_score"],
                             "ts_code": leader["ts_code"], "name": leader["name"],
                             "anchor_board_height": leader["board_height"],
                             "anchor_leader_rank": leader["rank"]})
    anchor = pd.DataFrame(rows).sort_values(["anchor_date", "ts_code", "anchor_heat"],
                                            ascending=[True, True, False])
    anchor = anchor.drop_duplicates(["anchor_date", "ts_code"])
    daily.trade_date = daily.trade_date.astype(str)
    daily = daily[(daily.level1_name != "ST与次新") &
                  ~daily.canonical_name.isin(["ST板块", "ST摘帽", "次新股"])].copy()
    daily["eligible"] = daily.limit_up_count.ge(3) & daily.heat_score.ge(50)
    daily = daily.sort_values(["trade_date", "eligible", "heat_score", "theme_id"],
                              ascending=[True, False, False, True])
    daily["rank"] = daily[daily.eligible].groupby("trade_date").cumcount().add(1).reindex(daily.index)
    return anchor, daily, calendar


def _reactivation(stock: pd.DataFrame, anchor_date: str, anchor_close: float,
                  theme_id: str, daily_map: dict, calendar: list[str]) -> dict:
    """分歧从锚点次日起观察；每根K结束时由历史低点、MA和量能判定。"""
    empty = {"signal_time": None, "signal_close": None, "neckline": None,
             "observed_trough": None, "drawdown_pct": None,
             "prior_theme_heat": None, "prior_theme_rank": None,
             "prior_theme_width": None}
    pos = calendar.index(anchor_date)
    if pos + 10 >= len(calendar):
        return empty
    from_date = calendar[pos + 1]
    to_date = calendar[pos + 10]
    window = stock[stock.trade_date.between(int(from_date), int(to_date))].reset_index(drop=True)
    if len(window) != 160:
        return empty
    prior = stock[stock.trade_date == int(anchor_date)]
    if len(prior) != 16:
        return empty
    close = window.close.to_numpy(float)
    high = window.high.to_numpy(float)
    low = window.low.to_numpy(float)
    volume = window.vol.to_numpy(float)
    ma5 = pd.Series(np.r_[prior.close.to_numpy(float), close]).rolling(5).mean().to_numpy()[16:]
    trough = anchor_close
    trough_idx = None
    for i, bar in enumerate(window.itertuples()):
        if low[i] < trough:
            trough, trough_idx = low[i], i
        if trough < anchor_close * .75:
            break
        if i < 5 or trough_idx is None or i - trough_idx < 2 or trough > anchor_close * .92:
            continue
        # 不允许某次早期恐慌的低点在数日后被反弹过高时再次称为“回调买点”。
        if close[i] > min(anchor_close * 1.10, trough * 1.22):
            continue
        if min(low[i - 1:i + 1]) < trough * 1.001:
            continue
        prior_date = calendar[calendar.index(str(bar.trade_date)) - 1]
        theme = daily_map.get((prior_date, theme_id))
        if theme is None or theme["heat"] < 50 or theme["width"] < 3 or theme["rank"] > 3:
            continue
        neckline = float(max(high[i - 4:i]))
        if (close[i] > neckline * 1.001 and close[i] > ma5[i] > ma5[i - 1] and
                volume[i] >= np.median(volume[i - 4:i]) * 1.2):
            return {"signal_time": str(bar.datetime), "signal_close": float(bar.close),
                    "neckline": neckline, "observed_trough": float(trough),
                    "drawdown_pct": round(100 * (trough / anchor_close - 1), 3),
                    "prior_theme_heat": theme["heat"], "prior_theme_rank": theme["rank"],
                    "prior_theme_width": theme["width"]}
    return empty


def run(first: str, last: str, data_end: str, graph_db: Path | None
        ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    anchors, daily, calendar = _anchors(first, last, data_end, graph_db)
    if anchors.empty:
        raise RuntimeError("No dated mainline core anchors")
    daily_map = {(r.trade_date, r.theme_id): {
        "heat": float(r.heat_score), "rank": float(r.rank), "width": int(r.limit_up_count)}
        for r in daily.itertuples()}
    codes = sorted(anchors.ts_code.unique())
    bars = get_fifteenMin(ts_codes=codes, start_date=first, end_date=data_end)
    bars.trade_date = bars.trade_date.astype(int)
    bars = bars.sort_values(["ts_code", "datetime"])
    stock_bars = {code: part.reset_index(drop=True) for code, part in bars.groupby("ts_code")}
    counts = bars.groupby(["ts_code", "trade_date"]).size()
    limits = get_stk_limit(start_date=first, end_date=data_end, source="database_only")
    limits.trade_date = limits.trade_date.astype(str)
    limit_map = {(r.trade_date, r.ts_code): (float(r.up_limit), float(r.down_limit))
                 for r in limits.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    prospects = []
    for anchor in anchors.itertuples():
        stock = stock_bars.get(anchor.ts_code)
        if stock is None:
            continue
        idx = calendar.index(anchor.anchor_date)
        required = calendar[idx:idx + 11]
        if len(required) != 11 or any(counts.get((anchor.ts_code, int(d)), 0) != 16
                                      for d in required):
            continue
        anchor_close = float(stock[stock.trade_date == int(anchor.anchor_date)].iloc[-1].close)
        setup = _reactivation(stock, anchor.anchor_date, anchor_close, anchor.theme_id,
                              daily_map, calendar)
        if setup["signal_time"] is None:
            continue
        prospects.append({"anchor_date": anchor.anchor_date, "ts_code": anchor.ts_code,
                          "name": anchor.name, "theme_id": anchor.theme_id,
                          "theme_name": anchor.theme_name, "anchor_heat": anchor.anchor_heat,
                          "anchor_board_height": anchor.anchor_board_height,
                          "anchor_leader_rank": anchor.anchor_leader_rank,
                          "anchor_close": anchor_close, **setup})
    prospects = pd.DataFrame(prospects)
    if prospects.empty:
        return anchors, prospects, prospects, {"anchors": len(anchors), "signals": 0}
    # 同一股票旧锚点会生成相同或重叠信号：在信号时选当时最近的锚点。
    prospects = prospects.sort_values(["signal_time", "anchor_date"],
                                      ascending=[True, False]).drop_duplicates(
                                          ["ts_code", "signal_time"])
    completed = []
    last_signal_by_stock: dict[str, int] = {}
    for r in prospects.sort_values(["signal_time", "ts_code"]).itertuples():
        signal_day = pd.Timestamp(r.signal_time).strftime("%Y%m%d")
        signal_idx = calendar.index(signal_day)
        if signal_idx - last_signal_by_stock.get(r.ts_code, -1000) <= 10:
            continue
        last_signal_by_stock[r.ts_code] = signal_idx
        stock = stock_bars[r.ts_code]
        signal = {"time": r.signal_time, "close": r.signal_close}
        baseline = _outcome(stock, signal, limit_map, calendar)
        dynamic = _dynamic_exit(stock, signal, baseline, r.neckline,
                                r.observed_trough, calendar, limit_map)
        next_rule = _next_day_rule_exit(stock, baseline, calendar, limit_map)
        row = r._asdict()
        row.update(baseline)
        row.update(dynamic)
        row.update(next_rule)
        completed.append(row)
    trades = pd.DataFrame(completed)
    sync_events = _synchronized_core_events(trades)
    audit = {"anchors": len(anchors), "anchor_stocks": len(codes),
             "prospect_signals_before_cooldown": len(prospects),
             "signals_after_10day_cooldown": len(trades),
             "buyable": int(trades.buyable.eq(True).sum()),
             "sync_events": len(sync_events),
             "sync_buyable": int(sync_events.buyable.eq(True).sum()) if len(sync_events) else 0,
             "source_last_theme_day": str(daily.trade_date.max()),
             "source_last_15m_day": str(bars.trade_date.max()),
             "result_status": "case-inspired exploratory search; no untouched 2026 holdout"}
    return anchors, trades, sync_events, audit


def _synchronized_core_events(trades: pd.DataFrame) -> pd.DataFrame:
    """半小时内第二只旧核心确认时选一只；选择不看下一根可否成交。"""
    if trades.empty:
        return trades.copy()
    work = trades.copy()
    work["stamp"] = pd.to_datetime(work.signal_time)
    work["signal_day"] = work.stamp.dt.strftime("%Y%m%d")
    selected = []
    for (_, _), group in work.groupby(["signal_day", "theme_id"]):
        for stamp in sorted(group.stamp.unique()):
            recent = group[group.stamp.between(stamp - pd.Timedelta(minutes=30), stamp)]
            if recent.ts_code.nunique() < 2:
                continue
            now = group[group.stamp.eq(stamp)].sort_values(
                ["anchor_leader_rank", "anchor_board_height", "anchor_heat", "ts_code"],
                ascending=[True, False, False, True])
            chosen = now.iloc[0].drop(labels=["stamp", "signal_day"]).to_dict()
            chosen["theme_sync_time"] = str(stamp)
            chosen["theme_sync_stock_count"] = int(recent.ts_code.nunique())
            selected.append(chosen)
            break  # 同题材同日最多一次入场计划。
    return pd.DataFrame(selected).sort_values("signal_time") if selected else trades.iloc[:0].copy()


def _stats(frame: pd.DataFrame, col: str) -> dict:
    ret = frame[col].dropna()
    gross_gain = ret[ret > 0].sum()
    gross_loss = -ret[ret < 0].sum()
    return {"trades": len(frame), "scored": len(ret),
            "mean_net_pct": round(100 * ret.mean(), 3) if len(ret) else None,
            "median_net_pct": round(100 * ret.median(), 3) if len(ret) else None,
            "win_pct": round(100 * ret.gt(0).mean(), 2) if len(ret) else None,
            "profit_factor": round(gross_gain / gross_loss, 3) if gross_loss else None,
            "worst_pct": round(100 * ret.min(), 3) if len(ret) else None,
            "best_pct": round(100 * ret.max(), 3) if len(ret) else None}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/fifteen_min_core_reactivation_2026")
    parser.add_argument("--first", default="20260105")
    parser.add_argument("--last", default="20260915")
    parser.add_argument("--data-end", default="20260924")
    parser.add_argument("--graph-db", type=Path,
                        help="隔离的历史题材库；不填则使用provider默认的2026生产库")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    anchors, trades, sync_events, audit = run(
        args.first, args.last, args.data_end, args.graph_db)
    anchors.to_csv(out / "anchors.csv", index=False)
    trades.to_csv(out / "trades.csv", index=False)
    sync_events.to_csv(out / "synchronized_core_events.csv", index=False)
    groups = {"all": trades, "synchronized_core": sync_events}
    if not trades.empty:
        for month, part in trades.groupby(trades.signal_time.str.slice(0, 7)):
            groups[month] = part
    result = {name: {exit_name: _stats(part[part.buyable.eq(True)], col)
                     for exit_name, col in (("next_close", "dplus1_net"),
                                            ("next_rule", "next_rule_net"),
                                            ("third_close", "dplus3_net"),
                                            ("dynamic", "dynamic_net"))}
              for name, part in groups.items()}
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audit": audit, "results": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
