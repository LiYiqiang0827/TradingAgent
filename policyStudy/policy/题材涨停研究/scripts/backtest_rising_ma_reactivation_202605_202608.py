"""Retrospective full-universe May-Aug 2026 audit of frozen chart templates.

The May-Aug sample is NOT an independent holdout: the bounds were written after
inspecting July and September examples. No intraday fill or exit is assumed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_adj_factor, get_day, get_kpl_list, get_stk_limit
from offlineDataManager.scripts.core.offline_db_client import DB_PATH_BASIC
from study_rising_ma_reactivation import MA_PERIODS, describe


START, END, HISTORY_START, FOLLOW_END = "20260501", "20260831", "20250901", "20260918"
ROUND_TRIP_COST = .004


def universe(start: str, end: str) -> list[str]:
    """Read only symbol keys directly; price/factor/limit data use provider APIs."""
    conn = sqlite3.connect(f"file:{DB_PATH_BASIC}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT DISTINCT ts_code FROM tbl_cn_day "
                            "WHERE trade_date BETWEEN ? AND ? ORDER BY ts_code",
                            (start, end)).fetchall()
        return [r[0] for r in rows if r[0].endswith((".SH", ".SZ"))]
    finally:
        conn.close()


def _stock_frame(raw: pd.DataFrame, adj: pd.DataFrame) -> pd.DataFrame:
    x = raw.merge(adj[["trade_date", "adj_factor"]], on="trade_date",
                  how="left", validate="one_to_one").sort_values("trade_date").reset_index(drop=True)
    x.trade_date = x.trade_date.astype(str)
    for col in ("open", "high", "low", "close", "vol", "pre_close", "adj_factor"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    for col in ("open", "high", "low", "close"):
        x[f"raw_{col}"] = x[col]
        # Prices times the contemporaneous factor are geometrically equivalent
        # to QFQ; no future reference factor is needed for ratios or returns.
        x[col] = x[col] * x.adj_factor
    x["raw_vol"] = x.vol
    for period in MA_PERIODS:
        x[f"ma{period}"] = x.close.rolling(period, min_periods=period).mean()
    return x


def _prefilter(stock: pd.DataFrame, start: str, end: str) -> pd.Series:
    x = stock
    common = (x.trade_date.between(start, end) &
              (x.index >= 145) & x.adj_factor.gt(0) &
              x[["open", "high", "low", "close", "raw_vol"]].notna().all(axis=1) &
              x.ma30.gt(x.ma30.shift(10)) & x.ma60.gt(x.ma60.shift(10)) &
              x.ma120.gt(x.ma120.shift(20)))
    early = (x.low.div(x.ma20).between(.92, 1.03) &
             x.close.ge(x.ma20 * .99) & x.ma20.gt(x.ma20.shift(10)))
    coil = (x.low.div(x.ma30).between(.90, 1.05) &
            x.close.ge(x.ma30 * .95) &
            x.close.ge(x.close.shift(1) * 1.05) &
            x.raw_vol.ge(x.raw_vol.shift(1).rolling(5).mean() * 1.15) &
            x.close.gt(x.high.shift(1).rolling(3).max()))
    return common & (early | coil)


def _first_wave_boards(stock: pd.DataFrame, peak_date: str,
                       kpl_dates: set[str], limit_map: dict) -> tuple[str, list[str]]:
    """Require a KPL sealed board after the recent pre-peak trough, through peak."""
    peak_hits = stock.index[stock.trade_date.eq(peak_date)]
    if len(peak_hits) != 1:
        return "", []
    peak_i = int(peak_hits[0])
    before = stock.iloc[max(0, peak_i - 30):peak_i]
    if before.empty:
        return "", []
    trough_i = int(before.low.idxmin())
    wave = stock.iloc[trough_i:peak_i + 1]
    matches = []
    for day in wave.itertuples():
        upper = limit_map.get(day.trade_date, (np.nan, np.nan))[0]
        # KPL is the primary sealed-board source; raw close/official cap checks
        # protect against a wrong tag or a changed daily-limit regime.
        if (day.trade_date in kpl_dates and np.isfinite(upper) and
                pd.notna(day.pre_close) and float(day.pre_close) > 0 and
                upper / float(day.pre_close) >= 1.075 and
                float(day.raw_close) >= upper - .005 and
                float(day.raw_vol) > 0):
            matches.append(day.trade_date)
    return str(stock.iloc[trough_i].trade_date), matches


def _wave_volume(stock: pd.DataFrame, start_date: str,
                 peak_date: str, signal_date: str) -> dict:
    """Compare both impulse and pullback with the pre-wave 20-bar baseline."""
    dates = {date: i for i, date in enumerate(stock.trade_date)}
    start_i, peak_i, signal_i = (dates[day] for day in
                                 (start_date, peak_date, signal_date))
    baseline = stock.raw_vol.iloc[start_i - 20:start_i].mean()
    first_wave = stock.raw_vol.iloc[start_i:peak_i + 1].mean()
    pullback = stock.raw_vol.iloc[peak_i + 1:signal_i].mean()
    whole = stock.raw_vol.iloc[start_i:signal_i + 1].mean()
    return {
        "pre_wave_volume": float(baseline),
        "first_wave_volume_vs_pre": float(first_wave / baseline) if baseline > 0 else np.nan,
        "pullback_volume_vs_pre": float(pullback / baseline) if baseline > 0 else np.nan,
        "whole_wave_volume_vs_pre": float(whole / baseline) if baseline > 0 else np.nan,
    }


def _shape_without_sustained_volume(result: dict) -> bool:
    """Keep a transparent denominator for the new volume gate."""
    for field in ("ma20_pullback_gates", "ma30_coil_restart_gates"):
        gates = result.get(field, {})
        if gates and all(value for key, value in gates.items()
                         if key not in {"first_wave_volume_sustained",
                                        "pullback_volume_sustained"}):
            return True
    return False


def _outcome(stock: pd.DataFrame, idx: int, calendar: list[str],
             cal_index: dict[str, int], limit_map: dict) -> dict:
    date = str(stock.iloc[idx].trade_date)
    cal_i = cal_index[date]
    by_date = stock.set_index("trade_date")
    out = {"entry_status": "missing_next_day", "entry_date": None,
           "net_5d_pct": None, "net_10d_pct": None,
           "max_close_gain_10d_pct": None, "min_close_gain_10d_pct": None,
           "blocked_exit_days_5d": None, "blocked_exit_days_10d": None}
    if cal_i + 1 >= len(calendar):
        return out
    entry_day = calendar[cal_i + 1]
    out["entry_date"] = entry_day
    future_days = calendar[cal_i + 1:min(cal_i + 11, len(calendar))]
    future = by_date.reindex(future_days)
    if future.close.notna().all() and len(future) == 10:
        anchor = float(stock.iloc[idx].close)
        out["max_close_gain_10d_pct"] = 100 * (float(future.close.max()) / anchor - 1)
        out["min_close_gain_10d_pct"] = 100 * (float(future.close.min()) / anchor - 1)
    if entry_day not in by_date.index:
        return out
    entry = by_date.loc[entry_day]
    up = limit_map.get(entry_day, (np.nan, np.nan))[0]
    if not np.isfinite(up):
        out["entry_status"] = "limit_data_missing"
        return out
    if entry.raw_open >= up - .005 or entry.raw_vol <= 0:
        out["entry_status"] = "locked_at_upper_or_no_volume"
        return out
    out["entry_status"] = "open_proxy_filled"
    for horizon in (5, 10):
        target_i = cal_i + 1 + horizon
        if target_i >= len(calendar):
            out[f"exit_status_{horizon}d"] = "right_censored"
            continue
        blocked = 0
        for day in calendar[target_i:min(target_i + 6, len(calendar))]:
            if day not in by_date.index:
                out[f"exit_status_{horizon}d"] = "quote_missing"
                break
            bar = by_date.loc[day]
            down = limit_map.get(day, (np.nan, np.nan))[1]
            if not np.isfinite(down):
                out[f"exit_status_{horizon}d"] = "limit_data_missing"
                break
            if bar.raw_open <= down + .005 or bar.raw_vol <= 0:
                blocked += 1
                continue
            out[f"exit_status_{horizon}d"] = "open_proxy_filled"
            out[f"exit_date_{horizon}d"] = day
            out[f"net_{horizon}d_pct"] = 100 * (float(bar.open) / float(entry.open) - 1 - ROUND_TRIP_COST)
            break
        else:
            out[f"exit_status_{horizon}d"] = "blocked_over_5_sessions"
        out[f"blocked_exit_days_{horizon}d"] = blocked
    return out


def _theme_context(rows: list[dict], kpl: pd.DataFrame, calendar: list[str]) -> None:
    if kpl.empty:
        return
    kpl = kpl.copy()
    kpl.trade_date = kpl.trade_date.astype(str)
    kpl = kpl[~kpl.name.fillna("").str.contains("ST|退", case=False) &
              ~kpl.ts_code.astype(str).str.endswith(".BJ") &
              ~kpl.theme.fillna("").str.contains("ST板块|ST摘帽|次新")]
    history = defaultdict(list)
    theme_days = defaultdict(set)
    for rec in kpl.itertuples():
        tags = tuple(dict.fromkeys(t.strip() for t in str(rec.theme).replace("、", ",").split(",") if t.strip()))
        history[rec.ts_code].append((rec.trade_date, tags))
        for tag in tags:
            theme_days[(rec.trade_date, tag)].add(rec.ts_code)
    for entries in history.values():
        entries.sort(key=lambda item: item[0])
    counts_by_day = defaultdict(list)
    for (day, _tag), members in theme_days.items():
        counts_by_day[day].append(len(members))
    cal_index = {date: i for i, date in enumerate(calendar)}
    for row in rows:
        date, code = row["trade_date"], row["ts_code"]
        board_day = row["first_wave_board_dates"].split("|")[-1]
        anchor = next((entry for entry in history[code] if entry[0] == board_day), None)
        if anchor is None:
            row.update(theme_link_status="no_first_wave_kpl_tag", theme_last_tag_date=None,
                       theme_width_today_max=None, theme_width_20d_max=None,
                       theme_rank_today_best=None)
            continue
        last_date, tags = anchor
        if not tags:
            row.update(theme_link_status="empty_first_wave_kpl_tag", theme_last_tag_date=last_date,
                       theme_width_today_max=None, theme_width_20d_max=None,
                       theme_rank_today_best=None)
            continue
        day_i = cal_index[date]
        prior20 = calendar[max(0, day_i - 19):day_i + 1]
        today_counts = {tag: len(theme_days[(date, tag)]) for tag in tags}
        best_tag = max(tags, key=lambda tag: today_counts[tag])
        best_rank = (1 + sum(other > today_counts[best_tag]
                             for other in counts_by_day[date])) if today_counts[best_tag] else None
        row.update(theme_link_status="first_wave_kpl_tag", theme_last_tag_date=last_date,
                   theme_tags="|".join(tags),
                   theme_width_today_max=today_counts[best_tag],
                   theme_best_tag_today=best_tag if today_counts[best_tag] else None,
                   theme_rank_today_best=best_rank,
                   theme_width_20d_max=max(len(theme_days[(day, tag)])
                                           for day in prior20 for tag in tags))


def _summarize(rows: pd.DataFrame, denominator: dict) -> dict:
    summary = {"window": [START, END], "data_through": FOLLOW_END,
               "interpretation": "retrospective shape audit, thresholds chosen after examples",
               **denominator, "daily_matches": int(len(rows)),
               "unique_stocks": int(rows.ts_code.nunique()) if len(rows) else 0,
               "types": dict(Counter(rows.template_observation)) if len(rows) else {}}
    for kind, group in rows.groupby("template_observation"):
        for horizon in (5, 10):
            values = pd.to_numeric(group[f"net_{horizon}d_pct"], errors="coerce").dropna()
            summary[f"{kind}_{horizon}d"] = {
                "matches": len(group), "filled": int(group.entry_status.eq("open_proxy_filled").sum()),
                "closed": len(values),
                "mean_net_pct": round(float(values.mean()), 3) if len(values) else None,
                "median_net_pct": round(float(values.median()), 3) if len(values) else None,
                "win_rate_pct": round(float((values > 0).mean() * 100), 2) if len(values) else None,
                "p10_net_pct": round(float(values.quantile(.1)), 3) if len(values) else None,
                "worst_net_pct": round(float(values.min()), 3) if len(values) else None,
            }
    return summary


def _episodes(rows: pd.DataFrame, calendar: list[str], gap: int = 10) -> pd.DataFrame:
    """One first observation per stock and subtype in each gap-sized window."""
    if rows.empty:
        return rows.copy()
    cal_index = {date: i for i, date in enumerate(calendar)}
    keep = []
    ordered = rows.sort_values(["ts_code", "template_observation", "trade_date"])
    for _, group in ordered.groupby(["ts_code", "template_observation"], sort=False):
        last = -10**9
        for idx, row in group.iterrows():
            now = cal_index[row.trade_date]
            if now - last >= gap:
                keep.append(idx)
                last = now
    return rows.loc[keep].sort_values(["trade_date", "ts_code"])


def run(output: Path, *, batch_size: int = 200) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    codes = universe(START, END)
    calendar = sorted(get_day(ts_code="000001.SZ", start_date=HISTORY_START,
                              end_date=FOLLOW_END, qfq=False,
                              source="database_only").trade_date.astype(str).unique())
    cal_index = {date: i for i, date in enumerate(calendar)}
    kpl = get_kpl_list(start_date=HISTORY_START, end_date=END,
                       tags="涨停", source="database_only")
    kpl.trade_date = kpl.trade_date.astype(str)
    kpl = kpl[~kpl.name.fillna("").str.contains("ST|退", case=False) &
              ~kpl.ts_code.astype(str).str.endswith(".BJ") &
              ~kpl.theme.fillna("").str.contains("ST板块|ST摘帽|次新")]
    board_dates = {code: set(part.trade_date) for code, part in kpl.groupby("ts_code")}
    rows: list[dict] = []
    audit = Counter()
    for offset in range(0, len(codes), batch_size):
        batch = codes[offset:offset + batch_size]
        raw = get_day(ts_codes=batch, start_date=HISTORY_START, end_date=FOLLOW_END,
                      qfq=False, source="database_only")
        adj = get_adj_factor(ts_codes=batch, start_date=HISTORY_START,
                             end_date=FOLLOW_END, source="database_only")
        limits = get_stk_limit(ts_codes=batch, start_date=HISTORY_START,
                               end_date=FOLLOW_END, source="database_only")
        raw.trade_date = raw.trade_date.astype(str)
        adj.trade_date = adj.trade_date.astype(str)
        limits.trade_date = limits.trade_date.astype(str)
        raw_groups = dict(tuple(raw.groupby("ts_code", sort=False)))
        adj_groups = dict(tuple(adj.groupby("ts_code", sort=False)))
        limit_groups = dict(tuple(limits.groupby("ts_code", sort=False)))
        for code in batch:
            if code not in raw_groups or code not in adj_groups:
                audit["stocks_missing_price_or_factor"] += 1
                continue
            stock = _stock_frame(raw_groups[code], adj_groups[code])
            valid = stock.trade_date.between(START, END)
            limit_part = limit_groups.get(code)
            if limit_part is None:
                audit["stocks_missing_limit"] += 1
                continue
            limit_map = {str(r.trade_date): (float(r.up_limit), float(r.down_limit))
                         for r in limit_part.itertuples()
                         if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
            stock["limit_ratio"] = [limit_map.get(day, (np.nan, np.nan))[0] / prior
                                    if np.isfinite(prior) and prior > 0 else np.nan
                                    for day, prior in zip(stock.trade_date, stock.raw_close.shift(1))]
            eligible = (valid & (stock.index >= 145) & stock.adj_factor.gt(0) &
                        stock.limit_ratio.ge(1.075) &
                        stock[["open", "high", "low", "close", "raw_vol"]].notna().all(axis=1))
            audit["raw_stock_days"] += int(valid.sum())
            audit["eligible_stock_days"] += int(eligible.sum())
            audit["excluded_stock_days"] += int(valid.sum() - eligible.sum())
            pre = _prefilter(stock, START, END) & eligible
            audit["prefilter_stock_days"] += int(pre.sum())
            for idx in np.flatnonzero(pre.to_numpy()):
                date = str(stock.iloc[idx].trade_date)
                result = describe(stock, date)
                if not _shape_without_sustained_volume(result):
                    continue
                audit["structural_matches_before_board_gate"] += 1
                wave_start, boards = _first_wave_boards(
                    stock, result["peak_date"], board_dates.get(code, set()), limit_map)
                if not boards:
                    audit["rejected_no_first_wave_sealed_board"] += 1
                    continue
                volume = _wave_volume(stock, wave_start, result["peak_date"], date)
                if (not np.isfinite(volume["first_wave_volume_vs_pre"]) or
                        volume["first_wave_volume_vs_pre"] < 1.5 or
                        not np.isfinite(volume["pullback_volume_vs_pre"]) or
                        volume["pullback_volume_vs_pre"] < 1.5):
                    audit["rejected_volume_not_sustained_above_pre_wave"] += 1
                    continue
                assert result["template_observation"] != "unmatched"
                result.update(ts_code=code, trade_date=date)
                result.update(first_wave_start=wave_start,
                              first_wave_board_count=len(boards),
                              first_wave_board_dates="|".join(boards))
                result.update(volume)
                result.update(_outcome(stock, idx, calendar, cal_index, limit_map))
                rows.append(result)
        print(f"scanned {min(offset + batch_size, len(codes))}/{len(codes)} codes; "
              f"eligible={audit['eligible_stock_days']} matches={len(rows)}", flush=True)
    _theme_context(rows, kpl, calendar)
    frame = pd.DataFrame(rows)
    if len(frame):
        frame = frame.sort_values(["trade_date", "ts_code"])
    frame.to_csv(output / "daily_matches.csv", index=False, encoding="utf-8-sig")
    summary = _summarize(frame, {"universe_stocks": len(codes), **dict(audit)})
    episodes = _episodes(frame, calendar)
    episodes.to_csv(output / "first_episode_matches.csv", index=False, encoding="utf-8-sig")
    summary["episodes"] = _summarize(episodes, {"gap_trading_sessions": 10})
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/rising_ma_reactivation/may_aug_2026"))
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()
    print(json.dumps(run(args.output, batch_size=args.batch_size), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
