"""Read-only local market snapshot for the frozen 300k account replay.

Run with PYTHONPATH=.:offlineDataManager/scripts and the project Python runtime.
No provider download/fallback or production catalog write is used.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_oneMin, get_tradecal
from offlineDataManager.scripts.core.one_min_store import ONE_MIN_PARQUET_ROOT


COLUMNS = ["ts_code", "trade_date", "datetime", "open", "high", "low", "close", "adj_factor"]
POLICIES = ("minute_old", "minute_carry")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def clean_scalar(value):
    if pd.isna(value):
        return None
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" ")
    return value


def is_true(value) -> bool:
    return str(value).lower() == "true"


def scheduled_times(day: str) -> list[pd.Timestamp]:
    return list(pd.date_range(day + " 09:31", day + " 11:30", freq="min")) + list(
        pd.date_range(day + " 13:01", day + " 15:00", freq="min")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trades", type=Path, default=Path("outputs/core_reactivation_minute_execution_2026/trades.csv"))
    parser.add_argument("--output", type=Path, default=Path("outputs/core_reactivation_compound_300k_2026/market"))
    parser.add_argument("--start", default="20260601")
    parser.add_argument("--end", default="20260924")
    args = parser.parse_args()
    begin = time.monotonic()
    source_hash = sha256(args.trades)
    trades = pd.read_csv(args.trades)
    codes = sorted(trades.ts_code.astype(str).unique())
    calls = []
    cal_params = dict(start_date=args.start, end_date=args.end, market="SSE", source="database_only")
    cal = get_tradecal(**cal_params)
    calendar = sorted(cal.loc[cal.is_open.eq(1), "cal_date"].astype(str).unique().tolist())
    calls.append(dict(api="coreClient.data_provider.get_tradecal", parameters=cal_params, rows=len(cal)))
    calendar_set = set(calendar)
    holdings: set[tuple[str, str]] = set()
    audit_only: set[tuple[str, str]] = set()
    candidates = []
    timestamp_problems = []
    for idx, row in trades.iterrows():
        candidate = dict(signal_id=f"S{idx + 1:03d}", source_row_0based=int(idx),
                         ts_code=row.ts_code, theme_id=clean_scalar(row.get("theme_id")),
                         theme_name=clean_scalar(row.get("theme_name")),
                         signal_time=clean_scalar(row.get("signal_time")), policies={})
        for policy in POLICIES:
            filled = is_true(row.get(f"{policy}_buyable"))
            entry = pd.to_datetime(row.get(f"{policy}_entry_execution_at"), errors="coerce")
            exit_at = pd.to_datetime(row.get(f"{policy}_exit_execution_at"), errors="coerce")
            record = dict(status=clean_scalar(row.get(f"{policy}_status")), filled=filled,
                          entry_execution_at=clean_scalar(entry), exit_execution_at=clean_scalar(exit_at))
            if filled:
                if pd.isna(entry):
                    raise ValueError(f"Filled row {idx} {policy} has no entry timestamp")
                # Missing exits remain outstanding; never drop them from mark coverage.
                last = args.end if pd.isna(exit_at) else exit_at.strftime("%Y%m%d")
                first = entry.strftime("%Y%m%d")
                if first < args.start or last > args.end or first > last:
                    raise ValueError(f"Holding outside declared range: {idx} {policy} {first}..{last}")
                selected = [day for day in calendar if first <= day <= last]
                holdings.update((row.ts_code, day) for day in selected)
                record["required_stock_days"] = selected
            elif pd.notna(entry):
                audit_only.add((row.ts_code, entry.strftime("%Y%m%d")))
            for role, stamp in (("entry", entry), ("exit", exit_at)):
                if pd.notna(stamp) and stamp.strftime("%Y%m%d") not in calendar_set:
                    timestamp_problems.append(dict(signal_id=candidate["signal_id"], policy=policy,
                                                   role=role, problem="execution_day_not_in_SSE_calendar"))
            candidate["policies"][policy] = record
        candidates.append(candidate)
    audit_only -= holdings
    required = holdings | audit_only
    # One range scan avoids repeating the provider's full partition-glob discovery per day.
    minute_params = dict(ts_codes=codes, start_date=args.start, end_date=args.end, columns=COLUMNS)
    print(f"Reading local minutes: {len(codes)} codes; {len(holdings)} holding stock-days + {len(audit_only)} audit-only", flush=True)
    one = get_oneMin(**minute_params)
    fetched_rows = len(one)
    one["trade_date"] = one.trade_date.astype(str)
    one["datetime"] = pd.to_datetime(one.datetime)
    keep = pd.MultiIndex.from_frame(one[["ts_code", "trade_date"]]).isin(sorted(required))
    one = one.loc[keep, COLUMNS].sort_values(["ts_code", "datetime"], kind="stable").reset_index(drop=True)
    calls.append(dict(api="coreClient.data_provider.get_oneMin", parameters=minute_params,
                      fetched_rows=fetched_rows, retained_rows=len(one), online_fallback=False))
    day_params = dict(ts_codes=codes, start_date=args.start, end_date=args.end, qfq=False, source="database_only")
    days = get_day(**day_params)
    days["trade_date"] = days.trade_date.astype(str)
    days = days.sort_values(["ts_code", "trade_date"], kind="stable").reset_index(drop=True)
    calls.append(dict(api="coreClient.data_provider.get_day", parameters=day_params, rows=len(days)))
    groups = {key: group for key, group in one.groupby(["ts_code", "trade_date"], sort=False)}
    key_groups = {key: group for key, group in one.groupby(["ts_code", "datetime"], sort=False)}
    day_groups = {key: group for key, group in days.groupby(["ts_code", "trade_date"], sort=False)}
    coverage = []
    close_checks = []
    for code, day in sorted(required):
        group = groups.get((code, day), one.iloc[:0])
        actual = set(group.datetime)
        expected = set(scheduled_times(day))
        coverage.append(dict(ts_code=code, trade_date=day, purpose="holding" if (code, day) in holdings else "unfilled_entry_audit_only",
                             rows=len(group), unique_minutes=len(actual),
                             missing_times=[str(x) for x in sorted(expected - actual)],
                             extra_times=[str(x) for x in sorted(actual - expected)],
                             duplicated_timestamps=int(group.datetime.duplicated(keep=False).sum())))
        last = key_groups.get((code, pd.Timestamp(day + " 15:00")))
        daily = day_groups.get((code, day))
        check = dict(ts_code=code, trade_date=day, minute_close=None, day_close=None, difference=None, status="missing_minute_or_daily")
        if last is not None and daily is not None and len(last) == 1 and len(daily) == 1:
            a, b = float(last.iloc[0].close), float(daily.iloc[0].close)
            if np.isfinite(a) and np.isfinite(b):
                check.update(minute_close=a, day_close=b, difference=a-b,
                             status="match" if abs(a-b) <= 1e-8 else "mismatch")
        elif (last is not None and len(last) > 1) or (daily is not None and len(daily) > 1):
            check["status"] = "duplicate_source_key"
        close_checks.append(check)
    endpoints = []
    for idx, row in trades.iterrows():
        for policy in POLICIES:
            filled = is_true(row.get(f"{policy}_buyable"))
            for role in ("entry", "exit"):
                execution = pd.to_datetime(row.get(f"{policy}_{role}_execution_at"), errors="coerce")
                recorded_end = pd.to_datetime(row.get(f"{policy}_{role}_bar_end"), errors="coerce")
                old_price = clean_scalar(row.get(f"{policy}_{role}_open"))
                check = dict(signal_id=f"S{idx + 1:03d}", policy=policy, role=role, ts_code=row.ts_code,
                             filled=filled, execution_at=clean_scalar(execution), recorded_bar_end=clean_scalar(recorded_end),
                             expected_bar_end=None, reference_open=old_price, current_open=None, difference=None,
                             current_adj_factor=None, status="unfilled_no_execution" if not filled else "missing_execution_timestamp")
                if pd.notna(execution):
                    bar_end = execution + pd.Timedelta(minutes=1)
                    check["expected_bar_end"] = str(bar_end)
                    check["recorded_bar_end_matches_plus_one_minute"] = bool(pd.notna(recorded_end) and recorded_end == bar_end)
                    matches = key_groups.get((row.ts_code, bar_end))
                    if matches is None:
                        check["status"] = "missing_minute_bar"
                    elif len(matches) != 1:
                        check["status"] = "duplicate_minute_bar"
                    else:
                        current = matches.iloc[0]
                        check["current_open"] = clean_scalar(current.open)
                        check["current_adj_factor"] = clean_scalar(current.adj_factor)
                        if not filled:
                            check["status"] = "unfilled_candidate_bar_present_no_fill_reference"
                        elif old_price is None or not np.isfinite(float(current.open)):
                            check["status"] = "missing_or_nonfinite_price"
                        else:
                            difference = float(current.open) - float(old_price)
                            check.update(difference=difference, status="match" if abs(difference) <= 1e-8 else "mismatch")
                endpoints.append(check)
    manifests = {}
    lineage = []
    for code, day in sorted(required):
        if day not in manifests:
            path = ONE_MIN_PARQUET_ROOT / "_manifest" / f"trade_date={day}.json"
            payload = json.loads(path.read_text()) if path.exists() else {}
            manifests[day] = dict(path=str(path), exists=path.exists(), sha256=sha256(path) if path.exists() else None,
                                  data_source=payload.get("data_source"), status=payload.get("status"),
                                  source_lineage=payload.get("source_lineage"))
        item = manifests[day]
        by_source = (item["source_lineage"] or {}).get("stock_codes_by_source", {})
        sources = [source for source, members in by_source.items() if code in members]
        source = sources[0] if len(sources) == 1 else "unknown"
        lineage.append(dict(ts_code=code, trade_date=day, manifest_data_source=item["data_source"],
                            stock_day_source=source, evidence="explicit_source_lineage" if len(sources) == 1 else "no_unique_explicit_stock_day_lineage"))
    bad_numeric = ~np.isfinite(one[["open", "high", "low", "close", "adj_factor"]]).all(axis=1)
    bad_ohlc = (one.high < one[["open", "low", "close"]].max(axis=1)) | (one.low > one[["open", "high", "close"]].min(axis=1))
    bad_positive = (one[["open", "high", "low", "close", "adj_factor"]] <= 0).any(axis=1)
    date_mismatch = one.trade_date.ne(one.datetime.dt.strftime("%Y%m%d"))
    invalid = one.loc[bad_numeric | bad_ohlc | bad_positive | date_mismatch].copy()
    args.output.mkdir(parents=True, exist_ok=True)
    one.to_parquet(args.output / "one_min.parquet", index=False, compression="zstd")
    days.to_parquet(args.output / "day.parquet", index=False, compression="zstd")
    save_json(args.output / "calendar.json", calendar)
    save_json(args.output / "candidates.json", candidates)
    artifacts = {name: dict(path=str((args.output / name).resolve()), sha256=sha256(args.output / name),
                            bytes=(args.output / name).stat().st_size)
                 for name in ("one_min.parquet", "day.parquet", "calendar.json", "candidates.json")}
    manifest = dict(
        schema_version="core_compound_market.v1", generated_at_local=datetime.now().isoformat(), timezone="Asia/Shanghai",
        source_trades=dict(path=str(args.trades.resolve()), sha256=source_hash, signals=len(trades), unique_codes=len(codes),
                           all_candidates_retained=True, candidate_selection="all source rows; no return/outcome threshold"),
        preparation_script=dict(path=str(Path(__file__).resolve()), sha256=sha256(Path(__file__))),
        requested_range=[args.start, args.end], provider_calls=calls,
        minute_scope="Union of inclusive entry-to-exit SSE stock-days for both frozen minute policies; extra unfilled entry days only for audit. Unresolved positions extend to range end. Other stock-days intentionally absent.",
        minute_timestamp="Naive local Asia/Shanghai bar-END timestamps; execution is bar-END minus one minute, including 09:30 execution for 09:31 bar.",
        scope_uses_exit_dates_only_for_data_fetch=True, no_production_data_writes=True, no_downloads=True,
        schema={"one_min": {c: str(t) for c, t in one.dtypes.items()}, "day": {c: str(t) for c, t in days.dtypes.items()}},
        counts=dict(calendar_open_days=len(calendar), holding_stock_days=len(holdings), unfilled_audit_only_stock_days=len(audit_only),
                    requested_stock_days=len(required), minute_rows=len(one), minute_codes=int(one.ts_code.nunique()), daily_rows=len(days),
                    daily_codes=int(days.ts_code.nunique()), complete_240_stock_days=sum(x["rows"] == 240 and not x["missing_times"] and not x["extra_times"] and not x["duplicated_timestamps"] for x in coverage)),
        coverage=coverage, timestamp_problems=timestamp_problems,
        invalid_minute_rows=json.loads(invalid.to_json(orient="records", date_format="iso")),
        endpoint_price_tolerance=1e-8, endpoint_status_counts=dict(Counter(x["status"] for x in endpoints)), endpoint_checks=endpoints,
        daily_close_status_counts=dict(Counter(x["status"] for x in close_checks)), daily_close_checks=close_checks,
        daily_missing_requested_stock_days=[dict(ts_code=c,trade_date=d) for c,d in sorted(required) if (c,d) not in day_groups],
        source_lineage_limitation="Historical tdx_with_csv_fallback denotes a mixed-source label retaining old partitions; retained Parquet may already be TDX. Without explicit per-stock-day lineage, source is unknown: neither proven CSV contamination nor proven pure TDX. OHLC agreement is not lineage proof.",
        source_lineage_counts=dict(Counter(x["stock_day_source"] for x in lineage)), source_lineage=lineage,
        source_partition_manifests=manifests, artifacts=artifacts, elapsed_seconds=round(time.monotonic()-begin,3),
    )
    if sha256(args.trades) != source_hash:
        raise RuntimeError("Source trade file changed during preparation")
    save_json(args.output / "MANIFEST.json", manifest)
    print(json.dumps(dict(counts=manifest["counts"], endpoints=manifest["endpoint_status_counts"], daily_close=manifest["daily_close_status_counts"],
                          lineage=manifest["source_lineage_counts"], invalid_rows=len(invalid), timestamp_problems=len(timestamp_problems),
                          elapsed_seconds=manifest["elapsed_seconds"], output=str(args.output.resolve())), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
