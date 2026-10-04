"""Frozen-cohort and raw-input cutoff tests for the F1 production contract."""
import hashlib
import json
import sys
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import production as p

def event(code,height=0,ret=0.,**changes):
    row=dict(ts_code=code,eligible=True,price_ok=True,limit_ok=True,ref_ok=True,height=height,
             U=height>0,Touch=height>0,Z=False,Dn=False,H2L=False,ret=ret,oret=ret,cC=1000,cP=1000)
    row.update(changes)
    return row

def statistics_frame(rows):
    x=pd.DataFrame(rows)
    x['amount']=100.;x['previous_height']=0;x['prev_eligibility']=False
    return x

def test_frozen_cohort_separates_suspension_missing_and_unusable_and_keeps_new_ST():
    yesterday=pd.DataFrame([event('600001.SH',2),event('600002.SH',3),event('600003.SH',1),event('600004.SH',1)])
    today=pd.DataFrame([event('600003.SH',0,.02,eligible=False),event('600004.SH',0,.01,ref_ok=False)])
    c=p.matched_cohort(yesterday,today,{'600001.SH'})
    assert len(c)==4
    assert c.paused.sum()==1 and c.missing.sum()==1
    assert c.return_obs.tolist()==[False,False,True,False]
    assert c.promotion_obs.tolist()==[False,False,True,False]
    r,_=p.raw_statistics(statistics_frame(today.to_dict('records')),c,'eligible','height',True,True)
    assert r['P_n']==1 and r['P_close']==pytest.approx(.02)
    assert r['n_h1']==1 and r['k_h1']==0
    assert r['cohort_unobservable_n']==1
    assert r['cohort_original_n']==sum(r[k] for k in ['cohort_suspended_n','cohort_missing_n','cohort_observed_n','cohort_unobservable_n'])

def test_high_levels_selected_before_observability_and_all_ties_equal_weight():
    yesterday=pd.DataFrame([event('600001.SH',6),event('600002.SH',5),event('600003.SH',4),event('600004.SH',4),event('600005.SH',3)])
    today=pd.DataFrame([event('600002.SH',0,.01,cC=1010),event('600003.SH',0,-.01,cC=990),event('600004.SH',0,0),event('600005.SH',4,.1,cC=1100)])
    c=p.matched_cohort(yesterday,today,set())
    r,details=p.raw_statistics(statistics_frame(today.to_dict('records')),c,'eligible','height',True,True)
    assert r['H_original_n']==4 and r['H_n']==3
    assert r['H_score']==pytest.approx((75+35+50)/3)
    assert details.high_selected.tolist()==[True,True,True,True,False]
    assert r['max_h']==4

def test_high_outcome_priority_and_cent_boundary():
    c=pd.DataFrame([event('a',1,.1,H2L=True,Dn=True),event('b',1,.1,Dn=True),event('c',1,-.1),
                    event('d',0,-.049999999,cC=950),event('e',0,-.049,cC=951),event('f',0,1e-15,cC=1000),event('g',0,.01,cC=1010)])
    c['promoted']=[True,True,True,False,False,False,False]
    assert p.high_outcomes(c).tolist()==[0,0,100,15,35,50,75]
    assert p.pennies([1.005,2.675,np.nan]).tolist()==[101,268,-1]

def test_resume_resets_market_height_but_legacy_crosses_gap():
    codes=np.array(['a','a','a','a','b']);ups=np.array([True,True,True,False,True]);market=np.array([0,1,3,4,4])
    assert p.board_heights(codes,ups,market,True).tolist()==[1,2,1,0,1]
    assert p.board_heights(codes,ups,market,False).tolist()==[1,2,3,0,1]

def test_inclusive_name_boundary_latest_start_and_real_conflict():
    e=pd.DataFrame({'ts_code':['600001.SH']*3,'trade_date':['20260102','20260105','20260106'],
                    'board':['main']*3,'regime':['n10']*3,'ref_ok':[True]*3})
    names=pd.DataFrame([
        ['600001.SH','普通名','20200101','20260105','20191231','a'],
        ['600001.SH','ST测试','20260105',None,'20260104','b'],
        ['600001.SH','冲突普通名','20260106',None,'20260105','c'],
        ['600001.SH','ST冲突名','20260106',None,'20260105','d']],
        columns=['ts_code','name','start_date','end_date','ann_date','raw_record_hash'])
    out=p.resolve_history(e,names,pd.DataFrame(columns=['ts_code','trade_date','name']))
    assert out.historical_st.tolist()==[0,1,0] # last day falls back to identifiable pre-July main-board 10% regime.
    assert out.name_overlap.tolist()==[False,True,True]
    assert out.latest_name_st_conflict.tolist()==[False,False,True]
    assert out.name_source.astype(str).tolist()==['namechange','namechange','identifiable_limit_regime']

