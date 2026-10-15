"""Visibility, daily gates, dynamic exits and actual legal sell timing."""

import unittest
from dataclasses import replace

import numpy as np
import pandas as pd

from study_daily_intraday_pullback import (
    CombinedRules,
    daily_pool_snapshot,
    prepare_daily,
    exit_decisions,
    execute_dynamic,
    combined_entries,
    revised_rules,
    relaxed_restart_alignment,
)
from study_fifteen_min_ma_coil import slots
from replay_fifteen_min_ma_coil import minute_slots
from replay_daily_intraday_pullback import board_events
from review_ma_pullback_template import wave_metrics


def exit_frame(prices, calendar=None):
    calendar = calendar or ["20260901", "20260902", "20260903"]
    t = slots(calendar)[1 : len(prices) + 1]
    x = pd.DataFrame(
        dict(
            datetime=t,
            p_close=prices,
            p_high=np.array(prices) + 0.1,
            p_low=np.array(prices) - 0.1,
            p_open=np.array(prices) - 0.01,
            valid_bar=True,
            adj_factor=1.0,
            body_pct=0.01,
            body_fraction=0.05,
        )
    )
    for n, v in (
        (5, 99.0),
        (10, 98.0),
        (20, 97.0),
        (30, 96.0),
        (60, 95.0),
        (120, 94.0),
    ):
        x[f"ma{n}"] = v
    return x


SIGNAL = dict(
    ts_code="600000.SH",
    name="样本",
    signal_time="2026-09-01 09:45:00",
    signal_close=101.0,
    signal_factor=1.0,
    launch_start_raw=100.0,
)


