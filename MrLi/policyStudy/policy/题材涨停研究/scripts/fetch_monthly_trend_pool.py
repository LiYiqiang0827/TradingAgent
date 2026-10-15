"""Freeze causal monthly theme/trend observation pools, without outcomes.

One trailing-feature pass is shared by fifteen month-end snapshots. Membership
is valid only in its following month; the union is a storage list, not a past
trading universe. All selected minute lookups occur after the membership freeze.
Only local data_provider databases and existing raw snapshots are used.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from coreClient import data_provider as dp
from fetch_theme_trend_pool import dates, digest, persist, write_json, WEIGHTS
from study_fifteen_min_ma_coil import rising_run
from trend_pullback_theme_context import prepare_kpl, heat_score, board_height


ROOT = Path(__file__).resolve().parents[5]
DEFAULT = ROOT/"outputs/trend_pullback_monthly_v3"
REUSE = ROOT/"outputs/trend_pullback_multiscale_v1/inputs"
MONTHS = pd.period_range("2025-07", "2026-09", freq="M")


def monthly_asofs(calendar):
    calendar = sorted(set(str(day) for day in calendar))
    snapshots = []
    for month in MONTHS:
        start = month.start_time.strftime("%Y%m%d")
        days = [day for day in calendar if day < start]
        if not days:
            raise ValueError(f"No previous market day for {month}")
        snapshots.append(dict(month=month.strftime("%Y%m"), as_of=days[-1],
                              active_start=start,
                              active_end=month.end_time.strftime("%Y%m%d")))
    return snapshots


def daily_snapshot_features(raw, adj, calendar, basic, snapshots):
    """Original strict daily gates, computed once with right-aligned windows."""
    x=dates(raw); a=dates(adj)
    x=x.merge(a[["ts_code","trade_date","adj_factor"]],
              on=["ts_code","trade_date"], validate="one_to_one")
    x=x.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    x["p_close"]=x.close*x.adj_factor
    x["date_index"]=x.trade_date.map({day:i for i,day in enumerate(calendar)})
    g=x.groupby("ts_code",sort=False)
    for n in (5,10,20,30,60,120):
        x[f"ma{n}"]=g.p_close.transform(lambda z:z.rolling(n).mean())
        if n in (20,30,60):
            x[f"rise{n}"]=x.groupby("ts_code",sort=False)[f"ma{n}"].transform(rising_run)
    x["history_span120"]=g.date_index.transform(lambda z:z-z.shift(119))
    x["volatility20"]=g.p_close.transform(lambda z:z.pct_change(fill_method=None).rolling(20).std())
    x["amount20"]=g.amount.transform(lambda z:z.rolling(20).mean())*1000
    asofs={snapshot["as_of"] for snapshot in snapshots}
    q=x[x.trade_date.isin(asofs)].copy()
    q=q.merge(basic[["ts_code","name","industry","list_date"]],on="ts_code",how="left")
    q["eligible"]=(q.history_span120.eq(119)&q.ts_code.str.endswith((".SH",".SZ"))
        &~q.name.fillna("").str.contains("ST|退",case=False)
        &q.amount20.ge(1e8)&q.close.ge(3)&q.close.le(150)
        &q.ma5.gt(q.ma10)&q.ma10.gt(q.ma20)&q.ma20.gt(q.ma30)
        &q.ma30.gt(q.ma60)&q.ma60.gt(q.ma120)
        &q[["rise20","rise30","rise60"]].max(axis=1).ge(20))
    return q.reset_index(drop=True)


def rank_month(features, kpl, calendar, snapshot, count=20, industry_cap=3, theme_cap=5):
    """Literal historical memberships plus declared theme/trend/liquidity score."""
    as_of=snapshot["as_of"]
    calendar=sorted(set(day for day in calendar if day<=as_of))
    if not calendar or calendar[-1]!=as_of:
        raise ValueError("Monthly cutoff must be in source calendar")
    q=features[features.trade_date.eq(as_of)].copy()
    x=dates(kpl); x=x[x.trade_date.le(as_of)]
    limitups, observed=prepare_kpl(x)
    prior60=calendar[-60:]; last20=calendar[-20:]; prior25=calendar[-25:]
    membership=limitups[limitups.trade_date.isin(prior60)].copy()
    window=limitups[limitups.trade_date.isin(prior25)].copy()
    labels=sorted(membership.lu_desc.unique())
    groups={(day,label):group for (day,label),group in window.groupby(["trade_date","lu_desc"],sort=False)}
    theme_records=[]
    for label in labels:
        history=[]
        for day in prior25:
            group=groups.get((day,label)); known=day in observed
            if group is None:
                heights=[]; width=0
            else:
                unique=group.assign(_height=group.status.map(board_height)).sort_values(
                    ["_height","status"]).drop_duplicates("ts_code",keep="last")
                heights=unique._height.tolist(); width=len(unique)
            current=dict(trade_date=day,known=known,width=width if known else None)
            history.append(current)
            active=sum(item["known"] and (item["width"] or 0)>0 for item in history[-5:])
            current["heat"]=heat_score(heights,active) if known else None
        rows=[row for row in history if row["trade_date"] in last20 and row["known"]]
        theme_records.append(dict(theme=label,
            theme_heat_mean20=float(np.mean([row["heat"] for row in rows])) if rows else np.nan,
            theme_width_mean20=float(np.mean([row["width"] for row in rows])) if rows else np.nan,
            theme_heat_max20=max((row["heat"] for row in rows),default=np.nan),
            theme_width_max20=max((row["width"] for row in rows),default=np.nan),
            theme_active_sessions20=sum((row["width"] or 0)>0 for row in rows),
            kpl_observed_sessions20=len(rows)))
    themes=pd.DataFrame(theme_records)
    members=membership.groupby("ts_code")
    historical_codes=set(membership.ts_code)
    daily_pool=q[q.eligible].copy()
    pool=daily_pool[daily_pool.ts_code.isin(historical_codes)].copy()
    records=[]
    lookup=themes.set_index("theme") if len(themes) else pd.DataFrame()
    for row in pool.itertuples():
        matches=members.get_group(row.ts_code)
        literal=sorted(matches.lu_desc.unique())
        # Fully unknown themes cannot become highly ranked because of NaN sort.
        choices=sorted(literal,key=lambda label:(
            -float(lookup.loc[label,"theme_heat_mean20"] if pd.notna(lookup.loc[label,"theme_heat_mean20"]) else -1),
            -float(lookup.loc[label,"theme_width_mean20"] if pd.notna(lookup.loc[label,"theme_width_mean20"]) else -1),label))
        label=choices[0]
        records.append(dict(ts_code=row.ts_code,ranking_theme=label,
            literal_themes60=json.dumps(literal,ensure_ascii=False),
            latest_kpl_date=matches.trade_date.max(),last60_limitup_days=matches.trade_date.nunique(),
            **lookup.loc[label].to_dict()))
    if records:
        pool=pool.merge(pd.DataFrame(records),on="ts_code",validate="one_to_one")
    else:
        for column in ("ranking_theme","literal_themes60","latest_kpl_date","last60_limitup_days",
                       "theme_heat_mean20","theme_width_mean20"):
            pool[column]=pd.Series(dtype="object")
    pool["daily_trend_days"]=pool[["rise20","rise30","rise60"]].max(axis=1)
    cols=dict(theme_heat="theme_heat_mean20",theme_width="theme_width_mean20",
              daily_trend="daily_trend_days",liquidity="amount20")
    for key,col in cols.items():pool[f"{key}_percentile"]=pool[col].rank(method="average",pct=True)
    pool["selection_score"]=sum(100*WEIGHTS[key]*pool[f"{key}_percentile"] for key in WEIGHTS)
    pool=pool.sort_values(["selection_score","theme_heat_mean20","theme_width_mean20","amount20","ts_code"],
        ascending=[False,False,False,False,True]).reset_index(drop=True)
    pool["rank_before_caps"]=np.arange(1,len(pool)+1)
    chosen=[]; industry_counts=Counter(); theme_counts=Counter(); reasons={}
    for index,row in pool.iterrows():
        industry=str(row.industry); label=str(row.ranking_theme)
        if len(chosen)>=count:reasons[index]="after_fixed_count";continue
        if industry_counts[industry]>=industry_cap:reasons[index]="industry_cap3";continue
        if theme_counts[label]>=theme_cap:reasons[index]="ranking_theme_cap5";continue
        if not np.isfinite(row.selection_score):reasons[index]="unknown_theme_coverage";continue
        chosen.append(index);industry_counts[industry]+=1;theme_counts[label]+=1;reasons[index]="selected"
    pool["selection_outcome"]=[reasons[index] for index in pool.index]
    for key,value in snapshot.items():pool[key]=value
    pool["selection_as_of"]=as_of
    selected=pool.loc[chosen].copy().reset_index(drop=True)
    selected["observation_rank"]=np.arange(1,len(selected)+1)
    stats=dict(**snapshot,all_market_on_asof=len(q),strict_daily_eligible=len(daily_pool),
        strict_daily_with_limitup60=len(pool),selected=len(selected),requested=count,
        shortage=count-len(selected),industry_counts=dict(industry_counts),theme_counts=dict(theme_counts),
        selection_outcomes=pool.selection_outcome.value_counts().to_dict(),
        kpl_missing_sessions20=sorted(set(last20)-observed),kpl_missing_sessions60=sorted(set(prior60)-observed),
        last60_window_start=prior60[0],last20_window_start=last20[0],
        selection_asof_max=str(q.trade_date.max()) if len(q) else None,
        membership_asof_max=str(membership.trade_date.max()) if len(membership) else None)
    return selected,pool,stats


def union_membership(monthly):
    rows=[]
    for code,group in monthly.groupby("ts_code",sort=True):
        first=group.sort_values("month").iloc[0]
        rows.append(dict(ts_code=code,name=first["name"],industry=first.industry,
            first_active_month=group.month.min(),last_active_month=group.month.max(),
            active_month_count=group.month.nunique(),
            active_months=json.dumps(sorted(group.month.unique()),ensure_ascii=False),
            ranking_themes=json.dumps(sorted(group.ranking_theme.unique()),ensure_ascii=False),
            storage_union_only=True))
    return pd.DataFrame(rows)


def audit_prefix(raw,adj,basic,kpl,calendar,features,monthly):
    """Independent prefix recomputation and future-attribution injection checks."""
    checks=[]
    for as_of in ("20250630","20251231","20260831"):
        snapshot=next(item for item in monthly_asofs(calendar) if item["as_of"]==as_of)
        prefix_calendar=[day for day in calendar if day<=as_of]
        prefix=daily_snapshot_features(raw[raw.trade_date.le(as_of)],adj[adj.trade_date.le(as_of)],
                                       prefix_calendar,basic,[snapshot])
        full=features[features.trade_date.eq(as_of)].sort_values("ts_code").reset_index(drop=True)
        prefix=prefix.sort_values("ts_code").reset_index(drop=True)
        for col in ("ma5","ma10","ma20","ma30","ma60","ma120","rise20","rise30","rise60", "amount20", "eligible"):
            pd.testing.assert_series_equal(prefix[col],full[col],check_names=False,check_dtype=False,rtol=1e-10)
        # Future labels are adversarial. A current monthly snapshot cannot
        # acquire members or change ranks from next month's attribution.
        future=kpl[kpl.trade_date.eq(as_of)].copy()
        future["trade_date"]=snapshot["active_start"];future["lu_desc"]="未来99连板伪题材";future["status"]="99连板"
        selected,_,stats=rank_month(prefix,pd.concat([kpl,future],ignore_index=True),calendar,snapshot)
        expected=monthly[monthly.month.eq(snapshot["month"])].reset_index(drop=True)
        assert selected.ts_code.tolist()==expected.ts_code.tolist()
        pd.testing.assert_series_equal(selected.selection_score,expected.selection_score,
                                       check_names=False,check_dtype=False,rtol=1e-10)
        assert selected.as_of.lt(selected.active_start).all()
        assert selected.latest_kpl_date.le(as_of).all()
        checks.append(dict(as_of=as_of,month=snapshot["month"],passed=True,
            checks=["full_trailing_pass_equals_independent_prefix", "future99board_attribution_cannot_change_members_or_scores",
                    "daily_and_kpl_cutoffs_before_active_month", "industrycap3_and_themecap5"],
            selected=len(selected)))
    return dict(passed=True,checkpoints=checks,
        training_contract="A sample is eligible only if sample.ts_code belongs to MONTHLY_POOLS at sample.time.strftime('%Y%m'). Union observation_pool is storage metadata only.")


def main(out=DEFAULT,reuse=REUSE):
    out=Path(out); reuse=Path(reuse); inp=out/"inputs"; inp.mkdir(parents=True,exist_ok=True)
    calendar=dates(persist(inp/"calendar.parquet",lambda:pd.read_parquet(reuse/"calendar.parquet")))
    days=sorted(calendar.cal_date.astype(str).unique())
    basic=persist(inp/"basic.parquet",lambda:pd.read_parquet(reuse/"basic.parquet"))
    raw=dates(persist(inp/"selection_daily.parquet",lambda:dp.get_day(
        start_date="20241001",end_date="20260930",qfq=False,source="database_only")))
    adj=dates(persist(inp/"selection_adj.parquet",lambda:dp.get_adj_factor(
        start_date="20241001",end_date="20260930",source="database_only")))
    kpl=dates(persist(inp/"kpl.parquet",lambda:pd.read_parquet(reuse/"kpl.parquet")))
    snapshots=monthly_asofs(days)
    features=persist(inp/"selection_features.parquet",lambda:daily_snapshot_features(raw,adj,days,basic,snapshots))
    manifest=out/"OBSERVATION_SELECTION.json"
    if not manifest.exists():
        pools=[]; denominators=[]; scored=[]
        for snapshot in snapshots:
            selected,pool,stats=rank_month(features,kpl,days,snapshot)
            pools.append(selected);scored.append(pool);denominators.append(stats)
            print("monthly_pool",snapshot["month"],stats["all_market_on_asof"],stats["strict_daily_eligible"],
                  stats["strict_daily_with_limitup60"],len(selected),stats["theme_counts"],flush=True)
        monthly=pd.concat(pools,ignore_index=True)
        pd.concat(scored,ignore_index=True).to_csv(out/"MONTHLY_SCORED_CANDIDATES.csv",index=False)
        monthly.to_csv(out/"MONTHLY_POOLS.csv",index=False)
        union=union_membership(monthly);union.to_csv(out/"observation_pool.csv",index=False)
        selection_inputs=[inp/name for name in ("calendar.parquet","basic.parquet","selection_daily.parquet",
                           "selection_adj.parquet","kpl.parquet","selection_features.parquet")]
        source=out/"selection_source";source.mkdir(exist_ok=True)
        source_names=["fetch_monthly_trend_pool.py","fetch_theme_trend_pool.py","trend_pullback_features.py",
                      "trend_pullback_theme_context.py","study_fifteen_min_ma_coil.py"]
        for name in source_names:shutil.copyfile(Path(__file__).with_name(name),source/name)
        write_json(manifest,dict(version="causal_monthly_theme_trend_pool_v3",months=[s["month"] for s in snapshots],
            policy=dict(snapshot="last market close strictly before active month",monthly_target=20,
                        industry_cap=3,ranking_theme_cap=5,shortage="retain fewer; no future replacement",
                        daily_gates="unchanged universe_snapshot strict MA stack, >=20-day rise, 120-day contiguous history, turnover/name/price gates",
                        ranking_weights=WEIGHTS,training="stock/date must be in that same month's actual selected pool"),
            monthly_denominators=denominators,union_stocks=len(union),monthly_member_slots=len(monthly),
            future_returns_used=False,future_minute_coverage_used=False,
            selected_before_union_minute_fetch=True,
            selection_sha256={str(path.relative_to(out)):digest(path) for path in selection_inputs+
                [out/"MONTHLY_POOLS.csv",out/"MONTHLY_SCORED_CANDIDATES.csv",out/"observation_pool.csv"]},
            source_sha256={name:digest(source/name) for name in source_names},
            caveats=["v3 monthly refresh corrects a known fixed-pool mechanism failure after viewing v1/v2; chronological evaluation is not a pristine unseen holdout.",
                     "Current survivor/ST-name/industry metadata are not historical archived metadata.",
                     "KPL first-published snapshot versions are not independently archived; dated close availability is assumed.",
                     "Literal theme labels are prior-market attribution, not business benefit or established catalyst causality.",
                     "Union membership cannot authorize earlier training or trading before actual monthly selection.",
                     "No future return or minute coverage changes monthly names. Data deficiencies remain deficiencies."] ))
        print("FROZEN",len(monthly),"monthly slots",len(union),"union stocks",flush=True)
        write_json(out/"SELECTION_CAUSAL_AUDIT.json",audit_prefix(raw,adj,basic,kpl,days,features,monthly))
        print("Causal prefix audit passed",flush=True)
    else:
        frozen=json.loads(manifest.read_text())
        for relative,sha in frozen["selection_sha256"].items():
            if digest(out/relative)!=sha:raise ValueError(f"Frozen selection changed: {relative}")
        union=pd.read_csv(out/"observation_pool.csv")
        if not (out/"SELECTION_CAUSAL_AUDIT.json").exists():
            monthly=pd.read_csv(out/"MONTHLY_POOLS.csv",dtype={"month":str,"as_of":str,"active_start":str,"active_end":str})
            write_json(out/"SELECTION_CAUSAL_AUDIT.json",audit_prefix(raw,adj,basic,kpl,days,features,monthly))
    codes=union.ts_code.tolist()
    # Per-code native-minute caches prevent one enormous database result and
    # make interrupted extraction resumable without changing membership.
    cache=inp/"per_stock";cache.mkdir(exist_ok=True)
    for number,code in enumerate(codes,1):
        persist(cache/f"{code}_fifteen.parquet",lambda code=code:dp.get_fifteenMin(
            ts_code=code,start_date="20241001",end_date="20260930"))
        persist(cache/f"{code}_limits.parquet",lambda code=code:dp.get_stk_limit(
            ts_code=code,start_date="20241001",end_date="20260930",source="database_only"))
        if number%10==0:print("local union fetch",number,"/",len(codes),flush=True)
    persist(inp/"daily.parquet",lambda:raw[raw.ts_code.isin(codes)].copy())
    persist(inp/"adj.parquet",lambda:adj[adj.ts_code.isin(codes)].copy())
    persist(inp/"fifteen.parquet",lambda:pd.concat([pd.read_parquet(cache/f"{code}_fifteen.parquet") for code in codes],ignore_index=True))
    persist(inp/"limits.parquet",lambda:pd.concat([pd.read_parquet(cache/f"{code}_limits.parquet") for code in codes],ignore_index=True))
    if not (inp/"news.parquet").exists():shutil.copyfile(reuse/"news.parquet",inp/"news.parquet")
    write_json(out/"INPUT_SNAPSHOT.json",dict(selection_manifest_sha256=digest(manifest),
        input_hashes={path.name:digest(path) for path in sorted(inp.glob("*.parquet"))},
        source="local coreClient unified providers/database_only/native TDX; no online fallback or background downloads",
        training_membership_table="MONTHLY_POOLS.csv",storage_union_codes=codes))
    coverage=[]
    for name in ("daily","adj","fifteen","limits"):
        x=pd.read_parquet(inp/f"{name}.parquet")
        col="datetime" if name=="fifteen" else "trade_date"
        for code in codes:
            group=x[x.ts_code.eq(code)]
            coverage.append(dict(dataset=name,ts_code=code,rows=len(group),
                first=str(group[col].min()) if len(group) else None,last=str(group[col].max()) if len(group) else None,
                used_for_selection=False))
    pd.DataFrame(coverage).to_csv(out/"INPUT_COVERAGE.csv",index=False)
    print("Complete local monthly v3 snapshot",out,flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,default=DEFAULT)
    parser.add_argument("--reuse",type=Path,default=REUSE);args=parser.parse_args();main(args.output,args.reuse)
