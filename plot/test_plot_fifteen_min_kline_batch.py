from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plot.plot_fifteen_min_kline import DEFAULT_SAVE_DIR
from plot.plot_fifteen_min_kline_batch import (
    WATCHLIST_SAVE_DIR,
    build_parser,
    plot_batch,
    resolve_batch_save_dir,
)


class PlotFifteenMinKlineBatchTest(unittest.TestCase):
    def test_cli_defaults_show_one_month_before_and_one_week_after(self):
        args = build_parser().parse_args(["--items-json", "[]"])
        self.assertEqual(args.lookback_days, 30)
        self.assertEqual(args.lookahead_days, 7)

    def test_default_save_directories(self):
        self.assertEqual(resolve_batch_save_dir(None, watchlist_mode=False), DEFAULT_SAVE_DIR)
        self.assertEqual(resolve_batch_save_dir(None, watchlist_mode=True), WATCHLIST_SAVE_DIR)

    def test_batch_isolates_invalid_item(self):
        items = [
            {"trade_date": "20260924"},
            {"ts_code": "000001.SZ", "trade_date": "20260924"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("plot.plot_fifteen_min_kline_batch.load_stock_name_map", return_value={}),
                patch("plot.plot_fifteen_min_kline_batch.load_fifteen_min_data", return_value=object()),
                patch(
                    "plot.plot_fifteen_min_kline_batch.plot_fifteen_min_kline",
                    side_effect=lambda *args, **kwargs: kwargs["save_path"],
                ),
            ):
                summary = plot_batch(items, save_dir=Path(directory), progress=False)
        self.assertEqual(summary.success, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.errors[0]["index"], 1)


if __name__ == "__main__":
    unittest.main()
