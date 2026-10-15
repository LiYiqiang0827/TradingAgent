import numpy as np
import pandas as pd
import pytest

import pullback_price_confirmation as pc
from study_fifteen_min_ma_coil import Rules, features, slots


def test_depth_is_continuous_score_not_a_35_percent_rejection_gate():
    scores=[pc.pullback_depth_score(d) for d in (30.,35.,35.09350935093509,40.,45.)]
    assert scores[0] == scores[1] == 100.
    assert 99. < scores[2] < 100.
    assert scores[3] == 80. and scores[4] < scores[3]
    assert scores == sorted(scores,reverse=True)


def daily_volume_sample():
    dates=pd.bdate_range("2025-01-02",periods=32).strftime("%Y%m%d")
    d=pd.DataFrame(dict(trade_date=dates,raw_vol=[100.]*20+[1000.]*3+[300.]*7+[50000.,50000.],
                        high=[10.]*20+[11.,12.,13.]+[12.]*7+[100.,100.],valid_day=True))
    return d,str(dates[20]),str(dates[29])


def test_phase_volume_reports_shrinkage_in_an_elevated_regime_without_future():
    d,launch,cutoff=daily_volume_sample()
    p=pc.phase_volume_profile(d,launch,cutoff)
    assert p == pc.phase_volume_profile(d.iloc[:30],launch,cutoff)
    assert p["advance_mean_vs_baseline"] == 10.
    assert p["correction_mean_vs_baseline"] == p["correction_tail_mean_vs_baseline"] == 3.
    assert p["correction_mean_vs_advance"] == .3
    assert p["correction_days_above_baseline_fraction"] == 1.


def test_phase_volume_median_and_tail_expose_one_spike_and_activity_fading():
    d,launch,cutoff=daily_volume_sample()
    d.loc[23:29,"raw_vol"]=[2000.,60.,60.,60.,60.,60.,60.]
    p=pc.phase_volume_profile(d,launch,cutoff)
    assert p["correction_mean_vs_baseline"] > 3.
    assert p["correction_median_vs_baseline_median"] == .6
    assert p["correction_tail_mean_vs_baseline"] == .6
    assert p["correction_days_above_baseline_fraction"] == pytest.approx(1/7)
    assert not p["automatic_gate_applied"]


def test_phase_volume_rejects_observed_gaps_but_ignores_future_gaps():
    d,launch,cutoff=daily_volume_sample()
    d.loc[30:,"valid_day"]=False
    pc.phase_volume_profile(d,launch,cutoff)
    d.loc[25,"valid_day"]=False
    with pytest.raises(ValueError,match="Gap"):
        pc.phase_volume_profile(d,launch,cutoff)


def sample():
    days=pd.bdate_range("2025-01-02",periods=10).strftime("%Y%m%d").tolist()
    stamps=slots(days)
    raw=pd.DataFrame(dict(datetime=stamps,open=10.,high=10.1,low=9.9,close=10.,vol=1000.,adj_factor=1.))
    raw.loc[120:127,["high","low","vol"]]=[10.02,9.98,100.]
    raw.loc[128,["open","high","low","close","vol"]]=[10.1,10.65,10.05,10.6,2500.]
    f=features(raw,days,Rules())
    base=pc.freeze_base(f,stamps[120],stamps[127])
    return raw,days,f,base,stamps


def test_single_bull_confirms_price_volume_without_ma_order_gate():
    _,_,f,b,t=sample()
    z=pc.evaluate_at(f,b,t[128])
    assert z["confirmed"]
    assert z["close"] > b["upper"]
    assert b["volume_vs_preceding_window"] == pytest.approx(.1)


def test_full_future_prefix_equivalent_even_if_future_corrupted():
    raw,days,f,b,t=sample()
    raw.loc[129:,["open","high","low","close","adj_factor"]]=np.nan
    corrupted=features(raw,days,Rules())
    assert pc.evaluate_at(f,b,t[128]) == pc.evaluate_at(corrupted,b,t[128])
    assert pc.evaluate_at(f,b,t[128]) == pc.evaluate_at(f.iloc[:129],b,t[128])


