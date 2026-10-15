"""Keep frozen setups; replay execution on local TDX-primary minute partitions.

No new signals, threshold search, or live orders. Minute labels are interval ends.
One-minute OHLC opens/volume are execution proxies, not exchange fill evidence.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_fifteenMin, get_oneMin, get_stk_limit, get_tradecal
from config.settings import ONE_MIN_CATALOG_PATH
from study_core_reactivation_causal import TIMES
from study_fifteen_min_core_reactivation import _synchronized_core_events
from study_fifteen_min_two_board_pullback import _net


DELAY = pd.Timedelta(seconds=60)
MINUTE = pd.Timedelta(minutes=1)


def minute_schedule(calendar: list[str]) -> list[pd.Timestamp]:
    return [stamp for day in calendar for start, end in (("09:31", "11:30"), ("13:01", "15:00"))
            for stamp in pd.date_range(f"{day} {start}", f"{day} {end}", freq="min")]


def _valid_factor(value: float, reference: float) -> bool:
    return bool(np.isfinite(value) and value > 0 and np.isfinite(reference) and reference > 0
                and abs(value / reference - 1) <= 1e-5)


def execute_minutes(one: pd.DataFrame, fifteen: pd.DataFrame, signal: dict,
                    calendar: list[str], limits: dict, preclose: dict,
                    carry: bool) -> dict:
    """First eligible scheduled entry only; minute-by-minute exits after T+1."""
    result = {"buyable": False, "status": "entry_beyond_calendar", "entry_bar_end": None,
              "entry_execution_at": None, "entry_open": None, "exit_bar_end": None,
              "exit_execution_at": None, "exit_open": None, "net": None,
              "decision_time": None, "reason": None, "blocked_minutes": 0,
              "mae": None, "mark_net": None, "mark_time": None,
              "entry_day_invalidation": None, "gap_time": None,
              "max_15m_1m_close_difference": 0.0}
    slots = minute_schedule(calendar)
    signal_at = pd.Timestamp(signal["signal_time"])
    # To fill at an open >= signal+60 seconds, the minute end must be >= signal+120.
    start = bisect_left(slots, signal_at + DELAY + MINUTE)
    if start >= len(slots):
        return result
    lookup = one.set_index("datetime")
    decisions = fifteen.set_index("datetime")
    entry_end = slots[start]
    result.update({"entry_bar_end": str(entry_end),
                   "entry_execution_at": str(entry_end - MINUTE)})
    if entry_end not in lookup.index:
        return {**result, "status": "entry_minute_missing", "gap_time": str(entry_end)}
    entry_bar = lookup.loc[entry_end]
    if isinstance(entry_bar, pd.DataFrame):
        return {**result, "status": "entry_duplicate_minute"}
    entry = float(entry_bar.open)
    factor = float(entry_bar.adj_factor)
    if not _valid_factor(factor, float(signal["signal_factor"])):
        return {**result, "status": "entry_factor_missing_or_changed"}
    day = entry_end.strftime("%Y%m%d")
    code = signal["ts_code"]
    up, down = limits.get((day, code), (None, None))
    previous = preclose.get((day, code))
    if up is None or down is None or previous is None:
        return {**result, "status": "entry_limit_unknown"}
    if up / previous <= 1.075:
        return {**result, "status": "excluded_limit_regime"}
    if not np.isfinite(entry) or entry <= 0 or entry > up + .011 or entry < down - .011:
        return {**result, "status": "entry_price_outside_limits"}
    if not np.isfinite(float(entry_bar.vol)) or entry_bar.vol <= 0:
        return {**result, "status": "entry_no_volume"}
    if entry >= up - .005:
        return {**result, "status": "entry_at_up_limit"}
    if entry > float(signal["signal_close"]) * 1.03:
        return {**result, "status": "entry_gap_above_cap"}
    result.update({"buyable": True, "status": "open_unresolved", "entry_open": entry})
    day_index = calendar.index(day)
    deadline = calendar[day_index + 3] if day_index + 3 < len(calendar) else None
    min_low, high_close = entry, entry
    pending = None
    for stamp in slots[start:]:
        open_at = stamp - MINUTE
        now_day = stamp.strftime("%Y%m%d")
        if stamp not in lookup.index:
            return {**result, "status": "holding_minute_missing", "gap_time": str(stamp),
                    "mae": min_low / entry - 1}
        bar = lookup.loc[stamp]
        if isinstance(bar, pd.DataFrame):
            return {**result, "status": "holding_duplicate_minute", "gap_time": str(stamp),
                    "mae": min_low / entry - 1}
        if not _valid_factor(float(bar.adj_factor), factor):
            return {**result, "status": "holding_factor_missing_or_changed", "gap_time": str(stamp),
                    "mae": min_low / entry - 1}
        lower = limits.get((now_day, code), (None, None))[1]
        if pending is not None and now_day > day and open_at >= pending + DELAY:
            if lower is None:
                return {**result, "status": "exit_limit_unknown", "gap_time": str(stamp),
                        "mae": min_low / entry - 1}
            if not np.isfinite(float(bar.open)) or not np.isfinite(float(bar.vol)):
                return {**result, "status": "exit_data_invalid", "gap_time": str(stamp),
                        "mae": min_low / entry - 1}
            upper = limits[(now_day, code)][0]
            if bar.open < lower - .011 or bar.open > upper + .011:
                return {**result, "status": "exit_price_outside_limits", "gap_time": str(stamp),
                        "mae": min_low / entry - 1}
            if bar.vol > 0 and bar.open > lower + .005:
                # The selling minute's low/close has not happened at the open.
                return {**result, "status": "closed", "exit_bar_end": str(stamp),
                        "exit_execution_at": str(open_at), "exit_open": float(bar.open),
                        "net": _net(entry, float(bar.open)),
                        "mae": min(min_low, float(bar.open)) / entry - 1}
            result["blocked_minutes"] += 1
        values = np.array([bar.open, bar.high, bar.low, bar.close], dtype=float)
        if not np.isfinite(values).all() or values.min() <= 0:
            return {**result, "status": "holding_ohlc_invalid", "gap_time": str(stamp),
                    "mae": min_low / entry - 1}
        min_low = min(min_low, float(bar.low))
        result.update({"mark_net": _net(entry, float(bar.close)), "mark_time": str(stamp)})
        if stamp.strftime("%H:%M") not in TIMES or pending is not None:
            continue
        # Only completed 15-minute bars can change the exit decision.
        if stamp not in decisions.index:
            return {**result, "status": "holding_15m_missing", "gap_time": str(stamp),
                    "mae": min_low / entry - 1}
        row = decisions.loc[stamp]
        if isinstance(row, pd.DataFrame):
            return {**result, "status": "holding_15m_duplicate", "gap_time": str(stamp),
                    "mae": min_low / entry - 1}
        if not _valid_factor(float(row.adj_factor), factor):
            return {**result, "status": "holding_15m_factor_mismatch", "gap_time": str(stamp),
                    "mae": min_low / entry - 1}
        close = float(row.close)
        if not np.isfinite(close) or close <= 0:
            return {**result, "status": "holding_15m_close_invalid", "gap_time": str(stamp),
                    "mae": min_low / entry - 1}
        result["max_15m_1m_close_difference"] = max(
            result["max_15m_1m_close_difference"], abs(close - float(bar.close)))
        high_close = max(high_close, close)
        failed = close <= entry * .97 or close <= float(signal["neckline"]) * .99
        trailed = high_close >= entry * 1.05 and close <= high_close * .97
        if now_day == day and (failed or trailed) and result["entry_day_invalidation"] is None:
            result["entry_day_invalidation"] = str(stamp)
        if now_day == day and not carry:
            continue
        reason = ("stop_or_neckline" if failed else "trailing_after_5pct" if trailed
                  else "day3_time_exit" if now_day == deadline and stamp.strftime("%H:%M") >= "14:45"
                  else None)
        if reason:
            pending = stamp
            result.update({"decision_time": str(stamp), "reason": reason})
    return {**result, "mae": min_low / entry - 1}


def summarize(frame: pd.DataFrame, prefix: str) -> dict:
    buy = frame[frame[f"{prefix}_buyable"].eq(True)]
    scored = buy[buy[f"{prefix}_net"].notna()]
    ret = scored[f"{prefix}_net"]
    dates = pd.to_datetime(scored.signal_time).dt.strftime("%Y%m%d")
    interval = None
    if len(ret) and dates.nunique() >= 2:
        agg = pd.DataFrame({"date": dates, "ret": ret}).groupby("date").ret.agg(["sum", "count"]).to_numpy(float)
        rng = np.random.default_rng(20260929)
        draws = agg[rng.integers(0, len(agg), size=(5000, len(agg)))].sum(axis=1)
        interval = (100 * np.quantile(draws[:, 0] / draws[:, 1], [.025, .975])).tolist()
    return {"signals": len(frame), "buyable": len(buy), "entry_unfilled": len(frame) - len(buy),
            "closed": len(ret), "unresolved": len(buy) - len(ret),
            "mean_net_pct": float(ret.mean() * 100) if len(ret) else None,
            "median_net_pct": float(ret.median() * 100) if len(ret) else None,
            "win_pct": float(ret.gt(0).mean() * 100) if len(ret) else None,
            "p10_net_pct": float(ret.quantile(.1) * 100) if len(ret) else None,
            "worst_pct": float(ret.min() * 100) if len(ret) else None,
            "best_pct": float(ret.max() * 100) if len(ret) else None,
            "date_block_mean_95_pct": interval,
            "remove_best_3_mean_net_pct": float(ret.sort_values().iloc[:-3].mean() * 100) if len(ret) > 3 else None,
            "extra_cost_per_100_of_entry_capital": {
                str(extra): {"mean_net_pct": float(ret.mean() * 100 - extra) if len(ret) else None,
                             "cost_definition": "additional yuan per 100 yuan initial trade capital, on top of existing approx 0.31 yuan round-trip cost"}
                for extra in (0.1, 0.25, 0.5, 1.0)},
            "closed_losses_below_10pct": int(ret.lt(-.1).sum()),
            "worst_observed_mae_pct": float(buy[f"{prefix}_mae"].min() * 100) if len(buy) else None,
            "blocked_exit_trades": int(buy[f"{prefix}_blocked_minutes"].gt(0).sum()),
            "distinct_signal_dates": int(pd.to_datetime(frame.signal_time).dt.date.nunique()),
            "status_counts": frame[f"{prefix}_status"].value_counts().to_dict()}


def source_audit(first: str, end: str, codes: list[str]) -> dict:
    con = duckdb.connect(str(ONE_MIN_CATALOG_PATH), read_only=True)
    try:
        rows = con.execute("""SELECT trade_date,source_path,data_source,status,rows,stock_count
                              FROM one_min_ingest_catalog WHERE trade_date BETWEEN ? AND ?
                              ORDER BY trade_date""", [first, end]).df()
        con.register("selected_symbols", pd.DataFrame({"ts_code": codes}))
        sync = con.execute("""SELECT t.ts_code,t.actual_start,t.actual_end,t.row_count,t.status
                              FROM one_min_tdx_sync t JOIN selected_symbols USING(ts_code)
                              ORDER BY t.ts_code""").df()
    finally:
        con.close()
    tdx = rows.source_path.str.startswith("tdx://") & rows.status.eq("imported")
    if not len(rows) or not tdx.all():
        raise RuntimeError("This comparison requires imported partitions with TDX as primary source")
    return {"catalog": str(ONE_MIN_CATALOG_PATH), "dates": len(rows),
            "first": str(rows.trade_date.min()), "last": str(rows.trade_date.max()),
            "data_sources": rows.data_source.value_counts().to_dict(),
            "source_paths_all_tdx": bool(tdx.all()), "stock_count_min": int(rows.stock_count.min()),
            "stock_count_max": int(rows.stock_count.max()),
            "all_partitions_pure_tdx": bool(rows.data_source.eq("tdx").all()),
            "mixed_partition_count": int(rows.data_source.ne("tdx").sum()),
            "row_level_source_proven": False,
            "provenance_limitation": "Mixed-day manifests retain fallback counts but no fallback symbol list; TDX sync ranges are not per-stock-day provenance",
            "selected_stock_tdx_sync": sync.to_dict("records"),
            "source_rows_sha256": hashlib.sha256(rows.to_json(orient="records").encode()).hexdigest()}


def aggregation_audit(one: pd.DataFrame, fifteen: pd.DataFrame,
                      trades: pd.DataFrame, out: Path) -> dict:
    """Ex-post holding-day QA; discrepancies never screen candidates or fills."""
    bought = trades[trades.minute_old_buyable.eq(True)]
    keys = set()
    entry_keys = set()
    for row in bought.to_dict("records"):
        start_day = int(pd.Timestamp(row["minute_old_entry_bar_end"]).strftime("%Y%m%d"))
        endpoint = max(pd.Timestamp(row[key]) for key in
                       ("minute_old_exit_bar_end", "minute_carry_exit_bar_end",
                        "minute_old_mark_time", "minute_carry_mark_time") if pd.notna(row[key]))
        end_day = int(endpoint.strftime("%Y%m%d"))
        code_days = one.loc[one.ts_code.eq(row["ts_code"]) & one.trade_date.between(start_day, end_day),
                            "trade_date"].unique()
        keys.update((row["ts_code"], int(day)) for day in code_days)
        entry_keys.add((row["ts_code"], start_day))
    mask = pd.Series(list(zip(one.ts_code, one.trade_date)), index=one.index).isin(keys)
    selected = one.loc[mask].sort_values(["ts_code", "datetime"]).copy()
    if selected.empty:
        return {"entry_stock_days": 0, "holding_stock_days": 0}
    selected["bucket"] = selected.datetime.dt.ceil("15min")
    agg = selected.groupby(["ts_code", "bucket"]).agg(
        minute_count=("datetime", "size"), open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), vol=("vol", "sum"),
        factor_min=("adj_factor", "min"), factor_max=("adj_factor", "max")).reset_index()
    merged = agg.merge(fifteen[["ts_code", "datetime", "open", "high", "low", "close", "vol"]],
                       left_on=["ts_code", "bucket"], right_on=["ts_code", "datetime"],
                       how="left", suffixes=("_from_1m", "_15m"), validate="one_to_one")
    merged["first_bar"] = merged.bucket.dt.strftime("%H:%M").eq("09:45")
    for column in ("open", "high", "low", "close", "vol"):
        merged[f"{column}_diff"] = merged[f"{column}_from_1m"] - merged[f"{column}_15m"]
    merged.to_csv(out / "holding_day_aggregation_audit.csv", index=False)
    groups = {}
    for label, part in (("first_0945", merged[merged.first_bar]),
                        ("other_15m_bars", merged[~merged.first_bar])):
        complete = part[part.minute_count.eq(15) & part.datetime.notna()]
        groups[label] = {"bars": len(part), "complete_paired_bars": len(complete),
                         "ohlc": {c: {"differ_by_more_than_1cent": int(complete[f"{c}_diff"].abs().gt(.011).sum()),
                                      "mean_abs_diff": float(complete[f"{c}_diff"].abs().mean()),
                                      "max_abs_diff": float(complete[f"{c}_diff"].abs().max())}
                                  for c in ("open", "high", "low", "close")},
                         "volume_differ_gt1unit": int(complete.vol_diff.abs().gt(1).sum())}
    deviations = merged[merged[[f"{c}_diff" for c in ("open", "high", "low", "close")]].abs().gt(.005).any(axis=1)].copy()
    deviations["bucket"] = deviations.bucket.astype(str)
    differences = deviations[["ts_code", "bucket", "first_bar", "open_diff", "high_diff", "low_diff", "close_diff"]]
    differences.to_csv(out / "holding_ohlc_differences.csv", index=False)
    return {"purpose": "ex-post full stock-days from entry through latest exit of either mode; no candidate/fill filtering",
            "entry_stock_days": len(entry_keys), "holding_stock_days": len(keys), "groups": groups,
            "ohlc_different_bar_count_gt_halfcent": len(differences),
            "ohlc_difference_examples": differences.head(30).to_dict("records")}


def run(input_path: Path, first: str, end: str, out: Path) -> dict:
    original = pd.read_csv(input_path, dtype={"ts_code": str})
    signals = original[pd.to_datetime(original.signal_time).ge(pd.Timestamp(first))].copy()
    if signals.empty:
        raise RuntimeError("No frozen signals in requested period")
    source = source_audit(first, end, sorted(signals.ts_code.unique()))
    calendar = sorted(get_tradecal(start_date=first, end_date=end, source="database_only").cal_date.astype(str))
    codes = sorted(signals.ts_code.unique())
    one = get_oneMin(ts_codes=codes, start_date=first, end_date=end)
    fifteen = get_fifteenMin(ts_codes=codes, start_date=first, end_date=end)
    for bars in (one, fifteen):
        bars.datetime = pd.to_datetime(bars.datetime)
        bars.trade_date = bars.trade_date.astype(int)
    one_by_stock = {code: x.sort_values("datetime") for code, x in one.groupby("ts_code")}
    fifteen_by_stock = {code: x.sort_values("datetime") for code, x in fifteen.groupby("ts_code")}
    bounds = get_stk_limit(start_date=first, end_date=end, source="database_only")
    limits = {(str(r.trade_date), r.ts_code): (float(r.up_limit), float(r.down_limit))
              for r in bounds.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    days = get_day(ts_codes=codes, start_date=first, end_date=end, qfq=False, source="database_only")
    preclose = {(str(r.trade_date), r.ts_code): float(r.pre_close) for r in days.itertuples()
                if pd.notna(r.pre_close) and r.pre_close > 0}
    rows = []
    for item in signals.to_dict("records"):
        code = item["ts_code"]
        mins = one_by_stock.get(code, one.iloc[:0])
        quarters = fifteen_by_stock.get(code, fifteen.iloc[:0])
        for mode, carry in (("old", False), ("carry", True)):
            outcome = execute_minutes(mins, quarters, item, calendar, limits, preclose, carry)
            item.update({f"minute_{mode}_{key}": value for key, value in outcome.items()})
        rows.append(item)
    frame = pd.DataFrame(rows)
    aggregation = aggregation_audit(one, fifteen, frame, out)
    sync = _synchronized_core_events(frame)
    groups = {"all": frame, "synchronized": sync}
    groups.update({month: part for month, part in frame.groupby(frame.signal_time.str[:7])})
    summaries = {label: {mode: summarize(part, f"minute_{mode}") for mode in ("old", "carry")}
                 for label, part in groups.items()}
    comparison = {}
    for mode in ("old", "carry"):
        paired = frame[frame[f"{mode}_net"].notna() & frame[f"minute_{mode}_net"].notna()]
        delta = paired[f"minute_{mode}_net"] - paired[f"{mode}_net"]
        comparison[mode] = {
            "input_15m_buyable": int(frame[f"{mode}_buyable"].eq(True).sum()),
            "minute_buyable": int(frame[f"minute_{mode}_buyable"].eq(True).sum()),
            "input_15m_scored": int(frame[f"{mode}_net"].notna().sum()),
            "input_15m_mean_net_pct": float(frame[f"{mode}_net"].mean() * 100),
            "paired_closed": len(paired), "paired_minute_minus_15m_mean_pp": float(delta.mean() * 100),
            "paired_worst_delta_pp": float(delta.min() * 100),
            "15m_unfillable_but_minute_buyable": int((frame[f"{mode}_buyable"].ne(True) & frame[f"minute_{mode}_buyable"].eq(True)).sum()),
            "15m_buyable_but_minute_unfillable": int((frame[f"{mode}_buyable"].eq(True) & frame[f"minute_{mode}_buyable"].ne(True)).sum()),
        }
    audit = {"input": str(input_path), "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
             "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             "first_signal_filter": first, "data_end": end, "input_total_signals": len(original),
             "selected_all_signals": len(signals), "selected_stocks": len(codes),
             "market_sessions": len(calendar), "source": source, "reaction_delay_seconds": 60,
             "minute_rows_loaded": len(one), "fifteen_rows_loaded": len(fifteen),
             "holding_day_1m_to_15m_aggregation_audit": aggregation,
             "candidate_filter": "frozen causal signals after date cutoff; NO 15m buyability filter",
             "cost": "same fixed ~0.31% round-trip costs as 15m baseline",
             "execution": "first scheduled minute open at least 60sec after signal; 1m end=open+60sec; T+1",
             "exit": "unchanged closed-15m neckline/3pct stop/5pct activation+3pct trail/D+3 time exit; 60sec latency",
             "summaries": summaries, "comparison_to_15m": comparison,
             "limitations": ["bar OHLC/volume proxies cannot prove queue fills or capacity",
                             "mixed-source dates may contain CSV fallback stocks; source_path and sync coverage alone cannot prove row-level TDX provenance",
                             "source signal cooldown and theme membership are frozen from prior exploratory study",
                             "factor changes cause unresolved/rejected status, not corporate-action accounting",
                             "no portfolio equity/cash or concurrent-position sizing", "2026 already explored, not untouched holdout"]}
    frame.to_csv(out / "trades.csv", index=False)
    sync.to_csv(out / "synchronized.csv", index=False)
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("outputs/core_reactivation_causal_2026/trades.csv"))
    parser.add_argument("--first", default="20260601")
    parser.add_argument("--data-end", default="20260924")
    parser.add_argument("--output", type=Path, default=Path("outputs/core_reactivation_minute_execution_2026"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run(args.input, args.first, args.data_end, args.output)


if __name__ == "__main__":
    main()
