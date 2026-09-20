"""Pure early-limit overlays. No source IO, downloads, writes, or trading operations.

attach_context returns one row per original mother stock-day. Callers pass the
accepted peer-context-v2 and frozen signal frames; no historical panel rebuilding.
"""
import json
import numpy as np
import pandas as pd

KEYS=['ts_code','trade_date']
SIGNAL_FILES={name:name+'-peer_rank.parquet' for name in
              ('combo_participation','leader_continuation','flow_absorption')}
PEER_FIELDS=['peer_flow_ratio','peer_breadth','peer_active','peer_return_coverage',
             'peer_active_coverage','peer_flow_coverage','theme_count']


def _unique(frame,label):
    if not set(KEYS).issubset(frame.columns) or frame.duplicated(KEYS).any():
        raise ValueError(label+'_missing_or_duplicate_stockday')
    if frame[KEYS].isna().any().any(): raise ValueError(label+'_null_stockday')


def _state(values,threshold):
    return np.where(~np.isfinite(values),'UNKNOWN',np.where(values>threshold,'PASS','FAIL'))


def attach_context(mother,peer,dates,signal_frames):
    """Add D-postclose, exact D-1, frozen-pool top8 and common-mother top3 labels.

dates must be the ordered unique exchange calendar, including desired warmup.
signal_frames maps SIGNAL_FILES family keys to frames (None => unavailable).
All original mother fields are retained. UNKNOWN never becomes a failed signal.
"""
    _unique(mother,'mother');_unique(peer,'peer')
    if dates!=sorted(set(dates)): raise ValueError('unsorted_or_duplicate_exchange_calendar')
    if not set(mother.trade_date).issubset(dates): raise ValueError('mother_noncalendar_date')
    if not set(PEER_FIELDS).issubset(peer.columns): raise ValueError('peer_schema_mismatch')
    if set(signal_frames)-set(SIGNAL_FILES): raise ValueError('unexpected_signal_family')
    ordinal={day:i for i,day in enumerate(dates)}
    previous={dates[i]:dates[i-1] for i in range(1,len(dates))}
    p=peer.loc[peer.trade_date.isin(dates),KEYS+PEER_FIELDS].copy().sort_values(KEYS)
    p[PEER_FIELDS]=p[PEER_FIELDS].apply(pd.to_numeric,errors='coerce')
    p['ordinal']=p.trade_date.map(ordinal)
    valid_theme=np.isfinite(p.theme_count)&p.theme_count.ge(1)&p.theme_count.mod(1).eq(0)
    p['flow_input']=p.peer_flow_ratio.where(valid_theme&p.peer_flow_coverage.between(.8,1)&np.isfinite(p.peer_flow_ratio))
    p['breadth']=p.peer_breadth.where(valid_theme&p.peer_return_coverage.between(.8,1)&p.peer_breadth.between(0,1))
    p['active']=p.peer_active.where(valid_theme&p.peer_active_coverage.between(.8,1)&p.peer_active.ge(0)&np.isfinite(p.peer_active))
    group=p.groupby('ts_code',sort=False)
    p['flow5']=group.flow_input.transform(lambda s:s.rolling(5,min_periods=5).mean())
    p['flow5_sessions']=group.flow_input.transform(lambda s:s.rolling(5,min_periods=1).count())
    contiguous=p.ordinal-group.ordinal.shift(4)==4
    p['flow5']=p.flow5.where(contiguous)
    p['flow5_exact_calendar']=contiguous
    keep=KEYS+['flow5','breadth','active','flow5_sessions','flow5_exact_calendar']+PEER_FIELDS
    out=mother.copy();out['_mother_order']=np.arange(len(out))
    # Source separation is part of ranking, not merely ex-post reporting:
    # adding uncertified KPL-only observations cannot displace Tushare top3.
    out['clock_cohort']=_clock_cohort(out)
    if 'board_streak_group' not in out:
        count=pd.to_numeric(out.get('board_count',pd.Series(np.nan,index=out.index)),errors='coerce')
        out['board_streak_group']=np.where(count.eq(1),'FIRST_BOARD',np.where(count.ge(2)&count.mod(1).eq(0),'SECOND_OR_HIGHER','UNKNOWN'))
    for prefix in ('postclose','lag1'):
        out[prefix+'_source_date']=out.trade_date if prefix=='postclose' else out.trade_date.map(previous)
        out[prefix+'_source_date']=out[prefix+'_source_date'].astype(object)
        renamed=p[keep].rename(columns={'trade_date':prefix+'_source_date',**{c:prefix+'_'+c for c in keep if c not in KEYS}})
        out=out.merge(renamed,on=['ts_code',prefix+'_source_date'],how='left',validate='many_to_one',sort=False)
        for feature,cutoff in [('flow5',0),('breadth',.5),('active',1)]:
            out[prefix+'_'+feature+'_state']=_state(out[prefix+'_'+feature],cutoff)
        statecols=[prefix+'_'+feature+'_state' for feature in ('flow5','breadth','active')]
        all_known=out[statecols].ne('UNKNOWN').all(axis=1)
        out[prefix+'_all3_state']=np.where(~all_known,'UNKNOWN',np.where(out[statecols].eq('PASS').all(axis=1),'PASS','FAIL'))
        out[prefix+'_context_quality']=np.where(out[prefix+'_theme_count'].isna(),'PEER_ROW_MISSING',
                                      np.where(all_known,'OBSERVED_PROXY_FIELDS_KNOWN','PEER_FIELDS_PARTIAL_UNKNOWN'))
        # Within exact same date/time-group known-feature mother, never use outcomes.
        out[prefix+'_rank_score']=np.nan;out[prefix+'_rank']=np.nan
        if all_known.any():
            known=out.loc[all_known].copy()
            rank_groups=['trade_date','time_group','clock_cohort']
            ranks=known.groupby(rank_groups)[[prefix+'_flow5',prefix+'_breadth',prefix+'_active']].rank(pct=True)
            known[prefix+'_rank_score']=ranks.mean(axis=1)
            ordered=known.sort_values(rank_groups+[prefix+'_rank_score','ts_code'],ascending=[True,True,True,False,True])
            ordinal_rank=ordered.groupby(rank_groups).cumcount()+1
            out.loc[known.index,prefix+'_rank_score']=known[prefix+'_rank_score']
            out.loc[ordered.index,prefix+'_rank']=ordinal_rank
        out[prefix+'_rank3_state']=np.where(~all_known,'UNKNOWN',np.where(out[prefix+'_rank'].le(3),'PASS','FAIL'))
    for family in SIGNAL_FILES:
        signal=signal_frames.get(family)
        state=family+'_top8_state'
        out[state]='UNKNOWN';out[family+'_literal_top8_intersection']=False
        out[family+'_frozen_rank']=np.nan
        if signal is None: continue
        _unique(signal,family)
        if not {'selection_score','aux_source_date'}.issubset(signal.columns): raise ValueError('frozen_signal_schema')
        if not np.isfinite(signal.selection_score).all(): raise ValueError('nonfinite_frozen_score')
        if not (signal.aux_source_date<signal.trade_date).all(): raise ValueError('frozen_signal_future_context')
        # Validate exact prior date when both dates lie in supplied calendar.
        comparable=signal.trade_date.isin(previous)
        if not signal.loc[comparable,'aux_source_date'].eq(signal.loc[comparable,'trade_date'].map(previous)).all():
            raise ValueError('frozen_signal_not_exact_previous_session')
        ranked=signal.sort_values(['trade_date','selection_score','ts_code'],ascending=[True,False,True]).copy()
        ranked['_rank']=ranked.groupby('trade_date').cumcount()+1
        lookup=ranked.set_index(KEYS)['_rank']
        out[family+'_frozen_rank']=pd.MultiIndex.from_frame(out[KEYS]).map(lookup).astype(float)
        known=out[family+'_frozen_rank'].notna();positive=out[family+'_frozen_rank'].le(8)
        out[state]=np.where(~known,'UNKNOWN',np.where(positive,'PASS','FAIL'))
        out[family+'_literal_top8_intersection']=positive
    out['peer_source']='discovery-v1/peer-context-v2.parquet; equal-weight historical KPL memberships, leave-one-out; NOT TI-name linkage'
    out['peer_count_quality']='Inherited producer >=3/80pct screening; aggregate file lacks raw peer-count proof'
    out['context_availability']='D postclose only for D+1; lag1 exact prior-session proxy; historical arrival NOT certified'
    out['signal_pool_scope']='Frozen context-known/ST/120session/top20 family prescreens or their union; outside pool UNKNOWN, NOT original strategy failure'
    out['rank_scope']='Same exact date/time_group/clock_cohort known-feature mother; source-separated BEFORE ranking'
    return out.sort_values('_mother_order').drop(columns='_mother_order').reset_index(drop=True)


