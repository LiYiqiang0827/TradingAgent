"""Point-in-time daily pilot: 2-4 boards, rising MA20/30, volume, MA20 retest.

Signals are evaluated at the close, entered at the next tradable open, and
exited at an open after a close-based decision. This is research, not orders.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_fifteenMin, get_kpl_list, get_stk_limit


START = "20260601"
END = "20260831"
DATA_END = "20260928"
ROUND_TRIP_COST = 0.004  # 20 bp per side, combined fees/slippage assumption


def rising_run(values: pd.Series, index: int) -> int:
    """Consecutive strictly increasing MA observations ending at index."""
    run = 0
    while index > 0 and np.isfinite(values.iloc[index]) and np.isfinite(values.iloc[index-1]):
        if values.iloc[index] <= values.iloc[index-1]:
            break
        run += 1
        index -= 1
    return run


def _stock_frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values("trade_date").reset_index(drop=True).copy()
    for col in ("open", "high", "low", "close", "vol"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    # Forward-adjusted day API inversely adjusts volume. Compare actual traded
    # shares across the platform and retest, including corporate-action dates.
    out["vol"] = pd.to_numeric(out.raw_vol, errors="coerce")
    for period in (5, 10, 20, 30):
        out[f"ma{period}"] = out.close.rolling(period, min_periods=period).mean()
    out["trade_date"] = out.trade_date.astype(str)
    return out


def evaluate_run(stock: pd.DataFrame, first: int, peak: int, peak_height: int,
                 limit_dates: set[str], *, min_rising: int = 10,
                 volume_ratio: float = 1.5) -> dict:
    """Each gate uses only data through its decision day.

    Calling code waits for the first non-board day before treating peak_height
    as final, so filtering 5+ board runs does not leak their later outcome.
    """
    row = stock.iloc[first]
    result = {"first_date": row.trade_date, "peak_date": stock.iloc[peak].trade_date,
              "peak_height": peak_height, "stage": "insufficient_history",
              "touch_date": None, "signal_date": None,
              "rising_ma20_before_first": rising_run(stock.ma20, first - 1),
              "rising_ma30_before_first": rising_run(stock.ma30, first - 1)}
    if first < 35 or peak + 2 >= len(stock):
        return result
    pre = stock.iloc[first-20:first]
    if pre[["high", "low", "vol"]].isna().any().any() or pre.vol.mean() <= 0:
        return result
    result["pre_platform_range_pct"] = 100 * (pre.high.max() / pre.low.min() - 1)
    result["breakout_pct"] = 100 * (stock.iloc[peak].close / pre.high.max() - 1)
    if result["pre_platform_range_pct"] > 35 or result["breakout_pct"] < 3:
        result["stage"] = "no_platform_breakout"
        return result
    result["stage"] = "platform_breakout"
    if max(result["rising_ma20_before_first"], result["rising_ma30_before_first"]) < min_rising:
        result["stage"] = "ma_not_rising_long"
        return result
    result["stage"] = "long_rising_ma"
    # First lower/sideways non-board day is known. Retest search spans at most
    # 15 sessions; no condition refers to bars after a candidate touch.
    for touch in range(peak + 1, min(peak + 16, len(stock))):
        bar = stock.iloc[touch]
        if not np.isfinite(bar.ma20) or not np.isfinite(bar.ma30):
            break
        if bar.close < bar.ma20 * .97:
            result["stage"] = "lost_ma20_before_touch"
            break
        if bar.trade_date in limit_dates:
            continue
        if touch < peak + 2 or bar.low > bar.ma20 * 1.02 or bar.close < bar.ma20 * .98:
            continue
        result["touch_date"] = bar.trade_date
        result["touch_close_to_ma20_pct"] = 100 * (bar.close / bar.ma20 - 1)
        result["stage"] = "touched_ma20"
        interval = stock.iloc[first:touch+1]
        result["bull_alignment_share"] = float(
            ((interval.ma10 >= interval.ma20) & (interval.ma20 >= interval.ma30)).mean())
        result["ma_rising_share"] = float(
            ((interval.ma20.diff() > 0) | (interval.ma30.diff() > 0)).iloc[1:].mean())
        if (bar.ma20 <= bar.ma30 or result["bull_alignment_share"] < .70
                or result["ma_rising_share"] < .75):
            result["stage"] = "lost_bull_alignment"
            break
        result["stage"] = "bull_alignment"
        result["interval_volume_ratio"] = float(interval.vol.mean() / pre.vol.mean())
        pullback = stock.iloc[peak+1:touch+1]
        result["pullback_volume_ratio"] = float(pullback.vol.mean() / pre.vol.mean())
        if result["interval_volume_ratio"] < volume_ratio:
            result["stage"] = "volume_not_expanded"
            break
        result["stage"] = "volume_expanded"
        result["setup_ready"] = True
        for signal in range(touch, min(touch + 9, len(stock))):
            day = stock.iloc[signal]
            if day.close < day.ma20 * .97:
                result["stage"] = "lost_ma20_before_trigger"
                break
            prev = stock.iloc[signal-1]
            prev_high_close = stock.close.iloc[max(0, signal-3):signal].max()
            if (day.close >= prev.close * 1.05
                    and day.close > prev_high_close
                    and day.close > day.ma5):
                result["stage"] = "signal"
                result["signal_date"] = day.trade_date
                result["signal_gain_pct"] = 100 * (day.close / prev.close - 1)
                result["signal_close"] = float(day.close)
                result["signal_ma20"] = float(day.ma20)
                break
        break
    return result


def execute(stock: pd.DataFrame, signal_date: str, limits: dict,
            *, cost: float = ROUND_TRIP_COST) -> dict:
    """Daily-open execution proxy, with next-day and limit-lock constraints."""
    idx = stock.index[stock.trade_date == signal_date]
    if not len(idx):
        return {"trade_status": "signal_quote_missing"}
    i = int(idx[0]); entry_i = i + 1
    if entry_i >= len(stock):
        return {"trade_status": "right_censored_entry"}
    entry_bar = stock.iloc[entry_i]
    upper = limits.get(entry_bar.trade_date, (np.nan, np.nan))[0]
    if not np.isfinite(upper):
        return {"trade_status": "entry_limit_missing"}
    # qfq prices cannot be compared to raw limit prices. The raw day frame is
    # supplied separately by caller as raw_open/raw_high/raw_low columns.
    if (entry_bar.raw_open >= upper - .005 or entry_bar.raw_open <= 0
            or entry_bar.vol <= 0):
        return {"trade_status": "entry_limit_or_no_volume",
                "entry_date": entry_bar.trade_date}
    entry = float(entry_bar.open)
    result = {"trade_status": "unresolved", "entry_date": entry_bar.trade_date,
              "entry_price_qfq": entry, "decision_date": None, "exit_date": None,
              "exit_reason": None, "net_return_pct": None, "mae_pct": None,
              "mfe_pct": None, "blocked_exit_days": 0}
    max_close = entry
    min_low = entry
    max_high = entry
    pending = None
    for j in range(entry_i, min(entry_i + 21, len(stock))):
        day = stock.iloc[j]
        if j > entry_i and pending is not None:
            lower = limits.get(day.trade_date, (np.nan, np.nan))[1]
            if not np.isfinite(lower):
                result["trade_status"] = "exit_limit_missing"
                return result
            if day.raw_open > lower + .005 and day.vol > 0:
                result.update(trade_status="closed", exit_date=day.trade_date,
                              exit_reason=pending[1], decision_date=pending[0],
                              exit_price_qfq=float(day.open),
                              net_return_pct=100 * (float(day.open) / entry - 1 - cost),
                              mae_pct=100 * (min(min_low, float(day.open)) / entry - 1),
                              mfe_pct=100 * (max_high / entry - 1))
                return result
            result["blocked_exit_days"] += 1
            continue
        min_low = min(min_low, float(day.low))
        max_high = max(max_high, float(day.high))
        max_close = max(max_close, float(day.close))
        result["mae_pct"] = 100 * (min_low / entry - 1)
        result["mfe_pct"] = 100 * (max_high / entry - 1)
        result["mark_return_pct"] = 100 * (float(day.close) / entry - 1 - cost)
        if pending is not None:
            continue
        if day.close < day.ma20 * .99:
            pending = (day.trade_date, "ma20_close_break")
        elif j >= entry_i + 2 and j == entry_i + 2 and max_close < entry * 1.03:
            pending = (day.trade_date, "three_days_no_3pct_rise")
        elif max_close >= entry * 1.08 and day.close < max_close * .95:
            pending = (day.trade_date, "five_pct_trail_after_eight_pct_gain")
        elif j >= entry_i + 14:
            pending = (day.trade_date, "fifteen_day_cap")
    return result


def audit_fifteen_minute_opens(frame: pd.DataFrame, stocks: dict) -> dict:
    """Check each proxy fill against the first TDX 15-minute bar of its day."""
    pairs = set()
    for row in frame.itertuples():
        for field in ("early_entry_date", "early_exit_date", "entry_date", "exit_date"):
            day = getattr(row, field, None)
            if isinstance(day, str) and day:
                pairs.add((row.ts_code, day))
    if not pairs:
        return {"date_pairs": 0, "missing": 0, "not_16_bars": 0,
                "open_mismatch_over_0p01": 0}
    bars = get_fifteenMin(ts_codes=sorted({code for code, _ in pairs}),
                          start_date=min(day for _, day in pairs),
                          end_date=max(day for _, day in pairs))
    bars.trade_date = bars.trade_date.astype(str)
    summary = bars.sort_values("datetime").groupby(["ts_code", "trade_date"]).agg(
        count=("open", "size"), first_open=("open", "first"))
    raw_open = {(code, day.trade_date): float(day.raw_open)
                for code, stock in stocks.items() for day in stock.itertuples()}
    missing = bad_count = mismatch = 0
    for pair in pairs:
        if pair not in summary.index:
            missing += 1
            continue
        row = summary.loc[pair]
        bad_count += int(row["count"] != 16)
        mismatch += int(abs(row["first_open"] - raw_open[pair]) > .011)
    return {"date_pairs": len(pairs), "missing": missing,
            "not_16_bars": bad_count, "open_mismatch_over_0p01": mismatch}


def run(start: str, end: str, data_end: str, output: Path,
        *, min_rising: int = 10, volume_ratio: float = 1.5) -> dict:
    kpl = get_kpl_list(start_date="20260501", end_date=data_end,
                       tags="涨停", source="database_only")
    kpl = kpl.copy()
    kpl.trade_date = kpl.trade_date.astype(str)
    kpl["height"] = pd.to_numeric(kpl.status.astype(str).str.extract(
        r"^(\d+)连板$", expand=False), errors="coerce")
    kpl = kpl[~kpl.name.fillna("").str.contains("ST|退", case=False)
              & ~kpl.ts_code.str.endswith(".BJ")
              & ~kpl.theme.fillna("").str.contains("ST板块|ST摘帽|次新", case=False)]
    kpl = kpl.sort_values(["ts_code", "trade_date", "height"]).drop_duplicates(
        ["ts_code", "trade_date"], keep="last")
    two = kpl[kpl.height == 2].query("trade_date >= @start and trade_date <= @end")
    codes = sorted(two.ts_code.unique())
    if not codes:
        raise RuntimeError("No two-board candidates")
    # Full prehistory for rolling averages and platform detection, plus a
    # held-out follow-up tail after the June-August signal window.
    qfq = get_day(ts_codes=codes, start_date="20260301", end_date=data_end,
                  qfq=True, source="database_only")
    raw = get_day(ts_codes=codes, start_date="20260301", end_date=data_end,
                  qfq=False, source="database_only")
    raw = raw[["ts_code", "trade_date", "open", "high", "low", "vol"]].rename(
        columns={"open": "raw_open", "high": "raw_high", "low": "raw_low",
                 "vol": "raw_vol"})
    qfq = qfq.merge(raw, on=["ts_code", "trade_date"], how="left", validate="one_to_one")
    stocks = {code: _stock_frame(frame) for code, frame in qfq.groupby("ts_code")}
    limit_frame = get_stk_limit(ts_codes=codes, start_date=start,
                                end_date=data_end, source="database_only")
    limit_map = {(r.ts_code, str(r.trade_date)): (float(r.up_limit), float(r.down_limit))
                 for r in limit_frame.itertuples()
                 if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    board_map = {(r.ts_code, r.trade_date): r for r in kpl.itertuples()}
    board_dates = {code: set(part.trade_date) for code, part in kpl.groupby("ts_code")}
    rows = []
    prefix_checks = 0
    for candidate in two.itertuples():
        stock = stocks.get(candidate.ts_code)
        if stock is None or stock.empty:
            rows.append({"ts_code": candidate.ts_code, "name": candidate.name,
                         "two_date": candidate.trade_date, "theme": candidate.theme,
                         "stage": "daily_data_missing"})
            continue
        dates = stock.trade_date.tolist()
        positions = {date: i for i, date in enumerate(dates)}
        second = positions.get(candidate.trade_date)
        if second is None or second == 0:
            rows.append({"ts_code": candidate.ts_code, "name": candidate.name,
                         "two_date": candidate.trade_date, "theme": candidate.theme,
                         "stage": "first_board_missing"})
            continue
        first = second - 1
        if stock.iloc[first].trade_date not in board_dates.get(candidate.ts_code, set()):
            rows.append({"ts_code": candidate.ts_code, "name": candidate.name,
                         "two_date": candidate.trade_date, "theme": candidate.theme,
                         "stage": "first_board_missing"})
            continue
        peak, height = second, 2
        while peak + 1 < len(stock):
            next_date = stock.iloc[peak + 1].trade_date
            following = board_map.get((candidate.ts_code, next_date))
            if following is None or following.height != height + 1:
                break
            peak += 1; height += 1
        if height > 4:
            rows.append({"ts_code": candidate.ts_code, "name": candidate.name,
                         "two_date": candidate.trade_date, "theme": candidate.theme,
                         "peak_height": height, "stage": "excluded_five_plus"})
            continue
        if peak + 1 >= len(stock):
            rows.append({"ts_code": candidate.ts_code, "name": candidate.name,
                         "two_date": candidate.trade_date, "theme": candidate.theme,
                         "peak_height": height, "stage": "right_censored_peak"})
            continue
        result = evaluate_run(stock, first, peak, height,
                              board_dates.get(candidate.ts_code, set()),
                              min_rising=min_rising, volume_ratio=volume_ratio)
        result.update(ts_code=candidate.ts_code, name=candidate.name,
                      two_date=candidate.trade_date, theme=candidate.theme)
        if result.get("setup_ready"):
            touch_i = positions[result["touch_date"]]
            prefix = evaluate_run(stock.iloc[:touch_i+1], first, peak, height,
                                  {d for d in board_dates[candidate.ts_code]
                                   if d <= result["touch_date"]},
                                  min_rising=min_rising, volume_ratio=volume_ratio)
            assert prefix.get("setup_ready") and prefix["touch_date"] == result["touch_date"]
            prefix_checks += 1
            # Retrospective label, never used to form the setup or its entry.
            next10 = stock.iloc[touch_i+1:touch_i+11]
            if len(next10) == 10:
                touch_close = float(stock.iloc[touch_i].close)
                result["outcome_max_close_gain_10d_pct"] = 100 * (next10.close.max() / touch_close - 1)
                result["outcome_min_close_gain_10d_pct"] = 100 * (next10.close.min() / touch_close - 1)
                result["outcome_relimit_10d"] = bool(set(next10.trade_date) & board_dates[candidate.ts_code])
            else:
                result["outcome_10d_censored"] = True
        if result.get("signal_date"):
            signal_i = positions[result["signal_date"]]
            prefix = evaluate_run(stock.iloc[:signal_i+1], first, peak, height,
                                  {d for d in board_dates[candidate.ts_code]
                                   if d <= result["signal_date"]},
                                  min_rising=min_rising, volume_ratio=volume_ratio)
            assert prefix.get("signal_date") == result["signal_date"]
            prefix_checks += 1
        # If the trigger is outside the requested June-August window, retain
        # the candidate in the denominator but do not score a trade.
        if result.get("signal_date") and result["signal_date"] > end:
            result["stage"] = "signal_after_window"
        if result.get("setup_ready") and result["touch_date"] <= end:
            limit_by_date = {date: value for (code, date), value in limit_map.items()
                             if code == candidate.ts_code}
            early = execute(stock, result["touch_date"], limit_by_date)
            result.update({f"early_{key}": value for key, value in early.items()})
        if result["stage"] == "signal":
            limit_by_date = {date: value for (code, date), value in limit_map.items()
                             if code == candidate.ts_code}
            result.update(execute(stock, result["signal_date"], limit_by_date))
        rows.append(result)
    frame = pd.DataFrame(rows).sort_values(["two_date", "ts_code"])
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "candidate_audit.csv", index=False, encoding="utf-8-sig")
    closed = frame[frame.get("trade_status", pd.Series(index=frame.index)).eq("closed")]
    returns = pd.to_numeric(closed.net_return_pct, errors="coerce").dropna()
    early_closed = frame[frame.get("early_trade_status", pd.Series(index=frame.index)).eq("closed")]
    early_returns = pd.to_numeric(early_closed.early_net_return_pct, errors="coerce").dropna()
    in_window_setup = frame[frame.get("setup_ready", pd.Series(index=frame.index)).eq(True)
                            & frame.touch_date.astype(str).le(end)]
    outcome = pd.to_numeric(in_window_setup.outcome_max_close_gain_10d_pct, errors="coerce").dropna()
    stats = {"window": [start, end], "data_end": data_end,
             "all_two_board_runs": int(len(frame)),
             "unique_stocks": int(frame.ts_code.nunique()),
             "stage_counts": dict(Counter(frame.stage)),
             "signals": int((frame.stage == "signal").sum()),
             "trade_status_counts": dict(Counter(frame.trade_status.dropna())) if "trade_status" in frame else {},
             "closed": int(len(returns)), "mean_net_pct": float(returns.mean()) if len(returns) else None,
             "median_net_pct": float(returns.median()) if len(returns) else None,
             "win_rate_pct": float(100 * (returns > 0).mean()) if len(returns) else None,
             "worst_net_pct": float(returns.min()) if len(returns) else None,
             "best_net_pct": float(returns.max()) if len(returns) else None,
             "min_rising_days": min_rising, "volume_ratio_gate": volume_ratio,
             "round_trip_cost": ROUND_TRIP_COST}
    stats.update(setups_all_touch_dates=int(frame.get("setup_ready", pd.Series(index=frame.index)).eq(True).sum()),
                 setups_touch_within_window=int(len(in_window_setup)),
                 outcome_observed_10d=int(len(outcome)),
                 outcome_close_plus_10pct_10d=int((outcome >= 10).sum()),
                 outcome_relimit_10d=int(in_window_setup.outcome_relimit_10d.eq(True).sum()),
                 prefix_checks=prefix_checks,
                 early_trade_status_counts=dict(Counter(frame.early_trade_status.dropna()))
                 if "early_trade_status" in frame else {},
                 early_closed=int(len(early_returns)),
                 early_mean_net_pct=float(early_returns.mean()) if len(early_returns) else None,
                 early_median_net_pct=float(early_returns.median()) if len(early_returns) else None,
                 early_win_rate_pct=float(100 * (early_returns > 0).mean()) if len(early_returns) else None,
                 early_worst_net_pct=float(early_returns.min()) if len(early_returns) else None,
                 early_best_net_pct=float(early_returns.max()) if len(early_returns) else None)
    stats["fifteen_minute_open_audit"] = audit_fifteen_minute_opens(frame, stocks)
    (output / "summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default=START)
    parser.add_argument("--end-date", default=END)
    parser.add_argument("--data-end", default=DATA_END)
    parser.add_argument("--min-rising", type=int, default=10)
    parser.add_argument("--volume-ratio", type=float, default=1.5)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/ma20_second_wave_202606_202608"))
    args = parser.parse_args()
    print(json.dumps(run(args.start_date, args.end_date, args.data_end,
                         args.output_dir, min_rising=args.min_rising,
                         volume_ratio=args.volume_ratio), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
