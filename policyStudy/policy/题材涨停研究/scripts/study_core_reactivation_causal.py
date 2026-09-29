"""Causal replay of the existing core reactivation setup, without future quality gates.

Keeps the old setup thresholds fixed. Compares the old three-session trailing
exit with carrying an entry-day invalidation to the first eligible next open.
Full anchor and missing-data denominators are retained. No live orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_fifteenMin, get_stk_limit, get_theme_daily, get_tradecal
from study_fifteen_min_core_reactivation import _isolated_store, _synchronized_core_events
from study_fifteen_min_two_board_pullback import _net


TIMES = ["09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30",
         "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00"]


def schedule(calendar: list[str]) -> list[pd.Timestamp]:
    return [pd.Timestamp(f"{d} {t}") for d in calendar for t in TIMES]


def bar_open_time(stamp: pd.Timestamp) -> pd.Timestamp:
    return stamp - pd.Timedelta(minutes=15)


def find_setup(stock: pd.DataFrame, anchor: dict, daily_map: dict,
               calendar: list[str], as_of: pd.Timestamp) -> dict:
    """Output up to any cutoff depends only on bars and theme records up to cutoff."""
    a = str(anchor["anchor_date"])
    pos = calendar.index(a)
    base = {**anchor, "status": "pending", "signal_time": None}
    if as_of < pd.Timestamp(f"{a} 15:00"):
        return {**base, "status": "anchor_not_yet_known"}
    prior = stock[stock.trade_date.eq(int(a))].sort_values("datetime")
    if len(prior) != 16 or list(prior.datetime.dt.strftime("%H:%M")) != TIMES:
        return {**base, "status": "anchor_data_gap"}
    anchor_close = float(prior.iloc[-1].close)
    factor = float(prior.iloc[-1].adj_factor)
    if not np.isfinite(factor) or factor <= 0:
        return {**base, "status": "anchor_factor_missing"}
    lookup = stock.set_index("datetime")
    closes = prior.close.astype(float).tolist()
    highs, lows, volumes = [], [], []
    trough, trough_idx = anchor_close, None
    future_days = calendar[pos + 1:pos + 11]
    prior_day = {calendar[i]: calendar[i - 1] for i in range(1, len(calendar))}
    for i, stamp in enumerate(schedule(future_days)):
        if stamp > as_of:
            return base
        if stamp not in lookup.index:
            return {**base, "status": "observed_prefix_gap", "gap_time": str(stamp)}
        row = lookup.loc[stamp]
        if isinstance(row, pd.DataFrame):
            return {**base, "status": "duplicate_bar", "gap_time": str(stamp)}
        if not np.isfinite(float(row.adj_factor)) or abs(float(row.adj_factor) / factor - 1) > 1e-5:
            return {**base, "status": "observed_factor_change", "gap_time": str(stamp)}
        if min(row.open, row.high, row.low, row.close) <= 0:
            return {**base, "status": "invalid_bar", "gap_time": str(stamp)}
        closes.append(float(row.close)); highs.append(float(row.high))
        lows.append(float(row.low)); volumes.append(float(row.vol))
        if row.low < trough:
            trough, trough_idx = float(row.low), i
        if trough < anchor_close * .75:
            return {**base, "status": "invalidated_drawdown"}
        if i < 5 or trough_idx is None or i - trough_idx < 2 or trough > anchor_close * .92:
            continue
        if row.close > min(anchor_close * 1.10, trough * 1.22):
            continue
        if min(lows[-2:]) < trough * 1.001:
            continue
        theme = daily_map.get((prior_day[stamp.strftime("%Y%m%d")], anchor["theme_id"]))
        if theme is None or theme["heat"] < 50 or theme["width"] < 3 or not theme["rank"] <= 3:
            continue
        neck = max(highs[-5:-1])
        ma, previous_ma = np.mean(closes[-5:]), np.mean(closes[-6:-1])
        if (row.close > neck * 1.001 and row.close > ma > previous_ma and
                row.vol >= np.median(volumes[-5:-1]) * 1.2):
            return {**base, "status": "signal", "signal_time": str(stamp),
                    "signal_close": float(row.close), "anchor_close": anchor_close,
                    "signal_factor": factor,
                    "neckline": float(neck), "observed_trough": trough,
                    "drawdown_pct": 100 * (trough / anchor_close - 1),
                    "prior_theme_heat": theme["heat"], "prior_theme_rank": theme["rank"],
                    "prior_theme_width": theme["width"]}
    return {**base, "status": "expired" if len(future_days) == 10 else "pending"}


def execute(stock: pd.DataFrame, signal: dict, calendar: list[str],
            limit_map: dict, preclose_map: dict, carry_invalidation: bool) -> dict:
    """Use the immediately scheduled next bar, preserve missing and blocked exits."""
    base = {"buyable": False, "entry_bar_end": None, "entry_execution_at": None,
            "entry_open": None, "exit_bar_end": None, "exit_execution_at": None,
            "net": None, "status": "entry_missing", "decision_time": None,
            "reason": None, "blocked_bars": 0, "mae": None,
            "entry_day_invalidation": None, "mark_net": None}
    slots = schedule(calendar)
    signal_stamp = pd.Timestamp(signal["signal_time"])
    if signal_stamp not in slots or slots.index(signal_stamp) + 1 >= len(slots):
        return base
    idx = slots.index(signal_stamp) + 1
    entry_stamp = slots[idx]
    lookup = stock.set_index("datetime")
    if entry_stamp not in lookup.index:
        return base
    entry_bar = lookup.loc[entry_stamp]
    entry = float(entry_bar.open)
    day = entry_stamp.strftime("%Y%m%d")
    up = limit_map.get((day, signal["ts_code"]), (None, None))[0]
    previous_close = preclose_map.get((day, signal["ts_code"]))
    if up is None or previous_close is None:
        return {**base, "status": "entry_limit_unknown"}
    if up / previous_close <= 1.075:
        return {**base, "status": "excluded_limit_regime"}
    if (entry_bar.vol <= 0 or entry <= 0 or entry >= up - .005 or
            entry > signal["signal_close"] * 1.03):
        return {**base, "status": "entry_unfillable_proxy"}
    factor = float(entry_bar.adj_factor)
    if not np.isfinite(factor) or factor <= 0:
        return {**base, "status": "entry_factor_missing"}
    if abs(factor / signal["signal_factor"] - 1) > 1e-5:
        return {**base, "status": "entry_factor_change"}
    base.update({"buyable": True, "entry_bar_end": str(entry_stamp),
                 "entry_execution_at": str(bar_open_time(entry_stamp)), "entry_open": entry,
                 "status": "open_unresolved"})
    entry_day_idx = calendar.index(day)
    deadline = calendar[entry_day_idx + 3] if entry_day_idx + 3 < len(calendar) else None
    pending, reason, high_close = None, None, entry
    min_low = entry
    for stamp in slots[idx:]:
        if stamp not in lookup.index:
            return {**base, "status": "holding_data_gap", "gap_time": str(stamp),
                    "mae": min_low / entry - 1}
        row = lookup.loc[stamp]
        if not np.isfinite(float(row.adj_factor)) or abs(float(row.adj_factor) / factor - 1) > 1e-5:
            return {**base, "status": "holding_factor_change", "mae": min_low / entry - 1}
        now_day = stamp.strftime("%Y%m%d")
        # This open precedes the bar's high/low/close: do not include post-sale lows.
        if pending is not None and stamp > pending and now_day > day:
            lower = limit_map.get((now_day, signal["ts_code"]), (None, None))[1]
            if lower is None:
                return {**base, "status": "exit_limit_unknown", "mae": min_low / entry - 1}
            if row.vol > 0 and row.open > lower + .005:
                return {**base, "status": "closed", "net": _net(entry, float(row.open)),
                        "exit_bar_end": str(stamp), "exit_execution_at": str(bar_open_time(stamp)),
                        "decision_time": str(pending), "reason": reason,
                        "mae": min(min_low, float(row.open)) / entry - 1}
            base["blocked_bars"] += 1
        min_low = min(min_low, float(row.low))
        high_close = max(high_close, float(row.close))
        base["mark_net"] = _net(entry, float(row.close))
        failed = row.close <= entry * .97 or row.close <= signal["neckline"] * .99
        trailed = high_close >= entry * 1.05 and row.close <= high_close * .97
        if now_day == day and (failed or trailed) and base["entry_day_invalidation"] is None:
            base["entry_day_invalidation"] = str(stamp)
        if pending is not None:
            continue
        if now_day == day and not carry_invalidation:
            continue
        if failed:
            pending, reason = stamp, "stop_or_neckline"
        elif trailed:
            pending, reason = stamp, "trailing_after_5pct"
        elif now_day == deadline and stamp.strftime("%H:%M") >= "14:45":
            pending, reason = stamp, "day3_time_exit"
        if pending is not None:
            base.update({"decision_time": str(pending), "reason": reason})
    return {**base, "mae": min_low / entry - 1}


def _summary(frame: pd.DataFrame, prefix: str) -> dict:
    buy = frame[frame[f"{prefix}_buyable"].eq(True)]
    ret = buy[f"{prefix}_net"].dropna()
    return {"signals": len(frame), "buyable": len(buy), "closed": len(ret),
            "unresolved": int(buy[f"{prefix}_net"].isna().sum()),
            "mean_net_pct": float(100 * ret.mean()) if len(ret) else None,
            "median_net_pct": float(100 * ret.median()) if len(ret) else None,
            "win_pct": float(100 * ret.gt(0).mean()) if len(ret) else None,
            "worst_pct": float(100 * ret.min()) if len(ret) else None,
            "best_pct": float(100 * ret.max()) if len(ret) else None,
            "status_counts": frame[f"{prefix}_status"].value_counts().to_dict()}


def run(anchor_path: Path, graph_db: Path | None, data_end: str, out: Path) -> None:
    anchors = pd.read_csv(anchor_path, dtype={"anchor_date": str, "ts_code": str})
    anchors = anchors[~anchors.name.str.contains("ST|退", case=False, na=False) &
                      ~anchors.ts_code.str.endswith(".BJ")].copy()
    first = str(anchors.anchor_date.min())
    cal = sorted(get_tradecal(start_date=first, end_date=data_end,
                              source="database_only").cal_date.astype(str))
    daily = (_isolated_store(graph_db).query_theme_daily(start_date=first, end_date=data_end)
             if graph_db else get_theme_daily(start_date=first, end_date=data_end))
    daily.trade_date = daily.trade_date.astype(str)
    daily = daily[(daily.level1_name != "ST与次新") &
                  ~daily.canonical_name.isin(["ST板块", "ST摘帽", "次新股"])].copy()
    daily["eligible"] = daily.limit_up_count.ge(3) & daily.heat_score.ge(50)
    daily = daily.sort_values(["trade_date", "eligible", "heat_score", "theme_id"],
                              ascending=[True, False, False, True])
    daily["rank"] = daily[daily.eligible].groupby("trade_date").cumcount().add(1).reindex(daily.index)
    daily_map = {(r.trade_date, r.theme_id): {"heat": float(r.heat_score),
                 "width": int(r.limit_up_count), "rank": float(r.rank)} for r in daily.itertuples()}
    codes = sorted(anchors.ts_code.unique())
    bars = get_fifteenMin(ts_codes=codes, start_date=first, end_date=data_end)
    bars.trade_date = bars.trade_date.astype(int)
    bars.datetime = pd.to_datetime(bars.datetime)
    stocks = {code: x.sort_values("datetime").reset_index(drop=True) for code, x in bars.groupby("ts_code")}
    limits = get_stk_limit(start_date=first, end_date=data_end, source="database_only")
    limit_map = {(str(r.trade_date), r.ts_code): (float(r.up_limit), float(r.down_limit))
                 for r in limits.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    day = get_day(ts_codes=codes, start_date=first, end_date=data_end,
                  qfq=False, source="database_only")
    preclose = {(str(r.trade_date), r.ts_code): float(r.pre_close) for r in day.itertuples()
                if pd.notna(r.pre_close) and r.pre_close > 0}
    rows = []
    as_of = pd.Timestamp(f"{data_end} 15:00")
    prefix_checks = 0
    for a in anchors.to_dict("records"):
        stock = stocks.get(a["ts_code"], pd.DataFrame(columns=bars.columns))
        result = find_setup(stock, a, daily_map, cal, as_of)
        if result["status"] == "signal":
            stamp = pd.Timestamp(result["signal_time"])
            truncated = find_setup(stock[stock.datetime <= stamp], a, daily_map, cal, stamp)
            assert truncated == result, "Future bar removal changed an already visible signal"
            # A missing post-signal bar must not retroactively delete this signal.
            prefix_checks += 1
        rows.append(result)
    denominator = pd.DataFrame(rows)
    prospects = denominator[denominator.status.eq("signal")].sort_values(
        ["signal_time", "anchor_date"], ascending=[True, False]).drop_duplicates(["ts_code", "signal_time"])
    last_signal = {}
    trades = []
    for item in prospects.to_dict("records"):
        date = pd.Timestamp(item["signal_time"]).strftime("%Y%m%d")
        idx = cal.index(date)
        if idx - last_signal.get(item["ts_code"], -1000) <= 10:
            continue
        last_signal[item["ts_code"]] = idx
        for prefix, carry in [("old", False), ("carry", True)]:
            outcome = execute(stocks[item["ts_code"]], item, cal, limit_map, preclose, carry)
            item.update({f"{prefix}_{k}": v for k, v in outcome.items()})
        trades.append(item)
    frame = pd.DataFrame(trades)
    sync = _synchronized_core_events(frame) if len(frame) else frame.copy()
    result = {"anchors": len(anchors), "anchor_status": denominator.status.value_counts().to_dict(),
              "prefix_invariance_checks": prefix_checks,
              "prospects_before_cooldown": len(prospects), "signals": len(frame),
              "theme_data_end": str(daily.trade_date.max()), "bar_data_end": str(bars.trade_date.max()),
              "source_anchor_sha256": hashlib.sha256(anchor_path.read_bytes()).hexdigest(),
              "rule_status": "unchanged setup thresholds; causal repair and one predeclared exit comparison; exploratory",
              "all": {p: _summary(frame, p) for p in ("old", "carry")},
              "synchronized": {p: _summary(sync, p) for p in ("old", "carry")}}
    denominator.to_csv(out / "anchor_audit.csv", index=False)
    frame.to_csv(out / "trades.csv", index=False)
    sync.to_csv(out / "synchronized.csv", index=False)
    (out / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--anchors", type=Path, required=True)
    p.add_argument("--graph-db", type=Path)
    p.add_argument("--data-end", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    run(args.anchors, args.graph_db, args.data_end, args.output)


if __name__ == "__main__":
    main()