def test_amount_missing_nonpositive_and_whole_day_not_zero():
    x=statistics_frame([event('a'),event('b'),event('c')]);x.amount=[100.,np.nan,-2.]
    c=p.matched_cohort(x.iloc[:0],x,set())
    r,_=p.raw_statistics(x,c,'eligible','height',True,True)
    assert r['amount']==100000 and r['amount_observed_n']==1 and not r['amount_complete']
    x.amount=np.nan
    r,_=p.raw_statistics(x,c,'eligible','height',True,True)
    assert np.isnan(r['amount']) and r['amount_observed_n']==0

def test_cached_raw_frames_physically_cutoff_before_builder(tmp_path):
    root=tmp_path/'db';root.mkdir();cache_root=tmp_path/'cache'
    digests={}
    for name in ['db_cn_basic.db','db_cn_kpl.db']:
        (root/name).write_bytes(b'bounded raw-cache fixture');digests[name]=p.file_sha(root/name)
    fingerprint=hashlib.sha256(json.dumps(digests,sort_keys=True).encode()).hexdigest();start='20241101';end='20260331'
    key=hashlib.sha256((fingerprint+'|'+str(root.resolve())+'|'+start).encode()).hexdigest()[:20]
    cache=cache_root/key;cache.mkdir(parents=True)
    tables={name:pd.DataFrame({'trade_date':['20260331','20260401'],'ts_code':['600001.SH']*2}) for name in ['daily','limits','kpl','suspends']}
    tables.update(calendar=pd.DataFrame({'cal_date':['20260331','20260401']}),names=pd.DataFrame({'start_date':['20200101','20260401']}),basic=pd.DataFrame({'ts_code':['600001.SH']}))
    for name,frame in tables.items():frame.to_parquet(cache/f'{name}.parquet',index=False)
    (cache/'metadata.json').write_text(json.dumps({'input_fingerprint':fingerprint,'read_end':'20260930','tables':list(tables)}))
    out,_,_=p.load_inputs(root,start,end,cache_dir=cache_root)
    for name in ['daily','limits','kpl','suspends']:assert out[name].trade_date.tolist()==['20260331']
    assert out['calendar'].cal_date.tolist()==['20260331']
    assert out['names'].start_date.tolist()==['20200101']

def synthetic_tables():
    cal=pd.bdate_range('2024-11-01',periods=32).strftime('%Y%m%d').tolist()
    days=cal[-3:];rows=[];limits=[]
    # A second market day has no price rows: fixed cohort survives as missing;
    # final day is a resumption, so height must start at one.
    for day in [days[0],days[2]]:
        rows.append(dict(ts_code='600001.SH',trade_date=day,open=10.,high=11.,low=10.,close=11.,pre_close=10.,vol=10.,amount=100.))
        limits.append(dict(ts_code='600001.SH',trade_date=day,up_limit=11.,down_limit=9.))
    return dict(daily=pd.DataFrame(rows),limits=pd.DataFrame(limits),basic=pd.DataFrame([dict(ts_code='600001.SH',name='测试',list_date=cal[0],delist_date='')]),
                calendar=pd.DataFrame({'cal_date':cal}),names=pd.DataFrame([dict(ts_code='600001.SH',name='测试',start_date='20200101',end_date=None,ann_date='20191231',raw_record_hash='a')]),
                kpl=pd.DataFrame(columns=['ts_code','trade_date','name']),suspends=pd.DataFrame(columns=['ts_code','trade_date','suspend_type','suspend_timing'])),days

