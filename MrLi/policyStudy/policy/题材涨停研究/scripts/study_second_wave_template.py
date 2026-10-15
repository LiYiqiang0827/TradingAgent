"""Prefix-only second-wave template features; separate from frozen MA v1/v2.

Core examples are supplied development cases, not held-out winners. All rules
below are fixed before target-period outcomes are opened. Scores describe
template quality and are not probabilities. Daily volume remains unadjusted.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import json
import numpy as np
import pandas as pd
from pullback_price_confirmation import pullback_depth_score


def theme_snapshot(kpl, signal, calendar):
    """Literal KPL peers known at the prior close, not same-day attribution.

    Labels and breadth are context diagnostics, not verified catalysts or an
    independently validated edge. Do not infer aliases from future winners.
    """
    as_of=str(signal["context_date"]); theme=str(signal["prior_theme_name"])
    days=[d for d in calendar if d<=as_of][-20:]
    hist=kpl[kpl.trade_date.astype(str).isin(days)].copy()
    hist=hist[~hist.name.str.contains("ST|退",case=False,na=False) &
              ~hist.lu_desc.str.contains("ST|次新",case=False,na=False)]
    peers=hist[hist.lu_desc.eq(theme)].drop_duplicates(["trade_date","ts_code"])
    recent=peers[peers.trade_date.astype(str).isin(days[-5:])]
    latest=peers[peers.trade_date.astype(str).eq(as_of)]
    counts=peers.groupby("trade_date").ts_code.nunique()
    stock=hist[hist.ts_code.eq(signal["ts_code"])].sort_values("trade_date")
    result=dict(theme_context_as_of=as_of,theme_basis="historical_literal_kpl_lu_desc",
        theme_prevday_limitup_count=int(latest.ts_code.nunique()),
        theme_last5_limitup_stock_count=int(recent.ts_code.nunique()),
        theme_last5_limitup_occurrences=len(recent),
        theme_last20_peak_day_width=int(counts.max()) if len(counts) else 0,
        theme_last5_peer_codes="|".join(sorted(recent.ts_code.unique())),
        theme_last_occurrence=str(peers.trade_date.max()) if len(peers) else None,
        stock_last20_limitup_count=int(stock.trade_date.nunique()),
        stock_last_observed_themes=str(stock.iloc[-1].get("theme","")) if len(stock) else "",
        contemporaneous_catalyst_verified=False)
    return result


@dataclass(frozen=True)
class TemplateRules:
    version: str = "second_wave_phase_volume_price_launch_v3"
    baseline_days: int = 20
    new_high_days: int = 63
    history_days: int = 250
    board_lookback: int = 70
    board_min: int = 2
    board_max: int = 4
    wave_gain_min: float = 10.
    wave_gain_max: float = 100.
    correction_days_min: int = 4
    correction_days_max: int = 40
    long_rise_days: int = 20
    medium_rise_days: int = 15
    target_rise_days: int = 10
    touch_days: int = 5
    touch_pct: float = 3.
    close_target_pct: float = 6.
    daily_coil_pct: float = 7.
    advance_volume_multiple: float = 2.
    correction_volume_multiple: float = 2.
    correction_median_multiple: float = 2.
    correction_tail_multiple: float = 2.
    correction_low_rolling_multiple: float = 1.5
    correction_above_baseline_share: float = .8
    atr_contraction_ratio: float = .92
    quiet_windows: tuple[int,...] = (4,5,8)
    quiet_memory_bars: int = 12
    quiet_width_pct: float = 3.5
    quiet_volume_ratio: float = .8
    quiet_candle_range_ratio: float = .8
    trigger_volume_multiple: float = 2.
    trigger_same_clock_multiple: float = 1.2
    trigger_body_pct: float = .35
    trigger_relative_body_multiple: float = 2.
    trigger_body_fraction: float = .55
    trigger_close_location: float = .7
    entry_above_platform_max_pct: float = 4.
    entry_floor_distance_max_pct: float = 7.


def core_rules():
    """Stronger volume regime calibrated to supplied templates, not outcomes."""
    return replace(TemplateRules(),version="second_wave_core_phase_volume_v4",
        advance_volume_multiple=4.,correction_volume_multiple=3.,
        correction_median_multiple=3.,correction_tail_multiple=2.5,
        correction_low_rolling_multiple=2.,daily_coil_pct=5.)


def daily_setup(daily, events, as_of, rules=TemplateRules()):
    x=daily[daily.trade_date.le(as_of)].reset_index(drop=True)
    fail=dict(context_date=as_of,eligible=False,reason="history_or_current_day_missing")
    if len(x)<rules.history_days or x.iloc[-1].trade_date!=as_of or not x.iloc[-1].valid_day:
        return fail
    lookup={d:i for i,d in enumerate(x.trade_date)}
    e=events[events.trade_date.between(str(x.iloc[-rules.board_lookback].trade_date),as_of)]
    anchors=e[e.qualifying_board.ge(rules.board_min)]
    if anchors.empty:return {**fail,"reason":"no_qualifying_prior_board"}
    results=[]
    for _,g in anchors.groupby("wave_start_date",sort=False):
        anchor=g.sort_values("trade_date").iloc[-1]
        launch=str(anchor.wave_start_date); i=lookup.get(launch)
        if i is None or i<rules.new_high_days:continue
        phase=x.iloc[i:]; p=int(phase.high.idxmax()); c=x.iloc[p+1:]; last=x.iloc[-1]; peak=x.iloc[p]
        baseline=x.iloc[i-rules.baseline_days:i]
        used=x.iloc[i-rules.new_high_days:]
        if not used.valid_day.all() or len(c)<rules.correction_days_min:
            continue
        av=x.raw_vol.iloc[i:p+1]; cv=c.raw_vol; mean=float(baseline.raw_vol.mean()); med=float(baseline.raw_vol.median())
        stage=e[e.trade_date.between(launch,str(peak.trade_date))]
        height=int(stage.qualifying_board.max())
        gain=float(100*(peak.high/x.iloc[i].close-1))
        depth=float(100*(1-c.low.min()/peak.high))
        short=[last[f"ma{n}"] for n in (5,10,20)]
        coil=float(100*(max(short)/min(short)-1))
        tail=float(cv.tail(5).mean()/mean)
        rolling=cv.rolling(min(5,len(cv))).mean().dropna()
        atr_ratio=float(last.atr14_pct/c.atr14_pct.max())
        touches=[]
        for n in (20,30,60):
            if last[f"rise{n}"]<rules.target_rise_days:continue
            recent=c.tail(rules.touch_days)
            near=(recent.low.le(recent[f"ma{n}"]*(1+rules.touch_pct/100)) &
                  recent.high.ge(recent[f"ma{n}"]*(1-rules.touch_pct/100)) &
                  recent[f"rise{n}"].gt(0))
            if near.any() and abs(100*(last.close/last[f"ma{n}"]-1))<=rules.close_target_pct:
                touches.append(n)
        gates=dict(
            historical_st_or_ipo_excluded=("ST" not in str(anchor["name"]).upper() and "退" not in str(anchor["name"]) and x.iloc[:i].valid_day.sum()>=rules.history_days),
            height=rules.board_min<=height<=rules.board_max,
            wave_gain=rules.wave_gain_min<=gain<=rules.wave_gain_max,
            stage_new_high=peak.high>x.high.iloc[i-rules.new_high_days:i].max(),
            correction_length=rules.correction_days_min<=len(c)<=rules.correction_days_max,
            meaningful_pullback=depth>=5.,
            peak_trend=(min(peak.ma5,peak.ma10,peak.ma20)>=max(peak.ma60,peak.ma120) and peak.ma20>=peak.ma30*.98),
            current_trend=(min(last.ma5,last.ma10,last.ma20,last.ma30)>=max(last.ma60,last.ma120)*.98 and last.ma60>=last.ma120*.99),
            long_rise=max(last.rise60,last.rise120)>=rules.long_rise_days,
            medium_rise=max(last.rise20,last.rise30)>=rules.medium_rise_days,
            rising_ma_touch=bool(touches), daily_coil=coil<=rules.daily_coil_pct,
            volatility_contraction=atr_ratio<=rules.atr_contraction_ratio,
            first_wave_volume=av.mean()/mean>=rules.advance_volume_multiple,
            persistent_correction_volume=(cv.mean()/mean>=rules.correction_volume_multiple and cv.median()/med>=rules.correction_median_multiple and tail>=rules.correction_tail_multiple and rolling.min()/mean>=rules.correction_low_rolling_multiple and cv.gt(mean).mean()>=rules.correction_above_baseline_share),
        )
        score=(25*min(1,float(cv.mean()/mean)/5)+15*min(1,tail/4)+
               15*min(1,max(last.rise60,last.rise120)/30)+10*min(1,max(last.rise20,last.rise30)/25)+
               15*max(0,1-coil/10)+10*max(0,1-atr_ratio)+10*pullback_depth_score(depth)/100)
        results.append(dict(context_date=as_of,eligible=all(gates.values()),reason="eligible" if all(gates.values()) else "|".join(k for k,v in gates.items() if not v),
            gates=json.dumps({k:bool(v) for k,v in gates.items()}),wave_start_date=launch,peak_date=str(peak.trade_date),name=str(anchor["name"]),prior_theme_name=str(anchor.lu_desc),
            first_wave_height=height,first_wave_board_type=str(anchor.board_type),gain_pct=gain,drawdown_pct=depth,
            daily_coil_pct=coil,atr_contraction_ratio=atr_ratio,rising_target_mas="|".join(map(str,touches)),
            advance_volume_multiple=float(av.mean()/mean),correction_volume_multiple=float(cv.mean()/mean),
            correction_median_multiple=float(cv.median()/med),correction_tail_multiple=tail,correction_min_volume_multiple=float(cv.min()/mean),
            correction_min_rolling_multiple=float(rolling.min()/mean),correction_above_baseline_share=float(cv.gt(mean).mean()),
            correction_vs_advance=float(cv.mean()/av.mean()),baseline_volume=mean,
            template_quality_score=float(score),known_core_development_case=str(anchor.ts_code)=="002810.SZ"))
    if not results:return {**fail,"reason":"wave_history_or_correction_missing"}
    yes=[r for r in results if r["eligible"]]
    return max(yes,key=lambda r:r["template_quality_score"]) if yes else min(results,key=lambda r:len(r["reason"].split("|")))


def quiet_bases(frame, rules=TemplateRules()):
    """Discover each base from its completed prefix, before a later trigger."""
    f=frame.reset_index(drop=True); bases={}
    for n in rules.quiet_windows:
        upper=f.high.rolling(n).max();lower=f.low.rolling(n).min()
        width=100*(upper/lower-1);volume=f.vol.rolling(n).mean()
        prior_volume=volume.shift(n).where(volume.shift(n).gt(0))
        candle_range=((f.high-f.low)/f.close).rolling(n).mean()
        vr=volume/prior_volume;rr=candle_range/candle_range.shift(n).where(candle_range.shift(n).gt(0))
        valid=f.valid_bar.rolling(2*n).sum().eq(2*n)
        valid &= f.adj_factor.rolling(2*n).max().eq(f.adj_factor.rolling(2*n).min())
        ok=valid & volume.gt(0) & width.le(rules.quiet_width_pct) & vr.le(rules.quiet_volume_ratio) & rr.le(rules.quiet_candle_range_ratio)
        for end in np.flatnonzero(ok.to_numpy()):
            if end<16:continue
            bases.setdefault(int(end),[]).append(dict(end_index=int(end),base_start=str(f.datetime.iloc[end-n+1]),base_end=str(f.datetime.iloc[end]),bars=n,
                upper=float(upper.iloc[end]),lower=float(lower.iloc[end]),range_pct=float(width.iloc[end]),volume_mean=float(volume.iloc[end]),
                volume_vs_preceding_window=float(vr.iloc[end]),candle_range_vs_preceding_window=float(rr.iloc[end]),adj_factor=float(f.adj_factor.iloc[end]),
                quality=float((1-vr.iloc[end])+(1-rr.iloc[end])+(1-width.iloc[end]/rules.quiet_width_pct))))
    return bases


def find_signals(frame, daily_context, first, last, rules=TemplateRules()):
    """First accepted launch per already visible daily wave; no outcome access."""
    f=frame.reset_index(drop=True); bases=quiet_bases(f,rules); taken=set(); signals=[]
    for j in range(80,len(f)):
        b=f.iloc[j]; day=b.datetime.strftime("%Y%m%d")
        if not first<=day<=last or day not in daily_context:continue
        context=daily_context[day]
        if not context.get("eligible") or context["wave_start_date"] in taken:continue
        if not b.valid_bar or b.close<=b.open or not np.isfinite(b.volume_multiple_same_slot5):continue
        if b.body_pct<rules.trigger_body_pct or b.body_fraction<rules.trigger_body_fraction or b.close_location<rules.trigger_close_location or b.volume_multiple_same_slot5<rules.trigger_same_clock_multiple:continue
        previous=f.iloc[j-8:j]
        if not previous.valid_bar.all():continue
        median=float((previous.close-previous.open).abs().median())
        relative=(float((b.close-b.open)/median) if median>0 else None)
        if b.body_pct<1. and (relative is None or relative<rules.trigger_relative_body_multiple):continue
        choices=[]
        for end in range(max(0,j-rules.quiet_memory_bars),j):
            for base in bases.get(end,[]):
                path=f.iloc[end+1:j+1]
                if not path.valid_bar.all() or not np.isclose(path.adj_factor,base["adj_factor"]).all():continue
                # A close failure expires this base; intrabar wicks are recorded
                # separately and cannot retrospectively erase a valid recovery.
                if path.close.lt(base["lower"]).any():continue
                above=float(100*(b.close/base["upper"]-1)); risk=float(100*(1-base["lower"]/b.close))
                if not 0<above<=rules.entry_above_platform_max_pct or risk>rules.entry_floor_distance_max_pct:continue
                if b.vol/base["volume_mean"]<rules.trigger_volume_multiple:continue
                opening=f.iloc[end+1:j+1]
                first_bar=opening[opening.datetime.dt.strftime("%H:%M").eq("09:45")]
                failed=first_bar[(first_bar.close<=first_bar.open)&(first_bar.close<=base["upper"])]
                if len(failed) and b.close<=failed.high.max():continue
                choices.append((base["quality"],base))
        if not choices:continue
        base=max(choices,key=lambda z:z[0])[1]
        signals.append(dict(signal_time=str(b.datetime),signal_close=float(b.close),signal_open=float(b.open),
            body_pct=float(b.body_pct),relative_body_multiple=relative,volume_vs_quiet=float(b.vol/base["volume_mean"]),
            same_clock_volume_multiple=float(b.volume_multiple_same_slot5),
            intraday_short_order=bool(b.ma5>b.ma10>b.ma20>b.ma30),
            intraday_short_all_rising=bool(all(b[f"rise{n}"]>0 for n in (5,10,20,30))),
            intraday_ma60_rise=int(b.rise60),intraday_ma120_rise=int(b.rise120),base=base,**context))
        taken.add(context["wave_start_date"])
    return signals


def outcome_path(frame, signal, calendar, horizon=20):
    """Next-bar-open proxy; path labels, NOT a completed exit strategy.

    Evaluate 25/30% observed space and separately require no prior close below
    the frozen floor. Preserve every signal in the denominator, including gaps
    and unfilled one-price bars. Never count the decision candle's own high.
    """
    at=pd.Timestamp(signal["signal_time"]); future=frame[frame.datetime.gt(at)].reset_index(drop=True)
    missing=dict(execution_status="no_next_bar",evaluable=False)
    if future.empty:return missing
    entry=future.iloc[0]
    if not entry.valid_bar:return {**missing,"execution_status":"next_bar_missing"}
    if entry.high==entry.low or entry.vol<=0:return {**missing,"execution_status":"one_price_next_bar_no_fill_assumed"}
    fill=float(entry.open); day=entry.datetime.strftime("%Y%m%d"); di=calendar.index(day)
    end=calendar[min(di+horizon-1,len(calendar)-1)]
    path=future[future.datetime.dt.strftime("%Y%m%d").le(end)].copy()
    start_slot=int(frame.index[frame.datetime.eq(entry.datetime)][0])
    expected=frame.iloc[start_slot:][frame.iloc[start_slot:].datetime.dt.strftime("%Y%m%d").le(end)]
    if not expected.valid_bar.all() or calendar[di:di+horizon][-1]!=end or len(calendar[di:di+horizon])<horizon:
        return dict(execution_status="entered_but_outcome_gap",evaluable=False,entry_time=str(entry.datetime),entry_price=fill)
    factor=float(entry.adj_factor); basis=float(signal["base"]["adj_factor"])
    high=path.high*path.adj_factor/factor; low=path.low*path.adj_factor/factor; close=path.close*path.adj_factor/factor
    floor=signal["base"]["lower"]*basis/factor; upper=signal["base"]["upper"]*basis/factor
    failed=close.lt(floor); first_fail=path.loc[failed,"datetime"].iloc[0] if failed.any() else None
    upper_fail=path.loc[close.lt(upper),"datetime"].iloc[0] if close.lt(upper).any() else None
    result=dict(execution_status="next_15m_open_proxy",evaluable=True,entry_time=str(entry.datetime),entry_price=fill,
        entry_gap_from_signal_pct=100*(fill/signal["signal_close"]-1),observed_through=end,
        floor_risk_pct=100*(1-floor/fill),max_favourable_pct=100*(high.max()/fill-1),max_adverse_pct=100*(low.min()/fill-1),
        first_floor_failure=str(first_fail) if first_fail is not None else None,first_platform_reentry=str(upper_fail) if upper_fail is not None else None,
        floor_failure_after_bars=int(np.flatnonzero(failed)[0])+1 if failed.any() else None)
    for h in (10,20):
        limit=calendar[min(di+h-1,len(calendar)-1)]; keep=path.datetime.dt.strftime("%Y%m%d").le(limit)
        result[f"mfe_{h}d_pct"]=100*(high[keep].max()/fill-1)
        result[f"mae_{h}d_pct"]=100*(low[keep].min()/fill-1)
        for target in (25,30):
            hit=keep & high.ge(fill*(1+target/100)); time=path.loc[hit,"datetime"].iloc[0] if hit.any() else None
            result[f"reached_{target}_{h}d"]=bool(hit.any())
            result[f"intact_{target}_{h}d"]=bool(time is not None and (first_fail is None or time<first_fail))
            if h==20:
                result[f"first_{target}_time"]=str(time) if time is not None else None
                prior=path.datetime.le(time) if time is not None else pd.Series(False,index=path.index)
                result[f"mae_before_{target}_pct"]=float(100*(low[prior].min()/fill-1)) if time is not None else None
    byday=pd.DataFrame(dict(day=path.datetime.dt.strftime("%Y%m%d"),close=close)).groupby("day").close.last()
    result["daily_closes_above_entry_fraction"]=float(byday.gt(fill).mean())
    result["daily_closes_at_least_15pct"]=int(byday.ge(fill*1.15).sum())
    result["sustained_intact_25_20d"]=bool(result["intact_25_20d"] and result["daily_closes_at_least_15pct"]>=3)
    # Earliest sellable open after a floor failure, with T+1 and one-price bars.
    # This is a failure-loss diagnostic, not a fitted stop/exit strategy.
    if first_fail is not None:
        failure_day=first_fail.strftime("%Y%m%d")
        earliest=calendar[di+1] if failure_day==day else failure_day
        sell=future[(future.datetime>first_fail)&future.datetime.dt.strftime("%Y%m%d").ge(earliest)]
        sell=sell[sell.valid_bar & sell.high.gt(sell.low) & sell.vol.gt(0)]
        if len(sell):
            s=sell.iloc[0];price=float(s.open*s.adj_factor/factor)
            result.update(failure_sell_proxy_time=str(s.datetime),failure_sell_proxy_return_pct=100*(price/fill-1),failure_sell_proxy_net_pct=100*(price/fill-1)-.4)
        else:result.update(failure_sell_proxy_time=None,failure_sell_proxy_return_pct=None,failure_sell_proxy_net_pct=None)
    return result
