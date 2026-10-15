"""二板题材龙头后的15分钟回调承接与二次启动：固定阈值的探索性回放。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_fifteenMin, get_stk_limit, get_theme_daily
from offlineDataManager.scripts.core.theme_daily_review import _leader_rows


BUY_SLIPPAGE = .001
SELL_SLIPPAGE = .001
COMMISSION = .0003
SELL_TAX = .0005


def _net(entry: float, exit_price: float) -> float:
    return exit_price * (1 - SELL_SLIPPAGE - COMMISSION - SELL_TAX) / (
        entry * (1 + BUY_SLIPPAGE + COMMISSION)) - 1


def _summarize(frame: pd.DataFrame, prefix: str) -> dict:
    signal = frame[f"{prefix}_signal_time"].notna()
    buy = frame[f"{prefix}_buyable"].eq(True)
    ret = frame[f"{prefix}_dplus1_net"].dropna()
    ret3 = frame[f"{prefix}_dplus3_net"].dropna()
    mae = frame.loc[frame[f"{prefix}_dplus1_net"].notna(), f"{prefix}_dplus1_mae"]
    return {"candidates": len(frame), "signals": int(signal.sum()), "buyable": int(buy.sum()),
            "dplus1_exit_blocked": int(frame[f"{prefix}_dplus1_exit_blocked"].eq(True).sum()),
            "dplus1_scored": len(ret), "dplus1_mean_net_pct": round(100 * ret.mean(), 3) if len(ret) else None,
            "dplus1_median_net_pct": round(100 * ret.median(), 3) if len(ret) else None,
            "dplus1_win_pct": round(100 * ret.gt(0).mean(), 2) if len(ret) else None,
            "dplus1_p10_net_pct": round(100 * ret.quantile(.1), 3) if len(ret) else None,
            "dplus1_mae_below_3pct_share": round(100 * mae.lt(-.03).mean(), 2) if len(mae) else None,
            "dplus3_scored": len(ret3), "dplus3_mean_net_pct": round(100 * ret3.mean(), 3) if len(ret3) else None,
            "two_session_up3_before_down3": int(frame[f"{prefix}_two_session_path"].eq("up_first").sum()),
            "two_session_down3_before_up3": int(frame[f"{prefix}_two_session_path"].eq("down_first").sum()),
            "two_session_neither": int(frame[f"{prefix}_two_session_path"].eq("neither").sum()),
            "distinct_setup_dates": int(frame.loc[signal, "d0"].nunique())}


def _bootstrap_mean(frame: pd.DataFrame, col: str, seed: int = 20260929) -> list[float] | None:
    x = frame[frame[col].notna()]
    if len(x) < 3:
        return None
    agg = x.groupby("d0")[col].agg(["sum", "count"]).to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    draw = agg[rng.integers(0, len(agg), (4000, len(agg)))].sum(axis=1)
    return [round(100 * n, 3) for n in np.quantile(draw[:, 0] / draw[:, 1], [.025, .975])]


def _signal_info(bars: pd.DataFrame, index: int | None) -> dict:
    if index is None:
        return {"idx": None, "time": None, "close": None, "low": None}
    row = bars.iloc[index]
    return {"idx": int(index), "time": str(row["datetime"]),
            "close": float(row["close"]), "low": float(row["low"])}


def _find_signals(bars: pd.DataFrame, d0: str, second_close: float,
                  calendar: list[str], support_ceiling_pct: float) -> tuple[dict, dict, dict]:
    """只看 D0 到 D+4；每根15分钟K结束后更新一次状态，不用未来极值。"""
    d0_index = calendar.index(d0)
    last_setup_date = calendar[d0_index + 4]
    window = bars[(bars.trade_date >= int(d0)) &
                  (bars.trade_date <= int(last_setup_date))].reset_index(drop=True)
    if len(window) != 80:
        return _signal_info(window, None), _signal_info(window, None), {"valid_window": False}
    close = window.close.to_numpy(float)
    open_ = window.open.to_numpy(float)
    high = window.high.to_numpy(float)
    low = window.low.to_numpy(float)
    volume = window.vol.to_numpy(float)
    # 15分钟MA从二板日最后16根开始预热；窗口内任何时点都只用已经结束的K线。
    prior_day = bars[bars.trade_date == int(calendar[d0_index - 1])].tail(16)
    if len(prior_day) != 16:
        return _signal_info(window, None), _signal_info(window, None), {"valid_window": False}
    combined_close = np.r_[prior_day.close.to_numpy(float), close]
    ma5 = pd.Series(combined_close).rolling(5).mean().to_numpy()[16:]
    trough = second_close
    trough_idx = None
    active_support = None
    first_support = None
    restart = None
    breakdown = False
    for i in range(len(window)):
        if low[i] < trough - .000001:
            trough, trough_idx = low[i], i
            active_support = None
        if trough < second_close * .90:
            breakdown = True
            break
        if trough_idx is None or trough > second_close * .98:
            continue
        if (active_support is None and i >= trough_idx + 2 and i >= 2 and
                min(low[i-1:i+1]) >= trough * 1.002 and
                close[i] >= trough * 1.01 and close[i] > open_[i] and
                (support_ceiling_pct >= 999 or
                 close[i] <= min(second_close * 1.01,
                                 trough * (1 + support_ceiling_pct / 100)))):
            active_support = i
            if first_support is None:
                first_support = i
        if active_support is None or i <= active_support or i - active_support > 32 or i < 5:
            continue
        if (close[i] > max(high[i-4:i]) * 1.001 and
                close[i] > ma5[i] and ma5[i] > ma5[i-1] and
                volume[i] >= np.median(volume[i-4:i]) * 1.2 and
                second_close * .96 <= close[i] <= second_close * 1.06):
            restart = i
            break
    audit = {"valid_window": True, "breakdown_below_10pct": breakdown,
             "trough_pct": round(100 * (trough / second_close - 1), 3),
             "restart_neckline": float(max(high[restart-4:restart])) if restart is not None else None,
             "restart_trough": float(trough) if restart is not None else None,
             "trough_seen_time": str(window.iloc[trough_idx]["datetime"]) if trough_idx is not None else None}
    return _signal_info(window, first_support), _signal_info(window, restart), audit


def _outcome(bars: pd.DataFrame, signal: dict, limit_map: dict,
             calendar: list[str]) -> dict:
    empty = {"buyable": False, "entry_time": None, "entry_open": None,
             "dplus1_net": None, "dplus1_mae": None, "dplus1_exit_blocked": False,
             "dplus3_net": None, "factor_changed": False, "two_session_path": None}
    if signal["time"] is None:
        return empty
    after = bars[bars["datetime"] > pd.Timestamp(signal["time"])]
    if after.empty:
        return empty
    entry = after.iloc[0]
    entry_date = str(entry.trade_date)
    code = str(entry.ts_code)
    up = limit_map.get((entry_date, code), (None, None))[0]
    if (entry.vol <= 0 or up is None or entry.open >= up - .005 or
            entry.open > signal["close"] * 1.03 or entry.open <= 0):
        return empty
    result = dict(empty)
    result.update({"buyable": True, "entry_time": str(entry["datetime"]),
                   "entry_open": float(entry.open)})
    pos = calendar.index(entry_date)
    if pos + 2 < len(calendar):
        path = bars[(bars["datetime"] > entry["datetime"]) &
                    (bars.trade_date <= int(calendar[pos + 2]))]
        path = path[path.close.ge(float(entry.open) * 1.03) |
                    path.close.le(float(entry.open) * .97)]
        if path.empty:
            result["two_session_path"] = "neither"
        else:
            result["two_session_path"] = (
                "up_first" if path.iloc[0]["close"] >= float(entry.open) * 1.03 else "down_first")
    for horizon in (1, 3):
        if pos + horizon >= len(calendar):
            continue
        target_date = calendar[pos + horizon]
        target = bars[bars.trade_date == int(target_date)]
        if len(target) != 16:
            continue
        exit_bar = target.iloc[-1]
        holding = bars[(bars["datetime"] >= entry["datetime"]) &
                       (bars["datetime"] <= exit_bar["datetime"])]
        if holding.adj_factor.max() / holding.adj_factor.min() - 1 > .00001:
            result["factor_changed"] = True
            continue
        down = limit_map.get((target_date, code), (None, None))[1]
        exit_blocked = bool(exit_bar.vol <= 0 or (down is not None and exit_bar.close <= down + .005))
        if horizon == 1:
            result["dplus1_exit_blocked"] = exit_blocked
            result["dplus1_mae"] = float(holding.low.min() / entry.open - 1)
        if not exit_blocked:
            result[f"dplus{horizon}_net"] = _net(float(entry.open), float(exit_bar.close))
    return result


def _september_candidates(graph_path: Path) -> pd.DataFrame:
    """9月检查集：与6–8月同一二板/龙头定义，固定于规则完成后生成。"""
    con = duckdb.connect(str(graph_path), read_only=True)
    try:
        facts = con.execute(
            """SELECT e.trade_date,e.ts_code,e.name,e.tag,e.status_raw,e.board_height,
                      e.lu_time,e.limit_order,e.lu_limit_order,e.bid_amount,e.amount,
                      e.free_float,r.theme_id,t.canonical_name AS theme_name,
                      COALESCE(x.level1_name,'') AS level1_name
               FROM fact_limit_event e JOIN rel_limit_theme r USING(event_id)
               JOIN dim_theme t USING(theme_id)
               LEFT JOIN dim_theme_taxonomy x USING(theme_id)
               WHERE r.attribution_role='primary'
                 AND e.trade_date BETWEEN '20260801' AND '20260914'"""
        ).df()
    finally:
        con.close()
    facts = facts[(facts.tag == "涨停") &
                  ~facts.theme_name.isin(["ST板块", "ST摘帽", "次新股"]) &
                  (facts.level1_name != "ST与次新") &
                  ~facts.name.str.contains("ST|退", case=False, na=False) &
                  ~facts.ts_code.str.endswith(".BJ")].copy()
    daily = get_theme_daily(start_date="20260801", end_date="20260915")
    daily.trade_date = daily.trade_date.astype(str)
    daily = daily[(daily.level1_name != "ST与次新") &
                  ~daily.canonical_name.isin(["ST板块", "ST摘帽", "次新股"])].copy()
    calendar = sorted(daily.trade_date.unique())
    next_date = {d: calendar[i+1] for i, d in enumerate(calendar[:-1])}
    prior = facts[facts.status_raw.eq("2连板") & facts.trade_date.between("20260831", "20260914")].copy()
    prior["d0"] = prior.trade_date.map(next_date)
    prior = prior[prior.d0.between("20260901", "20260915")].copy()
    prior = prior.rename(columns={"trade_date": "dminus1"})
    height = facts.groupby(["trade_date", "theme_id"]).board_height.max().rename("theme_max_height")
    prior = prior.join(height, on=["dminus1", "theme_id"])
    # 已知的D0涨跌停幅度是开盘前信息，与6–8月候选剔除口径一致。
    day = get_day(ts_codes=prior.ts_code.unique().tolist(), start_date="20260901",
                  end_date="20260915", qfq=False, source="database_only")
    day.trade_date = day.trade_date.astype(str)
    limit = get_stk_limit(start_date="20260901", end_date="20260915", source="database_only")
    limit.trade_date = limit.trade_date.astype(str)
    band = prior[["d0", "ts_code"]].merge(
        day[["trade_date", "ts_code", "pre_close"]].rename(columns={"trade_date": "d0"}),
        on=["d0", "ts_code"], how="left", validate="one_to_one").merge(
            limit[["trade_date", "ts_code", "up_limit"]].rename(columns={"trade_date": "d0"}),
            on=["d0", "ts_code"], how="left", validate="one_to_one")
    prior = prior.loc[(band.up_limit / band.pre_close).gt(1.075).to_numpy()].copy()
    prior["order_float"] = prior.limit_order / prior.free_float.where(prior.free_float > 0)
    prior = prior.sort_values(["dminus1", "theme_id", "lu_time", "order_float", "ts_code"],
                              ascending=[True, True, True, False, True], na_position="last")
    prior["height_leader"] = prior.theme_max_height.eq(2) & prior.groupby(
        ["dminus1", "theme_id"]).cumcount().eq(0)
    dates_all = sorted(facts.trade_date.unique())
    date_index = {d: i for i, d in enumerate(dates_all)}
    stock_indices = {code: sorted({date_index[d] for d in part.trade_date})
                     for code, part in facts.groupby("ts_code")}
    wanted = set(prior[["dminus1", "theme_id"]].itertuples(index=False, name=None))
    score_top = {}
    for (d, theme), part in facts.groupby(["trade_date", "theme_id"]):
        if (d, theme) in wanted:
            top = _leader_rows(part, stock_indices, date_index, 1)
            if top:
                score_top[(d, theme)] = top[0]["ts_code"]
    prior["existing_score_leader"] = (prior.theme_max_height.eq(2) & prior.apply(
        lambda row: score_top.get((row.dminus1, row.theme_id)) == row.ts_code, axis=1))
    daily["eligible"] = daily.limit_up_count.ge(3) & daily.heat_score.ge(50)
    daily = daily.sort_values(["trade_date", "eligible", "heat_score", "theme_id"],
                              ascending=[True, False, False, True])
    daily["rank"] = daily[daily.eligible].groupby("trade_date").cumcount().add(1).reindex(daily.index)
    daily["prior_strong_theme"] = daily.eligible & daily["rank"].le(3)
    prior = prior.merge(daily[["trade_date", "theme_id", "prior_strong_theme"]].rename(
        columns={"trade_date": "dminus1"}), on=["dminus1", "theme_id"], how="left",
        validate="many_to_one")
    prior["prior_strong_theme"] = prior.prior_strong_theme.eq(True)
    return prior[prior.height_leader][["d0", "dminus1", "ts_code", "name", "theme_name",
                                        "height_leader", "existing_score_leader",
                                        "prior_strong_theme"]].copy()


def run(candidate_path: Path, graph_path: Path,
        support_ceiling_pct: float) -> tuple[pd.DataFrame, dict]:
    candidate = pd.read_csv(candidate_path, dtype={"trade_date": str, "dminus1": str, "ts_code": str})
    candidate = candidate[candidate.height_leader].copy()
    candidate = candidate.rename(columns={"trade_date": "d0"})
    assert len(candidate) == 264 and not candidate.duplicated(["d0", "ts_code"]).any()
    september = _september_candidates(graph_path)
    candidate = pd.concat([candidate, september], ignore_index=True)
    assert not candidate.duplicated(["d0", "ts_code"]).any()
    codes = sorted(candidate.ts_code.unique())
    bars = get_fifteenMin(ts_codes=codes, start_date="20260527", end_date="20260924")
    bars["trade_date"] = bars.trade_date.astype(int)
    bars = bars.sort_values(["ts_code", "datetime"])
    stock_bars = {code: part.reset_index(drop=True) for code, part in bars.groupby("ts_code")}
    calendar = sorted(bars.trade_date.astype(str).unique())
    completeness = bars.groupby(["ts_code", "trade_date"]).size()
    limits = get_stk_limit(start_date="20260601", end_date="20260924", source="database_only")
    limits["trade_date"] = limits.trade_date.astype(str)
    limit_map = {(r.trade_date, r.ts_code): (float(r.up_limit), float(r.down_limit))
                 for r in limits.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    rows = []
    for row in candidate.itertuples():
        stock = stock_bars.get(row.ts_code)
        if stock is None or row.d0 not in calendar:
            continue
        i = calendar.index(row.d0)
        if i < 1 or i + 7 >= len(calendar):
            continue
        required = calendar[i-1:i+8]
        if any(completeness.get((row.ts_code, int(date)), 0) != 16 for date in required):
            rows.append({"d0": row.d0, "ts_code": row.ts_code, "quality_ok": False})
            continue
        dminus1 = stock[stock.trade_date == int(row.dminus1)]
        assert len(dminus1) == 16 and str(calendar[i-1]) == row.dminus1
        second_close = float(dminus1.iloc[-1]["close"])
        support, restart, audit = _find_signals(
            stock, row.d0, second_close, calendar, support_ceiling_pct)
        # 二次启动仅为初判；后续两根已收完的K线持续守住突破线，才作延迟确认。
        # 这条规则是在第一轮结果之后追加的探索，不把9月再称为独立检验。
        confirmed = _signal_info(stock, None)
        early_rejection = False
        if restart["time"] is not None:
            next_two = stock[stock["datetime"] > pd.Timestamp(restart["time"])].head(2)
            if len(next_two) == 2:
                level = audit["restart_neckline"]
                trough_price = audit["restart_trough"]
                kept = next_two.close.ge(level).all() and next_two.low.gt(trough_price * .99).all()
                if kept:
                    confirmed = {"idx": None, "time": str(next_two.iloc[-1]["datetime"]),
                                 "close": float(next_two.iloc[-1]["close"]),
                                 "low": float(next_two.iloc[-1]["low"])}
                else:
                    early_rejection = True
        support_out = _outcome(stock, support, limit_map, calendar)
        restart_out = _outcome(stock, restart, limit_map, calendar)
        confirmed_out = _outcome(stock, confirmed, limit_map, calendar)
        result = {"d0": row.d0, "dminus1": row.dminus1, "ts_code": row.ts_code,
                  "name": row.name, "theme": row.theme_name,
                  "prior_strong_theme": bool(row.prior_strong_theme),
                  "existing_score_leader": bool(row.existing_score_leader),
                  "second_close": second_close, "quality_ok": audit["valid_window"], **audit}
        result["restart_rejected_within_two_bars"] = early_rejection
        for prefix, signal, outcome in [("support", support, support_out),
                                        ("restart", restart, restart_out),
                                        ("confirmed", confirmed, confirmed_out)]:
            result[f"{prefix}_signal_time"] = signal["time"]
            result[f"{prefix}_signal_close"] = signal["close"]
            result.update({f"{prefix}_{key}": value for key, value in outcome.items()})
        rows.append(result)
    frame = pd.DataFrame(rows)
    audit = {"candidates": len(candidate), "september_check_candidates": len(september),
             "support_ceiling_pct": support_ceiling_pct,
             "retained_rows": len(frame),
             "quality_ok": int(frame.quality_ok.eq(True).sum()),
             "fifteen_min_symbols": bars.ts_code.nunique(),
             "fifteen_min_stock_days_incomplete": int(completeness.ne(16).sum()),
             "first_day": min(calendar), "last_day": max(calendar),
             "rule": {"pullback": "D0-D+4 running low 2-10% below D-1 close; two later bars hold 0.2% above trough and rebound 1% with bullish close; refined version also closes <=D-1 close+1% and within support_ceiling_pct of trough",
                      "restart": "after support, within 32 bars: close above previous 4 highs +0.1%, rising 15m MA5, volume >=1.2x prior 4 median, price 96-106% of D-1 close",
                      "confirmed": "exploratory post-result stage: next two closed 15m candles stay above breakout neckline and their lows above 99% of observed trough",
                      "entry": "next 15m open below up limit, volume positive, <=3% gap above signal close",
                      "exit": "next and third trading-session 15:00 close, down-limit or zero-volume exit blocked; costs 0.31% round trip"}}
    return frame, audit


def summarize(frame: pd.DataFrame) -> dict:
    frame = frame[frame.quality_ok].copy()
    output = {}
    for label, period in [("jun_jul", frame[frame.d0 <= "20260731"]),
                          ("aug_not_blind", frame[frame.d0.between("20260801", "20260831")]),
                          ("sep_diagnostic", frame[frame.d0 >= "20260901"])]:
        output[label] = {
            "all": {"support": _summarize(period, "support"),
                    "restart": _summarize(period, "restart"),
                    "confirmed": _summarize(period, "confirmed")},
            "prior_strong_theme": {"support": _summarize(period[period.prior_strong_theme], "support"),
                                   "restart": _summarize(period[period.prior_strong_theme], "restart"),
                                   "confirmed": _summarize(period[period.prior_strong_theme], "confirmed")},
            "existing_score_leader": {"support": _summarize(period[period.existing_score_leader], "support"),
                                      "restart": _summarize(period[period.existing_score_leader], "restart"),
                                      "confirmed": _summarize(period[period.existing_score_leader], "confirmed")},
            "day_block_mean_95_pct": {prefix: _bootstrap_mean(period, f"{prefix}_dplus1_net")
                                       for prefix in ("support", "restart", "confirmed")},
        }
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default="outputs/one_min_limit_up_pilot/two_to_three_leaders_202606_202608/candidate_events.csv")
    parser.add_argument("--graph-db", default="offlineDataManager/data/db_theme_graph.duckdb")
    parser.add_argument("--output", default="outputs/fifteen_min_two_board_pullback_202606_20260915")
    parser.add_argument("--support-ceiling-pct", type=float, default=4.0,
                        help="回调承接确认价距观察低点的最大涨幅；>=999复现初版无上限")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    events, audit = run(Path(args.candidates), Path(args.graph_db), args.support_ceiling_pct)
    results = summarize(events)
    events.to_csv(out / "events.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"audit": audit, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
