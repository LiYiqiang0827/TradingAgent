"""Read-only-result analysis primitives; no IO, downloads, orders or backtest.

Kimi supplied the first mechanical draft. Its ret field, outcome states, lag
validation and unknown-gap transitions were rejected and corrected here.
"""
import re
import numpy as np
import pandas as pd

SCEN = {
    'early_trade':['hold','cost','clock_cohort','time_group'],
    'early_description':['h','clock_cohort','time_group'],
    'minute8':['card','scan_stride','entry_delay','exit_point_policy','cost','scope','baseline','population_role','cohort_role'],
    'minute_extra':['card','scan_stride','entry_delay','exit_point_policy','cost','scope','baseline','population_role','cohort_role'],
    'exit_policy_v2':['family','top_n','policy','cost','regime'],
}
PHASES={'RISING','RETREAT','ACTIVE','INACTIVE','UNKNOWN'}
LEVELS={'HIGH','LOW','UNKNOWN'}
VARIANTS=('REL','ABS2T')
MINUTE={'minute8','minute_extra'}


def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{8}',value):
        raise ValueError('activity_date_must_be_YYYYMMDD_string')
    return value


def _calendar(values):
    result=[_date(v) for v in values]
    if not result or result!=sorted(set(result)):raise ValueError('activity_calendar_unique_sorted')
    return result


def _require(frame,columns):
    if not set(columns)<=set(frame):raise ValueError('activity_missing_columns:'+','.join(sorted(set(columns)-set(frame))))


def _boolean(series):
    if not series.map(lambda x:isinstance(x,(bool,np.bool_))).all():raise ValueError('activity_strict_bool')
    return series.astype(bool)


def normalize_events(frame,source_kind):
    if source_kind not in SCEN:raise ValueError('activity_unknown_source_kind')
    d=frame.copy().reset_index(drop=True)
    _require(d,SCEN[source_kind])
    if source_kind in MINUTE:
        _require(d,['cohort_id','ts_code','target_date','state','entry_date','net_return'])
        identity=['cohort_id','ts_code'];date='target_date';ret='net_return';state='state'
        closed=d.state.eq('COMPLETED')
        entered=d.entry_date.notna() & d.entry_date.ne('')
    else:
        identity=['event_id'];date='trade_date';state='status' if source_kind=='early_description' else 'state'
        ret='ret' if source_kind=='early_description' else 'net_return'
        _require(d,identity+[date,ret,state])
        if source_kind=='early_description':
            _require(d,['complete']);closed=_boolean(d.complete);entered=pd.Series(pd.NA,index=d.index,dtype='boolean')
        elif source_kind=='early_trade':
            _require(d,['closed','entered']);closed=_boolean(d.closed);entered=_boolean(d.entered)
        else:
            _require(d,['entered']);closed=d.state.eq('COMPLETED');entered=_boolean(d.entered)
    for col in identity+[state]:
        if not d[col].map(lambda x:isinstance(x,str) and bool(x) and '|' not in x).all():
            raise ValueError('activity_invalid_identity_or_state')
    d['package_kind']=source_kind
    d['event_key']=d[identity].agg('|'.join,axis=1)
    d['event_date']=d[date].map(_date)
    d['asof_mode']='PREVIOUS_SESSION' if source_kind in MINUTE else 'CLOSE_CONFIRMED'
    d['closed_for_metrics']=closed.astype(bool)
    d['entered_for_metrics']=entered
    d['metric_return']=pd.to_numeric(d[ret],errors='coerce').where(closed)
    d['outcome_state']=d[state]
    if d.duplicated(['event_key']+SCEN[source_kind]).any():raise ValueError('activity_duplicate_event_scenario')
    # A cohort is a context, not a new economic trade. The current complete
    # packages contain no repeated stock-day within each retained context/
    # scenario layer. Any future input that violates this is blocked rather
    # than silently overweighted, deduplicated optimistically, or pooled.
    if source_kind in MINUTE and d.duplicated(['event_date','ts_code']+SCEN[source_kind]).any():
        raise ValueError('activity_repeated_stock_day_within_context_requires_review')
    if not np.isfinite(d.loc[closed,'metric_return']).all():raise ValueError('activity_closed_return_not_finite')
    if source_kind!='early_description' and (closed & ~entered).any():raise ValueError('activity_closed_without_entry')
    return d


