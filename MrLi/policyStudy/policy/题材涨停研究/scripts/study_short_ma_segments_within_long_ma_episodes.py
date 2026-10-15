"""Study MA20/MA30 rising segments inside selected MA60/MA120 rising episodes."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_annual_ma_rising_streak_distribution import (
    add_ma_streak_columns,
    extract_episodes,
    price_basis_metadata,
)


ANCHOR_PERIODS = (60, 120)
SHORT_PERIODS = (20, 30)
SHARE_EPSILON = 1e-12


def validate_pool_price_basis(metadata: dict[str, Any], qfq: bool) -> None:
    expected = f"qfq={bool(qfq)}"
    observed = str(metadata.get("ranking_price_basis", ""))
    if expected not in observed:
        raise ValueError(
            f"pool/daily price-basis mismatch: ranking={observed!r}, daily={expected}"
        )


def compact_date(value: Any) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"invalid compact date: {value!r}")
    return text


def _as_bool(value: Any) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def prepare_anchor_episodes(
    union: pd.DataFrame,
    identities: pd.DataFrame,
) -> pd.DataFrame:
    """Materialize one anchor row per selected stock and long-MA period."""
    required_union = {"observation_rank", "ts_code"}
    if not required_union.issubset(union.columns):
        raise ValueError(f"union missing columns: {sorted(required_union - set(union.columns))}")
    required_identity = {"ts_code", "stock_name", "primary_theme", "broad_category"}
    if not required_identity.issubset(identities.columns):
        raise ValueError(
            f"identities missing columns: {sorted(required_identity - set(identities.columns))}"
        )
    if union["ts_code"].astype(str).duplicated().any():
        raise ValueError("union contains duplicate ts_code rows")
    if identities["ts_code"].astype(str).duplicated().any():
        raise ValueError("identities contain duplicate ts_code rows")

    identity_by_code = identities.assign(ts_code=identities["ts_code"].astype(str)).set_index(
        "ts_code"
    )
    missing = sorted(set(union["ts_code"].astype(str)) - set(identity_by_code.index))
    if missing:
        raise ValueError(f"identity coverage mismatch: missing={missing}")

    records: list[dict[str, Any]] = []
    for _, row in union.sort_values("observation_rank").iterrows():
        code = str(row["ts_code"])
        identity = identity_by_code.loc[code]
        for period in ANCHOR_PERIODS:
            if not _as_bool(row.get(f"in_ma{period}_top100", False)):
                continue
            records.append(
                {
                    "observation_rank": int(row["observation_rank"]),
                    "ts_code": code,
                    "stock_name": str(identity["stock_name"]),
                    "primary_theme": str(identity["primary_theme"]),
                    "broad_category": str(identity["broad_category"]),
                    "anchor_period": period,
                    "anchor_rank": int(row[f"ma{period}_rank"]),
                    "anchor_start_date": compact_date(row[f"ma{period}_start_date"]),
                    "anchor_end_date": compact_date(row[f"ma{period}_end_date"]),
                    "anchor_length": int(row[f"ma{period}_length"]),
                    "anchor_target_days": int(row[f"ma{period}_target_days"]),
                    "anchor_left_censored": _as_bool(row[f"ma{period}_left_censored"]),
                    "anchor_carried_in": _as_bool(row[f"ma{period}_carried_in"]),
                    "anchor_right_censored": _as_bool(row[f"ma{period}_right_censored"]),
                }
            )
    if not records:
        raise ValueError("no selected MA60/MA120 anchor episodes")
    return pd.DataFrame(records).sort_values(
        ["observation_rank", "anchor_period"]
    ).reset_index(drop=True)


def _close_at(indexed: pd.DataFrame, date: str) -> float:
    if date not in indexed.index:
        raise ValueError(f"daily close missing for anchor/segment date {date}")
    value = indexed.loc[date, "close"]
    if isinstance(value, pd.Series):
        raise ValueError(f"duplicate daily rows for date {date}")
    value = float(value)
    if not np.isfinite(value):
        raise ValueError(f"non-finite close for date {date}")
    return value


def _return_pct(start_close: float, end_close: float) -> float:
    if abs(start_close) <= SHARE_EPSILON:
        return float("nan")
    return 100.0 * (end_close / start_close - 1.0)


def analyze_anchor_short_period(
    anchor: pd.Series,
    stock_daily: pd.DataFrame,
    short_episodes: pd.DataFrame,
    short_period: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Count intersecting short-MA episodes and quantify the first episode's gain share."""
    if short_period not in SHORT_PERIODS:
        raise ValueError(f"unsupported short period: {short_period}")
    code = str(anchor["ts_code"])
    start = compact_date(anchor["anchor_start_date"])
    end = compact_date(anchor["anchor_end_date"])
    if start > end:
        raise ValueError(f"anchor start after end for {code}")

    daily = stock_daily.copy()
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily["trade_date"] = daily["trade_date"].map(compact_date)
    daily["close"] = pd.to_numeric(daily["close"], errors="coerce")
    daily = daily.loc[daily["ts_code"].eq(code)].sort_values("trade_date")
    if daily.duplicated("trade_date").any():
        raise ValueError(f"duplicate daily dates for {code}")
    anchor_daily = daily.loc[daily["trade_date"].between(start, end)].copy()
    if anchor_daily.empty:
        raise ValueError(f"no daily rows inside anchor for {code}")
    indexed = daily.set_index("trade_date")
    anchor_start_close = _close_at(indexed, start)
    anchor_end_close = _close_at(indexed, end)
    anchor_price_change = anchor_end_close - anchor_start_close
    anchor_return_pct = _return_pct(anchor_start_close, anchor_end_close)
    anchor_total_gain_positive = bool(anchor_price_change > SHARE_EPSILON)
    anchor_dates = anchor_daily["trade_date"].tolist()
    date_positions = {date: index for index, date in enumerate(anchor_dates)}

    episodes = short_episodes.copy()
    if not episodes.empty:
        episodes["ts_code"] = episodes["ts_code"].astype(str)
        episodes["start_date"] = episodes["start_date"].map(compact_date)
        episodes["end_date"] = episodes["end_date"].map(compact_date)
        episodes = episodes.loc[
            episodes["ts_code"].eq(code)
            & episodes["period"].eq(short_period)
            & episodes["start_date"].le(end)
            & episodes["end_date"].ge(start)
        ].sort_values(["start_date", "end_date"])

    details: list[dict[str, Any]] = []
    for segment_index, (_, episode) in enumerate(episodes.iterrows(), start=1):
        episode_start = compact_date(episode["start_date"])
        episode_end = compact_date(episode["end_date"])
        clipped_start = max(start, episode_start)
        clipped_end = min(end, episode_end)
        overlap = anchor_daily.loc[
            anchor_daily["trade_date"].between(clipped_start, clipped_end)
        ]
        if overlap.empty:
            continue
        clipped_start = str(overlap.iloc[0]["trade_date"])
        clipped_end = str(overlap.iloc[-1]["trade_date"])
        segment_start_close = _close_at(indexed, clipped_start)
        segment_end_close = _close_at(indexed, clipped_end)
        details.append(
            {
                "observation_rank": int(anchor["observation_rank"]),
                "ts_code": code,
                "stock_name": str(anchor["stock_name"]),
                "primary_theme": str(anchor["primary_theme"]),
                "broad_category": str(anchor["broad_category"]),
                "anchor_period": int(anchor["anchor_period"]),
                "anchor_rank": int(anchor["anchor_rank"]),
                "anchor_start_date": start,
                "anchor_end_date": end,
                "short_period": short_period,
                "segment_index": segment_index,
                "episode_id": str(episode["episode_id"]),
                "episode_start_date": episode_start,
                "episode_end_date": episode_end,
                "episode_full_length": int(episode["length"]),
                "episode_left_censored": _as_bool(episode.get("left_censored", False)),
                "episode_right_censored": _as_bool(episode.get("right_censored", False)),
                "clipped_start_date": clipped_start,
                "clipped_end_date": clipped_end,
                "days_in_anchor": int(len(overlap)),
                "started_before_anchor": bool(episode_start < start),
                "ended_after_anchor": bool(episode_end > end),
                "segment_start_close": segment_start_close,
                "segment_end_close": segment_end_close,
                "segment_price_change": segment_end_close - segment_start_close,
                "segment_return_pct": _return_pct(segment_start_close, segment_end_close),
            }
        )

    details.sort(key=lambda item: (item["clipped_start_date"], item["clipped_end_date"]))
    for index, detail in enumerate(details, start=1):
        detail["segment_index"] = index

    first = details[0] if details else None
    total_rising_days = int(sum(item["days_in_anchor"] for item in details))
    summary: dict[str, Any] = {
        "observation_rank": int(anchor["observation_rank"]),
        "ts_code": code,
        "stock_name": str(anchor["stock_name"]),
        "primary_theme": str(anchor["primary_theme"]),
        "broad_category": str(anchor["broad_category"]),
        "anchor_period": int(anchor["anchor_period"]),
        "anchor_rank": int(anchor["anchor_rank"]),
        "anchor_start_date": start,
        "anchor_end_date": end,
        "anchor_length": int(anchor["anchor_length"]),
        "anchor_observed_days": int(len(anchor_daily)),
        "anchor_length_matches_daily": bool(int(anchor["anchor_length"]) == len(anchor_daily)),
        "anchor_target_days": int(anchor["anchor_target_days"]),
        "anchor_left_censored": _as_bool(anchor["anchor_left_censored"]),
        "anchor_carried_in": _as_bool(anchor["anchor_carried_in"]),
        "anchor_right_censored": _as_bool(anchor["anchor_right_censored"]),
        "anchor_start_close": anchor_start_close,
        "anchor_end_close": anchor_end_close,
        "anchor_price_change": anchor_price_change,
        "anchor_total_return_pct": anchor_return_pct,
        "anchor_total_gain_positive": anchor_total_gain_positive,
        "short_period": short_period,
        "segment_count": int(len(details)),
        "segment_count_ge5": int(sum(item["days_in_anchor"] >= 5 for item in details)),
        "segment_count_ge10": int(sum(item["days_in_anchor"] >= 10 for item in details)),
        "short_rising_days": total_rising_days,
        "short_rising_coverage_pct": 100.0 * total_rising_days / len(anchor_daily),
        "first_segment_episode_id": first["episode_id"] if first else "",
        "first_segment_start_date": first["clipped_start_date"] if first else "",
        "first_segment_end_date": first["clipped_end_date"] if first else "",
        "first_segment_days": int(first["days_in_anchor"]) if first else np.nan,
        "first_segment_carried_in": bool(first["started_before_anchor"]) if first else False,
        "first_segment_right_clipped": bool(first["ended_after_anchor"]) if first else False,
        "first_segment_start_offset_days": (
            int(date_positions[first["clipped_start_date"]]) if first else np.nan
        ),
        "first_segment_end_offset_days": (
            int(date_positions[first["clipped_end_date"]]) if first else np.nan
        ),
        "first_segment_start_close": first["segment_start_close"] if first else np.nan,
        "first_segment_end_close": first["segment_end_close"] if first else np.nan,
        "first_segment_price_change": first["segment_price_change"] if first else np.nan,
        "first_segment_return_pct": first["segment_return_pct"] if first else np.nan,
        "first_segment_gain_share_pct": np.nan,
        "first_end_cumulative_return_pct": np.nan,
        "first_end_cumulative_gain_share_pct": np.nan,
        "gain_share_valid": bool(
            first is not None
            and anchor_total_gain_positive
            and not _as_bool(anchor["anchor_left_censored"])
        ),
    }
    if first:
        first_end_close = float(first["segment_end_close"])
        summary["first_end_cumulative_return_pct"] = _return_pct(
            anchor_start_close, first_end_close
        )
        if anchor_total_gain_positive:
            summary["first_segment_gain_share_pct"] = (
                100.0 * float(first["segment_price_change"]) / anchor_price_change
            )
            summary["first_end_cumulative_gain_share_pct"] = (
                100.0 * (first_end_close - anchor_start_close) / anchor_price_change
            )
    return summary, details


