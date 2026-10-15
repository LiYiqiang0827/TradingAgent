from __future__ import annotations

import unittest

import pandas as pd

from policyStudy.policy.market_review.build_review_packet import (
    parse_board,
    reconcile_limit_states,
    reclassify_boards,
    valid_time,
    validate_packet,
)
from policyStudy.policy.market_review.export_research_watchlist import export_rows


class MarketReviewRulesTest(unittest.TestCase):
    def test_board_statuses_are_separate(self) -> None:
        self.assertEqual(parse_board("首板"), ("continuous", 1, 1))
        self.assertEqual(parse_board("3连板"), ("continuous", 3, 3))
        self.assertEqual(parse_board("5天3板"), ("n_day_m_board", 5, 3))
        self.assertEqual(parse_board("", 2), ("continuous_fallback", 2, 2))

    def test_two_limit_rates_are_independently_validated(self) -> None:
        packet = {
            "metadata": {},
            "data_quality": {"required_missing": [], "late_break_minute_validated": False},
            "market": {"closed_limit_up_count": 8, "broken_board_count": 2, "touch_seal_rate": 0.8},
            "ladder": {},
            "themes": [{
                "theme": "A", "eligible_member_count": 20,
                "closed_limit_up_count": 4, "broken_board_count": 1,
                "theme_close_limit_rate": 0.2, "touch_seal_rate": 0.8,
            }],
            "past_mainline_tracking": [],
            "candidate_cards": [],
        }
        self.assertEqual(validate_packet(packet)["status"], "PASS")

    def test_kpl_epoch_time_is_converted_to_shanghai_clock(self) -> None:
        self.assertEqual(valid_time(1789960626), "11:17:06")
        self.assertEqual(valid_time("093015"), "09:30:15")

    def test_close_price_repairs_broken_board_label(self) -> None:
        events = pd.DataFrame([{
            "ts_code": "000002.SZ", "name": "万科A", "limit_state": "Z",
            "limit_state_raw": "Z", "board_kind": "continuous_fallback",
            "board_days": 2, "board_count": 2,
        }])
        daily = pd.DataFrame([{"ts_code": "000002.SZ", "close": 3.65, "high": 3.65}])
        limits = pd.DataFrame([{"ts_code": "000002.SZ", "up_limit": 3.65, "down_limit": 2.99}])
        fixed = reconcile_limit_states(events, daily, limits, {"000002.SZ": "万科A"})
        self.assertEqual(fixed.iloc[0]["limit_state"], "U")
        self.assertTrue(bool(fixed.iloc[0]["limit_state_conflict"]))

    def test_nonconsecutive_fallback_is_derived_as_n_day_m_board(self) -> None:
        events = pd.DataFrame([{
            "ts_code": "600721.SH", "limit_state": "U",
            "board_kind": "continuous_fallback", "board_days": 2, "board_count": 2,
        }])
        history = pd.DataFrame([
            {"ts_code": "600721.SH", "trade_date": "20260917", "limit": "U"},
            {"ts_code": "600721.SH", "trade_date": "20260921", "limit": "U"},
        ])
        fixed = reclassify_boards(
            events, history, ["20260917", "20260918", "20260921"], "20260921"
        )
        self.assertEqual(fixed.iloc[0]["board_kind"], "n_day_m_board")
        self.assertEqual(int(fixed.iloc[0]["board_days"]), 3)
        self.assertEqual(int(fixed.iloc[0]["board_count"]), 2)

    def test_research_watchlist_keeps_stage_semantics(self) -> None:
        packet = {
            "metadata": {"trade_date": "20260921"},
            "candidate_cards": [
                {
                    "ts_code": "600721.SH", "name": "百花医药",
                    "pattern_stage": "restart_completed", "pattern_shape": "再次封板",
                    "board_kind": "n_day_m_board", "board_days": 3, "board_count": 2,
                    "primary_theme": "医药", "first_limit_time": "10:02:07",
                },
                {
                    "ts_code": "000001.SZ", "name": "示例观察",
                    "pattern_stage": "pullback_in_progress", "pattern_shape": "tight_platform",
                    "board_kind": "pullback_watch", "board_days": 1, "board_count": 1,
                    "primary_theme": "示例", "launch_date": "20260918",
                },
            ],
        }
        default = export_rows(packet)
        self.assertEqual(default["ts_code"].tolist(), ["600721.SH"])
        self.assertEqual(default.iloc[0]["status"], "3天2板")
        with_pullback = export_rows(packet, include_pullbacks=True)
        pullback = with_pullback[with_pullback["ts_code"] == "000001.SZ"].iloc[0]
        self.assertEqual(pullback["trade_date"], "20260918")
        self.assertEqual(pullback["review_date"], "20260921")


if __name__ == "__main__":
    unittest.main()
