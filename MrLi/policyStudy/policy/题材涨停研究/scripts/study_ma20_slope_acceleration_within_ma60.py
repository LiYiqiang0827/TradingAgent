"""Measure MA20 speed and acceleration inside selected MA60 rising episodes."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

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
        "--segment-file",
        type=Path,
        default=Path(
            "outputs/ma_short_segments_within_long_episodes/2026_through_20260930_qfq/"
            "short_ma_segments_detail.csv"
        ),
    )
    parser.add_argument(
        "--anchor-summary-file",
        type=Path,
        default=Path(
            "outputs/ma_short_segments_within_long_episodes/2026_through_20260930_qfq/"
            "anchor_short_ma_summary.csv"
        ),
    )
    parser.add_argument(
        "--classifications-file",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_ppt/2026_through_20260930_qfq/"
            "classifications.csv"
        ),
    )
    parser.add_argument(
        "--deck-metadata-file",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_ppt/2026_through_20260930_qfq/metadata.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "outputs/ma20_slope_acceleration_within_ma60/2026_through_20260930_qfq"
        ),
    )
    parser.add_argument("--min-segment-days", type=int, default=5)
    parser.add_argument("--intra-segment-min-days", type=int, default=10)
    parser.add_argument("--acceleration-threshold", type=float, default=0.25)
    parser.add_argument("--min-absolute-increase-bps", type=float, default=2.0)
    parser.add_argument(
        "--sensitivity-thresholds", nargs="+", type=float, default=[0.10, 0.25, 0.50]
    )
    return parser.parse_args(argv)


def terminal_acceleration_tier(row: pd.Series) -> str:
    if not bool(row.get("terminal_comparison_valid", False)):
        return "N-不可判定"
    if bool(row.get("combined_terminal_acceleration_25", False)):
        return "A-跨段且段内加速"
    if bool(row.get("terminal_acceleration_25", False)):
        return "B-跨段加速"
    if bool(row.get("final_is_fastest", False)):
        return "C-末段最快但未达强加速"
    return "D-未达到末段加速"


def _compact_date(value: Any) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"invalid compact date: {value!r}")
    return text


def _safe_relative_pct(current: float, reference: float) -> float:
    if not np.isfinite(current) or not np.isfinite(reference) or reference <= 0:
        return float("nan")
    return 100.0 * (current / reference - 1.0)


def _ols_slope_and_r2(values: np.ndarray) -> tuple[float, float]:
    if len(values) < 2 or not np.isfinite(values).all():
        return float("nan"), float("nan")
    x = np.arange(len(values), dtype=float)
    slope, intercept = np.polyfit(x, values, 1)
    fitted = intercept + slope * x
    residual = float(np.square(values - fitted).sum())
    total = float(np.square(values - values.mean()).sum())
    r2 = 1.0 if total <= 1e-24 else 1.0 - residual / total
    return float(slope), float(r2)


def compute_segment_speed_metrics(
    stock_daily: pd.DataFrame,
    segment: pd.Series,
) -> dict[str, Any]:
    """Compute scale-invariant MA20 speed metrics for one clipped rising segment."""
    required_daily = {"trade_date", "ma20"}
    if missing := required_daily.difference(stock_daily.columns):
        raise ValueError(f"daily data missing columns: {sorted(missing)}")
    required_segment = {
        "segment_index", "clipped_start_date", "clipped_end_date", "days_in_anchor"
    }
    if missing := required_segment.difference(segment.index):
        raise ValueError(f"segment missing fields: {sorted(missing)}")

    daily = stock_daily.copy()
    daily["trade_date"] = daily["trade_date"].map(_compact_date)
    daily["ma20"] = pd.to_numeric(daily["ma20"], errors="coerce")
    daily = daily.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    daily = daily.reset_index(drop=True)
    daily["previous_ma20"] = daily["ma20"].shift()
    daily["log_speed_bps"] = BPS_PER_LOG_UNIT * np.log(
        daily["ma20"] / daily["previous_ma20"]
    )

    start = _compact_date(segment["clipped_start_date"])
    end = _compact_date(segment["clipped_end_date"])
    clipped = daily.loc[daily["trade_date"].between(start, end)].copy()
    expected_days = int(segment["days_in_anchor"])
    if len(clipped) != expected_days:
        raise ValueError(
            f"segment day count mismatch: expected={expected_days}, actual={len(clipped)}"
        )
    if clipped.empty:
        raise ValueError("segment contains no daily rows")
    if clipped[["ma20", "previous_ma20", "log_speed_bps"]].isna().any().any():
        raise ValueError("segment contains unavailable MA20 speed values")
    if (clipped[["ma20", "previous_ma20"]] <= 0).any().any():
        raise ValueError("MA20 must be positive for log-speed measurement")

    speeds = clipped["log_speed_bps"].to_numpy(dtype=float)
    if not np.isfinite(speeds).all() or (speeds <= 0).any():
        raise ValueError("MA20 rising segment contains non-positive log speed")

    mean_speed = float(speeds.mean())
    median_speed = float(np.median(speeds))
    speed_trend, _ = _ols_slope_and_r2(speeds)
    half = len(speeds) // 2
    if half >= 2:
        first_half = float(speeds[:half].mean())
        second_half = float(speeds[-half:].mean())
        half_diff = second_half - first_half
        half_relative = _safe_relative_pct(second_half, first_half)
    else:
        first_half = second_half = half_diff = half_relative = float("nan")

    start_row_index = int(clipped.index[0])
    if start_row_index <= 0:
        raise ValueError("daily history lacks the observation before segment start")
    prior_ma20 = float(daily.loc[start_row_index - 1, "ma20"])
    ma20_values = np.concatenate(
        ([prior_ma20], clipped["ma20"].to_numpy(dtype=float))
    )
    log_ma20 = np.log(ma20_values)
    log_ma20_slope, log_ma20_r2 = _ols_slope_and_r2(log_ma20)

    return {
        "mean_log_speed_bps_per_day": mean_speed,
        "median_log_speed_bps_per_day": median_speed,
        "compound_speed_bps_per_day": float(math.expm1(mean_speed / BPS_PER_LOG_UNIT) * BPS_PER_LOG_UNIT),
        "ols_log_ma20_slope_bps_per_day": float(log_ma20_slope * BPS_PER_LOG_UNIT),
        "ols_log_ma20_r2": log_ma20_r2,
        "daily_speed_trend_bps_per_day2": speed_trend,
        "first_half_mean_speed_bps_per_day": first_half,
        "second_half_mean_speed_bps_per_day": second_half,
        "second_vs_first_half_diff_bps": half_diff,
        "second_vs_first_half_pct": half_relative,
        "ma20_before_segment": prior_ma20,
        "ma20_at_segment_end": float(clipped.iloc[-1]["ma20"]),
        "segment_ma20_return_pct": float(
            100.0 * (float(clipped.iloc[-1]["ma20"]) / prior_ma20 - 1.0)
        ),
        "speed_observations": int(len(speeds)),
    }


def _threshold_passes(
    current: float,
    reference: float,
    *,
    threshold: float,
    min_absolute_increase_bps: float,
) -> bool:
    if not np.isfinite(current) or not np.isfinite(reference) or reference <= 0:
        return False
    return bool(
        current >= reference * (1.0 + threshold)
        and current - reference >= min_absolute_increase_bps
    )


def summarize_stock_acceleration(
    segments: pd.DataFrame,
    *,
    min_segment_days: int = 5,
    intra_segment_min_days: int = 10,
    acceleration_threshold: float = 0.25,
    min_absolute_increase_bps: float = 2.0,
) -> dict[str, Any]:
    """Summarize inter-segment and within-final-segment MA20 acceleration."""
    required = {
        "segment_index", "days_in_anchor", "mean_log_speed_bps_per_day",
        "first_half_mean_speed_bps_per_day", "second_half_mean_speed_bps_per_day",
        "second_vs_first_half_pct", "daily_speed_trend_bps_per_day2",
    }
    if missing := required.difference(segments.columns):
        raise ValueError(f"segment metrics missing columns: {sorted(missing)}")
    if min_segment_days <= 0 or intra_segment_min_days <= 0:
        raise ValueError("segment-day thresholds must be positive")
    if acceleration_threshold < 0 or min_absolute_increase_bps < 0:
        raise ValueError("acceleration thresholds must be non-negative")
    if segments.empty:
        raise ValueError("stock has no MA20 rising segments")

    ordered = segments.copy().sort_values("segment_index").reset_index(drop=True)
    ordered["segment_index"] = pd.to_numeric(ordered["segment_index"], errors="raise").astype(int)
    ordered["days_in_anchor"] = pd.to_numeric(ordered["days_in_anchor"], errors="raise").astype(int)
    ordered["mean_log_speed_bps_per_day"] = pd.to_numeric(
        ordered["mean_log_speed_bps_per_day"], errors="coerce"
    )
    actual_final = ordered.iloc[-1]
    actual_final_index = int(actual_final["segment_index"])
    eligible = ordered.loc[
        ordered["days_in_anchor"].ge(min_segment_days)
        & np.isfinite(ordered["mean_log_speed_bps_per_day"])
    ].copy()
    eligible_count = int(len(eligible))
    last_eligible_index = (
        int(eligible.iloc[-1]["segment_index"]) if eligible_count else float("nan")
    )
    actual_final_eligible = bool(
        eligible_count and last_eligible_index == actual_final_index
    )

    speeds = eligible["mean_log_speed_bps_per_day"].to_numpy(dtype=float)
    speed_trend_valid = bool(eligible_count >= 3)
    strictly_increasing = bool(speed_trend_valid and np.all(np.diff(speeds) > 0))
    if speed_trend_valid:
        speed_order_spearman = float(
            pd.Series(np.arange(eligible_count), dtype=float).corr(
                pd.Series(speeds, dtype=float), method="spearman"
            )
        )
    else:
        speed_order_spearman = float("nan")
    pairwise_increase_share = (
        float((np.diff(speeds) > 0).mean()) if eligible_count >= 2 else float("nan")
    )

    terminal_valid = bool(actual_final_eligible and eligible_count >= 2)
    final_speed = previous_speed = prior_median = float("nan")
    final_vs_previous_pct = final_vs_prior_median_pct = float("nan")
    final_vs_previous_diff = final_vs_prior_median_diff = float("nan")
    final_is_fastest = False
    terminal_acceleration = False
    if terminal_valid:
        final_speed = float(eligible.iloc[-1]["mean_log_speed_bps_per_day"])
        previous_speed = float(eligible.iloc[-2]["mean_log_speed_bps_per_day"])
        prior_speeds = eligible.iloc[:-1]["mean_log_speed_bps_per_day"].to_numpy(dtype=float)
        prior_median = float(np.median(prior_speeds))
        final_vs_previous_pct = _safe_relative_pct(final_speed, previous_speed)
        final_vs_prior_median_pct = _safe_relative_pct(final_speed, prior_median)
        final_vs_previous_diff = final_speed - previous_speed
        final_vs_prior_median_diff = final_speed - prior_median
        final_is_fastest = bool(final_speed > float(np.max(prior_speeds)))
        terminal_acceleration = bool(
            _threshold_passes(
                final_speed,
                previous_speed,
                threshold=acceleration_threshold,
                min_absolute_increase_bps=min_absolute_increase_bps,
            )
            and _threshold_passes(
                final_speed,
                prior_median,
                threshold=acceleration_threshold,
                min_absolute_increase_bps=min_absolute_increase_bps,
            )
        )

    intra_valid = bool(
        actual_final_eligible
        and int(actual_final["days_in_anchor"]) >= intra_segment_min_days
        and np.isfinite(float(actual_final["first_half_mean_speed_bps_per_day"]))
        and np.isfinite(float(actual_final["second_half_mean_speed_bps_per_day"]))
    )
    first_half = second_half = half_relative = half_diff = speed_trend = float("nan")
    intra_acceleration = False
    if intra_valid:
        first_half = float(actual_final["first_half_mean_speed_bps_per_day"])
        second_half = float(actual_final["second_half_mean_speed_bps_per_day"])
        half_relative = _safe_relative_pct(second_half, first_half)
        half_diff = second_half - first_half
        speed_trend = float(actual_final["daily_speed_trend_bps_per_day2"])
        intra_acceleration = bool(
            _threshold_passes(
                second_half,
                first_half,
                threshold=acceleration_threshold,
                min_absolute_increase_bps=min_absolute_increase_bps,
            )
            and speed_trend > 0
        )

    return {
        "segment_count": int(len(ordered)),
        "eligible_segment_count": eligible_count,
        "min_segment_days": int(min_segment_days),
        "intra_segment_min_days": int(intra_segment_min_days),
        "actual_final_segment_index": actual_final_index,
        "actual_final_segment_days": int(actual_final["days_in_anchor"]),
        "last_eligible_segment_index": last_eligible_index,
        "actual_final_segment_eligible": actual_final_eligible,
        "speed_trend_valid": speed_trend_valid,
        "strictly_increasing_speed": strictly_increasing,
        "speed_order_spearman": speed_order_spearman,
        "pairwise_increase_share": pairwise_increase_share,
        "eligible_speed_sequence_bps": " | ".join(f"{value:.6f}" for value in speeds),
        "terminal_comparison_valid": terminal_valid,
        "final_speed_bps_per_day": final_speed,
        "previous_speed_bps_per_day": previous_speed,
        "prior_speed_median_bps_per_day": prior_median,
        "final_vs_previous_pct": final_vs_previous_pct,
        "final_vs_prior_median_pct": final_vs_prior_median_pct,
        "final_vs_previous_diff_bps": final_vs_previous_diff,
        "final_vs_prior_median_diff_bps": final_vs_prior_median_diff,
        "final_is_fastest": final_is_fastest,
        "classification_threshold_pct": 100.0 * acceleration_threshold,
        "min_absolute_increase_bps": float(min_absolute_increase_bps),
        "terminal_acceleration_25": terminal_acceleration,
        "final_intra_comparison_valid": intra_valid,
        "final_first_half_speed_bps_per_day": first_half,
        "final_second_half_speed_bps_per_day": second_half,
        "final_second_vs_first_half_pct": half_relative,
        "final_second_vs_first_half_diff_bps": half_diff,
        "final_daily_speed_trend_bps_per_day2": speed_trend,
        "final_intra_segment_acceleration_25": intra_acceleration,
        "combined_terminal_acceleration_25": bool(
            terminal_acceleration and intra_acceleration
        ),
    }


def aggregate_acceleration_results(
    stocks: pd.DataFrame,
    *,
    thresholds: Iterable[float] = (0.10, 0.25, 0.50),
    min_absolute_increase_bps: float = 2.0,
) -> dict[str, Any]:
    """Aggregate continuous terminal-speed metrics at several transparent thresholds."""
    required = {
        "terminal_comparison_valid", "final_vs_previous_pct",
        "final_vs_prior_median_pct", "final_vs_previous_diff_bps",
        "final_vs_prior_median_diff_bps", "final_intra_comparison_valid",
        "final_second_vs_first_half_pct", "final_second_vs_first_half_diff_bps",
        "final_daily_speed_trend_bps_per_day2", "strictly_increasing_speed",
        "speed_trend_valid", "final_is_fastest",
    }
    if missing := required.difference(stocks.columns):
        raise ValueError(f"stock summary missing columns: {sorted(missing)}")
    terminal_valid = stocks["terminal_comparison_valid"].fillna(False).astype(bool)
    intra_valid = stocks["final_intra_comparison_valid"].fillna(False).astype(bool)
    trend_valid = stocks["speed_trend_valid"].fillna(False).astype(bool)
    result: dict[str, Any] = {
        "stocks": int(len(stocks)),
        "terminal_comparison_sample": int(terminal_valid.sum()),
        "final_intra_comparison_sample": int(intra_valid.sum()),
        "speed_trend_sample": int(trend_valid.sum()),
        "strictly_increasing_count": int(
            (stocks["strictly_increasing_speed"].fillna(False).astype(bool) & trend_valid).sum()
        ),
        "final_is_fastest_count": int(
            (stocks["final_is_fastest"].fillna(False).astype(bool) & terminal_valid).sum()
        ),
        "last_faster_than_previous_count": int(
            (terminal_valid & stocks["final_vs_previous_pct"].gt(0)).sum()
        ),
        "last_faster_than_prior_median_count": int(
            (terminal_valid & stocks["final_vs_prior_median_pct"].gt(0)).sum()
        ),
        "thresholds": {},
    }
    for threshold in thresholds:
        if threshold < 0:
            raise ValueError("thresholds must be non-negative")
        terminal = (
            terminal_valid
            & stocks["final_vs_previous_pct"].ge(100.0 * threshold)
            & stocks["final_vs_prior_median_pct"].ge(100.0 * threshold)
            & stocks["final_vs_previous_diff_bps"].ge(min_absolute_increase_bps)
            & stocks["final_vs_prior_median_diff_bps"].ge(min_absolute_increase_bps)
        )
        intra = (
            intra_valid
            & stocks["final_second_vs_first_half_pct"].ge(100.0 * threshold)
            & stocks["final_second_vs_first_half_diff_bps"].ge(min_absolute_increase_bps)
            & stocks["final_daily_speed_trend_bps_per_day2"].gt(0)
        )
        combined_sample = terminal_valid & intra_valid
        combined = terminal & intra
        key = f"{int(round(100.0 * threshold))}pct"
        result["thresholds"][key] = {
            "relative_threshold_pct": 100.0 * threshold,
            "absolute_threshold_bps_per_day": float(min_absolute_increase_bps),
            "terminal_acceleration_count": int(terminal.sum()),
            "terminal_acceleration_pct": (
                100.0 * int(terminal.sum()) / int(terminal_valid.sum())
                if terminal_valid.any() else float("nan")
            ),
            "intra_segment_acceleration_count": int(intra.sum()),
            "intra_segment_acceleration_pct": (
                100.0 * int(intra.sum()) / int(intra_valid.sum())
                if intra_valid.any() else float("nan")
            ),
            "combined_comparison_sample": int(combined_sample.sum()),
            "combined_terminal_acceleration_count": int(combined.sum()),
            "combined_terminal_acceleration_pct": (
                100.0 * int(combined.sum()) / int(combined_sample.sum())
                if combined_sample.any() else float("nan")
            ),
        }
    return result


def summarize_min_day_sensitivity(
    segment_speeds: pd.DataFrame,
    *,
    min_day_values: Iterable[int] = (1, 5, 10),
    intra_segment_min_days: int = 10,
    acceleration_threshold: float = 0.25,
    min_absolute_increase_bps: float = 2.0,
) -> pd.DataFrame:
    """Recompute stock sequences for each segment-duration cutoff."""
    if "ts_code" not in segment_speeds.columns:
        raise ValueError("segment_speeds must contain ts_code")
    records: list[dict[str, Any]] = []
    threshold_key = f"{int(round(acceleration_threshold * 100))}pct"
    for min_days in min_day_values:
        stock_rows = [
            {
                "ts_code": code,
                **summarize_stock_acceleration(
                    group,
                    min_segment_days=int(min_days),
                    intra_segment_min_days=intra_segment_min_days,
                    acceleration_threshold=acceleration_threshold,
                    min_absolute_increase_bps=min_absolute_increase_bps,
                ),
            }
            for code, group in segment_speeds.groupby("ts_code", sort=False)
        ]
        stocks = pd.DataFrame(stock_rows)
        aggregate = aggregate_acceleration_results(
            stocks,
            thresholds=(acceleration_threshold,),
            min_absolute_increase_bps=min_absolute_increase_bps,
        )
        threshold = aggregate["thresholds"][threshold_key]
        trend_sample = int(aggregate["speed_trend_sample"])
        strict_count = int(aggregate["strictly_increasing_count"])
        records.append(
            {
                "min_segment_days": int(min_days),
                "stocks": int(aggregate["stocks"]),
                "speed_trend_sample": trend_sample,
                "strictly_increasing_count": strict_count,
                "strictly_increasing_pct": _share(strict_count, trend_sample),
                "terminal_comparison_sample": int(
                    aggregate["terminal_comparison_sample"]
                ),
                "terminal_acceleration_count": int(
                    threshold["terminal_acceleration_count"]
                ),
                "terminal_acceleration_pct": float(
                    threshold["terminal_acceleration_pct"]
                ),
                "final_is_fastest_count": int(aggregate["final_is_fastest_count"]),
                "actual_final_segment_below_min_days_count": int(
                    (~stocks["actual_final_segment_eligible"]).sum()
                ),
            }
        )
    return pd.DataFrame(records)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _share(count: int, sample: int) -> float:
    return 100.0 * count / sample if sample else float("nan")


def _write_report(
    stocks: pd.DataFrame,
    aggregate: dict[str, Any],
    candidates: pd.DataFrame,
    output: Path,
    *,
    data_end: str,
    min_segment_days: int,
    intra_segment_min_days: int,
    acceleration_threshold: float,
    min_absolute_increase_bps: float,
) -> None:
    trend_sample = int(aggregate["speed_trend_sample"])
    strict_count = int(aggregate["strictly_increasing_count"])
    terminal_sample = int(aggregate["terminal_comparison_sample"])
    intra_sample = int(aggregate["final_intra_comparison_sample"])
    threshold_25 = aggregate["thresholds"]["25pct"]
    positive_rho_count = int(aggregate["positive_spearman_count"])
    rho_half_count = int(aggregate["spearman_ge_0_5_count"])
    lines = [
        "# MA60上升阶段内MA20逐段斜率与末段加速研究",
        "",
        f"- 数据截止：`{data_end}`；价格口径：前复权（`qfq=True`）。",
        f"- 样本：MA60名义前100并列保留的{len(stocks)}只股票，每只使用已选中的完整已观测MA60上升episode。",
        "- MA20上升段：严格采用 `MA20_t > MA20_(t-1)` 的最大连续段。",
        "",
        "## 斜率/上升速度定义",
        "",
        "为了跨股票比较，不使用元/日，而使用MA20的日对数变化：",
        "",
        "`v_t = 10000 × ln(MA20_t / MA20_(t-1))`，单位为基点/交易日（bp/day）。",
        "",
        "每段主速度取该段所有上升日 `v_t` 的均值；同时输出中位数、对数MA20的OLS斜率和R²。该定义不受股票绝对价格高低影响，且对复利可加。",
        "",
        f"- 主分析仅比较持续至少{min_segment_days}日的MA20上升段，避免1—2日微小波动主导结果。",
        f"- “逐段不断上升”：至少3个合格段，且每个后一段的平均速度都严格高于前一段；同时保留段序与速度的Spearman相关系数。",
        f"- “末段强加速”：实际最后一段必须合格，其速度同时高于上一合格段和此前所有合格段中位数至少{100 * acceleration_threshold:.0f}%，且绝对增量均不少于{min_absolute_increase_bps:.1f} bp/day。",
        f"- “末段内部加速”：最后一段至少{intra_segment_min_days}日，后半段速度较前半段至少提高{100 * acceleration_threshold:.0f}%且不少于{min_absolute_increase_bps:.1f} bp/day，同时日速度回归趋势为正。",
        "- 所有连续指标均保留，25%只是明确的筛选阈值；另给出10%和50%敏感性结果。",
        "",
        "## 总体结果",
        "",
        f"- 可检验逐段趋势（至少3个≥{min_segment_days}日段）：{trend_sample}只。",
        f"- 每一段速度严格递增：{strict_count}只，占可检验样本{_share(strict_count, trend_sample):.1f}%。",
        f"- Spearman相关系数为正：{positive_rho_count}只（{_share(positive_rho_count, trend_sample):.1f}%）；≥0.5：{rho_half_count}只（{_share(rho_half_count, trend_sample):.1f}%）。",
        f"- 可比较实际末段与前段：{terminal_sample}只；末段快于上一段{aggregate['last_faster_than_previous_count']}只（{_share(int(aggregate['last_faster_than_previous_count']), terminal_sample):.1f}%）；末段为历史合格段最快{aggregate['final_is_fastest_count']}只（{_share(int(aggregate['final_is_fastest_count']), terminal_sample):.1f}%）。",
        f"- 25%末段跨段强加速：{threshold_25['terminal_acceleration_count']}只，占可比较样本{threshold_25['terminal_acceleration_pct']:.1f}%。",
        f"- 可检验末段内部加速：{intra_sample}只；25%内部强加速{threshold_25['intra_segment_acceleration_count']}只（{threshold_25['intra_segment_acceleration_pct']:.1f}%）。",
        f"- 同时满足跨段强加速与末段内部强加速：{threshold_25['combined_terminal_acceleration_count']}只，占同时可比较样本{threshold_25['combined_terminal_acceleration_pct']:.1f}%。",
        "",
        "### 阈值敏感性",
        "",
        "| 相对阈值 | 跨段末段加速 | 段内加速 | 两者同时满足 |",
        "|---:|---:|---:|---:|",
    ]
    for key in ("10pct", "25pct", "50pct"):
        item = aggregate["thresholds"][key]
        lines.append(
            f"| {item['relative_threshold_pct']:.0f}% | "
            f"{item['terminal_acceleration_count']}/{terminal_sample} ({item['terminal_acceleration_pct']:.1f}%) | "
            f"{item['intra_segment_acceleration_count']}/{intra_sample} ({item['intra_segment_acceleration_pct']:.1f}%) | "
            f"{item['combined_terminal_acceleration_count']}/{item['combined_comparison_sample']} ({item['combined_terminal_acceleration_pct']:.1f}%) |"
        )

    lines.extend(
        [
            "",
            "## 末段加速候选（按分层和相对前段增速排序）",
            "",
            "| 代码 | 名称 | 分层 | 合格段数 | 末段速度(bp/日) | 较上一段 | 较此前中位数 | 后半段较前半段 | MA60是否右截断 |",
            "|---|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for _, row in candidates.head(20).iterrows():
        intra_text = (
            f"{row['final_second_vs_first_half_pct']:.1f}%"
            if bool(row["final_intra_comparison_valid"]) else "—"
        )
        lines.append(
            f"| {row['ts_code']} | {row['stock_name']} | {row['acceleration_tier']} | "
            f"{int(row['eligible_segment_count'])} | {row['final_speed_bps_per_day']:.2f} | "
            f"{row['final_vs_previous_pct']:.1f}% | {row['final_vs_prior_median_pct']:.1f}% | "
            f"{intra_text} | {'是' if bool(row['anchor_right_censored']) else '否'} |"
        )
    lines.extend(
        [
            "",
            "## 解读限制",
            "",
            "- MA20上升段本身是按正斜率筛出来的，因此这里检验的是速度是否逐段增强，不是检验斜率是否为正。",
            "- 实际最后一段不足5日时不做末段强加速判断，不能拿更早的最后一个合格段替代实际末段。",
            "- MA60右截断或末段延伸至样本末日时，末段速度只是截至2026-09-30的阶段观测，后续可能变化。",
            "- 分段之间存在回调期；逐段速度增加不等价于股价单调加速，也不直接构成交易信号。",
            "",
            "## 产物",
            "",
            "- `ma20_segment_speed_detail.csv`：每个MA20上升段一行，含多种斜率和段内加速度指标。",
            "- `ma60_stock_acceleration_summary.csv`：每只股票一行，含逐段趋势、末段比较和分层。",
            "- `terminal_acceleration_candidates.csv`：所有可比较末段的候选排序。",
            "- `threshold_sensitivity.csv`：10% / 25% / 50%阈值结果。",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    deck_metadata = json.loads(args.deck_metadata_file.read_text(encoding="utf-8"))
    if deck_metadata.get("ranking_price_basis") != "QFQ close (qfq=True)":
        raise ValueError("ranked pool is not QFQ")
    if not str(deck_metadata.get("daily_access", "")).endswith("qfq=True)"):
        raise ValueError("daily chart cache is not QFQ")
    if args.daily_file.name != str(deck_metadata.get("daily_input_file", "")):
        raise ValueError("daily input file does not match deck metadata")

    daily = pd.read_csv(
        args.daily_file,
        dtype={"ts_code": str, "trade_date": str},
        usecols=["ts_code", "trade_date", "ma20"],
    )
    source_segments = pd.read_csv(
        args.segment_file,
        dtype={"ts_code": str, "clipped_start_date": str, "clipped_end_date": str},
    )
    source_segments = source_segments.loc[
        source_segments["anchor_period"].eq(60)
        & source_segments["short_period"].eq(20)
    ].copy()
    anchor_summary = pd.read_csv(
        args.anchor_summary_file,
        dtype={"ts_code": str, "anchor_start_date": str, "anchor_end_date": str},
    )
    anchor_summary = anchor_summary.loc[
        anchor_summary["anchor_period"].eq(60)
        & anchor_summary["short_period"].eq(20)
    ].copy()
    classifications = pd.read_csv(args.classifications_file, dtype={"ts_code": str})

    codes = set(source_segments["ts_code"])
    expected_stocks = int(deck_metadata["ma60"]["selected_with_ties"])
    if len(codes) != expected_stocks:
        raise ValueError(f"expected {expected_stocks} MA60 stocks, got {len(codes)}")
    if len(anchor_summary) != expected_stocks or anchor_summary["ts_code"].duplicated().any():
        raise ValueError("MA60 anchor summary coverage mismatch")
    if not codes.issubset(set(classifications["ts_code"])):
        raise ValueError("classification coverage is incomplete")
    if not codes.issubset(set(daily["ts_code"])):
        raise ValueError("daily coverage is incomplete")

    daily_groups = {
        str(code): group.copy()
        for code, group in daily.loc[daily["ts_code"].isin(codes)].groupby(
            "ts_code", sort=False
        )
    }
    segment_records: list[dict[str, Any]] = []
    for _, segment in source_segments.sort_values(
        ["observation_rank", "segment_index"]
    ).iterrows():
        code = str(segment["ts_code"])
        metrics = compute_segment_speed_metrics(daily_groups[code], segment)
        record = segment.to_dict()
        record.update(metrics)
        record["eligible_ge5"] = bool(int(segment["days_in_anchor"]) >= 5)
        record["eligible_ge10"] = bool(int(segment["days_in_anchor"]) >= 10)
        segment_records.append(record)
    segment_speeds = pd.DataFrame(segment_records).sort_values(
        ["observation_rank", "segment_index"]
    ).reset_index(drop=True)
    if not segment_speeds["speed_observations"].astype(int).equals(
        segment_speeds["days_in_anchor"].astype(int)
    ):
        raise ValueError("segment speed observation counts do not match segment days")
    if not segment_speeds["mean_log_speed_bps_per_day"].gt(0).all():
        raise ValueError("non-positive MA20 speed found inside a rising segment")

    identities = classifications.loc[
        classifications["ts_code"].isin(codes),
        [
            "observation_rank", "ts_code", "stock_name", "primary_theme",
            "broad_category", "membership", "ma60_rank",
        ],
    ].drop_duplicates("ts_code").set_index("ts_code")
    anchors = anchor_summary.set_index("ts_code")
    stock_records: list[dict[str, Any]] = []
    for code, group in segment_speeds.groupby("ts_code", sort=False):
        metrics = summarize_stock_acceleration(
            group,
            min_segment_days=args.min_segment_days,
            intra_segment_min_days=args.intra_segment_min_days,
            acceleration_threshold=args.acceleration_threshold,
            min_absolute_increase_bps=args.min_absolute_increase_bps,
        )
        identity = identities.loc[code]
        anchor = anchors.loc[code]
        final_segment = group.sort_values("segment_index").iloc[-1]
        record = {
            "observation_rank": int(identity["observation_rank"]),
            "ts_code": code,
            "stock_name": str(identity["stock_name"]),
            "primary_theme": str(identity["primary_theme"]),
            "broad_category": str(identity["broad_category"]),
            "membership": str(identity["membership"]),
            "ma60_rank": int(float(identity["ma60_rank"])),
            "anchor_start_date": _compact_date(anchor["anchor_start_date"]),
            "anchor_end_date": _compact_date(anchor["anchor_end_date"]),
            "anchor_length": int(anchor["anchor_length"]),
            "anchor_target_days": int(anchor["anchor_target_days"]),
            "anchor_left_censored": bool(anchor["anchor_left_censored"]),
            "anchor_right_censored": bool(anchor["anchor_right_censored"]),
            "final_segment_start_date": _compact_date(final_segment["clipped_start_date"]),
            "final_segment_end_date": _compact_date(final_segment["clipped_end_date"]),
            "final_segment_episode_id": str(final_segment["episode_id"]),
            "final_segment_reaches_anchor_end": bool(
                _compact_date(final_segment["clipped_end_date"])
                == _compact_date(anchor["anchor_end_date"])
            ),
            "final_segment_episode_right_censored": bool(
                final_segment["episode_right_censored"]
            ),
            "final_segment_ended_after_anchor": bool(final_segment["ended_after_anchor"]),
            "final_segment_ma20_return_pct": float(
                final_segment["segment_ma20_return_pct"]
            ),
            **metrics,
        }
        stock_records.append(record)
    stocks = pd.DataFrame(stock_records).sort_values("observation_rank").reset_index(drop=True)
    stocks["acceleration_tier"] = stocks.apply(terminal_acceleration_tier, axis=1)
    if len(stocks) != expected_stocks or stocks["ts_code"].nunique() != expected_stocks:
        raise ValueError("stock acceleration summary coverage mismatch")

    aggregate = aggregate_acceleration_results(
        stocks,
        thresholds=args.sensitivity_thresholds,
        min_absolute_increase_bps=args.min_absolute_increase_bps,
    )
    trend_valid = stocks["speed_trend_valid"].fillna(False).astype(bool)
    rho = pd.to_numeric(stocks.loc[trend_valid, "speed_order_spearman"], errors="coerce")
    aggregate.update(
        {
            "positive_spearman_count": int(rho.gt(0).sum()),
            "spearman_ge_0_5_count": int(rho.ge(0.5).sum()),
            "spearman_median": float(rho.median()),
            "ma60_anchor_right_censored_count": int(
                stocks["anchor_right_censored"].sum()
            ),
            "actual_final_segment_below_min_days_count": int(
                (~stocks["actual_final_segment_eligible"]).sum()
            ),
        }
    )

    tier_order = {
        "A-跨段且段内加速": 0,
        "B-跨段加速": 1,
        "C-末段最快但未达强加速": 2,
        "D-未达到末段加速": 3,
        "N-不可判定": 4,
    }
    candidates = stocks.loc[stocks["terminal_comparison_valid"]].copy()
    candidates["_tier_order"] = candidates["acceleration_tier"].map(tier_order)
    candidates = candidates.sort_values(
        ["_tier_order", "final_vs_prior_median_pct", "final_vs_previous_pct"],
        ascending=[True, False, False],
    ).drop(columns="_tier_order").reset_index(drop=True)

    sensitivity_records = []
    for key, value in aggregate["thresholds"].items():
        sensitivity_records.append({"threshold": key, **value})
    sensitivity = pd.DataFrame(sensitivity_records)
    duration_sensitivity = summarize_min_day_sensitivity(
        segment_speeds,
        min_day_values=(1, 5, 10),
        intra_segment_min_days=args.intra_segment_min_days,
        acceleration_threshold=args.acceleration_threshold,
        min_absolute_increase_bps=args.min_absolute_increase_bps,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    segment_path = args.output_dir / "ma20_segment_speed_detail.csv"
    stock_path = args.output_dir / "ma60_stock_acceleration_summary.csv"
    candidate_path = args.output_dir / "terminal_acceleration_candidates.csv"
    sensitivity_path = args.output_dir / "threshold_sensitivity.csv"
    duration_sensitivity_path = args.output_dir / "segment_duration_sensitivity.csv"
    report_path = args.output_dir / "report.md"
    segment_speeds.to_csv(segment_path, index=False, encoding="utf-8-sig")
    stocks.to_csv(stock_path, index=False, encoding="utf-8-sig")
    candidates.to_csv(candidate_path, index=False, encoding="utf-8-sig")
    sensitivity.to_csv(sensitivity_path, index=False, encoding="utf-8-sig")
    duration_sensitivity.to_csv(
        duration_sensitivity_path, index=False, encoding="utf-8-sig"
    )
    _write_report(
        stocks,
        aggregate,
        candidates,
        report_path,
        data_end=str(deck_metadata["actual_target_end"]),
        min_segment_days=args.min_segment_days,
        intra_segment_min_days=args.intra_segment_min_days,
        acceleration_threshold=args.acceleration_threshold,
        min_absolute_increase_bps=args.min_absolute_increase_bps,
    )

    output_files = [
        segment_path,
        stock_path,
        candidate_path,
        sensitivity_path,
        duration_sensitivity_path,
        report_path,
    ]
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "price_basis": "QFQ close (qfq=True)",
        "daily_access": str(deck_metadata["daily_access"]),
        "actual_target_end": str(deck_metadata["actual_target_end"]),
        "sample": "107 MA60 Top-100-with-ties selected anchor episodes",
        "stocks": int(len(stocks)),
        "segments": int(len(segment_speeds)),
        "speed_definition": (
            "v_t = 10000 * ln(MA20_t / MA20_(t-1)) basis points per trading day; "
            "segment speed is the arithmetic mean of v_t over clipped rising days"
        ),
        "scale_invariance": "log percentage speed; invariant to multiplying all prices by a constant",
        "eligible_segment_min_days": int(args.min_segment_days),
        "intra_segment_min_days": int(args.intra_segment_min_days),
        "primary_relative_acceleration_threshold": float(args.acceleration_threshold),
        "primary_absolute_acceleration_threshold_bps_per_day": float(
            args.min_absolute_increase_bps
        ),
        "strictly_increasing_rule": (
            "at least three eligible segments and each later segment mean log speed is "
            "strictly greater than its predecessor"
        ),
        "terminal_acceleration_rule": (
            "actual final segment is eligible and exceeds both the previous eligible segment "
            "and the median of all prior eligible segments by the relative and absolute thresholds"
        ),
        "final_intra_acceleration_rule": (
            "actual final segment has at least the intra minimum days; second-half mean speed "
            "exceeds first-half mean speed by both thresholds and OLS daily-speed trend is positive"
        ),
        "aggregate": aggregate,
        "segment_duration_sensitivity": duration_sensitivity.to_dict(orient="records"),
        "source_sha256": {
            str(path): _sha256(path)
            for path in (
                args.daily_file,
                args.segment_file,
                args.anchor_summary_file,
                args.classifications_file,
                args.deck_metadata_file,
            )
        },
        "output_sha256": {path.name: _sha256(path) for path in output_files},
    }
    metadata_path = args.output_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
