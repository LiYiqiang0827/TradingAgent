"""Freeze twelve anonymous, as-of core-pullback packets without reading outcomes.

The unified provider fetches minutes by date. Its result is immediately clipped
to the signal timestamp before any other processing; this is a trusted prefix
isolation layer, not a claim of timestamp-filtered physical database reads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
SEED = 2026092901
SCHEMA_VERSION = "core_pullback_blind.v1"
SOURCE_COLUMNS = (
    "ts_code", "signal_time", "anchor_date", "anchor_close",
    "anchor_board_height", "anchor_leader_rank", "anchor_theme_rank",
    "prior_theme_heat", "prior_theme_rank", "prior_theme_width",
)
DAILY_COLUMNS = ["id", "day", "open", "high", "low", "close", "volume_ratio"]
M15_COLUMNS = ["id", "day", "bar_end", "open", "high", "low", "close", "volume_ratio"]
PRICES = ["open", "high", "low", "close"]
BAR_TIMES = [f"{h:02}:{m:02}" for h, m in (
    (9, 45), (10, 0), (10, 15), (10, 30), (10, 45), (11, 0), (11, 15), (11, 30),
    (13, 15), (13, 30), (13, 45), (14, 0), (14, 15), (14, 30), (14, 45), (15, 0),
)]


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return _clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(_clean(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _number(value: Any, digits: int = 6) -> float | None:
    try:
        n = float(value)
        return round(n, digits) if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> str:
    return str(value).replace("-", "")[:8]


def prefix_minutes(frame: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Call immediately after date-granularity fetch, before features/counts."""
    if frame.empty:
        return pd.DataFrame(columns=["trade_date", "datetime", *PRICES, "vol"])
    stamp = pd.to_datetime(frame["datetime"])
    return frame.loc[stamp.le(cutoff)].copy()


def select_samples(path: Path, seed: int = SEED, count: int = 12
                   ) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    # Never load result columns, even temporarily or for an input-file hash.
    pool = pd.read_csv(path, usecols=list(SOURCE_COLUMNS),
                       dtype={"ts_code": str, "anchor_date": str, "signal_time": str})
    pool = pool.sort_values(["signal_time", "ts_code", "anchor_date"], kind="stable")
    records = pool.loc[:, list(SOURCE_COLUMNS)].to_dict("records")
    shuffled = list(records)
    random.Random(seed).shuffle(shuffled)
    chosen, codes = [], set()
    for row in shuffled:
        if row["ts_code"] in codes:
            continue
        codes.add(row["ts_code"])
        chosen.append(row)
        if len(chosen) == count:
            break
    return pool, chosen


