"""Evaluate volume-price divergence as a top signal in long MA trends."""

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
        "--source-metadata-file",
        type=Path,
        default=Path("outputs/ma_short_segments_within_long_episodes")
        / qfq
        / "metadata.json",
    )
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--volume-window", type=int, default=5)
    parser.add_argument("--volume-ratio-threshold", type=float, default=0.8)
    parser.add_argument("--forward-days", type=int, default=20)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/volume_price_divergence_top_signal") / qfq,
    )
    return parser.parse_args(argv)


def _compact_date(value: object) -> str:
    return str(value).strip().replace("-", "").split(".")[0].zfill(8)


def _as_bool(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def detect_divergence_candidates(
    daily: pd.DataFrame,
    *,
    anchor_start_date: object,
    anchor_end_date: object,
    price_col: str,
    lookback_days: int,
    volume_window: int,
    volume_ratio_threshold: float,
) -> pd.DataFrame:
    """Detect no-lookahead new-high days and classify lower-volume divergence."""
    required = {"trade_date", "close", "vol", price_col}
    missing = required.difference(daily.columns)
    if missing:
        raise ValueError(f"missing daily columns: {sorted(missing)}")
    if lookback_days < 1 or volume_window < 1:
        raise ValueError("lookback_days and volume_window must be positive")
    if volume_ratio_threshold <= 0:
        raise ValueError("volume_ratio_threshold must be positive")
    frame = daily[list(required)].copy()
    frame["trade_date"] = frame["trade_date"].map(_compact_date)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame[price_col] = pd.to_numeric(frame[price_col], errors="coerce")
    frame["vol"] = pd.to_numeric(frame["vol"], errors="coerce")
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    if frame["trade_date"].duplicated().any():
        raise ValueError("duplicate trade dates")
    if frame[["close", price_col, "vol"]].isna().any().any():
        raise ValueError("price or volume contains missing values")
    frame["volume_average"] = frame["vol"].rolling(
        volume_window, min_periods=volume_window
    ).mean()
    position = {date: idx for idx, date in enumerate(frame["trade_date"])}
    start = _compact_date(anchor_start_date)
    end = _compact_date(anchor_end_date)
    if start not in position or end not in position:
        raise ValueError("anchor boundary not found")
    start_pos = position[start]
    end_pos = position[end]
    rows: list[dict[str, Any]] = []
    for current_pos in range(start_pos + lookback_days, end_pos + 1):
        prior_start = current_pos - lookback_days
        if prior_start < start_pos:
            continue
        prior = frame.iloc[prior_start:current_pos]
        current_price = float(frame.iloc[current_pos][price_col])
        prior_max = float(prior[price_col].max())
        if not current_price > prior_max:
            continue
        peak_offsets = np.flatnonzero(
            np.isclose(prior[price_col].to_numpy(dtype=float), prior_max)
        )
        prior_peak_pos = prior_start + int(peak_offsets[-1])
        current_volume = float(frame.iloc[current_pos]["volume_average"])
        prior_peak_volume = float(frame.iloc[prior_peak_pos]["volume_average"])
        if not np.isfinite(current_volume) or not np.isfinite(prior_peak_volume):
            continue
        ratio = current_volume / prior_peak_volume if prior_peak_volume > 0 else np.nan
        if not np.isfinite(ratio):
            continue
        rows.append(
            {
                "signal_date": str(frame.iloc[current_pos]["trade_date"]),
                "anchor_offset_days": int(current_pos - start_pos),
                "signal_price": current_price,
                "signal_close": float(frame.iloc[current_pos]["close"]),
                "prior_peak_date": str(frame.iloc[prior_peak_pos]["trade_date"]),
                "prior_peak_price": prior_max,
                "price_breakout_pct": 100.0 * (current_price / prior_max - 1.0),
                "current_volume_average": current_volume,
                "prior_peak_volume_average": prior_peak_volume,
                "volume_ratio_to_prior_peak": ratio,
                "volume_reduction_pct": 100.0 * (1.0 - ratio),
                "is_volume_price_divergence": bool(
                    ratio <= volume_ratio_threshold
                ),
                "price_col": price_col,
                "lookback_days": int(lookback_days),
                "volume_window": int(volume_window),
                "volume_ratio_threshold": float(volume_ratio_threshold),
            }
        )
    return pd.DataFrame(rows)


def add_forward_top_outcomes(
    events: pd.DataFrame,
    daily: pd.DataFrame,
    *,
    forward_days: int,
) -> pd.DataFrame:
    """Add forward drawdown/run-up and completed-anchor peak proximity."""
    if forward_days < 1:
        raise ValueError("forward_days must be positive")
    required_events = {
        "signal_date",
        "signal_close",
        "anchor_start_date",
        "anchor_end_date",
        "anchor_right_censored",
    }
    missing = required_events.difference(events.columns)
    if missing:
        raise ValueError(f"missing event columns: {sorted(missing)}")
    frame = daily[["trade_date", "close"]].copy()
    frame["trade_date"] = frame["trade_date"].map(_compact_date)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.sort_values("trade_date").reset_index(drop=True)
    position = {date: idx for idx, date in enumerate(frame["trade_date"])}
    rows: list[dict[str, Any]] = []
    for _, event in events.iterrows():
        signal_date = _compact_date(event["signal_date"])
        signal_pos = position[signal_date]
        signal_close = float(event["signal_close"])
        future = frame.iloc[signal_pos + 1 : signal_pos + 1 + forward_days]
        complete = len(future) == forward_days
        record = event.to_dict()
        record.update(
            {
                "forward_days": int(forward_days),
                "forward_window_complete": bool(complete),
                "forward_end_date": (
                    str(future.iloc[-1]["trade_date"]) if complete else None
                ),
                "forward_return_pct": float("nan"),
                "forward_max_runup_pct": float("nan"),
                "forward_max_drawdown_pct": float("nan"),
                "forward_drawdown_ge_10pct": False,
                "top_like_forward_pattern": False,
                "anchor_peak_observable": False,
                "anchor_peak_date": None,
                "anchor_peak_close": float("nan"),
                "trading_days_to_anchor_peak": float("nan"),
                "within_5d_before_anchor_peak": False,
                "within_10d_before_anchor_peak": False,
                "within_20d_before_anchor_peak": False,
            }
        )
        if complete:
            end_close = float(future.iloc[-1]["close"])
            max_close = float(future["close"].max())
            min_close = float(future["close"].min())
            forward_return = 100.0 * (end_close / signal_close - 1.0)
            max_runup = 100.0 * (max_close / signal_close - 1.0)
            max_drawdown = 100.0 * (min_close / signal_close - 1.0)
            record.update(
                {
                    "forward_return_pct": forward_return,
                    "forward_max_runup_pct": max_runup,
                    "forward_max_drawdown_pct": max_drawdown,
                    "forward_drawdown_ge_10pct": bool(max_drawdown <= -10.0),
                    "top_like_forward_pattern": bool(
                        max_runup <= 5.0 and max_drawdown <= -10.0
                    ),
                }
            )
        if not _as_bool(event["anchor_right_censored"]):
            start_pos = position[_compact_date(event["anchor_start_date"])]
            end_pos = position[_compact_date(event["anchor_end_date"])]
            anchor = frame.iloc[start_pos : end_pos + 1]
            peak_close = float(anchor["close"].max())
            peak_relative = int(
                np.flatnonzero(
                    np.isclose(anchor["close"].to_numpy(dtype=float), peak_close)
                )[0]
            )
            peak_pos = start_pos + peak_relative
            distance = peak_pos - signal_pos
            record.update(
                {
                    "anchor_peak_observable": True,
                    "anchor_peak_date": str(frame.iloc[peak_pos]["trade_date"]),
                    "anchor_peak_close": peak_close,
                    "trading_days_to_anchor_peak": int(distance),
                    "within_5d_before_anchor_peak": bool(0 <= distance <= 5),
                    "within_10d_before_anchor_peak": bool(0 <= distance <= 10),
                    "within_20d_before_anchor_peak": bool(0 <= distance <= 20),
                }
            )
        rows.append(record)
    return pd.DataFrame(rows)


def apply_class_cooldown(
    events: pd.DataFrame,
    *,
    cooldown_days: int,
) -> pd.DataFrame:
    """Deduplicate serial events within each anchor and signal class."""
    if cooldown_days < 1:
        raise ValueError("cooldown_days must be positive")
    keys = [
        "ts_code",
        "anchor_period",
        "anchor_start_date",
        "is_volume_price_divergence",
    ]
    required = set(keys + ["anchor_offset_days"])
    missing = required.difference(events.columns)
    if missing:
        raise ValueError(f"missing cooldown columns: {sorted(missing)}")
    result = events.copy()
    selected = pd.Series(False, index=result.index, dtype=bool)
    for _, group in result.groupby(keys, sort=False, dropna=False):
        last_selected: int | None = None
        for idx, row in group.sort_values("anchor_offset_days").iterrows():
            offset = int(row["anchor_offset_days"])
            if last_selected is None or offset - last_selected >= cooldown_days:
                selected.loc[idx] = True
                last_selected = offset
    result["cooldown_days"] = int(cooldown_days)
    result["cooldown_selected"] = selected
    return result


def summarize_effectiveness(events: pd.DataFrame) -> pd.DataFrame:
    """Compare divergence events with non-divergent new-high controls."""
    required = {
        "is_volume_price_divergence",
        "forward_window_complete",
        "forward_return_pct",
        "forward_max_runup_pct",
        "forward_max_drawdown_pct",
        "forward_drawdown_ge_10pct",
        "top_like_forward_pattern",
        "anchor_peak_observable",
        "within_10d_before_anchor_peak",
    }
    missing = required.difference(events.columns)
    if missing:
        raise ValueError(f"missing effectiveness columns: {sorted(missing)}")
    rows: list[dict[str, Any]] = []
    for is_divergence, group in events.groupby(
        "is_volume_price_divergence", sort=False
    ):
        forward = group.loc[group["forward_window_complete"].map(_as_bool)]
        peak = group.loc[group["anchor_peak_observable"].map(_as_bool)]
        forward_count = int(len(forward))
        peak_count = int(len(peak))
        drawdown_count = int(
            forward["forward_drawdown_ge_10pct"].map(_as_bool).sum()
        )
        top_like_count = int(forward["top_like_forward_pattern"].map(_as_bool).sum())
        near_peak_count = int(
            peak["within_10d_before_anchor_peak"].map(_as_bool).sum()
        )
        rows.append(
            {
                "event_class": (
                    "divergence" if _as_bool(is_divergence)
                    else "non_divergent_new_high"
                ),
                "event_count": int(len(group)),
                "forward_comparable_count": forward_count,
                "median_forward_return_pct": (
                    float(forward["forward_return_pct"].median())
                    if forward_count else float("nan")
                ),
                "median_forward_max_runup_pct": (
                    float(forward["forward_max_runup_pct"].median())
                    if forward_count else float("nan")
                ),
                "median_forward_max_drawdown_pct": (
                    float(forward["forward_max_drawdown_pct"].median())
                    if forward_count else float("nan")
                ),
                "drawdown_ge_10pct_count": drawdown_count,
                "drawdown_ge_10pct_rate": (
                    100.0 * drawdown_count / forward_count
                    if forward_count else float("nan")
                ),
                "top_like_forward_count": top_like_count,
                "top_like_forward_rate": (
                    100.0 * top_like_count / forward_count
                    if forward_count else float("nan")
                ),
                "completed_anchor_peak_comparable_count": peak_count,
                "near_anchor_peak_10d_count": near_peak_count,
                "near_anchor_peak_10d_rate": (
                    100.0 * near_peak_count / peak_count
                    if peak_count else float("nan")
                ),
            }
        )
    return pd.DataFrame(rows)


def build_within_anchor_effectiveness(events: pd.DataFrame) -> pd.DataFrame:
    """Create paired divergence-minus-control outcome differences per anchor."""
    required = {
        "ts_code", "anchor_period", "anchor_start_date",
        "is_volume_price_divergence", "forward_window_complete",
        "forward_drawdown_ge_10pct", "forward_return_pct",
    }
    missing = required.difference(events.columns)
    if missing:
        raise ValueError(f"missing within-anchor columns: {sorted(missing)}")
    complete = events.loc[events["forward_window_complete"].map(_as_bool)]
    keys = ["ts_code", "anchor_period", "anchor_start_date"]
    rows: list[dict[str, Any]] = []
    for key, group in complete.groupby(keys, sort=False):
        divergence = group.loc[group["is_volume_price_divergence"].map(_as_bool)]
        control = group.loc[~group["is_volume_price_divergence"].map(_as_bool)]
        if divergence.empty or control.empty:
            continue
        divergence_rate = 100.0 * divergence[
            "forward_drawdown_ge_10pct"
        ].map(_as_bool).mean()
        control_rate = 100.0 * control[
            "forward_drawdown_ge_10pct"
        ].map(_as_bool).mean()
        rows.append(
            {
                "ts_code": key[0],
                "anchor_period": int(key[1]),
                "anchor_start_date": key[2],
                "divergence_event_count": int(len(divergence)),
                "control_event_count": int(len(control)),
                "divergence_drawdown_rate_pct": float(divergence_rate),
                "control_drawdown_rate_pct": float(control_rate),
                "drawdown_rate_difference_pct": float(
                    divergence_rate - control_rate
                ),
                "divergence_median_forward_return_pct": float(
                    divergence["forward_return_pct"].median()
                ),
                "control_median_forward_return_pct": float(
                    control["forward_return_pct"].median()
                ),
                "median_forward_return_difference_pct": float(
                    divergence["forward_return_pct"].median()
                    - control["forward_return_pct"].median()
                ),
            }
        )
    return pd.DataFrame(rows)


def aggregate_within_anchor_effectiveness(
    per_anchor: pd.DataFrame,
) -> dict[str, Any]:
    """Aggregate paired per-anchor differences without mixing stock populations."""
    required = {
        "drawdown_rate_difference_pct",
        "median_forward_return_difference_pct",
    }
    missing = required.difference(per_anchor.columns)
    if missing:
        raise ValueError(f"missing paired columns: {sorted(missing)}")
    difference = pd.to_numeric(
        per_anchor["drawdown_rate_difference_pct"], errors="coerce"
    ).dropna()
    return_difference = pd.to_numeric(
        per_anchor["median_forward_return_difference_pct"], errors="coerce"
    ).dropna()
    return {
        "paired_anchor_count": int(len(difference)),
        "divergence_rate_higher_count": int(difference.gt(0).sum()),
        "divergence_rate_lower_count": int(difference.lt(0).sum()),
        "divergence_rate_equal_count": int(difference.eq(0).sum()),
        "median_drawdown_rate_difference_pct": (
            float(difference.median()) if len(difference) else float("nan")
        ),
        "mean_drawdown_rate_difference_pct": (
            float(difference.mean()) if len(difference) else float("nan")
        ),
        "median_forward_return_difference_pct": (
            float(return_difference.median())
            if len(return_difference) else float("nan")
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
        usecols=["ts_code", "trade_date", "close", "high", "vol"],
    )
    daily["trade_date"] = daily["trade_date"].map(_compact_date)
    if len(daily) != int(source_metadata["daily_rows"]):
        raise ValueError("daily row count does not match source metadata")
    if daily.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("duplicate stock trading dates")
    daily_by_code = {
        str(code): group.sort_values("trade_date").reset_index(drop=True)
        for code, group in daily.groupby("ts_code", sort=False)
    }

    anchor_frames = []
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
    anchors = anchors.loc[
        ~anchors["anchor_left_censored"].map(_as_bool)
    ].copy()

    def run_spec(
        *,
        price_col: str,
        lookback_days: int,
        volume_window: int,
        volume_ratio_threshold: float,
        spec_name: str,
    ) -> pd.DataFrame:
        event_frames: list[pd.DataFrame] = []
        for _, anchor in anchors.iterrows():
            code = str(anchor["ts_code"])
            events = detect_divergence_candidates(
                daily_by_code[code],
                anchor_start_date=anchor["anchor_start_date"],
                anchor_end_date=anchor["anchor_end_date"],
                price_col=price_col,
                lookback_days=lookback_days,
                volume_window=volume_window,
                volume_ratio_threshold=volume_ratio_threshold,
            )
            if events.empty:
                continue
            for column in [
                "ts_code", "stock_name", "primary_theme", "broad_category",
                "anchor_period", "anchor_rank", "anchor_start_date",
                "anchor_end_date", "anchor_length", "anchor_right_censored",
            ]:
                events[column] = anchor[column]
            events["spec_name"] = spec_name
            events = add_forward_top_outcomes(
                events,
                daily_by_code[code],
                forward_days=args.forward_days,
            )
            event_frames.append(events)
        if not event_frames:
            return pd.DataFrame()
        return pd.concat(event_frames, ignore_index=True)

    primary_events = run_spec(
        price_col="close",
        lookback_days=args.lookback_days,
        volume_window=args.volume_window,
        volume_ratio_threshold=args.volume_ratio_threshold,
        spec_name="primary_close_l60_v5_t0.8",
    )
    if primary_events.empty:
        raise ValueError("primary divergence study produced no new-high candidates")
    primary_events = apply_class_cooldown(primary_events, cooldown_days=10)

    summary_records: list[dict[str, Any]] = []
    lift_records: list[dict[str, Any]] = []
    for anchor_period, period_group in primary_events.groupby(
        "anchor_period", sort=True
    ):
        scopes = {
            "all_candidate_days": period_group,
            "cooldown_10d": period_group.loc[
                period_group["cooldown_selected"].map(_as_bool)
            ],
        }
        for scope, scope_group in scopes.items():
            summary = summarize_effectiveness(scope_group)
            summary.insert(0, "anchor_period", int(anchor_period))
            summary.insert(1, "scope", scope)
            summary_records.extend(summary.to_dict(orient="records"))
            indexed = summary.set_index("event_class")
            if {
                "divergence", "non_divergent_new_high"
            }.issubset(indexed.index):
                divergence = indexed.loc["divergence"]
                control = indexed.loc["non_divergent_new_high"]

                def lift(metric: str) -> float:
                    denominator = float(control[metric])
                    return (
                        float(divergence[metric]) / denominator
                        if denominator > 0 else float("nan")
                    )

                lift_records.append(
                    {
                        "anchor_period": int(anchor_period),
                        "scope": scope,
                        "drawdown_ge_10pct_rate_lift": lift(
                            "drawdown_ge_10pct_rate"
                        ),
                        "top_like_forward_rate_lift": lift(
                            "top_like_forward_rate"
                        ),
                        "near_anchor_peak_10d_rate_lift": lift(
                            "near_anchor_peak_10d_rate"
                        ),
                        "median_forward_return_difference_pct": float(
                            divergence["median_forward_return_pct"]
                            - control["median_forward_return_pct"]
                        ),
                        "median_forward_drawdown_difference_pct": float(
                            divergence["median_forward_max_drawdown_pct"]
                            - control["median_forward_max_drawdown_pct"]
                        ),
                    }
                )
    effectiveness_summary = pd.DataFrame(summary_records)
    effect_lifts = pd.DataFrame(lift_records)

    within_anchor_frames: list[pd.DataFrame] = []
    within_anchor_aggregate_records: list[dict[str, Any]] = []
    for anchor_period, period_group in primary_events.groupby(
        "anchor_period", sort=True
    ):
        for scope, scope_group in {
            "all_candidate_days": period_group,
            "cooldown_10d": period_group.loc[
                period_group["cooldown_selected"].map(_as_bool)
            ],
        }.items():
            paired = build_within_anchor_effectiveness(scope_group)
            if paired.empty:
                continue
            paired["scope"] = scope
            within_anchor_frames.append(paired)
            within_anchor_aggregate_records.append(
                {
                    "anchor_period": int(anchor_period),
                    "scope": scope,
                    **aggregate_within_anchor_effectiveness(paired),
                }
            )
    within_anchor_detail = pd.concat(within_anchor_frames, ignore_index=True)
    within_anchor_summary = pd.DataFrame(within_anchor_aggregate_records)

    top_coverage_records: list[dict[str, Any]] = []
    completed_anchors = anchors.loc[
        ~anchors["anchor_right_censored"].map(_as_bool)
    ]
    for anchor_period, anchor_group in completed_anchors.groupby(
        "anchor_period", sort=True
    ):
        period_events = primary_events.loc[
            primary_events["anchor_period"].eq(anchor_period)
        ]
        for scope, scoped_events in {
            "all_candidate_days": period_events,
            "cooldown_10d": period_events.loc[
                period_events["cooldown_selected"].map(_as_bool)
            ],
        }.items():
            anchors_with_candidates = 0
            anchors_with_divergence = 0
            anchors_with_divergence_near_top = 0
            nearest_distances: list[int] = []
            for _, anchor in anchor_group.iterrows():
                group = scoped_events.loc[
                    scoped_events["ts_code"].eq(anchor["ts_code"])
                    & scoped_events["anchor_start_date"].eq(
                        anchor["anchor_start_date"]
                    )
                ]
                if not group.empty:
                    anchors_with_candidates += 1
                divergence = group.loc[
                    group["is_volume_price_divergence"].map(_as_bool)
                ]
                if not divergence.empty:
                    anchors_with_divergence += 1
                near = divergence.loc[
                    divergence["within_10d_before_anchor_peak"].map(_as_bool)
                ]
                if not near.empty:
                    anchors_with_divergence_near_top += 1
                    nearest_distances.append(
                        int(near["trading_days_to_anchor_peak"].min())
                    )
            total = int(len(anchor_group))
            top_coverage_records.append(
                {
                    "anchor_period": int(anchor_period),
                    "scope": scope,
                    "completed_anchor_count": total,
                    "anchors_with_new_high_candidates": anchors_with_candidates,
                    "anchors_with_any_divergence": anchors_with_divergence,
                    "anchors_with_any_divergence_pct": (
                        100.0 * anchors_with_divergence / total
                        if total else float("nan")
                    ),
                    "anchors_with_divergence_within_10d_of_top": (
                        anchors_with_divergence_near_top
                    ),
                    "top_coverage_rate_pct": (
                        100.0 * anchors_with_divergence_near_top / total
                        if total else float("nan")
                    ),
                    "near_top_signal_lead_median_days": (
                        float(np.median(nearest_distances))
                        if nearest_distances else float("nan")
                    ),
                }
            )
    top_coverage = pd.DataFrame(top_coverage_records)

    sensitivity_event_sets: list[tuple[str, pd.DataFrame]] = []
    for threshold in (0.7, 0.8, 0.9):
        events = primary_events.drop(
            columns=["cooldown_days", "cooldown_selected"]
        ).copy()
        events["is_volume_price_divergence"] = events[
            "volume_ratio_to_prior_peak"
        ].le(threshold)
        events["volume_ratio_threshold"] = threshold
        events["spec_name"] = f"close_l60_v5_t{threshold:.1f}"
        events = apply_class_cooldown(events, cooldown_days=10)
        sensitivity_event_sets.append((events["spec_name"].iloc[0], events))

    additional_specs = [
        ("close_l40_v5_t0.8", "close", 40, 5, 0.8),
        ("close_l120_v5_t0.8", "close", 120, 5, 0.8),
        ("close_l60_v10_t0.8", "close", 60, 10, 0.8),
        ("high_l60_v5_t0.8", "high", 60, 5, 0.8),
    ]
    for spec_name, price_col, lookback, volume_window, threshold in additional_specs:
        events = run_spec(
            price_col=price_col,
            lookback_days=lookback,
            volume_window=volume_window,
            volume_ratio_threshold=threshold,
            spec_name=spec_name,
        )
        if events.empty:
            continue
        events = apply_class_cooldown(events, cooldown_days=10)
        sensitivity_event_sets.append((spec_name, events))

    sensitivity_records: list[dict[str, Any]] = []
    for spec_name, events in sensitivity_event_sets:
        for anchor_period, group in events.groupby("anchor_period", sort=True):
            for scope, scoped in {
                "all_candidate_days": group,
                "cooldown_10d": group.loc[group["cooldown_selected"].map(_as_bool)],
            }.items():
                summary = summarize_effectiveness(scoped).set_index("event_class")
                if not {
                    "divergence", "non_divergent_new_high"
                }.issubset(summary.index):
                    continue
                divergence = summary.loc["divergence"]
                control = summary.loc["non_divergent_new_high"]

                def safe_lift(numerator: float, denominator: float) -> float:
                    return numerator / denominator if denominator > 0 else float("nan")

                sensitivity_records.append(
                    {
                        "spec_name": spec_name,
                        "anchor_period": int(anchor_period),
                        "scope": scope,
                        "divergence_event_count": int(divergence["event_count"]),
                        "control_event_count": int(control["event_count"]),
                        "divergence_drawdown_ge_10pct_rate": float(
                            divergence["drawdown_ge_10pct_rate"]
                        ),
                        "control_drawdown_ge_10pct_rate": float(
                            control["drawdown_ge_10pct_rate"]
                        ),
                        "drawdown_rate_lift": safe_lift(
                            float(divergence["drawdown_ge_10pct_rate"]),
                            float(control["drawdown_ge_10pct_rate"]),
                        ),
                        "divergence_top_like_rate": float(
                            divergence["top_like_forward_rate"]
                        ),
                        "control_top_like_rate": float(
                            control["top_like_forward_rate"]
                        ),
                        "top_like_rate_lift": safe_lift(
                            float(divergence["top_like_forward_rate"]),
                            float(control["top_like_forward_rate"]),
                        ),
                        "divergence_median_forward_return_pct": float(
                            divergence["median_forward_return_pct"]
                        ),
                        "control_median_forward_return_pct": float(
                            control["median_forward_return_pct"]
                        ),
                        "divergence_near_peak_10d_rate": float(
                            divergence["near_anchor_peak_10d_rate"]
                        ),
                        "control_near_peak_10d_rate": float(
                            control["near_anchor_peak_10d_rate"]
                        ),
                    }
                )
    sensitivity_summary = pd.DataFrame(sensitivity_records)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "events": args.output_dir / "divergence_event_detail.csv",
        "effectiveness": args.output_dir / "effectiveness_summary.csv",
        "lifts": args.output_dir / "effect_lift_summary.csv",
        "within_detail": args.output_dir / "within_anchor_effect_detail.csv",
        "within_summary": args.output_dir / "within_anchor_effect_summary.csv",
        "coverage": args.output_dir / "top_coverage_summary.csv",
        "sensitivity": args.output_dir / "sensitivity_summary.csv",
        "report": args.output_dir / "report.md",
        "metadata": args.output_dir / "metadata.json",
    }
    primary_events.to_csv(files["events"], index=False, encoding="utf-8-sig")
    effectiveness_summary.to_csv(
        files["effectiveness"], index=False, encoding="utf-8-sig"
    )
    effect_lifts.to_csv(files["lifts"], index=False, encoding="utf-8-sig")
    within_anchor_detail.to_csv(
        files["within_detail"], index=False, encoding="utf-8-sig"
    )
    within_anchor_summary.to_csv(
        files["within_summary"], index=False, encoding="utf-8-sig"
    )
    top_coverage.to_csv(files["coverage"], index=False, encoding="utf-8-sig")
    sensitivity_summary.to_csv(
        files["sensitivity"], index=False, encoding="utf-8-sig"
    )
    _write_divergence_report(
        effectiveness=effectiveness_summary,
        lifts=effect_lifts,
        within_anchor=within_anchor_summary,
        coverage=top_coverage,
        sensitivity=sensitivity_summary,
        output=files["report"],
        actual_end=str(source_metadata["actual_target_end"]),
    )

    output_files = [path for name, path in files.items() if name != "metadata"]
    metadata = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "actual_target_end": source_metadata["actual_target_end"],
        "price_basis": source_metadata["price_basis"],
        "daily_access": source_metadata["daily_access"],
        "qfq": source_metadata["qfq"],
        "primary_signal": {
            "price": "QFQ close makes a strict new 60-stock-trading-day high within the long-MA anchor",
            "volume": "5-day trailing mean volume <= 80% of the 5-day mean volume at the prior 60-day price peak",
            "no_lookahead": True,
            "control": "new 60-day high days without the volume divergence condition",
        },
        "outcomes": {
            "forward_window_days": args.forward_days,
            "drawdown_event": "minimum forward close within 20 stock trading days <= -10% from signal close",
            "top_like_pattern": "forward max run-up <= 5% and forward max drawdown <= -10%",
            "near_final_top": "signal is 0-10 stock trading days before completed anchor's final maximum close",
        },
        "right_censoring": (
            "final-anchor-top proximity is evaluated only for completed anchors; "
            "forward outcomes retain right-censored anchors only when the full forward window exists"
        ),
        "primary_event_rows": int(len(primary_events)),
        "primary_divergence_rows": int(
            primary_events["is_volume_price_divergence"].map(_as_bool).sum()
        ),
        "effectiveness_summary": effectiveness_summary.to_dict(orient="records"),
        "effect_lifts": effect_lifts.to_dict(orient="records"),
        "within_anchor_effectiveness": within_anchor_summary.to_dict(
            orient="records"
        ),
        "top_coverage": top_coverage.to_dict(orient="records"),
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
    print(effectiveness_summary.to_string(index=False))
    print("\nLifts:")
    print(effect_lifts.to_string(index=False))
    print("\nTop coverage:")
    print(top_coverage.to_string(index=False))
    print(f"\nOutput: {args.output_dir}")


def _write_divergence_report(
    *,
    effectiveness: pd.DataFrame,
    lifts: pd.DataFrame,
    within_anchor: pd.DataFrame,
    coverage: pd.DataFrame,
    sensitivity: pd.DataFrame,
    output: Path,
    actual_end: str,
) -> None:
    def fmt(value: object, suffix: str = "") -> str:
        number = float(value)
        return "—" if not math.isfinite(number) else f"{number:.1f}{suffix}"

    lines = [
        "# 量价背离是否是长趋势个股的有效见顶信号",
        "",
        f"- 数据截至：`{actual_end}`；价格使用前复权收盘价。",
        "- 无前视信号：收盘价严格突破此前60个交易日最高收盘价，但当日5日均量不超过前一价格高点5日均量的80%。",
        "- 对照组：同样创60日新高、但没有量价背离的交易日。",
        "- 主要结果：信号后20日内最大回撤≥10%；“顶部形态”另要求后20日最大上涨≤5%。",
        "- 已结束episode另检验信号是否位于最终最高收盘价前0–10个交易日；右截断episode不做最终顶部判断。",
        "",
        "## 事件效果",
        "",
        "| 锚定均线 | 去重口径 | 事件类别 | 事件数 | 20日前瞻有效数 | 20日收益中位数 | 最大回撤中位数 | ≥10%回撤率 | 顶部形态率 | 最终顶前10日命中率 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    class_names = {
        "divergence": "量价背离",
        "non_divergent_new_high": "非背离新高",
    }
    scope_names = {
        "all_candidate_days": "全部候选日",
        "cooldown_10d": "同类信号间隔10日",
    }
    for _, row in effectiveness.sort_values(
        ["anchor_period", "scope", "event_class"]
    ).iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | {scope_names[row['scope']]} | "
            f"{class_names[row['event_class']]} | {int(row['event_count'])} | "
            f"{int(row['forward_comparable_count'])} | {fmt(row['median_forward_return_pct'], '%')} | "
            f"{fmt(row['median_forward_max_drawdown_pct'], '%')} | "
            f"{fmt(row['drawdown_ge_10pct_rate'], '%')} | "
            f"{fmt(row['top_like_forward_rate'], '%')} | "
            f"{fmt(row['near_anchor_peak_10d_rate'], '%')} |"
        )
    lines.extend(
        [
            "",
            "## 相对非背离新高的倍率",
            "",
            "| 锚定均线 | 去重口径 | ≥10%回撤率倍率 | 顶部形态率倍率 | 最终顶前10日命中倍率 | 20日收益差 |",
            "|---:|---|---:|---:|---:|---:|",
        ]
    )
    for _, row in lifts.sort_values(["anchor_period", "scope"]).iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | {scope_names[row['scope']]} | "
            f"{fmt(row['drawdown_ge_10pct_rate_lift'], '×')} | "
            f"{fmt(row['top_like_forward_rate_lift'], '×')} | "
            f"{fmt(row['near_anchor_peak_10d_rate_lift'], '×')} | "
            f"{fmt(row['median_forward_return_difference_pct'], '个百分点')} |"
        )
    lines.extend(
        [
            "",
            "## 同一长趋势内部配对比较",
            "",
            "| 锚定均线 | 去重口径 | 可配对episode | 背离回撤率更高 | 更低 | 回撤率差中位数 | 回撤率差均值 | 20日收益差中位数 |",
            "|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in within_anchor.sort_values(["anchor_period", "scope"]).iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | {scope_names[row['scope']]} | "
            f"{int(row['paired_anchor_count'])} | {int(row['divergence_rate_higher_count'])} | "
            f"{int(row['divergence_rate_lower_count'])} | "
            f"{fmt(row['median_drawdown_rate_difference_pct'], '个百分点')} | "
            f"{fmt(row['mean_drawdown_rate_difference_pct'], '个百分点')} | "
            f"{fmt(row['median_forward_return_difference_pct'], '个百分点')} |"
        )
    lines.extend(
        [
            "",
            "## 已结束长趋势的顶部覆盖率",
            "",
            "| 锚定均线 | 去重口径 | 已结束episode | 有背离episode | 顶前10日出现背离 | 顶部覆盖率 | 命中信号领先中位数 |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for _, row in coverage.sort_values(["anchor_period", "scope"]).iterrows():
        lines.append(
            f"| MA{int(row['anchor_period'])} | {scope_names[row['scope']]} | "
            f"{int(row['completed_anchor_count'])} | {int(row['anchors_with_any_divergence'])} | "
            f"{int(row['anchors_with_divergence_within_10d_of_top'])} | "
            f"{fmt(row['top_coverage_rate_pct'], '%')} | "
            f"{fmt(row['near_top_signal_lead_median_days'], '日')} |"
        )
    lines.extend(
        [
            "",
            "## 参数敏感性（10日同类信号去重）",
            "",
            "| 参数组合 | 锚定均线 | 背离事件 | 对照事件 | 背离≥10%回撤率 | 对照率 | 倍率 | 背离20日收益中位数 | 对照中位数 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    sensitivity_view = sensitivity.loc[
        sensitivity["scope"].eq("cooldown_10d")
    ].sort_values(["spec_name", "anchor_period"])
    for _, row in sensitivity_view.iterrows():
        lines.append(
            f"| {row['spec_name']} | MA{int(row['anchor_period'])} | "
            f"{int(row['divergence_event_count'])} | {int(row['control_event_count'])} | "
            f"{fmt(row['divergence_drawdown_ge_10pct_rate'], '%')} | "
            f"{fmt(row['control_drawdown_ge_10pct_rate'], '%')} | "
            f"{fmt(row['drawdown_rate_lift'], '×')} | "
            f"{fmt(row['divergence_median_forward_return_pct'], '%')} | "
            f"{fmt(row['control_median_forward_return_pct'], '%')} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 信号特征只使用当日及此前数据；20日回撤和最终顶部仅用于事后评价。",
            "- 连续创高会产生相关事件，因此同时提供全部候选日和同类信号间隔10日的去重结果。",
            "- MA120样本全部右截断，只能评价完整20日前瞻窗口，不能评价最终顶部命中率。",
            "- 背离若优于非背离新高，只说明条件关联；仍需结合交易成本和退出规则回测后才能作为交易信号。",
            "",
            "## 产物",
            "",
            "- `divergence_event_detail.csv`",
            "- `effectiveness_summary.csv` / `effect_lift_summary.csv`",
            "- `top_coverage_summary.csv` / `sensitivity_summary.csv`",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
