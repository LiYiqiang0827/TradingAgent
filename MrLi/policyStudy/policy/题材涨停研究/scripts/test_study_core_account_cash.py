"""Cash ledger invariants from synthetic signals only; never load a replay CSV."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from itertools import permutations

import pytest

import study_core_account_cash as subject


D = Decimal
PREFIX = "synthetic"


def row(code="SYN001", *, signal="2025-02-03 10:00:00",
        entry="2025-02-03 10:01:00", price="10",
        exit_at="2025-02-04 10:00:00", exit_price="10",
        buyable="true", status="closed", theme="THEME_A"):
    return {
        "ts_code": code, "theme_id": theme, "theme_name": "Synthetic theme",
        "signal_time": signal, f"{PREFIX}_buyable": buyable,
        f"{PREFIX}_status": status, f"{PREFIX}_entry_execution_at": entry,
        f"{PREFIX}_entry_open": price, f"{PREFIX}_exit_execution_at": exit_at,
        f"{PREFIX}_exit_open": exit_price,
    }


def replay(rows, weight="0.1", initial="100000", minimum="5"):
    return subject.replay(subject.normalize(rows, PREFIX), D(weight),
                          initial=D(initial), minimum_commission=D(minimum))


def records_by_code(result):
    return {item["ts_code"]: item for item in result["records"]}


def entry_footprint(result):
    """Decision-time allocation fields, deliberately excluding later status/PnL."""
    keys = ("id", "entry_at", "shares", "entry_price", "entry_cash", "buy_charges")
    return [{key: item.get(key) for key in keys} for item in result["records"]]


def test_charges_minimum_commission_and_sell_only_tax():
    buy = subject.charges(D("10"), 100)
    sell = subject.charges(D("10"), 100, selling=True)
    assert buy == {"gross": D("1000.00"), "slippage": D("1.00"),
                   "commission": D("5.00"), "tax": D("0"), "cash": D("1006.00")}
    assert sell == {"gross": D("1000.00"), "slippage": D("1.00"),
                    "commission": D("5.00"), "tax": D("0.50"), "cash": D("993.50")}
    assert subject.charges(D("10"), 100, minimum_commission=D("0"))["cash"] == D("1001.30")
    assert subject.charges(D("10"), 10000)["commission"] == D("30.00")


def test_cash_charges_round_half_up_to_cents():
    buy = subject.charges(D("10.005"), 100)
    assert buy["gross"] == D("1000.50")
    assert buy["cash"] == D("1006.50")
    assert subject.money(D("1.005")) == D("1.01")


@pytest.mark.parametrize("price,shares", [("0", 100), ("-1", 100), ("10", 0),
                                          ("10", 99), ("10", 150), ("Infinity", 100)])
def test_charges_reject_invalid_price_or_non_round_lot(price, shares):
    with pytest.raises(ValueError):
        subject.charges(D(price), shares)


@pytest.mark.parametrize("budget,expected", [("0", 0), ("1005.99", 0),
                                             ("1006.00", 100), ("2006.99", 100),
                                             ("2007.00", 200), ("100000", 9900)])
def test_sizing_round_lots_and_all_in_budget(budget, expected):
    shares = subject.size_shares(D("10"), D(budget), D("5"))
    assert shares == expected and shares % 100 == 0
    if shares:
        assert subject.charges(D("10"), shares)["cash"] <= D(budget)
    assert subject.charges(D("10"), shares + 100)["cash"] > D(budget)


def test_full_allocation_never_spends_commissions_on_borrowed_cash():
    result = replay([row()], weight="1", initial="1006")
    assert result["records"][0]["shares"] == 100
    assert result["lowest_cash"] == "0.00"
    assert result["complete"] is True
    assert D(result["final_cash_if_complete"]) == D("993.50")
    assert D(result["pnl_from_closed_positions"]) == D("-12.50")


@pytest.mark.parametrize("exit_at", ["2025-02-02 15:00:00", "2025-02-03 15:00:00"])
def test_normalize_rejects_same_day_or_earlier_exit(exit_at):
    with pytest.raises(ValueError, match="T\\+1"):
        subject.normalize([row(exit_at=exit_at)], PREFIX)


@pytest.mark.parametrize("entry", ["2025-02-03 10:00:00", "2025-02-03 10:00:59"])
def test_normalize_requires_frozen_minute_reaction_delay(entry):
    with pytest.raises(ValueError, match="reaction delay"):
        subject.normalize([row(entry=entry)], PREFIX)


def test_normalize_accepts_exact_sixty_second_reaction_and_next_day_exit():
    trade = subject.normalize([row()], PREFIX)[0]
    assert (trade["entry_at"] - trade["signal"]).total_seconds() == 60
    assert trade["exit_at"].date() > trade["entry_at"].date()


def test_normalize_rejects_duplicate_stock_and_equivalent_signal_time():
    duplicate = row(signal="2025-02-03T10:00:00")
    with pytest.raises(ValueError, match="Duplicate"):
        subject.normalize([row(), duplicate], PREFIX)


def test_insufficient_cash_rejects_entire_fixed_lot_order_without_leverage():
    rows = [row("SYN001"), row("SYN002", signal="2025-02-03 10:01:00",
                                entry="2025-02-03 10:02:00")]
    result = replay(rows, weight="0.6", initial="10000")
    assert result["counts"]["signals"] == 2
    assert result["counts"]["accepted"] == 1
    assert records_by_code(result)["SYN002"]["status"] == "cash_rejected"
    assert "shares" not in records_by_code(result)["SYN002"]
    assert all(D(event["cash"]) >= 0 for event in result["timeline"])
    assert D(result["max_cost_used"]) <= D("10000")


def test_same_stock_cannot_stack_and_ignored_trade_exit_cannot_close_another_position():
    rows = [row(exit_at="2025-02-05 10:00:00"),
            row(signal="2025-02-04 10:00:00", entry="2025-02-04 10:01:00",
                exit_at="2025-02-06 10:00:00", exit_price="100")]
    result = replay(rows)
    assert result["counts"]["signals"] == 2
    assert result["counts"]["accepted"] == 1
    assert result["counts"]["already_held"] == 1
    assert result["counts"]["closed"] == 1
    assert result["max_positions"] == 1
    assert [record["status"] for record in result["records"]] == ["closed", "already_held"]
    assert len([x for x in result["timeline"] if x["event"] == "closed"]) == 1


def test_known_unfilled_excluded_and_below_lot_remain_in_signal_denominator():
    statuses = ("entry_at_up_limit", "entry_no_volume", "entry_gap_above_cap",
                "excluded_limit_regime")
    rows = [row(f"SYN{i}", buyable="false", status=status, entry="", price="",
                exit_at="", exit_price="") for i, status in enumerate(statuses)]
    rows.append(row("SYN_EXPENSIVE", price="1000", exit_price="1000"))
    result = replay(rows, weight="0.1", initial="10000")
    assert result["counts"]["signals"] == len(result["records"]) == 5
    assert result["counts"]["source_unfilled"] == 3
    assert result["counts"]["source_excluded"] == 1
    assert result["counts"]["below_one_lot"] == 1
    assert result["complete"] is True
    assert D(result["final_cash_if_complete"]) == D("10000")


@pytest.mark.parametrize("buyable", ["", "nan", "false"])
def test_unknown_entry_preserves_denominator_but_forbids_final_account_result(buyable):
    rows = [row("SYN_UNKNOWN", entry="", price="", exit_at="", exit_price="",
                buyable=buyable, status="entry_missing"), row("SYN_KNOWN")]
    result = replay(rows)
    assert result["counts"]["signals"] == len(result["records"]) == 2
    assert result["unknown_entry_present"] is True
    assert result["complete"] is False
    assert result["final_cash_if_complete"] is None
    assert result["total_return_pct_if_complete"] is None
    assert records_by_code(result)["SYN_UNKNOWN"]["status"] == "source_entry_unknown"
    assert records_by_code(result)["SYN_KNOWN"]["decision_uncertain_due_to_prior_gap"] is True


def test_unresolved_accepted_holding_forbids_final_cash_and_return():
    result = replay([row(status="holding_unresolved", exit_at="", exit_price="")])
    assert result["counts"]["signals"] == result["counts"]["accepted"] == 1
    assert result["records"][0]["status"] == "holding_unresolved"
    assert result["unresolved_holdings"] == 1
    assert result["complete"] is False
    assert result["final_cash_if_complete"] is None
    assert result["total_return_pct_if_complete"] is None


def test_future_exit_price_time_and_net_cannot_change_earlier_entry_allocation():
    original = [row("SYN001", exit_at="2025-02-05 10:00:00"),
                row("SYN002", signal="2025-02-04 10:00:00", entry="2025-02-04 10:01:00",
                    exit_at="2025-02-06 10:00:00")]
    changed = deepcopy(original)
    for item in changed:
        item[f"{PREFIX}_exit_execution_at"] = "2025-02-10 15:00:00"
        item[f"{PREFIX}_exit_open"] = "0.01"
        item[f"{PREFIX}_net"] = "-0.999999"
        item["future_max_return"] = "999999"
    before, after = replay(original), replay(changed)
    assert entry_footprint(before) == entry_footprint(after)
    cutoff = "2025-02-05T00:00:00"
    assert [e for e in before["timeline"] if e["time"] < cutoff] == [
        e for e in after["timeline"] if e["time"] < cutoff]
    assert before["final_cash_if_complete"] != after["final_cash_if_complete"]


def test_future_unresolved_exit_status_does_not_select_earlier_entry():
    known = row()
    unknown = row(exit_at="", exit_price="", status="holding_unresolved")
    before, after = replay([known]), replay([unknown])
    assert entry_footprint(before) == entry_footprint(after)
    assert before["complete"] is True and after["complete"] is False


def test_same_timestamp_exit_proceeds_are_unavailable_to_new_entry():
    rows = [row("SYN001", exit_at="2025-02-04 10:01:00", exit_price="20"),
            row("SYN002", signal="2025-02-04 10:00:00", entry="2025-02-04 10:01:00",
                exit_at="2025-02-05 10:00:00")]
    result = replay(rows, weight="0.6", initial="10000")
    records = records_by_code(result)
    assert records["SYN001"]["status"] == "closed"
    assert records["SYN002"]["status"] == "cash_same_time_reuse_blocked"
    assert result["counts"]["accepted"] == 1
    assert result["counts"]["signals"] == 2


def test_next_timestamp_may_use_prior_timestamp_sale_proceeds():
    rows = [row("SYN001", exit_at="2025-02-04 10:01:00", exit_price="20"),
            row("SYN002", signal="2025-02-04 10:01:00", entry="2025-02-04 10:02:00",
                exit_at="2025-02-05 10:00:00")]
    result = replay(rows, weight="0.6", initial="10000")
    assert result["counts"]["accepted"] == result["counts"]["closed"] == 2
    assert records_by_code(result)["SYN002"]["status"] == "closed"


def test_same_timestamp_multiple_entries_share_remaining_pre_timestamp_cash():
    rows = [row("SYN000", exit_at="2025-02-04 10:01:00", exit_price="20")]
    rows += [row(code, signal="2025-02-04 10:00:00", entry="2025-02-04 10:01:00",
                 exit_at="2025-02-05 10:00:00") for code in ("SYN001", "SYN002", "SYN003")]
    result = replay(rows, weight="0.4", initial="10000")
    records = records_by_code(result)
    # First order costs 3008; pre-timestamp cash is 6992. Two, not three,
    # further 3008 orders fit, irrespective of the simultaneous sale receipt.
    assert D(records["SYN000"]["entry_cash"]) == D("3008.00")
    assert records["SYN001"]["status"] == records["SYN002"]["status"] == "closed"
    assert records["SYN003"]["status"] == "cash_same_time_reuse_blocked"
    assert result["counts"]["accepted"] == 3
    assert result["counts"]["signals"] == 4


def test_same_timestamp_exit_clears_stock_before_reentry_if_old_cash_suffices():
    rows = [row(exit_at="2025-02-04 10:01:00", exit_price="20"),
            row(signal="2025-02-04 10:00:00", entry="2025-02-04 10:01:00",
                exit_at="2025-02-05 10:00:00")]
    result = replay(rows, weight="0.4", initial="10000")
    assert result["counts"]["accepted"] == result["counts"]["closed"] == 2
    assert result["counts"].get("already_held", 0) == 0
    assert result["max_positions"] == 1


def test_same_timestamp_entry_order_is_stable_by_signal_then_stock():
    rows = [row("SYN_Z", signal="2025-02-03 09:59:00", entry="2025-02-03 10:01:00"),
            row("SYN_B"), row("SYN_A"), row("SYN_C")]
    baseline = None
    for ordering in permutations(rows):
        result = replay(list(ordering), weight="0.4", initial="10000")
        if baseline is None:
            baseline = result
        assert result == baseline
    assert [r["ts_code"] for r in baseline["records"]] == ["SYN_Z", "SYN_A", "SYN_B", "SYN_C"]
    assert [r["status"] for r in baseline["records"]] == ["closed", "closed", "closed", "cash_rejected"]


def test_minimum_commission_changes_account_cost_once_each_side():
    rows = [row()]
    minimum = replay(rows, weight="1", initial="1500", minimum="5")
    no_minimum = replay(rows, weight="1", initial="1500", minimum="0")
    assert minimum["records"][0]["shares"] == no_minimum["records"][0]["shares"] == 100
    assert D(no_minimum["final_cash_if_complete"]) - D(minimum["final_cash_if_complete"]) == D("9.40")


@pytest.mark.parametrize("weight,initial,minimum", [("0", "100000", "5"),
    ("1.01", "100000", "5"), ("0.1", "0", "5"), ("0.1", "100000", "-1")])
def test_invalid_account_configuration_is_rejected(weight, initial, minimum):
    with pytest.raises(ValueError, match="configuration"):
        replay([], weight=weight, initial=initial, minimum=minimum)
