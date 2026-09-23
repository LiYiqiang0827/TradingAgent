"""Build bounded, as-of evidence inventories for four retrospective cases.

No future prices or outcomes enter these packets. The researcher has previously
seen this historical period, so this is reconstruction, not independent blindness.
"""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import pandas as pd

from build_case_dossiers import BASE, SOURCE, normalize, save, dp

OUT=BASE/"data/attribution_v1"
CASES=[("20260514","20260515","半导体"),("20260520","20260521","半导体"),
       ("20260616","20260617","机器人"),("20260623","20260624","机器人")]


def cached(name,method,**kwargs):
    path=OUT/"cache"/(name+".pkl")
    if path.exists():return pd.read_pickle(path)
    frame=getattr(dp,method)(source="database_only",**kwargs)
    frame.to_pickle(path)
    save(path.with_suffix(".json"),{"method":method,"source":"database_only","kwargs":kwargs,"rows":len(frame)})
    return frame


def asof_news(frame,start,cutoff):
    stamp=pd.to_datetime(frame.datetime,format="mixed")
    return frame.loc[stamp.between(pd.Timestamp(start),pd.Timestamp(cutoff))].copy()


def main():
    (OUT/"cache").mkdir(parents=True,exist_ok=True)
    features=json.loads((SOURCE/"features.json").read_text())
    kpl=normalize(pd.read_pickle(SOURCE/"cache/kpl.pkl"))
    manifest=[]
    for p,d,theme in CASES:
        folder=OUT/(p+"_"+theme);folder.mkdir(exist_ok=True)
        cutoff=pd.Timestamp(d).strftime("%Y-%m-%d")+" 06:00:00"
        start=(pd.Timestamp(p)-timedelta(days=14)).strftime("%Y-%m-%d")+" 00:00:00"
        facts=next(r for r in features if r["date"]==p)
        own=[r for r in facts["leaders"] if r["theme"]==theme]
        pool=[r["ts_code"] for r in own]
        news=asof_news(cached("news_"+p,"get_news",start_datetime=start,end_datetime=cutoff,limit=None),start,cutoff)
        concepts=cached("concept_"+p,"get_kpl_concept_cons",trade_date=p)
        lp=cached("performance_"+p,"get_kpl_limit_performance",trade_date=p)
        records=[]
        for leader in own:
            code=leader["ts_code"]; name=leader["name"]
            raw=kpl.loc[kpl.trade_date.eq(p)&kpl.ts_code.eq(code)&kpl.tag.eq("涨停")].to_dict("records")
            linked=concepts.loc[concepts.con_code.eq(code)].to_dict("records") if len(concepts) else []
            company=news.loc[news.title.fillna("").str.contains(name,regex=False)|news.content.fillna("").str.contains(name,regex=False)]
            records.append({"ts_code":code,"name":name,"height":leader["height"],"kpl":raw,
                            "lp":lp.loc[lp.ts_code.eq(code)].to_dict("records"),
                            "historical_concept_rows":linked,"company_news":company.fillna("").to_dict("records")})
        packet={"mode":"as-of reconstruction; previously seen period; not blind", "feature_date":p,"decision_at":cutoff,
                "news_start":start,"source_availability":"News uses source datetime; KPL final P assumed visible D06:00; historical first-publication archives absent. Concept descriptions may be revised: auxiliary only.",
                "original_primary_theme":theme,"pool":pool,"stocks":records,
                "news_rows":len(news),"news_source_counts":news.groupby("src").size().to_dict()}
        save(folder/"input_packet.json",packet)
        manifest.append({"feature_date":p,"decision_at":cutoff,"theme":theme,"pool_n":len(pool),"news_rows":len(news),
                         "company_news_counts":{r["name"]:len(r["company_news"]) for r in records},
                         "packet_sha256":hashlib.sha256((folder/"input_packet.json").read_bytes()).hexdigest()})
        print(p,theme,len(pool),len(news),flush=True)
    save(OUT/"input_manifest.json",manifest)


if __name__=="__main__":main()