def _quantile(series: pd.Series, q: float) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.quantile(q)) if len(values) else float("nan")


def aggregate_anchor_results(summary: pd.DataFrame) -> pd.DataFrame:
    """Summarize segment counts and first-segment gain shares by period pair."""
    if summary.empty:
        return pd.DataFrame()
    records: list[dict[str, Any]] = []
    for (anchor_period, short_period), group in summary.groupby(
        ["anchor_period", "short_period"], sort=True
    ):
        valid_mask = (
            group["gain_share_valid"]
            if "gain_share_valid" in group.columns
            else group["anchor_total_gain_positive"]
        ).fillna(False).astype(bool)
        valid_mask &= ~group["anchor_left_censored"].fillna(False).astype(bool)
        valid_share = group.loc[valid_mask]
        records.append(
            {
                "anchor_period": int(anchor_period),
                "short_period": int(short_period),
                "anchors": int(len(group)),
                "anchors_with_segments": int(group["segment_count"].gt(0).sum()),
                "positive_total_gain_anchors": int(group["anchor_total_gain_positive"].sum()),
                "gain_share_sample": int(len(valid_share)),
                "left_censored_anchors": int(group["anchor_left_censored"].sum()),
                "right_censored_anchors": int(group["anchor_right_censored"].sum()),
                "segment_count_mean": float(group["segment_count"].mean()),
                "segment_count_median": _quantile(group["segment_count"], 0.5),
                "segment_count_p25": _quantile(group["segment_count"], 0.25),
                "segment_count_p75": _quantile(group["segment_count"], 0.75),
                "segment_count_p90": _quantile(group["segment_count"], 0.9),
                "segment_count_max": int(group["segment_count"].max()),
                "segment_count_ge5_mean": float(group["segment_count_ge5"].mean()),
                "segment_count_ge5_median": _quantile(group["segment_count_ge5"], 0.5),
                "segment_count_ge10_mean": float(group["segment_count_ge10"].mean()),
                "segment_count_ge10_median": _quantile(group["segment_count_ge10"], 0.5),
                "short_rising_coverage_median_pct": _quantile(
                    group["short_rising_coverage_pct"], 0.5
                ),
                "anchor_total_return_median_pct": _quantile(
                    group["anchor_total_return_pct"], 0.5
                ),
                "first_segment_days_median": _quantile(group["first_segment_days"], 0.5),
                "first_segment_start_offset_median_days": _quantile(
                    group["first_segment_start_offset_days"], 0.5
                ),
                "first_segment_return_median_pct": _quantile(
                    group["first_segment_return_pct"], 0.5
                ),
                "first_segment_gain_share_median_pct": _quantile(
                    valid_share["first_segment_gain_share_pct"], 0.5
                ),
                "first_segment_gain_share_p25_pct": _quantile(
                    valid_share["first_segment_gain_share_pct"], 0.25
                ),
                "first_segment_gain_share_p75_pct": _quantile(
                    valid_share["first_segment_gain_share_pct"], 0.75
                ),
                "first_end_cumulative_gain_share_median_pct": _quantile(
                    valid_share["first_end_cumulative_gain_share_pct"], 0.5
                ),
                "first_end_cumulative_gain_share_p25_pct": _quantile(
                    valid_share["first_end_cumulative_gain_share_pct"], 0.25
                ),
                "first_end_cumulative_gain_share_p75_pct": _quantile(
                    valid_share["first_end_cumulative_gain_share_pct"], 0.75
                ),
            }
        )
    return pd.DataFrame(records).sort_values(
        ["anchor_period", "short_period"]
    ).reset_index(drop=True)


