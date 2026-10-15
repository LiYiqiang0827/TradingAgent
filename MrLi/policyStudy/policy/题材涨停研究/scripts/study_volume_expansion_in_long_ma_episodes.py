"""Study volume expansion in MA60/MA120 and nested MA20 rising episodes."""

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
    qfq = "2026_through_20260930_qfq"
    parser.add_argument(
        "--daily-file",
        type=Path,
        default=Path("outputs/ma60_ma120_top100_ppt") / qfq / "daily_input_qfq.csv.gz",
    )
    parser.add_argument(
        "--ma60-file",
        type=Path,
        default=Path("outputs/ma60_ma120_top100_themes")
        / qfq
        / "ma60_top100_with_ties_named.csv",
    )
    parser.add_argument(
        "--ma120-file",
        type=Path,
        default=Path("outputs/ma60_ma120_top100_themes")
        / qfq
        / "ma120_top100_with_ties_named.csv",
    )
    parser.add_argument(
        "--ma20-segments-file",
        type=Path,
        default=Path("outputs/ma_short_segments_within_long_episodes")
        / qfq
        / "short_ma_segments_detail.csv",
    )
    parser.add_argument(
        "--source-metadata-file",
        type=Path,
        default=Path("outputs/ma_short_segments_within_long_episodes")
        / qfq
        / "metadata.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/volume_expansion_in_long_ma_episodes") / qfq,
    )
    return parser.parse_args(argv)


def _compact_date(value: object) -> str:
    text = str(value).strip().replace("-", "")
    return text.split(".")[0].zfill(8)


