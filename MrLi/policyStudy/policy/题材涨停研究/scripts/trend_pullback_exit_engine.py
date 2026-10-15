"""Causal daily-trend / 15-minute pullback portfolio research engine.

This module submits no broker orders. Completed-bar decisions execute at the
next bar's physical open (the end label minus fifteen minutes). Missing buy/add
slots cancel that order; missing sell slots retain an explicitly delayed order.
fill is a conservative OHLC proxy, not proof of an executable historical fill.
All lots obey T+1; a stop therefore does not guarantee a three-percent loss.
Prices prefixed ``p_`` are the caller's causal adjustment scale, with
``p_price == raw_price * scale``. Corporate-action accounting is deliberately
not invented here: a held scale change is surfaced as an acceptance issue.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExecutionRules:
    commission: float = .0003
    min_commission: float = 5.0
    transfer: float = .00001
    stamp: float = .0005
    slippage: float = .0005
    lot_size: int = 100
    account_risk_pct: float = .005
    max_initial_stop_pct: float = .03
    trial_fraction: float = .5
    no_progress_bars: int = 16
    add_min_bars: int = 3


@dataclass
class Position:
    candidate: dict
    entry_index: int
    entry_ordinal: int
    entry_time: pd.Timestamp
    entry_raw: float
    entry_p: float
    entry_scale: float
    stop_p: float
    structural_stop_p: float
    entry_atr: float
    target_budget: float
    lots: list = field(default_factory=list)
    pending: dict | None = None
    flags: set = field(default_factory=set)
    stage_counts: Counter = field(default_factory=Counter)
    reclaim_bars: int = 0
    reclaim_armed: bool = False
    last_trim_ordinal: int = -100000
    added: bool = False
    add_attempted: bool = False
    buys: int = 0
    sells: int = 0
    buy_cash: float = 0.
    sell_cash: float = 0.
    fees: float = 0.
    peak_p: float = 0.
    peak_close_p: float = 0.
    last_p: float = 0.
    last_raw: float = 0.
    last_ma20: float = float("nan")
    last_ma30: float = float("nan")
    last_ma60: float = float("nan")
    recent_closes: list = field(default_factory=list)
    last_exit_time: pd.Timestamp | None = None
    last_exit_reason: str | None = None
    issue_codes: set = field(default_factory=set)
    sell_delay_bars: int = 0

    @property
    def qty(self):
        return sum(lot["qty"] for lot in self.lots)


def _number(value, fallback=float("nan")):
    try:
        answer = float(value)
        return answer if math.isfinite(answer) else fallback
    except (ValueError, TypeError):
        return fallback


def _physical_open(stamp):
    return pd.Timestamp(stamp) - pd.Timedelta(minutes=15)


def _fee(qty, price, sell, rules):
    amount = qty * price
    return max(rules.min_commission, amount * rules.commission) + amount * (
        rules.transfer + (rules.stamp if sell else 0.))


def _quote_shares(budget, price, cash, rules):
    qty = int(min(budget, cash) / price // rules.lot_size) * rules.lot_size
    while qty and qty * price + _fee(qty, price, False, rules) > cash + 1e-8:
        qty -= rules.lot_size
    return max(0, qty)


def _sell_fraction(qty, fraction, rules):
    if fraction >= 1.:
        return qty
    # A single lot cannot be fractionally sold. A requested trim exits that
    # lot; this is recorded explicitly in the order's requested fraction.
    return min(qty, max(rules.lot_size,
                       int(qty * fraction // rules.lot_size) * rules.lot_size))


def _normal_prob(probability_fn, row, state):
    if probability_fn is None:
        return dict(p_fail=None, p_extend=None, n=0, supported=False)
    result = dict(probability_fn(row.copy(), state) or {})
    answer = {"n": int(result.get("n", 0) or 0),
              "supported": bool(result.get("supported", False))}
    for key in ("p_fail", "p_extend"):
        value = _number(result.get(key))
        answer[key] = min(1., max(0., value)) if math.isfinite(value) else None
    answer["supported"] &= answer["p_fail"] is not None and answer["p_extend"] is not None
    return answer


def _state_view(pos, ordinal, row):
    return dict(entry_p=pos.entry_p, stop_p=pos.stop_p,
                held_bars=ordinal-pos.entry_ordinal,
                mfe_p=pos.peak_p, mfe_return=pos.peak_p/pos.entry_p-1,
                peak_p=pos.peak_p, peak_close_p=pos.peak_close_p,
                entry_atr=pos.entry_atr, qty=pos.qty, added=pos.added,
                flags=sorted(pos.flags), entry_index=pos.entry_index,
                return_now=_number(row.get("p_close"))/pos.entry_p-1,
                risk_fraction=1-pos.stop_p/pos.entry_p,
                realized_cash=pos.sell_cash, invested_cash=pos.buy_cash)


def _exit_decision(pos, row, ordinal, probability, policy, rules):
    """Return one action from present and past state, never a future target."""
    close = float(row["p_close"])
    age = ordinal-pos.entry_ordinal
    ma20 = _number(row.get("ma20")); ma30 = _number(row.get("ma30"))
    ma60 = _number(row.get("ma60"))
    weak20 = math.isfinite(ma20) and close < ma20
    ma20_down = math.isfinite(pos.last_ma20) and ma20 <= pos.last_ma20
    ma30_down = math.isfinite(pos.last_ma30) and ma30 < pos.last_ma30-1e-10
    vol = _number(row.get("volume_multiple8"), 1.)
    supported = probability["supported"]
    fail = probability["p_fail"] if supported else None
    extend = probability["p_extend"] if supported else None
    trim_ready = ordinal-pos.last_trim_ordinal >= 4

    if close < pos.stop_p:
        return "sell", 1., "frozen_stop_failed", None
    if age >= rules.no_progress_bars and close <= pos.entry_p and weak20:
        return "sell", 1., "sixteen_bars_no_progress_and_ma20_lost", None
    if policy == "ma30":
        if math.isfinite(ma30) and close < ma30:
            return "sell", 1., "ma30_control_exit", None
        return None

    # The caller may keep a small runner when continuation is supported.
    # This does not excuse a base failure or a high failure probability.
    profitable = close >= pos.entry_p + 2*pos.entry_atr
    keep_runner = policy == "adaptive" and profitable and supported and extend >= .60
    if math.isfinite(ma30) and close < ma30 and ma30_down:
        if keep_runner and fail <= .5:
            if math.isfinite(ma60) and close < ma60:
                return "sell", 1., "runner_ma60_failed", None
        else:
            return "sell", 1., "falling_ma30_failed", None
    if "runner" in pos.flags and math.isfinite(ma60) and close < ma60:
        return "sell", 1., "runner_ma60_failed", None
    if keep_runner and close < ma30:
        pos.flags.add("runner")
    if trim_ready and policy == "adaptive" and supported and fail >= .65 and "prob_fail_trim" not in pos.flags:
        return "sell", .5, "supported_failure_probability", "prob_fail_trim"

    # Acceleration means an observed four-bar gain before the present bear
    # candle, not a future highest price. It remains a fallible exhaustion cue.
    prior4 = pos.recent_closes[-5:] if len(pos.recent_closes) >= 5 else []
    accelerated = bool(prior4 and prior4[-1]/prior4[0]-1 > .03)
    body = close/_number(row.get("p_open"), close)-1
    if trim_ready and accelerated and vol >= 2. and body <= -.01 and "acceleration_trim" not in pos.flags:
        return "sell", 1/3, "acceleration_bear_volume_exhaustion", "acceleration_trim"
    if trim_ready and weak20 and (ma20_down or vol >= 1.5) and "ma20_trim" not in pos.flags:
        return "sell", 1/3, "first_weak_ma20_loss", "ma20_trim"
    if trim_ready and policy == "adaptive" and supported and extend <= .35 and weak20 and (
            ma20_down or close < pos.last_p) and "low_extend_trim" not in pos.flags:
        return "sell", .5, "weakness_and_low_extension_probability", "low_extend_trim"

    # Add only once, after an actual higher close. The high being broken was
    # observed on earlier completed bars; current high is excluded here.
    if not pos.add_attempted and pos.sells == 0 and age >= rules.add_min_bars and (
            close > pos.peak_p and math.isfinite(ma20) and close > ma20 and
            ma20 > pos.last_ma20):
        if policy == "structure" or (supported and extend >= .60):
            return "add", .5, "confirmed_extension_add", "add"
    return None


def replay(frames: dict, candidates: list, limits: dict,
           probability_fn: Callable | None = None, policy="adaptive",
           initial_cash=300000., max_name_pct=.20, max_total_pct=.80,
           rules: ExecutionRules | None = None,
           entry_eligibility_fn: Callable | None = None):
    """Replay all names on a common timestamp clock; return JSON-ready records.

    Required frame fields are datetime, valid, raw OHLC, p_OHLC, scale.
    MA20/30 and optional other features drive the exit/add rules. Each candidate
    contains id, ts_code, integer frame index, stop in p-price and entry_score.
    Each accepted buy begins a new trade. Overlapping candidates are counted as
    unavailable opportunities, not silently removed. No position is forcibly
    liquidated at study end: unrealized holdings remain explicitly censored.
    Optional ``entry_eligibility_fn(ts_code, physical_fill_time)`` validates new
    buys and confirmation additions against the actual current monthly pool.
    It never blocks sales of an existing position. None preserves the earlier
    fixed-pool engine behavior; rejected candidates remain in the denominator.
    """
    if policy not in ("adaptive", "structure", "ma30"):
        raise ValueError(f"Unknown policy: {policy}")
    if not 0 < max_name_pct <= max_total_pct <= 1:
        raise ValueError("Require 0 < name cap <= total cap <= 1")
    rules = rules or ExecutionRules()
    cash = float(initial_cash)
    positions = {}; waiting = {}; archive = []; ledger = []; decisions = []
    issues = []; curve = []; last_mark = {}; outcomes = Counter()
    events = defaultdict(list); row_lookup = {}; ordinals = {}
    for code, frame in frames.items():
        if not frame.index.is_unique:
            raise ValueError(f"Duplicate index in {code}")
        previous = None
        for ordinal, (index, original) in enumerate(frame.sort_index().iterrows()):
            row = original.to_dict(); stamp = pd.Timestamp(row["datetime"])
            if previous is not None and stamp <= previous:
                raise ValueError(f"Unordered or duplicated timestamp in {code}")
            previous = stamp; row["datetime"] = stamp
            row_lookup[(code, int(index))] = row; ordinals[(code, int(index))] = ordinal
            events[stamp].append((code, int(index), ordinal, row))
    candidate_map = defaultdict(list)
    seen_ids = set()
    for candidate in candidates:
        candidate = dict(candidate)
        key = (candidate["ts_code"], int(candidate["index"]))
        if candidate["id"] in seen_ids:
            raise ValueError(f"Duplicate candidate id: {candidate['id']}")
        seen_ids.add(candidate["id"])
        if key not in row_lookup:
            issues.append(dict(id=candidate["id"], code="candidate_row_missing")); outcomes["missing_row"] += 1
            continue
        stamp = row_lookup[key]["datetime"]
        stated = candidate.get("signal_time")
        if stated is not None and pd.Timestamp(stated) != stamp:
            raise ValueError(f"Candidate time differs from referenced bar: {candidate['id']}")
        candidate["signal_time"] = stamp.isoformat()
        if not bool(row_lookup[key].get("valid", False)):
            issues.append(dict(id=candidate["id"], code="candidate_bar_invalid"))
            outcomes["invalid_signal"] += 1
            continue
        candidate_map[key].append(candidate)

    def portfolio_equity():
        return cash + sum(pos.qty*last_mark.get(code, pos.last_raw)
                          for code, pos in positions.items())

    def exposure():
        return sum(pos.qty*last_mark.get(code, pos.last_raw)
                   for code, pos in positions.items())

    def note_issue(pos, code, stamp, **extra):
        if code not in pos.issue_codes:
            issues.append(dict(id=pos.candidate["id"], ts_code=pos.candidate["ts_code"],
                               code=code, datetime=stamp.isoformat(), **extra))
            pos.issue_codes.add(code)

    def quote_cap(code, price, requested):
        equity = portfolio_equity()
        existing = positions.get(code)
        held_value = existing.qty*price if existing else 0.
        # Opening-price exposure replaces that name's stale mark for this cap.
        exp = exposure() - (existing.qty*last_mark.get(code, existing.last_raw)
                            if existing else 0.) + held_value
        budget = min(requested, max(0., equity*max_name_pct-held_value),
                     max(0., equity*max_total_pct-exp), cash)
        qty = _quote_shares(budget, price, cash, rules)
        # Fees reduce equity at execution; include that reduction in the cap
        # rather than allowing fees to push a fully allocated account above it.
        while qty:
            equity_after_fee = equity-_fee(qty, price, False, rules)
            if (held_value+qty*price <= equity_after_fee*max_name_pct+1e-8 and
                    exp+qty*price <= equity_after_fee*max_total_pct+1e-8):
                break
            qty -= rules.lot_size
        return max(0, qty)

    def trade_record(pos, closed):
        mark = 0. if closed else pos.qty*pos.last_raw
        pnl = pos.sell_cash + mark - pos.buy_cash
        return dict(id=pos.candidate["id"], ts_code=pos.candidate["ts_code"],
                    signal_time=pos.candidate["signal_time"],
                    entry_time=pos.entry_time.isoformat(), entry_index=pos.entry_index,
                    entry_price=pos.entry_raw, entry_p=pos.entry_p,
                    exit_time=pos.last_exit_time.isoformat() if closed else None,
                    exit_reason=pos.last_exit_reason if closed else "study_end_censored",
                    closed=closed, buys=pos.buys, sells=pos.sells, shares_remaining=pos.qty,
                    buy_cash=pos.buy_cash, sell_cash=pos.sell_cash, marked_value=mark,
                    pnl=pnl, net_return=pnl/pos.buy_cash if pos.buy_cash else 0.,
                    fees=pos.fees, mfe_return=pos.peak_p/pos.entry_p-1,
                    stop_p=pos.stop_p, structural_stop_p=pos.structural_stop_p,
                    realized_return=(pos.sell_cash-pos.buy_cash)/pos.buy_cash if closed else None,
                    stop_distance=1-pos.stop_p/pos.entry_p, added=pos.added,
                    sell_delay_bars=pos.sell_delay_bars, issue_codes=sorted(pos.issue_codes),
                    pending_exit_reason=pos.pending.get("reason") if pos.pending else None,
                    pending_exit_qty=pos.pending.get("qty", 0) if pos.pending else 0,
                    risk_acceptance_failed=bool(pnl/pos.buy_cash < -.10),
                    entry_score=pos.candidate.get("entry_score"))

    def fill_sell(code, pos, row, index, ordinal, stamp):
        nonlocal cash
        order = pos.pending
        if not order or order["side"] != "sell":
            return
        day = stamp.strftime("%Y%m%d")
        available = sum(lot["qty"] for lot in pos.lots if lot["day"] < day)
        qty = min(available, order["qty"])
        cap = limits.get((code, day), {})
        down = _number(cap.get("down_limit"))
        if qty <= 0 or (math.isfinite(down) and row["open"] <= down+.005):
            pos.sell_delay_bars += 1
            note_issue(pos, "t1_delayed" if qty <= 0 else "down_limit_open_delayed", stamp)
            return
        price = float(row["open"])*(1-rules.slippage)
        fee = _fee(qty, price, True, rules); proceeds = qty*price-fee
        cash += proceeds; pos.sell_cash += proceeds; pos.fees += fee; pos.sells += 1
        left = qty
        for lot in pos.lots:
            if lot["day"] < day:
                take = min(left, lot["qty"]); lot["qty"] -= take; left -= take
            if left == 0:
                break
        pos.lots = [lot for lot in pos.lots if lot["qty"]]
        pos.last_exit_time = _physical_open(stamp); pos.last_exit_reason = order["reason"]
        ledger.append(dict(id=pos.candidate["id"], ts_code=code, side="sell",
                           reason=order["reason"], decision_time=order["decision_time"],
                           fill_time=_physical_open(stamp).isoformat(), bar_index=index,
                           qty=qty, price=price, fee=fee, cash_flow=proceeds, cash=cash,
                           requested_fraction=order["fraction"], shares_remaining=pos.qty))
        order["qty"] -= qty
        if order["qty"] == 0 or pos.qty == 0:
            pos.pending = None
        if pos.qty == 0:
            archive.append(trade_record(pos, True)); del positions[code]

    def fill_buy(code, order, row, index, ordinal, stamp, existing=None):
        nonlocal cash
        candidate = order["candidate"]; day = stamp.strftime("%Y%m%d")
        fill_time = _physical_open(stamp)
        if entry_eligibility_fn is not None and not bool(entry_eligibility_fn(code, fill_time)):
            reason = "not_in_current_month_pool"
            outcomes[("add_" if existing is not None else "")+reason] += 1
            issues.append(dict(id=candidate["id"], ts_code=code, code=reason,
                               datetime=stamp.isoformat(), fill_time=fill_time.isoformat(),
                               decision_time=order["decision_time"],
                               side="add" if existing is not None else "buy"))
            return False
        up = _number(limits.get((code, day), {}).get("up_limit"))
        reason = None
        if math.isfinite(up) and row["open"] >= up-.005:
            reason = "up_limit_open_unfilled"
        price = float(row["open"])*(1+rules.slippage)
        price_p = price*float(row["scale"])
        structural = float(candidate["stop"])
        if price_p <= structural:
            reason = "gap_below_structural_stop_cancelled"
        stop = max(structural, price_p*(1-rules.max_initial_stop_pct)) if existing is None else existing.stop_p
        if existing is None and 1-structural/price_p > rules.max_initial_stop_pct+1e-10:
            reason = "entry_gap_risk_above_three_pct"
        if existing is not None and price_p <= existing.stop_p:
            reason = "add_below_stop_cancelled"
        if reason:
            outcomes[("add_" if existing is not None else "")+reason] += 1
            issues.append(dict(id=candidate["id"], ts_code=code, datetime=stamp.isoformat(),
                               code=reason, side="add" if existing is not None else "buy"))
            return False
        risk = max(1e-5, 1-stop/price_p)
        if existing is None:
            target = portfolio_equity()*min(max_name_pct, rules.account_risk_pct/risk)
            requested = target*(1. if policy == "ma30" else rules.trial_fraction)
        else:
            target = existing.target_budget
            requested = max(0., target-existing.buy_cash)
            # The combined remaining loss-to-stop must also fit today's
            # account risk budget; adding after a rally cannot enlarge it.
            existing_risk = sum(lot["qty"] * max(0., lot["price"]-existing.stop_p/row["scale"])
                                for lot in existing.lots)
            free_risk = max(0., portfolio_equity()*rules.account_risk_pct-existing_risk)
            requested = min(requested, free_risk/max(1e-5, risk))
        qty = quote_cap(code, price, requested)
        if qty == 0:
            outcomes[("add_" if existing is not None else "")+"allocation_or_cash_unfilled"] += 1
            issues.append(dict(id=candidate["id"], ts_code=code, datetime=stamp.isoformat(),
                               code="allocation_or_cash_unfilled"))
            return False
        fee = _fee(qty, price, False, rules); paid = qty*price+fee; cash -= paid
        if existing is None:
            source = row_lookup[(code, int(candidate["index"]))]
            pos = Position(candidate=candidate, entry_index=index, entry_ordinal=ordinal,
                           entry_time=_physical_open(stamp), entry_raw=price, entry_p=price_p,
                           entry_scale=float(row["scale"]), stop_p=stop,
                           structural_stop_p=structural, entry_atr=_number(source.get("atr14"), .01*price_p),
                           target_budget=target, peak_p=price_p, peak_close_p=price_p,
                           last_p=price_p, last_raw=price, last_ma20=_number(source.get("ma20")),
                           last_ma30=_number(source.get("ma30")), last_ma60=_number(source.get("ma60")))
            positions[code] = pos; outcomes["entered"] += 1
        else:
            pos = existing; pos.added = True; pos.pending = None
        pos.lots.append(dict(qty=qty, price=price, day=day)); pos.buys += 1
        pos.buy_cash += paid; pos.fees += fee
        ledger.append(dict(id=candidate["id"], ts_code=code, side="buy",
                           reason=order["reason"], decision_time=order["decision_time"],
                           fill_time=_physical_open(stamp).isoformat(), bar_index=index,
                           qty=qty, price=price, fee=fee, cash_flow=-paid, cash=cash,
                           requested_fraction=1. if policy == "ma30" else .5,
                           shares_remaining=pos.qty))
        return True

    for stamp in sorted(events):
        group = sorted(events[stamp], key=lambda value: value[0])
        # At the common opening time, sell available old inventory first.
        for code, index, ordinal, row in group:
            valid = bool(row.get("valid", False))
            if not valid:
                continue
            pos = positions.get(code)
            if pos and pos.pending and pd.Timestamp(pos.pending["decision_time"]) <= _physical_open(stamp):
                if pos.pending["side"] == "sell":
                    fill_sell(code, pos, row, index, ordinal, stamp)
        def buy_priority(event):
            code = event[0]; pos = positions.get(code)
            adding = bool(pos and pos.pending and pos.pending["side"] == "add")
            order = pos.pending if adding else waiting.get(code)
            score = _number(order["candidate"].get("entry_score"), 0.) if order else -1e10
            # Confirmed additions precede fresh trials; ties between trials
            # follow the caller's causal score (including its theme tiebreak).
            return (0 if adding else 1, -score, code)

        for code, index, ordinal, row in sorted(group, key=buy_priority):
            if not bool(row.get("valid", False)):
                if code in waiting and pd.Timestamp(waiting[code]["decision_time"]) <= _physical_open(stamp):
                    order = waiting.pop(code)
                    outcomes["invalid_next_slot_entry_cancelled"] += 1
                    issues.append(dict(id=order["candidate"]["id"], ts_code=code,
                                       code="invalid_next_slot_entry_cancelled", datetime=stamp.isoformat()))
                pos = positions.get(code)
                if pos and pos.pending and pos.pending["side"] == "add" and (
                        pd.Timestamp(pos.pending["decision_time"]) <= _physical_open(stamp)):
                    pos.pending = None
                    outcomes["invalid_next_slot_add_cancelled"] += 1
                    note_issue(pos, "invalid_next_slot_add_cancelled", stamp)
                continue
            pos = positions.get(code)
            if pos and pos.pending and pos.pending["side"] == "add" and (
                    pd.Timestamp(pos.pending["decision_time"]) <= _physical_open(stamp)):
                order = pos.pending; pos.pending = None
                fill_buy(code, order, row, index, ordinal, stamp, pos)
            if code in waiting and pd.Timestamp(waiting[code]["decision_time"]) <= _physical_open(stamp):
                order = waiting.pop(code)
                if code in positions:
                    outcomes["overlap_at_fill"] += 1
                else:
                    fill_buy(code, order, row, index, ordinal, stamp)

        # Now these bars have completed and their high/low/close are visible.
        for code, index, ordinal, row in group:
            if not bool(row.get("valid", False)):
                if code in positions:
                    note_issue(positions[code], "held_bar_invalid", stamp)
                continue
            last_mark[code] = float(row["close"])
            pos = positions.get(code)
            if pos:
                if not math.isclose(float(row["scale"]), pos.entry_scale, rel_tol=1e-8):
                    note_issue(pos, "corporate_action_unmodeled", stamp,
                               initial_scale=pos.entry_scale, current_scale=float(row["scale"]))
                state = _state_view(pos, ordinal, row)
                probability = _normal_prob(probability_fn, row, state) if policy == "adaptive" else dict(
                    p_fail=None, p_extend=None, n=0, supported=False)
                decision = _exit_decision(pos, row, ordinal, probability, policy, rules)
                # Urgent full exits supersede an unexecuted partial order,
                # including the unsellable add lot retained under T+1.
                can_replace = bool(decision and decision[0] == "sell" and decision[1] == 1. and
                    (not pos.pending or pos.pending["side"] != "sell" or
                     pos.pending["qty"] < pos.qty))
                if decision and (pos.pending is None or can_replace):
                    side, fraction, reason, flag = decision
                    if side == "sell":
                        qty = _sell_fraction(pos.qty, fraction, rules)
                        pos.pending = dict(side="sell", qty=qty, fraction=fraction,
                                           reason=reason, decision_time=stamp.isoformat())
                        if flag:
                            pos.flags.add(flag); pos.stage_counts[flag] += 1
                        if fraction < 1.:
                            pos.last_trim_ordinal = ordinal
                    else:
                        pos.add_attempted = True
                        pos.pending = dict(side="add", candidate=pos.candidate, reason=reason,
                                           decision_time=stamp.isoformat())
                    decisions.append(dict(id=pos.candidate["id"], ts_code=code,
                                          datetime=stamp.isoformat(), index=index, side=side,
                                          fraction=fraction, reason=reason, close=row["close"],
                                          p_close=row["p_close"], stop_p=pos.stop_p,
                                          held_bars=state["held_bars"], **probability))
                elif policy == "adaptive":
                    decisions.append(dict(id=pos.candidate["id"], ts_code=code,
                                          datetime=stamp.isoformat(), index=index, side="hold",
                                          fraction=0., reason="retain_or_pending", close=row["close"],
                                          p_close=row["p_close"], stop_p=pos.stop_p,
                                          held_bars=state["held_bars"], **probability))
                # A stage may rearm after two completed closes over MA20,
                # but each stage has a maximum of two trims per trade.
                if row["p_close"] > _number(row.get("ma20")):
                    pos.reclaim_bars += 1
                else:
                    pos.reclaim_bars = 0
                    pos.reclaim_armed = True
                if pos.reclaim_armed and pos.reclaim_bars >= 2 and pos.pending is None:
                    for flag in ("ma20_trim", "prob_fail_trim", "low_extend_trim"):
                        if pos.stage_counts[flag] < 2:
                            pos.flags.discard(flag)
                    pos.reclaim_armed = False
                pos.peak_p = max(pos.peak_p, float(row["p_high"]))
                pos.peak_close_p = max(pos.peak_close_p, float(row["p_close"]))
                pos.last_p = float(row["p_close"]); pos.last_raw = float(row["close"])
                pos.last_ma20 = _number(row.get("ma20")); pos.last_ma30 = _number(row.get("ma30"))
                pos.last_ma60 = _number(row.get("ma60")); pos.recent_closes.append(float(row["p_close"]))
                pos.recent_closes = pos.recent_closes[-8:]

            for candidate in sorted(candidate_map.get((code, index), []),
                                    key=lambda value: -_number(value.get("entry_score"), 0.)):
                if code in positions or code in waiting:
                    outcomes["overlap_signal"] += 1
                    decisions.append(dict(id=candidate["id"], ts_code=code,
                                          datetime=stamp.isoformat(), index=index, side="skip",
                                          fraction=0., reason="existing_or_pending_position"))
                    continue
                if not math.isfinite(_number(candidate.get("stop"))) or candidate["stop"] <= 0:
                    raise ValueError(f"Invalid frozen stop: {candidate['id']}")
                waiting[code] = dict(side="buy", candidate=candidate, reason="pullback_restart_trial",
                                     decision_time=stamp.isoformat())
                decisions.append(dict(id=candidate["id"], ts_code=code,
                                      datetime=stamp.isoformat(), index=index, side="buy_signal",
                                      fraction=1. if policy == "ma30" else .5,
                                      reason="pullback_restart_trial", stop_p=candidate["stop"]))
        curve.append(dict(datetime=stamp.isoformat(), cash=cash, equity=portfolio_equity(),
                          exposure=exposure(), positions=len(positions)))
    for code, order in waiting.items():
        outcomes["study_end_unfilled"] += 1
        issues.append(dict(id=order["candidate"]["id"], ts_code=code, code="study_end_unfilled"))
    for code, pos in positions.items():
        if pos.pending:
            issues.append(dict(id=pos.candidate["id"], ts_code=code,
                               code="study_end_pending_order", side=pos.pending["side"],
                               reason=pos.pending["reason"]))
    trades = archive+[trade_record(pos, False) for pos in positions.values()]
    closed = [trade for trade in trades if trade["closed"]]
    final = portfolio_equity()
    peaks = np.maximum.accumulate([initial_cash]+[point["equity"] for point in curve])
    values = np.array([initial_cash]+[point["equity"] for point in curve])
    drawdown = float(np.max(1-values/peaks))
    pnl = [trade["net_return"] for trade in closed]
    corporate = any(issue["code"] == "corporate_action_unmodeled" for issue in issues)
    summary = dict(policy=policy, candidates=len(candidates), trades=len(trades),
                   closed_trades=len(closed), censored_trades=len(trades)-len(closed),
                   wins=sum(value > 0 for value in pnl),
                   win_rate=sum(value > 0 for value in pnl)/len(pnl) if pnl else None,
                   initial_cash=float(initial_cash), final_equity=final,
                   net_return=final/initial_cash-1, max_drawdown=drawdown,
                   mean_trade_return=float(np.mean(pnl)) if pnl else None,
                   median_trade_return=float(np.median(pnl)) if pnl else None,
                   worst_trade_return=min(pnl) if pnl else None,
                   best_trade_return=max(pnl) if pnl else None,
                   fees=sum(order["fee"] for order in ledger),
                   risk_acceptance_failed=any(trade["risk_acceptance_failed"] for trade in trades),
                   cash_accounting_verified=not corporate, outcomes=dict(outcomes),
                   max_name_pct=max_name_pct, max_total_pct=max_total_pct,
                   probability_supported_decisions=sum(bool(x.get("supported")) for x in decisions),
                   execution="next_bar_open_proxy_t1_per_lot_invalid_buy_cancelled")
    return dict(summary=summary, trades=trades, ledger=ledger, decisions=decisions,
                issues=issues, curve=curve)
