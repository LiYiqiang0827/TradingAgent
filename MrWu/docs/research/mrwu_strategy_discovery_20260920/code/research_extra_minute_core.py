"""Four primary-defined pure point mechanisms; no IO, fills or portfolio P&L.

Corrected from an unaccepted Kimi mechanical draft. The primary protocol owns
all thresholds. Only consumed slices are validated; no future-value filtering.
"""
import numpy as np

CARDS=('M03','M04','M16','M17')
RANGES={'M03':(9,119),'M04':(5,59),'M16':(124,149),'M17':(20,119)}
TRIGGER='TRIGGER_POINT_PROXY'
UNKNOWN_PRIOR='UNKNOWN_PRIOR'
UNKNOWN_CURRENT='UNKNOWN_CURRENT_CONTEXT'
NO_PRECONDITION='NO_PRECONDITION'
NO_TRIGGER='NO_TRIGGER'


def number(value):
    try:
        if isinstance(value,(bool,np.bool_)):return None
        value=float(value)
        return value if np.isfinite(value) else None
    except (TypeError,ValueError,OverflowError):return None


def _prior(card,row,ctx):
    close=number(row.get('prior_close'));amount=number(row.get('prior_amount20'))
    st=row.get('prior_st_status')
    if close is None or amount is None or not isinstance(st,str) or st not in ('ST_LISTED','NOT_LISTED_ON_ACCEPTED_DAY'):
        return UNKNOWN_PRIOR,{}
    if st=='ST_LISTED' or close<2 or amount<20000:return NO_PRECONDITION,{}
    if card in ('M04','M17'):
        adjusted=number(row.get('prior_adjclose'));ma=number(row.get('prior_ma20_adjusted'))
        if adjusted is None or ma is None:return UNKNOWN_PRIOR,{}
        if card=='M04':
            mom=number(row.get('prior_mom5'));anchor_mom=number(ctx.get('prior_anchor_mom5'))
            if mom is None or anchor_mom is None:return UNKNOWN_PRIOR,{}
            if not mom<anchor_mom:return NO_PRECONDITION,{}
        if not adjusted>ma:return NO_PRECONDITION,{}
    return None,dict(prior_close=close)


def _raw(ctx,name,ndim=1):
    try:
        values=np.asarray(ctx.get(name))
        return values if values.ndim==ndim and (ndim!=2 or values.shape[0]==2) else None
    except (TypeError,ValueError):return None


def _slice(ctx,name,start,end,*,matrix=False,positive=False,nonnegative=False,flag=False):
    """Only convert/check the consumed slice, never unrelated future values."""
    raw=_raw(ctx,name,2 if matrix else 1)
    if raw is None or start<0 or end<=start or raw.shape[-1]<end:return None
    try:values=np.asarray(raw[:,start:end] if matrix else raw[start:end],dtype=float)
    except (TypeError,ValueError,OverflowError):return None
    if not np.isfinite(values).all():return None
    if positive and not (values>0).all():return None
    if nonnegative and not (values>=0).all():return None
    if flag and not np.isin(values,[0.,1.]).all():return None
    return values


def _at(ctx,name,t,**kwargs):
    result=_slice(ctx,name,t,t+1,**kwargs)
    return None if result is None else result[:,0] if kwargs.get('matrix') else float(result[0])


def decisions(stride,lower,upper):
    if isinstance(stride,(bool,np.bool_)) or stride not in (1,5):raise ValueError('extra_minute_stride')
    return [t for t in range(int(stride)-1,upper+1,int(stride)) if t>=lower]


def _validate(ctx,card,t):
    values={}
    def need(name,value):
        if value is None:raise ValueError(name)
        values[name]=value
        return value
    try:
        need('price',_at(ctx,'price',t,positive=True))
        if need('usable',_at(ctx,'usable',t,flag=True))!=1:return None,'usable_false'
        if card!='M04':need('healthy',_at(ctx,'healthy',t,flag=True))
        if card in ('M03','M04'):
            need('past_prices',_slice(ctx,'price',t-5,t,positive=True))
            need('anchor_prices',_slice(ctx,'anchor_prices',t-5,t if card=='M03' else t+1,matrix=True,positive=True))
            need('current_anchor_returns',_at(ctx,'anchor_returns',t,matrix=True))
            need('prior_breadth',_at(ctx,'peer_breadth',t-5))
            if card=='M03':
                need('past_anchor_returns',_slice(ctx,'anchor_returns',t-5,t,matrix=True))
                need('share',_at(ctx,'participation_proxy',t))
                need('prior_share',_at(ctx,'participation_proxy',t-5))
                need('maximum_peer_share',_at(ctx,'max_participation_proxy',t))
            else:need('anchor_drawdown',_at(ctx,'anchor_drawdown',t,matrix=True))
        if card in ('M03','M04','M16'):
            need('peer_median',_at(ctx,'peer_median',t))
            need('breadth',_at(ctx,'peer_breadth',t))
            need('volume_ratio',_at(ctx,'volume_ratio',t,nonnegative=True))
        if card=='M16':
            need('morning_prices',_slice(ctx,'price',0,120,positive=True))
            need('morning_peer_median',_at(ctx,'peer_median',119))
            need('morning_breadth',_at(ctx,'peer_breadth',119))
        if card=='M17':
            need('advance_start',_at(ctx,'price',t-19,positive=True))
            need('advance_end',_at(ctx,'price',t-11,positive=True))
            need('prior10_prices',_slice(ctx,'price',t-10,t,positive=True))
            need('volumes19',_slice(ctx,'volume',t-19,t+1,nonnegative=True))
        return values,None
    except ValueError as exc:return None,str(exc)