def nominal_tables(code,day,pre,up,down,open_=None,high=None,low=None,close=None,with_name=True):
    """A seasoned stock with a specified two-ended nominal limit pair."""
    cal=pd.bdate_range(end=day,periods=32).strftime('%Y%m%d').tolist()
    open_=pre if open_ is None else open_;close=up if close is None else close
    high=max(open_,close) if high is None else high;low=min(open_,close) if low is None else low
    name_rows=[dict(ts_code=code,name='普通名',start_date='20200101',end_date=None,ann_date='20191231',raw_record_hash='a')]
    return dict(daily=pd.DataFrame([dict(ts_code=code,trade_date=day,open=open_,high=high,low=low,close=close,pre_close=pre,vol=10.,amount=100.)]),
                limits=pd.DataFrame([dict(ts_code=code,trade_date=day,up_limit=up,down_limit=down)]),
                basic=pd.DataFrame([dict(ts_code=code,name='普通名',list_date=cal[0],delist_date='')]),calendar=pd.DataFrame({'cal_date':cal}),
                names=pd.DataFrame(name_rows if with_name else [],columns=['ts_code','name','start_date','end_date','ann_date','raw_record_hash']),
                kpl=pd.DataFrame(columns=['ts_code','trade_date','name']),suspends=pd.DataFrame(columns=['ts_code','trade_date','suspend_type','suspend_timing']))

@pytest.mark.parametrize('code,day,open_,high,low,close',[
    ('600225.SH','20250221',.24,.26,.24,.26),
    # These OHLC values and the .24/.26/.22 nominal pair are frozen raw-cache rows.
    ('000584.SZ','20250624',.25,.26,.24,.26),
    ('000584.SZ','20250702',.24,.25,.23,.23),
])
def test_observed_low_price_ten_percent_pairs_are_formal_only(code,day,open_,high,low,close):
    tables=nominal_tables(code,day,.24,.26,.22,open_,high,low,close)
    e,_=p.build_events(tables);r=e.iloc[0]
    assert r.regime=='n10' and r.ref_ok and r.eligible
    assert r.legacy_regime=='other' and not r.legacy_ref_ok
    assert not r.eligible_legacy and not r.eligible_history_own
    assert not r.nominal_regime_ambiguous

@pytest.mark.parametrize('code,pre,up,down,regime',[
    ('600001.SH',.07,.07,.07,'st5'),
    ('300001.SZ',.03,.04,.02,'n20'),
    ('688001.SH',10.,12.,8.,'n20'),
])
def test_unique_nominal_matches_respect_board_and_both_limit_ends(code,pre,up,down,regime):
    tables=nominal_tables(code,'20250221',pre,up,down)
    e,_=p.build_events(tables)
    assert e.regime.iloc[0]==regime and e.ref_ok.iloc[0]
    tables['limits'].loc[0,'down_limit']=down+.01
    bad,_=p.build_events(tables)
    assert bad.regime.iloc[0]=='other' and not bad.ref_ok.iloc[0]

def test_main_board_ambiguous_five_and_ten_percent_pairs_remain_unknown():
    tables=nominal_tables('600001.SH','20250221',.03,.03,.03)
    e,cal=p.build_events(tables);r=e.iloc[0]
    assert r.nominal_regime_ambiguous and r.regime=='other'
    assert not r.ref_ok and not r.eligible
    assert r.eligibility_reason=='ambiguous_nominal_limit_regime'
    _,q,_=p.compute_raw(e,cal,tables['suspends'])
    assert q.ambiguous_nominal_regime_n.tolist()==[1]
    # The board contract offers only the 20% candidate for a 20pct stock.
    e20,_=p.build_events(nominal_tables('300001.SZ','20250221',.01,.01,.01))
    assert e20.regime.iloc[0]=='n20' and e20.ref_ok.iloc[0]
    assert not e20.nominal_regime_ambiguous.iloc[0]

def test_history_fallback_and_legacy_ST_use_their_respective_regimes():
    normal,_=p.build_events(nominal_tables('600001.SH','20250221',.24,.26,.22,with_name=False))
    r=normal.iloc[0]
    assert r.historical_st==0 and r.name_source=='identifiable_limit_regime'
    assert r.historical_st_history_own==-1 and r.name_source_history_own=='unknown'
    assert r.eligible and not r.eligible_history_own and not r.eligible_legacy
    st,_=p.build_events(nominal_tables('600001.SH','20250221',.07,.07,.07,with_name=False))
    r=st.iloc[0]
    assert r.regime=='st5' and r.historical_st==1
    assert r.legacy_regime=='other' and not r.legacy_st and r.historical_st_history_own==-1

def test_exact_nominal_match_still_requires_valid_prices_and_limits():
    tables=nominal_tables('600001.SH','20250221',.24,.26,.22)
    tables['daily'].loc[0,'high']=.25
    e,_=p.build_events(tables)
    assert e.regime.iloc[0]=='n10' and not e.price_ok.iloc[0]
    assert not e.ref_ok.iloc[0] and not e.eligible.iloc[0]
    tables['limits'].loc[0,'down_limit']=np.nan
    e,_=p.build_events(tables)
    assert not e.limit_ok.iloc[0] and e.regime.iloc[0]=='other' and not e.ref_ok.iloc[0]

