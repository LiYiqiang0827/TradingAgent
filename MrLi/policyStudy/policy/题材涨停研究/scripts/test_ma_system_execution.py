from dataclasses import replace
import pandas as pd
import pytest

from ma_system_execution import Costs, order_quantity, replay
from ma_system_pullback import Rules
from study_ma250_reclaim import TIMES


def packet():
    stamps = pd.to_datetime([f"{day} {time}" for day in ("20260601", "20260602") for time in TIMES])
    f = pd.DataFrame(dict(datetime=stamps, valid=True, open=10., high=10.5, low=9.5,
                         close=10., vol=100000., adj_factor=1., p_close=10., p_low=9.5,
                         ma5=9.9, ma10=9.8, ma20=9.7, ma30=9.6, ma120=9.,
                         rise30=3, expected=True))
    limits = pd.DataFrame(dict(trade_date=["20260601", "20260602"], up_limit=11., down_limit=9.))
    return f, limits


def test_board_minimums_and_odd_remainder_exception():
    assert order_quantity(125, "688627.SH", sell=True, total_held=500, eligible=500) == 0
    assert order_quantity(125, "688627.SH", sell=True, total_held=125, eligible=125) == 125
    assert order_quantity(201, "688627.SH") == 201
    assert order_quantity(201, "000725.SZ") == 200
    assert order_quantity(99, "000725.SZ", sell=True, total_held=99, eligible=99) == 99


def test_full_exit_persists_through_t_plus_one_and_conserves_cash():
    f, limits = packet()
    f.loc[1:, "ma120"] = 10.1
    s, ledger, curve = replay(f, limits, f.datetime.iloc[0], "000725.SZ")
    fills = [e for e in ledger if e["kind"] == "fill"]
    assert len(fills) == 2 and s["status"] == "closed"
    assert fills[0]["time"].startswith("2026-06-01")
    assert fills[1]["time"] == "2026-06-02 09:30:00"
    assert fills[0]["qty"] == fills[1]["qty"]
    assert any(e.get("reason") == "t_plus_one" for e in ledger)
    expected = Costs().initial_cash - fills[0]["qty"] * fills[0]["price"] - fills[0]["fee"] + fills[1]["qty"] * fills[1]["price"] - fills[1]["fee"]
    assert s["cash"] == pytest.approx(expected)


def test_unfilled_reduction_restoration_does_not_create_phantom_rebuy():
    f, limits = packet()
    warmup = f.iloc[:16].copy()
    warmup["datetime"] -= pd.Timedelta(days=3)
    f = pd.concat([warmup, f], ignore_index=True)
    # Confirm at first held close; coil and MA20 break next bar; recover following bar.
    f.loc[20, ["ma5", "ma10", "ma20", "ma30", "p_close"]] = [10.03, 10.02, 10.01, 9.5, 10.]
    f.loc[21, "p_low"] = 9.6
    f.loc[21, ["p_close", "close"]] = 10.2
    s, ledger, _ = replay(f, limits, f.datetime.iloc[16], "000725.SZ")
    assert any("reduce_a_quarter_original" in e.get("events", []) for e in ledger)
    assert any("restore_a_quarter_original" in e.get("events", []) for e in ledger)
    assert s["rebuys"] == 0 and s["sells"] == 0


def test_missing_next_slot_cancels_entry_and_down_limit_delays_exit():
    f, limits = packet()
    f.loc[1, "valid"] = False
    s, _, _ = replay(f, limits, f.datetime.iloc[0], "000725.SZ")
    assert s["status"] == "entry_cancelled"
    f, limits = packet()
    f.loc[1:, "ma120"] = 10.1
    f.loc[16, "open"] = 9.
    s, ledger, _ = replay(f, limits, f.datetime.iloc[0], "000725.SZ")
    sells = [e for e in ledger if e["kind"] == "fill" and e["side"] == "sell"]
    assert sells[0]["time"] == "2026-06-02 09:45:00"
    assert any(e.get("reason") == "down_limit_open" for e in ledger)


def test_held_factor_change_marks_cash_accounting_unverified():
    f, limits = packet()
    f.loc[16:, "adj_factor"] = 1.2
    s, _, _ = replay(f, limits, f.datetime.iloc[0], "000725.SZ")
    assert not s["return_accounting_verified"]
    assert "corporate_action_cash_unmodeled" in s["issues"]
