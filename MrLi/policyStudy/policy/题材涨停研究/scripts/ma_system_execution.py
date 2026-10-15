"""Isolated, conditional paper replay. No theme authorization or broker orders.

Actual inventory, cash and T+1 eligibility are separate from logical targets.
Signal-close -> next scheduled slot open is an OHLC execution approximation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd

from ma_system_pullback import Rules, TargetState, target_step


@dataclass(frozen=True)
class Costs:
    initial_cash: float = 300000.
    initial_budget_fraction: float = .9
    commission: float = .0003
    minimum_commission: float = 5.
    transfer: float = .00001
    stamp_sell: float = .0005
    slippage: float = .0005


def fee(qty, price, sell, costs):
    value = qty * price
    return max(costs.minimum_commission, value * costs.commission) + value * (
        costs.transfer + (costs.stamp_sell if sell else 0))


def order_quantity(requested, code, *, sell=False, total_held=0, eligible=0):
    """STAR: >=200, increments 1; ordinary buys/partial sells: increments 100.

    An odd remainder may be sold only when disposing of the entire remainder.
    STAR's below-200 exception also requires the entire held remainder.
    """
    q = max(0, math.floor(requested + 1e-8))
    if sell:
        q = min(q, eligible)
        if q == total_held and q > 0:
            return q
    if code.startswith("688"):
        return q if q >= 200 else 0
    return q // 100 * 100


def enrich_exit_features(frame, rules):
    f = frame.copy()
    f["outgoing10_next"] = f.p_close.shift(9)
    f["outgoing20_next"] = f.p_close.shift(19)
    touch = (f.valid & f.p_low.between(f.ma30 * (1 - rules.retest_tolerance_pct / 100),
                                     f.ma30 * (1 + rules.retest_tolerance_pct / 100)))
    f["touch30_recent"] = touch.rolling(rules.rebuy_wait, min_periods=1).max().eq(1)
    return f


def replay(frame, limits, signal_time, code, rules=Rules(), costs=Costs(), *, allow_rebuy=True):
    f = enrich_exit_features(frame, rules)
    signal_time = pd.Timestamp(signal_time)
    indices = np.flatnonzero(f.datetime.eq(signal_time))
    if len(indices) != 1:
        raise ValueError("Signal must have one scheduled slot")
    start = int(indices[0]) + 1
    lim = limits.copy()
    lim["trade_date"] = lim.trade_date.astype(str)
    lim = lim.drop_duplicates("trade_date").set_index("trade_date")
    cash = costs.initial_cash
    lots, ledger, curve = [], [], []
    state = TargetState()
    original_qty = 0
    buy_cash = sell_cash = fees = initial_outlay = 0.
    last_price = last_factor = None
    issues = set()
    reasons = ["initial_entry"]
    prior_stamp = signal_time
    confirmation_at = None

    def record(stamp, kind, **data):
        ledger.append(dict(time=str(stamp), bar_end=str(f.iloc[i].datetime), kind=kind, **data))

    if start >= len(f):
        return dict(status="entry_not_observed", signal_time=str(signal_time)), [], []
    for i in range(start, len(f)):
        row = f.iloc[i]
        stamp = row.datetime
        physical_open = stamp - pd.Timedelta(minutes=15)
        day = stamp.strftime("%Y%m%d")
        held = sum(l["qty"] for l in lots)
        target_qty = math.floor(original_qty * state.target + 1e-8) if original_qty else None
        difference = target_qty - held if target_qty is not None else 1
        if difference:
            sell = difference < 0
            wanted = -difference if sell else difference
            if not row.valid or row.vol <= 0 or day not in lim.index:
                reason = "missing_or_zero_volume" if not row.valid or row.vol <= 0 else "missing_limit_price"
                record(physical_open, "blocked", side="sell" if sell else "buy", reason=reason,
                       decision_at=str(prior_stamp), held=held, target=target_qty)
                if not sell:
                    if not original_qty:
                        return dict(status="entry_cancelled", reason=reason, signal_time=str(signal_time)), ledger, curve
                    # Rebuys require a new observed restore event after a missing slot.
                    issues.add("rebuy_cancelled_missing_slot")
                    if "restore_a_quarter_original" in reasons:
                        state.tranche_a = False
                        state.a_sale_age = state.age
                    if "restore_b_half_original" in reasons:
                        state.tranche_b = False
                        state.b_sale_age = state.age
            else:
                upper, lower = float(lim.loc[day, "up_limit"]), float(lim.loc[day, "down_limit"])
                blocked = (row.open <= lower + .005) if sell else (row.open >= upper - .005)
                if blocked:
                    reason = "down_limit_open" if sell else "up_limit_open"
                    record(physical_open, "blocked", side="sell" if sell else "buy", reason=reason,
                           decision_at=str(prior_stamp), held=held, target=target_qty)
                    if not original_qty:
                        return dict(status="entry_cancelled", reason=reason, signal_time=str(signal_time)), ledger, curve
                else:
                    price = max(lower, row.open * (1 - costs.slippage)) if sell else min(upper, row.open * (1 + costs.slippage))
                    eligible = sum(l["qty"] for l in lots if l["day"] < day)
                    if not original_qty:
                        wanted = math.floor(costs.initial_cash * costs.initial_budget_fraction / price)
                    q = order_quantity(wanted, code, sell=sell, total_held=held, eligible=eligible)
                    if not sell:
                        step = 1 if code.startswith("688") else 100
                        while q and q * price + fee(q, price, False, costs) > cash + 1e-8:
                            q -= step
                        q = order_quantity(q, code)
                    if q:
                        charge = fee(q, price, sell, costs)
                        fees += charge
                        if sell:
                            cash += q * price - charge
                            sell_cash += q * price - charge
                            left = q
                            for lot in lots:
                                if lot["day"] < day:
                                    take = min(lot["qty"], left)
                                    lot["qty"] -= take
                                    left -= take
                            assert left == 0
                            lots = [l for l in lots if l["qty"]]
                        else:
                            cash -= q * price + charge
                            buy_cash += q * price + charge
                            lots.append(dict(qty=q, day=day, price=price))
                            if not original_qty:
                                original_qty = q
                                initial_outlay = q * price + charge
                        record(physical_open, "fill", side="sell" if sell else "buy", qty=q,
                               price=float(price), fee=float(charge), cash=float(cash),
                               held_after=sum(l["qty"] for l in lots), target=target_qty,
                               decision_at=str(prior_stamp), reasons=reasons, eligible_before=eligible)
                    else:
                        reason = "t_plus_one" if sell and eligible == 0 else "order_size_or_cash"
                        record(physical_open, "blocked", side="sell" if sell else "buy", reason=reason,
                               held=held, eligible=eligible, wanted=wanted, target=target_qty,
                               decision_at=str(prior_stamp))
                        if not original_qty:
                            return dict(status="entry_cancelled", reason=reason, signal_time=str(signal_time)), ledger, curve
        held = sum(l["qty"] for l in lots)
        if row.valid:
            if held and last_factor is not None and not np.isclose(row.adj_factor, last_factor, rtol=1e-8):
                issues.add("corporate_action_cash_unmodeled")
            last_factor = float(row.adj_factor)
            last_price = float(row.close)
        elif held:
            issues.add("held_data_gap")
        equity = cash + held * last_price if last_price is not None else cash
        curve.append(dict(time=str(stamp), cash=cash, qty=held, raw_mark=last_price,
                          equity=equity, target=state.target, mark_valid=bool(row.valid)))
        assert cash >= -1e-6 and held >= 0
        assert abs(cash - (costs.initial_cash - buy_cash + sell_cash)) < 1e-5
        if state.closed and held == 0:
            break
        previously_confirmed = state.confirmed
        events = target_step(state, row, rules, theme_retreat=not allow_rebuy)
        events = [e for e in events if e != "theme_retreat_freeze_restores"]
        if state.confirmed and not previously_confirmed:
            confirmation_at = str(stamp)
            record(stamp, "confirmation", age=state.age)
        if events:
            record(stamp, "decision", events=events, target=state.target, held=held, age=state.age)
            reasons = events
        prior_stamp = stamp
    held = sum(l["qty"] for l in lots)
    summary = dict(status="closed" if held == 0 and state.closed else "censored",
                   signal_time=str(signal_time), code=code, conditional_only=True,
                   theme_gate_verified=False, original_qty=original_qty, held_at_end=held,
                   expectation_confirmed=state.confirmed, confirmation_at=confirmation_at,
                   last_time=curve[-1]["time"] if curve else None,
                   cash=cash, ending_equity=equity, fees=fees, initial_outlay=initial_outlay,
                   account_return_pct=100 * (equity / costs.initial_cash - 1),
                   return_on_initial_outlay_pct=100 * (equity - costs.initial_cash) / initial_outlay,
                   fills=sum(e["kind"] == "fill" for e in ledger),
                   sells=sum(e["kind"] == "fill" and e["side"] == "sell" for e in ledger),
                   rebuys=max(0, sum(e["kind"] == "fill" and e["side"] == "buy" for e in ledger) - 1),
                   issues=sorted(issues), return_accounting_verified="corporate_action_cash_unmodeled" not in issues,
                   costs=asdict(costs), allow_rebuy=allow_rebuy)
    return summary, ledger, curve
