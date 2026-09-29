"""Synthetic, independent cash/valuation fixtures; never read real research files."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import permutations

import pytest

import study_core_compound_nav as subject
from study_core_account_cash import normalize


D = Decimal
CALENDAR = ["20250203", "20250204", "20250205"]
PREFIX = "synthetic"


def stamp(value):
    return datetime.fromisoformat(value)


def row(code="SYN_A", *, signal="2025-02-03 10:00:00", entry="2025-02-03 10:01:00",
        price="10", exit_at="2025-02-04 10:01:00", exit_price="10",
        buyable="true", status="closed", theme="SYN_THEME"):
    return {"ts_code": code, "theme_id": theme, "theme_name": "Synthetic theme",
            "signal_time": signal, f"{PREFIX}_buyable": buyable,
            f"{PREFIX}_status": status, f"{PREFIX}_entry_execution_at": entry,
            f"{PREFIX}_entry_open": price, f"{PREFIX}_exit_execution_at": exit_at,
            f"{PREFIX}_exit_open": exit_price}


def independent_clocks(day):
    date = datetime.strptime(day, "%Y%m%d")
    return [date.replace(hour=hour, minute=minute) + timedelta(minutes=i)
            for hour, minute in ((9, 31), (13, 1)) for i in range(120)]


def quotes(codes=("SYN_A",), calendar=None, price="10"):
    return {(code, time): {"open": D(price), "close": D(price), "adj_factor": D("1")}
            for code in codes for day in (calendar or CALENDAR) for time in independent_clocks(day)}


def replay(rows, data, *, weight="0.5", initial="10000", calendar=None):
    return subject.replay_compound(normalize(rows, PREFIX), calendar or CALENDAR, data,
                                   D(weight), initial=D(initial), minimum_commission=D("5"))


def point(result, time):
    return next(p for p in result["minute_nav"] if p["time"] == stamp(time).isoformat())


def bycode(result):
    return {r["ts_code"]: r for r in result["records"]}


def test_minute_close_schedule_contains_exact_240_regular_close_points():
    actual = subject.minute_closes("20250203")
    assert actual == independent_clocks("20250203")
    assert len(actual) == len(set(actual)) == 240
    assert [x.strftime("%H:%M") for x in (actual[0], actual[119], actual[120], actual[-1])] == [
        "09:31", "11:30", "13:01", "15:00"]
    assert not {"09:30", "13:00"}.intersection(x.strftime("%H:%M") for x in actual)


def test_drawdown_peak_trough_recovery_and_initial_peak():
    navs = [100, 120, 90, 100, 120, 119]
    times = [f"2025-02-03T10:0{i}:00" for i in range(len(navs))]
    result = subject.drawdown([{"time": t, "nav": str(n)} for t, n in zip(times, navs)])
    assert result == {"max_drawdown_pct": 25.0, "peak_time": times[1], "trough_time": times[2],
                      "recovery_time": times[4], "peak_nav": "120", "trough_nav": "90"}
    initial_peak = subject.drawdown([{"time": times[i], "nav": str(n)}
                                     for i, n in enumerate([100, 80, 99])])
    assert initial_peak["max_drawdown_pct"] == 20.0
    assert initial_peak["peak_time"] == times[0]
    assert initial_peak["recovery_time"] is None


def test_drawdown_replaces_earlier_recovered_loss_with_larger_later_loss():
    points = [{"time": f"2025-02-03T10:0{i}:00", "nav": str(n)}
              for i, n in enumerate([100, 80, 100, 150, 90, 149])]
    result = subject.drawdown(points)
    assert result["max_drawdown_pct"] == 40.0
    assert result["peak_time"] == points[3]["time"]
    assert result["trough_time"] == points[4]["time"]
    assert result["recovery_time"] is None


@pytest.mark.parametrize("nav", ["0", "-1", "NaN", "Infinity"])
def test_drawdown_rejects_invalid_nav(nav):
    with pytest.raises(ValueError, match="NAV"):
        subject.drawdown([{"time": "2025-02-03T09:30:00", "nav": nav}])


def test_empty_holdings_need_no_quotes_and_still_emit_full_synchronized_nav():
    result = subject.replay_compound([], CALENDAR, {}, D("0.2"))
    assert D(result["initial_capital"]) == D(result["final_capital"]) == D("300000")
    assert len(result["minute_nav"]) == 1 + 3 * 240
    assert len(result["daily_nav"]) == 4
    assert D(result["minute_nav"][0]["nav"]) == D("300000")
    assert all(D(x["nav"]) == D("300000") for x in result["minute_nav"])
    assert result["minute_drawdown"]["max_drawdown_pct"] == 0
    assert result["daily_drawdown"]["max_drawdown_pct"] == 0


def test_unrealized_loss_is_in_nav_and_mdd_before_position_is_sold():
    data = quotes()
    data[("SYN_A", stamp("2025-02-03 11:00:00"))]["close"] = D("5")
    result = replay([row()], data)
    assert bycode(result)["SYN_A"]["shares"] == 400
    observed = point(result, "2025-02-03 11:00:00")
    assert D(observed["cash"]) == D("5991")
    assert D(observed["market_value"]) == D("2000")
    assert D(observed["nav"]) == D("7991")
    assert result["minute_drawdown"]["max_drawdown_pct"] == pytest.approx(20.09)
    assert result["minute_drawdown"]["peak_time"] == "2025-02-03T09:30:00"
    assert result["minute_drawdown"]["trough_time"] == "2025-02-03T11:00:00"
    assert D(result["final_capital"]) == D("9980")
    assert result["daily_drawdown"]["max_drawdown_pct"] == pytest.approx(0.2)


def test_minute_close_valuation_precedes_next_minute_open_executions():
    data = quotes()
    data[("SYN_A", stamp("2025-02-03 10:02:00"))]["close"] = D("5")
    data[("SYN_A", stamp("2025-02-04 10:01:00"))]["close"] = D("4")
    result = replay([row()], data)
    # Entry at 10:01 uses the 10:02-labelled bar's OPEN, after the 10:01 close mark.
    assert point(result, "2025-02-03 10:01:00")["positions"] == 0
    assert D(point(result, "2025-02-03 10:01:00")["nav"]) == D("10000")
    assert point(result, "2025-02-03 10:02:00")["positions"] == 1
    assert D(point(result, "2025-02-03 10:02:00")["nav"]) == D("7991")
    # Sale at 10:01 does not erase the lower 10:01 marked value.
    assert D(point(result, "2025-02-04 10:01:00")["nav"]) == D("7591")
    assert point(result, "2025-02-04 10:01:00")["positions"] == 1
    assert point(result, "2025-02-04 10:02:00")["positions"] == 0
    assert D(point(result, "2025-02-04 10:02:00")["nav"]) == D("9980")


@pytest.mark.parametrize("time,signal", [("09:30:00", "09:29:00"), ("13:00:00", "12:59:00")])
def test_opening_boundary_execution_requires_next_close_bar_not_boundary_quote(time, signal):
    rows = [row(signal=f"2025-02-03 {signal}", entry=f"2025-02-03 {time}")]
    data = quotes()
    assert ("SYN_A", stamp(f"2025-02-03 {time}")) not in data
    result = replay(rows, data)
    assert result["counts"]["accepted"] == result["counts"]["closed"] == 1
    close = stamp(f"2025-02-03 {time}") + timedelta(minutes=1)
    assert point(result, close.isoformat())["positions"] == 1


def compound_fixture():
    rows = [row("SYN_A", exit_at="2025-02-05 10:01:00"),
            row("SYN_B", signal="2025-02-04 10:00:00", entry="2025-02-04 10:01:00",
                exit_at="2025-02-05 10:02:00"),
            row("SYN_C", signal="2025-02-04 14:00:00", entry="2025-02-04 14:01:00",
                exit_at="2025-02-05 10:03:00")]
    data = quotes(("SYN_A", "SYN_B", "SYN_C"))
    data[("SYN_A", stamp("2025-02-03 15:00:00"))]["close"] = D("40")
    return rows, data


def test_day_budget_compounds_from_previous_1500_actual_held_value():
    rows, data = compound_fixture()
    result = replay(rows, data, weight="0.25")
    budgets = {r["day"]: r for r in result["budgets"]}
    assert D(budgets["20250203"]["previous_close_nav"]) == D("10000")
    assert D(budgets["20250203"]["total_cost_budget_per_entry"]) == D("2500")
    # 200 shares cost 2007; closing value is 200*40; 7993+8000=15993.
    assert D(budgets["20250204"]["previous_close_nav"]) == D("15993")
    assert D(budgets["20250204"]["total_cost_budget_per_entry"]) == D("3998.25")
    records = bycode(result)
    assert records["SYN_A"]["shares"] == 200
    assert records["SYN_B"]["shares"] == records["SYN_C"]["shares"] == 300
    assert D(records["SYN_B"]["day_budget"]) == D(records["SYN_C"]["day_budget"]) == D("3998.25")


def test_intraday_nav_cannot_rebudget_same_day_entries_or_rebalance_old_shares():
    rows, data = compound_fixture()
    altered = deepcopy(data)
    altered[("SYN_A", stamp("2025-02-04 09:31:00"))]["close"] = D("1")
    altered[("SYN_A", stamp("2025-02-04 14:00:00"))]["close"] = D("100")
    baseline, changed = replay(rows, data, weight="0.25"), replay(rows, altered, weight="0.25")
    for result in (baseline, changed):
        assert bycode(result)["SYN_A"]["shares"] == 200
        assert [(r["ts_code"], r["shares"], r["day_budget"], r["entry_cash"])
                for r in result["records"]] == [(r["ts_code"], r["shares"], r["day_budget"], r["entry_cash"])
                                                   for r in baseline["records"]]
        assert result["counts"]["accepted"] == 3
    assert D(point(changed, "2025-02-04 09:31:00")["market_value"]) == D("200")
    assert D(point(changed, "2025-02-04 14:00:00")["market_value"]) == D("23000")
    assert baseline["budgets"] == changed["budgets"]
    assert baseline["final_capital"] == changed["final_capital"]


@pytest.mark.parametrize("missing", ["2025-02-03 10:02:00", "2025-02-03 11:15:00",
                                      "2025-02-03 15:00:00", "2025-02-04 09:31:00"])
def test_missing_required_minute_or_daily_close_fails_instead_of_forward_fill(missing):
    data = quotes()
    del data[("SYN_A", stamp(missing))]
    with pytest.raises(ValueError, match="Missing quote"):
        replay([row()], data)


@pytest.mark.parametrize("changed", ["2025-02-03 11:15:00", "2025-02-03 15:00:00",
                                      "2025-02-04 10:02:00"])
def test_holding_factor_change_at_mark_close_or_sale_fails(changed):
    data = quotes()
    data[("SYN_A", stamp(changed))]["adj_factor"] = D("2")
    with pytest.raises(ValueError, match="factor change"):
        replay([row()], data)


@pytest.mark.parametrize("key,value,time", [("open", "0", "2025-02-03 10:02:00"),
    ("close", "NaN", "2025-02-03 11:15:00"), ("adj_factor", "Infinity", "2025-02-03 11:15:00")])
def test_invalid_required_quote_fields_fail(key, value, time):
    data = quotes()
    data[("SYN_A", stamp(time))][key] = D(value)
    with pytest.raises(ValueError, match="Invalid"):
        replay([row()], data)


@pytest.mark.parametrize("time,error", [("2025-02-03 10:02:00", "entry"),
                                        ("2025-02-04 10:02:00", "exit")])
def test_frozen_execution_open_mismatch_is_rejected(time, error):
    data = quotes()
    data[("SYN_A", stamp(time))]["open"] = D("10.01")
    with pytest.raises(ValueError, match=f"Frozen {error} quote"):
        replay([row()], data)


def test_missing_nonheld_stock_and_flat_account_prices_are_not_required():
    data = quotes()
    for key in list(data):
        if key[1] <= stamp("2025-02-03 10:01:00") or key[1] > stamp("2025-02-04 10:02:00"):
            del data[key]
    result = replay([row()], data)
    assert D(result["final_capital"]) == D("9980")
    assert all(D(x["market_value"]) == 0 for x in result["minute_nav"]
               if x["time"] >= "2025-02-04T10:02:00")


def test_rejected_trade_exit_never_creates_cash_or_requests_its_missing_quotes():
    rows = [row("SYN_A"), row("SYN_B", signal="2025-02-03 10:01:00",
                              entry="2025-02-03 10:02:00", exit_price="1000")]
    result = replay(rows, quotes(("SYN_A",)), weight="0.6")
    assert bycode(result)["SYN_B"]["status"] == "cash_rejected"
    assert result["counts"]["signals"] == 2
    assert result["counts"]["accepted"] == result["counts"]["closed"] == 1
    assert D(result["final_capital"]) == D("9977.50")
    assert not any(e["id"].startswith("SYN_B|") and e["event"] == "closed" for e in result["events"])


def test_known_unfilled_and_excluded_preserve_denominator_without_quotes():
    rows = [row(f"SYN_{i}", entry="", price="", exit_at="", exit_price="", buyable="false", status=s)
            for i, s in enumerate(("entry_at_up_limit", "entry_no_volume", "entry_gap_above_cap",
                                    "excluded_limit_regime"))]
    result = replay(rows, {})
    assert result["counts"]["signals"] == len(result["records"]) == 4
    assert result["counts"]["source_unfilled"] == 3
    assert result["counts"]["source_excluded"] == 1
    assert D(result["final_capital"]) == D("10000")


def test_unknown_entry_cannot_publish_complete_nav():
    unknown = row(entry="", price="", exit_at="", exit_price="", buyable="", status="entry_missing")
    with pytest.raises(ValueError, match="Unknown entry"):
        replay([unknown], {})


def test_unresolved_holding_cannot_be_silently_liquidated():
    pending = row(exit_at="", exit_price="", status="holding_unresolved")
    with pytest.raises(ValueError, match="Unresolved holding"):
        replay([pending], quotes())


def test_same_clock_sale_proceeds_cannot_fund_new_buy_but_next_clock_can():
    first = row("SYN_A", exit_price="20")
    second = row("SYN_B", signal="2025-02-04 10:00:00", entry="2025-02-04 10:01:00",
                 exit_at="2025-02-05 10:01:00")
    data = quotes(("SYN_A", "SYN_B"))
    data[("SYN_A", stamp("2025-02-04 10:02:00"))]["open"] = D("20")
    same = replay([first, second], data, weight="0.6")
    assert bycode(same)["SYN_B"]["status"] == "cash_same_time_reuse_blocked"
    second[f"{PREFIX}_entry_execution_at"] = "2025-02-04 10:02:00"
    next_clock = replay([first, second], data, weight="0.6")
    assert bycode(next_clock)["SYN_B"]["status"] == "closed"
    assert next_clock["counts"]["accepted"] == 2


def test_multiple_same_clock_orders_share_only_remaining_pre_sale_cash():
    rows = [row("SYN_A", exit_price="20")]
    rows += [row(code, signal="2025-02-04 10:00:00", entry="2025-02-04 10:01:00",
                 exit_at="2025-02-05 10:01:00") for code in ("SYN_B", "SYN_C", "SYN_D")]
    data = quotes(("SYN_A", "SYN_B", "SYN_C", "SYN_D"))
    data[("SYN_A", stamp("2025-02-04 10:02:00"))]["open"] = D("20")
    result = replay(rows, data, weight="0.4")
    records = bycode(result)
    assert records["SYN_B"]["status"] == records["SYN_C"]["status"] == "closed"
    assert records["SYN_D"]["status"] == "cash_same_time_reuse_blocked"
    assert result["counts"]["signals"] == 4
    assert result["counts"]["accepted"] == 3
    entries = [e for e in result["events"] if e["time"] == "2025-02-04T10:01:00" and e["event"] == "accepted"]
    assert [D(e["remaining_timestamp_start_cash"]) for e in entries] == [D("3984"), D("976")]


def test_same_stock_is_cleared_before_same_clock_reentry_using_old_cash():
    rows = [row(), row(signal="2025-02-04 10:00:00", entry="2025-02-04 10:01:00",
                       exit_at="2025-02-05 10:01:00")]
    result = replay(rows, quotes(), weight="0.4")
    assert result["counts"]["accepted"] == result["counts"]["closed"] == 2
    assert result["max_positions"] == 1


def test_same_clock_sorting_is_input_permutation_independent():
    rows = [row("SYN_Z", signal="2025-02-03 09:59:00"), row("SYN_B"), row("SYN_A")]
    data = quotes(("SYN_Z", "SYN_B", "SYN_A"))
    baseline = replay(rows, data, weight="0.6")
    for shuffled in permutations(rows):
        assert replay(list(shuffled), data, weight="0.6") == baseline
    assert [r["ts_code"] for r in baseline["records"]] == ["SYN_Z", "SYN_A", "SYN_B"]
    assert [r["status"] for r in baseline["records"]] == ["closed", "cash_rejected", "cash_rejected"]


def test_cash_and_each_nav_reconcile_and_no_leverage_occurs():
    rows, data = compound_fixture()
    result = replay(rows, data, weight="0.25")
    records = result["records"]
    assert all(r["shares"] % 100 == 0 for r in records)
    assert all(D(r["entry_cash"]) <= D(r["day_budget"]) for r in records)
    purchases = sum((D(r["entry_cash"]) for r in records), D("0"))
    sales = sum((D(r["exit_cash"]) for r in records), D("0"))
    pnls = sum((D(r["pnl"]) for r in records), D("0"))
    assert D(result["final_capital"]) == D("10000") - purchases + sales == D("10000") + pnls
    for p in result["minute_nav"]:
        assert D(p["cash"]) >= 0
        assert D(p["nav"]) == D(p["cash"]) + D(p["market_value"])
    assert result["counts"]["signals"] == len(records) == 3
    assert result["max_positions"] == result["max_same_theme_positions"] == 3


@pytest.mark.parametrize("time", ["11:30:00", "12:00:00", "15:00:00"])
def test_no_execution_at_lunch_or_final_close_boundary(time):
    attempted = row(signal="2025-02-03 10:00:00", entry=f"2025-02-03 {time}")
    with pytest.raises(ValueError, match="continuous-session open grid"):
        replay([attempted], quotes())


def test_execution_must_be_an_exact_minute_grid_boundary():
    attempted = row(signal="2025-02-03 09:29:30", entry="2025-02-03 09:30:30")
    data = quotes()
    data[("SYN_A", stamp("2025-02-03 09:31:30"))] = {
        "open": D("10"), "close": D("10"), "adj_factor": D("1")}
    with pytest.raises(ValueError, match="continuous-session open grid"):
        replay([attempted], data)


def test_duplicate_trade_identifier_is_rejected_before_valuation():
    trade = normalize([row()], PREFIX)[0]
    with pytest.raises(ValueError, match="Duplicate trade"):
        subject.replay_compound([trade, deepcopy(trade)], CALENDAR, {}, D("0.5"))


@pytest.mark.parametrize("remove", [False, True])
def test_sale_requires_only_next_bar_open_not_its_later_close(remove):
    data = quotes()
    next_bar = data[("SYN_A", stamp("2025-02-04 10:02:00"))]
    if remove:
        del next_bar["close"]
    else:
        next_bar["close"] = D("NaN")
    result = replay([row()], data)
    assert D(result["final_capital"]) == D("9980")
    assert point(result, "2025-02-04 10:02:00")["positions"] == 0
    assert D(point(result, "2025-02-04 10:02:00")["nav"]) == D("9980")


@pytest.mark.parametrize("remove", [False, True])
def test_held_minute_mark_requires_close_and_factor_but_not_open(remove):
    data = quotes()
    held_bar = data[("SYN_A", stamp("2025-02-03 11:15:00"))]
    if remove:
        del held_bar["open"]
    else:
        held_bar["open"] = D("NaN")
    result = replay([row()], data)
    assert D(point(result, "2025-02-03 11:15:00")["nav"]) == D("9991")
    assert D(result["final_capital"]) == D("9980")


def test_unused_stock_or_flat_account_quote_nan_does_not_prevalidate_future():
    data = quotes()
    invalid = {key: D("NaN") for key in ("open", "close", "adj_factor")}
    data[("SYN_UNUSED", stamp("2025-02-03 10:02:00"))] = invalid.copy()
    data[("SYN_A", stamp("2025-02-03 09:31:00"))] = invalid.copy()
    data[("SYN_A", stamp("2025-02-04 10:03:00"))] = invalid.copy()
    result = replay([row()], data)
    assert D(result["final_capital"]) == D("9980")


def test_bought_bar_missing_close_fails_only_at_later_close_valuation(monkeypatch):
    data = quotes()
    target = stamp("2025-02-03 10:02:00")
    data[("SYN_A", target)]["close"] = D("NaN")
    original = subject._quote
    calls = []

    def observe(data, code, time, factor=None, price_field="close"):
        try:
            value = original(data, code, time, factor, price_field)
        except ValueError:
            calls.append((time, price_field, "rejected"))
            raise
        calls.append((time, price_field, "accepted"))
        return value

    monkeypatch.setattr(subject, "_quote", observe)
    with pytest.raises(ValueError, match="Invalid close"):
        replay([row()], data)
    assert calls == [(target, "open", "accepted"), (target, "close", "rejected")]


def test_loader_preserves_none_and_duplicate_keys_until_required(tmp_path):
    import pandas as pd

    path = tmp_path / "synthetic_quotes.parquet"
    pd.DataFrame([
        {"ts_code": "SYN_MISSING", "datetime": "2025-02-03 10:02:00", "open": None, "close": None, "adj_factor": None},
        {"ts_code": "SYN_DUP", "datetime": "2025-02-03 10:02:00", "open": 10, "close": 10, "adj_factor": 1},
        {"ts_code": "SYN_DUP", "datetime": "2025-02-03 10:02:00", "open": 10, "close": 10, "adj_factor": 1},
    ]).to_parquet(path, index=False)
    loaded = subject.load_quotes(path)
    assert all(loaded[("SYN_MISSING", stamp("2025-02-03 10:02:00"))][key].is_nan()
               for key in ("open", "close", "adj_factor"))
    assert loaded[("SYN_DUP", stamp("2025-02-03 10:02:00"))]["duplicate"] is True
    # Neither source is ever held, so neither affects the cash-only account.
    assert D(replay([], loaded)["final_capital"]) == D("10000")
    with pytest.raises(ValueError, match="Duplicate required quote"):
        subject._quote(loaded, "SYN_DUP", stamp("2025-02-03 10:02:00"), price_field="open")
    with pytest.raises(ValueError, match="Invalid open"):
        subject._quote(loaded, "SYN_MISSING", stamp("2025-02-03 10:02:00"), price_field="open")


def test_required_duplicate_quote_fails_only_when_position_needs_it():
    data = quotes()
    data[("SYN_UNUSED", stamp("2025-02-03 10:02:00"))] = {"duplicate": True}
    assert D(replay([row()], data)["final_capital"]) == D("9980")
    data[("SYN_A", stamp("2025-02-03 11:15:00"))] = {"duplicate": True}
    with pytest.raises(ValueError, match="Duplicate required quote"):
        replay([row()], data)