class DailyGatesTests(unittest.TestCase):
    def setUp(self):
        self.dates = (
            pd.bdate_range("2025-06-01", periods=320).strftime("%Y%m%d").tolist()
        )
        self.x = pd.DataFrame(
            dict(
                trade_date=self.dates,
                valid_day=True,
                high=115.0,
                low=100.0,
                close=110.0,
                raw_vol=100.0,
                true_range_pct=2.0,
            )
        )
        for n, v in (
            (5, 131.0),
            (10, 130.9),
            (20, 130.6),
            (30, 130.3),
            (60, 129.8),
            (120, 125.0),
        ):
            self.x[f"ma{n}"] = v
            self.x[f"rise{n}"] = 20
        self.x.loc[240, "low"] = 80.0
        self.x.loc[240:, "raw_vol"] = 200.0
        self.x.loc[280, "high"] = 150.0
        self.x.loc[281:299, ["close", "high", "low"]] = [130.0, 132.0, 128.0]
        self.x.loc[281:287, "true_range_pct"] = 1.2
        self.x.loc[288:293, "true_range_pct"] = 0.8
        self.x.loc[294:299, "true_range_pct"] = 0.4
        self.e = pd.DataFrame(
            [
                dict(
                    trade_date=self.dates[265],
                    qualifying_board=2,
                    continuous_board=2,
                    board_type="continuous",
                    name="样本",
                    lu_desc="算力",
                )
            ]
        )
        self.as_of = self.dates[299]

    def test_complete_gates_and_future_daily_tail_do_not_change_snapshot(self):
        a = daily_pool_snapshot(self.x, self.e, self.as_of, CombinedRules())
        self.assertTrue(a["eligible"], a["reason"])
        changed = self.x.copy()
        changed.loc[300:, "high"] = 999.0
        b = daily_pool_snapshot(changed, self.e, self.as_of, CombinedRules())
        self.assertEqual(a, b)

    def test_target_ma_must_itself_rise_20_days(self):
        self.x.loc[299, ["rise20", "rise30", "rise60"]] = [19, 20, 19]
        r = daily_pool_snapshot(self.x, self.e, self.as_of, CombinedRules())
        self.assertTrue(r["eligible"])
        self.assertEqual(r["rising_targets"], "30")
        self.x.loc[299, "rise30"] = 19
        r = daily_pool_snapshot(self.x, self.e, self.as_of, CombinedRules())
        self.assertFalse(r["eligible"])
        self.assertIn("medium_ma20day_rise", r["reason"])

    def test_new_high_and_wave_gain_constraints(self):
        self.x.loc[200, "high"] = 151.0
        r = daily_pool_snapshot(self.x, self.e, self.as_of, CombinedRules())
        self.assertFalse(r["eligible"])
        self.assertIn("stage_120d_new_high", r["reason"])
        self.x.loc[200, "high"] = 115.0
        self.x.loc[240, "low"] = 70.0
        self.assertIn(
            "first_wave_gain",
            daily_pool_snapshot(self.x, self.e, self.as_of, CombinedRules())["reason"],
        )

    def test_volatility_must_progressively_contract(self):
        self.x.loc[294:299, "true_range_pct"] = 1.0
        r = daily_pool_snapshot(self.x, self.e, self.as_of, CombinedRules())
        self.assertFalse(r["eligible"])
        self.assertIn("volatility_contracts", r["reason"])

    def test_future_board_event_does_not_change_prior_daily_pool(self):
        original = daily_pool_snapshot(self.x, self.e, self.as_of, CombinedRules())
        future = self.e.iloc[0].copy()
        future["trade_date"] = self.dates[310]
        future["qualifying_board"] = 9
        e = pd.concat([self.e, pd.DataFrame([future])], ignore_index=True)
        self.assertEqual(
            original, daily_pool_snapshot(self.x, e, self.as_of, CombinedRules())
        )

    def test_three_day_two_board_is_allowed_but_other_scattered_counts_are_not(self):
        k = pd.DataFrame(
            dict(
                ts_code=["600000.SH"] * 4,
                trade_date=["20260901"] * 4,
                status=["2连板", "3天2板", "5天2板", "5连板"],
            )
        )
        e = board_events(k, CombinedRules())
        self.assertEqual(e.qualifying_board.tolist(), [2, 2, 0, 5])
        self.assertEqual(
            e.board_type.tolist(),
            ["continuous", "3day2", "single_or_other", "continuous"],
        )

    def test_prepare_daily_keeps_missing_days_and_resets_ma_rising_run(self):
        dates = self.dates[:180]
        raw = pd.DataFrame(
            dict(
                trade_date=dates,
                open=np.arange(180) + 100.0,
                high=np.arange(180) + 102.0,
                low=np.arange(180) + 99.0,
                close=np.arange(180) + 101.0,
                vol=100.0,
            )
        )
        adj = pd.DataFrame(dict(trade_date=dates, adj_factor=1.0))
        a = prepare_daily(raw, adj, dates)
        self.assertGreater(a.iloc[150].rise60, 20)
        b = prepare_daily(raw.drop(140), adj, dates)
        self.assertFalse(b.iloc[140].valid_day)
        self.assertTrue(pd.isna(b.iloc[150].ma60))
        c = raw.copy()
        c.loc[160:, ["open", "high", "low", "close"]] *= 3
        changed = prepare_daily(c, adj, dates)
        pd.testing.assert_frame_equal(a.iloc[:160], changed.iloc[:160])