def test_breaking_platform_but_not_failed_opening_high_is_only_probe():
    raw,days,_,_,t=sample()
    raw.loc[128,["open","high","low","close","vol"]]=[10.1,10.4,9.8,10.,2500.]
    raw.loc[129,["open","high","low","close","vol"]]=[10.05,10.31,10.,10.3,2500.]
    raw.loc[130,["open","high","low","close","vol"]]=[10.3,10.52,10.28,10.5,2500.]
    f=features(raw,days,Rules());b=pc.freeze_base(f,t[120],t[127])
    probe=pc.evaluate_at(f,b,t[129]);confirmed=pc.evaluate_at(f,b,t[130])
    assert probe["probe"] and not probe["confirmed"]
    assert probe["failed_opening_high"] == 10.4
    assert confirmed["confirmed"] and confirmed["consecutive_bulls"]["bars"] == 2


def test_same_clock_volume_prevents_open_vs_afternoon_volume_artifact():
    raw,days,_,_,t=sample()
    raw.loc[128,"vol"]=800.
    f=features(raw,days,Rules());b=pc.freeze_base(f,t[120],t[127])
    z=pc.evaluate_at(f,b,t[128])
    assert z["volume_vs_quiet_mean"] == 8.
    assert z["volume_same_slot5"] < 1.
    assert z["probe"] and not z["confirmed"]


def test_observed_gap_rejected_but_future_gap_ignored():
    raw,days,_,_,t=sample()
    f=features(raw.drop(index=129),days,Rules());b=pc.freeze_base(f,t[120],t[127])
    assert pc.evaluate_at(f,b,t[128])["confirmed"]
    with pytest.raises(ValueError,match="missing"):
        pc.evaluate_at(f,b,t[130])


def test_signal_factor_change_is_not_silently_adjusted():
    _,_,f,b,t=sample()
    f.loc[128,"adj_factor"]=2.
    with pytest.raises(ValueError,match="factor change"):
        pc.evaluate_at(f,b,t[128])


def test_support_requires_two_completed_right_bars():
    _,_,f,_,t=sample()
    x=f.iloc[:12].copy()
    x["low"]=[11.,10.5,10.,10.5,11.,11.5,11.2,10.8,11.1,11.5,12.,12.2]
    x["high"]=x.low+.4
    assert pc.rising_support(x.iloc[:9],t[0]) is None
    line=pc.rising_support(x.iloc[:10],t[0])
    assert line is not None
    assert pd.Timestamp(line["confirmed_on"]) == t[9]
    assert line["slope_per_bar"] > 0


def test_existing_line_is_checked_before_refitting_after_break(monkeypatch):
    _,_,f,_,t=sample()
    x=f.iloc[:5].copy()
    x.loc[3,["close","body_pct"]]=[9.9,-3.]
    calls=[]
    def candidate(p,entry):
        calls.append(len(p))
        if len(p)>=3:
            return dict(price_at_creation=10.,slope_per_bar=.05,created_bar=len(p)-1,
                        first_time=str(t[0]),second_time=str(t[1]))
    monkeypatch.setattr(pc,"rising_support",candidate)
    entry=dict(decision_time=str(t[0]),close=10.,signal_factor=1.,price_scale=1.,
               trigger_candle_open=9.,launch_origin=9.,base_upper=9.,invalidation_base_lower=8.)
    trace=pc.monitor_path(x,entry,t[4])
    assert "frozen_rising_line_broken" in trace.iloc[2]["flags"]
    assert 4 not in calls


def test_t_plus_one_failure_waits_for_next_trading_day():
    assert pc.sell_eligibility("2026-09-11 14:00","2026-09-11 10:15",["20260911","20260914"]) == pd.Timestamp("2026-09-14 09:31")
    assert pc.sell_eligibility("2026-09-14 10:00","2026-09-11 10:15",["20260911","20260914"]) == pd.Timestamp("2026-09-14 10:00")
    with pytest.raises(ValueError):
        pc.sell_eligibility("2026-09-11 10:00","2026-09-11 10:15",["20260911","20260914"])
