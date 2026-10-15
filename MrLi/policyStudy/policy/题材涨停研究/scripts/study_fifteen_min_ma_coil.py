"""Causal 15-minute trend/pullback/MA-coil/restart experiment.

The September examples are development examples, not an untouched holdout.
Signals only use completed bars. No orders, future pivots or parameter search.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


TIMES = ("09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30",
         "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00")
MA_WINDOWS = (5, 10, 20, 30, 60, 120, 250)


@dataclass(frozen=True)
class Rules:
    version: str = "15m_ma_coil_v3_dual_group"
    coil_pct: float = 1.0
    coil_with_ma60_pct: float = 2.0
    coil_mode: str = "dual"
    volume_week_bars: int = 80
    impulse_body_pct: float = 1.5
    impulse_top_rank: int = 3
    impulse_body_fraction: float = .6
    impulse_max_age: int = 160
    long_rising_bars: int = 32
    price_history_bars: int = 32
    price_above_share: float = .75
    long_ma_proximity_pct: float = 1.5
    fallback_ma120_rising_bars: int = 60
    fallback_ma60_rising_bars: int = 16
    alignment_history_bars: int = 16
    alignment_share: float = .5
    alignment_tolerance_pct: float = .3
    peak_window_bars: int = 80
    pullback_min_bars: int = 4
    pullback_min_pct: float = .5
    pullback_max_rate_pct: float = .5
    pullback_recent_bear_body_pct: float = 1.5
    restart_wait_bars: int = 2
    restart_body_pct: float = .5
    restart_volume_multiple: float = 1.5
    restart_volume_baseline_bars: int = 8
    restart_body_fraction: float = .5
    restart_close_location: float = .7
    restart_above_coil_pct: float = .1
    compression_bars: int = 80
    compression_range_pct: float = 10.
    strict_restart_alignment: bool = True
    deduplicate_stock_session: bool = True

    def __post_init__(self) -> None:
        if self.coil_mode not in ("dual", "legacy_or"):
            raise ValueError("coil_mode must be dual or legacy_or")
        for value in (self.coil_pct, self.coil_with_ma60_pct):
            if not np.isfinite(value) or value <= 0:
                raise ValueError("Coil thresholds must be finite positive percentages")


def rules_from_dict(payload: dict) -> Rules:
    """Older frozen JSON lacks coil_mode: preserve its original OR definition."""
    values = dict(payload)
    if "coil_mode" not in values:
        if values.get("version", "").startswith("15m_ma_coil_v3_dual_group"):
            raise ValueError("Dual-group rule file must explicitly record coil_mode")
        values["coil_mode"] = "legacy_or"
    return Rules(**values)


def load_rules(path: Path) -> Rules:
    return rules_from_dict(json.loads(path.read_text()))


def coil_description(row: pd.Series) -> str:
    if row.get("rule_coil_mode") == "dual":
        return (f"第一组 {row.coil_short_pct:.3f}%≤{row.rule_coil_pct:g}% / "
                f"加入MA60 {row.coil_with_ma60_pct:.3f}%≤{row.rule_coil_with_ma60_pct:g}%")
    return f"{row.coil_branch}组 {row.coil_spread_pct:.3f}%（原分支口径）"


def coil_features(frame: pd.DataFrame, rules: Rules) -> pd.DataFrame:
    """Both constraints refer to the SAME completed bar, including its close.

    Keep the two historical branch metrics for reproducibility. The extended
    group includes MA30 as well as MA60; it does not substitute one for the other.
    """
    result = frame.copy()
    groups = {
        "coil30": ("p_close", "ma5", "ma10", "ma20", "ma30"),
        "coil60": ("p_close", "ma5", "ma10", "ma20", "ma60"),
        "coil_with_ma60": ("p_close", "ma5", "ma10", "ma20", "ma30", "ma60"),
    }
    for key, cols in groups.items():
        values = result[list(cols)]
        complete = values.notna().all(axis=1) & values.gt(0).all(axis=1)
        high = values.max(axis=1).where(complete)
        low = values.min(axis=1).where(complete)
        result[f"{key}_pct"] = 100 * (high / low - 1)
        result[f"{key}_high"] = high
        result[f"{key}_low"] = low
    # Inclusive decimal percentage boundaries should survive binary round-off.
    eps = 1e-10 if rules.coil_mode == "dual" else 0.
    result["coil_short_ok"] = result.coil30_pct.le(rules.coil_pct + eps)
    result["coil_with_ma60_ok"] = result.coil_with_ma60_pct.le(rules.coil_with_ma60_pct + eps)
    if rules.coil_mode == "dual":
        result["cluster_only"] = result.coil_short_ok & result.coil_with_ma60_ok
    else:
        result["cluster_only"] = (result.coil30_pct.le(rules.coil_pct)
                                  | result.coil60_pct.le(rules.coil_pct))
    return result


def slots(calendar: list[str]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(f"{d} {t}") for d in calendar for t in TIMES])


def rising_run(values: pd.Series) -> pd.Series:
    up = values.diff().gt(1e-10)
    return up.groupby((~up).cumsum()).cumsum().astype(int)


def features(raw: pd.DataFrame, calendar: list[str], rules: Rules) -> pd.DataFrame:
    """Missing slots remain NaN and invalidate rolling windows; never compress time.

    Price*dated factor is normalized to the first OBSERVED valid factor. This
    constant has no future dependency. Original OHLC and volume remain intact.
    """
    raw = raw.copy()
    raw["datetime"] = pd.to_datetime(raw.datetime)
    grid = slots(calendar)
    duplicates = set(raw.loc[raw.datetime.duplicated(keep=False), "datetime"])
    # Invalid future slots must not erase an earlier signal. Duplicates invalidate
    # their own bar; off-grid timestamps are not compressed into valid clock slots.
    raw = raw[raw.datetime.isin(grid)].drop_duplicates("datetime", keep="first")
    frame = raw.set_index("datetime").reindex(grid).rename_axis("datetime").reset_index()
    for col in ("open", "high", "low", "close", "vol", "adj_factor"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    valid = (frame[["open", "high", "low", "close", "vol", "adj_factor"]].notna().all(axis=1)
             & frame[["open", "high", "low", "close", "adj_factor"]].gt(0).all(axis=1)
             & frame.vol.ge(0) & frame.high.ge(frame[["open", "close", "low"]].max(axis=1))
             & frame.low.le(frame[["open", "close", "high"]].min(axis=1)))
    valid &= ~frame.datetime.isin(duplicates)
    frame["valid_bar"] = valid
    for c in ("open", "high", "low", "close", "vol", "adj_factor"):
        frame.loc[~valid, c] = np.nan
    factors = frame.adj_factor.dropna()
    base_factor = float(factors.iloc[0]) if len(factors) else 1.
    for c in ("open", "high", "low", "close"):
        frame[f"p_{c}"] = frame[c] * frame.adj_factor / base_factor
    close = frame.p_close
    for n in MA_WINDOWS:
        frame[f"ma{n}"] = close.rolling(n).mean()
        frame[f"rise{n}"] = rising_run(frame[f"ma{n}"])
    frame["body_pct"] = 100 * (close / frame.p_open - 1)
    span = frame.p_high - frame.p_low
    frame["body_fraction"] = (close - frame.p_open) / span.where(span.gt(0))
    frame["close_location"] = (close - frame.p_low) / span.where(span.gt(0))
    volume = frame.vol.to_numpy(float)
    ranks = np.full(len(frame), np.nan)
    w = rules.volume_week_bars
    if len(frame) > w:
        previous = np.lib.stride_tricks.sliding_window_view(volume, w)[:-1]
        current = volume[w:]
        ok = np.isfinite(previous).all(axis=1) & np.isfinite(current)
        ranks[w:] = np.where(ok, 1 + (previous >= current[:, None]).sum(axis=1), np.nan)
    frame["volume_rank_previous_week"] = ranks
    frame["volume_multiple8"] = frame.vol / frame.vol.shift().rolling(
        rules.restart_volume_baseline_bars).mean().replace(0, np.nan)
    # Same clock-time baseline is a diagnostic, not a new optimized condition.
    prior_slots = pd.concat([frame.vol.shift(16 * k) for k in range(1, 6)], axis=1)
    frame["volume_multiple_same_slot5"] = frame.vol / prior_slots.mean(axis=1).where(
        prior_slots.notna().all(axis=1)).replace(0, np.nan)
    frame["impulse"] = (frame.body_pct.ge(rules.impulse_body_pct)
                         & frame.body_fraction.ge(rules.impulse_body_fraction)
                         & frame.close_location.ge(rules.restart_close_location)
                         & frame.volume_rank_previous_week.le(rules.impulse_top_rank))
    frame["impulse_index"] = pd.Series(np.where(frame.impulse, np.arange(len(frame)), np.nan)).ffill()
    tol = 1 - rules.alignment_tolerance_pct / 100
    aligned = (frame.ma5.ge(frame.ma10 * tol) & frame.ma10.ge(frame.ma20 * tol)
               & frame.ma20.ge(frame.ma30 * tol) & frame.ma30.notna())
    frame["prior_alignment_share"] = aligned.shift().rolling(
        rules.alignment_history_bars).mean()
    for n in (120, 250):
        above = close.ge(frame[f"ma{n}"]) & frame[f"ma{n}"].notna()
        frame[f"above{n}_share"] = above.rolling(rules.price_history_bars).mean()
        frame[f"long{n}_ok"] = (frame[f"rise{n}"].ge(rules.long_rising_bars)
                                  & frame[f"above{n}_share"].ge(rules.price_above_share)
                                  & close.ge(frame[f"ma{n}"] *
                                             (1 - rules.long_ma_proximity_pct / 100)))
    frame["long_trend_ok"] = frame.long120_ok | frame.long250_ok
    proximity = (frame.rise250.ge(rules.long_rising_bars)
                 & (100 * (close / frame.ma250 - 1)).between(-rules.long_ma_proximity_pct, 0))
    frame["long_trend_ok"] |= proximity
    bullish60 = frame.ma60.ge(frame.ma120) & frame.ma120.notna()
    frame["fallback_trend"] = (frame.rise120.ge(rules.fallback_ma120_rising_bars)
                                 & frame.rise60.ge(rules.fallback_ma60_rising_bars)
                                 & bullish60.rolling(rules.fallback_ma60_rising_bars).min().eq(1))
    # Require a wholly observed recent window before examining its running peak.
    highs = frame.p_high.to_numpy(float)
    window = rules.peak_window_bars
    peaks = np.full(len(frame), np.nan)
    peak_indices = np.full(len(frame), np.nan)
    if len(frame) >= window:
        arr = np.lib.stride_tricks.sliding_window_view(highs, window)
        ok = np.isfinite(arr).all(axis=1)
        # Last occurrence: a flat repeated peak should not manufacture a long pullback.
        last_max = window - 1 - np.argmax(arr[:, ::-1], axis=1)
        peaks[window - 1:] = np.where(ok, arr.max(axis=1), np.nan)
        peak_indices[window - 1:] = np.where(ok, np.arange(len(arr)) + last_max, np.nan)
    frame["observed_peak"] = peaks
    frame["peak_index"] = peak_indices
    frame["pullback_bars"] = np.arange(len(frame)) - peak_indices
    frame["pullback_pct"] = 100 * (1 - close / frame.observed_peak)
    frame["recent_bear_body_pct"] = (-frame.body_pct).clip(lower=0).rolling(8).max()
    frame["slow_pullback"] = (frame.pullback_bars.ge(rules.pullback_min_bars)
                                & frame.pullback_pct.ge(rules.pullback_min_pct)
                                & (frame.pullback_pct / frame.pullback_bars.replace(0, np.nan))
                                  .le(rules.pullback_max_rate_pct)
                                & frame.recent_bear_body_pct.le(rules.pullback_recent_bear_body_pct))
    recent_impulse = (np.arange(len(frame)) - frame.impulse_index).between(1, rules.impulse_max_age)
    frame["origin"] = np.where(recent_impulse, "volume_impulse",
                               np.where(frame.fallback_trend, "persistent_ma", "none"))
    frame = coil_features(frame, rules)
    frame["coil_ready"] = (frame.cluster_only & frame.long_trend_ok & frame.slow_pullback
                             & frame.origin.ne("none")
                             & frame.prior_alignment_share.ge(rules.alignment_share))
    # Prior week, excludes the restart candle itself. High/low, not closing-price range.
    prior_high = frame.p_high.shift().rolling(rules.compression_bars).max()
    prior_low = frame.p_low.shift().rolling(rules.compression_bars).min()
    frame["pre_restart_range_pct"] = 100 * (prior_high / prior_low - 1)
    strict = pd.Series(True, index=frame.index)
    for a, b in zip((5, 10, 20, 30, 60), (10, 20, 30, 60, 120)):
        strict &= frame[f"ma{a}"].gt(frame[f"ma{b}"])
    for n in (5, 10, 20, 30, 60, 120):
        strict &= frame[f"rise{n}"].gt(0)
    frame["strict_restart_alignment"] = strict
    return frame


def find_signals(frame: pd.DataFrame, code: str, name: str, rules: Rules,
                 first: str, last: str) -> pd.DataFrame:
    """A coil at k can only trigger at k+1 or k+2, using the latest eligible coil.

    One signal/stock/session is a signal-count convention, not a position ledger.
    """
    restart = (frame.body_pct.ge(rules.restart_body_pct)
               & frame.body_fraction.ge(rules.restart_body_fraction)
               & frame.close_location.ge(rules.restart_close_location)
               & frame.volume_multiple8.ge(rules.restart_volume_multiple)
               & frame.pre_restart_range_pct.le(rules.compression_range_pct))
    if rules.strict_restart_alignment:
        restart &= frame.strict_restart_alignment
    restart = restart.to_numpy()
    ready = frame.coil_ready.to_numpy()
    rows = []
    emitted_days = set()
    for i in np.flatnonzero(restart):
        now = frame.iloc[i]
        day = now.datetime.strftime("%Y%m%d")
        if not first <= day <= last or (rules.deduplicate_stock_session and day in emitted_days):
            continue
        for lag in range(1, rules.restart_wait_bars + 1):
            k = i - lag
            if k < 0 or not ready[k] or not frame.valid_bar.iloc[k:i + 1].all():
                continue
            coil = frame.iloc[k]
            # Dual mode confirms two widths but keeps the short group's upper
            # edge as the existing restart-price trigger.
            branch = 30 if rules.coil_mode == "dual" or coil.coil30_pct <= rules.coil_pct else 60
            top = float(coil[f"coil{branch}_high"])
            if now.p_close <= top * (1 + rules.restart_above_coil_pct / 100):
                continue
            origin_idx = int(coil.impulse_index) if coil.origin == "volume_impulse" else None
            factor_scale = float(coil.p_close / coil.close)
            row = {"ts_code": code, "name": name, "signal_time": str(now.datetime),
                   "signal_close": float(now.close), "signal_factor": float(now.adj_factor),
                   "coil_time": str(coil.datetime),
                   "coil_branch": "dual" if rules.coil_mode == "dual" else f"MA{branch}",
                   "coil_spread_pct": float(coil[f"coil{branch}_pct"]),
                   "coil_short_pct": float(coil.coil30_pct),
                   "coil_with_ma60_pct": float(coil.coil_with_ma60_pct),
                   "coil_low_raw": float(coil[f"coil{branch}_low"] / factor_scale),
                   "coil_high_raw": top / factor_scale, "coil_to_restart_bars": lag,
                   "origin": coil.origin,
                   "impulse_time": str(frame.iloc[origin_idx].datetime) if origin_idx is not None else None,
                   "impulse_body_pct": float(frame.iloc[origin_idx].body_pct) if origin_idx is not None else None,
                   "impulse_volume_rank": float(frame.iloc[origin_idx].volume_rank_previous_week)
                                          if origin_idx is not None else None,
                   "ma120_rising_bars": int(coil.rise120), "ma250_rising_bars": int(coil.rise250),
                   "ma60_rising_bars": int(coil.rise60),
                   "prior_alignment_share": float(coil.prior_alignment_share),
                   "pullback_pct": float(coil.pullback_pct), "pullback_bars": int(coil.pullback_bars),
                   "restart_body_pct": float(now.body_pct),
                   "restart_volume_multiple8": float(now.volume_multiple8),
                   "restart_volume_multiple_same_slot5": float(now.volume_multiple_same_slot5),
                   "pre_restart_range_pct": float(now.pre_restart_range_pct),
                   "strict_restart_alignment": bool(now.strict_restart_alignment),
                   "rule_coil_pct": rules.coil_pct,
                   "rule_coil_with_ma60_pct": rules.coil_with_ma60_pct,
                   "rule_coil_mode": rules.coil_mode}
            rows.append(row)
            emitted_days.add(day)
            break
    return pd.DataFrame(rows)


def save_rules(path: Path, rules: Rules) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(rules), ensure_ascii=False, indent=2)
    if path.exists() and path.read_text() != payload:
        raise ValueError("Refusing to overwrite a frozen rule file")
    path.write_text(payload)
    return hashlib.sha256(payload.encode()).hexdigest()
