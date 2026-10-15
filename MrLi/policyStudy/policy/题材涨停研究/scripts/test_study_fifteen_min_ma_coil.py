"""Timing and future-isolation regressions for the new exploratory rule."""
import unittest
import argparse
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from study_fifteen_min_ma_coil import Rules, features, slots, coil_features, rules_from_dict, find_signals, save_rules
from replay_fifteen_min_ma_coil import execute, minute_slots, resolve_run_rules, argument_parser, digest


class FrozenRulesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        signals = self.out / "signals_before_outcomes.csv"
        signals.write_text("ts_code,signal_time\n600000.SH,2026-09-01 10:00:00\n")
        rules = self.out / "rules_1.5.json"
        rules.write_text(json.dumps({"version": "15m_ma_coil_v2_exploratory", "coil_pct": 1.5}))
        self.manifest = dict(first="20260901", last="20260915", warmup_start="20260701",
                             signal_file_sha256=digest(signals), rules_hashes={"1.5": digest(rules)})
        (self.out / "FROZEN_MANIFEST.json").write_text(json.dumps(self.manifest))
        self.args = argparse.Namespace(score_only=True, first="20260901", last="20260915",
                                       warmup_start="20260701", rules_file=None)

    def test_score_only_loads_original_rule_without_rewriting_it(self):
        before = digest(self.out / "FROZEN_MANIFEST.json")
        rules, manifest = resolve_run_rules(self.args, self.out)
        self.assertEqual(rules.coil_mode, "legacy_or")
        self.assertEqual(rules.coil_pct, 1.5)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(before, digest(self.out / "FROZEN_MANIFEST.json"))

    def test_score_only_cannot_apply_new_rules_to_old_signals(self):
        new = self.out / "dual.json"
        save_rules(new, Rules())
        self.args.rules_file = str(new)
        with self.assertRaisesRegex(ValueError, "cannot change"):
            resolve_run_rules(self.args, self.out)

    def test_changed_frozen_rules_or_signals_are_rejected(self):
        (self.out / "rules_1.5.json").write_text(json.dumps({"version": "v2", "coil_pct": 1.4}))
        with self.assertRaisesRegex(ValueError, "rule file hash"):
            resolve_run_rules(self.args, self.out)
        (self.out / "signals_before_outcomes.csv").write_text("changed")
        with self.assertRaisesRegex(ValueError, "signal file hash"):
            resolve_run_rules(self.args, self.out)

    def test_new_scan_cannot_overwrite_frozen_results(self):
        self.args.score_only = False
        with self.assertRaisesRegex(ValueError, "Frozen signals already exist"):
            resolve_run_rules(self.args, self.out)

    def test_parser_default_is_dual_and_help_renders(self):
        parser = argument_parser()
        self.assertIn("1.0%/2.0%", parser.format_help())
        args = parser.parse_args([])
        self.assertTrue(args.output.endswith("v3_dual"))
        rules, manifest = resolve_run_rules(args, self.out / "fresh")
        self.assertEqual(rules, Rules())
        self.assertIsNone(manifest)


