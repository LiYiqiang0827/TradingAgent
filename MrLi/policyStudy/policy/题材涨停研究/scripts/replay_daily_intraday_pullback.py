"""Freeze and replay the combined daily/15m pullback development rules."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "offlineDataManager/scripts"))
from coreClient.data_provider import (
    get_day,
    get_adj_factor,
    get_kpl_list,
    get_tradecal,
    get_basic,
    get_fifteenMin,
    get_oneMin,
    get_stk_limit,
)
from offlineDataManager.scripts.core.theme_graph_store import parse_board_status
from study_fifteen_min_ma_coil import Rules as CoilRules, features
from study_daily_intraday_pullback import (
    CombinedRules,
    save_combined_rules,
    prepare_daily,
    daily_pool_snapshot,
    combined_entries,
    execute_dynamic,
    revised_rules,
)
from replay_fifteen_min_ma_coil import digest


def dump(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str, allow_nan=False)
    )


def board_events(
    kpl: pd.DataFrame,
    rules: CombinedRules,
    daily_raw: pd.DataFrame | None = None,
    limits: pd.DataFrame | None = None,
    calendar: list[str] | None = None,
) -> pd.DataFrame:
    e = kpl.copy()
    e.trade_date = e.trade_date.astype(str)
    parsed = e.status.apply(lambda s: parse_board_status(s, "涨停"))
    e["continuous_board"] = parsed.apply(
        lambda t: t[0] if t[0] is not None else (t[2] if t[1] == t[2] else 0)
    )
    three = parsed.apply(lambda t: t[1] == 3 and t[2] == 2)
    e["qualifying_board"] = e.continuous_board
    if rules.allow_three_day_two_board:
        e.loc[three, "qualifying_board"] = 2
    e["board_type"] = np.where(
        e.continuous_board.ge(2),
        "continuous",
        np.where(three, "3day2", "single_or_other"),
    )
    e["twenty_pct_limit_verified"] = False
    if rules.allow_twenty_pct_equivalent:
        if daily_raw is None or limits is None or calendar is None:
            raise ValueError(
                "20cm equivalence requires dated daily prices, limits and calendar"
            )
        raw = daily_raw[["ts_code", "trade_date", "close", "pre_close"]].copy()
        cap = limits[["ts_code", "trade_date", "up_limit"]].copy()
        raw.trade_date = raw.trade_date.astype(str)
        cap.trade_date = cap.trade_date.astype(str)
        checked = (
            e[["ts_code", "trade_date"]]
            .merge(
                raw, on=["ts_code", "trade_date"], how="left", validate="many_to_one"
            )
            .merge(
                cap, on=["ts_code", "trade_date"], how="left", validate="many_to_one"
            )
        )
        band = checked.up_limit / checked.pre_close - 1
        verified = (
            band.between(0.195, 0.205)
            & checked.pre_close.gt(0)
            & (checked.close - checked.up_limit).abs().le(0.010001)
        )
        e["twenty_pct_limit_verified"] = verified.to_numpy()
        native = e.qualifying_board.clip(lower=1)
        e.loc[verified.to_numpy(), "qualifying_board"] = native[verified.to_numpy()] * 2
        e.loc[
            verified.to_numpy() & e.continuous_board.le(1).to_numpy(), "board_type"
        ] = "20cm_equivalent"
        # Each qualifying event is annotated causally. Later events cannot move
        # an earlier episode's launch backwards, or supply its earlier facts.
        positions = {d: i for i, d in enumerate(calendar)}
        e["wave_start_date"] = e.trade_date
        for _, group in e.groupby("ts_code"):
            previous_i = None
            start_i = None
            for row_i, event in group.sort_values("trade_date").iterrows():
                if event.qualifying_board < rules.board_min:
                    continue
                current_i = positions.get(str(event.trade_date))
                if current_i is None:
                    continue
                span = (
                    int(event.continuous_board)
                    if event.continuous_board >= 2
                    else (3 if event.board_type == "3day2" else 1)
                )
                own_start = max(0, current_i - span + 1)
                if (
                    previous_i is None
                    or current_i - previous_i > rules.episode_gap_days
                    or current_i - start_i >= rules.board_lookback_days
                ):
                    start_i = own_start
                e.loc[row_i, "wave_start_date"] = calendar[start_i]
                previous_i = current_i
    return e.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def run(args: argparse.Namespace) -> None:
    out = Path(args.output)
    inputs = out / "inputs"
    if (out / "signals_before_outcomes.csv").exists():
        raise ValueError(
            "Refusing to overwrite frozen combined replay; choose a fresh output directory"
        )
    inputs.mkdir(parents=True, exist_ok=True)
    started = time.time()
    rules = revised_rules() if args.rules_version == "v2" else CombinedRules()
    save_combined_rules(out / "rules.json", rules)

    def cached(name, fn):
        path = inputs / (name + ".parquet")
        if path.exists():
            return pd.read_parquet(path)
        x = fn()
        x.to_parquet(path, index=False)
        return x

    def progress(stage, **values):
        v = dict(stage=stage, elapsed_seconds=round(time.time() - started, 1), **values)
        dump(out / "status.json", v)
        print(json.dumps(v, ensure_ascii=False), flush=True)

    history_start = (pd.Timestamp(args.first) - pd.DateOffset(months=13)).strftime(
        "%Y%m%d"
    )
    kpl_start = (pd.Timestamp(args.first) - pd.Timedelta(days=150)).strftime("%Y%m%d")
    cal = cached(
        "calendar",
        lambda: get_tradecal(
            start_date=history_start, end_date=args.last, source="database_only"
        ),
    )
    calendar = sorted(cal.cal_date.astype(str).unique())
    idx = {d: i for i, d in enumerate(calendar)}
    kpl = cached(
        "kpl",
        lambda: get_kpl_list(
            start_date=kpl_start,
            end_date=args.last,
            tags="涨停",
            source="database_only",
        ),
    )
    events = board_events(kpl, CombinedRules())
    codes = sorted(
        events.loc[
            events.qualifying_board.ge(2) & events.ts_code.str.endswith((".SZ", ".SH")),
            "ts_code",
        ].unique()
    )
    if rules.allow_twenty_pct_equivalent:
        codes = sorted(
            kpl.loc[kpl.ts_code.str.endswith((".SZ", ".SH")), "ts_code"].unique()
        )
    if args.ts_codes:
        codes = sorted(set(args.ts_codes))
    raw = cached(
        "daily_raw",
        lambda: get_day(
            ts_codes=codes,
            start_date=history_start,
            end_date=args.last,
            qfq=False,
            source="database_only",
        ),
    )
    adj = cached(
        "daily_adj",
        lambda: get_adj_factor(
            ts_codes=codes,
            start_date=history_start,
            end_date=args.last,
            source="database_only",
        ),
    )
    if rules.allow_twenty_pct_equivalent:
        board_limits = cached(
            "board_limits",
            lambda: get_stk_limit(
                ts_codes=codes,
                start_date=history_start,
                end_date=args.last,
                source="database_only",
            ),
        )
        events = board_events(
            kpl[kpl.ts_code.isin(codes)].copy(), rules, raw, board_limits, calendar
        )
        if not args.ts_codes:
            codes = sorted(
                events.loc[events.qualifying_board.ge(2), "ts_code"].unique()
            )
    first_idx = idx[next(d for d in calendar if d >= args.first)]
    context_days = calendar[max(0, first_idx - 3) : idx[args.last]]
    raw_groups = {c: g for c, g in raw.groupby("ts_code")}
    adj_groups = {c: g for c, g in adj.groupby("ts_code")}
    event_groups = {c: g for c, g in events.groupby("ts_code")}
    snapshots = {}
    records = []
    for number, code in enumerate(codes, 1):
        if code not in raw_groups or code not in adj_groups:
            continue
        d = prepare_daily(raw_groups[code], adj_groups[code], calendar)
        pool = {}
        for day in context_days:
            r = daily_pool_snapshot(
                d, event_groups.get(code, events.iloc[:0]), day, rules
            )
            if r.get("name") and ("ST" in r["name"].upper() or "退" in r["name"]):
                r.update(eligible=False, reason="excluded_st_name")
            pool[day] = r
            records.append(dict(ts_code=code, **r))
        if any(r["eligible"] for r in pool.values()):
            snapshots[code] = pool
        if number % 50 == 0:
            progress(
                "daily_pool",
                processed=number,
                stocks=len(codes),
                eligible_stocks=len(snapshots),
            )
    diagnostics = pd.DataFrame(records)
    diagnostics.to_csv(out / "daily_pool_audit.csv", index=False, encoding="utf-8-sig")
    progress(
        "daily_pool_complete",
        stocks=len(codes),
        candidate_stock_days=int(diagnostics.eligible.sum()),
        eligible_stocks=len(snapshots),
    )
    source = ROOT / "outputs/fifteen_min_ma_coil_20260901_20260915_v2"
    minute_calendar = [d for d in calendar if args.warmup_start <= d <= args.last]
    cache_sources = sorted((source / "inputs").glob("bars_*.parquet"))
    wanted = sorted(snapshots)
    if wanted:
        fifteen = cached(
            "fifteen_min",
            lambda: (
                pd.read_parquet(
                    [str(p) for p in cache_sources], filters=[("ts_code", "in", wanted)]
                )
                if cache_sources
                and args.first == "20260901"
                and args.last == "20260915"
                and args.warmup_start == "20260701"
                else get_fifteenMin(
                    ts_codes=wanted, start_date=args.warmup_start, end_date=args.last
                )
            ),
        )
        fifteen["datetime"] = pd.to_datetime(fifteen.datetime)
    else:
        fifteen = pd.DataFrame()
    signal_frames = []
    frames = {}
    prefix_checks = 0
    for code, g in fifteen.groupby("ts_code") if len(fifteen) else []:
        f = features(g, minute_calendar, CoilRules())
        frames[code] = f
        s = combined_entries(f, snapshots[code], calendar, args.first, args.last, rules)
        if s.empty:
            continue
        s["ts_code"] = code
        for stamp in s.signal_time:
            pf = features(
                g[pd.to_datetime(g.datetime).le(pd.Timestamp(stamp))],
                minute_calendar,
                CoilRules(),
            )
            ps = combined_entries(
                pf, snapshots[code], calendar, args.first, args.last, rules
            )
            expected = (
                s[s.signal_time.le(stamp)]
                .drop(columns="ts_code")
                .reset_index(drop=True)
            )
            pd.testing.assert_frame_equal(
                ps.reset_index(drop=True), expected, check_dtype=False
            )
            prefix_checks += 1
        signal_frames.append(s)
    signals = (
        pd.concat(signal_frames, ignore_index=True).sort_values(
            ["signal_time", "ts_code"]
        )
        if signal_frames
        else pd.DataFrame(columns=["ts_code", "signal_time"])
    )
    signals.to_csv(
        out / "signals_before_outcomes.csv", index=False, encoding="utf-8-sig"
    )
    manifest = dict(
        first=args.first,
        last=args.last,
        warmup_start=args.warmup_start,
        calendar=calendar,
        rules_sha256=digest(out / "rules.json"),
        signal_sha256=digest(out / "signals_before_outcomes.csv"),
        entry_time_visibility="daily/KPL through prior close; completed 15m signal only",
        preliminary_universe_stocks=len(codes),
        daily_candidate_stock_days=int(diagnostics.eligible.sum()),
        requested_ts_codes=args.ts_codes,
        input_sha256={p.name: digest(p) for p in inputs.glob("*.parquet")},
        prefix_signal_checks=prefix_checks,
        signals=len(signals),
        rules_version=args.rules_version,
        development_sample=True,
        no_position_ledger=True,
        models_called=False,
    )
    dump(out / "FROZEN_MANIFEST.json", manifest)
    snapshot = out / "source_snapshot"
    snapshot.mkdir(exist_ok=True)
    for path in (
        Path(__file__),
        Path(__file__).with_name("study_daily_intraday_pullback.py"),
        Path(__file__).with_name("study_fifteen_min_ma_coil.py"),
    ):
        (snapshot / path.name).write_bytes(path.read_bytes())
    progress("signals_frozen", signals=len(signals), prefix_checks=prefix_checks)
    if args.signals_only:
        dump(
            out / "summary.json",
            dict(
                signals=len(signals),
                daily_candidate_stock_days=int(diagnostics.eligible.sum()),
                prefix_checks=prefix_checks,
                elapsed_seconds=round(time.time() - started, 1),
                outcomes_loaded=False,
                note="Structural replay only; no entry/exit return or profitability claim",
            ),
        )
        progress("completed_signals_only", signals=len(signals))
        return
    trades = []
    traces = out / "traces"
    traces.mkdir(exist_ok=True)
    if len(signals):
        wanted = sorted(signals.ts_code.unique())
        one = cached(
            "one_min",
            lambda: get_oneMin(
                ts_codes=wanted, start_date=args.first, end_date=args.last
            ),
        )
        limits = cached(
            "limits",
            lambda: get_stk_limit(
                ts_codes=wanted,
                start_date=args.first,
                end_date=args.last,
                source="database_only",
            ),
        )
        listing = cached("listing", lambda: get_basic(source="database_only"))
        listed = dict(zip(listing.ts_code, listing.list_date))
        cap = {
            (str(r.trade_date), r.ts_code): (float(r.up_limit), float(r.down_limit))
            for r in limits.itertuples()
        }
        prior = {
            (str(r.trade_date), r.ts_code): float(r.pre_close) for r in raw.itertuples()
        }
        one_groups = {c: g for c, g in one.groupby("ts_code")}
        active_until = {}
        evaluation = [d for d in calendar if args.first <= d <= args.last]
        for n, row in enumerate(signals.to_dict("records"), 1):
            code = row["ts_code"]
            stamp = pd.Timestamp(row["signal_time"])
            row["listing_age_days"] = (
                stamp.normalize()
                - pd.to_datetime(str(listed.get(code, "")), errors="coerce")
            ).days
            if code in active_until and stamp <= active_until[code]:
                trades.append(
                    {
                        **row,
                        "entry_status": "skipped_existing_position",
                        "buyable_proxy": False,
                        "exit_status": "not_entered",
                        "net_pct": None,
                    }
                )
                continue
            o = one_groups.get(code, one.iloc[:0])
            result, trace = execute_dynamic(
                row,
                frames[code],
                o,
                evaluation,
                cap,
                prior,
                rules,
                allow_limit_fill=True,
            )
            conservative, _ = execute_dynamic(
                row,
                frames[code],
                o,
                evaluation,
                cap,
                prior,
                rules,
                allow_limit_fill=False,
            )
            trades.append(
                {
                    **row,
                    **result,
                    **{"without_limit_" + k: v for k, v in conservative.items()},
                }
            )
            if result["buyable_proxy"]:
                active_until[code] = pd.Timestamp(
                    result["exit_time"] or (args.last + " 15:00")
                )
            dump(
                traces / f"{n:03d}_{code}.json",
                dict(signal=row, execution=result, states=trace),
            )
    table = pd.DataFrame(trades)
    table.to_csv(out / "trades.csv", index=False, encoding="utf-8-sig")
    resolved = table.net_pct.dropna() if len(table) else pd.Series(dtype=float)
    result = dict(
        signals=len(signals),
        executed=int(table.buyable_proxy.sum()) if len(table) else 0,
        resolved=len(resolved),
        mean_net_pct=float(resolved.mean()) if len(resolved) else None,
        median_net_pct=float(resolved.median()) if len(resolved) else None,
        win_pct=float(100 * resolved.gt(0).mean()) if len(resolved) else None,
        worst_net_pct=float(resolved.min()) if len(resolved) else None,
        best_net_pct=float(resolved.max()) if len(resolved) else None,
        entry_statuses=dict(Counter(table.entry_status)) if len(table) else {},
        exit_statuses=dict(Counter(table.exit_status)) if len(table) else {},
        daily_first_failed_gates=dict(
            Counter(diagnostics.reason.str.split("|").str[0])
        ),
        elapsed_seconds=round(time.time() - started, 1),
        prefix_checks=prefix_checks,
        note="Development scan only; no portfolio return, no independent profitability validation",
    )
    dump(out / "summary.json", result)
    assert digest(out / "signals_before_outcomes.csv") == manifest["signal_sha256"]
    progress("completed", signals=len(signals), resolved=len(resolved))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--first", default="20260901")
    p.add_argument("--last", default="20260915")
    p.add_argument("--warmup-start", default="20260701")
    p.add_argument("--rules-version", choices=("v1", "v2"), default="v2")
    p.add_argument(
        "--signals-only",
        action="store_true",
        help="Freeze structural signals without reading execution outcomes",
    )
    p.add_argument(
        "--ts-codes",
        nargs="+",
        help="Optional explicit stock subset for case validation",
    )
    p.add_argument(
        "--output", default="outputs/daily_intraday_pullback_20260901_20260915_v2"
    )
    run(p.parse_args())
