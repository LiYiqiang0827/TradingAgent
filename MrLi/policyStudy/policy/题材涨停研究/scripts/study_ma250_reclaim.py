"""Completed-bar MA250 reclaim experiment; no real orders or parameter fitting.

The flat-price forecast recalculates BOTH moving averages. Session gaps are
retained. Execution is the next bar's open proxy, with A-share T+1 and cash lots.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import numpy as np
import pandas as pd

TIMES = ("09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30",
         "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00")


@dataclass(frozen=True)
class Rules:
    version: str = "ma250_reclaim_15m_v1"
    down_bars: int = 80
    down_share: float = .8
    cross_lookback: int = 16
    forecast_bars: int = 16
    reclaim_age: int = 0  # Literal crossing on this bar; 15 is an explicit ablation.
    initial_cash: float = 300000.
    commission: float = .0003
    minimum_commission: float = 5.
    stamp: float = .0005
    transfer: float = .00001
    slippage: float = .0005
    lot: int = 100
    stop_mode: str = "close"

    def __post_init__(self):
        if self.down_bars < 1 or not 0 < self.down_share <= 1:
            raise ValueError("Invalid long-decline definition")
        if not 1 <= self.forecast_bars <= 20 or self.cross_lookback < 1:
            raise ValueError("Invalid crossover/forecast horizon")
        if self.stop_mode not in ("close", "low"):
            raise ValueError("Stop uses completed close or completed bar low")


def crossing_level(a0, b0, a1, b1):
    """Linear intersection between the two already completed MA observations."""
    if not all(np.isfinite(x) for x in (a0, b0, a1, b1)):
        raise ValueError("Non-finite crossing")
    d0, d1 = a0 - b0, a1 - b1
    if not d0 <= 0 < d1:
        raise ValueError("Not a golden cross")
    u = -d0 / (d1 - d0)
    return float(b0 + u * (b1 - b0))


def flat_projection(history, horizon=16):
    """All history ends at decision time; append constant current close.

    MA_N(t+h) = MA_N(t) + (h*C_t - sum(first h outgoing closes))/N.
    Includes h=0 so a golden cross cannot be fabricated if MA20 is already above.
    """
    x = np.asarray(history, dtype=float)
    if len(x) < 250 or not np.isfinite(x[-250:]).all() or not 1 <= horizon <= 20:
        raise ValueError("Need 250 valid observed closes and horizon <= 20")
    result = []
    paths = {}
    for n in (20, 250):
        old = x[-n:]
        paths[n] = np.r_[old.mean(), old.mean() +
                         (np.arange(1, horizon + 1) * x[-1] - np.cumsum(old[:horizon])) / n]
    for h in range(horizon + 1):
        result.append(dict(h=h, ma20=float(paths[20][h]), ma250=float(paths[250][h]),
                           assumed_close=float(x[-1]), golden_cross=bool(h > 0 and
                           paths[20][h - 1] <= paths[250][h - 1] and paths[20][h] > paths[250][h])))
    return result


def prepare(raw, calendar):
    """16 genuine session slots; invalid or missing bars break rolling windows."""
    x = raw.copy()
    x["datetime"] = pd.to_datetime(x.datetime)
    duplicate = set(x.loc[x.datetime.duplicated(keep=False), "datetime"])
    grid = pd.DatetimeIndex([pd.Timestamp(f"{d} {t}") for d in calendar for t in TIMES])
    x = x[x.datetime.isin(grid)].drop_duplicates("datetime").set_index("datetime")
    f = x.reindex(grid).rename_axis("datetime").reset_index()
    for col in ("open", "high", "low", "close", "vol", "adj_factor"):
        f[col] = pd.to_numeric(f[col], errors="coerce")
    valid = (f[["open", "high", "low", "close", "vol", "adj_factor"]].notna().all(axis=1)
             & f[["open", "high", "low", "close", "adj_factor"]].gt(0).all(axis=1)
             & f.vol.ge(0) & f.high.ge(f[["open", "low", "close"]].max(axis=1))
             & f.low.le(f[["open", "high", "close"]].min(axis=1)) & ~f.datetime.isin(duplicate))
    f["valid"] = valid
    f.loc[~valid, ["open", "high", "low", "close", "vol", "adj_factor"]] = np.nan
    factors = f.adj_factor.dropna()
    base = float(factors.iloc[0]) if len(factors) else 1.
    f["scale"] = f.adj_factor / base
    for c in ("open", "high", "low", "close"):
        f["p_" + c] = f[c] * f.scale
    for n in (5, 20, 30, 250):
        f[f"ma{n}"] = f.p_close.rolling(n).mean()
    for a, b in ((5, 20), (5, 30), (20, 250)):
        d = f[f"ma{a}"] - f[f"ma{b}"]
        f[f"cross{a}_{b}"] = d.shift().le(0) & d.gt(0)
    d = f.p_close - f.ma250
    f["reclaim"] = d.shift().le(0) & d.gt(0)
    f["index"] = np.arange(len(f))
    for label, flag in (("cross5_20", "cross5_20"), ("cross5_30", "cross5_30"), ("reclaim", "reclaim")):
        f[label + "_index"] = f["index"].where(f[flag]).ffill()
    levels = pd.Series(np.nan, index=f.index)
    for i in np.flatnonzero(f.cross5_20):
        levels.iloc[i] = crossing_level(f.ma5.iloc[i - 1], f.ma20.iloc[i - 1], f.ma5.iloc[i], f.ma20.iloc[i])
    f["stop_level"] = levels.ffill()
    return f


def signals(f, start, end, rules=Rules()):
    slopes = f.ma250.diff()
    declining = (f.ma250.lt(f.ma250.shift(rules.down_bars)) & slopes.lt(0)
                 & slopes.lt(0).rolling(rules.down_bars).mean().ge(rules.down_share))
    price = f.p_close.gt(f.ma250) & (f["index"] - f.reclaim_index).between(0, rules.reclaim_age)
    alignment = f.ma5.gt(f.ma20) & f.ma20.gt(f.ma30)
    recent = ((f["index"] - f.cross5_20_index).between(0, rules.cross_lookback - 1)
              & (f["index"] - f.cross5_30_index).between(0, rules.cross_lookback - 1))
    in_range = f.datetime.ge(pd.Timestamp(start)) & f.datetime.lt(pd.Timestamp(end) + pd.Timedelta(days=1))
    f = f.copy()
    f["declining"] = declining
    f["price_gate"] = price
    f["alignment_gate"] = alignment
    f["recent_cross_gate"] = recent
    f["signal_gate"] = False
    gates = [in_range & f.ma250.notna(), in_range & declining,
             in_range & declining & price, in_range & declining & price & alignment,
             in_range & declining & price & alignment & recent]
    funnel = dict(zip(("warmed_bars", "declining_bars", "price_reclaim", "bull_alignment", "two_recent_crosses"),
                      (int(g.sum()) for g in gates)))
    result = []
    closes = f.p_close.to_numpy()
    for i in np.flatnonzero(gates[-1]):
        try:
            path = flat_projection(closes[max(0, i - 249):i + 1], rules.forecast_bars)
        except ValueError:
            continue
        crosses = [r["h"] for r in path if r["golden_cross"]]
        if not crosses or f.ma20.iloc[i] > f.ma250.iloc[i]:
            continue
        row = f.iloc[i]
        j = int(row.cross5_20_index)
        result.append(dict(id=f"S{len(result) + 1:03d}", index=int(i), time=str(row.datetime),
                           close=float(row.close), adjusted_close=float(row.p_close),
                           ma5=float(row.ma5), ma20=float(row.ma20), ma30=float(row.ma30),
                           ma250=float(row.ma250), stop=float(row.stop_level),
                           stop_raw_at_signal=float(row.stop_level / row.scale),
                           cross5_20_time=str(f.datetime.iloc[j]),
                           cross5_30_time=str(f.datetime.iloc[int(row.cross5_30_index)]),
                           decline_share=float(slopes.lt(0).iloc[i - rules.down_bars + 1:i + 1].mean()),
                           projected_cross_h=int(crosses[0]), projection=path))
        f.loc[i, "signal_gate"] = True
    funnel["flat_forecast_cross"] = len(result)
    return result, funnel, f


def replay(f, candidates, limits, start, end, rules=Rules()):
    """One cash-only position. Pending invalidation persists through T+1.

    Price/MA conditions use completed closes. Low-stop sensitivity still detects
    at bar END and fills no earlier than next open, never at the nominal stop.
    Limit-price opening trades are conservatively rejected/deferred, without
    looking at that execution bar's subsequent range to infer queue fills.
    """
    if f.loc[f.datetime.between(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1)),
             "adj_factor"].dropna().nunique() > 1:
        raise ValueError("Corporate-action cash accounting is not implemented; cannot call this a cash backtest")
    by = {s["index"]: s for s in candidates}
    cash = rules.initial_cash
    position = pending_buy = pending_sell = None
    ledger, issues, trades, curve, observations, dispositions = [], [], [], [], [], []
    prior_mark = 0.
    for i, row in f.iterrows():
        if not pd.Timestamp(start) <= row.datetime < pd.Timestamp(end) + pd.Timedelta(days=1):
            continue
        day = row.datetime.strftime("%Y%m%d")
        li = limits.get(day)
        if not row.valid or not li:
            if position or pending_buy:
                issues.append(dict(index=i, time=str(row.datetime), kind="missing_bar_or_daily_limits"))
            curve.append(dict(index=i, datetime=str(row.datetime), equity=cash + (position["shares"] * prior_mark if position else 0.),
                              cash=cash, shares=position["shares"] if position else 0))
            continue
        opening_time = str(row.datetime - pd.Timedelta(minutes=15))
        # Execution is before this bar's close, so today's close cannot cancel a pending order.
        if position and pending_sell:
            if day <= position["entry_day"]:
                issues.append(dict(index=i, time=opening_time, kind="T_plus_1_wait", reason=pending_sell["reason"]))
            elif row.open <= li["down_limit"] + .005:
                issues.append(dict(index=i, time=opening_time, kind="down_limit_open_unfilled", reason=pending_sell["reason"]))
            else:
                price = max(float(row.open) * (1 - rules.slippage), li["down_limit"])
                notional = position["shares"] * price
                fee = max(rules.minimum_commission, notional * rules.commission) + notional * (rules.stamp + rules.transfer)
                proceeds = notional - fee
                cash += proceeds
                ret = 100 * (proceeds / position["entry_cost"] - 1)
                end_record = dict(id=position["signal"]["id"], signal_time=position["signal"]["time"],
                                  signal_index=position["signal"]["index"], entry_index=position["entry_index"],
                                  entry_time=position["entry_time"], exit_index=i, exit_time=opening_time,
                                  entry_price=position["entry_price"], exit_price=price, shares=position["shares"],
                                  stop_raw=position["signal"]["stop_raw_at_signal"],
                                  projected_cross_h=position["signal"]["projected_cross_h"],
                                  actual_cross_h=position["actual_cross_h"],
                                  exit_reason=pending_sell["reason"], exit_decision_time=pending_sell["time"],
                                  exit_decision_index=pending_sell["index"],
                                  holding_bars=i - position["entry_index"], gross_return_pct=100 * (price / position["entry_price"] - 1),
                                  net_return_pct=ret, pnl=proceeds - position["entry_cost"],
                                  fees=position["entry_fee"] + fee, status="closed")
                trades.append(end_record)
                ledger.append(dict(side="sell", id=end_record["id"], index=i, time=opening_time,
                                   decision_time=pending_sell["time"], price=price, shares=position["shares"], fee=fee,
                                   reason=pending_sell["reason"], cash_after=cash))
                position = pending_sell = None
        if pending_buy and not position:
            s = pending_buy
            pending_buy = None
            reason = None
            if row.open >= li["up_limit"] - .005:
                reason = "up_limit_open_unfilled"
            elif row.p_open <= s["stop"]:
                reason = "open_already_invalidates_stop"
            elif i > s["index"] + 1:
                reason = "next_execution_bar_missing"
            if reason:
                issues.append(dict(index=i, time=opening_time, id=s["id"], kind=reason))
                dispositions.append(dict(id=s["id"], status=reason))
            else:
                price = min(float(row.open) * (1 + rules.slippage), li["up_limit"])
                shares = int(cash / price / rules.lot) * rules.lot
                while shares:
                    n = price * shares
                    fee = max(rules.minimum_commission, n * rules.commission) + n * rules.transfer
                    if n + fee <= cash:
                        break
                    shares -= rules.lot
                if not shares:
                    dispositions.append(dict(id=s["id"], status="insufficient_cash"))
                else:
                    cash -= n + fee
                    position = dict(signal=s, entry_index=i, entry_time=opening_time, entry_price=price,
                                    entry_adjusted=price * float(row.scale), entry_day=day, entry_fee=fee,
                                    entry_cost=n + fee, shares=shares, actual_cross_h=None, trend_armed=False)
                    ledger.append(dict(side="buy", id=s["id"], index=i, time=opening_time, decision_time=s["time"],
                                       price=price, shares=shares, fee=fee, reason="signal", cash_after=cash))
                    dispositions.append(dict(id=s["id"], status="filled"))
        prior_mark = float(row.close)
        if position:
            s = position["signal"]
            h = i - s["index"]
            if row.cross20_250 and h <= rules.forecast_bars and position["actual_cross_h"] is None:
                position["actual_cross_h"] = int(h)
            if row.p_close > position["entry_adjusted"]:
                position["trend_armed"] = True
            why = None
            stop_price = row.p_close if rules.stop_mode == "close" else row.p_low
            if stop_price < s["stop"]:
                why = "cross_level_stop"
            elif h >= rules.forecast_bars and position["actual_cross_h"] is None:
                why = "no_ma20_ma250_cross_by_16"
            elif position["trend_armed"] and row.p_close < row.ma30:
                why = "below_ma30_after_rise"
            if why and not pending_sell:
                pending_sell = dict(reason=why, time=str(row.datetime), index=i)
            observations.append(dict(index=i, time=str(row.datetime), id=s["id"], h=h,
                                     actual_cross_h=position["actual_cross_h"], trend_armed=position["trend_armed"],
                                     pending_exit=pending_sell["reason"] if pending_sell else None))
        # Signals observed while holding are not additional purchases.
        if i in by:
            s = by[i]
            if position or pending_sell:
                dispositions.append(dict(id=s["id"], status="already_holding"))
            elif i == len(f) - 1:
                dispositions.append(dict(id=s["id"], status="no_next_bar"))
            else:
                pending_buy = s
        curve.append(dict(index=i, datetime=str(row.datetime), equity=cash + (position["shares"] * prior_mark if position else 0.),
                          cash=cash, shares=position["shares"] if position else 0))
    if pending_buy:
        dispositions.append(dict(id=pending_buy["id"], status="no_next_bar"))
    if position:
        trades.append(dict(id=position["signal"]["id"], signal_time=position["signal"]["time"],
                           signal_index=position["signal"]["index"], entry_index=position["entry_index"],
                           entry_time=position["entry_time"], entry_price=position["entry_price"],
                           shares=position["shares"], stop_raw=position["signal"]["stop_raw_at_signal"],
                           actual_cross_h=position["actual_cross_h"], status="open", net_return_pct=None))
    q = pd.DataFrame(curve)
    eq = np.r_[rules.initial_cash, q.equity.to_numpy()]
    dd = 100 * (1 - eq / np.maximum.accumulate(eq))
    closed = [r for r in trades if r["status"] == "closed"]
    rr = np.array([r["net_return_pct"] for r in closed])
    counts = pd.Series([r["status"] for r in dispositions]).value_counts().to_dict()
    summary = dict(signals=len(candidates), dispositions={str(k): int(v) for k, v in counts.items()},
                   buys=len([r for r in ledger if r["side"] == "buy"]), closed=len(closed), open=int(position is not None),
                   wins=int((rr > 0).sum()), win_rate_pct=float(100 * (rr > 0).mean()) if len(rr) else None,
                   mean_net_pct=float(rr.mean()) if len(rr) else None, median_net_pct=float(np.median(rr)) if len(rr) else None,
                   worst_net_pct=float(rr.min()) if len(rr) else None, best_net_pct=float(rr.max()) if len(rr) else None,
                   initial_cash=rules.initial_cash, final_equity=float(eq[-1]), net_account_return_pct=float(100 * (eq[-1] / eq[0] - 1)),
                   max_drawdown_pct=float(dd.max()), fees=float(sum(r["fee"] for r in ledger)),
                   T_plus_1_wait_bars=len([r for r in issues if r["kind"] == "T_plus_1_wait"]),
                   T_plus_1_wait_trades=len({r["reason"] + r["time"][:10] for r in issues if r["kind"] == "T_plus_1_wait"}),
                   exit_counts=pd.Series([r["exit_reason"] for r in closed]).value_counts().to_dict())
    return dict(summary=summary, trades=trades, ledger=ledger, issues=issues, observations=observations,
                dispositions=dispositions, curve=q)