class CoilGroupTests(unittest.TestCase):
    def groups(self, **changes):
        row = dict(p_close=100., ma5=100., ma10=100., ma20=100., ma30=100., ma60=100.)
        row.update(changes)
        return coil_features(pd.DataFrame([row]), Rules()).iloc[0]

    def test_extended_group_keeps_ma30(self):
        row = self.groups(ma30=100.9, ma60=98.8)
        self.assertLess(row.coil30_pct, 1.)
        self.assertLess(row.coil60_pct, 2.)  # Old branch alone misses MA30.
        self.assertGreater(row.coil_with_ma60_pct, 2.)
        self.assertFalse(row.cluster_only)

    def test_extended_group_has_its_own_looser_threshold(self):
        row = self.groups(ma30=100.8, ma60=99.1)
        self.assertTrue(row.coil_short_ok)
        self.assertGreater(row.coil_with_ma60_pct, 1.)
        self.assertLess(row.coil_with_ma60_pct, 2.)
        self.assertTrue(row.cluster_only)

    def test_short_group_cannot_be_rescued_by_ma60_group(self):
        row = self.groups(ma30=101.2)
        self.assertFalse(row.coil_short_ok)
        self.assertTrue(row.coil_with_ma60_ok)
        self.assertFalse(row.cluster_only)

    def test_inclusive_boundaries_with_binary_roundoff(self):
        self.assertTrue(self.groups(ma30=101., ma60=102.).cluster_only)
        self.assertFalse(self.groups(ma30=101.00001, ma60=102.).cluster_only)
        self.assertFalse(self.groups(ma30=101., ma60=102.00001).cluster_only)

    def test_both_groups_must_pass_on_same_bar(self):
        raw = pd.DataFrame([dict(p_close=100., ma5=100., ma10=100., ma20=100., ma30=100., ma60=103.),
                            dict(p_close=100., ma5=100., ma10=100., ma20=100., ma30=101.2, ma60=100.)])
        f = coil_features(raw, Rules())
        self.assertEqual(f.coil_short_ok.tolist(), [True, False])
        self.assertEqual(f.coil_with_ma60_ok.tolist(), [False, True])
        self.assertFalse(f.cluster_only.any())

    def test_close_is_included_and_ma60_must_exist(self):
        self.assertFalse(self.groups(p_close=101.2).cluster_only)
        self.assertFalse(self.groups(ma60=np.nan).cluster_only)
        self.assertTrue(pd.isna(self.groups(ma60=np.nan).coil_with_ma60_pct))

    def test_common_adjustment_scale_does_not_change_widths(self):
        raw = pd.DataFrame([dict(p_close=100., ma5=100.2, ma10=100.4, ma20=100.1, ma30=100.8, ma60=99.1)])
        a, b = coil_features(raw, Rules()), coil_features(raw * 3.25, Rules())
        np.testing.assert_allclose(a[["coil30_pct", "coil_with_ma60_pct"]],
                                   b[["coil30_pct", "coil_with_ma60_pct"]], rtol=1e-12)
        self.assertEqual(a.cluster_only.tolist(), b.cluster_only.tolist())

    def test_old_frozen_rules_keep_old_or_interpretation(self):
        rules = rules_from_dict({"version": "15m_ma_coil_v2_exploratory", "coil_pct": 1.5})
        self.assertEqual(rules.coil_mode, "legacy_or")
        raw = pd.DataFrame([dict(p_close=100., ma5=100., ma10=100., ma20=100., ma30=103., ma60=100.)])
        self.assertTrue(coil_features(raw, rules).iloc[0].cluster_only)
        self.assertFalse(coil_features(raw, Rules()).iloc[0].cluster_only)

    def test_thresholds_must_be_positive_finite(self):
        for value in (-1, 0, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Rules(coil_with_ma60_pct=value)

    def test_signal_records_both_widths_and_short_group_breakout(self):
        f = pd.DataFrame(dict(
            datetime=pd.to_datetime(["2026-09-01 10:00", "2026-09-01 10:15"]),
            body_pct=[0., 1.], body_fraction=[0., .8], close_location=[.5, .9],
            volume_multiple8=[1., 2.], pre_restart_range_pct=[5., 5.],
            strict_restart_alignment=[False, True], coil_ready=[True, False], valid_bar=[True, True],
            p_close=[100., 102.], close=[100., 102.], adj_factor=[1., 1.],
            coil30_pct=[.8, 2.], coil_with_ma60_pct=[1.7, 3.],
            coil30_high=[100.8, 102.], coil30_low=[100., 100.],
            origin=["persistent_ma", "persistent_ma"], rise120=[60, 61], rise250=[40, 41], rise60=[20, 21],
            prior_alignment_share=[1., 1.], pullback_pct=[1., .5], pullback_bars=[5, 6],
            volume_multiple_same_slot5=[1., 2.]))
        row = find_signals(f, "600000.SH", "样本", Rules(), "20260901", "20260901").iloc[0]
        self.assertEqual(row.coil_branch, "dual")
        self.assertEqual(row.coil_short_pct, .8)
        self.assertEqual(row.coil_with_ma60_pct, 1.7)
        self.assertEqual(row.coil_high_raw, 100.8)
        self.assertEqual(row.rule_coil_pct, 1.)
        self.assertEqual(row.rule_coil_with_ma60_pct, 2.)


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.calendar = pd.bdate_range("2026-07-01", periods=45).strftime("%Y%m%d").tolist()
        t = slots(self.calendar)
        close = 10 + np.arange(len(t)) * .002 + np.sin(np.arange(len(t)) / 9) * .03
        self.raw = pd.DataFrame({"datetime": t, "open": close - .003, "high": close + .01,
                                 "low": close - .01, "close": close, "vol": 10000., "adj_factor": 1.})

    def test_future_prices_factors_volumes_do_not_change_past(self):
        a = features(self.raw, self.calendar, Rules())
        b = self.raw.copy(); cut = 560
        b.loc[cut:, ["open", "high", "low", "close"]] *= 4
        b.loc[cut:, "adj_factor"] *= 3; b.loc[cut:, "vol"] *= 1000
        altered = features(b, self.calendar, Rules())
        pd.testing.assert_frame_equal(a.iloc[:cut], altered.iloc[:cut])
        truncated = features(self.raw.iloc[:cut], self.calendar, Rules())
        pd.testing.assert_frame_equal(a.iloc[:cut], truncated.iloc[:cut])

    def test_missing_bar_does_not_compress_clock_or_preserve_ma_history(self):
        raw = self.raw.drop(index=400)
        f = features(raw, self.calendar, Rules())
        self.assertEqual(f.iloc[400].datetime, self.raw.iloc[400].datetime)
        self.assertFalse(f.iloc[400].valid_bar)
        self.assertTrue(pd.isna(f.iloc[500].ma120))
        self.assertEqual(f.iloc[400].rise120, 0)

    def test_future_duplicate_does_not_erase_earlier_features(self):
        original = features(self.raw, self.calendar, Rules())
        raw = pd.concat([self.raw, self.raw.iloc[[600]]], ignore_index=True)
        changed = features(raw, self.calendar, Rules())
        pd.testing.assert_frame_equal(original.iloc[:600], changed.iloc[:600])
        self.assertFalse(changed.iloc[600].valid_bar)

    def test_volume_week_excludes_current_bar_and_ties_are_conservative(self):
        self.raw.loc[400, "vol"] = 20000.
        f = features(self.raw, self.calendar, Rules())
        self.assertEqual(f.iloc[400].volume_rank_previous_week, 1)
        self.assertEqual(f.iloc[399].volume_rank_previous_week, 81)

    def test_compression_uses_prior_wicks_and_excludes_current_launch(self):
        self.raw.loc[400, ["open", "low"]] = 11.
        self.raw.loc[400, ["close", "high"]] = 16.
        f = features(self.raw, self.calendar, Rules())
        self.assertLess(f.iloc[400].pre_restart_range_pct, 10.)
        self.assertGreater(f.iloc[401].pre_restart_range_pct, 10.)

    def test_strict_restart_requires_every_ma_rising_and_strict_order(self):
        price = 10 + np.arange(len(self.raw)) * .005
        for c, offset in (("open", -.005), ("high", .01), ("low", -.01), ("close", 0)):
            self.raw[c] = price + offset
        f = features(self.raw, self.calendar, Rules())
        self.assertTrue(f.iloc[500].strict_restart_alignment)
        self.raw.loc[499:510, ["open", "close", "high", "low"]] = [11.01, 11., 11.02, 10.99]
        f = features(self.raw, self.calendar, Rules())
        self.assertFalse(f.iloc[505].strict_restart_alignment)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.calendar = ["20260901", "20260902", "20260903", "20260904", "20260907", "20260908"]
        self.code = "600000.SH"
        self.one = pd.DataFrame({"datetime": minute_slots(self.calendar), "open": 100., "close": 100.,
                                 "high": 101., "low": 99., "vol": 10000., "adj_factor": 1.})
        self.signal = {"ts_code": self.code, "signal_time": "2026-09-01 09:45:00", "signal_factor": 1.}
        self.limits = {(d, self.code): (110., 90.) for d in self.calendar}
        self.preclose = {(d, self.code): 100. for d in self.calendar}

    def test_entry_waits_one_minute_and_tplus1(self):
        r = execute(self.signal, self.one, self.calendar, self.limits, self.preclose)
        self.assertEqual(r["entry_time"], "2026-09-01 09:46:00")
        self.assertEqual(r["d1_exit_time"], "2026-09-02 14:45:00")
        self.assertEqual(r["d5_exit_time"], "2026-09-08 14:45:00")

    def test_does_not_skip_missing_scheduled_entry(self):
        one = self.one[self.one.datetime.ne(pd.Timestamp("2026-09-01 09:47"))]
        r = execute(self.signal, one, self.calendar, self.limits, self.preclose)
        self.assertEqual(r["entry_status"], "entry_minute_missing")
        self.assertFalse(r["buyable_proxy"])

    def test_exit_minute_low_is_not_a_holding_drawdown(self):
        self.one.loc[self.one.datetime.eq(pd.Timestamp("2026-09-02 14:46")), "low"] = 20.
        r = execute(self.signal, self.one, self.calendar, self.limits, self.preclose)
        self.assertAlmostEqual(r["d1_mae"], -.01)

    def test_locked_down_exit_is_retained_and_deferred(self):
        locked = self.one.datetime.between("2026-09-02 14:46", "2026-09-02 15:00")
        self.one.loc[locked, ["open", "high", "low", "close"]] = 90.
        r = execute(self.signal, self.one, self.calendar, self.limits, self.preclose)
        self.assertEqual(r["d1_exit_time"], "2026-09-03 09:30:00")
        self.assertEqual(r["d1_blocked_minutes"], 15)

    def test_future_factor_change_does_not_delete_entry_or_earlier_exit(self):
        self.one.loc[self.one.datetime.ge(pd.Timestamp("2026-09-03 09:31")), "adj_factor"] = 2.
        r = execute(self.signal, self.one, self.calendar, self.limits, self.preclose)
        self.assertTrue(r["buyable_proxy"])
        self.assertEqual(r["d1_status"], "closed_proxy")
        self.assertEqual(r["d3_status"], "holding_factor_change_or_mismatch")

    def test_limit_fill_is_an_explicit_separate_hypothesis(self):
        self.signal["signal_close"] = 110.
        self.one.loc[self.one.datetime.eq(pd.Timestamp("2026-09-01 09:47")), "open"] = 110.
        assumed = execute(self.signal, self.one, self.calendar, self.limits, self.preclose,
                          allow_limit_fill=True)
        ordinary = execute(self.signal, self.one, self.calendar, self.limits, self.preclose)
        self.assertTrue(assumed["buyable_proxy"])
        self.assertTrue(assumed["limit_fill_assumption"])
        self.assertEqual(assumed["entry_time"], "2026-09-01 09:45:00")
        self.assertEqual(assumed["entry_open"], 110.)
        self.assertEqual(ordinary["entry_status"], "entry_at_up_limit")

    def test_future_duplicate_does_not_erase_earlier_entry_and_exit(self):
        self.one = pd.concat([self.one, self.one[self.one.datetime.eq(pd.Timestamp("2026-09-03 09:31"))]])
        r = execute(self.signal, self.one, self.calendar, self.limits, self.preclose)
        self.assertTrue(r["buyable_proxy"])
        self.assertEqual(r["d1_status"], "closed_proxy")
        self.assertEqual(r["d3_status"], "holding_duplicate_minute")

    def test_st_source_name_never_enters_even_with_limit_fill(self):
        self.signal.update({"name": "ST样本", "signal_close": 110.})
        r = execute(self.signal, self.one, self.calendar, self.limits, self.preclose, allow_limit_fill=True)
        self.assertEqual(r["entry_status"], "excluded_st_source_name")
        self.assertFalse(r["buyable_proxy"])

    def test_new_listing_uses_decision_date_age(self):
        self.signal["listing_age_days"] = 314
        r = execute(self.signal, self.one, self.calendar, self.limits, self.preclose)
        self.assertEqual(r["entry_status"], "excluded_recent_listing_under_365days")
        self.assertFalse(r["buyable_proxy"])


if __name__ == "__main__":
    unittest.main()
