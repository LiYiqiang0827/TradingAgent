"""Render two explicit development cases with decision-prefix and outcome views.

Run after the provider inputs and FROZEN_CONFIRMATION.json have been saved.
No new data download, universe screen, trade execution or parameter search.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT), str(ROOT / "offlineDataManager/scripts")]
from plot.plot_daily_kline import MA_COLORS, _configure_chinese_font, _draw_candles
from study_daily_intraday_pullback import prepare_daily
from review_ma_pullback_template import wave_metrics
from pullback_price_confirmation import evaluate_at, freeze_base, monitor_path, pullback_depth_score, phase_volume_profile

CASES = {
    "688137.SH": dict(name="近岸蛋白", launch="20260807", prior="20260911"),
    "002413.SZ": dict(name="雷科防务", launch="20251124", prior="20251224"),
}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")


def metrics(code, case, output):
    q = output/code
    cal = pd.read_parquet(q/"calendar_decision_prefix.parquet")
    dates = sorted(cal.cal_date.astype(str).unique())
    dates = [d for d in dates if d <= case["prior"]]
    raw = pd.read_parquet(q/"daily_decision_prefix.parquet")
    adj = pd.read_parquet(q/"adj_decision_prefix.parquet")
    d = prepare_daily(raw[raw.trade_date.astype(str).le(case["prior"])],
                      adj[adj.trade_date.astype(str).le(case["prior"])], dates)
    w = wave_metrics(d, case["launch"], case["prior"])
    peak = d.index[d.trade_date.eq(w["peak_date"])][0]
    path = d.iloc[peak+1:]
    last = d.iloc[-1]
    factor = float(last.adj_factor)
    w.update(max_correction_drawdown_pct=float(100*(1-path.low.min()/d.iloc[peak].high)),
             current_low_drawdown_pct=float(100*(1-last.low/d.iloc[peak].high)),
             last_date=str(last.trade_date),
             ma_values={f"ma{n}":float(last[f"ma{n}"]/factor) for n in (5,10,20,30,60,120)},
             ma_rising_runs={f"ma{n}":int(last[f"rise{n}"]) for n in (5,10,20,30,60,120)})
    w["pullback_depth_score"]=pullback_depth_score(w["max_correction_drawdown_pct"])
    w["depth_score_policy"]="provisional soft_v1: max(0,100-4*max(0,drawdown_pct-35)); component only"
    w["depth_hard_gate_applied"]=False
    w["phase_volume_profile"]=phase_volume_profile(d,case["launch"],case["prior"])
    return d, w


def decorate(ax, vol, x, dates, *, intraday=False):
    ax.grid(alpha=.16)
    vol.grid(axis="y", alpha=.15)
    ax.set_xlim(-.8, len(x)-.2)
    vol.set_xlim(ax.get_xlim())
    ax.tick_params(axis="x", labelbottom=False)
    if intraday:
        indexes = [i for i,t in enumerate(dates) if i == 0 or t.date()!=dates[i-1].date()]
        labels = [dates[i].strftime("%m/%d\n%H:%M") for i in indexes]
    else:
        indexes = np.linspace(0,len(x)-1,7,dtype=int).tolist()
        labels = [dates[i].strftime("%m/%d") for i in indexes]
    vol.set_xticks(indexes,labels)
    vol.set_ylabel("成交量\n万手" if not intraday else "成交量\n万股")
    ax.set_ylabel("价格（元）")
    ax.legend(loc="upper left", ncol=6, fontsize=9)


def draw_daily(ax, vol, d, w, name):
    f = d.tail(100).copy().reset_index(drop=True)
    scale = float(d.adj_factor.iloc[-1])
    for c in ("open","high","low","close"):
        f[c] /= scale
    colors = _draw_candles(ax,f)
    pos = np.arange(len(f))
    for n in (5,10,20,30,60,120):
        ax.plot(pos,f[f"ma{n}"]/scale,color=MA_COLORS[n],lw=1.1,label=f"MA{n}")
    vol.bar(pos,f.raw_vol/10000,color=colors,width=.68)
    vol.axhline(w["baseline_volume"]/10000,color="#555555",ls="--",lw=1.2,
                label="启动前20日均量")
    vol.plot(pos,f.raw_vol.rolling(5,min_periods=5).mean()/10000,color="#cc7800",lw=1.15,
             label="5日均量")
    vol.legend(loc="upper left",ncol=2,fontsize=8)
    loc = {str(day):i for i,day in enumerate(f.trade_date)}
    a,b = loc[w["launch_date"]],loc[w["peak_date"]]
    for axis in (ax,vol):
        axis.axvspan(a-.5,b+.5,color="#e6ac42",alpha=.12)
        axis.axvspan(b+.5,len(f)-.5,color="#4a9fce",alpha=.10)
    ax.annotate(f"首波涨停启动\n{w['launch_date']}",(a,float(f.close.iloc[a])),
                xytext=(-12,-42),textcoords="offset points",fontsize=9,
                arrowprops=dict(arrowstyle="->",color="#a66a08"))
    ax.annotate(f"已知波段高点 {w['peak_raw_high']:.2f}\n{w['peak_date']}",
                (b,w["peak_raw_high"]),xytext=(-95,20),textcoords="offset points",fontsize=9,
                arrowprops=dict(arrowstyle="->",color="#7b4c16"))
    ax.annotate(f"观察日 {w['last_date']}\n低点 {w['pullback_low']:.2f}",
                (len(f)-1,w["pullback_low"]),xytext=(-125,-30),textcoords="offset points",fontsize=9,
                arrowprops=dict(arrowstyle="->",color="#12658c"))
    ax.set_title(f"{name}：日线背景仅到前一交易日 {w['last_date']}｜不含信号日收盘K线",fontsize=13)
    dates = pd.to_datetime(f.trade_date).tolist()
    decorate(ax,vol,f,dates)
    return dict(price_volume_xlim_equal=bool(np.allclose(ax.get_xlim(),vol.get_xlim())),
                volume_bars=len(pos), positive_volume_bars=int((f.raw_vol>0).sum()), data_end=w["last_date"])


def draw_intraday(ax, vol, f, base, entry, name, outcome=False, trace=None):
    # Include the first bar of the first displayed day for consistent day ticks.
    first_day=f.tail(80).datetime.iloc[0].normalize()
    f = f[f.datetime.ge(first_day)].copy().reset_index(drop=True)
    colors = _draw_candles(ax,f)
    pos = np.arange(len(f))
    scale = float(entry["price_scale"])
    for n in (5,10,20,30,60,120):
        ax.plot(pos,f[f"ma{n}"]*scale,color=MA_COLORS[n],lw=1.05,label=f"MA{n}")
    vol.bar(pos,f.vol/10000,color=colors,width=.68)
    lookup = {str(t):i for i,t in enumerate(f.datetime)}
    signal_x = lookup.get(entry["decision_time"])
    if signal_x is not None:
        ax.scatter([signal_x],[entry["close"]],s=95,marker="^",color="#951aad",zorder=8)
        ax.annotate(f"{entry.get('chart_label','确认')} {pd.Timestamp(entry['decision_time']).strftime('%m/%d %H:%M')}\n收盘 {entry['close']:.2f}（非成交价）",
                    (signal_x,entry["close"]),xytext=entry.get("annotation_offset",(-145,35 if outcome else 47)),textcoords="offset points",
                    fontsize=10,color="#7c148e",arrowprops=dict(arrowstyle="->",color="#951aad"),
                    bbox=dict(facecolor="white",edgecolor="none",alpha=.88,pad=2))
    start = lookup.get(base["base_start"])
    end = lookup.get(base["base_end"])
    if start is not None and end is not None:
        box_end = signal_x if signal_x is not None else end
        ax.add_patch(Rectangle((start-.5,base["lower"]),box_end-start+1,
                              base["upper"]-base["lower"],facecolor="#42a2c1",edgecolor="#1688a5",alpha=.15))
        ax.annotate(f"冻结整理区 {base['lower']:.2f}—{base['upper']:.2f}\n宽度 {base['range_pct']:.2f}%，量比 {base['volume_vs_preceding_window']:.2f}",
                    ((start+end)/2,base["lower"]),xytext=(-225,-42 if name=="近岸蛋白" else -70),textcoords="offset points",
                    fontsize=9,color="#12657c",arrowprops=dict(arrowstyle="->",color="#12657c"),
                    bbox=dict(facecolor="white",edgecolor="none",alpha=.85,pad=2))
    if not outcome:
        # User scope: the most recent, locally steep descending line only.
        for line in base["lines"][-1:]:
            first = lookup.get(line["first_time"])
            second = lookup.get(line["second_time"])
            if first is None or second is None:
                continue
            xp = np.arange(first,len(f))
            yp = line["first_price"]+line["slope_per_bar"]*(xp-first)
            ax.plot(xp,yp,"-.",color="#ac7700",lw=1.4)
            ax.scatter([first,second],[line["first_price"],line["second_price"]],s=24,color="#a44a35")
            ax.annotate("较陡的局部下降线",(second,line["second_price"]),
                        xytext=(-135,24),textcoords="offset points",fontsize=9,color="#9b5934",
                        bbox=dict(facecolor="white",edgecolor="none",alpha=.85,pad=2))
        if entry["failed_opening_high"] is not None:
            opening_x = lookup.get(entry["decision_time"][:10]+" 09:45:00")
            ax.hlines(entry["failed_opening_high"],opening_x,len(f)-.3,color="#7b7b7b",ls=":")
            ax.annotate(f"早盘高点 {entry['failed_opening_high']:.2f}",
                        (opening_x,entry["failed_opening_high"]),xytext=(-90,25),textcoords="offset points",fontsize=9)
    elif signal_x is not None:
        for axis in (ax,vol):
            axis.axvspan(signal_x+.5,len(f)-.2,color="#b8bcc2",alpha=.16)
        if trace is not None:
            first = trace[trace["flags"].fillna("").ne("")].head(1)
            if len(first):
                row = first.iloc[0]; j=lookup.get(str(row.time))
                if j is not None:
                    ax.scatter([j],[row.close],s=90,marker="v",color="#174daf",zorder=8)
                    ax.annotate(f"首个退出证据\n{pd.Timestamp(row.time).strftime('%m/%d %H:%M')}",
                                (j,row.close),xytext=(18,35),textcoords="offset points",fontsize=10,
                                color="#174daf",arrowprops=dict(arrowstyle="->",color="#174daf"))
            aligned=trace[trace.short_order_run.ge(3)].head(1)
            if len(aligned):
                row=aligned.iloc[0];j=lookup.get(str(row.time))
                if j is not None:
                    ax.scatter([j],[row.close],s=35,color="#2c8654",zorder=8)
            # Draw separately frozen lines only over periods in which available.
            for _,g in trace[trace.support_line.notna()].groupby(["line_anchor1","line_anchor2"],sort=False):
                pts=[(lookup[str(r.time)],r.support_line) for r in g.itertuples() if str(r.time) in lookup]
                if len(pts)>1:ax.plot(*zip(*pts),color="#23875c",ls="--",lw=1.15)
    suffix = "事后检验；灰区为确认之后，仅验证退出证据" if outcome else "仅显示确认时点及以前；趋势线均已冻结"
    ax.set_title(f"{name}：15分钟量价结构｜{suffix}",fontsize=13)
    decorate(ax,vol,f,f.datetime.tolist(),intraday=True)
    return dict(price_volume_xlim_equal=bool(np.allclose(ax.get_xlim(),vol.get_xlim())),
                volume_bars=len(pos), positive_volume_bars=int((f.vol>0).sum()), data_end=str(f.datetime.max()),
                confirmation_marker_visible=signal_x is not None)


def run(args):
    out = Path(args.output)
    _configure_chinese_font()
    accept = {}
    all_facts = {}
    for code,case in CASES.items():
        q=out/code
        frozen=json.loads((q/"FROZEN_MANIFEST.json").read_text())
        assert all(hashlib.sha256((q/file).read_bytes()).hexdigest()==sha for file,sha in frozen["files"].items())
        base=json.loads((q/"FROZEN_BASE.json").read_text())
        entry=json.loads((q/"FROZEN_CONFIRMATION.json").read_text())
        f=pd.read_parquet(q/"fifteen_features_decision_prefix.parquet")
        d,w=metrics(code,case,out)
        actual=evaluate_at(f,base,entry["decision_time"])
        assert actual["confirmed"] and actual["close"] == entry["close"]
        assert d.trade_date.max() < pd.Timestamp(entry["decision_time"]).strftime("%Y%m%d")
        assert freeze_base(f,base["base_start"],base["base_end"]) == base
        # Evaluate all completed bars after the quiet base, not only the winner.
        obs=[evaluate_at(f,base,t) for t in f.datetime if t>pd.Timestamp(base["base_end"])]
        pd.DataFrame([{k:v for k,v in z.items() if k not in ("lines","consecutive_bulls")} for z in obs]).to_csv(q/"prefix_observations.csv",index=False,encoding="utf-8-sig")
        future=pd.read_parquet(q/"outcome_fifteen_features.parquet")
        checks=[evaluate_at(future,base,t) == evaluate_at(future[future.datetime.le(t)],base,t)
                for t in f.datetime if t>pd.Timestamp(base["base_end"])]
        assert all(checks)
        trace=monitor_path(future,entry,future.datetime.max())
        checkpoint=pd.Timestamp("2026-09-15 10:00" if code=="688137.SH" else "2025-12-26 11:30")
        prefix_trace=monitor_path(future[future.datetime.le(checkpoint)],entry,checkpoint)
        pd.testing.assert_frame_equal(trace[trace.time.le(str(checkpoint))].reset_index(drop=True),prefix_trace)
        trace.to_csv(q/"post_entry_evidence.csv",index=False,encoding="utf-8-sig")
        plot_entry=dict(entry)
        if code=="002413.SZ":
            plot_entry["annotation_offset"]=[-180,18]
        fig,axs=plt.subplots(4,1,figsize=(17,14),gridspec_kw=dict(height_ratios=[4,1,4,1],hspace=.55))
        accept[code]=dict(daily=draw_daily(axs[0],axs[1],d,w,case["name"]),
                          minute=draw_intraday(axs[2],axs[3],f,base,plot_entry,case["name"]))
        fig.suptitle(f"{case['name']} {code}｜日线回调背景 + 15分钟启动确认｜开发样本，不是收益回测",fontsize=16,y=.975)
        fig.subplots_adjust(top=.925,bottom=.055,left=.06,right=.985)
        fig.savefig(q/"paired_decision.jpg",dpi=145);plt.close(fig)
        fig,axs=plt.subplots(2,1,figsize=(17,8),gridspec_kw=dict(height_ratios=[4,1],hspace=.25))
        accept[code]["outcome"]=draw_intraday(axs[0],axs[1],future,base,entry,case["name"],True,trace)
        fig.subplots_adjust(top=.9,bottom=.08,left=.06,right=.985)
        fig.savefig(q/"post_entry_review.jpg",dpi=145);plt.close(fig)
        first=trace[trace["flags"].ne("")].head(1)
        facts=dict(daily=w,base=base,entry=entry,
                   first_exit_evidence=first.astype(object).where(pd.notna(first),None).to_dict("records"),
                   outcome_cutoff=str(future.datetime.max()),
                   price_prefix_checks=len(checks),price_prefix_checks_passed=sum(checks))
        write_json(q/"case_facts.json",facts)
        accept[code]["prefix_checks"]=dict(passed=sum(checks),total=len(checks))
        accept[code]["monitor_prefix_check"]=dict(time=str(checkpoint),passed=True)
        all_facts[code]=facts
    write_json(out/"CHART_ACCEPTANCE.json",accept)
    write_json(out/"CASE_FACTS.json",all_facts)
    print(json.dumps(accept,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",default=str(ROOT/"outputs/pullback_price_confirmation_cases_v1"))
    run(p.parse_args())
