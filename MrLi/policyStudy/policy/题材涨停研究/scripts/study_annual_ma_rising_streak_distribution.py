"""Annual distributions of consecutive rising moving-average days."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


DAILY_QFQ = False


def price_basis_metadata(qfq: bool) -> dict[str, str]:
    return {
        "data_access": (
            "coreClient.data_provider.get_day(source='database_only', "
            f"qfq={bool(qfq)})"
        ),
        "price_basis": "QFQ close (qfq=True)" if qfq else "Unadjusted close (qfq=False)",
    }


DURATION_BUCKET_EDGES = [0, 2, 5, 10, 20, 40, 60, 90, 120, 180, np.inf]
DURATION_BUCKET_LABELS = [
    "1-2", "3-5", "6-10", "11-20", "21-40",
    "41-60", "61-90", "91-120", "121-180", "181+",
]


def add_ma_streak_columns(
    daily: pd.DataFrame,
    periods: Iterable[int] = (20, 30, 60, 120),
    *,
    epsilon: float = 1e-10,
) -> pd.DataFrame:
    """Add moving averages and consecutive positive-slope run lengths per stock."""
    required = {"ts_code", "trade_date", "close"}
    missing = required.difference(daily.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")

    out = daily.copy()
    out["trade_date"] = out["trade_date"].astype(str)
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    grouped_close = out.groupby("ts_code", sort=False)["close"]

    for period in periods:
        if period <= 0:
            raise ValueError("moving-average periods must be positive")
        ma_col = f"ma{period}"
        rising_col = f"ma{period}_rising"
        streak_col = f"ma{period}_streak"
        out[ma_col] = grouped_close.transform(
            lambda values: values.rolling(period, min_periods=period).mean()
        )
        out[rising_col] = out.groupby("ts_code", sort=False)[ma_col].diff().gt(epsilon)
        out[streak_col] = out.groupby("ts_code", sort=False)[rising_col].transform(
            lambda rising: rising.groupby((~rising).cumsum()).cumsum()
        ).astype("int32")

    return out


def _compact_date(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y%m%d")


def extract_episodes(
    featured: pd.DataFrame,
    *,
    period: int,
    target_start: str,
    target_end: str,
) -> pd.DataFrame:
    """Return one row per rising-MA run that intersects the target window."""
    rising_col = f"ma{period}_rising"
    ma_col = f"ma{period}"
    if rising_col not in featured or ma_col not in featured:
        raise ValueError(f"missing MA{period} feature columns")
    start = _compact_date(target_start)
    end = _compact_date(target_end)
    if start > end:
        raise ValueError("target_start must not be after target_end")

    work = featured[["ts_code", "trade_date", ma_col, rising_col]].copy()
    work["trade_date"] = work["trade_date"].astype(str)
    work["_rising"] = work[rising_col].fillna(False).astype(bool)
    prior_valid = work.groupby("ts_code", sort=False)[ma_col].shift().notna()
    work["_comparable"] = work[ma_col].notna() & prior_valid
    work["_run_id"] = (~work["_rising"]).groupby(work["ts_code"], sort=False).cumsum()
    work["_in_target"] = work["trade_date"].between(start, end)
    rising = work[work["_rising"]]
    columns = [
        "ts_code", "period", "episode_id", "start_date", "end_date",
        "length", "target_days", "left_censored", "carried_in", "right_censored",
    ]
    if rising.empty:
        return pd.DataFrame(columns=columns)

    episodes = (
        rising.groupby(["ts_code", "_run_id"], sort=False, observed=True)
        .agg(
            start_date=("trade_date", "min"),
            end_date=("trade_date", "max"),
            length=("trade_date", "size"),
            target_days=("_in_target", "sum"),
        )
        .reset_index()
    )
    episodes = episodes[episodes["target_days"].gt(0)].copy()
    if episodes.empty:
        return pd.DataFrame(columns=columns)

    last_dates = work.groupby("ts_code", sort=False)["trade_date"].max()
    first_comparable_dates = (
        work[work["_comparable"]]
        .groupby("ts_code", sort=False)["trade_date"]
        .min()
    )
    episodes["period"] = period
    episodes["episode_id"] = (
        episodes["ts_code"] + f"_ma{period}_" + episodes["_run_id"].astype(str)
    )
    episodes["left_censored"] = episodes["start_date"].eq(
        episodes["ts_code"].map(first_comparable_dates)
    )
    episodes["carried_in"] = episodes["start_date"].lt(start)
    episodes["right_censored"] = episodes["end_date"].eq(
        episodes["ts_code"].map(last_dates)
    )
    episodes["target_days"] = episodes["target_days"].astype("int32")
    episodes["length"] = episodes["length"].astype("int32")
    return episodes[columns].reset_index(drop=True)


def summarize_period(
    featured: pd.DataFrame,
    episodes: pd.DataFrame,
    *,
    period: int,
    target_start: str,
    target_end: str,
) -> dict[str, int | float | str]:
    """Summarize stock-day states and run durations for one MA period."""
    start = _compact_date(target_start)
    end = _compact_date(target_end)
    ma_col = f"ma{period}"
    rising_col = f"ma{period}_rising"
    if ma_col not in featured or rising_col not in featured:
        raise ValueError(f"missing MA{period} feature columns")

    target = featured[featured["trade_date"].astype(str).between(start, end)]
    valid = target[ma_col].notna()
    rising = target[rising_col].fillna(False) & valid
    valid_count = int(valid.sum())
    rising_count = int(rising.sum())
    lengths = episodes["target_days"].astype(float) if len(episodes) else pd.Series(dtype=float)
    full_lengths = episodes["length"].astype(float) if len(episodes) else pd.Series(dtype=float)

    def stat(values: pd.Series, operation: str, quantile: float | None = None) -> float:
        if values.empty:
            return 0.0
        if operation == "mean":
            return float(values.mean())
        if operation == "max":
            return float(values.max())
        if operation == "quantile" and quantile is not None:
            return float(values.quantile(quantile))
        raise ValueError(f"unknown operation: {operation}")

    return {
        "period": period,
        "target_start": start,
        "target_end": end,
        "stocks_with_rows": int(target["ts_code"].nunique()),
        "valid_stocks": int(target.loc[valid, "ts_code"].nunique()),
        "valid_stock_days": valid_count,
        "rising_stock_days": rising_count,
        "rising_share_pct": round(100.0 * rising_count / valid_count, 6) if valid_count else 0.0,
        "episodes": int(len(episodes)),
        "completed_episodes": int((~episodes["right_censored"]).sum()) if len(episodes) else 0,
        "right_censored_episodes": int(episodes["right_censored"].sum()) if len(episodes) else 0,
        "left_censored_episodes": int(episodes["left_censored"].sum()) if len(episodes) else 0,
        "carried_in_episodes": int(episodes["carried_in"].sum()) if len(episodes) else 0,
        "window_days_mean": round(stat(lengths, "mean"), 6),
        "window_days_median": stat(lengths, "quantile", 0.5),
        "window_days_p75": stat(lengths, "quantile", 0.75),
        "window_days_p90": stat(lengths, "quantile", 0.9),
        "window_days_p95": stat(lengths, "quantile", 0.95),
        "window_days_max": int(stat(lengths, "max")),
        "full_length_mean": round(stat(full_lengths, "mean"), 6),
        "full_length_median": stat(full_lengths, "quantile", 0.5),
        "full_length_p90": stat(full_lengths, "quantile", 0.9),
        "full_length_max": int(stat(full_lengths, "max")),
    }


def distribution_tables(
    values: pd.Series,
    *,
    period: int,
    population: str,
    metric: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build exact and fixed-bucket duration distributions."""
    clean = pd.to_numeric(values, errors="coerce").dropna().astype(int)
    clean = clean[clean.gt(0)]
    total = int(len(clean))

    exact_columns = [
        "period", "population", "metric", "duration", "count", "pct",
        "cumulative_pct", "survival_pct",
    ]
    if total:
        counts = clean.value_counts().sort_index()
        exact = counts.rename_axis("duration").reset_index(name="count")
        exact["pct"] = 100.0 * exact["count"] / total
        exact["cumulative_pct"] = 100.0 * exact["count"].cumsum() / total
        exact["survival_pct"] = 100.0 * exact["count"][::-1].cumsum()[::-1] / total
        exact.insert(0, "metric", metric)
        exact.insert(0, "population", population)
        exact.insert(0, "period", period)
        exact = exact[exact_columns]
    else:
        exact = pd.DataFrame(columns=exact_columns)

    categories = pd.cut(
        clean,
        bins=DURATION_BUCKET_EDGES,
        labels=DURATION_BUCKET_LABELS,
        right=True,
        include_lowest=True,
    )
    bucket_counts = categories.value_counts(sort=False).reindex(DURATION_BUCKET_LABELS, fill_value=0)
    buckets = bucket_counts.rename_axis("bucket").reset_index(name="count")
    buckets["bucket_order"] = range(1, len(buckets) + 1)
    buckets["pct"] = 100.0 * buckets["count"] / total if total else 0.0
    buckets.insert(0, "metric", metric)
    buckets.insert(0, "population", population)
    buckets.insert(0, "period", period)
    buckets = buckets[[
        "period", "population", "metric", "bucket_order", "bucket", "count", "pct",
    ]]
    return exact, buckets


