"""Point-in-time four-board cohort and descriptive post-board path audit.

The cohort and theme features are known after the four-board close. Future
prices and KPL statuses are used only for outcome labels, never as features.
This is a path study, not a trade or a fill simulation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from coreClient.data_provider import get_day, get_kpl_list, get_theme_daily
from study_high_board_followthrough import load_events


ROOT = Path(__file__).resolve().parents[4]
GRAPH = ROOT / "offlineDataManager/data/db_theme_graph.duckdb"


def first_primary_themes(graph_path: Path, end_date: str) -> pd.DataFrame:
    """Read the immutable event-to-primary-theme facts, not episode hindsight."""
    con = duckdb.connect(str(graph_path), read_only=True)
    try:
        return con.execute(
            """SELECT e.trade_date, e.ts_code, r.theme_id
               FROM fact_limit_event e
               JOIN rel_limit_theme r USING (event_id)
               WHERE e.trade_date BETWEEN '20260101' AND ?
                 AND e.tag='涨停' AND r.attribution_role='primary'""",
            [end_date],
        ).df().drop_duplicates(["trade_date", "ts_code"])
    finally:
        con.close()


def classify_broken_path(bars: list[dict], later_board_dates: set[str],
                         anchor_close: float) -> tuple[str, str | None]:
    """Classify only a complete 10-session path after the four-board day.

    A-kill means a 20% intraday drawdown from D0 close before any renewed
    limit-up. A renewed limit-up first is a rebound candidate, not a buy fill.
    """
    if len(bars) != 10:
        return "right_censored_or_missing", None
    for bar in bars:
        date = bar["trade_date"]
        if date in later_board_dates and bar["low"] <= anchor_close * 0.8:
            return "same_day_order_unknown", date
        if date in later_board_dates:
            return "relimit_before_deep_drawdown", date
        if bar["low"] <= anchor_close * 0.8:
            return "deep_drawdown_before_relimit", date
    if any(bar["close"] >= anchor_close for bar in bars):
        return "reclaimed_close_without_relimit", None
    return "neither_relimit_nor_reclaim", None


def run(end_date: str, graph_path: Path = GRAPH) -> tuple[pd.DataFrame, dict]:
    events, audit = load_events("20260101", end_date, 4)
    events = events[events.first_height.eq(4)].copy()
    kpl_end = audit["kpl_last_date"]
    benchmark = get_day(ts_code="000001.SZ", start_date="20260101",
                        end_date=end_date, qfq=False, source="database_only")
    dates = sorted(benchmark.trade_date.astype(str).unique())
    indices = {day: i for i, day in enumerate(dates)}
    day_end = dates[-1]
    prices = get_day(ts_codes=events.ts_code.unique().tolist(),
                     start_date="20260101", end_date=day_end,
                     qfq=True, source="database_only")
    prices["trade_date"] = prices.trade_date.astype(str)
    price = {(code, date): rec for (code, date), rec in
             prices.set_index(["ts_code", "trade_date"]).to_dict("index").items()}
    kpl = get_kpl_list(start_date="20260101", end_date=kpl_end,
                       tags="涨停", source="database_only")
    statuses = {(r.ts_code, str(r.trade_date)): str(r.status)
                for r in kpl.itertuples()}
    kpl_record = {(r.ts_code, str(r.trade_date)): r for r in kpl.itertuples()}

    themes = first_primary_themes(graph_path, kpl_end)
    theme_id = {(r.ts_code, str(r.trade_date)): r.theme_id
                for r in themes.itertuples()}
    daily = get_theme_daily(start_date="20260101", end_date=kpl_end)
    theme_daily = {(r.theme_id, str(r.trade_date)): r
                   for r in daily.itertuples()}
    rank = {}
    ranked_daily = daily[
        daily.level1_name.ne("ST与次新")
        & ~daily.canonical_name.str.contains(r"ST板块|ST摘帽|次新", case=False, na=False)
    ]
    for date, group in ranked_daily.groupby("trade_date"):
        for i, r in enumerate(group.sort_values("heat_score", ascending=False).itertuples(), 1):
            rank[(r.theme_id, str(date))] = i

    rows = []
    for event in events.itertuples(index=False):
        d0 = str(event.threshold_or_first_date)
        anchor = price.get((event.ts_code, d0))
        i = indices.get(d0)
        future_dates = dates[i + 1:i + 11] if i is not None else []
        future = [price.get((event.ts_code, day)) for day in future_dates]
        complete10 = len(future_dates) == 10 and all(x is not None for x in future)
        bars = ([{"trade_date": date, **bar} for date, bar in zip(future_dates, future)]
                if complete10 else [])
        d1 = future_dates[0] if future_dates else None
        direct = statuses.get((event.ts_code, d1)) == "5连板" if d1 else False
        later = {date for date in future_dates[1:]
                 if (event.ts_code, date) in statuses}
        base = float(anchor["close"]) if anchor else np.nan
        if direct:
            path, path_date = "direct_five_board", d1
        elif not np.isfinite(base):
            path, path_date = "missing_anchor_quote", None
        else:
            path, path_date = classify_broken_path(bars, later, base)
        good5 = len(future_dates) >= 5 and all(x is not None for x in future[:5])
        td = theme_id.get((event.ts_code, d0))
        theme = theme_daily.get((td, d0))
        d1_theme = theme_daily.get((td, d1))
        d0_kpl = kpl_record.get((event.ts_code, d0))
        row = {
            "ts_code": event.ts_code, "name": event.name, "d0": d0,
            "d1": d1, "d0_theme_raw": event.theme, "theme_id": td,
            "theme_name": theme.canonical_name if theme else None,
            "theme_heat_d0": float(theme.heat_score) if theme else np.nan,
            "theme_width_d0": int(theme.limit_up_count) if theme else np.nan,
            "theme_break_count_d0": int(theme.break_count) if theme else np.nan,
            "theme_rank_d0": rank.get((td, d0)),
            "theme_heat_d1": float(d1_theme.heat_score) if d1_theme else np.nan,
            "theme_width_d1": int(d1_theme.limit_up_count) if d1_theme else np.nan,
            "theme_rank_d1": rank.get((td, d1)),
            "d0_kpl_bid_amount": pd.to_numeric(d0_kpl.bid_amount, errors="coerce")
                if d0_kpl else np.nan,
            "d0_kpl_limit_time": str(d0_kpl.lu_time) if d0_kpl else None,
            "d0_close_qfq": base, "d0_amount": float(anchor["amount"]) if anchor else np.nan,
            "d0_volume": float(anchor["vol"]) if anchor else np.nan,
            "d1_amount_ratio": (float(future[0]["amount"]) / float(anchor["amount"])
                if future and future[0] and anchor and anchor["amount"] else np.nan),
            "d1_direct_five": direct, "d1_open_pct":
                (100 * (float(future[0]["open"]) / base - 1)
                 if future and future[0] and np.isfinite(base) else np.nan),
            "d1_close_pct":
                (100 * (float(future[0]["close"]) / base - 1)
                 if future and future[0] and np.isfinite(base) else np.nan),
            "d1_low_pct":
                (100 * (float(future[0]["low"]) / base - 1)
                 if future and future[0] and np.isfinite(base) else np.nan),
            "d1_high_pct":
                (100 * (float(future[0]["high"]) / base - 1)
                 if future and future[0] and np.isfinite(base) else np.nan),
            "complete5": good5, "complete10": complete10,
            "min_low_5_pct":
                (100 * (min(float(x["low"]) for x in future[:5]) / base - 1)
                 if good5 and np.isfinite(base) else np.nan),
            "min_low_10_pct":
                (100 * (min(float(x["low"]) for x in future) / base - 1)
                 if complete10 and np.isfinite(base) else np.nan),
            "max_high_10_pct":
                (100 * (max(float(x["high"]) for x in future) / base - 1)
                 if complete10 and np.isfinite(base) else np.nan),
            "d5_close_pct":
                (100 * (float(future[4]["close"]) / base - 1)
                 if good5 and np.isfinite(base) else np.nan),
            "d10_close_pct":
                (100 * (float(future[9]["close"]) / base - 1)
                 if complete10 and np.isfinite(base) else np.nan),
            "path": path, "path_first_date": path_date,
        }
        for horizon in range(1, 11):
            bar = future[horizon - 1] if len(future) >= horizon else None
            row[f"close_d{horizon}_pct"] = (
                100 * (float(bar["close"]) / base - 1)
                if bar and np.isfinite(base) else np.nan
            )
            row[f"low_d{horizon}_pct"] = (
                100 * (float(bar["low"]) / base - 1)
                if bar and np.isfinite(base) else np.nan
            )
        rows.append(row)
    frame = pd.DataFrame(rows).sort_values(["d0", "ts_code"]).reset_index(drop=True)
    audit.update({
        "cohort_exact_four": len(frame), "day_last_date": day_end,
        "theme_daily_last_date": str(daily.trade_date.max()),
        "theme_covered": int(frame.theme_name.notna().sum()),
        "complete5": int(frame.complete5.sum()),
        "complete10": int(frame.complete10.sum()),
        "path_counts": frame.path.value_counts().to_dict(),
    })
    return frame, audit


def plot_paths(frame: pd.DataFrame, output: Path) -> None:
    mature = frame[frame.complete10]
    groups = [
        ("direct_five_board", "D+1 continuous board"),
        ("relimit_before_deep_drawdown", "Break, then renewed board"),
        ("deep_drawdown_before_relimit", "Break, then -20% before any renewed board"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2), sharey=True)
    cols = [f"close_d{i}_pct" for i in range(1, 11)]
    for ax, (key, title) in zip(axes, groups):
        values = mature.loc[mature.path.eq(key), cols].to_numpy(dtype=float)
        points = np.arange(11)
        for value in values:
            ax.plot(points, np.r_[0.0, value], color="#8ca2b9", alpha=.16, lw=.65)
        if len(values):
            full = np.c_[np.zeros(len(values)), values]
            ax.plot(points, np.median(full, axis=0), color="#24416b", lw=2.3)
            ax.fill_between(points, np.percentile(full, 25, axis=0),
                            np.percentile(full, 75, axis=0), color="#799bc1", alpha=.3)
        ax.axhline(0, color="black", lw=.8)
        ax.axhline(-20, color="#b4423e", ls="--", lw=.8)
        ax.set(title=f"{title} (n={len(values)})", xlabel="Trading sessions after fourth board")
        ax.grid(alpha=.2)
    axes[0].set_ylabel("Close return vs fourth-board close (%)")
    fig.suptitle("2026 fourth-board paths: complete 10-session observations")
    fig.tight_layout()
    fig.savefig(output, dpi=170)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-date", default="20260929")
    parser.add_argument("--output-dir", default="outputs/four_board_paths_2026")
    args = parser.parse_args()
    frame, audit = run(args.end_date)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out / "four_board_paths.csv", index=False, encoding="utf-8-sig")
    plot_paths(frame, out / "four_board_paths.png")
    (out / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
