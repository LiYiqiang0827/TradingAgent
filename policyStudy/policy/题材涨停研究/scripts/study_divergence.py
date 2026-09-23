"""Full 338-event divergence audit with P-day pools frozen before divergence.

Prices are reference-close ratios, never rounded-return compounding. Historical
exploration only; all eight months have been seen. No predictive accuracy claim.
"""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from coreClient import data_provider as dp
from build_case_dossiers import normalize, save, clean

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE/"data/stage1_v1"
OUT = BASE/"data/divergence_v1"
CUTOFFS = ["09:31:00", "09:45:00", "10:30:00", "14:00:00", "15:00:00"]


def finite_ratio(numerator, denominator):
    value = numerator / denominator
    value = value.where((numerator>0)&(denominator>0)&np.isfinite(value))
    return value.mask((value-1).abs()<1e-10,1.0)


def choose_cores(prior_leaders, prior_prices, prior_limits):
    """No core from an all-first-board regime; preserve ties in each regime."""
    bins = defaultdict(list)
    for r in prior_leaders:
        ratio = prior_limits.loc[r["ts_code"],"up_limit"]/prior_prices.loc[r["ts_code"],"pre_close"]-1
        bins["10%" if ratio < .15 else "20%"].append(r)
    cores = []
    for regime, group in bins.items():
        height = max(r["height"] for r in group)
        if height >= 2:
            cores.extend(dict(ts_code=r["ts_code"],name=r["name"],height=r["height"],regime=regime)
                         for r in group if r["height"]==height)
    return cores


def snapshot(pool, cores, date, minute, day_t, ratio_d, cutoff):
    stamp = pd.Timestamp(date).strftime("%Y-%m-%d")+" "+cutoff
    rows = minute.loc[minute.datetime.eq(stamp)&minute.ts_code.isin(pool)].set_index("ts_code")
    assert rows.index.is_unique
    price = rows.price.reindex(pool)
    current = finite_ratio(price,day_t.pre_close)-1
    restored = (1+current)*ratio_d-1
    restored = restored.mask(restored.abs()<1e-10,0.0)
    remaining = finite_ratio(day_t.close,price)-1
    valid = current.dropna(); core = current.reindex(cores).dropna()
    complete = len(valid)==len(pool)
    has_core = bool(cores) and len(core)==len(cores)
    core_positive = has_core and core.median()>0
    group_positive = complete and int((valid>0).sum())>len(pool)/2
    core_group = core_positive and group_positive
    return {"cutoff":cutoff,"expected":len(pool),"covered":len(valid),
            "positive_count":int((valid>0).sum()),"median_pct":valid.median()*100,
            "since_p_covered":int(restored.notna().sum()),"since_p_median_pct":restored.median()*100,
            "core_expected":len(cores),"core_covered":len(core),"core_median_pct":core.median()*100,
            "remaining_covered":int(remaining.notna().sum()),"remaining_median_pct":remaining.median()*100,
            "core_only":bool(core_positive),"core_group":bool(core_group),
            "core_group_restored":bool(core_group and restored.notna().all() and restored.median()>=0),
            "missing":sorted(set(pool)-set(valid.index))}


def load_minute(request):
    date, pool = request
    path = OUT/"cache"/("minute_"+date+".pkl")
    identity = hashlib.sha256("|".join(sorted(pool)).encode()).hexdigest()
    meta = path.with_suffix(".json")
    if path.exists() and meta.exists() and json.loads(meta.read_text())["pool_sha256"]==identity:
        frame = pd.read_pickle(path)
    else:
        frame = dp.get_minute(trade_date=date,ts_codes=sorted(pool),source="database_only")
        frame.to_pickle(path)
        save(meta,{"date":date,"pool_sha256":identity,"requested":len(pool),"rows":len(frame)})
    return date,frame


