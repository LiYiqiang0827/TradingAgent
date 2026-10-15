"""Compare first MA20/MA30 rising onsets with MA60/MA120 rising onsets."""

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


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(
        "outputs/ma_short_segments_within_long_episodes/"
        "2026_through_20260930_qfq"
    )
    parser.add_argument(
        "--summary-file", type=Path, default=base / "anchor_short_ma_summary.csv"
    )
    parser.add_argument(
        "--segment-file", type=Path, default=base / "short_ma_segments_detail.csv"
    )
    parser.add_argument("--source-metadata-file", type=Path, default=base / "metadata.json")
    parser.add_argument(
        "--daily-file",
        type=Path,
        default=Path(
            "outputs/ma60_ma120_top100_ppt/2026_through_20260930_qfq/"
            "daily_input_qfq.csv.gz"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "outputs/short_ma_onset_lead_vs_long_ma/"
            "2026_through_20260930_qfq"
        ),
    )
    return parser.parse_args(argv)


def select_first_eligible_segments(
    segments: pd.DataFrame, *, min_episode_days: int
) -> pd.DataFrame:
    """Select the earliest intersecting short-MA episode meeting a duration floor."""
    if min_episode_days < 1:
        raise ValueError("min_episode_days must be at least 1")
    keys = ["ts_code", "anchor_period", "short_period", "anchor_start_date"]
    required = set(keys + ["segment_index"])
    missing = required.difference(segments.columns)
    if missing:
        raise ValueError(f"missing segment columns: {sorted(missing)}")
    if "episode_full_length" in segments.columns:
        duration_column = "episode_full_length"
    elif "episode_length" in segments.columns:
        duration_column = "episode_length"
    else:
        raise ValueError(
            "missing segment duration column: episode_full_length or episode_length"
        )
    eligible = segments.loc[
        pd.to_numeric(segments[duration_column], errors="coerce").ge(
            min_episode_days
        )
    ].copy()
    eligible = eligible.sort_values(keys + ["segment_index"])
    return eligible.groupby(keys, sort=False, as_index=False).head(1).reset_index(drop=True)


def _compact_date(value: object) -> str:
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def signed_trading_day_lead(
    trade_dates: pd.Series,
    short_start_date: object,
    anchor_start_date: object,
) -> int:
    """Positive means the short-MA rising episode started earlier."""
    dates = [_compact_date(value) for value in trade_dates]
    positions = {date: index for index, date in enumerate(dates)}
    short_start = _compact_date(short_start_date)
    anchor_start = _compact_date(anchor_start_date)
    missing = [date for date in (short_start, anchor_start) if date not in positions]
    if missing:
        raise ValueError(f"date not found in stock trading calendar: {missing}")
    return positions[anchor_start] - positions[short_start]


