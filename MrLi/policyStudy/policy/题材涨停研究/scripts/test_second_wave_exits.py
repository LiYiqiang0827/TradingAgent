from dataclasses import replace
from copy import deepcopy
import numpy as np
import pandas as pd
import pytest
from study_fifteen_min_ma_coil import slots
from study_second_wave_exits import ExitRules, State, theme_context, stock_theme_timeline, context_for, replay, blocked_sell, sellable, quote_shares, completed_decision, reentry_decision

def sample():
    cal=pd.bdate_range('2026-01-05',periods=24).strftime('%Y%m%d').tolist();times=slots(cal)
    rows=[]
    for t in times:
        r=dict(datetime=t,valid_bar=True,open=10.2,high=10.25,low=10.15,close=10.2,vol=1000.,body_pct=0.,body_fraction=0.,close_location=.5)
        for n in (5,10,20,30,60,120):r[f'm{n}']=10.;r[f'rise{n}']=0
        rows.append(r)
    frames={'000001.SZ':dict(datetime=times,rows=rows)}
    s=dict(ts_code='000001.SZ',name='甲',signal_time=str(times[0]),signal_open=10.,signal_close=10.1,
           prior_theme_name='化工',template_quality_score=80.,known_core_development_case=False,base=dict(lower=9.8,upper=10.))
    return frames,[s],cal,{}, {},{'000001.SZ':{}},replace(ExitRules(),horizon_sessions=4)

def run_sample(data,**kw):
    f,s,c,t,l,b,r=data
    return replay(f,s,c,t,l,b,rules=r,initial_cash=100000.,isolated=True,**kw)

def test_decision_close_fills_only_following_open_not_signal_high():
    d=sample();d[0]['000001.SZ']['rows'][0]['high']=100.
    x=run_sample(d,policy='floor_only');buy=x['ledger'][0]
    assert buy['execution_bar'].endswith('10:00:00')
    assert buy['execution_time'].endswith('09:45:00')
    assert buy['price']==pytest.approx(10.2*1.0005)

def test_floor_failed_buy_day_waits_for_t_plus_one_and_gap():
    d=sample();rows=d[0]['000001.SZ']['rows'];rows[2].update(close=9.7,low=9.65)
    rows[16].update(open=8.,high=8.1,low=7.95,close=8.05)
    x=run_sample(d,policy='floor_only');sell=x['ledger'][1]
    assert sell['execution_time']=='2026-01-06 09:30:00'
    assert sell['open_proxy']==8.
    assert x['summary']['issues']['T_plus_1_wait']>0
    assert x['summary']['net_return_pct'] < -20

def test_opening_gap_standing_floor_exits_without_waiting_current_close():
    d=sample();d[0]['000001.SZ']['rows'][16].update(open=9.5,low=9.4,close=10.5,high=10.6)
    d[0]['000001.SZ']['rows'][16]['m20']=100. # unavailable current-candle value at OPEN
    x=run_sample(d,policy='floor_only');sell=x['ledger'][1]
    assert sell['reason']=='opening_gap_below_floor'
    assert sell['decision_time']==sell['execution_time']=='2026-01-06 09:30:00'
    decision=x['decisions'][-1]
    assert decision['ma20']==d[0]['000001.SZ']['rows'][15]['m20']

def test_down_limit_sell_blocked_but_up_limit_one_price_can_sell():
    assert blocked_sell(dict(valid_bar=True,open=9.,high=9.,low=9.,close=9.),9.)
    assert not blocked_sell(dict(valid_bar=True,open=11.,high=11.,low=11.,close=11.),9.)
    d=sample();rows=d[0]['000001.SZ']['rows'];rows[2].update(close=9.7,low=9.65)
    for i in (16,17):rows[i].update(open=9.,high=9.,low=9.,close=9.)
    d[4][('000001.SZ',d[2][1])]=dict(down_limit=9.)
    x=run_sample(d,policy='floor_only')
    assert x['ledger'][1]['execution_bar'].endswith('10:15:00')
    assert x['summary']['issues']['down_limit_unfilled']==2

def test_one_price_buy_not_deferred_into_future_winner():
    d=sample();d[0]['000001.SZ']['rows'][1].update(open=11.,high=11.,low=11.,close=11.)
    x=run_sample(d,policy='floor_only')
    assert not x['ledger'];assert x['results'][0]['status']=='unfilled';assert x['summary']['final_equity']==100000.

