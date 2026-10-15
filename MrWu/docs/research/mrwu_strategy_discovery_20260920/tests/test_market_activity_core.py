import unittest

import numpy as np
import pandas as pd

import market_activity_core as core


class MarketActivityCoreTest(unittest.TestCase):
    def calendar(self, n=80):
        return pd.bdate_range("2024-07-01", periods=n).strftime("%Y%m%d").tolist()

    def test_causal_baseline_uses_prior_sessions(self):
        dates = self.calendar()
        amount = pd.Series(np.arange(1, len(dates) + 1, dtype=float), index=dates)
        stats = core.causal_statistics(amount)
        expected = np.median(np.arange(1, 61, dtype=float))
        self.assertAlmostEqual(stats.loc[dates[60], "B60"], expected)

    def test_missing_is_unknown_not_zero(self):
        dates = self.calendar(3)
        daily = pd.DataFrame({
            "trade_date": [dates[0], dates[2]],
            "amount_cny": [1e12, 1.1e12],
            "coverage_state": ["OBSERVED_UNIVERSE_PROXY"] * 2,
        })
        frame = core.input_frame(daily, dates)
        self.assertTrue(pd.isna(frame.loc[dates[1], "A_cny"]))
        self.assertEqual(frame.loc[dates[1], "coverage_state"], "UNKNOWN")

    def test_decision_phase_is_exact_prior_session(self):
        dates = self.calendar(80)
        daily = pd.DataFrame({
            "trade_date": dates,
            "amount_cny": np.linspace(1e12, 2e12, len(dates)),
            "coverage_state": ["OBSERVED_UNIVERSE_PROXY"] * len(dates),
        })
        labels = core.build_regimes(daily, dates)
        rel = labels[labels.variant.eq("REL")].reset_index(drop=True)
        self.assertEqual(rel.loc[70, "decision_source_date"], rel.loc[69, "trade_date"])
        self.assertEqual(rel.loc[70, "decision_phase"], rel.loc[69, "confirmed_phase"])


if __name__ == "__main__":
    unittest.main()