BOOTSTRAP_SEED=20260920
BOOTSTRAP_DRAWS=500
STRATA=['board','st_status','board_streak_group','flat_at_upper','reopen_label']


def _flag(series):
    if series.dropna().map(lambda x:not isinstance(x,(bool,np.bool_))).any():
        raise ValueError('outcome_flag_not_boolean')
    return series.fillna(False).astype(bool)


def metric(g,kind):
    """Kimi statistics draft corrected for description schema and unknown paths."""
    if kind not in ('trade','description'): raise ValueError('unknown_result_kind')
    flag='closed' if kind=='trade' else 'complete'
    field='net_return' if kind=='trade' else 'ret'
    done=_flag(g[flag]);entered=_flag(g.entered) if kind=='trade' else pd.Series(True,index=g.index)
    if kind=='trade' and (done&~entered).any(): raise ValueError('closed_without_entry')
    values=pd.to_numeric(g[field],errors='coerce')
    valid=done&np.isfinite(values);r=values.loc[valid]
    wins=r.loc[r>0];losses=r.loc[r<0]
    day_means=g.loc[valid,['trade_date']].assign(_r=r).groupby('trade_date')._r.mean()
    holding=pd.to_numeric(g.loc[done,'actual_holding_sessions'],errors='coerce') if 'actual_holding_sessions' in g else pd.Series(dtype=float)
    holding=holding.loc[np.isfinite(holding)]
    states=g['state' if kind=='trade' else 'status'].fillna('UNKNOWN').astype(str)
    return dict(mother_candidates=len(g),unique_stockdays=int(g[KEYS].drop_duplicates().shape[0]),
        unique_stocks=int(g.ts_code.nunique()),unique_dates=int(g.trade_date.nunique()),
        entered=int(entered.sum()) if kind=='trade' else None,entry_not_applicable=kind=='description',
        not_entered=int((~entered).sum()) if kind=='trade' else None,
        entered_unknown=int(g.entered.isna().sum()) if kind=='trade' else 0,
        closed=int(done.sum()),closed_flag_unknown=int(g[flag].isna().sum()),closed_returns=int(valid.sum()),
        finite_missing_closed=int((done&~np.isfinite(values)).sum()),unresolved=int((entered&~done).sum()),
        unfilled=int(states.str.startswith('UNFILLED').sum()) if kind=='trade' else 0,
        rejected_entry=int(states.eq('UNSUPPORTED_BOARD_ENTRY').sum()) if kind=='trade' else 0,
        st_or_unknown_entry_exclusion=int(states.eq('UNFILLED_POSTHOC_ST_OR_UNKNOWN').sum()) if kind=='trade' else 0,
        unknown_state=int(states.str.contains('UNKNOWN').sum()),right_censored=int(states.str.contains('CENSORED').sum()),
        state_counts_json=json.dumps({str(k):int(v) for k,v in states.value_counts(dropna=False).items()},sort_keys=True),
        wins=len(wins),losses=len(losses),zeros=int(r.eq(0).sum()),
        win_rate=float(len(wins)/len(r)) if len(r) else None,
        closure_coverage=float(valid.sum()/entered.sum()) if kind=='trade' and entered.sum() else None,
        all_entered_win_rate_lower_bound=float(len(wins)/entered.sum()) if kind=='trade' and entered.sum() else None,
        all_entered_win_rate_upper_bound=float((len(wins)+entered.sum()-valid.sum())/entered.sum()) if kind=='trade' and entered.sum() else None,
        avgwin=float(wins.mean()) if len(wins) else None,avgloss=float(losses.mean()) if len(losses) else None,
        payoff=float(wins.mean()/abs(losses.mean())) if len(wins) and len(losses) else None,
        profit_factor=float(wins.sum()/abs(losses.sum())) if len(losses) else None,
        mean=float(r.mean()) if len(r) else None,worst=float(r.min()) if len(r) else None,
        tail15_count=int(r.le(-.15).sum()),tail30_count=int(r.le(-.30).sum()),
        date_balanced_mean=float(day_means.mean()) if len(day_means) else None,
        holding_mean=float(holding.mean()) if len(holding) else None,
        quantity='FIXED_NOTIONAL_EVENT_PRICE_PROXY_NOT_ACCOUNT' if kind=='trade' else 'UNTRADEABLE_CLOSE_CONTINUATION_DESCRIPTION')


