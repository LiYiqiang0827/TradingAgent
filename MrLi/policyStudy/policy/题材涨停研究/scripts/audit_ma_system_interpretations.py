"""Compare explicit wording interpretations using frozen local inputs, no P&L.

New files only. Original strict diagnostics and inputs are retained. More
coverage is a wording diagnostic, never the optimization objective for profits.
"""
from dataclasses import asdict, replace
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd

from ma_system_pullback import Rules, daily_facts, intraday_facts, scan_entries
from review_ma_system_pullback import plot_case, _configure_chinese_font


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    source = Path(args.input)
    dest = source / "interpretation_audit"
    if dest.exists():
        raise ValueError("Interpretation audit already saved")
    dest.mkdir()
    _configure_chinese_font()
    variants = {
        "strict_original": Rules(),
        "current_daily_strict_deduction": replace(Rules(), daily_order_mode="current"),
        "segment_daily_end_lower": replace(Rules(), deduction_mode="end_lower"),
        "current_daily_end_lower_4": replace(Rules(), daily_order_mode="current", deduction_mode="end_lower"),
        "current_daily_end_lower_8": replace(Rules(), daily_order_mode="current", deduction_mode="end_lower", cross_window=8, forecast_bars=8),
    }
    all_results = []
    cases = json.loads((source / "summary.json").read_text())
    for case in cases:
        if case["status"] != "diagnosed":
            continue
        folder = source / f"{case['code']}_{case['month']}"
        raw = pd.read_parquet(folder / "daily.parquet")
        adj = pd.read_parquet(folder / "factors.parquet")
        minute = pd.read_parquet(folder / "fifteen.parquet")
        cal = sorted(pd.read_parquet(folder / "calendar.parquet").cal_date.astype(str).unique())
        minute_first = (pd.Timestamp(case["month"] + "01") - pd.DateOffset(months=2)).strftime("%Y%m%d")
        minute_cal = [day for day in cal if day >= minute_first]
        for name, rules in variants.items():
            d = daily_facts(raw, adj, cal, rules)
            f = intraday_facts(minute, minute_cal, d, rules)
            di, candidates = scan_entries(f, d, rules)
            in_month = f.trade_date.str.startswith(case["month"])
            sliced = di.loc[in_month]
            row = dict(case=case["name"], code=case["code"], month=case["month"], interpretation=name,
                       daily_qualifying_days=int((d.daily_qualify & d.trade_date.str.startswith(case["month"])).sum()),
                       watch_bars=int(sliced.daily_watch.sum()),
                       **{key: int(sliced[key].sum()) for key in ["projection_ready", "stage3", "retest", "daily_confirm", "technical_candidate"]})
            all_results.append(row)
            target = dest / f"{case['code']}_{case['month']}" / name
            target.mkdir(parents=True)
            di.to_csv(target / "diagnostics.csv", index=False)
            month_candidates = [c for c in candidates if pd.Timestamp(c["time"]).strftime("%Y%m") == case["month"]]
            (target / "candidates.json").write_text(json.dumps(month_candidates, ensure_ascii=False, indent=2, default=str))
            # One independently rebuilt prefix per interpretation at mid-month.
            stamp = f.loc[in_month].datetime.iloc[len(sliced) // 2]
            prior = max(day for day in cal if day < stamp.strftime("%Y%m%d"))
            p_d = daily_facts(raw[raw.trade_date.astype(str).le(prior)], adj[adj.trade_date.astype(str).le(prior)], [day for day in cal if day <= prior], rules)
            p_f = intraday_facts(minute[pd.to_datetime(minute.datetime).le(stamp)], [day for day in minute_cal if day <= stamp.strftime("%Y%m%d")], p_d, rules)
            p_f = p_f[p_f.datetime.le(stamp)]
            p_di, p_cand = scan_entries(p_f, p_d, rules)
            pd.testing.assert_frame_equal(di.loc[f.datetime.le(stamp)].reset_index(drop=True), p_di.reset_index(drop=True))
            assert [c for c in candidates if pd.Timestamp(c["time"]) <= stamp] == p_cand
            row["prefix_check"] = str(stamp)
            if name == "strict_original":
                # Review images use ordinary month-end qfq prices for readability.
                plot_case(target, case["name"], case["month"], d, f, di)
            print(row, flush=True)
    pd.DataFrame(all_results).to_csv(dest / "coverage.csv", index=False)
    (dest / "rules.json").write_text(json.dumps({name: asdict(r) for name, r in variants.items()}, ensure_ascii=False, indent=2))
    (dest / "manifest.json").write_text(json.dumps(dict(
        purpose="wording_and_parameter_coverage_only", returns_evaluated=False,
        source_manifest_sha256=hashlib.sha256((source / "MANIFEST.json").read_bytes()).hexdigest(),
        code_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                     [Path(__file__), Path(__file__).with_name("ma_system_pullback.py"), Path(__file__).with_name("review_ma_system_pullback.py")]},
        independent_test=False, prefix_checks=len(all_results)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
