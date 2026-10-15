from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from study_fifteen_min_ma_coil import features,Rules as CoilRules,slots
from study_second_wave_template import quiet_bases,find_signals,outcome_path,TemplateRules,daily_setup,theme_snapshot


def intraday_sample():
    cal=pd.bdate_range('2026-01-05',periods=30).strftime('%Y%m%d').tolist()
    times=slots(cal)
    raw=pd.DataFrame(dict(datetime=times,open=10.,high=10.05,low=9.95,close=10.,vol=1000.,adj_factor=1.))
    raw.loc[120:127,['high','low','vol']]=[10.01,9.99,100.]
    raw.loc[128,['open','high','low','close','vol']]=[10.,10.12,10.,10.11,2000.]
    f=features(raw,cal,CoilRules())
    context={cal[8]:dict(eligible=True,wave_start_date='20251201',context_date=cal[7])}
    return raw,cal,f,context


def test_signal_and_discovered_base_do_not_see_future_corruption():
    raw,cal,f,context=intraday_sample()
    r=replace(TemplateRules(),trigger_body_fraction=.5,trigger_close_location=.7)
    s=find_signals(f,context,cal[8],cal[8],r)
    assert len(s)==1
    raw.loc[129:,['close','high','low','adj_factor']]=np.nan
    bad=features(raw,cal,CoilRules())
    assert s==find_signals(bad,context,cal[8],cal[8],r)
    base=s[0]['base'];assert pd.Timestamp(base['base_end'])<pd.Timestamp(s[0]['signal_time'])
    end=base['end_index'];assert quiet_bases(f,r)[end]==quiet_bases(f.iloc[:end+1],r)[end]


def test_daily_setup_ignores_future_peak_and_future_volume():
    dates=pd.bdate_range('2024-09-02',periods=360).strftime('%Y%m%d')
    d=pd.DataFrame(dict(trade_date=dates,valid_day=True,high=10.,low=9.9,close=10.,raw_vol=100.,atr14_pct=2.))
    for n in (5,10,20,30,60,120):d[f'ma{n}']=10.;d[f'rise{n}']=30
    launch=str(dates[300]);cutoff=str(dates[320])
    d.loc[300:310,'high']=np.linspace(10.5,12.5,11);d.loc[300:310,'raw_vol']=500.
    d.loc[311:320,['low','high','close','raw_vol','atr14_pct']]=[9.95,10.1,10.,300.,1.]
    d.loc[311:320,'atr14_pct']=np.linspace(2.,1.,10)
    e=pd.DataFrame([dict(trade_date=str(dates[301]),wave_start_date=launch,qualifying_board=2,name='测试股份',lu_desc='化工',board_type='continuous',ts_code='000001.SZ')])
    before=daily_setup(d,e,cutoff)
    d.loc[321:,['high','raw_vol','atr14_pct']]=[500.,100000.,100.]
    assert before==daily_setup(d,e,cutoff)
    assert before['eligible']


def path_sample():
    _,cal,f,_=intraday_sample()
    stamp=f.datetime.iloc[128]
    s=dict(signal_time=str(stamp),signal_close=10.11,base=dict(lower=9.9,upper=10.,adj_factor=1.))
    return cal,f,s


def test_outcome_never_counts_signal_candle_high_or_same_bar_fill():
    cal,f,s=path_sample();f.loc[128,'high']=100.
    result=outcome_path(f,s,cal)
    assert result['entry_price']==10.
    assert result['max_favourable_pct']==pytest.approx(.5)
    assert not result['reached_25_20d']


def test_intrabar_target_space_is_separate_from_sustained_rise():
    cal,f,s=path_sample();f.loc[140,'high']=12.6
    r=outcome_path(f,s,cal)
    assert r['intact_25_20d'] and not r['sustained_intact_25_20d']
    f.loc[150,'close']=9.8;f.loc[150,'low']=9.7;f.loc[170,'high']=13.5
    r=outcome_path(f,s,cal)
    assert r['reached_30_20d'] and not r['intact_30_20d']


def test_t_plus_one_gap_loss_can_exceed_frozen_floor_distance():
    cal,f,s=path_sample();f.loc[130,['close','low']]=[9.8,9.7]
    f.loc[144,['open','high','low','close']]=[8.7,8.8,8.6,8.7]
    r=outcome_path(f,s,cal)
    assert r['failure_sell_proxy_time']==str(f.datetime.iloc[144])
    assert r['failure_sell_proxy_return_pct']==pytest.approx(-13.)
    assert r['failure_sell_proxy_return_pct'] < -r['floor_risk_pct']


def test_unfilled_next_one_price_bar_and_outcome_gaps_remain_in_denominator():
    cal,f,s=path_sample();f.loc[129,['open','high','low','close']]=11.
    r=outcome_path(f,s,cal);assert not r['evaluable'] and 'no_fill' in r['execution_status']
    cal,f,s=path_sample();f.loc[170,'valid_bar']=False
    r=outcome_path(f,s,cal);assert not r['evaluable'] and r['execution_status']=='entered_but_outcome_gap'


def test_theme_context_uses_prior_close_and_excludes_st_ipo():
    cal=pd.bdate_range('2026-01-05',periods=6).strftime('%Y%m%d').tolist()
    k=pd.DataFrame([
        dict(trade_date=cal[4],ts_code='000001.SZ',name='甲',lu_desc='化工',theme='化工'),
        dict(trade_date=cal[4],ts_code='000002.SZ',name='ST乙',lu_desc='化工',theme='化工'),
        dict(trade_date=cal[4],ts_code='000003.SZ',name='丙',lu_desc='次新股',theme='化工'),
        dict(trade_date=cal[5],ts_code='000004.SZ',name='丁',lu_desc='化工',theme='化工')])
    s=dict(context_date=cal[4],prior_theme_name='化工',ts_code='000001.SZ')
    result=theme_snapshot(k,s,cal)
    assert result['theme_prevday_limitup_count']==1
    assert result['theme_last5_peer_codes']=='000001.SZ'
    assert not result['contemporaneous_catalyst_verified']
    assert result==theme_snapshot(k.iloc[:3],s,cal)
