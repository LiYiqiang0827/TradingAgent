import numpy as np
import pandas as pd
import unittest

from research_news_context_helpers import aggregate_news

CERT = "OBSERVED_COLLECTION_UNCERTIFIED"
DAYS = [f"2024-01-0{i}" for i in range(1, 8)]  # 7 sessions


def cov(days, states=None):
    states = states or {d: CERT for d in days}
    return pd.DataFrame({"trade_date": days,
                         "observed_articles": [1] * len(days),
                         "coverage_state": [states.get(d, "UNKNOWN") for d in days]})


def mk(rows):
    return pd.DataFrame(rows, columns=["article_id", "theme_code", "available_date",
                                       "source", "text_sha256", "repeat_similarity",
                                       "novel_fact_count"])


def cell(df, theme, day):
    return df[(df.theme_code == theme) & (df.available_date == day)].iloc[0]


class TestAggregateNews(unittest.TestCase):
    def base(self):
        arts = mk([
            ["a1", "T1", DAYS[0], "s1", "h1", 0.9, 2],
            ["a2", "T1", DAYS[1], "s2", "h2", 0.1, 3],
            ["a3", "T1", DAYS[2], "s3", "h3", 0.9, 1],
            ["a4", "T2", DAYS[0], "s9", "h9", 0.5, 5],
        ])
        return arts, ["T1", "T2"], cov(DAYS)

    def test_duplicate_key_rejected(self):
        arts = mk([["a1", "T1", DAYS[0], "s1", "h1", 0.1, 1],
                   ["a1", "T1", DAYS[0], "s2", "h2", 0.1, 1]])
        with self.assertRaises(ValueError):
            aggregate_news(arts, ["T1"], DAYS, cov(DAYS))

    def test_theme_separation(self):
        arts, themes, c = self.base()
        out = aggregate_news(arts, themes, DAYS, c)
        self.assertEqual(cell(out, "T1", DAYS[0]).observed_news_count, 1)
        self.assertEqual(cell(out, "T2", DAYS[1]).observed_news_count, 0)
        self.assertEqual(cell(out, "T2", DAYS[0]).novel_fact_count, 5)

    def test_exact_rolling_3sessions(self):
        arts, themes, c = self.base()
        out = aggregate_news(arts, themes, DAYS, c)
        r = cell(out, "T1", DAYS[2])
        self.assertEqual(r.observed_unique_3, 3.0)
        self.assertEqual(r.history_sessions_3, 3)
        self.assertEqual(r.covered_sessions_3, 3)
        self.assertEqual(r.observed_sources_3, 3)
        self.assertEqual(r.density_3, 1.0)
        r0 = cell(out, "T1", DAYS[0])
        self.assertEqual(r0.history_sessions_3, 1)
        self.assertTrue(np.isnan(r0.density_3))  # window shorter than 3

    def test_unknown_day_density_nan_and_consecutive_reset(self):
        states = {d: CERT for d in DAYS}
        states[DAYS[3]] = "UNKNOWN"
        arts, themes, _ = self.base()
        out = aggregate_news(arts, themes, DAYS, cov(DAYS, states))
        self.assertTrue(np.isnan(cell(out, "T1", DAYS[4]).density_3))
        r = cell(out, "T1", DAYS[3])
        self.assertEqual(r.coverage_state, "UNKNOWN")
        self.assertEqual(r.consecutive_observed_news_sessions, 0)
        self.assertEqual(cell(out, "T1", DAYS[2]).consecutive_observed_news_sessions, 3)

    def test_future_append_does_not_change_prefix(self):
        arts, themes, c = self.base()
        out1 = aggregate_news(arts, themes, DAYS, c)
        days2 = DAYS + ["2024-01-08"]
        extra = mk([["a0", "T1", "2024-01-08", "sx", "h1", 0.1, 7]])
        arts2 = pd.concat([arts, extra])
        out2 = aggregate_news(arts2, themes, days2, cov(days2))
        p1 = out1[out1.theme_code == "T1"].reset_index(drop=True)
        p2 = out2[(out2.theme_code == "T1") & (out2.available_date.isin(DAYS))].reset_index(drop=True)
        pd.testing.assert_frame_equal(p1, p2)

    def test_duplicate_text_facts_counted_once_and_source_union(self):
        arts = mk([
            ["b2", "T1", DAYS[0], "s2", "dup", 0.9, 4],
            ["b1", "T1", DAYS[0], "s1", "dup", 0.1, 4],
            ["b3", "T1", DAYS[2], "s1", "h3", 0.9, 1],
        ])
        out = aggregate_news(arts, ["T1"], DAYS, cov(DAYS))
        self.assertEqual(cell(out, "T1", DAYS[2]).observed_novel_fact_3, 5.0)
        self.assertEqual(cell(out, "T1", DAYS[2]).observed_sources_3, 2)
        self.assertEqual(cell(out, "T1", DAYS[0]).unique_news_count, 1)
        self.assertEqual(cell(out, "T1", DAYS[0]).repeat_fraction, 0.5)

    def test_empty_articles(self):
        out = aggregate_news(mk([]), ["T1"], DAYS, cov(DAYS))
        self.assertEqual(len(out), len(DAYS))
        r = cell(out, "T1", DAYS[5])
        self.assertEqual(r.observed_news_count, 0)
        self.assertTrue(np.isnan(r.repeat_fraction))
        self.assertEqual(r.observed_unique_3, 0.0)
        self.assertEqual(r.density_3, 0.0)

    def test_missing_coverage_unknown(self):
        c = cov(DAYS[:5])  # missing last two days
        arts, themes, _ = self.base()
        out = aggregate_news(arts, themes, DAYS, c)
        self.assertEqual(cell(out, "T1", DAYS[6]).coverage_state, "UNKNOWN")
        self.assertTrue(np.isnan(cell(out, "T1", DAYS[6]).density_3))


if __name__ == "__main__":
    unittest.main()