def analyze_frame(
    daily: pd.DataFrame,
    *,
    periods: Iterable[int] = (20, 30, 60, 120),
    target_start: str,
    target_end: str,
) -> dict[str, pd.DataFrame]:
    """Compute all episode and stock-day distributions for an input daily frame."""
    periods = tuple(periods)
    featured = add_ma_streak_columns(daily, periods=periods)
    start = _compact_date(target_start)
    end = _compact_date(target_end)
    target = featured[featured["trade_date"].astype(str).between(start, end)]

    summaries: list[dict] = []
    episode_frames: list[pd.DataFrame] = []
    exact_frames: list[pd.DataFrame] = []
    bucket_frames: list[pd.DataFrame] = []

    for period in periods:
        episodes = extract_episodes(
            featured,
            period=period,
            target_start=start,
            target_end=end,
        )
        summaries.append(
            summarize_period(
                featured,
                episodes,
                period=period,
                target_start=start,
                target_end=end,
            )
        )
        episode_frames.append(episodes)

        populations = [
            ("episodes_all", "window_days", episodes["target_days"]),
            (
                "episodes_completed",
                "window_days",
                episodes.loc[~episodes["right_censored"], "target_days"],
            ),
            (
                "episodes_uncensored",
                "full_length",
                episodes.loc[
                    ~episodes["right_censored"] & ~episodes["left_censored"],
                    "length",
                ],
            ),
            (
                "stock_days",
                "current_streak",
                target.loc[target[f"ma{period}_streak"].gt(0), f"ma{period}_streak"],
            ),
        ]
        for population, metric, values in populations:
            exact, buckets = distribution_tables(
                values,
                period=period,
                population=population,
                metric=metric,
            )
            exact_frames.append(exact)
            bucket_frames.append(buckets)

    nonempty_exact = [frame for frame in exact_frames if not frame.empty]
    exact_distribution = (
        pd.concat(nonempty_exact, ignore_index=True)
        if nonempty_exact
        else exact_frames[0].iloc[0:0].copy()
    )
    nonempty_episodes = [frame for frame in episode_frames if not frame.empty]
    episodes_output = (
        pd.concat(nonempty_episodes, ignore_index=True)
        if nonempty_episodes
        else episode_frames[0].iloc[0:0].copy()
    )
    return {
        "featured": featured,
        "summary": pd.DataFrame(summaries),
        "episodes": episodes_output,
        "exact_distribution": exact_distribution,
        "bucket_distribution": pd.concat(bucket_frames, ignore_index=True),
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--history-start", default="20230101")
    parser.add_argument("--periods", nargs="+", type=int, default=[20, 30, 60, 120])
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--qfq",
        action="store_true",
        help="use forward-adjusted daily prices instead of unadjusted prices",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()

    root = Path(__file__).resolve().parents[5]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from coreClient.data_provider import get_day, get_tradecal

    requested_start = f"{args.year}0101"
    requested_end = f"{args.year}1231"
    daily_qfq = bool(args.qfq)
    loaded = get_day(
        start_date=args.history_start,
        end_date=requested_end,
        qfq=daily_qfq,
        source="database_only",
    )
    if loaded.empty:
        raise SystemExit("local daily database returned no rows")
    daily = loaded[["ts_code", "trade_date", "close"]].copy()
    del loaded
    daily["trade_date"] = daily["trade_date"].astype(str)
    duplicate_rows = int(daily.duplicated(["ts_code", "trade_date"]).sum())
    if duplicate_rows:
        raise SystemExit(f"duplicate ts_code/trade_date rows: {duplicate_rows}")

    target_mask = daily["trade_date"].between(requested_start, requested_end)
    target_rows = daily[target_mask]
    if target_rows.empty:
        raise SystemExit(f"no daily rows for requested year {args.year}")
    actual_start = str(target_rows["trade_date"].min())
    actual_end = str(target_rows["trade_date"].max())

    result = analyze_frame(
        daily,
        periods=args.periods,
        target_start=requested_start,
        target_end=actual_end,
    )
    output = args.output or (
        root / "outputs" / "ma_rising_streak_distribution"
        / f"{args.year}_through_{actual_end}_{'qfq' if daily_qfq else 'unadjusted'}"
    )
    output.mkdir(parents=True, exist_ok=True)
    result["summary"].to_csv(output / "summary.csv", index=False, float_format="%.6f")
    result["episodes"].to_csv(output / "episodes.csv", index=False)
    result["exact_distribution"].to_csv(
        output / "exact_distribution.csv", index=False, float_format="%.8f"
    )
    result["bucket_distribution"].to_csv(
        output / "bucket_distribution.csv", index=False, float_format="%.8f"
    )

    calendar = get_tradecal(
        start_date=requested_start,
        end_date=requested_end,
        source="database_only",
    )
    calendar_dates = (
        set(calendar.loc[calendar["is_open"].eq(1), "cal_date"].astype(str))
        if len(calendar) else set()
    )
    observed_dates = set(target_rows["trade_date"].astype(str))
    suffix_counts = (
        target_rows["ts_code"].str.rsplit(".", n=1).str[-1].value_counts().sort_index().to_dict()
    )
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        **price_basis_metadata(daily_qfq),
        "requested_year": args.year,
        "requested_target_start": requested_start,
        "requested_target_end": requested_end,
        "actual_target_start": actual_start,
        "actual_target_end": actual_end,
        "history_start": _compact_date(args.history_start),
        "history_end": str(daily["trade_date"].max()),
        "periods": list(args.periods),
        "history_rows": int(len(daily)),
        "target_rows": int(len(target_rows)),
        "target_codes": int(target_rows["ts_code"].nunique()),
        "target_dates": int(target_rows["trade_date"].nunique()),
        "calendar_open_dates": int(len(calendar_dates)),
        "calendar_dates_missing_from_daily_table": sorted(calendar_dates - observed_dates),
        "daily_dates_missing_from_calendar": sorted(observed_dates - calendar_dates),
        "duplicate_rows": duplicate_rows,
        "missing_close_rows": int(daily["close"].isna().sum()),
        "target_code_suffix_counts": {str(k): int(v) for k, v in suffix_counts.items()},
        "rising_definition": "MA_t - MA_(t-1) > 1e-10 on consecutive stock trading observations",
        "primary_duration": (
            "target_days: days from each contiguous rising episode that fall inside the requested year; "
            "carry-in days before Jan 1 are excluded and open runs at the database cutoff are right-censored"
        ),
        "full_length_duration": (
            "full observed run length from history_start; use left_censored/right_censored flags"
        ),
        "bucket_labels": DURATION_BUCKET_LABELS,
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(output),
        "actual_target_start": actual_start,
        "actual_target_end": actual_end,
        "target_rows": int(len(target_rows)),
        "target_codes": int(target_rows["ts_code"].nunique()),
        "target_dates": int(target_rows["trade_date"].nunique()),
        "summary": result["summary"].to_dict(orient="records"),
    }, ensure_ascii=False, indent=2))