def test_lots_and_cash_never_allow_t_plus_zero_or_borrowing():
    lots=[dict(shares=500,buy_day='20260105'),dict(shares=300,buy_day='20260106')]
    assert sellable(lots,'20260106')==500
    assert quote_shares(100000.,10.,1000.,ExitRules())==0 # minimum fee makes one lot unaffordable
    x=run_sample(sample(),policy='phased')
    assert x['curve'].cash.min()>=0
    assert x['ledger'][0]['shares']<=4900

def test_future_price_corruption_does_not_change_past_orders():
    d=sample();a=run_sample(d,policy='trend');modified=deepcopy(d)
    for row in modified[0]['000001.SZ']['rows'][32:]:row.update(open=100.,close=100.,high=101.,low=99.)
    b=run_sample(modified,policy='trend');cut='2026-01-06 15:00:00'
    assert [r for r in a['ledger'] if r['execution_bar']<=cut]==[r for r in b['ledger'] if r['execution_bar']<=cut]
    assert [r for r in a['decisions'] if r['decision_time']<=cut]==[r for r in b['decisions'] if r['decision_time']<=cut]

def test_theme_only_previous_completed_day_and_no_future_peak():
    cal=['20260105','20260106','20260107'];k=pd.DataFrame([
        dict(trade_date=cal[0],ts_code='000001.SZ',name='甲',lu_desc='化工',tag='涨停',status='2连板',lu_time='09:35:00'),
        dict(trade_date=cal[1],ts_code='000002.SZ',name='乙',lu_desc='化工',tag='涨停',status='8连板',lu_time='09:35:00')])
    a,_=theme_context(k,cal,['化工']);changed=k.copy();changed.loc[1,'status']='1连板';b,_=theme_context(changed,cal,['化工'])
    assert ('化工',cal[0]) not in a
    assert a[('化工',cal[1])]==b[('化工',cal[1])]
    assert a[('化工',cal[1])]['as_of']==cal[0]
    assert a[('化工',cal[2])]!=b[('化工',cal[2])]

def test_unknown_source_day_does_not_become_zero_heat():
    k=pd.DataFrame([dict(trade_date='20260105',ts_code='000001.SZ',name='甲',lu_desc='化工',tag='涨停',status='首板',lu_time='09:35:00')])
    x,_=theme_context(k,['20260105','20260106','20260107'],['化工'])
    assert not x[('化工','20260107')]['known'] and x[('化工','20260107')]['heat'] is None

def test_theme_cooling_needs_price_weakness_not_just_declining_heat():
    d=sample();s=d[1][0];st=State(s,0,100,9.8,10.,10.,entry_index=1,entry_price=10.2,risk=.04,peak_high=10.3,peak_close=10.3)
    row=d[0]['000001.SZ']['rows'][3];h=[row]*4
    assert completed_decision(st,row,3,h,dict(cooling=True),'phased_theme',d[6]) is None
    row['m20']=10.3
    assert completed_decision(st,row,3,h,dict(cooling=True),'phased_theme',d[6])[2]=='theme_cooling_with_price_weakness'

def test_big_bear_uses_absolute_body_fraction():
    d=sample();st=State(d[1][0],0,100,9.8,10.,10.,entry_index=1,entry_price=10.,risk=.02,peak_high=12.,peak_close=12.,trend_armed=True,accelerated_until=12)
    row={**d[0]['000001.SZ']['rows'][5], 'open':12.,'close':11.7,'body_fraction':-.8}
    assert completed_decision(st,row,5,[row]*6,{},'trend',d[6])[2]=='acceleration_failed'

def test_data_exclusion_stays_in_full_denominator_without_fictional_pnl():
    d=sample();key=d[1][0]['ts_code']+'_'+d[1][0]['signal_time']
    x=run_sample(d,excluded={key:'corporate_action_accounting_unmodeled'})
    assert len(x['results'])==1 and x['results'][0]['status']=='excluded'
    assert x['summary']['filled_cases']==0 and x['results'][0]['pnl']==0

