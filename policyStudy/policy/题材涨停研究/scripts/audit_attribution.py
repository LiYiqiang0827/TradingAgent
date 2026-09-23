"""Validate evidence timing and retain both reviewed and unresolved attribution."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import pandas as pd

from build_attribution_inputs import OUT, CASES
from build_case_dossiers import BASE, SOURCE, save, normalize
from study_divergence import choose_cores


def resolve_evidence(registry, identity, cutoff, company):
    records=registry.get(identity,[])
    valid=[r for r in records if pd.Timestamp(r["datetime"])<=pd.Timestamp(cutoff)
           and company in str(r.get("title",""))+str(r.get("content",""))]
    if not valid:
        raise ValueError(f"Missing, late or wrong-company evidence: {identity} / {company} / {cutoff}")
    return min(valid,key=lambda r:pd.Timestamp(r["datetime"]))


def main():
    annotation_path=BASE/"research/attribution_reviews_v1.json"
    reviews=json.loads(annotation_path.read_text())["reviews"]
    by_key={(r["date"],r["code"]):r for r in reviews}
    assert len(by_key)==len(reviews)
    packets=[json.loads(p.read_text()) for p in sorted(OUT.glob("202*_*/input_packet.json"))]
    registry={}
    for p in packets:
        for s in p["stocks"]:
            for r in s["company_news"]:registry.setdefault(r["md5"],[]).append(r)
    day=normalize(pd.read_pickle(SOURCE/"cache/day.pkl")).set_index(["trade_date","ts_code"])
    limits=normalize(pd.read_pickle(SOURCE/"cache/limits.pkl")).set_index(["trade_date","ts_code"])
    rows=[]; selected={}; case_summaries=[]; used=set()
    for p in packets:
        date=p["feature_date"]; group=[]
        pool=p["pool"]
        assert len(set(pool))==len(pool)
        cores=choose_cores(p["stocks"],day.loc[date],limits.loc[date])
        for s in p["stocks"]:
            raw=s["kpl"][0];lp=s["lp"][0] if s["lp"] else {}
            note=by_key.get((date,s["ts_code"]))
            evidence=[]
            if note:
                used.add((date,s["ts_code"]))
                for identity in note["news_ids"]:
                    r=resolve_evidence(registry,identity,p["decision_at"],s["name"])
                    evidence.append({"id":identity,"datetime":r["datetime"],"src":r["src"]})
                    selected[identity]=r
            row={"feature_date":date,"decision_at":p["decision_at"],"primary_theme":p["original_primary_theme"],
                 "ts_code":s["ts_code"],"name":s["name"],"height":s["height"],
                 "regime":"10%" if limits.loc[(date,s["ts_code"]),"up_limit"]/day.loc[(date,s["ts_code"]),"pre_close"]<1.15 else "20%",
                 "is_original_height_core":s["ts_code"] in [c["ts_code"] for c in cores],
                 "first_limit_time":raw["lu_time"],"last_limit_time":raw["last_time"],
                 "turnover_yuan":raw["amount"],"order_float_pct":float(raw["limit_order"])/float(raw["free_float"])*100,
                 "raw_main_label":raw["lu_desc"],"raw_multi_labels":raw["theme"],"lp_reason":lp.get("limit_reason"),
                 "review_state":"新闻逐条核验" if note else "仅标签/描述线索；语义未定",
                 "branch":note["branch"] if note else "待核验："+str(lp.get("limit_reason") or raw["theme"]),
                 "exposure":note["exposure"] if note else "未知；不能由标签或缺新闻推断",
                 "judgment":note["judgment"] if note else "保留原名单，待查具体事件及公司事实，不强行授予产业核心。",
                 "invalid_inference":note["invalid_inference"] if note else "原榜主归因、LP原因与概念描述不是三个独立来源。",
                 "news_window_mentions":len(s["company_news"]),"concept_rows":len(s["historical_concept_rows"]),"evidence":evidence}
            group.append(row);rows.append(row)
        assert {r["ts_code"] for r in group}==set(pool)
        folder=OUT/(date+"_"+p["original_primary_theme"])
        save(folder/"reviewed_attribution.json",group)
        case_summaries.append({"feature_date":date,"decision_at":p["decision_at"],"theme":p["original_primary_theme"],"pool_n":len(group),
                               "reviewed_n":sum(r["review_state"]=="新闻逐条核验" for r in group),
                               "no_company_news_n":sum(r["news_window_mentions"]==0 for r in group),
                               "original_height_cores":cores,
                               "reviewed_branch_counts":dict(Counter(r["branch"] for r in group if r["review_state"]=="新闻逐条核验")),
                               "capacity_candidates":[{"name":r["name"],"amount_yuan":r["turnover_yuan"]} for r in sorted(group,key=lambda r:-r["turnover_yuan"])[:3]]})
    assert used==set(by_key) and len(rows)==52
    table=pd.DataFrame(rows)
    table["evidence_ids"]=table.evidence.map(lambda xs:";".join(r["id"] for r in xs))
    table.drop(columns="evidence").to_csv(OUT/"all_52_attributions.csv",index=False,encoding="utf-8-sig")
    save(OUT/"all_52_attributions.json",rows)
    save(OUT/"reviewed_news.json",list(selected.values()))
    v1=json.loads((SOURCE/"prediction_manifest.json").read_text())
    protected={key:hashlib.sha256((SOURCE/file).read_bytes()).hexdigest()==v1[key+"_sha256"]
               for key,file in [("predictions","predictions.json"),("labels","labels.json"),("model","frozen_model.json")]}
    assert all(protected.values())
    amount_deltas=[abs(float(r["turnover_yuan"])/(float(day.loc[(r["feature_date"],r["ts_code"]),"amount"])*1000)-1) for r in rows]
    assert all(x<.001 for x in amount_deltas),"KPL and daily traded amount differ by >=0.1%"
    summary={"rows":len(rows),"reviewed_stock_dates":len(reviews),"selected_news_ids":len(selected),
             "remaining_unresolved_stock_dates":len(rows)-len(reviews),
             "original_height_candidates":sum(r["is_original_height_core"] for r in rows),
             "cases":case_summaries,"v1_unchanged":protected,
             "all_evidence_before_cutoff":True,"all_pools_preserved":True,
             "kpl_daily_amount_checked":len(rows),"max_amount_relative_difference":max(amount_deltas),
             "script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             "annotations_sha256":hashlib.sha256(annotation_path.read_bytes()).hexdigest(),
             "protocol_sha256":hashlib.sha256((BASE/"research/attribution_protocol_v1.json").read_bytes()).hexdigest()}
    save(OUT/"summary.json",summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