def aggregate_onset_leads(detail: pd.DataFrame) -> dict[str, Any]:
    """Aggregate earlier/same/later categories on uncensored onset observations."""
    required = {
        "anchor_left_censored", "short_episode_left_censored", "lead_trading_days"
    }
    missing = required.difference(detail.columns)
    if missing:
        raise ValueError(f"missing onset columns: {sorted(missing)}")
    anchor_left = detail["anchor_left_censored"].map(_as_bool)
    short_left = detail["short_episode_left_censored"].map(_as_bool)
    lead = pd.to_numeric(detail["lead_trading_days"], errors="coerce")
    valid = (~anchor_left) & (~short_left) & lead.notna()
    sample = lead.loc[valid]
    earlier = sample.loc[sample.gt(0)]
    same_count = int(sample.eq(0).sum())
    later = sample.loc[sample.lt(0)]
    total = int(len(sample))

    def pct(count: int) -> float:
        return 100.0 * count / total if total else float("nan")

    def quantile(values: pd.Series, value: float) -> float:
        return float(values.quantile(value)) if len(values) else float("nan")

    earlier_count = int(len(earlier))
    later_count = int(len(later))
    return {
        "rows": int(len(detail)),
        "primary_sample": total,
        "excluded_anchor_left_censored": int(anchor_left.sum()),
        "excluded_short_left_censored": int((~anchor_left & short_left).sum()),
        "earlier_count": earlier_count,
        "earlier_pct": pct(earlier_count),
        "same_day_count": same_count,
        "same_day_pct": pct(same_count),
        "later_count": later_count,
        "later_pct": pct(later_count),
        "at_or_before_count": earlier_count + same_count,
        "at_or_before_pct": pct(earlier_count + same_count),
        "signed_lead_median_days": quantile(sample, 0.50),
        "earlier_lead_median_days": quantile(earlier, 0.50),
        "earlier_lead_p25_days": quantile(earlier, 0.25),
        "earlier_lead_p75_days": quantile(earlier, 0.75),
        "earlier_lead_p90_days": quantile(earlier, 0.90),
        "earlier_at_least_5d_count": int(earlier.ge(5).sum()),
        "earlier_at_least_10d_count": int(earlier.ge(10).sum()),
        "earlier_at_least_20d_count": int(earlier.ge(20).sum()),
        "later_lag_median_days": (
            float((-later).median()) if len(later) else float("nan")
        ),
    }


def compute_duration_sensitivity(
    summary: pd.DataFrame,
    segments: pd.DataFrame,
    calendars: dict[str, pd.Series],
    *,
    thresholds: Sequence[int] = (1, 5, 10),
) -> pd.DataFrame:
    """Re-select the first intersecting episode at each duration threshold."""
    keys = ["ts_code", "anchor_period", "short_period", "anchor_start_date"]
    needed_summary = set(keys + ["anchor_left_censored"])
    missing_summary = needed_summary.difference(summary.columns)
    if missing_summary:
        raise ValueError(f"missing summary columns: {sorted(missing_summary)}")
    records: list[dict[str, Any]] = []
    summary_base = summary[keys + ["anchor_left_censored"]].copy()
    for threshold in thresholds:
        first = select_first_eligible_segments(
            segments, min_episode_days=int(threshold)
        )
        first = first[
            keys + ["episode_start_date", "episode_left_censored"]
        ].rename(
            columns={
                "episode_start_date": "short_episode_start_date",
                "episode_left_censored": "short_episode_left_censored",
            }
        )
        joined = summary_base.merge(
            first, on=keys, how="left", validate="one_to_one"
        )
        leads: list[float] = []
        for _, row in joined.iterrows():
            if pd.isna(row["short_episode_start_date"]):
                leads.append(float("nan"))
                continue
            code = str(row["ts_code"])
            leads.append(
                float(
                    signed_trading_day_lead(
                        calendars[code],
                        row["short_episode_start_date"],
                        row["anchor_start_date"],
                    )
                )
            )
        joined["lead_trading_days"] = leads
        for (anchor_period, short_period), group in joined.groupby(
            ["anchor_period", "short_period"], sort=True
        ):
            records.append(
                {
                    "min_episode_days": int(threshold),
                    "anchor_period": int(anchor_period),
                    "short_period": int(short_period),
                    "eligible_anchors": int(group["lead_trading_days"].notna().sum()),
                    **aggregate_onset_leads(group),
                }
            )
    return pd.DataFrame(records)