def test_reentry_requires_fresh_base_after_exit_and_cooldown():
    d=sample();st=State(d[1][0],0,100,9.8,10.,10.,last_exit=10,leg=1)
    row={**d[0]['000001.SZ']['rows'][20],'open':10.,'close':10.2,'body_pct':2.,'body_fraction':.8,'close_location':.9,'vol':1000.}
    for n,value in zip((5,10,20,30),(10.15,10.1,10.05,10.)):row[f'm{n}']=value;row[f'rise{n}']=2
    base=dict(start_index=7,base_start='2026-01-05 11:30:00',upper=10.05,lower=9.9,volume_mean=100.)
    assert reentry_decision(st,row,20,{19:[base]},[row]*8,{},'phased',d[6]) is None
    base['start_index']=12
    assert reentry_decision(st,row,20,{19:[base]},[row]*8,{},'phased',d[6])==base
    assert reentry_decision(st,row,20,{19:[base]},[row]*8,dict(warming=False),'phased_theme',d[6]) is None

def test_expired_reentry_platform_cannot_be_restored_using_later_recovery():
    d=sample();st=State(d[1][0],0,100,9.8,10.,10.,last_exit=10,leg=1)
    row={**d[0]['000001.SZ']['rows'][20],'open':10.,'close':10.2,'body_pct':2.,'body_fraction':.8,'close_location':.9,'vol':1000.}
    for n,value in zip((5,10,20,30),(10.15,10.1,10.05,10.)):row[f'm{n}']=value;row[f'rise{n}']=2
    base=dict(start_index=12,base_start='2026-01-05 14:15:00',upper=10.05,lower=9.9,volume_mean=100.)
    rows=deepcopy(d[0]['000001.SZ']['rows']);rows[19]['close']=9.8;rows[20]=row
    assert reentry_decision(st,row,20,{18:[base]},[row]*8,{},'phased',d[6],rows) is None

def test_nontrading_zero_volume_slot_does_not_receive_an_order_fill():
    d=sample();d[0]['000001.SZ']['rows'][1]['vol']=0.
    x=run_sample(d,policy='floor_only')
    assert x['ledger'][0]['execution_bar'].endswith('10:15:00')
    assert x['summary']['issues']['missing_execution_bar']==1

def test_partial_can_sell_old_lot_while_todays_add_must_wait():
    d=sample();rows=d[0]['000001.SZ']['rows']
    for i,close in ((16,10.7),(17,11.1)):
        rows[i].update(close=close,high=close+.05)
        for n,value in zip((5,10,20,30),(10.6,10.5,10.4,10.3)):
            rows[i][f'm{n}']=value;rows[i][f'rise{n}']=3
    rows[18].update(close=9.7,low=9.65);rows[32].update(open=8.,high=8.1,low=7.95,close=8.05)
    x=run_sample(d,policy='phased');orders=x['ledger']
    add=next(o for o in orders if o['action']=='add')
    partial=next(o for o in orders if o['reason']=='two_R_partial_profit')
    final=next(o for o in orders if o['reason']=='platform_floor_failed' and o['action']=='sell')
    assert add['execution_time'].startswith('2026-01-06') and partial['execution_time'].startswith('2026-01-06')
    assert partial['remaining_shares']==add['shares']
    assert final['execution_time'].startswith('2026-01-07')

def test_stock_theme_updates_on_next_day_not_current_limitup_close():
    cal=['20260105','20260106','20260107'];k=pd.DataFrame([
        dict(trade_date=cal[0],ts_code='000001.SZ',name='甲',lu_desc='化工',tag='涨停'),
        dict(trade_date=cal[1],ts_code='000001.SZ',name='甲',lu_desc='机器人',tag='涨停')])
    t=stock_theme_timeline(k,cal,['000001.SZ'])
    assert ('000001.SZ',cal[0]) not in t
    assert t[('000001.SZ',cal[1])]['theme_name']=='化工'
    assert t[('000001.SZ',cal[2])]['theme_name']=='机器人'
    changed=k.copy();changed.loc[1,'lu_desc']='未来标签'
    assert stock_theme_timeline(changed,cal,['000001.SZ'])[('000001.SZ',cal[1])]==t[('000001.SZ',cal[1])]

def test_excluded_theme_names_never_generate_even_zero_heat_projection():
    k=pd.DataFrame([dict(trade_date='20260105',ts_code='000001.SZ',name='甲',lu_desc='化工',tag='涨停',status='首板',lu_time='09:35:00')])
    context,table=theme_context(k,['20260105','20260106'],['化工','ST板块','ST摘帽','次新股'])
    assert set(table.theme)=={'化工'}
    assert all(k[0]=='化工' for k in context)
