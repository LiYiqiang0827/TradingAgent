"""Causal daily first-wave pool + 15m coil entry + state-dependent exits.

Development rules; completed bars only. Execution is a separate, T+1 aware
minute-open proxy. No fitted parameters, future themes or real orders.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from bisect import bisect_left
import json
from pathlib import Path

import numpy as np
import pandas as pd

from study_fifteen_min_ma_coil import (
    Rules as CoilRules,
    features as minute_features,
    rising_run,
)
from replay_fifteen_min_ma_coil import minute_slots, net


@dataclass(frozen=True)
class CombinedRules:
    version: str = "daily_2to4_board_intraday_coil_v1"
    board_min: int = 2
    board_max: int | None = 4
    allow_three_day_two_board: bool = True
    board_lookback_days: int = 60
    trough_lookback_days: int = 30
    new_high_days: int = 120
    wave_gain_max_pct: float = 100.0
    rise_days: int = 20
    daily_near_pct: float = 3.0
    pullback_min_pct: float = 5.0
    peak_age_max_days: int = 35
    contraction_min_days: int = 9
    wave_volume_multiple: float = 1.5
    pullback_volume_multiple: float = 1.5
    start_lags: tuple[int, ...] = (2, 3)
    restart_body_pct: float = 1.0
    restart_body_fraction: float = 0.6
    restart_volume_multiple: float = 1.5
    failure_bars: int = 16
    sideways_bars: int = 32
    fast_gain_pct: float = 3.0
    acceleration_bars: int = 4
    acceleration_ratio: float = 2.0
    acceleration_recent_gain_pct: float = 3.0
    large_bear_pct: float = 1.0
    large_bear_body_fraction: float = 0.6
    drawdown_pct: float = 5.0
    # The v1 defaults preserve the already frozen initial replay. The revised
    # factory below applies the user's launch/20cm/daily-coil clarifications.
    wave_origin: str = "trough_low"
    allow_twenty_pct_equivalent: bool = False
    episode_gap_days: int = 10
    daily_order_mode: str = "strict"
    volatility_mode: str = "three_tr_chunks"
    daily_coil_relaxation: bool = False
    daily_short_coil_pct: float = 7.0
    relaxed_intraday_short_pct: float = 2.0
    touch_memory_days: int = 5


def revised_rules() -> CombinedRules:
    """Clarified development rules, not a fit to subsequent trade returns."""
    return replace(
        CombinedRules(),
        version="daily_launch_close_intraday_conditional_coil_v2",
        wave_origin="first_limit_close",
        allow_twenty_pct_equivalent=True,
        daily_order_mode="wave_then_pullback",
        volatility_mode="atr14_trend",
        daily_coil_relaxation=True,
    )


def save_combined_rules(path: Path, rules: CombinedRules) -> None:
    payload = json.dumps(asdict(rules), ensure_ascii=False, indent=2)
    if path.exists() and path.read_text() != payload:
        raise ValueError("Refusing to change frozen combined rules")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)


def prepare_daily(
    raw: pd.DataFrame, adj: pd.DataFrame, calendar: list[str]
) -> pd.DataFrame:
    x = raw.copy()
    x.trade_date = x.trade_date.astype(str)
    a = adj.copy()
    a.trade_date = a.trade_date.astype(str)
    x = x.drop(columns=["adj_factor"], errors="ignore").merge(
        a[["trade_date", "adj_factor"]],
        on="trade_date",
        validate="one_to_one",
        how="left",
    )
    x = (
        x.set_index("trade_date")
        .reindex(calendar)
        .rename_axis("trade_date")
        .reset_index()
    )
    for col in ("open", "high", "low", "close", "vol", "adj_factor"):
        x[col] = pd.to_numeric(x[col], errors="coerce")
    valid = (
        x[["open", "high", "low", "close", "vol", "adj_factor"]].notna().all(axis=1)
        & x[["open", "high", "low", "close", "adj_factor"]].gt(0).all(axis=1)
        & x.vol.gt(0)
        & x.high.ge(x[["open", "close", "low"]].max(axis=1))
        & x.low.le(x[["open", "close", "high"]].min(axis=1))
    )
    x["valid_day"] = valid
    for col in ("open", "high", "low", "close"):
        x[f"raw_{col}"] = x[col]
        x[col] = (x[col] * x.adj_factor).where(valid)
    x["raw_vol"] = x.vol.where(valid)
    for n in (5, 10, 20, 30, 60, 120):
        x[f"ma{n}"] = x.close.rolling(n).mean()
        x[f"rise{n}"] = rising_run(x[f"ma{n}"])
    prior = x.close.shift()
    tr = pd.concat(
        [x.high - x.low, (x.high - prior).abs(), (x.low - prior).abs()], axis=1
    ).max(axis=1)
    x["true_range_pct"] = (100 * tr / prior).where(valid & prior.notna())
    x["atr14_pct"] = x.true_range_pct.rolling(14).mean()
    return x


def _daily_pool_snapshot(
    daily: pd.DataFrame,
    events: pd.DataFrame,
    as_of: str,
    rules: CombinedRules,
    anchor_override: pd.Series | None = None,
) -> dict:
    """Use only as_of and earlier completed days; never today's daily candle."""
    x = daily[daily.trade_date.le(as_of)].reset_index(drop=True)
    result = {
        "context_date": as_of,
        "eligible": False,
        "reason": "daily_history_missing",
    }
    if len(x) < 160 or x.iloc[-1].trade_date != as_of or not x.iloc[-1].valid_day:
        return result
    latest = x.iloc[-1]
    last_i = len(x) - 1
    e = events[
        events.trade_date.le(as_of)
        & events.trade_date.ge(x.iloc[-rules.board_lookback_days].trade_date)
    ]
    anchors = e[e.qualifying_board.ge(rules.board_min)]
    if anchors.empty:
        return {**result, "reason": "no_recent_2plus_or_3day2_board"}
    anchor = (
        anchors.sort_values("trade_date").iloc[-1]
        if anchor_override is None
        else anchor_override
    )
    indices = {d: i for i, d in enumerate(x.trade_date)}
    ai = indices.get(str(anchor.trade_date))
    if ai is None or ai < rules.trough_lookback_days:
        return result
    if rules.wave_origin == "first_limit_close":
        ti = indices.get(str(anchor.wave_start_date))
        if ti is None or ti < 20:
            return {**result, "reason": "daily_history_before_launch_missing"}
        before = x.iloc[ti - 20 : ti]
        reference_price = x.iloc[ti].close
        wave_tail = x.iloc[ti : last_i + 1]
    else:
        before = x.iloc[ai - rules.trough_lookback_days : ai]
        ti = int(before.low.idxmin())
        reference_price = x.iloc[ti].low
        wave_tail = x.iloc[ai : last_i + 1]
    if not before.valid_day.all():
        return {**result, "reason": "daily_gap_before_wave"}
    if not wave_tail.valid_day.all():
        return {**result, "reason": "daily_gap_in_wave"}
    pi = int(wave_tail.high.idxmax())
    peak = x.iloc[pi]
    stage_boards = e[e.trade_date.between(x.iloc[ti].trade_date, peak.trade_date)]
    max_board = int(stage_boards.qualifying_board.max())
    gain = 100 * (peak.high / reference_price - 1)
    ordered = lambda bar: all(
        pd.notna(bar[f"ma{a}"])
        and pd.notna(bar[f"ma{b}"])
        and bar[f"ma{a}"] >= bar[f"ma{b}"]
        for a, b in zip((5, 10, 20, 30, 60), (10, 20, 30, 60, 120))
    )
    peak_prior = x.iloc[max(0, pi - rules.new_high_days) : pi]
    correction = x.iloc[pi + 1 : last_i + 1]
    tr_means = [np.nan] * 3
    if (
        len(correction) >= rules.contraction_min_days
        and correction.true_range_pct.notna().all()
    ):
        tr_means = [
            float(part.mean())
            for part in np.array_split(correction.true_range_pct.to_numpy(), 3)
        ]
    baseline = x.raw_vol.iloc[ti - 20 : ti].mean() if ti >= 20 else np.nan
    first_volume = x.raw_vol.iloc[ti : pi + 1].mean() / baseline
    pullback_volume = correction.raw_vol.mean() / baseline
    targets = [n for n in (20, 30, 60) if latest[f"rise{n}"] >= rules.rise_days]
    daily_short = [latest[f"ma{n}"] for n in (5, 10, 20)]
    daily_short_pct = 100 * (max(daily_short) / min(daily_short) - 1)
    daily_relaxed = bool(
        rules.daily_coil_relaxation
        and daily_short_pct <= rules.daily_short_coil_pct + 1e-10
    )
    bullish_peak = ordered(peak)
    bullish_now = ordered(latest)
    if rules.daily_order_mode == "wave_then_pullback":
        # The advance must put the short averages above both long averages.
        # MA60/120 can cross later; short averages may intertwine in a pullback.
        bullish_peak = bool(
            all(peak[f"ma{n}"] >= max(peak.ma60, peak.ma120) for n in (5, 10, 20, 30))
            and peak.ma5 >= peak.ma10 >= peak.ma20 >= peak.ma30
            and peak.rise60 > 0
            and peak.rise120 > 0
        )
        bullish_now = bool(
            all(
                latest[f"ma{n}"] >= max(latest.ma60, latest.ma120)
                for n in (5, 10, 20, 30)
            )
            and latest.ma60 >= latest.ma120
            and (daily_relaxed or ordered(latest))
        )
    atr = x.atr14_pct if "atr14_pct" in x else x.true_range_pct.rolling(14).mean()
    atr_values = atr.iloc[pi + 1 : last_i + 1]
    atr_slope = np.nan
    contracts = bool(tr_means[0] > tr_means[1] > tr_means[2])
    if rules.volatility_mode == "atr14_trend":
        if len(atr_values) >= rules.contraction_min_days and atr_values.notna().all():
            atr_slope = float(np.polyfit(np.arange(len(atr_values)), atr_values, 1)[0])
            contracts = bool(
                atr_slope < 0
                and atr_values.iloc[-3:].mean() < atr_values.iloc[:3].mean()
            )
        else:
            contracts = False
    touches = []
    for j in range(max(pi + 1, last_i - rules.touch_memory_days + 1), last_i + 1):
        bar = x.iloc[j]
        for n in (20, 30, 60):
            ma = bar[f"ma{n}"]
            if (
                bar.valid_day
                and pd.notna(ma)
                and bar[f"rise{n}"] >= rules.rise_days
                and latest[f"rise{n}"] >= rules.rise_days
                and bar.low <= ma * (1 + rules.daily_near_pct / 100)
                and bar.high >= ma * (1 - rules.daily_near_pct / 100)
            ):
                touches.append((j, n))
    gates = {
        "first_wave_height": max_board >= rules.board_min
        and (rules.board_max is None or max_board <= rules.board_max),
        "first_wave_gain": gain <= rules.wave_gain_max_pct,
        "stage_120d_new_high": len(peak_prior) == rules.new_high_days
        and peak_prior.valid_day.all()
        and peak.high > peak_prior.high.max(),
        "daily_bullish_at_peak": bullish_peak,
        "daily_bullish_now": bullish_now,
        "long_ma20day_rise": max(latest.rise60, latest.rise120) >= rules.rise_days,
        "medium_ma20day_rise": max(latest.rise20, latest.rise30) >= rules.rise_days,
        "rising_target_exists": bool(targets),
        "pullback_depth": 100 * (1 - latest.close / peak.high)
        >= rules.pullback_min_pct,
        "peak_recent": last_i - pi <= rules.peak_age_max_days,
        "volatility_contracts": contracts,
        "first_wave_volume": bool(first_volume >= rules.wave_volume_multiple),
        "pullback_volume": bool(pullback_volume >= rules.pullback_volume_multiple),
    }
    if rules.wave_origin == "first_limit_close":
        gates["recent_rising_ma_touch"] = bool(touches)
    result.update(
        eligible=all(gates.values()),
        reason=(
            "eligible"
            if all(gates.values())
            else "|".join(k for k, v in gates.items() if not v)
        ),
        daily_gate_json=json.dumps({k: bool(v) for k, v in gates.items()}),
        first_wave_board_max=max_board,
        first_wave_continuous_board_max=int(stage_boards.continuous_board.max()),
        first_wave_board_day=str(anchor.trade_date),
        first_wave_board_type=str(anchor.board_type),
        wave_start_date=str(x.iloc[ti].trade_date),
        wave_origin=rules.wave_origin,
        wave_reference_price=float(reference_price),
        peak_date=str(peak.trade_date),
        stage_gain_pct=float(gain),
        peak_age_days=last_i - pi,
        volatility_early_pct=tr_means[0],
        volatility_middle_pct=tr_means[1],
        volatility_late_pct=tr_means[2],
        atr14_start_pct=float(atr_values.iloc[0]) if len(atr_values) else np.nan,
        atr14_end_pct=float(atr_values.iloc[-1]) if len(atr_values) else np.nan,
        atr14_slope=atr_slope,
        daily_short_coil_pct=float(daily_short_pct),
        daily_relaxed_intraday=daily_relaxed,
        recent_rising_ma_touch=bool(touches),
        last_touch_date=str(x.iloc[touches[-1][0]].trade_date) if touches else None,
        last_touch_ma=touches[-1][1] if touches else None,
        first_wave_volume_vs_pre=float(first_volume),
        pullback_volume_vs_pre=float(pullback_volume),
        rising_targets="|".join(map(str, targets)),
        daily_close=float(latest.close),
        name=str(anchor["name"]),
        prior_theme_name=str(anchor.lu_desc),
    )
    for n in (5, 10, 20, 30, 60, 120):
        result[f"daily_ma{n}"] = float(latest[f"ma{n}"])
        result[f"daily_rise{n}"] = int(latest[f"rise{n}"])
    return result


