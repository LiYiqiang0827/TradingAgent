"""Meaningful causal/execution checks for the separate trend-pullback engine."""
import unittest

import numpy as np
import pandas as pd

from trend_pullback_exit_engine import ExecutionRules, replay


TIMES = ("09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30",
         "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00")


def frame(days=3, price=10.):
    stamps = [pd.Timestamp(f"2026-01-{5+day:02d} {time}")
              for day in range(days) for time in TIMES]
    x = pd.DataFrame({"datetime": stamps, "valid": True, "scale": 1.})
    for field in ("open", "high", "low", "close", "p_open", "p_high", "p_low", "p_close"):
        x[field] = price
    x["vol"] = 100000.; x["atr14"] = .2; x["volume_multiple8"] = 1.
    for n in (5, 10, 20, 30, 60, 120, 250):
        x[f"ma{n}"] = price-.1-n/1000
    return x


def set_price(x, index, price, fields=("open", "high", "low", "close")):
    for field in fields:
        x.loc[index, field] = price; x.loc[index, "p_"+field] = price*x.loc[index, "scale"]


def candidate(code="TEST", index=0, stop=9.8, id="case"):
    return dict(id=id, ts_code=code, index=index, stop=stop, entry_score=1.)


class ReplayTest(unittest.TestCase):
    def test_completed_decision_uses_next_bar_open_not_signal_close(self):
        x = frame(); set_price(x, 0, 100., ("high", "close"))
        x.loc[0, "ma30"] = 99.
        result = replay({"TEST": x}, [candidate()], {}, policy="ma30")
        buy = result["ledger"][0]
        self.assertEqual(buy["bar_index"], 1)
        self.assertAlmostEqual(buy["price"], 10.005)
        self.assertEqual(buy["decision_time"], "2026-01-05T09:45:00")
        self.assertEqual(buy["fill_time"], "2026-01-05T09:45:00")

    def test_t1_retains_stop_order_and_records_large_gap_loss(self):
        x = frame(); set_price(x, 1, 9.5, ("low", "close"))
        for index in range(16, 48):
            set_price(x, index, 8.)
        result = replay({"TEST": x}, [candidate()], {}, policy="structure")
        sells = [order for order in result["ledger"] if order["side"] == "sell"]
        self.assertEqual(sells[0]["bar_index"], 16)
        self.assertEqual(sells[0]["fill_time"], "2026-01-06T09:30:00")
        self.assertGreater(result["trades"][0]["sell_delay_bars"], 0)
        self.assertTrue(result["summary"]["risk_acceptance_failed"])
        self.assertLess(result["trades"][0]["net_return"], -.10)

    def test_stop_risk_gap_cancels_entry_instead_of_widening_base(self):
        x = frame(); set_price(x, 1, 10.3)
        result = replay({"TEST": x}, [candidate()], {})
        self.assertEqual(result["summary"]["trades"], 0)
        self.assertEqual(result["summary"]["outcomes"]["entry_gap_risk_above_three_pct"], 1)

    def test_up_limit_open_unfilled_and_down_limit_open_sell_waits(self):
        x = frame()
        rejected = replay({"TEST": x}, [candidate()], {("TEST", "20260105"): {"up_limit": 10.}})
        self.assertEqual(rejected["summary"]["trades"], 0)
        set_price(x, 1, 9.5, ("low", "close"))
        for index in range(16, 32):
            set_price(x, index, 9.)
        result = replay({"TEST": x}, [candidate()], {
            ("TEST", "20260106"): {"down_limit": 9.}}, policy="structure")
        sells = [order for order in result["ledger"] if order["side"] == "sell"]
        self.assertEqual(sells[0]["bar_index"], 32)
        self.assertIn("down_limit_open_delayed", [issue["code"] for issue in result["issues"]])

    def test_probability_trim_occurs_once_without_reclaim(self):
        x = frame()
        probability = lambda row, state: dict(p_fail=.8, p_extend=.1, n=40, supported=True)
        result = replay({"TEST": x}, [candidate()], {}, probability_fn=probability)
        actions = [row for row in result["decisions"] if row["reason"] == "supported_failure_probability"]
        # Being continuously above MA20 is not a fresh reclaim. The stage
        # cannot repeatedly trim solely because the model stays pessimistic.
        self.assertEqual(len(actions), 1)
        sells = [row for row in result["ledger"] if row["side"] == "sell"]
        self.assertEqual(len(sells), 1)
        self.assertTrue(all(row["qty"] % 100 == 0 for row in result["ledger"]))

    def test_unsupported_probabilities_do_not_trim_or_add(self):
        x = frame()
        probability = lambda row, state: dict(p_fail=.99, p_extend=.99, n=2, supported=False)
        result = replay({"TEST": x}, [candidate()], {}, probability_fn=probability)
        self.assertEqual(len(result["ledger"]), 1)
        self.assertEqual(result["summary"]["probability_supported_decisions"], 0)

    def test_add_requires_past_high_break_and_separate_t1_lot(self):
        x = frame()
        for index in range(2, 48):
            set_price(x, index, 10.+(index-1)*.01)
            x.loc[index, "ma20"] = 9.9+(index-1)*.005
        probability = lambda row, state: dict(p_fail=.1, p_extend=.8, n=40, supported=True)
        result = replay({"TEST": x}, [candidate()], {}, probability_fn=probability)
        buys = [row for row in result["ledger"] if row["side"] == "buy"]
        self.assertEqual(len(buys), 2)
        self.assertGreaterEqual(buys[1]["bar_index"]-buys[0]["bar_index"], 4)
        self.assertEqual(buys[1]["reason"], "confirmed_extension_add")
        self.assertGreater(result["trades"][0]["fees"], buys[0]["fee"])

    def test_fee_accounting_and_cash_identity_for_closed_trade(self):
        x = frame()
        x.loc[16:, "ma30"] = 10.5
        result = replay({"TEST": x}, [candidate()], {}, policy="ma30")
        trade = result["trades"][0]
        self.assertTrue(trade["closed"])
        self.assertAlmostEqual(result["summary"]["final_equity"], 300000+trade["pnl"])
        self.assertAlmostEqual(trade["fees"], sum(order["fee"] for order in result["ledger"]))
        self.assertLess(trade["net_return"], 0.)

    def test_overlap_signals_and_portfolio_caps_are_explicit(self):
        x = frame(); frames = {f"NAME{n}": x.copy() for n in range(12)}
        candidates = [candidate(code, id=code) for code in frames]
        candidates.append(candidate("NAME0", index=2, id="overlap"))
        result = replay(frames, candidates, {}, max_name_pct=.2, max_total_pct=.8)
        self.assertEqual(result["summary"]["outcomes"]["overlap_signal"], 1)
        for point in result["curve"]:
            self.assertLessEqual(point["exposure"]/point["equity"], .800001)
        self.assertTrue(all(order["qty"] % 100 == 0 for order in result["ledger"]))
        self.assertGreater(result["summary"]["outcomes"].get("allocation_or_cash_unfilled", 0), 0)

    def test_future_mutation_cannot_change_prefix_actions(self):
        x = frame(); altered = x.copy()
        for index in range(32, 48):
            set_price(altered, index, 5.)
        base = replay({"TEST": x}, [candidate()], {}, policy="structure")
        changed = replay({"TEST": altered}, [candidate()], {}, policy="structure")
        cutoff = "2026-01-07T09:30:00"
        for key, stamp in (("ledger", "fill_time"), ("decisions", "datetime")):
            left = [row for row in base[key] if row[stamp] < cutoff]
            right = [row for row in changed[key] if row[stamp] < cutoff]
            self.assertEqual(left, right)

    def test_scale_change_is_not_hidden_in_verified_cash_result(self):
        x = frame(); x.loc[16:, "scale"] = 1.01
        for field in ("open", "high", "low", "close"):
            x["p_"+field] = x[field]*x["scale"]
        result = replay({"TEST": x}, [candidate()], {}, policy="structure")
        self.assertFalse(result["summary"]["cash_accounting_verified"])
        self.assertIn("corporate_action_unmodeled", [row["code"] for row in result["issues"]])

    def test_study_end_does_not_force_sell_and_last_signal_unfilled(self):
        x = frame(1)
        result = replay({"TEST": x}, [candidate()], {}, policy="structure")
        self.assertEqual(result["summary"]["closed_trades"], 0)
        self.assertEqual(result["summary"]["censored_trades"], 1)
        self.assertEqual(result["trades"][0]["exit_reason"], "study_end_censored")
        final_signal = replay({"TEST": x}, [candidate(index=15)], {})
        self.assertEqual(final_signal["summary"]["outcomes"]["study_end_unfilled"], 1)

    def test_invalid_intended_next_slot_cancels_entry_instead_of_stale_fill(self):
        x = frame(); x.loc[1:2, "valid"] = False
        result = replay({"TEST": x}, [candidate()], {}, policy="ma30")
        self.assertEqual(len(result["ledger"]), 0)
        self.assertEqual(result["summary"]["outcomes"]["invalid_next_slot_entry_cancelled"], 1)
        invalid = replay({"TEST": x}, [candidate(index=1)], {})
        self.assertEqual(invalid["summary"]["outcomes"]["invalid_signal"], 1)

    def test_trial_and_add_keep_loss_to_frozen_stop_in_account_budget(self):
        x = frame()
        for index in range(2, 48):
            set_price(x, index, 10.+(index-1)*.02)
            x.loc[index, "ma20"] = 9.9+(index-1)*.01
        probability = lambda row, state: dict(p_fail=.1, p_extend=.9, n=100, supported=True)
        result = replay({"TEST": x}, [candidate()], {}, probability_fn=probability)
        buys = [row for row in result["ledger"] if row["side"] == "buy"]
        self.assertEqual(len(buys), 2)
        stop = result["trades"][0]["stop_p"]
        self.assertLessEqual(sum(order["qty"]*(order["price"]-stop) for order in buys),
                             .005*300000+1e-8)
        self.assertLessEqual(buys[0]["qty"]*buys[0]["price"], .1*300000)

    def test_urgent_stop_sells_old_lot_then_waits_added_same_day_lot(self):
        x = frame(3)
        for index in range(16, 20):
            set_price(x, index, 10.+(index-15)*.02)
            x.loc[index, "ma20"] = 9.9+(index-15)*.01
        # The add was requested at bar 16 and filled at bar 17. That bar
        # subsequently closes below the stop: old shares may sell at 18,
        # but the new lot must remain until the next session.
        set_price(x, 17, 9.5, ("low", "close"))
        probability = lambda row, state: dict(p_fail=.1, p_extend=.9, n=100, supported=True)
        result = replay({"TEST": x}, [candidate()], {}, probability_fn=probability)
        buys = [row for row in result["ledger"] if row["side"] == "buy"]
        sells = [row for row in result["ledger"] if row["side"] == "sell"]
        self.assertEqual(buys[1]["bar_index"], 17)
        self.assertEqual(sells[0]["bar_index"], 18)
        self.assertEqual(sells[0]["qty"], buys[0]["qty"])
        self.assertEqual(sells[1]["bar_index"], 32)
        self.assertEqual(sells[1]["qty"], buys[1]["qty"])

    def test_callback_receives_only_prefix_peak_and_no_future_row(self):
        x = frame(2); set_price(x, 2, 20., ("high",))
        calls = []
        def probability(row, state):
            calls.append((row["datetime"], dict(state)))
            return dict(p_fail=.1, p_extend=.5, n=100, supported=True)
        replay({"TEST": x}, [candidate()], {}, probability_fn=probability)
        current = dict(calls)
        self.assertLess(current[x.loc[2, "datetime"]]["peak_p"], 20.)
        self.assertEqual(current[x.loc[3, "datetime"]]["peak_p"], 20.)

    def test_minimum_lot_trim_is_recorded_and_minimum_fee_not_diluted(self):
        x = frame(2)
        probability = lambda row, state: dict(p_fail=.8, p_extend=.1, n=40, supported=True)
        result = replay({"TEST": x}, [candidate()], {}, probability_fn=probability,
                        initial_cash=11000.)
        orders = result["ledger"]
        self.assertEqual(orders[0]["qty"], 100)
        self.assertEqual(orders[1]["qty"], 100)
        self.assertEqual(orders[1]["requested_fraction"], .5)
        self.assertGreaterEqual(orders[0]["fee"], 5.)
        self.assertGreaterEqual(orders[1]["fee"], 5.)

    def test_probability_and_ma20_partial_stages_have_four_bar_cooldown(self):
        x = frame(3)
        for index in range(16, 48):
            set_price(x, index, 10.1)
            x.loc[index, "ma20"] = 10.2
        def probability(row, state):
            active = row["datetime"] >= x.loc[17, "datetime"]
            return dict(p_fail=.8 if active else .1, p_extend=.1 if active else .8,
                        n=40, supported=True)
        result = replay({"TEST": x}, [candidate()], {}, probability_fn=probability)
        trims = [row for row in result["decisions"]
                 if row["side"] == "sell" and row["fraction"] < 1.]
        self.assertGreaterEqual(len(trims), 2)
        self.assertTrue(all(b["index"]-a["index"] >= 4 for a, b in zip(trims, trims[1:])))

    def test_invalid_intended_add_slot_cancels_and_never_stale_adds(self):
        x = frame(3)
        for index in range(2, 48):
            set_price(x, index, 10.+(index-1)*.01)
            x.loc[index, "ma20"] = 9.9+(index-1)*.005
        x.loc[5, "valid"] = False
        probability = lambda row, state: dict(p_fail=.1, p_extend=.8, n=40, supported=True)
        result = replay({"TEST": x}, [candidate()], {}, probability_fn=probability)
        self.assertEqual(len([row for row in result["ledger"] if row["side"] == "buy"]), 1)
        self.assertEqual(result["summary"]["outcomes"]["invalid_next_slot_add_cancelled"], 1)

    def test_common_clock_buy_priority_uses_causal_score_not_stock_code(self):
        x = frame(1)
        candidates = [candidate(f"NAME{n}", id=f"case{n}") for n in range(12)]
        for n, item in enumerate(candidates):
            item["entry_score"] = n
        result = replay({item["ts_code"]: x.copy() for item in candidates}, candidates, {})
        self.assertEqual(result["ledger"][0]["ts_code"], "NAME11")
        self.assertNotIn("NAME0", [row["ts_code"] for row in result["ledger"]])

    def test_month_end_signal_cannot_buy_in_next_month_after_pool_exit(self):
        x = frame(3)
        days = ("2026-01-30", "2026-02-02", "2026-02-03")
        x["datetime"] = [pd.Timestamp(f"{day} {time}") for day in days for time in TIMES]
        calls = []
        def eligible(code, fill_time):
            calls.append((code, fill_time))
            return fill_time.strftime("%Y%m") == "202601"
        result = replay({"TEST": x}, [candidate(index=15)], {}, entry_eligibility_fn=eligible)
        self.assertEqual(result["summary"]["candidates"], 1)
        self.assertEqual(result["summary"]["trades"], 0)
        self.assertEqual(result["summary"]["outcomes"]["not_in_current_month_pool"], 1)
        self.assertEqual(calls, [("TEST", pd.Timestamp("2026-02-02 09:30:00"))])
        issue = result["issues"][0]
        self.assertEqual(issue["code"], "not_in_current_month_pool")
        self.assertEqual(issue["side"], "buy")
        self.assertEqual(issue["decision_time"], "2026-01-30T15:00:00")

    def test_pool_exit_cannot_block_existing_position_stop_sale(self):
        x = frame(3)
        days = ("2026-01-30", "2026-02-02", "2026-02-03")
        x["datetime"] = [pd.Timestamp(f"{day} {time}") for day in days for time in TIMES]
        set_price(x, 15, 9.5, ("low", "close"))
        calls = []
        def eligible(code, fill_time):
            calls.append((code, fill_time))
            return fill_time.strftime("%Y%m") == "202601"
        result = replay({"TEST": x}, [candidate()], {}, policy="structure", entry_eligibility_fn=eligible)
        sells = [row for row in result["ledger"] if row["side"] == "sell"]
        self.assertEqual(sells[0]["bar_index"], 16)
        self.assertEqual(sells[0]["fill_time"], "2026-02-02T09:30:00")
        self.assertTrue(result["trades"][0]["closed"])
        self.assertEqual(len(calls), 1)  # Buy checked; no sell-side pool check.

    def test_confirmation_add_cancelled_after_monthly_pool_exit(self):
        x = frame(3)
        days = ("2026-01-30", "2026-02-02", "2026-02-03")
        x["datetime"] = [pd.Timestamp(f"{day} {time}") for day in days for time in TIMES]
        for index in range(16, 48):
            set_price(x, index, 10.+(index-15)*.02)
            x.loc[index, "ma20"] = 9.9+(index-15)*.01
        probability = lambda row, state: dict(p_fail=.1, p_extend=.9, n=100, supported=True)
        eligible = lambda code, fill_time: fill_time.strftime("%Y%m") == "202601"
        result = replay({"TEST": x}, [candidate()], {}, probability_fn=probability, entry_eligibility_fn=eligible)
        buys = [row for row in result["ledger"] if row["side"] == "buy"]
        self.assertEqual(len(buys), 1)
        self.assertFalse(result["trades"][0]["added"])
        self.assertEqual(result["summary"]["outcomes"]["add_not_in_current_month_pool"], 1)
        issue = next(row for row in result["issues"] if row["code"] == "not_in_current_month_pool")
        self.assertEqual(issue["side"], "add")
        self.assertEqual(issue["fill_time"], "2026-02-02T09:45:00")

    def test_explicit_none_pool_callback_preserves_previous_behavior(self):
        x = frame()
        previous = replay({"TEST": x}, [candidate()], {}, policy="structure")
        explicit = replay({"TEST": x}, [candidate()], {}, policy="structure", entry_eligibility_fn=None)
        self.assertEqual(previous, explicit)


if __name__ == "__main__":
    unittest.main()
