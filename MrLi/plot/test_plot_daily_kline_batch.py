from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plot.plot_daily_kline import DEFAULT_SAVE_DIR
from plot.plot_daily_kline_batch import (
    DEFAULT_WATCHLIST,
    WATCHLIST_SAVE_DIR,
    build_parser,
    load_watchlist,
    merge_watchlist_same_tscode,
    normalize_batch_items,
    parse_items_json,
    plot_batch,
    resolve_batch_save_dir,
)


class PlotDailyKlineBatchTest(unittest.TestCase):
    def test_mixed_items_and_aliases(self):
        items = normalize_batch_items(
            [
                {
                    "ts_code": "600519.SH",
                    "start_date": "2026-03-05",
                    "end_date": "2026-06-05",
                },
                {"tscode": "000001", "tradedate": "20260605"},
            ]
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["ts_code"], "600519.SH")
        self.assertEqual(items[0]["start_date"], "2026-03-05")
        self.assertEqual(items[1]["ts_code"], "000001.SZ")
        self.assertEqual(items[1]["trade_date"], "20260605")

    def test_multiple_trade_dates_item(self):
        items = normalize_batch_items(
            [
                {
                    "ts_code": "000001.SZ",
                    "trade_dates": ["20260605", "2026-04-07", "20260605"],
                }
            ]
        )
        self.assertEqual(
            items[0]["trade_dates"],
            ["20260407", "20260605"],
        )

    def test_buy_sell_are_normalized_and_distinct_trades_are_preserved(self):
        rows = [
            {"ts_code": "000001.SZ", "trade_date": "20260605",
             "buydate": "20260608", "selldate": "20260610"},
            {"ts_code": "000001.SZ", "trade_date": "20260605",
             "buydate": "20260609", "selldate": "20260611"},
        ]
        normalized = normalize_batch_items(rows)
        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["buy_date"], "20260608")
        self.assertEqual(normalized[1]["sell_date"], "20260611")
        merged = merge_watchlist_same_tscode(rows)
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(item["trade_date"] == "20260605" for item in merged))

    def test_invalid_single_trade_date_is_rejected_in_batch(self):
        with self.assertRaisesRegex(ValueError, "必须同时提供"):
            normalize_batch_items([
                {"ts_code": "000001.SZ", "trade_date": "20260605",
                 "buy_date": "20260608"},
            ])

    def test_exact_duplicate_is_removed(self):
        item = {"ts_code": "600519.SH", "trade_date": "20260605"}
        normalized = normalize_batch_items([item, dict(item)])
        self.assertEqual(len(normalized), 1)

    def test_invalid_mixed_dates_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "不能同时使用"):
            normalize_batch_items(
                [
                    {
                        "ts_code": "600519.SH",
                        "trade_date": "20260605",
                        "start_date": "20260305",
                        "end_date": "20260605",
                    }
                ]
            )

    def test_parse_json_list_and_items_wrapper(self):
        direct = parse_items_json('[{"ts_code":"600519.SH","trade_date":"20260605"}]')
        wrapped = parse_items_json(
            '{"items":[{"ts_code":"000001.SZ","trade_date":"20260605"}]}'
        )
        self.assertEqual(direct[0]["ts_code"], "600519.SH")
        self.assertEqual(wrapped[0]["ts_code"], "000001.SZ")

    def test_load_mixed_csv_as_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "watchlist.csv"
            path.write_text(
                "ts_code,trade_date,start_date,end_date,name\n"
                "000001.SZ,20260605,,,平安银行\n"
                "600519.SH,,20260305,20260605,贵州茅台\n",
                encoding="utf-8",
            )
            rows = load_watchlist(path)
        normalized = normalize_batch_items(rows)
        self.assertEqual(normalized[0]["trade_date"], "20260605")
        self.assertEqual(normalized[1]["start_date"], "20260305")

    def test_default_watchlist_schema_merge_mode_and_save_dir(self):
        rows = load_watchlist(DEFAULT_WATCHLIST)
        self.assertGreater(len(rows), 0)
        self.assertIn("ts_code", rows[0])
        self.assertIn("trade_date", rows[0])

        args = build_parser().parse_args(["--watchlist", "--limit", "1"])
        self.assertEqual(args.watchlist, DEFAULT_WATCHLIST)
        self.assertTrue(args.merge_same_tscode)
        self.assertIsNone(args.save_dir)
        self.assertEqual(
            resolve_batch_save_dir(args.save_dir, watchlist_mode=True),
            WATCHLIST_SAVE_DIR,
        )
        self.assertEqual(
            resolve_batch_save_dir(None, watchlist_mode=False),
            DEFAULT_SAVE_DIR,
        )

        no_merge = build_parser().parse_args(["--watchlist", "--no-merge-same-tscode"])
        self.assertFalse(no_merge.merge_same_tscode)

    def test_merge_watchlist_same_tscode(self):
        merged = merge_watchlist_same_tscode(
            [
                {"ts_code": "000001.SZ", "trade_date": "20260605", "name": "平安银行"},
                {"ts_code": "600519.SH", "trade_date": "20260605", "name": "贵州茅台"},
                {"ts_code": "000001.SZ", "trade_date": "20260407", "name": "平安银行"},
                {"ts_code": "000001.SZ", "trade_date": "20260605", "name": "平安银行"},
            ]
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["ts_code"], "000001.SZ")
        self.assertEqual(merged[0]["trade_dates"], ["20260407", "20260605"])
        self.assertEqual(merged[1]["trade_date"], "20260605")

    def test_plot_batch_isolates_invalid_item(self):
        items = [
            {"trade_date": "20260605"},
            {"ts_code": "000001.SZ", "trade_date": "20260605"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("plot.plot_daily_kline_batch.load_stock_name_map", return_value={}),
                patch("plot.plot_daily_kline_batch.load_daily_data", return_value=object()),
                patch(
                    "plot.plot_daily_kline_batch.plot_daily_kline",
                    side_effect=lambda *args, **kwargs: kwargs["save_path"],
                ),
            ):
                summary = plot_batch(items, save_dir=Path(directory), progress=False)
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.success, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.errors[0]["index"], 1)


if __name__ == "__main__":
    unittest.main()
