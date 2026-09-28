from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from static_structure.common import json_ready, normalize_frame
from static_structure.consolidation import detect_platforms
from static_structure.engine import analyze_packet
from static_structure.envelopes import detect_multiscale_envelopes, select_envelope_trendlines
from static_structure.gaps import detect_gaps
from static_structure.patterns import detect_flag, detect_reversal_patterns
from static_structure.pivots import detect_pivots
from static_structure.trendlines import build_connected_swing_paths, detect_acceleration_legs, detect_trend_channels, detect_trendlines
from static_structure.zones import build_zones


def frame_from_close(close: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(close))
    values = np.asarray(close, dtype=float)
    frame = pd.DataFrame(
        {
            "trade_date": [item.strftime("%Y%m%d") for item in dates],
            "open": values * 0.997,
            "high": values * 1.012,
            "low": values * 0.988,
            "close": values,
            "vol": np.linspace(1_000_000, 800_000, len(values)),
            "amount": values * np.linspace(1_000_000, 800_000, len(values)),
        }
    )
    return normalize_frame(frame.to_dict("records"))


class StaticStructureTest(unittest.TestCase):
    def test_pivots_are_confirmed_before_last_bar(self) -> None:
        frame = frame_from_close([10, 10.5, 11, 12, 11, 10.5, 10, 10.7, 11.5, 11, 10.2, 10.8, 11.8, 11.1, 10.4])
        pivots = detect_pivots(frame, timeframe="daily", scales=(("short", 2, 0.10),))
        self.assertTrue(pivots)
        self.assertTrue(all(item["confirmed_on"] <= frame.iloc[-1]["trade_date"] for item in pivots))
        self.assertTrue(all(item["index"] <= len(frame) - 3 for item in pivots))

    def test_confirmed_pivots_do_not_change_after_distant_future_is_appended(self) -> None:
        base = frame_from_close([10 + np.sin(index / 2) for index in range(40)])
        extended = frame_from_close([10 + np.sin(index / 2) for index in range(55)])
        old = detect_pivots(base, timeframe="daily", scales=(("short", 2, 0.10),))
        new = detect_pivots(extended, timeframe="daily", scales=(("short", 2, 0.10),))
        cutoff = str(base.iloc[-1]["trade_date"])
        old_keys = {(item["trade_date"], item["kind"], item["price"]) for item in old}
        new_keys = {(item["trade_date"], item["kind"], item["price"]) for item in new if item["confirmed_on"] <= cutoff}
        self.assertEqual(old_keys, new_keys)

    def test_gap_fill_lifecycle(self) -> None:
        frame = normalize_frame(
            [
                {"trade_date": "20260101", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "vol": 1},
                {"trade_date": "20260102", "open": 10.8, "high": 11.1, "low": 10.7, "close": 11, "vol": 1},
                {"trade_date": "20260105", "open": 10.9, "high": 11.0, "low": 10.5, "close": 10.7, "vol": 1},
                {"trade_date": "20260106", "open": 10.4, "high": 10.6, "low": 10.1, "close": 10.3, "vol": 1},
            ]
        )
        gaps = detect_gaps(frame, timeframe="daily", min_atr_fraction=0)
        upward = next(item for item in gaps if item["direction"] == "up")
        self.assertEqual(upward["status"], "filled")
        self.assertEqual(upward["fill_date"], "20260106")

    def test_zones_merge_nearby_multitimeframe_levels(self) -> None:
        frame = frame_from_close([10 + np.sin(index / 3) * 0.3 for index in range(80)])
        candidates = [
            {"price": 10.0, "timeframe": "daily", "source_type": "pivot", "source": "d", "kind": "low"},
            {"price": 10.05, "timeframe": "weekly", "source_type": "pivot", "source": "w", "kind": "low"},
            {"price": 10.08, "timeframe": "monthly", "source_type": "platform_edge", "source": "m", "kind": "low"},
        ]
        zones = build_zones(frame, candidates)
        self.assertEqual(len(zones), 1)
        self.assertEqual(set(zones[0]["timeframes"]), {"daily", "weekly", "monthly"})
        self.assertGreater(zones[0]["evidence_score"], 20)

    def test_flat_range_detects_platform(self) -> None:
        close = [10 + 0.20 * np.sin(index * 0.8) for index in range(80)]
        frame = frame_from_close(close)
        platforms = detect_platforms(frame, timeframe="daily")
        self.assertTrue(platforms)
        self.assertIn(platforms[0]["status"], {"active", "completed_inside"})

    def test_trendline_uses_confirmed_pivots(self) -> None:
        frame = frame_from_close([9 + index * 0.05 + 0.2 * np.sin(index) for index in range(80)])
        lows = [(10, 9.5), (30, 10.5), (50, 11.5), (70, 12.5)]
        pivots = [
            {
                "timeframe": "daily",
                "scale": "medium",
                "kind": "low",
                "index": index,
                "trade_date": frame.iloc[index]["trade_date"],
                "price": price,
                "prominence_atr": 1.0,
            }
            for index, price in lows
        ]
        lines = detect_trendlines(frame, pivots)
        self.assertTrue(any(item["kind"] == "support" and item["touch_count"] >= 3 for item in lines))

    def test_connected_swing_path_alternates_and_reaches_current_provisionally(self) -> None:
        frame = frame_from_close([10 + np.sin(index / 2) for index in range(80)])
        pivots = [
            {
                "timeframe": "daily",
                "scale": "long",
                "kind": kind,
                "index": index,
                "trade_date": frame.iloc[index]["trade_date"],
                "confirmed_on": frame.iloc[index + 3]["trade_date"],
                "price": price,
            }
            for index, kind, price in [
                (10, "low", 9.0),
                (20, "high", 12.0),
                (24, "high", 12.5),
                (38, "low", 9.5),
                (55, "high", 13.0),
            ]
        ]
        path = next(item for item in build_connected_swing_paths(frame, pivots) if item["structure_level"] == "major")
        self.assertEqual([point["kind"] for point in path["points"]], ["low", "high", "low", "high"])
        self.assertEqual(path["points"][1]["price"], 12.5)
        self.assertEqual(path["provisional_endpoint"]["trade_date"], frame.iloc[-1]["trade_date"])
        self.assertEqual(path["provisional_endpoint"]["status"], "provisional")

    def test_outer_envelope_contains_every_high_and_low(self) -> None:
        close = [10 + 0.025 * index + 0.7 * np.sin(index / 5) for index in range(220)]
        frame = frame_from_close(close)
        envelope = next(item for item in detect_multiscale_envelopes(frame) if item["structure_level"] == "minor")
        offset = len(frame) - envelope["lookback_bars"]
        indexes = np.arange(offset, len(frame))
        upper_x = np.asarray([point["index"] for point in envelope["upper_points"]], dtype=float)
        upper_y = np.log(np.asarray([point["price"] for point in envelope["upper_points"]], dtype=float))
        lower_x = np.asarray([point["index"] for point in envelope["lower_points"]], dtype=float)
        lower_y = np.log(np.asarray([point["price"] for point in envelope["lower_points"]], dtype=float))
        upper = np.exp(np.interp(indexes, upper_x, upper_y))
        lower = np.exp(np.interp(indexes, lower_x, lower_y))
        self.assertTrue(np.all(frame.iloc[offset:]["high"].to_numpy() <= upper + 1e-6))
        self.assertTrue(np.all(frame.iloc[offset:]["low"].to_numpy() >= lower - 1e-6))
        self.assertEqual(envelope["upper_points"][-1]["status"], "provisional")
        self.assertEqual(envelope["lower_points"][-1]["status"], "provisional")

    def test_envelope_trendlines_follow_envelope_segments(self) -> None:
        close = [10 + 0.03 * index + 0.8 * np.sin(index / 6) for index in range(420)]
        frame = frame_from_close(close)
        envelopes = detect_multiscale_envelopes(frame)
        lines = select_envelope_trendlines(envelopes)
        segment_ids = {segment["segment_id"] for envelope in envelopes for segment in envelope["segments"]}
        self.assertTrue(lines)
        self.assertTrue(all(item["segment_id"] in segment_ids for item in lines))
        self.assertTrue(all(item["timeframe"] == "daily" for item in lines))
        self.assertTrue(all(item["touch_count"] >= 2 for item in lines))

    def test_recent_channel_and_acceleration_are_separate_structures(self) -> None:
        close = np.asarray([10 + index * 0.05 + 0.20 * np.sin(index * 0.8) for index in range(45)])
        close[-2] = close[-3] * 1.10
        close[-1] = close[-2] * 1.10
        frame = frame_from_close(close.tolist())
        channels = detect_trend_channels(frame, timeframe="daily")
        minor = next(item for item in channels if item["structure_level"] == "minor")
        self.assertEqual(minor["direction"], "rising")
        self.assertEqual(minor["status"], "broken_up")
        self.assertGreaterEqual(minor["breakout_bars"], 2)
        legs = detect_acceleration_legs(frame, channels, timeframe="daily")
        self.assertTrue(legs)
        self.assertEqual(legs[0]["status"], "provisional_acceleration")
        self.assertGreater(legs[0]["slope_multiple"], 2.5)

    def test_double_bottom_waiting_and_confirmed(self) -> None:
        frame = frame_from_close([10] * 60)
        sequence = [
            (10, "low", 9.0),
            (20, "high", 10.5),
            (30, "low", 9.1),
        ]
        pivots = [
            {
                "timeframe": "daily",
                "scale": "medium",
                "kind": kind,
                "index": index,
                "trade_date": frame.iloc[index]["trade_date"],
                "price": price,
                "prominence_atr": 1.0,
            }
            for index, kind, price in sequence
        ]
        frame.loc[40:, "close"] = 10.8
        patterns = detect_reversal_patterns(frame, pivots)
        item = next(pattern for pattern in patterns if pattern["pattern_type"] == "double_bottom")
        self.assertEqual(item["status"], "confirmed")

    def test_bull_flag_candidate(self) -> None:
        pole = list(np.linspace(10, 13, 10))
        flag = list(np.linspace(12.9, 12.3, 15))
        frame = frame_from_close([9.8] * 10 + pole + flag)
        patterns = detect_flag(frame)
        self.assertTrue(patterns)
        self.assertEqual(patterns[0]["pattern_type"], "bull_flag")

    def test_engine_rejects_future_bar(self) -> None:
        daily = frame_from_close([10 + np.sin(index / 4) for index in range(100)])
        packet = {
            "future_isolated": True,
            "as_of": daily.iloc[-2]["trade_date"],
            "ts_code": "000001.SZ",
            "daily": daily.to_dict("records"),
            "weekly_from_truncated_daily": daily.iloc[::5].to_dict("records"),
            "monthly_from_truncated_daily": daily.iloc[::20].to_dict("records"),
            "latest": {},
        }
        with self.assertRaisesRegex(ValueError, "aligned"):
            analyze_packet(json_ready(packet))


if __name__ == "__main__":
    unittest.main()
