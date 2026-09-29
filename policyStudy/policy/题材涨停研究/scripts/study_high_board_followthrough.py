"""Descriptive high-board close-to-close returns from offline KPL and day tables.

An event is the first observed board at or above the selected threshold within
one consecutive limit-up run. Later board records in that run are not counted
as new observations. The peak-board view is retrospective only.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from coreClient.data_provider import get_day, get_kpl_list


HORIZONS = (1, 2, 3, 5, 10)


def load_events(start_date: str, end_date: str, threshold: int) -> tuple[pd.DataFrame, dict]:
    kpl = get_kpl_list(
        start_date=start_date, end_date=end_date, tags="涨停", source="database_only"
    )
    if kpl.empty:
        raise RuntimeError("No offline KPL limit-up rows in the requested period")
    last_kpl_date = str(kpl.trade_date.max())
    kpl = kpl[kpl.trade_date.astype(str) <= last_kpl_date].copy()
    kpl["board_height"] = pd.to_numeric(
        kpl.status.astype(str).str.extract(r"^(\d+)连板$", expand=False), errors="coerce"
    )
    high = kpl[kpl.board_height >= threshold].copy()
    high["excluded_st"] = high.name.fillna("").str.contains(r"ST|退", case=False)
    high["excluded_bj"] = high.ts_code.fillna("").str.endswith(".BJ")
    high["excluded_theme"] = high.theme.fillna("").str.contains(
        r"ST板块|ST摘帽|次新", case=False
    )
    exclude = high[["excluded_st", "excluded_bj", "excluded_theme"]].any(axis=1)
    valid = high[~exclude].copy().sort_values(["ts_code", "trade_date", "board_height"])
    valid = valid.drop_duplicates(["ts_code", "trade_date"], keep="last")
    valid["prior_height"] = valid.groupby("ts_code").board_height.shift()
    valid["new_run"] = valid.prior_height.isna() | (
        valid.board_height != valid.prior_height + 1
    )
    valid["run_number"] = valid.groupby("ts_code").new_run.cumsum().astype(int)
    event = valid.groupby(["ts_code", "run_number"], sort=False).agg(
        name=("name", "first"),
        threshold_or_first_date=("trade_date", "first"),
        first_height=("board_height", "first"),
        peak_date=("trade_date", "last"),
        peak_height=("board_height", "last"),
        theme=("theme", "first"),
    ).reset_index()
    event["first_height"] = event.first_height.astype(int)
    event["peak_height"] = event.peak_height.astype(int)
    audit = {
        "board_threshold": threshold,
        "kpl_last_date": last_kpl_date,
        "kpl_high_board_rows_raw": int(len(high)),
        "excluded_high_board_rows": int(exclude.sum()),
        "excluded_st_rows": int(high.excluded_st.sum()),
        "excluded_bj_rows": int(high.excluded_bj.sum()),
        "excluded_st_or_new_theme_rows": int(high.excluded_theme.sum()),
        "eligible_high_board_rows": int(len(valid)),
        "events": int(len(event)),
        "unique_stocks": int(event.ts_code.nunique()),
        "left_censored_first_above_threshold": int((event.first_height > threshold).sum()),
    }
    return event, audit


def score(events: pd.DataFrame, end_date: str) -> tuple[pd.DataFrame, str]:
    # A liquid benchmark provides the actual daily trading-date grid, including
    # any gaps in the locally cached trading-calendar table.
    benchmark = get_day(
        ts_code="000001.SZ", start_date=events.threshold_or_first_date.min(),
        end_date=end_date, qfq=False, source="database_only",
    )
    if benchmark.empty:
        raise RuntimeError("No offline trading-date grid")
    dates = sorted(benchmark.trade_date.astype(str).unique().tolist())
    date_index = {date: i for i, date in enumerate(dates)}
    last_day_date = dates[-1]
    prices = get_day(
        ts_codes=events.ts_code.unique().tolist(),
        start_date=events.threshold_or_first_date.min(), end_date=last_day_date,
        qfq=True, source="database_only",
    )
    prices["trade_date"] = prices.trade_date.astype(str)
    price = prices.set_index(["ts_code", "trade_date"]).close.to_dict()
    rows = []
    for event in events.itertuples(index=False):
        for anchor_kind, anchor_date in (
            ("first_threshold_plus", event.threshold_or_first_date),
            ("final_board_hindsight", event.peak_date),
        ):
            anchor_date = str(anchor_date)
            anchor_price = price.get((event.ts_code, anchor_date))
            for horizon in HORIZONS:
                target_index = date_index.get(anchor_date, -1) + horizon
                target_date = dates[target_index] if 0 <= target_index < len(dates) else ""
                target_price = price.get((event.ts_code, target_date)) if target_date else None
                if anchor_date not in date_index or not anchor_price:
                    status = "missing_anchor_quote"
                elif not target_date:
                    status = "right_censored"
                elif target_price is None or pd.isna(target_price):
                    status = "missing_target_quote"
                else:
                    status = "observed"
                rows.append({
                    "ts_code": event.ts_code,
                    "name": event.name,
                    "run_number": event.run_number,
                    "first_height": event.first_height,
                    "peak_height": event.peak_height,
                    "theme": event.theme,
                    "anchor_kind": anchor_kind,
                    "anchor_date": anchor_date,
                    "target_date": target_date,
                    "horizon_trading_days": horizon,
                    "status": status,
                    "return_pct": round((target_price / anchor_price - 1) * 100, 6)
                    if status == "observed" else None,
                })
    samples = pd.DataFrame(rows)
    return samples, last_day_date


def summarize(samples: pd.DataFrame) -> pd.DataFrame:
    summaries = []
    for (kind, horizon), group in samples.groupby(
        ["anchor_kind", "horizon_trading_days"], sort=False
    ):
        observed = group.loc[group.status == "observed", "return_pct"].astype(float)
        summaries.append({
            "anchor_kind": kind,
            "horizon_trading_days": int(horizon),
            "event_count": int(len(group)),
            "observed_count": int(len(observed)),
            "right_censored_count": int((group.status == "right_censored").sum()),
            "missing_quote_count": int(group.status.str.startswith("missing").sum()),
            "mean_return_pct": round(float(observed.mean()), 4) if len(observed) else None,
            "median_return_pct": round(float(observed.median()), 4) if len(observed) else None,
            "positive_rate_pct": round(float((observed > 0).mean() * 100), 2)
            if len(observed) else None,
            "min_return_pct": round(float(observed.min()), 4) if len(observed) else None,
            "max_return_pct": round(float(observed.max()), 4) if len(observed) else None,
        })
    return pd.DataFrame(summaries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", default="20260101")
    parser.add_argument("--end-date", default="20260929")
    parser.add_argument("--board-threshold", type=int, default=6)
    parser.add_argument("--output-dir")
    args = parser.parse_args()
    if args.board_threshold < 2:
        parser.error("--board-threshold must be at least 2")
    threshold_name = {5: "five", 6: "six"}.get(args.board_threshold, str(args.board_threshold))
    output = Path(args.output_dir or f"outputs/{threshold_name}_board_followthrough_2026")
    output.mkdir(parents=True, exist_ok=True)
    events, audit = load_events(args.start_date, args.end_date, args.board_threshold)
    if events.empty:
        raise RuntimeError("No eligible high-board events")
    samples, audit["day_last_date"] = score(events, args.end_date)
    summary = summarize(samples)
    exact_threshold = summarize(samples[samples.first_height == args.board_threshold])
    events.to_csv(output / "events.csv", index=False, encoding="utf-8-sig")
    samples.to_csv(output / "horizon_samples.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(output / "summary.csv", index=False, encoding="utf-8-sig")
    exact_threshold.to_csv(output / "exact_threshold_summary.csv", index=False, encoding="utf-8-sig")
    exact_threshold.to_csv(
        output / f"exact_{threshold_name}_summary.csv", index=False, encoding="utf-8-sig"
    )
    (output / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    print(summary.to_string(index=False))
    print(f"Exactly-{args.board_threshold} anchors (excludes 2025 carry-in runs):")
    print(exact_threshold.to_string(index=False))


if __name__ == "__main__":
    main()