def daily_pool_snapshot(
    daily: pd.DataFrame, events: pd.DataFrame, as_of: str, rules: CombinedRules
) -> dict:
    """Evaluate every still-visible launch episode; a new board does not erase
    the earlier wave's pullback. Select by rules, never by subsequent return.
    """
    if rules.wave_origin == "trough_low" or "wave_start_date" not in events:
        return _daily_pool_snapshot(daily, events, as_of, rules)
    x = daily[daily.trade_date.le(as_of)]
    if len(x) < rules.board_lookback_days:
        return _daily_pool_snapshot(daily, events, as_of, rules)
    e = events[
        events.trade_date.between(
            str(x.iloc[-rules.board_lookback_days].trade_date), as_of
        )
        & events.qualifying_board.ge(rules.board_min)
    ]
    if e.empty:
        return _daily_pool_snapshot(daily, events, as_of, rules)
    results = []
    for _, episode in e.groupby("wave_start_date", sort=False):
        anchor = episode.sort_values("trade_date").iloc[-1]
        results.append(_daily_pool_snapshot(daily, events, as_of, rules, anchor))
    eligible = [r for r in results if r["eligible"]]
    if eligible:
        result = max(eligible, key=lambda r: r["wave_start_date"])
    else:
        result = min(
            results,
            key=lambda r: (len(r["reason"].split("|")), r.get("wave_start_date", "")),
        )
    return {**result, "launch_episodes_evaluated": len(results)}


