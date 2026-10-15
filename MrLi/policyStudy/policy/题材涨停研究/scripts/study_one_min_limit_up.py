"""2026 年涨停触板与次日分钟路径的可复现小样本研究。

只读离线数据；固定规则，不用 D+1/D+2 信息决定 D/D+1 信号。
这不是逐笔委托回测：分钟 OHLC 不能还原封单队列、集合竞价和滑点。
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_kpl_list, get_oneMin, get_stk_limit


BUY_SLIPPAGE = 0.001
SELL_SLIPPAGE = 0.001
BUY_COMMISSION = 0.0003
SELL_COMMISSION = 0.0003
SELL_TAX = 0.0005
EPS = 0.005


def _day_summary(frame: pd.DataFrame, up: float | None, down: float | None) -> dict:
    frame = frame.sort_values("time_idx")
    if len(frame) != 240 or frame.time_idx.tolist() != list(range(240)):
        return {"complete": False, "bars": len(frame)}
    o = frame.open.to_numpy(dtype=float)
    h = frame.high.to_numpy(dtype=float)
    low = frame.low.to_numpy(dtype=float)
    c = frame.close.to_numpy(dtype=float)
    v = frame.vol.to_numpy(dtype=float)
    a = frame.amount.to_numpy(dtype=float)
    result = {
        "complete": True,
        "name": str(frame.iloc[0]["name"]),
        "open_0931": o[0], "close_0931": c[0], "open_0932": o[1], "open_0946": o[15],
        "open_1001": o[30], "close_0945": c[14], "close_1000": c[29],
        "vwap_0945": a[:15].sum() / v[:15].sum() if v[:15].sum() > 0 else np.nan,
        "vwap_1000": a[:30].sum() / v[:30].sum() if v[:30].sum() > 0 else np.nan,
        "low_1000": np.nanmin(low[:30]), "low_after_0946": np.nanmin(low[15:]), "last_close": c[-1],
        "max_high": np.nanmax(h), "vol_0932": v[1],
        "vol_0946": v[15], "vol_1001": v[30],
        "up_limit": up, "down_limit": down,
        "open_0932_down_limit": bool(down is not None and o[1] <= down + EPS),
    }
    if up is not None and np.isfinite(up):
        touched = np.flatnonzero((h >= up - EPS) & (v > 0))
        first = int(touched[0]) if len(touched) else None
        result["first_touch_idx"] = first
        result["first_touch_close_limit"] = bool(first is not None and c[first] >= up - EPS)
        result["first_touch_next_open"] = o[first + 1] if first is not None and first < 239 else np.nan
        result["first_touch_next_vol"] = v[first + 1] if first is not None and first < 239 else np.nan
        result["final_sealed"] = bool(c[-1] >= up - EPS)
        result["post_touch_below_minutes"] = int(np.sum(low[first + 1:] < up - EPS)) if first is not None else np.nan
        result["first_touch_bar_close"] = c[first] if first is not None else np.nan
    return result


def _net_return(entry: float, exit_: float) -> float:
    if not np.isfinite(entry) or not np.isfinite(exit_) or entry <= 0:
        return np.nan
    buy = entry * (1 + BUY_SLIPPAGE + BUY_COMMISSION)
    sell = exit_ * (1 - SELL_SLIPPAGE - SELL_COMMISSION - SELL_TAX)
    return sell / buy - 1


def _metrics(frame: pd.DataFrame, return_col: str) -> dict:
    valid = frame[return_col].dropna()
    if valid.empty:
        return {"n": 0}
    n = len(valid)
    hits = int((valid > 0).sum())
    p = hits / n
    z = 1.96
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return {
        "n": n, "net_mean_pct": round(float(valid.mean() * 100), 3),
        "net_median_pct": round(float(valid.median() * 100), 3),
        "net_win_rate_pct": round(100 * p, 2),
        "win_wilson_95_pct": [round(100 * (center - half), 2), round(100 * (center + half), 2)],
        "net_p10_pct": round(float(valid.quantile(.1) * 100), 3),
        "net_p90_pct": round(float(valid.quantile(.9) * 100), 3),
    }


def _day_bootstrap_diff(frame: pd.DataFrame, value: str, group: str,
                        a: str, b: str, seed: int = 20260928) -> dict:
    """按交易日整块重抽样的 a-b 平均收益差，不把同日股票当独立样本。"""
    subset = frame[frame[group].isin([a, b]) & frame[value].notna()]
    agg = subset.groupby(["trade_date", group])[value].agg(["sum", "count"]).unstack(group, fill_value=0)
    if a not in agg["sum"] or b not in agg["sum"]:
        return {"n_days": 0}
    values = np.column_stack((agg[("sum", a)], agg[("count", a)],
                              agg[("sum", b)], agg[("count", b)])).astype(float)
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(values), size=(3000, len(values)))
    totals = values[picks].sum(axis=1)
    good = (totals[:, 1] > 0) & (totals[:, 3] > 0)
    diffs = totals[good, 0] / totals[good, 1] - totals[good, 2] / totals[good, 3]
    actual = values[:, 0].sum() / values[:, 1].sum() - values[:, 2].sum() / values[:, 3].sum()
    return {"n_days": len(values), "a_n": int(values[:, 1].sum()), "b_n": int(values[:, 3].sum()),
            "a_minus_b_pct": round(100 * actual, 3),
            "day_bootstrap_95_pct": [round(100 * v, 3) for v in np.quantile(diffs, [.025, .975])]}


def build_study(start: str, end: str) -> tuple[pd.DataFrame, dict]:
    # 1 月起的日线只用于剔除上市不足 60 个有行情交易日的次新股票。
    daily = get_day(start_date="20260101", end_date="20260903", qfq=False, source="database_only")
    daily["trade_date"] = daily.trade_date.astype(str)
    daily = daily.sort_values(["ts_code", "trade_date"])
    daily["history_bars"] = daily.groupby("ts_code").cumcount()
    limits = get_stk_limit(start_date=start, end_date="20260903", source="database_only")
    limits["trade_date"] = limits.trade_date.astype(str)
    lim_map = {(r.trade_date, r.ts_code): (float(r.up_limit), float(r.down_limit))
               for r in limits.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    kpl = get_kpl_list(start_date=start, end_date=end, tags=["涨停", "炸板"], source="database_only")
    kpl["trade_date"] = kpl.trade_date.astype(str)
    kpl_map = {(r.trade_date, r.ts_code): (str(r.name), str(r.tag), str(r.lu_desc)) for r in kpl.itertuples()}
    touch = daily[(daily.trade_date >= start) & (daily.trade_date <= end)].merge(
        limits[["trade_date", "ts_code", "up_limit", "down_limit"]], on=["trade_date", "ts_code"]
    )
    touch = touch[(touch.up_limit / touch.pre_close > 1.075) &
                  (touch.high >= touch.up_limit - EPS) & (touch.history_bars >= 60) &
                  ~touch.ts_code.str.endswith(".BJ")].copy()
    dates = sorted(daily.trade_date.unique())
    date_next = {d: (dates[i + 1] if i + 1 < len(dates) else None,
                     dates[i + 2] if i + 2 < len(dates) else None)
                 for i, d in enumerate(dates)}
    need: dict[str, set[str]] = defaultdict(set)
    for row in touch.itertuples():
        d1, d2 = date_next[row.trade_date]
        for date in (row.trade_date, d1, d2):
            if date:
                need[date].add(row.ts_code)
    minute: dict[tuple[str, str], dict] = {}
    ingest_audit = {}
    for i, (date, codes) in enumerate(sorted(need.items()), 1):
        bars = get_oneMin(ts_codes=sorted(codes), trade_date=date,
                          columns=["ts_code", "name", "time_idx", "open", "high", "low", "close", "vol", "amount"])
        for code, group in bars.groupby("ts_code", sort=False):
            up, down = lim_map.get((date, code), (None, None))
            minute[(date, code)] = _day_summary(group, up, down)
        ingest_audit[date] = {"requested_stocks": len(codes), "returned_stocks": bars.ts_code.nunique()}
        if i % 10 == 0:
            print(f"minute read {i}/{len(need)} days, {len(minute)} stock-days", flush=True)
    rows = []
    for r in touch.itertuples():
        d0 = r.trade_date
        d1, d2 = date_next[d0]
        m0 = minute.get((d0, r.ts_code), {})
        m1 = minute.get((d1, r.ts_code), {})
        m2 = minute.get((d2, r.ts_code), {})
        name, tag, primary_theme = kpl_map.get((d0, r.ts_code), (m0.get("name", ""), "not_in_kpl", ""))
        if ("ST" in name.upper() or "ST" in str(m0.get("name", "")).upper() or
                primary_theme in {"ST板块", "ST摘帽", "次新股"}):
            continue
        quality = (m0.get("complete") and m1.get("complete") and m2.get("complete") and
                   m0.get("first_touch_idx") is not None and
                   abs(m0.get("last_close", np.nan) - float(r.close)) <= max(.03, .005 * float(r.close)) and
                   abs(m0.get("max_high", np.nan) - float(r.high)) <= max(.03, .005 * float(r.high)))
        row = {"trade_date": d0, "d1": d1, "d2": d2, "ts_code": r.ts_code, "name": name,
               "kpl_tag": tag, "limit_band": "20pct_plus" if r.up_limit / r.pre_close > 1.13 else "10pct",
               "history_bars": int(r.history_bars), "quality_ok": bool(quality),
               "daily_close": float(r.close), "up_limit": float(r.up_limit)}
        if not quality:
            rows.append(row)
            continue
        first = m0["first_touch_idx"]
        row.update({"first_touch_idx": first, "first_touch_bucket": "before_1000" if first <= 29 else ("1000_to_1400" if first <= 179 else "after_1400"),
                    "first_touch_close_limit": m0["first_touch_close_limit"],
                    "final_sealed": m0["final_sealed"], "post_touch_below_minutes": m0["post_touch_below_minutes"],
                    "d1_gap_0931_pct": 100 * (m1["open_0931"] / m0["last_close"] - 1),
                    "d1_0945_return_pct": 100 * (m1["close_0945"] / m0["last_close"] - 1),
                    "d1_1000_return_pct": 100 * (m1["close_1000"] / m0["last_close"] - 1),
                    "d1_0932_exit_blocked": m1["open_0932_down_limit"] or m1["vol_0932"] <= 0,
                    "d2_0932_exit_blocked": m2["open_0932_down_limit"] or m2["vol_0932"] <= 0,
                    "d1_0932_buyable": bool(m1["vol_0932"] > 0 and m1["open_0932"] < m1["up_limit"] - EPS),
                    "d0_after_touch_buyable": bool(first < 239 and m0["first_touch_next_vol"] > 0 and m0["first_touch_next_open"] < r.up_limit - EPS),
                    "d1_0946_buyable": bool(m1["vol_0946"] > 0 and m1["open_0946"] < m1["up_limit"] - EPS),
                    "d1_1001_buyable": bool(m1["vol_1001"] > 0 and m1["open_1001"] < m1["up_limit"] - EPS),
                    "d1_weak_strong_0945": bool(m0["final_sealed"] and m1["open_0931"] <= m0["last_close"] * .99 and
                                                 m1["close_0945"] >= m0["last_close"] and m1["close_0945"] >= m1["open_0931"] * 1.01),
                    "d1_weak_strong_any": bool(m1["open_0931"] <= m0["last_close"] * .99 and
                                                m1["close_0945"] >= m0["last_close"] and m1["close_0945"] >= m1["open_0931"] * 1.01),
                    "d1_strength_0945": bool(m0["final_sealed"] and m1["close_0945"] > m0["last_close"] and
                                              m1["close_0945"] > m1["open_0931"] and m1["close_0945"] > m1["vwap_0945"]),
                    "d1_reclaim_1000": bool(m0["final_sealed"] and m1["low_1000"] < m0["last_close"] * .995 and
                                              m1["close_1000"] > m0["last_close"] and m1["close_1000"] > m1["vwap_1000"]),
                    "d1_green_0931": bool(m0["final_sealed"] and m1["close_0931"] > m1["open_0931"] and
                                           m1["close_0931"] > m0["last_close"]),
                    "d1_0945_level": "below_prev_close" if m1["close_0945"] <= m0["last_close"] else
                                      ("0_to_3pct" if m1["close_0945"] <= m0["last_close"] * 1.03 else
                                       ("3_to_6pct" if m1["close_0945"] <= m0["last_close"] * 1.06 else "over_6pct")),
                    })
        # 第一笔是理论涨停价买入，不模拟队列，收益只是不可成交的纸面基准。
        row["d0_limit_paper_to_d1_0932_net"] = _net_return(r.up_limit, m1["open_0932"]) if not row["d1_0932_exit_blocked"] else np.nan
        row["d0_limit_paper_to_d1_close_net"] = _net_return(r.up_limit, m1["last_close"])
        row["d0_pullback_to_d1_0932_net"] = _net_return(m0["first_touch_next_open"], m1["open_0932"]) if row["d0_after_touch_buyable"] and not row["d1_0932_exit_blocked"] else np.nan
        row["d1_0932_to_d2_0932_net"] = _net_return(m1["open_0932"], m2["open_0932"]) if row["d1_0932_buyable"] and not row["d2_0932_exit_blocked"] else np.nan
        row["d1_0946_to_d2_0932_net"] = _net_return(m1["open_0946"], m2["open_0932"]) if row["d1_0946_buyable"] and not row["d2_0932_exit_blocked"] else np.nan
        row["d1_1001_to_d2_0932_net"] = _net_return(m1["open_1001"], m2["open_0932"]) if row["d1_1001_buyable"] and not row["d2_0932_exit_blocked"] else np.nan
        row["d1_0946_to_d2_close_mark_pct"] = 100 * (m2["last_close"] / m1["open_0946"] - 1) if row["d1_0946_buyable"] else np.nan
        row["d1_0946_later_mae_pct"] = 100 * (m1["low_after_0946"] / m1["open_0946"] - 1) if row["d1_0946_buyable"] else np.nan
        row["d1_0946_to_d1_close_pct"] = 100 * (m1["last_close"] / m1["open_0946"] - 1) if row["d1_0946_buyable"] else np.nan
        rows.append(row)
    audit = {"dates": [start, end], "raw_daily_limit_touch": len(touch), "kpl_rows": len(kpl),
             "minute_ingest": ingest_audit, "transaction_costs": {
                 "buy_slippage": BUY_SLIPPAGE, "sell_slippage": SELL_SLIPPAGE,
                 "buy_commission": BUY_COMMISSION, "sell_commission": SELL_COMMISSION, "sell_tax": SELL_TAX}}
    return pd.DataFrame(rows), audit


def summarize(events: pd.DataFrame) -> dict:
    q = events[events.quality_ok].copy()
    q["period"] = np.where(q.trade_date <= "20260731", "discover_jun_jul", "validate_aug")
    result = {"raw_after_exclusions": len(events), "quality_accepted": len(q),
              "quality_rejected": len(events) - len(q), "periods": {}}
    for period, x in q.groupby("period"):
        piece = {"n_events": len(x), "n_days": x.trade_date.nunique(),
                 "kpl_coverage_pct": round(100 * (x.kpl_tag != "not_in_kpl").mean(), 2),
                 "sealed_rate_pct": round(100 * x.final_sealed.mean(), 2),
                 "d0_first_touch": {}, "d1_after_sealed": {}}
        for bucket, y in x.groupby("first_touch_bucket"):
            piece["d0_first_touch"][bucket] = {
                "n": len(y), "sealed_rate_pct": round(100 * y.final_sealed.mean(), 2),
                "next_min_below_limit_pct": round(100 * y.d0_after_touch_buyable.mean(), 2),
                "paper_at_limit_d1_0932": _metrics(y, "d0_limit_paper_to_d1_0932_net"),
                "buyable_pullback_d1_0932": _metrics(y, "d0_pullback_to_d1_0932_net")}
        piece["d0_first_touch_bar"] = {}
        for stable, y in x.groupby("first_touch_close_limit"):
            piece["d0_first_touch_bar"]["close_at_limit" if stable else "touch_then_below"] = {
                "n": len(y), "sealed_rate_pct": round(100 * y.final_sealed.mean(), 2),
                "paper_at_limit_d1_0932": _metrics(y, "d0_limit_paper_to_d1_0932_net")}
        piece["d0_late_vs_early_paper"] = _day_bootstrap_diff(
            x[x.first_touch_bucket.isin(["after_1400", "before_1000"])],
            "d0_limit_paper_to_d1_0932_net", "first_touch_bucket", "after_1400", "before_1000")
        sealed = x[x.final_sealed]
        failed = x[x.final_sealed == False]
        for name, y, ret in [
            ("all_sealed_0932", sealed, "d1_0932_to_d2_0932_net"),
            ("all_sealed_0946", sealed, "d1_0946_to_d2_0932_net"),
            ("green_0931", sealed[sealed.d1_green_0931], "d1_0932_to_d2_0932_net"),
            ("weak_to_strong_0945", sealed[sealed.d1_weak_strong_0945], "d1_0946_to_d2_0932_net"),
            ("strength_0945", sealed[sealed.d1_strength_0945], "d1_0946_to_d2_0932_net"),
            ("reclaim_1000", sealed[sealed.d1_reclaim_1000], "d1_1001_to_d2_0932_net")]:
            piece["d1_after_sealed"][name] = {"signal_count": len(y),
                "buyable_count": int(y.d1_0932_buyable.sum()) if ret == "d1_0932_to_d2_0932_net" else
                                (int(y.d1_0946_buyable.sum()) if ret == "d1_0946_to_d2_0932_net" else int(y.d1_1001_buyable.sum())),
                "d2_open_exit_blocked": int((y.d2_0932_exit_blocked &
                    (y.d1_0932_buyable if ret == "d1_0932_to_d2_0932_net" else
                     (y.d1_0946_buyable if ret == "d1_0946_to_d2_0932_net" else y.d1_1001_buyable))).sum()),
                "d2_0932_net": _metrics(y, ret)}
        matched = sealed[sealed.d1_0932_to_d2_0932_net.notna() & sealed.d1_0946_to_d2_0932_net.notna() &
                         sealed.d1_1001_to_d2_0932_net.notna()]
        piece["d1_matched_timing"] = {"n": len(matched),
            "buy_0932": _metrics(matched, "d1_0932_to_d2_0932_net"),
            "buy_0946": _metrics(matched, "d1_0946_to_d2_0932_net"),
            "buy_1001": _metrics(matched, "d1_1001_to_d2_0932_net")}
        piece["d1_0945_level"] = {level: {"signal_count": len(y),
            "buyable_count": int(y.d1_0946_buyable.sum()),
            "d2_0932_net": _metrics(y, "d1_0946_to_d2_0932_net")}
            for level, y in sealed.groupby("d1_0945_level")}
        piece["d1_after_failed_board"] = {
            "all_failed_0946": {"signal_count": len(failed),
                "d2_0932_net": _metrics(failed, "d1_0946_to_d2_0932_net")},
            "weak_to_strong_0945": {"signal_count": int(failed.d1_weak_strong_any.sum()),
                "d2_0932_net": _metrics(failed[failed.d1_weak_strong_any], "d1_0946_to_d2_0932_net")}}
        piece["by_band"] = {band: {"n": len(y), "sealed_rate_pct": round(100 * y.final_sealed.mean(), 2),
                                   "d0_paper": _metrics(y, "d0_limit_paper_to_d1_0932_net")}
                            for band, y in x.groupby("limit_band")}
        result["periods"][period] = piece
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20260601")
    p.add_argument("--end", default="20260831")
    p.add_argument("--output", default="outputs/one_min_limit_up_pilot/202606_202608")
    args = p.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    events, audit = build_study(args.start, args.end)
    result = summarize(events)
    events.to_csv(output / "events.csv", index=False)
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
