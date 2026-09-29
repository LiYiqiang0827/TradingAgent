"""Exploratory returns for decision-time theme filters on fixed MA20 cases.

The 26 stock setups and exits come from study_ma20_second_wave.py. Theme
signals are formed at the close using only primary-attribution limit-ups
available through that date; execute() tries the following tradable open.
These variants are post-hoc research, not independently validated rules.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

from coreClient.data_provider import get_day, get_stk_limit, get_theme_members
from study_ma20_second_wave import _stock_frame, execute


VARIANTS = {
    "touch_all": lambda x: pd.Series(True, index=x.index),
    "touch_width_ge3": lambda x: x.touch_theme_width >= 3,
    "touch_old_peer_ge1": lambda x: x.touch_old_peer_limit >= 1,
    "touch_width_ge3_and_old_peer_ge1": lambda x: (
        (x.touch_theme_width >= 3) & (x.touch_old_peer_limit >= 1)),
}


def _statistics(returns: pd.Series, *, signals: int, statuses: dict | None = None) -> dict:
    values = pd.to_numeric(returns, errors="coerce").dropna()
    return {
        "signals": int(signals), "closed": int(len(values)),
        "status_counts": statuses or {"closed": int(len(values))},
        "mean_net_pct": float(values.mean()) if len(values) else None,
        "median_net_pct": float(values.median()) if len(values) else None,
        "wins": int((values > 0).sum()),
        "worst_net_pct": float(values.min()) if len(values) else None,
        "best_net_pct": float(values.max()) if len(values) else None,
    }


def _daily_stocks(codes: list[str], data_end: str) -> tuple[dict, dict]:
    qfq = get_day(ts_codes=codes, start_date="20260301", end_date=data_end,
                  qfq=True, source="database_only")
    raw = get_day(ts_codes=codes, start_date="20260301", end_date=data_end,
                  qfq=False, source="database_only")
    raw = raw[["ts_code", "trade_date", "open", "high", "low", "vol"]].rename(
        columns={"open": "raw_open", "high": "raw_high", "low": "raw_low",
                 "vol": "raw_vol"})
    merged = qfq.merge(raw, on=["ts_code", "trade_date"], how="left",
                       validate="one_to_one")
    stocks = {code: _stock_frame(frame) for code, frame in merged.groupby("ts_code")}
    limits = get_stk_limit(ts_codes=codes, start_date="20260601",
                           end_date=data_end, source="database_only")
    by_code = {code: {} for code in codes}
    for row in limits.itertuples():
        if pd.notna(row.up_limit) and pd.notna(row.down_limit):
            by_code[row.ts_code][str(row.trade_date)] = (
                float(row.up_limit), float(row.down_limit))
    return stocks, by_code


def _first_theme_reexpansion(case: pd.Series, stock: pd.DataFrame,
                             *, old_min: int, max_sessions: int = 8) -> dict:
    """First close with >=3 original-theme boards and enough frozen peers."""
    dates = stock.trade_date.tolist()
    if case.touch_date not in dates:
        return {"signal_status": "touch_quote_missing"}
    start = dates.index(case.touch_date)
    old = set(str(case.touch_frozen_old_peer_codes).split(",")) \
        if pd.notna(case.touch_frozen_old_peer_codes) else set()
    old -= {"", "nan", case.ts_code}
    for day in stock.iloc[start:start+max_sessions+1].itertuples():
        # get_theme_members enforces the as-of cutoff; neither later KPL
        # attribution nor future theme heat can enter the day's signal.
        members = get_theme_members(case.theme_id, as_of=day.trade_date,
                                    historical=True)
        today = set(members.loc[
            members.trade_date.astype(str) == day.trade_date, "ts_code"])
        old_count = len(today & old)
        if (len(today) >= 3 and old_count >= old_min
                and day.close >= day.ma20 * .99):
            return {"signal_status": "ready", "signal_date": day.trade_date,
                    "signal_theme_width": len(today),
                    "signal_old_peer_limit": old_count,
                    "signal_stock_close_to_ma20_pct": 100 * (day.close / day.ma20 - 1)}
    return {"signal_status": "no_theme_reexpansion"}


def run(audit_path: Path, output: Path, *, data_end: str = "20260928") -> dict:
    audit = pd.read_csv(audit_path, dtype={
        "first_date": str, "peak_date": str, "touch_date": str})
    audit = audit[audit.status.eq("ok")].copy()
    if len(audit) != 26 or not audit.early_net_return_pct.notna().all():
        raise ValueError("Expected 26 completed fixed stock-only cases")
    static_summary = {}
    for name, selector in VARIANTS.items():
        selected = audit.loc[selector(audit)]
        static_summary[name] = _statistics(
            selected.early_net_return_pct, signals=len(selected))

    codes = sorted(audit.ts_code.unique())
    stocks, limits = _daily_stocks(codes, data_end)
    dynamic_rows = []
    for case in audit.itertuples(index=False):
        stock = stocks.get(case.ts_code)
        if stock is None or stock.empty:
            for old_min in (1, 2):
                dynamic_rows.append({"variant": f"reexpand_old_ge{old_min}",
                                     "ts_code": case.ts_code, "name": case.name,
                                     "signal_status": "stock_quote_missing"})
            continue
        case_series = pd.Series(case._asdict())
        for old_min in (1, 2):
            variant = f"reexpand_old_ge{old_min}"
            signal = _first_theme_reexpansion(case_series, stock, old_min=old_min)
            row = {"variant": variant, "ts_code": case.ts_code,
                   "name": case.name, "touch_date": case.touch_date,
                   "peak_primary": case.peak_primary, **signal}
            if signal["signal_status"] == "ready":
                row.update(execute(stock, signal["signal_date"], limits[case.ts_code]))
            dynamic_rows.append(row)
    dynamic = pd.DataFrame(dynamic_rows)
    output.mkdir(parents=True, exist_ok=True)
    dynamic.to_csv(output / "theme_return_dynamic_audit.csv", index=False,
                   encoding="utf-8-sig")
    dynamic_summary = {}
    for variant, group in dynamic.groupby("variant"):
        triggered = group[group.signal_status.eq("ready")]
        closed = triggered[triggered.trade_status.eq("closed")]
        dynamic_summary[variant] = _statistics(
            closed.net_return_pct, signals=len(triggered),
            statuses=dict(Counter(triggered.trade_status.fillna("unknown"))))
        dynamic_summary[variant]["no_signal"] = int(
            group.signal_status.eq("no_theme_reexpansion").sum())
    strict = dynamic[dynamic.variant.eq("reexpand_old_ge2")].merge(
        audit[["ts_code", "touch_wave_theme_peak_width", "touch_theme_width"]],
        on="ts_code", validate="one_to_one")
    strict = strict[(strict.touch_wave_theme_peak_width >= 3)
                    & (strict.touch_theme_width < 3)
                    & strict.signal_status.eq("ready")
                    & (strict.signal_date > strict.touch_date)]
    strict_closed = strict[strict.trade_status.eq("closed")]
    dynamic_summary["first_broad_touch_narrow_then_reexpand_old_ge2"] = _statistics(
        strict_closed.net_return_pct, signals=len(strict),
        statuses=dict(Counter(strict.trade_status.fillna("unknown"))))
    summary = {"sample": "2026-06-01 to 2026-08-31 MA20 touches",
               "cases": len(audit), "data_end": data_end,
               "round_trip_cost_pct": 0.4,
               "static_at_touch": static_summary,
               "dynamic_reexpansion": dynamic_summary,
               "interpretation": "Post-hoc exploratory variants; not out-of-sample returns."}
    (output / "theme_return_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path,
                        default=Path("outputs/ma20_second_wave_202606_202608/theme_cohort_audit.csv"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("outputs/ma20_second_wave_202606_202608"))
    parser.add_argument("--data-end", default="20260928")
    args = parser.parse_args()
    print(json.dumps(run(args.audit, args.output_dir, data_end=args.data_end),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