def validate_labels(labels,calendar):
    cal=_calendar(calendar);pos={d:i for i,d in enumerate(cal)}
    _require(labels,['trade_date','variant','confirmed_phase','decision_phase','level','decision_level',
                     'decision_source_date','coverage_state','market_scope'])
    l=labels.copy();l['trade_date']=l.trade_date.map(_date)
    if set(l.variant)!=set(VARIANTS) or l.duplicated(['trade_date','variant']).any():raise ValueError('activity_label_identity')
    if not set(l.trade_date)<=set(cal):raise ValueError('activity_label_noncalendar')
    if any(not set(l[c])<=PHASES for c in ('confirmed_phase','decision_phase')):raise ValueError('activity_bad_phase')
    if any(not set(l[c])<=LEVELS for c in ('level','decision_level')):raise ValueError('activity_bad_level')
    lookup={(r.trade_date,r.variant):r for r in l.itertuples(index=False)}
    for r in l.itertuples(index=False):
        previous=cal[pos[r.trade_date]-1] if pos[r.trade_date] else None
        ds=None if pd.isna(r.decision_source_date) else _date(r.decision_source_date)
        if ds!=previous:raise ValueError('activity_not_exact_previous_session')
        prev=lookup.get((previous,r.variant))
        phase=prev.confirmed_phase if prev is not None else 'UNKNOWN'
        level=prev.level if prev is not None else 'UNKNOWN'
        if r.decision_phase!=phase or r.decision_level!=level:raise ValueError('activity_shifted_label_disagrees')
        if hasattr(r,'decision_coverage_state') and r.decision_coverage_state!=(prev.coverage_state if prev is not None else 'UNKNOWN'):
            raise ValueError('activity_shifted_coverage_disagrees')
    return l


def join_labels(events,labels,calendar_dates):
    cal=_calendar(calendar_dates);l=validate_labels(labels,cal)
    _require(events,['event_key','event_date','package_kind','asof_mode'])
    if not set(events.event_date)<=set(cal):raise ValueError('activity_event_noncalendar')
    lv={(r.trade_date,r.variant):r for r in l.itertuples(index=False)}
    previous={d:cal[i-1] if i else None for i,d in enumerate(cal)}
    rows=[]
    for record in events.to_dict('records'):
        for variant in VARIANTS:
            row=dict(record);day=row['event_date'];lab=lv.get((day,variant))
            minute=row['asof_mode']=='PREVIOUS_SESSION'
            if row['asof_mode'] not in {'PREVIOUS_SESSION','CLOSE_CONFIRMED'}:raise ValueError('activity_unknown_asof')
            source_day=previous[day] if minute else day
            source=lv.get((source_day,variant)) if minute else lab
            row.update(activity_variant=variant,regime_source_date=source_day,
                activity_phase=('UNKNOWN' if lab is None else lab.decision_phase if minute else lab.confirmed_phase),
                activity_level=('UNKNOWN' if lab is None else lab.decision_level if minute else lab.level),
                activity_coverage=source.coverage_state if source is not None and lab is not None else 'UNKNOWN',
                activity_market_scope=source.market_scope if source is not None and lab is not None else 'UNKNOWN')
            rows.append(row)
    out=pd.DataFrame(rows)
    if len(out)!=2*len(events):raise AssertionError('activity_row_loss')
    if len(events):
        for kind,g in out.groupby('package_kind',sort=False):
            if g.duplicated(['event_key']+SCEN[kind]+['activity_variant']).any():raise ValueError('activity_join_duplicate')
    return out