def relaxed_restart_alignment(bar: pd.Series) -> bool:
    """The user-approved daily-coil branch: strict short four, rising long two."""
    return bool(
        all(bar[f"ma{a}"] > bar[f"ma{b}"] for a, b in zip((5, 10, 20), (10, 20, 30)))
        and all(bar[f"rise{n}"] > 0 for n in (5, 10, 20, 30, 60, 120))
    )


def combined_entries(
    f: pd.DataFrame,
    pool: dict[str, dict],
    calendar: list[str],
    first: str,
    last: str,
    rules: CombinedRules,
) -> pd.DataFrame:
    """Candidate at prior close; coil at k; launch decision at k+2/k+3."""
    base_factor = float(f.adj_factor.dropna().iloc[0])
    date_index = {d: i for i, d in enumerate(calendar)}
    rows = []
    emitted = set()
    for i in range(3, len(f)):
        now = f.iloc[i]
        day = now.datetime.strftime("%Y%m%d")
        if not first <= day <= last or day in emitted:
            continue
        prior = calendar[date_index[day] - 1]
        daily = pool.get(prior, {})
        if not daily.get("eligible", False):
            continue
        for lag in rules.start_lags:
            k = i - lag
            coil = f.iloc[k]
            if not f.valid_bar.iloc[k : i + 1].all():
                continue
            # The coil's daily context must already be available at its own time.
            coil_day = coil.datetime.strftime("%Y%m%d")
            coil_prior = calendar[date_index[coil_day] - 1]
            coil_daily = pool.get(coil_prior, {})
            if not coil_daily.get("eligible", False):
                continue
            relaxed = bool(
                rules.daily_coil_relaxation
                and coil_daily.get("daily_relaxed_intraday", False)
                and daily.get("daily_relaxed_intraday", False)
            )
            if relaxed:
                ready = bool(
                    coil.coil30_pct <= rules.relaxed_intraday_short_pct + 1e-10
                    and coil.ma60 >= coil.ma120
                    and coil.rise60 > 0
                    and coil.rise120 > 0
                    and coil.p_close >= coil.ma120 * 0.985
                    and coil.origin != "none"
                )
                if not ready or not relaxed_restart_alignment(now):
                    continue
            elif not coil.coil_ready or not now.strict_restart_alignment:
                continue
            price = float(coil.p_close * base_factor)
            target_distances = {
                n: abs(100 * (price / coil_daily[f"daily_ma{n}"] - 1))
                for n in (20, 30, 60)
                if coil_daily[f"daily_rise{n}"] >= rules.rise_days
            }
            targets = [
                n for n, d in target_distances.items() if d <= rules.daily_near_pct
            ]
            if rules.wave_origin == "first_limit_close":
                if not coil_daily.get("recent_rising_ma_touch", False):
                    continue
                # The daily support touch can precede the intraday coil by days.
                # Do not require the already rebounding price to retouch the MA.
                targets = [int(coil_daily["last_touch_ma"])]
            elif not targets:
                continue
            launch = f.iloc[k + 1 : i + 1]
            single = (
                now.body_pct >= rules.restart_body_pct
                and now.body_fraction >= rules.restart_body_fraction
                and now.volume_multiple8 >= rules.restart_volume_multiple
                and now.close_location >= 0.7
            )
            cumulative_gain = 100 * (now.p_close / launch.iloc[0].p_open - 1)
            reference = f.vol.iloc[k - lag + 1 : k + 1]
            sequence = (
                launch.body_pct.gt(0).all()
                and launch.p_close.diff().dropna().ge(0).all()
                and cumulative_gain >= rules.restart_body_pct
                and launch.vol.sum() >= rules.restart_volume_multiple * reference.sum()
                and reference.notna().all()
                and reference.sum() > 0
            )
            if not (single or sequence) or not now.pre_restart_range_pct <= 10.0:
                continue
            if now.p_close <= coil.coil30_high * 1.001:
                continue
            target = min(targets, key=target_distances.get)
            factor = float(now.adj_factor)
            scale = base_factor / factor
            row = {
                "signal_time": str(now.datetime),
                "signal_close": float(now.close),
                "signal_factor": factor,
                "coil_time": str(coil.datetime),
                "coil_branch": "daily_relaxed" if relaxed else "dual",
                "coil_spread_pct": float(coil.coil30_pct),
                "coil_short_pct": float(coil.coil30_pct),
                "coil_with_ma60_pct": float(coil.coil_with_ma60_pct),
                "coil_low_raw": float(coil.coil30_low * scale),
                "coil_high_raw": float(coil.coil30_high * scale),
                "rule_coil_pct": rules.relaxed_intraday_short_pct if relaxed else 1.0,
                "rule_coil_with_ma60_pct": None if relaxed else 2.0,
                "rule_coil_mode": "daily_relaxed" if relaxed else "dual",
                "coil_to_restart_bars": lag,
                "launch_kind": "medium_large_bull" if single else "consecutive_bulls",
                "launch_start_time": str(
                    now.datetime if single else launch.iloc[0].datetime
                ),
                "launch_start_raw": float(
                    (now.p_open if single else launch.iloc[0].p_open) * scale
                ),
                "daily_target_ma": target,
                "daily_target_distance_pct": target_distances[target],
                "daily_snapshot_time": coil_prior + " 15:00:00",
                "entry_daily_context_date": prior,
                "restart_volume_multiple8": float(now.volume_multiple8),
                "restart_body_pct": float(now.body_pct),
                "pre_restart_range_pct": float(now.pre_restart_range_pct),
                **coil_daily,
            }
            rows.append(row)
            emitted.add(day)
            break
    return pd.DataFrame(rows)