def paired_dates(g,group_col,positive,negative,strata,return_col='net_return'):
    """Date+stratum paired difference; date cluster bootstrap, 500 fixed draws.

Kimi retry wrongly treated the positive/negative labels as DataFrames and joined
on contradictory group values. Correctly subset labels, then join ONLY strata.
"""
    keys=['trade_date']+list(strata)
    values=pd.to_numeric(g[return_col],errors='coerce')
    done=_flag(g.closed) if 'closed' in g else pd.Series(True,index=g.index)
    work=g.loc[done&np.isfinite(values)].copy();work[return_col]=values.loc[work.index]
    pos=work.loc[work[group_col].eq(positive)];neg=work.loc[work[group_col].eq(negative)]
    result=dict(status='INSUFFICIENT_DATES',shared_dates=0,matched_strata=0,paired_mean=None,ci_low=None,ci_high=None,
        total_pos_rows=len(pos),total_neg_rows=len(neg),matched_pos_rows=0,matched_neg_rows=0,
        bootstrap_draws=BOOTSTRAP_DRAWS,bootstrap_seed=BOOTSTRAP_SEED)
    if pos.empty or neg.empty: return result
    left=pos.groupby(keys,dropna=False)[return_col].agg(['mean','size'])
    right=neg.groupby(keys,dropna=False)[return_col].agg(['mean','size'])
    matched=left.join(right,how='inner',lsuffix='_pos',rsuffix='_neg')
    if matched.empty: return result
    diff=matched.mean_pos-matched.mean_neg
    daily=diff.groupby(level='trade_date').mean();n=len(daily)
    result.update(shared_dates=n,matched_strata=len(matched),paired_mean=float(daily.mean()),
                  matched_pos_rows=int(matched.size_pos.sum()),matched_neg_rows=int(matched.size_neg.sum()))
    if n>=5:
        rng=np.random.default_rng(BOOTSTRAP_SEED)
        boot=rng.choice(daily.to_numpy(),size=(BOOTSTRAP_DRAWS,n),replace=True).mean(axis=1)
        lo,hi=np.quantile(boot,[.025,.975]);result.update(status='DATE_CLUSTER_DESCRIPTIVE_INTERVAL',ci_low=float(lo),ci_high=float(hi))
    return result