def aggregate_paired_onsets(paired: pd.DataFrame) -> dict[str, Any]:
    """Aggregate joint MA20/MA30 onset ordering for each long-MA anchor."""
    required = {
        "anchor_left_censored",
        "ma20_short_left_censored",
        "ma30_short_left_censored",
        "ma20_lead_trading_days",
        "ma30_lead_trading_days",
    }
    missing = required.difference(paired.columns)
    if missing:
        raise ValueError(f"missing paired onset columns: {sorted(missing)}")
    anchor_left = paired["anchor_left_censored"].map(_as_bool)
    ma20_left = paired["ma20_short_left_censored"].map(_as_bool)
    ma30_left = paired["ma30_short_left_censored"].map(_as_bool)
    ma20 = pd.to_numeric(paired["ma20_lead_trading_days"], errors="coerce")
    ma30 = pd.to_numeric(paired["ma30_lead_trading_days"], errors="coerce")
    valid = (~anchor_left) & (~ma20_left) & (~ma30_left) & ma20.notna() & ma30.notna()
    ma20 = ma20.loc[valid]
    ma30 = ma30.loc[valid]
    total = int(len(ma20))

    def count(mask: pd.Series) -> int:
        return int(mask.sum())

    def pct(value: int) -> float:
        return 100.0 * value / total if total else float("nan")

    both_earlier = count(ma20.gt(0) & ma30.gt(0))
    either_earlier = count(ma20.gt(0) | ma30.gt(0))
    both_at_or_before = count(ma20.ge(0) & ma30.ge(0))
    ma20_first = count(ma20.gt(ma30))
    ma30_first = count(ma30.gt(ma20))
    same = count(ma20.eq(ma30))
    return {
        "rows": int(len(paired)),
        "primary_sample": total,
        "both_earlier_count": both_earlier,
        "both_earlier_pct": pct(both_earlier),
        "either_earlier_count": either_earlier,
        "either_earlier_pct": pct(either_earlier),
        "both_at_or_before_count": both_at_or_before,
        "both_at_or_before_pct": pct(both_at_or_before),
        "ma20_started_earlier_count": ma20_first,
        "ma20_started_earlier_pct": pct(ma20_first),
        "ma30_started_earlier_count": ma30_first,
        "ma30_started_earlier_pct": pct(ma30_first),
        "same_start_count": same,
        "same_start_pct": pct(same),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _write_report(
    *,
    aggregate: pd.DataFrame,
    paired_aggregate: pd.DataFrame,
    duration_sensitivity: pd.DataFrame,
    detail: pd.DataFrame,
    output: Path,
    data_end: str,
) -> None:
    lines = [
        "# 第一段MA20/MA30连涨是否早于MA60/MA120连涨",
        "",
        f"- 数据截至：`{data_end}`；全部使用前复权日线。",
        "- 长均线锚定段：MA60名义前100并列107个，MA120名义前100并列126个。",
        "- 短均线首段：最早与长均线上升episode相交的全局MA20/MA30连续上升段。",
        "- 领先交易日数 = 长均线上升起点交易日序号 − 短均线episode真实起点交易日序号；正数=短均线更早，0=同日，负数=短均线更晚。",
        "- 主样本排除长均线或短均线episode左截断；右截断不影响起点比较。",
        "",
        "## 单条短均线结果",
        "",
        "| 长均线 | 短均线 | 主样本 | 提前 | 同日 | 滞后 | 长均线起点时已在上涨 | 提前样本领先中位数 | 提前IQR |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in aggregate.sort_values(["anchor_period", "short_period"]).iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | MA{int(row['short_period'])} | "
            f"{int(row['primary_sample'])} | {int(row['earlier_count'])} ({row['earlier_pct']:.1f}%) | "
            f"{int(row['same_day_count'])} ({row['same_day_pct']:.1f}%) | "
            f"{int(row['later_count'])} ({row['later_pct']:.1f}%) | "
            f"{int(row['at_or_before_count'])} ({row['at_or_before_pct']:.1f}%) | "
            f"{row['earlier_lead_median_days']:.1f}日 | "
            f"{row['earlier_lead_p25_days']:.1f}–{row['earlier_lead_p75_days']:.1f}日 |"
        )
    lines.extend(
        [
            "",
            "## MA20与MA30联合结果",
            "",
            "| 长均线 | 主样本 | MA20和MA30都提前 | 至少一个提前 | 两者均不晚于长均线 | MA20更早 | MA30更早 | 同日启动 |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in paired_aggregate.sort_values("anchor_period").iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | {int(row['primary_sample'])} | "
            f"{int(row['both_earlier_count'])} ({row['both_earlier_pct']:.1f}%) | "
            f"{int(row['either_earlier_count'])} ({row['either_earlier_pct']:.1f}%) | "
            f"{int(row['both_at_or_before_count'])} ({row['both_at_or_before_pct']:.1f}%) | "
            f"{int(row['ma20_started_earlier_count'])} ({row['ma20_started_earlier_pct']:.1f}%) | "
            f"{int(row['ma30_started_earlier_count'])} ({row['ma30_started_earlier_pct']:.1f}%) | "
            f"{int(row['same_start_count'])} ({row['same_start_pct']:.1f}%) |"
        )
    lines.extend(
        [
            "",
            "## 短均线段长门槛敏感性",
            "",
            "每个门槛下重新选择最早满足完整episode时长门槛的相交短均线段，并非只改变分母。",
            "",
            "| 最短episode | 长均线 | 短均线 | 有合格段锚点 | 主样本 | 提前 | 同日 | 滞后 | 不晚于长均线 |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in duration_sensitivity.sort_values(
        ["min_episode_days", "anchor_period", "short_period"]
    ).iterrows():
        lines.append(
            f"| ≥{int(row['min_episode_days'])}日 | MA{int(row['anchor_period'])} | "
            f"MA{int(row['short_period'])} | {int(row['eligible_anchors'])}/{int(row['rows'])} | "
            f"{int(row['primary_sample'])} | {int(row['earlier_count'])} ({row['earlier_pct']:.1f}%) | "
            f"{int(row['same_day_count'])} ({row['same_day_pct']:.1f}%) | "
            f"{int(row['later_count'])} ({row['later_pct']:.1f}%) | "
            f"{int(row['at_or_before_count'])} ({row['at_or_before_pct']:.1f}%) |"
        )
    lines.extend(
        [
            "",
            "## 领先时间最长的样本",
            "",
            "| 长均线 | 短均线 | 代码 | 名称 | 短均线起点 | 长均线起点 | 领先交易日 |",
            "|---:|---:|---|---|---|---|---:|",
        ]
    )
    primary = detail.loc[detail["primary_valid"]].sort_values(
        "lead_trading_days", ascending=False
    )
    for _, row in primary.head(20).iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | MA{int(row['short_period'])} | "
            f"{row['ts_code']} | {row['stock_name']} | {row['short_episode_start_date']} | "
            f"{row['anchor_start_date']} | {int(row['lead_trading_days'])} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 这里比较的是最早与长均线episode相交的短均线段，不是任意更早但已经结束、与长均线阶段不相交的历史短段。",
            "- “提前”说明短均线趋势先启动，并不等于之后一定形成MA60/MA120长趋势。该样本是按长趋势事后入选，不能直接解释为无条件预测概率。",
            "- MA120锚定段全部在2026-09-30右截断，但起点先后关系已经可观测；两条左截断MA120锚定段从主样本排除。",
            "",
            "## 文件",
            "",
            "- `onset_lead_detail.csv`：每个锚定段×MA20/MA30一行。",
            "- `aggregate_summary.csv`：四种长短均线组合汇总。",
            "- `paired_anchor_summary.csv`：每个长均线锚定段的MA20/MA30联合比较。",
            "- `paired_aggregate_summary.csv`：联合比较汇总。",
            "- `duration_sensitivity.csv`：按完整短均线episode至少1/5/10日重新选首段的敏感性。",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = _parse_args()
    source_metadata = json.loads(
        args.source_metadata_file.read_text(encoding="utf-8")
    )
    if source_metadata.get("price_basis") != "QFQ close (qfq=True)":
        raise ValueError("source nested-episode study is not QFQ")
    if "database_only" not in str(source_metadata.get("daily_access", "")):
        raise ValueError("source daily access is not database_only")

    summary = pd.read_csv(
        args.summary_file,
        dtype={
            "ts_code": str,
            "anchor_start_date": str,
            "anchor_end_date": str,
            "first_segment_start_date": str,
            "first_segment_end_date": str,
        },
    )
    segments = pd.read_csv(
        args.segment_file,
        dtype={
            "ts_code": str,
            "anchor_start_date": str,
            "anchor_end_date": str,
            "episode_start_date": str,
            "episode_end_date": str,
            "clipped_start_date": str,
            "clipped_end_date": str,
        },
    )
    daily = pd.read_csv(
        args.daily_file,
        dtype={"ts_code": str, "trade_date": str},
        usecols=["ts_code", "trade_date"],
    )
    if len(summary) != int(source_metadata["summary_rows"]):
        raise ValueError("summary row count does not match source metadata")
    if len(segments) != int(source_metadata["segment_detail_rows"]):
        raise ValueError("segment row count does not match source metadata")

    first_segments = segments.loc[segments["segment_index"].eq(1)].copy()
    keys = ["ts_code", "anchor_period", "short_period", "anchor_start_date"]
    if first_segments.duplicated(keys).any() or summary.duplicated(keys).any():
        raise ValueError("first-segment join keys are not unique")
    first_segments = first_segments[
        keys
        + [
            "episode_id", "episode_start_date", "episode_end_date",
            "episode_left_censored", "episode_right_censored",
            "clipped_start_date", "clipped_end_date", "started_before_anchor",
        ]
    ].rename(
        columns={
            "episode_id": "short_episode_id",
            "episode_start_date": "short_episode_start_date",
            "episode_end_date": "short_episode_end_date",
            "episode_left_censored": "short_episode_left_censored",
            "episode_right_censored": "short_episode_right_censored",
            "started_before_anchor": "source_started_before_anchor",
        }
    )
    detail = summary.merge(
        first_segments,
        on=keys,
        how="left",
        validate="one_to_one",
    )
    if detail["short_episode_id"].isna().any() or len(detail) != len(summary):
        raise ValueError("first-segment coverage is incomplete")

    calendars = {
        str(code): group.sort_values("trade_date")["trade_date"].reset_index(drop=True)
        for code, group in daily.groupby("ts_code", sort=False)
    }
    records: list[dict[str, Any]] = []
    for _, row in detail.iterrows():
        code = str(row["ts_code"])
        lead = signed_trading_day_lead(
            calendars[code],
            row["short_episode_start_date"],
            row["anchor_start_date"],
        )
        calendar_lead = (
            pd.Timestamp(_compact_date(row["anchor_start_date"]))
            - pd.Timestamp(_compact_date(row["short_episode_start_date"]))
        ).days
        relation = "earlier" if lead > 0 else "same_day" if lead == 0 else "later"
        anchor_left = _as_bool(row["anchor_left_censored"])
        short_left = _as_bool(row["short_episode_left_censored"])
        expected_offset = max(-lead, 0)
        if int(row["first_segment_start_offset_days"]) != expected_offset:
            raise ValueError(
                f"{code} MA{row['anchor_period']}/MA{row['short_period']} offset mismatch"
            )
        if _as_bool(row["source_started_before_anchor"]) != (lead > 0):
            raise ValueError(
                f"{code} MA{row['anchor_period']}/MA{row['short_period']} carried-in mismatch"
            )
        expected_clipped_start = max(
            _compact_date(row["anchor_start_date"]),
            _compact_date(row["short_episode_start_date"]),
        )
        if _compact_date(row["clipped_start_date"]) != expected_clipped_start:
            raise ValueError("clipped first-segment start mismatch")
        record = row.to_dict()
        record.update(
            {
                "lead_trading_days": int(lead),
                "lead_calendar_days": int(calendar_lead),
                "onset_relation": relation,
                "short_active_at_anchor_start": bool(lead >= 0),
                "primary_valid": bool(not anchor_left and not short_left),
            }
        )
        records.append(record)
    detail = pd.DataFrame(records).sort_values(
        ["anchor_period", "anchor_rank", "short_period"]
    ).reset_index(drop=True)

    aggregate_records: list[dict[str, Any]] = []
    for (anchor_period, short_period), group in detail.groupby(
        ["anchor_period", "short_period"], sort=True
    ):
        aggregate_records.append(
            {
                "anchor_period": int(anchor_period),
                "short_period": int(short_period),
                **aggregate_onset_leads(group),
            }
        )
    aggregate = pd.DataFrame(aggregate_records)
    duration_sensitivity = compute_duration_sensitivity(
        summary,
        segments,
        calendars,
        thresholds=(1, 5, 10),
    )
    main_from_sensitivity = duration_sensitivity.loc[
        duration_sensitivity["min_episode_days"].eq(1)
    ].sort_values(["anchor_period", "short_period"]).reset_index(drop=True)
    main_sorted = aggregate.sort_values(
        ["anchor_period", "short_period"]
    ).reset_index(drop=True)
    check_columns = [
        "primary_sample", "earlier_count", "same_day_count", "later_count"
    ]
    if not main_from_sensitivity[check_columns].equals(main_sorted[check_columns]):
        raise ValueError("one-day duration sensitivity does not reproduce main results")

    identity_columns = [
        "observation_rank", "ts_code", "stock_name", "primary_theme",
        "broad_category", "anchor_period", "anchor_rank", "anchor_start_date",
        "anchor_end_date", "anchor_length", "anchor_left_censored",
        "anchor_carried_in", "anchor_right_censored",
    ]
    ma20 = detail.loc[detail["short_period"].eq(20)].copy()
    ma30 = detail.loc[detail["short_period"].eq(30)].copy()
    ma20 = ma20[
        identity_columns
        + [
            "short_episode_start_date", "short_episode_left_censored",
            "lead_trading_days", "onset_relation",
        ]
    ].rename(
        columns={
            "short_episode_start_date": "ma20_episode_start_date",
            "short_episode_left_censored": "ma20_short_left_censored",
            "lead_trading_days": "ma20_lead_trading_days",
            "onset_relation": "ma20_onset_relation",
        }
    )
    paired = ma20.merge(
        ma30[
            ["ts_code", "anchor_period", "anchor_start_date"]
            + [
                "short_episode_start_date", "short_episode_left_censored",
                "lead_trading_days", "onset_relation",
            ]
        ].rename(
            columns={
                "short_episode_start_date": "ma30_episode_start_date",
                "short_episode_left_censored": "ma30_short_left_censored",
                "lead_trading_days": "ma30_lead_trading_days",
                "onset_relation": "ma30_onset_relation",
            }
        ),
        on=["ts_code", "anchor_period", "anchor_start_date"],
        how="inner",
        validate="one_to_one",
    )
    paired["both_earlier"] = paired["ma20_lead_trading_days"].gt(0) & paired[
        "ma30_lead_trading_days"
    ].gt(0)
    paired["either_earlier"] = paired["ma20_lead_trading_days"].gt(0) | paired[
        "ma30_lead_trading_days"
    ].gt(0)
    paired["both_at_or_before"] = paired["ma20_lead_trading_days"].ge(0) & paired[
        "ma30_lead_trading_days"
    ].ge(0)
    paired["which_short_ma_started_first"] = np.select(
        [
            paired["ma20_lead_trading_days"].gt(paired["ma30_lead_trading_days"]),
            paired["ma30_lead_trading_days"].gt(paired["ma20_lead_trading_days"]),
        ],
        ["MA20", "MA30"],
        default="same_day",
    )
    paired["primary_valid"] = (
        ~paired["anchor_left_censored"].map(_as_bool)
        & ~paired["ma20_short_left_censored"].map(_as_bool)
        & ~paired["ma30_short_left_censored"].map(_as_bool)
    )
    if len(paired) != int(source_metadata["anchor_rows"]):
        raise ValueError("paired anchor coverage mismatch")
    paired_aggregate_records = []
    for anchor_period, group in paired.groupby("anchor_period", sort=True):
        paired_aggregate_records.append(
            {
                "anchor_period": int(anchor_period),
                **aggregate_paired_onsets(group),
            }
        )
    paired_aggregate = pd.DataFrame(paired_aggregate_records)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    detail_file = args.output_dir / "onset_lead_detail.csv"
    aggregate_file = args.output_dir / "aggregate_summary.csv"
    paired_file = args.output_dir / "paired_anchor_summary.csv"
    paired_aggregate_file = args.output_dir / "paired_aggregate_summary.csv"
    duration_sensitivity_file = args.output_dir / "duration_sensitivity.csv"
    report_file = args.output_dir / "report.md"
    metadata_file = args.output_dir / "metadata.json"

    detail.to_csv(detail_file, index=False, encoding="utf-8-sig")
    aggregate.to_csv(aggregate_file, index=False, encoding="utf-8-sig")
    paired.to_csv(paired_file, index=False, encoding="utf-8-sig")
    paired_aggregate.to_csv(
        paired_aggregate_file, index=False, encoding="utf-8-sig"
    )
    duration_sensitivity.to_csv(
        duration_sensitivity_file, index=False, encoding="utf-8-sig"
    )
    _write_report(
        aggregate=aggregate,
        paired_aggregate=paired_aggregate,
        duration_sensitivity=duration_sensitivity,
        detail=detail,
        output=report_file,
        data_end=str(source_metadata["actual_target_end"]),
    )

    output_files = [
        detail_file,
        aggregate_file,
        paired_file,
        paired_aggregate_file,
        duration_sensitivity_file,
        report_file,
    ]
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "actual_target_end": source_metadata["actual_target_end"],
        "history_start": source_metadata["history_start"],
        "price_basis": source_metadata["price_basis"],
        "daily_access": source_metadata["daily_access"],
        "qfq": source_metadata["qfq"],
        "anchor_counts": source_metadata["anchor_counts"],
        "anchor_rows": int(len(paired)),
        "detail_rows": int(len(detail)),
        "first_short_episode_rule": (
            "earliest global MA20/MA30 rising episode intersecting the selected "
            "MA60/MA120 anchor; a short episode already active at anchor onset "
            "retains its true pre-anchor start"
        ),
        "rising_definition": "MA_t > MA_(t-1); flat, down, or unavailable breaks the episode",
        "lead_definition": (
            "anchor-start stock-trading-position minus short-episode-start "
            "stock-trading-position; positive=short earlier, zero=same day, "
            "negative=short later"
        ),
        "primary_filter": (
            "exclude left-censored long anchors and left-censored first short "
            "episodes; retain right-censored anchors because onset is observed"
        ),
        "aggregate": aggregate.to_dict(orient="records"),
        "paired_aggregate": paired_aggregate.to_dict(orient="records"),
        "duration_sensitivity": duration_sensitivity.to_dict(orient="records"),
        "input_files": {
            "summary_file": {
                "path": str(args.summary_file),
                "sha256": _sha256(args.summary_file),
            },
            "segment_file": {
                "path": str(args.segment_file),
                "sha256": _sha256(args.segment_file),
            },
            "source_metadata_file": {
                "path": str(args.source_metadata_file),
                "sha256": _sha256(args.source_metadata_file),
            },
            "daily_file": {
                "path": str(args.daily_file),
                "sha256": _sha256(args.daily_file),
            },
        },
        "output_files": {
            path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in output_files
        },
    }
    metadata_file.write_text(
        json.dumps(_clean_json(metadata), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(aggregate.to_string(index=False))
    print("\nPaired:")
    print(paired_aggregate.to_string(index=False))
    print(f"\nOutput: {args.output_dir}")


if __name__ == "__main__":
    main()