def _finite(value):
    return float(value) if pd.notna(value) and np.isfinite(float(value)) else None


def _stats(g):
    closed=g.loc[g.closed_for_metrics];r=closed.metric_return.astype(float)
    description=g.package_kind.iloc[0]=='early_description'
    entered=None if description else int(g.entered_for_metrics.sum())
    unresolved=None if description else entered-len(closed)
    if unresolved is not None and unresolved<0:raise AssertionError('activity_entry_denominator')
    wins=r[r>0];loss=r[r<0];w=len(wins);n=len(r)
    aw=_finite(wins.mean());al=_finite(loss.mean())
    return dict(candidate_rows=len(g),unique_events=int(g.event_key.nunique()),
        unique_stock_days=int(g[['event_date','ts_code']].drop_duplicates().shape[0]) if 'ts_code' in g else None,
        candidate_dates=int(g.event_date.nunique()),closed_dates=int(closed.event_date.nunique()),
        entered=entered,closed=n,unresolved_entered=unresolved,W=w,L=len(loss),Z=int(r.eq(0).sum()),
        insufficient_closed_sample_lt5=n<5,few_closed_dates_lt5=int(closed.event_date.nunique())<5,
        mean=_finite(r.mean()),median=_finite(r.median()),avgwin=aw,avgloss=al,
        payoff=aw/-al if aw is not None and al is not None and al<0 else None,
        return_profit_factor=float(wins.sum()/-loss.sum()) if len(loss) else None,
        worst=_finite(r.min()),p05=_finite(r.quantile(.05)) if n else None,
        tail15_count=int(r.le(-.15).sum()),tail30_count=int(r.le(-.30).sum()),
        win_rate=w/n if n else None,all_entered_win_rate_lower_bound=w/entered if entered else None,
        all_entered_win_rate_upper_bound=(w+unresolved)/entered if entered else None,
        mean_date_balanced=_finite(closed.groupby('event_date').metric_return.mean().mean()) if n else None,
        unknown_outcome_rows=int(g.outcome_state.str.contains('UNKNOWN',regex=False).sum()),
        state_counts={str(k):int(v) for k,v in g.outcome_state.value_counts(dropna=False).items()})


def summarize(joined,extra_dimensions=()):
    if joined.empty:return pd.DataFrame()
    _require(joined,extra_dimensions);rows=[]
    for kind,group in joined.groupby('package_kind',sort=True):
        keys=list(dict.fromkeys(SCEN[kind]+['asof_mode','activity_variant','activity_phase']+list(extra_dimensions)))
        for identity,g in group.groupby(keys,dropna=False,sort=True):
            identity=identity if isinstance(identity,tuple) else (identity,)
            rows.append(dict(package_kind=kind,**dict(zip(keys,identity)),**_stats(g)))
    return pd.DataFrame(rows)


def market_coverage(labels,calendar):
    cal=_calendar(calendar);l=validate_labels(labels,cal);rows=[]
    for variant,group in l.groupby('variant',sort=True):
        index=group.set_index('trade_date');switches=0;last=None
        for day in cal:
            phase=index.loc[day,'confirmed_phase'] if day in index.index else 'UNKNOWN'
            if phase=='UNKNOWN':last=None;continue
            if last is not None and phase!=last:switches+=1
            last=phase
        missing=[d for d in cal if d not in index.index]
        rows.append(dict(variant=variant,observed_dates=len(index),missing_dates=missing,
            unknown_phase_dates=sorted(set(missing)|set(group.loc[group.confirmed_phase.eq('UNKNOWN'),'trade_date'])),
            phase_counts={str(k):int(v) for k,v in group.confirmed_phase.value_counts().items()},
            level_counts={str(k):int(v) for k,v in group.level.value_counts().items()},
            coverage_counts={str(k):int(v) for k,v in group.coverage_state.value_counts().items()},switch_count=switches))
    return pd.DataFrame(rows)
