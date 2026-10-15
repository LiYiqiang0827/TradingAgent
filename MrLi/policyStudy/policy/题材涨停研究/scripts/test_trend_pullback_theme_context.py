"""Decision-time and missing-source tests for the new theme context module."""
import unittest

import pandas as pd

from trend_pullback_theme_context import build_context, heat_score, prepare_news


def calendar(n=12):
    return pd.bdate_range("2026-01-05", periods=n).strftime("%Y%m%d").tolist()


def kpl(days):
    return pd.DataFrame([
        {"trade_date": d, "ts_code": "300480.SZ", "name": "光力科技",
         "lu_desc": "半导体", "status": "首板", "tag": "涨停"}
        for d in days])


class ThemeContextTest(unittest.TestCase):
    def test_future_primary_labels_and_future_news_cannot_leak(self):
        dates = calendar()
        early = kpl(dates[:6])
        future = kpl(dates[6:])
        future["lu_desc"] = "机器人"
        future_news = pd.DataFrame([{"datetime": "2026-01-15 08:00:00",
                                    "title": "光力科技发布新产品", "src": "原文"}])
        a = build_context(early, dates, ["300480.SZ"])
        b = build_context(pd.concat([early, future]), dates, ["300480.SZ"], future_news)
        pd.testing.assert_frame_equal(a[a.trade_date <= dates[6]].reset_index(drop=True),
                                      b[b.trade_date <= dates[6]].reset_index(drop=True))
        self.assertEqual(b[b.trade_date == dates[6]].iloc[0].theme_name, "半导体")
        self.assertEqual(b[b.trade_date == dates[7]].iloc[0].theme_name, "机器人")

    def test_full_calendar_prefix_replay_is_identical(self):
        dates = calendar()
        raw = kpl(dates)
        full = build_context(raw, dates, ["300480.SZ"])
        short = build_context(raw, dates[:8], ["300480.SZ"])
        pd.testing.assert_frame_equal(full.iloc[:8].reset_index(drop=True), short)

    def test_no_same_day_mapping(self):
        dates = calendar(3)
        out = build_context(kpl(dates[:1]), dates, ["300480.SZ"])
        self.assertFalse(out.iloc[0].theme_known)
        self.assertTrue(out.iloc[1].theme_known)
        self.assertEqual(out.iloc[1].context_as_of, dates[0])

    def test_missing_source_is_not_zero(self):
        dates = calendar(8)
        raw = kpl([dates[0], dates[2], dates[3], dates[4], dates[5]])
        out = build_context(raw, dates, ["300480.SZ"])
        self.assertTrue(out.iloc[2].theme_known)
        self.assertFalse(out.iloc[2].kpl_day_known)
        self.assertTrue(pd.isna(out.iloc[2].theme_width))
        self.assertTrue(pd.isna(out.iloc[6].theme_width5))

    def test_observed_day_with_no_matching_theme_is_zero(self):
        dates = calendar(7)
        raw = kpl(dates[:6])
        raw.loc[raw.trade_date == dates[5], ["ts_code", "name", "lu_desc"]] = [
            "600001.SH", "测试公司", "机器人"]
        out = build_context(raw, dates, ["300480.SZ"])
        row = out.iloc[6]
        self.assertEqual(row.theme_name, "半导体")
        self.assertEqual(row.theme_width, 0)
        self.assertEqual(row.theme_height, 0)
        self.assertEqual(row.theme_heat, 0)
        self.assertEqual(row.theme_width5, 1)

    def test_exclusions_and_repeated_revisions(self):
        dates = calendar(7)
        raw = kpl(dates[:6])
        duplicate = raw.iloc[[5]].copy()
        duplicate["status"] = "3连板"
        exclude = raw.iloc[[5]].copy()
        exclude["ts_code"] = "000001.SZ"
        exclude["name"] = "*ST测试"
        exclude["status"] = "10连板"
        out = build_context(pd.concat([raw, duplicate, exclude]), dates, ["300480.SZ"])
        self.assertEqual(out.iloc[6].theme_width, 1)
        self.assertEqual(out.iloc[6].theme_height, 3)
        self.assertLessEqual(out.iloc[6].theme_heat, 100)

    def test_conflicting_same_day_primary_does_not_pick_arbitrary_label(self):
        dates = calendar(3)
        raw = kpl(dates[:1])
        other = raw.copy()
        other["lu_desc"] = "机器人"
        out = build_context(pd.concat([raw, other]), dates, ["300480.SZ"])
        self.assertTrue(out.iloc[1].theme_conflict)
        self.assertFalse(out.iloc[1].theme_known)
        self.assertEqual(out.iloc[1].theme_name, "")

    def test_expiry_at_sixty_market_sessions(self):
        dates = calendar(63)
        out = build_context(kpl(dates[:1]), dates, ["300480.SZ"])
        self.assertTrue(out.iloc[60].theme_known)
        self.assertFalse(out.iloc[61].theme_known)
        self.assertEqual(out.iloc[61].theme_name, "")

    def test_raw_news_cutoff_and_result_filter(self):
        dates = calendar(3)
        news = pd.DataFrame([
            {"datetime": "2026-01-06 09:29:59", "title": "光力科技中标设备采购项目"},
            {"datetime": "2026-01-06 09:30:00", "title": "光力科技发布新产品"},
            {"datetime": "2026-01-06 09:29:58", "title": "光力科技涨停，半导体板块拉升"},
            {"datetime": "2026-01-06", "title": "光力科技获批项目"},
        ])
        out = build_context(kpl(dates), dates, ["300480.SZ"], news)
        row = out.iloc[1]
        self.assertTrue(row.logic_evidence_known)
        self.assertEqual(row.logic_title, "光力科技中标设备采购项目")
        self.assertEqual(row.logic_source_time, "2026-01-06 09:29:59")
        self.assertEqual(row.logic_matches, 1)
        self.assertEqual(row.logic_match_kind, "stock_title")

    def test_empty_news_and_unknown_attribution_stay_unknown(self):
        dates = calendar(3)
        out = build_context(pd.DataFrame(), dates, ["300480.SZ"])
        self.assertEqual(len(out), 3)
        self.assertFalse(out.theme_known.any())
        self.assertFalse(out.logic_evidence_known.any())
        self.assertTrue(out.theme_heat.isna().all())

    def test_news_deduplicates_and_converts_timezone(self):
        news = pd.DataFrame([
            {"datetime": "2026-01-06T01:00:00Z", "title": "半导体政策发布", "src": "a"},
            {"datetime": "2026-01-06 09:00:00", "title": "半导体政策发布", "src": "b"},
        ])
        out = prepare_news(news)
        self.assertEqual(len(out), 1)
        self.assertEqual(out.source_time.iloc[0], pd.Timestamp("2026-01-06 09:00:00"))

    def test_heat_empty_theme_and_caps(self):
        self.assertEqual(heat_score([], 5), 0)
        self.assertEqual(heat_score([20] * 100, 5), 100)


if __name__ == "__main__":
    unittest.main()