class ExitTests(unittest.TestCase):
    def decide(self, f, signal=None):
        return exit_decisions(
            f, signal or SIGNAL, "2026-09-01 09:46", 101.0, CombinedRules()
        )

    def test_failed_restart_is_requested_immediately_even_on_buy_day(self):
        trace, status = self.decide(exit_frame([99.0, 110.0]))
        self.assertEqual(status, "exit_requested")
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["reason"], "failed_restart_16bars")

    def test_sideways_alignment_and_two_day_timeout(self):
        f = exit_frame([102.0] * 36)
        trace, _ = self.decide(f)
        self.assertEqual(trace[-1]["bars"], 32)
        self.assertEqual(trace[-1]["reason"], "sideways_32bars")
        f.loc[0, "ma5"] = 97.0
        trace, _ = self.decide(f)
        self.assertEqual(trace[-1]["reason"], "sideways_lost_alignment")

    def test_normal_trend_uses_ma30(self):
        f = exit_frame([106.0, 105.0])
        f["ma30"] = 105.5
        signal = {**SIGNAL, "launch_start_raw": 90.0}
        trace, _ = self.decide(f, signal)
        self.assertEqual(trace[-1]["reason"], "trend_below_ma30")

    def test_acceleration_first_large_bear(self):
        f = exit_frame([101.2, 101.4, 101.6, 101.8, 102.5, 104.0, 105.0, 107.0, 105.5])
        f.loc[8, ["body_pct", "body_fraction"]] = [-1.5, -0.8]
        trace, _ = self.decide(f, {**SIGNAL, "launch_start_raw": 90.0})
        self.assertTrue(trace[-1]["acceleration"])
        self.assertEqual(trace[-1]["reason"], "acceleration_first_large_bear")

    def test_drawdown_at_close_and_recovery(self):
        f = exit_frame([110.0] * 14 + [104.0])
        f["body_pct"] = 0.0
        f["p_high"] = 110.0
        trace, _ = self.decide(f, {**SIGNAL, "launch_start_raw": 90.0})
        self.assertEqual(trace[-1]["time"], "2026-09-01 15:00:00")
        self.assertEqual(trace[-1]["reason"], "drawdown5_unrecovered_at_close")
        f.loc[14, "p_close"] = 106.0
        trace, status = self.decide(f, {**SIGNAL, "launch_start_raw": 90.0})
        self.assertEqual(status, "open_at_cutoff")

    def test_future_tail_cannot_erase_existing_exit_request(self):
        f = exit_frame([99.0, 110.0, 111.0])
        a = self.decide(f)
        f.loc[1:, "valid_bar"] = False
        b = self.decide(f)
        self.assertEqual(a, b)


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.calendar = ["20260831", "20260901", "20260902"]
        times = slots(self.calendar[1:])
        self.f = pd.DataFrame(
            dict(
                datetime=times,
                adj_factor=1.0,
                valid_bar=True,
                strict_restart_alignment=False,
                coil_ready=False,
                p_close=100.0,
                close=100.0,
                p_open=100.0,
                body_pct=0.0,
                body_fraction=0.0,
                volume_multiple8=1.0,
                close_location=0.5,
                pre_restart_range_pct=5.0,
                vol=100.0,
                coil30_high=100.5,
                coil30_low=100.0,
                coil30_pct=0.5,
                coil_with_ma60_pct=1.5,
            )
        )
        self.f.loc[2, "coil_ready"] = True
        self.f.loc[3:4, ["p_open", "p_close", "close", "vol"]] = [
            [100.0, 100.3, 100.3, 300.0],
            [100.3, 101.5, 101.5, 300.0],
        ]
        self.f.loc[
            3:4, ["body_pct", "body_fraction", "volume_multiple8", "close_location"]
        ] = [1.2, 0.8, 2.0, 0.9]
        self.f.loc[3:4, "strict_restart_alignment"] = True
        daily = dict(
            eligible=True,
            context_date="20260831",
            name="样本",
            daily_rise20=20,
            daily_rise30=20,
            daily_rise60=20,
            daily_ma20=100.0,
            daily_ma30=100.0,
            daily_ma60=99.0,
        )
        self.pool = {
            "20260831": daily,
            "20260901": {**daily, "context_date": "20260901"},
        }

    def scan(self, f=None, pool=None):
        return combined_entries(
            f if f is not None else self.f,
            pool or self.pool,
            self.calendar,
            "20260901",
            "20260902",
            CombinedRules(),
        )

    def test_entry_uses_second_completed_bar_not_first(self):
        signs = self.scan()
        self.assertEqual(len(signs), 1)
        self.assertEqual(signs.iloc[0].signal_time, "2026-09-01 10:45:00")
        self.assertEqual(signs.iloc[0].coil_to_restart_bars, 2)
        self.assertEqual(signs.iloc[0].daily_snapshot_time, "20260831 15:00:00")
        self.assertEqual(signs.iloc[0].launch_start_raw, 100.3)

    def test_no_future_price_or_daily_context_can_create_earlier_signal(self):
        expected = self.scan(self.f.iloc[:5])
        changed = self.f.copy()
        changed.loc[5:, "p_close"] = 200.0
        actual = self.scan(changed)
        pd.testing.assert_frame_equal(expected, actual)
        self.assertTrue(
            self.scan(
                pool={
                    "20260831": {"eligible": False},
                    "20260901": self.pool["20260901"],
                }
            ).empty
        )

    def test_final_strict_alignment_required_even_after_volume_bull(self):
        self.f["strict_restart_alignment"] = False
        self.assertTrue(self.scan().empty)

    def relaxed_fixture(self):
        f = self.f.copy()
        f["coil_ready"] = False
        f["strict_restart_alignment"] = False
        f["origin"] = "volume_impulse"
        f["coil30_pct"] = 2.0
        f["coil_with_ma60_pct"] = 9.0
        for n, ma in (
            (5, 101),
            (10, 100.8),
            (20, 100.6),
            (30, 100.3),
            (60, 98),
            (120, 97),
        ):
            f[f"ma{n}"] = ma
            f[f"rise{n}"] = 1
        pool = {
            d: {
                **r,
                "daily_relaxed_intraday": True,
                "recent_rising_ma_touch": True,
                "last_touch_ma": 30,
                "last_touch_date": "20260831",
                "daily_ma30": 90.0,
            }
            for d, r in self.pool.items()
        }
        return f, pool

    def scan_relaxed(self, f, pool):
        return combined_entries(
            f, pool, self.calendar, "20260901", "20260902", revised_rules()
        )

    def test_daily_coil_allows_2pct_short_without_ma60_cluster_or_retouch(self):
        f, pool = self.relaxed_fixture()
        s = self.scan_relaxed(f, pool)
        self.assertEqual(len(s), 1)
        self.assertEqual(s.iloc[0].coil_branch, "daily_relaxed")
        self.assertIsNone(s.iloc[0].rule_coil_with_ma60_pct)
        self.assertGreater(s.iloc[0].daily_target_distance_pct, 3)

    def test_relaxation_needs_both_prior_daily_contexts_not_future_daily_coil(self):
        f, pool = self.relaxed_fixture()
        pool["20260831"]["daily_relaxed_intraday"] = False
        self.assertTrue(self.scan_relaxed(f, pool).empty)

    def test_relaxed_group_still_needs_short_alignment_and_rising_long_mas(self):
        f, pool = self.relaxed_fixture()
        f["ma10"] = f.ma20 - 0.1
        self.assertFalse(relaxed_restart_alignment(f.iloc[4]))
        self.assertTrue(self.scan_relaxed(f, pool).empty)
        f, pool = self.relaxed_fixture()
        f["rise120"] = 0
        self.assertTrue(self.scan_relaxed(f, pool).empty)

    def test_relaxed_group_does_not_silently_drop_compression_or_volume(self):
        f, pool = self.relaxed_fixture()
        f["pre_restart_range_pct"] = 10.01
        self.assertTrue(self.scan_relaxed(f, pool).empty)
        f, pool = self.relaxed_fixture()
        f["vol"] = 100.0
        f["volume_multiple8"] = 1.0
        f["body_pct"] = 0.0
        self.assertTrue(self.scan_relaxed(f, pool).empty)


