"""Audit the supplied Heda development template, preserving v1 non-signals.

The relative-size diagnostic explains a visual pattern; it does not replace
the frozen absolute-body entry rules or claim a completed trade/backtest.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[5]
sys.path.insert(0,str(ROOT))
from plot.plot_daily_kline import _configure_chinese_font
from study_daily_intraday_pullback import prepare_daily
from review_ma_pullback_template import wave_metrics
from review_pullback_price_confirmation import draw_daily,draw_intraday,write_json
from pullback_price_confirmation import freeze_base,evaluate_at,pullback_depth_score,phase_volume_profile

CASE=dict(code="002810.SZ",name="山东赫达",launch="20251222",prior="20260115",
          quiet_start="2026-01-16 10:45",quiet_end="2026-01-16 11:30",
          observation="2026-01-16 14:15")


def analyze(out):
    out=Path(out)
    raw=pd.read_parquet(out/"daily_raw.parquet")
    adj=pd.read_parquet(out/"adj.parquet")
    cal=pd.read_parquet(out/"calendar.parquet")
    raw=raw[raw.trade_date.astype(str).le(CASE["prior"])]
    adj=adj[adj.trade_date.astype(str).le(CASE["prior"])]
    cal=cal[cal.cal_date.astype(str).le(CASE["prior"])]
    d=prepare_daily(raw,adj,sorted(cal.cal_date.astype(str).unique()))
    w=wave_metrics(d,CASE["launch"],CASE["prior"])
    peak_index=d.index[d.trade_date.eq(w["peak_date"])][0]
    correction=d.iloc[peak_index+1:]
    last=d.iloc[-1];factor=float(last.adj_factor)
    launch_index=d.index[d.trade_date.eq(CASE["launch"])][0]
    w.update(last_date=str(last.trade_date),
             max_correction_drawdown_pct=float(100*(1-correction.low.min()/d.iloc[peak_index].high)),
             current_low_drawdown_pct=float(100*(1-last.low/d.iloc[peak_index].high)),
             atr14_max_during_correction=float(correction.atr14_pct.max()),
             previous_63_session_high=float(d.iloc[launch_index-63:launch_index].high.max()/factor),
             ma_values={f"ma{n}":float(last[f"ma{n}"]/factor) for n in (5,10,20,30,60,120)},
             ma_rising_runs={f"ma{n}":int(last[f"rise{n}"]) for n in (5,10,20,30,60,120)})
    w["pullback_depth_score"]=pullback_depth_score(w["max_correction_drawdown_pct"])
    w["depth_hard_gate_applied"]=False
    w["phase_volume_profile"]=phase_volume_profile(d,CASE["launch"],CASE["prior"])
    full=pd.read_parquet(out/"fifteen_features.parquet")
    stamp=pd.Timestamp(CASE["observation"])
    prefix=full[full.datetime.le(stamp)].copy()
    base=freeze_base(prefix,CASE["quiet_start"],CASE["quiet_end"])
    e=evaluate_at(prefix,base,stamp)
    b=prefix.iloc[-1];prior8=prefix.iloc[-9:-1]
    median_body=float((prior8.close-prior8.open).abs().median())
    median_range=float((prior8.high-prior8.low).median())
    e.update(chart_label="量价启动观察",base_upper=base["upper"],signal_factor=float(b.adj_factor),
             price_scale=float(b.close/b.p_close),manual_template_match=True,
             assessment="relative-size local launch; v1 absolute-body rules remain unconfirmed",
             body_raw=float(b.close-b.open),previous8_median_body=median_body,
             body_multiple_previous8=float((b.close-b.open)/median_body) if median_body>0 else None,
             range_multiple_previous8=float((b.high-b.low)/median_range) if median_range>0 else None)
    # A recent envelope edge can be provisional even if it is not a confirmed
    # swing high. Never label it as a confirmed two-pivot trendline.
    constraint=dict(first_time="2026-01-16 10:00:00",first_price=15.04,
                    second_time="2026-01-16 11:30:00",second_price=14.84,
                    frozen_at=CASE["quiet_end"],status="provisional_high_envelope_constraint")
    for t in (constraint["first_time"],constraint["second_time"]):
        assert float(prefix.loc[prefix.datetime.eq(t),"high"].iloc[0]) == constraint[
            "first_price" if t==constraint["first_time"] else "second_price"]
    checks=[]
    for t in ("2026-01-16 13:15","2026-01-16 13:30","2026-01-16 14:00",CASE["observation"]):
        checks.append(evaluate_at(full,base,t)==evaluate_at(full[full.datetime.le(t)],base,t))
    assert all(checks)
    inputs=out/"decision_inputs";inputs.mkdir(exist_ok=True)
    for name,value in (("daily",raw),("adj",adj),("calendar",cal),("fifteen_features",prefix)):
        value.to_parquet(inputs/(name+".parquet"),index=False)
    write_json(out/"FROZEN_BASE.json",base)
    write_json(out/"FROZEN_OBSERVATION.json",e)
    write_json(out/"PROVISIONAL_CONSTRAINT.json",constraint)
    write_json(out/"case_facts.json",dict(daily=w,base=base,observation=e,constraint=constraint,
                                         known_development_case=True,prefix_checks_passed=sum(checks)))
    write_json(out/"FROZEN_MANIFEST.json",dict(
        observation_time=str(stamp),daily_last=CASE["prior"],known_development_case=True,
        manual_quiet_interval=True,not_a_blind_backtest=True,
        files={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in list(inputs.glob("*.parquet"))+[out/"FROZEN_BASE.json",out/"FROZEN_OBSERVATION.json",out/"PROVISIONAL_CONSTRAINT.json"]}))
    afternoon=full[full.datetime.dt.strftime("%Y%m%d").eq("20260116")].copy()
    afternoon[["datetime","open","high","low","close","vol","body_pct",
               "volume_multiple8","volume_multiple_same_slot5"]].to_csv(out/"intraday_20260116.csv",index=False,encoding="utf-8-sig")
    _configure_chinese_font()
    plot_base=deepcopy(base);plot_base["lines"]=[]
    def constraint_plot(ax,view):
        first_day=view.tail(80).datetime.iloc[0].normalize()
        shown=view[view.datetime.ge(first_day)].reset_index(drop=True)
        lookup={str(t):i for i,t in enumerate(shown.datetime)}
        a,z=lookup[constraint["first_time"]],lookup[constraint["second_time"]]
        slope=(constraint["second_price"]-constraint["first_price"])/(z-a)
        positions=np.arange(a,len(shown))
        prices=constraint["first_price"]+slope*(positions-a)
        ax.plot(positions,prices,"--",color="#ae7337",lw=1.3)
        ax.scatter([a,z],[constraint["first_price"],constraint["second_price"]],s=24,color="#ae7337")
        ax.annotate("较陡的下降约束线（临时）",(z,constraint["second_price"]),
                    xytext=(-180,-33),textcoords="offset points",fontsize=9,color="#925a27",
                    bbox=dict(facecolor="white",edgecolor="none",alpha=.9,pad=2))
    fig,axes=plt.subplots(4,1,figsize=(17,14),gridspec_kw=dict(height_ratios=[4,1,4,1],hspace=.55))
    daily_accept=draw_daily(axes[0],axes[1],d,w,CASE["name"])
    minute_accept=draw_intraday(axes[2],axes[3],prefix,plot_base,e,CASE["name"])
    constraint_plot(axes[2],prefix)
    axes[2].set_title("山东赫达：15分钟局部量价启动观察｜截至1月16日14:15；临时下降线 + 冻结平台",fontsize=13)
    fig.suptitle("山东赫达002810.SZ｜第三个形态模板核对｜量价研判与旧机械规则分开记录",fontsize=16,y=.975)
    fig.subplots_adjust(top=.925,bottom=.055,left=.06,right=.985)
    fig.savefig(out/"paired_prefix.jpg",dpi=145);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(17,8),gridspec_kw=dict(height_ratios=[4,1],hspace=.25))
    detail_entry=deepcopy(e);detail_entry["annotation_offset"]=[-145,-110]
    detail_accept=draw_intraday(axes[0],axes[1],afternoon,plot_base,detail_entry,CASE["name"],outcome=True)
    constraint_plot(axes[0],afternoon)
    axes[0].set_title("山东赫达：2026年1月16日15分钟放大｜时间为K线结束时刻；灰区为14:15之后",fontsize=13)
    lookup={str(t):i for i,t in enumerate(afternoon.datetime)}
    x=lookup["2026-01-16 14:30:00"]
    axes[0].annotate("14:30：小阴，收15.00",(x,15.),xytext=(-165,42),textcoords="offset points",fontsize=10,
                     arrowprops=dict(arrowstyle="->",color="#666"),bbox=dict(facecolor="white",edgecolor="none",alpha=.9))
    axes[1].set_xticks(np.arange(16),afternoon.datetime.dt.strftime("%H:%M"),rotation=35)
    fig.subplots_adjust(top=.9,bottom=.13,left=.06,right=.985)
    fig.savefig(out/"afternoon_detail.jpg",dpi=145);plt.close(fig)
    write_json(out/"CHART_ACCEPTANCE.json",dict(daily=daily_accept,minute=minute_accept,detail=detail_accept,
                                              prefix_checks_passed=sum(checks),timestamp_ohlc_verified=True))
    print(json.dumps(dict(daily=w,observation=e,chart_acceptance=dict(daily=daily_accept,minute=minute_accept,detail=detail_accept)),ensure_ascii=False,indent=2))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",default=str(ROOT/"outputs/shandong_heda_pullback_template_20260116_v1"))
    analyze(p.parse_args().output)