def _m03(v,prior_close):
    platform=v['anchor_prices'][:,:5]
    eligible=np.all(v['past_anchor_returns']>=.02,axis=1)&(platform.max(axis=1)/platform.min(axis=1)-1<=.004)
    own_return=v['price']/prior_close-1
    maximum_anchor=float(np.max(v['current_anchor_returns']))
    fired=(bool(eligible.any()) and v['peer_median']>=0 and v['breadth']>=.60
        and v['breadth']-v['prior_breadth']>=.10 and v['maximum_peer_share']<.50
        and v['share']>v['prior_share'] and v['price']>max(v['past_prices'])
        and v['volume_ratio']>=1.2 and v['healthy']==1 and own_return<=maximum_anchor-.005)
    return bool(fired),dict(own_return_from_prior_close=own_return,maximum_anchor_return=maximum_anchor,
        qualifying_platform_anchors=int(eligible.sum()),own_participation_proxy=v['share'],prior_participation_proxy=v['prior_share'],
        peer_breadth=v['breadth'],breadth_change=v['breadth']-v['prior_breadth'],volume_ratio_previous_session=v['volume_ratio'])


def _m04(v,prior_close):
    anchors=v['anchor_prices'];mean=anchors[:,:5].mean(axis=1)
    eligible=(v['current_anchor_returns']>0)&(anchors[:,-1]>mean)&(v['anchor_drawdown']>-.008)
    fired=(bool(eligible.all()) and v['price']>max(v['past_prices']) and v['volume_ratio']>=1.2
        and v['peer_median']>=0 and v['breadth']>=.55 and v['breadth']-v['prior_breadth']>=.05)
    return bool(fired),dict(anchor_returns=v['current_anchor_returns'].tolist(),anchor_prior5_means=mean.tolist(),
        anchor_current_prices=anchors[:,-1].tolist(),own_return_from_prior_close=v['price']/prior_close-1,
        breadth_change=v['breadth']-v['prior_breadth'],volume_ratio_previous_session=v['volume_ratio'])


def _m16(v,prior_close):
    morning=v['morning_prices'];drawdown=morning[119]/morning.max()-1
    own_return=v['price']/prior_close-1
    fired=(v['morning_peer_median']>=.005 and drawdown>=-.015 and v['peer_median']>v['morning_peer_median']
        and v['breadth']>=.60 and v['breadth']-v['morning_breadth']>=.10 and v['healthy']==1
        and v['price']>max(morning[90:120]) and v['volume_ratio']>=1.2 and own_return>0)
    return bool(fired),dict(morning_drawdown=drawdown,morning_last30_high=float(max(morning[90:120])),
        morning_peer_median=v['morning_peer_median'],peer_median=v['peer_median'],
        breadth_change=v['breadth']-v['morning_breadth'],own_return_from_prior_close=own_return,
        volume_ratio_previous_session=v['volume_ratio'])


def _m17(v,prior_close):
    # Volume slice is t-19..t; prior windows all exclude t. Advance uses endpoints.
    volume=v['volumes19'];advance=v['advance_end']/v['advance_start']-1
    prior_prices=v['prior10_prices'];base_volume=float(volume[:9].mean());prior_volume=float(volume[9:19].mean())
    band=float(prior_prices.max()/prior_prices.min()-1)
    fired=(advance>=.015 and band<=.006 and base_volume>0 and prior_volume>0 and prior_volume<=.7*base_volume
        and v['price']>prior_prices.max() and volume[19]>=1.5*prior_volume and v['healthy']==1)
    return bool(fired),dict(prior_advance=advance,prior10_price_range=band,baseline9_mean_volume=base_volume,
        prior10_mean_volume=prior_volume,current_volume=float(volume[19]),prior10_high=float(prior_prices.max()),
        own_return_from_prior_close=v['price']/prior_close-1)


CHECKS={'M03':_m03,'M04':_m04,'M16':_m16,'M17':_m17}


def extra_first_trigger(card,row,ctx,stride):
    if card not in CARDS:raise ValueError('extra_minute_card')
    times=decisions(stride,*RANGES[card])
    ctx={} if ctx is None else ctx
    prior_state,prior=_prior(card,row,ctx)
    if prior_state:return None,prior_state,dict(card=card)
    p=_raw(ctx,'price')
    if p is None or (card=='M16' and len(p)<150):
        return None,UNKNOWN_CURRENT,dict(card=card,reason='missing_price_or_M16_less_than_150')
    gap=None
    if card=='M04':
        first=_at(ctx,'price',0,positive=True)
        if first is None:return None,UNKNOWN_CURRENT,dict(card=card,reason='gap_price_missing',t=0)
        gap=first/prior['prior_close']-1
        if not -.01<=gap<=.01:return None,NO_PRECONDITION,dict(card=card,gap=gap)
    for t in times:
        values,reason=_validate(ctx,card,t)
        if reason is not None:return None,UNKNOWN_CURRENT,dict(card=card,t=t,reason=reason)
        fired,evidence=CHECKS[card](values,prior['prior_close'])
        if fired:return t,TRIGGER,dict(card=card,t=t,gap=gap,**evidence)
    return None,NO_TRIGGER,dict(card=card,observed_checks=len(times),gap=gap)