def _as_bool(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def compare_interval_to_prior_volume(
    daily: pd.DataFrame,
    *,
    start_date: object,
    end_date: object,
    prior_days: int | None,
) -> dict[str, Any]:
    """Compare interval mean volume with an immediately preceding complete window."""
    required_columns = {"trade_date", "vol"}
    missing = required_columns.difference(daily.columns)
    if missing:
        raise ValueError(f"missing daily columns: {sorted(missing)}")
    frame = daily[["trade_date", "vol"]].copy()
    frame["trade_date"] = frame["trade_date"].map(_compact_date)
    frame["vol"] = pd.to_numeric(frame["vol"], errors="coerce")
    if frame["trade_date"].duplicated().any():
        raise ValueError("duplicate trade dates")
    if frame["vol"].isna().any() or frame["vol"].lt(0).any():
        raise ValueError("volume must be finite and nonnegative")
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    start = _compact_date(start_date)
    end = _compact_date(end_date)
    start_matches = frame.index[frame["trade_date"].eq(start)].tolist()
    end_matches = frame.index[frame["trade_date"].eq(end)].tolist()
    if len(start_matches) != 1 or len(end_matches) != 1:
        raise ValueError("interval boundary date not found")
    start_pos = int(start_matches[0])
    end_pos = int(end_matches[0])
    if end_pos < start_pos:
        raise ValueError("end date precedes start date")
    interval = frame.iloc[start_pos : end_pos + 1]
    interval_days = int(len(interval))
    required = interval_days if prior_days is None else int(prior_days)
    if required < 1:
        raise ValueError("prior_days must be at least 1")
    available = min(start_pos, required)
    complete = start_pos >= required
    result: dict[str, Any] = {
        "interval_days": interval_days,
        "interval_mean_volume": float(interval["vol"].mean()),
        "prior_window_days_required": required,
        "prior_window_days_available": int(available),
        "prior_window_complete": bool(complete),
        "prior_start_date": None,
        "prior_end_date": None,
        "prior_mean_volume": float("nan"),
        "volume_ratio": float("nan"),
        "volume_increase_pct": float("nan"),
    }
    if not complete:
        return result
    prior = frame.iloc[start_pos - required : start_pos]
    prior_mean = float(prior["vol"].mean())
    result.update(
        {
            "prior_start_date": str(prior.iloc[0]["trade_date"]),
            "prior_end_date": str(prior.iloc[-1]["trade_date"]),
            "prior_mean_volume": prior_mean,
        }
    )
    if prior_mean > 0:
        ratio = float(result["interval_mean_volume"] / prior_mean)
        result["volume_ratio"] = ratio
        result["volume_increase_pct"] = 100.0 * (ratio - 1.0)
    return result


def compute_ma20_segment_volume_metrics(
    daily: pd.DataFrame,
    *,
    clipped_start_date: object,
    clipped_end_date: object,
    anchor_start_date: object,
    anchor_end_date: object,
    short_episode_start_date: object,
    short_episode_end_date: object,
    fixed_prior_days: int = 20,
) -> dict[str, Any]:
    """Compute local prior-window and within-anchor volume ratios for one MA20 segment."""
    equal = compare_interval_to_prior_volume(
        daily,
        start_date=clipped_start_date,
        end_date=clipped_end_date,
        prior_days=None,
    )
    fixed = compare_interval_to_prior_volume(
        daily,
        start_date=clipped_start_date,
        end_date=clipped_end_date,
        prior_days=fixed_prior_days,
    )
    episode_equal = compare_interval_to_prior_volume(
        daily,
        start_date=short_episode_start_date,
        end_date=short_episode_end_date,
        prior_days=None,
    )
    episode_fixed = compare_interval_to_prior_volume(
        daily,
        start_date=short_episode_start_date,
        end_date=short_episode_end_date,
        prior_days=fixed_prior_days,
    )
    frame = daily[["trade_date", "vol"]].copy()
    frame["trade_date"] = frame["trade_date"].map(_compact_date)
    frame["vol"] = pd.to_numeric(frame["vol"], errors="coerce")
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    position = {date: idx for idx, date in enumerate(frame["trade_date"])}
    required_dates = [
        clipped_start_date,
        clipped_end_date,
        anchor_start_date,
        anchor_end_date,
        short_episode_start_date,
        short_episode_end_date,
    ]
    compact_dates = [_compact_date(value) for value in required_dates]
    if any(value not in position for value in compact_dates):
        raise ValueError("segment, anchor, or short-episode boundary not found")
    (
        clipped_start,
        clipped_end,
        anchor_start,
        anchor_end,
        short_start,
        short_end,
    ) = [position[value] for value in compact_dates]
    anchor = frame.iloc[anchor_start : anchor_end + 1]
    anchor_mean = float(anchor["vol"].mean())

    def overlap_days(result: dict[str, Any]) -> int:
        if not result["prior_window_complete"]:
            return 0
        prior_start = position[str(result["prior_start_date"])]
        prior_end = position[str(result["prior_end_date"])]
        left = max(prior_start, short_start)
        right = min(prior_end, short_end)
        return max(0, right - left + 1)

    equal_overlap = overlap_days(equal)
    fixed_overlap = overlap_days(fixed)
    segment_mean = float(equal["interval_mean_volume"])
    return {
        "episode_days": int(episode_equal["interval_days"]),
        "episode_mean_volume": float(episode_equal["interval_mean_volume"]),
        "episode_equal_prior_days_required": int(
            episode_equal["prior_window_days_required"]
        ),
        "episode_equal_prior_days_available": int(
            episode_equal["prior_window_days_available"]
        ),
        "episode_equal_prior_complete": bool(
            episode_equal["prior_window_complete"]
        ),
        "episode_equal_prior_start_date": episode_equal["prior_start_date"],
        "episode_equal_prior_end_date": episode_equal["prior_end_date"],
        "episode_equal_prior_mean_volume": float(
            episode_equal["prior_mean_volume"]
        ),
        "episode_equal_prior_volume_ratio": float(episode_equal["volume_ratio"]),
        "episode_equal_prior_volume_increase_pct": float(
            episode_equal["volume_increase_pct"]
        ),
        "episode_fixed20_prior_complete": bool(
            episode_fixed["prior_window_complete"]
        ),
        "episode_fixed20_prior_start_date": episode_fixed["prior_start_date"],
        "episode_fixed20_prior_end_date": episode_fixed["prior_end_date"],
        "episode_fixed20_prior_mean_volume": float(
            episode_fixed["prior_mean_volume"]
        ),
        "episode_fixed20_prior_volume_ratio": float(
            episode_fixed["volume_ratio"]
        ),
        "episode_fixed20_prior_volume_increase_pct": float(
            episode_fixed["volume_increase_pct"]
        ),
        "segment_days": int(equal["interval_days"]),
        "segment_mean_volume": segment_mean,
        "equal_prior_days_required": int(equal["prior_window_days_required"]),
        "equal_prior_days_available": int(equal["prior_window_days_available"]),
        "equal_prior_complete": bool(equal["prior_window_complete"]),
        "equal_prior_start_date": equal["prior_start_date"],
        "equal_prior_end_date": equal["prior_end_date"],
        "equal_prior_mean_volume": float(equal["prior_mean_volume"]),
        "equal_prior_volume_ratio": float(equal["volume_ratio"]),
        "equal_prior_volume_increase_pct": float(equal["volume_increase_pct"]),
        "equal_prior_overlap_short_episode_days": int(equal_overlap),
        "equal_prior_onset_clean": bool(
            equal["prior_window_complete"] and equal_overlap == 0
        ),
        "fixed20_prior_days_required": int(fixed["prior_window_days_required"]),
        "fixed20_prior_days_available": int(fixed["prior_window_days_available"]),
        "fixed20_prior_complete": bool(fixed["prior_window_complete"]),
        "fixed20_prior_start_date": fixed["prior_start_date"],
        "fixed20_prior_end_date": fixed["prior_end_date"],
        "fixed20_prior_mean_volume": float(fixed["prior_mean_volume"]),
        "fixed20_prior_volume_ratio": float(fixed["volume_ratio"]),
        "fixed20_prior_volume_increase_pct": float(fixed["volume_increase_pct"]),
        "fixed20_prior_overlap_short_episode_days": int(fixed_overlap),
        "fixed20_prior_onset_clean": bool(
            fixed["prior_window_complete"] and fixed_overlap == 0
        ),
        "anchor_mean_volume": anchor_mean,
        "segment_to_anchor_volume_ratio": (
            segment_mean / anchor_mean if anchor_mean > 0 else float("nan")
        ),
    }


def summarize_volume_ratios(
    detail: pd.DataFrame,
    *,
    ratio_col: str,
    days_col: str | None = None,
    min_days: int = 1,
) -> dict[str, Any]:
    """Summarize per-event volume ratios with explicit comparable denominators."""
    if ratio_col not in detail.columns:
        raise ValueError(f"missing ratio column: {ratio_col}")
    if min_days < 1:
        raise ValueError("min_days must be at least 1")
    eligible = detail
    if days_col is not None:
        if days_col not in detail.columns:
            raise ValueError(f"missing days column: {days_col}")
        eligible = detail.loc[
            pd.to_numeric(detail[days_col], errors="coerce").ge(min_days)
        ]
    ratios = pd.to_numeric(eligible[ratio_col], errors="coerce")
    ratios = ratios.loc[np.isfinite(ratios) & ratios.gt(0)]
    total = int(len(ratios))

    def count(mask: pd.Series) -> int:
        return int(mask.sum())

    def pct(value: int) -> float:
        return 100.0 * value / total if total else float("nan")

    amplified = count(ratios.gt(1.0))
    ge_1_2 = count(ratios.ge(1.2))
    ge_1_5 = count(ratios.ge(1.5))
    ge_2 = count(ratios.ge(2.0))
    return {
        "rows": int(len(detail)),
        "duration_eligible_rows": int(len(eligible)),
        "comparable_count": total,
        "ratio_mean": float(ratios.mean()) if total else float("nan"),
        "ratio_geometric_mean": (
            float(np.exp(np.log(ratios).mean())) if total else float("nan")
        ),
        "ratio_median": float(ratios.median()) if total else float("nan"),
        "ratio_p25": float(ratios.quantile(0.25)) if total else float("nan"),
        "ratio_p75": float(ratios.quantile(0.75)) if total else float("nan"),
        "ratio_p90": float(ratios.quantile(0.90)) if total else float("nan"),
        "amplified_gt_1_count": amplified,
        "amplified_gt_1_pct": pct(amplified),
        "amplified_ge_1_2_count": ge_1_2,
        "amplified_ge_1_2_pct": pct(ge_1_2),
        "amplified_ge_1_5_count": ge_1_5,
        "amplified_ge_1_5_pct": pct(ge_1_5),
        "amplified_ge_2_count": ge_2,
        "amplified_ge_2_pct": pct(ge_2),
    }


def build_anchor_all_segments_summary(
    detail: pd.DataFrame,
    *,
    ratio_col: str,
    days_col: str = "days_in_anchor",
    min_days: int,
    clean_only: bool,
) -> pd.DataFrame:
    """Build one row per long-MA anchor for the 'every MA20 segment' question."""
    keys = ["ts_code", "anchor_period", "anchor_start_date"]
    required = set(keys + [days_col, "started_before_anchor", ratio_col])
    missing = required.difference(detail.columns)
    if missing:
        raise ValueError(f"missing segment columns: {sorted(missing)}")
    if min_days < 1:
        raise ValueError("min_days must be at least 1")
    base = detail[keys].drop_duplicates().sort_values(keys).reset_index(drop=True)
    candidates = detail.loc[
        pd.to_numeric(detail[days_col], errors="coerce").ge(min_days)
    ].copy()
    if clean_only:
        candidates = candidates.loc[
            ~candidates["started_before_anchor"].map(_as_bool)
        ]
    rows: list[dict[str, Any]] = []
    grouped = {
        key: group
        for key, group in candidates.groupby(keys, sort=False, dropna=False)
    }
    for _, anchor in base.iterrows():
        key = tuple(anchor[column] for column in keys)
        group = grouped.get(key, candidates.iloc[0:0])
        ratios = pd.to_numeric(group[ratio_col], errors="coerce")
        ratios = ratios.loc[np.isfinite(ratios) & ratios.gt(0)]
        comparable = int(len(ratios))
        gt1 = int(ratios.gt(1.0).sum())
        ge12 = int(ratios.ge(1.2).sum())
        rows.append(
            {
                **anchor.to_dict(),
                "min_segment_days": int(min_days),
                "clean_only": bool(clean_only),
                "candidate_segments": int(len(group)),
                "comparable_segments": comparable,
                "amplified_gt_1_segments": gt1,
                "amplified_gt_1_pct": (
                    100.0 * gt1 / comparable if comparable else float("nan")
                ),
                "amplified_ge_1_2_segments": ge12,
                "amplified_ge_1_2_pct": (
                    100.0 * ge12 / comparable if comparable else float("nan")
                ),
                "all_segments_amplified_gt_1": bool(
                    comparable > 0 and gt1 == comparable
                ),
                "all_segments_amplified_ge_1_2": bool(
                    comparable > 0 and ge12 == comparable
                ),
                "minimum_volume_ratio": (
                    float(ratios.min()) if comparable else float("nan")
                ),
                "median_volume_ratio": (
                    float(ratios.median()) if comparable else float("nan")
                ),
            }
        )
    return pd.DataFrame(rows)


def summarize_anchor_all_segments(per_anchor: pd.DataFrame) -> dict[str, Any]:
    """Aggregate the share of anchors where every comparable MA20 segment expands."""
    required = {
        "comparable_segments",
        "all_segments_amplified_gt_1",
        "all_segments_amplified_ge_1_2",
        "amplified_gt_1_pct",
    }
    missing = required.difference(per_anchor.columns)
    if missing:
        raise ValueError(f"missing anchor summary columns: {sorted(missing)}")
    evaluable = per_anchor.loc[
        pd.to_numeric(per_anchor["comparable_segments"], errors="coerce").gt(0)
    ]
    total = int(len(evaluable))
    all_gt1 = int(evaluable["all_segments_amplified_gt_1"].map(_as_bool).sum())
    all_ge12 = int(evaluable["all_segments_amplified_ge_1_2"].map(_as_bool).sum())

    def pct(value: int) -> float:
        return 100.0 * value / total if total else float("nan")

    return {
        "anchor_rows": int(len(per_anchor)),
        "evaluable_anchor_count": total,
        "all_segments_amplified_gt_1_count": all_gt1,
        "all_segments_amplified_gt_1_pct": pct(all_gt1),
        "all_segments_amplified_ge_1_2_count": all_ge12,
        "all_segments_amplified_ge_1_2_pct": pct(all_ge12),
        "median_anchor_amplified_segment_pct": (
            float(evaluable["amplified_gt_1_pct"].median())
            if total
            else float("nan")
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    source_metadata = json.loads(
        args.source_metadata_file.read_text(encoding="utf-8")
    )
    if source_metadata.get("price_basis") != "QFQ close (qfq=True)":
        raise ValueError("source study is not QFQ")
    if "database_only" not in str(source_metadata.get("daily_access", "")):
        raise ValueError("source daily access is not database_only")

    daily = pd.read_csv(
        args.daily_file,
        dtype={"ts_code": str, "trade_date": str},
        usecols=["ts_code", "trade_date", "vol"],
    )
    daily["trade_date"] = daily["trade_date"].map(_compact_date)
    daily["vol"] = pd.to_numeric(daily["vol"], errors="coerce")
    if len(daily) != int(source_metadata["daily_rows"]):
        raise ValueError("daily row count does not match source metadata")
    if daily["vol"].isna().any() or daily["vol"].lt(0).any():
        raise ValueError("daily volume contains missing or negative values")
    if daily.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("duplicate stock trading dates")
    daily_by_code = {
        str(code): group.sort_values("trade_date").reset_index(drop=True)
        for code, group in daily.groupby("ts_code", sort=False)
    }

    anchor_frames: list[pd.DataFrame] = []
    for period, path in ((60, args.ma60_file), (120, args.ma120_file)):
        anchors = pd.read_csv(
            path,
            dtype={"ts_code": str, "start_date": str, "end_date": str},
        ).rename(
            columns={
                "rank": "anchor_rank",
                "start_date": "anchor_start_date",
                "end_date": "anchor_end_date",
                "length": "anchor_length",
                "left_censored": "anchor_left_censored",
                "carried_in": "anchor_carried_in",
                "right_censored": "anchor_right_censored",
            }
        )
        anchors["anchor_period"] = period
        anchor_frames.append(anchors)
    anchors = pd.concat(anchor_frames, ignore_index=True)
    if len(anchors) != int(source_metadata["anchor_rows"]):
        raise ValueError("anchor row count does not match source metadata")
    if anchors.duplicated(
        ["ts_code", "anchor_period", "anchor_start_date"]
    ).any():
        raise ValueError("anchor keys are not unique")

    long_records: list[dict[str, Any]] = []
    for _, row in anchors.iterrows():
        code = str(row["ts_code"])
        stock_daily = daily_by_code[code]
        equal = compare_interval_to_prior_volume(
            stock_daily,
            start_date=row["anchor_start_date"],
            end_date=row["anchor_end_date"],
            prior_days=None,
        )
        if int(equal["interval_days"]) != int(row["anchor_length"]):
            raise ValueError(f"{code} anchor length mismatch")
        fixed_results = {
            days: compare_interval_to_prior_volume(
                stock_daily,
                start_date=row["anchor_start_date"],
                end_date=row["anchor_end_date"],
                prior_days=days,
            )
            for days in (20, 60, 120)
        }
        record = row.to_dict()
        record.update(
            {
                "episode_days": int(equal["interval_days"]),
                "episode_mean_volume": float(equal["interval_mean_volume"]),
                "equal_prior_days_required": int(
                    equal["prior_window_days_required"]
                ),
                "equal_prior_days_available": int(
                    equal["prior_window_days_available"]
                ),
                "equal_prior_complete": bool(equal["prior_window_complete"]),
                "equal_prior_start_date": equal["prior_start_date"],
                "equal_prior_end_date": equal["prior_end_date"],
                "equal_prior_mean_volume": float(equal["prior_mean_volume"]),
                "equal_prior_volume_ratio": float(equal["volume_ratio"]),
                "equal_prior_volume_increase_pct": float(
                    equal["volume_increase_pct"]
                ),
            }
        )
        for days, result in fixed_results.items():
            prefix = f"pre{days}"
            record.update(
                {
                    f"{prefix}_complete": bool(result["prior_window_complete"]),
                    f"{prefix}_start_date": result["prior_start_date"],
                    f"{prefix}_end_date": result["prior_end_date"],
                    f"{prefix}_mean_volume": float(result["prior_mean_volume"]),
                    f"{prefix}_volume_ratio": float(result["volume_ratio"]),
                    f"{prefix}_volume_increase_pct": float(
                        result["volume_increase_pct"]
                    ),
                }
            )
        long_records.append(record)
    long_detail = pd.DataFrame(long_records).sort_values(
        ["anchor_period", "anchor_rank"]
    ).reset_index(drop=True)

    baseline_columns = {
        "equal_length": "equal_prior_volume_ratio",
        "pre20": "pre20_volume_ratio",
        "pre60": "pre60_volume_ratio",
        "pre120": "pre120_volume_ratio",
    }
    long_summary_records: list[dict[str, Any]] = []
    for anchor_period, period_group in long_detail.groupby(
        "anchor_period", sort=True
    ):
        cohorts = {
            "all_observed": period_group,
            "completed": period_group.loc[
                ~period_group["anchor_right_censored"].map(_as_bool)
            ],
            "right_censored": period_group.loc[
                period_group["anchor_right_censored"].map(_as_bool)
            ],
        }
        for cohort, cohort_group in cohorts.items():
            if cohort_group.empty:
                continue
            non_left = cohort_group.loc[
                ~cohort_group["anchor_left_censored"].map(_as_bool)
            ]
            for baseline, ratio_col in baseline_columns.items():
                result = summarize_volume_ratios(
                    non_left,
                    ratio_col=ratio_col,
                    min_days=1,
                )
                long_summary_records.append(
                    {
                        "anchor_period": int(anchor_period),
                        "cohort": cohort,
                        "baseline": baseline,
                        "cohort_anchor_rows": int(len(cohort_group)),
                        "excluded_left_censored": int(
                            cohort_group["anchor_left_censored"].map(_as_bool).sum()
                        ),
                        "right_censored_rows": int(
                            cohort_group["anchor_right_censored"].map(_as_bool).sum()
                        ),
                        **result,
                    }
                )
    long_summary = pd.DataFrame(long_summary_records)

    segment_source = pd.read_csv(
        args.ma20_segments_file,
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
    segment_source = segment_source.loc[
        segment_source["short_period"].eq(20)
    ].copy()
    anchor_flags = anchors[
        [
            "ts_code", "anchor_period", "anchor_start_date",
            "anchor_left_censored", "anchor_right_censored",
            "anchor_length", "anchor_rank",
        ]
    ]
    segment_source = segment_source.merge(
        anchor_flags,
        on=["ts_code", "anchor_period", "anchor_start_date"],
        how="left",
        validate="many_to_one",
        suffixes=("", "_anchor"),
    )
    if segment_source["anchor_left_censored"].isna().any():
        raise ValueError("MA20 segment anchor metadata coverage is incomplete")

    segment_records: list[dict[str, Any]] = []
    for _, row in segment_source.iterrows():
        code = str(row["ts_code"])
        metrics = compute_ma20_segment_volume_metrics(
            daily_by_code[code],
            clipped_start_date=row["clipped_start_date"],
            clipped_end_date=row["clipped_end_date"],
            anchor_start_date=row["anchor_start_date"],
            anchor_end_date=row["anchor_end_date"],
            short_episode_start_date=row["episode_start_date"],
            short_episode_end_date=row["episode_end_date"],
            fixed_prior_days=20,
        )
        if int(metrics["segment_days"]) != int(row["days_in_anchor"]):
            raise ValueError(f"{code} MA20 clipped segment length mismatch")
        if int(metrics["episode_days"]) != int(row["episode_full_length"]):
            raise ValueError(f"{code} MA20 full episode length mismatch")
        segment_records.append({**row.to_dict(), **metrics})
    segment_detail = pd.DataFrame(segment_records).sort_values(
        ["anchor_period", "anchor_rank", "segment_index"]
    ).reset_index(drop=True)

    comparison_specs = [
        (
            "full_episode_equal_prior",
            "episode_equal_prior_volume_ratio",
            "episode_full_length",
            None,
        ),
        (
            "full_episode_pre20",
            "episode_fixed20_prior_volume_ratio",
            "episode_full_length",
            None,
        ),
        (
            "clipped_local_equal_prior",
            "equal_prior_volume_ratio",
            "days_in_anchor",
            None,
        ),
        (
            "clipped_local_equal_prior_onset_clean",
            "equal_prior_volume_ratio",
            "days_in_anchor",
            "equal_prior_onset_clean",
        ),
        (
            "clipped_vs_anchor_mean",
            "segment_to_anchor_volume_ratio",
            "days_in_anchor",
            None,
        ),
    ]
    segment_summary_records: list[dict[str, Any]] = []
    for anchor_period, period_group in segment_detail.groupby(
        "anchor_period", sort=True
    ):
        primary_group = period_group.loc[
            ~period_group["anchor_left_censored"].map(_as_bool)
        ]
        for comparison, ratio_col, days_col, clean_col in comparison_specs:
            comparison_group = primary_group
            if clean_col is not None:
                comparison_group = comparison_group.loc[
                    comparison_group[clean_col].map(_as_bool)
                ]
            for min_days in (1, 5, 10):
                duration_eligible = comparison_group.loc[
                    pd.to_numeric(
                        comparison_group[days_col], errors="coerce"
                    ).ge(min_days)
                ]
                ratios = pd.to_numeric(
                    duration_eligible[ratio_col], errors="coerce"
                )
                comparable = duration_eligible.loc[
                    np.isfinite(ratios) & ratios.gt(0)
                ]
                result = summarize_volume_ratios(
                    comparison_group,
                    ratio_col=ratio_col,
                    days_col=days_col,
                    min_days=min_days,
                )
                segment_summary_records.append(
                    {
                        "anchor_period": int(anchor_period),
                        "comparison": comparison,
                        "min_segment_days": int(min_days),
                        "anchor_left_censored_excluded": int(
                            period_group["anchor_left_censored"].map(_as_bool).sum()
                        ),
                        "unique_anchor_count": int(
                            comparable[
                                ["ts_code", "anchor_period", "anchor_start_date"]
                            ].drop_duplicates().shape[0]
                        ),
                        "unique_stock_count": int(comparable["ts_code"].nunique()),
                        "right_censored_segment_rows": int(
                            comparable["anchor_right_censored"].map(_as_bool).sum()
                        ),
                        **result,
                    }
                )
    segment_summary = pd.DataFrame(segment_summary_records)

    per_anchor_records: list[pd.DataFrame] = []
    anchor_all_records: list[dict[str, Any]] = []
    primary_segments = segment_detail.loc[
        ~segment_detail["anchor_left_censored"].map(_as_bool)
    ]
    anchor_names = anchors[
        [
            "ts_code", "anchor_period", "anchor_start_date", "stock_name",
            "primary_theme", "broad_category", "anchor_rank",
            "anchor_end_date", "anchor_right_censored",
        ]
    ]
    for anchor_period, period_group in primary_segments.groupby(
        "anchor_period", sort=True
    ):
        for min_days in (1, 5, 10):
            per_anchor = build_anchor_all_segments_summary(
                period_group,
                ratio_col="episode_equal_prior_volume_ratio",
                days_col="episode_full_length",
                min_days=min_days,
                clean_only=False,
            ).merge(
                anchor_names,
                on=["ts_code", "anchor_period", "anchor_start_date"],
                how="left",
                validate="one_to_one",
            )
            per_anchor["comparison"] = "full_episode_equal_prior"
            per_anchor_records.append(per_anchor)
            anchor_all_records.append(
                {
                    "anchor_period": int(anchor_period),
                    "comparison": "full_episode_equal_prior",
                    "min_episode_days": int(min_days),
                    "right_censored_anchor_count": int(
                        per_anchor["anchor_right_censored"].map(_as_bool).sum()
                    ),
                    **summarize_anchor_all_segments(per_anchor),
                }
            )
    per_anchor_summary = pd.concat(per_anchor_records, ignore_index=True)
    anchor_all_summary = pd.DataFrame(anchor_all_records)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "long_detail": args.output_dir / "long_episode_volume_detail.csv",
        "long_summary": args.output_dir / "long_episode_volume_summary.csv",
        "segment_detail": args.output_dir / "ma20_segment_volume_detail.csv",
        "segment_summary": args.output_dir / "ma20_segment_volume_summary.csv",
        "per_anchor": args.output_dir / "ma20_anchor_all_segments_detail.csv",
        "anchor_all_summary": args.output_dir / "ma20_anchor_all_segments_summary.csv",
        "report": args.output_dir / "report.md",
        "metadata": args.output_dir / "metadata.json",
    }
    long_detail.to_csv(files["long_detail"], index=False, encoding="utf-8-sig")
    long_summary.to_csv(files["long_summary"], index=False, encoding="utf-8-sig")
    segment_detail.to_csv(
        files["segment_detail"], index=False, encoding="utf-8-sig"
    )
    segment_summary.to_csv(
        files["segment_summary"], index=False, encoding="utf-8-sig"
    )
    per_anchor_summary.to_csv(
        files["per_anchor"], index=False, encoding="utf-8-sig"
    )
    anchor_all_summary.to_csv(
        files["anchor_all_summary"], index=False, encoding="utf-8-sig"
    )
    _write_volume_report(
        long_summary=long_summary,
        segment_summary=segment_summary,
        anchor_all_summary=anchor_all_summary,
        output=files["report"],
        actual_end=str(source_metadata["actual_target_end"]),
    )

    output_files = [path for name, path in files.items() if name != "metadata"]
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "actual_target_end": source_metadata["actual_target_end"],
        "history_start": source_metadata["history_start"],
        "price_basis": source_metadata["price_basis"],
        "daily_access": source_metadata["daily_access"],
        "qfq": source_metadata["qfq"],
        "volume_field": "vol",
        "volume_unit_note": "within-stock ratio; raw volume units cancel in ratios",
        "anchor_counts": source_metadata["anchor_counts"],
        "anchor_rows": int(len(long_detail)),
        "ma20_intersecting_segment_rows": int(len(segment_detail)),
        "long_episode_primary_baseline": (
            "immediately preceding complete stock-trading window with the same "
            "number of days as the observed long-MA episode"
        ),
        "ma20_primary_baseline": (
            "full global MA20 rising episode mean volume divided by the "
            "immediately preceding complete equal-length stock-trading window"
        ),
        "amplification_thresholds": {
            "any": "ratio > 1.0",
            "material": "ratio >= 1.2",
            "strong": "ratio >= 1.5",
            "double": "ratio >= 2.0",
        },
        "long_episode_summary": long_summary.to_dict(orient="records"),
        "ma20_segment_summary": segment_summary.to_dict(orient="records"),
        "ma20_anchor_all_segments_summary": anchor_all_summary.to_dict(
            orient="records"
        ),
        "input_files": {
            "daily_file": {
                "path": str(args.daily_file),
                "sha256": _sha256(args.daily_file),
            },
            "ma60_file": {
                "path": str(args.ma60_file),
                "sha256": _sha256(args.ma60_file),
            },
            "ma120_file": {
                "path": str(args.ma120_file),
                "sha256": _sha256(args.ma120_file),
            },
            "ma20_segments_file": {
                "path": str(args.ma20_segments_file),
                "sha256": _sha256(args.ma20_segments_file),
            },
            "source_metadata_file": {
                "path": str(args.source_metadata_file),
                "sha256": _sha256(args.source_metadata_file),
            },
        },
        "output_files": {
            path.name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for path in output_files
        },
    }
    files["metadata"].write_text(
        json.dumps(_clean_json(metadata), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(long_summary.to_string(index=False))
    print("\nMA20 segment summary:")
    print(segment_summary.to_string(index=False))
    print("\nAll-segment summary:")
    print(anchor_all_summary.to_string(index=False))
    print(f"\nOutput: {args.output_dir}")


def _write_volume_report(
    *,
    long_summary: pd.DataFrame,
    segment_summary: pd.DataFrame,
    anchor_all_summary: pd.DataFrame,
    output: Path,
    actual_end: str,
) -> None:
    lines = [
        "# 长均线与MA20上升阶段成交量研究",
        "",
        f"- 数据截至：`{actual_end}`；锚定episode使用前复权日线计算。",
        "- 成交量字段：`vol`；所有比较均为同一股票自身前后比值，不跨股票比较绝对手数。",
        "- 放量倍率 = 研究阶段日均成交量 ÷ 基准阶段日均成交量。",
        "- 任意放量：倍率>1；明显放量：≥1.2；强放量：≥1.5；翻倍：≥2.0。",
        "",
        "## 问题一：MA60/MA120上升阶段是否整体放量",
        "",
        "主基准为上升episode之前、长度与episode完全相同且不重叠的交易日窗口；左截断样本排除。",
        "",
        "| 锚定均线 | 可比较episode | 倍率中位数 | IQR | 任意放量 | ≥20%放量 | ≥50%放量 | 翻倍 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    primary_long = long_summary.loc[
        long_summary["cohort"].eq("all_observed")
        & long_summary["baseline"].eq("equal_length")
    ].sort_values("anchor_period")
    for _, row in primary_long.iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | {int(row['comparable_count'])} | "
            f"{row['ratio_median']:.2f}× | {row['ratio_p25']:.2f}–{row['ratio_p75']:.2f}× | "
            f"{int(row['amplified_gt_1_count'])} ({row['amplified_gt_1_pct']:.1f}%) | "
            f"{int(row['amplified_ge_1_2_count'])} ({row['amplified_ge_1_2_pct']:.1f}%) | "
            f"{int(row['amplified_ge_1_5_count'])} ({row['amplified_ge_1_5_pct']:.1f}%) | "
            f"{int(row['amplified_ge_2_count'])} ({row['amplified_ge_2_pct']:.1f}%) |"
        )
    lines.extend(
        [
            "",
            "### 前置窗口敏感性",
            "",
            "| 锚定均线 | 前置基准 | 可比较数 | 倍率中位数 | 任意放量 | ≥20%放量 |",
            "|---:|---|---:|---:|---:|---:|",
        ]
    )
    sensitivity = long_summary.loc[
        long_summary["cohort"].eq("all_observed")
    ].sort_values(["anchor_period", "baseline"])
    baseline_names = {
        "equal_length": "等长窗口",
        "pre20": "前20日",
        "pre60": "前60日",
        "pre120": "前120日",
    }
    for _, row in sensitivity.iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | {baseline_names[row['baseline']]} | "
            f"{int(row['comparable_count'])} | {row['ratio_median']:.2f}× | "
            f"{row['amplified_gt_1_pct']:.1f}% | {row['amplified_ge_1_2_pct']:.1f}% |"
        )
    lines.extend(
        [
            "",
            "## 问题二：每段MA20上升是否都放量",
            "",
            "主口径取与长均线episode相交的完整全局MA20上升episode，比较其日均量与该episode之前的等长、不重叠窗口。",
            "",
            "| 锚定均线 | MA20最短时长 | 可比较段 | 倍率中位数 | 任意放量段 | ≥20%放量段 | ≥50%放量段 |",
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    primary_segments = segment_summary.loc[
        segment_summary["comparison"].eq("full_episode_equal_prior")
    ].sort_values(["anchor_period", "min_segment_days"])
    for _, row in primary_segments.iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | ≥{int(row['min_segment_days'])}日 | "
            f"{int(row['comparable_count'])} | {row['ratio_median']:.2f}× | "
            f"{int(row['amplified_gt_1_count'])} ({row['amplified_gt_1_pct']:.1f}%) | "
            f"{int(row['amplified_ge_1_2_count'])} ({row['amplified_ge_1_2_pct']:.1f}%) | "
            f"{int(row['amplified_ge_1_5_count'])} ({row['amplified_ge_1_5_pct']:.1f}%) |"
        )
    lines.extend(
        [
            "",
            "### 一个长均线episode内，是否每一段MA20都放量",
            "",
            "| 锚定均线 | MA20最短时长 | 可判定锚点 | 所有段均放量 | 所有段均≥20%放量 | 每个锚点放量段占比中位数 |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in anchor_all_summary.sort_values(
        ["anchor_period", "min_episode_days"]
    ).iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | ≥{int(row['min_episode_days'])}日 | "
            f"{int(row['evaluable_anchor_count'])} | "
            f"{int(row['all_segments_amplified_gt_1_count'])} ({row['all_segments_amplified_gt_1_pct']:.1f}%) | "
            f"{int(row['all_segments_amplified_ge_1_2_count'])} ({row['all_segments_amplified_ge_1_2_pct']:.1f}%) | "
            f"{row['median_anchor_amplified_segment_pct']:.1f}% |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- MA120入选episode均截至样本末日仍在延续，因此其均量是截至2026-09-30的阶段均量，不是完整周期终值。",
            "- 日均成交量易受极端单日放量影响，因此报告以股票/episode倍率中位数为主，同时保留均值和分位数明细。",
            "- 这是长趋势入选样本内的条件统计，不能直接反推普通股票放量后形成长趋势的概率。",
            "- `full_episode_equal_prior`是问题二主口径；裁切段相对局部前窗及相对长episode均量另保存在明细和汇总表。",
            "",
            "## 产物",
            "",
            "- `long_episode_volume_detail.csv` / `long_episode_volume_summary.csv`",
            "- `ma20_segment_volume_detail.csv` / `ma20_segment_volume_summary.csv`",
            "- `ma20_anchor_all_segments_detail.csv` / `ma20_anchor_all_segments_summary.csv`",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
