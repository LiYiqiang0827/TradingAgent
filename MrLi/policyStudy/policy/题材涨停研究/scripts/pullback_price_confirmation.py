"""Price-first, prefix-only diagnostics for researcher-selected pullback cases.

The quiet interval is explicitly supplied, not discovered from future winners.
This is a development-case auditor, not an automatic candidate screen or a
validated trading strategy. The existing MA-coil backtests remain unchanged.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
import sys
import warnings

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "policyStudy/skills/a-share-kline-structure-analysis/scripts"))
from static_structure.pivots import detect_pivots


def local_pivots(frame):
    # A flat limit-up run has no local prominence; it cannot invent line anchors.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="some peaks have a prominence of 0")
        return [p for p in detect_pivots(frame, timeframe="15minute", scales=(("local", 2, 0.0),))
                if p["prominence"] > 0]


@dataclass(frozen=True)
class ConfirmationRules:
    version: str = "price_confirmation_case_v1"
    body_pct: float = 1.0
    body_fraction: float = 0.55
    close_location: float = 0.70
    quiet_volume_multiple: float = 2.0
    same_slot_volume_multiple: float = 1.2
    consecutive_gain_pct: float = 2.0
    line_tolerance_pct: float = 0.3
    pivot_right_bars: int = 2


def pullback_depth_score(drawdown_pct: float) -> float:
    """Provisional depth component only; never a standalone rejection gate.

    Full credit through 35%; a continuous, modest deduction to 80 at 40%.
    Greater depth lowers this component further, without inventing a new cutoff.
    The weight in an eventual composite score requires separate validation.
    """
    if not np.isfinite(drawdown_pct) or not 0 <= drawdown_pct <= 100:
        raise ValueError("Drawdown must be a finite percentage in [0, 100]")
    return float(max(0., 100.-4.*max(0., drawdown_pct-35.)))


def phase_volume_profile(frame: pd.DataFrame, launch: str, cutoff: str,
                         baseline_sessions: int = 20, tail_sessions: int = 5) -> dict:
    """Describe a higher volume regime through an observed first-wave pullback.

    Use actual daily volume, never price-adjusted volume. Compare the correction
    both with the first advance and with the pre-launch regime. Medians, lower
    tails and rolling means expose a spike-inflated average or fading activity.
    This is a feature report, not a calibrated score or an automatic entry gate.
    """
    if baseline_sessions < 2 or tail_sessions < 2:
        raise ValueError("Volume windows must contain at least two sessions")
    x = frame[frame.trade_date.astype(str).le(cutoff)].copy().reset_index(drop=True)
    dates = x.trade_date.astype(str)
    if x.empty or dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError("Daily prefix must be nonempty, ordered and unique")
    hits = np.flatnonzero(dates.eq(launch))
    if len(hits) != 1 or hits[0] < baseline_sessions:
        raise ValueError("Launch or its volume baseline is missing")
    i = int(hits[0])
    observed = x.iloc[i-baseline_sessions:]
    if (not observed.valid_day.all() or
            not np.isfinite(observed.raw_vol).all() or
            not observed.raw_vol.gt(0).all() or
            not np.isfinite(observed.high).all()):
        raise ValueError("Gap or invalid actual volume in reviewed prefix")
    peak_i = int(x.high.iloc[i:].idxmax())
    baseline = x.raw_vol.iloc[i-baseline_sessions:i]
    advance = x.raw_vol.iloc[i:peak_i+1]
    correction = x.raw_vol.iloc[peak_i+1:]
    if correction.empty:
        raise ValueError("No observed correction after the first-wave high")
    base_mean, base_median = float(baseline.mean()), float(baseline.median())
    rolling = correction.rolling(tail_sessions, min_periods=tail_sessions).mean().dropna()
    tail = correction.tail(tail_sessions)
    return dict(
        cutoff=str(dates.iloc[-1]), launch_date=launch,
        observed_peak_date=str(dates.iloc[peak_i]),
        baseline_first=str(dates.iloc[i-baseline_sessions]),
        baseline_last=str(dates.iloc[i-1]), baseline_sessions=baseline_sessions,
        baseline_mean=base_mean, baseline_median=base_median,
        advance_sessions=len(advance), correction_sessions=len(correction),
        advance_mean_vs_baseline=float(advance.mean()/base_mean),
        correction_mean_vs_baseline=float(correction.mean()/base_mean),
        correction_median_vs_baseline_median=float(correction.median()/base_median),
        correction_p20_vs_baseline_mean=float(correction.quantile(.2)/base_mean),
        correction_min_vs_baseline_mean=float(correction.min()/base_mean),
        correction_days_above_baseline_fraction=float(correction.gt(base_mean).mean()),
        correction_mean_vs_advance=float(correction.mean()/advance.mean()),
        correction_tail_mean_vs_baseline=float(tail.mean()/base_mean),
        tail_sessions_observed=len(tail), rolling_window=tail_sessions,
        correction_min_rolling_mean_vs_baseline=(float(rolling.min()/base_mean) if len(rolling) else None),
        whole_phase_mean_vs_baseline=float(x.raw_vol.iloc[i:].mean()/base_mean),
        whole_phase_median_vs_baseline_median=float(x.raw_vol.iloc[i:].median()/base_median),
        volume_unit="unchanged actual daily volume from raw_vol",
        automatic_gate_applied=False,
    )


def visible(frame: pd.DataFrame, cutoff) -> pd.DataFrame:
    """Truncate before checking gaps, factors, extrema or pivot confirmation."""
    x = frame[frame.datetime.le(pd.Timestamp(cutoff))].copy().reset_index(drop=True)
    if x.empty or x.datetime.duplicated().any():
        raise ValueError("Empty prefix or duplicated timestamp")
    return x


def resistance_lines(prefix: pd.DataFrame, rules: ConfirmationRules) -> list[dict]:
    """Reuse confirmed-pivot primitives, freeze two-touch candidate lines.

    Lines are candidates, not an inferred unique market trend. A shallower,
    longer line can remain overhead after a local steeper line is broken.
    """
    f = prefix.tail(64).copy().reset_index(drop=True)
    if not f.valid_bar.all() or f.adj_factor.nunique() != 1:
        raise ValueError("Line context has missing data or a factor change")
    f["trade_date"] = f.datetime.astype(str)
    pivots = local_pivots(f)
    highs = [p for p in pivots if p["kind"] == "high"]
    result = []
    for a, b in combinations(highs, 2):
        i, j = a["index"], b["index"]
        if j - i < 4 or b["price"] >= a["price"]:
            continue
        slope = (b["price"] - a["price"]) / (j - i)
        line = a["price"] + slope * (np.arange(i, len(f)) - i)
        if np.any(f.high.iloc[i:].to_numpy() > line * (1 + rules.line_tolerance_pct / 100)):
            continue
        result.append(dict(
            first_time=a["trade_date"], second_time=b["trade_date"],
            first_price=a["price"], second_price=b["price"],
            confirmed_on=b["confirmed_on"], span_bars=j-i,
            slope_per_bar=float(slope), price_at_base_end=float(line[-1]),
            status="two_confirmed_touches_candidate",
        ))
    return sorted(result, key=lambda z: (z["second_time"], z["first_time"]))


def freeze_base(frame: pd.DataFrame, start, end, rules=ConfirmationRules()) -> dict:
    x = visible(frame, end)
    w = x[x.datetime.ge(pd.Timestamp(start))]
    if len(w) < 4 or not w.valid_bar.all() or not x.datetime.iloc[-1] == pd.Timestamp(end):
        raise ValueError("Quiet interval incomplete or shorter than four bars")
    if len(x) < 2 * len(w) or x.adj_factor.nunique() != 1:
        raise ValueError("No comparison interval or factor change in context")
    prior = x.iloc[-2*len(w):-len(w)]
    if not prior.valid_bar.all() or prior.vol.mean() <= 0 or w.vol.mean() <= 0:
        raise ValueError("Quiet volume context missing")
    ranges = lambda a: ((a.high - a.low) / a.close).mean()
    lines = resistance_lines(x, rules)
    return dict(
        base_start=str(pd.Timestamp(start)), base_end=str(pd.Timestamp(end)),
        bars=len(w), upper=float(w.high.max()), lower=float(w.low.min()),
        range_pct=float(100*(w.high.max()/w.low.min()-1)),
        volume_mean=float(w.vol.mean()),
        volume_vs_preceding_window=float(w.vol.mean()/prior.vol.mean()),
        candle_range_vs_preceding_window=float(ranges(w)/ranges(prior)),
        previous_session_high=float(x[x.datetime.dt.date.eq(x.datetime.iloc[-1].date())].high.max()),
        adj_factor=float(w.adj_factor.iloc[-1]), lines=lines, rules=asdict(rules),
        selection=("explicit prior-close quiet interval for development-case review"
                   if pd.Timestamp(end).strftime("%H:%M")=="15:00" else
                   "explicit completed intraday quiet interval for development-case review"),
    )


def evaluate_at(frame: pd.DataFrame, base: dict, cutoff, rules=ConfirmationRules()) -> dict:
    x = visible(frame, cutoff)
    at = pd.Timestamp(cutoff)
    if at <= pd.Timestamp(base["base_end"]) or x.datetime.iloc[-1] != at:
        raise ValueError("Decision must be an observed completed bar after base")
    context = x[x.datetime.ge(pd.Timestamp(base["base_start"]))]
    if not context.valid_bar.all() or not np.isclose(context.adj_factor, base["adj_factor"]).all():
        raise ValueError("Observed path contains missing data or a factor change")
    bar = x.iloc[-1]
    span = float(bar.high-bar.low)
    body = float(100*(bar.close/bar.open-1))
    fraction = float((bar.close-bar.open)/span) if span > 0 else 0.
    location = float((bar.close-bar.low)/span) if span > 0 else 0.
    earlier_slot = x.iloc[:-1]
    earlier_slot = earlier_slot[earlier_slot.datetime.dt.strftime("%H:%M").eq(at.strftime("%H:%M"))].tail(5)
    same_slot = float(bar.vol/earlier_slot.vol.mean()) if len(earlier_slot) == 5 and earlier_slot.vol.mean() > 0 else None
    quiet_multiple = float(bar.vol/base["volume_mean"])
    day = x[x.datetime.dt.date.eq(at.date())]
    opening = day.iloc[0]
    # A failed opening breakout must also reclaim its already-observed high.
    opening_pressure = float(opening.high) if (
        opening.close <= opening.open and opening.close <= base["upper"]
    ) else None
    price_clear = bool(bar.close > base["upper"])
    opening_clear = opening_pressure is None or bar.close > opening_pressure
    large_bull = body >= rules.body_pct and fraction >= rules.body_fraction and location >= rules.close_location
    chain = None
    for n in (2, 3):
        w = x.tail(n)
        if (len(w) == n and w.datetime.iloc[0] > pd.Timestamp(base["base_end"])
            and (w.close > w.open).all() and w.close.diff().iloc[1:].gt(0).all()
            and 100*(w.close.iloc[-1]/w.open.iloc[0]-1) >= rules.consecutive_gain_pct
            and w.vol.mean() >= base["volume_mean"]*rules.quiet_volume_multiple):
            chain = dict(bars=n, origin_time=str(w.datetime.iloc[0]), origin_price=float(w.open.iloc[0]))
            break
    lines = []
    for line in base["lines"]:
        projection = line["price_at_base_end"] + line["slope_per_bar"]*(len(x)-len(x[x.datetime.le(base["base_end"])]))
        lines.append({**line, "projected_at_decision":float(projection), "broken_by_close":bool(bar.close > projection)})
    local = lines[-1] if lines else None
    local_clear = local is None or local["broken_by_close"]
    probe = price_clear and quiet_multiple >= rules.quiet_volume_multiple and (large_bull or chain is not None)
    confirmed = bool(probe and opening_clear and local_clear and same_slot is not None and same_slot >= rules.same_slot_volume_multiple)
    return dict(
        decision_time=str(at), close=float(bar.close), body_pct=body,
        body_fraction=fraction, close_location=location,
        volume_vs_quiet_mean=quiet_multiple, volume_same_slot5=same_slot,
        platform_clear=price_clear, failed_opening_high=opening_pressure,
        opening_high_clear=bool(opening_clear), local_line_clear=bool(local_clear),
        large_bull=bool(large_bull), consecutive_bulls=chain,
        probe=bool(probe), confirmed=confirmed, lines=lines,
        trigger_candle_open=float(bar.open),
        launch_origin=float(chain["origin_price"] if chain else bar.open),
        invalidation_base_lower=base["lower"],
    )


def sell_eligibility(request_at, entry_at, calendar: list[str]) -> pd.Timestamp:
    """Earliest continuous-session time allowed by T+1; NOT a guaranteed fill."""
    request, entry = pd.Timestamp(request_at), pd.Timestamp(entry_at)
    if request < entry:
        raise ValueError("Exit request precedes entry")
    if request.date() > entry.date():
        return request
    later = [day for day in calendar if day > entry.strftime("%Y%m%d")]
    if not later:
        raise ValueError("Next trading date not present")
    return pd.Timestamp(later[0]+" 09:31")


def rising_support(prefix: pd.DataFrame, entry_at) -> dict | None:
    """Only post-entry, two-right-bar-confirmed lows can anchor a new line."""
    f = prefix.copy().reset_index(drop=True)
    f["trade_date"] = f.datetime.astype(str)
    lows = [p for p in local_pivots(f)
            if p["kind"] == "low" and pd.Timestamp(p["trade_date"]) >= pd.Timestamp(entry_at)]
    for a, b in reversed(list(combinations(lows, 2))):
        i, j = a["index"], b["index"]
        if j-i < 4 or b["price"] <= a["price"]:
            continue
        slope = (b["price"]-a["price"])/(j-i)
        line = a["price"]+slope*(np.arange(j, len(f))-i)
        if np.any(f.low.iloc[j:].to_numpy() < line*.997):
            continue
        return dict(first_time=a["trade_date"], second_time=b["trade_date"],
                    confirmed_on=b["confirmed_on"], first_price=a["price"],
                    second_price=b["price"], slope_per_bar=float(slope),
                    price_at_creation=float(line[-1]), created_bar=len(f)-1,
                    status="two_confirmed_lows_candidate")
    return None


def monitor_path(frame: pd.DataFrame, entry: dict, cutoff, rules=ConfirmationRules()) -> pd.DataFrame:
    """Evidence flags, not fills or an optimized exit policy.

    Test an existing support line BEFORE allowing a replacement. A straight
    limit-up advance often has no confirmed rising line; expose that absence.
    """
    x = visible(frame, cutoff)
    stamp = pd.Timestamp(entry["decision_time"])
    rows = []
    peak_close = float(entry["close"])
    line = None
    line_broken_latched = False
    broken_count = 0
    order_run = 0
    first_request = None
    post = x[x.datetime.gt(stamp)]
    for count, idx in enumerate(post.index, 1):
        b = x.iloc[idx]
        if not b.valid_bar or not np.isclose(b.adj_factor, entry["signal_factor"]):
            rows.append(dict(time=str(b.datetime), data_status="unknown", flags="data_gap_or_factor_change"))
            break
        ordered = all(b[f"ma{a}"] > b[f"ma{c}"] for a, c in ((5, 10), (10, 20)))
        ordered &= all(b[f"rise{n}"] > 0 for n in (5, 10, 20))
        order_run = order_run+1 if ordered else 0
        accelerated = peak_close/entry["close"]-1 >= .08
        flags = []
        if b.close < entry["trigger_candle_open"]:
            flags.append("trigger_open_lost_warning")
        if b.close < entry["launch_origin"]:
            flags.append("launch_origin_failed")
        if b.close < entry["base_upper"]:
            flags.append("back_inside_platform")
        if b.close < entry["invalidation_base_lower"]:
            flags.append("base_floor_failed")
        if count >= 16 and peak_close/entry["close"] < 1.02 and order_run < 3:
            flags.append("no_followthrough_16bars")
        if accelerated and b.body_pct <= -1.5:
            flags.append("fast_rise_first_large_bear")
        if accelerated and b.close < b.ma20*entry["price_scale"]:
            flags.append("fast_rise_below_ma20")
        support = None
        if line:
            support = line["price_at_creation"]+line["slope_per_bar"]*(idx-line["created_bar"])
            broken_count = broken_count+1 if b.close < support*(1-rules.line_tolerance_pct/100) else 0
            if broken_count >= 2 or (broken_count and b.body_pct <= -1.5):
                flags.append("frozen_rising_line_broken")
                line_broken_latched = True
        if flags and first_request is None:
            first_request = str(b.datetime)
        peak_close = max(peak_close, float(b.close))
        rows.append(dict(time=str(b.datetime), close=float(b.close), bars_since_confirmation=count,
                         short_ma_order_rising=bool(ordered), short_order_run=order_run,
                         accelerated=accelerated, support_line=support,
                         line_anchor1=line["first_time"] if line else None,
                         line_anchor2=line["second_time"] if line else None,
                         flags="|".join(flags), first_exit_evidence_at=first_request,
                         data_status="known"))
        # Once broken, the old line cannot be silently refitted to suppress it.
        if line_broken_latched:
            line = None
        elif broken_count == 0:
            candidate = rising_support(x.iloc[:idx+1], stamp)
            if candidate:
                line = candidate
    result = pd.DataFrame(rows)
    if "support_line" in result:
        # An unavailable line must have the same dtype in a short prefix and a
        # later trace; future availability cannot change the meaning of None.
        result["support_line"] = pd.to_numeric(result["support_line"], errors="coerce")
    return result
