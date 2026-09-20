"""Primary-frozen turnover phases. Kimi mechanical draft, Codex-verified.

Pure preparation: no IO, prices, strategy returns, optimization or orders.
"""
import re
import numpy as np
import pandas as pd

VARIANTS=('REL','ABS2T')
PHASES=('UNKNOWN','RISING','RETREAT','ACTIVE','INACTIVE')
SPEC=dict(version='market-activity-v1',mean_sessions=5,baseline_sessions=60,growth_lag=5,
    baseline_shift=1,relative_high=1.10,relative_low=.90,relative_seed=1.,
    rising=.05,retreat=-.05,confirmation_sessions=2,absolute_cny=2e12,
    first='20240701',last='20260831',evaluation_first='20250101',expected_sessions=528,
    variants=list(VARIANTS),no_outcome_optimization=True)
KNOWN={'OBSERVED_UNIVERSE_PROXY','RECONCILED_TOTAL'}


def validate_calendar(calendar):
    dates=list(calendar)
    if not dates or dates!=sorted(set(dates)) or not all(isinstance(x,str) and re.fullmatch(r'\d{8}',x) for x in dates):
        raise ValueError('market_activity_exact_sorted_calendar')
    return dates


def input_frame(daily,calendar):
    dates=validate_calendar(calendar)
    if not {'trade_date','amount_cny','coverage_state'}<=set(daily):raise ValueError('market_activity_daily_schema')
    if daily.trade_date.isna().any() or daily.trade_date.duplicated().any() or not set(daily.trade_date)<=set(dates):
        raise ValueError('market_activity_date_identity')
    result=daily.copy().set_index('trade_date').reindex(dates)
    result.index.name='trade_date'
    result['coverage_state']=result.coverage_state.fillna('UNKNOWN')
    if not set(result.coverage_state)<=KNOWN|{'UNKNOWN'}:raise ValueError('market_activity_coverage_state')
    numeric=pd.to_numeric(result.amount_cny,errors='coerce')
    valid=np.isfinite(numeric)&numeric.gt(0)&result.coverage_state.isin(KNOWN)
    # Invalid and absent observations are retained and become UNKNOWN, not zero.
    result['A_cny']=numeric.where(valid)
    result['coverage_state']=result.coverage_state.where(valid,'UNKNOWN')
    result['observed_amount_ge_2t']=pd.Series(pd.NA,index=result.index,dtype='boolean')
    result.loc[valid,'observed_amount_ge_2t']=numeric.loc[valid].ge(SPEC['absolute_cny']).to_numpy()
    return result


def causal_statistics(amount):
    stats=pd.DataFrame(index=amount.index)
    stats['M5']=amount.rolling(5,min_periods=5).mean()
    stats['B60']=amount.shift(1).rolling(60,min_periods=60).median()
    stats['R']=stats.M5/stats.B60
    stats['G']=stats.M5/stats.M5.shift(5)-1
    stats['windows_complete']=np.isfinite(stats[['M5','B60','R','G']]).all(axis=1)&stats.M5.gt(0)&stats.B60.gt(0)
    return stats


def phase_machine(stats,variant):
    """Kimi state-machine draft retained; missing G and gap handling corrected."""
    if variant not in VARIANTS:raise ValueError('market_activity_variant')
    level=confirmed=pending='UNKNOWN';count=0;rows=[]
    for r in stats.itertuples():
        valid=bool(r.windows_complete) and all(np.isfinite(v) for v in (r.M5,r.B60,r.R,r.G)) and r.M5>0 and r.B60>0
        if not valid:
            level=confirmed=pending='UNKNOWN';count=0;raw='UNKNOWN'
        else:
            if variant=='ABS2T':level='HIGH' if r.M5>=2e12 else 'LOW'
            elif level=='UNKNOWN':level='HIGH' if r.R>=1. else 'LOW'
            elif r.R>=1.10:level='HIGH'
            elif r.R<=.90:level='LOW'
            raw='RISING' if r.G>=.05 else 'RETREAT' if r.G<=-.05 else 'ACTIVE' if level=='HIGH' else 'INACTIVE'
            if raw==confirmed:
                pending='UNKNOWN';count=0
            else:
                count=count+1 if pending==raw else 1;pending=raw
                if count>=2:confirmed=raw;pending='UNKNOWN';count=0
        rows.append(dict(level=level,raw_phase=raw,confirmed_phase=confirmed,pending_phase=pending,pending_count=count))
    return pd.DataFrame(rows,index=stats.index)


def build_regimes(daily,calendar):
    """One row/date/variant. D-close information cannot become a D-open label."""
    base=input_frame(daily,calendar);stats=causal_statistics(base.A_cny);outputs=[]
    for variant in VARIANTS:
        frame=base.join(stats).join(phase_machine(stats,variant));frame['variant']=variant
        frame=frame.reset_index()
        frame['decision_source_date']=frame.trade_date.shift(1)
        frame['decision_phase']=frame.confirmed_phase.shift(1).fillna('UNKNOWN')
        frame['decision_level']=frame.level.shift(1).fillna('UNKNOWN')
        frame['decision_coverage_state']=frame.coverage_state.shift(1).fillna('UNKNOWN')
        frame['earliest_decision_date']=frame.trade_date.shift(-1)
        frame['availability']='D_POSTCLOSE_FOR_DPLUS1; ORIGINAL_PUBLICATION_CLOCK_UNCERTIFIED'
        frame['exposure']='2025/2026_EXPOSED_RESEARCH_NOT_BLIND_TEST'
        outputs.append(frame)
    result=pd.concat(outputs,ignore_index=True)
    if len(result)!=2*len(calendar) or result.duplicated(['trade_date','variant']).any():raise AssertionError('market_activity_denominator')
    return result
