from datetime import date
from pathlib import Path
import unittest

import pandas as pd

from plot.plot_daily_kline import (
    build_parser,
    build_save_path,
    normalize_ts_code,
    parse_date,
    resolve_window,
)


class PlotDailyKlineTest(unittest.TestCase):
    def test_normalize_ts_code(self):
        self.assertEqual(normalize_ts_code("600519"), "600519.SH")
        self.assertEqual(normalize_ts_code("000001"), "000001.SZ")
        self.assertEqual(normalize_ts_code("830799"), "830799.BJ")
        self.assertEqual(normalize_ts_code("300750.sz"), "300750.SZ")

    def test_range_window_defaults(self):
        window = resolve_window(
            start_date="2026-03-05",
            end_date="2026-06-05",
            trade_date=None,
            lookback_months=12,
            lookahead_months=1,
            ma_warmup_days=400,
            to_latest=False,
        )
        self.assertEqual(window.selected_start, pd.Timestamp("2026-03-05"))
        self.assertEqual(window.selected_end, pd.Timestamp("2026-06-05"))
        self.assertEqual(window.display_start, pd.Timestamp("2025-03-05"))
        self.assertEqual(window.display_end, pd.Timestamp("2026-07-05"))
        self.assertEqual(window.fetch_start, pd.Timestamp("2024-01-30"))
        self.assertFalse(window.trade_date_mode)

    def test_trade_date_to_latest(self):
        window = resolve_window(
            start_date=None,
            end_date=None,
            trade_date="20260305",
            lookback_months=12,
            lookahead_months=1,
            ma_warmup_days=400,
            to_latest=True,
            today=date(2026, 9, 24),
        )
        self.assertEqual(window.display_start, pd.Timestamp("2025-03-05"))
        self.assertEqual(window.display_end, pd.Timestamp("2026-09-24"))
        self.assertTrue(window.trade_date_mode)

    def test_multiple_trade_dates_define_window_and_markers(self):
        window = resolve_window(
            start_date=None,
            end_date=None,
            trade_date=None,
            trade_dates=["2026-06-05", "20260305", "2026-06-05", "2026-04-07"],
            lookback_months=12,
            lookahead_months=1,
            ma_warmup_days=400,
            to_latest=False,
        )
        self.assertEqual(window.selected_start, pd.Timestamp("2026-03-05"))
        self.assertEqual(window.selected_end, pd.Timestamp("2026-06-05"))
        self.assertEqual(window.display_start, pd.Timestamp("2025-03-05"))
        self.assertEqual(window.display_end, pd.Timestamp("2026-07-05"))
        self.assertEqual(
            window.marked_dates,
            (
                pd.Timestamp("2026-03-05"),
                pd.Timestamp("2026-04-07"),
                pd.Timestamp("2026-06-05"),
            ),
        )
        self.assertTrue(window.trade_date_mode)
        self.assertEqual(window.selection_mode, "multi")

        args = build_parser().parse_args(
            ["600519", "--trade-dates", "20260305,20260407", "20260605"]
        )
        self.assertEqual(args.trade_dates, ["20260305,20260407", "20260605"])
        self.assertEqual(
            build_save_path("600519.SH", window, Path("unused")),
            Path("unused/kline_day_600519.SH_20260305_20260605_multidays.jpg"),
        )

    def test_save_path_for_range_and_trade_date(self):
        range_window = resolve_window(
            start_date="2026-03-05",
            end_date="2026-06-05",
            trade_date=None,
            lookback_months=12,
            lookahead_months=1,
            ma_warmup_days=400,
            to_latest=False,
        )
        self.assertEqual(
            build_save_path("600519.SH", range_window, Path("unused")),
            Path("unused/kline_day_600519.SH_20260305_20260605.jpg"),
        )

        trade_window = resolve_window(
            start_date=None,
            end_date=None,
            trade_date="2026-06-05",
            lookback_months=12,
            lookahead_months=1,
            ma_warmup_days=400,
            to_latest=False,
        )
        self.assertEqual(
            build_save_path("000001.SZ", trade_window, Path("unused")),
            Path("unused/kline_day_000001.SZ_20260605_20260605.jpg"),
        )

    def test_invalid_date_modes(self):
        with self.assertRaisesRegex(ValueError, "必须同时提供"):
            resolve_window(
                start_date="2026-03-05",
                end_date=None,
                trade_date=None,
                lookback_months=12,
                lookahead_months=1,
                ma_warmup_days=400,
                to_latest=False,
            )
        with self.assertRaisesRegex(ValueError, "不能同时使用"):
            resolve_window(
                start_date=None,
                end_date=None,
                trade_date="2026-04-01",
                trade_dates=["2026-05-01"],
                lookback_months=12,
                lookahead_months=1,
                ma_warmup_days=400,
                to_latest=False,
            )
        with self.assertRaisesRegex(ValueError, "不能同时使用"):
            resolve_window(
                start_date="2026-03-05",
                end_date="2026-06-05",
                trade_date="2026-04-01",
                lookback_months=12,
                lookahead_months=1,
                ma_warmup_days=400,
                to_latest=False,
            )

    def test_parse_date_rejects_ambiguous_format(self):
        with self.assertRaisesRegex(ValueError, "日期格式错误"):
            parse_date("03/05/2026")


if __name__ == "__main__":
    unittest.main()
