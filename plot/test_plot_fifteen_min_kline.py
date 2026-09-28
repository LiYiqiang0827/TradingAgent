from datetime import date
from pathlib import Path
import unittest
from unittest.mock import patch

import matplotlib.pyplot as plt
import pandas as pd

from plot.plot_fifteen_min_kline import (
    DEFAULT_LOOKAHEAD_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    _apply_datetime_ticks,
    build_parser,
    build_save_path,
    load_fifteen_min_data,
    resolve_window,
)


class PlotFifteenMinKlineTest(unittest.TestCase):
    def test_datetime_ticks_use_first_bar_and_skip_every_other_day(self):
        rows = []
        for day in pd.bdate_range("2026-08-03", periods=20):
            for offset in range(16):
                stamp = day + pd.Timedelta(hours=9, minutes=45) + pd.Timedelta(minutes=15 * offset)
                rows.append({"datetime": stamp, "date": day.normalize()})
        frame = pd.DataFrame(rows)
        fig, ax = plt.subplots()
        try:
            _apply_datetime_ticks(ax, frame)
            self.assertEqual(list(ax.get_xticks()), list(range(0, 20 * 16, 2 * 16)))
            labels = [item.get_text() for item in ax.get_xticklabels()]
            self.assertTrue(all(label.endswith("09:45") for label in labels))
        finally:
            plt.close(fig)

    def test_cli_defaults_show_one_month_before_and_one_week_after(self):
        args = build_parser().parse_args(["000001.SZ", "--trade-date", "20260924"])
        self.assertEqual(args.lookback_days, DEFAULT_LOOKBACK_DAYS)
        self.assertEqual(args.lookback_days, 30)
        self.assertEqual(args.lookahead_days, DEFAULT_LOOKAHEAD_DAYS)
        self.assertEqual(args.lookahead_days, 7)

    def test_single_and_multi_window(self):
        single = resolve_window(
            start_date=None, end_date=None, trade_date="20260924",
            lookback_days=5, lookahead_days=2, ma_warmup_bars=250,
        )
        self.assertEqual(single.display_start, pd.Timestamp("2026-09-19"))
        self.assertEqual(single.display_end, pd.Timestamp("2026-09-26"))
        self.assertLess(single.fetch_start, single.display_start)
        self.assertEqual(
            build_save_path("000001.SZ", single, Path("unused")),
            Path("unused/kline_15min_000001.SZ_20260924_20260924.jpg"),
        )

        multi = resolve_window(
            start_date=None, end_date=None, trade_date=None,
            trade_dates=["20260921,20260924", "20260921"],
            lookback_days=0, lookahead_days=0, ma_warmup_bars=0,
        )
        self.assertEqual(len(multi.marked_dates), 2)
        self.assertEqual(
            build_save_path("000001.SZ", multi, Path("unused")),
            Path("unused/kline_15min_000001.SZ_20260921_20260924_multidays.jpg"),
        )

    def test_to_latest_and_invalid_modes(self):
        window = resolve_window(
            start_date=None, end_date=None, trade_date="20260924",
            lookback_days=0, lookahead_days=0, ma_warmup_bars=0,
            to_latest=True, today=date(2026, 9, 26),
        )
        self.assertEqual(window.display_end, pd.Timestamp("2026-09-26"))
        with self.assertRaisesRegex(ValueError, "互斥"):
            resolve_window(
                start_date="20260921", end_date="20260924", trade_date="20260924",
                lookback_days=0, lookahead_days=0, ma_warmup_bars=0,
            )

    def test_qfq_adjusts_price_but_keeps_actual_volume(self):
        times = pd.date_range("2026-09-24 09:45", periods=16, freq="15min")
        frame = pd.DataFrame({
            "ts_code": "000001.SZ", "name": "平安银行", "trade_date": 20260924,
            "datetime": times, "time_idx": range(16),
            "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.1,
            "vol": 10000, "amount": 100000.0, "adj_factor": 2.0,
        })
        factors = pd.DataFrame({"trade_date": [20260924], "adj_factor": [4.0]})
        window = resolve_window(
            start_date=None, end_date=None, trade_date="20260924",
            lookback_days=0, lookahead_days=0, ma_warmup_bars=0,
        )
        with patch(
            "plot.plot_fifteen_min_kline.get_fifteenMin",
            side_effect=[frame, factors],
        ):
            result = load_fifteen_min_data("000001.SZ", window, qfq=True)
        self.assertAlmostEqual(result.iloc[0]["open"], 5.0)
        self.assertEqual(result.iloc[0]["vol"], 10000)


if __name__ == "__main__":
    unittest.main()