def _clock_cohort(frame):
    source=frame.get('time_source',pd.Series('UNKNOWN',index=frame.index)).fillna('UNKNOWN').astype(str)
    quality=frame.get('time_quality',pd.Series('UNKNOWN',index=frame.index)).fillna('UNKNOWN').astype(str)
    return np.select([quality.str.contains('CONFLICT'),source.eq('TUSHARE_FIRST_TIME'),source.eq('KPL_LU_TIME_ONLY')],
                     ['CONFLICT_OR_UNKNOWN','TUSHARE_SOURCE_LABEL_PROXY','KPL_ONLY_UNCERTIFIED'],default='CONFLICT_OR_UNKNOWN')


def _prepare_statistics(frame,kind):
    if frame is None or frame.empty: return pd.DataFrame()
    frame=frame.copy();scenario=['h'] if kind=='description' else ['hold','cost']
    if not set(KEYS+scenario).issubset(frame.columns) or frame.duplicated(KEYS+scenario).any():
        raise ValueError('duplicate_or_missing_stockday_scenario')
    for col in ('time_group','board','st_status','flat_at_upper','reopen_label','board_streak_group','time_source','time_quality'):
        if col not in frame: frame[col]='UNKNOWN'
        frame[col]=frame[col].fillna('UNKNOWN').astype(str)
    frame['month']=frame.trade_date.astype(str).str[:6]
    frame['clock_cohort']=_clock_cohort(frame)
    # Fail closed on string "False", inconsistent closed/entered, or outcomes
    # wrongly marked closed. Missing numeric result stays in missing denominator.
    flags=['complete'] if kind=='description' else ['entered','closed']
    for flag in flags:
        if flag not in frame: raise ValueError('missing_outcome_flag')
        bad=frame[flag].dropna().map(lambda x:not isinstance(x,(bool,np.bool_)))
        if bad.any(): raise ValueError('outcome_flag_not_boolean')
    if kind=='trade' and (frame.closed.fillna(False)&~frame.entered.fillna(False)).any():
        raise ValueError('closed_without_entry')
    return frame


