from dataclasses import replace
import numpy as np
import pandas as pd
import pytest

import ma_system_pullback as ms


def synthetic_daily(size=185):
    dates = pd.bdate_range("2025-01-01", periods=size).strftime("%Y%m%d").tolist()
    closes = 20 + np.maximum(0, np.arange(size) - 125) * .2
    closes[-3:] = closes[-4] - np.arange(1, 4) * .1
    raw = pd.DataFrame(dict(trade_date=dates, open=closes - .03, high=closes + .3,
                            low=closes - .3, close=closes, vol=100., amount=1000.))
    adj = pd.DataFrame(dict(trade_date=dates, adj_factor=1.))
    return raw, adj, dates


def test_flat_projection_exact_and_declining_deductions_are_not_sufficient():
    prices = np.arange(30, 0, -1, dtype=float)
    result = ms.flat_projection(prices, 4)
    assert all(result["deduction_falling"].values())
    assert not result["ready"]
    for h, row in enumerate(result["path"]):
        appended = np.r_[prices, np.repeat(prices[-1], h)]
        for n in (10, 20, 30):
            assert row[f"ma{n}"] == pytest.approx(appended[-n:].mean())
    assert result["outgoing"][10] == prices[-10:-6].tolist()


def test_flat_projection_ready_and_missing_history():
    prices = np.ones(30)
    prices[:4] = [20, 19, 18, 17]
    prices[10:14] = [15, 14, 13, 12]
    prices[20:24] = [8, 7, 6, 5]
    prices[24:] = 25
    assert ms.flat_projection(prices)["ready"]
    prices[5] = np.nan
    assert not ms.flat_projection(prices)["known"]


def test_daily_watch_survives_close_pullback_and_breaks_on_missing_day():
    raw, adj, dates = synthetic_daily()
    d = ms.daily_facts(raw, adj, dates)
    assert d.daily_qualify.iloc[-4]
    assert not d.daily_qualify.iloc[-1]
    assert d.watch_active.iloc[-1]
    assert d.selected_at.iloc[-1] == dates[-4]
    raw.loc[len(raw) - 2, "close"] = np.nan
    gap = ms.daily_facts(raw, adj, dates)
    assert not gap.watch_active.iloc[-1]


def test_daily_future_mutation_does_not_change_prior_watchlist():
    raw, adj, dates = synthetic_daily()
    whole = ms.daily_facts(raw, adj, dates)
    cut = 165
    raw.loc[cut:, ["open", "high", "low", "close"]] *= 100
    changed = ms.daily_facts(raw, adj, dates)
    pd.testing.assert_frame_equal(whole.iloc[:cut], changed.iloc[:cut])
    prefix = ms.daily_facts(raw.iloc[:cut], adj.iloc[:cut], dates[:cut])
    pd.testing.assert_frame_equal(whole.iloc[:cut], prefix)


def test_daily_reversal_uses_only_partial_minute_candle():
    raw, adj, dates = synthetic_daily()
    d = ms.daily_facts(raw, adj, dates)
    day = (pd.Timestamp(dates[-1]) + pd.offsets.BDay(1)).strftime("%Y%m%d")
    support = d.ma30.iloc[-1]
    minute = pd.DataFrame(dict(datetime=pd.to_datetime([day + " 09:45", day + " 10:00"]),
                              trade_date=day, valid=True, p_open=[support + 1, support + 1],
                              p_high=support + 1.2, p_low=[support, support + .9], p_close=support + 1.1))
    result = ms.daily_evidence(d, minute, pd.Timestamp(day + " 09:45"))
    assert result["known"] and result["confirm"]
    assert result["reversal"][0]["partial"]
    # Future full daily low must not alter the already observable partial candle.
    future = d.iloc[-1:].copy()
    future["trade_date"] = day
    future["low"] = .01
    assert ms.daily_evidence(pd.concat([d, future]), minute, pd.Timestamp(day + " 09:45")) == result
    minute.loc[0, "valid"] = False
    assert not ms.daily_evidence(d, minute, pd.Timestamp(day + " 09:45"))["known"]


