"""C0-v1: causal descriptive phases on a complete market calendar."""
import numpy as np
import pandas as pd

PHASES=['冰点','退潮','启动候选','修复','分歧','扩散','强势','中性/混沌']
def compute_cycle(daily):
    f=daily.sort_values('trade_date').reset_index(drop=True).copy()
    assert not f.trade_date.duplicated().any()
    s=f.score5
    f['delta1']=s.diff(); f['delta5']=s.diff(5)
    f['lo5']=s.shift().rolling(5,min_periods=5).min();f['hi5']=s.shift().rolling(5,min_periods=5).max()
    f['breadth_up']=f[['M1','M2','M3','M4','M5']].diff().ge(2).sum(axis=1)
    f['M4_delta']=f.M4.diff()
    current_ok=f[['score5','M1','M2','M3','M4','M5','panic']].notna().all(axis=1)
    history_ok=s.notna().shift().rolling(5,min_periods=5).sum().eq(5)
    phase=[];trigger=[];tags=[]
    for i,r in f.iterrows():
        if not current_ok.iloc[i]: ph,tr='证据不足','current_formal_module_missing'
        elif i<5: ph,tr='预热','fewer_than_five_previous_market_days'
        elif not history_ok.iloc[i] or not f.loc[i-1,['M1','M2','M3','M4','M5']].notna().all(): ph,tr='证据不足','history_gap'
        elif r.score5<35: ph,tr='冰点','score_lt_35'
        elif (bool(r.panic) and r.delta1<=-5) or (r.score5<50 and r.delta5<=-5) or (r.M4_delta<=-15 and r.delta1<=-5): ph,tr='退潮','panic_or_five_day_decline_or_mid_failure'
        elif r.lo5<35 and f.score5.iloc[i-1]<50 and r.score5>=50 and r.delta1>=5 and r.breadth_up>=3: ph,tr='启动候选','cross_50_after_low_with_breadth'
        elif r.lo5<50 and r.delta1>=5 and r.breadth_up>=3: ph,tr='修复','improve_after_low_with_breadth'
        elif r.hi5>=65 and r.delta1<=-5: ph,tr='分歧','decline_after_recent_strength'
        elif r.score5>=65 and r.delta5>=5 and r.breadth_up>=3: ph,tr='扩散','high_and_broad_five_day_improvement'
        elif r.score5>=65: ph,tr='强势','score_ge_65'
        else: ph,tr='中性/混沌','none_of_above'
        phase.append(ph);trigger.append(tr);tags.append('冰点内改善' if ph=='冰点' and r.delta1>=5 else '高强度区' if ph=='强势' and r.score5>=80 else '')
    f['phase']=phase;f['phase_trigger']=trigger;f['phase_tag']=tags;f['cycle_version']='C0-v1'
    f['phase_quality']=np.where(f.phase.isin(PHASES),'observed','insufficient')
    group=f.phase.ne(f.phase.shift()).cumsum();f['phase_consecutive_days']=f.groupby(group).cumcount()+1
    return f[['trade_date','score5','phase','phase_trigger','phase_tag','phase_consecutive_days','delta1','delta5','lo5','hi5','breadth_up','M4_delta','phase_quality','cycle_version']]