def summarize(description_frame,trade_frame):
    """Return safe filename stems -> tables; no writes or recomputation of returns.

Rows are never pooled across holds/costs. Main comparisons separate Tushare labels,
KPL-only unverified labels and conflicts. Bootstrap resamples dates, not events.
"""
    summary=[];main=[];overlays=[]
    for kind,incoming in [('description',description_frame),('trade',trade_frame)]:
        frame=_prepare_statistics(incoming,kind)
        if frame.empty: continue
        scenario=['h'] if kind=='description' else ['hold','cost']
        for values,group in frame.groupby(scenario,dropna=False,sort=True):
            if not isinstance(values,tuple): values=(values,)
            spec=dict(zip(scenario,values));spec['kind']=kind
            clocks=[('ALL_CLOCKS_DESCRIPTIVE_ONLY',group),*list(group.groupby('clock_cohort',dropna=False))]
            for clock,cg in clocks:
                views=[('all','all',cg)]
                for dim in ('month','time_group','board','st_status','flat_at_upper','reopen_label','board_streak_group','time_source','time_quality'):
                    views.extend((dim,str(label),sub) for label,sub in cg.groupby(dim,dropna=False,sort=True))
                union=cg.loc[cg.time_group.isin(['OPEN_0_TO_5','AFTER5_TO10'])]
                views.append(('time_group','OPEN_0_TO10_OVERLAPPING_UNION',union))
                statecols=sorted(c for c in cg if c.endswith(('_flow5_state','_breadth_state','_active_state','_all3_state','_top8_state','_rank3_state')))
                for col in statecols:
                    for state in ('PASS','FAIL','UNKNOWN'):
                        views.append(('overlay:'+col,state,cg.loc[cg[col].eq(state)]))
                for dimension,label,sub in views:
                    summary.append(dict(**spec,clock_cohort=clock,dimension=dimension,label=label,**metric(sub,kind)))
                # User's actual question: within each EARLY time group, does the
                # overlay alter conditional win/payoff quality? No Cartesian grid.
                early_views=[('OPEN_0_TO_5',['OPEN_0_TO_5']),('AFTER5_TO10',['AFTER5_TO10']),
                             ('OPEN_0_TO10_OVERLAPPING_UNION',['OPEN_0_TO_5','AFTER5_TO10'])]
                selected_cols=[c for c in statecols if c.endswith(('_flow5_state','_all3_state','_rank3_state','_top8_state'))]
                for early_label,members in early_views:
                    early=cg.loc[cg.time_group.isin(members)]
                    for col in selected_cols:
                        for state in ('ALL_KNOWN','PASS','FAIL','UNKNOWN'):
                            sub=early.loc[early[col].isin(['PASS','FAIL'])] if state=='ALL_KNOWN' else early.loc[early[col].eq(state)]
                            summary.append(dict(**spec,clock_cohort=clock,dimension='time_group_overlay',label=state,
                                time_group=early_label,overlay=col,
                                comparison_scope='Same date/time-group known-feature or frozen-pool mother; event means NOT cash account',
                                **metric(sub,kind)))
            if kind!='trade': continue
            for clock,cg in group.groupby('clock_cohort',dropna=False,sort=True):
                # No pooled-clock primary contrast, no retrospective fallback upgrade.
                closed=cg.loc[cg.closed.fillna(False)&np.isfinite(pd.to_numeric(cg.net_return,errors='coerce'))].copy()
                for label,early in [('OPEN_0_TO_5',['OPEN_0_TO_5']),('AFTER5_TO10',['AFTER5_TO10']),
                                    ('OPEN_0_TO10_OVERLAPPING_UNION',['OPEN_0_TO_5','AFTER5_TO10'])]:
                    valid=closed.loc[closed.time_group.isin(early+['AFTER10'])].copy()
                    valid['_contrast']=np.where(valid.time_group.isin(early),'EARLY','LATE')
                    result=paired_dates(valid,'_contrast','EARLY','LATE',STRATA)
                    main.append(dict(**spec,clock_cohort=clock,comparison=label+'_vs_AFTER10',
                        early_candidates=int(cg.time_group.isin(early).sum()),late_candidates=int(cg.time_group.eq('AFTER10').sum()),
                        early_closed=int(valid._contrast.eq('EARLY').sum()),late_closed=int(valid._contrast.eq('LATE').sum()),
                        inference='CONDITIONAL_CLOSED_EVENT_PRICE_PROXY_NOT_CAUSAL_OR_ACCOUNT',**result))
                primary_cols=sorted(c for c in cg if c.endswith(('_all3_state','_top8_state','_rank3_state')))
                for col in primary_cols:
                    known=closed.loc[closed[col].isin(['PASS','FAIL'])]
                    result=paired_dates(known,col,'PASS','FAIL',['time_group']+STRATA)
                    overlays.append(dict(**spec,clock_cohort=clock,overlay=col,
                        pass_candidates=int(cg[col].eq('PASS').sum()),fail_candidates=int(cg[col].eq('FAIL').sum()),
                        unknown_candidates=int(cg[col].eq('UNKNOWN').sum()),known_candidates=int(cg[col].isin(['PASS','FAIL']).sum()),
                        pass_closed=int(known[col].eq('PASS').sum()),fail_closed=int(known[col].eq('FAIL').sum()),
                        interpretation='Known same frozen/feature mother only; closed-path selection remains; no causal claim',**result))
    return {'summary':pd.DataFrame(summary),'main_comparisons':pd.DataFrame(main),'overlay_comparisons':pd.DataFrame(overlays)}
