"""Monthly, point-in-time theme/trend pools; no retrospective pool ownership."""
from pathlib import Path
import argparse,json,shutil
import pandas as pd
import numpy as np
import joblib
from sklearn.metrics import log_loss
from replay_trend_pullback import prepare, time_masks, probability, truncate, enrich_summary, save_replay, digest, write_json
from replay_theme_trend import fit, candidates
from trend_pullback_features import EntryRules, MODEL_FEATURES
from trend_pullback_exit_engine import replay,ExecutionRules

DEFAULT=Path(__file__).resolve().parents[5]/'outputs/trend_pullback_monthly_v3'


def freeze(out):
    grid=[dict(family=f,volume_multiple=v,max_risk_pct=2.5,pullback_max_pct=dd,base_bars=8,base_width_pct=width)
          for f in ['base_break','higher_low','ma_reclaim'] for v in [1.5,2.]
          for dd,width in [(18.,3.),(35.,5.)]]
    p=dict(version=3,selection='At most20 names at each preceding month-end; strict rising daily MAs plus historical literal KPL; themecap5 industrycap3; no future return choice.',
           training_sample_ownership='A probability origin enters a split only if the name was actually in that origin month\'s historical pool; never train on early data of future-added names.',
           train=['20250701','20251130'],calibration=['20251201','20251231'],validation=['20260101','20260331'],temporal_test=['20260401','20260930'],
           grid=grid,context_modes=['attribution','active_or_fact'],policies=['structure','adaptive'],
           model_gate='Same v2: independent December sigmoid fit, Jan-Mar Brier beat constant>=0.01 and mean extension error<=0.10, else probability exits disabled.',
           selection_rule='At least5 closed validation trades, verified actual held corporate actions, no closed loss worse than10%; prefer win>=50%, payoff>=2 and positive cash-account return; otherwise disclose fallback best development account return.',
           position_rule='Entry only during actual current monthly ownership; existing holdings may continue across month boundaries; no forced calendar exit. Per-lot T+1 and priced next-open proxy.',
           hindsight_caveat='Previous fixed-pool experiments used Apr-Sep already. Refresh policy is based on pool staleness and frozen before reading the new universe outcomes; not a pristine untouched test.',
           limitation='Current surviving metadata, KPL publication history and news original ingestion time unavailable; conservative quoted fills cannot prove live execution.')
    path=out/'FROZEN_RESEARCH_PROTOCOL.json'
    if path.exists() and json.loads(path.read_text())!=p:raise ValueError('Frozen protocol differs')
    write_json(path,p);return p


def read_pools(out):
    manifest=json.loads((out/'OBSERVATION_SELECTION.json').read_text())
    expected=manifest['selection_sha256']['MONTHLY_POOLS.csv']
    if digest(out/'MONTHLY_POOLS.csv')!=expected:raise ValueError('Frozen monthly ownership changed')
    p=pd.read_csv(out/'MONTHLY_POOLS.csv',dtype={'ts_code':str,'month':str})
    # Normalize YYYYMM and YYYY-MM to one membership key.
    p['month']=p.month.str.replace('-','',regex=False).str[:6]
    if p.duplicated(['month','ts_code']).any():raise ValueError('Duplicate monthly membership')
    asof=p.as_of.astype(str); active=p.active_start.astype(str)
    if not (asof<active).all():raise ValueError('Selection cutoff reaches active month')
    return p


def validate_inputs(out):
    snapshot=json.loads((out/'INPUT_SNAPSHOT.json').read_text())
    if digest(out/'OBSERVATION_SELECTION.json')!=snapshot['selection_manifest_sha256']:
        raise ValueError('Selection manifest changed')
    for name,expected in snapshot['input_hashes'].items():
        if digest(out/'inputs'/name)!=expected:raise ValueError(f'Input snapshot changed: {name}')


