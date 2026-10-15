"""Independent pre-2026 theme/trend cohort; keep rejected fixed-pool v1 intact."""
from pathlib import Path
import argparse
import json
import shutil
import numpy as np
import pandas as pd
import joblib
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import log_loss
from replay_trend_pullback import prepare, time_masks, probability, candidates_for, truncate, enrich_summary, save_replay, digest, write_json
from trend_pullback_features import MODEL_FEATURES, EntryRules
from trend_pullback_exit_engine import replay, ExecutionRules

DEFAULT=Path(__file__).resolve().parents[5]/'outputs/trend_pullback_theme_v2'


def freeze_protocol(out):
    grid=[dict(family=f,volume_multiple=v,max_risk_pct=2.5,pullback_max_pct=dd,base_bars=8,base_width_pct=width)
          for f in ['base_break','higher_low','ma_reclaim'] for v in [1.5,2.]
          for dd,width in [(18.,3.),(35.,5.)]]
    value=dict(version=2,selection_as_of='20251231', selection='Pre-2026 daily trend plus historical KPL themes; 20 names, no future winners.',
               train=['20250701','20251130'],calibration=['20251201','20251231'],validation=['20260101','20260331'],temporal_test=['20260401','20260930'],
               entry_grid=grid,entry_context_modes=['attribution','active_or_fact'],policies=['structure','adaptive'],
               model_selection='Calibrate on December only. Deploy variable probabilities only if Jan-Mar multiclass Brier beats constant by >=0.01 AND mean extension probability differs from realized by <=0.10. Otherwise disable variable probability trims and use deterministic structure; never optimize calibration against Apr-Sep.',
               strategy_selection='At least 5 closed development trades, verified held corporate-action accounting, no closed loss below -10%. Prefer positive account net, win>=50%, payoff>=2; otherwise choose highest development net return, disclose unmet targets.',
               new_features='Existing exact outgoing-price slopes, compression ratio, same-clock volume, signed supply balance, price efficiency, breakout retention, known theme/dated factual leads.',
               risk='Trial50%; account risk0.5%; name20% total80%; raw next open costs and per-lot T+1; pending/censored retained.',
               final_test_caveat='Apr-Sep bank results were already seen in rejected v1; new cohort rules frozen without inspecting these theme names\' Apr-Sep outcomes. This is a chronological test, not fully blind.',
               scope_caveat='Snapshot of 20 surviving names, not market-wide generalization. KPL publication versions and original news ingestion times unverified.')
    p=out/'FROZEN_RESEARCH_PROTOCOL.json'
    if p.exists() and json.loads(p.read_text())!=value: raise ValueError('Protocol changed: use a new experiment')
    write_json(p,value); return value


