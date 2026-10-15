"""Evaluate an MA20 first-pullback cohesion breakout entry in the long-trend pool."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[5]
DEFAULT_DAILY = (
    ROOT
    / "outputs"
    / "ma60_ma120_top100_ppt"
    / "2026_through_20260930_qfq"
    / "daily_input_qfq.csv.gz"
)
DEFAULT_MA60 = (
    ROOT
    / "outputs"
    / "ma60_ma120_top100_themes"
    / "2026_through_20260930_qfq"
    / "ma60_top100_with_ties_named.csv"
)
DEFAULT_MA120 = (
    ROOT
    / "outputs"
    / "ma60_ma120_top100_themes"
    / "2026_through_20260930_qfq"
    / "ma120_top100_with_ties_named.csv"
)
DEFAULT_SEGMENTS = (
    ROOT
    / "outputs"
    / "ma_short_segments_within_long_episodes"
    / "2026_through_20260930_qfq"
    / "short_ma_segments_detail.csv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "outputs"
    / "ma20_first_pullback_cohesion_entry"
    / "2026_through_20260930_qfq"
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-file", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--ma60-file", type=Path, default=DEFAULT_MA60)
    parser.add_argument("--ma120-file", type=Path, default=DEFAULT_MA120)
    parser.add_argument("--ma20-segments-file", type=Path, default=DEFAULT_SEGMENTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cohesion-threshold-pct", type=float, default=3.0)
    parser.add_argument("--bullish-body-threshold-pct", type=float, default=3.0)
    parser.add_argument("--volume-ratio-threshold", type=float, default=1.5)
    parser.add_argument("--volume-lookback", type=int, default=20)
    parser.add_argument("--deduction-horizon", type=int, default=10)
    return parser.parse_args(argv)


def add_deduction_price_features(frame: pd.DataFrame, *, horizon: int = 10) -> pd.DataFrame:
    """Add future MA20/MA30 deduction paths that are fully known at each date."""
    if horizon <= 0 or horizon > 20:
        raise ValueError("horizon must be between 1 and 20")
    result = frame.copy()
    close = pd.to_numeric(result["close"], errors="coerce")
    for period in (20, 30):
        shifted_end = close.shift(period - horizon)
        result[f"ma{period}_deduction_start"] = close.shift(period - 1)
        result[f"ma{period}_deduction_end"] = shifted_end
        result[f"ma{period}_deduction_max"] = shifted_end.rolling(
            horizon, min_periods=horizon
        ).max()
        result[f"ma{period}_deduction_complete"] = shifted_end.rolling(
            horizon, min_periods=horizon
        ).count().eq(horizon)
        result[f"ma{period}_deduction_all_below_entry"] = (
            result[f"ma{period}_deduction_complete"]
            & result[f"ma{period}_deduction_max"].lt(close)
        )
        result[f"ma{period}_deduction_end_below_start"] = (
            result[f"ma{period}_deduction_complete"]
            & result[f"ma{period}_deduction_end"].lt(
                result[f"ma{period}_deduction_start"]
            )
        )
    result["both_deduction_all_below_entry"] = (
        result["ma20_deduction_all_below_entry"]
        & result["ma30_deduction_all_below_entry"]
    )
    result["both_deduction_declining"] = (
        result["ma20_deduction_end_below_start"]
        & result["ma30_deduction_end_below_start"]
    )
    return result


def add_entry_candidate_features(
    frame: pd.DataFrame,
    *,
    cohesion_threshold_pct: float,
    bullish_body_threshold_pct: float,
    volume_ratio_threshold: float,
    volume_lookback: int,
) -> pd.DataFrame:
    """Add previous-day three-MA cohesion and signal-day bar/volume features."""
    required = {"open", "close", "vol", "ma20", "ma30", "ma60"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing candidate columns: {sorted(missing)}")
    if volume_lookback <= 0:
        raise ValueError("volume_lookback must be positive")
    result = frame.copy()
    ma_values = result[["ma20", "ma30", "ma60"]].apply(
        pd.to_numeric, errors="coerce"
    )
    daily_spread = 100.0 * (
        ma_values.max(axis=1) - ma_values.min(axis=1)
    ) / ma_values.mean(axis=1)
    result["previous_ma_cohesion_spread_pct"] = daily_spread.shift(1)
    result["previous_day_ma_cohesive"] = result[
        "previous_ma_cohesion_spread_pct"
    ].le(cohesion_threshold_pct)
    open_price = pd.to_numeric(result["open"], errors="coerce")
    close = pd.to_numeric(result["close"], errors="coerce")
    volume = pd.to_numeric(result["vol"], errors="coerce")
    result["bullish_body_pct"] = 100.0 * (close / open_price - 1.0)
    result["prior_volume_mean"] = volume.shift(1).rolling(
        volume_lookback, min_periods=volume_lookback
    ).mean()
    result["volume_ratio_to_prior_mean"] = volume / result["prior_volume_mean"]
    result["large_volume_bullish_bar"] = (
        result["bullish_body_pct"].ge(bullish_body_threshold_pct)
        & close.gt(close.shift(1))
        & result["volume_ratio_to_prior_mean"].ge(volume_ratio_threshold)
    )
    result["cohesion_large_volume_bullish_bar"] = (
        result["previous_day_ma_cohesive"]
        & result["large_volume_bullish_bar"]
    )
    return result


def compute_rising_streak(series: pd.Series) -> pd.Series:
    """Return the strict consecutive-rise count ending at each observation."""
    values = pd.to_numeric(series, errors="coerce")
    rising = values.gt(values.shift(1)) & values.notna() & values.shift(1).notna()
    streaks: list[int] = []
    current = 0
    for is_rising in rising:
        current = current + 1 if bool(is_rising) else 0
        streaks.append(current)
    return pd.Series(streaks, index=series.index, dtype=int)


def compute_forward_outcomes(
    frame: pd.DataFrame,
    *,
    signal_position: int,
    horizons: Sequence[int],
) -> dict[str, Any]:
    """Compute executable next-open forward returns and high/low excursions."""
    result: dict[str, Any] = {
        "next_open_date": None,
        "next_open_price": float("nan"),
        "next_open_gap_pct": float("nan"),
    }
    if signal_position + 1 >= len(frame):
        for horizon in horizons:
            result[f"forward_{horizon}d_complete"] = False
            result[f"forward_{horizon}d_return_pct"] = float("nan")
            result[f"forward_{horizon}d_max_runup_pct"] = float("nan")
            result[f"forward_{horizon}d_max_drawdown_pct"] = float("nan")
        return result
    next_row = frame.iloc[signal_position + 1]
    entry_price = float(next_row["open"])
    result["next_open_date"] = str(next_row["trade_date"])
    result["next_open_price"] = entry_price
    signal_close = float(frame.iloc[signal_position]["close"])
    if signal_close > 0:
        result["next_open_gap_pct"] = 100.0 * (entry_price / signal_close - 1.0)
    for horizon in horizons:
        window = frame.iloc[signal_position + 1 : signal_position + 1 + horizon]
        complete = (
            len(window) == horizon
            and entry_price > 0
            and window[["close", "high", "low"]].notna().all().all()
        )
        result[f"forward_{horizon}d_complete"] = bool(complete)
        if not complete:
            result[f"forward_{horizon}d_return_pct"] = float("nan")
            result[f"forward_{horizon}d_max_runup_pct"] = float("nan")
            result[f"forward_{horizon}d_max_drawdown_pct"] = float("nan")
            continue
        result[f"forward_{horizon}d_return_pct"] = 100.0 * (
            float(window.iloc[-1]["close"]) / entry_price - 1.0
        )
        result[f"forward_{horizon}d_max_runup_pct"] = 100.0 * (
            float(window["high"].max()) / entry_price - 1.0
        )
        result[f"forward_{horizon}d_max_drawdown_pct"] = 100.0 * (
            float(window["low"].min()) / entry_price - 1.0
        )
    return result


def summarize_future_joint_ma_rise(
    frame: pd.DataFrame,
    *,
    signal_position: int,
    horizon: int,
) -> dict[str, Any]:
    """Measure whether MA20 and MA30 jointly re-enter sustained rises."""
    window = frame.iloc[signal_position + 1 : signal_position + 1 + horizon]
    complete = len(window) == horizon
    joint = (
        window["ma20_rising"].fillna(False).astype(bool)
        & window["ma30_rising"].fillna(False).astype(bool)
    )
    current = 0
    maximum = 0
    for value in joint:
        current = current + 1 if bool(value) else 0
        maximum = max(maximum, current)
    return {
        f"future_{horizon}d_complete": bool(complete),
        f"future_{horizon}d_joint_ma_rise_days": int(joint.sum()),
        f"future_{horizon}d_joint_ma_rise_max_run": int(maximum),
        f"future_{horizon}d_joint_ma_rise_run_ge_5": bool(
            complete and maximum >= 5
        ),
        f"future_{horizon}d_joint_ma_rise_all_days": bool(
            complete and bool(joint.all())
        ),
    }


def select_first_signal_after_segment(
    frame: pd.DataFrame,
    *,
    first_segment_end_date: object,
    anchor_end_date: object,
    signal_column: str,
) -> int | None:
    """Return the first qualifying positional index strictly after segment end."""
    if signal_column not in frame:
        raise ValueError(f"missing signal column: {signal_column}")
    dates = frame["trade_date"].astype(str).str.replace("-", "", regex=False)
    segment_end = str(first_segment_end_date).replace("-", "").split(".")[0]
    anchor_end = str(anchor_end_date).replace("-", "").split(".")[0]
    mask = (
        dates.gt(segment_end)
        & dates.le(anchor_end)
        & frame[signal_column].fillna(False).astype(bool)
    )
    positions = np.flatnonzero(mask.to_numpy())
    return int(positions[0]) if len(positions) else None


def select_tier_signal_positions(
    frame: pd.DataFrame,
    *,
    first_segment_end_date: object,
    anchor_end_date: object,
) -> dict[str, int | None]:
    """Select the first bar, then apply deduction filters to that same bar."""
    big_bar = select_first_signal_after_segment(
        frame,
        first_segment_end_date=first_segment_end_date,
        anchor_end_date=anchor_end_date,
        signal_column="large_volume_bullish_bar",
    )
    cohesion = select_first_signal_after_segment(
        frame,
        first_segment_end_date=first_segment_end_date,
        anchor_end_date=anchor_end_date,
        signal_column="cohesion_large_volume_bullish_bar",
    )
    main: int | None = None
    strict: int | None = None
    if cohesion is not None:
        row = frame.iloc[cohesion]
        if _as_bool(row["both_deduction_all_below_entry"]):
            main = cohesion
            if _as_bool(row["both_deduction_declining"]):
                strict = cohesion
    return {
        "big_bar": big_bar,
        "cohesion": cohesion,
        "main": main,
        "strict": strict,
    }


def rising_episode_status_at_position(
    rising: pd.Series,
    position: int,
) -> dict[str, Any]:
    """Describe the strict rising episode that contains a given position."""
    values = rising.fillna(False).astype(bool).to_numpy()
    if position < 0 or position >= len(values):
        raise IndexError(position)
    if not values[position]:
        return {
            "streak_at_position": 0,
            "observed_episode_total": 0,
            "remaining_observed_days": 0,
            "episode_right_censored": False,
        }
    start = position
    while start > 0 and values[start - 1]:
        start -= 1
    end = position
    while end + 1 < len(values) and values[end + 1]:
        end += 1
    return {
        "streak_at_position": int(position - start + 1),
        "observed_episode_total": int(end - start + 1),
        "remaining_observed_days": int(end - position),
        "episode_right_censored": bool(end == len(values) - 1),
    }


def deduction_price_features_at_position(
    frame: pd.DataFrame,
    *,
    signal_position: int,
    entry_price: float,
    horizon: int = 10,
) -> dict[str, Any]:
    """Compute the already-known deduction-price paths for future MA20/MA30."""
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
    paths: dict[int, list[float]] = {}
    for period in (20, 30):
        start = signal_position + 1 - period
        end = signal_position + horizon - period
        if start < 0 or end >= len(close):
            paths[period] = []
        else:
            paths[period] = close[start : end + 1].tolist()

    result: dict[str, Any] = {}
    for period in (20, 30):
        values = paths[period]
        complete = len(values) == horizon and np.isfinite(values).all()
        result[f"ma{period}_deduction_prices"] = values
        result[f"ma{period}_deduction_complete"] = bool(complete)
        result[f"ma{period}_deduction_all_below_entry"] = bool(
            complete and max(values) < entry_price
        )
        result[f"ma{period}_deduction_end_below_start"] = bool(
            complete and values[-1] < values[0]
        )
    result["both_deduction_all_below_entry"] = bool(
        result["ma20_deduction_all_below_entry"]
        and result["ma30_deduction_all_below_entry"]
    )
    result["both_deduction_declining"] = bool(
        result["ma20_deduction_end_below_start"]
        and result["ma30_deduction_end_below_start"]
    )
    return result


def _as_bool(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _compact_date(value: object) -> str:
    return str(value).strip().replace("-", "").split(".")[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def prepare_stock_features(
    daily: pd.DataFrame,
    *,
    cohesion_threshold_pct: float,
    bullish_body_threshold_pct: float,
    volume_ratio_threshold: float,
    volume_lookback: int,
    deduction_horizon: int,
) -> pd.DataFrame:
    """Compute all no-lookahead signal features for one stock."""
    frame = daily.copy()
    frame["trade_date"] = frame["trade_date"].map(_compact_date)
    frame = (
        frame.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )
    close = pd.to_numeric(frame["close"], errors="coerce")
    for period in (20, 30, 60, 120):
        frame[f"ma{period}"] = close.rolling(period, min_periods=period).mean()
        frame[f"ma{period}_rising"] = (
            frame[f"ma{period}"].gt(frame[f"ma{period}"].shift(1))
            & frame[f"ma{period}"].notna()
            & frame[f"ma{period}"].shift(1).notna()
        )
        frame[f"ma{period}_rising_streak"] = compute_rising_streak(
            frame[f"ma{period}"]
        )
    frame = add_entry_candidate_features(
        frame,
        cohesion_threshold_pct=cohesion_threshold_pct,
        bullish_body_threshold_pct=bullish_body_threshold_pct,
        volume_ratio_threshold=volume_ratio_threshold,
        volume_lookback=volume_lookback,
    )
    frame = add_deduction_price_features(frame, horizon=deduction_horizon)
    frame["big_bar_entry_signal"] = frame["large_volume_bullish_bar"]
    frame["cohesion_entry_signal"] = frame[
        "cohesion_large_volume_bullish_bar"
    ]
    frame["main_entry_signal"] = (
        frame["cohesion_large_volume_bullish_bar"]
        & frame["both_deduction_all_below_entry"]
    )
    frame["strict_entry_signal"] = (
        frame["main_entry_signal"] & frame["both_deduction_declining"]
    )
    return frame


def build_entry_record(
    frame: pd.DataFrame,
    *,
    signal_position: int,
    tier: str,
    anchor: pd.Series,
    first_segment: pd.Series,
    deduction_horizon: int,
    forward_horizons: Sequence[int] = (5, 10, 20, 40, 60),
) -> dict[str, Any]:
    """Materialize one signal event with observable and hindsight diagnostics."""
    row = frame.iloc[signal_position]
    date_to_position = {
        str(date): int(position)
        for position, date in enumerate(frame["trade_date"].astype(str))
    }
    anchor_start = _compact_date(anchor["start_date"])
    anchor_end = _compact_date(anchor["end_date"])
    first_end = _compact_date(first_segment["episode_end_date"])
    if anchor_start not in date_to_position or first_end not in date_to_position:
        raise ValueError(f"missing anchor/segment dates for {anchor['ts_code']}")
    anchor_start_position = date_to_position[anchor_start]
    first_end_position = date_to_position[first_end]
    record: dict[str, Any] = {
        "ts_code": str(anchor["ts_code"]),
        "stock_name": str(anchor["stock_name"]),
        "primary_theme": str(anchor.get("primary_theme", "")),
        "broad_category": str(anchor.get("broad_category", "")),
        "anchor_period": int(anchor["anchor_period"]),
        "anchor_rank": int(anchor["rank"]),
        "anchor_start_date": anchor_start,
        "anchor_end_date": anchor_end,
        "anchor_length": int(anchor["length"]),
        "anchor_right_censored": _as_bool(anchor["right_censored"]),
        "tier": tier,
        "first_ma20_episode_start_date": _compact_date(
            first_segment["episode_start_date"]
        ),
        "first_ma20_episode_end_date": first_end,
        "first_ma20_episode_length": int(first_segment["episode_full_length"]),
        "first_ma20_episode_started_before_anchor": _as_bool(
            first_segment["started_before_anchor"]
        ),
        "signal_date": str(row["trade_date"]),
        "signal_position": int(signal_position),
        "signal_close": float(row["close"]),
        "search_wait_trading_days": int(signal_position - first_end_position),
        "anchor_observed_days_at_signal": int(
            signal_position - anchor_start_position + 1
        ),
        "previous_ma_cohesion_spread_pct": float(
            row["previous_ma_cohesion_spread_pct"]
        ),
        "bullish_body_pct": float(row["bullish_body_pct"]),
        "volume_ratio_to_prior_mean": float(row["volume_ratio_to_prior_mean"]),
        "ma20": float(row["ma20"]),
        "ma30": float(row["ma30"]),
        "ma60": float(row["ma60"]),
        "ma120": float(row["ma120"]) if pd.notna(row["ma120"]) else float("nan"),
        "ma20_rising_streak_at_signal": int(row["ma20_rising_streak"]),
        "ma30_rising_streak_at_signal": int(row["ma30_rising_streak"]),
        "deduction_horizon": int(deduction_horizon),
        "ma20_deduction_max": float(row["ma20_deduction_max"]),
        "ma30_deduction_max": float(row["ma30_deduction_max"]),
        "ma20_deduction_all_below_entry": _as_bool(
            row["ma20_deduction_all_below_entry"]
        ),
        "ma30_deduction_all_below_entry": _as_bool(
            row["ma30_deduction_all_below_entry"]
        ),
        "both_deduction_all_below_entry": _as_bool(
            row["both_deduction_all_below_entry"]
        ),
        "both_deduction_declining": _as_bool(row["both_deduction_declining"]),
    }
    deduction = deduction_price_features_at_position(
        frame,
        signal_position=signal_position,
        entry_price=float(row["close"]),
        horizon=deduction_horizon,
    )
    for period in (20, 30):
        record[f"ma{period}_deduction_prices"] = "|".join(
            f"{value:.6f}" for value in deduction[f"ma{period}_deduction_prices"]
        )
    for period in (60, 120):
        status = rising_episode_status_at_position(
            frame[f"ma{period}_rising"], signal_position
        )
        for key, value in status.items():
            record[f"ma{period}_{key}"] = value
    record.update(
        compute_forward_outcomes(
            frame,
            signal_position=signal_position,
            horizons=forward_horizons,
        )
    )
    record.update(
        summarize_future_joint_ma_rise(
            frame,
            signal_position=signal_position,
            horizon=deduction_horizon,
        )
    )
    return record


def summarize_performance(
    events: pd.DataFrame,
    *,
    horizons: Sequence[int] = (5, 10, 20, 40, 60),
) -> pd.DataFrame:
    """Aggregate next-open performance by scope and signal tier."""
    rows: list[dict[str, Any]] = []
    if events.empty:
        return pd.DataFrame(rows)
    for (scope, tier), group in events.groupby(["scope", "tier"], sort=True):
        for horizon in horizons:
            complete = group.loc[
                group[f"forward_{horizon}d_complete"].map(_as_bool)
            ].copy()
            returns = pd.to_numeric(
                complete[f"forward_{horizon}d_return_pct"], errors="coerce"
            ).dropna()
            runup = pd.to_numeric(
                complete[f"forward_{horizon}d_max_runup_pct"], errors="coerce"
            ).dropna()
            drawdown = pd.to_numeric(
                complete[f"forward_{horizon}d_max_drawdown_pct"], errors="coerce"
            ).dropna()
            rows.append(
                {
                    "scope": scope,
                    "tier": tier,
                    "horizon_days": int(horizon),
                    "signal_count": int(len(group)),
                    "comparable_count": int(len(returns)),
                    "return_mean_pct": float(returns.mean()) if len(returns) else float("nan"),
                    "return_median_pct": float(returns.median()) if len(returns) else float("nan"),
                    "return_p25_pct": float(returns.quantile(0.25)) if len(returns) else float("nan"),
                    "return_p75_pct": float(returns.quantile(0.75)) if len(returns) else float("nan"),
                    "positive_return_count": int(returns.gt(0).sum()),
                    "positive_return_pct": 100.0 * float(returns.gt(0).mean()) if len(returns) else float("nan"),
                    "return_ge_10pct_count": int(returns.ge(10).sum()),
                    "return_ge_10pct_pct": 100.0 * float(returns.ge(10).mean()) if len(returns) else float("nan"),
                    "max_runup_median_pct": float(runup.median()) if len(runup) else float("nan"),
                    "max_drawdown_median_pct": float(drawdown.median()) if len(drawdown) else float("nan"),
                }
            )
    return pd.DataFrame(rows)


def summarize_mechanism(events: pd.DataFrame, *, horizon: int = 10) -> pd.DataFrame:
    """Summarize deduction support and subsequent joint MA20/MA30 rises."""
    rows: list[dict[str, Any]] = []
    if events.empty:
        return pd.DataFrame(rows)
    for (scope, tier), group in events.groupby(["scope", "tier"], sort=True):
        complete = group.loc[group[f"future_{horizon}d_complete"].map(_as_bool)]
        run5 = complete[f"future_{horizon}d_joint_ma_rise_run_ge_5"].map(_as_bool)
        all_days = complete[f"future_{horizon}d_joint_ma_rise_all_days"].map(_as_bool)
        max_run = pd.to_numeric(
            complete[f"future_{horizon}d_joint_ma_rise_max_run"], errors="coerce"
        ).dropna()
        rows.append(
            {
                "scope": scope,
                "tier": tier,
                "signal_count": int(len(group)),
                "future_window_comparable_count": int(len(complete)),
                "deduction_below_entry_count": int(
                    group["both_deduction_all_below_entry"].map(_as_bool).sum()
                ),
                "deduction_below_entry_pct": 100.0 * float(
                    group["both_deduction_all_below_entry"].map(_as_bool).mean()
                ),
                "deduction_declining_count": int(
                    group["both_deduction_declining"].map(_as_bool).sum()
                ),
                "deduction_declining_pct": 100.0 * float(
                    group["both_deduction_declining"].map(_as_bool).mean()
                ),
                "joint_ma_rise_run_ge_5_count": int(run5.sum()),
                "joint_ma_rise_run_ge_5_pct": 100.0 * float(run5.mean()) if len(run5) else float("nan"),
                "joint_ma_rise_all_days_count": int(all_days.sum()),
                "joint_ma_rise_all_days_pct": 100.0 * float(all_days.mean()) if len(all_days) else float("nan"),
                "joint_ma_rise_max_run_median": float(max_run.median()) if len(max_run) else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def summarize_streaks(events: pd.DataFrame) -> pd.DataFrame:
    """Report observable and eventual MA60/MA120 streak lengths at each entry."""
    rows: list[dict[str, Any]] = []
    if events.empty:
        return pd.DataFrame(rows)
    for (scope, tier), group in events.groupby(["scope", "tier"], sort=True):
        for period in (60, 120):
            streak = pd.to_numeric(
                group[f"ma{period}_streak_at_position"], errors="coerce"
            ).dropna()
            rising = streak.gt(0)
            positive = streak.loc[rising]
            total = pd.to_numeric(
                group.loc[rising.index[rising], f"ma{period}_observed_episode_total"],
                errors="coerce",
            ).dropna()
            remaining = pd.to_numeric(
                group.loc[rising.index[rising], f"ma{period}_remaining_observed_days"],
                errors="coerce",
            ).dropna()
            rows.append(
                {
                    "scope": scope,
                    "tier": tier,
                    "ma_period": int(period),
                    "signal_count": int(len(group)),
                    "currently_rising_count": int(rising.sum()),
                    "currently_rising_pct": 100.0 * float(rising.mean()) if len(rising) else float("nan"),
                    "streak_at_entry_median": float(positive.median()) if len(positive) else float("nan"),
                    "streak_at_entry_p25": float(positive.quantile(0.25)) if len(positive) else float("nan"),
                    "streak_at_entry_p75": float(positive.quantile(0.75)) if len(positive) else float("nan"),
                    "observed_episode_total_median": float(total.median()) if len(total) else float("nan"),
                    "remaining_observed_days_median": float(remaining.median()) if len(remaining) else float("nan"),
                    "episode_right_censored_count": int(
                        group.loc[rising.index[rising], f"ma{period}_episode_right_censored"].map(_as_bool).sum()
                    ) if int(rising.sum()) else 0,
                }
            )
    return pd.DataFrame(rows)


def build_unique_stock_tier_events(events: pd.DataFrame) -> pd.DataFrame:
    """Select each stock's first observable signal before applying filters."""
    selected: list[pd.DataFrame] = []
    unique_cohesion = pd.DataFrame()
    for tier in ("big_bar", "cohesion"):
        tier_events = events.loc[events["tier"].eq(tier)].copy()
        if tier_events.empty:
            continue
        tier_events = (
            tier_events.sort_values(
                ["signal_date", "anchor_period"], kind="mergesort"
            )
            .drop_duplicates("ts_code", keep="first")
            .copy()
        )
        selected.append(tier_events)
        if tier == "cohesion":
            unique_cohesion = tier_events
    if not unique_cohesion.empty:
        main = unique_cohesion.loc[
            unique_cohesion["both_deduction_all_below_entry"].map(_as_bool)
        ].copy()
        main["tier"] = "main"
        selected.append(main)
        strict = main.loc[main["both_deduction_declining"].map(_as_bool)].copy()
        strict["tier"] = "strict"
        selected.append(strict)
    if not selected:
        return events.iloc[0:0].copy()
    result = pd.concat(selected, ignore_index=True)
    result["scope"] = "UNION_UNIQUE"
    return result.sort_values(["tier", "signal_date", "ts_code"], kind="mergesort").reset_index(drop=True)