def build_segment_count_distribution(summary: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for (anchor_period, short_period), group in summary.groupby(
        ["anchor_period", "short_period"], sort=True
    ):
        counts = group["segment_count"].value_counts().sort_index()
        total = int(len(group))
        for segment_count, count in counts.items():
            records.append(
                {
                    "anchor_period": int(anchor_period),
                    "short_period": int(short_period),
                    "segment_count": int(segment_count),
                    "stock_anchor_count": int(count),
                    "pct": 100.0 * int(count) / total,
                }
            )
    return pd.DataFrame(records)


def build_per_stock_wide(
    identities: pd.DataFrame,
    summary: pd.DataFrame,
) -> pd.DataFrame:
    base_columns = ["observation_rank", "ts_code", "stock_name"]
    base_columns.extend(
        column
        for column in ("membership", "in_ma60_top100", "in_ma120_top100")
        if column in identities.columns
    )
    base_columns.extend(["primary_theme", "broad_category"])
    base = identities[base_columns].drop_duplicates("ts_code").sort_values("observation_rank")
    metrics = [
        "anchor_rank", "anchor_start_date", "anchor_end_date", "anchor_length",
        "anchor_target_days", "anchor_left_censored", "anchor_right_censored",
        "anchor_total_return_pct", "anchor_total_gain_positive", "segment_count",
        "segment_count_ge5", "segment_count_ge10", "short_rising_coverage_pct",
        "first_segment_start_date", "first_segment_end_date", "first_segment_days",
        "first_segment_carried_in", "first_segment_start_offset_days",
        "first_segment_return_pct", "first_segment_gain_share_pct",
        "first_end_cumulative_return_pct", "first_end_cumulative_gain_share_pct",
        "gain_share_valid",
    ]
    records: list[dict[str, Any]] = []
    grouped = {
        str(code): group
        for code, group in summary.groupby("ts_code", sort=False)
    }
    for _, identity in base.iterrows():
        code = str(identity["ts_code"])
        record = {column: identity[column] for column in base_columns}
        for _, row in grouped.get(code, pd.DataFrame()).iterrows():
            prefix = f"ma{int(row['anchor_period'])}_ma{int(row['short_period'])}"
            for metric in metrics:
                record[f"{prefix}_{metric}"] = row[metric]
        records.append(record)
    return pd.DataFrame(records)


def _write_report(
    aggregate: pd.DataFrame,
    metadata: dict[str, Any],
    output: Path,
) -> None:
    lines = [
        "# 长周期均线上升段内 MA20 / MA30 分段研究",
        "",
        f"- 数据截止：`{metadata['actual_target_end']}`，价格口径：{metadata['price_basis_zh']}（`qfq={metadata['qfq']}`）。",
        f"- 观察池：{metadata['pool_stocks']}只；MA60锚定段 {metadata['anchor_counts']['60']} 个，MA120锚定段 {metadata['anchor_counts']['120']} 个。",
        "- 连续上升定义：`MA_t > MA_(t-1)`；持平或下降立即断段。这里的“段”是严格最大连续段，不主观合并短暂停顿。",
        "- 统计整个已观测锚定段，而不只统计2026年内部分；左/右截断标志保留。",
        "- 首段自身贡献 = 首个MA20/MA30上升段的净价格变化 ÷ 长周期锚定段净价格变化。",
        "- 到首段结束累计完成度 = 从长周期锚定段起点到首个短均线上升段终点的净价格变化 ÷ 长周期锚定段净价格变化。",
        "- 当长周期锚定段首尾净价格变化不为正时，上述两个占比留空；左截断锚定段也不进入贡献占比汇总，避免把未观测起点当成真实起点。",
        "",
        "## 汇总",
        "",
        "| 锚定均线 | 短均线 | 样本 | 段数中位数 | ≥5日段中位数 | 上升覆盖中位数 | 首段自身贡献中位数 | 到首段结束累计完成度中位数 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in aggregate.iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | MA{int(row['short_period'])} | {int(row['anchors'])} | "
            f"{row['segment_count_median']:.1f} | {row['segment_count_ge5_median']:.1f} | "
            f"{row['short_rising_coverage_median_pct']:.1f}% | "
            f"{row['first_segment_gain_share_median_pct']:.1f}% | "
            f"{row['first_end_cumulative_gain_share_median_pct']:.1f}% |"
        )
    lines.extend(
        [
            "",
            "## 产物说明",
            "",
            "- `per_stock_summary.csv`：每只股票一行，便于直接筛选和排序。",
            "- `anchor_short_ma_summary.csv`：每个股票×锚定均线×短均线一行，是主分析表。",
            "- `short_ma_segments_detail.csv`：每个相交短均线上升段一行，保留裁切边界和段内涨幅。",
            "- `aggregate_summary.csv` 与 `segment_count_distribution.csv`：总体分布。",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pool-dir",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_themes/2026_through_20260930_unadjusted"
        ),
    )
    parser.add_argument(
        "--classification-file",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_ppt/2026_through_20260930_unadjusted/"
            "classifications.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "outputs/ma_short_segments_within_long_episodes/"
            "2026_through_20260930_unadjusted"
        ),
    )
    parser.add_argument("--history-start", default="20220101")
    parser.add_argument(
        "--qfq",
        action="store_true",
        help="use forward-adjusted daily prices matching the selected pool",
    )
    return parser.parse_args(argv)


