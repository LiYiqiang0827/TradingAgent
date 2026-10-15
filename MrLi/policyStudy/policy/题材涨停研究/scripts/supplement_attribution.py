"""Bounded offline lookback for unresolved members, preserving historical names."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import pandas as pd

from build_case_dossiers import BASE, SOURCE, dp, normalize, save

OLD=BASE/"data/attribution_v1"
OUT=BASE/"data/attribution_v2"


def text_matches(frame, terms):
    pattern="|".join(re.escape(t) for t in terms if t)
    return (frame.title.fillna("")+" "+frame.content.fillna("")).str.contains(pattern,regex=True)


def fetch_week(request):
    start,end,terms=request
    name=start[:10].replace("-","")
    path=OUT/"cache"/("news_matches_"+name+".pkl")
    identity=hashlib.sha256(json.dumps([start,end,sorted(terms)],ensure_ascii=False).encode()).hexdigest()
    meta=path.with_suffix(".json")
    if path.exists() and meta.exists() and json.loads(meta.read_text())["request_sha256"]==identity:
        return pd.read_pickle(path),json.loads(meta.read_text())
    news=dp.get_news(start_datetime=start,end_datetime=end,limit=None,source="database_only")
    selected=news.loc[text_matches(news,terms)].copy()
    selected.to_pickle(path)
    info={"start":start,"end":end,"request_sha256":identity,"source":"database_only","raw_rows":len(news),
          "retained_rows":len(selected),"source_counts":news.groupby("src").size().to_dict()}
    save(meta,info)
    return selected,info


def main():
    (OUT/"cache").mkdir(parents=True,exist_ok=True)
    rows=json.loads((OLD/"all_52_attributions.json").read_text())
    unresolved=[r for r in rows if r["review_state"]!="新闻逐条核验"]
    kpl=normalize(pd.read_pickle(SOURCE/"cache/kpl.pkl"))
    requests={}
    for r in unresolved:
        previous=kpl.loc[kpl.ts_code.eq(r["ts_code"])&kpl.trade_date.le(r["feature_date"])]
        requests[r["ts_code"]]={"aliases":sorted(set(previous.name)|{r["name"]}),"cutoff":r["decision_at"]}
    terms=sorted({t for v in requests.values() for t in v["aliases"]})
    windows=[]
    end_all=pd.Timestamp(max(r["decision_at"] for r in unresolved))
    start=pd.Timestamp("2026-01-01")
    while start<=end_all:
        end=min(start+pd.Timedelta(days=7)-pd.Timedelta(seconds=1),end_all)
        windows.append((str(start),str(end),terms));start=end+pd.Timedelta(seconds=1)
    frames=[];manifest=[]
    with ThreadPoolExecutor(max_workers=3) as executor:
        for frame,info in executor.map(fetch_week,windows):
            frames.append(frame);manifest.append(info)
            print(info["start"][:10],info["raw_rows"],info["retained_rows"],flush=True)
    merged=pd.concat(frames,ignore_index=True)
    times=pd.to_datetime(merged.datetime,format="mixed")
    inventories=[]
    for r in unresolved:
        q=requests[r["ts_code"]]
        selected=merged.loc[(times<=pd.Timestamp(q["cutoff"]))&text_matches(merged,q["aliases"])]
        folder=OUT/(r["feature_date"]+"_"+r["primary_theme"]);folder.mkdir(exist_ok=True)
        save(folder/(r["ts_code"]+"_news.json"),selected.fillna("").to_dict("records"))
        inventories.append({"date":r["feature_date"],"code":r["ts_code"],"name":r["name"],"aliases":q["aliases"],
                            "cutoff":q["cutoff"],"news_n":len(selected),"news_before_old_window":int((pd.to_datetime(selected.datetime,format="mixed")<pd.Timestamp(r["feature_date"])-pd.Timedelta(days=14)).sum())})
    save(OUT/"retrieval_manifest.json",{"windows":manifest,"stocks":inventories,
                                        "raw_rows":sum(x["raw_rows"] for x in manifest),"retained_rows":len(merged),
                                        "method":"2026-01-01 to each case cutoff; exact names and prior KPL aliases; no latest company list"})
    print(json.dumps(inventories,ensure_ascii=False,indent=2),flush=True)


if __name__=="__main__":main()