class RevisedDailyTests(unittest.TestCase):
    setUp = DailyGatesTests.setUp

    def revised_fixture(self):
        x = self.x.copy()
        x.loc[:264, "raw_vol"] = 100.0
        x.loc[265:, "raw_vol"] = 200.0
        x.loc[299, ["ma5", "ma10", "ma20"]] = [130.4, 130.5, 130.6]
        e = self.e.copy()
        e["wave_start_date"] = self.dates[265]
        return x, e

    def test_launch_close_reference_and_pullback_short_cross_are_preserved(self):
        x, e = self.revised_fixture()
        r = daily_pool_snapshot(x, e, self.as_of, revised_rules())
        self.assertTrue(r["eligible"], r["reason"])
        self.assertEqual(r["wave_start_date"], self.dates[265])
        self.assertAlmostEqual(r["stage_gain_pct"], 100 * (150 / 110 - 1))
        self.assertTrue(r["daily_relaxed_intraday"])
        self.assertTrue(r["recent_rising_ma_touch"])
        x.loc[240, "low"] = 30
        self.assertEqual(r, daily_pool_snapshot(x, e, self.as_of, revised_rules()))

    def test_revised_pool_is_future_tail_invariant(self):
        x, e = self.revised_fixture()
        expected = daily_pool_snapshot(x, e, self.as_of, revised_rules())
        future = e.iloc[0].copy()
        future["trade_date"] = self.dates[310]
        future["wave_start_date"] = self.dates[309]
        future["qualifying_board"] = 12
        changed = pd.concat([e, pd.DataFrame([future])], ignore_index=True)
        x.loc[300:, ["close", "high"]] = 999
        self.assertEqual(
            expected, daily_pool_snapshot(x, changed, self.as_of, revised_rules())
        )

    def test_new_board_does_not_erase_an_earlier_eligible_pullback_episode(self):
        x, e = self.revised_fixture()
        recent = e.iloc[0].copy()
        recent["trade_date"] = self.dates[298]
        recent["wave_start_date"] = self.dates[298]
        changed = pd.concat([e, pd.DataFrame([recent])], ignore_index=True)
        result = daily_pool_snapshot(x, changed, self.as_of, revised_rules())
        self.assertTrue(result["eligible"], result["reason"])
        self.assertEqual(result["wave_start_date"], self.dates[265])
        self.assertEqual(result["launch_episodes_evaluated"], 2)

    def test_twenty_pct_requires_actual_dated_limit_and_preserves_native_height(self):
        calendar = (
            pd.bdate_range("2026-08-01", "2026-09-16").strftime("%Y%m%d").tolist()
        )
        dates = ["20260807", "20260813", "20260821"]
        k = pd.DataFrame(
            dict(
                ts_code=["688137.SH"] * 3,
                trade_date=dates,
                status=["首板", "首板", "2连板"],
            )
        )
        raw = pd.DataFrame(
            dict(
                ts_code=["688137.SH"] * 3,
                trade_date=dates,
                close=[120.0, 120.0, 120.0],
                pre_close=100.0,
            )
        )
        cap = raw[["ts_code", "trade_date"]].assign(up_limit=120.0)
        e = board_events(k, revised_rules(), raw, cap, calendar)
        self.assertEqual(e.continuous_board.tolist(), [1, 1, 2])
        self.assertEqual(e.qualifying_board.tolist(), [2, 2, 4])
        self.assertEqual(e.wave_start_date.tolist(), ["20260807"] * 3)
        raw.loc[0, "close"] = 119.0
        e = board_events(k, revised_rules(), raw, cap, calendar)
        self.assertEqual(e.iloc[0].qualifying_board, 1)
        self.assertFalse(e.iloc[0].twenty_pct_limit_verified)

    def test_template_wave_and_volume_metrics_ignore_future_peak(self):
        x, _ = self.revised_fixture()
        x["raw_close"] = x.close
        x["raw_low"] = x.low
        x["raw_high"] = x.high
        x["adj_factor"] = 1.0
        x["atr14_pct"] = x.true_range_pct.rolling(14).mean()
        launch = self.dates[265]
        before = wave_metrics(x, launch, self.as_of)
        x.loc[300:, ["raw_high", "high", "raw_vol"]] = 999.0
        self.assertEqual(before, wave_metrics(x, launch, self.as_of))
        self.assertEqual(before["advance_volume_ratio"], 2.0)
        self.assertEqual(before["correction_volume_ratio"], 2.0)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.calendar = ["20260901", "20260902", "20260903"]
        self.one = pd.DataFrame(
            dict(
                datetime=minute_slots(self.calendar),
                open=101.0,
                high=101.1,
                low=100.9,
                close=101.0,
                vol=100.0,
                adj_factor=1.0,
            )
        )
        self.one.loc[
            self.one.datetime.dt.strftime("%Y%m%d").eq("20260902"),
            ["open", "high", "low", "close"],
        ] = [95.0, 95.1, 94.9, 95.0]
        self.limits = {(d, "600000.SH"): (110.0, 90.0) for d in self.calendar}
        self.prior = {(d, "600000.SH"): 100.0 for d in self.calendar}
        self.f = exit_frame([99.0] + [101.0] * 35, self.calendar)

    def run_trade(self, **kwargs):
        return execute_dynamic(
            SIGNAL,
            self.f,
            self.one,
            self.calendar,
            self.limits,
            self.prior,
            CombinedRules(),
            **kwargs,
        )[0]

    def test_same_day_failure_sells_next_open_at_gap_price(self):
        r = self.run_trade()
        self.assertTrue(r["tplus1_deferred"])
        self.assertEqual(r["entry_time"], "2026-09-01 09:46:00")
        self.assertEqual(r["exit_time"], "2026-09-02 09:30:00")
        self.assertEqual(r["exit_open"], 95.0)
        self.assertLess(r["net_pct"], -5)

    def test_locked_limit_down_waits_for_actual_sellable_open(self):
        t = self.one.datetime.between("2026-09-02 09:31", "2026-09-02 09:33")
        self.one.loc[t, ["open", "high", "low", "close"]] = 90.0
        r = self.run_trade()
        self.assertEqual(r["blocked_exit_minutes"], 3)
        self.assertEqual(r["exit_time"], "2026-09-02 09:33:00")

    def test_missing_minute_is_retained_and_not_bridged(self):
        self.one = self.one[self.one.datetime.ne(pd.Timestamp("2026-09-01 09:50"))]
        r = self.run_trade()
        self.assertEqual(r["exit_status"], "holding_minute_missing")
        self.assertIsNone(r["net_pct"])

    def test_future_duplicate_after_exit_does_not_invalidate_trade(self):
        self.one = pd.concat(
            [self.one, self.one[self.one.datetime.eq(pd.Timestamp("2026-09-03 10:00"))]]
        )
        self.assertEqual(self.run_trade()["exit_status"], "closed_proxy")

    def test_entry_at_limit_is_separate_assumption(self):
        signal = {**SIGNAL, "signal_close": 110.0, "launch_start_raw": 100.0}
        t = self.one.datetime.eq(pd.Timestamp("2026-09-01 09:47"))
        self.one.loc[t, ["open", "high", "low", "close"]] = 110.0
        conservative, _ = execute_dynamic(
            signal,
            self.f,
            self.one,
            self.calendar,
            self.limits,
            self.prior,
            CombinedRules(),
        )
        assumed, _ = execute_dynamic(
            signal,
            self.f,
            self.one,
            self.calendar,
            self.limits,
            self.prior,
            CombinedRules(),
            True,
        )
        self.assertEqual(conservative["entry_status"], "entry_at_upper_limit")
        self.assertTrue(assumed["limit_fill_assumption"])


if __name__ == "__main__":
    unittest.main()
