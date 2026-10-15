"""Boundary, causal forecast and execution tests for the MA250 experiment."""
import unittest
from dataclasses import replace
import numpy as np
import pandas as pd
from study_ma250_reclaim import Rules, flat_projection, crossing_level, prepare, signals, replay


def bars(days):
    from study_ma250_reclaim import TIMES
    rows = []
    for d in days:
        for t in TIMES:
            rows.append(dict(datetime=pd.Timestamp(f"{d} {t}"), open=10., high=10.1, low=9.9,
                             close=10., vol=1000., adj_factor=1.))
    return pd.DataFrame(rows)


class StudyTest(unittest.TestCase):
    def test_projection_matches_explicit_append_all_horizons(self):
        rng = np.random.default_rng(17)
        for _ in range(20):
            hist = rng.uniform(8, 15, 400)
            for r in flat_projection(hist):
                ext = np.r_[hist, np.full(r['h'], hist[-1])]
                for n in (20, 250):
                    self.assertAlmostEqual(r[f'ma{n}'], ext[-n:].mean(), places=12)

    def test_projection_already_above_is_not_future_cross(self):
        x = np.r_[np.full(230, 8.), np.full(20, 10.)]
        self.assertFalse(any(r['golden_cross'] for r in flat_projection(x)))

    def test_interpolated_cross_equal_prices(self):
        self.assertAlmostEqual(crossing_level(9, 10, 12, 11), 10.5)

    def test_gap_keeps_clock_and_invalidates_rolling(self):
        days = pd.bdate_range('2025-07-01', periods=22).strftime('%Y%m%d').tolist()
        raw = bars(days).drop(index=[270])
        f = prepare(raw, days)
        self.assertEqual(len(f), 22 * 16)
        self.assertFalse(f.valid.iloc[270])
        self.assertTrue(np.isnan(f.ma250.iloc[300]))

    def test_duplicate_does_not_erase_prior_history(self):
        days = pd.bdate_range('2025-07-01', periods=22).strftime('%Y%m%d').tolist()
        raw = bars(days)
        f = prepare(pd.concat([raw, raw.iloc[[270]]]), days)
        self.assertTrue(f.valid.iloc[:270].all())
        self.assertFalse(f.valid.iloc[270])

    def test_prefix_signals_cannot_change_with_future(self):
        days = pd.bdate_range('2025-07-01', periods=70).strftime('%Y%m%d').tolist()
        raw = bars(days)
        price = np.r_[np.linspace(15, 8, 800), np.linspace(8, 13, 320)]
        raw['open'] = raw['close'] = price
        raw['high'], raw['low'] = price + .1, price - .1
        full = prepare(raw, days)
        early = prepare(raw.iloc[:960], days[:60])
        a, _, _ = signals(full, '20250701', '20261231')
        b, _, _ = signals(early, '20250701', '20261231')
        self.assertEqual([s for s in a if s['index'] < 960], b)
        pd.testing.assert_frame_equal(full.iloc[:960].reset_index(drop=True), early, check_dtype=False)

    def setup_execution(self):
        days = ['20260105', '20260106', '20260107']
        f = prepare(bars(days), days)
        f['ma30'] = 9.
        limits = {d: dict(up_limit=12., down_limit=8.) for d in days}
        s = dict(id='S001', index=0, time=str(f.datetime.iloc[0]), stop=9.5,
                 stop_raw_at_signal=9.5, projected_cross_h=8)
        return f, limits, s

    def test_t1_stop_is_pending_until_next_session_open(self):
        f, li, s = self.setup_execution()
        f.loc[2, ['close', 'p_close']] = 9.4
        r = replay(f, [s], li, '20260105', '20260107')
        t = r['trades'][0]
        self.assertEqual(t['entry_index'], 1)
        self.assertEqual(t['exit_index'], 16)
        self.assertEqual(t['exit_decision_index'], 2)
        self.assertEqual(t['exit_time'], '2026-01-06 09:30:00')
        self.assertGreater(r['summary']['T_plus_1_wait_bars'], 0)

    def test_timeout_at_signal_plus_16_not_fill_plus_16(self):
        f, li, s = self.setup_execution()
        r = replay(f, [s], li, '20260105', '20260107')
        t = r['trades'][0]
        self.assertEqual(t['exit_reason'], 'no_ma20_ma250_cross_by_16')
        self.assertEqual(t['exit_decision_index'], 16)
        self.assertEqual(t['exit_index'], 17)

    def test_cross_at_deadline_is_success(self):
        f, li, s = self.setup_execution()
        f.loc[16, 'cross20_250'] = True
        r = replay(f, [s], li, '20260105', '20260107')
        self.assertEqual(r['summary']['closed'], 0)
        self.assertEqual(r['trades'][0]['actual_cross_h'], 16)

    def test_down_limit_sell_defers_without_nominal_stop_fill(self):
        f, li, s = self.setup_execution()
        f.loc[2, ['close', 'p_close']] = 9.4
        f.loc[16, ['open', 'p_open']] = 8.
        r = replay(f, [s], li, '20260105', '20260107')
        self.assertEqual(r['trades'][0]['exit_index'], 17)
        self.assertTrue(any(v['kind'] == 'down_limit_open_unfilled' for v in r['issues']))

    def test_up_limit_buy_unfilled_and_no_signal_bar_fill(self):
        f, li, s = self.setup_execution()
        f.loc[1, ['open', 'p_open']] = 12.
        r = replay(f, [s], li, '20260105', '20260107')
        self.assertEqual(r['summary']['buys'], 0)
        self.assertEqual(r['dispositions'][0]['status'], 'up_limit_open_unfilled')

    def test_ma30_exit_arms_only_after_close_above_paid_price(self):
        f, li, s = self.setup_execution()
        f['ma30'] = 10.1
        f.loc[16, 'cross20_250'] = True
        f.loc[16, ['close', 'p_close']] = 10.05
        r = replay(f, [s], li, '20260105', '20260107')
        self.assertEqual(r['trades'][0]['exit_reason'], 'below_ma30_after_rise')
        self.assertEqual(r['trades'][0]['exit_decision_index'], 16)

    def test_outcome_bar_close_cannot_cancel_pending_sell(self):
        f, li, s = self.setup_execution()
        f.loc[2, ['close', 'p_close']] = 9.4
        f.loc[16, ['close', 'p_close']] = 11.9
        r = replay(f, [s], li, '20260105', '20260107')
        self.assertLess(r['trades'][0]['exit_price'], 10.)

    def test_factor_change_blocks_cash_account_claim(self):
        f, li, s = self.setup_execution()
        f.loc[16:, 'adj_factor'] = 1.1
        with self.assertRaisesRegex(ValueError, 'Corporate-action'):
            replay(f, [s], li, '20260105', '20260107')


if __name__ == '__main__':
    unittest.main()