def test_unmatched_nominal_pair_is_counted_outside_validated_candidates():
    tables=nominal_tables('600001.SH','20250221',.24,.26,.23)
    e,cal=p.build_events(tables)
    assert e.regime.iloc[0]=='other' and not e.nominal_regime_ambiguous.iloc[0]
    assert e.eligibility_reason.iloc[0]=='unmatched_or_special_limit_regime'
    assert e.legacy_eligibility_reason.iloc[0]=='special_limit_regime'
    _,q,_=p.compute_raw(e,cal,tables['suspends'])
    assert q.nominal_regime_unmatched_n.tolist()==[1]
    assert q.ref_candidate_n.tolist()==[0] and q.ref_validated_n.tolist()==[0]
    assert (q.market_observed_n==q.ref_candidate_n+q.ambiguous_nominal_regime_n+q.nominal_regime_unmatched_n).all()

def test_legacy_and_history_own_sequences_keep_original_low_price_exclusion():
    tables,days=synthetic_tables()
    tables['daily']=pd.DataFrame([
        dict(ts_code='600001.SH',trade_date=day,open=pre,high=up,low=pre,close=up,pre_close=pre,vol=10.,amount=100.)
        for day,pre,up in zip(days,[10.,.24,.24],[11.,.26,.26])])
    tables['limits']=pd.DataFrame([
        dict(ts_code='600001.SH',trade_date=day,up_limit=up,down_limit=down)
        for day,up,down in zip(days,[11.,.26,.26],[9.,.22,.22])])
    e,cal=p.build_events(tables)
    legacy,lq,lc=p.compute_raw(e,cal,tables['suspends'],'legacy',True)
    history,hq,hc=p.compute_raw(e,cal,tables['suspends'],'history_own',True)
    pd.testing.assert_frame_equal(legacy,history)
    for col,expected in {'N':[1,0,0],'U':[1,0,0],'max_h':[1,0,0],'cls_U':[1,0,0],
                         'cohort_original_n':[0,1,0],'n_h1':[0,1,0],'k_h1':[0,1,0]}.items():
        assert legacy[col].tolist()==expected
    assert legacy.P_close.iloc[1]==pytest.approx(.26/.24-1)
    assert legacy.cohort_unobservable_n.tolist()==[0,0,0]
    assert lq.ref_candidate_n.tolist()==[1,0,0] and hq.ref_candidate_n.tolist()==[1,0,0]
    assert not lc.ref_ok.any() and not hc.ref_ok.any()
    formal,_,cohort=p.compute_raw(e,cal,tables['suspends'],'formal',True)
    assert formal.N.tolist()==[1,1,1] and formal.cohort_original_n.tolist()==[0,1,1]
    assert formal.cohort_unobservable_n.tolist()==[0,0,0] and cohort.return_obs.all()
    assert formal.n_h2.tolist()==[0,0,1] and formal.k_h2.tolist()==[0,0,1]

def test_whole_price_day_missing_keeps_calendar_cohort_and_rolling_unknown():
    tables,days=synthetic_tables();events,cal=p.build_events(tables)
    raw,q,c=p.compute_raw(events,cal,tables['suspends'],'formal',True)
    assert raw.date.tolist()==days
    missing=raw[raw.date==days[1]].iloc[0]
    assert missing.N==0 and missing.cohort_original_n==1 and missing.cohort_missing_n==1
    assert np.isnan(missing.amount) and np.isnan(missing.BL) and np.isnan(missing.Ddens)
    assert events.height.tolist()==[1,1]
    assert q[q.date==days[1]].market_observed_n.iloc[0]==0

def test_delisting_marker_changes_comparison_only():
    tables,_=synthetic_tables();tables['names']['name']='测试退'
    events,cal=p.build_events(tables)
    assert events.eligible.all() and not events.broad_eligible.any()
    raw,q,_=p.compute_raw(events,cal,tables['suspends'])
    assert raw.comparison_delisting_excluded_n.tolist()==[1,0,1]
    assert raw.cls_U.tolist()==[0,0,0] and raw.U.tolist()==[1,0,1]

