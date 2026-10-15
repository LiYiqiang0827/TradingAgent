"""Causal 15-minute exit/scale/reentry development replay, without broker orders.

Order decisions use completed bars; fills use the following slot's open proxy.
Every lot separately obeys T+1. Daily theme data are shifted one trading day.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from collections import Counter
import math
import numpy as np
import pandas as pd
from core.theme_graph_store import board_points, parse_board_status

POLICIES = ("floor_only", "expectation", "trend", "phased", "phased_theme", "phased_theme_current")

@dataclass(frozen=True)
class ExitRules:
    version: str = "second_wave_exits_development_v5_reproducible"
    horizon_sessions: int = 20
    failure_bars: int = 16
    sideways_bars: int = 32
    no_followthrough_r: float = .5
    trend_arm_r: float = 1.
    partial_profit_r: float = 2.
    partial_fraction: float = .5
    accelerated_8bar_gain: float = .08
    accelerated_slope_ratio: float = 1.8
    accelerated_ma_spread: float = .03
    big_bear_body: float = .015
    peak_retracement: float = .05
    theme_peak_min: float = 50.
    theme_cooling_ratio: float = .65
    theme_width_ratio: float = .5
    max_reentries: int = 2
    reentry_cooldown: int = 8
    lot_size: int = 100
    commission: float = .0003
    min_commission: float = 5.
    sell_stamp: float = .0005
    slippage: float = .0005

def theme_context(kpl, calendar, names, rules=ExitRules()):
    """Rebuild heat using existing graph score, literal historical KPL labels.

    No future alias/taxonomy/catalyst or episode-ending data are consulted.
    A day without ANY source rows is unknown, not a zero-width observation.
    """
    x=kpl.copy();x["trade_date"]=x.trade_date.astype(str)
    x=x[~x.name.str.contains("ST|退",case=False,na=False) &
        ~x.lu_desc.str.contains("ST|次新",case=False,na=False)]
    x=x.drop_duplicates(["trade_date","ts_code","tag"])
    available=set(x.trade_date);by={(d,n):g for (d,n),g in x.groupby(["trade_date","lu_desc"])}
    values=[];lookup={}
    for name in names:
        if "ST" in str(name).upper() or "次新" in str(name):continue
        history=[];active=[];series=[]
        for i,day in enumerate(calendar):
            known=day in available;g=by.get((day,name));width=height=breaks=0;score=0.
            if known and g is not None:
                lim=g[g.tag.eq("涨停")];br=g[g.tag.eq("炸板")]
                width=len(lim);breaks=len(br)
                heights=[parse_board_status(v,"涨停")[3] for v in lim.status]
                height=max(heights,default=0);ladder=sum(board_points(h) for h in heights)
                early=sum(str(t) not in ("","None","nan") and str(t)<= "10:00:00" for t in lim.lu_time)/width if width else 0.
                persistence=(sum(i-4<=a<i for a in active)+1)/5
                seal=width/(width+breaks) if width+breaks else 0.
                score=round(min(25.,width*2.5)+min(35.,ladder)+min(15.,height*2.5)+10*seal+10*persistence+5*early,4)
                active.append(i)
            item=dict(trade_date=day,theme=name,known=known,heat=score if known else None,
                      width=width if known else None,height=height if known else None,breaks=breaks if known else None)
            # Context to be used on NEXT session, never the current session.
            series.append(item);past=[a for a in series[-10:] if a["known"]]
            peak=max((a["heat"] for a in past),default=0.);peakwidth=max((a["width"] for a in past),default=0)
            recent=[a["heat"] for a in series[-4:-1] if a["known"]]
            item.update(peak10=peak,peak_width10=peakwidth,
                cooling=bool(known and peak>=rules.theme_peak_min and score<peak*rules.theme_cooling_ratio and width<peakwidth*rules.theme_width_ratio),
                warming=bool(known and (score>=rules.theme_peak_min or (recent and score>=np.mean(recent) and width>=2))))
            values.append(item)
            if i+1<len(calendar):lookup[(name,calendar[i+1])]={**item,"as_of":day}
    return lookup,pd.DataFrame(values)

def physical_open(stamp):
    return pd.Timestamp(stamp)-pd.Timedelta(minutes=15)

def stock_theme_timeline(kpl,calendar,codes):
    """The latest visible stock primary attribution, updated only next session."""
    x=kpl[kpl.tag.eq("涨停") & kpl.ts_code.isin(codes)].copy()
    x=x[~x.name.str.contains("ST|退",case=False,na=False) & ~x.lu_desc.str.contains("ST|次新",case=False,na=False)]
    by={c:g.sort_values("trade_date").to_dict("records") for c,g in x.groupby("ts_code")};answer={}
    for code,rows in by.items():
        cursor=0;latest=None
        for day in calendar:
            while cursor<len(rows) and rows[cursor]["trade_date"]<day:
                if rows[cursor]["lu_desc"]:latest=rows[cursor]
                cursor+=1
            if latest:answer[(code,day)]=dict(theme_name=latest["lu_desc"],stock_theme_source_date=latest["trade_date"])
    return answer

def context_for(st,day,themes,policy):
    label=st.signal["prior_theme_name"];source=None
    if policy=="phased_theme_current":
        observed=themes.get("_stock_theme_timeline",{}).get((st.signal["ts_code"],day),{})
        label=observed.get("theme_name",label);source=observed.get("stock_theme_source_date")
    context=themes.get((label,day),dict(known=False,as_of=None,heat=None,width=None,cooling=False,warming=False))
    return {**context,"theme_name":label,"stock_theme_source_date":source}

def one_price(row):
    return bool(row["valid_bar"] and max(row[k] for k in ("open","high","low","close"))-min(row[k] for k in ("open","high","low","close"))<.005)

def blocked_sell(row, down_limit):
    # A one-price UP-limit bar is not a sell prohibition.
    return one_price(row) and down_limit is not None and row["open"]<=down_limit+.005

def short_trend(row, minimum=1):
    return all(row[f"m{a}"]>row[f"m{b}"] for a,b in ((5,10),(10,20),(20,30))) and all(row[f"rise{n}"]>=minimum for n in (5,10,20,30))

@dataclass
class State:
    signal: dict
    start: int
    end: int
    floor: float
    upper: float
    launch_open: float
    lots: list = field(default_factory=list)
    pending: dict | None = None
    phase: str = "waiting"
    last_exit: int = -10000
    leg: int = 0
    entry_index: int = -1
    entry_price: float = 0.
    risk: float = .01
    target_budget: float = 0.
    invested: float = 0.
    peak_high: float = 0.
    peak_close: float = 0.
    trend_armed: bool = False
    accelerated_until: int = -1
    partial_done: bool = False
    added: bool = False
    last_mark: float = 0.
    fees: float = 0.
    net_flow: float = 0.
    buys: int = 0
    sells: int = 0
    full_failure: bool = False

    @property
    def shares(self):return sum(l["shares"] for l in self.lots)

def sellable(lots, day):return sum(l["shares"] for l in lots if l["buy_day"]<day)

def quote_shares(budget, price, cash, rules):
    n=int(min(budget,cash)/price//rules.lot_size)*rules.lot_size
    while n and n*price+max(rules.min_commission,n*price*rules.commission)>cash:n-=rules.lot_size
    return max(0,n)

def completed_decision(st, row, j, history, theme, policy, rules):
    """Pure prefix calculation; no row after j and no uncompleted candle."""
    age=j-st.entry_index+1;close=row["close"];r=st.risk
    if close<st.floor:return "sell",1.,"platform_floor_failed"
    if policy=="floor_only":return None
    if age<=rules.failure_bars and (close<st.launch_open or close<st.upper):
        return "sell",1.,"early_launch_failed"
    if age==rules.failure_bars and st.peak_close<st.entry_price*(1+rules.no_followthrough_r*r) and close<=st.entry_price*1.01:
        return "sell",1.,"one_day_no_followthrough"
    if age>=rules.sideways_bars and st.peak_close<st.entry_price*(1+r) and not short_trend(row):
        return "sell",1.,"two_day_sideways"
    if policy=="expectation":return None
    if close>=st.entry_price*(1+rules.trend_arm_r*r) or short_trend(row,3):st.trend_armed=True
    if len(history)>=9:
        p8=history[-9]["close"];p4=history[-5]["close"]
        g8=close/p8-1;g4=close/p4-1;old=p4/p8-1
        if g8>=rules.accelerated_8bar_gain and g4>max(.01,old*rules.accelerated_slope_ratio) and row["m5"]/row["m20"]-1>=rules.accelerated_ma_spread:
            st.accelerated_until=j+8
    if st.trend_armed:
        bear=close/row["open"]-1<=-rules.big_bear_body and abs(row["body_fraction"])>=.6
        if j<=st.accelerated_until and (bear or close<row["m20"]):return "sell",1.,"acceleration_failed"
        if close<row["m30"]:return "sell",1.,"trend_ma30_failed"
        if str(row["datetime"])[11:16]=="15:00" and close<st.peak_high*(1-rules.peak_retracement):
            return "sell",1.,"five_pct_peak_retracement_at_close"
    if policy.startswith("phased"):
        if policy.startswith("phased_theme") and theme.get("cooling",False) and (close<row["m20"] or close<st.upper):
            return "sell",1.,"theme_cooling_with_price_weakness"
        if not st.partial_done and close>=st.entry_price*(1+rules.partial_profit_r*r):
            st.partial_done=True
            return "sell",rules.partial_fraction,"two_R_partial_profit"
        if not st.added and not st.partial_done and age>=3 and close>=st.entry_price*(1+r) and short_trend(row,3):
            if not policy.startswith("phased_theme") or (theme.get("known",False) and not theme.get("cooling",False)):
                return "add",.5,"confirmed_trend_add"
    return None

def reentry_decision(st,row,j,bases,history,theme,policy,rules,all_rows=None):
    if not policy.startswith("phased") or j-st.last_exit<rules.reentry_cooldown or st.leg>=1+rules.max_reentries:return None
    if policy.startswith("phased_theme") and not theme.get("warming",False):return None
    if not short_trend(row) or row["body_pct"]<.35 or row["body_fraction"]<.55 or row["close_location"]<.7:return None
    for end in range(max(st.last_exit+1,j-12),j):
        for base in bases.get(end,[]):
            if base["start_index"]<=st.last_exit:continue
            if all_rows is not None:
                path=all_rows[end+1:j+1]
                if any(not r["valid_bar"] or r["close"]<base["lower"] for r in path):continue
            if row["close"]>max(st.signal["base"]["upper"],base["upper"]) and row["vol"]>=2*base["volume_mean"]:
                if row["close"]/base["upper"]-1<=.04 and (1-base["lower"]/row["close"])<=.07:
                    return base
    return None

def replay(frames, signals, calendar, themes, limits, bases, *,policy="trend",profile="equal20",initial_cash=300000.,rules=ExitRules(),excluded=None,isolated=False):
    """Cash/lot event engine shared by isolated case and overlapping portfolio."""
    if policy not in POLICIES:raise ValueError(policy)
    excluded=excluded or {};timeline=pd.DatetimeIndex(frames[next(iter(frames))]["datetime"])
    time_index={str(t):i for i,t in enumerate(timeline)};states={};ledger=[];decisions=[];issues=[];curve=[];cash=float(initial_cash)
    cap=.99 if isolated else .2;exposure_cap=.99 if isolated else .6
    for s in signals:
        key=s["ts_code"]+"_"+s["signal_time"];start=time_index[s["signal_time"]]
        entry_day=timeline[min(start+1,len(timeline)-1)].strftime("%Y%m%d")
        last_day=calendar[min(calendar.index(entry_day)+rules.horizon_sessions-1,len(calendar)-1)]
        end=time_index[last_day[:4]+"-"+last_day[4:6]+"-"+last_day[6:]+" 14:45:00"]
        st=State(s,start,end,s["base"]["lower"],s["base"]["upper"],s["signal_open"])
        if key in excluded:st.phase="excluded";issues.append(dict(key=key,kind=excluded[key]))
        states[key]=st
    begin=min(st.start for st in states.values());stop=max(st.end for st in states.values())+33
    stop=min(stop,len(timeline)-1)
    def equity():return cash+sum(s.shares*s.last_mark for s in states.values())
    def queue(st,action,fraction,reason,row,j,theme):
        shares=st.shares if fraction>=1 else int(st.shares*fraction//rules.lot_size)*rules.lot_size
        if action=="sell" and fraction<1 and shares==0:shares=st.shares
        st.pending=dict(action=action,fraction=fraction,reason=reason,decision_index=j,decision_time=str(row["datetime"]),
                        remaining=shares,theme=dict(theme))
        decisions.append(dict(key=st.signal["ts_code"]+"_"+st.signal["signal_time"],ts_code=st.signal["ts_code"],leg=st.leg,
            action=action,reason=reason,decision_time=str(row["datetime"]),close=float(row["close"]),platform_floor=st.floor,platform_upper=st.upper,
            ma20=row["m20"],ma30=row["m30"],theme_as_of=theme.get("as_of"),theme_heat=theme.get("heat"),theme_width=theme.get("width"),theme_cooling=theme.get("cooling",False),theme_known=theme.get("known",False),
            current_theme=theme.get("theme_name"),stock_theme_source_date=theme.get("stock_theme_source_date")))
    for j in range(begin,stop+1):
        stamp=timeline[j];day=stamp.strftime("%Y%m%d")
        # OPEN: known opening marks and pending sell orders before buys.
        for st in states.values():
            row=frames[st.signal["ts_code"]]["rows"][j]
            if row["valid_bar"]:st.last_mark=row["open"]
            # Standing frozen-floor invalidation can act at the next day's
            # known OPEN, before waiting for the 09:45 completed candle.
            if st.shares and row["valid_bar"] and stamp.strftime("%H:%M")=="09:45" and row["open"]<st.floor:
                if st.pending is None or st.pending["action"]!="sell" or st.pending["remaining"]<st.shares:
                    context=context_for(st,day,themes,policy)
                    prior=frames[st.signal["ts_code"]]["rows"][j-1]
                    opening={**row,"datetime":physical_open(stamp),"close":row["open"],"m20":prior["m20"],"m30":prior["m30"]}
                    queue(st,"sell",1.,"opening_gap_below_floor",opening,j-1,context)
        active=sorted(states.items(),key=lambda kv:(0 if kv[1].pending and kv[1].pending["action"]=="sell" else 1,-kv[1].signal["template_quality_score"],kv[0]))
        for key,st in active:
            order=st.pending
            if not order or j<=order["decision_index"]:continue
            row=frames[st.signal["ts_code"]]["rows"][j]
            if not row["valid_bar"] or row["vol"]<=0:
                issues.append(dict(key=key,kind="missing_execution_bar",bar_time=str(stamp)));continue
            price=row["open"]*(1+rules.slippage if order["action"]!="sell" else 1-rules.slippage)
            if order["action"]=="sell":
                eligible=sellable(st.lots,day)
                if not eligible:
                    issues.append(dict(key=key,kind="T_plus_1_wait",bar_time=str(stamp),reason=order["reason"]));continue
                down=limits.get((st.signal["ts_code"],day),{}).get("down_limit")
                if blocked_sell(row,down):
                    issues.append(dict(key=key,kind="down_limit_unfilled",bar_time=str(stamp),reason=order["reason"]));continue
                n=min(eligible,order["remaining"]);fee=max(rules.min_commission,n*price*rules.commission)+n*price*rules.sell_stamp
                cash+=n*price-fee;st.net_flow+=n*price-fee;st.fees+=fee;left=n
                for lot in st.lots:
                    if lot["buy_day"]<day:
                        used=min(left,lot["shares"]);lot["shares"]-=used;left-=used
                st.lots=[l for l in st.lots if l["shares"]];order["remaining"]-=n;st.sells+=1
                if not st.shares:st.phase="flat";st.last_exit=j;st.full_failure=order["reason"] not in ("research_horizon","two_R_partial_profit")
                if order["remaining"]==0:st.pending=None
            else:
                if one_price(row):
                    issues.append(dict(key=key,kind="one_price_buy_unfilled",bar_time=str(stamp),reason=order["reason"]));st.pending=None
                    if not st.shares:st.phase="unfilled"
                    continue
                if order["action"]=="reentry" and row["open"]<st.upper:
                    issues.append(dict(key=key,kind="reentry_open_below_platform",bar_time=str(stamp)));st.pending=None;continue
                eq=equity();holding=sum(s.shares*s.last_mark for s in states.values());room=max(0,eq*exposure_cap-holding)
                if order["action"]!="add":
                    risk=max(.01,1-st.floor/price);target=eq*cap
                    if not isolated and profile!="equal20":target=min(target,eq*(.005 if profile=="risk05" else .01)/risk)
                    st.target_budget=target;fraction=.5 if policy.startswith("phased") else 1.
                    budget=target*fraction;st.invested=0.
                else:budget=max(0,st.target_budget-st.invested)
                # Mark-to-market cap: an add cannot turn a winning trial into an oversized position.
                budget=min(budget,room,max(0,eq*cap-st.shares*row["open"]))
                n=quote_shares(budget,price,cash,rules)
                if not n:
                    issues.append(dict(key=key,kind="cash_or_risk_cap_skip",bar_time=str(stamp),reason=order["reason"]));st.pending=None
                    if not st.shares:st.phase="unfilled"
                    continue
                fee=max(rules.min_commission,n*price*rules.commission);cash-=n*price+fee;st.net_flow-=n*price+fee;st.fees+=fee
                st.lots.append(dict(shares=n,buy_day=day,price=price));st.invested+=n*price;st.buys+=1
                if order["action"]!="add":
                    st.leg+=1;st.entry_index=j;st.entry_price=price;st.risk=max(.01,1-st.floor/price)
                    st.peak_high=st.peak_close=price;st.trend_armed=False;st.accelerated_until=-1;st.partial_done=False;st.added=False
                else:st.added=True
                st.phase="holding";st.pending=None
            ledger.append(dict(key=key,name=st.signal["name"],ts_code=st.signal["ts_code"],leg=st.leg,action=order["action"],reason=order["reason"],
                decision_time=order["decision_time"],execution_time=str(physical_open(stamp)),execution_bar=str(stamp),price=price,open_proxy=row["open"],shares=n,
                fees=fee,cash_after=cash,remaining_shares=st.shares,theme_as_of=order["theme"].get("as_of"),theme_heat=order["theme"].get("heat"),theme_width=order["theme"].get("width"),
                current_theme=order["theme"].get("theme_name"),stock_theme_source_date=order["theme"].get("stock_theme_source_date")))
        # CLOSE: all current marks are known before sizing/decisions.
        for st in states.values():
            row=frames[st.signal["ts_code"]]["rows"][j]
            if row["valid_bar"]:st.last_mark=row["close"]
        for key,st in states.items():
            row=frames[st.signal["ts_code"]]["rows"][j]
            if st.phase=="excluded" or not row["valid_bar"]:continue
            context=context_for(st,day,themes,policy)
            if st.shares:
                st.peak_high=max(st.peak_high,row["high"]);st.peak_close=max(st.peak_close,row["close"])
                if j>=st.end:
                    if st.pending is None or st.pending["action"]!="sell" or st.pending["remaining"]<st.shares:
                        queue(st,"sell",1.,"research_horizon",row,j,context)
                elif st.pending is None:
                    h=frames[st.signal["ts_code"]]["rows"][max(0,j-16):j+1]
                    decision=completed_decision(st,row,j,h,context,policy,rules)
                    if decision:queue(st,*decision,row,j,context)
                elif st.pending["action"]=="sell" and st.pending["remaining"]<st.shares:
                    # A locked partial take-profit does not disable hard-risk
                    # evidence arriving before that order can actually fill.
                    if row["close"]<st.floor or (j-st.entry_index+1<=rules.failure_bars and row["close"]<st.upper):
                        queue(st,"sell",1.,"partial_order_upgraded_on_failure",row,j,context)
            elif st.phase=="waiting" and j==st.start:queue(st,"buy",1.,"frozen_initial_signal",row,j,context)
            elif st.phase=="flat" and st.pending is None and j<st.end:
                h=frames[st.signal["ts_code"]]["rows"][max(0,j-16):j+1]
                base=reentry_decision(st,row,j,bases[st.signal["ts_code"]],h,context,policy,rules,frames[st.signal["ts_code"]]["rows"])
                if base:
                    st.floor=base["lower"];st.upper=base["upper"];st.launch_open=row["open"]
                    queue(st,"reentry",1.,"fresh_quiet_platform_reclaim",row,j,context)
        curve.append(dict(datetime=str(stamp),cash=cash,equity=equity(),exposure=sum(s.shares*s.last_mark for s in states.values()),
                          positions=sum(s.shares>0 for s in states.values()),locked_shares=sum(s.shares-sellable(s.lots,day) for s in states.values())))
        assert cash>=-.001, "cash accounting may not borrow"
    results=[]
    for key,st in states.items():
        value=st.net_flow+st.shares*st.last_mark
        results.append(dict(key=key,ts_code=st.signal["ts_code"],name=st.signal["name"],signal_time=st.signal["signal_time"],theme=st.signal["prior_theme_name"],
            known_core=st.signal["known_core_development_case"],status=st.phase,legs=st.leg,buy_orders=st.buys,sell_orders=st.sells,
            pnl=value,net_return_pct=100*value/initial_cash if isolated else None,fees=st.fees,open_shares=st.shares))
    cf=pd.DataFrame(curve);peak=cf.equity.cummax().clip(lower=initial_cash);dd=cf.equity/peak-1
    summary=dict(policy=policy,profile=profile,initial_cash=initial_cash,final_equity=float(cf.equity.iloc[-1]),net_return_pct=float(100*(cf.equity.iloc[-1]/initial_cash-1)),
        max_drawdown_pct=float(-100*dd.min()),fee_total=sum(s.fees for s in states.values()),filled_cases=sum(s.buys>0 for s in states.values()),
        open_cases=sum(s.shares>0 for s in states.values()),reentry_orders=sum(r["action"]=="reentry" for r in ledger),
        add_orders=sum(r["action"]=="add" for r in ledger),partial_orders=sum(r["reason"]=="two_R_partial_profit" for r in ledger),
        exit_reasons=dict(Counter(r["reason"] for r in ledger if r["action"]=="sell")),issues=dict(Counter(r["kind"] for r in issues)),
        max_exposure_pct=float((cf.exposure/cf.equity).max()*100),max_positions=int(cf.positions.max()))
    return dict(summary=summary,results=results,ledger=ledger,decisions=decisions,issues=issues,curve=cf)