def exit_decisions(
    f: pd.DataFrame,
    signal: dict,
    entry_time: str,
    entry_price: float,
    rules: CombinedRules,
) -> tuple[list[dict], str]:
    """Completed 15m state transitions. First exit request is irrevocable.

    Entry/launch prices and f prices share the signal-date adjusted scale.
    A same-day request is retained until execution becomes possible next day.
    """
    signal_stamp = pd.Timestamp(signal["signal_time"])
    base = float(f.adj_factor.dropna().iloc[0])
    scale = base / float(signal["signal_factor"])
    view = f[f.datetime.gt(signal_stamp)].copy()
    peak = entry_price
    floor = entry_price
    closes = [entry_price]
    acceleration = False
    count = 0
    trace = []
    for row in view.itertuples():
        if row.datetime <= pd.Timestamp(entry_time):
            continue
        if not row.valid_bar:
            return trace, "holding_15m_missing"
        if abs(float(row.adj_factor) / float(signal["signal_factor"]) - 1) > 1e-5:
            return trace, "holding_factor_change"
        price = float(row.p_close * scale)
        high = float(row.p_high * scale)
        low = float(row.p_low * scale)
        peak = max(peak, high)
        floor = min(floor, low)
        count += 1
        closes.append(price)
        n = rules.acceleration_bars
        if len(closes) >= 2 * n + 1:
            recent = 100 * (closes[-1] / closes[-n - 1] - 1)
            previous = 100 * (closes[-n - 1] / closes[-2 * n - 1] - 1)
            if (
                recent >= rules.acceleration_recent_gain_pct
                and previous > 0
                and recent >= rules.acceleration_ratio * previous
            ):
                acceleration = True
        order = all(
            getattr(row, f"ma{a}") >= getattr(row, f"ma{b}")
            for a, b in zip((5, 10, 20, 30, 60), (10, 20, 30, 60, 120))
        )
        # A single upper wick is not sustained rapid appreciation.
        sideways = 100 * (max(closes) / entry_price - 1) < rules.fast_gain_pct
        cause = None
        if count <= rules.failure_bars and price <= float(signal["launch_start_raw"]):
            cause = "failed_restart_16bars"
        elif sideways and not order:
            cause = "sideways_lost_alignment"
        elif sideways and count >= rules.sideways_bars:
            cause = "sideways_32bars"
        elif (
            acceleration
            and row.body_pct <= -rules.large_bear_pct
            and -row.body_fraction >= rules.large_bear_body_fraction
        ):
            cause = "acceleration_first_large_bear"
        elif acceleration and price < float(row.ma20 * scale):
            cause = "acceleration_below_ma20"
        elif price < float(row.ma30 * scale):
            cause = "trend_below_ma30"
        elif row.datetime.strftime("%H:%M") == "15:00" and price <= peak * (
            1 - rules.drawdown_pct / 100
        ):
            cause = "drawdown5_unrecovered_at_close"
        trace.append(
            dict(
                time=str(row.datetime),
                close=price,
                peak=peak,
                bars=count,
                acceleration=acceleration,
                sideways=sideways,
                reason=cause,
            )
        )
        if cause:
            return trace, "exit_requested"
    return trace, "open_at_cutoff"


