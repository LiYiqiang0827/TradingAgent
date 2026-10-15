"""Four within-theme hindsight contrasts; no case-based accuracy estimate."""
from __future__ import annotations

import json
from pathlib import Path
import pandas as pd
import numpy as np

from study_divergence import BASE, SOURCE, OUT, dp, finite_ratio
from build_case_dossiers import normalize, save, news_candidates

CASES = [("20260515","半导体"),("20260521","半导体"),("20260617","机器人"),("20260624","机器人")]


def first_sustained(series, length=3):
    """First completed run, with no overnight/lunch gaps hidden in the run."""
    count = 0; previous = None
    for time,value in series.items():
        if previous is None or time-previous != pd.Timedelta(minutes=1):count=0
        count = count+1 if pd.notna(value) and bool(value) else 0
        if count>=length:return time.strftime("%H:%M:%S")
        previous=time
    return None


def cached(name,method,**kwargs):
    path=OUT/"cache"/(name+".pkl")
    if path.exists():return pd.read_pickle(path)
    frame=getattr(dp,method)(source="database_only",**kwargs)
    frame.to_pickle(path)
    return frame


def main():
    details=json.loads((OUT/"event_details.json").read_text())
    daily=normalize(pd.read_pickle(SOURCE/"cache/day.pkl")).set_index(["trade_date","ts_code"])
    kpl=normalize(pd.read_pickle(SOURCE/"cache/kpl.pkl"))
    manifest=[]
    for date,theme in CASES:
        detail=next(x for x in details if x["summary"]["date"]==date and x["summary"]["theme"]==theme)
        row=detail["summary"];folder=OUT/(date+"_"+theme);folder.mkdir(exist_ok=True)
        p,d,t=row["p_date"],date,row["t_date"]
        pool=detail["pool"];cores=[r["ts_code"] for r in detail["cores"]]
        minute=pd.read_pickle(OUT/"cache"/("minute_"+t+".pkl"))
        minute=minute.loc[minute.ts_code.isin(pool)&minute.datetime.str.startswith(pd.Timestamp(t).strftime("%Y-%m-%d"))]
        prices=minute.pivot(index="datetime",columns="ts_code",values="price").reindex(columns=pool)
        prices.index=pd.to_datetime(prices.index)
        day_d=daily.loc[d].reindex(pool);day_t=daily.loc[t].reindex(pool)
        ratios=prices.divide(day_t.pre_close,axis=1)
        ratios=ratios.where((prices>0)&np.isfinite(ratios))
        returns=(ratios-1)*100
        returns=returns.mask(returns.abs()<1e-8,0.0)
        since_p=ratios.multiply(finite_ratio(day_d.close,day_d.pre_close),axis=1).subtract(1)*100
        since_p=since_p.mask(since_p.abs()<1e-8,0.0)
        matrix=pd.DataFrame({"covered":returns.notna().sum(axis=1),"positive":(returns>0).sum(axis=1),
                             "pool_median_pct":returns.median(axis=1),"since_p_median_pct":since_p.median(axis=1)})
        matrix["core_median_pct"]=returns[cores].median(axis=1) if cores else np.nan
        matrix["core_covered"]=returns[cores].notna().sum(axis=1) if cores else 0
        group=(matrix.covered==len(pool))&(matrix.positive>len(pool)/2)
        core=(matrix.core_covered==len(cores))&(matrix.core_median_pct>0) if cores else pd.Series(False,index=matrix.index)
        detail["first_three_minute_run"]={"interpretation":"连续3个完整一分钟报价后确认；只描述先后，不证明带动因果或可交易性",
                                          "core_positive":first_sustained(core),"pool_majority_positive":first_sustained(group),
                                          "core_and_group":first_sustained(core&group),
                                          "pool_back_to_p":first_sustained((matrix.covered==len(pool))&(matrix.since_p_median_pct>=0))}
        losers=[r for r in detail["stock_outcomes"] if r["d_pct"] is not None and r["d_pct"]<0]
        detail["supplemental_d_losers"]={"note":"补充描述：D收盘已知下跌成员；不修改冻结的原群体修复标签", "n":len(losers),
                                          "t_restored":sum(r["t_since_p_pct"] is not None and r["t_since_p_pct"]>=0 for r in losers),
                                          "u_restored":sum(r["u_since_p_pct"] is not None and r["u_since_p_pct"]>=0 for r in losers)}
        matrix.to_csv(folder/"fixed_pool_minute_path.csv",encoding="utf-8-sig")
        start=pd.Timestamp(detail["timeline"][0]["date"]).strftime("%Y-%m-%d")+" 00:00:00"
        end=pd.Timestamp(t).strftime("%Y-%m-%d")+" 09:30:00"
        news=cached("news_divergence_"+date,"get_news",start_datetime=start,end_datetime=end,limit=None)
        names=[r["name"] for r in detail["stock_outcomes"]]
        extra=["长鑫","DRAM","液冷"] if theme=="半导体" else []
        candidates=news_candidates(news,theme,names,extra_keywords=extra)
        d_end=pd.Timestamp(d).strftime("%Y-%m-%d")+" 18:00:00"
        t_6=pd.Timestamp(t).strftime("%Y-%m-%d")+" 06:00:00"
        for r in candidates:r["time_bucket"]="截至D18:00" if r["datetime"]<=d_end else "D18:00至T06:00" if r["datetime"]<=t_6 else "T06:00—09:30"
        save(folder/"news_candidates.json",candidates)
        detail["news"]={"start":start,"end":end,"raw_count":len(news),"candidate_count":len(candidates),
                        "extra_retrieval_keywords":extra,
                        "source_counts":news.groupby("src").size().to_dict(),
                        "coverage_by_day":news.assign(date=news.datetime.str[:10]).groupby(["date","src"]).size().reset_index(name="n").to_dict("records")}
        prior_kpl=kpl.loc[(kpl.trade_date==p)&kpl.ts_code.isin(pool)&(kpl.tag=="涨停")]
        save(folder/"prior_kpl.json",prior_kpl.to_dict("records"))
        detail["core_kpl_history"]=kpl.loc[kpl.trade_date.isin([p,d,t,row["u_date"]])&kpl.ts_code.isin(cores),
                                                   ["trade_date","ts_code","name","tag","status","lu_desc","theme","lu_time","last_time"]].to_dict("records")
        lp=cached("lp_divergence_"+p,"get_kpl_limit_performance",trade_date=p)
        save(folder/"prior_reasons.json",lp.loc[lp.ts_code.isin(pool)].to_dict("records"))
        detail["selection"]="事后选定两组同题材近时段对照：5月半导体展示宽度/原群体修复方向不一致，6月机器人展示7股同规模群体的恢复与失败；未控制全部混杂因素"
        save(folder/"evidence_pack.json",detail)
        manifest.append({"date":d,"theme":theme,"pool":len(pool),"news":len(news),"first_three_minute_run":detail["first_three_minute_run"]})
        print(date,theme,len(news),detail["first_three_minute_run"],flush=True)
    save(OUT/"case_manifest.json",manifest)


if __name__=="__main__":main()
