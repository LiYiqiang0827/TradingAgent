"""Replay a frozen September 15m coil rule, with 1m execution proxies.

The entry decision is 15m; execution waits >=60 seconds and uses a scheduled
minute open. Horizons are entry day +1/+3/+5 sessions, planned 14:45 exits.
Missing/blocked/censored outcomes remain in the complete signal denominator.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import time

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "offlineDataManager/scripts"))
from coreClient.data_provider import (get_basic, get_day, get_fifteenMin, get_oneMin,
                                      get_stk_limit, get_theme_daily, get_tradecal)
from study_fifteen_min_ma_coil import Rules, features, find_signals, save_rules, load_rules

EXAMPLES = ["600371.SH", "000978.SZ", "600540.SH", "000993.SZ", "605577.SH", "600865.SH", "002790.SZ"]
BUY_SLIPPAGE = SELL_SLIPPAGE = .001
COMMISSION = .0003
SELL_TAX = .0005


def net(entry: float, exit_price: float) -> float:
    return exit_price * (1 - SELL_SLIPPAGE - COMMISSION - SELL_TAX) / (
        entry * (1 + BUY_SLIPPAGE + COMMISSION)) - 1


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str, allow_nan=False))


def scan_stock(args: tuple) -> tuple[pd.DataFrame, dict]:
    code, raw, calendar, rules, first, last = args
    name = str(raw.name.dropna().iloc[-1]) if "name" in raw and raw.name.notna().any() else code
    try:
        f = features(raw, calendar, rules)
    except ValueError as exc:
        return pd.DataFrame(), {"ts_code": code, "name": name, "error": str(exc)}
    mask = f.datetime.dt.strftime("%Y%m%d").between(first, last)
    s = f[mask]
    signs = find_signals(f, code, name, rules, first, last)
    audit = {"ts_code": code, "name": name, "scheduled_bars": int(mask.sum()),
             "observed_bars": int(s.valid_bar.sum()),
             "ma250_ready_bars": int(s.ma250.notna().sum()),
             "cluster_bars": int(s.cluster_only.sum()), "coil_ready_bars": int(s.coil_ready.sum()),
             "signals": len(signs), "error": None}
    if code in EXAMPLES:
        audit["cluster_long_ok_bars"] = int((s.cluster_only & s.long_trend_ok).sum())
        audit["cluster_origin_ok_bars"] = int((s.cluster_only & s.origin.ne("none")).sum())
        audit["cluster_slow_pullback_bars"] = int((s.cluster_only & s.slow_pullback).sum())
    return signs, audit


def minute_slots(calendar: list[str]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([x for day in calendar for a, b in (("09:31", "11:30"), ("13:01", "15:00"))
                            for x in pd.date_range(f"{day} {a}", f"{day} {b}", freq="min")])


def execute(signal: dict, one: pd.DataFrame, calendar: list[str], limits: dict, preclose: dict,
            allow_limit_fill: bool = False, prepared: pd.DataFrame | None = None) -> dict:
    result = {"entry_status": "entry_right_censored", "entry_time": None, "entry_bar_end": None,
              "entry_open": None, "buyable_proxy": False, "limit_fill_assumption": False}
    for h in (1, 3, 5):
        result.update({f"d{h}_status": "not_entered", f"d{h}_net": None,
                       f"d{h}_exit_time": None, f"d{h}_blocked_minutes": 0,
                       f"d{h}_mae": None, f"d{h}_mfe": None})
    source_name = str(signal.get("name", "")).upper()
    if "ST" in source_name or "退" in source_name:
        return {**result, "entry_status": "excluded_st_source_name"}
    age = signal.get("listing_age_days")
    if age is not None and pd.notna(age) and float(age) < 365:
        return {**result, "entry_status": "excluded_recent_listing_under_365days"}
    schedule = minute_slots(calendar)
    stamp = pd.Timestamp(signal["signal_time"])
    signal_day, code = stamp.strftime("%Y%m%d"), signal["ts_code"]
    signal_up = limits.get((signal_day, code), (None, None))[0]
    assumed = (allow_limit_fill and signal_up is not None
               and abs(float(signal.get("signal_close", -1)) - signal_up) <= .005)
    # Minute labels are interval ends: the open must be >= signal + 60 seconds.
    idx = bisect_left(schedule, stamp + pd.Timedelta(minutes=1 if assumed else 2))
    if idx >= len(schedule) and not assumed:
        return result
    entry_end = schedule[idx] if idx < len(schedule) else stamp
    result.update({"entry_time": str(stamp if assumed else entry_end - pd.Timedelta(minutes=1)),
                   "entry_bar_end": None if assumed else str(entry_end),
                   "limit_fill_assumption": assumed})
    if one.empty and not assumed:
        return {**result, "entry_status": "entry_minute_missing"}
    b = one.copy(); b["datetime"] = pd.to_datetime(b.datetime)
    duplicates = set(b.loc[b.datetime.duplicated(keep=False), "datetime"])
    lookup = {r.datetime: r for r in b.itertuples(index=False)}
    if entry_end in duplicates and not assumed:
        return {**result, "entry_status": "duplicate_entry_minute"}
    if entry_end not in lookup and not assumed:
        return {**result, "entry_status": "entry_minute_missing"}
    row = lookup.get(entry_end)
    day = signal_day if assumed else entry_end.strftime("%Y%m%d")
    up, down = limits.get((day, code), (None, None))
    prior = preclose.get((day, code))
    if up is None or down is None or prior is None or not np.isfinite(prior) or prior <= 0:
        return {**result, "entry_status": "entry_limit_unknown"}
    if up / prior <= 1.075:
        return {**result, "entry_status": "excluded_st_limit_regime"}
    entry = float(signal_up) if assumed else float(row.open)
    factor = float(signal["signal_factor"]) if assumed else float(row.adj_factor)
    volume = 1. if assumed else float(row.vol)
    if not np.isfinite([entry, factor, volume]).all() or entry <= 0 or factor <= 0:
        return {**result, "entry_status": "entry_invalid_data"}
    if abs(factor / signal["signal_factor"] - 1) > 1e-5:
        return {**result, "entry_status": "entry_factor_change_or_mismatch"}
    if not down - .011 <= entry <= up + .011:
        return {**result, "entry_status": "entry_outside_price_limits"}
    if volume <= 0:
        return {**result, "entry_status": "entry_no_volume"}
    if entry >= up - .005 and not assumed:
        return {**result, "entry_status": "entry_at_up_limit"}
    result.update({"entry_status": "limit_fill_assumption" if assumed else "open_proxy",
                   "buyable_proxy": True, "entry_open": entry})
    entry_day_idx = calendar.index(day)
    for h in (1, 3, 5):
        if entry_day_idx + h >= len(calendar):
            result[f"d{h}_status"] = "right_censored"
            continue
        planned = pd.Timestamp(f"{calendar[entry_day_idx + h]} 14:46")
        exit_start = bisect_left(schedule, planned)
        min_low, max_high = entry, entry
        blocked = 0
        for j in range(idx, len(schedule)):
            t = schedule[j]
            if t in duplicates:
                result[f"d{h}_status"] = "holding_duplicate_minute"
                break
            if t not in lookup:
                result[f"d{h}_status"] = "holding_minute_missing"
                break
            r = lookup[t]
            if not np.isfinite([r.open, r.high, r.low, r.close, r.vol, r.adj_factor]).all():
                result[f"d{h}_status"] = "holding_invalid_data"
                break
            if abs(float(r.adj_factor) / factor - 1) > 1e-5:
                result[f"d{h}_status"] = "holding_factor_change_or_mismatch"
                break
            now = t.strftime("%Y%m%d")
            upper, lower = limits.get((now, code), (None, None))
            if j >= exit_start:
                if upper is None or lower is None:
                    result[f"d{h}_status"] = "exit_limit_unknown"
                    break
                if not lower - .011 <= float(r.open) <= upper + .011:
                    result[f"d{h}_status"] = "exit_outside_price_limits"
                    break
                if r.vol > 0 and r.open > lower + .005:
                    # Exclude the exit minute's future low/high from holding excursions.
                    result.update({f"d{h}_status": "closed_proxy",
                                   f"d{h}_net": net(entry, float(r.open)),
                                   f"d{h}_exit_time": str(t - pd.Timedelta(minutes=1)),
                                   f"d{h}_mae": min(min_low, float(r.open)) / entry - 1,
                                   f"d{h}_mfe": max(max_high, float(r.open)) / entry - 1})
                    break
                blocked += 1
            min_low, max_high = min(min_low, float(r.low)), max(max_high, float(r.high))
        else:
            result[f"d{h}_status"] = "blocked_until_cutoff"
        result[f"d{h}_blocked_minutes"] = blocked
    return result


def context(signals: pd.DataFrame, calendar: list[str], out: Path) -> pd.DataFrame:
    daily = get_theme_daily(start_date="20260801", end_date=calendar[-1])
    daily.to_parquet(out / "inputs/theme_daily.parquet", index=False)
    daily = daily[daily.level1_name.ne("ST与次新")].copy()
    daily.trade_date = daily.trade_date.astype(str)
    daily = daily.sort_values(["trade_date", "heat_score", "limit_up_count", "canonical_name"],
                              ascending=[True, False, False, True])
    daily["rank"] = daily.groupby("trade_date").cumcount() + 1
    heat = {(r.trade_date, r.theme_id): r._asdict() for r in daily.itertuples(index=False)}
    with duckdb.connect(str(ROOT / "offlineDataManager/data/db_theme_graph.duckdb"), read_only=True) as conn:
        events = conn.execute("""SELECT e.trade_date,e.ts_code,e.tag,e.board_height,
                  r.theme_id,t.canonical_name AS theme_name
             FROM fact_limit_event e JOIN rel_limit_theme r USING(event_id)
             JOIN dim_theme t USING(theme_id)
             LEFT JOIN dim_theme_taxonomy x USING(theme_id)
             WHERE e.trade_date BETWEEN '20260801' AND ? AND e.tag='涨停'
               AND r.attribution_role='primary' AND COALESCE(x.level1_name,'') <> 'ST与次新'
             ORDER BY e.trade_date,e.ts_code,r.theme_id""", [calendar[-1]]).df()
    events.to_parquet(out / "inputs/theme_events.parquet", index=False)
    groups = {code: frame for code, frame in events.groupby("ts_code")}
    date_idx = {d: i for i, d in enumerate(calendar)}
    rows = []
    for r in signals.to_dict("records"):
        day = pd.Timestamp(r["signal_time"]).strftime("%Y%m%d")
        i = date_idx[day]; prior = calendar[i - 1]
        frame = groups.get(r["ts_code"], events.iloc[:0])
        past = frame[frame.trade_date.isin(calendar[max(0, i - 10):i])]
        if len(past):
            # Most recent completed-day primary KPL theme. No current-day attribution.
            chosen = past.iloc[-1]; d = heat.get((prior, chosen.theme_id), {})
            r.update({"prior_theme_name": chosen.theme_name, "prior_theme_id": chosen.theme_id,
                      "known_limit_date": chosen.trade_date, "recent_limit_up_10sessions": True,
                      "prior_max_board_height": int(past.board_height.max()),
                      "prior_theme_heat": d.get("heat_score"), "prior_theme_width": d.get("limit_up_count"),
                      "prior_theme_rank": d.get("rank"),
                      "prior_strong_theme": bool(d and d["heat_score"] >= 50
                                                  and d["limit_up_count"] >= 3 and d["rank"] <= 3)})
        else:
            r.update({"prior_theme_name": None, "prior_theme_id": None, "known_limit_date": None,
                      "recent_limit_up_10sessions": False, "prior_max_board_height": None,
                      "prior_theme_heat": None, "prior_theme_width": None, "prior_theme_rank": None,
                      "prior_strong_theme": False})
        r["context_date"] = prior
        rows.append(r)
    return pd.DataFrame(rows)


def summary(trades: pd.DataFrame) -> dict:
    out = {"signals": len(trades), "stocks": int(trades.ts_code.nunique()) if len(trades) else 0}
    if trades.empty:
        return out
    out["entry_statuses"] = {str(k): int(v) for k, v in trades.entry_status.value_counts().items()}
    out["buyable_proxies"] = int(trades.buyable_proxy.sum())
    for h in (1, 3, 5):
        r = trades[f"d{h}_net"].dropna()
        out[f"d{h}"] = {"resolved": len(r),
                         "statuses": {str(k): int(v) for k, v in trades[f"d{h}_status"].value_counts().items()},
                         "mean_net_pct": float(100 * r.mean()) if len(r) else None,
                         "median_net_pct": float(100 * r.median()) if len(r) else None,
                         "win_pct": float(100 * r.gt(0).mean()) if len(r) else None,
                         "p10_net_pct": float(100 * r.quantile(.1)) if len(r) else None,
                         "worst_net_pct": float(100 * r.min()) if len(r) else None,
                         "best_net_pct": float(100 * r.max()) if len(r) else None}
    return out


def resolve_run_rules(args: argparse.Namespace, out: Path) -> tuple[Rules, dict | None]:
    """Validate immutable definitions before querying or writing any data."""
    prior_manifest = None
    if args.score_only:
        prior_manifest = json.loads((out / "FROZEN_MANIFEST.json").read_text())
        for key, value in (("first", args.first), ("last", args.last), ("warmup_start", args.warmup_start)):
            if prior_manifest[key] != value:
                raise ValueError(f"score-only window differs from frozen {key}")
        if digest(out / "signals_before_outcomes.csv") != prior_manifest["signal_file_sha256"]:
            raise ValueError("Frozen signal file hash mismatch")
        rule_file = out / prior_manifest.get("rules_file", "rules_1.5.json")
        rules = load_rules(rule_file)
        expected_hash = prior_manifest["rules_hashes"].get("main", prior_manifest["rules_hashes"].get("1.5"))
        if digest(rule_file) != expected_hash:
            raise ValueError("Frozen rule file hash mismatch")
        if args.rules_file and load_rules(Path(args.rules_file)) != rules:
            raise ValueError("score-only cannot change the frozen rule definition")
    else:
        if (out / "signals_before_outcomes.csv").exists():
            raise ValueError("Frozen signals already exist: use --score-only or a new output directory")
        rules = load_rules(Path(args.rules_file)) if args.rules_file else Rules()
    return rules, prior_manifest


def run(args: argparse.Namespace) -> None:
    out = Path(args.output)
    rules, prior_manifest = resolve_run_rules(args, out)
    (out / "inputs").mkdir(parents=True, exist_ok=True)
    started = time.time()
    calendar = sorted(get_tradecal(start_date=args.warmup_start, end_date=args.last,
                                   source="database_only").cal_date.astype(str).unique())
    coverage_db = ROOT / "offlineDataManager/data/db_fifteenMinute.db"
    with duckdb.connect(str(coverage_db), read_only=True) as conn:
        coverage = conn.execute("""SELECT trade_date,COUNT(DISTINCT ts_code) stocks,COUNT(*) bars
                                   FROM fifteen_min_bars WHERE trade_date BETWEEN ? AND ?
                                   GROUP BY trade_date ORDER BY trade_date""", [int(args.first), int(args.last)]).df()
        codes = [row[0] for row in conn.execute("""SELECT DISTINCT ts_code FROM fifteen_min_bars
                   WHERE trade_date BETWEEN ? AND ? ORDER BY ts_code""", [int(args.first), int(args.last)]).fetchall()]
    codes = [code for code in codes if not code.endswith(".BJ")]
    if args.score_only:
        calendar = prior_manifest["calendar"]
        codes = prior_manifest["universe"]
    elif args.examples_only:
        codes = [code for code in EXAMPLES if code in codes]
    if not args.score_only:
        coverage.to_csv(out / "inputs/coverage.csv", index=False)
        hashes = {"main": save_rules(out / "rules.json", rules)}
    else:
        hashes = prior_manifest["rules_hashes"]
    manifest = {"first": args.first, "last": args.last, "warmup_start": args.warmup_start,
                "calendar": calendar, "universe": codes, "rules_hashes": hashes, "rules_file": "rules.json",
                "execution": "completed 15m decision; >=60s latency; scheduled 1m open proxy",
                "limit_entry": "user assumption: signal candle closes at upper limit, fill at that limit price",
                "excluded_universe": "ST source names, ST price-limit regime, listings younger than 365 calendar days",
                "exit": "entry day +1/+3/+5 trading sessions, planned 14:45, blocked waits retained",
                "costs": {"buy_slippage": BUY_SLIPPAGE, "sell_slippage": SELL_SLIPPAGE,
                          "commission_each_side": COMMISSION, "sell_tax": SELL_TAX},
                "limitations": ["user-selected September templates; exploratory, not independent holdout",
                                "minute volume only establishes proxy plausibility, not queue fills",
                                "no position ledger; per-trade means are not account returns",
                                "prior-day final KPL assumed available before next opening"]}
    if args.score_only:
        manifest = prior_manifest
    else:
        dump(out / "FROZEN_MANIFEST.json", manifest)
        snapshot = out / "source_snapshot"; snapshot.mkdir(exist_ok=True)
        for script in (Path(__file__), Path(__file__).with_name("study_fifteen_min_ma_coil.py")):
            target = snapshot / script.name; target.write_bytes(script.read_bytes())
    if args.score_only:
        signals = pd.read_csv(out / "signals_before_outcomes.csv")
    else:
        all_signals, audits = [], []
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for j in range(0, len(codes), args.batch_size):
                chunk = codes[j:j + args.batch_size]
                path = out / f"inputs/bars_{j:04d}.parquet"
                if path.exists():
                    raw = pd.read_parquet(path)
                else:
                    raw = get_fifteenMin(ts_codes=chunk, start_date=args.warmup_start, end_date=args.last)
                    raw.to_parquet(path, index=False)
                groups = {c: f for c, f in raw.groupby("ts_code")}
                tasks = [(c, groups[c], calendar, rules, args.first, args.last) for c in chunk if c in groups]
                for signals, audit in pool.map(scan_stock, tasks):
                    audits.append(audit)
                    if len(signals): all_signals.append(signals)
                progress = {"stage": "15m_scan", "processed": min(j + args.batch_size, len(codes)),
                            "universe": len(codes), "signals": sum(len(x) for x in all_signals),
                            "elapsed_seconds": round(time.time() - started, 1)}
                dump(out / "status.json", progress)
                print(json.dumps(progress), flush=True)
        signals = pd.concat(all_signals, ignore_index=True) if all_signals else pd.DataFrame()
        pd.DataFrame(audits).to_csv(out / "stock_scan_audit.csv", index=False)
    if signals.empty:
        dump(out / "summary.json", {"all": summary(signals), "manifest": manifest})
        return
    signals = signals.sort_values(["signal_time", "ts_code"]).reset_index(drop=True)
    if not args.score_only:
        signals.to_csv(out / "signals_before_outcomes.csv", index=False)
    manifest["signal_file_sha256"] = digest(out / "signals_before_outcomes.csv")
    manifest["inputs_sha256"] = {p.name: digest(p) for p in (out / "inputs").glob("bars_*.parquet")}
    dump(out / "FROZEN_MANIFEST.json", manifest)
    print(f"Frozen {len(signals)} signals; now reading post-signal outcomes within cutoff", flush=True)
    contextual = context(signals, calendar, out)
    listing = get_basic(source="database_only")[["ts_code", "list_date"]]
    listing.to_parquet(out / "inputs/listing_dates.parquet", index=False)
    contextual = contextual.merge(listing, on="ts_code", how="left", validate="many_to_one")
    contextual["listing_age_days"] = (pd.to_datetime(contextual.signal_time).dt.normalize()
          - pd.to_datetime(contextual.list_date.astype(str), errors="coerce")).dt.days
    wanted = sorted(signals.ts_code.unique())
    day = get_day(ts_codes=wanted, start_date=args.first, end_date=args.last, qfq=False, source="database_only")
    limits = get_stk_limit(ts_codes=wanted, start_date=args.first, end_date=args.last, source="database_only")
    day.to_parquet(out / "inputs/day.parquet", index=False)
    limits.to_parquet(out / "inputs/limits.parquet", index=False)
    limit_map = {(str(r.trade_date), r.ts_code): (float(r.up_limit), float(r.down_limit))
                 for r in limits.itertuples() if pd.notna(r.up_limit) and pd.notna(r.down_limit)}
    preclose = {(str(r.trade_date), r.ts_code): float(r.pre_close) for r in day.itertuples()}
    print(f"Reading TDX 1m for {len(wanted)} signal stocks", flush=True)
    minute_path = out / "inputs/one_min.parquet"
    if minute_path.exists():
        one = pd.read_parquet(minute_path)
    else:
        one = get_oneMin(ts_codes=wanted, start_date=args.first, end_date=args.last,
                        columns=["ts_code", "trade_date", "datetime", "open", "high", "low", "close", "vol", "adj_factor"])
        one.to_parquet(minute_path, index=False)
    one_groups = {c: f for c, f in one.groupby("ts_code")}
    evaluation_calendar = [d for d in calendar if args.first <= d <= args.last]
    rows = []
    for i, signal in enumerate(contextual.to_dict("records")):
        minute_frame = one_groups.get(signal["ts_code"], one.iloc[:0])
        row = {**signal, **execute(signal, minute_frame, evaluation_calendar, limit_map, preclose,
                                   allow_limit_fill=True, prepared=None)}
        conservative = execute(signal, minute_frame, evaluation_calendar, limit_map, preclose,
                               allow_limit_fill=False, prepared=None)
        row.update({f"without_limit_assumption_{k}": v for k, v in conservative.items()})
        rows.append(row)
        if (i + 1) % 100 == 0:
            print(f"Scored {i + 1}/{len(contextual)}", flush=True)
    trades = pd.DataFrame(rows)
    trades.to_csv(out / "trades.csv", index=False)
    all_key = "all_dual_group" if rules.coil_mode == "dual" else f"all_{rules.coil_pct:.1f}pct"
    table = {all_key: summary(trades),
             "without_limit_assumption": summary(trades.rename(columns=lambda c: c.removeprefix(
                 "without_limit_assumption_")) .loc[:, lambda f: ~f.columns.duplicated(keep="last")]),
             "prior_limit_up_10sessions": summary(trades[trades.recent_limit_up_10sessions]),
             "prior_strong_theme": summary(trades[trades.prior_strong_theme]),
             "seven_examples": summary(trades[trades.ts_code.isin(EXAMPLES)]),
             "rule_note": ("dual group: close+MA5/10/20/30 and that entire group+MA60 must pass on the same bar"
                           if rules.coil_mode == "dual" else "legacy OR branches; historical definition preserved"),
             "elapsed_seconds": round(time.time() - started, 1)}
    if rules.coil_mode == "legacy_or":
        table["coil_1.0pct_nested_subset"] = summary(trades[trades.coil_spread_pct.le(1.)])
    dump(out / "summary.json", table)
    dump(out / "status.json", {"stage": "completed", "universe": len(codes), "signals": len(trades),
                                "elapsed_seconds": table["elapsed_seconds"]})
    dump(out / "DATA_RECEIPT.json", {p.name: digest(p) for p in (out / "inputs").glob("*.parquet")})
    print(json.dumps(table, ensure_ascii=False, indent=2), flush=True)


def argument_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--first", default="20260901"); p.add_argument("--last", default="20260915")
    p.add_argument("--warmup-start", default="20260701")
    p.add_argument("--output", default="outputs/fifteen_min_ma_coil_20260901_20260915_v3_dual")
    p.add_argument("--rules-file", help="Explicit frozen JSON; default is the dual-group 1.0%%/2.0%% rule")
    p.add_argument("--workers", type=int, default=2); p.add_argument("--batch-size", type=int, default=128)
    p.add_argument("--examples-only", action="store_true")
    p.add_argument("--score-only", action="store_true", help="Reuse frozen signals; do not rescan")
    return p


if __name__ == "__main__":
    run(argument_parser().parse_args())