def build_packet(sample_id: str, signal: dict[str, Any], calendar: list[str],
                 daily: pd.DataFrame, factors: pd.DataFrame, m15: pd.DataFrame
                 ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pure boundary: output never depends on post-cutoff rows or extra fields."""
    cutoff = pd.Timestamp(signal["signal_time"])
    m15 = prefix_minutes(m15, cutoff)  # Must be the first data operation.
    today = cutoff.strftime("%Y%m%d")
    days = sorted({_date(x) for x in calendar if _date(x) <= today})
    if today not in days:
        raise ValueError("signal_date_not_in_local_open_calendar")
    before = [d for d in days if d < today]
    history_days, minute_days = before[-60:], before[-5:] + [today]
    rel = {d: ("D0" if d == today else f"D{i - len(days) + 1}")
           for i, d in enumerate(days)}
    missing: list[str] = []
    warnings: list[str] = []

    def bounded(frame: pd.DataFrame, allowed: list[str]) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=["trade_date", *allowed])
        f = frame.copy()
        f["trade_date"] = f.trade_date.map(_date)
        return f[f.trade_date.isin(history_days)].loc[:, ["trade_date", *allowed]].sort_values("trade_date")

    d = bounded(daily, PRICES + ["vol"])
    if d.trade_date.duplicated().any():
        raise ValueError("duplicate_daily_key")
    if len(history_days) < 60:
        missing.append("calendar_history_less_than_60_days")
    present = set(d.trade_date)
    missing += [f"daily:{rel[x]}" for x in history_days if x not in present]
    m = m15.copy()
    m["trade_date"] = m.trade_date.map(_date)
    m["datetime"] = pd.to_datetime(m.datetime)
    m = m[m.trade_date.isin(minute_days)].loc[:, ["trade_date", "datetime", *PRICES, "vol"]]
    m = m.sort_values("datetime")
    if m.datetime.duplicated().any():
        raise ValueError("duplicate_m15_key")
    # Reject unexpected timestamps rather than silently changing the packet grid.
    if not m.empty and not m.datetime.dt.strftime("%H:%M").isin(BAR_TIMES).all():
        raise ValueError("unexpected_m15_timestamp")

    if factors.empty:
        f = pd.DataFrame(columns=["trade_date", "adj_factor"])
    else:
        f = factors.loc[:, ["trade_date", "adj_factor"]].copy()
        f["trade_date"] = f.trade_date.map(_date)
        f = f[f.trade_date.le(today)].sort_values("trade_date")
    factor_map = {}
    for date, part in f.groupby("trade_date"):
        vals = {_number(x, 10) for x in part.adj_factor}
        vals.discard(None)
        factor_map[date] = next(iter(vals)) if len(vals) == 1 and min(vals) > 0 else None
    anchor_day = _date(signal["anchor_date"])
    anchor_factor = factor_map.get(anchor_day)
    anchor_raw_close = _number(signal.get("anchor_close"))
    denominator = (anchor_raw_close * anchor_factor
                   if anchor_raw_close and anchor_raw_close > 0 and anchor_factor else None)
    if denominator is None:
        missing.append("anchor_price_or_factor")
    for day in set(d.trade_date) | set(m.trade_date):
        if not factor_map.get(day):
            missing.append(f"factor:{rel[day]}")

    # Tushare daily vol is lots (100 shares), TDX15 vol is shares. Verify
    # against complete overlapping *past* days before using any volume ratios.
    ratios = []
    daily_by_day = d.set_index("trade_date")
    for day, group in m[m.trade_date.lt(today)].groupby("trade_date"):
        if len(group) == 16 and day in daily_by_day.index:
            daily_shares = _number(daily_by_day.loc[day, "vol"])
            if daily_shares and daily_shares > 0:
                ratios.append(float(group.vol.sum()) / (daily_shares * 100.0))
    max_error = max((abs(v - 1) for v in ratios), default=None)
    volume_check = {"status": "unknown" if not ratios else (
        "consistent" if max_error <= .02 else "mismatch"),
        "compared_days": len(ratios), "max_relative_error": _number(max_error)}
    if not ratios:
        missing.append("volume_unit_crosscheck")
    elif max_error > .02:
        warnings.append("daily_lots_vs_tdx15_shares_mismatch")
    prior20 = before[-20:]
    denom_rows = d[d.trade_date.isin(prior20)]
    complete_volume = (len(prior20) == 20 and len(denom_rows) == 20 and
                       pd.to_numeric(denom_rows.vol, errors="coerce").gt(0).all())
    volume_base = float(denom_rows.vol.median() * 100) if complete_volume else None
    if not complete_volume:
        missing.append("prior_20_completed_daily_volume")
    # A failed unit check is explicitly unknown to the model; retain sample.
    if volume_check["status"] != "consistent":
        volume_base = None

    def price(raw: Any, day: str) -> float | None:
        value, factor = _number(raw), factor_map.get(day)
        return _number(100 * value * factor / denominator) if (
            value is not None and factor and denominator) else None

    daily_rows = [[f"D_{rel[r.trade_date]}", rel[r.trade_date],
                   *[price(getattr(r, k), r.trade_date) for k in PRICES],
                   _number(float(r.vol) * 100 / volume_base) if volume_base else None]
                  for r in d.itertuples()]
    minute_rows = [[f"M_{rel[r.trade_date]}_{r.datetime:%H%M}", rel[r.trade_date],
                    r.datetime.strftime("%H:%M"),
                    *[price(getattr(r, k), r.trade_date) for k in PRICES],
                    _number(float(r.vol) / volume_base) if volume_base else None]
                   for r in m.itertuples()]
    expected_m15 = 0
    for day in minute_days:
        expected = len(BAR_TIMES) if day < today else sum(t <= cutoff.strftime("%H:%M") for t in BAR_TIMES)
        expected_m15 += expected
        actual = int(m.trade_date.eq(day).sum())
        if actual != expected:
            missing.append(f"m15:{rel[day]}:{actual}_of_{expected}")
    packet = {
        "schema_version": SCHEMA_VERSION,
        "sample_id": sample_id,
        "decision": {"day": "D0", "bar_end": cutoff.strftime("%H:%M"), "timezone": "Asia/Shanghai"},
        "conventions": {
            "bar_time_is_end": True,
            "prices_normalized_to_anchor_close_100": True,
            "price_formula": "100 * raw_price * factor_at_bar_date / (anchor_raw_close * factor_at_anchor_date)",
            "price_adjustment_uses_only_factors_known_at_cutoff": True,
            "factor_availability_assumption": "effective_date_is_known_by_session_open; historical_publication_time_not_archived",
            "volume_is_ratio_to_prior_20_completed_daily_median": True,
            "volume_conversion": "daily_lots_x100_and_tdx15_shares_use_same_denominator",
            "execution_not_assessed": True,
        },
        "anchor": {"id": "ANCHOR", "day": rel.get(anchor_day), "close": 100.0 if denominator else None,
                   "board_height_known_then": _number(signal.get("anchor_board_height")),
                   "leader_rank_known_then": _number(signal.get("anchor_leader_rank")),
                   "theme_rank_known_then": _number(signal.get("anchor_theme_rank")),
                   "known_at": "anchor_day_close"},
        "daily_columns": DAILY_COLUMNS, "daily_bars": daily_rows,
        "m15_columns": M15_COLUMNS, "m15_bars": minute_rows,
        "prior_theme": {"id": "THEME_D-1", "day": "D-1", "theme_id": "T1",
                        "eligible_theme_rank": _number(signal.get("prior_theme_rank")),
                        "limit_up_count": _number(signal.get("prior_theme_width")),
                        "failed_limit_up_count": None, "max_board_height": None,
                        "heat_score": _number(signal.get("prior_theme_heat")),
                        "core_rank_known_then": None, "own_board_height_known_then": None},
        "data_quality": {"missing_past_fields": sorted(set(missing)), "warnings": warnings,
                         "observed_daily_bars": len(daily_rows), "expected_daily_bars": 60,
                         "observed_m15_bars": len(minute_rows), "expected_m15_bars": expected_m15,
                         "volume_unit_check": volume_check,
                         "future_fields_present": False},
    }
    private = {
        "sample_id": sample_id, "signal": {k: signal.get(k) for k in SOURCE_COLUMNS},
        "relative_date_mapping": rel, "volume_denominator_shares": volume_base,
        "volume_overlap_ratios": ratios, "anchor_factor": anchor_factor,
        "daily_source_prefix": d.to_dict("records"),
        "m15_source_prefix": m.to_dict("records"),
        "factor_source_prefix": f.to_dict("records"),
        "source_prefix_hashes": {"daily": sha256(d.to_dict("records")),
                                 "m15": sha256(m.to_dict("records")),
                                 "factors": sha256(f.to_dict("records"))},
    }
    return packet, _clean(private)


def load_inputs(signal: dict[str, Any]) -> tuple[list[str], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    from coreClient.data_provider import get_adj_factor, get_day, get_fifteenMin, get_tradecal
    cutoff = pd.Timestamp(signal["signal_time"])
    end = cutoff.strftime("%Y%m%d")
    cal = get_tradecal(start_date=(cutoff - pd.Timedelta(days=250)).strftime("%Y%m%d"),
                       end_date=end, source="database_only")
    days = sorted(cal.cal_date.map(_date).unique())
    before = [d for d in days if d < end]
    if not before:
        raise ValueError("no_past_open_calendar")
    kwargs = {"ts_code": signal["ts_code"]}
    m = get_fifteenMin(**kwargs, start_date=before[-5:][0], end_date=end)
    m = prefix_minutes(m, cutoff)  # No counts, metadata, factors or features before this.
    daily = get_day(**kwargs, start_date=before[-60:][0], end_date=before[-1],
                    qfq=False, source="database_only")
    factors = get_adj_factor(**kwargs, start_date=min(before[-60:][0], _date(signal["anchor_date"])),
                             end_date=end, source="database_only")
    return days, daily, factors, m


def _write_frozen(path: Path, value: Any) -> str:
    content = canonical_bytes(value)
    if path.exists() and path.read_bytes() != content:
        raise RuntimeError(f"Refusing to overwrite different frozen artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def run(source: Path, output: Path, count: int = 12, seed: int = SEED) -> dict[str, Any]:
    from run_core_pullback_blind import validate_packet
    pool, samples = select_samples(source, seed, count)
    private_root = output / "private"
    private_root.mkdir(parents=True, exist_ok=True)
    private_root.chmod(0o700)
    safe_pool = pool.loc[:, list(SOURCE_COLUMNS)].to_dict("records")
    mapping = {"seed": seed, "sort": ["signal_time", "ts_code", "anchor_date"],
               "candidate_signals": len(pool), "candidate_stocks": int(pool.ts_code.nunique()),
               "candidate_whitelist_sha256": sha256(safe_pool),
               "samples": [{"sample_id": f"S{i:02}", **s} for i, s in enumerate(samples, 1)]}
    _write_frozen(private_root / "sample_mapping.json", mapping)
    _write_frozen(private_root / "candidate_pool_whitelist.json", safe_pool)
    entries = []
    for i, sample in enumerate(samples, 1):
        sample_id = f"S{i:02}"
        packet, audit = build_packet(sample_id, sample, *load_inputs(sample))
        validate_packet(packet)
        packet_hash = _write_frozen(output / "packets" / f"{sample_id}.json", packet)
        audit_hash = _write_frozen(private_root / f"{sample_id}_audit.json", audit)
        entries.append({"sample_id": sample_id, "packet_file": f"packets/{sample_id}.json",
                        "input_sha256": packet_hash, "private_audit_sha256": audit_hash,
                        "daily_bars": len(packet["daily_bars"]), "m15_bars": len(packet["m15_bars"]),
                        "volume_unit_check": packet["data_quality"]["volume_unit_check"],
                        "missing_past_fields": packet["data_quality"]["missing_past_fields"]})
        print(f"{sample_id} frozen; daily={len(packet['daily_bars'])}, m15={len(packet['m15_bars'])}", flush=True)
    manifest = {"schema_version": SCHEMA_VERSION, "seed": seed, "requested_samples": count,
                "actual_samples": len(samples), "candidate_signals": len(pool),
                "candidate_stocks": int(pool.ts_code.nunique()),
                "candidate_whitelist_sha256": sha256(safe_pool),
                "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "source_columns": list(SOURCE_COLUMNS),
                "selection": "sort_then_seeded_shuffle_one_per_stock_no_outcome_or_post_signal_quality_filter",
                "fetch_isolation": "date-granularity fetch + immediate prefix isolation",
                "physical_query_timestamp_isolated": False,
                "factor_availability_assumption": "effective_date_known_at_open_not_publication_audited",
                "model_calls": 0, "read_outcome_columns": False, "packets": entries}
    _write_frozen(output / "PACKET_MANIFEST.json", manifest)
    for path in private_root.glob("*.json"):
        path.chmod(0o600)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "outputs/core_reactivation_causal_2025/trades.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/core_pullback_blind_2025")
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    manifest = run(args.source, args.output, args.count, args.seed)
    print(json.dumps({"packets": manifest["actual_samples"], "pool": manifest["candidate_signals"],
                      "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