def main():
    (OUT/"cache").mkdir(parents=True,exist_ok=True)
    records = json.loads((SOURCE/"features.json").read_text())
    dates = [r["date"] for r in records]
    positions = {d:i for i,d in enumerate(dates)}
    events = pd.read_csv(BASE/"data/theme_atlas_v1/all_divergence_events.csv",dtype={"date":str})
    day = normalize(pd.read_pickle(SOURCE/"cache/day.pkl")).set_index(["trade_date","ts_code"]).sort_index()
    limits = normalize(pd.read_pickle(SOURCE/"cache/limits.pkl")).set_index(["trade_date","ts_code"]).sort_index()
    requests = defaultdict(set)
    for e in events.to_dict("records"):
        i = positions[e["date"]]
        requests[dates[i+1]].update(l["ts_code"] for l in records[i-1]["leaders"] if l["theme"]==e["theme"])
    minutes = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        for j,(date,frame) in enumerate(executor.map(load_minute, sorted(requests.items())),1):
            minutes[date] = frame
            if j%30==0 or j==len(requests):print(f"minute days {j}/{len(requests)}",flush=True)
    details, flat = [], []
    for e in events.to_dict("records"):
        i = positions[e["date"]]; p,d,t,u = dates[i-1:i+3]
        prior_leaders = [l for l in records[i-1]["leaders"] if l["theme"]==e["theme"]]
        pool = sorted(l["ts_code"] for l in prior_leaders)
        assert len(pool)==e["prior_width"]
        prices = {date:day.loc[date].reindex(pool) for date in [p,d,t,u]}
        cores = choose_cores(prior_leaders,prices[p],limits.loc[p])
        core_codes = [r["ts_code"] for r in cores]
        dr = finite_ratio(prices[d].close,prices[d].pre_close)
        tr = finite_ratio(prices[t].close,prices[t].pre_close)
        ur = finite_ratio(prices[u].close,prices[u].pre_close)
        dt = dr*tr; dtu = dt*ur
        # Snap exact-flat products back to 1 after floating-point multiplication.
        dt = dt.mask((dt-1).abs()<1e-10,1.0);dtu = dtu.mask((dtu-1).abs()<1e-10,1.0)
        complete = bool(dr.notna().all() and dt.notna().all() and dtu.notna().all())
        actual_loss = bool(dr.notna().all() and dr.median()<1)
        repaired_pool = bool(complete and actual_loss and max(dt.median(),dtu.median())>=1)
        market_d = records[i]["market"];market_t = records[i+1]["market"]
        row = dict(e,p_date=p,t_date=t,u_date=u,pool_n=len(pool),core_n=len(cores),
                   d_covered=int(dr.notna().sum()),t_covered=int(dt.notna().sum()),u_covered=int(dtu.notna().sum()),
                   daily_complete=complete,d_actual_loss=actual_loss,d_median_pct=(dr.median()-1)*100,
                   t_day_median_pct=(tr.median()-1)*100,t_since_p_median_pct=(dt.median()-1)*100,
                   u_since_p_median_pct=(dtu.median()-1)*100,old_pool_restored=repaired_pool,
                   market_d_breadth=market_d["breadth"],market_t_breadth=market_t["breadth"],
                   market_d_amount_ratio=market_d["amount_ratio"],market_t_index_ret=market_t["index_ret"])
        timeline = []
        for j in range(max(0,i-3),i+3):
            members = [l for l in records[j]["leaders"] if l["theme"]==e["theme"]]
            timeline.append({"date":dates[j],"width":len(members),"original_pool_in_theme":sum(l["ts_code"] in pool for l in members),
                             "members":[{k:l[k] for k in ["ts_code","name","height"]} for l in members],
                             "market":{k:records[j]["market"][k] for k in ["breadth","up_count","promotion","amount_ratio","prev_up_excess"]}})
        first_repair = next((x for x in timeline if x["date"] in [t,u] and x["width"]>=.8*len(pool)),None)
        assert bool(first_repair)==bool(e["future2_repaired"])
        row["first_width_repair_date"] = first_repair["date"] if first_repair else None
        row["width_repair_outside_p_share"] = 1-first_repair["original_pool_in_theme"]/first_repair["width"] if first_repair else None
        snaps = [snapshot(pool,core_codes,t,minutes[t],prices[t],dr,cutoff) for cutoff in CUTOFFS]
        for s in snaps:
            prefix = "m"+s["cutoff"][:5].replace(":","")
            for k in ["covered","median_pct","since_p_median_pct","remaining_covered","remaining_median_pct","core_only","core_group","core_group_restored","core_covered","core_median_pct"]:
                row[prefix+"_"+k]=s[k]
        daily_stock = []
        for l in prior_leaders:
            code=l["ts_code"]
            daily_stock.append({"ts_code":code,"name":l["name"],"p_height":l["height"],"core":code in core_codes,
                                "d_pct":(dr.loc[code]-1)*100,"t_pct":(tr.loc[code]-1)*100,
                                "t_since_p_pct":(dt.loc[code]-1)*100,"u_since_p_pct":(dtu.loc[code]-1)*100})
        details.append(dict(summary=row,pool=pool,cores=cores,timeline=timeline,snapshots=snaps,stock_outcomes=daily_stock))
        flat.append(row)
    a = pd.DataFrame(flat)
    assert len(a)==338 and not a.duplicated(["date","theme"]).any()
    a.to_csv(OUT/"all_338_events.csv",index=False,encoding="utf-8-sig")
    save(OUT/"event_details.json",details)
    summary = {"events":len(a),"width_repairs":int(a.future2_repaired.sum()),"daily_complete":int(a.daily_complete.sum()),
               "actual_loss_events":int(a.d_actual_loss.sum()),"width_drop_without_negative_median":int(((a.d_covered==a.pool_n)&~a.d_actual_loss).sum()),
               "no_height_core":int((a.core_n==0).sum()),"cross_tab":[],"diagnostics":[],"missing":[]}
    eligible = a.loc[a.daily_complete & a.d_actual_loss]
    for label,g in eligible.groupby("future2_repaired"):
        summary["cross_tab"].append({"width_repaired":bool(label),"n":len(g),"old_pool_restored":int(g.old_pool_restored.sum()),
                                     "t_rebound_positive":int((g.t_day_median_pct>0).sum())})
    for cutoff in ["0945","1030","1400"]:
        prefix="m"+cutoff
        complete = eligible.loc[(eligible[prefix+"_covered"]==eligible.pool_n)&(eligible[prefix+"_remaining_covered"]==eligible.pool_n)]
        for criterion in ["core_only","core_group","core_group_restored"]:
            group=complete.loc[complete[prefix+"_"+criterion]]
            summary["diagnostics"].append({"time":cutoff,"criterion":criterion,"eligible":len(complete),"n":len(group),
                                            "remaining_negative":int((group[prefix+"_remaining_median_pct"]<0).sum()),
                                            "remaining_median_pct":group[prefix+"_remaining_median_pct"].median(),
                                            "old_pool_restored_by_two_closes":int(group.old_pool_restored.sum())})
    summary["missing"] = a.loc[~a.daily_complete|a.m1030_covered.lt(a.pool_n),["date","theme","pool_n","d_covered","t_covered","u_covered","m1030_covered"]].to_dict("records")
    save(OUT/"summary.json",summary)
    protocol=BASE/"research/divergence_protocol_v1.json"
    save(OUT/"manifest.json",{"features_sha256":hashlib.sha256((SOURCE/"features.json").read_bytes()).hexdigest(),
                              "protocol_sha256":hashlib.sha256(protocol.read_bytes()).hexdigest(),"script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                              "minute_days":len(minutes),"events":len(a),"mode":"retrospective; not independent prediction validation"})
    print(json.dumps(clean(summary),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
