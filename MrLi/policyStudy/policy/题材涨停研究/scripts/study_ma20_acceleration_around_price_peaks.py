"""Study whether MA20 sharply accelerates around observed price peaks."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

BPS_PER_LOG_UNIT = 10000.0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--daily-file",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_ppt/2026_through_20260930_qfq/"
            "daily_input_qfq.csv.gz"
        ),
    )
    parser.add_argument(
        "--ma60-file",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_themes/2026_through_20260930_qfq/"
            "ma60_top100_with_ties_named.csv"
        ),
    )
    parser.add_argument(
        "--ma120-file",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_themes/2026_through_20260930_qfq/"
            "ma120_top100_with_ties_named.csv"
        ),
    )
    parser.add_argument(
        "--deck-metadata-file",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_ppt/2026_through_20260930_qfq/"
            "metadata.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "outputs/ma20_acceleration_around_price_peaks/"
            "2026_through_20260930_qfq"
        ),
    )
    parser.add_argument("--speed-window", type=int, default=10)
    parser.add_argument("--baseline-days", type=int, default=40)
    parser.add_argument("--lag-half-window", type=int, default=5)
    parser.add_argument("--relative-gain-threshold", type=float, default=0.25)
    parser.add_argument("--absolute-gain-threshold-bps", type=float, default=5.0)
    parser.add_argument("--percentile-threshold", type=float, default=0.80)
    parser.add_argument(
        "--relative-sensitivity", nargs="+", type=float, default=[0.10, 0.25, 0.50]
    )
    parser.add_argument(
        "--percentile-sensitivity", nargs="+", type=float, default=[0.75, 0.80, 0.90]
    )
    return parser.parse_args(argv)


def compute_ma20_speed_features(
    daily: pd.DataFrame,
    *,
    speed_window: int = 10,
) -> pd.DataFrame:
    """Add scale-free MA20 daily and trailing-window speeds."""
    if speed_window < 2:
        raise ValueError("speed_window must be at least two trading days")
    if "ma20" not in daily.columns:
        raise ValueError("daily input must contain ma20")
    result = daily.sort_values("trade_date").reset_index(drop=True).copy()
    ma20 = pd.to_numeric(result["ma20"], errors="coerce")
    if ma20.dropna().le(0).any():
        raise ValueError("ma20 values must be positive")
    result["ma20_daily_speed_bps"] = (
        np.log(ma20 / ma20.shift(1)) * BPS_PER_LOG_UNIT
    )
    result["ma20_trailing_speed_bps"] = result["ma20_daily_speed_bps"].rolling(
        speed_window, min_periods=speed_window
    ).mean()
    return result


def _percentile_rank(values: pd.Series, value: float) -> float:
    valid = pd.to_numeric(values, errors="coerce").dropna()
    if not np.isfinite(value) or valid.empty:
        return float("nan")
    return float(valid.le(value).mean())


def _large_acceleration_flag(
    *,
    speed: float,
    baseline: float,
    percentile: float,
    relative_gain_threshold: float,
    absolute_gain_threshold_bps: float,
    percentile_threshold: float,
) -> bool:
    if not all(np.isfinite(value) for value in (speed, baseline, percentile)):
        return False
    gain = speed - baseline
    relative_pass = (
        speed >= baseline * (1.0 + relative_gain_threshold)
        if baseline > 0
        else speed >= absolute_gain_threshold_bps
    )
    return bool(
        speed > 0
        and gain >= absolute_gain_threshold_bps
        and relative_pass
        and percentile >= percentile_threshold
    )


def classify_large_acceleration(
    events: pd.DataFrame,
    *,
    speed_col: str,
    gain_col: str,
    percentile_col: str,
    relative_gain_threshold: float = 0.25,
    absolute_gain_threshold_bps: float = 5.0,
    percentile_threshold: float = 0.80,
) -> pd.Series:
    """Vectorized large-acceleration classification for sensitivity analysis."""
    required = {
        "baseline_valid", "prior_baseline_speed_bps",
        speed_col, gain_col, percentile_col,
    }
    missing = required.difference(events.columns)
    if missing:
        raise ValueError(f"missing classifier columns: {sorted(missing)}")
    valid = events["baseline_valid"].fillna(False).astype(bool)
    baseline = pd.to_numeric(events["prior_baseline_speed_bps"], errors="coerce")
    speed = pd.to_numeric(events[speed_col], errors="coerce")
    gain = pd.to_numeric(events[gain_col], errors="coerce")
    percentile = pd.to_numeric(events[percentile_col], errors="coerce")
    relative_pass = pd.Series(False, index=events.index)
    positive_baseline = baseline.gt(0)
    relative_pass.loc[positive_baseline] = speed.loc[positive_baseline].ge(
        baseline.loc[positive_baseline] * (1.0 + relative_gain_threshold)
    )
    relative_pass.loc[~positive_baseline] = speed.loc[~positive_baseline].ge(
        absolute_gain_threshold_bps
    )
    return (
        valid
        & speed.gt(0)
        & gain.ge(absolute_gain_threshold_bps)
        & relative_pass
        & percentile.ge(percentile_threshold)
    ).fillna(False)


def analyze_peak_event(
    anchor_daily: pd.DataFrame,
    *,
    price_col: str = "high",
    speed_window: int = 10,
    baseline_days: int = 40,
    lag_half_window: int = 5,
    relative_gain_threshold: float = 0.25,
    absolute_gain_threshold_bps: float = 5.0,
    percentile_threshold: float = 0.80,
) -> dict[str, object]:
    """Compare MA20 speed at the first observed price peak with prior speed."""
    required = {
        "trade_date", "close", price_col,
        "ma20_daily_speed_bps", "ma20_trailing_speed_bps",
    }
    missing = required.difference(anchor_daily.columns)
    if missing:
        raise ValueError(f"missing peak-event columns: {sorted(missing)}")
    if baseline_days < 1 or lag_half_window < 0:
        raise ValueError("baseline_days must be positive and lag window non-negative")
    frame = anchor_daily.sort_values("trade_date").reset_index(drop=True).copy()
    if frame.empty:
        raise ValueError("anchor_daily is empty")
    prices = pd.to_numeric(frame[price_col], errors="coerce")
    if prices.notna().sum() == 0:
        raise ValueError(f"no valid values in {price_col}")
    peak_value = float(prices.max())
    peak_indices = np.flatnonzero(
        np.isclose(prices.to_numpy(dtype=float), peak_value, rtol=0.0, atol=1e-12)
    )
    peak_index = int(peak_indices[0])
    peak_count = int(len(peak_indices))
    speed_at_peak = float(frame.loc[peak_index, "ma20_trailing_speed_bps"])

    baseline_start = peak_index - speed_window - baseline_days + 1
    baseline_end = peak_index - speed_window + 1
    if baseline_start >= 0 and baseline_end > baseline_start:
        baseline_values = pd.to_numeric(
            frame.loc[
                baseline_start:baseline_end - 1, "ma20_daily_speed_bps"
            ],
            errors="coerce",
        ).dropna()
    else:
        baseline_values = pd.Series(dtype=float)
    baseline_observations = int(len(baseline_values))
    baseline_valid = baseline_observations == baseline_days
    baseline_speed = (
        float(baseline_values.median()) if baseline_valid else float("nan")
    )
    gain = speed_at_peak - baseline_speed
    relative_gain = (
        100.0 * gain / baseline_speed
        if baseline_valid and baseline_speed > 0
        else float("nan")
    )
    percentile_to_peak = _percentile_rank(
        frame.loc[:peak_index, "ma20_trailing_speed_bps"], speed_at_peak
    )
    strong_at_peak = baseline_valid and _large_acceleration_flag(
        speed=speed_at_peak,
        baseline=baseline_speed,
        percentile=percentile_to_peak,
        relative_gain_threshold=relative_gain_threshold,
        absolute_gain_threshold_bps=absolute_gain_threshold_bps,
        percentile_threshold=percentile_threshold,
    )

    lag_start = max(0, peak_index - lag_half_window)
    lag_end = min(len(frame) - 1, peak_index + lag_half_window)
    lag_values = pd.to_numeric(
        frame.loc[lag_start:lag_end, "ma20_trailing_speed_bps"], errors="coerce"
    )
    if lag_values.notna().any():
        lag_index = int(lag_values.idxmax())
        lag_speed = float(frame.loc[lag_index, "ma20_trailing_speed_bps"])
        lag_percentile = _percentile_rank(
            frame.loc[:lag_index, "ma20_trailing_speed_bps"], lag_speed
        )
    else:
        lag_index = peak_index
        lag_speed = float("nan")
        lag_percentile = float("nan")
    lag_gain = lag_speed - baseline_speed
    lag_relative_gain = (
        100.0 * lag_gain / baseline_speed
        if baseline_valid and baseline_speed > 0
        else float("nan")
    )
    strong_with_lag = baseline_valid and _large_acceleration_flag(
        speed=lag_speed,
        baseline=baseline_speed,
        percentile=lag_percentile,
        relative_gain_threshold=relative_gain_threshold,
        absolute_gain_threshold_bps=absolute_gain_threshold_bps,
        percentile_threshold=percentile_threshold,
    )

    denominator = max(len(frame) - 1, 1)
    start_close = float(pd.to_numeric(frame.loc[0, "close"], errors="coerce"))
    return {
        "price_field": price_col,
        "price_peak_value": peak_value,
        "price_peak_index": peak_index,
        "price_peak_date": str(frame.loc[peak_index, "trade_date"]),
        "price_peak_last_date": str(frame.loc[int(peak_indices[-1]), "trade_date"]),
        "price_peak_count": peak_count,
        "price_peak_position": peak_index / denominator,
        "price_peak_at_episode_end": bool(int(peak_indices[-1]) == len(frame) - 1),
        "price_peak_return_from_start_close_pct": (
            100.0 * (peak_value / start_close - 1.0)
            if np.isfinite(start_close) and start_close > 0
            else float("nan")
        ),
        "baseline_valid": bool(baseline_valid),
        "baseline_observations": baseline_observations,
        "prior_baseline_speed_bps": baseline_speed,
        "ma20_speed_at_peak_bps": speed_at_peak,
        "speed_gain_vs_baseline_bps": gain,
        "relative_speed_gain_pct": relative_gain,
        "speed_percentile_to_peak": percentile_to_peak,
        "large_acceleration_at_peak": bool(strong_at_peak),
        "lag_aware_speed_bps": lag_speed,
        "lag_aware_speed_date": str(frame.loc[lag_index, "trade_date"]),
        "lag_aware_speed_peak_distance_days": lag_index - peak_index,
        "lag_aware_speed_gain_bps": lag_gain,
        "lag_aware_relative_speed_gain_pct": lag_relative_gain,
        "lag_aware_speed_percentile": lag_percentile,
        "large_acceleration_with_lag": bool(strong_with_lag),
    }


def compute_background_acceleration(
    anchor_daily: pd.DataFrame,
    *,
    speed_window: int = 10,
    baseline_days: int = 40,
    relative_gain_threshold: float = 0.25,
    absolute_gain_threshold_bps: float = 5.0,
    percentile_threshold: float = 0.80,
) -> dict[str, object]:
    """Apply the peak-day rule to every eligible day as a background rate."""
    frame = anchor_daily.sort_values("trade_date").reset_index(drop=True)
    flags: list[bool] = []
    for index in range(len(frame)):
        baseline_start = index - speed_window - baseline_days + 1
        baseline_end = index - speed_window + 1
        if baseline_start < 0 or baseline_end <= baseline_start:
            continue
        baseline_values = pd.to_numeric(
            frame.loc[
                baseline_start:baseline_end - 1, "ma20_daily_speed_bps"
            ],
            errors="coerce",
        ).dropna()
        if len(baseline_values) != baseline_days:
            continue
        speed = float(frame.loc[index, "ma20_trailing_speed_bps"])
        if not np.isfinite(speed):
            continue
        baseline = float(baseline_values.median())
        percentile = _percentile_rank(
            frame.loc[:index, "ma20_trailing_speed_bps"], speed
        )
        flags.append(
            _large_acceleration_flag(
                speed=speed,
                baseline=baseline,
                percentile=percentile,
                relative_gain_threshold=relative_gain_threshold,
                absolute_gain_threshold_bps=absolute_gain_threshold_bps,
                percentile_threshold=percentile_threshold,
            )
        )
    valid_days = len(flags)
    large_days = int(sum(flags))
    return {
        "background_valid_days": valid_days,
        "background_large_acceleration_days": large_days,
        "background_large_acceleration_rate": (
            large_days / valid_days if valid_days else float("nan")
        ),
    }


def _binomial_upper_tail(observed: int, total: int, probability: float) -> float:
    if total <= 0:
        return float("nan")
    return float(
        sum(
            math.comb(total, value)
            * probability ** value
            * (1.0 - probability) ** (total - value)
            for value in range(observed, total + 1)
        )
    )


def aggregate_peak_events(
    events: pd.DataFrame,
    *,
    percentile_threshold: float = 0.80,
) -> dict[str, object]:
    """Aggregate price-peak events with explicit valid denominators."""
    valid = events["baseline_valid"].fillna(False).astype(bool)
    sample = events.loc[valid].copy()
    total = int(len(events))
    valid_count = int(len(sample))
    at_peak_count = int(sample["large_acceleration_at_peak"].fillna(False).sum())
    lag_count = int(sample["large_acceleration_with_lag"].fillna(False).sum())
    percentiles = pd.to_numeric(sample["speed_percentile_to_peak"], errors="coerce")
    top_count = int(percentiles.ge(percentile_threshold).sum())
    lag_distance = pd.to_numeric(
        sample["lag_aware_speed_peak_distance_days"], errors="coerce"
    )
    if {
        "background_valid_days", "background_large_acceleration_days"
    }.issubset(events.columns):
        background_valid_days = int(
            pd.to_numeric(events["background_valid_days"], errors="coerce").sum()
        )
        background_large_days = int(
            pd.to_numeric(
                events["background_large_acceleration_days"], errors="coerce"
            ).sum()
        )
        background_rate = (
            background_large_days / background_valid_days
            if background_valid_days else float("nan")
        )
    else:
        background_valid_days = 0
        background_large_days = 0
        background_rate = float("nan")

    def percentage(count: int) -> float:
        return 100.0 * count / valid_count if valid_count else float("nan")

    def median(column: str) -> float:
        return float(pd.to_numeric(sample[column], errors="coerce").median())

    return {
        "events": total,
        "valid_events": valid_count,
        "invalid_events": total - valid_count,
        "large_acceleration_at_peak_count": at_peak_count,
        "large_acceleration_at_peak_pct": percentage(at_peak_count),
        "background_valid_days": background_valid_days,
        "background_large_acceleration_days": background_large_days,
        "background_large_acceleration_pct": (
            100.0 * background_rate if np.isfinite(background_rate) else float("nan")
        ),
        "peak_event_to_background_lift": (
            (at_peak_count / valid_count) / background_rate
            if valid_count and np.isfinite(background_rate) and background_rate > 0
            else float("nan")
        ),
        "peak_vs_background_binomial_pvalue": (
            _binomial_upper_tail(at_peak_count, valid_count, background_rate)
            if np.isfinite(background_rate) else float("nan")
        ),
        "large_acceleration_with_lag_count": lag_count,
        "large_acceleration_with_lag_pct": percentage(lag_count),
        "peak_speed_positive_count": int(
            pd.to_numeric(sample["ma20_speed_at_peak_bps"], errors="coerce").gt(0).sum()
        ),
        "peak_speed_top_threshold_count": top_count,
        "peak_speed_top_threshold_pct": percentage(top_count),
        "top_threshold_binomial_pvalue": _binomial_upper_tail(
            top_count, valid_count, 1.0 - percentile_threshold
        ),
        "median_peak_speed_bps": median("ma20_speed_at_peak_bps"),
        "median_speed_gain_bps": median("speed_gain_vs_baseline_bps"),
        "median_relative_speed_gain_pct": median("relative_speed_gain_pct"),
        "median_speed_percentile": median("speed_percentile_to_peak"),
        "median_price_peak_return_pct": median(
            "price_peak_return_from_start_close_pct"
        ),
        "lag_speed_before_peak_count": int(lag_distance.lt(0).sum()),
        "lag_speed_on_peak_count": int(lag_distance.eq(0).sum()),
        "lag_speed_after_peak_count": int(lag_distance.gt(0).sum()),
        "median_lag_speed_distance_days": float(lag_distance.median()),
        "price_peak_at_episode_end_count": int(
            events["price_peak_at_episode_end"].fillna(False).sum()
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_aggregate_table(
    events: pd.DataFrame,
    *,
    percentile_threshold: float,
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for period in (60, 120):
        for price_field in ("high", "close"):
            base = events.loc[
                events["anchor_period"].eq(period)
                & events["price_field"].eq(price_field)
            ]
            cohorts = {
                "all": base,
                "completed": base.loc[~base["anchor_right_censored"]],
                "right_censored": base.loc[base["anchor_right_censored"]],
            }
            for cohort_name, cohort in cohorts.items():
                if cohort.empty:
                    continue
                records.append(
                    {
                        "anchor_period": period,
                        "price_field": price_field,
                        "cohort": cohort_name,
                        **aggregate_peak_events(
                            cohort, percentile_threshold=percentile_threshold
                        ),
                    }
                )
    return pd.DataFrame(records)


def build_threshold_sensitivity(
    events: pd.DataFrame,
    *,
    relative_thresholds: Sequence[float],
    percentile_thresholds: Sequence[float],
    primary_relative_threshold: float,
    primary_percentile_threshold: float,
    absolute_gain_threshold_bps: float,
) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    high_events = events.loc[events["price_field"].eq("high")]
    settings = [
        ("relative", value, primary_percentile_threshold)
        for value in relative_thresholds
    ] + [
        ("percentile", primary_relative_threshold, value)
        for value in percentile_thresholds
    ]
    for period in (60, 120):
        base = high_events.loc[high_events["anchor_period"].eq(period)]
        for cohort_name, cohort in (
            ("all", base),
            ("completed", base.loc[~base["anchor_right_censored"]]),
        ):
            if cohort.empty:
                continue
            valid_count = int(cohort["baseline_valid"].fillna(False).sum())
            for sensitivity_type, relative_threshold, percentile_threshold in settings:
                at_peak = classify_large_acceleration(
                    cohort,
                    speed_col="ma20_speed_at_peak_bps",
                    gain_col="speed_gain_vs_baseline_bps",
                    percentile_col="speed_percentile_to_peak",
                    relative_gain_threshold=relative_threshold,
                    absolute_gain_threshold_bps=absolute_gain_threshold_bps,
                    percentile_threshold=percentile_threshold,
                )
                lag = classify_large_acceleration(
                    cohort,
                    speed_col="lag_aware_speed_bps",
                    gain_col="lag_aware_speed_gain_bps",
                    percentile_col="lag_aware_speed_percentile",
                    relative_gain_threshold=relative_threshold,
                    absolute_gain_threshold_bps=absolute_gain_threshold_bps,
                    percentile_threshold=percentile_threshold,
                )
                records.append(
                    {
                        "anchor_period": period,
                        "cohort": cohort_name,
                        "sensitivity_type": sensitivity_type,
                        "relative_threshold_pct": 100.0 * relative_threshold,
                        "percentile_threshold_pct": 100.0 * percentile_threshold,
                        "absolute_threshold_bps": absolute_gain_threshold_bps,
                        "valid_events": valid_count,
                        "at_peak_count": int(at_peak.sum()),
                        "at_peak_pct": (
                            100.0 * at_peak.sum() / valid_count
                            if valid_count else float("nan")
                        ),
                        "lag_aware_count": int(lag.sum()),
                        "lag_aware_pct": (
                            100.0 * lag.sum() / valid_count
                            if valid_count else float("nan")
                        ),
                    }
                )
    result = pd.DataFrame(records)
    return result.drop_duplicates(
        [
            "anchor_period", "cohort", "relative_threshold_pct",
            "percentile_threshold_pct", "absolute_threshold_bps",
        ]
    ).reset_index(drop=True)


def _aggregate_row(
    aggregate: pd.DataFrame,
    *,
    period: int,
    price_field: str,
    cohort: str,
) -> pd.Series:
    rows = aggregate.loc[
        aggregate["anchor_period"].eq(period)
        & aggregate["price_field"].eq(price_field)
        & aggregate["cohort"].eq(cohort)
    ]
    if len(rows) != 1:
        raise ValueError(
            f"missing aggregate row period={period}, field={price_field}, cohort={cohort}"
        )
    return rows.iloc[0]


def _write_report(
    *,
    aggregate: pd.DataFrame,
    candidates: pd.DataFrame,
    sensitivity: pd.DataFrame,
    output: Path,
    data_end: str,
    speed_window: int,
    baseline_days: int,
    lag_half_window: int,
    relative_threshold: float,
    absolute_threshold_bps: float,
    percentile_threshold: float,
) -> None:
    ma60_completed = _aggregate_row(
        aggregate, period=60, price_field="high", cohort="completed"
    )
    ma60_all = _aggregate_row(
        aggregate, period=60, price_field="high", cohort="all"
    )
    ma120_all = _aggregate_row(
        aggregate, period=120, price_field="high", cohort="all"
    )
    ma60_close = _aggregate_row(
        aggregate, period=60, price_field="close", cohort="completed"
    )
    ma120_close = _aggregate_row(
        aggregate, period=120, price_field="close", cohort="all"
    )
    lines = [
        "# 股价冲顶时MA20是否大幅加速",
        "",
        f"- 数据截至：`{data_end}`；价格和MA20均为前复权。",
        "- 样本：MA60名义前100并列保留107个锚定episode；MA120名义前100并列保留126个锚定episode。",
        "- 股价冲顶：锚定episode内第一次达到前复权日内最高价；收盘价峰值另作敏感性检验。",
        "",
        "## 定义",
        "",
        "MA20日速度：`v_t = 10000 × ln(MA20_t / MA20_(t-1))`，单位bp/交易日。",
        f"冲顶速度取价格最高日结束时最近{speed_window}个交易日的平均 `v_t`。",
        f"基准速度取与冲顶窗口不重叠的此前{baseline_days}个交易日 `v_t` 中位数。",
        "",
        "“大幅加速”必须同时满足：",
        f"1. 冲顶速度位于截至冲顶日的历史{100 * percentile_threshold:.0f}分位以上；",
        f"2. 相对基准至少提高{100 * relative_threshold:.0f}%；",
        f"3. 绝对至少提高{absolute_threshold_bps:.1f} bp/交易日。",
        f"另检查冲顶日前后±{lag_half_window}个交易日内的最高MA20速度，以容许均线轻微滞后。",
        "",
        "## 主结果：日内最高价",
        "",
        "| 锚定段 | 事件数 | 可判定 | 最高日已大幅加速 | 普通日大幅加速基准率 | 冲顶/普通日倍率 | ±5日存在大幅加速 | 冲顶速度处于历史前20% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in (
        ("MA60已结束episode（主样本）", ma60_completed),
        ("MA60全部episode", ma60_all),
        ("MA120全部episode（截至样本日）", ma120_all),
    ):
        lines.append(
            f"| {label} | {int(row['events'])} | {int(row['valid_events'])} | "
            f"{int(row['large_acceleration_at_peak_count'])} ({row['large_acceleration_at_peak_pct']:.1f}%) | "
            f"{row['background_large_acceleration_pct']:.1f}% | "
            f"{row['peak_event_to_background_lift']:.2f}× | "
            f"{int(row['large_acceleration_with_lag_count'])} ({row['large_acceleration_with_lag_pct']:.1f}%) | "
            f"{int(row['peak_speed_top_threshold_count'])} ({row['peak_speed_top_threshold_pct']:.1f}%) |"
        )
    lines.extend(
        [
            "",
            "### 速度与时序",
            "",
            "| 锚定段 | 冲顶MA20速度中位数(bp/日) | 较基准增量中位数(bp/日) | ±5日速度峰值相对价格顶：前/同日/后 | 时差中位数 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, row in (
        ("MA60已结束", ma60_completed),
        ("MA120全部", ma120_all),
    ):
        lines.append(
            f"| {label} | {row['median_peak_speed_bps']:.2f} | "
            f"{row['median_speed_gain_bps']:.2f} | "
            f"{int(row['lag_speed_before_peak_count'])}/"
            f"{int(row['lag_speed_on_peak_count'])}/"
            f"{int(row['lag_speed_after_peak_count'])} | "
            f"{row['median_lag_speed_distance_days']:.1f}日 |"
        )
    lines.extend(
        [
            "",
            "## 收盘价峰值敏感性",
            "",
            "| 锚定段 | 可判定 | 峰值日大幅加速 | ±5日大幅加速 |",
            "|---|---:|---:|---:|",
            f"| MA60已结束 | {int(ma60_close['valid_events'])} | "
            f"{int(ma60_close['large_acceleration_at_peak_count'])} ({ma60_close['large_acceleration_at_peak_pct']:.1f}%) | "
            f"{int(ma60_close['large_acceleration_with_lag_count'])} ({ma60_close['large_acceleration_with_lag_pct']:.1f}%) |",
            f"| MA120全部 | {int(ma120_close['valid_events'])} | "
            f"{int(ma120_close['large_acceleration_at_peak_count'])} ({ma120_close['large_acceleration_at_peak_pct']:.1f}%) | "
            f"{int(ma120_close['large_acceleration_with_lag_count'])} ({ma120_close['large_acceleration_with_lag_pct']:.1f}%) |",
            "",
            "## 强加速样本（按冲顶速度分位和增量排序）",
            "",
            "| 均线锚定 | 代码 | 名称 | 价格顶日期 | MA20速度 | 基准速度 | 增量 | 历史分位 | ±5日速度顶时差 | 右截断 |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for _, row in candidates.head(25).iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | {row['ts_code']} | {row['stock_name']} | "
            f"{row['price_peak_date']} | {row['ma20_speed_at_peak_bps']:.2f} | "
            f"{row['prior_baseline_speed_bps']:.2f} | {row['speed_gain_vs_baseline_bps']:.2f} | "
            f"{100 * row['speed_percentile_to_peak']:.1f}% | "
            f"{int(row['lag_aware_speed_peak_distance_days']):+d}日 | "
            f"{'是' if bool(row['anchor_right_censored']) else '否'} |"
        )
    lines.extend(
        [
            "",
            "## 边界与解释",
            "",
            "- 该研究检验的是伴随关系，不证明MA20加速导致冲顶，也不能单独作为卖点。",
            "- MA20由收盘价构成，而价格顶使用日内最高价，因此同日也可能存在盘中先冲高、收盘回落。",
            "- MA120入选episode全部在2026-09-30右截断，其“最高价”只是截至样本日观察到的最高价，不能当作最终顶部；MA60已结束episode是主样本。",
            "- 价格顶过早、没有40日非重叠基准时标记为不可判定，不进入比例分母。",
            "",
            "## 文件",
            "",
            "- `peak_event_detail.csv`：全部MA60/MA120锚定episode的最高价与收盘价峰值事件。",
            "- `ma20_peak_acceleration_candidates.csv`：日内最高价冲顶时的大幅加速候选。",
            "- `aggregate_summary.csv`：按锚定均线、峰值类型和截断状态汇总。",
            "- `threshold_sensitivity.csv`：相对增速和历史分位阈值敏感性。",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def _as_bool(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _clean_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]
    if isinstance(value, np.generic):
        return _clean_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    args = _parse_args()
    deck_metadata = json.loads(args.deck_metadata_file.read_text(encoding="utf-8"))
    if deck_metadata.get("ranking_price_basis") != "QFQ close (qfq=True)":
        raise ValueError("ranked pool is not QFQ")
    if "database_only" not in str(deck_metadata.get("daily_access", "")):
        raise ValueError("daily source is not database_only")
    if "qfq=True" not in str(deck_metadata.get("daily_access", "")):
        raise ValueError("daily source is not QFQ")
    if args.daily_file.name != deck_metadata.get("daily_input_file"):
        raise ValueError("daily file does not match verified deck metadata")

    daily = pd.read_csv(
        args.daily_file,
        dtype={"ts_code": str, "trade_date": str},
        usecols=["ts_code", "trade_date", "high", "close", "ma20", "ma60", "ma120"],
    )
    featured_groups = {
        str(code): compute_ma20_speed_features(group, speed_window=args.speed_window)
        for code, group in daily.groupby("ts_code", sort=False)
    }

    anchor_frames: list[pd.DataFrame] = []
    for period, path in ((60, args.ma60_file), (120, args.ma120_file)):
        frame = pd.read_csv(
            path,
            dtype={"ts_code": str, "start_date": str, "end_date": str},
        )
        frame["anchor_period"] = period
        frame = frame.rename(
            columns={
                "rank": "anchor_rank",
                "length": "anchor_length",
                "target_days": "anchor_target_days",
                "left_censored": "anchor_left_censored",
                "carried_in": "anchor_carried_in",
                "right_censored": "anchor_right_censored",
            }
        )
        expected = int(deck_metadata[f"ma{period}"]["selected_with_ties"])
        if len(frame) != expected or frame["ts_code"].duplicated().any():
            raise ValueError(f"MA{period} anchor coverage mismatch")
        anchor_frames.append(frame)
    anchors = pd.concat(anchor_frames, ignore_index=True)

    event_records: list[dict[str, object]] = []
    for _, anchor in anchors.sort_values(["anchor_period", "anchor_rank"]).iterrows():
        code = str(anchor["ts_code"])
        full = featured_groups[code]
        start_date = str(anchor["start_date"])
        end_date = str(anchor["end_date"])
        episode = full.loc[
            full["trade_date"].ge(start_date) & full["trade_date"].le(end_date)
        ].copy().reset_index(drop=True)
        expected_length = int(anchor["anchor_length"])
        if len(episode) != expected_length:
            raise ValueError(
                f"{code} MA{anchor['anchor_period']} expected {expected_length} rows, "
                f"got {len(episode)}"
            )
        ma_col = f"ma{int(anchor['anchor_period'])}"
        full_ma = pd.to_numeric(full[ma_col], errors="coerce")
        full_diff = full_ma.diff()
        episode_indices = full.index[
            full["trade_date"].ge(start_date) & full["trade_date"].le(end_date)
        ]
        if not full_diff.loc[episode_indices].gt(0).all():
            raise ValueError(f"{code} {ma_col} anchor is not strictly rising")
        background = compute_background_acceleration(
            episode,
            speed_window=args.speed_window,
            baseline_days=args.baseline_days,
            relative_gain_threshold=args.relative_gain_threshold,
            absolute_gain_threshold_bps=args.absolute_gain_threshold_bps,
            percentile_threshold=args.percentile_threshold,
        )
        context = {
            "anchor_key": f"MA{int(anchor['anchor_period'])}:{code}:{start_date}",
            "anchor_period": int(anchor["anchor_period"]),
            "anchor_rank": int(anchor["anchor_rank"]),
            "ts_code": code,
            "stock_name": str(anchor["stock_name"]),
            "primary_theme": str(anchor["primary_theme"]),
            "broad_category": str(anchor["broad_category"]),
            "membership": str(anchor["membership"]),
            "anchor_start_date": start_date,
            "anchor_end_date": end_date,
            "anchor_length": expected_length,
            "anchor_target_days": int(anchor["anchor_target_days"]),
            "anchor_left_censored": _as_bool(anchor["anchor_left_censored"]),
            "anchor_carried_in": _as_bool(anchor["anchor_carried_in"]),
            "anchor_right_censored": _as_bool(anchor["anchor_right_censored"]),
            **background,
        }
        for price_col in ("high", "close"):
            event = analyze_peak_event(
                episode,
                price_col=price_col,
                speed_window=args.speed_window,
                baseline_days=args.baseline_days,
                lag_half_window=args.lag_half_window,
                relative_gain_threshold=args.relative_gain_threshold,
                absolute_gain_threshold_bps=args.absolute_gain_threshold_bps,
                percentile_threshold=args.percentile_threshold,
            )
            event_records.append({**context, **event})
    events = pd.DataFrame(event_records)
    expected_anchors = int(deck_metadata["ma60"]["selected_with_ties"]) + int(
        deck_metadata["ma120"]["selected_with_ties"]
    )
    if len(events) != 2 * expected_anchors:
        raise ValueError("peak event row count mismatch")
    if events["anchor_key"].nunique() != expected_anchors:
        raise ValueError("anchor keys are not unique")
    if not events.loc[events["anchor_period"].eq(120), "anchor_right_censored"].all():
        raise ValueError("expected all selected MA120 anchors to be right-censored")

    aggregate = build_aggregate_table(
        events, percentile_threshold=args.percentile_threshold
    )
    sensitivity = build_threshold_sensitivity(
        events,
        relative_thresholds=args.relative_sensitivity,
        percentile_thresholds=args.percentile_sensitivity,
        primary_relative_threshold=args.relative_gain_threshold,
        primary_percentile_threshold=args.percentile_threshold,
        absolute_gain_threshold_bps=args.absolute_gain_threshold_bps,
    )
    candidates = events.loc[
        events["price_field"].eq("high")
        & events["large_acceleration_at_peak"]
    ].copy()
    candidates = candidates.sort_values(
        [
            "anchor_right_censored", "speed_percentile_to_peak",
            "speed_gain_vs_baseline_bps", "price_peak_return_from_start_close_pct",
        ],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    events_path = args.output_dir / "peak_event_detail.csv"
    candidates_path = args.output_dir / "ma20_peak_acceleration_candidates.csv"
    aggregate_path = args.output_dir / "aggregate_summary.csv"
    sensitivity_path = args.output_dir / "threshold_sensitivity.csv"
    report_path = args.output_dir / "report.md"
    events.to_csv(events_path, index=False, encoding="utf-8-sig")
    candidates.to_csv(candidates_path, index=False, encoding="utf-8-sig")
    aggregate.to_csv(aggregate_path, index=False, encoding="utf-8-sig")
    sensitivity.to_csv(sensitivity_path, index=False, encoding="utf-8-sig")
    _write_report(
        aggregate=aggregate,
        candidates=candidates,
        sensitivity=sensitivity,
        output=report_path,
        data_end=str(deck_metadata["actual_target_end"]),
        speed_window=args.speed_window,
        baseline_days=args.baseline_days,
        lag_half_window=args.lag_half_window,
        relative_threshold=args.relative_gain_threshold,
        absolute_threshold_bps=args.absolute_gain_threshold_bps,
        percentile_threshold=args.percentile_threshold,
    )

    output_files = [
        events_path, candidates_path, aggregate_path, sensitivity_path, report_path
    ]
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "price_basis": "QFQ OHLC and MA20 (qfq=True)",
        "daily_access": deck_metadata["daily_access"],
        "actual_target_end": deck_metadata["actual_target_end"],
        "anchor_samples": {
            "ma60": int(deck_metadata["ma60"]["selected_with_ties"]),
            "ma120": int(deck_metadata["ma120"]["selected_with_ties"]),
            "total_anchor_episodes": expected_anchors,
        },
        "event_rows": int(len(events)),
        "price_peak_definition": (
            "first date attaining the maximum QFQ daily high within each selected anchor "
            "episode; maximum close is a sensitivity endpoint"
        ),
        "ma20_daily_speed_definition": (
            "10000 * ln(MA20_t / MA20_(t-1)) basis points per trading day"
        ),
        "peak_speed_definition": (
            f"mean MA20 daily log speed over the trailing {args.speed_window} trading "
            "days ending on the price-peak date"
        ),
        "baseline_definition": (
            f"median MA20 daily log speed over the prior non-overlapping "
            f"{args.baseline_days} trading days"
        ),
        "large_acceleration_rule": {
            "relative_gain_threshold": args.relative_gain_threshold,
            "absolute_gain_threshold_bps_per_day": args.absolute_gain_threshold_bps,
            "within_history_percentile_threshold": args.percentile_threshold,
        },
        "lag_sensitivity_half_window_days": args.lag_half_window,
        "ma120_censoring_note": (
            "all selected MA120 anchor episodes are right-censored at 20260930; their "
            "price peaks are observed-to-date, not final tops"
        ),
        "aggregate": aggregate.to_dict(orient="records"),
        "source_sha256": {
            str(path): _sha256(path)
            for path in (
                args.daily_file,
                args.ma60_file,
                args.ma120_file,
                args.deck_metadata_file,
            )
        },
        "output_sha256": {path.name: _sha256(path) for path in output_files},
    }
    clean_metadata = _clean_json(metadata)
    metadata_path = args.output_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(clean_metadata, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "events": len(events),
                "candidates": len(candidates),
                "aggregate": _clean_json(aggregate.to_dict(orient="records")),
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