def monthly_candidates(frames,rules,start,end,mode,pools):
    accepted=[]
    for month,group in pools.groupby('month'):
        first=pd.Timestamp(month+'01');last=first+pd.offsets.MonthEnd(0)
        s=max(first,pd.Timestamp(start));e=min(last,pd.Timestamp(end))
        if s>e:continue
        available={c:frames[c] for c in group.ts_code.unique() if c in frames}
        accepted.extend(candidates(available,rules,s.strftime('%Y%m%d'),e.strftime('%Y%m%d'),mode))
    return sorted(accepted,key=lambda c:(c['signal_time'],c['ts_code']))


def compact(frames):
    cols=['datetime','index','valid','open','high','low','close','vol','scale',
          'p_open','p_high','p_low','p_close','ma5','ma10','ma20','ma30','ma60','ma120','ma250',
          'atr14','volume_multiple8','p_fail_pred','p_extend_pred','model_supported','n_train']
    return {c:f[[k for k in cols if k in f]].copy() for c,f in frames.items()}


def run(out=DEFAULT,stage='prepare'):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);p=freeze(out)
    pools=read_pools(out); validate_inputs(out)
    if stage=='prepare':
        frames,samples,calendar=prepare(out)
        owners=set(zip(pools.ts_code,pools.month))
        mask=[(c,pd.Timestamp(t).strftime('%Y%m')) in owners for c,t in zip(samples.ts_code,samples.time)]
        excluded=len(samples)-sum(mask);samples=samples[mask].copy()
        samples.to_parquet(out/'probability_samples.parquet',index=False)
        write_json(out/'PROBABILITY_OWNERSHIP_AUDIT.json',dict(monthly_membership_only=True,kept_rows=len(samples),excluded_unowned_rows=excluded))
        fit(out,frames,samples,calendar)
        for code,f in frames.items():
            f['model_supported'] &= f.datetime.dt.strftime('%Y%m').map(lambda month:(code,month) in owners)
            f.to_parquet(out/'frames'/f'{code}.parquet',index=False)
        return
    codes=pd.read_csv(out/'observation_pool.csv').ts_code.unique(); frames={c:pd.read_parquet(out/'frames'/f'{c}.parquet') for c in codes}
    lim=pd.read_parquet(out/'inputs/limits.parquet');lim['trade_date']=lim.trade_date.astype(str)
    limits={(r.ts_code,r.trade_date):dict(up_limit=r.up_limit,down_limit=r.down_limit) for r in lim.itertuples()}
    owners=set(zip(pools.ts_code,pools.month))
    allow=lambda code,stamp:(code,pd.Timestamp(stamp).strftime('%Y%m')) in owners
    if stage=='development':
        # Truncate to names actually observed in this split, including warm
        # probability features already computed from their genuine past.
        names=set(pools[pools.month.between('202601','202603')].ts_code);ff=compact(truncate({c:f for c,f in frames.items() if c in names},'20260101','20260331'))
        grid=[]
        for i,spec in enumerate(p['grid']):
            rules=EntryRules(**spec)
            for mode in p['context_modes']:
                cs=monthly_candidates(frames,rules,'20260101','20260331',mode,pools)
                for policy in p['policies']:
                    r=replay(ff,cs,limits,probability_fn=probability,policy=policy,entry_eligibility_fn=allow);s=enrich_summary(r)
                    s.update(grid_id=i,rules=spec,context_mode=mode,candidate_count=len(cs));grid.append(s)
                    save_replay(out/'development'/f'g{i}_{mode}_{policy}',r,cs)
                    print('development',i,mode,policy,len(cs),s['net_return'],s['win_rate'],s['payoff_ratio'],s['worst_trade'],s['cash_accounting_verified'],flush=True)
        write_json(out/'DEVELOPMENT_GRID.json',grid)
        accepted=[s for s in grid if s['closed_trades']>=5 and s['cash_accounting_verified'] and s['worst_trade']>=-.10]
        if not accepted:write_json(out/'SELECTION_BLOCKED.json',dict(reason='No valid combination'));return
        ideal=[s for s in accepted if s['net_return']>0 and s['win_rate']>=.5 and (s['payoff_ratio'] or 0)>=2]
        best=max(ideal or accepted,key=lambda s:s['net_return'])
        scripts=[Path(__file__).with_name(n) for n in ['replay_monthly_trend.py','replay_theme_trend.py','replay_trend_pullback.py','trend_pullback_features.py','trend_pullback_exit_engine.py','trend_pullback_theme_context.py','study_ma250_reclaim.py','study_daily_intraday_pullback.py','study_fifteen_min_ma_coil.py']]
        source=out/'frozen_source';source.mkdir(exist_ok=True)
        for f in scripts:shutil.copy2(f,source/f.name)
        artifacts=list((out/'frames').glob('*.parquet'))+[out/'FROZEN_MODEL.json',out/'probability_model.joblib',out/'probability_samples.parquet']
        write_json(out/'FROZEN_STRATEGY.json',dict(selected=best,development_targets_met=bool(ideal),selected_before_temporal_test=True,source_hashes={f.name:digest(f) for f in scripts},artifact_hashes={str(f.relative_to(out)):digest(f) for f in artifacts}))
        return
    if stage=='test':
        selection=json.loads((out/'FROZEN_STRATEGY.json').read_text());best=selection['selected']
        for name,expected in selection['source_hashes'].items():
            if digest(Path(__file__).with_name(name))!=expected:raise ValueError('Frozen source differs')
        for relative,expected in selection['artifact_hashes'].items():
            if digest(out/relative)!=expected:raise ValueError(f'Frozen computed artifact changed: {relative}')
        if (out/'selected/holdout/SUMMARY.json').exists():raise ValueError('Already tested')
        rules=EntryRules(**best['rules'])
        for split,start,end in [('validation','20260101','20260331'),('holdout','20260401','20260930')]:
            names=set(pools[pools.month.between(start[:6],end[:6])].ts_code);ff=compact(truncate({c:f for c,f in frames.items() if c in names},start,end))
            cs=monthly_candidates(frames,rules,start,end,best['context_mode'],pools)
            for policy in [best['policy']]+[z for z in ['structure','adaptive','ma30'] if z!=best['policy']]:
                r=replay(ff,cs,limits,probability_fn=probability,policy=policy,entry_eligibility_fn=allow)
                folder=out/'selected'/split if policy==best['policy'] else out/'controls'/policy/split
                save_replay(folder,r,cs);print(split,policy,enrich_summary(r),flush=True)
            r=replay(ff,cs,limits,policy='ma30',max_name_pct=.10,rules=ExecutionRules(account_risk_pct=.0025),entry_eligibility_fn=allow)
            save_replay(out/'diagnostics/ma30_trial_only'/split,r,cs)
        samples=pd.read_parquet(out/'probability_samples.parquet');calendar=pd.read_parquet(out/'inputs/calendar.parquet').cal_date.astype(str).tolist();test=samples[time_masks(samples,calendar)['temporal_test']]
        model=joblib.load(out/'probability_model.joblib');prob=np.tile(model['prior'],(len(test),1)) if model['model'] is None else model['model'].predict_proba(test[MODEL_FEATURES]);truth=np.eye(3)[test.label.astype(int)]
        write_json(out/'MODEL_TEMPORAL_METRICS.json',dict(n=len(test),dates=test.time.str[:10].nunique(),brier=float(np.mean(np.sum((prob-truth)**2,axis=1))),constant_brier=float(np.mean(np.sum((model['prior']-truth)**2,axis=1))),logloss=float(log_loss(test.label,prob,labels=[0,1,2]))))
        pd.DataFrame(dict(ts_code=test.ts_code,time=test.time,label=test.label,p_fail=prob[:,0],p_extend=prob[:,1])).to_csv(out/'MODEL_TEMPORAL_TEST.csv',index=False)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=DEFAULT);p.add_argument('--stage',choices=['prepare','development','test'],default='prepare');args=p.parse_args();run(args.output,args.stage)
