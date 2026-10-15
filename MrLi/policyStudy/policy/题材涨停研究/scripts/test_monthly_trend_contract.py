"""Synthetic contracts for causal monthly pools; never read replay outcomes."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import replay_monthly_trend as monthly
from replay_trend_pullback import time_masks
from trend_pullback_exit_engine import replay
from trend_pullback_features import EntryRules
from test_trend_pullback_exit_engine import frame, TIMES


def signal_candidates(frames, rules, start, end, mode):
    """Replace technical detection only, retaining real monthly ownership logic."""
    result = []
    for code, data in frames.items():
        first = pd.Timestamp(start)
        last = pd.Timestamp(end) + pd.Timedelta(days=1)
        for index, row in data.iterrows():
            if first <= row.datetime < last:
                result.append(dict(id=f"{code}_{index}", ts_code=code,
                                   index=index, stop=9.8, entry_score=1.,
                                   signal_time=row.datetime.isoformat()))
    return result


def toy_frames():
    dates = ["2026-01-29 14:45", "2026-01-30 15:00",
             "2026-02-02 09:45", "2026-02-03 09:45"]
    return {code: pd.DataFrame({"datetime": pd.to_datetime(dates)})
            for code in ("A", "B", "FUTURE")}


class MonthlyOwnershipTests(unittest.TestCase):
    def pools(self):
        return pd.DataFrame({"month": ["202601", "202602"],
                             "ts_code": ["A", "B"]})

    def test_signal_origins_only_belong_to_their_actual_month(self):
        frames = toy_frames()
        with patch.object(monthly, "candidates", side_effect=signal_candidates):
            selected = monthly.monthly_candidates(
                frames, EntryRules(), "20260101", "20260228",
                "attribution", self.pools())
        owners = set(zip(self.pools().ts_code, self.pools().month))
        self.assertTrue(selected)
        for item in selected:
            origin = pd.Timestamp(item["signal_time"]).strftime("%Y%m")
            self.assertIn((item["ts_code"], origin), owners)

    def test_future_added_member_cannot_change_earlier_candidates(self):
        frames = toy_frames()
        future = pd.concat([self.pools(), pd.DataFrame(
            {"month": ["202603"], "ts_code": ["FUTURE"]})],
            ignore_index=True)
        with patch.object(monthly, "candidates", side_effect=signal_candidates):
            base = monthly.monthly_candidates(frames, EntryRules(),
                "20260101", "20260228", "attribution", self.pools())
            changed = monthly.monthly_candidates(frames, EntryRules(),
                "20260101", "20260228", "attribution", future)
        self.assertEqual(base, changed)

    def test_month_end_signal_cannot_buy_expired_member_next_month(self):
        x = frame(3)
        x["datetime"] = [pd.Timestamp(f"{day} {clock}")
                         for day in ("2026-01-30", "2026-02-02", "2026-02-03")
                         for clock in TIMES]
        frames = {"A": x}
        with patch.object(monthly, "candidates", side_effect=signal_candidates):
            selected = monthly.monthly_candidates(
                frames, EntryRules(), "20260101", "20260228",
                "attribution", self.pools())
        owners = set(zip(self.pools().ts_code, self.pools().month))
        late = [item for item in selected if item["index"] == 15]
        self.assertEqual(len(late), 1)  # Retain unfilled opportunity in denominator.
        eligible = lambda code, fill: (code, fill.strftime("%Y%m")) in owners
        result = replay(frames, late, {}, entry_eligibility_fn=eligible)
        self.assertEqual(result["summary"]["candidates"], 1)
        self.assertEqual(result["summary"]["trades"], 0)
        self.assertEqual(result["summary"]["outcomes"]["not_in_current_month_pool"], 1)

    def filtered_samples(self, pools, samples):
        """Call prepare-stage ownership code while replacing feature/model work."""
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            captured = {}
            def fit(out, frames, sample, calendar):
                captured["sample"] = sample.copy()
            with patch.object(monthly, "read_pools", return_value=pools), \
                    patch.object(monthly, "validate_inputs", return_value=None), \
                    patch.object(monthly, "prepare", return_value=({}, samples, [])), \
                    patch.object(monthly, "fit", side_effect=fit):
                monthly.run(out, "prepare")
            audit = json.loads((out / "PROBABILITY_OWNERSHIP_AUDIT.json").read_text())
        return captured["sample"], audit

    def test_training_ownership_uses_origin_not_union_or_label_end(self):
        pools = pd.DataFrame({"ts_code": ["A", "B"],
                              "month": ["202507", "202508"]})
        samples = pd.DataFrame({
            "id": ["owned_origin", "future_member_early", "old_member_later", "new_owned"],
            "ts_code": ["A", "B", "A", "B"],
            "time": ["2025-07-31 14:15:00", "2025-07-31 14:15:00",
                     "2025-08-04 14:15:00", "2025-08-04 14:15:00"],
            "label_end": ["2025-08-06 14:15:00"] * 4,
        })
        kept, audit = self.filtered_samples(pools, samples)
        self.assertEqual(kept.id.tolist(), ["owned_origin", "new_owned"])
        self.assertEqual(audit["excluded_unowned_rows"], 2)

    def test_future_membership_cannot_authorize_earlier_training_samples(self):
        pools = pd.DataFrame({"ts_code": ["A"], "month": ["202507"]})
        samples = pd.DataFrame({"ts_code": ["A", "FUTURE"],
                              "time": ["2025-07-04 09:45:00"] * 2})
        before, _ = self.filtered_samples(pools, samples)
        changed = pd.concat([pools, pd.DataFrame({"ts_code": ["FUTURE"],
                                                "month": ["202508"]})],
                            ignore_index=True)
        after, _ = self.filtered_samples(changed, samples)
        pd.testing.assert_frame_equal(before, after)

    def test_probability_support_is_disabled_outside_origin_month_pool(self):
        pools = pd.DataFrame({"ts_code": ["A", "B"],
                              "month": ["202507", "202508"]})
        x = pd.DataFrame({"datetime": pd.to_datetime(
            ["2025-07-14 09:45", "2025-08-14 09:45"])})
        frames = {code: x.copy() for code in ("A", "B")}
        samples = pd.DataFrame({"ts_code": ["A", "B"],
            "time": ["2025-07-14 09:45", "2025-08-14 09:45"]})
        def fit(out, frames, samples, calendar):
            for data in frames.values():
                data["model_supported"] = True
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            (out / "frames").mkdir()
            with patch.object(monthly, "read_pools", return_value=pools), \
                    patch.object(monthly, "validate_inputs", return_value=None), \
                    patch.object(monthly, "prepare", return_value=(frames, samples, [])), \
                    patch.object(monthly, "fit", side_effect=fit):
                monthly.run(out, "prepare")
            a = pd.read_parquet(out / "frames" / "A.parquet")
            b = pd.read_parquet(out / "frames" / "B.parquet")
        self.assertEqual(a.model_supported.tolist(), [True, False])
        self.assertEqual(b.model_supported.tolist(), [False, True])

    def test_monthly_pool_expiration_does_not_force_existing_holding_exit(self):
        x = frame(3)
        x["datetime"] = [pd.Timestamp(f"{day} {clock}")
                         for day in ("2026-01-30", "2026-02-02", "2026-02-03")
                         for clock in TIMES]
        candidate = dict(id="hold_across_month", ts_code="A", index=0,
                         signal_time=x.loc[0, "datetime"].isoformat(),
                         stop=9.8, entry_score=1.)
        result = replay({"A": x}, [candidate], {}, policy="structure")
        self.assertEqual(len(result["ledger"]), 1)
        self.assertEqual(result["ledger"][0]["fill_time"], "2026-01-30T09:45:00")
        self.assertFalse(result["trades"][0]["closed"])
        self.assertEqual(result["trades"][0]["exit_reason"], "study_end_censored")
        self.assertGreater(result["trades"][0]["shares_remaining"], 0)

    def test_time_splits_wait_for_labels_and_have_no_origin_overlap(self):
        calendar = pd.bdate_range("2025-07-01", "2026-09-30").strftime("%Y%m%d").tolist()
        samples = pd.DataFrame({
            "time": ["2025-07-01 09:45:00", "2025-11-28 14:15:00",
                     "2025-12-05 09:45:00", "2025-12-25 14:15:00",
                     "2026-01-09 09:45:00", "2026-03-27 14:15:00",
                     "2026-04-10 09:45:00"],
            "label_end": ["2025-07-07 09:45:00", "2025-12-04 14:15:00",
                          "2025-12-11 09:45:00", "2026-01-02 14:15:00",
                          "2026-01-15 09:45:00", "2026-04-02 14:15:00",
                          "2026-04-16 09:45:00"],
        })
        masks = time_masks(samples, calendar)
        self.assertEqual({name: list(samples.index[mask]) for name, mask in masks.items()},
                         {"train": [0], "calibration": [2], "validation": [4],
                          "temporal_test": [6]})
        self.assertTrue(pd.DataFrame(masks).sum(axis=1).le(1).all())

    def test_read_pools_rejects_changed_frozen_membership(self):
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            file = out / "MONTHLY_POOLS.csv"
            self.pools().to_csv(file, index=False)
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            (out / "OBSERVATION_SELECTION.json").write_text(json.dumps({
                "selection_sha256": {"MONTHLY_POOLS.csv": digest}}))
            altered = self.pools().copy()
            altered.loc[0, "ts_code"] = "FUTURE"
            altered.to_csv(file, index=False)
            with self.assertRaises(ValueError):
                monthly.read_pools(out)

    def test_development_runner_passes_monthly_ownership_to_every_replay(self):
        """An engine callback alone is insufficient unless the runner passes it."""
        with tempfile.TemporaryDirectory() as root:
            out = Path(root)
            (out / "frames").mkdir()
            (out / "inputs").mkdir()
            pd.DataFrame({"ts_code": ["A", "B"]}).to_csv(
                out / "observation_pool.csv", index=False)
            for code in ("A", "B"):
                frame().to_parquet(out / "frames" / f"{code}.parquet")
            pd.DataFrame(columns=["ts_code", "trade_date", "up_limit", "down_limit"]).to_parquet(
                out / "inputs" / "limits.parquet")
            for name in ("FROZEN_MODEL.json", "probability_model.joblib", "probability_samples.parquet"):
                (out / name).write_text("synthetic frozen artifact")
            summary = dict(closed_trades=5, cash_accounting_verified=True,
                           worst_trade=-.01, net_return=0., win_rate=.4,
                           payoff_ratio=1.)
            with patch.object(monthly, "read_pools", return_value=self.pools()), \
                    patch.object(monthly, "validate_inputs", return_value=None), \
                    patch.object(monthly, "monthly_candidates", return_value=[]), \
                    patch.object(monthly, "replay", return_value={}) as execute, \
                    patch.object(monthly, "save_replay", return_value=None), \
                    patch.object(monthly, "enrich_summary", side_effect=lambda _: summary.copy()), \
                    patch("builtins.print"):
                monthly.run(out, "development")
            self.assertEqual(execute.call_count, 48)
            for call in execute.call_args_list:
                check = call.kwargs.get("entry_eligibility_fn")
                self.assertTrue(callable(check))
                self.assertTrue(check("A", pd.Timestamp("2026-01-30 14:45")))
                self.assertFalse(check("A", pd.Timestamp("2026-02-02 09:30")))
                self.assertTrue(check("B", pd.Timestamp("2026-02-02 09:30")))


if __name__ == "__main__":
    unittest.main()
