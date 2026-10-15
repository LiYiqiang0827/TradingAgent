"""Frozen-cohort exit research runner; no entry re-tuning and no real orders."""
from __future__ import annotations
import argparse,json,hashlib,time
from dataclasses import asdict
from pathlib import Path
import sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[5]
sys.path[:0]=[str(ROOT),str(ROOT/'offlineDataManager/scripts')]
from study_fifteen_min_ma_coil import features,Rules as CoilRules
from study_second_wave_template import quiet_bases,core_rules
from study_second_wave_exits import ExitRules,theme_context,stock_theme_timeline,replay,POLICIES
from coreClient.data_provider import get_kpl_list,get_stk_limit,get_theme_daily

def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False),encoding='utf-8')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def ensure_inputs(source,out):
    """Fresh output directories read through the unified offline provider."""
    inputs=out/'inputs';inputs.mkdir(parents=True,exist_ok=True)
    signals=json.loads((source/'signals_before_outcomes.json').read_text());codes=sorted({s['ts_code'] for s in signals})
    calls={'kpl_extended':lambda:get_kpl_list(start_date='20251103',end_date='20260515',tags=['涨停','炸板'],source='database_only'),
           'limits_extended':lambda:get_stk_limit(ts_codes=codes,start_date='20260101',end_date='20260515',source='database_only'),
           'theme_daily_reference':lambda:get_theme_daily(start_date='20260101',end_date='20260515')}
    for name,fn in calls.items():
        p=inputs/(name+'.parquet')
        if p.exists():continue
        d=fn()
        if d.empty:raise ValueError('Required offline input is missing: '+name)
        if name=='kpl_extended':
            d=d[['trade_date','ts_code','name','lu_desc','tag','status','lu_time']].copy()
            for column in d.columns:d[column]=d[column].fillna('').astype(str)
        d.to_parquet(p,index=False)

def prepare(source,out):
    signals=json.loads((source/'signals_before_outcomes.json').read_text());inputs=source/'inputs'
    manifest=json.loads((source/'FROZEN_SIGNAL_MANIFEST.json').read_text())
    assert all(sha(source/p)==h for p,h in manifest['files'].items())
    assert all(sha(Path(__file__).with_name(p))==h for p,h in manifest['scripts'].items())
    calendar=sorted(pd.read_parquet(inputs/'outcome_calendar.parquet').cal_date.astype(str).unique())
    bars=pd.read_parquet(inputs/'outcome_fifteen.parquet');frames={};bases={};audit=[];excluded={}
    for code,g in bars.groupby('ts_code'):
        f=features(g,calendar,CoilRules());scale=f.close/f.p_close
        for n in (5,10,20,30,60,120,250):f[f'm{n}']=f[f'ma{n}']*scale
        rows=f.to_dict('records');frames[code]=dict(datetime=f.datetime,rows=rows,frame=f)
        b=quiet_bases(f,core_rules())
        for end,items in b.items():
            for item in items:item['start_index']=end-item['bars']+1
        bases[code]=b
    for s in signals:
        key=s['ts_code']+'_'+s['signal_time'];f=frames[s['ts_code']]['frame'];at=pd.Timestamp(s['signal_time']);j=int(np.flatnonzero(f.datetime.eq(at))[0])
        entry=f.iloc[j+1];day=entry.datetime.strftime('%Y%m%d');end=calendar[calendar.index(day)+19]
        path=f.iloc[j+1:];path=path[path.datetime.dt.strftime('%Y%m%d').le(end)]
        reason=None
        if not path.valid_bar.all():reason='outcome_data_gap'
        elif path.adj_factor.nunique()!=1 or not np.isclose(entry.adj_factor,s['base']['adj_factor']):reason='corporate_action_accounting_unmodeled'
        if reason:excluded[key]=reason
        audit.append(dict(key=key,valid_bars=int(path.valid_bar.sum()),slots=len(path),factor_count=int(path.adj_factor.nunique()),exclusion=reason))
    kpl=pd.read_parquet(out/'inputs/kpl_extended.parquet')
    names=sorted((set(kpl.lu_desc)|{s['prior_theme_name'] for s in signals})-{''})
    theme,td=theme_context(kpl,calendar,names)
    theme['_stock_theme_timeline']=stock_theme_timeline(kpl,calendar,{s['ts_code'] for s in signals})
    limits=pd.read_parquet(out/'inputs/limits_extended.parquet');limits.trade_date=limits.trade_date.astype(str)
    li={(r.ts_code,r.trade_date):dict(up_limit=float(r.up_limit),down_limit=float(r.down_limit)) for r in limits.itertuples()}
    td.to_csv(out/'theme_history.csv',index=False,encoding='utf-8-sig');dump(out/'DATA_AUDIT.json',dict(cases=audit,excluded=excluded,kpl_tags=kpl.tag.value_counts().to_dict(),calendar=calendar,
        theme_available_days=int(td.loc[td.known,'trade_date'].nunique()),literal_mapping=True,theme_intraday_cutoff='previous complete session',one_price_fill_uses_future_bar_range=True))
    return signals,calendar,frames,bases,theme,li,excluded