def main() -> None:
    root = Path(__file__).resolve().parents[5]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from coreClient.data_provider import get_day

    args = _parse_args()
    daily_qfq = bool(args.qfq)
    pool_metadata = json.loads((args.pool_dir / "metadata.json").read_text(encoding="utf-8"))
    validate_pool_price_basis(pool_metadata, daily_qfq)
    union = pd.read_csv(args.pool_dir / "observation_union.csv", dtype={"ts_code": str})
    classifications = pd.read_csv(args.classification_file, dtype={"ts_code": str})
    anchors = prepare_anchor_episodes(union, classifications)
    expected_anchor_counts = {
        period: int((anchors["anchor_period"] == period).sum()) for period in ANCHOR_PERIODS
    }
    declared_anchor_counts = {
        period: int(pool_metadata[f"ma{period}"]["selected_with_ties"])
        for period in ANCHOR_PERIODS
    }
    if expected_anchor_counts != declared_anchor_counts:
        raise ValueError(
            "anchor counts do not match pool metadata: "
            f"actual={expected_anchor_counts}, declared={declared_anchor_counts}"
        )

    codes = union["ts_code"].astype(str).tolist()
    data_end = compact_date(pool_metadata["actual_target_end"])
    daily = get_day(
        ts_codes=codes,
        start_date=compact_date(args.history_start),
        end_date=data_end,
        qfq=daily_qfq,
        source="database_only",
    )
    if daily.empty:
        raise ValueError("local daily database returned no rows")
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily["trade_date"] = daily["trade_date"].map(compact_date)
    if daily.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("duplicate daily ts_code/trade_date rows")
    actual_codes = set(daily["ts_code"])
    if actual_codes != set(codes):
        raise ValueError(
            f"daily code coverage mismatch: missing={sorted(set(codes) - actual_codes)}"
        )

    featured = add_ma_streak_columns(daily, periods=SHORT_PERIODS)
    global_target_start = min(anchors["anchor_start_date"])
    episode_frames = [
        extract_episodes(
            featured,
            period=period,
            target_start=global_target_start,
            target_end=data_end,
        )
        for period in SHORT_PERIODS
    ]
    short_episodes = pd.concat(episode_frames, ignore_index=True)

    summary_records: list[dict[str, Any]] = []
    detail_records: list[dict[str, Any]] = []
    daily_groups = {code: group for code, group in featured.groupby("ts_code", sort=False)}
    episode_groups = {
        (str(code), int(period)): group
        for (code, period), group in short_episodes.groupby(["ts_code", "period"], sort=False)
    }
    empty_episodes = short_episodes.iloc[0:0].copy()
    for _, anchor in anchors.iterrows():
        code = str(anchor["ts_code"])
        for short_period in SHORT_PERIODS:
            summary, details = analyze_anchor_short_period(
                anchor,
                daily_groups[code],
                episode_groups.get((code, short_period), empty_episodes),
                short_period,
            )
            summary_records.append(summary)
            detail_records.extend(details)

    summary_frame = pd.DataFrame(summary_records).sort_values(
        ["observation_rank", "anchor_period", "short_period"]
    )
    detail_frame = pd.DataFrame(detail_records).sort_values(
        ["observation_rank", "anchor_period", "short_period", "segment_index"]
    )
    if len(summary_frame) != len(anchors) * len(SHORT_PERIODS):
        raise ValueError("anchor×short-period summary row count mismatch")
    if not summary_frame["anchor_length_matches_daily"].all():
        bad = summary_frame.loc[
            ~summary_frame["anchor_length_matches_daily"],
            ["ts_code", "anchor_period", "anchor_length", "anchor_observed_days"],
        ]
        raise ValueError(f"anchor length mismatch:\n{bad.to_string(index=False)}")
    if (summary_frame["segment_count"] <= 0).any():
        bad = summary_frame.loc[
            summary_frame["segment_count"].le(0), ["ts_code", "anchor_period", "short_period"]
        ]
        raise ValueError(f"anchors without short-MA rising segments:\n{bad.to_string(index=False)}")

    aggregate = aggregate_anchor_results(summary_frame)
    count_distribution = build_segment_count_distribution(summary_frame)
    per_stock = build_per_stock_wide(classifications, summary_frame)
    if len(per_stock) != len(union):
        raise ValueError("per-stock output row count mismatch")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_frame.to_csv(args.output_dir / "anchor_short_ma_summary.csv", index=False)
    detail_frame.to_csv(args.output_dir / "short_ma_segments_detail.csv", index=False)
    short_episodes.to_csv(args.output_dir / "short_ma_episodes_global.csv", index=False)
    aggregate.to_csv(args.output_dir / "aggregate_summary.csv", index=False)
    count_distribution.to_csv(
        args.output_dir / "segment_count_distribution.csv", index=False
    )
    per_stock.to_csv(
        args.output_dir / "per_stock_summary.csv", index=False, encoding="utf-8-sig"
    )

    basis_metadata = price_basis_metadata(daily_qfq)
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "requested_window_start": str(pool_metadata["requested_target_start"]),
        "actual_target_end": data_end,
        "history_start": compact_date(args.history_start),
        "price_basis": basis_metadata["price_basis"],
        "daily_access": basis_metadata["data_access"],
        "price_basis_zh": "前复权" if daily_qfq else "未复权",
        "qfq": daily_qfq,
        "pool_stocks": int(len(union)),
        "anchor_counts": {str(k): int(v) for k, v in expected_anchor_counts.items()},
        "anchor_rows": int(len(anchors)),
        "summary_rows": int(len(summary_frame)),
        "segment_detail_rows": int(len(detail_frame)),
        "short_episode_rows": int(len(short_episodes)),
        "daily_rows": int(len(daily)),
        "daily_codes": int(daily["ts_code"].nunique()),
        "long_episode_scope": (
            "entire observed selected MA60/MA120 rising episode, including pre-2026 carry-in; "
            "left/right censoring retained"
        ),
        "short_segment_definition": (
            "maximal consecutive stock trading observations where MA_t > MA_(t-1); "
            "flat/down breaks the segment; intersecting episodes are clipped to anchor bounds"
        ),
        "first_segment_rule": (
            "earliest short-MA rising episode intersecting the long anchor; if already active at "
            "anchor start it is clipped to the anchor start and marked carried-in"
        ),
        "anchor_total_return": "100 * (anchor_end_close / anchor_start_close - 1)",
        "first_segment_gain_share": (
            "100 * (first_segment_end_close - first_segment_start_close) / "
            "(anchor_end_close - anchor_start_close); only when denominator > 0"
        ),
        "first_end_cumulative_gain_share": (
            "100 * (first_segment_end_close - anchor_start_close) / "
            "(anchor_end_close - anchor_start_close); only when denominator > 0"
        ),
        "gain_share_aggregate_filter": (
            "anchor total price change > 0 and anchor_left_censored is false; "
            "right-censored anchors remain as through-cutoff observations"
        ),
        "aggregate": json.loads(aggregate.to_json(orient="records")),
    }
    (args.output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _write_report(aggregate, metadata, args.output_dir / "report.md")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
