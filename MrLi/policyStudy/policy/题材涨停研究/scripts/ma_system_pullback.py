"""User MA-system contract, v1 draft: causal facts and target-position signals.

No fills, returns, theme identity inference, or parameter fitting. Target fractions
are logical intentions; an execution ledger must acknowledge actual lot fills.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd

from study_daily_intraday_pullback import prepare_daily
from study_ma250_reclaim import prepare as prepare_clock


@dataclass(frozen=True)
class Rules:
    version: str = "ma_system_pullback_draft_v1"
    para_ma30up_period: int = 10
    daily_order_mode: str = "up_close_days"
    bear_bars: int = 1
    cross_window: int = 4
    forecast_bars: int = 4
    deduction_mode: str = "strict"
    retest_wait: int = 8
    retest_tolerance_pct: float = 1.0
    daily_near_pct: float = 3.0
    require_daily_reversal: bool = True
    expectation_bars: int = 16
    expectation_rise_bars: int = 3
    expectation_windows: tuple[int, ...] = (5, 10, 20, 30)
    coil_pct: float = 1.0
    death_wait: int = 3
    rebuy_wait: int = 4

    def __post_init__(self):
        if min(self.para_ma30up_period, self.bear_bars, self.cross_window,
               self.forecast_bars, self.retest_wait, self.expectation_bars,
               self.expectation_rise_bars, self.death_wait, self.rebuy_wait) < 1:
            raise ValueError("Bar/day windows must be positive")
        if self.forecast_bars > 10:
            raise ValueError("MA10 cannot keep strictly rising beyond 10 flat-price bars")
        if self.daily_order_mode not in ("up_close_days", "whole_run", "current"):
            raise ValueError("Unknown daily alignment interpretation")
        if self.deduction_mode not in ("strict", "end_lower"):
            raise ValueError("Unknown deduction interpretation")
        if tuple(sorted(set(self.expectation_windows))) != self.expectation_windows:
            raise ValueError("Expectation windows must be ascending and unique")
        if not set(self.expectation_windows).issubset({5, 10, 20, 30, 60, 120}):
            raise ValueError("Unsupported expectation MA")
        if min(self.retest_tolerance_pct, self.daily_near_pct, self.coil_pct) < 0:
            raise ValueError("Negative tolerance")


def run_length(flag):
    flag = flag.fillna(False).astype(bool)
    return flag.groupby((~flag).cumsum()).cumsum().astype(int)


def ordered(frame, windows, direction="bull"):
    flags = [frame[f"ma{a}"].gt(frame[f"ma{b}"]) if direction == "bull"
             else frame[f"ma{a}"].lt(frame[f"ma{b}"])
             for a, b in zip(windows, windows[1:])]
    return pd.concat(flags, axis=1).all(axis=1)


def daily_facts(raw, adj, calendar, rules=Rules()):
    d = prepare_daily(raw, adj, calendar).copy()
    d["rise30"] = run_length(d.ma30.diff().gt(0))
    d["short_bull"] = ordered(d, (5, 10, 20, 30))
    d["full_bull"] = ordered(d, (5, 10, 20, 30, 60, 120))
    d["close_up"] = d.close.diff().gt(0)
    for a, b in ((30, 60), (60, 120)):
        d[f"cross{a}_{b}"] = (d[f"ma{a}"].shift().le(d[f"ma{b}"].shift())
                                     & d[f"ma{a}"].gt(d[f"ma{b}"]))
    anchor = None
    records = []
    for i, r in d.iterrows():
        start = i - int(r.rise30) + 1
        if not r.valid_day or r.rise30 == 0:
            anchor = None
        segment = d.iloc[max(0, start):i + 1]
        if rules.daily_order_mode == "up_close_days":
            selected = segment[segment.close_up]
        elif rules.daily_order_mode == "whole_run":
            selected = segment
        else:
            selected = d.iloc[i:i + 1]
        short_ok = bool(len(selected) and selected.short_bull.all())
        long_ok = True
        crosses = {}
        for a, b in ((30, 60), (60, 120)):
            label = f"cross{a}_{b}_in_run"
            crosses[label] = bool(segment[f"cross{a}_{b}"].any())
            already = bool(len(segment) and segment.iloc[0][f"ma{a}"] > segment.iloc[0][f"ma{b}"])
            long_ok &= already or crosses[label]
        qualify = bool(r.valid_day and r.rise30 >= rules.para_ma30up_period
                       and r.close_up and r.full_bull and short_ok and long_ok)
        if qualify:
            anchor = str(r.trade_date)
        records.append(dict(daily_qualify=qualify, short_segment_ok=short_ok,
                            long_ready=long_ok, selected_at=anchor,
                            watch_active=anchor is not None,
                            ma30_run_start=str(segment.iloc[0].trade_date) if len(segment) else None,
                            **crosses))
    return pd.concat([d, pd.DataFrame(records)], axis=1)


def flat_projection(closes, horizon=4, deduction_mode="strict"):
    """Exact outgoing closes and both sides of future MA alignment, prefix only."""
    x = np.asarray(closes, dtype=float)
    if len(x) < 30 or not np.isfinite(x[-30:]).all() or not 1 <= horizon <= 10:
        return dict(known=False, ready=False, path=[], outgoing={})
    paths, outgoing, falling = {}, {}, {}
    for n in (10, 20, 30):
        old = x[-n:]
        out = old[:horizon]
        outgoing[n] = out.tolist()
        paths[n] = np.r_[old.mean(), old.mean() +
                         (np.arange(1, horizon + 1) * x[-1] - np.cumsum(out)) / n]
        falling[n] = bool(np.all(np.diff(out) <= 0) and np.any(np.diff(out) < 0)
                          if deduction_mode == "strict" else out[-1] < out[0])
    rises = all(np.all(np.diff(paths[n]) > 0) for n in paths)
    alignment = [h for h in range(1, horizon + 1)
                 if paths[10][h] > paths[20][h] > paths[30][h]]
    path = [dict(h=h, **{f"ma{n}": float(paths[n][h]) for n in paths})
            for h in range(horizon + 1)]
    return dict(known=True, ready=bool(rises and all(falling.values()) and alignment),
                rising_all=rises, deduction_falling=falling,
                first_bull_bar=min(alignment) if alignment else None,
                outgoing=outgoing, path=path)


def intraday_facts(raw, calendar, daily, rules=Rules()):
    f = prepare_clock(raw, calendar).copy()
    # Dated cumulative factors: uniform scaling agrees across daily and intraday.
    for col in ("open", "high", "low", "close"):
        f[f"p_{col}"] = f[col] * f.adj_factor
    for n in (5, 10, 20, 30, 60, 120):
        f[f"ma{n}"] = f.p_close.rolling(n).mean()
        f[f"rise{n}"] = run_length(f[f"ma{n}"].diff().gt(0))
    for n in (10, 20, 30):
        f[f"cross5_{n}"] = f.ma5.shift().le(f[f"ma{n}"].shift()) & f.ma5.gt(f[f"ma{n}"])
    f["bear"] = ordered(f, (10, 20, 30, 60, 120), "bear")
    f["bear_run"] = run_length(f.bear & f.valid)
    f["price_above120"] = f.p_close.gt(f.ma120)
    f["expected"] = (ordered(f, rules.expectation_windows) &
                        f[[f"rise{n}" for n in rules.expectation_windows]].ge(
                            rules.expectation_rise_bars).all(axis=1))
    f["trade_date"] = f.datetime.dt.strftime("%Y%m%d")
    previous = {calendar[i]: calendar[i - 1] for i in range(1, len(calendar))}
    f["daily_as_of"] = f.trade_date.map(previous)
    d = daily.set_index("trade_date")
    f["watch_active"] = f.daily_as_of.map(d.watch_active).eq(True)
    f["selected_at"] = f.daily_as_of.map(d.selected_at)
    projections = [flat_projection(f.p_close.iloc[max(0, i - 29):i + 1],
                                   rules.forecast_bars, rules.deduction_mode)
                   for i in range(len(f))]
    f["projection_ready"] = [p["ready"] for p in projections]
    return f


def daily_evidence(daily, minute_prefix, pullback_start, rules=Rules()):
    """Known daily low and reversal; today's candle is formed from prefix only."""
    current = minute_prefix.iloc[-1]
    day = str(current.trade_date)
    prior = daily[daily.trade_date.lt(day)]
    start_day = pd.Timestamp(pullback_start).strftime("%Y%m%d")
    candles = prior[prior.trade_date.ge(start_day)].copy()
    today = minute_prefix[minute_prefix.trade_date.eq(day)]
    # A partial candle requires every session slot so far, not future slots.
    partial = None
    if len(today) and today.valid.all() and today.datetime.iloc[0].strftime("%H:%M") == "09:45":
        partial = dict(trade_date=day, open=float(today.p_open.iloc[0]),
                       high=float(today.p_high.max()), low=float(today.p_low.min()),
                       close=float(current.p_close), valid_day=True)
        for n in (5, 10, 20, 30, 60, 120):
            history = prior.close.tail(n - 1)
            partial[f"ma{n}"] = float((history.sum() + current.p_close) / n) if len(history) == n - 1 and history.notna().all() else np.nan
        candles = pd.concat([candles, pd.DataFrame([partial])], ignore_index=True)
    if candles.empty or not candles.valid_day.all():
        return dict(known=False, confirm=False, reason="daily_or_partial_gap")
    trough = candles.loc[candles.low.idxmin()]
    distances = {n: float(100 * abs(trough.low / trough[f"ma{n}"] - 1))
                 for n in (5, 10, 20, 30, 60, 120) if pd.notna(trough[f"ma{n}"])}
    near = [n for n, distance in distances.items() if distance <= rules.daily_near_pct]
    reversal = []
    for i, candle in candles.reset_index(drop=True).iterrows():
        span = candle.high - candle.low
        body = abs(candle.close - candle.open)
        shadow = min(candle.open, candle.close) - candle.low
        at_support = any(abs(candle.low / candle[f"ma{n}"] - 1) * 100 <= rules.daily_near_pct
                         for n in (5, 10, 20, 30, 60, 120) if pd.notna(candle[f"ma{n}"]))
        if at_support and span > 0 and shadow >= 2 * max(body, span * .05) and (candle.close - candle.low) / span >= .65:
            reversal.append(dict(date=str(candle.trade_date), kind="lower_shadow", partial=str(candle.trade_date) == day))
        if i and candle.close > candle.open:
            prev = candles.reset_index(drop=True).iloc[i - 1]
            previous_support = any(abs(prev.low / prev[f"ma{n}"] - 1) * 100 <= rules.daily_near_pct
                                   for n in (5, 10, 20, 30, 60, 120) if pd.notna(prev[f"ma{n}"]))
            if prev.close < prev.open and candle.close > prev.close and previous_support:
                reversal.append(dict(date=str(candle.trade_date), kind="bear_then_bull", partial=str(candle.trade_date) == day))
    return dict(known=True, confirm=bool(near and (reversal or not rules.require_daily_reversal)),
                trough_date=str(trough.trade_date), trough_price=float(trough.low),
                near_windows=near, nearest_window=min(distances, key=distances.get) if distances else None,
                distances_pct=distances, reversal=reversal,
                current_partial_available=partial is not None, reason="observed_prefix")