def run(args):
    out=Path(args.output);source=Path(args.source);out.mkdir(parents=True,exist_ok=True);rules=ExitRules();started=time.time()
    config=dict(rules=asdict(rules),policies=list(POLICIES),profiles=['equal20','risk05','risk10'],initial_cash=300000.,
        cohort_source=str(source),signal_hash=sha(source/'signals_before_outcomes.json'),entry_rules_unchanged=True,
        development_only=True,known_paths_previously_seen=True,execution='completed close decision, next slot OPEN proxy; per-lot T+1; 100-share lot; sell down-limit one-price blocked',
        excluded_case_policy='exclude whole comparative case for missing data or factor change; exclusion is data-quality, never alpha',
        theme='literal historical KPL; previous-close only; static first-wave theme plus separate latest previously observed stock attribution ablation',
        reentry='fresh post-exit quiet base + original upper reclaim + volume + short MA order; max two, original 20-session deadline',
        portfolio='cash only, single-name cap20%, total new purchases cap60%; half-trial for phased; no averaging down',
        fee_scope='commission 0.03%, minimum5/order; sell stamp0.05%; slippage0.05%/side; transfer/other fees not modeled')
    p=out/'FROZEN_EXIT_RULES.json'
    if p.exists() and json.loads(p.read_text())!=config:raise ValueError('Changed rules: use another output directory')
    ensure_inputs(source,out)
    dump(p,config)
    frozen=dict(files={str(p):sha(p) for p in [source/'signals_before_outcomes.json',source/'inputs/outcome_fifteen.parquet',source/'inputs/outcome_calendar.parquet']+list((out/'inputs').glob('*.parquet'))},
        scripts={str(p):sha(p) for p in [Path(__file__),Path(__file__).with_name('study_second_wave_exits.py')]},frozen_before_this_exit_replay=True)
    manifest_path=out/'FROZEN_EXIT_MANIFEST.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text())!=frozen:raise ValueError('Frozen input or code changed: choose a fresh output directory')
    dump(manifest_path,frozen)
    snapshot=out/'frozen_code';snapshot.mkdir(exist_ok=True)
    for path in frozen['scripts']:(snapshot/Path(path).name).write_bytes(Path(path).read_bytes())
    signals,calendar,frames,bases,theme,limits,excluded=prepare(source,out)
    case_rows=[];case_ledgers=[];case_decisions=[];summaries=[]
    for policy in POLICIES:
        case_dir=out/'isolated'/policy;case_dir.mkdir(parents=True,exist_ok=True)
        for s in signals:
            data=replay(frames,[s],calendar,theme,limits,bases,policy=policy,initial_cash=100000.,isolated=True,excluded=excluded,rules=rules)
            case_rows.extend({**r,**{k:data['summary'][k] for k in ['policy','max_drawdown_pct','reentry_orders','add_orders','partial_orders']}} for r in data['results'])
            case_ledgers.extend({**r,'policy':policy} for r in data['ledger']);case_decisions.extend({**r,'policy':policy} for r in data['decisions'])
        for profile in config['profiles']:
            data=replay(frames,signals,calendar,theme,limits,bases,policy=policy,profile=profile,excluded=excluded,rules=rules)
            q=out/'portfolio'/f'{policy}_{profile}';q.mkdir(parents=True,exist_ok=True)
            data['curve'].to_csv(q/'curve.csv',index=False,encoding='utf-8-sig')
            for name in ('ledger','decisions','issues','results'):
                pd.DataFrame(data[name]).to_csv(q/(name+'.csv'),index=False,encoding='utf-8-sig')
            dump(q/'SUMMARY.json',data['summary']);summaries.append(data['summary'])
        print(json.dumps(dict(policy=policy,elapsed=round(time.time()-started,1),last_portfolio=summaries[-1]),ensure_ascii=False),flush=True)
    cases=pd.DataFrame(case_rows);cases.to_csv(out/'case_results.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(case_ledgers).to_csv(out/'case_ledger.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(case_decisions).to_csv(out/'case_decisions.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(summaries).to_csv(out/'portfolio_summary.csv',index=False,encoding='utf-8-sig')
    stats={}
    for policy,g in cases.groupby('policy',sort=False):
        valid=g[g.buy_orders.gt(0)];stats[policy]=dict(total_signals=len(g),data_excluded=int(g.status.eq('excluded').sum()),unfilled=int(g.status.eq('unfilled').sum()),filled=len(valid),
            mean_pct=float(valid.net_return_pct.mean()),median_pct=float(valid.net_return_pct.median()),worst_pct=float(valid.net_return_pct.min()),best_pct=float(valid.net_return_pct.max()),
            win_rate=float(valid.net_return_pct.gt(0).mean()),non_core_mean_pct=float(valid.loc[~valid.known_core,'net_return_pct'].mean()),
            reentry_orders=int(g.reentry_orders.sum()),add_orders=int(g.add_orders.sum()),
            monthly={month:dict(n=len(h),mean=float(h.net_return_pct.mean()),median=float(h.net_return_pct.median())) for month,h in valid.groupby(valid.signal_time.str[:7])})
    dump(out/'RESULT_SUMMARY.json',dict(case_statistics=stats,portfolio=summaries,elapsed_seconds=round(time.time()-started,1),status='development_replay_complete'))
    print(json.dumps(stats,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',default=str(ROOT/'outputs/second_wave_templates_202601_202603_v4'))
    p.add_argument('--output',default=str(ROOT/'outputs/second_wave_exits_202601_202603_v5'))
    run(p.parse_args())
