"""Conditional execution audits of every distinct grid candidate, not a portfolio."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ma_system_pullback import Rules, daily_facts, intraday_facts
from ma_system_execution import Costs, replay
from review_ma_system_pullback import ROOT, write_json, digest
from plot.plot_daily_kline import MA_COLORS, _configure_chinese_font, _draw_candles


def plot_replay(path, candidate, daily, frame, ledgers, summaries):
    time = pd.Timestamp(candidate["time"])
    center = int(np.flatnonzero(frame.datetime.eq(time))[0])
    # Include all completed execution evidence, with a capped pre-entry context.
    last = max(pd.Timestamp(s["last_time"]) for s in summaries if s.get("last_time"))
    end = int(np.flatnonzero(frame.datetime.eq(last))[0]) + 1
    minute = frame.iloc[max(0, center - 48):end].reset_index(drop=True).copy()
    factor = float(frame.adj_factor.iloc[center])
    view = daily[daily.trade_date.le(time.strftime("%Y%m%d"))].tail(100).reset_index(drop=True).copy()
    observed_day = frame[(frame.datetime.dt.strftime("%Y%m%d") == time.strftime("%Y%m%d")) & frame.datetime.le(time)]
    current_day = view.trade_date.eq(time.strftime("%Y%m%d"))
    if len(observed_day) and current_day.any() and observed_day.valid.all():
        partial = dict(open=observed_day.p_open.iloc[0], high=observed_day.p_high.max(),
                       low=observed_day.p_low.min(), close=observed_day.p_close.iloc[-1])
        for col, value in partial.items():
            view.loc[current_day, col] = value
        history = daily[daily.trade_date.lt(time.strftime("%Y%m%d"))].close
        for n in (5, 10, 20, 30, 60, 120):
            view.loc[current_day, f"ma{n}"] = (history.tail(n-1).sum() + partial["close"]) / n
    for c in ("open", "high", "low", "close"):
        view[c] /= factor
        minute[c] = minute[f"p_{c}"] / factor
    for n in (5, 10, 20, 30, 60, 120):
        view[f"ma{n}"] /= factor
        minute[f"ma{n}"] /= factor
    fig, axes = plt.subplots(3, 1, figsize=(17, 13), gridspec_kw=dict(height_ratios=[3, 4, 4], hspace=.35))
    _draw_candles(axes[0], view)
    for n in (5, 10, 20, 30, 60, 120):
        axes[0].plot(view.index, view[f"ma{n}"], c=MA_COLORS[n], lw=1, label=f"MA{n}")
    axes[0].set_title(f"{candidate['name']}｜{time:%Y-%m-%d %H:%M}候选：日线截至信号时点，末根由已完成15分钟K线聚合")
    axes[0].legend(ncol=6, fontsize=9)
    px = np.linspace(0, len(view)-1, 9).astype(int)
    axes[0].set_xticks(px, [pd.Timestamp(view.trade_date.iloc[j]).strftime("%m/%d") for j in px], fontsize=8)
    for ax, ledger, summary in zip(axes[1:], ledgers, summaries):
        _draw_candles(ax, minute)
        for n in (5, 10, 20, 30, 60, 120):
            ax.plot(minute.index, minute[f"ma{n}"], c=MA_COLORS[n], lw=.8, label=f"MA{n}")
        fill_number = 0
        for event in ledger:
            if event["kind"] != "fill":
                continue
            fill_number += 1
            j = int(np.flatnonzero(minute.datetime.eq(pd.Timestamp(event["bar_end"])))[0])
            buy = event["side"] == "buy"
            ax.scatter(j, event["price"] * frame.loc[frame.datetime.eq(pd.Timestamp(event["bar_end"])), "adj_factor"].iloc[0] / factor,
                       marker="^" if buy else "v", c="#d44949" if buy else "#15866b", s=55, zorder=5)
            ax.annotate(f"{'买' if buy else '卖'}{fill_number}", (j, minute.close.iloc[j]),
                        xytext=(2, 13 + 10 * (fill_number % 2) if buy else -19 - 10 * (fill_number % 2)),
                        textcoords="offset points", fontsize=8)
        split = int(np.flatnonzero(minute.datetime.eq(time))[0])
        ax.axvline(split, c="#26354a", ls="--", lw=1)
        ax.axvspan(split+.5, len(minute), color="#dbe4ef", alpha=.16)
        mode = "允许买回" if summary["allow_rebuy"] else "禁止买回（对照）"
        status = "已清仓" if summary["status"] == "closed" else "期末仍持仓（含浮盈）"
        ax.set_title(f"{mode}｜{status}｜初始投入净变动 {summary['return_on_initial_outlay_pct']:+.2f}%｜卖出{summary['sells']}笔、买回{summary['rebuys']}笔")
        px = np.linspace(0, len(minute)-1, 10).astype(int)
        ax.set_xticks(px, [minute.datetime.iloc[j].strftime("%m/%d\n%H:%M") for j in px], fontsize=8)
        ax.legend(ncol=6, fontsize=8, loc="lower left")
        ax.margins(y=.12)
    for ax in axes:
        ax.grid(alpha=.15)
    fig.suptitle("条件成交诊断：题材与回调时效未通过正式核验；每个候选账户独立，收益不能相加；右侧为事后执行路径", fontsize=12)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def main():
    root = ROOT / "outputs/ma_system_pullback_20261005_v2"
    out = root / "replay"
    out.mkdir(exist_ok=False)
    candidates = json.loads((root / "grid/unique_technical_candidates.json").read_text())
    candidates.sort(key=lambda c: (c["case"], c["time"]))
    raw_candidates = json.loads((root / "grid/candidates.json").read_text())
    rules = Rules(daily_order_mode="current")
    frames, summaries, checks = {}, [], []
    _configure_chinese_font()
    for number, candidate in enumerate(candidates, 1):
        key = candidate["case"]
        code = key.split("_")[0]
        if key not in frames:
            source = ROOT / "outputs/ma_system_pullback_20261005_v1" / key
            raw_d, adj, cal = [pd.read_parquet(source / f"{n}.parquet") for n in ("daily", "factors", "calendar")]
            d = daily_facts(raw_d, adj, sorted(cal.cal_date.astype(str).unique()), rules)
            new = root / "execution_inputs" / key
            raw_m, limits, cal_m = [pd.read_parquet(new / f"{n}.parquet") for n in ("fifteen", "limits", "calendar")]
            f = intraday_facts(raw_m, sorted(cal_m.cal_date.astype(str).unique()), d, rules)
            frames[key] = (d, f, limits)
        d, f, limits = frames[key]
        ident = f"T{number:02}"
        folder = out / ident
        folder.mkdir()
        sources = [c for c in raw_candidates if c["case"] == key and c["time"] == candidate["time"] and c["technical_candidate"]]
        first = sources[0]
        lookup = {str(t): i for i, t in enumerate(f.datetime)}
        candidate["episode_age_at_entry_bars"] = lookup[candidate["time"]] - lookup[first["episode_start"]]
        candidate["armed_wait_bars"] = lookup[candidate["time"]] - lookup[first["armed_at"]]
        candidate["episode_start"] = first["episode_start"]
        candidate["armed_at"] = first["armed_at"]
        write_json(folder / "candidate.json", candidate)
        ledgers, pair = [], []
        for allow in (True, False):
            summary, ledger, curve = replay(f, limits, candidate["time"], code, rules, allow_rebuy=allow)
            summary.update(id=ident, name=candidate["name"], case=key,
                           episode_age_at_entry_bars=candidate["episode_age_at_entry_bars"],
                           armed_wait_bars=candidate["armed_wait_bars"], configs=candidate["configs"])
            label = "rebuy" if allow else "no_rebuy"
            write_json(folder / f"{label}_summary.json", summary)
            write_json(folder / f"{label}_ledger.json", ledger)
            pd.DataFrame(curve).to_csv(folder / f"{label}_equity.csv", index=False)
            pair.append(summary); ledgers.append(ledger); summaries.append(summary)
            start = int(np.flatnonzero(f.datetime.eq(pd.Timestamp(candidate["time"])))[0])
            for cut in (start + 8, start + 16, min(start + 48, len(f)-1)):
                if cut >= len(f):
                    continue
                stamp = f.datetime.iloc[cut]
                _, prefix_ledger, prefix_curve = replay(f.iloc[:cut+1], limits, candidate["time"], code, rules, allow_rebuy=allow)
                full_ledger = [e for e in ledger if pd.Timestamp(e["bar_end"]) <= stamp]
                full_curve = [e for e in curve if pd.Timestamp(e["time"]) <= stamp]
                assert prefix_ledger == full_ledger
                assert prefix_curve == full_curve
                checks.append(dict(id=ident, rebuy=allow, cutoff=str(stamp), status="prefix_equal"))
        plot_replay(folder / "review.png", candidate, d, f, ledgers, pair)
        print(ident, candidate["name"], candidate["time"], [(p["status"], round(p["return_on_initial_outlay_pct"], 3), p["rebuys"]) for p in pair], flush=True)
    write_json(out / "summaries.json", summaries)
    write_json(out / "prefix_checks.json", checks)
    pd.DataFrame(summaries).drop(columns=["costs", "configs", "issues"]).to_csv(out / "summary.csv", index=False)
    write_json(out / "MANIFEST.json", dict(conditional_only=True, portfolio_returns=False,
                                          known_development_examples=True, prefix_checks=len(checks),
                                          costs=asdict(Costs()), exit_rules=asdict(rules),
                                          no_profit_optimization=True,
                                          candidate_inputs=digest(root / "grid/unique_technical_candidates.json"),
                                          execution_input_hashes={str(p.relative_to(root)): digest(p) for p in (root / "execution_inputs").glob("*/*.parquet")},
                                          code_hashes={p.name: digest(p) for p in [Path(__file__), Path(__file__).with_name("ma_system_execution.py"), Path(__file__).with_name("ma_system_pullback.py")]}))


if __name__ == "__main__":
    main()
