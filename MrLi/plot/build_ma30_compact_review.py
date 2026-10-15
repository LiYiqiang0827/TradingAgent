"""Prepare an exhaustive MA30 chart review with a causal 7% MA5/10/20 gate.

Keep the previous 90 episodes as controls. Apply the new condition to all 107
old daily MA30 matches *before* collapsing episodes, so a later qualifying day
is not lost merely because an earlier, wider-spread observation was selected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "policyStudy/policy/题材涨停研究/scripts"
for p in (ROOT, SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from coreClient.data_provider import get_adj_factor, get_day
from backtest_rising_ma_reactivation_202605_202608 import _episodes, _stock_frame
from plot.plot_ma30_reactivation_candidates import generate
from plot.plot_fifteen_min_kline import (build_save_path, load_fifteen_min_data,
                                       plot_fifteen_min_kline, resolve_window)

SOURCE = ROOT / "outputs/rising_ma_reactivation/may_aug_2026_final"
OUTPUT = ROOT / "outputs/rising_ma_reactivation/ma30_compact7_review"


def collapse_compact_matches(rows: pd.DataFrame, calendar: list[str], threshold: float = 7.) -> pd.DataFrame:
    passed = rows.loc[rows.ma5_20_spread_pct.le(threshold + 1e-10)].copy()
    return _episodes(passed, calendar, gap=10)


def _stats(frame: pd.DataFrame) -> dict:
    r = pd.to_numeric(frame.net_10d_pct, errors="coerce").dropna()
    return {"candidates": len(frame), "valid_returns": len(r),
            "mean_net_pct": float(r.mean()) if len(r) else None,
            "median_net_pct": float(r.median()) if len(r) else None,
            "win_rate_pct": float(r.gt(0).mean() * 100) if len(r) else None,
            "worst_net_pct": float(r.min()) if len(r) else None}


def prepare(source: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    dates = {c: str for c in ("ts_code", "trade_date", "entry_date", "exit_date_10d",
                              "first_wave_board_dates")}
    daily = pd.read_csv(source / "daily_matches.csv", dtype=dates)
    daily = daily.loc[daily.template_observation.eq("ma30_coil_restart")].copy()
    old = pd.read_csv(source / "first_episode_matches.csv", dtype=dates)
    old = old.loc[old.template_observation.eq("ma30_coil_restart")].copy()
    codes = sorted(daily.ts_code.unique())
    raw = get_day(ts_codes=codes, start_date="20250101", end_date="20260918",
                  qfq=False, source="database_only")
    adj = get_adj_factor(ts_codes=codes, start_date="20250101", end_date="20260918",
                         source="database_only")
    raw.trade_date = raw.trade_date.astype(str)
    adj.trade_date = adj.trade_date.astype(str)
    adj_groups = dict(tuple(adj.groupby("ts_code")))
    frames = {code: _stock_frame(group, adj_groups[code]).set_index("trade_date", drop=False)
              for code, group in raw.groupby("ts_code")}
    values = []
    for row in daily.itertuples():
        bar = frames[row.ts_code].loc[row.trade_date]
        mas = np.array([bar.ma5, bar.ma10, bar.ma20], dtype=float)
        if not np.isfinite(mas).all() or mas.min() <= 0:
            raise ValueError(f"{row.ts_code} {row.trade_date} 均线缺失")
        spread = 100 * (mas.max() / mas.min() - 1)
        values.append({"ts_code": row.ts_code, "trade_date": row.trade_date,
                       "ma5_20_spread_pct": spread,
                       "signal_ma5": bar.ma5 / bar.adj_factor,
                       "signal_ma10": bar.ma10 / bar.adj_factor,
                       "signal_ma20": bar.ma20 / bar.adj_factor})
    features = pd.DataFrame(values)
    daily = daily.drop(columns=[c for c in features.columns if c not in ("ts_code", "trade_date")
                               and c in daily.columns]).merge(features, on=["ts_code", "trade_date"],
                                                             validate="one_to_one")
    calendar = sorted(get_day(ts_code="000001.SZ", start_date="20250101", end_date="20260918",
                               qfq=False, source="database_only").trade_date.astype(str).unique())
    compact = collapse_compact_matches(daily, calendar)
    old_keys = set(zip(old.ts_code, old.trade_date))
    compact_keys = set(zip(compact.ts_code, compact.trade_date))
    union = daily.loc[[(c, d) in old_keys | compact_keys for c, d in zip(daily.ts_code, daily.trade_date)]].copy()
    union["original_episode"] = [(c, d) in old_keys for c, d in zip(union.ts_code, union.trade_date)]
    union["compact_episode"] = [(c, d) in compact_keys for c, d in zip(union.ts_code, union.trade_date)]
    union["compact_pass"] = union.ma5_20_spread_pct.le(7 + 1e-10)
    union = union.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    for filename, frame in (("daily_ma30_with_spread.csv", daily),
                            ("compact_episodes.csv", compact), ("review_episodes.csv", union)):
        frame.to_csv(output / filename, index=False, encoding="utf-8-sig")
    old_augmented = daily.loc[[(c, d) in old_keys for c, d in zip(daily.ts_code, daily.trade_date)]]
    stats = {"formula": "100*(max(MA5,MA10,MA20)/min(MA5,MA10,MA20)-1)",
             "feature_cutoff": "signal-day close, before next-day opening proxy entry",
             "threshold_pct": 7, "original_daily_matches": len(daily),
             "compact_daily_matches": int(daily.ma5_20_spread_pct.le(7 + 1e-10).sum()),
             "original_episodes": _stats(old_augmented),
             "original_episodes_passing_gate": _stats(old_augmented.loc[old_augmented.ma5_20_spread_pct.le(7 + 1e-10)]),
             "compact_reselected_episodes": _stats(compact),
             "new_signal_dates": len(compact_keys - old_keys), "review_cases": len(union),
             "monthly_compact": {month: _stats(group) for month, group in compact.groupby(compact.trade_date.str[:6])},
             "source_sha256": {f: hashlib.sha256((source / f).read_bytes()).hexdigest()
                               for f in ("daily_matches.csv", "first_episode_matches.csv")},
             "caveat": "Retrospective exploratory filter. Daily opening-price proxies, 0.4pp cost. No tested intraday fills or new exit strategy."}
    (output / "compact_summary.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
    return stats


def charts(output: Path) -> None:
    daily_charts = output / "charts"
    if not (daily_charts / "manifest.json").exists():
        print("生成全部日线筛选图与日线买卖回放图", flush=True)
        print(generate(output / "review_episodes.csv", daily_charts), flush=True)
    records = pd.read_csv(daily_charts / "MA30候选交易表.csv", dtype={
        "ts_code": str, "signal_date": str, "entry_date": str, "exit_date": str})
    episodes = pd.read_csv(output / "review_episodes.csv", dtype={"ts_code": str, "trade_date": str})
    records = records.merge(episodes[["ts_code", "trade_date", "signal_ma5", "signal_ma10", "signal_ma20",
                                     "ma5_20_spread_pct", "compact_pass", "compact_episode", "original_episode"]],
                            left_on=["ts_code", "signal_date"], right_on=["ts_code", "trade_date"],
                            validate="one_to_one").drop(columns="trade_date")
    result = []
    audits = []
    for i, row in enumerate(records.to_dict("records"), start=1):
        window = resolve_window(start_date=row["entry_date"], end_date=row["exit_date"], trade_date=None,
                                buy_date=row["entry_date"], sell_date=row["exit_date"],
                                lookback_months=1, lookahead_days=7)
        frame = load_fifteen_min_data(row["ts_code"], window, qfq=True)
        path = build_save_path(row["ts_code"], window, output / "15min_replay").resolve()
        if not path.exists():
            plot_fifteen_min_kline(frame, ts_code=row["ts_code"], stock_name=row["name"],
                                   window=window, qfq=True, save_path=path, show=False)
        row["minute_chart_path"] = str(path)
        row["minute_display_start"] = str(frame.iloc[0].date.date())
        row["minute_display_end"] = str(frame.iloc[-1].date.date())
        row["minute_bars"] = len(frame)
        by_day = frame.groupby("date").size()
        entry = frame.loc[frame.date.eq(window.buy_date)].iloc[0]
        exit_bar = frame.loc[frame.date.eq(window.sell_date)].iloc[0]
        minute_return = 100 * (exit_bar.open / entry.open - 1)
        row["minute_open_return_pct"] = minute_return
        audit = {"ts_code": row["ts_code"], "signal_date": row["signal_date"],
                 "bars": len(frame), "days": len(by_day), "days_not_16": int(by_day.ne(16).sum()),
                 "requested_display": [str(window.display_start.date()), str(window.display_end.date())],
                 "minute_open_return_pct": minute_return,
                 "daily_proxy_return_pct": row["price_change_pct"],
                 "return_difference_pp": minute_return - row["price_change_pct"]}
        audits.append(audit)
        result.append(row)
        print(f"15分钟图 {i}/{len(records)} {row['ts_code']} {row['signal_date']}", flush=True)
    full = pd.DataFrame(result)
    full.insert(0, "case_number", range(1, len(full) + 1))
    full.to_csv(output / "全部候选_均线粘合_图表索引.csv", index=False, encoding="utf-8-sig")
    full.loc[full.compact_episode].to_csv(output / "MA粘合7以内_重新去重交易表.csv", index=False, encoding="utf-8-sig")
    payload = {"summary": json.loads((output / "compact_summary.json").read_text()),
               "cases": full.to_dict("records"), "source": str(output.resolve())}
    (output / "deck_data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (output / "15min_data_audit.json").write_text(json.dumps(audits, ensure_ascii=False, indent=2))
    print(f"图表和CSV准备完成，共{len(full)}个案例、{len(full)*3}张图", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=SOURCE)
    p.add_argument("--output", type=Path, default=OUTPUT)
    p.add_argument("--prepare-only", action="store_true")
    args = p.parse_args()
    prepare(args.source, args.output)
    if not args.prepare_only:
        charts(args.output)
