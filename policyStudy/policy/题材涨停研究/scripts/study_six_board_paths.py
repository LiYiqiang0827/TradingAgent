"""Classify exact six-board runs and quantify post-board A-kill risk.

All outcomes are retrospective. The sixth-board-day theme and candle facts are
separate from future peak height, relimit, and drawdown labels.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import pandas as pd

from coreClient.data_provider import get_day, get_kpl_list
from study_high_board_followthrough import load_events


ROOT = Path(__file__).resolve().parents[4]
GRAPH = ROOT / "offlineDataManager/data/db_theme_graph.duckdb"


def _theme_facts(start: str, end: str) -> pd.DataFrame:
    con = duckdb.connect(str(GRAPH), read_only=True)
    try:
        return con.execute(
            """SELECT e.trade_date,e.ts_code,t.canonical_name AS primary_theme,
                      d.limit_up_count AS theme_width,d.break_count AS theme_breaks,
                      d.heat_score AS theme_heat,e.lu_time
               FROM fact_limit_event e
               JOIN rel_limit_theme r USING(event_id)
               JOIN dim_theme t USING(theme_id)
               JOIN fact_theme_daily d ON d.trade_date=e.trade_date
                                      AND d.theme_id=r.theme_id
               WHERE e.tag='涨停' AND r.attribution_role='primary'
                 AND e.board_height=6 AND e.trade_date BETWEEN ? AND ?""",
            [start, end],
        ).df()
    finally:
        con.close()


def _window(calendar: list[str], index: dict[str, int], prices: dict,
            code: str, anchor_day: str, sessions: int = 10) -> list[tuple[str, object | None]]:
    begin = index[anchor_day] + 1
    return [(day, prices.get((code, day))) for day in calendar[begin:begin + sessions]]


def _low_drawdown(window: list[tuple[str, object | None]],
                  anchor: float, sessions: int) -> float | None:
    values = [float(bar.low) for _, bar in window[:sessions] if bar is not None]
    return 100 * (min(values) / anchor - 1) if values and anchor > 0 else None


def _first_below(window: list[tuple[str, object | None]],
                 anchor: float, threshold: float = .80) -> str | None:
    return next((day for day, bar in window if bar is not None
                 and float(bar.low) <= anchor * threshold), None)


def run(end_date: str = "20260928") -> tuple[pd.DataFrame, dict]:
    events, source_audit = load_events("20260101", end_date, 6)
    events = events.loc[events.first_height.eq(6)].copy()
    if events.empty:
        raise ValueError("No exact sixth-board anchors")
    first = str(events.threshold_or_first_date.min())
    benchmark = get_day(ts_code="000001.SZ", start_date=first, end_date=end_date,
                        qfq=False, source="database_only")
    calendar = sorted(benchmark.trade_date.astype(str).unique())
    index = {date: i for i, date in enumerate(calendar)}
    codes = events.ts_code.unique().tolist()
    price_frame = get_day(ts_codes=codes, start_date=first, end_date=end_date,
                          qfq=True, source="database_only")
    price_frame.trade_date = price_frame.trade_date.astype(str)
    prices = {(r.ts_code, r.trade_date): r for r in price_frame.itertuples()}
    kpl = get_kpl_list(start_date=first, end_date=end_date, tags="涨停",
                       source="database_only")
    limit_dates = {(r.ts_code, str(r.trade_date)) for r in kpl.itertuples()}
    theme = _theme_facts(first, end_date)
    theme.trade_date = theme.trade_date.astype(str)
    if theme.duplicated(["trade_date", "ts_code"]).any():
        raise ValueError("Ambiguous sixth-board primary-theme facts")
    theme_map = {(r.ts_code, r.trade_date): r for r in theme.itertuples()}
    rows = []
    for event in events.itertuples(index=False):
        date = str(event.threshold_or_first_date)
        peak_day = str(event.peak_date)
        anchor = prices.get((event.ts_code, date))
        peak = prices.get((event.ts_code, peak_day))
        facts = theme_map.get((event.ts_code, date))
        if not anchor or not peak or not facts or date not in index or peak_day not in index:
            raise ValueError(f"Missing six-board anchor or theme: {event.ts_code} {date}")
        base = float(anchor.close)
        peak_price = float(peak.close)
        six_window = _window(calendar, index, prices, event.ts_code, date)
        peak_window = _window(calendar, index, prices, event.ts_code, peak_day)
        complete_six_5 = len(six_window) >= 5 and all(bar is not None for _, bar in six_window[:5])
        complete_six_10 = len(six_window) == 10 and all(bar is not None for _, bar in six_window)
        complete_peak_5 = len(peak_window) >= 5 and all(bar is not None for _, bar in peak_window[:5])
        complete_peak_10 = len(peak_window) == 10 and all(bar is not None for _, bar in peak_window)
        deep_day = _first_below(six_window, base)
        relimit_day = next((day for day, _ in six_window
                            if (event.ts_code, day) in limit_dates), None)
        if event.peak_height > 6:
            path = "continued_seven_plus"
        elif not complete_six_10:
            path = "incomplete_window"
        elif deep_day and (not relimit_day or deep_day < relimit_day):
            path = "direct_A20_before_relimit"
        elif deep_day and relimit_day == deep_day:
            path = "same_day_order_unknown"
        elif relimit_day:
            path = "break_then_relimit_before_A20"
        else:
            path = "no_relimit_or_A20_in_10d"
        width = int(facts.theme_width)
        theme_group = "独立高标" if width == 1 else "小梯队(2-4)" if width <= 4 else "集体题材(5+)"
        one_price = bool(abs(float(anchor.high) - float(anchor.low)) <= 1e-6)
        row = {
            "ts_code": event.ts_code, "name": event.name, "six_date": date,
            "primary_theme": facts.primary_theme, "theme_width": width,
            "theme_breaks": int(facts.theme_breaks), "theme_heat": float(facts.theme_heat),
            "theme_group": theme_group, "six_board_one_price": one_price,
            "six_board_lu_time": str(facts.lu_time), "six_close_qfq": base,
            "peak_height_hindsight": int(event.peak_height), "peak_date_hindsight": peak_day,
            "post_six_path_hindsight": path,
            "first_relimit_date_hindsight": relimit_day,
            "first_low_20pct_date_hindsight": deep_day,
            "six_5d_complete": complete_six_5, "six_10d_complete": complete_six_10,
            "peak_5d_complete": complete_peak_5, "peak_10d_complete": complete_peak_10,
        }
        for horizon in (3, 5, 10):
            row[f"six_min_low_{horizon}d_pct"] = _low_drawdown(six_window, base, horizon)
            row[f"peak_min_low_{horizon}d_pct"] = _low_drawdown(peak_window, peak_price, horizon)
        rows.append(row)
    frame = pd.DataFrame(rows).sort_values(["six_date", "ts_code"]).reset_index(drop=True)
    complete_10 = frame.loc[frame.six_10d_complete]
    complete_peak_10 = frame.loc[frame.peak_10d_complete]
    complete_peak_5 = frame.loc[frame.peak_5d_complete]
    audit = {
        "cutoff": end_date, "kpl_last_date": source_audit["kpl_last_date"],
        "exact_six_events": int(len(frame)),
        "carry_in_from_2025_excluded": int(source_audit["left_censored_first_above_threshold"]),
        "peak_height_counts_hindsight": {str(k): int(v) for k, v in
                                          frame.peak_height_hindsight.value_counts().sort_index().items()},
        "six_day_theme_group_counts": {str(k): int(v) for k, v in
                                       frame.theme_group.value_counts().items()},
        "six_day_one_price_count": int(frame.six_board_one_price.sum()),
        "post_six_path_counts_hindsight": {str(k): int(v) for k, v in
                                            frame.post_six_path_hindsight.value_counts().items()},
        "six_10d_complete": int(len(complete_10)),
        "direct_A20_before_relimit_complete": int(complete_10.post_six_path_hindsight.eq(
            "direct_A20_before_relimit").sum()),
        "six_10d_low_at_least_20pct": int(complete_10.six_min_low_10d_pct.le(-20).sum()),
        "peak_5d_complete": int(len(complete_peak_5)),
        "peak_5d_low_at_least_20pct": int(complete_peak_5.peak_min_low_5d_pct.le(-20).sum()),
        "peak_10d_complete": int(len(complete_peak_10)),
        "peak_10d_low_at_least_15pct": int(complete_peak_10.peak_min_low_10d_pct.le(-15).sum()),
        "peak_10d_low_at_least_20pct": int(complete_peak_10.peak_min_low_10d_pct.le(-20).sum()),
        "peak_10d_low_at_least_25pct": int(complete_peak_10.peak_min_low_10d_pct.le(-25).sum()),
        "partly_observed_deep_before_relimit": int(frame.loc[~frame.six_10d_complete].apply(
            lambda r: bool(r.first_low_20pct_date_hindsight and
                           (not r.first_relimit_date_hindsight or
                            r.first_low_20pct_date_hindsight < r.first_relimit_date_hindsight)),
            axis=1).sum()),
        "definition": "A20: post-anchor qfq daily low <= anchor close*0.80; direct means before any later KPL limit-up within ten market sessions. Full-window rates exclude missing-price or right-censored cases.",
        "limitation": "Intraday low is not an executable sale price; final-peak group and path are hindsight labels; same-day limit-up/low ordering is unobservable from daily bars.",
    }
    return frame, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-date", default="20260928")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/six_board_classification_2026")
    args = parser.parse_args()
    frame, audit = run(args.end_date)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output_dir / "classified_events.csv", index=False, encoding="utf-8-sig")
    (args.output_dir / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