def build_coverage_summary(
    contexts: pd.DataFrame,
    events: pd.DataFrame,
    *,
    tiers: Sequence[str],
) -> pd.DataFrame:
    """Summarize signal coverage using searchable anchors as the denominator."""
    rows: list[dict[str, Any]] = []
    for period, period_contexts in contexts.groupby("anchor_period", sort=True):
        eligible = period_contexts.loc[period_contexts["search_eligible"].map(_as_bool)]
        period_events = events.loc[events["anchor_period"].eq(period)]
        for tier in tiers:
            count = int(period_events["tier"].eq(tier).sum())
            denominator = int(len(eligible))
            rows.append(
                {
                    "scope": f"MA{int(period)}",
                    "anchor_period": int(period),
                    "tier": tier,
                    "eligible_count": denominator,
                    "signal_count": count,
                    "coverage_pct": 100.0 * count / denominator if denominator else float("nan"),
                }
            )
    return pd.DataFrame(rows)


def _paired_tier_summary(events: pd.DataFrame) -> pd.DataFrame:
    keys = ["ts_code", "anchor_period", "anchor_start_date"]
    rows: list[dict[str, Any]] = []
    main_events = events.loc[events["tier"].eq("main")]
    for comparator in ("big_bar", "cohesion"):
        other = events.loc[events["tier"].eq(comparator)]
        paired = main_events.merge(
            other,
            on=keys,
            suffixes=("_main", "_other"),
            validate="one_to_one",
        )
        for period in (60, 120):
            period_pairs = paired.loc[paired["anchor_period"].eq(period)]
            for horizon in (10, 20, 40, 60):
                main_return = pd.to_numeric(
                    period_pairs[f"forward_{horizon}d_return_pct_main"],
                    errors="coerce",
                )
                other_return = pd.to_numeric(
                    period_pairs[f"forward_{horizon}d_return_pct_other"],
                    errors="coerce",
                )
                valid = main_return.notna() & other_return.notna()
                difference = main_return.loc[valid] - other_return.loc[valid]
                rows.append(
                    {
                        "anchor_period": period,
                        "comparator_tier": comparator,
                        "horizon_days": horizon,
                        "paired_count": int(valid.sum()),
                        "main_better_count": int(difference.gt(0).sum()),
                        "main_worse_count": int(difference.lt(0).sum()),
                        "main_equal_count": int(difference.eq(0).sum()),
                        "return_difference_mean_pct": float(difference.mean()) if len(difference) else float("nan"),
                        "return_difference_median_pct": float(difference.median()) if len(difference) else float("nan"),
                    }
                )
    return pd.DataFrame(rows)