def test_invalid_internal_OHLC_is_local_exclusion_and_legacy_flags_remain_traceable():
    tables,_=synthetic_tables();tables['daily'].loc[0,'high']=10.5
    events,_=p.build_events(tables)
    assert events.price_ok.tolist()==[False,True]
    assert events.eligible.tolist()==[False,True]
    assert events.eligibility_reason.astype(str).iloc[0]=='invalid_price'
    assert events.legacy_U.tolist()==[True,True] and events.U.tolist()==[False,True]

def test_complete_calendar_even_when_first_prices_are_missing():
    tables,days=synthetic_tables();events,cal=p.build_events(tables)
    raw,_,_=p.compute_raw(events,cal,tables['suspends'],'formal',start=cal[0])
    assert raw.date.tolist()==cal
    assert raw.iloc[0].N==0 and np.isnan(raw.iloc[0].amount)

def test_optional_baseline_preserves_formal_score_and_labels_unrun_attribution():
    # Use raw columns from a single synthetic day, sufficient to exercise scoring
    # with explicit fixed priors/anchors rather than calibrating this fixture.
    tables,_=synthetic_tables();events,cal=p.build_events(tables);raw,_,_=p.compute_raw(events,cal,tables['suspends'])
    anchors={c:[0.,1.] for c in ['M1raw','P_open','P_close','C_open','C_close','BL','SQ','Ddens','MIDfail','lr20']}
    calibration={'version':'fixture','priors':{g:.25 for g in ['h1','h2','h3','h4','b1','b2','b3','mid']},'anchors':anchors,'panic_ddens_p90':1.}
    diff,stages=p.differences_without_baseline(raw,calibration)
    assert stages==[] and not diff.common_date.any()
    assert diff.attribution_basis.eq('not_run_no_B0_input').all()
    assert diff.score_B0.isna().all() and diff.delta_total.isna().all()

def test_parameterized_cli_outputs_without_private_B0(tmp_path,monkeypatch):
    tables,days=synthetic_tables()
    # Shift the fixture to 2026 so the exported public contract is nonempty.
    old_dates=tables['calendar'].cal_date.tolist();new_dates=pd.bdate_range('2026-01-05',periods=32).strftime('%Y%m%d').tolist()
    mapping=dict(zip(old_dates,new_dates))
    tables['calendar']['cal_date']=new_dates
    for name in ['daily','limits']:tables[name]['trade_date']=tables[name].trade_date.map(mapping)
    tables['basic']['list_date']=new_dates[0]
    root=tmp_path/'live';root.mkdir();output=tmp_path/'formal'
    with sqlite3.connect(root/'db_cn_basic.db') as c:
        for name,table in [('daily','tbl_cn_day'),('limits','tbl_cn_stk_limit'),('basic','tbl_cn_basic'),('names','tbl_cn_namechange'),('suspends','tbl_cn_suspend')]:tables[name].to_sql(table,c,index=False)
        calendar=tables['calendar'].rename(columns={'cal_date':'cal_date'}).copy();calendar['exchange']='SSE';calendar['is_open']=1
        calendar.to_sql('tbl_cn_tradecal',c,index=False)
    with sqlite3.connect(root/'db_cn_kpl.db') as c:tables['kpl'].to_sql('tbl_cn_kpl_list',c,index=False)
    anchors={col:[0.,1.] for col in ['M1raw','P_open','P_close','C_open','C_close','BL','SQ','Ddens','MIDfail','lr20']}
    fixed=tmp_path/'fixed.json';fixed.write_text(json.dumps({'version':'fixture','priors':{g:.25 for g in ['h1','h2','h3','h4','b1','b2','b3','mid']},'anchors':anchors,'panic_ddens_p90':1.}))
    monkeypatch.setattr(sys,'argv',['production.py','--data-root',str(root),'--output',str(output),'--end',new_dates[-1],'--fixed-calibration',str(fixed)])
    p.main()
    daily=pd.read_csv(output/'sentiment_daily.csv')
    validation=json.loads((output/'production_validation.json').read_text())
    assert len(daily)==32 and not daily.trade_date.duplicated().any()
    assert validation['all_deterministic_checks_pass']
    assert validation['attribution_status']=='not_run_no_B0_input'
    assert {'ambiguous_nominal_regime_n','nominal_regime_unmatched_n'}.issubset(validation['remaining_quality_totals'])
    assert {'score5','quality_status','eligible_n','available_at_basis'}.issubset(daily.columns)
    assert (output/'events.parquet').is_file() and (output/'cohorts.parquet').is_file()