def fit(out,frames,samples,calendar):
    masks=time_masks(samples,calendar)
    train=samples[masks['train']]; cal=samples[masks['calibration']]; val=samples[masks['validation']]
    prior=(train.label.value_counts().reindex([0,1,2],fill_value=0).values+1)/(len(train)+3)
    models={'constant':None}; scores=[]
    if len(train)>=500 and len(cal)>=100 and train.label.nunique()==cal.label.nunique()==3:
        for name,base in [('logistic',make_pipeline(StandardScaler(),LogisticRegression(C=.1,max_iter=1000,random_state=20261002))),
                          ('random_forest',RandomForestClassifier(n_estimators=150,max_depth=4,min_samples_leaf=50,random_state=20261002,n_jobs=2))]:
            base.fit(train[MODEL_FEATURES],train.label)
            models[name]=CalibratedClassifierCV(base,method='sigmoid',cv='prefit').fit(cal[MODEL_FEATURES],cal.label)
    for name,model in models.items():
        for split,part in [('calibration',cal),('validation',val)]:
            if part.empty: continue
            p=np.tile(prior,(len(part),1)) if model is None else model.predict_proba(part[MODEL_FEATURES])
            truth=np.eye(3)[part.label.astype(int)]
            scores.append(dict(model=name,split=split,n=len(part),dates=part.time.str[:10].nunique(),brier=float(np.mean(np.sum((p-truth)**2,axis=1))),
                               logloss=float(log_loss(part.label,p,labels=[0,1,2])),predicted_extend=float(p[:,1].mean()),realized_extend=float((part.label==1).mean())))
    vs=[s for s in scores if s['split']=='validation']; baseline=next(s for s in vs if s['model']=='constant')
    eligible=[s for s in vs if s['model']!='constant' and s['brier'] <= baseline['brier']-.01 and abs(s['predicted_extend']-s['realized_extend'])<=.10]
    selected=min(eligible,key=lambda s:s['brier'])['model'] if eligible else 'constant'
    deployed=bool(eligible); model=models[selected]
    pd.DataFrame(scores).to_csv(out/'MODEL_DEVELOPMENT.csv',index=False)
    write_json(out/'FROZEN_MODEL.json',dict(selected=selected,variable_probability_deployed=deployed,train_rows=len(train),calibration_rows=len(cal),validation_rows=len(val),
                prior=prior.tolist(),features=MODEL_FEATURES,selection='Jan-Mar predictive diagnostics; no Apr-Sep use',
                limitations='64-bar mutually exclusive directional labels, overlapping observations; no claim of calibrated trade success/return.'))
    joblib.dump(dict(model=model,prior=prior,features=MODEL_FEATURES),out/'probability_model.joblib')
    low=train[MODEL_FEATURES].quantile(.005); high=train[MODEL_FEATURES].quantile(.995)
    bounds=['d_ma20','d_ma60','slope20_pct','daily_atr_pct','flat20_change16_pct','coil_pct']
    for code,f in frames.items():
        good=f[MODEL_FEATURES].replace([np.inf,-np.inf],np.nan).notna().all(axis=1)
        p=np.tile(prior,(good.sum(),1)) if model is None else model.predict_proba(f.loc[good,MODEL_FEATURES])
        f['p_fail_pred']=np.nan; f['p_extend_pred']=np.nan
        f.loc[good,'p_fail_pred']=p[:,0];f.loc[good,'p_extend_pred']=p[:,1]
        in_domain=f[bounds].ge(low[bounds],axis=1).all(axis=1)&f[bounds].le(high[bounds],axis=1).all(axis=1)
        f['model_supported']=good&f.daily_trend_ok.eq(True)&in_domain&deployed
        f['n_train']=len(train); f.to_parquet(out/'frames'/f'{code}.parquet',index=False)


def candidates(frames,rules,start,end,mode):
    cs=candidates_for(frames,rules,start,end,False); accepted=[]
    for c in cs:
        r=frames[c['ts_code']].loc[c['index']]
        # Attribution is literal dated KPL membership; it does not establish
        # causal economic exposure. Strict mode additionally requires evidence.
        if not r.theme_known: continue
        if mode=='active_or_fact' and not (c['theme_heat']>=40 and c['theme_width']>=2 or c['logic_evidence_known']): continue
        accepted.append(c)
    return accepted