def staged_frame():
    return pd.DataFrame(dict(datetime=pd.date_range("2026-08-03 09:45", periods=4, freq="15min"),
                             trade_date="20260803", valid=True, watch_active=True, selected_at="20260731",
                             ma5=12., ma10=11., ma20=10.5, ma30=10., ma60=10.8, ma120=10.9,
                             p_close=11., p_low=[11., 11., 10., 10.], bear=[True, False, False, False],
                             bear_run=[1, 0, 0, 0], price_above120=True,
                             projection_ready=[False, False, True, True],
                             cross5_10=[True, False, False, False], cross5_20=[False, True, False, False],
                             cross5_30=[False, False, True, False]))


def test_retest_must_follow_arming_and_prior_daily_selection(monkeypatch):
    monkeypatch.setattr(ms, "daily_evidence", lambda *args: {"confirm": True})
    f = staged_frame()
    _, before = ms.scan_entries(f.iloc[:3], pd.DataFrame())
    assert before == []  # A touching low on the arming bar cannot be its later retest.
    _, candidates = ms.scan_entries(f, pd.DataFrame())
    assert candidates[0]["technical_candidate"]
    assert candidates[0]["time"].endswith("10:30:00")
    f.loc[0, "selected_at"] = None
    _, candidates = ms.scan_entries(f, pd.DataFrame())
    assert not candidates[0]["technical_candidate"]


def test_intraday_gap_cancels_cross_chain(monkeypatch):
    monkeypatch.setattr(ms, "daily_evidence", lambda *args: {"confirm": True})
    f = staged_frame()
    f.loc[1, "valid"] = False
    _, candidates = ms.scan_entries(f, pd.DataFrame())
    assert candidates == []


def row(**changes):
    result = dict(valid=True, expected=False, p_close=21., ma5=20.8, ma10=20.5,
                  ma20=20., ma30=19.5, ma120=18., rise30=4, touch30_recent=True,
                  outgoing10_next=19., outgoing20_next=19.2)
    result.update(changes)
    return result


def test_timeout_exact_boundary_and_confirmation_on_sixteenth_bar():
    s = ms.TargetState()
    for _ in range(15):
        assert not ms.target_step(s, row())
    assert ms.target_step(s, row()) == ["exit_expectation_timeout"]
    assert s.target == 0
    s = ms.TargetState(age=15)
    assert ms.target_step(s, row(expected=True)) == []
    assert s.confirmed and s.target == 1


def test_ma120_overrides_expected_state_and_all_restores():
    s = ms.TargetState(confirmed=True, tranche_a=False, a_sale_age=0)
    assert ms.target_step(s, row(expected=True, p_close=17.)) == ["exit_ma120"]
    assert s.target == 0
    assert ms.target_step(s, row(expected=True)) == []


def test_quarter_sell_and_restore_are_not_repeated_or_same_bar():
    s = ms.TargetState(confirmed=True)
    weak = row(p_close=20.03, ma5=20.04, ma10=20.06, ma20=20.08)
    assert ms.target_step(s, weak) == ["reduce_a_quarter_original"]
    assert s.target == .75
    assert ms.target_step(s, weak, theme_retreat=True) == ["theme_retreat_freeze_restores"]
    assert s.target == .75
    assert ms.target_step(s, row()) == ["restore_a_quarter_original"]
    assert s.target == 1


def test_three_unrecovered_bars_then_half_original_restore():
    s = ms.TargetState(confirmed=True)
    weak = row(ma5=19., ma10=19.1, ma20=21., ma30=20.)
    assert ms.target_step(s, weak) == []
    assert ms.target_step(s, weak) == []
    assert ms.target_step(s, weak) == ["reduce_b_half_original"]
    assert s.target == .5
    assert ms.target_step(s, row()) == ["restore_b_half_original"]
    assert s.target == 1


def test_missing_bars_cannot_fabricate_a_known_timeout_or_three_bar_death():
    s = ms.TargetState(age=14)
    assert ms.target_step(s, row(valid=False)) == ["data_unknown"]
    assert ms.target_step(s, row()) == ["expectation_unknown_due_to_gap"]
    assert not s.closed
    s = ms.TargetState(confirmed=True, death_count=2)
    ms.target_step(s, row(valid=False))
    assert s.death_count == 0


def test_invalid_projection_horizon_rejected():
    with pytest.raises(ValueError):
        replace(ms.Rules(), forecast_bars=16)
