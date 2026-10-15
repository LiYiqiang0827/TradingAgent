"""Local, versioned diagnosis of the user's 2026 MA-system examples.

Seven confirmed month windows. December 2026 is future on the request date;
the Leike case must be added with an explicitly confirmed historical year.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import html
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
from coreClient import data_provider as dp
from ma_system_pullback import Rules, daily_facts, intraday_facts, scan_entries
from plot.plot_daily_kline import MA_COLORS, _configure_chinese_font, _draw_candles

CASES = [("近岸蛋白", "688137.SH", "202609"),
         ("精智达", "688627.SH", "202604"), ("精智达", "688627.SH", "202606"),
         ("京东方A", "000725.SZ", "202606"), ("山东赫达", "002810.SZ", "202608"),
         ("万向德农", "600371.SH", "202608"), ("康龙化成", "300759.SZ", "202608")]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plot_case(out, name, month, daily, minute, diagnostics):
    first = pd.Timestamp(month + "01")
    last = first + pd.offsets.MonthEnd(0)
    view = daily[daily.trade_date.le(last.strftime("%Y%m%d"))].tail(130).reset_index(drop=True)
    factor = float(daily.adj_factor.dropna().iloc[-1])
    for c in ("open", "high", "low", "close"):
        view[c] /= factor
    for n in (5, 10, 20, 30, 60, 120):
        view[f"ma{n}"] /= factor
    selected = diagnostics[diagnostics.time.str.startswith(first.strftime("%Y-%m"))]
    # Selection affects this review view only; it never changes signals or rules.
    score = selected[["compact_cross", "price_above120", "projection_ready", "retest", "daily_confirm"]].astype(int).sum(axis=1)
    focus_time = pd.Timestamp(selected.loc[score.idxmax(), "time"]) if len(selected) else minute.datetime.iloc[-1]
    focus_i = int(np.flatnonzero(minute.datetime.eq(focus_time))[0])
    lo, hi = max(0, focus_i - 96), min(len(minute), focus_i + 65)
    intraday = minute.iloc[lo:hi].reset_index(drop=True)
    for c in ("open", "high", "low", "close"):
        intraday[f"p_{c}"] /= factor
    for n in (5, 10, 20, 30, 60, 120):
        intraday[f"ma{n}"] /= factor
    di = diagnostics.iloc[lo:hi].reset_index(drop=True)
    fig, axes = plt.subplots(4, 1, figsize=(17, 13), gridspec_kw=dict(height_ratios=[4, 1, 4, 1], hspace=.46))
    _draw_candles(axes[0], view)
    for n in (5, 10, 20, 30, 60, 120):
        axes[0].plot(view.index, view[f"ma{n}"], color=MA_COLORS[n], lw=1, label=f"MA{n}")
    q = view.daily_qualify
    axes[0].scatter(view.index[q], view.close[q], color="#059669", marker="v", s=25, label="日线入选截面")
    axes[1].bar(view.index, view.raw_vol, color=np.where(view.close >= view.open, "#e75a57", "#2aa77a"), width=.65)
    axes[0].set_title(f"{name} {month}｜日线入选与回调复核；未生成实际交易", fontsize=13)
    positions = np.linspace(0, len(view) - 1, min(12, len(view))).astype(int)
    for ax in axes[:2]:
        ax.set_xticks(positions, [pd.Timestamp(view.trade_date.iloc[i]).strftime("%m/%d") for i in positions], fontsize=9)
        ax.grid(alpha=.15)
    axes[0].legend(ncol=7, fontsize=9)
    candles = intraday.rename(columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"})
    for c in ("open", "high", "low", "close"):
        candles[c] = candles[f"p_{c}"]
    _draw_candles(axes[2], candles)
    for n in (5, 10, 20, 30, 60, 120):
        axes[2].plot(intraday.index, intraday[f"ma{n}"], color=MA_COLORS[n], lw=1, label=f"MA{n}")
    for i in np.flatnonzero(di.bear):
        axes[2].axvspan(i - .5, i + .5, color="#64748b", alpha=.09)
    for flag, marker, color, label in [("stage3", "o", "#7c3aed", "三项启动证据"),
                                        ("retest", "s", "#d97706", "MA30再回踩"),
                                        ("technical_candidate", "^", "#059669", "技术候选（题材待核）")]:
        positions = np.flatnonzero(di[flag])
        axes[2].scatter(positions, intraday.p_close.iloc[positions], marker=marker, s=42, color=color, label=label, zorder=8)
    center = focus_i - lo
    axes[2].axvline(center, color="#334155", ls="--", lw=1)
    axes[2].axvspan(center + .5, len(intraday), color="#e2e8f0", alpha=.25)
    axes[2].set_title(f"15分钟条件诊断｜复核截面 {focus_time:%Y-%m-%d %H:%M}；右侧行情只用于图形复核", fontsize=12)
    axes[3].bar(intraday.index, intraday.vol, color=np.where(intraday.p_close >= intraday.p_open, "#e75a57", "#2aa77a"), width=.65)
    positions = np.linspace(0, len(intraday) - 1, min(9, len(intraday))).astype(int)
    for ax in axes[2:]:
        ax.set_xticks(positions, [intraday.datetime.iloc[i].strftime("%m/%d\n%H:%M") for i in positions], fontsize=8)
        ax.grid(alpha=.15)
    axes[2].legend(ncol=5, fontsize=8)
    axes[1].set_ylabel("成交量（手）", fontsize=9)
    axes[3].set_ylabel("成交量（股）", fontsize=9)
    fig.suptitle("均线系统回调草案 v1｜阈值为解释性起点，未按收益调参｜价格按样本月末前复权显示", fontsize=13, y=.99)
    fig.savefig(out / "review.png", dpi=140, bbox_inches="tight")
    plt.close(fig)


def run_case(output, name, code, month, rules):
    out = output / f"{code}_{month}"
    out.mkdir()
    first = pd.Timestamp(month + "01")
    end = (first + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")
    daily_start = (first - pd.DateOffset(months=10)).strftime("%Y%m%d")
    minute_start = (first - pd.DateOffset(months=2)).strftime("%Y%m%d")
    cal = dp.get_tradecal(start_date=daily_start, end_date=end, source="database_only")
    calendar = sorted(cal.cal_date.astype(str).unique())
    raw_d = dp.get_day(ts_code=code, start_date=daily_start, end_date=end, qfq=False, source="database_only")
    adj = dp.get_adj_factor(ts_code=code, start_date=daily_start, end_date=end, source="database_only")
    raw_m = dp.get_fifteenMin(ts_code=code, start_date=minute_start, end_date=end)
    for filename, data in [("calendar", cal), ("daily", raw_d), ("factors", adj), ("fifteen", raw_m)]:
        data.to_parquet(out / f"{filename}.parquet", index=False)
    if raw_d.empty or raw_m.empty:
        result = dict(name=name, code=code, month=month, status="data_missing")
        write_json(out / "summary.json", result)
        return result
    minute_calendar = [day for day in calendar if day >= minute_start]
    d = daily_facts(raw_d, adj, calendar, rules)
    f = intraday_facts(raw_m, minute_calendar, d, rules)
    di, candidates = scan_entries(f, d, rules)
    d.to_csv(out / "daily_facts.csv", index=False)
    di.to_csv(out / "bar_diagnostics.csv", index=False)
    write_json(out / "candidates.json", candidates)
    in_month = f.trade_date.str.startswith(month)
    ix = np.flatnonzero(in_month)
    checks = []
    for i in np.unique(np.linspace(ix[0], ix[-1], 5).astype(int)):
        stamp = f.datetime.iloc[i]
        prior_day = max(day for day in calendar if day < stamp.strftime("%Y%m%d"))
        prefix_d = daily_facts(raw_d[raw_d.trade_date.astype(str).le(prior_day)],
                               adj[adj.trade_date.astype(str).le(prior_day)],
                               [day for day in calendar if day <= prior_day], rules)
        prefix_f = intraday_facts(raw_m[pd.to_datetime(raw_m.datetime).le(stamp)],
                                  [day for day in minute_calendar if day <= stamp.strftime("%Y%m%d")], prefix_d, rules)
        prefix_f = prefix_f[prefix_f.datetime.le(stamp)]
        prefix_di, prefix_candidates = scan_entries(prefix_f, prefix_d, rules)
        pd.testing.assert_frame_equal(di.iloc[:i + 1].reset_index(drop=True), prefix_di.reset_index(drop=True))
        assert [c for c in candidates if pd.Timestamp(c["time"]) <= stamp] == prefix_candidates
        checks.append(str(stamp))
    selected_di = di.loc[in_month]
    result = dict(name=name, code=code, month=month, status="diagnosed",
                  bars=int(in_month.sum()), valid_bars=int((in_month & f.valid).sum()),
                  daily_qualifying_days=int((d.trade_date.str.startswith(month) & d.daily_qualify).sum()),
                  active_watch_bars=int(selected_di.daily_watch.sum()),
                  gate_counts={col: int(selected_di[col].sum()) for col in
                               ("bear", "compact_cross", "price_above120", "projection_ready", "stage3", "retest", "daily_confirm", "technical_candidate")},
                  prefix_checks=checks, formal_entries="unknown_without_theme_evidence",
                  known_development_example=True, returns_evaluated=False)
    plot_case(out, name, month, d, f, di)
    write_json(out / "summary.json", result)
    # Interpretation comparisons only. No return/objective enters these runs.
    sensitivity = []
    for mode in ("up_close_days", "whole_run", "current"):
        alt = daily_facts(raw_d, adj, calendar, replace(rules, daily_order_mode=mode))
        sensitivity.append(dict(order_mode=mode,
                                qualifying_month_days=int((alt.trade_date.str.startswith(month) & alt.daily_qualify).sum())))
    write_json(out / "daily_interpretations.json", sensitivity)
    print(name, month, result["gate_counts"], flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "outputs/ma_system_pullback_20261005_v1"))
    parser.add_argument("--leike-month", help="Explicit confirmed historical month, e.g. 202512")
    args = parser.parse_args()
    cases = CASES.copy()
    if args.leike_month:
        stamp = pd.Timestamp(args.leike_month + "01")
        if stamp >= pd.Timestamp("20261005"):
            raise ValueError("Leike month must have occurred by the request date")
        cases.append(("雷科防务", "002413.SZ", args.leike_month))
    output = Path(args.output)
    if output.exists():
        raise ValueError("Use a fresh version directory; diagnostics cannot be overwritten")
    output.mkdir(parents=True)
    rules = Rules()
    write_json(output / "RULES.json", asdict(rules))
    _configure_chinese_font()
    results = [run_case(output, name, code, month, rules) for name, code, month in cases]
    write_json(output / "summary.json", results)
    manifest = dict(as_of="20261005", rules=asdict(rules), user_year_confirmation="2026",
                    unresolved_leike=not args.leike_month, local_only=True,
                    parameter_optimization=False, returns_evaluated=False,
                    code_sha256={str(p.relative_to(ROOT)): digest(p) for p in
                                 [Path(__file__), Path(__file__).with_name("ma_system_pullback.py")]},
                    inputs_sha256={str(p.relative_to(output)): digest(p) for p in output.glob("*/*.parquet")})
    write_json(output / "MANIFEST.json", manifest)
    cards = []
    for r in results:
        title = html.escape(f"{r['name']} {r['month']}｜{r['code']}")
        counts = html.escape(json.dumps(r.get("gate_counts", {}), ensure_ascii=False))
        cards.append(f'<article><h2>{title}</h2><p>{counts}</p><a href="{r["code"]}_{r["month"]}/review.png"><img src="{r["code"]}_{r["month"]}/review.png"></a></article>')
    (output / "index.html").write_text('<!doctype html><meta charset="utf-8"><title>均线系统回调诊断</title><style>body{font-family:system-ui;background:#f3f5f7;color:#243247;margin:32px}article{background:white;padding:20px;margin:24px 0;border-radius:12px}img{width:100%}p{overflow-wrap:anywhere}</style><h1>均线系统回调：用户样本诊断 v1</h1><p>已知开发样本；未按收益调参，未生成真实成交或收益。每项计数单位为15分钟K线；重叠证据不能相加为交易数。日线使用前一完整交易日，当前形态只使用截至当根的分钟聚合。</p>' + ''.join(cards))


if __name__ == "__main__":
    main()