def scan_entries(frame, daily, rules=Rules()):
    """Separate price observations from daily qualification and formal theme gate."""
    rows, candidates = [], []
    episode = None
    armed = None
    crosses = {}
    prior_bear = False
    for i, r in frame.iterrows():
        if not r.valid or not np.isfinite(r.ma120):
            episode, armed, crosses, prior_bear = None, None, {}, False
        elif r.bear and not prior_bear:
            episode, armed, crosses = i, None, {}
        if episode is not None:
            for n in (10, 20, 30):
                if r[f"cross5_{n}"]:
                    crosses[n] = i
        compact = bool(len(crosses) == 3 and max(crosses.values()) - min(crosses.values()) < rules.cross_window
                       and i - min(crosses.values()) < rules.cross_window
                       and r.ma5 > max(r.ma10, r.ma20, r.ma30))
        bear_complete = episode is not None and frame.bear_run.iloc[episode:i + 1].max() >= rules.bear_bars
        evidence3 = bool(bear_complete and compact and r.price_above120 and r.projection_ready)
        if evidence3 and armed is None:
            armed = i
        if armed is not None and (i - armed > rules.retest_wait or not r.price_above120):
            episode, armed, crosses = None, None, {}
        tol = rules.retest_tolerance_pct / 100
        retest = bool(armed is not None and i > armed and
                      r.ma30 * (1 - tol) <= r.p_low <= r.ma30 * (1 + tol) and r.p_close >= r.ma30)
        e = daily_evidence(daily, frame.iloc[:i + 1], frame.datetime.iloc[episode], rules) if retest else None
        observed = bool(retest and e["confirm"])
        # Stage one must already have existed by the pullback, not appear later.
        selected = frame.selected_at.iloc[episode] if episode is not None else None
        daily_ok = bool(r.watch_active and pd.notna(selected))
        if observed:
            candidates.append(dict(time=str(r.datetime), episode_start=str(frame.datetime.iloc[episode]),
                                   armed_at=str(frame.datetime.iloc[armed]), selected_at=selected,
                                   daily_gate=daily_ok, technical_candidate=daily_ok,
                                   formal_entry="unknown_theme_evidence", daily_evidence=e))
            episode, armed, crosses = None, None, {}
        rows.append(dict(time=str(r.datetime), valid=bool(r.valid), daily_watch=bool(r.watch_active),
                         bear=bool(r.bear), compact_cross=compact,
                         price_above120=bool(r.price_above120), projection_ready=bool(r.projection_ready),
                         stage3=evidence3, armed=armed is not None, retest=retest,
                         daily_confirm=bool(e and e["confirm"]), technical_candidate=observed and daily_ok))
        prior_bear = bool(r.bear) if r.valid else False
    return pd.DataFrame(rows), candidates


