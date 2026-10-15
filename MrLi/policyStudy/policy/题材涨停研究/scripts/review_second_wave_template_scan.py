"""Review frozen second-wave signals and separate decision/outcome charts.

Reads saved provider inputs only. Never changes entry rules or candidate lists.
The 25/30% lines illustrate observed space, not executable take-profit orders.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[5]
sys.path[:0]=[str(ROOT),str(ROOT/"offlineDataManager/scripts")]
from plot.plot_daily_kline import MA_COLORS,_configure_chinese_font,_draw_candles
from study_daily_intraday_pullback import prepare_daily
from study_fifteen_min_ma_coil import features,Rules as CoilRules
from review_ma_pullback_template import wave_metrics


def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False),encoding="utf-8")


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def number(value):
    return "—" if value is None or pd.isna(value) else f"{float(value):.2f}%"


def cohorts(row):
    theme=row["theme_last5_limitup_stock_count"]>=3 and row["theme_prevday_limitup_count"]>=1
    ma=row["intraday_short_order"] and row["intraday_short_all_rising"] and row["intraday_ma60_rise"]>0 and row["intraday_ma120_rise"]>0
    return dict(theme_active=bool(theme),ma_confirmed=bool(ma),theme_and_ma=bool(theme and ma))


def panel(ax,vol,f,*,intraday=False,scale=1.):
    f=f.copy().reset_index(drop=True)
    if intraday:
        for c in ("open","high","low","close"):f[c]=f[f"p_{c}"]*scale
        values=f.vol/10000; stamps=pd.to_datetime(f.datetime)
    else:
        for c in ("open","high","low","close"):f[c]*=scale
        values=f.raw_vol/10000; stamps=pd.to_datetime(f.trade_date)
    colors=_draw_candles(ax,f);positions=np.arange(len(f))
    for n in (5,10,20,30,60,120):
        ax.plot(positions,f[f"ma{n}"]*scale,color=MA_COLORS[n],lw=1.15,label=f"MA{n}")
    vol.bar(positions,values,color=colors,width=.68)
    ax.grid(alpha=.16);vol.grid(axis="y",alpha=.16)
    ax.set_xlim(-.8,len(f)-.2);vol.set_xlim(ax.get_xlim())
    ax.tick_params(axis="x",labelbottom=False)
    if intraday:
        ticks=[i for i,t in enumerate(stamps) if i==0 or t.date()!=stamps.iloc[i-1].date()]
        step=max(1,int(np.ceil(len(ticks)/12)));ticks=ticks[::step]
    else:ticks=np.linspace(0,len(f)-1,min(9,len(f)),dtype=int).tolist()
    vol.set_xticks(ticks,[stamps.iloc[i].strftime("%m/%d") for i in ticks])
    ax.set_ylabel("价格（元）");vol.set_ylabel("量（万股）" if intraday else "量（万手）")
    ax.legend(loc="upper left",ncol=6,fontsize=8)
    return f,dict(volume_bars=len(values),positive_volume_bars=int(values.gt(0).sum()),
        aligned_price_volume_axes=bool(np.allclose(ax.get_xlim(),vol.get_xlim())),data_end=str(stamps.max()))


def decision_chart(q,s,d,f):
    stamp=pd.Timestamp(s["signal_time"]);d=d[d.trade_date.le(s["context_date"])].copy()
    f=f[f.datetime.le(stamp)].copy()
    assert d.trade_date.max()<stamp.strftime("%Y%m%d") and f.datetime.max()==stamp
    w=wave_metrics(d,s["wave_start_date"],s["context_date"])
    daily=d.tail(120).copy();minute=f[f.datetime.ge(f.tail(160).datetime.iloc[0].normalize())].copy()
    scale=float(f.iloc[-1].close/f.iloc[-1].p_close)
    fig,axes=plt.subplots(4,1,figsize=(17,14),gridspec_kw=dict(height_ratios=[4,1,4,1],hspace=.45))
    dv,a=panel(axes[0],axes[1],daily,scale=1/float(d.adj_factor.iloc[-1]))
    lookup={str(day):i for i,day in enumerate(dv.trade_date)}
    first,peak=lookup[w["launch_date"]],lookup[w["peak_date"]]
    for axis in axes[:2]:
        axis.axvspan(first-.5,peak+.5,color="#e6ac42",alpha=.13)
        axis.axvspan(peak+.5,len(dv)-.3,color="#4a9fce",alpha=.10)
    axes[0].annotate("首波启动",(first,dv.close.iloc[first]),xytext=(-35,-30),textcoords="offset points",fontsize=9,arrowprops=dict(arrowstyle="->"))
    axes[0].annotate("当时已知高点",(peak,dv.high.iloc[peak]),xytext=(-60,22),textcoords="offset points",fontsize=9,arrowprops=dict(arrowstyle="->"))
    axes[1].axhline(s["baseline_volume"]/10000,color="#555",ls="--",lw=1.2,label="启动前20日均量")
    axes[1].plot(np.arange(len(dv)),dv.raw_vol.rolling(5).mean()/10000,color="#cc7800",lw=1.2,label="5日均量")
    axes[1].legend(loc="upper left",ncol=2,fontsize=8)
    axes[0].set_title(f"日线仅到前一交易日 {s['context_date']}｜首波量 {s['advance_volume_multiple']:.2f}倍；回调量 {s['correction_volume_multiple']:.2f}倍；日线粘合 {s['daily_coil_pct']:.2f}%",fontsize=11)
    mv,b=panel(axes[2],axes[3],minute,intraday=True,scale=scale)
    times={str(t):i for i,t in enumerate(mv.datetime)};base=s["base"]
    left,right=times[base["base_start"]],times[base["base_end"]];j=times[s["signal_time"]]
    lower,upper=base["lower"],base["upper"]
    axes[2].add_patch(Rectangle((left-.5,lower),j-left+1,upper-lower,facecolor="#42a2c1",edgecolor="#1688a5",alpha=.2))
    axes[2].hlines(lower,left,len(mv),ls="--",lw=1,color="#1486a0",label="冻结平台下沿")
    axes[2].scatter([j],[s["signal_close"]],s=95,marker="^",color="#951aad",zorder=8)
    axes[2].annotate(f"放量突破 {stamp.strftime('%m/%d %H:%M')}\n收{s['signal_close']:.2f}；不是成交价",(j,s["signal_close"]),xytext=(-200,40),textcoords="offset points",fontsize=9,arrowprops=dict(arrowstyle="->",color="#951aad"))
    axes[2].annotate(f"整理区 {lower:.2f}—{upper:.2f}\n宽{base['range_pct']:.2f}%；缩量比{base['volume_vs_preceding_window']:.2f}",((left+right)/2,lower),xytext=(-140,-45),textcoords="offset points",fontsize=9,arrowprops=dict(arrowstyle="->",color="#1688a5"),bbox=dict(facecolor="white",edgecolor="none",alpha=.85))
    axes[2].set_title(f"15分钟仅到信号时刻｜实体{s['body_pct']:.2f}%；相对整理均量{s['volume_vs_quiet']:.2f}倍；同钟量比{s['same_clock_volume_multiple']:.2f}倍",fontsize=11)
    fig.suptitle(f"{s['name']} {s['ts_code']}｜{s['prior_theme_name']}｜决策视图：不含后续走势",fontsize=16,y=.975)
    fig.subplots_adjust(top=.925,bottom=.05,left=.065,right=.985);fig.savefig(q/"decision.jpg",dpi=115);plt.close(fig)
    return dict(daily=a,minute=b,signal_marker_visible=j==len(mv)-1,base_marker_visible=left<=right<j)


def outcome_chart(q,s,result,f,calendar):
    stamp=pd.Timestamp(s["signal_time"]);end=result.get("observed_through")
    if end is None:
        day=stamp.strftime("%Y%m%d");index=calendar.index(day);end=calendar[min(index+19,len(calendar)-1)]
    first=f[f.datetime.le(stamp)].tail(48).datetime.iloc[0].normalize()
    shown=f[f.datetime.ge(first)&f.datetime.dt.strftime("%Y%m%d").le(end)].copy()
    signal=f[f.datetime.eq(stamp)].iloc[0];scale=float(signal.close/signal.p_close)
    entry_stamp=pd.Timestamp(result["entry_time"]) if result.get("entry_time") else None
    if entry_stamp is not None:
        entry=f[f.datetime.eq(entry_stamp)].iloc[0];scale=float(entry.close/entry.p_close)
    fig,axes=plt.subplots(2,1,figsize=(17,8),gridspec_kw=dict(height_ratios=[4,1],hspace=.2))
    view,qa=panel(axes[0],axes[1],shown,intraday=True,scale=scale)
    times={str(t):i for i,t in enumerate(view.datetime)};j=times[s["signal_time"]]
    for a in axes:a.axvspan(j+.5,len(view),color="#babfc5",alpha=.18)
    axes[0].axvline(j,color="#951aad",ls=":",lw=1.2)
    floor=s["base"]["lower"]*float(signal.p_close/signal.close)*scale
    axes[0].hlines(floor,j,len(view),ls="--",color="#1688a5",lw=1.3,label="冻结平台下沿")
    if result.get("evaluable"):
        fill=result["entry_price"];ex=times[result["entry_time"]]
        axes[0].scatter([ex],[fill],marker="^",s=90,color="#8a198f",zorder=8)
        axes[0].annotate(f"次根开盘代理 {fill:.2f}",(ex,fill),xytext=(15,-45),textcoords="offset points",fontsize=9,arrowprops=dict(arrowstyle="->",color="#8a198f"))
        for target,color in ((25,"#cc7800"),(30,"#be5625")):
            axes[0].hlines(fill*(1+target/100),ex,len(view),ls=":",lw=1.1,color=color)
            axes[0].text(len(view)-1,fill*(1+target/100),f"+{target}%",color=color,fontsize=9,ha="right",va="bottom")
        failure=result.get("first_floor_failure")
        if failure and failure in times:
            k=times[failure];price=view.close.iloc[k]
            axes[0].scatter([k],[price],marker="v",s=85,color="#174daf",zorder=9)
            axes[0].annotate("首次收盘失效",(k,price),xytext=(12,-40),textcoords="offset points",fontsize=9,arrowprops=dict(arrowstyle="->",color="#174daf"))
        text=f"20日最高空间 {number(result.get('mfe_20d_pct'))}；全窗口最低变化 {number(result.get('mae_20d_pct'))}；先失效前到25%：{'是' if result.get('intact_25_20d') else '否'}"
    else:text=f"未计作成交路径：{result['execution_status']}；保留在36个信号分母"
    axes[0].set_title(text,fontsize=12)
    axes[0].legend(loc="upper left",ncol=7,fontsize=8)
    fig.suptitle(f"{s['name']} {s['ts_code']}｜事后检验：灰区为未来，空间不是已实现收益",fontsize=15,y=.975)
    fig.subplots_adjust(top=.90,bottom=.07,left=.065,right=.985);fig.savefig(q/"outcome.jpg",dpi=115);plt.close(fig)
    return qa


def group_summary(results):
    groups={};all_rows=[{**r,**cohorts(r)} for r in results]
    masks={"all":lambda r:True,"non_core":lambda r:not r["known_core_development_case"],
        "theme_active":lambda r:r["theme_active"],"ma_confirmed":lambda r:r["ma_confirmed"],
        "theme_and_ma":lambda r:r["theme_and_ma"],"theme_and_ma_non_core":lambda r:r["theme_and_ma"] and not r["known_core_development_case"]}
    for name,match in masks.items():
        rows=[r for r in all_rows if match(r)];valid=[r for r in rows if r["evaluable"]];n=len(valid)
        groups[name]=dict(signals=len(rows),evaluable=n,statuses=dict(Counter(r["execution_status"] for r in rows)),
            counts={f"{kind}_{target}_{h}d":sum(r[f"{kind}_{target}_{h}d"] for r in valid) for h in (10,20) for target in (25,30) for kind in ("reached","intact")},
            sustained25=sum(r["sustained_intact_25_20d"] for r in valid))
    return groups


def run(out):
    out=Path(out);inputs=out/"inputs";charts=out/"charts";charts.mkdir(exist_ok=True)
    for manifest_name in ("FROZEN_SIGNAL_MANIFEST.json","COHORT_MANIFEST.json"):
        manifest=json.loads((out/manifest_name).read_text());assert all(digest(out/file)==value for file,value in manifest["files"].items())
    _configure_chinese_font();signals=json.loads((out/"signals_before_outcomes.json").read_text());results=json.loads((out/"outcomes.json").read_text())
    byresult={(r["ts_code"],r["signal_time"]):r for r in results}
    raw=pd.read_parquet(inputs/"daily_raw.parquet");adj=pd.read_parquet(inputs/"daily_adj.parquet")
    calendar=sorted(pd.read_parquet(inputs/"calendar.parquet").cal_date.astype(str).unique())
    ocalendar=sorted(pd.read_parquet(inputs/"outcome_calendar.parquet").cal_date.astype(str).unique())
    bars=pd.read_parquet(inputs/"fifteen_signal.parquet");future=pd.read_parquet(inputs/"outcome_fifteen.parquet")
    rg={c:g for c,g in raw.groupby("ts_code")};ag={c:g for c,g in adj.groupby("ts_code")};bg={c:g for c,g in bars.groupby("ts_code")};fg={c:g for c,g in future.groupby("ts_code")}
    accept={};cards=[];records=[]
    for i,s in enumerate(signals,1):
        key=f"{s['ts_code']}_{pd.Timestamp(s['signal_time']).strftime('%Y%m%d_%H%M')}";q=charts/key;q.mkdir(exist_ok=True)
        prior=s["context_date"];dc=[day for day in calendar if day<=prior]
        d=prepare_daily(rg[s['ts_code']][rg[s['ts_code']].trade_date.le(prior)],ag[s['ts_code']][ag[s['ts_code']].trade_date.le(prior)],dc)
        stamp=pd.Timestamp(s["signal_time"]);mc=[day for day in calendar if "20251103"<=day<=stamp.strftime("%Y%m%d")]
        prefix=features(bg[s["ts_code"]][pd.to_datetime(bg[s["ts_code"]].datetime).le(stamp)],mc,CoilRules())
        result=byresult[(s["ts_code"],s["signal_time"])];after=features(fg[s['ts_code']],ocalendar,CoilRules())
        accept[key]=dict(decision=decision_chart(q,s,d,prefix),outcome=outcome_chart(q,s,result,after,ocalendar))
        tags=cohorts(s);new=dict(result,**tags,chart_key=key,decision_image=str(q/"decision.jpg"),outcome_image=str(q/"outcome.jpg"))
        records.append(new)
        label="题材+均线确认" if tags["theme_and_ma"] else "全部技术候选对照"
        status="先未失效且有25%空间" if result.get("intact_25_20d") else ("先失效后才有25%空间" if result.get("reached_25_20d") else "未满足25%目标")
        if not result["evaluable"]:status="下一根一价K线，保守视为未成交"
        cards.append(f'<article data-group="{1 if tags["theme_and_ma"] else 0}"><h2>{i:02d} {html.escape(s["name"])} {s["ts_code"]} · {s["signal_time"]}</h2><p>{html.escape(s["prior_theme_name"])} | {label} | {status} | 20日空间 {number(result.get("mfe_20d_pct"))}</p><img loading="lazy" src="charts/{key}/decision.jpg"><details><summary>展开事后路径（含未来）</summary><img loading="lazy" src="charts/{key}/outcome.jpg"></details></article>')
        print(f"charts {i}/{len(signals)} {key}",flush=True)
    groups=group_summary(results);dump(out/"COHORT_RESULTS.json",groups);dump(out/"CHART_ACCEPTANCE.json",accept)
    dump(out/"review_records.json",records)
    html_text='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>第一波回调后二启：1—3月案例</title><style>body{background:#f3f5f8;color:#192432;font:16px system-ui;max-width:1500px;margin:30px auto;padding:0 20px}article{background:white;padding:18px;margin:24px 0;border-radius:12px}img{width:100%;height:auto}summary{cursor:pointer;color:#175891;padding:12px}button{padding:10px;margin-right:10px}h2{font-size:20px}p{line-height:1.6}</style><h1>2026年1—3月：首波回调后二次启动模板</h1><p>开发研究。全部36个信号保留；其中山东赫达是事先给定的核心案例。日线只到前一收盘，15分钟只到启动时刻；未来视图单独折叠。25%—30%表示后续可观察空间，不是策略收益。只有7个符合看收益前冻结的题材+均线分组，剔除赫达后6个。</p><button onclick="document.querySelectorAll('article').forEach(x=>x.hidden=false)">看全部36个</button><button onclick="document.querySelectorAll('article').forEach(x=>x.hidden=x.dataset.group!=='1')">只看题材+均线7个</button>'''+"\n".join(cards)+"</html>"
    (out/"index.html").write_text(html_text,encoding="utf-8")
    assert all(a["decision"]["signal_marker_visible"] and a["decision"]["base_marker_visible"] and a["decision"]["daily"]["positive_volume_bars"]>0 and a["decision"]["minute"]["positive_volume_bars"]>0 and a["outcome"]["positive_volume_bars"]>0 for a in accept.values())
    dump(out/"REVIEW_MANIFEST.json",dict(charts=len(accept)*2,chart_cases=len(accept),frozen_signals_digest=digest(out/"signals_before_outcomes.json"),
        images={str(p.relative_to(out)):digest(p) for p in charts.glob("*/*.jpg")},chart_assertions_passed=True))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",default=str(ROOT/"outputs/second_wave_templates_202601_202603_v4"));run(p.parse_args().output)
