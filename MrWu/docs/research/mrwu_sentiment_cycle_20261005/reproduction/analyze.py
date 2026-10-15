"""Rebuild descriptive research tables from frozen formal daily inputs."""
from pathlib import Path
import argparse, sys, json
import numpy as np
import pandas as pd

CODE=Path(__file__).resolve().parents[4]/'policyStudy'/'policy'/'speculation_sentiment_lite'
sys.path.insert(0,str(CODE))
from cycle import compute_cycle, PHASES
from score import score_raw

def save(f,out,name): f.to_csv(out/name,index=False,encoding='utf-8-sig')

def transitions(f,col,valid):
    rows=[]
    for a in valid:
        mask=f[col].eq(a)&f[col].shift(-1).isin(valid)
        n=int(mask.sum())
        for b in valid:
            k=int((mask&f[col].shift(-1).eq(b)).sum())
            rows.append({'from':a,'to':b,'count':k,'denominator':n,'probability':k/n if n else np.nan})
    return pd.DataFrame(rows)

def runs(f,col,valid):
    rows=[];g=f[col].ne(f[col].shift()).cumsum()
    for _,part in f.groupby(g):
        value=part[col].iloc[0]
        if value not in valid:continue
        i,j=part.index.min(),part.index.max()
        rows.append({'kind':col,'state':value,'start_date':part.trade_date.iloc[0],'end_date':part.trade_date.iloc[-1],'duration':len(part),'completed':bool(j<len(f)-1 and f[col].iloc[j+1] in valid),'left_censored':i==0,'right_censored':j==len(f)-1 or f[col].iloc[j+1] not in valid})
    return pd.DataFrame(rows)