@dataclass
class TargetState:
    """Logical target weights only. No signal is a statement of a filled order."""
    age: int = 0
    confirmed: bool = False
    closed: bool = False
    tranche_a: bool = True
    tranche_b: bool = True
    death_count: int = 0
    a_sale_age: int | None = None
    b_sale_age: int | None = None
    unknown_bars: int = 0

    @property
    def target(self):
        return 0.0 if self.closed else .25 + .25 * self.tranche_a + .5 * self.tranche_b


def target_step(state, row, rules=Rules(), *, theme_retreat=False):
    """Call after each held completed bar; entry-fill bar counts as bar one.

    MA120 and timeout override restoration. Theme retreat freezes restores; its
    independent reduction size remains unspecified and is not invented here.
    """
    if state.closed:
        return []
    state.age += 1
    if not row["valid"] or not all(np.isfinite(row[f"ma{n}"]) for n in (5, 10, 20, 30, 120)):
        state.unknown_bars += 1
        state.death_count = 0
        return ["data_unknown"]
    if row["p_close"] < row["ma120"]:
        state.closed = True
        return ["exit_ma120"]
    if row["expected"]:
        state.confirmed = True
    if not state.confirmed and state.age >= rules.expectation_bars:
        if state.unknown_bars:
            return ["expectation_unknown_due_to_gap"]
        state.closed = True
        return ["exit_expectation_timeout"]
    if not state.confirmed:
        return []
    events = []
    state.death_count = state.death_count + 1 if row["ma5"] < row["ma30"] and row["ma10"] < row["ma30"] else 0
    if state.tranche_b and state.death_count >= rules.death_wait:
        state.tranche_b, state.b_sale_age = False, state.age
        events.append("reduce_b_half_original")
    coil = 100 * (max(row[f"ma{n}"] for n in (5, 10, 20)) / min(row[f"ma{n}"] for n in (5, 10, 20)) - 1)
    if state.tranche_a and coil <= rules.coil_pct and row["p_close"] < row["ma20"]:
        state.tranche_a, state.a_sale_age = False, state.age
        events.append("reduce_a_quarter_original")
    if theme_retreat:
        events.append("theme_retreat_freeze_restores")
        return events
    if not state.tranche_b and state.age > state.b_sale_age and row["ma5"] > row["ma30"] and row["ma10"] > row["ma30"]:
        state.tranche_b = True
        events.append("restore_b_half_original")
    if not state.tranche_a and state.age > state.a_sale_age:
        timely = state.age - state.a_sale_age <= rules.rebuy_wait
        recovery = (row["rise30"] > 0 and row["touch30_recent"]
                    and row["p_close"] > max(row["outgoing10_next"], row["outgoing20_next"], row["ma30"]))
        if timely and recovery:
            state.tranche_a = True
            events.append("restore_a_quarter_original")
    return events