def execute_dynamic(
    signal: dict,
    f: pd.DataFrame,
    one: pd.DataFrame,
    calendar: list[str],
    limits: dict,
    preclose: dict,
    rules: CombinedRules,
    allow_limit_fill: bool = False,
) -> tuple[dict, list[dict]]:
    """Use completed 15m decisions; >=60s delay and actual next legal minute.

    Limit fills are a separately reported user assumption. Missing quotes are
    retained as unresolved, never bridged or converted into a theoretical stop.
    """
    result = dict(
        entry_status="entry_right_censored",
        buyable_proxy=False,
        entry_time=None,
        entry_open=None,
        entry_bar_end=None,
        exit_status="not_entered",
        exit_time=None,
        exit_open=None,
        net_pct=None,
        exit_reason=None,
        exit_request_time=None,
        tplus1_deferred=False,
        blocked_exit_minutes=0,
        limit_fill_assumption=False,
        mfe_pct=None,
        mae_pct=None,
        mark_to_market_pct=None,
    )
    if "ST" in str(signal.get("name", "")).upper() or "退" in str(
        signal.get("name", "")
    ):
        return {**result, "entry_status": "excluded_st_name"}, []
    age = signal.get("listing_age_days")
    if age is not None and pd.notna(age) and age < 365:
        return {**result, "entry_status": "excluded_recent_listing"}, []
    stamp = pd.Timestamp(signal["signal_time"])
    code = signal["ts_code"]
    day = stamp.strftime("%Y%m%d")
    upper = limits.get((day, code), (None, None))[0]
    assumed = bool(
        allow_limit_fill
        and upper is not None
        and abs(signal["signal_close"] - upper) <= 0.005
    )
    schedule = minute_slots(calendar)
    idx = bisect_left(schedule, stamp + pd.Timedelta(minutes=1 if assumed else 2))
    if idx >= len(schedule) and not assumed:
        return result, []
    end = schedule[idx] if idx < len(schedule) else stamp
    b = one.copy()
    b["datetime"] = pd.to_datetime(b.datetime)
    duplicates = set(b.loc[b.datetime.duplicated(keep=False), "datetime"])
    lookup = {row.datetime: row for row in b.itertuples(index=False)}
    entry_time = stamp if assumed else end - pd.Timedelta(minutes=1)
    entry_day = entry_time.strftime("%Y%m%d")
    cap, down = limits.get((entry_day, code), (None, None))
    previous = preclose.get((entry_day, code))
    if cap is None or down is None or previous is None or previous <= 0:
        return {**result, "entry_status": "entry_limits_missing"}, []
    if cap / previous <= 1.075:
        return {**result, "entry_status": "excluded_st_limit_regime"}, []
    if not assumed:
        if end in duplicates:
            return {**result, "entry_status": "duplicate_entry_minute"}, []
        if end not in lookup:
            return {**result, "entry_status": "entry_minute_missing"}, []
        r = lookup[end]
        if (
            not np.isfinite([r.open, r.high, r.low, r.close, r.vol, r.adj_factor]).all()
            or r.vol <= 0
        ):
            return {**result, "entry_status": "entry_invalid_minute"}, []
        if abs(r.adj_factor / signal["signal_factor"] - 1) > 1e-5:
            return {**result, "entry_status": "entry_factor_change"}, []
        if r.open >= cap - 0.005:
            return {**result, "entry_status": "entry_at_upper_limit"}, []
        if not down - 0.011 <= r.open <= cap + 0.011:
            return {**result, "entry_status": "entry_outside_limits"}, []
        entry = float(r.open)
    else:
        entry = float(upper)
    result.update(
        entry_status="limit_fill_assumption" if assumed else "open_proxy",
        buyable_proxy=True,
        entry_time=str(entry_time),
        entry_open=entry,
        entry_bar_end=str(end),
        limit_fill_assumption=assumed,
    )
    # The first post-signal 15m high/low may include time before minute entry.
    # Replace its extrema with only observed holding minutes; do not credit a
    # pre-entry high as a trailing-profit high-water mark.
    adjusted = f.copy()
    next_bars = adjusted[adjusted.datetime.gt(stamp)]
    if len(next_bars) and idx < len(schedule):
        first = next_bars.index[0]
        first_end = adjusted.loc[first, "datetime"]
        minutes = b[b.datetime.ge(end) & b.datetime.le(first_end)]
        if len(minutes):
            base = float(adjusted.adj_factor.dropna().iloc[0])
            factor = float(signal["signal_factor"])
            adjusted.loc[first, "p_high"] = float(minutes.high.max()) * factor / base
            adjusted.loc[first, "p_low"] = float(minutes.low.min()) * factor / base
    trace, decision_status = exit_decisions(
        adjusted, signal, str(entry_time), entry, rules
    )
    request = trace[-1] if trace and trace[-1]["reason"] else None
    if request:
        result.update(
            exit_request_time=request["time"],
            exit_reason=request["reason"],
            tplus1_deferred=pd.Timestamp(request["time"]).strftime("%Y%m%d")
            == entry_day,
        )
    request_stamp = pd.Timestamp(request["time"]) if request else None
    peak = entry
    floor = entry
    last_close = entry
    for t in schedule[idx:]:
        if t in duplicates:
            return {**result, "exit_status": "holding_duplicate_minute"}, trace
        r = lookup.get(t)
        if r is None:
            return {**result, "exit_status": "holding_minute_missing"}, trace
        if not np.isfinite([r.open, r.high, r.low, r.close, r.vol, r.adj_factor]).all():
            return {**result, "exit_status": "holding_invalid_minute"}, trace
        if abs(r.adj_factor / signal["signal_factor"] - 1) > 1e-5:
            return {**result, "exit_status": "holding_factor_change"}, trace
        open_time = t - pd.Timedelta(minutes=1)
        now = t.strftime("%Y%m%d")
        if (
            request_stamp is not None
            and open_time >= request_stamp + pd.Timedelta(minutes=1)
            and now > entry_day
        ):
            upper, lower = limits.get((now, code), (None, None))
            if upper is None or lower is None:
                return {**result, "exit_status": "exit_limits_missing"}, trace
            if not lower - 0.011 <= r.open <= upper + 0.011:
                return {**result, "exit_status": "exit_outside_limits"}, trace
            if r.vol > 0 and r.open > lower + 0.005:
                result.update(
                    exit_status="closed_proxy",
                    exit_time=str(open_time),
                    exit_open=float(r.open),
                    net_pct=100 * net(entry, float(r.open)),
                    mfe_pct=100 * (max(peak, r.open) / entry - 1),
                    mae_pct=100 * (min(floor, r.open) / entry - 1),
                )
                return result, trace
            result["blocked_exit_minutes"] += 1
        peak = max(peak, float(r.high))
        floor = min(floor, float(r.low))
        last_close = float(r.close)
    result.update(
        exit_status=("blocked_until_cutoff" if request else decision_status),
        mfe_pct=100 * (peak / entry - 1),
        mae_pct=100 * (floor / entry - 1),
        mark_to_market_pct=100 * net(entry, last_close),
    )
    return result, trace