def main():
    p=argparse.ArgumentParser();p.add_argument('--formal',type=Path,required=True);p.add_argument('--theme',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--human-source',type=Path);p.add_argument('--human-review',type=Path)
    a=p.parse_args();o=a.output;o.mkdir(parents=True,exist_ok=True)
    full=pd.read_csv(a.formal/'sentiment_daily.csv',float_precision='round_trip');full.trade_date=pd.to_datetime(full.trade_date).dt.strftime('%Y-%m-%d');full=full.sort_values('trade_date').reset_index(drop=True)
    assert full.trade_date.is_unique
    cycle=compute_cycle(full)
    f=full[full.trade_date.between('2026-01-01','2026-09-30')].copy().reset_index(drop=True)
    c=cycle[cycle.trade_date.between('2026-01-01','2026-09-30')].copy().reset_index(drop=True)
    save(c,o,'cycle_daily.csv'); save(f,o,'sentiment_daily.csv')
    theme=pd.read_csv(a.theme/'theme_context_daily.csv');theme.trade_date=pd.to_datetime(theme.trade_date).dt.strftime('%Y-%m-%d');assert theme.trade_date.is_unique
    all_env=full.merge(cycle.drop(columns=['score5','delta1','delta5']),on='trade_date',how='left',validate='one_to_one').merge(theme,on='trade_date',how='left',validate='one_to_one')
    env=all_env[all_env.trade_date.between('2026-01-01','2026-09-30')].copy().reset_index(drop=True)
    env['theme_available']=env.theme_heat_score.notna();env['theme_quality']=env.theme_quality.fillna('missing_theme_source');env['month']=env.trade_date.str[:7]
    save(env,o,'environment_daily.csv');save(theme[theme.trade_date.between('2026-01-01','2026-09-30')],o,'theme_context_daily.csv')
    ranks=pd.read_csv(a.theme/'theme_rank_daily.csv');ranks.trade_date=pd.to_datetime(ranks.trade_date).dt.strftime('%Y-%m-%d');save(ranks[ranks.trade_date.between('2026-01-01','2026-09-30')],o,'theme_rank_daily.csv')
    monthly=[];modules=[]
    for month,g in env.groupby('month'):
        r={'month':month,'calendar_n':len(g),'score_valid_n':int(g.score5.notna().sum()),'score_mean':g.score5.mean(),'theme_valid_n':int(g.theme_available.sum()),'score_median':g.score5.median()}
        for quality in ['OK','PARTIAL','UNKNOWN']:r['quality_'+quality.lower()+'_n']=int(g.quality_status.eq(quality).sum())
        for grade in ['S','A','B','C','D']:
            r[grade+'_n']=int(g.grade5.eq(grade).sum());r[grade+'_pct']=r[grade+'_n']/r['score_valid_n'] if r['score_valid_n'] else np.nan
        monthly.append(r)
        for m in ['M1','M2','M3','M4','M5']:modules.append({'month':month,'module':m,'mean':g[m].mean(),'valid_n':int(g[m].notna().sum())})
    save(pd.DataFrame(monthly),o,'monthly_summary.csv');save(pd.DataFrame(modules),o,'monthly_modules.csv')
    save(transitions(env,'grade5',['S','A','B','C','D']),o,'grade_transitions.csv');save(transitions(env,'phase',PHASES),o,'phase_transitions.csv')
    run_table=pd.concat([runs(env,'grade5',['S','A','B','C','D']),runs(env,'phase',PHASES)],ignore_index=True);save(run_table,o,'runs.csv')
    eventrows=[]
    # An event and its outcomes are separate. Do not use future score to set labels.
    q90=json.loads((a.formal/'calibration.json').read_text(encoding='utf-8-sig'))['panic_ddens_p90']
    masks={'panic':env.panic.eq(True),'high_down_density':(env.limit_down_n/env.eligible_n*1000>=q90)&(env.limit_down_n>0),'repair':env.phase.isin(['修复','启动候选'])}
    event_summary=[]
    for name,mask in masks.items():
        clusters=(mask&~mask.shift(fill_value=False)).cumsum();cluster_n=int((mask&~mask.shift(fill_value=False)).sum())
        for i in env.index[mask]:
            r={'event':name,'trade_date':env.trade_date.iloc[i],'score_t':env.score5.iloc[i],'phase_t':env.phase.iloc[i],'cluster_id':int(clusters.iloc[i]),'independent_consecutive_clusters':cluster_n}
            for h in [1,3,5]:
                complete=i+h<len(env) and env.score5.iloc[i:i+h+1].notna().all()
                r[f'complete_{h}']=bool(complete);r[f'score_t{h}']=env.score5.iloc[i+h] if complete else np.nan;r[f'delta_t{h}']=r[f'score_t{h}']-r['score_t'] if complete else np.nan
            eventrows.append(r)
        event_summary.append({'event':name,'event_n':int(mask.sum()),'consecutive_cluster_n':cluster_n})
    events=pd.DataFrame(eventrows);save(events,o,'event_windows.csv');save(pd.DataFrame(event_summary),o,'event_summary.csv')
    # Same score ranges, different directions: descriptive structure, not prediction.
    env['score_band']=pd.cut(env.score5,[0,35,50,65,80,101],right=False,labels=['0-35','35-50','50-65','65-80','80-100'])
    same=env.groupby(['score_band','direction'],observed=True).agg(n=('score5','count'),score_mean=('score5','mean'),M1=('M1','mean'),M2=('M2','mean'),M3=('M3','mean'),M4=('M4','mean'),M5=('M5','mean')).reset_index();save(same,o,'same_score_direction.csv')
    # Descriptive lead/lag only. M4 and H are ingredients of score5, so same-day
    # association is partly mechanical and is never a predictive validation.
    leadlag=[]
    for feature in ['M4','H_score']:
        if feature not in all_env:continue
        change=all_env[feature].diff()
        for h in [-1,0,1,3]:
            outcome=all_env.score5.diff().shift(-h)
            paired=pd.DataFrame({'x':change,'y':outcome})[all_env.trade_date.between('2026-01-01','2026-09-30')].dropna()
            leadlag.append({'feature_change_at_t':feature,'score_change_offset':h,'paired_n':len(paired),'pearson':paired.x.corr(paired.y),'interpretation':'shared_formula_descriptive_not_prediction'})
    save(pd.DataFrame(leadlag),o,'feedback_lead_lag.csv')
    cross=env.groupby(['grade5','structure'],dropna=False).agg(n=('trade_date','size'),theme_heat_mean=('theme_heat_score','mean'),score_mean=('score5','mean')).reset_index();save(cross,o,'theme_emotion_cross.csv')
    comparable=env[env.theme_available&env.score5.notna()].copy();comparable['heat_score_gap']=comparable.theme_heat_score-comparable.score5
    save(comparable.sort_values('heat_score_gap',ascending=False)[['trade_date','score5','grade5','phase','theme_heat_score','heat_score_gap','structure','top3_themes']],o,'divergence_days.csv')
    rotations=[]
    for mo,g in env.groupby('month'):
        observed=g[g.theme_available]
        top=observed.top3_themes.fillna('').str.split(' / ').str[0]
        # Only adjacent dates in the full calendar, avoid bridging absent theme days.
        nxt=env.top3_themes.fillna('').str.split(' / ').str[0].shift()
        mask=(env.index.isin(observed.index))&env.theme_available&env.theme_available.shift(fill_value=False)
        changes=(env.top3_themes.fillna('').str.split(' / ').str[0]!=nxt)&mask
        rotations.append({'month':mo,'theme_valid_n':len(observed),'top1_distinct_n':top[top!=''].nunique(),'adjacent_observed_n':int(mask.sum()),'top1_replacements_n':int(changes.sum()),'dominant_theme':top.mode().iloc[0] if len(top) else '', 'structure_replacement_days':int(observed.structure.eq('mainline_replacement').sum())})
    save(pd.DataFrame(rotations),o,'monthly_rotation.csv')
    # Describe coincidence with continuation/replacement, without causality.
    top1=env.top3_themes.fillna('').str.split(' / ').str[0]
    adjacent=env.theme_available&env.theme_available.shift(fill_value=False)
    env['top1_changed']=top1.ne(top1.shift())&adjacent
    direction_context=[]
    for direction in ['上','平','下']:
        g=env[env.direction.eq(direction)&env.theme_available]
        n_adj=int((env.direction.eq(direction)&adjacent).sum())
        direction_context.append({'direction':direction,'theme_observed_n':len(g),'adjacent_observed_n':n_adj,
            'top1_changed_n':int(g.top1_changed.sum()),'top1_changed_rate':float(g.top1_changed.sum()/n_adj) if n_adj else np.nan,
            'clear_or_multiple_mainline_n':int(g.structure.isin(['clear_single_mainline','multiple_mainlines']).sum()),
            'structure_replacement_n':int(g.structure.eq('mainline_replacement').sum()),
            'low_score_high_theme_n':int((g.grade5.isin(['C','D'])&g.theme_heat_score.ge(65)).sum())})
    save(pd.DataFrame(direction_context),o,'direction_theme_structure.csv')
    cases=[];used=set()
    def choose(label,indices,rule):
        for i in indices:
            if env.trade_date.iloc[i] not in used:
                r=env.iloc[i];used.add(r.trade_date);cases.append({'case':label,'trade_date':r.trade_date,'score5':r.score5,'phase':r.phase,'rule':rule});return
    choose('high_score',env.sort_values(['score5','trade_date'],ascending=[False,True]).index,'全期正式分最高；同分取最早')
    low=run_table[(run_table.kind=='grade5')&(run_table.state=='D')].sort_values(['duration','start_date'],ascending=[False,True])
    if len(low):choose('longest_low',env.index[env.trade_date.eq(low.start_date.iloc[0])],'最长D连续段首日；等长取最早')
    repair_events=events[(events.event=='repair')&events.complete_5&events.delta_t5.lt(0)].sort_values(['delta_t5','trade_date'])
    if len(repair_events):choose('repair_failure',env.index[env.trade_date.eq(repair_events.trade_date.iloc[0])],'修复/启动候选后5日分差最小；事件标签不使用未来')
    lowrepair=env[env.phase.isin(['启动候选','修复'])].sort_values(['delta1','trade_date'],ascending=[False,True])
    choose('low_repair',lowrepair.index,'改善幅度最大的启动候选/修复；同分取最早')
    choose('heat_divergence',comparable.sort_values(['heat_score_gap','trade_date'],ascending=[False,True]).index,'题材热度减接力分差最大；同分取最早')
    save(pd.DataFrame(cases),o,'case_selection.csv')
    details=[]
    for case in cases:
        i=all_env.index[all_env.trade_date.eq(case['trade_date'])][0]
        x=all_env.iloc[max(0,i-5):min(len(all_env),i+6)].copy();x['case']=case['case'];x['relative_market_day']=x.index-i;x['display_role']=np.where(x.relative_market_day>0,'future_outcome_only','available_by_event_date');details.append(x)
    save(pd.concat(details,ignore_index=True),o,'case_windows.csv')
    # Robustness: use a=5 calibration unchanged for a=2/10.
    raw=pd.read_csv(a.formal/'daily_raw.csv',dtype={'date':str},float_precision='round_trip');cal=json.loads((a.formal/'calibration.json').read_text(encoding='utf-8-sig'))
    variants=[];robust=[]
    for a_value in [2,5,10]:
        s=score_raw(raw,cal,a_value);s['trade_date']=pd.to_datetime(s.date).dt.strftime('%Y-%m-%d');cy=compute_cycle(s);s=s.merge(cy[['trade_date','phase']],on='trade_date',validate='one_to_one');s=s[s.trade_date.between('2026-01-01','2026-09-30')]
        v=s[['trade_date','pseudocount','score5','grade5','phase']];variants.append(v)
        m=v.merge(env[['trade_date','score5','grade5','phase']],on='trade_date',suffixes=('','_a5'));valid=m.score5.notna()&m.score5_a5.notna();m=m[valid]
        robust.append({'a':a_value,'paired_n':len(m),'mean_absolute_score_difference':(m.score5-m.score5_a5).abs().mean(),'max_absolute_score_difference':(m.score5-m.score5_a5).abs().max(),'grade_changed_n':int(m.grade5.ne(m.grade5_a5).sum()),'phase_changed_n':int(m.phase.ne(m.phase_a5).sum()),'grade_changed_rate':m.grade5.ne(m.grade5_a5).mean(),'phase_changed_rate':m.phase.ne(m.phase_a5).mean(),'anchors_priors_fixed':True})
    save(pd.concat(variants,ignore_index=True),o,'robustness_daily.csv');save(pd.DataFrame(robust),o,'robustness_summary.csv')
    diffpath=a.formal/'reference_to_formal_diff.csv'
    datachanges=[]
    if diffpath.exists():
        diff=pd.read_csv(diffpath)
        if 'delta_total' in diff:
            diff.trade_date=pd.to_datetime(diff.trade_date).dt.strftime('%Y-%m-%d')
            for year in ['2025','2026']:
                paired=diff[diff.trade_date.str.startswith(year)&diff.common_date.eq(True)]
                for step in ['delta_live_input','delta_historical_ST','delta_calendar_cohort_quality','delta_calibration','delta_total']:
                    x=paired[step].dropna()
                    datachanges.append({'year':year,'step':step,'paired_n':len(x),'affected_n':int(x.abs().gt(1e-10).sum()),'mean_signed_change':x.mean(),'mean_absolute_change':x.abs().mean(),'max_absolute_change':x.abs().max()})
    save(pd.DataFrame(datachanges,columns=['year','step','paired_n','affected_n','mean_signed_change','mean_absolute_change','max_absolute_change']),o,'data_change_summary.csv')
    if a.human_source:
        human=pd.read_csv(a.human_source).merge(env[['trade_date','score5','grade5','phase','phase_tag','direction','delta1','M1','M2','M3','M4','M5']],on='trade_date',how='left',validate='many_to_one')
        if a.human_review:human=human.merge(pd.read_csv(a.human_review),on='trade_date',how='left',validate='one_to_one')
        save(human,o,'human_comparison.csv')
    summary={'calendar_days':len(env),'score_valid_days':int(env.score5.notna().sum()),'score_unknown_days':int(env.score5.isna().sum()),'theme_valid_days':int(env.theme_available.sum()),'start':env.trade_date.min(),'end':env.trade_date.max(),'score_mean':float(env.score5.mean()),'grade_counts':env.loc[env.score5.notna(),'grade5'].value_counts().to_dict(),'phase_counts':env.phase.value_counts().to_dict(),'panic_days':int(env.panic.sum()),'score_theme_correlation':float(comparable.score5.corr(comparable.theme_heat_score)),'score_theme_paired_n':len(comparable),'sources':{'grade_counts':'sentiment_daily.csv valid score groupby grade5; unknown reported separately','phase_counts':'cycle_daily.csv groupby phase','score_theme_correlation':'environment_daily.csv pairwise complete Pearson; descriptive; shared price source','cases':'case_selection.csv deterministic rules'},'return_validation_performed':False,'cycle_version':'C0-v1'}
    (o/'analysis_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    assert sum(summary['grade_counts'].values())==summary['score_valid_days']
    assert sum(summary['phase_counts'].values())==len(env)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