def _write_report(
    *,
    coverage: pd.DataFrame,
    performance: pd.DataFrame,
    mechanism: pd.DataFrame,
    streaks: pd.DataFrame,
    paired: pd.DataFrame,
    sensitivity: pd.DataFrame,
    deduction_split_performance: pd.DataFrame,
    deduction_split_mechanism: pd.DataFrame,
    output: Path,
) -> None:
    def pick(frame: pd.DataFrame, **filters: Any) -> pd.Series:
        mask = pd.Series(True, index=frame.index)
        for key, value in filters.items():
            mask &= frame[key].eq(value)
        selected = frame.loc[mask]
        if len(selected) != 1:
            raise ValueError(f"expected one report row for {filters}, got {len(selected)}")
        return selected.iloc[0]

    tier_names = {
        "big_bar": "放量大阳线",
        "cohesion": "+粘合",
        "main": "+抵扣价（主）",
        "strict": "+抵扣价下降",
    }
    union_pass_mechanism = pick(
        deduction_split_mechanism,
        scope="UNION_UNIQUE",
        tier="deduction_pass",
    )
    union_fail_mechanism = pick(
        deduction_split_mechanism,
        scope="UNION_UNIQUE",
        tier="deduction_fail",
    )
    union_pass_20 = pick(
        deduction_split_performance,
        scope="UNION_UNIQUE",
        tier="deduction_pass",
        horizon_days=20,
    )
    union_fail_20 = pick(
        deduction_split_performance,
        scope="UNION_UNIQUE",
        tier="deduction_fail",
        horizon_days=20,
    )
    union_pass_60 = pick(
        deduction_split_performance,
        scope="UNION_UNIQUE",
        tier="deduction_pass",
        horizon_days=60,
    )
    union_fail_60 = pick(
        deduction_split_performance,
        scope="UNION_UNIQUE",
        tier="deduction_fail",
        horizon_days=60,
    )
    union_cohesion_20 = pick(
        performance,
        scope="UNION_UNIQUE",
        tier="cohesion",
        horizon_days=20,
    )
    union_main_20 = pick(
        performance,
        scope="UNION_UNIQUE",
        tier="main",
        horizon_days=20,
    )
    union_cohesion_60 = pick(
        performance,
        scope="UNION_UNIQUE",
        tier="cohesion",
        horizon_days=60,
    )
    union_main_60 = pick(
        performance,
        scope="UNION_UNIQUE",
        tier="main",
        horizon_days=60,
    )
    lines = [
        "# 第一段MA20结束后的均线粘合放量大阳线买点研究",
        "",
        "## 结论",
        "",
        "- **第一根‘粘合＋放量大阳线’值得继续作为候选买点验证，但当前结果不足以直接上线交易。** 这是事后长趋势强股条件样本，收益存在未来选股偏差。",
        f"- 抵扣价条件确实强烈提高短均线重新连升概率：唯一股票口径未来10日MA20/MA30同步连升≥5日由未通过组的{union_fail_mechanism['joint_ma_rise_run_ge_5_pct']:.1f}%升至通过组的{union_pass_mechanism['joint_ma_rise_run_ge_5_pct']:.1f}%，连升满10日由{union_fail_mechanism['joint_ma_rise_all_days_pct']:.1f}%升至{union_pass_mechanism['joint_ma_rise_all_days_pct']:.1f}%。",
        f"- **但它没有带来收益增强，反而筛掉了后续收益更高的一组。** 通过/未通过组20日收益中位数为{union_pass_20['return_median_pct']:.2f}%/{union_fail_20['return_median_pct']:.2f}%，60日为{union_pass_60['return_median_pct']:.2f}%/{union_fail_60['return_median_pct']:.2f}%。",
        f"- 与不加抵扣价硬过滤的首根粘合大阳线相比，过滤后唯一股票20日收益中位数由{union_cohesion_20['return_median_pct']:.2f}%降至{union_main_20['return_median_pct']:.2f}%，60日由{union_cohesion_60['return_median_pct']:.2f}%降至{union_main_60['return_median_pct']:.2f}%。因此抵扣价适合做‘MA20/MA30延续概率标签’，**不适合在当前证据下做硬性买入门槛**。",
        "",
        "样本为截至2026-09-30的前复权MA60/MA120长趋势榜。长均线锚点使用完整已观察区间，带入锚点可包含2025年。",
        "",
        "## 主口径",
        "",
        "- 第一段：最早与选中MA60/MA120锚点相交的完整全局MA20连续上升episode；从其真实结束日后的下一交易日开始搜索。",
        "- 粘合：信号日前一交易日 `max(MA20,MA30,MA60)-min(...)` 除以三者均值不超过3%。",
        "- 放量大阳线：实体涨幅至少3%，收盘高于前收盘，成交量至少为此前20日均量的1.5倍。",
        "- 抵扣价：信号日即可知道未来10日MA20抵扣价为信号日前19日至10日收盘价，MA30为前29日至20日；主条件要求二者全部低于信号收盘价。",
        "- 执行：完整日线信号只能收盘后确认，因此收益从下一交易日开盘价买入计算。",
        "- 主买点：先固定第一段MA20结束后、前一日三线粘合处出现的第一根放量大阳线；仅当这一根K线的MA20和MA30抵扣价条件同时通过时入场，不允许跳过它再等后续K线。",
        "",
        "## 信号覆盖率",
        "",
        "| 锚定组 | 可搜索锚点 | 大阳线 | +粘合 | +抵扣价（主买点） | +抵扣价下降（严格） |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for period in (60, 120):
        group = coverage.loc[coverage["scope"].eq(f"MA{period}")].set_index("tier")
        eligible_count = int(group.iloc[0]["eligible_count"])
        lines.append(
            f"| MA{period} | {eligible_count} | {int(group.loc['big_bar','signal_count'])}（{group.loc['big_bar','coverage_pct']:.1f}%） | "
            f"{int(group.loc['cohesion','signal_count'])}（{group.loc['cohesion','coverage_pct']:.1f}%） | "
            f"{int(group.loc['main','signal_count'])}（{group.loc['main','coverage_pct']:.1f}%） | "
            f"{int(group.loc['strict','signal_count'])}（{group.loc['strict','coverage_pct']:.1f}%） |"
        )
    lines.extend(
        [
            "",
            "## 后续MA20/MA30是否重新连续上升",
            "",
            "| 锚定组 | 层级 | 信号数 | 10日可比 | 未来10日两线同步连升≥5日 | 两线连升满10日 | 同步最长段中位数 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for scope in ("MA60", "MA120", "UNION_UNIQUE"):
        for tier in ("big_bar", "cohesion", "main", "strict"):
            row = pick(mechanism, scope=scope, tier=tier)
            lines.append(
                f"| {scope} | {tier_names[tier]} | {int(row['signal_count'])} | {int(row['future_window_comparable_count'])} | "
                f"{row['joint_ma_rise_run_ge_5_pct']:.1f}% | {row['joint_ma_rise_all_days_pct']:.1f}% | {row['joint_ma_rise_max_run_median']:.1f}日 |"
            )
    lines.extend(
        [
            "",
            "## 同一首根粘合大阳线：抵扣价通过与未通过",
            "",
            "这一比较固定同一套‘第一根’定义，不允许未通过后再等待下一根，因此最直接检验抵扣价过滤的增量信息。",
            "",
            "| 范围 | 抵扣价 | 信号数 | 两线连升≥5日 | 两线连升满10日 | 20日收益中位数 | 60日收益中位数 |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for scope in ("MA60", "MA120", "UNION_UNIQUE"):
        for split_tier, split_name in (
            ("deduction_pass", "通过"),
            ("deduction_fail", "未通过"),
        ):
            mechanism_row = pick(
                deduction_split_mechanism,
                scope=scope,
                tier=split_tier,
            )
            return_20 = pick(
                deduction_split_performance,
                scope=scope,
                tier=split_tier,
                horizon_days=20,
            )
            return_60 = pick(
                deduction_split_performance,
                scope=scope,
                tier=split_tier,
                horizon_days=60,
            )
            lines.append(
                f"| {scope} | {split_name} | {int(mechanism_row['signal_count'])} | "
                f"{mechanism_row['joint_ma_rise_run_ge_5_pct']:.1f}% | {mechanism_row['joint_ma_rise_all_days_pct']:.1f}% | "
                f"{return_20['return_median_pct']:.2f}% | {return_60['return_median_pct']:.2f}% |"
            )
    lines.extend(
        [
            "",
            "## 下一开盘价入场后的表现",
            "",
            "| 锚定组 | 层级 | 周期 | 可比数 | 收益中位数 | 正收益率 | ≥10%收益率 | 最大上行中位数 | 最大回撤中位数 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for scope in ("MA60", "MA120", "UNION_UNIQUE"):
        for tier in ("big_bar", "cohesion", "main"):
            for horizon in (10, 20, 40, 60):
                row = pick(performance, scope=scope, tier=tier, horizon_days=horizon)
                lines.append(
                    f"| {scope} | {tier_names[tier]} | {horizon}日 | {int(row['comparable_count'])} | "
                    f"{row['return_median_pct']:.2f}% | {row['positive_return_pct']:.1f}% | {row['return_ge_10pct_pct']:.1f}% | "
                    f"{row['max_runup_median_pct']:.2f}% | {row['max_drawdown_median_pct']:.2f}% |"
                )
    lines.extend(
        [
            "",
            "## 主买点当天MA60/MA120连续上升天数",
            "",
            "`streak_at_entry`是当时可观察到的已连续上升天数；`observed_episode_total`包含后来走出的天数，属于事后描述，右截断时只是下界。",
            "",
            "| 锚定组 | 均线 | 主信号数 | 当天正在上升 | 已连续天数中位数（IQR） | 已观察整段中位数 | 入场后剩余中位数 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for scope in ("MA60", "MA120", "UNION_UNIQUE"):
        for ma_period in (60, 120):
            row = pick(streaks, scope=scope, tier="main", ma_period=ma_period)
            lines.append(
                f"| {scope} | MA{ma_period} | {int(row['signal_count'])} | {int(row['currently_rising_count'])}（{row['currently_rising_pct']:.1f}%） | "
                f"{row['streak_at_entry_median']:.1f}日（{row['streak_at_entry_p25']:.1f}–{row['streak_at_entry_p75']:.1f}） | "
                f"{row['observed_episode_total_median']:.1f}日 | {row['remaining_observed_days_median']:.1f}日 |"
            )
    lines.extend(
        [
            "",
            "## 同锚点与宽松大阳线时点比较",
            "",
            "放量大阳线-only信号可能早于粘合信号；以下在同一锚点有两种信号时比较各自后续收益。主买点与其通过过滤的粘合信号是同一日期，因此不重复列示；这些时序比较仍不是随机对照。",
            "",
            "| 锚定组 | 对照 | 周期 | 配对数 | 主买点更好/更差 | 收益差中位数 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for period in (60, 120):
        for comparator in ("big_bar",):
            for horizon in (20, 40, 60):
                row = pick(
                    paired,
                    anchor_period=period,
                    comparator_tier=comparator,
                    horizon_days=horizon,
                )
                lines.append(
                    f"| MA{period} | {tier_names[comparator]} | {horizon}日 | {int(row['paired_count'])} | "
                    f"{int(row['main_better_count'])}/{int(row['main_worse_count'])} | {row['return_difference_median_pct']:.2f}个百分点 |"
                )
    main_sensitivity = sensitivity.loc[
        sensitivity["cohesion_threshold_pct"].eq(3.0)
        & sensitivity["bullish_body_threshold_pct"].eq(3.0)
        & sensitivity["volume_ratio_threshold"].eq(1.5)
    ]
    sensitivity_60 = sensitivity.loc[sensitivity["anchor_period"].eq(60)]
    sensitivity_120 = sensitivity.loc[sensitivity["anchor_period"].eq(120)]
    lines.extend(
        [
            "",
            "## 阈值敏感性与解释边界",
            "",
            f"完整敏感性表覆盖粘合2%/3%/4%/5%、阳线实体2%/3%/4%、放量1.2/1.5/2.0倍，共{len(sensitivity)}个‘参数×锚定组’组合。主参数共{int(main_sensitivity['signal_count'].sum())}个锚定事件。",
            f"36组阈值下，未来10日两线同步连升≥5日比例始终较高：MA60为{sensitivity_60['joint_ma_rise_run_ge_5_pct'].min():.1f}%–{sensitivity_60['joint_ma_rise_run_ge_5_pct'].max():.1f}%，MA120为{sensitivity_120['joint_ma_rise_run_ge_5_pct'].min():.1f}%–{sensitivity_120['joint_ma_rise_run_ge_5_pct'].max():.1f}%；但这只说明均线机制稳定，不等于收益过滤有效。",
            "",
            "**最关键限制：这178只股票是按后来形成的超长MA60/MA120上升段筛选出来的，存在明显未来选择与幸存者偏差。** 本研究只能回答‘在已经知道是长趋势强股的条件样本中，这个形态通常出现在什么位置、后续怎样’，不能证明它可直接用于全市场实时选股。",
            "",
            "若要决定是否正式纳入交易系统，下一步必须在未使用未来长趋势排名的全市场逐日股票池上做walk-forward回测，并计入次日开盘滑点、涨跌停不可成交和交易成本。",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_anchor_contexts(args: argparse.Namespace) -> pd.DataFrame:
    anchors: list[pd.DataFrame] = []
    for period, path in ((60, args.ma60_file), (120, args.ma120_file)):
        current = pd.read_csv(path, dtype={"ts_code": str})
        current["anchor_period"] = period
        current["start_date"] = current["start_date"].map(_compact_date)
        current["end_date"] = current["end_date"].map(_compact_date)
        anchors.append(current)
    anchor_frame = pd.concat(anchors, ignore_index=True)

    segments = pd.read_csv(args.ma20_segments_file, dtype={"ts_code": str})
    segments = segments.loc[
        segments["short_period"].eq(20) & segments["segment_index"].eq(1)
    ].copy()
    for column in (
        "anchor_start_date",
        "anchor_end_date",
        "episode_start_date",
        "episode_end_date",
        "clipped_start_date",
        "clipped_end_date",
    ):
        segments[column] = segments[column].map(_compact_date)
    segment_columns = [
        "ts_code",
        "anchor_period",
        "anchor_start_date",
        "episode_id",
        "episode_start_date",
        "episode_end_date",
        "episode_full_length",
        "episode_left_censored",
        "episode_right_censored",
        "started_before_anchor",
        "ended_after_anchor",
    ]
    contexts = anchor_frame.merge(
        segments[segment_columns],
        left_on=["ts_code", "anchor_period", "start_date"],
        right_on=["ts_code", "anchor_period", "anchor_start_date"],
        how="left",
        validate="one_to_one",
    )
    if contexts["episode_end_date"].isna().any():
        missing = contexts.loc[
            contexts["episode_end_date"].isna(),
            ["ts_code", "anchor_period", "start_date"],
        ]
        raise ValueError(f"missing first MA20 segment for anchors: {missing.to_dict('records')}")
    contexts["left_censored"] = contexts["left_censored"].map(_as_bool)
    contexts["search_eligible"] = (
        ~contexts["left_censored"]
        & contexts["episode_end_date"].lt(contexts["end_date"])
    )
    return contexts


def _run_sensitivity(
    *,
    contexts: pd.DataFrame,
    features_by_code: dict[str, pd.DataFrame],
    cohesion_thresholds: Sequence[float],
    body_thresholds: Sequence[float],
    volume_thresholds: Sequence[float],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    eligible = contexts.loc[contexts["search_eligible"]]
    for cohesion_threshold in cohesion_thresholds:
        for body_threshold in body_thresholds:
            for volume_threshold in volume_thresholds:
                signals: list[dict[str, Any]] = []
                for context in eligible.to_dict("records"):
                    frame = features_by_code[str(context["ts_code"])]
                    dates = frame["trade_date"].map(_compact_date)
                    condition = (
                        pd.to_numeric(frame["previous_ma_cohesion_spread_pct"], errors="coerce").le(cohesion_threshold)
                        & pd.to_numeric(frame["bullish_body_pct"], errors="coerce").ge(body_threshold)
                        & frame["close"].gt(frame["close"].shift(1))
                        & pd.to_numeric(frame["volume_ratio_to_prior_mean"], errors="coerce").ge(volume_threshold)
                    )
                    in_window = dates.gt(_compact_date(context["episode_end_date"])) & dates.le(
                        _compact_date(context["end_date"])
                    )
                    positions = np.flatnonzero((condition & in_window).to_numpy())
                    if not len(positions):
                        continue
                    position = int(positions[0])
                    if not _as_bool(frame.iloc[position]["both_deduction_all_below_entry"]):
                        continue
                    outcome = compute_forward_outcomes(
                        frame,
                        signal_position=position,
                        horizons=(10, 20, 40, 60),
                    )
                    mechanism = summarize_future_joint_ma_rise(
                        frame,
                        signal_position=position,
                        horizon=10,
                    )
                    signals.append(
                        {
                            "anchor_period": int(context["anchor_period"]),
                            "ts_code": str(context["ts_code"]),
                            **outcome,
                            **mechanism,
                        }
                    )
                signal_frame = pd.DataFrame(signals)
                for period in (60, 120):
                    period_signals = (
                        signal_frame.loc[signal_frame["anchor_period"].eq(period)]
                        if len(signal_frame)
                        else pd.DataFrame()
                    )
                    denominator = int(eligible["anchor_period"].eq(period).sum())
                    row: dict[str, Any] = {
                        "anchor_period": period,
                        "cohesion_threshold_pct": float(cohesion_threshold),
                        "bullish_body_threshold_pct": float(body_threshold),
                        "volume_ratio_threshold": float(volume_threshold),
                        "eligible_count": denominator,
                        "signal_count": int(len(period_signals)),
                        "coverage_pct": 100.0 * len(period_signals) / denominator if denominator else float("nan"),
                    }
                    for horizon in (10, 20, 40, 60):
                        values = (
                            pd.to_numeric(
                                period_signals.get(f"forward_{horizon}d_return_pct", pd.Series(dtype=float)),
                                errors="coerce",
                            ).dropna()
                            if len(period_signals)
                            else pd.Series(dtype=float)
                        )
                        drawdowns = (
                            pd.to_numeric(
                                period_signals.get(f"forward_{horizon}d_max_drawdown_pct", pd.Series(dtype=float)),
                                errors="coerce",
                            ).dropna()
                            if len(period_signals)
                            else pd.Series(dtype=float)
                        )
                        row[f"return_{horizon}d_comparable_count"] = int(len(values))
                        row[f"return_{horizon}d_median_pct"] = float(values.median()) if len(values) else float("nan")
                        row[f"return_{horizon}d_positive_pct"] = 100.0 * float(values.gt(0).mean()) if len(values) else float("nan")
                        row[f"max_drawdown_{horizon}d_median_pct"] = float(drawdowns.median()) if len(drawdowns) else float("nan")
                    complete = (
                        period_signals.loc[
                            period_signals["future_10d_complete"].map(_as_bool)
                        ]
                        if len(period_signals)
                        else pd.DataFrame()
                    )
                    row["joint_ma_rise_comparable_count"] = int(len(complete))
                    row["joint_ma_rise_run_ge_5_pct"] = (
                        100.0 * float(complete["future_10d_joint_ma_rise_run_ge_5"].map(_as_bool).mean())
                        if len(complete)
                        else float("nan")
                    )
                    rows.append(row)
    return pd.DataFrame(rows)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    daily = pd.read_csv(args.daily_file, compression="infer", dtype={"ts_code": str})
    daily["trade_date"] = daily["trade_date"].map(_compact_date)
    daily = daily.sort_values(["ts_code", "trade_date"], kind="mergesort")
    if daily.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("daily input contains duplicate stock-date rows")

    features_by_code = {
        str(code): prepare_stock_features(
            group,
            cohesion_threshold_pct=args.cohesion_threshold_pct,
            bullish_body_threshold_pct=args.bullish_body_threshold_pct,
            volume_ratio_threshold=args.volume_ratio_threshold,
            volume_lookback=args.volume_lookback,
            deduction_horizon=args.deduction_horizon,
        )
        for code, group in daily.groupby("ts_code", sort=False)
    }
    contexts = _build_anchor_contexts(args)
    missing_codes = sorted(set(contexts["ts_code"].astype(str)) - set(features_by_code))
    if missing_codes:
        raise ValueError(f"daily input missing anchor stocks: {missing_codes}")

    tiers = ("big_bar", "cohesion", "main", "strict")
    records: list[dict[str, Any]] = []
    eligibility_rows: list[dict[str, Any]] = []
    for context in contexts.to_dict("records"):
        eligibility: dict[str, Any] = {
            "ts_code": str(context["ts_code"]),
            "stock_name": context.get("stock_name", ""),
            "anchor_period": int(context["anchor_period"]),
            "anchor_start_date": _compact_date(context["start_date"]),
            "anchor_end_date": _compact_date(context["end_date"]),
            "anchor_left_censored": bool(context["left_censored"]),
            "first_ma20_episode_start_date": _compact_date(context["episode_start_date"]),
            "first_ma20_episode_end_date": _compact_date(context["episode_end_date"]),
            "search_eligible": bool(context["search_eligible"]),
        }
        frame = features_by_code[str(context["ts_code"])]
        positions = {tier: None for tier in tiers}
        if context["search_eligible"]:
            positions = select_tier_signal_positions(
                frame,
                first_segment_end_date=context["episode_end_date"],
                anchor_end_date=context["end_date"],
            )
        for tier in tiers:
            position = positions[tier]
            eligibility[f"{tier}_signal_date"] = (
                str(frame.iloc[position]["trade_date"]) if position is not None else ""
            )
            if position is None:
                continue
            record = build_entry_record(
                frame,
                signal_position=position,
                anchor=pd.Series(context),
                tier=tier,
                first_segment=pd.Series(context),
                forward_horizons=(10, 20, 40, 60),
                deduction_horizon=args.deduction_horizon,
            )
            record["scope"] = f"MA{int(context['anchor_period'])}"
            records.append(record)
        eligibility_rows.append(eligibility)

    events = pd.DataFrame(records)
    if events.empty:
        raise ValueError("no entry signals found")
    event_keys = ["ts_code", "anchor_period", "anchor_start_date", "tier"]
    if events.duplicated(event_keys).any():
        raise ValueError("more than one first signal per anchor and tier")

    unique_events = build_unique_stock_tier_events(events)
    summary_events = pd.concat([events, unique_events], ignore_index=True)

    coverage = build_coverage_summary(contexts, events, tiers=tiers)
    performance = summarize_performance(summary_events, horizons=(10, 20, 40, 60))
    mechanism = summarize_mechanism(summary_events)
    streaks = summarize_streaks(summary_events)
    paired = _paired_tier_summary(events)

    cohesion_split = summary_events.loc[summary_events["tier"].eq("cohesion")].copy()
    cohesion_split["tier"] = np.where(
        cohesion_split["both_deduction_all_below_entry"].map(_as_bool),
        "deduction_pass",
        "deduction_fail",
    )
    deduction_split_performance = summarize_performance(
        cohesion_split, horizons=(10, 20, 40, 60)
    )
    deduction_split_mechanism = summarize_mechanism(cohesion_split)

    sensitivity = _run_sensitivity(
        contexts=contexts,
        features_by_code=features_by_code,
        cohesion_thresholds=(2.0, 3.0, 4.0, 5.0),
        body_thresholds=(2.0, 3.0, 4.0),
        volume_thresholds=(1.2, 1.5, 2.0),
    )

    outputs: dict[str, pd.DataFrame] = {
        "anchor_eligibility.csv": pd.DataFrame(eligibility_rows),
        "entry_event_detail.csv": events,
        "unique_stock_entry_detail.csv": unique_events,
        "coverage_summary.csv": coverage,
        "performance_summary.csv": performance,
        "ma_reacceleration_summary.csv": mechanism,
        "ma_streak_at_entry_summary.csv": streaks,
        "paired_tier_comparison.csv": paired,
        "deduction_split_performance_summary.csv": deduction_split_performance,
        "deduction_split_ma_reacceleration_summary.csv": deduction_split_mechanism,
        "sensitivity_summary.csv": sensitivity,
    }
    for name, frame in outputs.items():
        frame.to_csv(output_dir / name, index=False)

    report_path = output_dir / "report.md"
    _write_report(
        coverage=coverage,
        performance=performance,
        mechanism=mechanism,
        streaks=streaks,
        paired=paired,
        sensitivity=sensitivity,
        deduction_split_performance=deduction_split_performance,
        deduction_split_mechanism=deduction_split_mechanism,
        output=report_path,
    )

    artifact_paths = [output_dir / name for name in outputs] + [report_path]
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "study": "first_ma20_end_cohesion_volume_breakout_entry",
        "target_window": "2026 YTD through 2026-09-30; anchor episodes use full observed intervals and may include 2025",
        "data_semantics": {
            "price_adjustment": "qfq=True",
            "volume_adjustment": "raw volume",
            "daily_input": str(Path(args.daily_file).resolve()),
            "data_source_contract": "formal cache previously built through coreClient.data_provider with source=database_only",
            "trading_days_only": True,
        },
        "definitions": {
            "first_ma20_segment": "first complete global MA20 rising episode intersecting the selected long-MA anchor; search starts strictly after its true end",
            "ma_rising": "MA_t > MA_(t-1); flat, falling, or unavailable breaks the streak",
            "cohesion": f"prior-day (max(MA20,MA30,MA60)-min)/mean <= {args.cohesion_threshold_pct}%",
            "large_bullish_bar": f"close/open-1 >= {args.bullish_body_threshold_pct}%, close > prior close, volume/prior-{args.volume_lookback}-day mean >= {args.volume_ratio_threshold}",
            "deduction_support": f"all next-{args.deduction_horizon}-day MA20 and MA30 deduction prices are below signal close; values use only prices known on signal day",
            "first_bar_filter_order": "first select the earliest cohesion + large-volume bullish bar after the first MA20 episode; apply deduction support only to that same bar and never skip forward to a later bar",
            "strict_deduction": "main deduction support plus final deduction price below first deduction price for both MA20 and MA30",
            "execution": "next trading day open after end-of-day signal",
            "primary_outcome": "next-open forward close returns and high/low excursions at 10/20/40/60 trading days",
        },
        "counts": {
            "daily_rows": int(len(daily)),
            "unique_stocks": int(daily["ts_code"].nunique()),
            "anchor_rows": int(len(contexts)),
            "search_eligible_anchor_rows": int(contexts["search_eligible"].sum()),
            "entry_event_rows_all_tiers": int(len(events)),
            "main_entry_anchor_rows": int(events["tier"].eq("main").sum()),
            "main_entry_unique_stocks": int(unique_events["tier"].eq("main").sum()),
        },
        "selection_bias_warning": "The 178-stock pool was selected using future-observed MA60/MA120 duration rankings. Results are conditional/descriptive and do not establish live tradability. A whole-market walk-forward study is required.",
        "artifacts": {
            path.name: {
                "path": str(path.resolve()),
                "sha256": _sha256(path),
            }
            for path in artifact_paths
        },
    }
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(_clean_json(metadata), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "output_dir": str(output_dir.resolve()),
                "anchor_rows": int(len(contexts)),
                "eligible_anchor_rows": int(contexts["search_eligible"].sum()),
                "main_entry_anchor_rows": int(events["tier"].eq("main").sum()),
                "main_entry_unique_stocks": int(unique_events["tier"].eq("main").sum()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