def run(out=DEFAULT,stage='prepare'):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);protocol=freeze_protocol(out)
    if stage=='prepare':
        f,s,c=prepare(out);fit(out,f,s,c);return
    pool=pd.read_csv(out/'observation_pool.csv');frames={c:pd.read_parquet(out/'frames'/f'{c}.parquet') for c in pool.ts_code}
    lim=pd.read_parquet(out/'inputs/limits.parquet'); lim['trade_date']=lim.trade_date.astype(str)
    limits={(r.ts_code,r.trade_date):dict(up_limit=r.up_limit,down_limit=r.down_limit) for r in lim.itertuples()}
    if stage=='development':
        ff=truncate(frames,'20260101','20260331'); grid=[]
        for i,spec in enumerate(protocol['entry_grid']):
            rules=EntryRules(**spec)
            for mode in protocol['entry_context_modes']:
                cs=candidates(frames,rules,'20260101','20260331',mode)
                for policy in protocol['policies']:
                    result=replay(ff,cs,limits,probability_fn=probability,policy=policy)
                    s=enrich_summary(result);s.update(grid_id=i,rules=spec,context_mode=mode,candidate_count=len(cs))
                    grid.append(s);save_replay(out/'development'/f'g{i}_{mode}_{policy}',result,cs)
                    print('development',i,mode,policy,len(cs),s['net_return'],s['win_rate'],s['payoff_ratio'],s['cash_accounting_verified'],flush=True)
        write_json(out/'DEVELOPMENT_GRID.json',grid)
        accepted=[s for s in grid if s['closed_trades']>=5 and s['cash_accounting_verified'] and s['worst_trade']>=-.10]
        if not accepted:
            write_json(out/'SELECTION_BLOCKED.json',dict(reason='Minimum trade count, loss tail or actual cash accounting failed'));return
        ideal=[s for s in accepted if s['net_return']>0 and s['win_rate']>=.5 and (s['payoff_ratio'] or 0)>=2]
        selected=max(ideal or accepted,key=lambda s:s['net_return'])
        scripts=[Path(__file__),Path(__file__).with_name('replay_trend_pullback.py'),Path(__file__).with_name('trend_pullback_features.py'),Path(__file__).with_name('trend_pullback_exit_engine.py'),Path(__file__).with_name('trend_pullback_theme_context.py')]
        source=out/'frozen_source';source.mkdir(exist_ok=True)
        for p in scripts: shutil.copy2(p,source/p.name)
        write_json(out/'FROZEN_STRATEGY.json',dict(selected=selected,development_targets_met=bool(ideal),source_hashes={p.name:digest(p) for p in scripts},selected_before_temporal_test=True))
    if stage=='test':
        selection=json.loads((out/'FROZEN_STRATEGY.json').read_text());best=selection['selected']
        for name,expected in selection['source_hashes'].items():
            if digest(Path(__file__).with_name(name))!=expected:raise ValueError('Source changed after freeze')
        if (out/'selected/holdout/SUMMARY.json').exists():raise ValueError('Already tested; no return-driven retest')
        rules=EntryRules(**best['rules'])
        for split,start,end in [('validation','20260101','20260331'),('holdout','20260401','20260930')]:
            cs=candidates(frames,rules,start,end,best['context_mode']);ff=truncate(frames,start,end)
            for policy in [best['policy']]+[p for p in ['structure','adaptive','ma30'] if p!=best['policy']]:
                r=replay(ff,cs,limits,probability_fn=probability,policy=policy)
                folder=out/'selected'/split if policy==best['policy'] else out/'controls'/policy/split
                save_replay(folder,r,cs); print(split,policy,enrich_summary(r),flush=True)
            # Allocation-matched trial-only MA30 comparator. No confirmation adds.
            r=replay(ff,cs,limits,policy='ma30',max_name_pct=.10,max_total_pct=.80,rules=ExecutionRules(account_risk_pct=.0025))
            save_replay(out/'diagnostics/ma30_trial_only'/split,r,cs)
        samples=pd.read_parquet(out/'probability_samples.parquet');calendar=pd.read_parquet(out/'inputs/calendar.parquet').cal_date.astype(str).tolist()
        test=samples[time_masks(samples,calendar)['temporal_test']];model=joblib.load(out/'probability_model.joblib')
        p=np.tile(model['prior'],(len(test),1)) if model['model'] is None else model['model'].predict_proba(test[MODEL_FEATURES]);truth=np.eye(3)[test.label.astype(int)]
        write_json(out/'MODEL_TEMPORAL_METRICS.json',dict(n=len(test),dates=test.time.str[:10].nunique(),brier=float(np.mean(np.sum((p-truth)**2,axis=1))),
                   constant_brier=float(np.mean(np.sum((model['prior']-truth)**2,axis=1))),logloss=float(log_loss(test.label,p,labels=[0,1,2]))))
        pd.DataFrame(dict(ts_code=test.ts_code,time=test.time,label=test.label,p_fail=p[:,0],p_extend=p[:,1])).to_csv(out/'MODEL_TEMPORAL_TEST.csv',index=False)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=DEFAULT);p.add_argument('--stage',choices=['prepare','development','test'],default='prepare')
    args=p.parse_args();run(args.output,args.stage)
