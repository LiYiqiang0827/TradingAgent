"""Resumable January-March development scan; outcomes are a separate stage."""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib,json,time
from pathlib import Path
import sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[5]
sys.path[:0]=[str(ROOT),str(ROOT/'offlineDataManager/scripts')]
from coreClient.data_provider import get_day,get_adj_factor,get_kpl_list,get_tradecal,get_stk_limit,get_fifteenMin
from study_daily_intraday_pullback import prepare_daily,revised_rules
from replay_daily_intraday_pullback import board_events
from study_fifteen_min_ma_coil import features,Rules as CoilRules
from study_second_wave_template import TemplateRules,core_rules,daily_setup,find_signals,outcome_path,theme_snapshot

def dump(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False),encoding='utf-8')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def run(args):
    out=Path(args.output);inputs=out/'inputs';inputs.mkdir(parents=True,exist_ok=True)
    rules=core_rules() if args.profile=='core' else TemplateRules();config=dict(rules=asdict(rules),first=args.first,last=args.last,
        success_definition=dict(primary='25% observed high within 20 entry sessions, before any completed-bar close below frozen floor',
            sustained_diagnostic='primary plus at least three daily closes >= entry +15%',secondary_targets=[25,30],windows=[10,20],
            no_profit_or_full_sell_strategy_claim=True),core_examples=['688137.SH 20260914','002413.SZ 20251225','002810.SZ 20260116'],
        universe='historical KPL qualifying boards only; historical names; >=250 prior observed sessions; ST/IPO themes excluded',
        execution='next native TDX 15m bar open proxy; one-price next bar assumed unfilled; never decision close')
    path=out/'FROZEN_RULES.json'
    if path.exists() and json.loads(path.read_text())!=json.loads(json.dumps(config)):
        raise ValueError('Rules changed: use a fresh output directory')
    dump(path,config);started=time.time()
    def progress(stage,**kw):
        value=dict(stage=stage,elapsed_seconds=round(time.time()-started,1),**kw);dump(out/'status.json',value);print(json.dumps(value,ensure_ascii=False),flush=True)
    def cached(name,fn):
        p=inputs/(name+'.parquet')
        if p.exists():return pd.read_parquet(p)
        d=fn();d.to_parquet(p,index=False);progress('input_saved',name=name,rows=len(d));return d
    cal=cached('calendar',lambda:get_tradecal(start_date='20240901',end_date=args.last,source='database_only'))
    calendar=sorted(cal.cal_date.astype(str).unique()); index={d:i for i,d in enumerate(calendar)}
    kpl=cached('kpl',lambda:get_kpl_list(start_date='20251001',end_date=args.last,tags='涨停',source='database_only'))
    usable=kpl[kpl.ts_code.str.endswith(('.SZ','.SH'))&~kpl.name.str.contains('ST|退',case=False,na=False)&~kpl.lu_desc.str.contains('ST|次新',case=False,na=False)].copy()
    codes=sorted(usable.ts_code.unique())
    raw=cached('daily_raw',lambda:get_day(ts_codes=codes,start_date='20240901',end_date=args.last,qfq=False,source='database_only'))
    adj=cached('daily_adj',lambda:get_adj_factor(ts_codes=codes,start_date='20240901',end_date=args.last,source='database_only'))
    caps=cached('limits',lambda:get_stk_limit(ts_codes=codes,start_date='20251001',end_date=args.last,source='database_only'))
    events=board_events(usable,revised_rules(),raw,caps,calendar)
    qualified=sorted(events.loc[events.qualifying_board.ge(2),'ts_code'].unique())
    dump(out/'UNIVERSE.json',dict(all_kpl_rows=len(kpl),non_excluded_kpl_rows=len(usable),limit_up_stocks=len(codes),qualifying_stocks=len(qualified)))
    dates=[d for d in calendar if args.first<=d<=args.last]
    if args.stage in ('daily','all'):
        records=[];rg={c:g for c,g in raw.groupby('ts_code')};ag={c:g for c,g in adj.groupby('ts_code')};eg={c:g for c,g in events.groupby('ts_code')}
        for n,code in enumerate(qualified,1):
            if code not in rg or code not in ag:continue
            d=prepare_daily(rg[code],ag[code],calendar)
            for signal_day in dates:
                prior=calendar[index[signal_day]-1]
                r=daily_setup(d,eg[code],prior,rules);records.append(dict(ts_code=code,signal_day=signal_day,**r))
            if n%25==0:progress('daily_pool',processed=n,total=len(qualified),eligible_days=sum(r['eligible'] for r in records))
        audit=pd.DataFrame(records);audit.to_csv(out/'daily_audit.csv',index=False,encoding='utf-8-sig')
        yes=audit[audit.eligible];yes.to_csv(out/'daily_candidates.csv',index=False,encoding='utf-8-sig')
        rejects=Counter(audit.loc[~audit.eligible,'reason']);dump(out/'daily_reject_reasons.json',dict(rejects))
        progress('daily_complete',audited_stocks=len(qualified),stock_days=len(audit),eligible_days=len(yes),eligible_stocks=yes.ts_code.nunique(),common_rejects=rejects.most_common(10))
        if args.stage=='daily':return
    yes=pd.read_csv(out/'daily_candidates.csv',dtype={'ts_code':str,'signal_day':str,'context_date':str,'wave_start_date':str,'peak_date':str})
    minute_calendar=[d for d in calendar if d>='20251103']
    wanted=sorted(yes.ts_code.unique())
    if args.stage in ('intraday','all'):
        bars=cached('fifteen_signal',lambda:get_fifteenMin(ts_codes=wanted,start_date='20251103',end_date=args.last)) if wanted else pd.DataFrame()
        signals=[];coverage=[];prefix_checks=0
        for n,code in enumerate(wanted,1):
            g=bars[bars.ts_code.eq(code)] if len(bars) else bars
            if g.empty:coverage.append(dict(ts_code=code,status='all_15m_missing',candidate_days=int(yes.ts_code.eq(code).sum())));continue
            f=features(g,minute_calendar,CoilRules());context={str(r['signal_day']):r for r in yes[yes.ts_code.eq(code)].to_dict('records')}
            found=find_signals(f,context,args.first,args.last,rules)
            for s in found:
                cutoff=pd.Timestamp(s['signal_time']);pf=features(g[pd.to_datetime(g.datetime).le(cutoff)],minute_calendar,CoilRules())
                test=find_signals(pf,context,args.first,args.last,rules)
                expected=[t for t in found if pd.Timestamp(t['signal_time'])<=cutoff]
                assert json.dumps(test,sort_keys=True,default=str)==json.dumps(expected,sort_keys=True,default=str)
                prefix_checks+=1
                with_code={**s,"ts_code":code}
                signals.append({**with_code,**theme_snapshot(kpl,with_code,calendar)})
            considered=f[f.datetime.dt.strftime('%Y%m%d').isin(context)]
            coverage.append(dict(ts_code=code,status='observed',candidate_days=len(context),expected_candidate_bars=len(context)*16,valid_candidate_bars=int(considered.valid_bar.sum()),signals=len(found)))
            if n%5==0:progress('intraday',processed=n,total=len(wanted),signals=len(signals))
        signals.sort(key=lambda r:(r['signal_time'],r['ts_code']))
        dump(out/'signals_before_outcomes.json',signals)
        flat=[{**{k:v for k,v in s.items() if k!='base'},**{'base_'+k:v for k,v in s['base'].items()}} for s in signals]
        pd.DataFrame(flat).to_csv(out/'signals_before_outcomes.csv',index=False,encoding='utf-8-sig')
        pd.DataFrame(coverage).to_csv(out/'intraday_coverage.csv',index=False,encoding='utf-8-sig')
        frozen=dict(stage='signals_frozen_before_outcomes',known_development_data=True,prefix_checks_passed=prefix_checks,signal_count=len(signals),
            files={str(p.relative_to(out)):sha(p) for p in list(inputs.glob('*.parquet'))+[out/'FROZEN_RULES.json',out/'signals_before_outcomes.json']},
            scripts={p.name:sha(p) for p in [Path(__file__),Path(__file__).with_name('study_second_wave_template.py')]})
        dump(out/'FROZEN_SIGNAL_MANIFEST.json',frozen);progress('signals_frozen',signals=len(signals),prefix_checks=prefix_checks)
        if args.stage=='intraday':return
    if args.stage in ('evaluate','all'):
        frozen=json.loads((out/'FROZEN_SIGNAL_MANIFEST.json').read_text())
        assert all(sha(out/p)==v for p,v in frozen['files'].items())
        assert all(sha(Path(__file__).with_name(p))==v for p,v in frozen['scripts'].items())
        signals=json.loads((out/'signals_before_outcomes.json').read_text());needed=sorted({s['ts_code'] for s in signals})
        ocal=cached('outcome_calendar',lambda:get_tradecal(start_date='20251103',end_date='20260515',source='database_only'))
        oc=sorted(ocal.cal_date.astype(str).unique())
        bars=cached('outcome_fifteen',lambda:get_fifteenMin(ts_codes=needed,start_date='20251103',end_date='20260515')) if needed else pd.DataFrame()
        frames={c:features(g,oc,CoilRules()) for c,g in bars.groupby('ts_code')} if len(bars) else {}
        results=[]
        for s in signals:
            outcome=outcome_path(frames[s['ts_code']],s,oc) if s['ts_code'] in frames else dict(evaluable=False,execution_status='all_missing')
            results.append({**{k:v for k,v in s.items() if k!='base'},**outcome})
        df=pd.DataFrame(results);df.to_csv(out/'outcomes.csv',index=False,encoding='utf-8-sig');dump(out/'outcomes.json',results)
        stats={}
        for label,rows in [('all',df)]+[(m,df[df.signal_time.str.startswith(m)]) for m in ['2026-01','2026-02','2026-03']] if len(df) else [('all',df)]:
            valid=rows[rows.evaluable.eq(True)] if len(rows) else rows
            n=len(valid);counts={}
            for h in (10,20):
                for target in (25,30):
                    for method in ['reached','intact']:
                        key=f'{method}_{target}_{h}d';counts[key]=dict(success=int(valid[key].sum()) if n else 0,total_signals=len(rows),evaluable=n,rate_evaluable=float(valid[key].mean()) if n else None)
            if n:
                key='intact_25_20d';p=float(valid[key].mean());z=1.96;den=1+z*z/n;center=(p+z*z/(2*n))/den;half=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
                counts['primary_wilson95']=[float(center-half),float(center+half)]
            stats[label]=dict(signals=len(rows),evaluable=n,execution_status=dict(Counter(rows.execution_status)) if len(rows) else {},counts=counts,
                median_mfe20=float(valid.mfe_20d_pct.median()) if n else None,median_mae20=float(valid.mae_20d_pct.median()) if n else None,
                worst_mae20=float(valid.mae_20d_pct.min()) if n else None,sustained25=int(valid.sustained_intact_25_20d.sum()) if n else 0)
        dump(out/'RESULT_SUMMARY.json',stats);progress('evaluation_complete',signals=len(df),summary=stats)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--first',default='20260101');p.add_argument('--last',default='20260331')
    p.add_argument('--stage',choices=['daily','intraday','evaluate','all'],default='daily');p.add_argument('--output',default=str(ROOT/'outputs/second_wave_templates_202601_202603_v3'))
    p.add_argument('--profile',choices=['broad','core'],default='broad')
    run(p.parse_args())
