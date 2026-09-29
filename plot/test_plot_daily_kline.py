from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import matplotlib.pyplot as plt
import pandas as pd
from PIL import Image

from plot.plot_daily_kline import (
    DEFAULT_MA_WINDOWS,
    _trade_positions,
    build_parser,
    build_save_path,
    normalize_ts_code,
    parse_date,
    plot_daily_kline,
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

    def test_buy_sell_dates_extend_window_and_filename(self):
        window = resolve_window(
            start_date=None, end_date=None, trade_date="20260605",
            lookback_months=1, lookahead_months=1, ma_warmup_days=400,
            to_latest=False, buy_date="20260501", sell_date="20260810",
        )
        self.assertEqual(window.buy_date, pd.Timestamp("2026-05-01"))
        self.assertEqual(window.sell_date, pd.Timestamp("2026-08-10"))
        self.assertEqual(window.display_end, pd.Timestamp("2026-08-10"))
        self.assertEqual(
            build_save_path("000001.SZ", window, Path("unused")).name,
            "kline_day_000001.SZ_20260605_20260605_buy20260501_sell20260810.jpg",
        )

    def test_buy_sell_date_validation_and_missing_bar(self):
        common = dict(start_date=None, end_date=None, trade_date="20260605",
                      lookback_months=1, lookahead_months=1,
                      ma_warmup_days=400, to_latest=False)
        with self.assertRaisesRegex(ValueError, "必须同时提供"):
            resolve_window(**common, buy_date="20260608")
        with self.assertRaisesRegex(ValueError, "不能晚于"):
            resolve_window(**common, buy_date="20260609", sell_date="20260608")
        window = resolve_window(**common, buy_date="20260608", sell_date="20260609")
        dates = pd.Series(pd.to_datetime(["2026-06-05", "2026-06-08"]))
        with self.assertRaisesRegex(ValueError, "卖出日期.*没有交易数据"):
            _trade_positions(dates, window)

    def test_buy_sell_marked_chart_is_readable(self):
        window = resolve_window(
            start_date="20260605", end_date="20260610", trade_date=None,
            lookback_months=0, lookahead_months=0, ma_warmup_days=0,
            to_latest=False, buy_date="20260608", sell_date="20260609",
        )
        frame = pd.DataFrame({
            "date": pd.to_datetime(["20260605", "20260608", "20260609", "20260610"]),
            "open": [10, 10.2, 10.5, 10.3],
            "high": [10.3, 10.7, 10.6, 10.5],
            "low": [9.9, 10.1, 10.2, 10.2],
            "close": [10.2, 10.6, 10.3, 10.4],
            "vol": [10000, 12000, 11000, 9000],
        })
        for period in DEFAULT_MA_WINDOWS:
            frame[f"ma{period}"] = frame["close"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trade.jpg"
            with patch("plot.plot_daily_kline.plt.close"):
                result = plot_daily_kline(
                    frame, ts_code="000001.SZ", stock_name="样本", window=window,
                    qfq=True, save_path=path, show=False,
                )
                volume_bars = plt.gcf().axes[1].containers[0].patches
                self.assertEqual(
                    [round(bar.get_x() + bar.get_width() / 2) for bar in volume_bars],
                    [0, 1, 2, 3],
                )
                self.assertEqual(
                    [round(bar.get_height(), 1) for bar in volume_bars],
                    [1.0, 1.2, 1.1, 0.9],
                )
            plt.close("all")
            self.assertEqual(result, path.resolve())
            with Image.open(path) as image:
                image.verify()


if __name__ == "__main__":
    unittest.main()
